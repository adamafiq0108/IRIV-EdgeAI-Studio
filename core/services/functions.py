# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
functions.py -- the Functions page's backend: presets, validation, storage.

A "function" is a no-code analytics rule the operator composes on the
dashboard: dwell time in a zone, travel time between two lines, a class-
filtered crossing counter, or a custom arithmetic expression over any stat
keys. The documents are stored as [[functions]] in the system package's
config.local.toml -- the same hot-reload channel zones and outputs use -- and
interpreted by the detection app (systems/<slug>/app/functions.py).

Functions are DATA, never code. The expression validator here mirrors the
app-side whitelist exactly (ast.parse + allowed node types); the app engine
re-validates on load, so a bypassed API still cannot plant executable code.
The two validators are duplicated on purpose: the app imports nothing from
core/ (it must run standalone on a bare Pi), same as detector_control vs
app/config.py.
"""

import ast
import re

from core.services import detector_control as dc

TYPES = ("zone_count", "zone_dwell", "zone_unique", "line_count",
         "line_travel", "zone_travel", "proximity", "guard", "trails", "expr",
         "api_poll", "query_value", "mqtt_sub", "total", "custom",
         "frame_count", "frame_unique",
         "zone_countdown", "zone_occupancy", "object_detect", "board_input",
         "modbus_read", "heatmap", "schedule", "inject", "state_watch")
TRIGGER_OPS = (">", ">=", "<", "<=", "==", "!=")

#: What an Inject node's payload may be. Mirrors systems/demo/app/functions.py
#: (duplicated on purpose -- the app imports nothing from core/):
#:   string     the text as typed
#:   number     a number
#:   boolean    true / false, published as 1 / 0 like every on/off here
#:   json       validated JSON; a number or true/false inside it is that
#:              number, anything else is its compact text
#:   timestamp  milliseconds since the epoch, taken when it FIRES
INJECT_TYPES = ("string", "number", "boolean", "json", "timestamp")

#: The function types that need no picture. Mirrors systems/demo/app/
#: functions.py LOGIC_TYPES: what the app's LOGIC MODE runs when the config
#: carries no compiled word on which rules wait for detection
#: (workflow.logic_artifacts is that word, and it is decided by the graph).
LOGIC_TYPES = ("expr", "api_poll", "query_value", "mqtt_sub", "total",
               "board_input", "modbus_read", "schedule", "inject",
               "state_watch")
#: The fastest an Inject may repeat, and the slowest. Faster than a second is
#: a stream, not a button; a day is what a Schedule is for past that.
MIN_INJECT_INTERVAL_S = 1
MAX_INJECT_INTERVAL_S = 86400
#: Longest string/JSON payload. A payload is a value on a wire, not a file.
MAX_INJECT_PAYLOAD = 2000


def clean_inject_payload(ptype, payload):
    """(type, payload as stored) for an Inject, or ValueError with the
    sentence the inspector shows. Stored as TEXT whatever the type, so one
    field round-trips every type and a JSON document keeps the spacing the
    operator typed."""
    ptype = str(ptype or "string").strip().lower()
    if ptype not in INJECT_TYPES:
        raise ValueError("the payload type must be one of "
                         + ", ".join(INJECT_TYPES))
    text = "" if payload is None else str(payload)
    if ptype == "boolean":
        word = text.strip().lower()
        if word not in ("true", "false", "1", "0"):
            raise ValueError("a true/false payload must be true or false")
        return ptype, "true" if word in ("true", "1") else "false"
    if ptype == "number":
        try:
            num = float(text.strip())
        except ValueError:
            raise ValueError(f"'{text[:40]}' is not a number")
        if num != num or num in (float("inf"), float("-inf")):
            raise ValueError("the payload must be a finite number")
        return ptype, text.strip()
    if ptype == "json":
        import json                                           # noqa: PLC0415
        try:
            json.loads(text)
        except ValueError as exc:
            raise ValueError(f"the JSON payload does not parse: {exc}")
        if len(text) > MAX_INJECT_PAYLOAD:
            raise ValueError(f"the JSON payload is longer than "
                             f"{MAX_INJECT_PAYLOAD} characters")
        return ptype, text
    if ptype == "timestamp":
        return ptype, ""              # evaluated when it fires
    if len(text) > MAX_INJECT_PAYLOAD:
        raise ValueError(f"the payload is longer than {MAX_INJECT_PAYLOAD} "
                         "characters")
    return ptype, text

#: How often a Schedule comes round. One node, one selector -- separate
#: Daily/Weekly/Monthly nodes would be the same clock three times.
SCHEDULE_EVERY = ("minutes", "hourly", "daily", "weekly", "monthly")
#: A schedule that repeats faster than this is a timer, not a report, and a
#: timer belongs on the output that publishes on an interval.
MIN_SCHEDULE_MINUTES = 1
MAX_SCHEDULE_MINUTES = 1440


#: The weekday names a window is written in, Monday first -- the order the
#: chips are drawn in and the order tm_wday counts in, so neither side has to
#: translate.
WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def clean_window(p):
    """{hours_from, hours_to, days, from_date, to_date} kept from a document.

    A schedule's own "and only then" -- a daily window, which weekdays, an
    absolute date range. Nothing here is a restriction unless it PARSES: a
    half-typed time or a malformed date is dropped rather than stored, because
    the failure mode of a bad window is a silently muted report, and that is
    discovered by the thing it was watching for happening unannounced.
    """
    out = {}
    for key in ("hours_from", "hours_to"):
        text = str(p.get(key) or "").strip()
        if re.match(r"^([01]\d|2[0-3]):[0-5]\d$", text):
            out[key] = text
    for key in ("from_date", "to_date"):
        text = str(p.get(key) or "").strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
            out[key] = text
    want = [d.strip().lower()[:3] for d in str(p.get("days") or "").split(",")]
    want = [d for d in want if d in WEEKDAY_NAMES]
    # EVERY day selected is the same as no restriction, and storing it as one
    # means a later change to the day names could mute the node.
    if want and len(set(want)) < 7:
        out["days"] = ",".join(sorted(set(want), key=WEEKDAY_NAMES.index))
    return out


def parse_hhmm(text, field="at"):
    """(hour, minute) from "HH:MM", or ValueError. Bare "9" reads as 09:00 --
    somebody typing an hour into a time box means the hour."""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError(f"{field} needs a time, like 12:00")
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a time like 12:00, not {raw!r}")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{field} must be between 00:00 and 23:59")
    return hour, minute

#: api_poll bounds, mirroring the app engine (duplicated on purpose).
MIN_POLL_S = 2.0
MAX_POLL_S = 3600.0
#: Each api_poll function is a thread on a Pi that also runs inference.
MAX_API_POLLS = 8
#: A query variable's floor, well above the API one: every tick is a database
#: round trip on a box that is also running inference, and no dashboard needs
#: a total that is one second old.
MIN_QUERY_POLL_S = 10.0
#: How a query's rows become one number. Mirrors sqlconsole.PICKS -- named
#: again here because this validator must not import a database module to
#: check a document.
QUERY_PICKS = ("first", "last", "sum", "avg", "min", "max", "count")
MAX_HEADER_ROWS = 8
#: The engine sets these itself; a document that overrides them is a
#: request-smuggling foot-gun, not a feature.
BANNED_HEADERS = ("host", "content-length", "transfer-encoding")
#: Mirrors the app engine's TRIGGER_MODES (the two validators are duplicated
#: on purpose -- the app imports nothing from core/).
TRIGGER_MODES = ("above", "at_least", "below", "at_most", "equals",
                 "not_equals", "is_true", "is_false", "rising", "falling",
                 "changed", "in_range", "out_of_range")
SPEED_UNITS = ("kmh", "mph", "ms")
SEVERITIES = ("info", "warn", "crit")

#: A json_path segment: a plain key or a list index, never an expression.
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _clean_headers(rows):
    """Validated [{name, value}] request headers, or []."""
    out = []
    for row in (rows or [])[:MAX_HEADER_ROWS]:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue                      # a blank row is an unfinished edit
        if not _HEADER_NAME_RE.match(name):
            raise ValueError(f"header name {name!r} may only contain letters, "
                             "digits and hyphens")
        if name.lower() in BANNED_HEADERS:
            raise ValueError(f"header {name!r} is set by the system and cannot "
                             "be overridden")
        value = str(row.get("value") or "")
        if len(value) > 500:
            raise ValueError(f"header {name!r} value is too long (max 500)")
        out.append({"name": name, "value": value})
    return out


#: params.camera set to this runs the rule on EVERY camera of the system.
#: Mirrors the app engine's ALL_CAMERAS (duplicated on purpose -- the app
#: imports nothing from core/).
ALL_CAMERAS = "*"

#: What each type publishes (suffixes of fn_<id>_*), for the UI to bind to.
OUTPUTS = {
    "zone_count": ("count",),
    # TIMES only. `current` -- how many are inside right now -- was the first
    # entry, which made it the node's primary output: an Alert dropped after a
    # Cycle Time defaulted to a HEAD COUNT, so "alert when somebody has been in
    # there a while" silently became "alert when two people are in there",
    # firing the instant the second walked in. Counting a zone is what the Zone
    # Counter and Object in Zone are for, and a node that answers a question
    # another node already answers is a node whose default is a trap.
    # The engine still keeps the number internally (it is what live_s is
    # measured from); it is simply no longer an output to bind to.
    "zone_dwell": ("live_s", "avg_s", "max_s"),
    # The engine keeps this one's per-day tallies on disk too, so it answers
    # today/week/month the same way the whole-frame counter does.
    "zone_unique": ("total", "new", "today", "week", "month"),
    # `cross` is every crossing whichever way -- the only tally a PLAIN line
    # (one with no IN side) can honestly publish, and still meaningful on a
    # faced one as in + out.
    "line_count": ("in", "out", "net", "cross"),
    # Trip times, and the two speeds it can work out from a real-world
    # distance. avg_s/max_s/min_s are the whole point of measuring A to B --
    # "the slowest trip today" is the bottleneck, and the average alone hides
    # it -- and they cost nothing: the engine already had the sum.
    "line_travel": ("trips", "last_s", "avg_s", "max_s", "min_s",
                    "last_speed", "avg_speed"),
    # A cycle measured between two PLACES rather than two lines: how long from
    # entering the start zone/line to reaching the end one. A line pair is the
    # special case line_travel already covered; this is the general one.
    "zone_travel": ("trips", "last_s", "avg_s"),
    # A time BUDGET for the zone: each arrival spends deduct_s of start_s.
    # remaining_s is what a "time is up" alert watches.
    "zone_countdown": ("remaining_s", "used_s", "entries"),
    # One clock for the PLACE: runs while anything is inside, stops when
    # empty. occupied_s is the current stretch, last_s the previous one,
    # total_s the accumulation, sessions how often it went empty->occupied.
    "zone_occupancy": ("occupied_s", "last_s", "total_s", "sessions", "active"),
    # Distance between two object classes (link line on the feed): pairs
    # currently within the threshold, nearest pair in pixels, alerts fired,
    # and the condition as a 0/1 state (1 while any pair is too close).
    "proximity": ("pairs", "nearest_px", "alerts", "active"),
    # A guard bubble around each object of the chosen classes; another class
    # entering it is an intrusion. overlap_pct is how deep the worst
    # encroachment is right now; active = 1 while anything is inside.
    "guard": ("intrusions", "inside_now", "overlap_pct", "active"),
    # Movement trails: objects currently leaving a trail, 1 while any moves,
    # and how many have been IDLE (no stored movement) for idle_s -- "the
    # forklift stopped" is a rule, not a picture.
    "trails": ("tracked", "active", "idle"),
    # WHERE things happen. The map itself is one stat per cell
    # (fn_<id>_c<row>_<col>, published like an object watch's per-class keys,
    # and read by the heatmap widget by prefix); these two are what can be
    # alerted on or put on a plain card -- how busy the busiest patch is, and
    # how much of the view has been used at all.
    "heatmap": ("hot", "spots"),
    "expr": ("value",),
    # The clock as a rule. `due` is 1 for the one tick the scheduled moment
    # passes and 0 the rest of the time, which is what makes a Schedule arm an
    # Email the way a Condition does -- without pretending a time of day is a
    # threshold somebody chose. `runs` is how many times it has come round
    # since the box was set up, `next_s` how long until the next one (what the
    # node shows so a schedule can be checked without waiting for it).
    "schedule": ("due", "runs", "next_s"),
    # A button on the canvas, and the clock that repeats it. `value` is the
    # last payload it injected (a number, 1/0, or text); `fired` is 1 for the
    # one tick an inject happens -- the edge that arms an output, exactly as a
    # Schedule's `due` does; `runs` counts injects since the app started.
    "inject": ("value", "fired", "runs"),
    # The ON or OFF point of a Detection Stream / Class Filter
    # (state_points.py). `value` is 1 while the state matches the point,
    # `fired` 1 for the one tick it starts matching (and once at start-up),
    # `runs` how many times that has happened -- an Inject's three, so
    # everything an Inject feeds takes a point the same way.
    "state_watch": ("value", "fired", "runs"),
    # A number fetched from an HTTP endpoint. `ok` is 0 while the endpoint has
    # been failing, so a workflow can tell "the reading is zero" apart from
    # "there is no reading" -- they mean opposite things to an alert.
    "api_poll": ("value", "ok"),
    # One number out of a Query, kept as a variable. Same two metrics as
    # api_poll and for the same reason: `ok` is 0 while the query is failing
    # or returning nothing, and "the total is zero" is a different fact from
    # "there is no total" -- an alert must be able to tell them apart.
    "query_value": ("value", "ok"),
    # A dry contact wired into the box: 1 while it is closed. `changes` counts
    # edges (how many times the machine cycled), `held_s` is how long it has
    # been in its current state -- "the guard door has been open 30 seconds"
    # is a rule, not a reading. `ok` is 0 when the pin cannot be read at all,
    # because an unreadable safety input is not the same as an open one.
    "board_input": ("state", "changes", "held_s", "ok"),
    # Modbus over the isolated RS485 port. `value` is the first register
    # read; a quantity > 1 also publishes r0..rN-1 (one per register,
    # counting up from the start address), reachable from a Custom Formula
    # or a Data Table column by key.
    "modbus_read": ("value", "ok"),
    "mqtt_sub": ("value", "ok", "messages"),
    "custom": ("value",),
    # A number accumulated over time from another value: how many times it
    # fired, or how much it grew. The thing "total = total + new" was always
    # trying to say, as a rule instead of a self-referencing formula.
    "total": ("total",),
    # Whole-FRAME counting, no zone involved: how many are on camera right
    # now, and how many distinct objects have been seen. The unique one keeps
    # per-day tallies on disk, so `today`/`week`/`month` survive a restart and
    # Analytics can scope them to its date range.
    "frame_count": ("count",),
    "frame_unique": ("total", "new", "today", "week", "month"),
    # "Is this object here?" -- the whole frame, or one area zone when
    # params.zone is set. `found` is 1 while at least one chosen object is
    # present and 0 otherwise (the thing an alert or a snapshot waits for),
    # `count` how many, `arrived` how many appeared since the last frame.
    "object_detect": ("found", "count", "arrived", "verdict"),
}

#: The shipped preset library: what the Functions page shows as draggable
#: cards. `form` tells the UI which inputs to render; everything compiles to
#: the same [[functions]] document.
PRESETS = (
    {"type": "zone_count", "title": "Zone occupancy",
     "description": "How many objects of the chosen classes are inside a zone right now.",
     "form": ("zone", "classes"), "metric": "count"},
    {"type": "zone_dwell", "title": "Time in zone",
     "description": "How long objects stay inside a zone: the stay running now, the average and the longest.",
     "form": ("zone", "classes"), "metric": "max_s"},
    {"type": "zone_unique", "title": "Unique visitors",
     "description": "How many DISTINCT tracked objects have entered the zone - each person counts once, no line needed.",
     "form": ("zone", "classes"), "metric": "total"},
    {"type": "line_count", "title": "Line crossing counter",
     "description": "Counts objects crossing a line, in and out, filtered by class.",
     "form": ("line", "classes"), "metric": "net"},
    {"type": "line_travel", "title": "Travel time & speed (A → B)",
     "description": "Time from crossing line A to crossing line B per object, and speed from the real-world distance between them.",
     "form": ("line_a", "line_b", "distance_m", "speed_unit", "classes"),
     "metric": "last_speed"},
    {"type": "expr", "title": "Custom formula",
     "description": "Combine any values with + − × ÷ and comparisons: totals, differences, ratios, alert conditions.",
     "form": ("expr",), "metric": "value"},
)

#: Mirrors the app engine's function table (systems/<slug>/app/functions.py
#: _ALLOWED_CALLS) by name -- the two validators are duplicated on purpose,
#: the app imports nothing from core/. tests/smoke_formula.py asserts the two
#: lists are identical, so an addition on one side cannot silently be accepted
#: here and refused (or worse, accepted) there.
_ALLOWED_CALLS = (
    # arithmetic
    "min", "max", "abs", "round", "ceil", "floor", "sqrt", "pow", "clamp",
    "iff", "trunc", "sign", "cbrt", "hypot", "text",
    # logs and growth
    "log", "ln", "log2", "log10", "exp",
    # trigonometry
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "degrees", "radians",
    # statistics / scaling
    "avg", "pct", "lerp", "scale",
)

#: Named constants the expression language resolves (app _CONSTANTS).
_CONSTANTS = ("pi", "e", "tau")


def validate_expr(text):
    """Whitelist-check an arithmetic expression. Returns the variable names it
    reads. Raises ValueError with the message the editor shows."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty expression")
    if len(text) > 500:
        raise ValueError("expression too long (max 500 characters)")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc.msg}")
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load,
                             ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
                             ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
                             ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
                             ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                             ast.FloorDiv)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                continue
            # Short text too: a variable may hold a word, and a rule writes
            # it down to set it or test it. Same cap as the engine's.
            if isinstance(node.value, str) and len(node.value) <= 120:
                continue
            raise ValueError("only numbers and short text are allowed as constants")
        if isinstance(node, ast.Name):
            # Neither a function name nor a named constant is a VARIABLE the
            # rule reads; reporting pi as an input would have the editor
            # warn about a value nothing publishes.
            if node.id not in _ALLOWED_CALLS and node.id not in _CONSTANTS:
                names.add(node.id)
            continue
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name)
                    and node.func.id in _ALLOWED_CALLS and not node.keywords):
                continue
            raise ValueError("only these functions are allowed: "
                             + ", ".join(_ALLOWED_CALLS))
        raise ValueError(f"'{type(node).__name__}' is not allowed in expressions")
    return sorted(names)


