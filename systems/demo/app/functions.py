# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
functions.py -- user-defined analytics functions, built on the dashboard.

The Functions page lets an operator compose analytics without code: "average
dwell time of people in cell A", "forklift speed between gate 1 and gate 2",
"cars in minus cars out", "alert when zone count > 5". Each one is stored as a
[[functions]] table in config.local.toml -- the same hot-reload path zones and
outputs already use -- and interpreted here, every frame, next to ZoneEngine.

Function documents are DATA, never code. The `expr` type carries an arithmetic
expression as a string, parsed with ast.parse() against a strict whitelist
(numbers, stat names, + - * / %, comparisons, and/or/not, and the pure
function table _ALLOWED_CALLS: min/max/abs/round/ceil/floor/sqrt/pow/clamp/iff).
Nothing else parses; eval() is never called. Same doctrine as the dashboard
builder: layouts and rules are JSON/TOML, the engine is the only code.

Types and the stat keys they publish (all prefixed fn_<id>_):

    zone_count   count                        objects of the chosen classes in a zone
    zone_dwell   live_s, avg_s, max_s         how long tracked objects stay in a zone
    line_count   in, out, net                 crossings of one line, class-filtered
    line_travel  trips, last_s, last_speed,   line A -> line B travel per track,
                 avg_speed                    speed from a real-world distance
    zone_travel  trips, last_s, avg_s         cycle time between two places,
                                              each end a zone OR a line
    expr         value, active                arithmetic over any stat keys

zone_dwell also publishes live_s (the longest CURRENT stay) and, with
params.stamp_events, emits an event per completed stay carrying entered_at /
exited_at / duration_s.

Every function may carry a [functions.trigger] table: when its trigger metric
passes the threshold (rising edge, rate-limited by cooldown_s), the loop emits
an event -- which the event feed shows and GPIO/MQTT/Modbus outputs can act on.

This module imports nothing from core/ -- the app must run standalone on a
bare Pi (see run.py header).
"""

import ast
import calendar
import datetime
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request

from app import broker_login
from app import mqtt_bus
from app import extensions as extensions_mod
from app import faults
from app import zones as zones_mod

log = faults.logger(__name__)

TYPES = ("zone_count", "zone_dwell", "zone_unique", "line_count",
         "line_travel", "zone_travel", "proximity", "guard", "trails", "expr",
         "api_poll", "query_value", "mqtt_sub", "total", "custom",
         "frame_count", "frame_unique",
         "zone_countdown", "zone_occupancy", "object_detect", "board_input",
         "modbus_read", "heatmap", "schedule", "inject", "state_watch")

#: The function types that need no picture, and so run with AI detection off
#: (run.py ai_config) when no compiled [logic] list says more. Everything that counts
#: or times what a camera sees is left out there: with no detections it would
#: read zero, and a zero is a real value to an alarm ("the zone is empty").
#: "custom" is out too: a store template's evaluator is handed the frame.
LOGIC_TYPES = ("expr", "api_poll", "query_value", "mqtt_sub", "total",
               "board_input", "modbus_read", "schedule", "inject",
               "state_watch")

#: What an Inject's payload may be. Mirrors core/services/functions.py
#: INJECT_TYPES (duplicated on purpose -- the app imports nothing from core/).
INJECT_TYPES = ("string", "number", "boolean", "json", "timestamp")
#: Where the master drops a press of an Inject button, beside the schedule
#: file (core/services/detector_control.py writes it). {fn_id: {seq, ts}}.
INJECT_FILE = "inject-requests.json"
#: How often that file is looked at, and how old a press may be and still
#: fire. The age limit is what keeps a press from firing AGAIN when the app
#: restarts and finds it still sitting in the file.
INJECT_POLL_S = 0.25
INJECT_STALE_S = 30.0

#: Per-Inject state that outlives an engine: a workflow save rebuilds the
#: FunctionEngine, and a repeat that restarted its count on every save -- or
#: an "inject once at start" that fired again each time anything else on the
#: canvas changed -- would be a clock nobody could trust. Keyed by function
#: id; `sig` is the node's own settings, so changing THIS node re-arms it the
#: way a redeploy does, and changing its neighbours does not.
_inject_state = {}
_PROCESS_START = time.time()

#: The ON/OFF points (type state_watch, core/services/state_points.py): what
#: run.py last said about the states they watch -- {"ai": bool, "paused":
#: frozenset of Class Filter node ids} -- or None until it has said, when no
#: point publishes or fires (an engine in a test, or the moment before the
#: first config is applied, must not fire "OFF" for a state nobody read).
_states = None
#: Per point, outliving an engine like _inject_state: a workflow save
#: rebuilds the engine, and a point that fired again on every save would be
#: a Telegram per edit. {fn id: {"sig", "hit", "runs", "last_at"}}.
_state_watch = {}


#: Triggers whose published level a snapshot has just spent: stat key
#: (fn_<id>_active) -> held low until the rule behind it really releases. A
#: Trigger works its level out from its inputs every tick, so there is nothing
#: stored to reset; after its snap the level is simply reported as false while
#: the input is still true, and the hold ends the moment the input falls (see
#: _check_triggers). Only Triggers wired into a Snapshot Detection Stream are
#: ever put in here (app/snapgate.py), so no other Trigger is affected.
_snap_hold = set()


def hold_low(key):
    """Report this Trigger's level as false until its rule releases."""
    _snap_hold.add(str(key))


def reset_inject(fn_id):
    """Put an Inject's published value back to false/0, as it was before it
    fired. Called when the frames of the snapshot it started have been taken,
    so the snap stops and the Inject is ready for its next true. Nothing else
    about it changes: its run count, its repeat clock and its Start/Stop state
    are left alone."""
    st = _inject_state.get(str(fn_id))
    if not st:
        return False
    try:
        ptype = json.loads(st.get("sig") or "{}").get("ptype")
    except ValueError:
        ptype = None
    st["value"] = "" if ptype in ("string", "json") else 0
    return True


def set_states(ai, paused=()):
    """What the ON/OFF points read: whether AI detection runs (the switch as
    the app applies it) and which Class Filters the dashboard paused. Called
    by run.py at start and whenever it applies a config."""
    global _states
    _states = {"ai": bool(ai),
               "paused": frozenset(str(p) for p in (paused or ()))}


def inject_value(ptype, payload, now):
    """What one inject PUBLISHES: a number, 1/0, or text.

    A timestamp is taken at the moment it fires, in milliseconds since the
    epoch. JSON that is a number or true/false inside is that value, so a
    Compare can test it; anything else is its compact text, which is what an
    MQTT Publish or a Webhook sends on.
    """
    text = "" if payload is None else str(payload)
    if ptype == "timestamp":
        return int(now * 1000)
    if ptype == "number":
        try:
            num = float(text)
        except ValueError:
            return 0
        return int(num) if num == int(num) else num
    if ptype == "boolean":
        return 1 if text.strip().lower() in ("true", "1") else 0
    if ptype == "json":
        try:
            doc = json.loads(text)
        except ValueError:
            return ""
        if isinstance(doc, bool):
            return 1 if doc else 0
        if isinstance(doc, (int, float)):
            return doc
        return json.dumps(doc, separators=(",", ":"))
    return text

def _parse_out(raw):
    """A Trigger's Advanced output, or None. Mirrors core/services/
    trigger_out.py (the app imports nothing from core/): the payload sent
    when it fires (absent: 1, what a moment has always sent), the one sent
    when it is not firing (absent: 0), and how it is held: hold_s 0 is
    Real-time (on while the moment holds); N is Hold N (on N s, then off),
    or with "hold": "interval" Interval N (re-checked every N s, kept on
    while the trigger is still active and unchanged)."""
    if not isinstance(raw, dict):
        return None
    out = {"type": "", "value": "", "idle_type": "", "idle_value": "",
           "hold_s": 0.0, "interval": False}
    for tkey, vkey in (("type", "value"), ("idle_type", "idle_value")):
        if str(raw.get(tkey) or "") in INJECT_TYPES:
            out[tkey], out[vkey] = str(raw[tkey]), str(raw.get(vkey) or "")
    try:
        out["hold_s"] = max(0.0, float(raw.get("hold_s") or 0))
    except (TypeError, ValueError):
        pass
    out["interval"] = bool(out["hold_s"]) and raw.get("hold") == "interval"
    return out


def _parse_rate(trig):
    """How often a Trigger may fire: "" every time (as before), "cooldown",
    or "once_per_oid" -- which needs a rule naming who, and is dropped
    without one rather than left to mean nothing."""
    rate = str(trig.get("rate") or "").strip().lower()
    if rate == "cooldown" or (rate == "once_per_oid" and trig.get("oid_source")):
        return rate
    return ""


#: Which objects each Object Detection with OID has SEEN, and when last:
#: {(engine scope, function id): {track id: time}}. Module-level so it
#: outlives a FunctionEngine -- every workflow save rebuilds the engine, and
#: an arrival judged by the new engine alone re-announced every object in
#: view. Per CAMERA engine (the scope), because a rule on every camera runs
#: in each one and every camera's tracker numbers its objects from 1: keyed
#: by rule alone, object 1 arriving on camera 2 read as camera 1's object 1
#: standing still, and never wrote its row.
_objdet_seen = {}
#: An object must be out of view this long before it can "arrive" again: a
#: tracker losing a box for a frame or two is not a second visit.
ARRIVE_GAP_S = 1.0
#: How long a departed object is remembered at all (then it may arrive anew).
SEEN_KEEP_S = 120.0

#: What a Trigger's Advanced "how often" remembers (core/services/
#: trigger_out.py): {(engine scope, function id): {"sig", "last_fire",
#: "seen": {track id: last seen}}}. Module-level for the reason _objdet_seen
#: is -- a workflow save rebuilds the FunctionEngine, and "once per object"
#: that forgot everybody on every save would fire again for the whole room.
#: NOT kept on disk: the tracker numbers objects from 1 again when the app
#: starts, so yesterday's ids would silence today's new objects.
_trigger_memory = {}
#: A seen object is forgotten once it has been out of view this long, and a
#: Trigger remembers at most this many: bounded memory on a box that runs for
#: months. Track ids only ever count up while the app runs, so forgetting an
#: id long gone cannot let the same object fire twice.
TRIGGER_OID_KEEP_S = 600.0
TRIGGER_OID_MAX = 2000
#: A "new object" Trigger's grace: an announced object stays announced until
#: it has been out of the rule's view this long, and the condition must stay
#: let go this long before the slate is wiped. Without it a tracker that
#: misses one of two people for a single frame -- or a count that dips for a
#: frame -- made somebody "new" again, and the alert (and its Data Table
#: rows) repeated every second or so for the same two ids (pcb_sensata,
#: 2026-10-13). Gone longer than this is a real leave: new again.
NEW_OBJECT_GRACE_S = 10.0

#: How often a Schedule comes round. Mirrors core/services/functions.py
#: (duplicated on purpose -- the app imports nothing from core/).
SCHEDULE_EVERY = ("minutes", "hourly", "daily", "weekly", "monthly")
#: Where the last run of each Schedule is remembered, beside the day tallies.
#: Without it a box rebooted at 12:00 sends the noon report again on every
#: restart until 12:01, which is the failure mode a report scheduler is judged
#: on.
SCHEDULE_FILE = "fn_schedule.json"

#: Where the per-day tallies live, beside the event log. A count an operator
#: reads as "today" must survive an app restart -- an in-memory counter that
#: silently returns to zero when detection reloads is worse than no number.
DAILY_FILE = "fn_daily.json"
#: The running totals set to continue after a restart ("Total Value" with
#: "after a restart: continue from the last saved total"), flushed with the
#: tallies above: {fid: {"total": n, "day": yday}}.
TOTALS_FILE = "fn_totals.json"
#: Days of history kept per function (~4 months): enough for the month range
#: with room to spare, bounded so the file cannot grow without limit.
DAILY_KEEP_DAYS = 120
#: How often the tallies are flushed. Every frame would be a disk write 30
#: times a second for a number that changes a few times a minute.
DAILY_FLUSH_S = 10.0

#: An api_poll function may not fetch faster than this. The value is read by
#: the detection loop every frame from a cache, so a short interval buys
#: nothing but load on whatever machine is being polled.
MIN_POLL_S = 2.0
MAX_POLL_S = 3600.0
#: A query variable's floor. Every tick is the master running SQL on a box
#: that is also running inference -- a total one second old buys nothing and
#: costs a database round trip per second, forever.
MIN_QUERY_POLL_S = 10.0
#: A hung endpoint must not hold a thread for long -- there is one per function.
POLL_TIMEOUT_S = 4.0
#: Failures tolerated before the value is declared stale (fn_<id>_ok = 0). One
#: dropped request on a busy network should not flip an alert.
POLL_FAILS_STALE = 3

#: Trigger condition modes beyond the comparison ops. Edge modes (rising /
#: falling / changed) compare against the metric's PREVIOUS frame value;
#: hold_s makes any mode require the condition to stay true continuously.
TRIGGER_MODES = ("above", "at_least", "below", "at_most", "equals",
                 "not_equals", "is_true", "is_false", "rising", "falling",
                 "changed", "in_range", "out_of_range")
_MODE_OPS = {"above": ">", "at_least": ">=", "below": "<", "at_most": "<=",
             "equals": "==", "not_equals": "!="}

#: A travel time above this is a stale/reused track id, not a real trip. The
#: IoU tracker can hand an id to a new object; a sanity cap keeps one bad
#: match from publishing a nonsense speed.
TRAVEL_CAP_S = 600.0

#: m/s -> chosen display unit.
SPEED_UNITS = {"ms": 1.0, "kmh": 3.6, "mph": 2.236936}

def _overlap_frac(a, b):
    """Intersection of two boxes as a fraction of the SMALLER box's area.

    Relative to the smaller one on purpose: a small object fully inside a big
    guard bubble reads as 1.0 ("completely in"), which is what an operator
    means, where intersection-over-union would report a small number and hide
    a total encroachment.
    """
    ix = min(a[2], b[2]) - max(a[0], b[0])
    iy = min(a[3], b[3]) - max(a[1], b[1])
    if ix <= 0 or iy <= 0:
        return 0.0
    area_a = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    area_b = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return min(1.0, (ix * iy) / min(area_a, area_b))


def _safe_sqrt(v):
    return math.sqrt(v) if v >= 0 else 0.0


def _safe_pow(a, b):
    try:
        r = float(a) ** float(b)
        return r if math.isfinite(r) else 0.0
    except (OverflowError, ValueError, ZeroDivisionError):
        return 0.0


def _guard(fn, *bad):
    """Wrap a math function so a bad input returns 0.0 instead of raising.

    This library runs INSIDE the detection loop, once per frame. A domain
    error -- log(0), asin(2), a value that overflows -- must not stop a camera
    from being watched, so every function here is total: it returns a number
    for every input, and 0.0 is the honest "this does not exist" answer.
    """
    caught = bad or (ValueError, OverflowError, ZeroDivisionError, TypeError)

    def call(*args):
        try:
            out = fn(*(float(a) for a in args))
        except caught:                                      # noqa: BLE001
            return 0.0
        return out if isinstance(out, (int, float)) and math.isfinite(out) else 0.0
    return call


def _safe_log(v, base=None):
    """Natural log, or log to `base`. Zero and negatives have no logarithm;
    they answer 0.0 rather than raising."""
    try:
        v = float(v)
        if v <= 0:
            return 0.0
        if base is None:
            return math.log(v)
        base = float(base)
        if base <= 0 or base == 1:
            return 0.0
        return math.log(v, base)
    except (ValueError, OverflowError, ZeroDivisionError, TypeError):
        return 0.0


#: The formula function library. Everything is a PURE numeric function -- a
#: rule must not crash the loop, so each degrades to 0 on bad input rather
#: than raising. iff(cond, a, b) is the Excel-style IF; clamp pins a value
#: into [lo, hi]. The core-side validator mirrors this table by NAME.
#:
#: Kept deliberately to functions that take numbers and return a number: no
#: strings, no sequences, nothing that allocates on the size of its input. The
#: expression parser (compile_expr) is a closed AST whitelist, and this table
#: is the only thing it can CALL, so every addition here is an addition to the
#: sandbox's surface -- which is why they are all wrapped and total.
_ALLOWED_CALLS = {
    # -- arithmetic ---------------------------------------------------------
    "min": min, "max": max, "abs": abs, "round": round,
    "ceil": math.ceil, "floor": math.floor,
    "sqrt": _safe_sqrt, "pow": _safe_pow,
    "clamp": lambda v, lo, hi: min(max(v, min(lo, hi)), max(lo, hi)),
    "iff": lambda cond, a, b: a if cond else b,
    # A value AS TEXT: a word stays a word, 3 is "3", and 0 -- what an unset
    # variable reads as -- is "" (see eval_expr, which dispatches this one
    # itself so the word is not coerced to a number on the way in).
    "text": lambda v: v if isinstance(v, str) else ("" if not v else str(v)),
    "trunc": _guard(math.trunc),
    "sign": _guard(lambda v: (v > 0) - (v < 0)),
    "cbrt": _guard(lambda v: math.copysign(abs(v) ** (1 / 3), v)),
    "hypot": _guard(math.hypot),
    # -- logs and growth ----------------------------------------------------
    "log": _safe_log,                       # log(v) or log(v, base)
    "ln": _guard(math.log),
    "log2": _guard(math.log2),
    "log10": _guard(math.log10),
    "exp": _guard(math.exp),
    # -- trigonometry (radians; deg/rad convert) ----------------------------
    "sin": _guard(math.sin), "cos": _guard(math.cos), "tan": _guard(math.tan),
    "asin": _guard(math.asin), "acos": _guard(math.acos),
    "atan": _guard(math.atan), "atan2": _guard(math.atan2),
    "degrees": _guard(math.degrees), "radians": _guard(math.radians),
    # -- statistics over the operands ---------------------------------------
    "avg": _guard(lambda *v: sum(v) / len(v) if v else 0.0),
    "pct": _guard(lambda part, whole: (part / whole) * 100.0 if whole else 0.0),
    "lerp": _guard(lambda a, b, t: a + (b - a) * t),
    "scale": _guard(lambda v, lo, hi: (v - lo) / (hi - lo) if hi != lo else 0.0),
}

#: Constants a formula may name. Same closed-list doctrine as the calls: the
#: evaluator resolves an unknown name to 0, so pi had to be typed as 3.14159
#: and e was simply unavailable.
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

_ALLOWED_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b if b else 0.0,   # a rule must not crash the loop
    ast.Mod: lambda a, b: a % b if b else 0.0,
    ast.FloorDiv: lambda a, b: a // b if b else 0.0,
}

_ALLOWED_CMP = {
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}

_CMP_FUNCS = {">": _ALLOWED_CMP[ast.Gt], ">=": _ALLOWED_CMP[ast.GtE],
              "<": _ALLOWED_CMP[ast.Lt], "<=": _ALLOWED_CMP[ast.LtE],
              "==": _ALLOWED_CMP[ast.Eq], "!=": _ALLOWED_CMP[ast.NotEq]}


# ---------------------------------------------------------------------------
# safe expression evaluation
# ---------------------------------------------------------------------------

def compile_expr(text):
    """Parse an arithmetic expression, allowing only whitelisted node types.

    Returns the AST root. Raises ValueError with a readable message on
    anything outside the whitelist -- the API surfaces that message in the
    editor, so a user learns "attribute access is not allowed" instead of
    getting a silent zero.
    """
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty expression")
    if len(text) > 500:
        raise ValueError("expression too long (max 500 characters)")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc.msg}") from exc
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.Load,
                             ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd)):
            continue
        if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare)):
            continue
        if isinstance(node, (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
                             ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                             ast.FloorDiv)):
            continue
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or isinstance(node.value, (int, float)):
                continue
            # A TEXT constant: "in", "night shift". A variable may hold a
            # word (Set Variable, text kind), and a rule then needs to write
            # the word down to set it or to test it -- iff(c, "in", status),
            # status == "in". Short: a value, not a document.
            if isinstance(node.value, str) and len(node.value) <= TEXT_MAX:
                continue
            raise ValueError("only numbers and short text are allowed as constants")
        if isinstance(node, ast.Name):
            continue
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name)
                    and node.func.id in _ALLOWED_CALLS and not node.keywords):
                continue
            raise ValueError("only these functions are allowed: "
                             + ", ".join(sorted(_ALLOWED_CALLS)))
        raise ValueError(f"'{type(node).__name__}' is not allowed in expressions")
    return tree


#: The longest text a variable holds or a rule writes down. A value, not a
#: document: a status word, a shift name, a batch code.
TEXT_MAX = 120


def _num(v):
    """A value as a number: a word that is not one reads as 0, the way an
    unknown name always has."""
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _txt(v):
    """A value as text: 3.0 is "3", so status == 3 and status == "3" agree."""
    if isinstance(v, str):
        return v
    f = _num(v)
    return str(int(f)) if f == int(f) else repr(f)


def _truthy(v):
    """A word is true when it says something; a number when it is not 0."""
    return v != "" if isinstance(v, str) else bool(v)


def eval_expr(tree, variables):
    """Evaluate a compiled expression against a flat {name: value} dict.

    Values are numbers, or TEXT: a variable may hold a word (Set Variable,
    text kind) and a rule may write one down. Text is compared whole
    (status == "in"), picked by iff(), and joined by + ; every other
    operation reads a word as the number it is not, 0 -- the same way an
    unknown name always has. A rule returns a number, or the text it
    picked; never anything else.

    Unknown names read as 0 -- a rule written before its inputs exist (a line
    nobody crossed yet) must yield a number, not an error.
    """
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                return node.value[:TEXT_MAX]
            return float(node.value)
        if isinstance(node, ast.Name):
            # A named constant beats a variable of the same name: `pi` is pi,
            # not whatever a rule happened to publish under that key.
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            v = variables.get(node.id, 0)
            if isinstance(v, str):
                return v[:TEXT_MAX]
            try:
                return float(v or 0)
            except (TypeError, ValueError):
                return 0.0
        if isinstance(node, ast.UnaryOp):
            v = walk(node.operand)
            if isinstance(node.op, ast.USub):
                return -_num(v)
            if isinstance(node.op, ast.Not):
                return 0.0 if _truthy(v) else 1.0
            return _num(v)
        if isinstance(node, ast.BinOp):
            fn = _ALLOWED_OPS.get(type(node.op))
            if fn is None:
                return 0.0
            left, right = walk(node.left), walk(node.right)
            # Text + anything joins: "Bay " + 3 is "Bay 3". Every other
            # operation on a word is arithmetic on 0.
            if isinstance(node.op, ast.Add) and (isinstance(left, str) or isinstance(right, str)):
                return (_txt(left) + _txt(right))[:TEXT_MAX]
            out = fn(_num(left), _num(right))
            # Plain arithmetic overflows: 1e308 + 1e308 is inf, and inf is not
            # a number anything downstream can use. Worse, json.dump writes it
            # as `Infinity`, which is not valid JSON -- so one overflowing
            # formula would corrupt the state file every reader parses.
            return out if math.isfinite(out) else 0.0
        if isinstance(node, ast.BoolOp):
            vals = [_truthy(walk(v)) for v in node.values]
            if isinstance(node.op, ast.And):
                return 1.0 if all(vals) else 0.0
            return 1.0 if any(vals) else 0.0
        if isinstance(node, ast.Compare):
            left = walk(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = walk(comp)
                fn = _ALLOWED_CMP.get(type(op))
                if fn is None:
                    return 0.0
                # A word beside anything compares as text, whole and exact;
                # numbers compare as numbers.
                if isinstance(left, str) or isinstance(right, str):
                    ok = fn(_txt(left), _txt(right))
                else:
                    ok = fn(left, right)
                if not ok:
                    return 0.0
                left = right
            return 1.0
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            # The whitelist ran at compile time; here we just dispatch. A bad
            # arity or domain error falls to the outer try -> 0.0.
            fn = _ALLOWED_CALLS.get(node.func.id)
            if fn is None:
                return 0.0
            args = [walk(a) for a in node.args]
            if node.func.id == "iff":
                # The one call that hands a value THROUGH: what it picks may
                # be a word, and the word must come out a word.
                return args[1] if _truthy(args[0]) else args[2]
            if node.func.id == "text":
                v = args[0] if args else ""
                if isinstance(v, str):
                    return v
                return "" if _num(v) == 0 else _txt(v)
            return float(fn(*[_num(a) for a in args]))
        return 0.0

    try:
        out = walk(tree)
        if isinstance(out, str):
            return out[:TEXT_MAX]
        out = float(out)
    except Exception:                                       # noqa: BLE001
        return 0.0
    # Last guard: whatever happened inside, a rule returns a real number.
    return out if math.isfinite(out) else 0.0


# ---------------------------------------------------------------------------
# function model
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# API pollers.
#
# An api_poll function reads a number from an HTTP endpoint (a PLC gateway, an
# ERP count, a weather feed) and publishes it as a stat, so the workflow can
# compare it, alert on it and chart it exactly like a detection metric.
#
# The fetch CANNOT happen on the detection loop: one slow endpoint would stall
# every camera. So each function gets a background thread, and the loop only
# ever reads the last value it left behind.
#
# State is MODULE level on purpose. FunctionEngine is rebuilt from scratch on
# every config reload (a few seconds apart), and a poller rebuilt with it would
# restart its interval forever and never actually fetch.
# ---------------------------------------------------------------------------
_api_lock = threading.Lock()
_api_state = {}          # fn id -> {value, ok, sig, stop, thread, fails}


def _api_sig(fn):
    """What makes a poller different. A change here restarts the thread; a
    rename or a re-wired downstream node must not."""
    p = fn.params
    return (str(p.get("url") or ""), float(p.get("interval_s") or MIN_POLL_S),
            str(p.get("json_path") or ""), str(p.get("token") or ""),
            json.dumps(p.get("headers") or [], sort_keys=True))


def _dig(payload, path):
    """Walk a dotted path into decoded JSON. `""` means the whole document.

    Digits index into lists, so `readings.0.value` works. Anything the path
    does not reach raises -- the caller keeps the previous value rather than
    publishing a zero that looks like a real reading.
    """
    cur = payload
    for part in [s for s in str(path or "").split(".") if s]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _as_number(value):
    """Coerce a JSON value to a float the workflow can compare.

    Booleans become 1/0 (an endpoint reporting `{"running": true}` is exactly
    the boolean case the When node handles), and a list becomes its length --
    "how many alarms are open" is the common shape.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple, dict)):
        return float(len(value))
    return float(str(value).strip())


def _mqtt_number(value):
    """_as_number for an MQTT message, with one difference: an object or a
    list is NOT a number.

    An API value's list becoming its length is a feature ("how many alarms
    are open"). On MQTT it was a trap (Yaya, review of 18c4480): a JSON
    message reaching a Subscribe with no value path -- a test publish, a
    status blob -- read as the number of its fields, 3, and could fire a
    "> 2 then Email" rule. So a message that is not a number is no reading:
    the previous value is kept and `ok` drops, exactly like unreadable text.
    """
    if isinstance(value, (list, tuple, dict)):
        raise ValueError("a JSON object or list is not a number - set the "
                         "value path to the field that holds it")
    return _as_number(value)


def _oid_same(stored, oid):
    """Is `oid` (a live track id) the object id a Data Table stored?

    A table records ids RUN-SCOPED ("k7m2-3", app/outputs.py _track_id) and
    the tracker hands out the bare 3, so both spellings are the same object
    -- this run's prefix only: yesterday's 3 is somebody else. A bare stored
    id and a number column ("3.0") match by value.

    A table set to one row for the whole group stores "k7m2-4, k7m2-5"
    (outputs.GROUP_SEP): that cell holds EACH of its ids, so any one of them
    matches."""
    text = "" if stored is None else str(stored).strip()
    if not text or oid is None:
        return False
    if "," in text:
        return any(_oid_same(part, oid) for part in text.split(",")
                   if part.strip())
    bare = str(oid).strip()
    if text == bare:
        return True
    try:
        from app import outputs                           # noqa: PLC0415
        if text == f"{outputs._RUN_ID}-{bare}":
            return True
    except Exception:                                     # noqa: BLE001
        pass
    try:
        return float(text) == float(bare)
    except ValueError:
        return False


def _api_request(p):
    """GET a user-given URL for an API value. Through netsafe.request, like a
    webhook: never this box itself, the checked address only, no redirects
    (Pipi M-R1)."""
    try:
        from . import netsafe                               # noqa: PLC0415
    except ImportError:                                     # a loose script
        import netsafe                                      # noqa: PLC0415
    url = str(p.get("url") or "")
    headers = {}
    for row in (p.get("headers") or []):
        name = str((row or {}).get("name") or "").strip()
        if name and name.lower() not in ("host", "content-length", "transfer-encoding"):
            headers[name] = str((row or {}).get("value") or "")
    token = str(p.get("token") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    _status, body = netsafe.request(url, method="GET", headers=headers,
                                    timeout=POLL_TIMEOUT_S, max_bytes=1_000_000)
    return json.loads(body.decode("utf-8", "replace"))


def _poll_loop(fn_id, params, sig, stop):
    interval = min(MAX_POLL_S, max(MIN_POLL_S, float(params.get("interval_s") or MIN_POLL_S)))
    while not stop.is_set():
        try:
            value = _dig(_api_request(params), params.get("json_path"))
            if params.get("text"):
                # A Trigger's Current OID: the stored word, as it is. No
                # answer is no reading, the same as an unreadable number.
                if value is None:
                    raise ValueError("no value in the answer")
                value = str(value)
            else:
                value = _as_number(value)
            with _api_lock:
                st = _api_state.get(fn_id)
                if st is not None and st["sig"] == sig:
                    st.update(value=value, ok=True, fails=0)
        except Exception as exc:                            # noqa: BLE001
            with _api_lock:
                st = _api_state.get(fn_id)
                if st is not None and st["sig"] == sig:
                    st["fails"] += 1
                    if st["fails"] == 1 or st["fails"] % 20 == 0:
                        log.warning("api_poll %s: %s", fn_id, exc)
                    if st["fails"] >= POLL_FAILS_STALE:
                        # Keep the last value -- a gap in the network is not a
                        # reading of zero -- but say it is no longer fresh.
                        st["ok"] = False
        stop.wait(interval)


def _ensure_poller(fn, now):
    """Start this function's poller, or restart it if its settings changed.

    Also stamps it as still-wanted, which is what keeps the reaper away.
    """
    sig = _api_sig(fn)
    with _api_lock:
        st = _api_state.get(fn.id)
        if st is not None and st["sig"] == sig and st["thread"].is_alive():
            st["seen"] = now
            return
        if st is not None:
            st["stop"].set()
        stop = threading.Event()
        fresh = {"value": ("" if fn.params.get("text")
                           else float(fn.params.get("fallback") or 0)),
                 "ok": False,
                 "sig": sig, "stop": stop, "fails": 0, "thread": None, "seen": now}
        thread = faults.spawn(f"api-{fn.id}", _poll_loop,
                              (fn.id, dict(fn.params), sig, stop))
        fresh["thread"] = thread
        _api_state[fn.id] = fresh
    thread.start()


# ---------------------------------------------------------------------------
# MQTT values: the broker pushes, we hold the last reading
# ---------------------------------------------------------------------------
#
# The mirror image of api_poll. An API value asks every few seconds; a broker
# sends when something changes, so there is nothing to schedule -- a
# subscriber thread holds the newest payload and the detection loop reads it,
# exactly as it reads a polled one. Neither ever blocks the loop.

_mqtt_lock = threading.Lock()
#: How much of the last raw MQTT payload is published as a stat, for Debug.
MQTT_PAYLOAD_KEPT = 200
_mqtt_state = {}         # fn id -> {value, ok, sig, stop, client, seen, msgs}


def _mqtt_sig(fn):
    """What makes a subscription different. A rename must not reconnect."""
    p = fn.params
    if p.get("conn"):
        # On a shared MQTT Connection: the broker and its login are the
        # Connection's business (app/mqtt_bus.py moves this subscription to
        # a new client when they change), so only what THIS node asks for.
        return ("conn", str(p["conn"]), str(p.get("topic") or ""),
                str(p.get("json_path") or ""))
    return (str(p.get("host") or ""), int(p.get("port") or 1883),
            str(p.get("topic") or ""), str(p.get("json_path") or ""),
            str(p.get("username") or ""), str(p.get("password") or ""),
            # This box's broker login (Settings > MQTT Broker): a new password
            # there reconnects the subscriber instead of leaving it refused.
            broker_login.for_address(p.get("host"), p.get("port")))


def _mqtt_handle(fn_id, sig, params, topic, payload_bytes):
    """One message for MQTT Subscribe `fn_id`, from its own client or from a
    shared MQTT Connection -- read the same way either way."""
    # What ARRIVED, kept whether or not it could be read: a JSON path that
    # misses is exactly when somebody needs to see the raw message (the
    # Debug node shows these three beside the value).
    raw = bytes(payload_bytes or b"").decode("utf-8", "replace")
    seen = {"payload": raw[:MQTT_PAYLOAD_KEPT], "topic": str(topic)[:200],
            "at": round(time.time(), 3)}
    try:
        text = raw.strip()
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        value = _mqtt_number(_dig(payload, params.get("json_path")))
    except Exception as exc:                                  # noqa: BLE001
        with _mqtt_lock:
            st = _mqtt_state.get(fn_id)
            if st is not None and st["sig"] == sig:
                st.update(ok=False, **seen)
        log.warning("mqtt_sub %s: unreadable payload: %s", fn_id, exc)
        return
    with _mqtt_lock:
        st = _mqtt_state.get(fn_id)
        if st is not None and st["sig"] == sig:
            st.update(value=value, ok=True, msgs=st.get("msgs", 0) + 1, **seen)


def _mqtt_loop(fn_id, params, sig, stop):
    try:
        import paho.mqtt.client as mqtt                       # noqa: PLC0415
    except ImportError:
        with _mqtt_lock:
            st = _mqtt_state.get(fn_id)
            if st is not None and st["sig"] == sig:
                st["ok"] = False
        log.warning("mqtt_sub %s: paho-mqtt is not installed", fn_id)
        return

    def on_message(_client, _userdata, msg):
        _mqtt_handle(fn_id, sig, params, msg.topic, msg.payload)

    def on_connect(client, _userdata, _flags, rc, *_):
        if rc == 0:
            client.subscribe(params.get("topic") or "#")
        else:
            log.warning("mqtt_sub %s: broker refused the connection (rc=%s)",
                        fn_id, rc)

    client = mqtt.Client()
    # A login typed on the node wins; otherwise this box's own broker's, when
    # that is where the node points (app/broker_login.py).
    login = ((params["username"], params.get("password") or None)
             if params.get("username")
             else broker_login.for_address(params.get("host"), params.get("port")))
    if login:
        client.username_pw_set(*login)
    client.on_connect = on_connect
    client.on_message = on_message
    with _mqtt_lock:
        st = _mqtt_state.get(fn_id)
        if st is not None and st["sig"] == sig:
            st["client"] = client

    while not stop.is_set():
        try:
            client.connect(params.get("host") or "127.0.0.1",
                           int(params.get("port") or 1883), keepalive=30)
            # loop_forever handles its own reconnects; it returns when the
            # client is disconnected, which is how stop() gets us out.
            client.loop_forever(retry_first_connection=False)
        except Exception as exc:                              # noqa: BLE001
            with _mqtt_lock:
                st = _mqtt_state.get(fn_id)
                if st is not None and st["sig"] == sig:
                    st["ok"] = False
                    st["fails"] = st.get("fails", 0) + 1
                    if st["fails"] == 1 or st["fails"] % 20 == 0:
                        log.warning("mqtt_sub %s: %s", fn_id, exc)
        # A broker that is down must not become a reconnect storm.
        stop.wait(5.0)


#: How long an MQTT Subscribe naming a Connection that does not exist waits
#: before looking again (the Connection may be being added right now).
MQTT_CONN_RETRY_S = 10.0


def _drop_subscriber(st):
    """Tear one subscriber down, whichever kind it is."""
    st["stop"].set()
    try:
        if st.get("client"):
            st["client"].disconnect()
    except Exception:                                         # noqa: BLE001
        pass
    if st.get("handle"):
        mqtt_bus.unsubscribe(st["handle"])


def _ensure_bus_subscriber(fn, now, sig):
    """An MQTT Subscribe on a shared MQTT Connection: its topic rides on the
    Connection's one client (app/mqtt_bus.py) instead of a client of its own."""
    with _mqtt_lock:
        st = _mqtt_state.get(fn.id)
        if st is not None and st["sig"] == sig and (
                st.get("handle") is not None or now < st.get("retry_at", 0)):
            st["seen"] = now
            return
        fresh = {"value": float(fn.params.get("fallback") or 0), "ok": False,
                 "sig": sig, "stop": threading.Event(), "fails": 0, "msgs": 0,
                 "client": None, "thread": None, "handle": None, "seen": now}
        _mqtt_state[fn.id] = fresh
    if st is not None:
        _drop_subscriber(st)
    params = dict(fn.params)
    fn_id = fn.id
    try:
        handle = mqtt_bus.subscribe(
            params["conn"], params.get("topic") or "#",
            lambda topic, payload: _mqtt_handle(fn_id, sig, params, topic, payload))
    except Exception as exc:                                  # noqa: BLE001
        fresh["retry_at"] = now + MQTT_CONN_RETRY_S
        log.warning("mqtt_sub %s: %s", fn_id, exc)
        return
    with _mqtt_lock:
        if _mqtt_state.get(fn_id) is fresh:
            fresh["handle"] = handle
            return
    mqtt_bus.unsubscribe(handle)          # replaced while subscribing


def _ensure_subscriber(fn, now):
    """Start this function's subscriber, or restart it if its settings
    changed. Also stamps it still-wanted, which keeps the reaper away."""
    sig = _mqtt_sig(fn)
    if fn.params.get("conn"):
        return _ensure_bus_subscriber(fn, now, sig)
    with _mqtt_lock:
        st = _mqtt_state.get(fn.id)
        if st is not None and st["sig"] == sig and st.get("thread") is not None \
                and st["thread"].is_alive():
            st["seen"] = now
            return
        if st is not None:
            _drop_subscriber(st)
        stop = threading.Event()
        fresh = {"value": float(fn.params.get("fallback") or 0), "ok": False,
                 "sig": sig, "stop": stop, "fails": 0, "msgs": 0,
                 "client": None, "thread": None, "seen": now}
        thread = faults.spawn(f"mqtt-{fn.id}", _mqtt_loop,
                              (fn.id, dict(fn.params), sig, stop))
        fresh["thread"] = thread
        _mqtt_state[fn.id] = fresh
    thread.start()


def _reap_subscribers(now):
    """Stop subscribers no engine claimed recently -- their rule was deleted."""
    with _mqtt_lock:
        stale = [k for k, v in _mqtt_state.items()
                 if now - v["seen"] > POLLER_TTL_S]
        gone = [_mqtt_state.pop(fn_id) for fn_id in stale]
    for st in gone:
        _drop_subscriber(st)


#: The LIST a custom (Python) function returned under a stat key, beside the
#: number published for it: {stat key: [floats]}. Module level because the
#: reader (outputs.py, Modbus Write) lives in the same process but not in the
#: engine, and the stats dict it is handed is numbers only by contract.
_list_stats = {}


def list_stat(key):
    """The list behind a stat key, or None when that key carries a number."""
    got = _list_stats.get(str(key or ""))
    return list(got) if got is not None else None


#: How many times each rule has fired since the app started, plus each
#: Interval re-check that kept a Trigger's payload on: {function id: n}. An
#: MQTT Publish after a Trigger publishes when this moves even if the value
#: is the same as last time (outputs.py _publish_follow; Tok Aba 2026-10-08:
#: the Trigger, not the Publish, says how often). Module level for the same
#: reason as _list_stats: the reader is in this process, not in the engine.
_fire_beats = {}


def fire_beat(fid):
    """The beat count for rule `fid` (0 before its first fire)."""
    return _fire_beats.get(str(fid or ""), 0)


def _beat(fid):
    fid = str(fid or "")
    if fid:
        _fire_beats[fid] = _fire_beats.get(fid, 0) + 1


#: How long a poller may go unclaimed before it is stopped. Generous, because
#: the cost of reaping too early (a live function's thread killed) is far worse
#: than the cost of reaping late (one extra request to an endpoint).
POLLER_TTL_S = 30.0


# ---------------------------------------------------------------------------
# Modbus (RS485) pollers.
#
# Same shape as the API pollers above -- a background thread per function, the
# loop only reads the last answer -- with one bus-shaped difference: RS485 is
# ONE WIRE. Two transactions at once corrupt each other at the electrical
# level, so every poller takes the per-device lock and the bus is sequential
# whatever the canvas looks like. "Parallel" readers simply take turns. The
# lock is buses.bus_lock, the SAME one the Modbus Write worker (outputs.py)
# takes, so a write can never land in the middle of a read either.
#
# Where the reader talks is the node's Modbus Server (params.server, a named
# connection shared with the Write nodes -- TCP or serial). A node saved before
# servers existed has none and keeps the old rule: the board's own RS485 port,
# /dev/ttyACM0 by default (the CP2102-style adapter the IRIV ships), at the
# node's baud rate.
# ---------------------------------------------------------------------------
_modbus_lock = threading.Lock()          # the state table below
_modbus_state = {}                       # fn id -> {values, ok, sig, stop, ...}
_FC_KIND = {1: "coil", 2: "discrete", 3: "holding", 4: "input"}


def rs485_device():
    """The serial device the RS485 port answers on.

    First /dev/ttyACM* present, else /dev/ttyACM0 -- resolved at each poll so
    replugging the adapter heals without a restart.
    """
    import glob as _glob                                    # noqa: PLC0415
    found = sorted(_glob.glob("/dev/ttyACM*"))
    return found[0] if found else "/dev/ttyACM0"


def _bus_lock(device):
    from app import buses as _buses                         # noqa: PLC0415
    return _buses.bus_lock(device)


def _modbus_sig(fn):
    p = fn.params
    return (int(p.get("fc") or 3), int(p.get("unit") or 1),
            int(p.get("address") or 0), int(p.get("quantity") or 1),
            int(p.get("baudrate") or 9600),
            _read_every(p),
            str(p.get("server") or ""),
            # A triggered reader (interval 0) re-spawns when the thing it
            # waits for changes, same as it would for a new address.
            json.dumps(p.get("trigger") or {}, sort_keys=True))


def _read_every(params):
    """The poll interval, or 0 for a reader that waits on its trigger.

    `or MIN_POLL_S` would turn the explicit 0 into a 2 s clock -- the
    opposite of what the node was set to -- so only a MISSING interval takes
    the default.
    """
    raw = params.get("interval_s")
    if raw in (None, ""):
        return MIN_POLL_S
    try:
        every = float(raw)
    except (TypeError, ValueError):
        return MIN_POLL_S
    if every <= 0 and params.get("trigger"):
        return 0.0
    return min(MAX_POLL_S, max(MIN_POLL_S, every))


def _modbus_target(params):
    """(config, bus key, where) for one read, or (None, None, why) when the
    node's server no longer exists. Resolved per poll, so an edited server
    takes effect on the next read with no node re-saved."""
    from app import buses as _buses                         # noqa: PLC0415

    server = str(params.get("server") or "").strip()
    if server:
        conn = _buses.client(server)
        if conn is None:
            return None, None, f"Modbus server {server} (not found)"
        cfg = _buses.client_config(conn, params.get("unit"))
        return cfg, _buses.bus_key(cfg), _buses.bus_key(cfg)
    device = rs485_device()
    cfg = {"mode": "rtu", "device": device,
           "baudrate": int(params.get("baudrate") or 9600),
           "unit": int(params.get("unit") or 1)}
    return cfg, device, device


def _modbus_loop(fn_id, params, sig, stop, kick=None):
    from app import buses as _buses                         # noqa: PLC0415

    interval = _read_every(params)
    # Interval 0: ONE read per kick (client UAT 2026-10-04, item 5). The
    # engine sets `kick` on the rising edge of the trigger wired into the
    # node (_kick_modbus); between kicks the loop sleeps on the event, and
    # wakes once a second only to notice a stop.
    kick = kick if kick is not None else threading.Event()
    fc = int(params.get("fc") or 3)
    # The loop never gives up on a device that does not answer: it keeps
    # polling until `stop` (its node re-saved, deleted or unclaimed). What it
    # does do is BACK OFF -- a failed read costs the device's whole timeout
    # under the bus lock, with the Modbus Write worker waiting behind it, so
    # a silent unit is asked at the interval, then 2x, 4x ... up to
    # MODBUS_BACKOFF_MAX_S, and at the interval again from the first answer.
    fails = 0
    while not stop.is_set():
        if interval <= 0:
            if not kick.wait(1.0):
                continue
            kick.clear()
        cfg, key, where = _modbus_target(params)
        rows = None
        if cfg is not None:
            with _bus_lock(key):
                rows = _buses.modbus_read(cfg, int(params.get("address") or 0),
                                          int(params.get("quantity") or 1),
                                          _FC_KIND.get(fc, "holding"))
        fails = fails + 1 if rows is None else 0
        with _modbus_lock:
            st = _modbus_state.get(fn_id)
            if st is not None and st["sig"] == sig:
                if rows is None:
                    st["fails"] += 1
                    if st["fails"] == 1 or st["fails"] % 20 == 0:
                        log.warning("modbus_read %s: no answer from unit %s "
                                    "on %s", fn_id,
                                    (cfg or {}).get("unit", "?"), where)
                    if st["fails"] >= POLL_FAILS_STALE:
                        # Keep the last readings -- a bus hiccup is not a
                        # reading of zero -- but say they are stale.
                        st["ok"] = False
                else:
                    st.update(values=[float(v) for v in rows], ok=True, fails=0)
        if interval > 0:
            stop.wait(modbus_backoff(interval, fails))


#: The longest a Modbus Read waits between two polls of a unit that does not
#: answer. Node-RED's reader keeps its interval regardless; 30 s is the
#: ceiling so a device that comes back is seen within half a minute.
MODBUS_BACKOFF_MAX_S = 30.0


def modbus_backoff(interval, fails):
    """Seconds to the next poll after `fails` misses in a row: the interval
    itself up to POLL_FAILS_STALE misses (a hiccup), doubling from there."""
    if fails < POLL_FAILS_STALE:
        return interval
    return min(MODBUS_BACKOFF_MAX_S,
               max(interval, interval * 2 ** (fails - POLL_FAILS_STALE + 1)))


def _ensure_modbus(fn, now):
    sig = _modbus_sig(fn)
    with _modbus_lock:
        st = _modbus_state.get(fn.id)
        if st is not None and st["sig"] == sig and st["thread"].is_alive():
            st["seen"] = now
            return
        if st is not None:
            st["stop"].set()
        stop = threading.Event()
        # `kick` wakes a triggered reader (interval 0); `trig_high` is the
        # last level of its trigger, kept HERE rather than per engine because
        # there is one engine per camera and one press must mean one read.
        fresh = {"values": [], "ok": False, "sig": sig, "stop": stop,
                 "fails": 0, "thread": None, "seen": now,
                 "kick": threading.Event(), "trig_high": False}
        # Through faults.spawn: this loop had no top-level guard and no
        # name, so a raise inside it left the Modbus readings frozen at their
        # last value with the page still drawing them as live -- and nothing
        # anywhere said the reader had died. A raise is still logged as
        # "stopped" and the next tick here respawns it (thread not alive).
        # once=True: the loop RETURNS only when its stop event is set -- the
        # node re-saved, unclaimed for POLLER_TTL_S, or an engine rebuild --
        # and a new loop takes over at once, so the "returned -- that feature
        # has stopped until the app is restarted" line it used to log on
        # every such hand-over (asri_test_4, 2026-10-07) was false.
        thread = faults.spawn(f"modbus-{fn.id}", _modbus_loop,
                              (fn.id, dict(fn.params), sig, stop, fresh["kick"]),
                              once=True)
        fresh["thread"] = thread
        _modbus_state[fn.id] = fresh
        thread.start()


def _reap_modbus(now):
    with _modbus_lock:
        for fn_id in [k for k, v in _modbus_state.items()
                      if now - v["seen"] > POLLER_TTL_S]:
            _modbus_state.pop(fn_id)["stop"].set()


def _reap_pollers(now):
    """Stop pollers no engine claimed recently -- their function was deleted.

    Claim-based rather than list-based: there is one FunctionEngine PER CAMERA
    and only the first camera's engine owns api_poll functions, so an engine
    reaping every id it does not personally hold would kill the pollers of the
    engine next door.
    """
    with _api_lock:
        for fn_id in [k for k, v in _api_state.items() if now - v["seen"] > POLLER_TTL_S]:
            _api_state.pop(fn_id)["stop"].set()


# -- the clock ---------------------------------------------------------------
# A Schedule is read against the BOX's local time, which is the clock the
# operator set and the one every other time on the product means. All of it is
# arithmetic on the current time: nothing is precomputed at load, so a schedule
# survives a clock correction, a timezone change and daylight saving without a
# reload -- the next moment is simply worked out again from where "now" landed.

#: How late a scheduled moment may be noticed and still count as due now. Two
#: minutes covers a slow tick, a config reload or a short app restart; past
#: that the moment was missed while the box was down, which is a different
#: question (params.catch_up decides it).
LATE_GRACE_S = 120.0


def _hhmm_parts(text):
    """(hour, minute) from "HH:MM", or None. Bare "9" reads as 09:00."""
    raw = str(text or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return None
    return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def _within_window(params, when):
    """May a schedule run at `when` (epoch seconds)?

    The "and only then" half of a clock: a daily window, which weekdays, an
    absolute date range. All optional, all skipped when blank. These used to
    live on the Email and Telegram nodes -- one copy per output, so three
    messages off one rule meant the same window typed three times -- and they
    belong to the node that already answers "when".

    Every check FAILS OPEN: a value that does not parse is ignored rather than
    read as "never", because the failure mode of a bad window is a silently
    muted report, and that is discovered by the thing it was watching for
    happening unannounced.
    """
    t = time.localtime(when)

    # ISO dates compare correctly as text, which is why they are stored that
    # way -- no parsing, no timezone, no ambiguity. Both ends included.
    today = time.strftime("%Y-%m-%d", t)
    lo_date = str(params.get("from_date") or "").strip()
    hi_date = str(params.get("to_date") or "").strip()
    if len(lo_date) == 10 and today < lo_date:
        return False
    if len(hi_date) == 10 and today > hi_date:
        return False

    days = str(params.get("days") or "").strip().lower()
    if days:
        names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        want = {d.strip()[:3] for d in days.split(",") if d.strip()}
        if want and names[t.tm_wday] not in want:
            return False

    lo = _hhmm_minutes(params.get("hours_from"))
    hi = _hhmm_minutes(params.get("hours_to"))
    # lo == hi is not an empty window, it is an unset one: a window from 09:00
    # to 09:00 says nothing anybody meant, and reading it as "never" would
    # mute the schedule on a typo.
    if lo is not None and hi is not None and lo != hi:
        minute = t.tm_hour * 60 + t.tm_min
        # A window that crosses midnight MEANS it -- 22:00 to 06:00 is the
        # night shift, not the empty set.
        inside = (lo <= minute < hi) if lo < hi else (minute >= lo or minute < hi)
        if not inside:
            return False
    return True


def _hhmm_minutes(text):
    """"HH:MM" as minutes past midnight, or None for anything else."""
    parts = str(text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _schedule_bounds(params, now):
    """(most recent scheduled moment at or before `now`, the next one after).

    Both epoch seconds, local clock. A monthly schedule set for a day the month
    does not have runs on that month's LAST day -- "the 31st" in February means
    the end of February, not a report that silently never goes out.
    """
    p = params or {}
    every = str(p.get("every") or "daily").strip().lower()
    lt = datetime.datetime.fromtimestamp(now)
    day = datetime.timedelta(days=1)

    if every == "minutes":
        try:
            step = int(p.get("interval_min") or 15)
        except (TypeError, ValueError):
            step = 15
        step = max(1, min(1440, step)) * 60
        # Counted from local midnight, so "every 15 minutes" lands on :00 :15
        # :30 :45 rather than on whatever minute the app happened to start.
        midnight = lt.replace(hour=0, minute=0, second=0,
                              microsecond=0).timestamp()
        prev = midnight + math.floor(max(0.0, now - midnight) / step) * step
        return prev, prev + step

    if every == "hourly":
        try:
            minute = int(p.get("minute") or 0)
        except (TypeError, ValueError):
            minute = 0
        cand = lt.replace(minute=max(0, min(59, minute)), second=0,
                          microsecond=0)
        hour = datetime.timedelta(hours=1)
        if cand.timestamp() > now:
            cand -= hour
        return cand.timestamp(), (cand + hour).timestamp()

    hour, minute = _hhmm_parts(p.get("at")) or (0, 0)

    if every == "weekly":
        try:
            want = int(p.get("weekday") or 0)
        except (TypeError, ValueError):
            want = 0
        want = max(0, min(6, want))
        cand = lt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        cand -= day * ((cand.weekday() - want) % 7)
        if cand.timestamp() > now:
            cand -= day * 7
        return cand.timestamp(), (cand + day * 7).timestamp()

    if every == "monthly":
        try:
            want = int(p.get("day", 1))
        except (TypeError, ValueError):
            want = 1
        want = max(0, min(31, want))

        def at_month(year, month):
            # day 0 = the LAST day, whatever length the month is: "the last
            # day of the month" is a thing operators say, and asking for the
            # 31st to mean it works right up until somebody reads the form.
            last = calendar.monthrange(year, month)[1]
            return datetime.datetime(year, month,
                                     last if want == 0 else min(want, last),
                                     hour, minute)

        cand = at_month(lt.year, lt.month)
        if cand.timestamp() > now:
            cand = at_month(*((lt.year - 1, 12) if lt.month == 1
                              else (lt.year, lt.month - 1)))
        nxt = at_month(*((cand.year + 1, 1) if cand.month == 12
                         else (cand.year, cand.month + 1)))
        return cand.timestamp(), nxt.timestamp()

    cand = lt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand.timestamp() > now:
        cand -= day
    return cand.timestamp(), (cand + day).timestamp()


class Function:
    """One validated function spec. Construction raises ValueError on a bad
    spec -- parse_functions() catches per item, so one typo never silences
    the rest."""

    __slots__ = ("id", "name", "type", "enabled", "params", "classes",
                 "model", "camera", "expr_tree", "trigger", "triggers",
                 # Said once, not once per frame: a rule watching a model
                 # that is gone would otherwise print at the frame rate.
                 "_said_stale")

    def __init__(self, spec):
        self._said_stale = False
        self.id = zones_mod._slug(spec.get("id") or spec.get("name") or "")
        if not self.id:
            raise ValueError("function needs an id")
        self.name = str(spec.get("name") or self.id).strip()
        self.type = str(spec.get("type") or "").strip().lower()
        if self.type not in TYPES:
            raise ValueError(f"function {self.id!r}: unknown type {self.type!r}")
        self.enabled = bool(spec.get("enabled", True))
        p = dict(spec.get("params") or {})
        self.params = p
        self.classes = {str(c) for c in (p.get("classes") or ())}
        # With several models on one camera ([[detector.models]]) a function
        # can bind to ONE of them; "" keeps the legacy any-model behaviour.
        self.model = str(p.get("model") or "")
        # With several CAMERAS in one system, a function belongs to exactly one
        # of them: it is evaluated by that camera's engine only. "" means the
        # system's first camera, which is where every function written before
        # multi-camera ran.
        self.camera = str(p.get("camera") or "")

        if self.type in ("zone_count", "zone_dwell", "zone_unique",
                         "zone_depth", "zone_countdown",
                         "zone_occupancy") and not p.get("zone"):
            raise ValueError(f"function {self.id!r}: params.zone is required")
        if self.type == "zone_countdown":
            try:
                if float(p.get("start_s") or 0) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.start_s must "
                                 "be a positive number of seconds")
        # Counting timing, shared by the zone and frame counters. Rejected
        # rather than clamped: a rule that silently counts over a different
        # window than the one typed is worse than one that refuses to load.
        for key in ("enter_after_s", "leave_after_s"):
            if p.get(key) in (None, ""):
                continue
            try:
                if not 0 <= float(p[key]) <= 3600:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.{key} must be "
                                 "0..3600 seconds")
        if self.type == "line_count" and not p.get("line"):
            raise ValueError(f"function {self.id!r}: params.line is required")
        if self.type == "line_travel":
            if not p.get("line_a") or not p.get("line_b"):
                raise ValueError(f"function {self.id!r}: params.line_a and line_b are required")
            # 0 = SPEED OFF: the rule measures trip times, which need no
            # distance. Only a negative (or unreadable) one is a mistake.
            try:
                if float(p.get("distance_m") or 0) < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.distance_m must "
                                 "be metres, or 0 for times only")
        if self.type == "zone_travel":
            if not p.get("start") or not p.get("end"):
                raise ValueError(f"function {self.id!r}: params.start and params.end are required")
            if p.get("start") == p.get("end"):
                raise ValueError(f"function {self.id!r}: start and end must be different")
        if self.type == "proximity":
            if not p.get("class_a") or not p.get("class_b"):
                raise ValueError(f"function {self.id!r}: params.class_a and class_b are required")
            try:
                if not 0 < float(p.get("max_distance") or 0) <= 1:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.max_distance must be "
                                 "0..1 (a fraction of the frame width)")
        if self.type == "guard":
            try:
                if not 0 < float(p.get("margin_pct") or 0) <= 200:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.margin_pct must be 1..200")
        if self.type == "trails":
            try:
                if not 1 <= float(p.get("length_s") or 10) <= 120:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.length_s must "
                                 "be 1..120 seconds")

        if self.type == "board_input":
            from app import iriv                            # noqa: PLC0415

            port = str(p.get("port") or "").upper()
            spec = iriv.PORT_BY_ID.get(port)
            if not spec or spec["kind"] != "input":
                names = ", ".join(q["id"] for q in iriv.BOARD_PORTS
                                  if q["kind"] == "input")
                raise ValueError(f"function {self.id!r}: params.port must be "
                                 f"one of {names}")
            p["port"] = port

        if self.type == "modbus_read":
            try:
                fc = int(p.get("fc") or 3)
                if fc not in (1, 2, 3, 4):
                    raise ValueError
                if not 1 <= int(p.get("quantity") or 1) <= 32:
                    raise ValueError
                interval = (MIN_POLL_S if p.get("interval_s") in (None, "")
                            else float(p["interval_s"]))
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: modbus fc must be "
                                 "1-4 and quantity 1-32")
            # Interval 0 = read on a trigger (fn + metric the engine watches
            # for a rising edge); refused without one, as core does, so a
            # reader that would never read is never started.
            if interval <= 0:
                trig = p.get("trigger") if isinstance(p.get("trigger"), dict) else {}
                trig_fn = zones_mod._slug(trig.get("fn") or "")
                if not trig_fn:
                    raise ValueError(f"function {self.id!r}: interval 0 means "
                                     "read on a trigger - params.trigger needs "
                                     "fn and metric")
                p["interval_s"] = 0
                p["trigger"] = {"fn": trig_fn,
                                "metric": str(trig.get("metric") or "value")}
            else:
                p["interval_s"] = min(MAX_POLL_S, max(MIN_POLL_S, interval))

        if self.type == "query_value":
            # A number out of a Query. The app has NO database -- it emits
            # events and core writes them -- so the statement is not run
            # here: the master runs it and this reads the answer over
            # localhost, which is the only address this type may name.
            url = str(p.get("url") or "").strip()
            # Prefix comparison rather than a regex, because this module
            # deliberately imports almost nothing: it runs on the box beside
            # inference, and every import here is paid at startup on a Pi.
            # The check is the same one core makes -- localhost, with or
            # without a port -- and the trailing separator is what stops
            # "http://127.0.0.1.evil.example/" passing as this machine.
            host_ok = any(
                url.startswith(f"http://{h}/") or url.startswith(f"http://{h}:")
                for h in ("127.0.0.1", "localhost"))
            if not host_ok:
                raise ValueError(f"function {self.id!r}: a query variable "
                                 "reads from this box only")
            try:
                interval = float(p.get("interval_s") or MIN_QUERY_POLL_S)
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.interval_s must be a number")
            p["interval_s"] = min(MAX_POLL_S, max(MIN_QUERY_POLL_S, interval))
            if self.camera == "*":
                raise ValueError(f"function {self.id!r}: a query variable is "
                                 "not tied to a camera")

        if self.type == "api_poll":
            url = str(p.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"function {self.id!r}: params.url must be an "
                                 "http:// or https:// address")
            try:
                interval = float(p.get("interval_s") or MIN_POLL_S)
            except (TypeError, ValueError):
                raise ValueError(f"function {self.id!r}: params.interval_s must be a number")
            # Clamped, not refused: an interval typed as 1 is a preference for
            # "as often as possible", and the floor is what protects the box.
            p["interval_s"] = min(MAX_POLL_S, max(MIN_POLL_S, interval))
            if self.camera == "*":
                # Every camera's engine evaluates its own copy of a "*" rule,
                # which for an HTTP fetch means N pollers hammering one
                # endpoint and N answers folded into nonsense.
                raise ValueError(f"function {self.id!r}: an API value is not "
                                 "about a camera; leave the camera unset")

        if self.type == "total" and not str(p.get("key") or "").strip():
            raise ValueError(f"function {self.id!r}: params.key is required")

        if self.type == "mqtt_sub":
            # An MQTT Connection (params.conn) or, saved before those, the
            # node's own broker address.
            if not str(p.get("host") or p.get("conn") or "").strip():
                raise ValueError(f"function {self.id!r}: params.conn or "
                                 "params.host is required")
            if not str(p.get("topic") or "").strip():
                raise ValueError(f"function {self.id!r}: params.topic is required")
            if self.camera == "*":
                raise ValueError(f"function {self.id!r}: an MQTT value is not "
                                 "about a camera; leave the camera unset")

        if self.type == "custom":
            # A store template's own evaluator. The handler name is resolved
            # at run time against app/store_ext/ (extensions.py) -- unresolved
            # stays a per-frame no-op there, because the function LIST must
            # still load on a box where the extension failed to.
            if not str(p.get("handler") or "").strip():
                raise ValueError(f"function {self.id!r}: params.handler is required")

        if self.type == "inject":
            if str(p.get("ptype") or "string") not in INJECT_TYPES:
                raise ValueError(f"function {self.id!r}: params.ptype must be "
                                 f"one of {', '.join(INJECT_TYPES)}")
            if self.camera == "*":
                raise ValueError(f"function {self.id!r}: an Inject is not "
                                 "about a camera; leave the camera unset")

        if self.type == "schedule":
            every = str(p.get("every") or "daily").strip().lower()
            if every not in SCHEDULE_EVERY:
                raise ValueError(f"function {self.id!r}: params.every must be "
                                 f"one of {', '.join(SCHEDULE_EVERY)}")
            p["every"] = every
            if every == "minutes":
                try:
                    minutes = int(p.get("interval_min") or 0)
                except (TypeError, ValueError):
                    minutes = 0
                if not 1 <= minutes <= 1440:
                    raise ValueError(f"function {self.id!r}: "
                                     "params.interval_min must be 1..1440")
            elif every != "hourly" and not _hhmm_parts(p.get("at")):
                raise ValueError(f"function {self.id!r}: params.at must be a "
                                 "time like 12:00")
            if self.camera == "*":
                raise ValueError(f"function {self.id!r}: a schedule is not "
                                 "about a camera; leave the camera unset")

        self.expr_tree = None
        if self.type == "expr":
            self.expr_tree = compile_expr(p.get("expr"))    # raises ValueError

        # One [functions.trigger] table plus any number of [[functions.triggers]]
        # entries (the workflow's When nodes) -- each fires independently.
        self.triggers = []
        if spec.get("trigger"):
            self.triggers.append(self._parse_trigger(spec["trigger"]))
        for t in (spec.get("triggers") or ()):
            if isinstance(t, dict):
                self.triggers.append(self._parse_trigger(t))
        self.trigger = self.triggers[0] if self.triggers else None

    def _parse_trigger(self, trig):
        mode = str(trig.get("mode") or "").strip().lower()
        op = str(trig.get("op") or "").strip()
        if mode:
            if mode not in TRIGGER_MODES:
                raise ValueError(f"function {self.id!r}: trigger.mode must be "
                                 f"one of {', '.join(TRIGGER_MODES)}")
            op = _MODE_OPS.get(mode, op or ">")
        else:
            op = op or ">"
            if op not in _CMP_FUNCS:
                raise ValueError(f"function {self.id!r}: trigger.op must be one of {sorted(_CMP_FUNCS)}")
        return {
            "metric": str(trig.get("metric") or self._default_metric()),
            "mode": mode,                        # "" = plain comparison via op
            "op": op if op in _CMP_FUNCS else ">",
            "value": float(trig.get("value", 1)),
            "value2": float(trig.get("value2", 0)),
            "hold_s": max(0.0, float(trig.get("hold_s", 0))),
            "severity": str(trig.get("severity") or "warn"),
            "message": str(trig.get("message") or f"{self.name} triggered"),
            "cooldown_s": max(0.0, float(trig.get("cooldown_s", 30))),
            # The rule whose objects this alarm is about (its id), and whether
            # a held alarm repeats on the cooldown ("") or only when a NEW
            # object joins it ("new_object"). The second reads the first.
            "oid_source": str(trig.get("oid_source") or ""),
            "repeat": (str(trig.get("repeat") or "").strip().lower()
                       if trig.get("oid_source") else ""),
            # The Trigger node's Advanced output; None and "" for every
            # trigger without it, which then runs exactly as it always did.
            "out": _parse_out(trig.get("out")),
            "rate": _parse_rate(trig),
            "rate_s": max(0.0, float(trig.get("rate_s", 0) or 0)),
            # The duplicate guard (the Trigger's Current OID / Current value
            # ports): the stats holding what the table already stored, and
            # how far the value must move. "" for every trigger without it.
            "cur_oid_key": str(trig.get("cur_oid_key") or ""),
            "cur_value_key": str(trig.get("cur_value_key") or ""),
            "dup_diff": max(0.0, float(trig.get("dup_diff", 0) or 0)),
        }

    def _default_metric(self):
        return {"zone_count": "count", "zone_dwell": "max_s",
                "zone_unique": "total",
                "line_count": "net", "line_travel": "last_speed",
                "zone_travel": "last_s",
                "proximity": "pairs", "guard": "inside_now",
                "trails": "tracked",
                "frame_count": "count", "frame_unique": "total",
                "expr": "value", "schedule": "due",
                "inject": "fired", "state_watch": "fired"}[self.type]

    #: Model names this system is actually running, set by the loop (see
    #: run.py, `_running_models`). Empty means "not told" -- every rule
    #: then filters exactly as it always did.
    running_models = frozenset()

    def accepts(self, label, model=None):
        if self.model and model and model != self.model \
                and not self.stale_model():
            return False
        return not self.classes or label in self.classes

    def stale_model(self):
        """Is this rule bound to a model the system no longer runs?

        A rule carries `model` so a graph with two models can say which
        one it watches. Change the system's model afterwards and that name
        matches nothing: every detection is rejected, the rule reads zero
        for ever, and nothing anywhere says why -- video flowing, objects
        boxed on screen, counter dead. Seen on a live parking counter
        whose line rule still named yolov8s after the system was moved to
        yolov8n.

        A filter that excludes everything is not a filter, so it is
        dropped: the rule counts what the system does detect, and says so
        once. Only when we have been TOLD what is running -- an empty set
        means the loop has not said, and nothing is assumed from that.
        """
        if not self.model or not self.running_models:
            return False
        if self.model in self.running_models:
            return False
        if not self._said_stale:
            self._said_stale = True
            log.warning("[FN] %s: watches model %r, which this system does "
                        "not run (%s) - counting every model instead",
                        self.id, self.model,
                        ", ".join(sorted(self.running_models)))
        return True

    def as_dict(self):
        d = {"id": self.id, "name": self.name, "type": self.type,
             "enabled": self.enabled, "params": dict(self.params)}
        if self.trigger:
            d["trigger"] = dict(self.trigger)
        if len(self.triggers) > 1:
            d["triggers"] = [dict(t) for t in self.triggers[1:]]
        return d