def _slugify(value):
    return "".join(c if c.isalnum() else "_"
                   for c in str(value or "").strip().lower()).strip("_")[:40]


#: The aggregates a table column may be reduced with when it feeds a Math
#: input (workflow._dataset_operand). Mirrors the cards' list plus the newest
#: value; kept here so the validator and the compiler cannot drift.
TABLE_AGGS = ("latest", "count", "sum", "avg", "min", "max", "unique", "changes")


def validate_function(f):
    """A cleaned function dict, or ValueError. Mirrors the app-side Function
    constructor so a document that saves here always loads there."""
    ftype = str(f.get("type") or "").strip().lower()
    if ftype not in TYPES:
        raise ValueError(f"function type must be one of {', '.join(TYPES)}")
    fid = _slugify(f.get("id") or f.get("name"))
    if not fid:
        raise ValueError("function needs an id or a name")
    p = dict(f.get("params") or {})
    clean_p = {}

    classes = [str(c).strip() for c in (p.get("classes") or []) if str(c).strip()]
    if classes:
        clean_p["classes"] = classes[:40]

    # With several models on one camera ([[detector.models]]) a function can
    # bind to ONE of them; blank keeps the legacy any-model behaviour.
    model = str(p.get("model") or "").strip()
    if model:
        clean_p["model"] = model[:80]

    # Which camera's detections this function reads. With several cameras on
    # one system the engine hands each camera only its own functions; blank
    # keeps the legacy single-camera behaviour (the first camera).
    # "*" runs the rule on EVERY camera (each evaluates its own copy); the
    # engine folds their values together.
    camera = str(p.get("camera") or "").strip()
    if camera:
        clean_p["camera"] = ALL_CAMERAS if camera == ALL_CAMERAS else camera[:32]

    # WHEN an object counts, for the counters that measure a population over
    # time (zone and frame). Kept here with the other params every counting
    # rule shares, because this validator builds `clean_p` from scratch: a key
    # it does not copy is a setting the operator typed and the box never sees.
    # That is exactly what happened -- the compiler wrote both, this dropped
    # both, and every "minimum stay" on every box counted in real time with no
    # error anywhere to say so.
    for key in ("enter_after_s", "leave_after_s"):
        if p.get(key) in (None, ""):
            continue
        try:
            value = float(p[key])
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be a number of seconds")
        # Clamped rather than refused, matching the compiler: an hour is
        # already past what a counter is for, and the app re-checks the range.
        value = max(0.0, min(3600.0, value))
        if value:
            clean_p[key] = value

    if ftype in ("zone_count", "zone_dwell", "zone_unique",
                 "zone_countdown", "zone_occupancy"):
        zone = str(p.get("zone") or "").strip()
        # A CLOCK may run on the whole view: "how long has this been on
        # camera" and "how long did each arrival get" mean the same thing with
        # no zone to be inside of, and the engine measures them the same way.
        # Everything else here counts a POPULATION of a place, and occupancy
        # asks whether a place is in use at all -- neither has an answer for a
        # frame, so both still insist.
        if not zone and ftype not in ("zone_dwell", "zone_countdown"):
            raise ValueError(f"{ftype} needs params.zone")
        if zone:
            clean_p["zone"] = zone
        if ftype == "zone_countdown":
            try:
                start_s = float(p.get("start_s") or 0)
            except (TypeError, ValueError):
                raise ValueError("start_s must be a number of seconds")
            if start_s <= 0:
                raise ValueError("zone_countdown needs a positive "
                                 "params.start_s (the time budget)")
            clean_p["start_s"] = min(604800.0, start_s)
            try:
                clean_p["deduct_s"] = max(
                    0.0, min(86400.0, float(p.get("deduct_s", 1))))
            except (TypeError, ValueError):
                raise ValueError("deduct_s must be a number of seconds")
            if str(p.get("reset") or "").strip().lower() == "never":
                clean_p["reset"] = "never"
        if ftype == "zone_dwell":
            # How long past a limit tested on this clock still counts as fine.
            try:
                tol = float(p.get("tolerance_s") or 0)
            except (TypeError, ValueError):
                tol = 0.0
            if 0 < tol <= 3600:
                clean_p["tolerance_s"] = tol
        if ftype == "zone_dwell" and p.get("stamp_events"):
            # Emit an event per completed stay carrying entered_at/exited_at.
            # Off by default: a busy zone would otherwise write an event per
            # object per pass, which is a lot of noise for a number most
            # operators only want as an average.
            clean_p["stamp_events"] = True
        if ftype == "zone_unique" and p.get("min_present") is not None:
            # Conditional counting: a new track only counts while the number
            # inside satisfies <count> <present_op> <min_present> (the
            # Zone Counter node and the Zones page's violation action).
            try:
                clean_p["min_present"] = max(0, min(100, int(p["min_present"])))
            except (TypeError, ValueError):
                raise ValueError("min_present must be a whole number")
            op = str(p.get("present_op") or ">").strip()
            if op not in dc.PRESENCE_OPS:
                raise ValueError(
                    f"present_op must be one of {', '.join(dc.PRESENCE_OPS)}")
            if op != ">":
                clean_p["present_op"] = op
    elif ftype == "line_count":
        line = str(p.get("line") or "").strip()
        if not line:
            raise ValueError("line_count needs params.line")
        clean_p["line"] = line
        # Count each object once per direction, by object id. Written only
        # when it is on, so a counter that has never been asked for it
        # keeps exactly the params it had.
        if p.get("distinct"):
            clean_p["distinct"] = True
        # Report the crossings of the moment (this frame), not the running
        # total -- see app/functions.py _update_line_count.
        if p.get("momentary"):
            clean_p["momentary"] = True
            # ...and beside it, the running total too -- only when asked.
            if p.get("totals"):
                clean_p["totals"] = True
    elif ftype == "line_travel":
        line_a = str(p.get("line_a") or "").strip()
        line_b = str(p.get("line_b") or "").strip()
        if not line_a or not line_b:
            raise ValueError("line_travel needs params.line_a and params.line_b")
        if line_a == line_b:
            raise ValueError("line_a and line_b must be different lines")
        try:
            distance = float(p.get("distance_m") or 0)
        except (TypeError, ValueError):
            distance = 0
        # A distance of 0 means SPEED IS OFF. The rule measures trip times
        # either way -- that needs no distance at all -- and speed needs a
        # real-world number nobody on the box can work out, so it is asked for
        # only when it is wanted. It used to be mandatory, which meant every
        # A-to-B timer carried a made-up distance and published confident
        # wrong speeds beside its honest times.
        if distance < 0:
            raise ValueError("line_travel needs params.distance_m to be "
                             "positive metres, or 0 for times only")
        unit = str(p.get("speed_unit") or "kmh").strip().lower()
        if unit not in SPEED_UNITS:
            raise ValueError(f"speed_unit must be one of {', '.join(SPEED_UNITS)}")
        clean_p.update({"line_a": line_a, "line_b": line_b,
                        "distance_m": distance, "speed_unit": unit})
    elif ftype == "zone_travel":
        start = str(p.get("start") or "").strip()
        end = str(p.get("end") or "").strip()
        if not start or not end:
            raise ValueError("zone_travel needs params.start and params.end")
        if start == end:
            raise ValueError("start and end must be different zones or lines")
        clean_p.update({"start": start, "end": end})
        for side in ("start_kind", "end_kind"):
            kind = str(p.get(side) or "area").strip().lower()
            if kind not in ("area", "line"):
                raise ValueError(f"{side} must be 'area' or 'line'")
            clean_p[side] = kind
    elif ftype == "proximity":
        class_a = str(p.get("class_a") or "").strip()
        class_b = str(p.get("class_b") or "").strip()
        if not class_a or not class_b:
            raise ValueError("proximity needs params.class_a and params.class_b")
        try:
            max_distance = float(p.get("max_distance") or 0.15)
        except (TypeError, ValueError):
            raise ValueError("max_distance must be a number")
        if not 0 < max_distance <= 1:
            raise ValueError("max_distance must be between 0 and 1 "
                             "(a fraction of the frame width)")
        draw = str(p.get("draw_link") or "always").strip().lower()
        if draw not in ("off", "alert", "always"):
            raise ValueError("draw_link must be off, alert or always")
        clean_p.update({"class_a": class_a, "class_b": class_b,
                        "max_distance": round(max_distance, 4),
                        "draw_link": draw})
        for side in ("model_a", "model_b"):
            v = str(p.get(side) or "").strip()
            if v:
                clean_p[side] = v[:80]
        # Alerting is the Alert/When node's job; these survive only for
        # hand-written configs that set them (the engine self-alerts then).
        if p.get("severity") is not None:
            _clean_alert_params(p, clean_p)
    elif ftype == "guard":
        try:
            margin = float(p.get("margin_pct") or 50)
        except (TypeError, ValueError):
            raise ValueError("margin_pct must be a number")
        if not 1 <= margin <= 200:
            raise ValueError("margin_pct must be between 1 and 200")
        clean_p["margin_pct"] = round(margin, 1)
        alert_classes = [str(c).strip() for c in (p.get("alert_classes") or [])
                         if str(c).strip()]
        if alert_classes:
            clean_p["alert_classes"] = alert_classes[:40]
        clean_p["draw"] = bool(p.get("draw", True))
        # What counts as an intrusion: the intruder's centre inside the bubble,
        # or the two bubbles overlapping by at least min_overlap_pct.
        touch = str(p.get("touch") or "center").strip().lower()
        if touch not in ("center", "overlap"):
            raise ValueError("touch must be 'center' or 'overlap'")
        clean_p["touch"] = touch
        try:
            overlap = float(p.get("min_overlap_pct") or 0)
        except (TypeError, ValueError):
            raise ValueError("min_overlap_pct must be a number")
        if not 0 <= overlap <= 100:
            raise ValueError("min_overlap_pct must be between 0 and 100")
        clean_p["min_overlap_pct"] = round(overlap, 1)
        if p.get("severity") is not None:
            _clean_alert_params(p, clean_p)
    elif ftype == "trails":
        # Same whitelist rule as every other type: a param with no branch
        # here is DROPPED, which is how the trail length and the idle
        # threshold were silently lost between the canvas and the engine.
        try:
            length = float(p.get("length_s") or 10)
        except (TypeError, ValueError):
            raise ValueError("length_s must be a number of seconds")
        if not 1 <= length <= 120:
            raise ValueError("length_s must be between 1 and 120 seconds")
        clean_p["length_s"] = round(length, 1)
        try:
            idle = float(p.get("idle_s") or 5)
        except (TypeError, ValueError):
            raise ValueError("idle_s must be a number of seconds")
        if not 1 <= idle <= 300:
            raise ValueError("idle_s must be between 1 and 300 seconds")
        clean_p["idle_s"] = round(idle, 1)
        clean_p["draw"] = bool(p.get("draw", True))
    elif ftype == "heatmap":
        # The zone is OPTIONAL: with one, the map covers that area only; with
        # none it covers the whole view. Everything else is the SHAPE of the
        # map -- how many patches, how often one object may add to it, and how
        # fast the map forgets.
        zone = str(p.get("zone") or "").strip()
        if zone:
            clean_p["zone"] = zone
        for key, lo, hi, default in (("cols", 2, 32, 12), ("rows", 2, 24, 8)):
            try:
                value = int(p.get(key) or default)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a whole number of cells")
            clean_p[key] = max(lo, min(hi, value))
        for key, lo, hi, default in (("interval_s", 0.0, 600.0, 1.0),
                                     ("hours", 0.0, 168.0, 0.0)):
            try:
                value = float(p.get(key, default) if p.get(key) is not None
                              else default)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")
            clean_p[key] = max(lo, min(hi, value))
    elif ftype == "object_detect":
        # The zone is OPTIONAL here, which is what makes one rule serve both
        # "in this area" and "anywhere on camera": present = watch that zone,
        # absent = watch the whole frame. `classes` is cleaned above for every
        # type, and an empty one means any detected object counts as found.
        zone = str(p.get("zone") or "").strip()
        if zone:
            clean_p["zone"] = zone
        # OID on the node: an event per ARRIVAL naming the object (its track
        # id and class), which is what a Data Table row per object is written
        # from. Off by default -- a busy view is an event per newcomer.
        if p.get("stamp_events"):
            clean_p["stamp_events"] = True
        # Anomaly: also publish `verdict`, 0 when nothing the filter names was
        # found (GOOD) and 1 when something was (NG). On a Snapshot camera the
        # loop holds it per snap (app/snapgate.py Gate.observe).
        if p.get("anomaly"):
            clean_p["anomaly"] = True
    elif ftype in ("frame_count", "frame_unique"):
        # Whole-frame counting: no zone, no line. `classes` (cleaned above for
        # every type) is the only filter, and an empty one means "everything
        # the model reports", which is what "how many objects are on camera"
        # usually means.
        pass
    elif ftype == "expr":
        expr = str(p.get("expr") or "").strip()
        validate_expr(expr)             # raises with a readable message
        clean_p["expr"] = expr
        # A test of a Cycle Time's live_s against a limit (compile_graph writes
        # these): which clock, and the limit, so an alarm can be about each
        # object's own stay and a photo can report it.
        tfn = _slugify(p.get("timer_fn"))
        try:
            tsec = float(p.get("timer_s") or 0)
        except (TypeError, ValueError):
            tsec = 0.0
        if tfn and 0 < tsec <= 604800:
            clean_p["timer_fn"] = tfn
            clean_p["timer_s"] = tsec
            try:
                ttol = float(p.get("timer_tol_s") or 0)
            except (TypeError, ValueError):
                ttol = 0.0
            if 0 < ttol <= 3600:
                clean_p["timer_tol_s"] = ttol
    elif ftype == "schedule":
        every = str(p.get("every") or "daily").strip().lower()
        if every not in SCHEDULE_EVERY:
            raise ValueError("a schedule repeats "
                             + ", ".join(SCHEDULE_EVERY))
        clean_p["every"] = every
        if every == "minutes":
            try:
                minutes = int(p.get("interval_min") or 0)
            except (TypeError, ValueError):
                raise ValueError("the interval must be a whole number of minutes")
            if not MIN_SCHEDULE_MINUTES <= minutes <= MAX_SCHEDULE_MINUTES:
                raise ValueError(f"the interval must be "
                                 f"{MIN_SCHEDULE_MINUTES}..{MAX_SCHEDULE_MINUTES} "
                                 "minutes")
            clean_p["interval_min"] = minutes
        elif every == "hourly":
            try:
                minute = int(p.get("minute") or 0)
            except (TypeError, ValueError):
                raise ValueError("the minute past the hour must be a number")
            if not 0 <= minute <= 59:
                raise ValueError("the minute past the hour must be 0..59")
            clean_p["minute"] = minute
        else:
            hour, minute = parse_hhmm(p.get("at"), "the time")
            clean_p["at"] = f"{hour:02d}:{minute:02d}"
            if every == "weekly":
                try:
                    weekday = int(p.get("weekday", 0))
                except (TypeError, ValueError):
                    raise ValueError("the weekday must be 0 (Monday) to 6 (Sunday)")
                if not 0 <= weekday <= 6:
                    raise ValueError("the weekday must be 0 (Monday) to 6 (Sunday)")
                clean_p["weekday"] = weekday
            elif every == "monthly":
                try:
                    day = int(p.get("day", 1))
                except (TypeError, ValueError):
                    raise ValueError("the day of the month must be 1..31, "
                                     "or 0 for the last day")
                if not 0 <= day <= 31:
                    raise ValueError("the day of the month must be 1..31, "
                                     "or 0 for the last day")
                # 0 = the last day, whatever length the month is. And a day the
                # month does not have (the 31st of April) runs on that month's
                # last day rather than skipping the report for that month.
                clean_p["day"] = day
        # A missed run -- the box was off at 12:00 -- either goes out when it
        # comes back or is dropped. Off by default: a report that arrives at
        # 3am because the box rebooted is a report nobody asked for.
        if p.get("catch_up"):
            clean_p["catch_up"] = True
        # WHEN this clock is allowed to run at all: a daily window, which
        # weekdays, and an absolute date range. These used to live on the
        # Email and Telegram nodes, one copy per output, which meant three
        # messages off one rule needed the same window typed three times and
        # kept in step by hand. They belong to the CLOCK, which is the node
        # that already answers "when".
        #
        # Every one of them is skipped when blank, and a value that does not
        # parse is DROPPED rather than treated as "never": the failure mode of
        # a bad window is a silently muted report, and that is discovered by
        # the thing it was watching for happening unannounced.
        clean_p.update(clean_window(p))
        # A clock is not tied to a camera, so "every camera" would mean the
        # same report sent once per lens.
        if clean_p.get("camera") == ALL_CAMERAS:
            raise ValueError("a schedule is not tied to a camera - leave the "
                             "camera unset")
    elif ftype == "inject":
        ptype, payload = clean_inject_payload(p.get("ptype"), p.get("payload"))
        clean_p["ptype"] = ptype
        clean_p["payload"] = payload
        topic = str(p.get("topic") or "").strip()
        if topic:
            clean_p["topic"] = topic[:120]
        if p.get("once"):
            try:
                delay = float(p.get("once_delay_s") or 0)
            except (TypeError, ValueError):
                raise ValueError("the start delay must be a number of seconds")
            clean_p["once"] = True
            clean_p["once_delay_s"] = max(0.0, min(3600.0, delay))
        if p.get("autostart") is False:
            clean_p["autostart"] = False
        if p.get("interval_s") not in (None, "", 0, "0"):
            try:
                interval = float(p.get("interval_s"))
            except (TypeError, ValueError):
                raise ValueError("the repeat interval must be a number of "
                                 "seconds")
            if not MIN_INJECT_INTERVAL_S <= interval <= MAX_INJECT_INTERVAL_S:
                raise ValueError(f"an Inject repeats every "
                                 f"{MIN_INJECT_INTERVAL_S} second to 24 hours")
            clean_p["interval_s"] = interval
        if clean_p.get("camera") == ALL_CAMERAS:
            raise ValueError("an Inject is not tied to a camera - leave the "
                             "camera unset")
    elif ftype == "state_watch":
        # An ON/OFF point (state_points.py). Mirrors the app's Function
        # check: what it watches, which side it fires on, and for a Class
        # Filter the drawn node the dashboard pauses by.
        watch = str(p.get("watch") or "")
        if watch not in ("ai", "class_filter"):
            raise ValueError("an ON/OFF point watches ai or class_filter")
        clean_p["watch"] = watch
        clean_p["want"] = "off" if p.get("want") == "off" else "on"
        if watch == "class_filter":
            node = str(p.get("filter") or "").strip()[:60]
            if not node:
                raise ValueError("an ON/OFF point on a Class Filter names "
                                 "the filter")
            clean_p["filter"] = node
        if p.get("fixed_off"):
            clean_p["fixed_off"] = True
        feeds = [str(f)[:80] for f in (p.get("feeds") or []) if str(f)]
        if feeds:
            clean_p["feeds"] = feeds[:50]
        if clean_p.get("camera"):
            raise ValueError("an ON/OFF point is not tied to a camera")
    elif ftype == "board_input":
        from core.services import board_io                  # noqa: PLC0415

        port = str(p.get("port") or "").upper()
        if port not in board_io.INPUT_PORTS:
            raise ValueError("a board input needs params.port set to one of "
                             + ", ".join(board_io.INPUT_PORTS))
        clean_p["port"] = port
        # Some contacts are wired normally-closed, which reads as 1 when
        # nothing is happening. Inverting here rather than making every rule
        # downstream say "== 0" keeps the workflow readable.
        if p.get("invert"):
            clean_p["invert"] = True
    elif ftype == "modbus_read":
        # The four Modbus READ function codes -- 1 coils, 2 discrete inputs,
        # 3 holding registers, 4 input registers. A write is not a variable.
        try:
            fc = int(p.get("fc") or 3)
        except (TypeError, ValueError):
            raise ValueError("fc must be a Modbus function code, 1 to 4")
        if fc not in (1, 2, 3, 4):
            raise ValueError("fc must be 1 (coils), 2 (discrete inputs), "
                             "3 (holding registers) or 4 (input registers)")
        clean_p["fc"] = fc
        # A named Modbus Server connection (TCP or serial), shared with the
        # Write nodes. Without one the reader keeps the older rule: the
        # board's own RS485 port at the node's baud rate.
        server = str(p.get("server") or "").strip()[:40]
        try:
            if server:
                unit = (None if p.get("unit") in (None, "")
                        else int(p["unit"]))
            else:
                unit = int(p.get("unit") or 1)
            address = int(p.get("address") or 0)
            # `or 1` would turn an explicit 0 into 1 -- an explicit zero is a
            # mistake to refuse, not a default to paper over.
            quantity = int(p["quantity"]) if p.get("quantity") is not None else 1
            baudrate = int(p.get("baudrate") or 9600)
            # An EXPLICIT 0 is "read on a trigger", not a missing interval,
            # so `or MIN_POLL_S` would have erased the one setting the node
            # exists for in that mode.
            interval = (MIN_POLL_S if p.get("interval_s") in (None, "")
                        else float(p["interval_s"]))
        except (TypeError, ValueError):
            raise ValueError("unit, address, quantity and baudrate must be numbers")
        # Through a server, blank borrows the server's Unit-Id and 0..255 is
        # allowed -- a TCP gateway commonly answers on 0 or 255. On the bare
        # RS485 port it is a slave id on a wire, 1..247, as before.
        if server:
            if unit is not None and not 0 <= unit <= 255:
                raise ValueError("unit must be a Unit-Id, 0 to 255")
        elif not 1 <= unit <= 247:
            raise ValueError("unit must be a Modbus slave id, 1 to 247")
        if not 0 <= address <= 65535:
            raise ValueError("address must be 0 to 65535")
        # 32 registers is a full sensor block; past that it is a bulk
        # transfer, and each register becomes a published stat.
        if not 1 <= quantity <= 32:
            raise ValueError("quantity must be 1 to 32 registers")
        if server:
            clean_p["server"] = server
        if unit is not None:
            clean_p["unit"] = unit
        clean_p["address"] = address
        clean_p["quantity"] = quantity
        if not server:
            clean_p["baudrate"] = baudrate
        # A custom request frame (Advanced editor): ID .. last field as hex,
        # CRC excluded -- the app computes and appends it.
        frame = re.sub(r"\s+", "", str(p.get("frame") or ""))
        if frame:
            if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}){2,64}", frame):
                raise ValueError("frame must be 2 to 64 bytes of hex")
            clean_p["frame"] = frame.upper()
        # Interval 0 = read once per rising edge of a trigger -- the app
        # watches fn_<fn>_<metric> (or that rule's alert) and kicks the
        # reader. Without a trigger a 0 would mean "never", and nothing on
        # the page would say why the value never arrives, so it is refused.
        if interval <= 0:
            trig = p.get("trigger") if isinstance(p.get("trigger"), dict) else {}
            trig_fn = _slugify(trig.get("fn"))
            metric = re.sub(r"[^a-z0-9_]", "", str(trig.get("metric") or "value").lower())
            if not trig_fn or not metric:
                raise ValueError("interval 0 means read on a trigger - wire an "
                                 "Inject, a Schedule or a Condition into its "
                                 "trigger port, or set an interval")
            clean_p["interval_s"] = 0
            clean_p["trigger"] = {"fn": trig_fn, "metric": metric}
        else:
            clean_p["interval_s"] = round(min(MAX_POLL_S, max(MIN_POLL_S, interval)), 2)
    elif ftype == "api_poll":
        url = str(p.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("an API value needs a params.url starting with "
                             "http:// or https://")
        # Another computer only, never this box (Pipi M-R1; same rule as a
        # webhook, core/services/netsafe.py).
        from core.services import detector_control as _dc     # noqa: PLC0415
        _dc.refuse_own_address(url[:500])
        clean_p["url"] = url[:500]
        try:
            interval = float(p.get("interval_s") or MIN_POLL_S)
        except (TypeError, ValueError):
            raise ValueError("interval_s must be a number of seconds")
        clean_p["interval_s"] = round(min(MAX_POLL_S, max(MIN_POLL_S, interval)), 2)
        # Dotted path into the JSON answer; blank means the whole document is
        # the number. Kept to plain segments so it can never be an expression.
        path = str(p.get("json_path") or "").strip()
        parts = [s for s in path.split(".") if s]
        if len(parts) > 10 or any(not _SAFE_PATH_RE.match(s) for s in parts):
            raise ValueError("json_path must be up to 10 dotted names or list "
                             "indexes, like readings.0.value")
        if parts:
            clean_p["json_path"] = ".".join(parts)
        try:
            clean_p["fallback"] = float(p.get("fallback") or 0)
        except (TypeError, ValueError):
            raise ValueError("fallback must be a number")
        headers = _clean_headers(p.get("headers"))
        if headers:
            clean_p["headers"] = headers
        token = str(p.get("token") or "").strip()
        if token:
            clean_p["token"] = token[:500]
        # An HTTP fetch is not about a camera, and one copy per camera would
        # mean N pollers on one endpoint answering with folded nonsense.
        if clean_p.get("camera") == ALL_CAMERAS:
            raise ValueError("an API value is not tied to a camera - leave "
                             "the camera unset")
    elif ftype == "query_value":
        # A Query reduced to one number and kept as a variable.
        #
        # The SQL is validated and STORED here, but it is not run here and it
        # is never run by the app: the detection app has no database at all
        # (it emits events; core writes them). So the app polls a local URL
        # and the master, which does have the database, answers with the
        # number. That is why this carries both a statement and a url.
        # TWO SHAPES. A Query's own SQL, or -- from a table column wired into
        # a Math input -- a table, a column, an aggregate and a period, with
        # the master running the same aggregate a card does. No SQL is
        # written for the second shape: what runs is the repo's aggregate,
        # under the same read-only role, so there is nothing to inject into.
        dataset = str(p.get("dataset") or "").strip()
        if dataset:
            clean_p["dataset"] = dataset[:60]
            agg = str(p.get("agg") or "count").strip()
            if agg not in TABLE_AGGS:
                raise ValueError(f"agg must be one of {', '.join(TABLE_AGGS)}")
            field = str(p.get("field") or "").strip()[:60]
            if agg != "count" and not field:
                raise ValueError("a table aggregate other than a row count "
                                 "needs the column")
            clean_p["agg"] = agg
            clean_p["field"] = field
            rng = str(p.get("range") or "").strip()
            if rng not in ("", "today", "week", "month"):
                raise ValueError("range must be today, week, month or blank")
            clean_p["range"] = rng
            # A column read as a WORD (a Trigger's Current OID port): the
            # newest value as it is stored, never cast to a number -- a
            # recorded object id is run-scoped text ("k7m2-3").
            if p.get("text"):
                clean_p["text"] = True
        sql = str(p.get("sql") or "").strip().rstrip(";").strip()
        if not sql and not dataset:
            raise ValueError("a query variable needs the Query's statement")
        if sql:
            if ";" in sql:
                raise ValueError("one statement at a time")
            # The same read-only rule the console enforces, checked again
            # here: this document is written by a compile and read by a
            # route, and a boundary that trusts the previous boundary is not
            # a boundary.
            if not re.match(r"^\s*(select|with)\b", sql, re.I):
                raise ValueError("a query variable may only SELECT - it reads "
                                 "the database, it never changes it")
            clean_p["sql"] = sql[:4000]
        tables = []
        for t in (p.get("tables") if isinstance(p.get("tables"), list) else [])[:12]:
            key = str((t or {}).get("key") or "").strip()
            if key:
                tables.append({"key": key[:60],
                               "name": str(t.get("name") or key)[:60],
                               "columns": [dict(c) for c in
                                           (t.get("columns") or [])[:60]]})
        if not tables:
            raise ValueError("a query variable needs the tables its Query may "
                             "read")
        clean_p["tables"] = tables
        clean_p["column"] = str(p.get("column") or "").strip()[:60]
        pick = str(p.get("pick") or "first")
        if pick not in QUERY_PICKS:
            raise ValueError(f"pick must be one of {QUERY_PICKS}")
        clean_p["pick"] = pick
        # Localhost only, and not negotiable: this URL is the app asking the
        # master to run SQL on its behalf. Pointed anywhere else it would be
        # a compiled document telling the box to fetch from a host the
        # operator never named.
        url = str(p.get("url") or "").strip()
        if not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?/", url):
            raise ValueError("a query variable reads from this box only - its "
                             "url must be http://127.0.0.1")
        clean_p["url"] = url[:500]
        # Not the operator's to choose: the answer's shape is this product's
        # own route, and the app reuses the API poller to read it. Written
        # here so the app needs no special case for the digging.
        clean_p["json_path"] = "value"
        try:
            interval = float(p.get("interval_s") or MIN_QUERY_POLL_S)
        except (TypeError, ValueError):
            raise ValueError("interval_s must be a number of seconds")
        # Slower floor than an API poll: every tick is a database round trip
        # on a box that is also running inference, and a query answering a
        # dashboard does not need to be a second old.
        clean_p["interval_s"] = round(min(MAX_POLL_S,
                                          max(MIN_QUERY_POLL_S, interval)), 2)
        try:
            clean_p["fallback"] = float(p.get("fallback") or 0)
        except (TypeError, ValueError):
            raise ValueError("fallback must be a number")
        if clean_p.get("camera") == ALL_CAMERAS:
            raise ValueError("a query variable is not tied to a camera - "
                             "leave the camera unset")
    elif ftype == "mqtt_sub":
        # WHERE: a named MQTT Connection (its id; the broker, the login and
        # the client settings live once in [[mqtt_clients]] and never in a
        # function, which /api/functions shows to any workflow viewer) -- or,
        # saved before Connections, the node's own host and port.
        if "conn" in p:
            conn = str(p.get("conn") or "").strip()
            if not re.match(r"^mq_[0-9a-f]{8}$", conn):
                raise ValueError("an MQTT value needs an MQTT Connection")
            clean_p["conn"] = conn
        else:
            host = str(p.get("host") or "").strip()
            if not host:
                raise ValueError("an MQTT value needs the broker's address")
            clean_p["host"] = host[:120]
            # `or 1883` would turn a typed 0 into the default and hide the
            # typo; absent means default, present means checked.
            raw_port = p.get("port")
            try:
                port = 1883 if raw_port in (None, "") else int(raw_port)
            except (TypeError, ValueError):
                raise ValueError("the MQTT port must be a whole number")
            if not 1 <= port <= 65535:
                raise ValueError("the MQTT port must be between 1 and 65535")
            clean_p["port"] = port
        topic = str(p.get("topic") or "").strip()
        if not topic:
            raise ValueError("an MQTT value needs a topic to subscribe to")
        # Wildcards are the point of MQTT, but a '#' anywhere but the end
        # subscribes to nothing and reads zero for ever with no error.
        if "#" in topic and not topic.endswith("#"):
            raise ValueError("'#' may only be the LAST part of a topic")
        clean_p["topic"] = topic[:200]
        # Same dotted reader as an API value: a broker usually carries JSON,
        # and "the whole payload is the number" is the blank case.
        path = str(p.get("json_path") or "").strip()
        parts = [s for s in path.split(".") if s]
        if len(parts) > 10 or any(not _SAFE_PATH_RE.match(s) for s in parts):
            raise ValueError("json_path must be up to 10 dotted names or list "
                             "indexes, like data.count")
        if parts:
            clean_p["json_path"] = ".".join(parts)
        try:
            clean_p["fallback"] = float(p.get("fallback") or 0)
        except (TypeError, ValueError):
            raise ValueError("fallback must be a number")
        # A Connection carries its own login; only a node from before
        # Connections still brings one.
        for field in (() if "conn" in clean_p else ("username", "password")):
            value = str(p.get(field) or "").strip()
            if value:
                clean_p[field] = value[:120]
        # One subscriber per rule, and a broker connection is not about a
        # camera -- the same reasoning as an API value.
        if clean_p.get("camera") == ALL_CAMERAS:
            raise ValueError("an MQTT value is not tied to a camera - leave "
                             "the camera unset")

    elif ftype == "total":
        # An accumulator over another value. There is no assignment in the
        # expression language ON PURPOSE -- "x = x + new" re-evaluated thirty
        # times a second is thirty additions a second, not a total -- so
        # accumulation is a stateful rule with its own semantics instead.
        key = str(p.get("key") or "").strip()
        if not key:
            raise ValueError("a running total needs params.key - the value "
                             "it accumulates")
        clean_p["key"] = key[:120]
        mode = str(p.get("mode") or "count").strip().lower()
        if mode not in ("count", "sum"):
            raise ValueError("mode must be 'count' (times it fired) or "
                             "'sum' (how much it grew)")
        clean_p["mode"] = mode
        # Optional Reset wire (a 0/1 value; 0 -> 1 starts the total again)
        # and whether a restart continues from the last saved total.
        reset_key = str(p.get("reset_key") or "").strip()
        if reset_key:
            clean_p["reset_key"] = reset_key[:120]
        if p.get("resume"):
            clean_p["resume"] = True
        reset = str(p.get("reset") or "never").strip().lower()
        if reset not in ("never", "daily"):
            raise ValueError("reset must be 'never' or 'daily'")
        if reset != "never":
            clean_p["reset"] = reset
        # Optional blip filter: an increase counts only once it has held this
        # long, so a gauge that blinks with detection dropouts cannot add on
        # every re-rise.
        if p.get("debounce_s") is not None:
            try:
                debounce = float(p["debounce_s"])
            except (TypeError, ValueError):
                raise ValueError("debounce_s must be a number of seconds")
            if debounce < 0 or debounce > 60:
                raise ValueError("debounce_s must be between 0 and 60")
            if debounce:
                clean_p["debounce_s"] = debounce

    elif ftype == "custom":
        # A store template's own evaluator: the handler name is resolved
        # against the system's app/store_ext/ package at run time (put there
        # only by the store installer, from a signature-verified bundle).
        # This validator accepts the REFERENCE; the code's trust is decided
        # at install time, not here.
        handler = _slugify(p.get("handler"))
        if not handler:
            raise ValueError("a custom function needs params.handler - the "
                             "handler name its template's code exports")
        clean_p["handler"] = handler
        # Whatever else the template's author parameterised travels verbatim,
        # bounded so a hostile document cannot balloon the config file.
        extra = {k: v for k, v in p.items()
                 if k not in clean_p and k not in ("classes", "model", "camera")
                 and isinstance(v, (str, int, float, bool))}
        for k in list(extra)[:20]:
            clean_p[str(k)[:40]] = (extra[k][:200]
                                    if isinstance(extra[k], str) else extra[k])

    clean = {
        "id": fid,
        "name": str(f.get("name") or fid).strip()[:80],
        "type": ftype,
        "enabled": bool(f.get("enabled", True)),
        "params": clean_p,
    }

    if f.get("trigger"):
        clean["trigger"] = _validate_trigger(f["trigger"], ftype, clean["name"])
    extra = [t for t in (f.get("triggers") or []) if isinstance(t, dict)]
    if extra:
        clean["triggers"] = [_validate_trigger(t, ftype, clean["name"])
                             for t in extra[:8]]
    return clean


def _clean_alert_params(p, clean_p):
    """severity/message/cooldown_s shared by the self-alerting function types
    (proximity, guard) -- their alert is intrinsic, not a separate trigger."""
    severity = str(p.get("severity") or "warn").strip().lower()
    clean_p["severity"] = severity if severity in SEVERITIES else "warn"
    message = str(p.get("message") or "").strip()
    if message:
        clean_p["message"] = message[:120]
    try:
        clean_p["cooldown_s"] = max(0, min(3600, int(p.get("cooldown_s", 10) or 0)))
    except (TypeError, ValueError):
        raise ValueError("cooldown_s must be a whole number")


def _validate_trigger(trig, ftype, name):
    """One cleaned trigger dict, or ValueError. Mirrors the app engine's
    Function._parse_trigger."""
    mode = str(trig.get("mode") or "").strip().lower()
    op = str(trig.get("op") or "").strip()
    if mode:
        if mode not in TRIGGER_MODES:
            raise ValueError(f"trigger.mode must be one of {', '.join(TRIGGER_MODES)}")
    else:
        op = op or ">"
        if op not in TRIGGER_OPS:
            raise ValueError(f"trigger.op must be one of {', '.join(TRIGGER_OPS)}")
    metric = str(trig.get("metric") or "").strip() or OUTPUTS[ftype][-1]
    if metric not in OUTPUTS[ftype] + ("value",):
        raise ValueError(f"trigger.metric must be one of {', '.join(OUTPUTS[ftype])}")
    severity = str(trig.get("severity") or "warn").strip().lower()
    if severity not in SEVERITIES:
        severity = "warn"
    try:
        value = float(trig.get("value", 1))
        value2 = float(trig.get("value2", 0))
        hold_s = max(0.0, float(trig.get("hold_s", 0) or 0))
    except (TypeError, ValueError):
        raise ValueError("trigger values must be numbers")
    out = {
        "metric": metric, "op": op if op in TRIGGER_OPS else ">", "value": value,
        "severity": severity,
        "message": str(trig.get("message") or f"{name} triggered")[:120],
        "cooldown_s": max(0, int(trig.get("cooldown_s", 30) or 0)),
    }
    if mode:
        out["mode"] = mode
    if mode in ("in_range", "out_of_range"):
        out["value2"] = value2
    if hold_s:
        out["hold_s"] = round(hold_s, 1)
    # WHICH rule's objects this trigger is about, when the canvas wired a
    # rule's OID port into the Trigger. The runtime reads that rule's members
    # each frame: it is what stamps the alarm with who, and what "re-raise
    # per new object" counts.
    oid_source = str(trig.get("oid_source") or "").strip()[:40]
    if oid_source:
        out["oid_source"] = oid_source
    # Complete the outputs after this (photo, Data Table row) when the timer
    # behind the moment ends. Workflow.compile hands it on to those outputs.
    if trig.get("wait_end"):
        out["wait_end"] = True
    # How a held alarm is raised again. Absent means the cooldown, exactly as
    # every trigger written before this existed. "new_object" is only
    # meaningful with somebody to name, so it is dropped without a source
    # rather than left to mean nothing at runtime.
    if str(trig.get("repeat") or "").strip().lower() == "new_object" and oid_source:
        out["repeat"] = "new_object"
    # The DUPLICATE GUARD (a Trigger's Current OID / Current value ports):
    # the stat keys that hold what the table already stored for the object,
    # and how far the value must move before the same object is sent again.
    # Absent on every trigger without the ports, which then runs as before.
    cur_oid_key = str(trig.get("cur_oid_key") or "").strip()[:80]
    if cur_oid_key:
        out["cur_oid_key"] = cur_oid_key
        cur_value_key = str(trig.get("cur_value_key") or "").strip()[:80]
        if cur_value_key:
            out["cur_value_key"] = cur_value_key
        try:
            out["dup_diff"] = max(0.0, float(trig.get("dup_diff") or 0))
        except (TypeError, ValueError):
            raise ValueError("the total difference threshold must be a number")
    # What colour the objects that trip this rule are drawn in. Empty means
    # red, which is what a violation has always been drawn as -- carried only
    # when the operator chose something else, so a config written before this
    # existed keeps meaning exactly what it did.
    colour = box_colour(trig.get("box_color"))
    # THE ZONE'S OWN COLOUR, separate from the boxes'. It used to be one
    # colour plus a "colour what" picker, which forced a choice the scene
    # rarely wants made: a zone going red AND the object inside it going
    # yellow says two different things at once, and the old shape could
    # only say one of them. Two colours, each optional, each meaning
    # exactly what it is called.
    zone_colour = box_colour(trig.get("zone_color"))
    if colour:
        out["box_color"] = colour
    if zone_colour:
        out["zone_color"] = zone_colour
        # WHICH zone it paints, when the canvas named one on the Trigger's
        # own zone port. Absent, the paint falls back to the zone the rule
        # watches -- which is the common case and the reason this is
        # optional rather than required.
        painted = str(trig.get("zone_paint") or "").strip()[:64]
        if painted:
            out["zone_paint"] = painted
    # Carried for a config written before the split: `color_target` said
    # which of the two the single colour meant. Kept ONLY when there is no
    # zone_color of its own, so the runtime can still read an old trigger
    # exactly as it always did, and a re-save quietly replaces it.
    if colour and not zone_colour:
        target = str(trig.get("color_target") or "box").strip().lower()
        if target in COLOR_TARGETS and target != "box":
            out["color_target"] = target
    # A Trigger's Advanced output (payload, idle, hold, how often): absent on
    # every trigger written before it, and then nothing is added.
    from core.services import trigger_out, trigger_paint      # noqa: PLC0415
    out.update(trigger_out.clean_compiled(trig))
    out.update(trigger_paint.clean_compiled(trig, colour,    # which boxes,
                                            zone_colour))    # which camera
    return out


#: A hex colour, as the canvas writes it. Nothing else is accepted -- this
#: string is handed to a drawing call, and "red" or "rgb(255,0,0)" would be a
#: silent no-draw rather than an error somebody sees.
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


#: DEPRECATED, read-only: what a single Alert colour repainted, back when
#: there was one colour and a picker to aim it. A Trigger now carries
#: `box_color` and `zone_color` separately -- two questions, two answers --
#: and this is kept so a config written before the split still paints what it
#: always did until it is next saved.
COLOR_TARGETS = ("box", "zone", "both")


def box_colour(value):
    """`value` as #RRGGBB, or "" for the default."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = "#" + text
    return text.upper() if _HEX_RE.match(text) else ""


#: Plain-English forms of PRESENCE_OPS, for the default alert message.
PRESENCE_WORDS = {">": "more than", ">=": "at least", "==": "exactly",
                  "<": "fewer than", "<=": "at most"}


def action_prefix(zid, action):
    """The function-id stem for one zone action.

    A zone may now carry SEVERAL actions of the same type -- two violation
    rules with different limits and classes, say -- so ids include the action's
    own id. An action WITHOUT one keeps the historic `z_<zone>_*` form: those
    ids are baked into existing dashboards, alert history and hand-bound
    widgets, and silently renaming them would break every one of them.
    """
    aid = "".join(c if c.isalnum() else "_"
                  for c in str(action.get("id") or "").strip().lower()).strip("_")
    return f"z_{zid}_{aid[:20]}" if aid else f"z_{zid}"


def _count_classes(zone, action):
    """Which classes a Count action breaks down by, or [] for one total."""
    mode = str(action.get("count_mode") or "all").lower()
    if mode == "each":
        return [str(c) for c in (zone.get("labels") or [])]
    if mode == "selected":
        return [str(c) for c in (action.get("classes") or [])]
    return []


def zone_action_cards(zone, action):
    """The value cards one action publishes: [{label, key, kind}].

    Shared with the Dashboard seeder and the overview API so a card's label and
    its stat key are defined ONCE. Two copies of this mapping is how a card ends
    up bound to a key nothing publishes.
    """
    zid, zname = zone["id"], zone.get("name") or zone["id"]
    pre = action_prefix(zid, action)
    atype = action.get("type")
    is_line = zone.get("kind") == "line"
    if atype == "count":
        per_class = _count_classes(zone, action)
        if per_class:
            if is_line:
                return [{"label": f"{zname} - {c} net", "key": f"fn_{pre}_{_cslug(c)}_net",
                         "kind": "count"} for c in per_class]
            return [{"label": f"{zname} - {c} now", "key": f"fn_{pre}_{_cslug(c)}_count",
                     "kind": "count"} for c in per_class]
        if is_line:
            return [{"label": f"{zname} - in", "key": f"fn_{pre}_count_in", "kind": "count"},
                    {"label": f"{zname} - out", "key": f"fn_{pre}_count_out", "kind": "count"},
                    {"label": f"{zname} - net", "key": f"fn_{pre}_count_net", "kind": "total"}]
        return [{"label": f"{zname} - in zone now", "key": f"fn_{pre}_count_count", "kind": "count"},
                {"label": f"{zname} - total seen", "key": f"fn_{pre}_total_total", "kind": "total"}]
    if atype == "violation":
        # The live count of what is in there, which is what the rule now
        # measures -- `_viol_total` was the distinct counter it used to be.
        return [{"label": f"{zname} - violating now", "key": f"fn_{pre}_viol_count",
                 "kind": "violation"}]
    if atype == "cycle":
        mode = str(action.get("mode") or "timer")
        if mode == "span":
            return [{"label": f"{zname} - last cycle", "key": f"fn_{pre}_span_last_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - average cycle", "key": f"fn_{pre}_span_avg_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - cycles", "key": f"fn_{pre}_span_trips",
                     "kind": "total"}]
        if mode == "countdown":
            return [{"label": f"{zname} - time left", "key": f"fn_{pre}_cd_remaining_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - time used", "key": f"fn_{pre}_cd_used_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - arrivals", "key": f"fn_{pre}_cd_entries",
                     "kind": "total"}]
        if mode == "occupancy":
            return [{"label": f"{zname} - occupied for", "key": f"fn_{pre}_occ_occupied_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - last stretch", "key": f"fn_{pre}_occ_last_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - occupied total", "key": f"fn_{pre}_occ_total_s",
                     "kind": "seconds"},
                    {"label": f"{zname} - times occupied", "key": f"fn_{pre}_occ_sessions",
                     "kind": "total"}]
        return [{"label": f"{zname} - in zone for", "key": f"fn_{pre}_cycle_live_s",
                 "kind": "seconds"},
                {"label": f"{zname} - average stay", "key": f"fn_{pre}_cycle_avg_s",
                 "kind": "seconds"}]
    return []


def _cslug(name):
    return "".join(c if c.isalnum() else "_" for c in str(name).lower()).strip("_")[:20]


def apply_zone_actions(systems_dir, slug):
    """Compile every zone's saved default ACTIONS into [[functions]].

    The Zones page's no-code flow: draw a zone, pick classes, add actions
    (count / violation / cycle time), optionally an alert. Each action becomes
    ordinary function documents with ids z_<zone>_* -- the same engine the
    Workflow uses, so the values show up in analytics, formulas and dashboards
    automatically. The whole z_* namespace is regenerated on every zone save;
    everything else (wf_*, hand-made) is preserved untouched.

    Three things a zone can be asked for:
      * Count -- how many, on an AREA or a LINE, optionally broken down per
        class (one function per class; the existing class filter does the work,
        so this needs nothing new from the engine).
      * Violation -- a presence rule plus an alert. That is all it is: the
        occupancy number it used to emit as a side effect belongs to a Count
        action, where the operator asked for it.
      * Cycle time -- by `mode`: countdown (a time budget arrivals spend),
        dwell (measure stays), timer (alert past a limit, per object),
        stamps (log each object's entry/exit), occupancy (one clock for the
        place while anything is inside), or span (time between two places).
    """
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    zones = dc._zone_list(cfg)
    by_id = {z["id"]: z for z in zones}
    docs = []
    #: Rules belonging to a switched-off zone. They are still WRITTEN -- a
    #: dashboard bound to one keeps its binding, and re-enabling the zone is
    #: one tick rather than a rebuild -- but written switched off, so the
    #: engine skips them. On the canvas they appear unwired from the Detection
    #: Stream, which is the same fact drawn instead of written.
    off_ids = set()
    for z in zones:
        zid, zname = z["id"], z["name"]
        zone_on = bool(z.get("enabled", True))
        mark = len(docs)
        is_line = z.get("kind") == "line"
        classes = z.get("labels") or []
        base_params = {"line" if is_line else "zone": zid}
        if classes:
            base_params["classes"] = classes
        # The zone's camera comes with it. A zone action that did not say which
        # camera it belongs to was evaluated by the FIRST camera's engine --
        # which does not hold that zone, so the action read zero for ever.
        if z.get("camera"):
            base_params["camera"] = z["camera"]

        # The zone's own "violation" tick, which predates actions: everything
        # inside this area is a violation. The engine already MARKS those
        # detections (red overlay, violation events), but nothing counted them
        # per zone, so the tick had no rule and therefore nothing to show on
        # the Workflow canvas.
        #
        # It publishes HOW MANY ARE VIOLATING RIGHT NOW, not a running total.
        # The tick states a condition about the present -- "anything in here
        # is a violation" -- and a live number is what an operator watching a
        # floor needs; a total that only ever grows answers a different
        # question. Anyone who wants the total wires a Zone Counter onto
        # this zone in the Workflow, where the choice is visible.
        # A Violation ACTION says the same thing this tick says, with the
        # operator's own number and message on it. Emitting both produced TWO
        # alerting presence rules for one statement: two chains on the canvas,
        # two alerts per incident, and an operator left to work out which of
        # the pair to hang a card on. The action wins -- it is the one they
        # can edit -- and the tick is what SWITCHES IT ON (below).
        viol_actions = [] if is_line else [
            a for a in (z.get("actions") or []) if a.get("type") == "violation"]
        if z.get("violation") and not is_line and not viol_actions:
            # ...and it carries its own ALERT. The tick is a statement that
            # something in here matters, which is exactly what an alert is;
            # without one the Workflow canvas drew a counter wired to nothing
            # and the operator had no node on which to set the box colour or
            # hang a snapshot. The message and severity are editable on that
            # Alert node afterwards -- this is the starting point, not a
            # fixture.
            #
            # The app's own keep-out event is SUPPRESSED for a zone that has
            # this rule (see _trigger_zone_ids below and app/run.py): the same
            # incident logged twice, once as "object in keep-out zone" and
            # once as this alert, is one incident an operator has to reconcile
            # in the log.
            trig = z.get("trigger") if isinstance(z.get("trigger"), dict) else {}
            docs.append({"id": f"z_{zid}_zoneviol",
                         "name": f"{zname} violations now",
                         "type": "zone_count", "enabled": True,
                         "params": dict(base_params),
                         "_verdict": True,
                         "trigger": {
                             "metric": "count", "op": ">", "value": 0,
                             "severity": ("crit" if trig.get("severity") == "crit"
                                          else "warn"),
                             "message": str(trig.get("message")
                                            or f"object in {zname}")[:120],
                             "cooldown_s": 2}})

        for a in z.get("actions") or []:
            atype = a.get("type")
            pre = action_prefix(zid, a)
            alert = a.get("alert") or {}
            sev = "crit" if alert.get("severity") == "crit" else "warn"
            capture = {"snapshot": bool(alert.get("snapshot")),
                       "record": bool(alert.get("record"))}

            if atype == "count":
                per_class = _count_classes(z, a)
                if per_class:
                    for c in per_class:
                        docs.append({
                            "id": f"{pre}_{_cslug(c)}",
                            "name": f"{zname} {c}",
                            "type": "line_count" if is_line else "zone_count",
                            "enabled": True,
                            "params": {**base_params, "classes": [c]}})
                elif is_line:
                    docs.append({"id": f"{pre}_count", "name": f"{zname} crossings",
                                 "type": "line_count", "enabled": True,
                                 "params": dict(base_params)})
                else:
                    docs.append({"id": f"{pre}_count", "name": f"{zname} count",
                                 "type": "zone_count", "enabled": True,
                                 "params": dict(base_params)})
                    docs.append({"id": f"{pre}_total", "name": f"{zname} total",
                                 "type": "zone_unique", "enabled": True,
                                 "params": dict(base_params)})

            elif atype == "violation" and not is_line:
                limit = int(a.get("limit") or 0)
                op = str(a.get("op") or ">")
                # "It is a violation when the number of objects in the zone is
                # at least 1" is a statement about NOW, so it compiles to the
                # live count with that comparison as its trigger.
                #
                # It used to compile to the DISTINCT counter alerting on `new`,
                # which counted each offender once. That answers a different
                # question ("how many offenders today"), and it made the state
                # unwatchable: `new` is 1 for a single frame, so a Verdict card
                # on this rule blinked VIOLATION and went back to COMPLIANT
                # while the person was still standing in the zone. The live
                # count holds for as long as the violation does, which is what
                # a verdict, a box colour and a tower light all need. Counting
                # offenders is a Zone Counter in its distinct mode, where the
                # choice is visible.
                doc = {"id": f"{pre}_viol", "name": f"{zname} violations",
                       "type": "zone_count", "enabled": True,
                       "params": dict(base_params), "_verdict": True}
                # The zone's own tick means "anything in here matters", so a
                # rule carrying it alerts even when this action's alert box is
                # unticked -- otherwise ticking the zone and adding the action
                # that describes it would switch the alarm OFF.
                if alert.get("on") or z.get("violation"):
                    doc["trigger"] = {
                        "metric": "count", "op": op, "value": limit,
                        "severity": sev,
                        "message": alert.get("message")
                                   or f"{PRESENCE_WORDS.get(op, op)} {limit} in {zname}",
                        # A live condition HOLDS, so the cooldown is what
                        # stands between one incident and an event every
                        # couple of seconds for as long as somebody stands
                        # there. Entry still alerts at once -- the cooldown
                        # only paces the repeat while the same violation
                        # continues (app/functions.py _check_triggers).
                        "cooldown_s": 30}
                doc["_capture"] = capture
                docs.append(doc)

            elif atype == "cycle":
                mode = str(a.get("mode") or "timer").lower()
                if mode == "span":
                    start = str(a.get("start") or zid)
                    end = str(a.get("end") or "")
                    if not end or end == start:
                        continue        # an unfinished span compiles to nothing
                    doc = {"id": f"{pre}_span", "name": f"{zname} cycle",
                           "type": "zone_travel", "enabled": True,
                           "params": {
                               **({"classes": classes} if classes else {}),
                               # A span reads BOTH ends, so it only works when
                               # they are on the same camera -- which is this
                               # zone's, since that is where the action lives.
                               **({"camera": z["camera"]} if z.get("camera") else {}),
                               "start": start, "end": end,
                               "start_kind": (by_id.get(start) or {}).get("kind") or "area",
                               "end_kind": (by_id.get(end) or {}).get("kind") or "area"}}
                    max_s = a.get("max_s")
                    if alert.get("on") and max_s:
                        doc["trigger"] = {
                            "metric": "last_s", "op": ">", "value": float(max_s),
                            "severity": sev,
                            "message": alert.get("message")
                                       or f"cycle through {zname} longer than {float(max_s):g}s",
                            "cooldown_s": 30}
                    doc["_capture"] = capture
                    docs.append(doc)
                    continue
                if is_line:
                    continue            # a line has no inside to dwell in
                if mode == "countdown":
                    # A time budget: each arrival spends deduct_s of start_s.
                    doc = {"id": f"{pre}_cd", "name": f"{zname} countdown",
                           "type": "zone_countdown", "enabled": True,
                           "params": {**base_params,
                                      "start_s": float(a.get("start_s") or 60),
                                      "deduct_s": float(a.get("deduct_s", 1)),
                                      **({"reset": "never"}
                                         if a.get("reset") == "never" else {})}}
                    if alert.get("on"):
                        doc["trigger"] = {
                            "metric": "remaining_s", "op": "<=", "value": 0,
                            "severity": sev,
                            "message": alert.get("message")
                                       or f"time is up for {zname}",
                            "cooldown_s": 30}
                    doc["_capture"] = capture
                    docs.append(doc)
                    continue
                if mode == "occupancy":
                    # One clock for the place: runs while anything is inside.
                    doc = {"id": f"{pre}_occ", "name": f"{zname} occupancy",
                           "type": "zone_occupancy", "enabled": True,
                           "params": dict(base_params)}
                    max_s = a.get("max_s")
                    if alert.get("on") and max_s:
                        doc["trigger"] = {
                            "metric": "occupied_s", "op": ">",
                            "value": float(max_s), "severity": sev,
                            "message": alert.get("message")
                                       or f"{zname} occupied longer than {float(max_s):g}s",
                            "cooldown_s": 30}
                    doc["_capture"] = capture
                    docs.append(doc)
                    continue
                params = dict(base_params)
                if mode == "stamps":
                    params["stamp_events"] = True
                doc = {"id": f"{pre}_cycle", "name": f"{zname} cycle time",
                       "type": "zone_dwell", "enabled": True, "params": params}
                max_s = a.get("max_s")
                if mode == "timer" and alert.get("on") and max_s:
                    doc["trigger"] = {
                        "metric": "live_s", "op": ">", "value": float(max_s),
                        "severity": sev,
                        "message": alert.get("message")
                                   or f"object in {zname} longer than {float(max_s):g}s",
                        "cooldown_s": 30}
                doc["_capture"] = capture
                docs.append(doc)

        # Everything this zone just contributed is off if the zone is off.
        if not zone_on:
            for doc in docs[mark:]:
                doc["enabled"] = False
                off_ids.add(doc["id"])

    keep = [f for f in get_functions(systems_dir, slug)
            if not f["id"].startswith("z_")]
    clean = [validate_function(d) for d in docs]
    set_functions(systems_dir, slug, keep + clean)
    # Read BEFORE _apply_zone_outputs, which consumes the `_capture` marker:
    # the canvas mirror needs to know which actions asked for a photo or a
    # clip so it can draw those nodes too.
    captures = {d["id"]: tuple(k for k in ("snapshot", "record")
                               if (d.get("_capture") or {}).get(k))
                for d in docs if d.get("_capture") and d.get("trigger")}
    # Which rules are a VIOLATION -- a state worth showing as COMPLIANT /
    # VIOLATION rather than as a number. The canvas draws each one a Verdict
    # card, which is what every operator built by hand as the first thing they
    # did after ticking the box.
    verdicts = {d["id"] for d in docs if d.get("_verdict") and d.get("trigger")}
    _apply_zone_outputs(systems_dir, slug, docs)
    # Mirror the same actions onto the Workflow canvas, wired to the Detection
    # Stream. Two ways to build one thing used to meet only at migration, so
    # an action added later ran while the page that shows how numbers are made
    # showed nothing. Best effort and imported late: the canvas is a VIEW of
    # this, and failing to draw it must never fail the save that feeds it.
    try:
        from core.services import workflow as workflow_service  # noqa: PLC0415
        workflow_service.sync_zone_actions(slug, clean, zones, off_ids=off_ids,
                                           captures=captures, verdicts=verdicts)
    except Exception:                                       # noqa: BLE001
        pass
    return len(docs)


def _apply_zone_outputs(systems_dir, slug, docs):
    """Emit the capture outputs a zone's actions asked for.

    An action that alerts may also request a photo or a clip. Each becomes an
    ordinary [[outputs]] doc bound to that action's function, so it travels the
    same path a workflow Snapshot node does -- one firing mechanism, one set of
    System Control switches. Like the functions above, the whole z_* namespace
    is regenerated here; hand-made and workflow (wf_*) outputs are untouched.
    """
    wanted = []
    for doc in docs:
        capture = doc.pop("_capture", None)
        if not capture or not doc.get("trigger"):
            continue
        for kind in ("snapshot", "record"):
            if not capture.get(kind):
                continue
            out = {"id": f"{doc['id']}_{kind[:4]}", "type": kind,
                   "label": f"{doc['name']} -> {kind}"[:60],
                   "trigger": "alert", "function": doc["id"], "enabled": True,
                   # One capture per episode: an alert that repeats while the
                   # condition holds should leave one image, not hundreds.
                   "mode": "once", "interval_s": 5}
            if kind == "record":
                out["duration_s"] = 5
            wanted.append(out)
    keep = [o for o in dc.get_outputs(systems_dir, slug)
            if not str(o.get("id", "")).startswith("z_")]
    dc.set_outputs(systems_dir, slug, keep + wanted)


def get_functions(systems_dir, slug):
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    out = []
    for f in cfg.get("functions") or []:
        try:
            out.append(validate_function(f))
        except (ValueError, TypeError):
            continue
    return out


def set_functions(systems_dir, slug, functions):
    """Replace [[functions]] in config.local.toml. The running app re-parses
    them on the next config change (within ~2s)."""
    if not isinstance(functions, list):
        raise ValueError("functions must be a list")
    cleaned = [validate_function(f) for f in functions]
    ids = [f["id"] for f in cleaned]
    if len(ids) != len(set(ids)):
        raise ValueError("two functions share an id; each must be unique")
    # Read and write under one lock: a workflow recompile and a zone-action
    # save both land here, and two of them interleaving used to mean the
    # second write was based on the document from before the first.
    with dc.edit_local(dc._pkg_dir(systems_dir, slug)) as local:
        local["functions"] = cleaned
    return cleaned


def variables(systems_dir, slug):
    """Everything the editor's dropdowns need: the system's zones and lines,
    the classes the current detector can emit, and the stat-key vocabulary the
    expression builder offers."""
    cfg = dc._merged(dc._pkg_dir(systems_dir, slug))
    zones = []
    lines = []
    # Which camera a zone belongs to travels with it: a rule wired to a zone on
    # camera 2 must not look like it reads camera 1.
    for z in dc._zone_list(cfg):
        (lines if z["kind"] == "line" else zones).append(
            {"id": z["id"], "name": z["name"], "camera": z.get("camera") or ""})
    cams = [{"id": c["id"], "name": c.get("name") or c.get("label") or c["id"]}
            for c in dc._camera_list(cfg)]

    detector = cfg.get("detector") or {}

    # The classes on offer come from the LABELS FILE THE DETECTOR POINTS AT --
    # the same file the app loads (detector_control.model_labels reads it, and
    # is the one definition of "what can this model emit"). This used to look
    # the model up by NAME in the manifest store instead, so a model chosen
    # from the drop folder or the shared catalogue -- which has no manifest --
    # fell through to the COCO 80. The editor then offered person/car/... for a
    # board running a 1-class "pcb" model: every rule written against those
    # names could never match a detection.
    by_model = dc.model_labels_by_model(systems_dir, slug)

    # Several models on one camera: the vocabulary is the UNION of every
    # entry's classes (its own keep-filter, else its labels), and the per-model
    # map lets a picker scope itself to one Detection Stream's model.
    entries = [e for e in (detector.get("models") or []) if isinstance(e, dict)]
    if entries:
        classes_by_model = {}
        for e in entries:
            name = str(e.get("model") or "")
            if not name:
                continue
            classes_by_model[name] = ([str(c) for c in (e.get("classes") or [])]
                                      or list(by_model.get(name) or []))
        seen, union = set(), []
        for cls in classes_by_model.values():
            for c in cls:
                if c not in seen:
                    seen.add(c)
                    union.append(c)
        if union:
            # Per-entry keep-filters are what actually fires; the union of the
            # models' full labels is what may be picked.
            active, seen = [], set()
            for e in entries:
                for c in (e.get("classes") or by_model.get(str(e.get("model") or "")) or []):
                    if str(c) not in seen:
                        seen.add(str(c))
                        active.append(str(c))
            full, seen2 = [], set()
            for cls in by_model.values():
                for c in cls:
                    if c not in seen2:
                        seen2.add(c)
                        full.append(c)
            return {"zones": zones, "lines": lines, "cameras": cams,
                    "classes": full or union,
                    "classes_active": active or union,
                    "models": list(classes_by_model),
                    "classes_by_model": classes_by_model}

    # NO MODEL CHOSEN AT ALL: there is no vocabulary to offer. The COCO
    # fallback below exists for a chosen zoo model that ships no labels file
    # -- reaching it with no detector configured offered person/car/... on a
    # fresh system whose AI cannot emit anything, and every rule written
    # against those names was dead on arrival. Empty says the truth, and the
    # picker tells the operator where to choose a model.
    if not str(detector.get("model") or "").strip():
        return {"zones": zones, "lines": lines, "cameras": cams,
                "classes": [], "classes_active": [],
                "models": [], "classes_by_model": {}}

    # Two different questions, and answering only the second is what made the
    # editor look broken:
    #   classes        -- every class the MODEL can emit (the picker's whole
    #                     vocabulary). Offering only the filtered set meant a
    #                     board keeping "person" showed ONE class, so no rule
    #                     could even be written for another one.
    #   classes_active -- what the keep-filter currently lets through. A rule
    #                     on a class outside this fires never, so the compiler
    #                     warns instead of leaving it silently dead.
    keep = [str(c) for c in ((detector.get("hailo") or {}).get("classes")
                             or detector.get("classes") or [])]
    classes = dc.model_labels(systems_dir, slug)
    if not classes:
        # Only when the model ships no labels file at all -- which is exactly
        # the built-in Model Zoo YOLOs, and those ARE COCO.
        from core.services import models as models_service
        classes = keep or list(models_service.COCO_CLASSES)
    # A filter naming something outside the labels file still belongs in the
    # vocabulary: it is real to the detector, and hiding it would make the
    # rule using it unfixable.
    for c in keep:
        if c not in classes:
            classes.append(c)
    return {"zones": zones, "lines": lines, "cameras": cams,
            "classes": classes,
            "classes_active": keep or list(classes),
            "models": list(by_model), "classes_by_model": by_model}