def parse_functions(specs):
    """Build Function objects from config, skipping (and logging) bad ones.

    Unlike zones -- where a typo failing loudly at startup is the right call --
    functions are edited live from the dashboard while detection runs; one bad
    rule must not stop the other nine.
    """
    out, seen = [], set()
    for spec in (specs or ()):
        try:
            fn = Function(spec)
        except (ValueError, TypeError) as exc:
            log.warning("skipping bad function: %s", exc)
            continue
        if fn.id in seen:
            log.warning("skipping duplicate function id %r", fn.id)
            continue
        seen.add(fn.id)
        out.append(fn)
    return out


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------

class FunctionEngine:
    """Interprets the function list, once per frame, after ZoneEngine.

    Stateful where the maths needs it (dwell entry times, travel start times,
    crossing tallies, trigger edges), keyed by track_id from app/tracker.py.
    stats() returns flat fn_<id>_* scalars, which run.py merges into the same
    counts dict the zone stats travel in -- so every existing widget, the
    master sync and the Blynk pusher see function outputs with no new plumbing.
    """

    def __init__(self, functions, anchor="bottom_center", state_dir=None,
                 scope=""):
        self.functions = [f for f in (functions or ()) if f.enabled]
        # Which camera's engine this is: a rule on every camera runs in each
        # one, and their trackers number objects independently, so a
        # Trigger's remembered objects are kept per engine (_trigger_memory).
        self._scope = str(scope or "")
        # A Trigger's Advanced output, per (fid, trigger index): on/off, the
        # value it carries, when the hold ends. _out_last is what it
        # published last frame, which is what a Formula reads this frame
        # (the triggers run after the formulas).
        self._outs = {}
        self._out_last = {}
        # Every enabled function, whether it runs this frame or not; see
        # set_inactive. `functions` is the part that runs.
        self._all = list(self.functions)
        self._inactive = frozenset()
        self.anchor = anchor if anchor in zones_mod.ANCHORS else "bottom_center"
        self._stats = {}
        # Per-day tallies, {fid: {"YYYY-MM-DD": n}}, loaded from disk so a
        # restart does not zero "today". state_dir=None keeps the engine
        # memory-only, which is what the tests and any embedder want.
        self._state_dir = str(state_dir) if state_dir else ""
        self._daily = self._load_daily()
        self._saved_totals = self._load_saved_totals()
        self._totals_dirty = False
        self._daily_dirty = False
        self._daily_flushed = 0.0
        # Per-function state, keyed by function id.
        self._dwell = {}     # fid -> {"open": {tid: entry_ts}, "n": 0, "sum": 0.0, "max": 0.0}
        self._countdown = {} # fid -> {"inside": set, "used": s, "entries": n, "day": "YYYY-MM-DD"}
        self._zocc = {}      # fid -> {"since": ts|None, "last": s, "total": s, "sessions": n}
        self._objdet = {}    # fid -> {"ids": set of track ids present last frame}
        self._travel = {}    # fid -> {"open": {tid: start_ts}, "trips": 0, "sum": 0.0,
                             #         "last_s": 0.0, "last_speed": 0.0}
        self._ztravel = {}   # fid -> same shape, for zone/line spans
        # WHERE things happen, as a coarse grid of counts:
        # fid -> {"grid": [n...], "cols", "rows", "seen": {tid: ts}, "at": ts}
        self._heat = {}
        # Events this frame that are not threshold triggers: entry/exit stamps.
        # They ride out on the same return value so run.py has one path from
        # "the engine noticed something" to "the operator can see it".
        self._pending = []
        # Counting-rule stays that ENDED this frame: {"fn", "track", "total_s"}.
        # A snapshot taken while the object was still there learns its real
        # total from this (app/snapmeta.py finish_timer). Rebuilt every update.
        self._departed = []
        self._counts = {}    # fid -> {"in": 0, "out": 0}
        # Zone Counter membership, for the counting rules that are not "count
        # what the frame shows": {tid: {"since": ts, "last": ts}}. A track is
        # a MEMBER once it has been inside for enter_after_s, and stays one
        # until it has been away for leave_after_s.
        self._member = {}    # fid -> {tid: {"since": ts, "last": ts}}
        self._unique = {}    # fid -> {"seen": set(track_id), "current": set()}
        self._trig = {}      # (fid, idx) -> per-trigger edge/hold state
        self._prox = {}      # fid -> {"alerts": 0, "cool": {pair: last_fire}}
        self._guard = {}     # fid -> {"count": 0, "cool": {}, "pairs": set()}
        self._trailst = {}   # fid -> {"hist": {tid: [(x, y, ts), ...]}}
        self._custom = {}    # fid -> plain dict a custom handler keeps between frames
        self._totals = {}    # fid -> {"prev": float|None, "total": float, "day": int}
        self._board_input = {}   # fid -> {"last": 0|1, "changes": n, "since": t}
        # Schedules, {fid: {"last": epoch of the run already sent, "runs": n}},
        # loaded from disk: a box restarted at 12:00 must not send the noon
        # report a second time, and a schedule that has never run must not
        # believe every past moment is owed to it.
        self._sched = self._load_sched()
        # WHICH object a rule is currently about, where the question has an
        # answer: the longest current stay for a clock, the intruder for a
        # bubble. It rides out on the rule's alarm as `source_oid`, so a
        # Snapshot or a clip after it can stamp the object it is about --
        # a wire could only ever say "an object", not which one.
        self._last_oid = {}      # fid -> track id
        # EVERY object a rule is about this frame, for the rules some
        # trigger's `oid_source` names: a zone rule's members, a frame rule's
        # detections, a line rule's crossers. Rebuilt per update(). This is
        # what "re-raise per new object" compares frame to frame -- one id
        # (_last_oid) cannot say whether a second person has joined.
        self._live_oids = {}     # fid -> set(track id)
        # Overlay geometry this frame, in FRAME PIXELS, for camera.annotate():
        # proximity link lines and guard bubbles. Rebuilt per update().
        self._links = []     # [(x1, y1, x2, y2, within_bool, dist_px)]
        self._bubbles = []   # [(x1, y1, x2, y2, intruded_bool)]
        self._trails = []    # [([(x, y), ...], moving_bool)] per live track

    # -- the AI detection switch ---------------------------------------------

    #: What a rule coming BACK from inactive forgets: the clocks and the
    #: per-object memory that described the scene before the AI went off. An
    #: open dwell whose object left while nothing watched would otherwise
    #: close as one stay spanning the whole gap. {table: (field, empty)};
    #: a None field drops the function's whole entry (it is nothing BUT
    #: per-object state). Everything else -- a Running Total, in/out tallies,
    #: occupancy totals, the unique-seen set, the per-day tallies and the
    #: schedules' "already sent" -- is a COUNT, and counts carry on.
    _TRANSIENT = (("_dwell", "open", dict), ("_travel", "open", dict),
                  ("_ztravel", "open", dict), ("_member", None, None),
                  ("_countdown", "inside", set), ("_objdet", "ids", set),
                  ("_heat", "seen", dict), ("_trailst", "hist", dict),
                  ("_unique", "current", set), ("_guard", "pairs", set),
                  ("_last_oid", None, None), ("_live_oids", None, None))

    def set_inactive(self, ids, gated=()):
        """Run every function EXCEPT `ids` from the next update on.

        The app's AI detection switch (run.py ai_config): with the AI off, the
        rules a Detection Stream feeds sit out while the rest keep running in
        this same engine -- same state, same timers, nothing rebuilt. A rule
        coming back forgets only its transient state (_TRANSIENT) and its
        triggers' last reading: an alarm that was raised before the AI went
        off is still raised, so it does not fire again, and an edge
        ("rising") cannot be read off a value from before the gap.

        `gated` is the part of `ids` an ON/OFF point holds back (run.py
        point_gates). Those come back as a FRESH START instead (_fresh): the
        point's transition is the new moment, so its branch must fire again
        every time -- OFF, ON, OFF fires the OFF side twice. Kept as the
        alarm above, the branch's Trigger came back still raised from the
        previous OFF (it sat out the ON and never saw its condition drop) and
        stayed silent (Tok Aba, 2026-10-09, v1.0.33).
        """
        ids = frozenset(str(i) for i in (ids or ()))
        marks = getattr(self, "_gated_marks", None)
        if marks is None:
            marks = self._gated_marks = set()
        # Remembered while it sits out, so a rule the point gated still comes
        # back fresh if something else (a lost camera) is what lets go last.
        marks |= {str(i) for i in (gated or ())} & ids
        if ids == self._inactive:
            return
        back = self._inactive - ids
        self._fresh(back & marks)
        marks -= back
        for name, field, empty in self._TRANSIENT:
            table = getattr(self, name, None)
            if not isinstance(table, dict):
                continue
            for fid in back:
                if field is None:
                    table.pop(fid, None)
                elif isinstance(table.get(fid), dict) and field in table[fid]:
                    table[fid][field] = empty()
        for fid in back:
            st = self._totals.get(fid)
            if isinstance(st, dict):
                # The TOTAL stays; the edge it counts from does not, so the
                # first reading after the gap is a baseline, not a rise.
                st.update(prev=None, level=None, rise_t=0.0, drop_t=0.0,
                          last_t=None)
            st = self._zocc.get(fid)
            if isinstance(st, dict):
                st["since"] = None
        for key, st in self._trig.items():
            if key[0] in back and isinstance(st, dict):
                st["prev"] = None
        self._inactive = ids
        self.functions = [f for f in self._all if f.id not in ids]

    def _fresh(self, fids):
        """Rules an ON/OFF point lets back in start as on their first frame:
        every Trigger un-raised (edge, hold, cooldown and the Advanced
        output's on/hold), its "how often" memory gone, and the payload the
        formulas read last frame dropped. Only these ids -- a rule coming
        back from AI off keeps the alarm it had (set_inactive)."""
        if not fids:
            return
        for key in [k for k in self._trig if k[0] in fids]:
            del self._trig[key]
        outs = getattr(self, "_outs", None) or {}
        for key in [k for k in outs if k[0] in fids]:
            del outs[key]
        last = getattr(self, "_out_last", None) or {}
        for fid in fids:
            last.pop(f"fn_{fid}_out", None)
            _trigger_memory.pop((getattr(self, "_scope", ""), fid), None)

    # -- per-day tallies ----------------------------------------------------

    def _daily_path(self):
        return os.path.join(self._state_dir, DAILY_FILE) if self._state_dir else ""

    def _load_daily(self):
        """The tallies from disk, or empty. A corrupt file is not fatal: a
        lost tally is a wrong number, an unstartable app is no numbers."""
        path = self._daily_path()
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        out = {}
        if isinstance(raw, dict):
            for fid, days in raw.items():
                if not isinstance(days, dict):
                    continue
                out[str(fid)] = {str(d): int(n) for d, n in days.items()
                                 if isinstance(n, (int, float))
                                 and not isinstance(n, bool)}
        return out

    def _totals_path(self):
        return os.path.join(self._state_dir, TOTALS_FILE) if self._state_dir else ""

    def _load_saved_totals(self):
        """{fid: {"total", "day"}} saved by the last run, or empty (a lost
        file starts the totals from zero, never blocks the app)."""
        path = self._totals_path()
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        out = {}
        if isinstance(raw, dict):
            for fid, st in raw.items():
                try:
                    out[str(fid)] = {"total": float(st["total"]),
                                     "day": int(st.get("day", 0))}
                except (TypeError, ValueError, KeyError, AttributeError):
                    continue
        return out

    def _flush_totals(self):
        """Persist the totals that continue after a restart."""
        path = self._totals_path()
        if not path or not self._totals_dirty:
            return
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._saved_totals, f, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            return
        self._totals_dirty = False

    def flush_daily(self, now=None, force=False):
        """Persist the tallies, at most every DAILY_FLUSH_S. Called from the
        detection loop; safe to call every frame."""
        now = time.time() if now is None else now
        if force or now - self._daily_flushed >= DAILY_FLUSH_S:
            self._flush_totals()
        path = self._daily_path()
        if not path or not self._daily_dirty:
            return
        if not force and now - self._daily_flushed < DAILY_FLUSH_S:
            return
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._daily, f, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            return                      # disk full / read-only: keep counting
        self._daily_dirty = False
        self._daily_flushed = now

    # -- inject ---------------------------------------------------------------

    def _inject_presses(self, now):
        """{fn_id: (seq, ts, action)} as the master last wrote them, re-read
        only when the file changed and at most every INJECT_POLL_S -- a stat
        on the detection loop, not a read."""
        cache = getattr(self, "_inject_cache", None)
        if cache is None:
            cache = self._inject_cache = {"checked": 0.0, "mtime": 0.0,
                                          "presses": {}}
        if not self._state_dir or now - cache["checked"] < INJECT_POLL_S:
            return cache["presses"]
        cache["checked"] = now
        path = os.path.join(self._state_dir, INJECT_FILE)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return cache["presses"]
        if mtime != cache["mtime"]:
            cache["mtime"] = mtime
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f) or {}
                # `action` is press / start / stop; a file written before
                # Start/Stop existed says nothing, which meant press.
                cache["presses"] = {
                    str(k): (int(v.get("seq") or 0), float(v.get("ts") or 0),
                             str(v.get("action") or "press"))
                    for k, v in raw.items() if isinstance(v, dict)}
            except (OSError, ValueError, TypeError, AttributeError):
                pass                    # half-written: next poll reads it
        return cache["presses"]

    def _update_inject(self, fn, prefix, now):
        """`fired` = 1 for the one tick an inject happens, `value` the payload
        it injected, `runs` how many there have been.

        Three things inject: a press of the button on the canvas (the master
        writes INJECT_FILE), "once after N seconds" when this node was
        started, and the repeat. All three are the same moment to everything
        downstream -- one rising edge of `fired`.

        A REPEATING inject can be started and stopped from its button
        (`running`, published so the canvas and the dashboard can say which).
        It comes up running unless params.autostart is False -- the behaviour
        every repeating inject had before Start/Stop existed. The state lives
        with the rhythm, so a save of another node keeps it; an app restart
        goes back to autostart (a Stop is deliberately not persisted: a box
        that reboots comes up doing what its flow says). "Once after N s" is
        not affected by either.
        """
        p = fn.params
        sig = json.dumps(p, sort_keys=True)
        st = _inject_state.get(fn.id)
        if st is None or st["sig"] != sig:
            # New, or its own settings changed: started now, as a redeploy
            # starts a Node-RED flow. The press already in the file is taken
            # as seen, so a restart does not replay the last click.
            prev = st or {}
            st = _inject_state[fn.id] = {
                "sig": sig, "start": now, "once_done": False,
                # An operator's Start/Stop outlives a change to this node's
                # own settings too: editing the payload is not a Stop.
                "running": prev.get("running", p.get("autostart") is not False),
                "last": now, "runs": prev.get("runs", 0),
                "value": prev.get("value", ""
                                  if p.get("ptype") in ("string", "json")
                                  else 0),
                "seq": prev.get("seq"),
                # Whether the LAST injection passed (1) or was dropped (0),
                # and when the last one fired: the green/red circle on the
                # node (client UAT 2026-10-04, item 2). None until one
                # has been tried.
                "ok": prev.get("ok"), "last_at": prev.get("last_at", 0.0)}
        fire = False
        interval = float(p.get("interval_s") or 0)
        press = self._inject_presses(now).get(fn.id)
        if press is not None and press[0] != st["seq"]:
            st["seq"] = press[0]
            # A press only counts while it is fresh: one found in the file
            # at start-up is a click from before, not a click now.
            if now - press[1] <= INJECT_STALE_S:
                action = press[2] if len(press) > 2 else "press"
                if action == "stop":
                    st["running"] = False
                elif action == "start":
                    # Started: the first inject is NOW, the rhythm from here.
                    # Only a repeat has anything to start; to any other
                    # Inject a Start is not a press.
                    if interval > 0 and not st["running"]:
                        st["running"] = True
                        fire = True
                else:
                    fire = True
            else:
                # A press that reached the box too late (INJECT_STALE_S) is
                # dropped, and the drop is said rather than swallowed: the
                # circle on the node goes red.
                st["ok"] = 0
        if p.get("once") and not st["once_done"] \
                and now - st["start"] >= float(p.get("once_delay_s") or 0):
            st["once_done"] = True
            fire = True
        if interval > 0 and st["running"] and now - st["last"] >= interval:
            fire = True
        if fire:
            st["last"] = now
            st["runs"] += 1
            st["value"] = inject_value(p.get("ptype"), p.get("payload"), now)
            st["ok"] = 1
            st["last_at"] = now
        self._stats[prefix + "fired"] = 1 if fire else 0
        self._stats[prefix + "runs"] = st["runs"]
        # `ok` / `last_at` exist only once an injection has been tried, so a
        # node nobody has pressed reads as "not yet" rather than as failed.
        if st.get("ok") is not None:
            self._stats[prefix + "ok"] = st["ok"]
        if st.get("last_at"):
            self._stats[prefix + "last_at"] = round(st["last_at"], 3)
        self._stats[prefix + "value"] = st["value"]
        self._stats[prefix + "running"] = 1 if interval > 0 and st["running"] \
            else 0
        if p.get("topic"):
            self._stats[prefix + "topic"] = str(p["topic"])

    # -- ON/OFF points -------------------------------------------------------

    def _update_state_watch(self, fn, prefix, now):
        """An ON or OFF point of a Detection Stream (params.watch "ai") or a
        Class Filter ("class_filter", params.filter the node the dashboard
        pauses by). `value` is 1 while the state matches the point (want
        "on" / "off"), `fired` 1 for the one tick it STARTS matching, `runs`
        how many times it has.

        The first look after the app starts counts as a start: a point whose
        state already matches fires once, so "when the AI is off, do X" is
        done on a box that boots with the AI off. Its own settings changing
        (a new wire into it is one -- params.feeds) is a start too, the way
        an Inject re-arms. Nothing is published until run.py has said what
        the states are (_states)."""
        if _states is None:
            return
        p = fn.params
        if p.get("fixed_off"):
            on = False
        elif p.get("watch") == "class_filter":
            on = str(p.get("filter") or "") not in _states["paused"]
        else:
            on = _states["ai"]
        hit = on == (p.get("want") != "off")
        sig = json.dumps(p, sort_keys=True)
        st = _state_watch.get(fn.id)
        if st is None or st["sig"] != sig:
            prev = st or {}
            st = _state_watch[fn.id] = {"sig": sig, "hit": None,
                                        "runs": prev.get("runs", 0),
                                        "last_at": prev.get("last_at", 0.0)}
        fire = hit and st["hit"] is not True
        st["hit"] = hit
        if fire:
            st["runs"] += 1
            st["last_at"] = now
        self._stats[prefix + "value"] = 1 if hit else 0
        self._stats[prefix + "fired"] = 1 if fire else 0
        self._stats[prefix + "runs"] = st["runs"]
        if st["last_at"]:
            self._stats[prefix + "last_at"] = round(st["last_at"], 3)

    # -- schedules ----------------------------------------------------------

    def _sched_path(self):
        return os.path.join(self._state_dir, SCHEDULE_FILE) if self._state_dir else ""

    def _load_sched(self):
        """What each schedule has already run, or empty. A corrupt file costs
        one duplicate report, never a start-up."""
        path = self._sched_path()
        if not path:
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        out = {}
        if isinstance(raw, dict):
            for fid, st in raw.items():
                if not isinstance(st, dict):
                    continue
                try:
                    out[str(fid)] = {"last": float(st.get("last") or 0.0),
                                     "runs": int(st.get("runs") or 0)}
                except (TypeError, ValueError):
                    continue
        return out

    def _save_sched(self):
        """Written the moment a schedule fires rather than on a flush timer:
        the whole point of the file is to survive the restart that may come
        one second later."""
        path = self._sched_path()
        if not path:
            return
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._sched, f, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            pass                        # read-only disk: the report still went

    def _update_schedule(self, fn, prefix, now):
        """`due` = 1 for the one tick a scheduled moment arrives on.

        The rule is deliberately edge-shaped rather than window-shaped ("it is
        12:00 for a whole minute"): an output fires on the rising edge, so a
        window would send once per tick for as long as the window lasted.
        """
        prev, nxt = _schedule_bounds(fn.params, now)
        st = self._sched.setdefault(fn.id, {"last": 0.0, "runs": 0})
        due = 0
        if not st["last"]:
            # First sight of this schedule: adopt the current period without
            # firing. A node dropped on the canvas at 15:00 does not owe
            # anybody today's noon report.
            st["last"] = prev
            self._save_sched()
        elif prev > st["last"] + 0.5:
            # A moment has passed since the last one that was sent. Whether it
            # is THIS one (fire) or one missed while the box was off
            # (params.catch_up decides) is the only question left.
            # The window is checked against the MOMENT THAT CAME DUE, not
            # against now: a catch-up run for last night's 22:00 belongs to
            # 22:00, and judging it by the hour the box happened to come back
            # would drop exactly the report catch_up exists to deliver.
            if (now - prev <= LATE_GRACE_S or fn.params.get("catch_up")) \
                    and _within_window(fn.params, prev):
                due = 1
                st["runs"] += 1          # runs counts what WENT OUT
            # The period is consumed either way, fired or not: a moment
            # outside the window has passed, it did not fail. Leaving it
            # unconsumed would make the next tick fire it the instant the
            # window opened -- a queue of every missed period rather than a
            # schedule.
            st["last"] = prev
            self._save_sched()
        self._stats[prefix + "due"] = due
        self._stats[prefix + "runs"] = st["runs"]
        self._stats[prefix + "next_s"] = max(0, int(round(nxt - now)))

    def _bump_daily(self, fid, n, day):
        if n <= 0:
            return
        days = self._daily.setdefault(fid, {})
        days[day] = days.get(day, 0) + n
        if len(days) > DAILY_KEEP_DAYS:
            for old in sorted(days)[:-DAILY_KEEP_DAYS]:
                days.pop(old, None)
        self._daily_dirty = True

    def _publish_daily(self, fid, prefix, now):
        """today / week / month scalars plus the by_day bucket.

        The scalars are what a tile binds to (a tile needs a number, and
        "objects today" is the number most people actually want); the bucket
        is what a chart binds to, and what Analytics filters when the operator
        picks a range -- see analytics.scope_stats.
        """
        days = self._daily.get(fid) or {}
        tm = time.localtime(now)
        today = time.strftime("%Y-%m-%d", tm)
        week_floor = time.strftime(
            "%Y-%m-%d", time.localtime(now - 6 * 86400))
        month_floor = time.strftime(
            "%Y-%m-%d", time.localtime(now - 29 * 86400))
        self._stats[prefix + "today"] = days.get(today, 0)
        self._stats[prefix + "week"] = sum(n for d, n in days.items()
                                           if d >= week_floor)
        self._stats[prefix + "month"] = sum(n for d, n in days.items()
                                            if d >= month_floor)
        self._stats[prefix + "by_day"] = dict(days)

    # -- helpers ------------------------------------------------------------

    def _zone_by_id(self, zones, zid):
        for z in (zones or ()):
            if z.id == zid:
                return z
        return None

    # -- per-frame update -----------------------------------------------------

    def update(self, result, detections, tracks, stats, frame_w, frame_h,
               zones=None, now=None):
        """Run every function against this frame. Returns trigger dicts:
        [{"id", "type", "severity", "message", "value"}].

        `result` is ZoneEngine.update() output (crossing_events included),
        `tracks` the live {track_id: Track} map, `stats` the flat zone stats.
        """
        now = time.time() if now is None else now
        self._stats = {}
        self._pending = []
        self._departed = []
        self._links = []
        self._bubbles = []
        self._trails = []
        events = result.get("crossing_events") or []
        live_ids = set(tracks or ())

        for fn in self.functions:
            prefix = f"fn_{fn.id}_"
            if fn.type == "zone_count":
                self._stats[prefix + "count"] = self._zone_count(
                    fn, detections, zones, frame_w, frame_h, now)
            elif fn.type == "zone_dwell":
                self._update_dwell(fn, prefix, detections, tracks, live_ids,
                                   zones, frame_w, frame_h, now)
            elif fn.type == "zone_unique":
                self._update_unique(fn, prefix, detections, zones,
                                    frame_w, frame_h, now)
            elif fn.type == "zone_countdown":
                self._update_countdown(fn, prefix, detections, zones,
                                       frame_w, frame_h, now)
            elif fn.type == "zone_occupancy":
                self._update_occupancy(fn, prefix, detections, zones,
                                       frame_w, frame_h, now)
            elif fn.type == "object_detect":
                self._update_object_detect(fn, prefix, detections, zones,
                                           frame_w, frame_h, now)
            elif fn.type == "heatmap":
                self._update_heatmap(fn, prefix, detections, zones,
                                     frame_w, frame_h, now)
            elif fn.type == "frame_count":
                self._update_frame_count(fn, prefix, detections, now)
            elif fn.type == "frame_unique":
                self._update_frame_unique(fn, prefix, detections, now)
            elif fn.type == "line_count":
                self._update_line_count(fn, prefix, events, now, detections,
                                        zones, frame_w, frame_h)
            elif fn.type == "line_travel":
                self._update_travel(fn, prefix, events, live_ids, now)
            elif fn.type == "zone_travel":
                self._update_zone_travel(fn, prefix, detections, events, zones,
                                         live_ids, frame_w, frame_h, now)
            elif fn.type == "proximity":
                self._update_proximity(fn, prefix, detections, frame_w, now)
            elif fn.type == "guard":
                self._update_guard(fn, prefix, detections, now)
            elif fn.type == "trails":
                self._update_trails(fn, prefix, detections, now)
            elif fn.type == "schedule":
                self._update_schedule(fn, prefix, now)
            elif fn.type == "inject":
                self._update_inject(fn, prefix, now)
            elif fn.type == "state_watch":
                self._update_state_watch(fn, prefix, now)
            elif fn.type == "custom":
                # A store template's own evaluator (app/store_ext/). The
                # extension is a guest: extensions.run_function contains any
                # crash and benches the handler, so a broken template reads
                # as fn_<id>_error = 1, never as a dead detection loop.
                out = extensions_mod.run_function(
                    str(fn.params.get("handler") or ""), fn,
                    {"detections": detections, "tracks": tracks,
                     "zones": zones, "stats": dict(stats or {}),
                     "frame_w": frame_w, "frame_h": frame_h, "now": now,
                     "state": self._custom.setdefault(fn.id, {})})
                if out is None:
                    self._stats[prefix + "error"] = 1
                else:
                    for suffix, value in out.items():
                        if isinstance(value, list):
                            # A list is not a stat -- every card, formula and
                            # state.json reader expects a number -- so the
                            # stat is its first item and the list waits
                            # beside it for the one reader that wants it
                            # whole (a Modbus Write on FC 15/16).
                            _list_stats[prefix + suffix] = value
                            value = value[0] if value else 0
                        else:
                            _list_stats.pop(prefix + suffix, None)
                        self._stats[prefix + suffix] = value
            elif fn.type in ("api_poll", "query_value"):
                # The loop never fetches: it reads whatever the background
                # poller left behind, so a dead endpoint costs nothing.
                # A query variable is the same machinery pointed at this
                # box's own master -- including the staleness contract, which
                # is what lets an alert tell "the total is zero" apart from
                # "the query is failing".
                _ensure_poller(fn, now)
                with _api_lock:
                    st = _api_state.get(fn.id) or {}
                    value = st.get("value", "" if fn.params.get("text")
                                   else float(fn.params.get("fallback") or 0))
                    ok = bool(st.get("ok"))
                # A text read (a Trigger's Current OID) stays a word.
                self._stats[prefix + "value"] = (str(value) if fn.params.get("text")
                                                 else round(float(value), 4))
                self._stats[prefix + "ok"] = 1 if ok else 0
            elif fn.type == "board_input":
                # A dry contact on the box itself: a light curtain, a machine's
                # "cycle running" signal, an e-stop loop. Read straight from
                # the pin -- it is a memory read through gpiozero, not a
                # network call, so it needs no poller thread.
                #
                # `ok` exists for the same reason api_poll has one: an
                # unreadable input is NOT the same as an open contact, and an
                # alert wired to a safety signal must be able to tell them
                # apart.
                from app import iriv                        # noqa: PLC0415

                raw = iriv.read_port(fn.params.get("port"))
                state = 0 if raw is None else int(raw)
                if bool(fn.params.get("invert")):
                    state = 0 if state else 1
                seen = self._board_input.setdefault(fn.id, {"last": state,
                                                            "changes": 0,
                                                            "since": now})
                if state != seen["last"]:
                    seen["last"] = state
                    seen["changes"] += 1
                    seen["since"] = now
                self._stats[prefix + "state"] = state
                self._stats[prefix + "changes"] = seen["changes"]
                self._stats[prefix + "held_s"] = round(now - seen["since"], 2)
                self._stats[prefix + "ok"] = 0 if raw is None else 1
            elif fn.type == "mqtt_sub":
                # Same shape as api_poll, opposite direction: the broker
                # pushes and the subscriber thread holds the last reading.
                _ensure_subscriber(fn, now)
                with _mqtt_lock:
                    st = _mqtt_state.get(fn.id) or {}
                    value = st.get("value", float(fn.params.get("fallback") or 0))
                    ok = bool(st.get("ok"))
                    msgs = int(st.get("msgs") or 0)
                    last = (st.get("payload"), st.get("topic"), st.get("at"))
                self._stats[prefix + "value"] = round(float(value), 4)
                self._stats[prefix + "ok"] = 1 if ok else 0
                self._stats[prefix + "messages"] = msgs
                # The last message as it arrived: its raw payload, the topic
                # it came on (a + or # filter matches many) and when. Only
                # once one has arrived -- an empty string would read as an
                # empty message.
                if last[2]:
                    self._stats[prefix + "payload"] = last[0] or ""
                    self._stats[prefix + "topic"] = last[1] or ""
                    self._stats[prefix + "at"] = last[2]
            elif fn.type == "modbus_read":
                # Same contract as api_poll: the loop reads what the bus
                # poller left behind, never the wire itself.
                _ensure_modbus(fn, now)
                with _modbus_lock:
                    st = _modbus_state.get(fn.id) or {}
                    values = list(st.get("values") or [])
                    ok = bool(st.get("ok"))
                self._stats[prefix + "value"] = round(values[0], 4) if values else 0
                self._stats[prefix + "ok"] = 1 if ok else 0
                # One stat per register, r0.. counting up from the start
                # address, so a 4-register block is four usable variables.
                for i, v in enumerate(values):
                    self._stats[f"{prefix}r{i}"] = round(v, 4)
        _reap_pollers(now)
        _reap_subscribers(now)
        _reap_modbus(now)

        # expr functions read everything computed above plus the zone stats --
        # evaluated last, in one pass (no cross-expr dependency ordering; an
        # expr referencing another expr sees its PREVIOUS frame value as 0 on
        # the first frame, which is fine for continuous metrics).
        variables = dict(stats or {})
        variables.update(self._stats)
        for key, value in getattr(self, "_out_last", {}).items():
            variables.setdefault(key, value)

        # Running totals, BEFORE exprs so a formula can read a total the same
        # frame it grows. Stateful on purpose: "total = total + new" written
        # as an expression re-evaluates thirty times a second and means
        # nothing; a total is a memory, and this is where the memory lives.
        today = time.localtime(now).tm_yday
        for fn in self.functions:
            if fn.type != "total":
                continue
            first = fn.id not in self._totals
            st = self._totals.setdefault(fn.id, {"prev": None, "total": 0.0,
                                                 "day": today, "level": None,
                                                 "rise_since": None, "rise_min": 0.0,
                                                 "drop_since": None, "drop_max": 0.0})
            if first and fn.params.get("resume"):
                # Continue from where the last run left off (saved to disk
                # beside the event log), instead of from zero.
                kept = self._saved_totals.get(fn.id)
                if kept:
                    st["total"] = kept["total"]
                    st["day"] = kept["day"] or today
            if fn.params.get("reset") == "daily" and st["day"] != today:
                st.update(total=0.0, day=today, prev=None, level=None,
                          rise_since=None, drop_since=None)
            try:
                value = float(variables.get(str(fn.params.get("key") or ""), 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            try:
                debounce = float(fn.params.get("debounce_s") or 0)
            except (TypeError, ValueError):
                debounce = 0.0
            if debounce > 0:
                # Debounced: a detection that blinks makes a gauge bounce
                # 1 -> 0 -> 1 thirty times a minute, and every re-rise would
                # add again. So the total follows a HELD level, and the hold
                # is CUMULATIVE: time spent above the level accumulates toward
                # debounce_s, a blink back down only pauses the clock, and the
                # pending change is abandoned only when the value stays back
                # at the old level for debounce_s. A strictly-continuous hold
                # was tried first and froze real totals -- a second violator
                # whose detection blinks every second could never hold 2s
                # unbroken, so nothing ever counted. rise_min is the lowest
                # value seen while above the level, so a wobbling rise
                # credits only what it actually reached throughout.
                if st["level"] is None:
                    st["level"] = value
                if st.get("last_t") is None:
                    st["last_t"] = now
                dt = min(1.0, max(0.0, now - st["last_t"]))
                st["last_t"] = now
                level = st["level"]
                if value > level:
                    if not st.get("rise_t"):
                        st["rise_min"] = value
                    st["rise_t"] = st.get("rise_t", 0.0) + dt
                    st["rise_gap"] = 0.0
                    st["rise_min"] = min(st["rise_min"], value)
                    st["drop_gap"] = st.get("drop_gap", 0.0) + dt
                    if st["drop_gap"] >= debounce:
                        st["drop_t"] = 0.0
                    if st["rise_t"] >= debounce:
                        if fn.params.get("mode") == "sum":
                            st["total"] += st["rise_min"] - level
                        elif level <= 0 < st["rise_min"]:
                            st["total"] += 1
                        st.update(level=st["rise_min"], rise_t=0.0)
                elif value < level:
                    if not st.get("drop_t"):
                        st["drop_max"] = value
                    st["drop_t"] = st.get("drop_t", 0.0) + dt
                    st["drop_gap"] = 0.0
                    st["drop_max"] = max(st["drop_max"], value)
                    st["rise_gap"] = st.get("rise_gap", 0.0) + dt
                    if st["rise_gap"] >= debounce:
                        st["rise_t"] = 0.0
                    if st["drop_t"] >= debounce:
                        st.update(level=st["drop_max"], drop_t=0.0)
                else:
                    st["rise_gap"] = st.get("rise_gap", 0.0) + dt
                    st["drop_gap"] = st.get("drop_gap", 0.0) + dt
                    if st["rise_gap"] >= debounce:
                        st["rise_t"] = 0.0
                    if st["drop_gap"] >= debounce:
                        st["drop_t"] = 0.0
            else:
                prev = st["prev"]
                if prev is not None:
                    if fn.params.get("mode") == "sum":
                        # Positive growth only: a source that resets (an app
                        # restart zeroes its counter) must not subtract what it
                        # already contributed.
                        if value > prev:
                            st["total"] += value - prev
                    elif prev <= 0 < value:
                        # count: one per rising edge -- "it fired again".
                        st["total"] += 1
            st["prev"] = value
            # The Reset wire: a 0 -> 1 edge starts the total again from zero.
            reset_key = str(fn.params.get("reset_key") or "")
            if reset_key:
                try:
                    rv = float(variables.get(reset_key, 0) or 0)
                except (TypeError, ValueError):
                    rv = 0.0
                if st.get("reset_prev") is not None and st["reset_prev"] <= 0 < rv:
                    st.update(total=0.0, prev=value, level=value,
                              rise_t=0.0, drop_t=0.0, rise_gap=0.0, drop_gap=0.0)
                st["reset_prev"] = rv
            if fn.params.get("resume"):
                kept = self._saved_totals.get(fn.id)
                if not kept or kept["total"] != st["total"] or kept["day"] != st["day"]:
                    self._saved_totals[fn.id] = {"total": st["total"],
                                                 "day": st["day"]}
                    self._totals_dirty = True
            self._stats[f"fn_{fn.id}_total"] = round(st["total"], 4)
            variables[f"fn_{fn.id}_total"] = st["total"]

        for fn in self.functions:
            if fn.type != "expr":
                continue
            value = eval_expr(fn.expr_tree, variables)
            # A rule that picked a word publishes the word: the stat IS the
            # variable's value, and downstream (a column, a card, another
            # rule's compare) reads it as text.
            self._stats[f"fn_{fn.id}_value"] = (value if isinstance(value, str)
                                                 else round(value, 4))
            variables[f"fn_{fn.id}_value"] = value

        self._collect_live_oids(detections, events, zones, frame_w, frame_h)
        self._publish_kept(now)
        fired = self._check_triggers(now)
        self._kick_modbus(fired)
        # The per-object alerts raised earlier this frame are fires too
        # (_check_triggers counts its own): an MQTT Publish reads the beat.
        for event in self._pending:
            if event.get("type") == "alert":
                _beat(event.get("id"))
        return fired + self._pending

    def _kick_modbus(self, fired):
        """Wake every triggered Modbus Read (interval 0) whose trigger ROSE
        this tick: the Inject's `fired`, the Schedule's `due`, a rule's
        primary value going truthy, or -- through a Condition -- that rule's
        alert among `fired`. Level is remembered in the module-level reader
        state, so a trigger that stays high reads once, not once per tick,
        and several engines seeing the same press kick once between them.
        """
        alerted = {str(e.get("id")) for e in fired}
        for fn in self.functions:
            if fn.type != "modbus_read" or not fn.params.get("trigger") \
                    or _read_every(fn.params) > 0:
                continue
            trig = fn.params["trigger"]
            if trig.get("metric") == "alert":
                high = trig.get("fn") in alerted
            else:
                level = self._stats.get(f"fn_{trig.get('fn')}_{trig.get('metric')}")
                try:
                    high = bool(float(level))
                except (TypeError, ValueError):
                    high = bool(level)
            with _modbus_lock:
                st = _modbus_state.get(fn.id)
                if st is None:
                    continue
                was = st.get("trig_high", False)
                st["trig_high"] = high
                if high and not was:
                    st["kick"].set()

    def _publish_kept(self, now):
        """The KEPT tallies of the rules sitting out while the AI is off.

        A rule a Detection Stream feeds publishes nothing live with the AI
        off (a count "now" of 0 would be a guess), but what it has already
        counted is real: today's tally, a Running Total, the objects seen so
        far. Those are published as they stand -- not advanced, not zeroed --
        and named in `kept_keys` so the app can tell the master which numbers
        are held rather than live (no history sample; an "AI off" badge)."""
        self._kept = set()
        # getattr: an embedder (and some tests) build an engine with only
        # the state their rules need.
        inactive = getattr(self, "_inactive", None)
        if not inactive:
            return
        before = set(self._stats)
        for fn in getattr(self, "_all", self.functions):
            if fn.id not in inactive:
                continue
            prefix = f"fn_{fn.id}_"
            if self._daily.get(fn.id):
                self._publish_daily(fn.id, prefix, now)
            st = self._totals.get(fn.id)
            if isinstance(st, dict):
                self._stats[prefix + "total"] = round(st.get("total") or 0, 4)
            st = self._unique.get(fn.id)
            if isinstance(st, dict) and isinstance(st.get("seen"), set):
                self._stats[prefix + "total"] = len(st["seen"])
        self._kept = set(self._stats) - before

    def kept_keys(self):
        """The keys _publish_kept added this frame (held, not live)."""
        return set(getattr(self, "_kept", ()) or ())

    def _crossers(self, fid):
        """Is rule `fid` about a LINE -- so its objects are the ones crossing
        this frame, not the ones standing somewhere?"""
        for fn in self.functions:
            if fn.id == fid:
                return bool(fn.params.get("line"))
        return False

    def _collect_live_oids(self, detections, events, zones, frame_w, frame_h):
        """Fill _live_oids for every rule some trigger's `oid_source` names.

        Run AFTER the rule loop, on purpose: a counter with a minimum stay
        stamps `zone_wait` / `count_wait` on the detections it is still
        waiting on as it runs, and somebody the counter does not count yet
        is not somebody this Trigger should announce either.
        """
        self._live_oids = {}
        wanted = {t["oid_source"] for fn in self.functions
                  for t in (fn.triggers or ()) if t.get("oid_source")}
        if not wanted:
            return
        by_id = {fn.id: fn for fn in self.functions}
        for fid in wanted:
            src = by_id.get(fid)
            if src is None:
                continue
            ids = set()
            line = src.params.get("line")
            if line:
                # A line rule is about crossings, and a crossing is a moment:
                # who crossed THIS frame.
                for ev in (events or ()):
                    tid = ev.get("track_id")
                    if tid is not None and ev.get("zone") == line \
                            and src.accepts(ev.get("label", ""), ev.get("model")):
                        ids.add(tid)
            else:
                zone_id = str(src.params.get("zone") or "")
                zone = self._zone_by_id(zones, zone_id) if zone_id else None
                for det in (detections or ()):
                    tid = det.get("track_id")
                    if tid is None:
                        continue
                    if zone_id and zone is None:
                        break                   # a zone rule whose zone is gone
                    if not self._scoped(src, det, zone, frame_w, frame_h):
                        continue
                    if zone_id:
                        if zone_id in (det.get("zone_wait") or ()):
                            continue
                    elif det.get("count_wait"):
                        continue
                    ids.add(tid)
            self._live_oids[fid] = ids

    # -- function bodies ------------------------------------------------------

    def _scoped(self, fn, det, zone, frame_w, frame_h):
        """Is this detection in scope for a rule whose zone is OPTIONAL?

        With a zone, "inside it". Without one, "on camera and of a class this
        rule watches" -- which is what a clock with no place measures. Kept
        apart from _inside, which answers a zone question and must go on
        saying no when there is no zone: a COUNTER with no zone would
        otherwise silently start counting the whole frame.
        """
        if zone is not None:
            return self._inside(fn, det, zone, frame_w, frame_h)
        return fn.accepts(det.get("label", ""), det.get("model"))

    def _inside(self, fn, det, zone, frame_w, frame_h):
        if zone is None or not fn.accepts(det.get("label", ""), det.get("model")):
            return False
        # Same per-zone containment mode (feet/center/full/touch) the
        # ZoneEngine uses, so a function and the zone overlay never disagree.
        rect = zones_mod.norm_rect(det.get("box"), frame_w, frame_h)
        return zone.hits(rect, self.anchor)

    @property
    def departed(self):
        """Counting-rule stays that ended in the last update."""
        return list(self._departed)

    def restate(self, stats, clock_id, total):
        """`stats` as they would read had a Cycle Time's clock reached `total`:
        its live_s (and max_s) at that value, and every Formula worked out again
        from it. What a Data Table row waiting for the stay to end records, so a
        column wired to the clock (or to a Formula on it, such as "time minus the
        limit") holds the FINISHED time, not the moment the alert fired."""
        out = dict(stats)
        try:
            total = float(total)
        except (TypeError, ValueError):
            return out
        out[f"fn_{clock_id}_live_s"] = round(total, 4)
        top = f"fn_{clock_id}_max_s"
        try:
            out[top] = round(max(float(out.get(top) or 0), total), 4)
        except (TypeError, ValueError):
            out[top] = round(total, 4)
        for _ in range(4):                      # a Formula on a Formula
            for fn in self.functions:
                if fn.type != "expr":
                    continue
                try:
                    value = eval_expr(fn.expr_tree, out)
                except Exception:               # noqa: BLE001 - keep what it had
                    continue
                out[f"fn_{fn.id}_value"] = (value if isinstance(value, str)
                                            else round(value, 4))
        return out

    def _past_limit(self, fn, who, now):
        """`who`, cut to the objects whose OWN stay is past the limit, for a
        rule that tests a Cycle Time's live_s (params timer_fn / timer_s).
        live_s is the longest stay running, so over the limit it names a
        newcomer at one second just as well as the one who earned it."""
        clock, limit = fn.params.get("timer_fn"), fn.params.get("timer_s")
        if not who or not clock or not limit:
            return who
        limit = float(limit) + float(fn.params.get("timer_tol_s") or 0)
        open_ = (self._dwell.get(clock) or {}).get("open")
        if open_ is None:
            return who
        return {t for t in who
                if t in open_ and now - open_[t] >= float(limit)}

    def presence(self, fn_id, tid, now=None):
        """What a counting rule knows of ONE object's time on camera:
        {"threshold_s": its "must stay this long" setting, "elapsed_s": how
        long this object has been there}, or None when the rule has no such
        timer or has not seen the object. Real data only."""
        now = time.time() if now is None else now
        fn = next((f for f in self.functions if f.id == fn_id), None)
        clock = fn.params.get("timer_fn") if fn is not None else None
        if clock:
            # A test of a Cycle Time's live_s against a limit: the object's
            # own stay on that clock, against that limit.
            t0 = ((self._dwell.get(clock) or {}).get("open") or {}).get(tid)
            if t0 is None or not fn.params.get("timer_s"):
                return None
            out = {"fn": clock, "threshold_s": float(fn.params["timer_s"]),
                   "elapsed_s": round(max(0.0, now - t0), 2)}
            if fn.params.get("timer_tol_s"):
                out["tolerance_s"] = float(fn.params["timer_tol_s"])
            return out
        entry = (self._member.get(fn_id) or {}).get(tid)
        if fn is None or entry is None:
            return None
        enter_after, _leave = self._timing(fn)
        if enter_after <= 0:
            return None
        return {"threshold_s": enter_after,
                "elapsed_s": round(max(0.0, now - entry["since"]), 2)}

    def _timing(self, fn):
        """(enter_after_s, leave_after_s) for a zone rule. (0, 0) = real time."""
        def secs(key):
            try:
                return max(0.0, min(3600.0, float(fn.params.get(key) or 0)))
            except (TypeError, ValueError):
                return 0.0
        return secs("enter_after_s"), secs("leave_after_s")

    def _timed_members(self, fn, detections, now, keep, zone_id=""):
        """The track ids that COUNT, by this rule's timing. `keep(det)` says
        which detections are in scope -- inside a zone, or simply on camera.

        Three questions an installer actually has, in one place:

          * real time      -- what is there right now. enter=0, leave=0.
          * after a timer  -- something loitering counts, something walking
                              through does not. enter_after_s.
          * keep counting  -- a tracker that drops an object for a frame, or
                             a person who steps a foot outside, must not make
                             the count flicker. leave_after_s.

        Returns None when the rule is plain real time, which tells the caller
        to count DETECTIONS instead of members -- that path needs no tracker
        and is what a box with tracking switched off relies on.

        Objects in scope that do NOT count yet are marked on the detection
        itself (`zone_wait` for a zone rule, `count_wait` for a frame rule),
        the same way app/zones.py stamps membership. Everything downstream of
        detection -- a Data Table above all -- already carries the detections,
        so the mark travels with no new plumbing, and a table wired to a
        counter can refuse to file a row for somebody the counter is still
        waiting on. Without it the number waits the minimum stay and the table
        does not, which reads as the setting being ignored.
        """
        enter_after, leave_after = self._timing(fn)
        if not enter_after and not leave_after:
            return None
        state = self._member.setdefault(fn.id, {})
        seen = set()
        scoped = []
        for det in (detections or ()):
            if not keep(det):
                continue
            tid = det.get("track_id")
            scoped.append((det, tid))
            if tid is None:
                # In scope, but the tracker has not confirmed it. It cannot
                # count -- these rules are questions about one object over
                # time -- so it waits with the rest.
                continue
            seen.add(tid)
            entry = state.get(tid)
            if entry is None:
                state[tid] = {"since": now, "last": now}
            else:
                entry["last"] = now

        # Forget anything that has been away longer than the grace, so the
        # dict cannot grow for the life of the process.
        for tid in list(state):
            if tid not in seen and (now - state[tid]["last"]) > max(leave_after, 1.0):
                gone = state.pop(tid)
                self._departed.append({"fn": fn.id, "track": tid,
                                       "total_s": round(gone["last"] - gone["since"], 2)})

        members = []
        for tid, entry in state.items():
            if (now - entry["since"]) < enter_after:
                continue                    # not there long enough yet
            if tid in seen or (now - entry["last"]) <= leave_after:
                members.append(tid)

        # Mark the ones still waiting, on the detection (see the docstring).
        counting = set(members)
        for det, tid in scoped:
            if tid is not None and tid in counting:
                continue
            if zone_id:
                waiting = det.setdefault("zone_wait", [])
                if zone_id not in waiting:
                    waiting.append(zone_id)
            else:
                det["count_wait"] = True
        return members

    def _zone_members(self, fn, detections, zones, frame_w, frame_h, now):
        """`_timed_members` scoped to this rule's zone."""
        zone = self._zone_by_id(zones, fn.params.get("zone"))
        return self._timed_members(
            fn, detections, now,
            lambda det: self._inside(fn, det, zone, frame_w, frame_h),
            zone_id=str(fn.params.get("zone") or ""))

    def _zone_count(self, fn, detections, zones, frame_w, frame_h, now=None):
        members = self._zone_members(fn, detections, zones, frame_w, frame_h,
                                     time.time() if now is None else now)
        if members is not None:
            return len(members)
        zone = self._zone_by_id(zones, fn.params.get("zone"))
        return sum(1 for d in (detections or ())
                   if self._inside(fn, d, zone, frame_w, frame_h))

    @staticmethod
    def _object_triggers(fn):
        """This rule's thresholds that are about ONE OBJECT'S clock.

        Only a Cycle Time with OID on, and only on `live_s` -- the metric that
        means "the stay running now". Everything else it publishes is an
        aggregate over the zone (how many are inside, the average, the record)
        and a per-object reading of those would be a different number, not a
        better one.

        Empty when OID is off, which is every Cycle Time that has not asked to
        know who: the ordinary aggregate trigger runs and nothing changes.
        """
        if fn.type != "zone_dwell" or not fn.params.get("stamp_events"):
            return ()
        return [t for t in (fn.triggers or ())
                if t.get("metric") == "live_s" and not t.get("mode")]

    def _update_dwell(self, fn, prefix, detections, tracks, live_ids,
                      zones, frame_w, frame_h, now):
        st = self._dwell.setdefault(fn.id, {"open": {}, "n": 0,
                                            "sum": 0.0, "max": 0.0,
                                            "fired": set()})
        st.setdefault("fired", set())   # state written before per-object alerts
        zone = self._zone_by_id(zones, fn.params.get("zone"))
        inside_now = set()
        for det in (detections or ()):
            tid = det.get("track_id")
            # _scoped, not _inside: this clock runs on the whole view when no
            # zone is wired ("how long has this been on camera").
            if tid is None or not self._scoped(fn, det, zone, frame_w, frame_h):
                continue
            inside_now.add(tid)
            st["open"].setdefault(tid, now)

        # Close out every stay that ended: the object left the zone, or its
        # track expired. Its dwell joins the completed aggregates.
        for tid in list(st["open"]):
            if tid in inside_now and tid in live_ids:
                continue
            entered = st["open"].pop(tid)
            dwell = now - entered
            if dwell > 0.5:             # sub-half-second blips are tracker noise
                st["n"] += 1
                st["sum"] += dwell
                st["max"] = max(st["max"], dwell)
                # "When did it arrive, when did it leave" -- opt-in, because a
                # busy zone would otherwise emit an event per object per pass.
                if fn.params.get("stamp_events"):
                    self._pending.append({
                        "id": fn.id, "type": "info", "severity": "info",
                        "message": f"{fn.name}: object {tid} stayed {dwell:.1f}s",
                        "value": round(dwell, 2),
                        "detail": {"track": tid,
                                   "entered_at": round(entered, 2),
                                   "exited_at": round(now, 2),
                                   "duration_s": round(dwell, 2)},
                    })
            st["fired"].discard(tid)    # its next visit starts the clock again

        # PER-OBJECT alerting. With OID on, "in the zone longer than T" is a
        # question about each object, not about the zone: one person already
        # past the threshold must not answer it for somebody who has just
        # walked in. The aggregate live_s cannot say that -- it is the longest
        # stay running, so it stays over the line while a newcomer is at one
        # second, and the moment that newcomer earns is never raised at all.
        #
        # So each object gets the threshold applied to ITS own clock, and
        # fires once per visit as it crosses. The stat stays what it was;
        # _check_triggers skips this trigger so nobody is told twice.
        for trig in self._object_triggers(fn):
            cmp_fn = _CMP_FUNCS.get(trig.get("op") or ">") or _CMP_FUNCS[">"]
            for tid, t0 in st["open"].items():
                elapsed = now - t0
                if tid in st["fired"] or not cmp_fn(elapsed, trig["value"]):
                    continue
                st["fired"].add(tid)
                self._pending.append({
                    "id": fn.id, "type": "alert",
                    "severity": trig.get("severity") or "warn",
                    "message": (trig.get("message")
                                or f"{fn.name}: object {tid} past "
                                   f"{trig['value']:g}s"),
                    "value": round(elapsed, 2),
                    # WHICH object earned this alarm, on the event itself.
                    # An output after it (a Snapshot, a clip) can then stamp
                    # the picture with the object it is a picture of --
                    # detail carries it too, for the log and the table.
                    "source_oid": tid,
                    # `threshold_s` is the line this object just crossed, so a
                    # photo taken on this alarm can say how far over it went
                    # (app/snapmeta.py); it changes nothing the rule decides.
                    "detail": {"track": tid, "duration_s": round(elapsed, 2),
                               "threshold_s": float(trig["value"])},
                })
            # An object that drops back under the line may earn the alert
            # again -- a threshold that never re-arms is a latch, and a
            # re-entry is a new visit.
            for tid in list(st["fired"]):
                t0 = st["open"].get(tid)
                if t0 is not None and not cmp_fn(now - t0, trig["value"]):
                    st["fired"].discard(tid)

        current = [now - t0 for t0 in st["open"].values()]
        live_max = max(current) if current else 0.0
        # The object `live_s` is about -- the longest stay running now, which
        # is the one a "too long in the zone" alarm fires on.
        if st["open"]:
            self._last_oid[fn.id] = min(st["open"], key=st["open"].get)
        else:
            self._last_oid.pop(fn.id, None)
        self._stats[prefix + "current"] = len(current)
        self._stats[prefix + "avg_s"] = round(st["sum"] / st["n"], 1) if st["n"] else 0.0
        self._stats[prefix + "max_s"] = round(max(st["max"], live_max), 1)
        # Longest CURRENT stay -- unlike max_s it resets when the object
        # leaves, so "in the zone longer than T" triggers re-arm per object.
        self._stats[prefix + "live_s"] = round(live_max, 1)

    def _update_countdown(self, fn, prefix, detections, zones, frame_w,
                          frame_h, now):
        """A time BUDGET for the zone: starts at start_s and each arrival
        spends deduct_s of it. remaining_s is what a "time is up" alert
        watches. Re-entering counts as a new arrival -- the budget is about
        visits, not about distinct visitors. reset="daily" (the default)
        refills the budget at local midnight; "never" keeps spending until
        the app restarts."""
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        st = self._countdown.setdefault(
            fn.id, {"inside": set(), "used": 0.0, "entries": 0, "day": day})
        if str(fn.params.get("reset") or "daily") != "never" and st["day"] != day:
            st.update(used=0.0, entries=0, day=day, inside=set())
        zone = self._zone_by_id(zones, fn.params.get("zone"))
        members = set()
        for det in (detections or ()):
            tid = det.get("track_id")
            # ...and so does the budget: with no zone, every object that
            # appears on camera is an arrival spending from it.
            if tid is not None and self._scoped(fn, det, zone, frame_w, frame_h):
                members.add(tid)
        arrived = len(members - st["inside"])
        st["inside"] = members
        if arrived:
            st["entries"] += arrived
            try:
                st["used"] += arrived * max(0.0, float(fn.params.get("deduct_s", 1)))
            except (TypeError, ValueError):
                st["used"] += arrived
        try:
            start_s = float(fn.params.get("start_s") or 0)
        except (TypeError, ValueError):
            start_s = 0.0
        self._stats[prefix + "remaining_s"] = round(max(0.0, start_s - st["used"]), 1)
        self._stats[prefix + "used_s"] = round(min(start_s, st["used"]), 1)
        self._stats[prefix + "entries"] = st["entries"]

    def _update_occupancy(self, fn, prefix, detections, zones, frame_w,
                          frame_h, now):
        """How long the zone stays OCCUPIED, as one clock for the place: it
        starts when the first object enters and stops when the last one
        leaves. occupied_s is the running stretch (0 while empty), last_s the
        previous completed stretch, total_s everything accumulated, sessions
        how many times it went from empty to occupied."""
        st = self._zocc.setdefault(
            fn.id, {"since": None, "last": 0.0, "total": 0.0, "sessions": 0})
        zone = self._zone_by_id(zones, fn.params.get("zone"))
        occupied = any(self._inside(fn, det, zone, frame_w, frame_h)
                       for det in (detections or ()))
        if occupied and st["since"] is None:
            st["since"] = now
            st["sessions"] += 1
        elif not occupied and st["since"] is not None:
            st["last"] = now - st["since"]
            st["total"] += st["last"]
            st["since"] = None
        live = (now - st["since"]) if st["since"] is not None else 0.0
        self._stats[prefix + "occupied_s"] = round(live, 1)
        self._stats[prefix + "last_s"] = round(st["last"], 1)
        self._stats[prefix + "total_s"] = round(st["total"] + live, 1)
        self._stats[prefix + "sessions"] = st["sessions"]
        self._stats[prefix + "active"] = 1 if occupied else 0

    def _update_object_detect(self, fn, prefix, detections, zones,
                              frame_w, frame_h, now=None):
        """Is one of the chosen objects HERE -- in a zone, or anywhere on
        camera when the rule carries no zone.

        Three numbers, because three questions get asked of the same watch:
        `found` (1/0) is what an alert, a snapshot or a light waits on and
        needs no threshold to reason about; `count` is how many; `arrived` is
        how many appeared since the last frame, which is the "something just
        turned up" spike a Data Table row or an event wants.

        Arrivals are counted by track id, so an object standing still is not
        an arrival every frame. Objects the tracker has not confirmed yet
        carry no id -- they are counted in `found`/`count` (they are visibly
        there) but cannot be arrivals, which is what keeps a one-frame ghost
        from firing an event.

        The counters' timing answers here too: with enter_after_s set, a
        forklift driving through the frame is not "present"; with
        leave_after_s set, a tracker dropping it for a frame does not make
        presence flicker off and on. Real time -- both 0 -- counts detections
        and needs no tracker, exactly as before.
        """
        now = time.time() if now is None else now
        zid = fn.params.get("zone")
        zone = self._zone_by_id(zones, zid) if zid else None
        # _inside applies the class/model filter itself, and uses the zone's
        # own containment mode so this and the overlay agree.
        keep = ((lambda det: self._inside(fn, det, zone, frame_w, frame_h))
                if zid
                else (lambda det: fn.accepts(det.get("label", ""),
                                             det.get("model"))))
        # None = plain real time, which is the path a box with tracking off
        # relies on: count what the frame shows.
        members = self._timed_members(fn, detections, now, keep,
                                      zone_id=str(zid or ""))
        st = self._objdet.setdefault(fn.id, {"ids": set(), "labels": {}})
        st.setdefault("labels", {})         # a state dict from an older run
        # What class each tracked object is, remembered while it is visible.
        # A member inside its leave grace is NOT in this frame's detections --
        # that is the point of the grace -- so without this the total would say
        # one object and every per-class port would say zero.
        for det in (detections or ()):
            tid = det.get("track_id")
            if tid is not None and keep(det):
                st["labels"][tid] = zones_mod._slug(det.get("label", ""))
        counted = []                        # (track id or None, class slug)
        if members is None:
            # Plain real time: what the frame shows, no tracker needed.
            for det in (detections or ()):
                if keep(det):
                    counted.append((det.get("track_id"),
                                    zones_mod._slug(det.get("label", ""))))
        else:
            # By MEMBERSHIP, which is what the timing means: one still waiting
            # out its minimum stay does not count, and one inside its leave
            # grace still does even though this frame cannot see it.
            counted = [(tid, st["labels"].get(tid, "")) for tid in members]
            st["labels"] = {tid: lb for tid, lb in st["labels"].items()
                            if tid in set(members)}
        ids = {tid for tid, _lb in counted if tid is not None}
        newcomers = ids - st["ids"]
        arrived = len(newcomers)
        st["ids"] = ids
        if fn.params.get("stamp_events"):
            # OID on the node: an event per ARRIVAL naming WHICH object -- its
            # track id and its class -- which is what a Data Table row per
            # object is written from (the row reads detail.track, exactly as
            # it does for a Cycle Time's stamped stay). Only tracked objects
            # can arrive, so a one-frame ghost never writes a row.
            #
            # "Arrived" is judged against _objdet_seen, which outlives this
            # engine: a workflow save or a zone edit rebuilds the engine, and
            # judged against the engine's own state every object standing in
            # view arrived again, with the same id, one row each. And an
            # object must have been GONE for ARRIVE_GAP_S before it can
            # arrive again -- a tracker blinking for one frame is not a visit.
            seen = _objdet_seen.setdefault(
                (getattr(self, "_scope", ""), fn.id), {})
            fresh = [tid for tid in ids
                     if now - seen.get(tid, float("-inf")) >= ARRIVE_GAP_S]
            for tid in ids:
                seen[tid] = now
            for tid in [t for t, at in seen.items() if now - at > SEEN_KEEP_S]:
                seen.pop(tid, None)
            # The CLASS NAME as the model says it ("cell phone"), the way
            # every other row records detection_label -- not the port slug.
            raw_of = {det.get("track_id"): str(det.get("label") or "")
                      for det in (detections or ()) if keep(det)}
            label_of = dict(counted)
            for tid in sorted(fresh, key=str):
                label = raw_of.get(tid) or label_of.get(tid) or ""
                self._pending.append({
                    "id": fn.id, "type": "info", "severity": "info",
                    "message": f"{fn.name}: {label or 'object'} {tid} arrived",
                    "value": 1,
                    "source_oid": tid,
                    "detail": {"track": tid, "label": label},
                })
            # ...and an Alert on this watch names who it is about.
            if ids:
                self._last_oid[fn.id] = min(ids, key=str)
            else:
                self._last_oid.pop(fn.id, None)
        self._stats[prefix + "found"] = 1 if counted else 0
        self._stats[prefix + "count"] = len(counted)
        self._stats[prefix + "arrived"] = arrived
        if fn.params.get("anomaly"):
            # GOOD / NG: 0 while nothing is found, 1 while something is. The
            # verdict card's own convention (0 calm, 1 alerting).
            self._stats[prefix + "verdict"] = 1 if counted else 0
        # ...and per CLASS, so "what is it seeing" is readable off the node
        # and bindable by a card: fn_<id>_person, fn_<id>_chair. Zeroed every
        # frame for every configured class -- an absent chair must read 0,
        # not whatever it read last.
        if fn.classes:
            per = {zones_mod._slug(c): 0 for c in fn.classes}
            for _tid, key in counted:
                if key in per:
                    per[key] += 1
            for key, n in per.items():
                if key:
                    self._stats[f"{prefix}{key}"] = n

    #: Grid limits. Twelve by eight is a floor plan a person can read at a
    #: glance; past the cap the cells are smaller than the objects standing in
    #: them, and every cell is a published stat key.
    HEAT_MAX_COLS = 32
    HEAT_MAX_ROWS = 24

    def _update_heatmap(self, fn, prefix, detections, zones, frame_w, frame_h,
                        now):
        """WHERE objects are, as a coarse grid of counts.

        One cell per patch of the view; an object standing in a patch adds to
        it, at most once every `interval_s` (a person standing still for ten
        minutes is one person, not eighteen thousand samples). The position
        used is the BOTTOM CENTRE of the box -- where the object is standing,
        not where its head is, which is the difference between a floor plan
        and a picture of hats.

        Published as one stat per cell (`fn_<id>_c<row>_<col>`), the same way
        an object watch publishes one per class: the dashboard widget picks
        them up by prefix, so this needs no new transport and rides the paths
        history and the cards already use.

        `hours` fades the grid instead of clearing it: the map answers "where
        has it been busy lately" with a half-life rather than a hard window,
        so it has no cliff where the busiest spot vanishes at a tick of the
        clock. 0 keeps everything since the app started.
        """
        p = fn.params
        cols = max(2, min(self.HEAT_MAX_COLS, int(p.get("cols") or 12)))
        rows = max(2, min(self.HEAT_MAX_ROWS, int(p.get("rows") or 8)))
        every = max(0.0, min(600.0, float(p.get("interval_s") or 1.0)))
        hours = max(0.0, min(168.0, float(p.get("hours") or 0)))
        zid = p.get("zone")
        zone = self._zone_by_id(zones, zid) if zid else None
        st = self._heat.setdefault(fn.id, {"grid": [0.0] * (cols * rows),
                                           "cols": cols, "rows": rows,
                                           "seen": {}, "at": now})
        if st["cols"] != cols or st["rows"] != rows:
            # The grid was resized on the canvas: the old counts describe
            # patches that no longer exist, and rescaling them would invent
            # detail that was never measured.
            st.update(grid=[0.0] * (cols * rows), cols=cols, rows=rows,
                      seen={}, at=now)
        if hours:
            # Exponential fade, applied on the clock rather than per sample so
            # a busy scene and a quiet one fade at the same rate.
            dt = max(0.0, now - st["at"])
            if dt > 0:
                st["grid"] = [v * math.exp(-dt / (hours * 3600.0))
                              for v in st["grid"]]
        st["at"] = now
        grid = st["grid"]
        for det in (detections or ()):
            if not self._scoped(fn, det, zone, frame_w, frame_h):
                continue
            tid = det.get("track_id")
            if tid is not None and every:
                if now - st["seen"].get(tid, 0.0) < every:
                    continue
                st["seen"][tid] = now
            box = det.get("box") or ()
            if len(box) != 4:
                continue
            x1, _y1, x2, y2 = box
            cx = (float(x1) + float(x2)) / 2.0
            cy = float(y2)                      # feet, not head
            c = min(cols - 1, max(0, int(cx / max(1, frame_w) * cols)))
            r = min(rows - 1, max(0, int(cy / max(1, frame_h) * rows)))
            grid[r * cols + c] += 1.0
        # Forget tracks that have not been seen for a while, so the sampling
        # map cannot grow for the life of the process.
        if len(st["seen"]) > 512:
            st["seen"] = {k: v for k, v in st["seen"].items()
                          if now - v < 3600}
        for r in range(rows):
            for c in range(cols):
                value = grid[r * cols + c]
                self._stats[f"{prefix}c{r}_{c}"] = round(value, 2)
        # Two numbers about the map itself, so it can be alerted on and put on
        # a card without a widget: how hot the busiest patch is, and how much
        # of the view has been used at all.
        self._stats[prefix + "hot"] = round(max(grid) if grid else 0.0, 2)
        self._stats[prefix + "spots"] = sum(1 for v in grid if v >= 1.0)

    def _update_frame_count(self, fn, prefix, detections, now=None):
        """How many matching objects are on camera RIGHT NOW -- the whole
        frame, no zone. The live number for "how busy is this view".

        Same timing rules as the zone counter: an object may have to be on
        camera for a moment before it counts, and may keep counting for a
        moment after it goes. A counter that answers "how busy is this view"
        should not report a van that drove past the lens in one frame.
        """
        members = self._timed_members(
            fn, detections, time.time() if now is None else now,
            lambda det: fn.accepts(det.get("label", ""), det.get("model")))
        if members is not None:
            self._stats[prefix + "count"] = len(members)
            return
        self._stats[prefix + "count"] = sum(
            1 for det in (detections or ())
            if fn.accepts(det.get("label", ""), det.get("model")))

    def _update_frame_unique(self, fn, prefix, detections, now):
        """How many DISTINCT objects have been on camera, by track id.

        Same identity rule as the zone counter: one object counts once
        however long it stays, and the per-day tally is what makes
        today/week/month answerable after a restart. The all-time `total` is
        this run's set (identity cannot survive a restart); the day tallies
        are the numbers that persist.
        """
        st = self._unique.setdefault(fn.id, {"seen": set()})
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        new = 0
        for det in (detections or ()):
            tid = det.get("track_id")
            if tid is None or not fn.accepts(det.get("label", ""), det.get("model")):
                continue
            if tid in st["seen"]:
                continue
            st["seen"].add(tid)
            new += 1
        self._stats[prefix + "total"] = len(st["seen"])
        self._stats[prefix + "new"] = new
        self._bump_daily(fn.id, new, day)
        self._publish_daily(fn.id, prefix, now)

    def _update_unique(self, fn, prefix, detections, zones, frame_w, frame_h,
                       now=None):
        """Unique visitors: how many DISTINCT tracked objects have been inside
        the zone since start. Counts by track_id (app/tracker.py), so nobody
        needs to draw a counting line -- entering the area once is one visit.

        Honesty note: identity comes from the tracker; a person lost for
        longer than its TTL (occlusion, leaving frame) re-enters as a NEW id
        and counts again. Good day-tally numbers, not a biometric census."""
        st = self._unique.setdefault(fn.id, {"seen": set()})
        zone = self._zone_by_id(zones, fn.params.get("zone"))
        # A CONDITIONAL counter (the Zone Counter / a zone's violation
        # action): a new track only counts at a moment when the number inside
        # satisfies <inside> <op> <min_present>. The default ">" with 0 is
        # plain unique visitors -- any presence counts, since 1 > 0.
        try:
            min_present = max(0, int(fn.params.get("min_present") or 0))
        except (TypeError, ValueError):
            min_present = 0
        op = str(fn.params.get("present_op") or ">")
        compare = _CMP_FUNCS.get(op) or _CMP_FUNCS[">"]
        # The same timing rules the realtime mode has: an entrant may need to
        # stay a moment before it is one, and a flicker must not end its
        # visit. `None` back means "no timing configured" -- then every track
        # inside is inside, exactly as before.
        stamp = time.time() if now is None else now
        timed = self._timed_members(
            fn, detections, stamp,
            lambda det: self._inside(fn, det, zone, frame_w, frame_h),
            zone_id=str(fn.params.get("zone") or ""))
        if timed is not None:
            inside = list(timed)
        else:
            inside = []
            for det in (detections or ()):
                tid = det.get("track_id")
                if tid is None or not self._inside(fn, det, zone, frame_w, frame_h):
                    continue
                inside.append(tid)
        # In conditional mode, only ARRIVALS count -- an occupant who was
        # already inside before the zone got crowded is not a new violation.
        # So "A waits, B walks in" is ONE count (B), not two.
        prev = st.setdefault("present", set())
        new = 0
        conditional = min_present > 0 or op != ">"
        if compare(len(inside), min_present):
            for tid in inside:
                if tid in st["seen"] or (conditional and tid in prev):
                    continue
                st["seen"].add(tid)
                new += 1
        st["present"] = set(inside)
        self._stats[prefix + "total"] = len(st["seen"])
        # Spikes on the frames where fresh tracks were counted -- the trigger
        # metric for "fire once per new arrival" (snapshots, MQTT, tallies).
        self._stats[prefix + "new"] = new
        # Per-day tallies, same as the frame counter: `total` is this run's
        # set and dies with the process, today/week/month are on disk.
        self._bump_daily(fn.id, new, time.strftime("%Y-%m-%d",
                                                   time.localtime(stamp)))
        self._publish_daily(fn.id, prefix, stamp)

    def _update_line_count(self, fn, prefix, events, now=None, detections=None,
                           zones=None, frame_w=None, frame_h=None):
        st = self._counts.setdefault(fn.id, {"in": 0, "out": 0, "cross": 0})
        line = fn.params.get("line")
        # `distinct`: count each OBJECT once per direction. A doorway
        # somebody paces through all morning is then one visitor in and one
        # out, not forty crossings. Off by default -- a turnstile really
        # does want every crossing.
        once = bool(fn.params.get("distinct"))
        seen = st.setdefault("seen", {"in": set(), "out": set(),
                                      "cross": set()}) if once else None
        # `momentary` -- LIVE, the default for a new counter: how many
        # objects are ON THE LINE right now, by the way they crossed it.
        # An object counts from the frame its crossing is detected for as
        # long as any part of its box still reaches the line, and drops
        # out the frame it has passed -- so In reads 1 while one person is
        # over the line going in, 2 while two are, 0 when nobody is. Not
        # the running total: a total is a number a Data Table can only log
        # as "the total so far" and the app forgets it on restart, where
        # the live value is logged row by row as it happens (Data Table on
        # change) and the total is the SUM of the stored column. A timed
        # hold was tried (a72c153) and asked off: the object itself is the
        # clock. The running tally is kept underneath and published as
        # total_in / total_out / total_cross only when asked (`totals`).
        momentary = bool(fn.params.get("momentary"))
        now_counts = {"in": 0, "out": 0, "cross": 0}
        for ev in events:
            if ev.get("zone") == line and fn.accepts(ev.get("label", ""),
                                                     ev.get("model")):
                # A PLAIN line (no face) reports "cross" and nothing else --
                # counting it as an entry would invent a direction the
                # operator deliberately did not give it.
                way = ev.get("direction")
                if way not in ("in", "out", "cross"):
                    continue
                if once:
                    tid = ev.get("track_id")
                    # No id, no way to tell this object from the last one.
                    # Counting it anyway would defeat the setting silently,
                    # on exactly the frames where tracking is struggling.
                    if tid is None or tid in seen[way]:
                        continue
                    seen[way].add(tid)
                st[way] += 1
                now_counts[way] += 1
                if momentary and ev.get("track_id") is not None:
                    st.setdefault("on_line", {})[ev["track_id"]] = way
        if momentary:
            src = self._on_line(st, line, now_counts, detections, zones,
                                frame_w, frame_h)
        else:
            src = st
        self._stats[prefix + "in"] = src["in"]
        self._stats[prefix + "out"] = src["out"]
        self._stats[prefix + "net"] = src["in"] - src["out"]
        # Every crossing, whichever way: on a plain line this is the count,
        # on a faced one it is in + out.
        self._stats[prefix + "cross"] = src["in"] + src["out"] + src["cross"]
        # The running tally beside the live count, as ports of the node:
        # total in, total out, inside (in - out) and crossings, so a card
        # or a Math node reads the total off the node. Since the app
        # started: the tally is not kept across a restart (the stored
        # table rows are).
        if momentary:
            self._stats[prefix + "total_in"] = st["in"]
            self._stats[prefix + "total_out"] = st["out"]
            self._stats[prefix + "total_net"] = st["in"] - st["out"]
            self._stats[prefix + "total_cross"] = st["in"] + st["out"] + st["cross"]

    def _on_line(self, st, line, now_counts, detections, zones, frame_w, frame_h):
        """{in, out, cross}: the objects still ON the line, by the way each
        crossed. `on_line` remembers who crossed which way (set at the
        crossing event); an object stays counted while its box touches the
        line and is dropped the frame it no longer does, or its track is
        gone. A crossing with no track id (tracker off) counts for its own
        frame only -- there is no object to follow across the line."""
        on = st.setdefault("on_line", {})
        zone = self._zone_by_id(zones, line)
        still = {}
        if zone is not None and on:
            for det in (detections or ()):
                tid = det.get("track_id")
                if tid is None or tid not in on:
                    continue
                rect = zones_mod.norm_rect(det.get("box"), frame_w, frame_h)
                if zone.touches(rect):
                    still[tid] = on[tid]
        on.clear()
        on.update(still)
        counts = {"in": 0, "out": 0, "cross": 0}
        for way in still.values():
            counts[way] += 1
        # Crossings with no object to follow: this frame's, and only this frame's.
        anon = {way: 0 for way in counts}
        if zone is None:
            # No line to test against (deleted, or none wired): every
            # crossing is a one-frame flag, the honest fallback.
            anon = dict(now_counts)
        for way in counts:
            counts[way] = max(counts[way], anon[way])
        return counts

    def _update_travel(self, fn, prefix, events, live_ids, now):
        st = self._travel.setdefault(fn.id, {"open": {}, "trips": 0, "sum": 0.0,
                                             "last_s": 0.0, "last_speed": 0.0,
                                             "max_s": 0.0, "min_s": 0.0})
        # State written before the slowest/fastest trip were kept.
        st.setdefault("max_s", 0.0)
        st.setdefault("min_s", 0.0)
        line_a, line_b = fn.params.get("line_a"), fn.params.get("line_b")
        distance = float(fn.params.get("distance_m") or 0)
        unit = SPEED_UNITS.get(str(fn.params.get("speed_unit") or "kmh"), 3.6)

        for ev in events:
            tid = ev.get("track_id")
            if tid is None or not fn.accepts(ev.get("label", ""), ev.get("model")):
                continue
            if ev.get("zone") == line_a:
                # (Re)crossing A restarts the clock -- the trip is A's LAST
                # crossing to B's next one.
                st["open"][tid] = now
            elif ev.get("zone") == line_b and tid in st["open"]:
                dt = now - st["open"].pop(tid)
                if 0 < dt <= TRAVEL_CAP_S:
                    st["trips"] += 1
                    st["sum"] += dt
                    st["last_s"] = dt
                    # The record trips. "Latest" answers what just happened;
                    # a bottleneck is a question about the SLOWEST one, and
                    # the average alone hides it.
                    st["max_s"] = max(st["max_s"], dt)
                    st["min_s"] = dt if not st["min_s"] else min(st["min_s"], dt)
                    st["last_speed"] = (distance / dt) * unit if distance else 0.0

        # Drop timers whose track vanished -- a reused id must not complete
        # someone else's trip.
        for tid in list(st["open"]):
            if tid not in live_ids or now - st["open"][tid] > TRAVEL_CAP_S:
                del st["open"][tid]

        avg_dt = (st["sum"] / st["trips"]) if st["trips"] else 0.0
        self._stats[prefix + "trips"] = st["trips"]
        self._stats[prefix + "last_s"] = round(st["last_s"], 2)
        self._stats[prefix + "avg_s"] = round(avg_dt, 2)
        self._stats[prefix + "max_s"] = round(st["max_s"], 2)
        self._stats[prefix + "min_s"] = round(st["min_s"], 2)
        self._stats[prefix + "last_speed"] = round(st["last_speed"], 2)
        self._stats[prefix + "avg_speed"] = round(
            (distance / avg_dt) * unit, 2) if avg_dt and distance else 0.0

    def _update_zone_travel(self, fn, prefix, detections, events, zones,
                            live_ids, frame_w, frame_h, now):
        """Cycle time between two PLACES: how long from reaching the start to
        reaching the end.

        line_travel already timed line A to line B, but a cycle is more often
        bounded by areas -- "from entering the loading bay to leaving the
        inspection cell" -- and mixing the two (start at a line, end in a zone)
        is common enough that both ends accept either kind.

        The clock starts at the FIRST frame an object is at the start and stops
        the first frame it reaches the end, so re-entering the start while a
        trip is open does not restart it: that is the same object still on its
        way, not a new cycle.
        """
        st = self._ztravel.setdefault(fn.id, {"open": {}, "trips": 0, "sum": 0.0,
                                              "last_s": 0.0})
        start, end = fn.params.get("start"), fn.params.get("end")
        start_kind = str(fn.params.get("start_kind") or "area")
        end_kind = str(fn.params.get("end_kind") or "area")

        at_start, at_end = set(), set()
        if start_kind == "area" or end_kind == "area":
            z_start = self._zone_by_id(zones, start) if start_kind == "area" else None
            z_end = self._zone_by_id(zones, end) if end_kind == "area" else None
            for det in (detections or ()):
                tid = det.get("track_id")
                if tid is None:
                    continue
                if z_start is not None and self._inside(fn, det, z_start, frame_w, frame_h):
                    at_start.add(tid)
                if z_end is not None and self._inside(fn, det, z_end, frame_w, frame_h):
                    at_end.add(tid)
        for ev in events:
            tid = ev.get("track_id")
            if tid is None or not fn.accepts(ev.get("label", ""), ev.get("model")):
                continue
            if start_kind == "line" and ev.get("zone") == start:
                at_start.add(tid)
            if end_kind == "line" and ev.get("zone") == end:
                at_end.add(tid)

        for tid in at_start:
            if tid not in at_end:
                st["open"].setdefault(tid, now)
        for tid in at_end:
            t0 = st["open"].pop(tid, None)
            if t0 is None:
                continue
            dt = now - t0
            if 0 < dt <= TRAVEL_CAP_S:
                st["trips"] += 1
                st["sum"] += dt
                st["last_s"] = dt

        # Drop timers whose track vanished -- a reused id must not complete
        # someone else's cycle.
        for tid in list(st["open"]):
            if tid not in live_ids or now - st["open"][tid] > TRAVEL_CAP_S:
                del st["open"][tid]

        self._stats[prefix + "trips"] = st["trips"]
        self._stats[prefix + "last_s"] = round(st["last_s"], 2)
        self._stats[prefix + "avg_s"] = round(st["sum"] / st["trips"], 2) if st["trips"] else 0.0

    def _update_proximity(self, fn, prefix, detections, frame_w, now):
        """Distance between two object classes, drawn as a link line.

        For every A the NEAREST B (box centre to box centre); a pair closer
        than params.max_distance (a fraction of the frame width, so the rule
        survives a resolution change) counts and, with an alert configured,
        fires an event. class_a == class_b is the same-class case -- nearest
        neighbour, self and same-track pairs excluded.

        O(A x B) pairwise; fine at edge detection counts (dozens, not
        thousands).
        """
        p = fn.params
        cls_a, cls_b = str(p.get("class_a") or ""), str(p.get("class_b") or "")
        mod_a, mod_b = str(p.get("model_a") or ""), str(p.get("model_b") or "")
        try:
            max_dist = float(p.get("max_distance") or 0.15)
        except (TypeError, ValueError):
            max_dist = 0.15
        draw = str(p.get("draw_link") or "always")
        st = self._prox.setdefault(fn.id, {"alerts": 0, "cool": {}})

        def side(label, model_want):
            out = []
            for d in (detections or ()):
                if d.get("label") != label:
                    continue
                if model_want and d.get("model") and d["model"] != model_want:
                    continue
                box = d.get("box") or (0, 0, 0, 0)
                out.append((d, (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0))
            return out

        group_a, group_b = side(cls_a, mod_a), side(cls_b, mod_b)
        pairs, nearest_px = 0, None
        limit_px = max_dist * float(frame_w or 1)
        for da, ax, ay in group_a:
            best = None                          # (dist_px, det, bx, by)
            for db, bx, by in group_b:
                if db is da:
                    continue
                ta, tb = da.get("track_id"), db.get("track_id")
                if ta is not None and ta == tb:
                    continue
                dist = math.hypot(bx - ax, by - ay)
                if best is None or dist < best[0]:
                    best = (dist, db, bx, by)
            if best is None:
                continue
            dist, db, bx, by = best
            within = dist <= limit_px
            if within:
                pairs += 1
            if nearest_px is None or dist < nearest_px:
                nearest_px = dist
            if draw == "always" or (draw == "alert" and within):
                self._links.append((int(ax), int(ay), int(bx), int(by),
                                    within, round(dist, 1)))
            if within:
                pair = (da.get("track_id"), db.get("track_id"))
                cooldown = max(0.0, float(p.get("cooldown_s") or 10))
                last = st["cool"].get(pair, 0.0)
                if p.get("severity") is not None and now - last >= cooldown:
                    st["cool"][pair] = now
                    st["alerts"] += 1
                    self._pending.append({
                        "id": fn.id, "type": "alert",
                        "severity": str(p.get("severity") or "warn"),
                        "message": (str(p.get("message") or "").strip()
                                    or f"{fn.name}: {cls_a} within "
                                       f"{max_dist:.0%} of frame width of {cls_b}"),
                        "value": round(dist, 1),
                        # WHICH two objects, not just which two classes. The
                        # pair is already known here (it is what the cooldown
                        # is keyed on); without it on the event, a row
                        # recording "a forklift came too close" cannot say
                        # which forklift.
                        # One of the two has to be the subject; the first is
                        # the one the rule names first (class A).
                        "source_oid": pair[0],
                        "detail": {"class_a": cls_a, "class_b": cls_b,
                                   "distance_px": round(dist, 1),
                                   "track_a": pair[0], "track_b": pair[1]},
                    })
        # Forget cooldown pairs that have not re-fired in a while, so the map
        # cannot grow without bound on a busy scene.
        if len(st["cool"]) > 512:
            cutoff = now - 3600
            st["cool"] = {k: v for k, v in st["cool"].items() if v >= cutoff}

        self._stats[prefix + "pairs"] = pairs
        self._stats[prefix + "nearest_px"] = round(nearest_px, 1) if nearest_px is not None else -1
        self._stats[prefix + "alerts"] = st["alerts"]
        # The condition AS A STATE: 1 while any pair is within the threshold.
        # This is what a verdict/status card binds to; without it the card
        # would need a separate trigger just to know when "too close" is true.
        # An explicit trigger on this function still overwrites it (evaluated
        # after) -- the operator's own condition wins.
        self._stats[prefix + "active"] = 1 if pairs else 0

    def _update_guard(self, fn, prefix, detections, now):
        """A guard bubble around each object of the chosen classes: the box
        grown by margin_pct each side. An object of an alert class that gets
        too close is an intrusion -- counted once per (guard, intruder) track
        pair, rate-limited by cooldown_s.

        params.touch decides what "too close" MEANS:
          "center"  -- the intruder's centre is inside the bubble (default)
          "overlap" -- the two BUBBLES overlap by at least min_overlap_pct of
                       the smaller one; 0 means merely touching counts. This
                       is the "their safety zones are touching" rule, which a
                       centre test cannot express: two objects side by side
                       overlap long before either centre crosses over.

        params.classes = who gets a bubble ([] = every class fn.accepts);
        params.alert_classes = who intrudes ([] = draw only, never counts).
        """
        p = fn.params
        try:
            margin = max(1.0, min(200.0, float(p.get("margin_pct") or 50))) / 100.0
        except (TypeError, ValueError):
            margin = 0.5
        touch = str(p.get("touch") or "center")
        try:
            min_overlap = max(0.0, min(100.0,
                                       float(p.get("min_overlap_pct") or 0))) / 100.0
        except (TypeError, ValueError):
            min_overlap = 0.0
        alert_classes = {str(c) for c in (p.get("alert_classes") or ())}
        draw = bool(p.get("draw", True))
        st = self._guard.setdefault(fn.id, {"count": 0, "cool": {}})

        def grow(box):
            w, h = box[2] - box[0], box[3] - box[1]
            return (box[0] - w * margin, box[1] - h * margin,
                    box[2] + w * margin, box[3] + h * margin)

        guards = []                              # (det, expanded box)
        intruders = []                           # (det, cx, cy, expanded box)
        for d in (detections or ()):
            label = d.get("label", "")
            box = d.get("box") or None
            if box is None:
                continue
            if fn.accepts(label, d.get("model")):
                guards.append((d, grow(box)))
            if label in alert_classes:
                intruders.append((d, (box[0] + box[2]) / 2.0,
                                  (box[1] + box[3]) / 2.0, grow(box)))

        inside_now, worst = 0, 0.0
        for dg, (x1, y1, x2, y2) in guards:
            intruded = False
            for di, cx, cy, ibox in intruders:
                if di is dg:
                    continue
                tg, ti = dg.get("track_id"), di.get("track_id")
                if tg is not None and tg == ti:
                    continue
                if touch == "overlap":
                    frac = _overlap_frac((x1, y1, x2, y2), ibox)
                    worst = max(worst, frac)
                    if frac <= 0 or frac < min_overlap:
                        continue
                elif x1 <= cx <= x2 and y1 <= cy <= y2:
                    worst = max(worst, 1.0)
                else:
                    continue
                intruded = True
                inside_now += 1
                pair = (tg, ti)
                cooldown = max(0.0, float(p.get("cooldown_s") or 10))
                last = st["cool"].get(pair, 0.0)
                if now - last >= cooldown:
                    st["cool"][pair] = now
                    st["count"] += 1
                    # Self-alert only when params carry a severity (hand-written
                    # config). Workflow graphs alert through an Alert/When node
                    # on `active`/`intrusions` instead -- one condition, one
                    # place. The intrusion COUNT above runs either way.
                    if p.get("severity") is not None:
                        self._pending.append({
                            "id": fn.id, "type": "alert",
                            "severity": str(p.get("severity") or "warn"),
                            "message": (str(p.get("message") or "").strip()
                                        or f"{fn.name}: {di.get('label')} entered "
                                           f"the guard zone around {dg.get('label')}"),
                            "value": st["count"],
                            # The INTRUDER is the object this alarm is about
                            # -- a picture of the intrusion is a picture of
                            # them, not of the thing they walked up to.
                            "source_oid": ti,
                            # The two OBJECTS, not just their classes: "a
                            # person entered a bubble" cannot say whose
                            # bubble, or which person, without the ids the
                            # cooldown pair already holds.
                            "detail": {"guard": dg.get("label"),
                                       "intruder": di.get("label"),
                                       "owner_id": tg, "intruder_id": ti},
                        })
            if draw:
                self._bubbles.append((int(x1), int(y1), int(x2), int(y2),
                                      intruded))
        if len(st["cool"]) > 512:
            cutoff = now - 3600
            st["cool"] = {k: v for k, v in st["cool"].items() if v >= cutoff}

        self._stats[prefix + "inside_now"] = inside_now
        self._stats[prefix + "intrusions"] = st["count"]
        # How deep the worst encroachment is right now, as a percentage of the
        # smaller bubble. A number to alert on directly ("more than 30% in"),
        # and 0 when nothing is near.
        self._stats[prefix + "overlap_pct"] = round(worst * 100, 1)
        # 1 while anything is inside a bubble -- same contract as proximity.
        self._stats[prefix + "active"] = 1 if inside_now else 0

    def _update_trails(self, fn, prefix, detections, now):
        """A dotted movement trail behind each tracked object.

        params.classes = which objects leave a trail ([] = every class);
        params.length_s = how long a point survives (the tail's length in
        TIME, not pixels -- a fast object leaves a long trail, a parked one
        a dot). Points are stored only when the object has MOVED a few
        pixels since the last stored point, which is what makes the drawn
        line dotted: dot spacing IS movement.

        Reported: `tracked` = objects currently leaving a trail, `active` =
        1 while any of them is actually moving (same contract as
        proximity/guard, so a status card can bind to it directly).
        """
        p = fn.params
        st = self._trailst.setdefault(fn.id, {"hist": {}})
        hist = st["hist"]
        keep_s = max(1.0, min(120.0, float(p.get("length_s") or 10)))
        try:
            idle_s = max(1.0, min(300.0, float(p.get("idle_s") or 5)))
        except (TypeError, ValueError):
            idle_s = 5.0
        classes = fn.classes
        seen = set()
        moving = 0
        idle = 0
        for det in detections:
            tid = det.get("track_id")
            if tid is None:
                continue
            if classes and str(det.get("label") or "") not in classes:
                continue
            box = det.get("box") or ()
            if len(box) != 4:
                continue
            x = int((box[0] + box[2]) / 2)
            y = int((box[1] + box[3]) / 2)
            seen.add(tid)
            trail = hist.setdefault(tid, [])
            if not trail or (abs(x - trail[-1][0]) + abs(y - trail[-1][1])) >= 5:
                trail.append((x, y, now))
                if len(trail) > 128:
                    del trail[:len(trail) - 128]
            # Moving = a stored point in the last second: the dot spacing
            # rule above means a parked object stores nothing.
            if len(trail) >= 2 and now - trail[-1][2] < 1.0:
                moving += 1
            # Idle = present but no stored movement for idle_s. A trail with
            # one point never moved at all -- its timestamp is its arrival.
            if now - trail[-1][2] >= idle_s:
                idle += 1
        # Age out old points, and whole tracks that left the frame -- a trail
        # must fade behind its object, not hang in the air forever.
        cutoff = now - keep_s
        for tid in list(hist):
            pts = [q for q in hist[tid] if q[2] >= cutoff]
            if pts and (tid in seen or pts[-1][2] >= now - 2.0):
                hist[tid] = pts
            else:
                del hist[tid]
        if bool(p.get("draw", True)):
            for tid, pts in hist.items():
                if len(pts) >= 2:
                    self._trails.append(([(q[0], q[1]) for q in pts],
                                         tid in seen))
        self._stats[prefix + "tracked"] = len(seen)
        self._stats[prefix + "active"] = 1 if moving else 0
        self._stats[prefix + "idle"] = idle

    def trails(self):
        """Movement trails this frame, frame pixels: [([(x, y), ...],
        live_bool)]. For camera.annotate() -- drawn as dotted lines."""
        return list(self._trails)

    def links(self):
        """Proximity link lines this frame, frame pixels: [(x1,y1,x2,y2,
        within_threshold, dist_px)]. For camera.annotate()."""
        return list(self._links)

    def bubbles(self):
        """Guard bubbles this frame, frame pixels: [(x1,y1,x2,y2, intruded)]."""
        return list(self._bubbles)

    # -- triggers -------------------------------------------------------------

    def _trigger_condition(self, trig, value, st):
        """Whether this trigger's raw condition holds this frame. Edge modes
        (rising/falling/changed) compare against st["prev"], the metric's
        value LAST frame."""
        mode = trig.get("mode") or ""
        prev = st.get("prev")
        if mode == "is_true":
            return value != 0
        if mode == "is_false":
            return value == 0
        if mode == "rising":
            return prev is not None and prev == 0 and value != 0
        if mode == "falling":
            return prev is not None and prev != 0 and value == 0
        if mode == "changed":
            return prev is not None and value != prev
        if mode == "in_range":
            lo, hi = sorted((trig["value"], trig["value2"]))
            return lo <= value <= hi
        if mode == "out_of_range":
            lo, hi = sorted((trig["value"], trig["value2"]))
            return not lo <= value <= hi
        return _CMP_FUNCS[trig["op"]](value, trig["value"])

    def _check_triggers(self, now):
        fired = []
        for fn in self.functions:
            any_active = False
            # Thresholds already raised per object by _update_dwell. Their
            # `active` is still computed below -- a Verdict Card bound to this
            # rule must still light while somebody is over the line -- but the
            # EVENT is not raised twice for one moment.
            per_object = [id(t) for t in self._object_triggers(fn)]
            for idx, trig in enumerate(fn.triggers):
                key = f"fn_{fn.id}_{trig['metric']}"
                value = self._stats.get(key)
                if value is None:
                    continue
                # What an Interval hold compares at each re-check: the
                # metric as published, so a word changing to another word
                # is a change (both read 1.0 below).
                raw = value
                if isinstance(value, str):
                    # A word under a numeric trigger: true when it says
                    # something, for is_true / is_false / rising / falling;
                    # a threshold on a word has no answer and stays quiet.
                    if trig.get("mode") not in ("is_true", "is_false", "rising",
                                                "falling", "changed"):
                        continue
                    value = 1.0 if value != "" else 0.0
                value = float(value)
                # last_fire None is "never fired", not "fired at epoch zero".
                # It was 0.0, which read as a fire infinitely long ago -- true
                # of any real clock, and false the moment a test or a replay
                # drives this from t=0, where the first alert would be swallowed
                # by its own cooldown.
                st = self._trig.setdefault((fn.id, idx),
                                           {"active": False, "last_fire": None,
                                            "prev": None, "since": None})
                cond = self._trigger_condition(trig, value, st)
                st["prev"] = value
                # hold_s: the condition must stay true continuously before it
                # fires -- "over the limit for 30 seconds", not one blip.
                if trig["hold_s"] > 0:
                    if cond:
                        if st["since"] is None:
                            st["since"] = now
                        cond = now - st["since"] >= trig["hold_s"]
                    else:
                        st["since"] = None
                any_active = any_active or cond
                # Rising edge only, then a cooldown: a zone that stays crowded
                # for an hour is ONE alert (plus repeats each cooldown), not
                # 30k events. Edge modes are single-frame by construction, so
                # the same logic simply fires them each time (per cooldown).
                #
                # The cooldown gates the EDGE too. It used to gate only the
                # repeats -- a rising edge fired whatever the clock said -- so
                # a condition that flickered defeated it completely: `current`
                # bouncing 1-2-1-2 with detector jitter is a fresh rising edge
                # every other frame, and a rule set to one alert every 30s sent
                # 46 in a minute (192.168.1.234, cycle_time_6la1). A cooldown
                # that only holds while the world holds still is not one.
                #
                # Zero is unchanged and still means "every edge": the test
                # passes trivially, so an edge fires as it always did.
                if id(trig) in per_object:
                    st["active"] = cond
                    continue
                # WHO the rule wired to this trigger's OID port is about, this
                # frame. getattr, because an embedder (and the trigger tests)
                # may build an engine with only the state the triggers
                # themselves need -- "which object" is extra.
                who = None
                if trig.get("oid_source"):
                    who = getattr(self, "_live_oids", {}).get(trig["oid_source"])
                    who = self._past_limit(fn, who, now)
                # The Advanced output reads what this frame changed: whether
                # the moment was already held, and which events it raised.
                was, n0 = st["active"], len(fired)
                if trig.get("rate"):
                    self._rated(fn, trig, st, cond, value, who, now, fired)
                    self._update_out(fn, idx, trig, cond, was, fired[n0:],
                                     now, raw)
                    st["active"] = cond
                    continue
                if trig.get("repeat") == "new_object":
                    # Per NEW OBJECT, not per cooldown: a crowd that stays is
                    # one alarm, and a person who JOINS it is another --
                    # raised the frame they are counted, not when a timer
                    # says so. The objects announced so far are remembered
                    # until they have been gone NEW_OBJECT_GRACE_S, so one
                    # leaving for good and coming back is new again, but a
                    # frame the tracker missed them is not. Likewise the
                    # slate is wiped once the condition has let go for the
                    # grace: the next time it holds is a fresh violation
                    # with fresh people in it, while a one-frame dip of the
                    # count is the same violation still going on.
                    #
                    # The rising edge fires whoever is there (possibly nobody
                    # the tracker has confirmed yet -- then the first id it
                    # does confirm reads as new, which is the honest answer:
                    # the alarm did not know who it was about).
                    present = set(who or ())
                    seen = st.get("seen_at")
                    if not isinstance(seen, dict):
                        seen = st["seen_at"] = {}
                    crossers = self._crossers(trig["oid_source"])
                    if cond:
                        st["off_since"] = None
                        if not crossers:
                            # Forget whoever has been gone the grace. A line
                            # rule's crossers are momentary: every id
                            # announced while the alarm holds is kept, or
                            # each would read as new again after it crossed.
                            # Whoever is here this frame is not gone.
                            for tid in [t for t, at in seen.items()
                                        if t not in present
                                        and now - at >= NEW_OBJECT_GRACE_S]:
                                del seen[tid]
                        fresh = present - seen.keys()
                        named = fresh or present
                        # The object the table already holds is not new,
                        # whatever its value (the Current OID guard).
                        if trig.get("cur_oid_key") and named:
                            named = {t for t in named
                                     if not self._stored_repeat(trig, t, value, True)}
                            fresh = fresh & named
                        # "held": the violation is still the one announced.
                        # Not st["active"], which a single false frame clears.
                        if (not st.get("held") or fresh) and (named or not (fresh or present)):
                            event = {"id": fn.id, "type": "alert",
                                     "severity": trig["severity"],
                                     "message": trig["message"],
                                     "value": value}
                            if named:
                                event["source_oid"] = min(named, key=str)
                                event["detail"] = {"new": sorted(fresh, key=str),
                                                   "present": sorted(present, key=str)}
                            st["last_fire"] = now
                            fired.append(event)
                        st["held"] = True
                        # Everyone here is (still) announced as of now. An
                        # empty frame refreshes nobody and forgets nobody
                        # until the grace runs out: a tracker that drops
                        # every id for a frame has not emptied the zone.
                        for tid in present:
                            seen[tid] = now
                    else:
                        if st.get("off_since") is None:
                            st["off_since"] = now
                        if now - st["off_since"] >= NEW_OBJECT_GRACE_S:
                            seen.clear()
                            st["held"] = False
                        elif not crossers:
                            # Inside the grace the violation is only paused:
                            # whoever was announced and is still here stays
                            # announced. Somebody who arrived meanwhile is
                            # not added -- they are new when it holds again.
                            for tid in present & seen.keys():
                                seen[tid] = now
                    self._update_out(fn, idx, trig, cond, was, fired[n0:],
                                     now, raw)
                    st["active"] = cond
                    continue
                if cond and (st["last_fire"] is None
                             or now - st["last_fire"] >= trig["cooldown_s"]):
                    if not st["active"] or trig["cooldown_s"] > 0:
                        st["last_fire"] = now
                        event = {"id": fn.id, "type": "alert",
                                 "severity": trig["severity"],
                                 "message": trig["message"],
                                 "value": value}
                        # The object this rule is currently about, where it has
                        # one. Absent for a rule about a place or a formula --
                        # which is why an output's "tag with the object id" is
                        # a soft no-op rather than an error: the graph either
                        # knows who, or honestly does not.
                        # An OID wire on the Trigger answers first: it names
                        # the rule the operator meant, where _last_oid guesses
                        # from the rule the moment happens to sit on.
                        oid = getattr(self, "_last_oid", {}).get(fn.id)
                        if who:
                            oid = min(who, key=str)
                        if oid is not None:
                            event["source_oid"] = oid
                        # The same object the table already holds, at a
                        # value within the threshold: this cooldown's push
                        # is skipped (and the cooldown runs on, so it is
                        # asked again next time round, not every frame).
                        if not self._stored_repeat(trig, oid, value, False):
                            fired.append(event)
                self._update_out(fn, idx, trig, cond, was, fired[n0:], now,
                                 raw)
                st["active"] = cond
            if fn.triggers:
                active_key = f"fn_{fn.id}_active"
                if active_key in _snap_hold:
                    # Its snapshot has been taken: false for as long as the rule
                    # still holds, and released the moment it does not.
                    if any_active:
                        any_active = False
                    else:
                        _snap_hold.discard(active_key)
                self._stats[active_key] = 1 if any_active else 0
        for event in fired:
            _beat(event.get("id"))      # each fire, for MQTT Publish (fire_beat)
        # What the Advanced Triggers published, for the formulas next frame.
        self._out_last = {f"fn_{fn.id}_out": self._stats[f"fn_{fn.id}_out"]
                          for fn in self.functions
                          if f"fn_{fn.id}_out" in self._stats
                          and any(t.get("out") is not None for t in fn.triggers)}
        return fired

    def _stored_repeat(self, trig, oid, value, watchdog):
        """Would pushing `oid` now only repeat what the Data Table holds?

        The Trigger's DUPLICATE GUARD (Tok Aba, 2026-10-13). Its Current OID
        port reads the newest object id stored in the operator's table
        (cur_oid_key, polled from the master like a Math input's column);
        Current value reads the value stored beside it (cur_value_key).

        watchdog  (only for a new object) the same object is never pushed
                  again, whatever its value.
        timer     (every cooldown) the same object is skipped while
                  |value - stored value| <= dup_diff; further apart, it is a
                  new reading and pushes. No Current value wired: nothing
                  says it changed, so it is skipped.

        False -- push exactly as before -- with no Current OID wired, no id
        to compare, or nothing stored yet (the first read is up to one poll
        after the app starts).
        """
        key = trig.get("cur_oid_key")
        if not key or oid is None or not _oid_same(self._stats.get(key), oid):
            return False
        if watchdog:
            return True
        vkey = trig.get("cur_value_key")
        if not vkey:
            return True
        stored = self._stats.get(vkey)
        try:
            return abs(float(value) - float(stored)) <= trig.get("dup_diff", 0.0)
        except (TypeError, ValueError):
            return False

    def _rated(self, fn, trig, st, cond, value, who, now, fired):
        """A Trigger whose Advanced "how often" is set: raise its event, or
        not, and append it to `fired`.

        cooldown      a NEW moment (the rising edge) fires when rate_s has
                      passed since the last fire. A moment that keeps holding
                      is one fire, not one per rate_s -- the setting is a
                      ceiling ("at most once every N seconds"), not a repeat.
        once_per_oid  each object the rule names (oid_source) fires it once,
                      the first frame the moment holds with it there, and the
                      same object never again. Who has fired is remembered
                      per engine in _trigger_memory, so a workflow save does
                      not make the whole room new; see TRIGGER_OID_KEEP_S
                      for how long and how many.
        Both clocks survive a save the same way. Changing THIS setting starts
        them afresh (the signature), as a redeploy of the node would.
        """
        mem = _trigger_memory.setdefault((getattr(self, "_scope", ""), fn.id), {})
        sig = (trig["rate"], trig["rate_s"], trig.get("oid_source"))
        if mem.get("sig") != sig:
            mem.clear()
            mem.update(sig=sig, last_fire=None, seen={}, pruned=now)
        event = {"id": fn.id, "type": "alert", "severity": trig["severity"],
                 "message": trig["message"], "value": value}
        if trig["rate"] == "cooldown":
            if cond and not st["active"] and (
                    mem["last_fire"] is None
                    or now - mem["last_fire"] >= trig["rate_s"]):
                oid = getattr(self, "_last_oid", {}).get(fn.id)
                if who:
                    oid = min(who, key=str)
                if oid is not None:
                    event["source_oid"] = oid
                mem["last_fire"] = st["last_fire"] = now
                if not self._stored_repeat(trig, oid, value, False):
                    fired.append(event)
            return
        seen = mem["seen"]
        present = set(who or ())
        if trig.get("cur_oid_key"):
            # Once per object: the object the table already holds counts
            # as fired already (the Current OID guard, watchdog rules).
            for tid in present - seen.keys():
                if self._stored_repeat(trig, tid, value, True):
                    seen[tid] = now
        for tid in present & seen.keys():
            seen[tid] = now                 # still in view: still remembered
        fresh = present - seen.keys()
        if cond and fresh:
            event["source_oid"] = min(fresh, key=str)
            event["detail"] = {"new": sorted(fresh, key=str),
                               "present": sorted(present, key=str)}
            for tid in fresh:
                seen[tid] = now
            mem["last_fire"] = st["last_fire"] = now
            fired.append(event)
        if now - mem.get("pruned", now) >= 1.0 or len(seen) > TRIGGER_OID_MAX:
            mem["pruned"] = now
            for tid in [t for t, at in seen.items()
                        if now - at > TRIGGER_OID_KEEP_S]:
                del seen[tid]
            if len(seen) > TRIGGER_OID_MAX:
                for tid in sorted(seen, key=seen.get)[:len(seen) - TRIGGER_OID_MAX]:
                    del seen[tid]

    def _update_out(self, fn, idx, trig, cond, was, new_events, now,
                    value=None):
        """Publish fn_<id>_out for a Trigger with an Advanced output.

        ON at a fire, carrying the payload (taken at that moment, so a
        timestamp is the time it fired); the events raised carry it too.
        When it goes OFF -- back to the idle payload, or 0 -- is the hold
        (Tok Aba, 2026-10-05 and 2026-10-07):

        Real-time (hold_s 0)  the moment it lets go. No timer: the output
                              follows the trigger live.
        Hold N                N s after the fire, whatever the trigger does
                              meanwhile. A new trigger while it is on
                              starts the N s again.
        Interval N            at a re-check, N s after the fire and every
                              N s after that. A re-check keeps it on for
                              another N s when the trigger is still active
                              AND its value is the one it fired with (the
                              rule's metric as published: a count, a
                              branch's 0/1, a word); anything else turns
                              it off, and it stays off until the next fire.
                              So on for at least N s, and for as long as
                              the trigger stays as it was. Only the
                              re-check looks: a blip between two of them
                              is not seen. A new trigger while it is on
                              (another fire -- for once per object, a new
                              object -- or the moment rising again inside
                              a cooldown) starts the interval again from
                              that frame, at its value.

        A hold saved before the choice compiled to a bare hold_s, which is
        Hold, as it always was. Never called for a rule sitting out
        with the AI off, so a held-back Trigger publishes nothing at all:
        not the payload, and not the idle payload either -- "not triggered"
        is a statement about the scene, and nobody is watching it.
        """
        out = trig.get("out")
        if out is None:
            return
        o = getattr(self, "_outs", None)
        if o is None:
            o = self._outs = {}
        o = o.setdefault((fn.id, idx), {"on": False, "until": None,
                                        "value": None, "idle": None,
                                        "ref": None})
        hold = out["hold_s"]
        if new_events:
            o["value"] = (inject_value(out["type"], out["value"], now)
                          if out["type"] else 1)
            o["on"], o["until"] = True, (now + hold if hold else None)
            o["ref"] = value
            if out["type"]:
                for ev in new_events:
                    ev["value"] = o["value"]
        elif o["on"] and hold and cond and not was:
            o["until"], o["ref"] = now + hold, value
        if o["on"] and hold and now >= o["until"]:
            if out.get("interval") and cond and value == o["ref"]:
                # Still as it fired: another interval, on the same beat --
                # unless frames were so sparse a whole one went by unseen.
                # An MQTT Publish after it sends the payload again (fire_beat).
                _beat(fn.id)
                o["until"] += hold
                if now >= o["until"]:
                    o["until"] = now + hold
            else:
                o["on"], o["idle"] = False, None
        elif o["on"] and not hold and not cond:
            o["on"], o["idle"] = False, None
        if not o["on"] and o["idle"] is None:
            o["idle"] = (inject_value(out["idle_type"], out["idle_value"], now)
                         if out["idle_type"] else 0)
        self._stats[f"fn_{fn.id}_out"] = o["value"] if o["on"] else o["idle"]

    def stats(self):
        """Flat fn_<id>_* scalars for this frame."""
        return dict(self._stats)
