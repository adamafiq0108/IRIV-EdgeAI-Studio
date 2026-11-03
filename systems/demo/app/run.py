# -*- coding: utf-8 -*-
"""
run.py -- the detection loop and the MJPEG stream.

    python -m app.run                 # from systems/<slug>/
    python -m app.run --once          # one cycle, for CI

capture -> detect -> decide -> store, then a heartbeat. The per-frame
decision is _gated_events() (what starts, continues and ends) plus the
rules the workflow compiles; everything else is plumbing that every system
needs identically, which is why this file is worth copying rather than
rewriting.

The loop owns the camera. The stream server hands out the frame the loop last
encoded (camera.last_jpeg) and never opens the device -- see camera.py.
"""

import argparse
import base64
import datetime
import json
import os
import sys
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

from app import buses as buses_mod
from app import camwatch as camwatch_mod
from app import chipshare as chipshare_mod
from app import mqtt_bus
from app import camera as camera_mod
from app import config as config_mod
from app import detector as detector_mod
from app import faults
from app import functions as functions_mod
from app import iriv as iriv_mod
from app import measure as measure_mod
from app import modbus_server as modbus_server_mod
from app import outputs as outputs_mod
from app import overlay_feed as overlay_mod
from app import passthrough as passthrough_mod
from app import preprocess as preprocess_mod
from app import runtime
from app import snapgate as snapgate_mod
from app import snapcam as snapcam_mod
from app import snapmeta as snapmeta_mod
from app import tracker as tracker_mod
from app import view_gate as view_gate_mod
from app import zones as zones_mod
from app.functions import Function, FunctionEngine, parse_functions
from app.store import Store, snapshot_cap
from app.tracker import Tracker

log = faults.logger(__name__)

_stop = threading.Event()

#: How often the loop checks whether its config changed on disk. The dashboard
#: switches the detection model by writing config.local.toml (the AI Models
#: page); the loop notices within this window and rebuilds the detector, so an
#: operator never SSHes in to restart the app to try a different model.
_CONFIG_POLL_S = 2.0

#: The zone editor's hint file, written into the data directory by the hub
#: while somebody is dragging a zone: {"hide_zones_until": <epoch>}.
#:
#: The app BURNS zone outlines into the frame, so the editor's unsaved shape
#: and the app's saved one are both on screen at once while the operator
#: works -- two outlines, and nothing to say which is which. This lets the
#: editor take its own shape off the picture for as long as the edit lasts.
#:
#: A deadline rather than a flag, because the writer is a browser tab: a
#: closed laptop must not leave a fleet's feeds without their zones. The page
#: refreshes it while it holds unsaved work and simply stops on save or
#: discard, and the outlines come back on their own.
OVERLAY_HINT_FILE = "overlay-hint.json"
OVERLAY_HINT_POLL_S = 0.5
#: The longest a hint is honoured no matter what it says -- a hand-written or
#: stale file cannot hide the zones for a shift.
OVERLAY_HINT_MAX_S = 30.0

#: AI DETECTION IS A SWITCH INSIDE THE APP, not a second app.
#:
#: A workflow is more than detection: an Inject pressed on the canvas, the
#: Math and Compare after it, a Modbus Write, an MQTT Publish. So the app runs
#: whenever its system is online, and [ai] on (config.local.toml, written by
#: the master's Start / Stop detection) decides only whether it opens the
#: camera(s) and the detector. ai_config() rewrites every config it loads
#: while the switch is off so that:
#:   * the detector is "none" -- the NullDetector: the chip is never opened,
#:     which is what the Hailo sharing rules require of a process that is not
#:     detecting (all-or-nothing multi_process: nobody half-joins)
#:   * every camera is switched off, so the loop runs its existing
#:     every-camera-off path: parked pipes, nothing opened, and the function
#:     engine still ticks every pass (at AI_OFF_FPS, not a camera's rate)
#:   * the functions and outputs that cannot stand alone (a Detection Stream
#:     reaches them -- the compiler's [logic] list) are listed in
#:     `_ai_inactive` / dropped from `outputs`: a zone count reading zero would
#:     otherwise fire "zone empty" alarms on a system nobody is watching
#: The functions list itself is NOT touched: the reload path rebuilds the rule
#: engines only when it changes, so flipping the switch leaves every free
#: node's state where it was (an Inject's rhythm, a total, a Modbus link).
#: Turning the switch back on is an ordinary reload: the detector is built,
#: the parked cameras reopen, and the detection-fed rules join the same engine.
#:
#: No [ai] table means ON: an app started by hand, or by a master from before
#: the switch, detects exactly as it always did.
AI_OFF_FPS = 4
#: A camera whose last real frame is older than this is not "producing".
AI_FRAME_FRESH_S = 5.0


def ai_on(cfg):
    """Is AI detection switched on in `cfg` ([ai] on; absent = on)?"""
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    return ai.get("on") is not False


def detection_fed(cfg):
    """The rule ids that sit out with AI detection OFF (ai_config's
    `_ai_inactive`), whatever the switch says right now: what the camera
    watchdog holds on a lost camera while detection restarts itself."""
    return list(ai_config(dict(cfg, ai={"on": False})).get("_ai_inactive")
                or [])


def ai_forced_off(cfg):
    """`cfg` with the AI switch off and everything else as written: what a
    watchdog restart's first half and a stop for a missing camera apply,
    while the user's own [ai] on stays untouched on disk."""
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    return dict(cfg, ai=dict(ai, on=False))


def cam_sources(cfg):
    """{camera id: source} of the cameras the user's config asks for: what
    the camera watchdog looks for while it decides whether a camera is
    missing (app/camwatch.py present())."""
    return {c["id"]: str(c.get("source") or "") for c in _camera_list(cfg)}


def cam_names(cfg):
    """{camera id: what the operator calls it} -- the name, else the label,
    else the source. Every word the app says about a camera (the AI dot's
    reason, the log) names it this way: an id like "main" means nothing to
    the person reading the page, and the page itself never shows it."""
    out = {}
    for c in _camera_list(cfg):
        word = str(c.get("name") or c.get("label") or "").strip()
        out[c["id"]] = word or faults.redact(str(c.get("source") or "")) or c["id"]
    return out


def standalone(cfg):
    """(function ids, outputs) of `cfg` that run with AI detection off."""
    fns = [f for f in (cfg.get("functions") or []) if isinstance(f, dict)]
    outs = [o for o in (cfg.get("outputs") or []) if isinstance(o, dict)]
    logic = cfg.get("logic") if isinstance(cfg.get("logic"), dict) else None
    if logic is not None:
        # The compiler's word (core workflow.logic_artifacts), decided by the
        # GRAPH: whatever a Detection Stream reaches waits, everything else
        # runs -- a Python node, a Data Table, an Email included. A type that
        # reads detections by nature is never kept, whatever the list says.
        keep = set(logic.get("functions") or [])
        fns = [f for f in fns if str(f.get("id") or "") in keep
               and str(f.get("type") or "") not in FRAME_TYPES]
        keep_out = set(logic.get("outputs") or [])
        outs = [o for o in outs if str(o.get("id") or "") in keep_out]
    else:
        # A config compiled before the list existed (the master recompiles it
        # at start -- this is the moment before that lands): the picture-free
        # TYPES, pruned below like everything else.
        fns = [f for f in fns if str(f.get("type") or "")
               in functions_mod.LOGIC_TYPES]
        outs = [o for o in outs if o.get("trigger") != "violation"]
    fns, outs = logic_prune(fns, outs)
    return {str(f.get("id") or "") for f in fns}, outs


def ai_config(cfg):
    """`cfg` as the app runs it with AI detection off; `cfg` itself when on."""
    if ai_on(cfg):
        return cfg
    out = dict(cfg)
    out["detector"] = {"kind": "none"}
    raw = cfg.get("cameras")
    if isinstance(raw, list):
        cams = [c for c in raw if isinstance(c, dict)]
    else:
        legacy = cfg.get("camera")
        cams = [dict(legacy, id=MAIN_CAMERA)] if isinstance(legacy, dict) \
            and legacy else []
    # The cameras detection would run come FIRST: the loop parks the first
    # raw camera as the pipe the camera-less rules live on, and that must be
    # the pipe detection makes primary -- or switching the AI on would drop
    # the pipe holding the free nodes' state and build them afresh.
    cams = ([c for c in cams if c.get("enabled", True)]
            + [c for c in cams if not c.get("enabled", True)])
    out["cameras"] = ([dict(c, enabled=False) for c in cams]
                      or [{"id": MAIN_CAMERA, "source": "", "enabled": False}])
    out["camera"] = {"source": "", "fps": AI_OFF_FPS}
    keep, out["outputs"] = standalone(cfg)
    out["_ai_inactive"] = sorted(
        str(f.get("id") or "") for f in (cfg.get("functions") or [])
        if isinstance(f, dict) and str(f.get("id") or "") not in keep)
    return out


def class_pause(cfg):
    """`cfg` as the app runs it with some named Class Filters PAUSED.

    The dashboard's "Class filter" card writes [class_filters] paused (node
    ids) into config.local.toml; the workflow compile wrote, per named
    filter, the functions and outputs after it ([[class_filters.nodes]],
    core/services/class_filter.py). A paused filter's functions join the
    AI-off machinery's inactive set (`_paused`, unioned with `_ai_inactive`
    by the caller): same engine, same state, nothing rebuilt, and nothing
    published live -- a count behind a switched-off filter reading 0 would
    be an "area empty" alarm nobody asked for. Its outputs are dropped, as
    with the AI off.

    Read on the ordinary config poll (_CONFIG_POLL_S), so a tick on the
    dashboard lands in the running app within seconds, no restart. No table, or
    nothing paused: `cfg` exactly as it was.
    """
    table = cfg.get("class_filters") if isinstance(cfg.get("class_filters"),
                                                   dict) else {}
    paused = {str(p) for p in (table.get("paused") or ())}
    if not paused:
        return cfg
    fns, outs = set(), set()
    for n in table.get("nodes") or ():
        if isinstance(n, dict) and str(n.get("node") or "") in paused:
            fns.update(str(f) for f in (n.get("functions") or ()))
            outs.update(str(o) for o in (n.get("outputs") or ()))
    if not fns and not outs:
        return cfg
    out = dict(cfg)
    out["_paused"] = sorted(fns)
    out["outputs"] = [o for o in (cfg.get("outputs") or [])
                      if not (isinstance(o, dict)
                              and str(o.get("id") or "") in outs)]
    return out


def paused_filters(cfg):
    """The Class Filter node ids the dashboard paused ([class_filters]
    paused): what a Class Filter's ON/OFF points read (functions.set_states).
    From the table as written, whatever the pause holds back."""
    table = cfg.get("class_filters") if isinstance(cfg.get("class_filters"),
                                                   dict) else {}
    return [str(p) for p in (table.get("paused") or ()) if isinstance(p, str)]


def point_gates(cfg):
    """`cfg` as the app runs it with the ON/OFF points' gates applied.

    Whatever a Detection Stream's or a Class Filter's ON/OFF point reaches
    runs only while that state matches the point (core/services/
    state_points.py): after the stream's OFF only while the AI is off, after
    a filter's ON only while it is not paused. The compile wrote, per point,
    the functions and outputs it gates ([[state_points.gates]]); those whose
    state does not match now join the inactive set (`_gated`, unioned by
    inactive_ids) and their outputs are dropped -- the class_pause
    treatment, so the AI switch or a tick on the dashboard lands on the next
    config poll, no restart. The state is read from `cfg` as applied (the AI
    switch after ai_config, which a watchdog restart may have forced off).
    No table, or nothing gated: `cfg` exactly as it was."""
    table = cfg.get("state_points") if isinstance(cfg.get("state_points"),
                                                  dict) else {}
    gates = [g for g in (table.get("gates") or ()) if isinstance(g, dict)]
    if not gates:
        return cfg
    ai, paused = ai_on(cfg), set(paused_filters(cfg))
    fns, outs = set(), set()
    for g in gates:
        if g.get("fixed_off"):
            on = False
        elif g.get("watch") == "class_filter":
            on = str(g.get("filter") or "") not in paused
        else:
            on = ai
        if on == (g.get("want") != "off"):
            continue                    # the state matches: it runs
        fns.update(str(f) for f in (g.get("functions") or ()))
        outs.update(str(o) for o in (g.get("outputs") or ()))
    if not fns and not outs:
        return cfg
    out = dict(cfg)
    out["_gated"] = sorted(fns)
    # ...and which outputs, so the ones the next poll lets back in can start
    # with no "last sent" (outputs.forget): a branch coming back sends again.
    out["_gated_outputs"] = sorted(outs)
    out["outputs"] = [o for o in (cfg.get("outputs") or [])
                      if not (isinstance(o, dict)
                              and str(o.get("id") or "") in outs)]
    return out


def inactive_ids(cfg):
    """Every function that sits out under `cfg`: the AI-off ones, the ones
    behind a paused Class Filter, and the ones an ON/OFF point gates."""
    return sorted(set(cfg.get("_ai_inactive") or ())
                  | set(cfg.get("_paused") or ())
                  | set(cfg.get("_gated") or ()))


#: A stat-key token in an expression or a code string (mirrors core
#: workflow._KEY_TOKEN_RE), and the keys only a camera fills.
_KEY_TOKEN_RE = re.compile(
    r"\b(?:fn|zone|line|objects|violations|cam)_[A-Za-z0-9_]+|\bcompliance\b")
_FRAME_KEY_PREFIXES = ("zone_", "line_", "objects_", "violations_", "cam_")
_STR_LITERAL_RE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")


def logic_prune(functions, outputs):
    """What may run of `functions` and `outputs` with AI off, once more, HERE.

    Whatever the list says, nothing runs that reads a key no kept function
    publishes: with no camera, an unpublished key reads as 0 (eval_expr), and
    "(people < 1)" on a 0 nobody measured is an "area empty" email from every
    box with detection off. So: a function reading a camera key or a key of
    a function that is not kept is dropped -- to a fixpoint -- and so is an
    output fired by one, reading one, or answering detections themselves.
    Mirrors core workflow.key_waits; the defence for a stale or hand-made
    config the compiler never judged.
    """
    kept = list(functions)

    def owned(key, ids):
        return any(key.startswith(f"fn_{fid}_") for fid in ids)

    def ok(key, ids):
        if key == "compliance" or key.startswith(_FRAME_KEY_PREFIXES):
            return False
        return not key.startswith("fn_") or owned(key, ids)

    while True:
        ids = {str(f.get("id") or "") for f in kept}
        nxt = []
        for f in kept:
            p = f.get("params") or {}
            keys = set()
            if isinstance(p.get("expr"), str):
                # A quoted word in an expression is text, not a key.
                keys.update(_KEY_TOKEN_RE.findall(
                    _STR_LITERAL_RE.sub('""', p["expr"])))
            if isinstance(p.get("code"), str):
                keys.update(_KEY_TOKEN_RE.findall(p["code"]))
            if isinstance(p.get("key"), str) and p["key"].strip():
                keys.add(p["key"].strip())
            code = str(p.get("code") or "")
            if all(ok(k, ids) for k in keys) and not (
                    f.get("type") == "custom"
                    and re.search(r"\b(detections|tracks)\b", code)):
                nxt.append(f)
        if len(nxt) == len(kept):
            break
        kept = nxt
    ids = {str(f.get("id") or "") for f in kept}
    outs = []
    for o in outputs:
        keys = set()
        if isinstance(o.get("key"), str) and o["key"].strip():
            keys.add(o["key"].strip())
        if isinstance(o.get("vars"), dict):
            keys.update(str(v).strip() for v in o["vars"].values()
                        if isinstance(v, str) and v.strip())
        for col in o.get("columns") or ():
            if isinstance(col, dict) and isinstance(col.get("key"), str) \
                    and col["key"].strip():
                keys.add(col["key"].strip())
        if o.get("trigger") == "violation" or o.get("mode") == "crossing":
            continue
        if o.get("function") and o["function"] not in ids:
            continue
        if all(ok(k, ids) for k in keys):
            outs.append(o)
    return kept, outs


#: Function types that read detections by what they are (mirrors core
#: workflow.FRAME_FN_TYPES): never run with AI detection off.
FRAME_TYPES = tuple(t for t in functions_mod.TYPES
                    if t not in functions_mod.LOGIC_TYPES and t != "custom")


#: Retry ladder for rebuilding a detector that fell back. Chip contention
#: between systems sharing one Hailo clears in SECONDS, so the first retry
#: comes fast; only repeated failures back off toward the ceiling. A flat
#: minute per attempt meant a minute of "running · none" for a fault that
#: had already passed -- and, worse, four apps falling together all retried
#: on the same clock, each rebuild kicking a neighbour off the chip, which
#: oscillated forever (watched live: alternating comm-closed drops every
#: 60-90s across four apps with no external cause left). The JITTER is what
#: breaks that resonance: retries spread out instead of colliding.
DETECTOR_RETRY_S = 60.0                 # ceiling after repeated failures
DETECTOR_RETRY_MIN_S = 8.0              # first retry after a fallback


def _retry_after(delay):
    """A jittered deadline: `delay` +/-25%, so simultaneous fallers do not
    rebuild in lockstep and re-collide on the shared chip."""
    import random                                           # noqa: PLC0415
    return time.time() + delay * random.uniform(0.75, 1.25)


def _held_ports():
    """The board terminals this app holds, or [] -- never an exception.

    Runs inside the per-cycle state write, so a box with no board (or a
    gpiozero that is not installed) must produce an empty list rather than
    stop the loop that is publishing detection state.
    """
    try:
        return iriv_mod.held_ports()
    except Exception:                                       # noqa: BLE001
        return []


def _config_mtime():
    """Newest mtime across the app's two config files, or 0.0 if neither is
    there yet. A change to either means the merged detector config may differ."""
    newest = 0.0
    for name in ("config.toml", "config.local.toml"):
        path = os.path.join(config_mod.PKG_DIR, name)
        try:
            newest = max(newest, os.path.getmtime(path))
        except OSError:
            pass
    return newest


def _now_iso():
    """ISO 8601 WITH an offset. The contract rejects naive timestamps, and a
    Pi that booted without NTP is exactly when this matters."""
    return datetime.datetime.now().astimezone().isoformat()


class _AlarmGate:
    """Edge-triggers a per-frame condition: fires on ENTRY, then a bounded
    heartbeat while it persists, then once when it CLEARS.

    Pure and camera-agnostic so it is unit-testable with a fake clock. The
    clear is held for `clear_hold_s` of continuous absence, so one missed
    detection frame does not split a single incident into an
    entry/clear/entry storm.
    """

    __slots__ = ("active", "since", "since_iso", "last_emit", "last_duration",
                 "zone", "_clear_since")

    def __init__(self):
        self.active = False
        self.since = 0.0
        self.since_iso = ""
        self.last_emit = 0.0
        self.last_duration = 0.0
        self.zone = None
        self._clear_since = None

    def update(self, signal, now, heartbeat_s, clear_hold_s):
        """One frame's condition -> "enter" | "heartbeat" | "clear" | None."""
        if signal:
            self._clear_since = None
            if not self.active:
                self.active = True
                self.since = now
                self.since_iso = _now_iso()
                self.last_emit = now
                return "enter"
            if heartbeat_s > 0 and now - self.last_emit >= heartbeat_s:
                self.last_emit = now
                return "heartbeat"
            return None
        if not self.active:
            return None
        if self._clear_since is None:
            self._clear_since = now
        if now - self._clear_since >= max(0.0, clear_hold_s):
            self.active = False
            self._clear_since = None
            self.last_duration = now - self.since
            return "clear"
        return None


def _alerting_zone_ids(pipe):
    """Zone ids whose function rules raise their own alert.

    Cached on the pipe and rebuilt whenever the engine is (a hot reload builds
    a new FunctionEngine object, so identity is the cheapest honest key). Read
    every frame otherwise, which is why it is not recomputed from the function
    list each time.
    """
    engine = getattr(pipe, "fn_engine", None)
    if engine is None:
        return frozenset()
    cached = getattr(pipe, "_alerting_zones", None)
    if cached is not None and cached[0] is engine:
        return cached[1]
    ids = set()
    for fn in getattr(engine, "functions", ()) or ():
        if not (getattr(fn, "trigger", None) or getattr(fn, "triggers", None)):
            continue
        zone = (fn.params or {}).get("zone")
        if zone:
            ids.add(str(zone))
    ids = frozenset(ids)
    pipe._alerting_zones = (engine, ids)
    return ids


#: Ceiling on crossing rows written from ONE frame. A busy line writes one row
#: per object per line, which is the point; a tracker having a bad second (ids
#: churning across a line) must still not write a thousand rows for one frame.
MAX_CROSSING_EVENTS = 32


def _gated_events(pipe, detections, result, now, heartbeat_s, clear_hold_s):
    """This frame's events for one camera, debounced to TRANSITIONS.

    Returns [(kind, relevant_detections, extra_detail)]:

      * violation  -- once on entry, then a heartbeat every `heartbeat_s`
        (detail.ongoing = true, detail.since = entry time) so downstream
        liveness never reads a stationary object as silence, then one
        info/"keep-out zone clear" with the incident's duration when it ends.
      * crossings  -- unchanged: the zone engine's in/out are per-frame deltas,
        so they are already edge-triggered.
      * "<label> detected" -- once when detections appear (outside any
        violation), silently re-armed when they go; the old per-frame repeat
        wrote a row for every frame an object was visible.

    A quarter-million identical rows for one part left in a zone overnight
    becomes: 1 entry + ~1440 heartbeats/day + 1 clear.
    """
    out = []
    violations = result.get("violations") or []
    # A zone whose own RULE raises the alert does not also get the built-in
    # keep-out event: the same incident written twice, once as "object in
    # keep-out zone" and once as the rule's own message, is one incident an
    # operator has to reconcile in the log. The rule wins because it is the
    # one an operator can see, name, colour and hang a snapshot off -- this
    # event has none of that. Zones with no such rule are untouched.
    owned = _alerting_zone_ids(pipe)
    if owned:
        violations = [v for v in violations if str(v.get("zone") or "") not in owned]

    gate = pipe.gates.setdefault("violation", _AlarmGate())
    action = gate.update(bool(violations), now, heartbeat_s, clear_hold_s)
    if action == "enter":
        gate.zone = violations[0].get("zone")
        out.append((("violation", "crit", "object in keep-out zone"),
                    violations, {"zone": gate.zone}))
    elif action == "heartbeat":
        if violations:
            gate.zone = violations[0].get("zone")
        out.append((("violation", "crit", "object in keep-out zone"),
                    violations,
                    {"zone": gate.zone, "ongoing": True,
                     "since": gate.since_iso}))
    elif action == "clear":
        out.append((("info", "ok", "keep-out zone clear"), [],
                    {"zone": gate.zone, "cleared": "violation",
                     "since": gate.since_iso,
                     "duration_s": round(gate.last_duration, 1)}))

    # ONE ROW PER CROSSING. This block used to collapse the whole frame into a
    # single event: the first line that had any crossing, and the first
    # direction that was non-zero -- so two people through the same door in one
    # frame wrote one row, an IN and an OUT in the same frame wrote "in", and a
    # second counting line was not recorded at all. Every tally read back OUT
    # of the event store (a Line Counter card over a date range, a per-zone
    # breakdown, "how many went out") therefore counted crossings in one lump
    # and undercounted them. The zone engine already publishes one
    # crossing_event per object per line, with its own direction, label and
    # track id; those are what gets written now.
    crossings = (result.get("crossing_events") or [])[:MAX_CROSSING_EVENTS]
    lines = result.get("crossings") or {}
    crossed = bool(crossings)
    by_track = {d.get("track_id"): d for d in (detections or [])
                if isinstance(d, dict) and d.get("track_id") is not None}
    for cev in crossings:
        zid = cev.get("zone")
        name = (lines.get(zid) or {}).get("name") or zid
        way = cev.get("direction") or "cross"
        # The object that crossed, when the tracker gave it an id -- the boxes
        # on the row are then the one that crossed rather than everything in
        # frame at the time.
        det = by_track.get(cev.get("track_id"))
        out.append((("info", "ok",
                     f"{name}: {'crossed' if way == 'cross' else way}"),
                    [det] if det else detections,
                    {"zone": zid, "direction": way,
                     "detection_label": cev.get("label") or None,
                     "track_id": cev.get("track_id"),
                     # A crossing is about ONE object. Naming it the way a
                     # Time in Zone stay does (detail.track) is what lets a
                     # Data Table row's id column record the crosser instead
                     # of the most confident bystander, and what lets a
                     # line-scoped OID column resolve at all -- a line stamps
                     # no membership on detections, only areas do. None is
                     # dropped by the copy loop, so an untracked crossing
                     # carries no key at all.
                     "track": cev.get("track_id"),
                     "model": cev.get("model")}))
    if not crossings:
        # A zone engine older than crossing_events (or a caller that supplies
        # only the per-frame deltas) still gets a row per line and per
        # direction, rather than one row for the frame.
        for zid, c in lines.items():
            for way in ("in", "out", "cross"):
                if c.get(way):
                    crossed = True
                    out.append((("info", "ok",
                                 f"{c['name']}: "
                                 f"{'crossed' if way == 'cross' else way}"),
                                detections, {"zone": zid, "direction": way}))

    gate = pipe.gates.setdefault("detected", _AlarmGate())
    # Presence is not an alarm: entry only, no heartbeat (0), silent re-arm.
    action = gate.update(bool(detections) and not violations, now,
                         0.0, clear_hold_s)
    if action == "enter" and not crossed:
        top = max(detections, key=lambda d: d["confidence"])
        out.append((("info", "ok", f"{top['label']} detected"),
                    detections, {}))
    return out


def _make_event(kind, detections, seq):
    etype, severity, label = kind
    snapshot_id = f"{int(time.time() * 1000)}-{seq}"
    return {
        "id": f"demo-{snapshot_id}",
        "ts": _now_iso(),
        "type": etype,
        "severity": severity,
        "label": label,
        "device": None,                 # the agent stamps identity, not the app
        "snapshot_id": snapshot_id,
        "detail": {"detections": detections},
    }


#: The id a lone [camera] table gets. Mirrors detector_control.MAIN_CAMERA --
#: the two are duplicated on purpose, the app imports nothing from core/.
MAIN_CAMERA = "main"

#: `params.camera` / a zone's `camera` set to this runs the rule on EVERY
#: camera. Each camera evaluates its own copy (own tracker, own timers), and
#: the published values are folded together by _MERGE below.
ALL_CAMERAS = "*"

#: The slowest a camera is ever paced at. Mirrors detector_control's
#: MIN_CAMERA_FPS (duplicated on purpose -- the app imports nothing from core).
#: Below roughly this, an object moves too far between frames for the tracker
#: to keep its identity, and every count built on identity starts lying.
MIN_CAMERA_FPS = 10.0


def _fps_share(cameras):
    """The per-camera FPS cap when `cameras` share one accelerator.

    A TABLE, not a division. An even split of a fixed budget kept producing
    awkward rates and, low enough, rates the tracker cannot hold an identity
    at. These are the rates the product promises:

        1-4 cameras -- no cap: each runs as fast as it can deliver
        beyond      -- the 10 fps floor, for a hand-written config that
                       ignores the cap; the dashboard never allows it

    Four IS the box's camera cap (core detector_control.MAX_CAMERAS), so the
    uncapped case is the only one the dashboard can produce; the floor exists
    for configs written by hand.

    `cameras` is the count across EVERY system on this box, because they all
    share the one chip AND the same CPU cores (grab thread + decode + encode
    per camera -- the cores run out before the accelerator does, which is why
    the cap is four). Returns None when there is no cap.
    """
    if cameras <= 4:
        return None
    return MIN_CAMERA_FPS

#: How one rule's value from SEVERAL cameras becomes one number.
#:
#: Summing everything would be wrong: "nearest pair in pixels" summed across
#: cameras is meaningless, and so is a summed average dwell. Counts add up;
#: "how bad is it right now" takes the worst camera; a distance takes the
#: closest. Anything unlisted sums, which is right for the counters that make
#: up most metrics.
_MERGE_MAX = ("active", "overlap_pct", "avg_s", "max_s", "live_s", "last_s",
              # An Advanced Trigger's payload (functions._update_out): the
              # moment held on either camera, never the two payloads added.
              "out",
              "last_speed", "avg_speed", "occupied_s", "total_s",
              # A size measured on two cameras is one object seen twice,
              # not two objects end to end.
              "farthest_mm", "length_mm", "width_mm", "height_mm",
              "diameter_mm", "radius_mm", "area_mm2", "perimeter_mm")
# remaining_s: the most-spent camera's budget is the zone's truth.
_MERGE_MIN = ("nearest_px", "remaining_s", "nearest_mm", "distance_mm")


def _merge_metric(key, old, new):
    """Fold one camera's value for a shared rule into the running total."""
    metric = key.rsplit("_", 1)[-1]
    for suffix in _MERGE_MAX:
        if key.endswith("_" + suffix):
            return max(old, new)
    for suffix in _MERGE_MIN:
        if key.endswith("_" + suffix):
            # -1 means "nothing seen"; a real distance always wins over it.
            if old < 0:
                return new
            if new < 0:
                return old
            return min(old, new)
    del metric
    return old + new


def _camera_list(cfg):
    """Every camera to capture, from [[cameras]] or the legacy [camera] table.

    Reading both shapes means a config written by an older dashboard, or by
    hand, still runs -- and a single-camera system behaves exactly as it did.
    """
    entries = cfg.get("cameras")
    if not isinstance(entries, list):
        legacy = cfg.get("camera")
        entries = [dict(legacy, id=MAIN_CAMERA)] if isinstance(legacy, dict) and legacy else []
    # An EXPLICIT empty list means every camera was removed (a blank-canvas
    # system) -- do not resurrect the legacy [camera] row the operator just
    # deleted. Mirrors core detector_control._camera_list; pinned by
    # tests/smoke_nocamera.py.
    elif not entries:
        entries = []
    out, seen = [], set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict) or not e.get("enabled", True):
            continue
        cid = str(e.get("id") or f"cam{i + 1}").strip() or f"cam{i + 1}"
        if cid in seen:
            continue
        seen.add(cid)
        out.append(dict(e, id=cid))
    return out


#: Sub-tables of [detector] that belonged to models running BESIDE the main
#: one -- depth, hands, the generative reader and OCR. This build runs object
#: detection only, so nothing writes them any more. The tuple STAYS so a
#: config.local.toml left over from an older build does not rebuild the
#: DETECTOR on every reload just because a dead [detector.ocr] table is still
#: sitting in it -- tearing the Hailo VDevice down mid-inference is exactly
#: what "VDevice released while CIM in use" is.
_SIDE_MODELS = ("depth", "mediapipe", "genai", "ocr")


#: How a trigger's comparison is read, for deciding whether a rule is CURRENTLY
#: alerting. Mirrors the engine's own ops -- the colour follows the condition,
#: not the event, so a box stays coloured for as long as the rule is true
#: rather than blinking once per cooldown.
_OPS = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


def _color_rules(cfg, mine=None):
    """Alerts that name a colour:
    [{zone, zone_colour, classes, colour, key, op, value}].

    `mine(owner)` says whether a function's camera is THIS camera (the
    same test rebuild_rules gives the rule engine); None keeps every one.
    Each camera's pipe used to take the whole list, so a colouring rule
    whose function ran on camera B found its key in the merged numbers and
    painted camera A's boxes too. A rule on every camera ("*") stays on
    each list and is judged per camera: the camera's own stats win over
    the merged set in _analyse, so it paints a camera only while THAT
    camera's instance of it holds.

    TWO colours, either or both: `colour` paints the objects that tripped
    the rule, `zone_colour` paints the zone it watches. They used to be one
    colour plus a "colour what" picker, which could say "the zone is red" or
    "the object is red" but never both at once in different colours.

    An Alert carries the colour and belongs to a function. WHICH objects it
    colours depends on what the function watches:

      * a zone       -- the objects violating that zone. They are stamped with
                        det["zone"] (app/zones.py), which is the one thing the
                        rule and the object share.
      * no zone      -- a class rule, or a count over the whole frame. There
                        is no zone to match on, so its objects are the ones it
                        counts: every detection of the classes it accepts,
                        while the rule is true. A workflow Trigger is always
                        this case (it decorates a branch or a Formula, which
                        narrows nothing itself), so the compile hands it the
                        classes of the rule behind it (`box_classes`,
                        core/services/trigger_paint.py): a Trigger on "person
                        in the bay" paints people, never the chair beside
                        them. Without any, every object in view, as before.
      * box_zone     -- a Trigger with "Only boxes inside the zone" ticked
                        (core/services/trigger_paint.py): of those, only the
                        objects the zone engine counts as inside that zone
                        (det["zones"], the anchor/footprint and class test
                        the zone rules count with). The rest keep their own
                        colour.

    The second case is the reason this is not keyed by zone alone. A violation
    does not require a zone -- "more than 3 people in frame" is a rule about
    nowhere in particular -- and its boxes have to change colour too.

    A workflow Trigger names its camera itself (`paint_camera`, stamped by
    core/services/trigger_paint.py stamp_camera) and that wins over the
    function's: a Trigger hangs off a branch or a Formula, those take no
    camera, and "no camera" means the first one -- so camera 2's violation
    painted camera 1's boxes (product owner, 2026-10-12). The rule's number
    is still read off the merged set, which is where a branch on another
    camera's engine publishes it.
    """
    rules = []
    for fn in cfg.get("functions") or []:
        params = fn.get("params") or {}
        trigs = list(fn.get("triggers") or [])
        if isinstance(fn.get("trigger"), dict):
            trigs.append(fn["trigger"])
        for trig in trigs:
            owner = str((trig or {}).get("paint_camera") or "").strip() \
                or params.get("camera")
            if mine is not None and not mine(owner):
                continue
            colour = str((trig or {}).get("box_color") or "").strip()
            # THE ZONE'S OWN COLOUR. Separate from the boxes' because they
            # answer separate questions -- which thing is wrong, and where --
            # and a rule may well want both at once in different colours.
            zone_colour = str((trig or {}).get("zone_color") or "").strip()
            # DEPRECATED shape: one colour aimed by `color_target`. A config
            # written before the split still means what it always did --
            # target "zone"/"both" made that single colour the zone's.
            legacy = str(trig.get("color_target") or "box").lower()
            if colour and not zone_colour and legacy in ("zone", "both"):
                zone_colour = colour
                if legacy == "zone":
                    colour = ""
            if not colour and not zone_colour:
                continue
            metric = str(trig.get("metric") or "").strip()
            rules.append({
                "zone": str(params.get("zone") or ""),
                "zone_colour": zone_colour,
                # WHICH zone the colour lands on. The rule's own zone is the
                # obvious answer and the usual one, but a Trigger may name a
                # different area on its zone port: a rule about a doorway
                # lighting the whole floor, or a frame-wide count -- which
                # watches no zone at all -- painting one anyway.
                "paint_zone": (str(trig.get("zone_paint") or "")
                               or str(params.get("zone") or "")),
                # Empty = every class this rule does not narrow. A Trigger's
                # own params narrow nothing; its classes are the rule's.
                "classes": {str(c).lower() for c in
                            (params.get("classes") or trig.get("box_classes")
                             or [])},
                "colour": colour,
                "box_zone": str(trig.get("box_zone") or "") if colour else "",
                "key": f"fn_{fn.get('id')}_{metric}" if metric else "",
                "op": str(trig.get("op") or ">"),
                "value": trig.get("value", 1),
            })
            break                       # one colouring trigger per function
    return rules


def _rule_active(rule, detections, counts):
    """Is this rule tripped right now?

    A zone rule needs no threshold check: being inside a violation zone IS the
    violation, and zones.py has already marked who. Everything else is decided
    by its own metric, so the colour follows the CONDITION rather than the
    event -- it holds for as long as the rule is true instead of blinking once
    per cooldown.
    """
    if rule["zone"]:
        return any(d.get("violation") and d.get("zone") == rule["zone"]
                   for d in detections or [])
    key, op = rule["key"], rule["op"]
    if not key or op not in _OPS:
        return False
    try:
        return _OPS[op](float((counts or {}).get(key)), float(rule["value"]))
    except (TypeError, ValueError):
        return False


def _stamp_colors(rules, detections, counts, overlays=None):
    """Recolour what each alerting rule is about, in place.

    A rule may paint its objects, its zone, or both -- in different colours,
    since they are separate settings. Zone outlines are RESTORED when the
    rule stops holding, so a zone that went red for one crossing does not
    stay red for the rest of the shift. The original colour is remembered the
    first time it is overridden -- reading it back off the zone later would
    read the override.
    """
    for ov in overlays or []:
        if "_base_color" not in ov:
            ov["_base_color"] = ov.get("color")
    # ZONES ARE RESOLVED ACROSS ALL RULES, then painted once. Painting per
    # rule -- colour when active, base when not -- let an INACTIVE rule stomp
    # an active one: an If/Else pair painting the same zone (red on violate,
    # green on the else branch) ran in order, the red landed, and the green
    # rule's "not my turn, back to base" erased it in the same frame. The
    # zone read green-or-base for ever and the red never survived a tick.
    # None = "a rule paints this zone but none holds": base colour.
    zone_colours = {}
    for rule in rules:
        active = _rule_active(rule, detections, counts)
        rule["_active"] = active
        if rule["zone_colour"] and rule["paint_zone"]:
            if active:
                zone_colours[rule["paint_zone"]] = rule["zone_colour"]
            else:
                zone_colours.setdefault(rule["paint_zone"], None)
    for ov in overlays or []:
        zid = str(ov.get("id") or "")
        if zid in zone_colours:
            ov["color"] = zone_colours[zid] or ov["_base_color"]
    for rule in rules:
        if not rule["_active"] or not rule["colour"]:
            continue
        for det in detections or []:
            if rule["zone"]:
                # The objects that are actually in it, not everything in view.
                if not (det.get("violation") and det.get("zone") == rule["zone"]):
                    continue
            elif rule["classes"] and str(det.get("label", "")).lower()                     not in rule["classes"]:
                continue
            if rule.get("box_zone") \
                    and rule["box_zone"] not in (det.get("zones") or ()):
                continue                # outside the zone: its own colour
            det["box_color"] = rule["colour"]


def _tracker_settings(cfg):
    """The [tracker] table as Tracker kwargs, each clamped to a sane range.

    These are the knobs that decide when two boxes on consecutive frames are
    the SAME object. They matter because every count built on identity --
    unique visitors, line crossings, dwell, travel, cycle time -- is only as
    good as the id, and the right values depend on the scene: a slow queue and
    a fast conveyor disagree about how far an object may move between frames.

    Clamped rather than trusted: a hand-edited config.local.toml never went
    through the API validator, and a zero here would either match everything
    to everything or nothing to anything.
    """
    raw = cfg.get("tracker") or {}

    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "kalman_dist_thr": num("kalman_dist_thr",
                               tracker_mod.KALMAN_DIST_THR, 0.05, 1.0),
        "iou_thr": num("iou_thr", tracker_mod.IOU_THR, 0.05, 0.98),
        "init_iou_thr": num("init_iou_thr",
                            tracker_mod.INIT_IOU_THR, 0.05, 0.98),
        "keep_new_frames": int(num("keep_new_frames",
                                   tracker_mod.KEEP_NEW_FRAMES, 1, 30)),
        "keep_tracked_frames": int(num("keep_tracked_frames",
                                       tracker_mod.KEEP_TRACKED_FRAMES, 1, 300)),
        "keep_lost_frames": int(num("keep_lost_frames",
                                    tracker_mod.KEEP_LOST_FRAMES, 0, 300)),
        # Capped low on purpose: this is tolerance for a detector that blinks,
        # not for an object that left. A large grace here would keep ghosts
        # alive long enough to confirm each other into real ids.
        "keep_new_grace": int(num("keep_new_grace",
                                  tracker_mod.KEEP_NEW_GRACE, 0, 5)),
    }


def _det_core(det_table):
    """The detector settings WITHOUT the dead side-model sub-tables."""
    return {k: v for k, v in (det_table or {}).items()
            if k not in _SIDE_MODELS}


def release_by_exec(det, restart_half=None, restart_phase=None, once=False):
    """Must the running detector be let go by re-executing the app, rather
    than det.close() in place?

    Yes for a hailort_service client (hailo.py via_service) when the config
    as written asked for the change -- the AI switch, a model, a setting.
    On .49 (Hailo-8L, HailoRT 4.23, 2026-10-09) releasing one took
    hailort_service down every time: gRPC 14, "Failed to shutdown",
    "Executing pipeline terminate failed", and the app died natively inside
    close() before it could log "AI detection switched off" -- 11 service
    restarts in 40 minutes, one per app death. An exec drops the handle
    without the release call; the service reaps the client itself.

    Not for the camera watchdog's own halves (`restart_half`, a "paused"
    wait): a fresh process would read the user's switch ON and lose the
    wait. Nor with --once, which is a test run."""
    if once or restart_half is not None or restart_phase == "paused":
        return False
    return bool(getattr(det, "via_service", False))


class _Pipe:
    """One camera and everything that must NOT be shared with another.

    The tracker assigns identities by nearest centroid, the zone engine keeps
    cumulative crossing totals and line-side memory per track, and the function
    engine keeps dwell/travel timers keyed by track id. Share any of them
    between two cameras and objects on one camera match objects on the other:
    invented crossings, dwell times that never end, bubbles drawn on the wrong
    frame. So each camera gets its own set, and only the DETECTOR (whose Hailo
    scheduler already time-slices) is shared.
    """

    __slots__ = ("id", "cfg", "cam", "tracker", "engine", "fn_engine", "capture",
                 "zone_list", "overlays", "overlay_dims", "frame_w", "frame_h",
                 "jpeg", "fps_ema", "cam_fails", "next_recover", "base_fps",
                 "last_frame", "last_dets", "opening", "gates", "data_dir",
                 # Switched off by the operator: the device is released and the
                 # recovery ladder must leave it alone. NOT a failure -- a
                 # camera giving no frames because nobody asked it to.
                 "parked",
                 # _alerting_zones caches the zones whose rules raise their own
                 # alert, so the built-in keep-out event stands down for them.
                 # Keyed by engine identity -- a hot reload builds a new one.
                 "box_colors", "_alerting_zones",
                 # The WebRTC publish for this camera, and when to next try
                 # building one. Both None/0 on an mjpeg system.
                 "publisher", "publish_retry",
                 # Which grab the publish thread last sent, so it sends
                 # each frame once and never spins on a stalled camera.
                 "publish_seq", "still_push_at",
                 # Snapshot gate, see app/snapgate.py: None means every frame.
                 # snap_jpeg: the last picture it snapped, held for the live view.
                 "snap", "snap_jpeg", "snap_event",
                 # The newest ANNOTATED frame, and when its JPEG was last
                 # encoded. The frame is kept every tick -- a snapshot needs
                 # the moment it fired on -- while the encode happens only for
                 # viewers, or on a slow heartbeat. See _draw.
                 "shot", "jpeg_at",
                 # [video] on_demand: the (frame, overlay) a snapshot would
                 # paint, kept INSTEAD of painting it every tick (see
                 # _keep_shot). None while the switch is off.
                 "shot_src",
                 # [video] on_demand: is anybody watching this camera's
                 # legacy view (view_gate.Gate, kept by _watch_viewers)? True
                 # until known otherwise, and the event wakes a paused
                 # publish the moment a viewer appears.
                 "view_wanted", "view_wake",
                 # What to DRAW: the last detections, zone outlines, links,
                 # bubbles, trails and HUD rows. Written by the loop, read by
                 # whichever viewer thread is painting a frame.
                 "overlay",
                 # The last frame a viewer painted, by grab sequence, so N
                 # viewers of one camera share one render: (seq, jpeg).
                 "view_seq", "view_jpeg", "view_lock",
                 # The last frame painted for WATCHING (HUD on), so the
                 # WebRTC publish and the MJPEG viewers paint each grab once
                 # between them. See _painted.
                 "paint_key", "paint_frame", "paint_lock",
                 # [video] overlay = "client": the browser draws the overlay,
                 # so frames sent for WATCHING go out plain. See
                 # app/overlay_feed.py and set_overlay_client.
                 "overlay_client",
                 # [video] feed = "passthrough": the gateway pulls this
                 # camera's main stream itself, so the publish thread sends
                 # nothing (app/passthrough.py). `view` is the camera's
                 # {"feed", "why"} for state.json, None when nothing asked.
                 "passthrough", "view",
                 # This camera's saved measurement profiles applied to its
                 # live detections (measure.Measurer). Every camera has one;
                 # on a camera without depth it does nothing, cheaply.
                 "measurer",
                 # The newest false-colour depth picture, by depth grab, so N
                 # viewers of the depth view share one render.
                 "depth_view_seq", "depth_view_jpeg")

    def __init__(self, entry, cfg, stream_port, data_dir, store, anchor):
        from app.capture import Capture                     # noqa: PLC0415
        self.id = entry["id"]
        self.cfg = dict(entry)
        # Every camera of this system shares the ONE stream port; the id is the
        # query parameter that picks it apart (see _serve_stream).
        self.cfg["_stream_port"] = stream_port
        # Where a "file:<name>" source resolves to. Stored relative so a
        # system exported as a template still finds its own footage on the
        # next box (core/services/media.py).
        self.cfg["media_dir"] = os.path.join(str(data_dir), "media")
        self.cam = camera_mod.Camera(self.cfg)
        #: Alerts that name a colour for the objects they are about. Built
        #: by rebuild_rules, with the rules they belong to.
        self.box_colors = []
        # Filled lazily by _alerting_zone_ids; __slots__ means it has to exist.
        self._alerting_zones = None
        self.tracker = Tracker(**_tracker_settings(cfg))
        self.frame_w = int(entry.get("width") or 640)
        self.frame_h = int(entry.get("height") or 480)
        self.zone_list = []
        self.engine = None
        self.fn_engine = None
        self.overlays = []
        self.overlay_dims = (self.frame_w, self.frame_h)
        self.jpeg = None
        self.shot = None
        self.shot_src = None
        self.view_wanted = True
        self.view_wake = threading.Event()
        self.jpeg_at = 0.0
        self.overlay = None
        self.view_seq = -1
        self.view_jpeg = None
        self.view_lock = threading.Lock()
        self.paint_key = None
        self.paint_frame = None
        self.paint_lock = threading.Lock()
        self.overlay_client = overlay_mod.client_mode(cfg)
        self.passthrough = False
        self.view = None
        self.depth_view_seq = -1
        self.depth_view_jpeg = None
        measure_cfg = entry.get("measure") if isinstance(entry.get("measure"), dict) else {}
        self.measurer = measure_mod.Measurer(
            measure_cfg.get("profiles"), measure_cfg.get("floor"),
            measure_mod.load_background(config_mod.PKG_DIR, measure_cfg.get("background")),
            measure_mod.load_colour_background(config_mod.PKG_DIR, measure_cfg.get("background")),
            background_thread=True)
        self.publisher = None
        self.publish_retry = 0.0
        self.publish_seq = -1
        self.still_push_at = 0.0     # a held still: when its frame last went out
        self.snap = None             # snapshot gate (app/snapgate.py); None = every frame
        self.snap_jpeg = None        # ...and the last picture it snapped
        self.snap_event = threading.Event()   # set when a NEW snap is ready to show
        self.fps_ema = 0.0
        self.cam_fails = 0
        self.next_recover = 0.0
        self.opening = False
        self.parked = False
        # Per-condition edge triggers (violation, presence): an event is
        # emitted when a condition STARTS or ENDS, not on every frame it
        # holds. Per camera, like every other piece of rule state.
        self.gates = {}
        # Floored, so a config written before the minimum existed (or edited by
        # hand) still paces at the rate the tracker needs.
        self.base_fps = max(MIN_CAMERA_FPS, float(entry.get("fps") or 10))
        self.last_frame = None
        #: This frame's tracked detections, for the clock-paced outputs -- see
        #: _analyse. Declared here so the slots class never has to grow one
        #: mid-loop, which raises rather than degrading.
        self.last_dets = []
        # Clips and snapshots carry the camera id, so two cameras recording in
        # the same second cannot write the same file (they did: the name was
        # clip_<timestamp>.mp4 to the second, with one writer for the system).
        self.capture = Capture(data_dir, store, fps=int(entry.get("fps") or 10),
                               tag=self.id)
        # The function engine keeps its per-day tallies here, so "objects
        # today" survives a restart or a hot reload.
        self.data_dir = data_dir
        self.rebuild_rules(cfg, anchor)

    def set_overlay_client(self, on):
        """Switch who draws the live overlay. Every cached picture was made
        for the OTHER mode -- a painted frame must not reach a client-mode
        viewer, nor a plain one a server-mode viewer -- so all three caches
        go with the switch."""
        on = bool(on)
        if on == self.overlay_client:
            return
        self.overlay_client = on
        with self.paint_lock:
            self.paint_key = self.paint_frame = None
        with self.view_lock:
            self.view_seq, self.view_jpeg = -1, None
        self.jpeg, self.jpeg_at = None, 0.0

    def rebuild_rules(self, cfg, anchor):
        """(Re)build this camera's zones and functions from config. Called on
        every hot reload, so a calibration saved while the app runs takes
        effect within a poll rather than at the next restart.

        The whole point of the id filter is here: a zone or
        function belonging to another camera must not reach this engine."""
        # With every camera switched off the one pipe that exists is the
        # PARKED first camera (see main), so that is where camera-less rules
        # belong -- "main" named no pipe, and they ran nowhere. That is also
        # the AI switched off, where every camera is off by construction.
        raw = [c for c in (cfg.get("cameras") or []) if isinstance(c, dict)] \
            if isinstance(cfg.get("cameras"), list) else []
        parked = str(raw[0].get("id") or MAIN_CAMERA) if raw else MAIN_CAMERA
        primary = (_camera_list(cfg) or [{"id": parked}])[0]["id"]

        def mine(owner):
            # "*" = every camera: a rule with no zone (proximity, a guard
            # bubble, a class count) is about OBJECTS, not about a place, so
            # running it on one camera of four is rarely what was meant.
            # Unset = the first camera, which is where everything written
            # before multi-camera ran.
            owner = str(owner or "").strip()
            return owner == ALL_CAMERAS or (owner or primary) == self.id

        # DISABLED zones ride along (the engine and the drawing skip them at
        # frame time): a workflow Zone Switch can arm one at runtime, and a
        # zone filtered out here would be one no switch could ever reach.
        zone_list = [z for z in zones_mod.parse_zones(cfg.get("zones"))
                     if mine(z.camera)]
        self.zone_list = zone_list
        self.engine = zones_mod.ZoneEngine(zone_list, anchor)
        self.overlays = [z.as_dict(self.frame_w, self.frame_h) for z in zone_list]
        self.overlay_dims = (self.frame_w, self.frame_h)
        fns = [f for f in parse_functions(cfg.get("functions"))
               if mine(f.camera)]
        # The colour rules are read off the same functions, so they follow
        # every hot reload too. They were built once, in __init__: a Trigger
        # colour saved on the canvas -- a new colour, "Only boxes inside the
        # zone" ticked, the box recolour switched off -- rebuilt the rules
        # within a poll and left the painting exactly as it was until the
        # next detection restart (Tok Aba, box .49, 2026-10-07: box_zone in
        # the saved config, every box still red). This camera's rules only,
        # by the same owner test the functions pass.
        self.box_colors = _color_rules(cfg, mine)
        # A hot reload builds a NEW engine; the tallies come back off disk, so
        # editing a workflow does not reset today's count. Flush the outgoing
        # engine first or the last few counts die with it.
        if self.fn_engine is not None:
            self.fn_engine.flush_daily(force=True)
        # scope: this camera's own tracker numbers its objects, so what a
        # Trigger remembers about them is kept per camera (functions.py).
        self.fn_engine = FunctionEngine(fns, anchor, state_dir=self.data_dir,
                                        scope=self.id)
        return fns

    def label(self):
        # Masked: a label saved by a page before 1.0.37 could be a pasted
        # address with its password ("IP camera admin:pw@10.0.0.5").
        return faults.mask_label(self.cfg.get("label") or self.cfg.get("name")
                                 or self.cam.label)

    def open_async(self):
        """Open the device OFF the detection loop.

        Camera.open() blocks: an RTSP handshake takes seconds on a camera that
        is slow to answer, and it then waits up to 2s more for a first frame.
        Run inline on the loop -- which is what adding a camera used to do --
        that freezes DETECTION AND EVERY OTHER CAMERA'S STREAM for the whole
        connect. The pipe joins the loop immediately and simply has no frames
        until this finishes.
        """
        cam = self.cam
        self.opening = True

        def _run():
            try:
                cam.open()
            except Exception as exc:                        # noqa: BLE001
                log.error("camera %s failed to open: %s", self.id, exc,
                          exc_info=True)
            finally:
                # The operator may have re-pointed this camera while the old
                # source was still connecting. Only the thread that owns the
                # CURRENT camera may declare it ready, or a stale connect
                # clears the flag for a device that is still opening.
                if self.cam is cam:
                    self.opening = False

        threading.Thread(target=_run, name=f"open-{self.id}", daemon=True).start()

    def drop_publisher(self):
        """End this camera's legacy publish, if any. Before the gateway is
        asked to pull the camera under the same path name: the two must
        never hold one path at once."""
        pub, self.publisher = self.publisher, None
        if pub is not None:
            try:
                pub.close()
            except Exception:                               # noqa: BLE001
                pass

    def close(self):
        # The publish first: it holds an RTSP session with the gateway, and
        # a session left open keeps the path "ready" -- a page would then be
        # offered WebRTC for a camera that stopped producing frames.
        self.drop_publisher()
        try:
            self.cam.close()
        except Exception:                                   # noqa: BLE001
            pass


def _model_label(det_cfg, det):
    """The model name(s) for the HUD, event stamps and state.json.

    A [[detector.models]] list is the truth when present (the factory runs
    every entry and ignores the single keys); otherwise the hailo sub-table
    first -- the factory merges it over the top level."""
    entries = [str(e.get("model")) for e in (det_cfg.get("models") or [])
               if isinstance(e, dict) and e.get("model")]
    if entries:
        return " + ".join(entries)
    return ((det_cfg.get("hailo") or {}).get("model")
            or det_cfg.get("model")
            or det.name)


def _try_open(cam_cfg, why=None):
    """A Camera for `cam_cfg` that actually opened, or None (never raises).

    `why`, a list, collects the reason a failed open gave: the camera object
    is thrown away here, and its error is the only current answer to "why is
    this camera still down" -- the old camera's own error is whatever it said
    before it stopped (usually just "camera read failed").
    """
    try:
        cam = camera_mod.Camera(cam_cfg)
        if cam.open():
            return cam
        if why is not None and cam.error:
            why.append(str(cam.error))
        cam.close()
    except Exception:                                       # noqa: BLE001
        pass
    return None


#: Where the MJPEG server listens. LOOPBACK by default, because that feed is
#: unauthenticated by design and every intended consumer is on this box: the
#: dashboard relays it through the master (core/routes/models.py, which does
#: enforce a session), and the fleet agent proxies it behind its bearer token.
#: Binding the wildcard meant anyone routable to the board could watch the
#: cameras -- annotated on /video_feed, and raw on /raw -- with no credential
#: of any kind.
#:
#: A deployment that genuinely shares a camera BETWEEN BOARDS (the documented
#: /raw use) opts in with [app] stream_bind = "0.0.0.0", which is then a
#: deliberate, visible decision rather than the silent default.
DEFAULT_STREAM_BIND = "127.0.0.1"

#: Concurrent viewers. Each one is a thread parked in a write loop, so an
#: unbounded count is a remote way to exhaust the box -- and past a handful
#: they are competing with inference for the same little CPU anyway.
#: The most frames a second any one camera's WebRTC publish sends.
#:
#: A camera set to 50 or 60 fps is being asked for motion smoothness that
#: no browser tile shows and no operator sees; past 30 each extra frame
#: costs a paint, a colour convert and an encode on a box that also has an
#: accelerator to feed. The camera keeps grabbing at its own rate for
#: detection -- this bounds only what is SENT.
WEBRTC_MAX_FPS = 30.0

MAX_STREAM_CLIENTS = 12

#: How long an overlay stream may say nothing before it writes a keepalive
#: comment. Keeps the master's relay (30 s read timeout) and the browser's
#: fetch alive through a still scene, and is how a browser that went away
#: without closing is noticed: the write fails.
OVERLAY_KEEPALIVE_S = 10.0

#: How often the annotated JPEG is refreshed when NOBODY is watching. Not zero,
#: because the first viewer to connect (and any probe that reads the cache)
#: should meet a picture rather than a blank while the next frame is drawn.
IDLE_JPEG_S = 1.0


def _paint(overlay, frame, hud=True):
    """Draw one camera's overlay onto a frame. The ONE renderer.

    The detection loop calls it for the live picture and for a clip; a viewer
    calls it for the picture it is about to send. One function, so the three
    can differ only in WHICH frame they paint -- never in what gets drawn.

    `hud` off leaves the status box out, which is what a recorded clip wants:
    the model name and the frame rate are chrome for somebody watching live.
    """
    if frame is None or not overlay:
        return frame
    annotated = camera_mod.annotate(
        frame, overlay.get("detections") or [], overlay.get("zone"),
        overlay.get("overlays") or [],
        links=overlay.get("links") or [], bubbles=overlay.get("bubbles") or [],
        trails=overlay.get("trails") or [])
    return (camera_mod.draw_hud(annotated, overlay.get("hud") or [])
            if hud else annotated)


def _painted(pipe, frame, seq):
    """_paint(pipe.overlay, frame), done ONCE per grab for every watcher.

    The WebRTC publish and each MJPEG viewer used to paint the same overlay
    onto the same grabbed frame independently -- with WebRTC and one MJPEG
    tab open that is two full renders per camera frame on a CPU that is
    already the bottleneck. Whoever asks first paints; the other gets the
    same array.

    The key is everything that changes the pixels: the grabbed frame itself
    (held, so a reopened camera restarting its sequence cannot match an old
    picture), its grab sequence, the overlay snapshot (held, compared by
    identity -- _draw swaps the dict whole, never edits it), and the zone
    colours, because _stamp_colors repaints zone dicts IN PLACE on the loop
    thread between two _draws. Detections, links, bubbles and trails are
    fresh lists per _draw and are finished before the overlay is published.

    The stored array is READ-ONLY (flag set, so a future in-place writer
    fails loudly instead of scribbling on another viewer's picture). HUD
    only: the clip's hud=False render and the preprocess_preview picture
    are different pixels and never come through here.
    """
    overlay = pipe.overlay
    if frame is None or not overlay:
        return frame
    # [video] overlay = "client": the browser draws the boxes over the video
    # from app/overlay_feed.py, so the plain grab goes out. Returned before
    # the cache is read or written, so a painted frame stored in server mode
    # can never be served in this one.
    if getattr(pipe, "overlay_client", False):
        return frame
    colours = tuple(ov.get("color") for ov in overlay.get("overlays") or []
                    if isinstance(ov, dict))
    with pipe.paint_lock:
        key = pipe.paint_key
        if (key is not None and key[0] is frame and key[1] == seq
                and key[2] is overlay and key[3] == colours):
            return pipe.paint_frame
    # Painted OUTSIDE the lock, so a viewer never waits on the publish: two
    # threads may both paint one grab in a race, which is today's cost, never
    # a wrong picture. draw_hud has already run (inside _paint) before the
    # array is shared, so its in-place stamp cannot reach a reader.
    painted = _paint(overlay, frame)
    if painted is not frame and hasattr(painted, "flags"):
        painted.flags.writeable = False
    with pipe.paint_lock:
        pipe.paint_key = (frame, seq, overlay, colours)
        pipe.paint_frame = painted
    return painted


def _keep_shot(pipe, frame, lazy=False):
    """Keep this tick's evidence picture for a snapshot that may fire on it.

    The default paints it NOW (pipe.shot), every inference tick, as it always
    has -- and returns the painted frame for _draw's other uses. With
    [video] on_demand (`lazy`) only the INGREDIENTS are kept: the grabbed
    frame and the overlay dict this tick drew from. _draw swaps that dict
    whole and never edits it, and a snapshot fires later in the SAME tick
    (outputs.fire / outputs.publish run after every _draw), so painting them
    on demand in _shot_frame gives exactly the pixels the eager paint gave --
    the boxes of the alert moment -- and a tick no snapshot reads costs no
    paint at all. Returns None then: there is nothing painted to reuse.
    """
    if lazy:
        pipe.shot, pipe.shot_src = None, (frame, pipe.overlay)
        return None
    stamped = _paint(pipe.overlay, frame)
    pipe.shot, pipe.shot_src = stamped, None
    return stamped


def _shot_frame(pipe):
    """The evidence picture _keep_shot kept: painted already, or painted now
    from the frame and overlay of the tick it was kept on."""
    if pipe.shot is not None:
        return pipe.shot
    src = getattr(pipe, "shot_src", None)
    if src is None or src[0] is None:
        return None
    return _paint(src[1], src[0])


#: How long a camera may deliver nothing before viewers are TOLD, and how
#: long before their stream ends instead.
#:
#: A frozen camera used to be invisible: every viewer path fell back to the
#: last good JPEG, so the feed showed a perfectly still picture, no `error`
#: event ever reached the browser, and the client-side stall watchdog had
#: already stood down after the first frame. The stream then lived for ever.
#: An operator clicking around opened fresh previews beside the dead ones
#: until the browser's ~6 connections per origin were all held by streams
#: that would never speak again -- at which point every OTHER request on the
#: page (stats, controls, config) simply never got sent, and the only cure
#: was leaving for the Hub, which force-closes the sockets. That is the "one
#: camera freezes and the whole system dies" report.
#:
#: So a stall becomes visible at 5s, and at 25s the stream ENDS -- the socket
#: closes, the slot comes back, and the viewer reconnects on its own.
STALL_NOTICE_S = 5.0
STALL_END_S = 25.0

_STALL_JPEG = None


def _stall_jpeg():
    """The "camera stalled" placeholder, rendered once. Built lazily and kept:
    this is served at the viewer's frame rate, and drawing it per frame per
    viewer would spend real CPU on saying nothing is happening."""
    global _STALL_JPEG                                      # noqa: PLW0603
    if _STALL_JPEG is None:
        _STALL_JPEG = camera_mod.notice_jpeg("Camera stalled - reconnecting...")
    return _STALL_JPEG


def _viewer_stall(pipe):
    """What a viewer should be shown INSTEAD of a stale frame, or False when
    the camera is fine. None means "end this stream"."""
    if pipe is None:
        return None
    stalled = pipe.cam.stalled_s
    if stalled >= STALL_END_S:
        return None
    if stalled >= STALL_NOTICE_S:
        return _stall_jpeg()
    return False


def _raw_jpeg(pipe):
    """The unannotated feed another system drinks. A shared camera reports its
    stall to its consumers too -- a feed that silently freezes is worse, not
    better, for belonging to somebody else."""
    # Another system is drinking this camera: a Snapshot camera must not sleep.
    snapcam_mod.RAW_SEEN[pipe.id] = time.time()
    stall = _viewer_stall(pipe)
    return pipe.cam.last_jpeg() if stall is False else stall


def _waiting_frame(like):
    """A Snapshot camera that has not snapped yet: a dark picture, the size of
    the camera's own frames, saying so. `like` is any frame from the camera
    (the size matters to the WebRTC publisher), or None for 640x360."""
    import cv2                                              # noqa: PLC0415
    import numpy as np                                      # noqa: PLC0415
    h, w = (like.shape[:2] if like is not None else (360, 640))
    img = np.full((h, w, 3), 24, np.uint8)
    text = "Waiting for snapshot"
    scale = max(0.6, w / 800.0)
    (tw, th), _b = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
    cv2.putText(img, text, (max(0, (w - tw) // 2), (h + th) // 2),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (210, 210, 210), 2, cv2.LINE_AA)
    return img


def _waiting_jpeg(pipe):
    import cv2                                              # noqa: PLC0415
    frame, _seq = pipe.cam.latest()
    ok, buf = cv2.imencode(".jpg", _waiting_frame(frame), [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


def _annotated_jpeg(pipe):
    """The annotated picture for a VIEWER: last known boxes, newest frame.

    Annotated frames used to exist only when the detection loop made one, so
    the stream ran at the inference rate and stopped dead during every
    inference -- measured on an Arducam box: 200ms holes in a feed whose
    camera was delivering steadily at 32fps. Detection is slow because a chip
    is shared; VIDEO has no reason to be.

    So the overlay is drawn here, on the viewer's own thread, onto whatever
    the camera has just grabbed. The boxes are up to one inference old on an
    image that is current -- which is what every camera system does, and what
    "live" means to somebody watching. The work is shared between viewers by
    grab sequence, so ten tiles of one camera cost one render.
    """
    stall = _viewer_stall(pipe)
    if stall is not False:
        return stall
    # SNAPSHOT MODE: the live view is the last picture that was snapped, held.
    # Nothing is grabbed, painted or encoded per viewer frame, which is the
    # CPU this mode exists to save; until the first snap the cached picture
    # below stands in. A camera in Video mode never gets here.
    if getattr(pipe, "snap", None) is not None and getattr(pipe, "snap_jpeg", None) is not None:
        return pipe.snap_jpeg
    if getattr(pipe, "snap", None) is not None:
        waiting = _waiting_jpeg(pipe)
        if waiting is not None:
            return waiting
    frame, seq = pipe.cam.latest()
    # THE PREVIEW SHOWS WHAT THE CAMERA SEES, unless this camera asks not to.
    # Software pre-processing is tuned by eye -- how much gamma, how much blur
    # -- and nobody can tune it against a picture that does not show it, so
    # `preprocess_preview` paints the processed frame here instead. Off by
    # default and per camera: with it on, the operator is no longer looking at
    # the camera, and a blurred feed that nobody switched on reads as a broken
    # lens. It costs a chain per PAINTED frame (shared across viewers by grab
    # sequence, like the annotation itself), which is why it is a tuning
    # switch and not the normal state of a running system.
    processed = bool(frame is not None and pipe.cfg.get("preprocess_preview"))
    if processed:
        frame = preprocess_mod.apply(frame, pipe.cfg.get("preprocess"))
    if frame is None or pipe.overlay is None:
        # Nothing grabbed yet, or nothing detected yet: the loop's own cache
        # is the honest answer (it also covers the synthetic camera).
        return pipe.jpeg or pipe.cam.last_jpeg()
    with pipe.view_lock:
        if seq == pipe.view_seq and pipe.view_jpeg is not None:
            return pipe.view_jpeg              # another viewer just drew it
    # The plain picture shares its paint with the WebRTC publish (_painted);
    # a processed one is different pixels and is painted on its own -- or
    # not at all when the browser draws the overlay ([video] overlay).
    if processed:
        shown = (frame if getattr(pipe, "overlay_client", False)
                 else _paint(pipe.overlay, frame))
    else:
        shown = _painted(pipe, frame, seq)
    jpeg = camera_mod.encode_jpeg(shown)
    if not jpeg:
        return pipe.jpeg
    with pipe.view_lock:
        # Last writer wins: a concurrent painter used the same frame or a
        # newer one, and either is a correct answer to "the latest picture".
        if seq >= pipe.view_seq:
            pipe.view_seq, pipe.view_jpeg = seq, jpeg
    return jpeg


#: The depth view's colour range, millimetres. Fixed rather than fitted to
#: each frame, so a thing does not change colour because something else
#: entered the picture; 0.3m to 4m is where the Gemini 336 measures well.
DEPTH_VIEW_NEAR_MM = 300.0
DEPTH_VIEW_FAR_MM = 4000.0


def _depth_view_jpeg(pipe):
    """The depth picture as a viewer sees it: near is warm, far is cool,
    nothing-measured is black, with the boxes and zones painted on top.

    A false-colour picture is how a person checks a depth camera at all --
    is the floor even, is that dark box invisible to the stereo, why does
    this lane read as "no depth". Rendered at the viewer's rate, shared
    between viewers by grab sequence like _annotated_jpeg.
    """
    grid, scale = pipe.cam.depth_frame()
    if grid is None:
        return None
    try:
        import cv2                                          # noqa: PLC0415
        import numpy as np                                  # noqa: PLC0415
    except ImportError:
        return None
    _frame, seq = pipe.cam.latest()
    with pipe.view_lock:
        if seq == pipe.depth_view_seq and pipe.depth_view_jpeg is not None:
            return pipe.depth_view_jpeg
    good = (grid != 0) & (grid != 0xFFFF)
    mm = grid.astype(np.float32) * float(scale or 1.0)
    span = DEPTH_VIEW_FAR_MM - DEPTH_VIEW_NEAR_MM
    norm = np.clip((mm - DEPTH_VIEW_NEAR_MM) / span, 0.0, 1.0)
    # Near = red end of the map, far = blue: inverted so a thing coming at
    # the camera turns warm, which is what every depth viewer does.
    img8 = (255 - norm * 255).astype(np.uint8)
    colour = cv2.applyColorMap(img8, cv2.COLORMAP_JET)
    colour[~good] = 0
    # The overlay was drawn for the DETECTOR's frame size; the grid is the
    # camera's own. Resize the picture to the overlay, not the other way.
    if pipe.frame_w and pipe.frame_h and colour.shape[1::-1] != (pipe.frame_w, pipe.frame_h):
        colour = cv2.resize(colour, (pipe.frame_w, pipe.frame_h),
                            interpolation=cv2.INTER_NEAREST)
    painted = _paint(pipe.overlay, colour) if pipe.overlay else colour
    cv2.putText(painted, f"depth  {DEPTH_VIEW_NEAR_MM / 1000:.1f}m (red) .. "
                f"{DEPTH_VIEW_FAR_MM / 1000:.0f}m (blue)  black = no reading",
                (8, painted.shape[0] - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                (255, 255, 255), 1)
    jpeg = camera_mod.encode_jpeg(painted)
    if jpeg:
        with pipe.view_lock:
            if seq >= pipe.depth_view_seq:
                pipe.depth_view_seq, pipe.depth_view_jpeg = seq, jpeg
    return jpeg


def _serve_stream(get_jpeg, get_fps, port, get_raw=None, probe=None,
                  bind=DEFAULT_STREAM_BIND, get_raw_fps=None, get_depth=None,
                  get_depth_view=None, get_dets=None, get_frame=None,
                  overlay_feed=None):
    """MJPEG on its own port. The agent proxies it; nothing else should.

    Two feeds:
      /video_feed (or /) -- the ANNOTATED frame (detection boxes + zones)
      /raw               -- the UNANNOTATED camera frame

    /raw lets ANOTHER system consume this camera without opening the device
    itself (a raw V4L2 camera has one reader): the second system sets its source
    to http://<ip>:<port>/raw and runs its own model on the shared feed -- which,
    with the shared Hailo VDevice, is how several models run on one camera.

    ONE port serves EVERY camera of this system: `?camera=<id>` picks which.
    A MISSING id answers with the first camera, so every existing consumer --
    the fleet agent, the dashboard relay, another system's shared feed -- keeps
    working against the bare path it already uses. An id naming no camera is a
    404 (`probe` decides), because silently substituting camera 1 made a
    just-added camera look like it was already streaming someone else's view.
    The getters therefore take the camera id: `get_jpeg(cam_id)`.

    Two optional query parameters let a VIEWER ask for less than the pipeline
    produces -- `fps` (1..30) and `quality` (20..90) -- so a wall of tiles is
    cheap while the page being worked on stays sharp. Neither can ask for more
    than exists, and the bare path is byte-for-byte what it always was.

    A third path, /depth, is not a stream: ONE depth picture, as 16-bit PNG,
    with the lens intrinsics and the millimetre scale in headers. It exists
    because a depth camera is held by its SDK exclusively -- the master
    cannot open it for a calibration snapshot or a manual measurement, so it
    asks the process that has it. `get_depth(cam_id)` returns
    (grid_uint16, scale, intrinsics) or None for a camera with no depth.

    /overlay is the live overlay as server-sent events, for a system whose
    browser draws it ([video] overlay = "client", app/overlay_feed.py).
    `camera` may repeat; none means the first camera. The master relays it
    behind a session (core/routes/overlay.py); it is loopback like the rest.
    """
    get_raw = get_raw or get_jpeg
    #: Live viewers, one entry per streaming connection. When the cap is hit
    #: the OLDEST viewer is evicted rather than the new one refused: rapidly
    #: switching cameras on the Zones page opens a stream per click and the
    #: browser's aborted ones linger a few seconds server-side -- refusing
    #: the operator's newest click over those zombies is how "more than 2
    #: switches" wedged the page at "Camera connecting" (reproduced: 12
    #: quick open+aborts answered 503 for seconds). A viewer evicted while
    #: genuinely watching reconnects on its own -- StreamImg retries -- so
    #: the wrong victim costs a blink, not a stream.
    _clients = []                       # [{ts, stop}] under _clients_lock
    _clients_lock = threading.Lock()

    def _requantize(jpeg, quality):
        """Re-encode one JPEG smaller, for a viewer that asked for less.

        Done on the HTTP thread, never on the detection loop -- a wall of
        preview tiles must not cost inference. Anything that fails (no cv2, a
        frame it cannot decode) simply serves the original: a bigger picture is
        always a safe answer.
        """
        cv2 = camera_mod.cv2          # optional dependency; camera.py owns it
        if cv2 is None:
            return jpeg
        try:
            import numpy as np                              # noqa: PLC0415

            frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                return jpeg
            return camera_mod.encode_jpeg(frame, quality) or jpeg
        except Exception:                                   # noqa: BLE001
            return jpeg

    class Handler(BaseHTTPRequestHandler):
        def _json(self, body, status=200):
            """One JSON answer. Used by every non-streaming path here."""
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError,
                    ConnectionAbortedError):
                pass

        def do_GET(self):               # noqa: N802 (http.server API)
            path, _, query = self.path.partition("?")
            path = path.rstrip("/") or "/"
            raw = path.endswith("/raw") or path == "/raw"
            if path not in ("/", "/video_feed", "/raw", "/depth", "/detections", "/frame",
                            "/overlay"):
                self.send_error(404)
                return
            cam_id = ""
            cam_ids = []
            view = ""
            want_fps = want_quality = 0.0
            for part in query.split("&"):
                key, _, value = part.partition("=")
                if key == "camera":
                    cam_id = unquote(value)
                    cam_ids.append(cam_id)
                elif key == "view":
                    # view=depth: the false-colour depth picture instead of
                    # the camera's, boxes and zones still painted on.
                    view = unquote(value).strip().lower()
                elif key in ("fps", "quality"):
                    try:
                        num = float(unquote(value))
                    except ValueError:
                        continue
                    if key == "fps":
                        want_fps = min(30.0, max(1.0, num))
                    else:
                        want_quality = min(90.0, max(20.0, num))
            # An id that names no camera is a mistake worth reporting. It used
            # to answer with the FIRST camera, so a page previewing a camera
            # that had not been created yet showed a different camera's picture
            # and looked like the zones were drawn on the wrong view. A bare
            # path still means "the first camera" -- the fleet agent, the
            # dashboard relay and shared-feed consumers all rely on that.
            if cam_id and probe is not None and probe(cam_id) is None:
                self.send_error(404, "no such camera")
                return
            if path == "/depth":
                self._depth(cam_id)
                return
            if path == "/detections":
                self._detections(cam_id)
                return
            if path == "/frame":
                self._frame(cam_id)
                return
            if path == "/overlay":
                self._overlay(cam_ids)
                return
            base = get_raw if raw else get_jpeg
            if view == "depth" and get_depth_view is not None and not raw:
                base = get_depth_view
            getter = lambda: base(cam_id)                   # noqa: E731
            # One thread per viewer, each parked in a write loop: without a
            # ceiling, opening connections is a way to starve the detection
            # loop of the little CPU it has. At the ceiling the OLDEST viewer
            # is told to stop (see _clients above); refusal is reserved for
            # a flood far past what eviction can absorb.
            me = {"ts": time.time(), "stop": threading.Event(), "raw": raw,
                  # WHICH camera this viewer is watching. "" is the bare path,
                  # which means "the first camera" -- and since this server
                  # cannot tell which id that is, it counts as watching all of
                  # them. Erring toward encoding is right: the cost of one
                  # extra JPEG is a millisecond, the cost of guessing wrong is
                  # a black stream.
                  "cam": str(cam_id or "")}
            with _clients_lock:
                if len(_clients) >= MAX_STREAM_CLIENTS * 2:
                    over = True
                else:
                    over = False
                    if len(_clients) >= MAX_STREAM_CLIENTS:
                        # Page viewers (/video_feed) first: /raw drinkers are
                        # OTHER SYSTEMS sharing this camera's one decode, and
                        # evicting one forces that whole system through its
                        # reconnect ladder over a browser click.
                        pool = ([c for c in _clients if not c["raw"]]
                                or _clients)
                        min(pool, key=lambda c: c["ts"])["stop"].set()
                    _clients.append(me)
            if over:
                self.send_error(503, "too many viewers")
                return
            try:
                # /raw paces at the camera's DELIVERY rate, not get_fps: the
                # chip balancer rewrites cam.fps down to this app's inference
                # allowance, and pacing the shared feed by that throttled
                # every CONSUMER system to the owner's chip share.
                rate_of = (get_raw_fps if raw and get_raw_fps is not None
                           else get_fps)
                self._stream(getter, cam_id, rate_of, want_fps, want_quality,
                             me["stop"])
            finally:
                with _clients_lock:
                    try:
                        _clients.remove(me)
                    except ValueError:
                        pass

        def _overlay(self, cam_ids):
            """The overlay messages for these cameras, as server-sent events,
            until the browser goes or the mode goes back to "server".

            The thread sleeps on the listener's condition between messages
            -- no polling -- and writes a comment line every
            OVERLAY_KEEPALIVE_S of silence, which is also how a vanished
            browser is noticed. 404 unless this system is in client mode, so
            a server-mode system never holds one of these open."""
            if overlay_feed is None or not overlay_feed.enabled:
                self.send_error(404, "the overlay is drawn on the server")
                return
            cams = set()
            for cid in cam_ids or [""]:
                if cid and probe is not None and probe(cid) is None:
                    self.send_error(404, "no such camera")
                    return
                # "" is the bare path: the first camera, as everywhere here.
                cams.add(overlay_feed.resolve(cid))
            cams.discard(None)
            if not cams:
                self.send_error(404, "no camera")
                return
            listener = overlay_feed.subscribe(cams)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while not _stop.is_set() and not listener.closed:
                    data = listener.get(OVERLAY_KEEPALIVE_S)
                    self.wfile.write(b": keepalive\n\n" if data is None
                                     else b"data: " + data + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass                    # the browser went: routine
            finally:
                overlay_feed.unsubscribe(listener)

        def _frame(self, cam_id):
            """One colour frame, lossless PNG, as the detector sees it. The
            calibration keeps it as the empty scene's colour, for the
            outline by colour subtraction."""
            if get_frame is None:
                self.send_error(404, "no frame here")
                return
            try:
                frame = get_frame(cam_id)
            except Exception:                               # noqa: BLE001
                frame = None
            if frame is None:
                self.send_error(404, "no frame yet")
                return
            try:
                import cv2
                ok, png = cv2.imencode(".png", frame)
            except Exception:                               # noqa: BLE001
                ok = False
            if not ok:
                self.send_error(500, "could not encode the frame")
                return
            data = png.tobytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _detections(self, cam_id):
            """This camera's last detections, as JSON with NORMALISED boxes.

            For the measurement setup: when the operator taps two points on
            a bottle and saves that as the bottle's profile, the master
            needs to know WHICH detection the taps were on, so the profile
            can be stored relative to that box and follow every bottle
            afterwards (measure.Measurer). Boxes leave here as fractions of
            the frame for the same reason taps arrive that way."""
            if get_dets is None:
                self.send_error(404, "no detections here")
                return
            try:
                answer = get_dets(cam_id)
            except Exception:                               # noqa: BLE001
                answer = None
            if answer is None:
                self.send_error(404, "no such camera")
                return
            cid, fw, fh, dets = answer
            rows = []
            for d in dets or []:
                box = d.get("box") if isinstance(d, dict) else None
                if not box or not fw or not fh:
                    continue
                rows.append({"label": d.get("label"), "confidence": d.get("confidence"),
                             "track_id": d.get("track_id"), "depth_mm": d.get("depth_mm"),
                             "box": [round(box[0] / fw, 4), round(box[1] / fh, 4),
                                     round(box[2] / fw, 4), round(box[3] / fh, 4)]})
            data = json.dumps({"camera": cid, "width": fw, "height": fh,
                               "detections": rows}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _depth(self, cam_id):
            """One depth picture, for the master's calibration and manual
            measurement. PNG because it is lossless and 16-bit, so every
            millimetre survives; headers because the numbers that make the
            picture mean something -- the scale and the lens -- belong with
            it, not in a second request that could describe a different
            frame."""
            if get_depth is None:
                self.send_error(404, "no depth here")
                return
            try:
                answer = get_depth(cam_id)
            except Exception:                               # noqa: BLE001
                answer = None
            if not answer or answer[0] is None:
                self.send_error(404, "this camera measures no depth")
                return
            grid, scale, intrinsics = answer
            try:
                import cv2
                ok, png = cv2.imencode(".png", grid)
            except Exception:                               # noqa: BLE001
                ok = False
            if not ok:
                self.send_error(500, "could not encode the depth picture")
                return
            data = png.tobytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Depth-Scale", repr(float(scale)))
            self.send_header("X-Depth-Intrinsics", json.dumps(intrinsics or {}))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def _stream(self, getter, cam_id, get_fps, want_fps, want_quality,
                    stop_evt=None):
            # Wait briefly for a first frame, then start the stream anyway with
            # a frame that SAYS the camera is still connecting: an RTSP camera
            # can take many seconds to answer, and a 503 turns that into a
            # browser error loop on a camera that is perfectly healthy.
            deadline = time.time() + 2.0
            while getter() is None and time.time() < deadline:
                time.sleep(0.1)
            waiting = camera_mod.notice_jpeg("Camera connecting...")
            if getter() is None and waiting is None:
                self.send_error(503, "no frames available")
                return
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            # How long a stream may serve only the "connecting" notice before
            # it ENDS instead. A camera deactivated or removed mid-stream has
            # no pipe any more (getter answers None for good); ending the
            # stream makes the viewer reconnect, and the reconnect gets the
            # honest 404 -- a placeholder forever would look like a camera
            # that never finishes connecting.
            none_since = None
            while not _stop.is_set():
                if stop_evt is not None and stop_evt.is_set():
                    return              # evicted: a newer viewer needed the slot
                frame = getter()
                if frame is None:
                    if none_since is None:
                        none_since = time.time()
                    elif time.time() - none_since > 10.0:
                        return          # camera gone: end, let 404 speak
                else:
                    none_since = None
                jpeg = frame or waiting
                if jpeg and want_quality:
                    jpeg = _requantize(jpeg, int(want_quality))
                if jpeg:
                    try:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " + str(len(jpeg)).encode()
                                         + b"\r\n\r\n" + jpeg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError,
                            ConnectionAbortedError):
                        # Viewer went away -- closed the tab, scrolled the tile
                        # off screen, backgrounded the page. Routine now that
                        # the frontend tears streams down deliberately, and
                        # every kind of half-closed socket must stay silent:
                        # Windows raises ConnectionAborted where Linux raises
                        # BrokenPipe, and an uncaught one prints a traceback
                        # per closed preview.
                        return
                # A viewer may ask for LESS than the pipeline produces (a wall
                # of ten tiles does not need every frame); it can never ask for
                # more than exists.
                rate = max(1.0, get_fps(cam_id))
                if want_fps:
                    rate = min(rate, want_fps)
                time.sleep(1.0 / rate)

        def log_message(self, *args):
            pass                        # the app log is for detection, not GETs

    server = ThreadingHTTPServer((str(bind or DEFAULT_STREAM_BIND), int(port)),
                                 Handler)
    if str(bind) not in ("127.0.0.1", "localhost", "::1"):
        log.warning("the video stream is bound to %s:%s and is NOT "
                    "authenticated - anyone who can reach this board can "
                    "watch its cameras. Set [app] stream_bind = "
                    "\"127.0.0.1\" unless another board genuinely consumes "
                    "this feed.", bind, port)
    def _watchers(cam_id):
        """How many viewers are streaming this camera right now.

        The detection loop asks before it encodes an annotated JPEG: with
        nobody watching, that encode is work nothing reads (see _draw). A bare
        path ("" -- the fleet agent, a shared-feed consumer) counts for every
        camera, because this server cannot tell which id it resolves to.
        """
        cid = str(cam_id or "")
        with _clients_lock:
            return sum(1 for c in _clients
                       if not c["cam"] or not cid or c["cam"] == cid)

    server.watchers = _watchers
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _build_gst(det_cfg, cam_cfg):
    """Build the GStreamer detector, which owns capture AND inference. Returns
    (detector, gst_or_None, error): on failure it returns a mock detector and
    the error, so the app still serves a stream and the dashboard shows why."""
    # Merge the [detector.hailo] sub-table up (model/models_dir/labels/classes
    # live there too), same as the plain hailo path -- a model chosen on the
    # dashboard must reach the pipeline whichever backend runs it.
    gd_cfg = {**det_cfg, **(det_cfg.get("hailo") or {}),
              "source": cam_cfg.get("source", "0"),
              "width": cam_cfg.get("width", 640),
              "height": cam_cfg.get("height", 480),
              "fps": cam_cfg.get("fps", 30)}
    try:
        from app.gst_detector import GstDetector           # noqa: PLC0415
        gd = GstDetector(gd_cfg)
        return gd, gd, None
    except Exception as exc:                                # noqa: BLE001
        msg = f"gstreamer detector failed: {exc}"
        # ONE record, not two. The failure and what was done about it are the
        # same event, and "falling back to a silent detector" on a line of
        # its own tells an operator nothing about what it fell back FROM.
        log.error("%s - falling back to a silent detector so the stream "
                  "stays up", msg, exc_info=True,
                  extra=faults.event(faults.detector_event(exc)))
        return detector_mod.fallback(det_cfg), None, msg


def _gst_detections(gst, frame_w, frame_h):
    """The pipeline's latest detections as frame-pixel boxes."""
    _, dets_norm = gst.latest()
    out = []
    for d in dets_norm:
        x1, y1, x2, y2 = d["box_norm"]
        out.append({"label": d["label"], "confidence": d["confidence"],
                    "box": [int(x1 * frame_w), int(y1 * frame_h),
                            int(x2 * frame_w), int(y2 * frame_h)]})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="demo detection app")
    ap.add_argument("--once", action="store_true", help="one cycle, then exit")
    args = ap.parse_args(argv)

    # CM5 + ATECC608 + Hailo (this app's own copy: it imports nothing from core).
    if not args.once:
        from app import hwcheck
        hwcheck.require()

    cfg = config_mod.load()
    data_dir = config_mod.data_dir(cfg)
    # The cameras the user asked for: what the camera watchdog looks for
    # when one stops giving frames (app/camwatch.py).
    cam_srcs = cam_sources(cfg)
    cam_words = cam_names(cfg)
    # Off the REAL [camera] table: ai_config below swaps it for a stub while
    # the AI is off, and the raw cameras still stream then.
    ffmpeg_cam = dict(cfg.get("camera") or {})
    cfg = point_gates(class_pause(ai_config(cfg)))   # data_dir from the real config, first
    # The AI detection switch as this run applies it (see ai_config), and
    # when each camera last gave a real frame -- together they are the
    # green/red "AI running" dot the pages show.
    ai_active = ai_on(cfg)
    ai_frames = {}
    # Which rules sit out while it is off, for the pages' "AI off" state.
    ai_inactive = list(cfg.get("_ai_inactive") or [])
    # FIRST, before a camera or a HEF can fail. Until this call the process
    # has no logging handler at all: every failure in it went out as a print
    # into app.log, which no route reads, so an operator watching the Logs
    # page saw nothing from the process that owns the cameras and the
    # accelerator. install() keeps app.log exactly as it was and adds the
    # spool the master merges into that page (app/faults.py).
    faults.install(data_dir)
    faults.install_hooks("the detection app")
    # Before the first VideoCapture, which reads them (see apply_ffmpeg_env);
    # after logging, so the one line saying what was applied is seen. Start
    # only: an edit takes effect at the next app restart.
    camera_mod.apply_ffmpeg_env(ffmpeg_cam)
    storage_cfg = cfg.get("storage") or {}
    store = Store(data_dir, storage_cfg.get("keep_snapshots", 0),
                  min_free_mb=storage_cfg.get("min_free_mb"),
                  max_event_bytes=int(float(storage_cfg.get("max_events_mb")
                                            or 64) * 1024 * 1024),
                  live_days=float(storage_cfg.get("events_live_days", 7) or 0),
                  archive_dir=storage_cfg.get("archive_dir") or None)
    # A restart forgets which snapshots were waiting for their timer: show them.
    store.release_held()
    # app.log grows with the loop's own prints; bound it the same way
    # events.jsonl is bounded (current + one previous file).
    max_log_bytes = int(float(storage_cfg.get("max_log_mb") or 16) * 1024 * 1024)
    stream_port = (cfg.get("app") or {}).get("stream_port", 5102)
    # Event debouncing (see _gated_events): how often an ONGOING condition
    # re-announces itself, and how long it must stay clear before the incident
    # is considered over. [app] keys so a deployment can tune either; the
    # defaults need no config change.
    app_cfg = cfg.get("app") or {}
    heartbeat_s = float(app_cfg.get("event_heartbeat_s", 60) or 0)
    clear_hold_s = float(app_cfg.get("event_clear_hold_s", 2.0) or 0)

    def _build_det(table):
        """detector_mod.build with the CURRENT data dir riding along (private
        key, copy only -- the raw tables keep comparing equal on reload).
        The labels loader retries a missing configured path there, because a
        storage migration moves data/labels_<model>.txt with the data dir."""
        # ...and how many cameras this system captures, which is the most
        # frames a batching detector can ever be handed in one pass. See
        # hailo._auto_batch: a batch wider than that makes the loop wait
        # for a second frame from every camera before it infers anything.
        return detector_mod.build(dict(table, _data_dir=data_dir,
                                       _cameras=len(_camera_list(cfg)) or 1))
    det_cfg = cfg.get("detector") or {}
    # Every camera this system captures. One entry = exactly the old behaviour.
    cam_list = _camera_list(cfg)
    cam_cfg = dict(cam_list[0]) if cam_list else dict(cfg.get("camera") or {})
    capture_cfg = dict(cfg.get("capture") or {})
    # Which rows of the burned-in status box to draw. Read live, like [capture].
    hud_cfg = dict(cfg.get("hud") or {})
    # The camera registers {device -> this stream port} when it owns a physical
    # device, so ANOTHER system can consume our /raw feed -- the mechanism that
    # lets several systems run on one camera.
    cam_cfg["_stream_port"] = stream_port
    # gstreamer owns capture itself and is single-camera by construction, so it
    # keeps its own Camera object; every other kind gets one per _Pipe below.
    cam = camera_mod.Camera(cam_cfg)
    # "gstreamer" is a whole pipeline that owns the camera AND inference, so it
    # replaces camera.py + the detector for this run. Every other kind is a
    # frame-in/detections-out detector fed by camera.py.
    gst_mode = det_cfg.get("kind") == "gstreamer"
    gst = None
    detector_error = None
    if gst_mode:
        det, gst, detector_error = _build_gst(det_cfg, cam_cfg)
        gst_mode = gst is not None          # None => fell back to mock (camera path)
    else:
        # A detector that cannot load (Hailo runtime missing, a bad model file)
        # must NOT take the whole app down -- the camera, the stream and the zone
        # counting still work, and the operator needs to SEE the detector error
        # on the dashboard rather than a device that vanished. Fall back to a
        # SILENT detector (mock only if mock was configured -- invented
        # detections on a real system read as hallucination) and record why;
        # fixing the cause and re-selecting hot-reloads the real one.
        try:
            det = _build_det(det_cfg)
        except Exception as exc:                            # noqa: BLE001
            detector_error = f"{det_cfg.get('kind') or 'configured'} detector failed to load: {exc}"
            log.error("%s - falling back to a silent detector so the stream "
                      "stays up", detector_error, exc_info=True,
                      extra=faults.event(faults.detector_event(exc)))
            det = detector_mod.fallback(det_cfg)
    zone = det_cfg.get("zone")
    anchor = det_cfg.get("anchor", "bottom_center")

    # One pipeline per camera: its own capture, tracker, zone engine and
    # function engine (see _Pipe). The DETECTOR is shared -- the Hailo
    # scheduler already time-slices it, and a second VDevice is exactly what
    # "VDevice released while CIM in use" complains about.
    # Configured cameras, every one of them switched off. _camera_list answers
    # [] for that AND for "no cameras at all", and the fallback below turns []
    # into the legacy [camera] row -- so a system whose cameras were all
    # deactivated opened the legacy source on the next start and held a device
    # nobody had asked it to. Told apart here by the RAW table: entries present,
    # none of them enabled.
    _raw_cams = cfg.get("cameras")
    all_off = bool(isinstance(_raw_cams, list) and _raw_cams and not cam_list)
    pipes = [_Pipe(e, cfg, stream_port, data_dir, store, anchor)
             for e in (cam_list or ([dict(_raw_cams[0], id=str(
                 _raw_cams[0].get("id") or MAIN_CAMERA))] if all_off
                 else [{"id": MAIN_CAMERA, **cam_cfg}]))]
    by_id = {p.id: p for p in pipes}
    primary = pipes[0]
    if all_off:
        # Parked from birth: the pipe exists so pipes[0] holds (HUD, stream,
        # runtime state), but it is never opened and the recovery ladder skips
        # it. Switching a camera back on unparks it on the next config reload.
        for p in pipes:
            p.parked = True
        log.info("every camera is switched off - none opened")
    if not primary.zone_list and not (cfg.get("zones") or []):
        # No [[zones]] configured: honour the legacy pixel rectangle so an
        # existing deployment keeps its violation rule after an upgrade.
        legacy = zones_mod.zone_from_rect(zone, primary.frame_w, primary.frame_h, "Zone")
        if legacy:
            primary.zone_list = [legacy]
            primary.engine = zones_mod.ZoneEngine([legacy], anchor)
            primary.overlays = [legacy.as_dict(primary.frame_w, primary.frame_h)]
    tracker_raw = cfg.get("tracker") or {}   # compared on reload, like zones
    zones_raw = cfg.get("zones")        # compared on reload to spot zone edits
    outputs = cfg.get("outputs") or []  # GPIO/MQTT/Modbus actions fired on events
    # The Modbus Servers the Read and Write nodes name ([[modbus_clients]]).
    # Handed to buses rather than threaded through every node: a node carries
    # the server's id, and the connection it means is looked up at each read
    # and each write, so editing a server takes effect without a re-save.
    buses_mod.set_clients(cfg.get("modbus_clients") or [])
    # The Drive watchdog's last SAVED value per connection survives a restart,
    # so a restart does not write the drive's flash again.
    buses_mod.set_state_dir(data_dir)
    # The MQTT Connections the MQTT nodes name ([[mqtt_clients]]): one shared
    # client each (app/mqtt_bus.py), started on first use. Same hand-over,
    # same reason -- and it runs with AI detection off too, since
    # ai_config() leaves this table alone.
    mqtt_bus.set_clients(cfg.get("mqtt_clients") or [])
    # ...and the ones the outputs publish through connect NOW, so the first
    # alert after a start is not the thing that has to wait for a connect.
    mqtt_bus.prestart(o.get("conn") for o in outputs if isinstance(o, dict))
    functions_raw = cfg.get("functions")

    def _apply_preroll(out_list):
        """Tell each camera how much RUN-UP its Record Clips want kept.

        Read from the outputs rather than configured per camera, because it is
        the output that asks: a clip with "include before" set needs a rolling
        buffer of rendered frames on the camera it names, filled every frame
        whether or not anything fires. Re-applied on every config reload, so
        turning it off on the canvas stops the cost in seconds rather than at
        the next restart.
        """
        want = {}
        for o in out_list or []:
            if str(o.get("type")) != "record" or o.get("enabled") is False:
                continue
            try:
                before = float(o.get("before_s") or 0)
            except (TypeError, ValueError):
                before = 0.0
            if before <= 0:
                continue
            cam = str(o.get("camera") or "")
            # A capture output with no camera named follows the rule that
            # fired, which may be any of them -- so every camera keeps a
            # buffer, exactly as every camera can end up being the one.
            for p in ([_pipe_for(cam)] if cam else pipes):
                if p is not None:
                    want[p.id] = max(want.get(p.id, 0.0), before)
        for p in pipes:
            p.capture.want_preroll(want.get(p.id, 0.0))
    for p in pipes:
        log.info("camera %s: %d zone(s), %d function(s)", p.id,
                 len(p.zone_list), len(p.fn_engine.functions))
    fn_engine = primary.fn_engine
    # With the AI off the rules a Detection Stream feeds sit out (ai_config).
    for p in pipes:
        p.fn_engine.set_inactive(inactive_ids(cfg), cfg.get("_gated") or ())
    # ...and what the ON/OFF points of the Detection Streams and Class Filters
    # read: here, on the path that runs with the AI off too, so an OFF point
    # fires while nothing is detecting.
    functions_mod.set_states(ai_active, paused_filters(cfg))
    log.info("%d function(s): %s", len(fn_engine.functions),
             ", ".join(f.id for f in fn_engine.functions) or "none")
    log.info("%d zone(s) on %s: %s", len(primary.zone_list), primary.id,
             ", ".join(f"{z.name}({z.kind})" for z in primary.zone_list)
             or "none")

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _stop.set())

    # In gstreamer mode the pipeline owns the camera; opening it here too would
    # be a second reader on the same device.
    if not gst_mode:
        for p in pipes:
            if not p.parked:
                p.cam.open()
    runtime.write(data_dir, running=True, started_at=time.time(),
                  pid=os.getpid(), mode="unified", ai_on=ai_active,
                  ai_ok=False, ai_error=None, ai_paused=None,
                  # Never the raw source: state.json reaches every account
                  # (Pipi H2). cam.label is masked; the gst path masks here.
                  camera=(faults.mask_label(str(cam_cfg.get("source", "camera")))
                          if gst_mode else primary.cam.label),
                  detector=det.name,
                  error=detector_error or (None if gst_mode else primary.cam.error))

    def _pipe_for(cam_id):
        """The camera a stream request asked for; the FIRST one only when it
        asked for nothing (the bare path every existing consumer fetches).

        A NAMED camera that is not running answers None, never the first one:
        the request-time 404 only covers new connections, and a viewer whose
        camera was deactivated mid-stream used to have this fallback quietly
        swap its picture to a different camera -- "it went to the next video
        by itself". A None ends that stream; the viewer's reconnect then gets
        the honest 404."""
        cid = str(cam_id or "").strip()
        if not cid:
            return primary
        return by_id.get(cid)

    # The outputs seam's capture hooks: snapshot grabs the latest annotated
    # JPEG; record arms the forward clip window. Both no-op (with one log
    # line) while their System Control switch is off -- the workflow decides
    # WHEN, the operator decides WHETHER.
    #
    # snap defaults ON because event evidence frames have always been saved;
    # the switch exists to STOP that. record defaults OFF because nothing ever
    # recorded video unless asked.
    #
    # `o["camera"]` picks which camera to capture from -- a workflow Snapshot
    # node fed by a rule on camera 2 must save camera 2's frame, not camera 1's.
    # A NAMED camera that is not running answers None (see _pipe_for), and a
    # capture output may now name one -- so both hooks say which camera is
    # missing instead of dying on it. Deactivating a camera must cost the
    # picture, not the app.
    def _capture_pipe(o, what):
        p = _pipe_for(o.get("camera"))
        if p is None:
            log.warning("capture: %s skipped - camera %r is not running",
                        what, o.get("camera"))
        return p

    def _source_oid(o, event):
        """The object that caused THIS firing, when the output asked to know.

        It rides on the event -- the engine names the object whose clock or
        whose crossing raised the alarm -- rather than being threaded through
        the graph as a wire: the wire would say "an object", the event says
        WHICH. Blank whenever the rule is about a place rather than about
        anybody (a zone's total, a formula), which is a soft no-op by design:
        the checkbox is a request, not a promise the graph can always keep.
        """
        if not o.get("oid_tag"):
            return ""
        ev = event or {}
        oid = ev.get("source_oid")
        if oid is None:
            oid = (ev.get("detail") or {}).get("track")
        return "" if oid is None else str(oid)

    def _snap(o, event=None):
        if not capture_cfg.get("snap", True):
            log.warning("capture: snapshot skipped - switch it on in "
                        "System Control")
            return
        p = _capture_pipe(o, "snapshot")
        if p is None:
            return
        # Encoded HERE, from the frame this moment was drawn on, rather than
        # taken from the stream's cache: that cache is only refreshed for
        # viewers now, and a snapshot of an alert nobody was watching would
        # otherwise be up to a second old -- which for a forklift is a
        # different picture. Painted here when [video] on_demand kept only
        # the ingredients (_keep_shot) -- the same moment, the same boxes.
        shot_img = _shot_frame(p)
        shot = camera_mod.encode_jpeg(shot_img) if shot_img is not None else None
        held = {"hold": True} if o.get("hold") else {}
        sid = p.capture.snap(shot or p.jpeg or p.cam.last_jpeg(), o.get("label") or "",
                             oid=_source_oid(o, event), **held)
        if sid:
            _snap_record(p, o, event, sid)

    #: (function id, track id) -> (the snapshot waiting to learn how long the
    #: stay it was taken in really lasted (app/snapmeta.py, "THE TIMER"), when
    #: it was taken). A HELD snapshot is shown when the stay ends, or after
    #: SNAP_HOLD_MAX_S whatever happens.
    snap_wait = {}
    SNAP_HOLD_MAX_S = 6 * 3600

    def _snap_sweep():
        now = time.time()
        for key in [k for k, (_s, at) in snap_wait.items() if now - at > SNAP_HOLD_MAX_S]:
            store.release_snapshot(snap_wait.pop(key)[0])

    def _snap_record(p, o, event, sid):
        """What this snapshot knows about itself: its sidecar record, the
        close-up of each detected object when the node asked for them, and the
        real timer data when a timer was running. Best effort -- the picture
        is already saved, and nothing here may take the loop down."""
        waiting = False
        try:
            _snap_sweep()
            detail = (event or {}).get("detail") or {}
            raw = (event or {}).get("source_oid")
            if raw is None:
                raw = detail.get("track")
            oid = "" if raw is None else str(raw)
            fn_id = str(detail.get("function") or "")
            presence = None
            if fn_id and raw is not None:
                presence = p.fn_engine.presence(fn_id, raw)
            timer = snapmeta_mod.timer_from_event(event, presence)
            crops, jpegs = [], []
            frame = getattr(p, "last_frame", None)
            if o.get("crop") and frame is not None and hasattr(frame, "shape"):
                fh, fw = frame.shape[:2]
                # Only what VIOLATES is cut out: the classes of the rule that
                # fired (in its zone, if it has one), not every box in view.
                fn = next((f for f in p.fn_engine.functions if f.id == fn_id), None)
                # An If / Else if has no classes of its own: the classes the
                # pipeline connects are those of the rule it reads (its clock,
                # or the rule its Trigger names the objects of).
                if fn is not None and not fn.classes:
                    src_id = fn.params.get("timer_fn") or next(
                        (t.get("oid_source") for t in (fn.triggers or ())
                         if t.get("oid_source")), "")
                    src = next((f for f in p.fn_engine.functions if f.id == src_id), None)
                    if src is not None:
                        fn = src
                bad = snapmeta_mod.violating(
                    getattr(p, "last_dets", None) or [],
                    accepts=(lambda d: fn.accepts(d.get("label", ""), d.get("model")))
                    if fn is not None else None,
                    has_filter=bool(fn is not None and fn.classes),
                    zone=str((fn.params or {}).get("zone") or "") if fn is not None else "",
                    oid=oid)
                for c in snapmeta_mod.crop_boxes(bad, fw, fh, oid):
                    x1, y1, x2, y2 = c["box"]
                    jpeg = camera_mod.encode_jpeg(frame[y1:y2, x1:x2], quality=88)
                    if jpeg:
                        c["n"] = len(jpegs)
                        crops.append(c)
                        jpegs.append(jpeg)
            cam_name = faults.mask_label(p.cfg.get("name") or "") or str(p.id)
            meta = snapmeta_mod.build_meta(
                sid, time.time(), p.id, cam_name, o.get("label") or "", oid,
                event, str(o.get("location") or ""), crops, timer,
                violation=str(o.get("violation") or ""),
                notice_title=str(o.get("notice_title") or ""))
            store.save_snapshot_extra(sid, meta, jpegs)
            if timer and fn_id and raw is not None:
                if len(snap_wait) > 200:                 # never grows without end
                    store.release_snapshot(snap_wait.pop(next(iter(snap_wait)))[0])
                snap_wait[(str((presence or {}).get("fn") or fn_id), str(raw))] = (
                    sid, time.time())
                waiting = True
        except Exception as exc:                            # noqa: BLE001
            log.warning("[%s] snapshot record not saved: %s", p.id, exc)
        # Nothing to wait for (no timer, or no object): show it now.
        if o.get("hold") and not waiting:
            store.release_snapshot(sid)

    #: (clock id, track id) -> (outputs waiting for that stay to end, the event,
    #: the frame's stats, the limit, when). The outputs are the Data Table rows a
    #: Trigger set to WAIT FOR THE TIMER holds back; the moment itself (alert,
    #: log, colours) was raised at once. Shown after SNAP_HOLD_MAX_S regardless.
    out_wait = {}

    def _park_outputs(pipe, trig, event, counts, parked):
        fn = next((f for f in pipe.fn_engine.functions if f.id == trig.get("id")), None)
        clock = fn.params.get("timer_fn") if fn is not None else None
        raw = trig.get("source_oid")
        if not clock or raw is None:               # nothing to wait for
            outputs_mod.fire(parked, event, capture_hooks, counts)
            return
        now = time.time()
        for key in [k for k, v in out_wait.items() if now - v[4] > SNAP_HOLD_MAX_S]:
            late, ev, cn = out_wait.pop(key)[:3]
            outputs_mod.fire(late, ev, capture_hooks, cn)
        out_wait[(str(clock), str(raw))] = (
            parked, event, dict(counts), float(fn.params.get("timer_s") or 0), now, pipe)

    def _fire_parked(clock_id, track, detail):
        """The stay ended: complete what waited for it, with the real total and
        how far past the limit it went."""
        got = out_wait.pop((str(clock_id), str(track)), None)
        if not got:
            return
        parked, event, counts, limit, _at, pipe = got
        d = event.setdefault("detail", {})
        for key in ("duration_s", "entered_at", "exited_at"):
            if detail.get(key) is not None:
                d[key] = detail[key]
        if detail.get("duration_s") is not None and limit > 0:
            d["threshold_s"] = limit
            d["exceeded_s"] = round(max(0.0, float(detail["duration_s"]) - limit), 2)
        if detail.get("duration_s") is not None:
            counts = pipe.fn_engine.restate(counts, clock_id, detail["duration_s"])
        outputs_mod.fire(parked, event, capture_hooks, counts)

    def _snap_finish(fn_id, track, total_s):
        """A stay ended: the snapshot taken during it learns the real total."""
        got = snap_wait.pop((str(fn_id), str(track)), None)
        if got:
            sid = got[0]
            store.update_snapshot_meta(
                sid, lambda m: snapmeta_mod.finish_timer(m, total_s))
            store.release_snapshot(sid)

    def _camsnap(p):
        """Keep the frame a Snapshot stream has just detected, with its boxes,
        so Camera Setup can show everything that was snapped. Saved whatever
        the Snapshots switch says: this is what Snapshot mode PRODUCES, not an
        extra a rule asked for, and it is the only record of what was looked
        at between triggers. The store's own cap prunes the oldest."""
        try:
            shot_img = _shot_frame(p)
            jpeg = camera_mod.encode_jpeg(shot_img) if shot_img is not None else None
            if jpeg:
                p.snap_jpeg = jpeg          # the live view holds this until the next snap
                p.snap_event.set()          # ...and the publish shows it NOW, not at its next tick
                p.capture.snap(jpeg, "camera snap", kind="snp")
        except Exception as exc:                            # noqa: BLE001
            log.warning("[%s] camera snap not saved: %s", p.id, exc)

    def _record(o, event=None):
        if not capture_cfg.get("record"):
            log.warning("capture: record skipped - switch it on in "
                        "System Control")
            return
        p = _capture_pipe(o, "clip")
        if p is None:
            return
        # The clip is played back at the rate in its header, so it has to be
        # written at the rate frames reach the RECORDER -- which is the
        # detection loop's rate, because one tick offers one frame per camera.
        # `measured_fps` is a different clock: it counts every grab() the
        # camera thread makes, whether or not the loop kept the frame. On a
        # Hailo sharing its budget across cameras those two numbers differ by
        # 3-4x, and a 30fps header over 8fps of offered frames plays the event
        # as a timelapse.
        p.capture.set_fps(p.fps_ema or p.cam.measured_fps or p.cam.fps)
        p.capture.record(o.get("after_s") or o.get("duration_s") or 5,
                         o.get("label") or "",
                         before_s=o.get("before_s") or 0,
                         oid=_source_oid(o, event))

    # A Dataset node records a ROW of the operator's own columns. This app has
    # no database -- it imports nothing from core/ -- so the row travels as an
    # ordinary event carrying detail.dataset, and the master forks it into its
    # own table on ingest (core/repos/datasets.py). That also means a dataset
    # is replayed and retained exactly like the log it lives in.
    dataset_seq = [0]

    def _log_row(o, cols):
        dataset_seq[0] += 1
        stamp = f"{int(time.time() * 1000)}-ds{dataset_seq[0]}"
        detail = {"dataset": o.get("dataset"), "cols": cols}
        # "Update the existing row": the master refreshes this object's row
        # instead of adding one, keyed on the column named here.
        key = outputs_mod.dataset_upsert_key(o, cols)
        if key:
            detail["upsert"] = key
        store.append({
            "id": f"demo-{stamp}",
            "ts": _now_iso(),
            "type": "info",
            "severity": "ok",
            "label": str(o.get("name") or o.get("dataset") or "dataset")[:60],
            "device": None,
            "snapshot_id": None,
            "detail": detail,
        })

    def _zone_enable(zone_id, on):
        """The Zone Switch output's reach into the LIVE engines: flip the
        matching zone's enabled flag on every camera that has it, and rebuild
        that camera's drawn overlays so the feed shows the zone appear or
        disappear. Runtime only -- config is never written, so the operator's
        saved checkbox state returns on restart."""
        zid = str(zone_id or "").strip()
        if not zid:
            return
        for p in pipes:
            hit = False
            for z in (p.engine.zones if p.engine else []):
                if str(z.id) == zid:
                    z.enabled = bool(on)
                    hit = True
            if hit:
                p.overlays = [z.as_dict(p.frame_w, p.frame_h)
                              for z in (p.engine.zones if p.engine else [])]

    # -- modbus server ------------------------------------------------------
    # This box AS a Modbus device, for a PLC to poll. The listener is owned
    # here because the VALUES are here: zone counts and rule states exist in
    # this loop's stats at a freshness nothing outside the process can match,
    # and a PLC scan cannot use a copy fetched over HTTP one poll ago.
    modbus_srv = modbus_server_mod.ModbusServer()
    _modbus_pending = []

    def _modbus_reg(reg):
        """One register's current value, on its way to the wire.

        Collected per tick rather than written straight through: the outputs
        run one at a time, and handing the server a batch means one lock for
        the scan instead of one per register.
        """
        _modbus_pending.append(reg)

    capture_hooks = {"snapshot": _snap, "record": _record, "dataset": _log_row,
                     "zone_enable": _zone_enable, "modbus_reg": _modbus_reg}

    # -- video transport ----------------------------------------------------
    # "webrtc" (default) or "mjpeg". WebRTC ADDS a publish to the local
    # gateway; the MJPEG feed below is served either way, because it is what
    # the page falls back to when the gateway or the publish is not there,
    # and what the hub's Live Wall and the fleet agent already consume.
    #
    # The default MUST match core/services/streaming.py DEFAULT_MODE (pinned
    # by tests/smoke_webrtc.py): the master offers a browser whatever this
    # process publishes. Default webrtc since 2026-10-09; a system saying
    # "mjpeg" is respected, and a box that cannot publish (no GStreamer, no
    # openh264, gateway down) lands on mjpeg below exactly as before.
    stream_mode = str((cfg.get("app") or {}).get("stream")
                      or "webrtc").strip().lower()
    if stream_mode not in ("mjpeg", "webrtc"):
        stream_mode = "webrtc"
    # What the system ASKED, before a box that cannot encode degrades it:
    # passthrough needs no encoder, only the gateway (app/passthrough.py).
    stream_asked = stream_mode
    publisher_mod = None
    if stream_mode == "webrtc":
        try:
            from app import publisher as publisher_mod       # noqa: PLC0415
        except ImportError as e:                             # pragma: no cover
            log.warning("webrtc requested but publisher unavailable (%s); "
                        "serving mjpeg", e)
            stream_mode = "mjpeg"
        else:
            if not publisher_mod.available():
                log.warning("webrtc requested but this box cannot publish "
                            "(needs gstreamer1.0-rtsp + openh264); serving "
                            "mjpeg")
                stream_mode, publisher_mod = "mjpeg", None
    #: A camera's path on the gateway. MUST match core/services/streaming.py
    #: path_name() -- the app and the master have to name the same stream, and
    #: the app cannot import core/, so the rule is duplicated and tested
    #: against that one (tests/smoke_webrtc.py).
    _slug = str((cfg.get("app") or {}).get("slug") or "system")

    def stream_url(camera_id):
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(camera_id or "main")).strip("-")
        base = re.sub(r"[^a-zA-Z0-9_-]+", "-", _slug).strip("-") or "system"
        return f"rtsp://127.0.0.1:8554/{base}__{safe or 'main'}"

    if stream_mode == "webrtc":
        up = publisher_mod.ensure_gateway()
        log.info("video transport: webrtc (gateway %s",
                 "up)" if up else "DOWN - mjpeg until it returns)")

    # Who draws the live overlay ([video] overlay, app/overlay_feed.py). In
    # "client" mode the frames go out plain and the browser draws from the
    # messages this feed makes -- only for cameras a browser is listening to.
    # Read live: a reload flips it (and every pipe's caches) in place.
    video_cfg = overlay_mod.settings(cfg)
    overlay_feed = overlay_mod.Feed(
        hz=video_cfg["hz"],
        resolver=lambda cid: (lambda p: p.id if p is not None else None)(
            _pipe_for(cid)))
    overlay_feed.enabled = video_cfg["mode"] == "client"
    # [video] on_demand (app/view_gate.py): pause a camera's legacy view work
    # while nobody watches it. Read live, mutated in place like hud_cfg.
    video_save = view_gate_mod.settings(cfg)

    # Who carries the live VIEW ([video] feed, app/passthrough.py): this
    # process (legacy, the default) or the gateway pulling an RTSP camera's
    # main stream itself. Synced at start, on every reload and re-ensured on
    # the 5 s balance check; the camera URLs stay in `views`' memory.
    views = passthrough_mod.Views(_slug, log=log)

    def _sync_views(cfg_now, sweep=False):
        """Bring every camera's view in line with `cfg_now`. The ORDER is the
        point: a camera going passthrough stops its legacy publish before the
        gateway is asked to pull under the same name, and a camera going back
        has its pull deleted before the publish thread may start again."""
        feed = passthrough_mod.feed_setting(cfg_now)
        wanted = {p.id: passthrough_mod.decide(
                      feed, overlay_feed.enabled, stream_asked, p.cfg,
                      gst_mode=gst_mode, parked=p.parked)
                  for p in pipes}
        for p in pipes:
            if wanted[p.id][0] == "passthrough" and not p.passthrough:
                p.passthrough = True
                p.drop_publisher()
        status = views.sync(wanted, sweep=sweep)
        for p in pipes:
            p.passthrough = views.passthrough(p.id)
            # Reported only when the system asked: a default system's
            # state.json is byte for byte what it was.
            p.view = status.get(p.id) if feed == "passthrough" else None

    server = _serve_stream(
        # The annotated feed is PAINTED FOR THE VIEWER (see _annotated_jpeg):
        # last known boxes on the newest frame, at the viewer's rate, off the
        # detection loop entirely.
        lambda cid: _annotated_jpeg(_pipe_for(cid)),
        # ...and paced at what the CAMERA delivers, not at the inference rate.
        # The picture is repainted per viewer now, so pacing it by the chip's
        # share of a shared accelerator would throw away frames the camera
        # already produced -- which is the stutter this whole path removes.
        # A held snapshot picture is sent four times a second, not at camera speed.
        lambda cid: (lambda p: (4.0 if p.snap is not None else
                                max(float(p.cam.fps or 0),
                                    float(p.cam.measured_fps or 0), 1.0))
                     if p else 1)(_pipe_for(cid)),
        (cfg.get("app") or {}).get("stream_port", 5102),
        # /raw = unannotated, for sharing with another system
        get_raw=lambda cid: _raw_jpeg(_pipe_for(cid)),
        # ...paced at what the camera DELIVERS (fps is rewritten to the chip
        # allowance by the balancer; measured_fps is the grab thread's count)
        get_raw_fps=lambda cid: (lambda p: max(
            float(p.cam.fps or 0), float(p.cam.measured_fps or 0), 1.0)
            if p else 1.0)(_pipe_for(cid)),
        # /depth: the aligned depth grid with its scale and the lens, for the
        # master's floor calibration and manual measurements. None for a
        # camera that measures no depth, which the handler turns into a 404.
        get_depth=lambda cid: (lambda p: (
            (*p.cam.depth_frame(), p.cam.intrinsics())
            if p is not None else None))(_pipe_for(cid)),
        # /video_feed?view=depth: the same feed, drawn on the depth picture.
        get_depth_view=lambda cid: (lambda p: _depth_view_jpeg(p)
                                    if p is not None else None)(_pipe_for(cid)),
        # /detections: what the camera sees right now, for the measurement
        # setup to tie the operator's taps to the object under them.
        get_dets=lambda cid: (lambda p: (
            (p.id, p.frame_w, p.frame_h, list(getattr(p, "last_dets", None) or []))
            if p is not None else None))(_pipe_for(cid)),
        # /frame: the newest colour frame, for the calibration's colour scene.
        get_frame=lambda cid: (lambda p: p.cam.latest()[0]
                               if p is not None else None)(_pipe_for(cid)),
        bind=(cfg.get("app") or {}).get("stream_bind", DEFAULT_STREAM_BIND),
        # /overlay: the overlay as data, for a browser that draws it.
        overlay_feed=overlay_feed,
        # Does this id name a camera at all? Kept separate from _pipe_for,
        # whose first-camera fallback the bare-path consumers depend on.
        probe=lambda cid: by_id.get(str(cid or "").strip()))
    log.info("cameras=%s detector=%s data=%s", [p.id for p in pipes],
             det.name, data_dir)

    seq = 0
    detect_fails = 0                    # consecutive inference failures
    infer_error = [""]                  # ...and what the last one said
    # When to try rebuilding after a fallback. A detector that failed AT
    # STARTUP schedules a retry too: with several apps (re)starting at once
    # the Hailo race is exactly when init fails, and without this the loser
    # ran camera-only ("running · none") until somebody restarted it -- the
    # mid-run failure ladder below already retried, only the boot path gave
    # up for good.
    detector_retry_at = (_retry_after(DETECTOR_RETRY_MIN_S)
                         if detector_error and not gst_mode else 0.0)
    # Grows toward DETECTOR_RETRY_S while rebuilds keep failing; resets on
    # success. Starts low: the common cause (a neighbour bouncing the shared
    # chip) is over before the first retry fires.
    detector_retry_delay = DETECTOR_RETRY_MIN_S
    last_gst_error = None               # so a stuck pipeline logs once, not per tick
    fps_ema = 0.0                       # measured processing rate (shown on the HUD)
    t_prev = time.time()
    base_fps = float(cam_cfg.get("fps", 10) or 10)
    # Pacing is the _fps_share table by default (see there). [app].fps_budget
    # is the escape hatch: set it and the old even split returns
    # (budget / cameras), for a box whose chip is faster or slower than the
    # table assumes. UNSET -- not 100 -- so "is there an override?" is a real
    # question rather than one the default has already answered.
    fps_budget = float((cfg.get("app") or {}).get("fps_budget") or 0) or 0.0
    next_balance = 0.0                  # when to re-check chip load share
    # The optional fair-share balancer ([system] chip_balance, box-wide,
    # default off -- app/chipshare.py). Off, it only writes this app's
    # heartbeat file and answers None, and the pacing below is the table.
    chip_bal = chipshare_mod.Balancer()
    interval = 1.0 / max(1.0, float(cam_cfg.get("fps", 10) or 10))
    cfg_mtime = _config_mtime()
    next_cfg_check = time.time() + _CONFIG_POLL_S

    # -- camera watchdog (app/camwatch.py) ----------------------------------
    # A camera silent for [app] camera_lost_restart_s (default 5) restarts
    # detection by itself. The FULL restart is the AI switch's own path run
    # twice: `restart_phase` "down" makes the next reload apply the config
    # with the AI forced off (camera(s) and accelerator released, as Stop
    # does), and "up" makes the one after apply it as written (re-probed and
    # reacquired, as Start does). A user's Stop or a Power off landing in
    # between simply wins: "up" reads the config the user wrote.
    #
    # A camera NOT FOUND (no device, a host that refuses) stops detection
    # instead: "stop" makes the next reload apply the AI forced off and
    # leaves `restart_phase` at "paused", which every later reload keeps
    # applying (a config edit does not wake the chip) until the presence
    # probe sees a camera again and sets "up". ai_on stays the user's word
    # throughout; `ai_paused` says the box stopped it.
    watch = camwatch_mod.CamWatch()
    watch.configure((cfg.get("app") or {}).get("camera_lost_restart_s",
                                               camwatch_mod.LOST_AFTER_S))
    presence = camwatch_mod.Presence()
    if ai_active:
        watch.arm(time.time())
    restart_phase = None
    # The lost cameras whose detection-fed rules sit out until they are
    # back -- held like AI off, so a dead camera never publishes zeros.
    held = set()
    held_ids = detection_fed(cfg)
    base_inactive = inactive_ids(cfg)
    # The ON/OFF points' share of it: these come back as a fresh start
    # (FunctionEngine.set_inactive `gated`), and their outputs with no memory.
    base_gated = list(cfg.get("_gated") or ())
    gated_outs = set(cfg.get("_gated_outputs") or ())

    def _apply_inactive():
        """What sits out, per camera: the AI switch's and the paused Class
        Filters' rules everywhere, plus the detection-fed ones on a camera
        the watchdog is waiting for."""
        for p_ in pipes:
            p_.fn_engine.set_inactive(set(base_inactive) | (
                set(held_ids) if p_.id in held else set()), base_gated)

    # -- zone editor hint ---------------------------------------------------
    # Read on its own short clock (a stat, and a small parse only when the
    # file actually changed) so that dragging a zone feels immediate without
    # putting a file read on every frame.
    hint_path = os.path.join(str(data_dir), OVERLAY_HINT_FILE)
    hint = {"checked": 0.0, "mtime": 0.0, "until": 0.0, "cameras": ()}

    def _hide_zone_overlay(pipe_id, now2):
        """Whether THIS camera's zone outlines are suppressed right now."""
        if now2 - hint["checked"] >= OVERLAY_HINT_POLL_S:
            hint["checked"] = now2
            try:
                mtime = os.path.getmtime(hint_path)
            except OSError:
                mtime = 0.0
                hint["until"] = 0.0
                hint["mtime"] = 0.0
            if mtime and mtime != hint["mtime"]:
                hint["mtime"] = mtime
                try:
                    with open(hint_path, "r", encoding="utf-8") as f:
                        raw = json.load(f) or {}
                    until = float(raw.get("hide_zones_until") or 0)
                    cams = raw.get("cameras") or ()
                    hint["cameras"] = tuple(str(c) for c in cams)
                except (OSError, ValueError, TypeError):
                    until = 0.0
                    hint["cameras"] = ()
                # Clamped against the WRITER's clock as well as ours: a file
                # asking for an hour gets thirty seconds.
                hint["until"] = min(until, mtime + OVERLAY_HINT_MAX_S)
        if now2 >= hint["until"]:
            return False
        # No camera list = every camera in this system; a list scopes it to
        # the one being edited, so a second viewer keeps its outlines.
        return not hint["cameras"] or str(pipe_id) in hint["cameras"]

    def _read_frame(pipe, now2):
        """One camera's frame, with the fallback/reclaim ladder that keeps a
        feed alive when another system owns the device."""
        if pipe.parked:
            # Switched off: no frames, and above all NO recovery. The ladder
            # below exists to resurrect a camera that died, and it cannot tell
            # that from one the operator deliberately switched off -- which is
            # exactly how a deactivated camera reopened its own device seconds
            # after being closed.
            return None
        if getattr(pipe.cam, "asleep", False):
            # A Snapshot camera closed between snaps on purpose (snapcam): no
            # frames, and no recovery ladder -- it is not broken.
            return None
        if pipe.opening:
            # Still connecting. The failure ladder below fires after ~2s of
            # empty reads and would swap a perfectly good RTSP camera for
            # synthetic in the middle of its handshake.
            return None
        frame = pipe.cam.read()
        if frame is None and not pipe.cam.synthetic:
            pipe.cam_fails += 1
            # ~2s of empty reads, and never more often than next_recover
            # allows. Without that second gate a camera that stays dead
            # re-runs the whole three-attempt ladder every couple of
            # seconds for ever -- log spam, and a reopen storm against a
            # device that is not there. A failed ladder pushes the gate out
            # (below), so retries settle to one every 10s.
            if pipe.cam_fails >= 20 and now2 >= pipe.next_recover:
                pipe.cam_fails = 0
                configured = str(pipe.cfg.get("source", ""))
                base = {"width": pipe.frame_w, "height": pipe.frame_h,
                        "fps": pipe.cfg.get("fps", 10),
                        "_stream_port": pipe.cfg.get("_stream_port")}
                replacement = msg = err = None
                # FIRST: the camera may just have hiccupped (a USB link
                # dropping frames, an RTSP stream mid-restart). Force the
                # connection closed and reopen it, up to three times, before
                # concluding anything about ownership or giving up -- a
                # reopen is what actually clears a wedged V4L2 handle, and
                # three attempts is the difference between "a bad second"
                # and "the camera is gone".
                # A USB camera's capture node MOVES between /dev/video
                # indexes when it re-enumerates (its metadata twin takes the
                # old number), so a numeric source is retried against every
                # real capture node, configured index first. This is the
                # single biggest cause of "frozen until someone re-picks the
                # camera": the config said 0, the camera came back as 1.
                candidates = [dict(pipe.cfg)]
                why = []
                if configured.isdigit():
                    for idx in camera_mod.usb_capture_indexes():
                        if str(idx) != configured:
                            candidates.append({**pipe.cfg, "source": str(idx)})
                for attempt in (1, 2, 3):
                    log.warning("[%s] camera %r stopped delivering; "
                                "reconnecting (attempt %d/3)", pipe.id,
                                configured, attempt,
                                extra=faults.event(
                                    faults.CAM_STREAM_STALLED,
                                    camera=pipe.id, attempt=attempt))
                    if pipe is primary:
                        runtime.write(data_dir,
                                      error=f"reconnecting camera ({attempt}/3)…")
                    cand = candidates[min(attempt - 1, len(candidates) - 1)]
                    if str(cand.get("source")) != configured:
                        log.info("[%s] trying capture node /dev/video%s - "
                                 "the camera may have re-enumerated onto it",
                                 pipe.id, cand["source"])
                    retry = _try_open(cand, why if attempt == 1 else None)
                    if retry is not None:
                        # Give the fresh connection a beat to deliver -- an
                        # open that succeeds but never produces a frame is
                        # the same failure with better manners.
                        deadline = time.time() + 1.5
                        got = None
                        while got is None and time.time() < deadline:
                            got = retry.read()
                            if got is None:
                                time.sleep(0.15)
                        if got is not None:
                            replacement, msg, err = retry, \
                                f"camera {configured!r} reconnected", None
                            break
                        retry.close()
                if replacement is None:
                    # Another system may already OWN this device and re-serve
                    # it at /raw -- consume that shared feed, so several
                    # systems run on the same camera.
                    shared = camera_mod.shared_feed_source(configured)
                else:
                    shared = None
                if shared and str(pipe.cam.source) != shared:
                    replacement = _try_open({**base, "source": shared})
                    msg = err = faults.redact(
                        f"camera {configured!r} is owned by another "
                        f"system; consuming its shared feed {shared}")
                if replacement is None and str(pipe.cam.source) != configured:
                    # We were on a fallback (say, a shared feed whose owner
                    # stopped). The owner stopping frees the DEVICE -- take the
                    # configured source back before ever resorting to synthetic.
                    replacement = _try_open(dict(pipe.cfg))
                    if replacement is not None:
                        msg, err = f"reclaimed camera {configured!r}", None
                if replacement is None:
                    # NO synthetic stand-in. A fake moving picture in place of
                    # a dead camera is a system that looks like it is watching
                    # something -- counts tick, zones arm, the wall looks
                    # healthy -- while the real scene goes unwatched. The
                    # camera stays as it is (reporting its failure) and the
                    # 10s reclaim loop below keeps trying the real device; the
                    # page shows the disconnected card instead of a picture.
                    #
                    # The error the UI's camera panel keys on: a stable
                    # prefix, then the detail.
                    # The CAUSE, never this function's own previous
                    # sentence: the error is written back to pipe.cam.error,
                    # so re-wrapping it each retry grew "camera disconnected:
                    # X (camera disconnected: X (camera disconnected: ...))"
                    # -- three attempts deep in the log and unreadable in the
                    # UI card that shows it.
                    # The configured source's OWN answer on the first retry
                    # wins when it gave one ("no device at /dev/video0",
                    # "username or password rejected"): that is the cause
                    # now, and the card turns it into the fix.
                    cause = str((why[0] if why else pipe.cam.error)
                                or "no answer")
                    if cause.startswith("camera disconnected: "):
                        inner = cause.split("(", 1)
                        cause = (inner[1].rstrip(")") if len(inner) == 2
                                 else "no answer")
                    err = faults.redact(
                        "camera disconnected: "
                        f"{configured!r} gave no frames after 3 "
                        f"reconnect attempts ({cause})")
                    pipe.cam.error = err
                    if pipe is primary:
                        runtime.write(data_dir, error=err)
                    log.error("[%s] %s", pipe.id, err,
                              extra=faults.event(faults.CAM_DISCONNECTED,
                                                 camera=pipe.id))
                    pipe.next_recover = now2 + 10.0     # settle the retries
                if replacement is not None:
                    # `err` is set here only when the replacement is somebody
                    # else's shared feed -- this camera is being used by
                    # another system, which is a fault an operator has to be
                    # told about, not a recovery to celebrate.
                    if err:
                        log.warning("[%s] %s", pipe.id, msg,
                                    extra=faults.event(faults.CAM_BUSY,
                                                       camera=pipe.id))
                    else:
                        log.info("[%s] %s", pipe.id, msg)
                    pipe.cam.close()
                    pipe.cam = replacement
                    if pipe is primary:
                        runtime.write(data_dir, camera=pipe.cam.label, error=err)
                    frame = pipe.cam.read()
        else:
            pipe.cam_fails = 0
        # Fallback recovery: while on a stand-in source retry the CONFIGURED
        # one every few seconds -- the system that held the device may have
        # stopped, and staying on synthetic forever reads as a broken camera.
        if (now2 >= pipe.next_recover
                and str(pipe.cam.source) != str(pipe.cfg.get("source"))
                and str(pipe.cfg.get("source", "")) not in ("", "synthetic")):
            pipe.next_recover = now2 + 10.0
            reclaimed = _try_open(dict(pipe.cfg))
            if reclaimed is not None:
                # Same beat the reconnect ladder gives a fresh connection:
                # a single immediate read() is None on a perfectly healthy
                # camera (the grab thread has not delivered its first frame
                # yet), so the old check could never reclaim a real device
                # -- it just cycled open/close against it every 10s.
                deadline = time.time() + 1.5
                got = None
                while got is None and time.time() < deadline:
                    got = reclaimed.read()
                    if got is None:
                        time.sleep(0.15)
                if got is not None:
                    log.info("[%s] configured camera %r is back; switching "
                             "to it", pipe.id, pipe.cfg.get("source"))
                    pipe.cam.close()
                    pipe.cam = reclaimed
                    if pipe is primary:
                        runtime.write(data_dir, camera=pipe.cam.label, error=None)
                else:
                    reclaimed.close()
        return frame

    def _pre(pipe, frame):
        """This camera's software pre-processing, between the grab and the
        detector. Off unless the camera entry carries a `preprocess` list, and
        then a no-op that returns the very same frame object.

        WHAT SEES THE PROCESSED PICTURE. Everything past this point in the
        loop: the detector, the tracker, the overlay the loop caches, the
        clips and the snapshots. There is one frame per camera per pass and
        keeping a second, unprocessed copy would double the memory the loop
        holds per camera -- so the rule is simply that the app works on the
        picture the operator told it to work on. The LIVE preview is the
        exception and goes the other way (see _annotated_jpeg): it paints on
        the camera's own raw grab, so what an operator watches is what the
        camera sees unless they ask otherwise.
        """
        if frame is None:
            return None
        return preprocess_mod.apply(frame, pipe.cfg.get("preprocess"))

    def _infer(frame):
        """One frame through the shared detector. See _infer_batch."""
        return _infer_batch([frame])[0]

    def _infer_batch(frames):
        """Run the SHARED detector on every camera's frame in ONE call, with
        the failure ladder. Returns one detection list per frame, in order.

        One call rather than one per camera because that is what lets the
        Hailo detector keep every camera's frame in flight at once
        (detect_many): submitted together, the chip overlaps them, and a
        two-camera system stops paying two full round trips per loop.
        """
        nonlocal det, detect_fails, detector_retry_at, detector_retry_delay
        # A frame the detector cannot read is a BAD INPUT, not a broken chip.
        # Counting it as an inference failure is how one unreadable frame
        # ended up demoting a perfectly healthy Hailo to the `none` detector,
        # which then reported nothing long after the camera recovered. Those
        # answer [] in their slot and never reach the detector.
        out = [[] for _ in frames]
        good = [(i, f) for i, f in enumerate(frames)
                if f is not None and hasattr(f, "shape")]
        if not good:
            return out
        t0 = time.monotonic()
        try:
            results = det.detect_many([f for _, f in good])
        except Exception as exc:                        # noqa: BLE001
            _infer_failed(exc)
            return out
        chip_bal.note_infer(time.monotonic() - t0, len(good))
        detect_fails = 0
        for (i, _f), dets in zip(good, results):
            out[i] = dets
        return out

    def _rpc_failure(exc):
        """Did the accelerator SERVICE drop us, rather than the chip fail?"""
        text = str(exc)
        return "RPC_FAILED" in text or "(77)" in text or "error: 77" in text

    #: RPC failures seen recently -- (when,) per failure. Three inside
    #: RPC_RESTART_WINDOW_S restart the app; one is treated as a transient.
    rpc_hits = []
    RPC_RESTART_AFTER, RPC_RESTART_WINDOW_S = 3, 10.0

    def _restart_self(why, fault=True):
        """Re-execute this app in place: same pid, same argv, same cwd.

        The only recovery from a dropped accelerator-service client. Tried
        in-process: not releasing the dead handle, letting the ladder rebuild
        -- the process still died natively inside the runtime's own thread.
        Everything that must not be lost is flushed first; the state file
        says what happened so the page does not show a blank restart.

        `fault=False` is the planned kind (release_by_exec): said as an
        ordinary line, and no error left on the state file.
        """
        # CRITICAL, and written before the exec: this process is about to
        # be replaced, and the record of WHY has to be on disk before that
        # happens or the restart looks spontaneous to everyone reading the
        # page afterwards.
        if fault:
            log.critical("%s - restarting the app in place", why,
                         extra=faults.event(faults.AI_SERVICE_CRASHED))
            try:
                runtime.write(data_dir, error=f"{why}; restarting")
            except Exception:                               # noqa: BLE001
                pass
        else:
            log.info("%s - restarting the app in place", why)
        for p_ in pipes:
            try:
                if p_.fn_engine is not None:
                    p_.fn_engine.flush_daily(force=True)
                p_.close()
            except Exception:                               # noqa: BLE001
                pass
        try:
            server.shutdown()
        except Exception:                                   # noqa: BLE001
            pass
        sys.stdout.flush(); sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, "-m", "app.run"] + sys.argv[1:])

    def _infer_failed(exc):
        """The failure ladder, shared by the synchronous path and the batch
        in flight: count it, say it, and after enough of them fall back to a
        detector that returns nothing while a rebuild is scheduled.

        An RPC failure is different in kind: the accelerator SERVICE has
        dropped this client (a sibling system stopped or reloaded), and the
        runtime has no way back from that in this process -- see
        _restart_self. Repeated ones restart the app instead of climbing the
        ladder."""
        nonlocal det, detect_fails, detector_retry_at, detector_error
        detect_fails += 1
        infer_error[0] = str(exc)
        if _rpc_failure(exc):
            now_ = time.time()
            rpc_hits.append(now_)
            del rpc_hits[:-RPC_RESTART_AFTER]
            if (len(rpc_hits) >= RPC_RESTART_AFTER
                    and now_ - rpc_hits[0] <= RPC_RESTART_WINDOW_S):
                _restart_self("the accelerator service dropped this client "
                              f"({RPC_RESTART_AFTER} RPC failures in "
                              f"{now_ - rpc_hits[0]:.0f}s)")
        if detect_fails == 1 or detect_fails % 30 == 0:
            log.error("detect() failed (%dx): %s", detect_fails, exc,
                      exc_info=True,
                      extra=faults.event(
                          faults.detector_event(
                              exc, faults.AI_SERVICE_UNAVAILABLE),
                          failures=detect_fails))
            runtime.write(data_dir, error=f"inference failed: {exc}")
        if detect_fails >= 10 and det.name not in ("mock", "none"):
            # A client the SERVICE has already dropped is not released, it is
            # replaced. HAILO_RPC_FAILED(77) means hailort_service no longer
            # knows this handle -- the usual cause is a sibling system on the
            # same chip stopping or reloading -- and releasing it anyway faults
            # inside the runtime ("VDevice_release failed", then the process
            # dies with no traceback). Seen three times on one box in a day.
            # Dropping the reference leaks nothing the service still holds;
            # the retry ladder builds a fresh client, which recovers.
            if _rpc_failure(exc):
                log.warning("the accelerator service dropped this client; "
                            "not releasing the dead handle, a fresh one will "
                            "be built",
                            extra=faults.event(faults.AI_SERVICE_CRASHED))
            else:
                try:
                    det.close()
                except Exception:                   # noqa: BLE001
                    pass
            # NEVER swap a real detector for mock here: mock invents
            # detections, and on a live system that is hallucination.
            det = detector_mod.fallback(det_cfg)
            # ...and the reason stays with it, so the "AI running" dot says
            # WHY it is red rather than "no model" until the retry lands.
            detector_error = (f"fell back to {det.name} after inference "
                              f"failures: {exc} (will retry)")
            # Schedule a rebuild instead of giving up for good. The usual
            # cause is chip contention -- another system on the same Hailo
            # released its VDevice mid-inference -- which passes on its own.
            detector_retry_at = _retry_after(detector_retry_delay)
            log.critical("too many inference failures - falling back to "
                         "%s, retrying the real detector in ~%.0fs",
                         det.name, detector_retry_delay,
                         extra=faults.event(faults.AI_PIPELINE_STALLED,
                                            failures=detect_fails))
            runtime.write(data_dir, detector=det.name,
                          error=f"fell back to {det.name} after inference "
                                f"failures: {exc} (will retry)")

    # ---- one batch in flight, on its own thread -----------------------------
    # Profiled with batching alone: the main thread spent 56% of its time
    # waiting for fresh frames and 36% waiting on the chip, in series, and the
    # rate did not move. Overlapping the two is what turns a cheaper batch
    # into a faster loop. `inflight` holds the batch on the chip; `ready` a
    # batch that was drained early (a detector swap) and not yet analysed.
    inflight = {"thread": None, "frames": None, "box": None, "ready": None}
    # How long the chip took to detect the last SNAPSHOT frames (seconds): the
    # Inject card shows it beside its button. Only set by a camera on Snapshot.
    snap_stat = {"s": None}

    def _infer_submit(frames):
        """Hand this pass's frames to the chip and return at once."""
        det_ref = det                    # the detector THIS batch runs on
        box = {"out": None, "exc": None}

        def work():
            real = [f for _p, f in frames if f is not None and hasattr(f, "shape")]
            t0 = time.monotonic()
            try:
                # Nothing to detect (every camera between snapshots): the chip
                # is not asked at all, and the balancer is told nothing. An
                # empty call is not a safe thing to hand a detector, and one
                # that raised would count towards the failure ladder that
                # demotes it.
                if real:
                    box["out"] = det_ref.detect_many(real)
                    chip_bal.note_infer(time.monotonic() - t0, len(real))
                    if any(p.snap is not None for p, f in frames if f is not None):
                        snap_stat["s"] = round(time.monotonic() - t0, 3)
                else:
                    box["out"] = []
            except Exception as exc:                    # noqa: BLE001
                box["exc"] = exc
        t = threading.Thread(target=work, name="infer", daemon=True)
        t.start()
        inflight.update(thread=t, frames=frames, box=box)

    def _infer_collect():
        """The previous batch's (frames, detections), or None when nothing is
        in flight. Applies the ladder to a failed batch, on THIS thread."""
        nonlocal detect_fails
        if inflight["ready"] is not None:
            done, inflight["ready"] = inflight["ready"], None
            return done
        t = inflight["thread"]
        if t is None:
            return None
        t.join()
        frames, box = inflight["frames"], inflight["box"]
        inflight.update(thread=None, frames=None, box=None)
        raw = [f for _p, f in frames]
        out = [[] for _ in raw]
        good = [i for i, f in enumerate(raw) if f is not None and hasattr(f, "shape")]
        if box["exc"] is None and box["out"] is not None:
            detect_fails = 0
            for i, dets in zip(good, box["out"]):
                out[i] = dets
        else:
            _infer_failed(box["exc"] or RuntimeError("the batch returned nothing"))
        return frames, out

    def _infer_drain():
        """Finish the batch in flight BEFORE the detector is closed or
        rebuilt, keeping its answer for the next collect. A VDevice released
        under a running batch is a crash, not an error."""
        if inflight["thread"] is not None and inflight["ready"] is None:
            inflight["ready"] = _infer_collect()

    # The previous frame's system-wide numbers, fed to every camera's rule
    # engine so formulas can read across cameras (see _analyse).
    last_merged = {}

    def _analyse(pipe, frame, detections, tracks=None):
        """Zones + functions for ONE camera, against ITS engines. Returns
        (result, counts, triggers)."""
        if frame is not None and hasattr(frame, "shape"):
            pipe.frame_h, pipe.frame_w = frame.shape[:2]
        # The frame is the authority on its own size. Rebuild this camera's
        # overlays whenever it differs from what they were drawn for.
        if ((pipe.frame_w, pipe.frame_h) != pipe.overlay_dims
                and pipe.frame_w and pipe.frame_h):
            pipe.overlay_dims = (pipe.frame_w, pipe.frame_h)
            pipe.overlays = [z.as_dict(pipe.frame_w, pipe.frame_h)
                             for z in pipe.zone_list]
        # Identity first: stamp track_id on each detection so the zone engine's
        # crossings and the function engine's timers agree on which object is
        # which. Per camera -- a shared tracker would match an object on one
        # camera to an object on another. Callers that already ran it hand
        # the result in; run it here only when nobody has yet.
        if tracks is None:
            tracks = pipe.tracker.update(detections, pipe.frame_w, pipe.frame_h)
        # Depth and size, AFTER identity and BEFORE the zones: the measurer
        # throttles by track id, and a zone with a near/far range reads
        # det["depth_mm"] to decide who is inside. On a camera without depth
        # the grid is None and this is a dict lookup.
        measure_stats = {}
        depth_grid, depth_scale = pipe.cam.depth_frame()
        if depth_grid is not None:
            measure_stats = pipe.measurer.stamp(
                depth_grid, depth_scale, pipe.cam.intrinsics(), detections,
                pipe.frame_w, pipe.frame_h, time.time(), frame=frame)
        # Keep this frame's tracked objects reachable by the CLOCK-paced
        # outputs. A Data Table on "every N seconds" or "on change" has no
        # event to read an identity off, so without this its track_id column
        # is unfillable by construction -- it would log a count with nobody
        # attached to it (outputs.py _dataset_tick).
        pipe.last_dets = detections
        result = pipe.engine.update(detections, pipe.frame_w, pipe.frame_h)
        counts = pipe.engine.stats(result)
        counts.update(measure_stats)
        # Formulas and totals see the whole SYSTEM's numbers (last frame's
        # merged set), not just this camera's. Without this, a rule that
        # referenced another camera's value -- or any cam_-prefixed key the
        # variable picker offers -- read zero for ever inside the engine while
        # the dashboards happily showed the number it was supposed to read.
        # One frame stale is the honest price of no cross-camera ordering.
        triggers = pipe.fn_engine.update(result, detections, tracks,
                                         {**last_merged, **counts},
                                         pipe.frame_w, pipe.frame_h,
                                         zones=pipe.engine.zones)
        counts.update(pipe.fn_engine.stats())
        for gone in pipe.fn_engine.departed:
            _snap_finish(gone["fn"], gone["track"], gone["total_s"])
        # Whatever an alerting rule is about is drawn in that rule's colour.
        # Stamped on the DETECTION, so the one annotate() call serves the live
        # stream, the snapshots and the recorded clips alike -- three places
        # that must never disagree about which box tripped the rule. After the
        # function stats, because a rule with no zone is decided by its own
        # metric and that metric does not exist until now.
        if pipe.box_colors:
            _stamp_colors(pipe.box_colors, detections,
                          {**last_merged, **counts}, pipe.overlays)
        # Rate-limited inside: a no-op on the frames where nothing changed.
        pipe.fn_engine.flush_daily(now=None)
        pipe.last_frame = frame
        return result, counts, triggers

    def _publish(pipe, frame):
        """Feed one annotated frame to this camera's WebRTC publish, building
        or rebuilding that publish as needed. No-op on an mjpeg system.

        Everything here is best-effort by design: the publish is a SECOND
        way to watch a camera, and the MJPEG feed it falls back to is built
        from the very same frame a line later. A gateway that is down, a
        pipeline that will not start, a camera that changed size mid-run --
        each costs the WebRTC picture and nothing else.
        """
        if stream_mode != "webrtc" or publisher_mod is None:
            return
        pub = pipe.publisher
        # A publisher negotiates its size once, so a camera that reconnects
        # into a different mode needs a fresh one rather than a rejected
        # frame per tick.
        try:
            h, w = frame.shape[:2]
        except Exception:                                   # noqa: BLE001
            return
        snap_mode = pipe.snap is not None
        if pub is not None and pub.ok and getattr(pub, "snapshot", False) != snap_mode:
            # The camera went into (or out of) Snapshot mode: the publish is
            # rebuilt with the matching key-frame spacing, at once.
            pub.close()
            pub = pipe.publisher = None
            pipe.publish_retry = 0.0
        if pub is not None and (not pub.ok or pub.width != w or pub.height != h):
            if pub.error:
                log.warning("[%s] webrtc publish stopped: %s", pipe.id,
                            pub.error)
            pub.close()
            pub = pipe.publisher = None
            pipe.publish_retry = time.time() + 5.0
        if pub is None:
            if time.time() < getattr(pipe, "publish_retry", 0.0):
                return
            pipe.publish_retry = time.time() + 5.0
            if not publisher_mod.ensure_gateway():
                return                  # gateway down: MJPEG carries the page
            if snap_mode:
                # Snapshot camera: about one held frame a second, EVERY frame a
                # key-frame so a viewer who joins late sees the picture within
                # a second, at the quality of a 30 fps stream of this size.
                fresh = publisher_mod.Publisher(
                    stream_url(pipe.id), w, h, 2, name=pipe.id, gop=1,
                    bitrate=publisher_mod.bitrate_for(w, h, 30))
            else:
                fresh = publisher_mod.Publisher(
                    stream_url(pipe.id), w, h,
                    max(1, int(getattr(pipe.cam, "measured_fps", 0) or
                               pipe.base_fps or 15)),
                    name=pipe.id)
            if not fresh.ok:
                log.warning("[%s] webrtc publish unavailable: %s", pipe.id,
                            fresh.error)
                fresh.close()
                return
            if pipe.passthrough:
                # Switched to passthrough while this one was connecting: the
                # gateway's pull owns the name now.
                fresh.close()
                return
            pub = pipe.publisher = fresh
            log.info("[%s] publishing webrtc %sx%s", pipe.id, w, h)
        pub.push(frame)

    def _publish_loop(pipe):
        """Push this camera's picture to its WebRTC publish, at the rate
        THE CAMERA delivers.

        The MJPEG path has painted for the viewer since a6f558d: last
        known boxes on the newest grabbed frame, off the detection loop
        entirely. The WebRTC path was still pushed from inside the loop,
        one frame per inference -- so a camera delivering 56 fps was
        watched at 17, in the bursts a batch arrives in, and the video
        looked broken while the number beside it said 56. Detection is
        slow because a chip is shared; VIDEO has no reason to be.

        Same contract as _annotated_jpeg, on the same overlay: the boxes
        are up to one inference old, the picture is current.

        [video] on_demand: while nobody watches (pipe.view_wanted, kept by
        _watch_viewers) the thread sleeps on view_wake and sends one PLAIN
        frame per view_gate.KEEPALIVE_S -- no paint, no 4 ms spin -- which
        keeps the gateway path ready for the next viewer. Waking asks the
        encoder for a key-frame first, so that viewer's picture starts at
        once instead of at the end of a GOP of keepalives.
        """
        idle = False
        while not _stop.is_set():
            try:
                if pipe.passthrough:
                    # The gateway pulls this camera itself: no paint, no
                    # copy, no encode here. Checked a few times a second
                    # so a switch back to legacy resumes promptly.
                    time.sleep(0.25)
                    continue
                if pipe.snap is not None:
                    # SNAPSHOT MODE: the live view is the last snapped picture,
                    # held. One frame a second keeps the gateway path alive and
                    # gives a late viewer a key-frame to join, at a fraction of
                    # the encode a moving picture costs. Before the first snap
                    # it is the "Waiting for snapshot" picture.
                    fresh = pipe.snap_event.wait(1.0)
                    pipe.snap_event.clear()
                    held = _shot_frame(pipe)
                    if held is None:
                        cam_frame, _seq = pipe.cam.latest()
                        if cam_frame is not None:
                            held = _waiting_frame(cam_frame)
                    if held is not None:
                        _publish(pipe, held)
                        if fresh:
                            # A NEW snap: send it at once, and twice more over
                            # the next third of a second, so the viewer's first
                            # frame is a key-frame it can start from rather than
                            # the next of the once-a-second ones.
                            for _n in range(2):
                                time.sleep(0.15)
                                _publish(pipe, held)
                    continue
                if video_save["on_demand"] and not pipe.view_wanted:
                    idle = True
                    pipe.view_wake.wait(view_gate_mod.KEEPALIVE_S)
                    pipe.view_wake.clear()
                    if pipe.view_wanted or not video_save["on_demand"]:
                        continue            # a viewer arrived: full rate now
                    frame, seq = pipe.cam.latest()
                    if frame is not None and seq != pipe.publish_seq:
                        pipe.publish_seq = seq
                        # Through _publish, so a publish that died while
                        # paused is rebuilt here too and the path stays up.
                        _publish(pipe, frame)
                    continue
                if idle:
                    idle = False
                    # Best-effort: an encoder that ignores it still sends a
                    # key-frame within its one-second GOP at full rate.
                    if pipe.publisher is not None:
                        pipe.publisher.force_keyframe()
                frame, seq = pipe.cam.latest()
                if (frame is not None and seq == pipe.publish_seq
                        and getattr(pipe.cam, "held_still", False)):
                    # A held still never grabs again, so nothing here is ever
                    # "new": its one frame went out once, at the start, and a
                    # viewer who opened the page later joined a stream that had
                    # stopped -- "Connecting..." for ever. Resend it a few times
                    # a second to keep the publish alive and give a late viewer
                    # a key-frame.
                    now_s = time.time()
                    if now_s - pipe.still_push_at >= 0.2:
                        pipe.still_push_at = now_s
                        _publish(pipe, _paint(pipe.overlay, frame)
                                 if pipe.overlay else frame)
                    time.sleep(0.05)
                    continue
                if frame is None or seq == pipe.publish_seq:
                    # Nothing new to send. Sleep a fraction of a frame
                    # rather than a frame, so the next grab is not held.
                    time.sleep(0.004)
                    continue
                pipe.publish_seq = seq
                # No overlay yet (nothing inferred since start): send the
                # plain picture rather than nothing at all. Painted once per
                # grab, shared with the MJPEG viewers (_painted).
                _publish(pipe, _painted(pipe, frame, seq))
                # Paced at the camera, and no faster than a screen can
                # show: past WEBRTC_MAX_FPS the extra frames cost a paint
                # and an encode each and nobody can see them.
                rate = max(float(getattr(pipe.cam, "measured_fps", 0) or 0),
                           float(getattr(pipe.cam, "fps", 0) or 0), 1.0)
                time.sleep(1.0 / min(rate, WEBRTC_MAX_FPS))
            except Exception as exc:                    # noqa: BLE001
                # A publish is the SECOND way to watch a camera and the
                # MJPEG feed is the first: this thread failing must cost
                # the WebRTC picture and nothing else.
                log.error("[%s] webrtc publish thread: %s", pipe.id, exc,
                          exc_info=True)
                time.sleep(1.0)

    def _draw(pipe, frame, detections, model_label):
        """Annotate one camera's frame and publish it to that camera's feed."""
        if frame is None:
            return
        cam_fps = getattr(pipe.cam, "measured_fps", 0.0) or 0.0
        # The status box is stamped INTO the frame, so every viewer sees the
        # same one -- and an operator who does not want a row on a wall display
        # has to be able to turn it off here rather than in a page. Absent key
        # = shown, so a system whose config predates the switches looks exactly
        # as it did. All four off draws no box at all (draw_hud returns the
        # frame untouched on an empty list), which is the point.
        hud = [row for row in
               (("model", str(model_label), "model"),
                ("camera", pipe.id, "camera"),
                ("hailo fps", f"{pipe.fps_ema:.1f}", "hailo_fps"),
                ("camera fps",
                 f"{cam_fps:.1f}" if cam_fps > 0 else f"{pipe.base_fps:.0f} (set)",
                 "camera_fps"))
               if hud_cfg.get(row[2], True)]
        hud = [(label, value) for label, value, _key in hud]
        # Zone outlines come OFF the picture while the operator is dragging one
        # in the editor: the shape they are moving is drawn by the browser on
        # top, and the app's saved copy underneath it reads as a second zone
        # nobody asked for. Everything else -- boxes, links, bubbles, trails --
        # keeps drawing, because the feed is still a live feed while a zone is
        # being placed. The legacy rectangle goes with them: annotate() draws
        # it as the fallback when there are no overlays, which would have put
        # a box back on the picture we just cleared.
        hide_zones = _hide_zone_overlay(pipe.id, time.time())
        # Everything it takes to draw this camera's picture, kept as ONE
        # snapshot. The loop draws it here for the recorder and for a
        # snapshot; a VIEWER draws it again on the newest camera frame, at
        # the viewer's own rate (see _annotated_jpeg). Both read the same
        # overlay, so the two pictures can never disagree about what was
        # detected -- only about how fresh the image under the boxes is.
        pipe.overlay = {
            "detections": detections,
            "zone": None if hide_zones else zone,
            "overlays": [] if hide_zones else pipe.overlays,
            "links": pipe.fn_engine.links(),
            "bubbles": pipe.fn_engine.bubbles(),
            "trails": pipe.fn_engine.trails(),
            "hud": hud,
        }
        # Client overlay: the same snapshot, as data, to the browsers that
        # watch this camera. A lock and a count when nobody does.
        if pipe.overlay_client and overlay_feed.enabled:
            overlay_feed.offer(pipe.id, pipe.overlay, pipe.frame_w,
                               pipe.frame_h, first=pipe is primary)
        # Painted in BOTH modes: pipe.shot is the snapshot's picture and the
        # clip below paints its own, and saved evidence keeps its boxes
        # burned in whoever draws the live view.
        # The WebRTC publish is NOT made here. It runs on its own thread at
        # the camera's rate (see _publish_loop), the same way the MJPEG
        # feed is painted per viewer -- both read this overlay, so the two
        # pictures still cannot disagree about what was detected, only
        # about how fresh the image under the boxes is.
        # The annotated frame is KEPT whatever happens next: a snapshot fires
        # on an alert, at a moment nobody was watching, and it must be that
        # moment's picture rather than the last one somebody streamed.
        # [video] on_demand keeps the frame and overlay instead and paints
        # only when a snapshot actually reads them (_keep_shot).
        on_demand = video_save["on_demand"]
        if pipe.snap is not None and pipe.snap.idle_now and pipe.shot is not None:
            # Between snaps this camera was not looked at: the picture kept
            # is the snapped one, and repainting the same frame twenty times
            # a second is exactly the CPU Snapshot mode is meant to spare.
            stamped = pipe.shot
        else:
            stamped = _keep_shot(pipe, frame, lazy=on_demand)
        # A Snapshot stream detected THIS frame (not a held one): keep it.
        if pipe.snap is not None and not pipe.snap.idle_now:
            _camsnap(pipe)
        # ...and encoded only as a FALLBACK, on a slow heartbeat. A viewer
        # does not read this any more -- it renders the overlay onto the
        # newest camera frame on its own thread, which is what keeps the video
        # smooth while inference runs at its own rate. This cache is what a
        # probe, a fleet agent thumbnail or the first instant of a new stream
        # gets before the first render lands. Not made at all while
        # on_demand has this camera paused: nobody is there to fall back.
        now_j = time.time()
        if now_j - pipe.jpeg_at >= IDLE_JPEG_S and (not on_demand
                                                     or pipe.view_wanted):
            if stamped is None and not pipe.overlay_client:
                stamped = _paint(pipe.overlay, frame)
            # A VIEWER's fallback, so plain when the browser draws the boxes.
            encoded = camera_mod.encode_jpeg(frame if pipe.overlay_client
                                             else stamped)
            if encoded:
                pipe.jpeg = encoded
                pipe.jpeg_at = now_j
        # Recording window open? Append this frame to the clip -- boxes and
        # zones, no HUD: the status box is chrome for somebody watching live,
        # and a saved clip is evidence. Painted only while a clip is actually
        # open (capture.offer closes the writer itself, so it is still called
        # every tick).
        # ...and painted while a RUN-UP is being kept, which is the cost of
        # asking for one: a Record Clip with "include before" set means this
        # render happens every frame, recording or not, because the seconds
        # before a trigger cannot be produced after it.
        # ...and only as often as the CLIP wants one. A clip is written at
        # 12fps; a loop running at 25 was painting every one of those frames
        # and handing half of them to a writer that threw them away. The
        # render is the expensive half, so the question is asked before it.
        if pipe.capture.wants_frames():
            if pipe.capture.due(now_j):
                pipe.capture.offer(_paint(pipe.overlay, frame, hud=False), now_j)
            else:
                # Still offered: `offer` is what closes a finished clip, and a
                # tick that skips it entirely would leave the writer open
                # until the next frame happened to be due.
                pipe.capture.offer(None, now_j)
        else:
            pipe.capture.offer(None)

    _apply_preroll(outputs)

    # One publish thread per camera, started once. A camera added later
    # gets one on the same path (see the camera-reload branch), and a
    # camera removed simply stops having frames for the thread to send.
    _publishing = set()

    def _start_publishers():
        """A publish thread for every camera that has none yet."""
        if stream_mode != "webrtc" or publisher_mod is None:
            return
        for p in pipes:
            if p.id in _publishing:
                continue
            _publishing.add(p.id)
            threading.Thread(target=_publish_loop, args=(p,),
                             name=f"publish-{p.id}", daemon=True).start()

    # [video] on_demand: ONE watch for every camera of this app, started the
    # first time the switch is on (at start or on a reload) and never while
    # it has not been -- a system that never asked makes no gateway request.
    view_gate = view_gate_mod.Gate()
    _watching = []

    def _view_exempt(p):
        """Cameras whose view is never paused: passthrough (no publish here
        to pause), and depth or measurement cameras (their pictures are what
        calibration and measuring are done on)."""
        return bool(p.passthrough
                    or getattr(p.cam, "_orbbec", None) is not None
                    or ((p.cfg.get("measure") or {}).get("profiles")
                        if isinstance(p.cfg.get("measure"), dict) else False))

    def _set_wanted(p, wanted):
        if p.view_wanted == wanted:
            return
        p.view_wanted = wanted
        if wanted:
            p.view_wake.set()
            log.info("[%s] video on demand: viewer arrived - full rate", p.id)
        else:
            log.info("[%s] video on demand: nobody watching - paused", p.id)

    def _watch_viewers():
        """Keep every camera's view_wanted in step with who watches it: the
        gateway's readers of its path (WebRTC) and this app's own MJPEG
        viewers. See app/view_gate.py for why the gateway and why unknown
        means watched."""
        while not _stop.is_set():
            try:
                if not video_save["on_demand"]:
                    # Switched off: everything back to full rate, and no
                    # gateway request until it is switched on again.
                    view_gate.reset()
                    for p in list(pipes):
                        _set_wanted(p, True)
                else:
                    counts = (view_gate_mod.readers()
                              if stream_mode == "webrtc" else {})
                    rows = []
                    for p in list(pipes):
                        if stream_mode == "webrtc":
                            path = stream_url(p.id).rsplit("/", 1)[-1]
                            n = None if counts is None else counts.get(path)
                        else:
                            n = 0           # nothing published: no readers
                        rows.append((p.id, n, server.watchers(p.id),
                                     _view_exempt(p)))
                    view_gate.update(rows)
                    for p in list(pipes):
                        _set_wanted(p, view_gate.wanted(p.id))
            except Exception as exc:                    # noqa: BLE001
                # Never strand a viewer on a broken watch: every camera goes
                # back to full rate, and the watch tries again.
                log.error("video on demand watch: %s", exc, exc_info=True)
                view_gate.reset()
                for p in list(pipes):
                    _set_wanted(p, True)
            _stop.wait(view_gate_mod.PRESENCE_S)

    def _start_watch():
        if video_save["on_demand"] and not _watching:
            _watching.append(True)
            threading.Thread(target=_watch_viewers, name="view-watch",
                             daemon=True).start()

    # Views BEFORE publishers: a passthrough camera's publish thread must
    # find the flag already set, never start an encode it then has to stop.
    _sync_views(cfg, sweep=True)
    _start_publishers()
    _start_watch()

    try:
        while not _stop.is_set():
            # Apply any config change the dashboard wrote (model, camera or
            # zones). Each part is rebuilt independently and a failure keeps the
            # old one, so a bad edit never takes detection down -- and the stream
            # keeps running throughout, so a change never drops the video.
            now = time.time()
            if now >= next_cfg_check:
                next_cfg_check = now + _CONFIG_POLL_S
                latest = _config_mtime()
                if ((latest and latest != cfg_mtime)
                        or restart_phase in ("down", "up", "stop")):
                    cfg_mtime = latest or cfg_mtime
                    raw_cfg = config_mod.load()
                    # The user's own switch, before the watchdog's restart
                    # (if one is in flight) bends it.
                    user_ai = ai_on(raw_cfg)
                    cam_srcs = cam_sources(raw_cfg)
                    cam_words = cam_names(raw_cfg)
                    restart_half = None
                    gone = []
                    if (restart_phase in ("stop", "paused") and user_ai
                            and not _stop.is_set()):
                        # Stopped for a missing camera: the AI stays forced
                        # off through any edit until the camera is back.
                        raw_cfg = ai_forced_off(raw_cfg)
                        if restart_phase == "stop":
                            restart_half = "down"
                        restart_phase = "paused"
                    elif (restart_phase == "down" and user_ai
                            and not _stop.is_set()):
                        # First half: the config with the AI forced off.
                        raw_cfg = ai_forced_off(raw_cfg)
                        restart_half, restart_phase = "down", "up"
                        next_cfg_check = now            # second half next pass
                    elif restart_phase == "up" and user_ai:
                        restart_half, restart_phase = "up", None
                    elif (restart_phase is None and user_ai and not ai_active
                            and not args.once and not _stop.is_set()
                            and (watch.hold or {}).get("state") == "stopped"):
                        # The user's Start after they stopped a wait for a
                        # missing camera. Still not one camera on the box:
                        # it waits again at once instead of spending the
                        # chip on a camera that cannot open. Any other Start
                        # opens as it always did; the watchdog stops it
                        # after one restart if the camera is not there.
                        gone = camwatch_mod.all_missing(cam_srcs)
                    else:
                        # Nothing in flight -- or the user's Stop overtook
                        # it, and the config as written is the answer.
                        restart_phase = None
                    if gone:
                        raw_cfg = ai_forced_off(raw_cfg)
                        restart_phase = "paused"
                        watch.pause(now, gone, by="start")
                        log.warning("AI detection switched on, but camera "
                                    "%s is not found - waiting for it "
                                    "(camera and accelerator not opened); it "
                                    "starts by itself when the camera is "
                                    "plugged in",
                                    ", ".join(cam_words.get(c, c) for c in gone),
                                    extra=faults.event(faults.CAM_DISCONNECTED,
                                                       camera=gone[0]))
                    new_cfg = point_gates(class_pause(ai_config(raw_cfg)))
                    held_ids = detection_fed(raw_cfg)
                    watch.configure((new_cfg.get("app") or {}).get(
                        "camera_lost_restart_s", camwatch_mod.LOST_AFTER_S))
                    new_det_cfg = new_cfg.get("detector") or {}
                    new_cam_cfg = dict(new_cfg.get("camera") or {})
                    new_cam_cfg["_stream_port"] = stream_port   # keep compare stable
                    outputs = new_cfg.get("outputs") or []   # picked up live
                    buses_mod.set_clients(new_cfg.get("modbus_clients") or [])
                    mqtt_bus.set_clients(new_cfg.get("mqtt_clients") or [])
                    mqtt_bus.prestart(o.get("conn") for o in outputs
                                      if isinstance(o, dict))
                    # [modbus_server] too: the listener below reads it from
                    # `cfg` on every tick, and `cfg` is the startup copy -- so
                    # a server switched on in the page waited for the next
                    # app restart instead of the "few seconds" it promises.
                    cfg["modbus_server"] = new_cfg.get("modbus_server") or {}
                    # ...including how much run-up the clips want kept, so
                    # switching "include before" off frees the buffer now.
                    _apply_preroll(outputs)
                    # [capture] switches: updated IN PLACE so the hooks'
                    # closure sees the change without re-wiring.
                    capture_cfg.clear()
                    capture_cfg.update(new_cfg.get("capture") or {})
                    # [hud] switches, same treatment: mutated in place so the
                    # draw closure above sees the change without re-wiring.
                    hud_cfg.clear()
                    hud_cfg.update(new_cfg.get("hud") or {})
                    # [video] overlay, live too. Back to "server" closes the
                    # browsers' overlay streams; their pages then see 404.
                    video_cfg = overlay_mod.settings(new_cfg)
                    overlay_feed.hz = video_cfg["hz"]
                    overlay_feed.enabled = video_cfg["mode"] == "client"
                    if not overlay_feed.enabled:
                        overlay_feed.close_all()
                    for p in pipes:
                        p.set_overlay_client(overlay_feed.enabled)
                    # [video] on_demand, live: on starts the one watch (if
                    # it never ran), off wakes every camera within a second.
                    video_save.update(view_gate_mod.settings(new_cfg))
                    _start_watch()
                    # Event debouncing knobs, picked up live like [capture].
                    new_app_cfg = new_cfg.get("app") or {}
                    heartbeat_s = float(new_app_cfg.get("event_heartbeat_s", 60) or 0)
                    clear_hold_s = float(new_app_cfg.get("event_clear_hold_s", 2.0) or 0)
                    new_storage = new_cfg.get("storage") or {}
                    store.keep_snapshots = snapshot_cap(new_storage.get("keep_snapshots", 0))
                    store.max_event_bytes = int(float(new_storage.get("max_events_mb")
                                                      or 64) * 1024 * 1024)
                    store.live_days = float(new_storage.get("events_live_days", 7) or 0)
                    max_log_bytes = int(float(new_storage.get("max_log_mb")
                                              or 16) * 1024 * 1024)

                    if gst_mode:
                        # The pipeline owns camera + model, so a change to either
                        # rebuilds the whole thing. A switch AWAY from gstreamer
                        # tears the pipeline down and hands the camera back to
                        # the ordinary capture path -- a model selected on the
                        # dashboard must take effect without an SSH restart.
                        if new_det_cfg.get("kind") != "gstreamer":
                            log.info("detector kind changed off gstreamer "
                                     "- rebuilding")
                            if gst is not None:
                                gst.close()
                                gst = None
                            gst_mode = False
                            try:
                                det = _build_det(new_det_cfg)
                                detector_error = None
                            except Exception as exc:            # noqa: BLE001
                                detector_error = f"detector reload failed: {exc}"
                                log.error("%s", detector_error,
                                          extra=faults.event(
                                              faults.MDL_LOAD_FAILED))
                                det = detector_mod.fallback(new_det_cfg)
                            det_cfg, cam_cfg = new_det_cfg, new_cam_cfg
                            # The pipeline owned the camera. The per-camera
                            # pipes take it back -- through the camera block
                            # below, which now runs for this reload too: every
                            # pipe is parked and the list forgotten, so it
                            # reopens exactly the cameras the config asks for
                            # and NONE when the AI was switched off. Opening
                            # the legacy camera here held a device with the
                            # AI off (and nothing ever read it).
                            cam.close()
                            cam = camera_mod.Camera(cam_cfg)
                            for p_ in pipes:
                                p_.close()
                                p_.parked = True
                            cam_list = []
                            runtime.write(data_dir, detector=det.name,
                                          error=detector_error)
                        elif new_det_cfg != det_cfg or new_cam_cfg != cam_cfg:
                            log.info("rebuilding gstreamer pipeline")
                            if gst is not None:
                                gst.close()
                            det, gst, err = _build_gst(new_det_cfg, new_cam_cfg)
                            det_cfg, cam_cfg = new_det_cfg, new_cam_cfg
                            gst_mode = gst is not None
                            runtime.write(data_dir, detector=det.name, error=err)
                    elif new_det_cfg.get("kind") == "gstreamer":
                        # Switch INTO gstreamer mode: the pipeline takes over the
                        # camera, so release it first.
                        log.info("detector kind changed to gstreamer - "
                                 "rebuilding")
                        _infer_drain()
                        det.close()
                        cam.close()
                        # ...ALL of it: the per-camera pipes too, or the
                        # pipeline and a pipe both hold one device. Parked
                        # and forgotten, so a pipeline that fails to build
                        # hands the cameras back through the block below.
                        for p_ in pipes:
                            p_.close()
                            p_.parked = True
                        cam_list = []
                        det, gst, err = _build_gst(new_det_cfg, new_cam_cfg)
                        det_cfg, cam_cfg = new_det_cfg, new_cam_cfg
                        gst_mode = gst is not None
                        runtime.write(data_dir, detector=det.name, error=err)
                    else:
                        # -- detector (model / confidence / classes) ----------
                        if (_det_core(new_det_cfg) != _det_core(det_cfg)
                                and release_by_exec(det, restart_half,
                                                    restart_phase, args.once)):
                            # A hailort_service client is never released in
                            # place (see release_by_exec): the process is
                            # replaced, and the fresh one reads the config as
                            # written -- AI off after a Stop, the new model
                            # after a model change.
                            if not ai_on(new_cfg) and ai_active:
                                log.info("AI detection switched off - camera "
                                         "and detector released, the rest of "
                                         "the workflow keeps running")
                            _restart_self("the detector is a client of the "
                                          "accelerator service, which must "
                                          "not be released in place",
                                          fault=False)
                        if _det_core(new_det_cfg) != _det_core(det_cfg):
                            # Release the OLD detector before building the new
                            # one. A Hailo accelerator has ONE owner: two live
                            # VDevices at once raise "VDevice released while CIM
                            # in use". Close first, then acquire; if the new one
                            # fails, fall back to mock rather than leave none.
                            _infer_drain()
                            det.close()
                            try:
                                det = _build_det(new_det_cfg)
                                det_cfg = new_det_cfg
                                detector_error = None
                                log.info("detector reloaded: %s", det.name)
                                runtime.write(data_dir, detector=det.name, error=None)
                            except Exception as exc:            # noqa: BLE001
                                det = detector_mod.fallback(new_det_cfg)
                                log.error("detector reload FAILED: %s; "
                                          "using %s", exc, det.name,
                                          exc_info=True,
                                          extra=faults.event(
                                              faults.detector_event(exc)))
                                det_cfg = new_det_cfg
                                detector_error = f"detector reload failed: {exc}"
                                runtime.write(data_dir, detector=det.name,
                                              error=detector_error)

                    if not gst_mode:
                        # -- cameras (added, removed, re-pointed) -------------
                        new_cam_list = _camera_list(new_cfg)
                        if new_cam_list != cam_list:
                            want = {c["id"]: c for c in new_cam_list}
                            # Drop the ones that went, so their device is
                            # released rather than held by a stopped pipeline.
                            for gone in [p for p in pipes if p.id not in want]:
                                # "Removed" covers both deleted and switched
                                # off -- the config no longer asks for it
                                # either way. The all-off case says so itself
                                # below, so it is not announced twice here.
                                if new_cam_list:
                                    log.info("camera removed: %s", gone.id)
                                gone.close()
                            kept = []
                            for entry in new_cam_list:
                                have = by_id.get(entry["id"])
                                spec = dict(entry)
                                spec["_stream_port"] = stream_port
                                # The RUNTIME-only keys the constructor
                                # stamps: they are not in the config entry,
                                # so a spec swap dropped them. Losing
                                # media_dir made every `file:` camera resolve
                                # to a bare filename the moment any camera
                                # setting was edited -- "cannot open camera
                                # 'file:clip.mov'" on a clip sitting right
                                # there in the system's media folder.
                                spec["media_dir"] = os.path.join(str(data_dir),
                                                                 "media")
                                if have is None:
                                    log.info("camera added: %s",
                                             entry["id"])
                                    try:
                                        made = _Pipe(entry, new_cfg, stream_port,
                                                     data_dir, store,
                                                     new_det_cfg.get("anchor", "bottom_center"))
                                        made.open_async()
                                    except Exception as exc:    # noqa: BLE001
                                        log.error("camera %s failed: %s",
                                                  entry["id"], exc,
                                                  exc_info=True)
                                        continue
                                    kept.append(made)
                                    continue
                                # Only re-open when the DEVICE settings changed;
                                # a rename must not drop the video.
                                same = all(str(have.cfg.get(k)) == str(spec.get(k))
                                           for k in ("source", "width", "height", "fps"))
                                have.cfg = spec
                                have.base_fps = float(entry.get("fps") or 10)
                                # The measurement table rides on the camera
                                # entry, so a calibration or a saved profile
                                # lands here -- without reopening the camera,
                                # which the `same` test below protects.
                                m_cfg = (spec.get("measure")
                                         if isinstance(spec.get("measure"), dict) else {})
                                have.measurer.configure(
                                    m_cfg.get("profiles"), m_cfg.get("floor"),
                                    measure_mod.load_background(config_mod.PKG_DIR,
                                                                m_cfg.get("background")),
                                    measure_mod.load_colour_background(config_mod.PKG_DIR,
                                                                       m_cfg.get("background")))
                                # A PARKED pipe just got switched back on. Its
                                # device is closed, so "the settings are
                                # unchanged, leave the video alone" is exactly
                                # backwards -- reopen it however equal the
                                # settings look, or Active would light up over
                                # a camera that never reconnects.
                                if have.parked:
                                    same = False
                                    have.parked = False
                                if not same:
                                    try:
                                        have.cam.close()
                                        have.cam = camera_mod.Camera(spec)
                                        have.open_async()
                                        have.frame_w = int(entry.get("width") or have.frame_w)
                                        have.frame_h = int(entry.get("height") or have.frame_h)
                                        log.info("camera reloaded: %s",
                                                 have.cam.label)
                                    except Exception as exc:    # noqa: BLE001
                                        log.error("camera reload FAILED: "
                                                  "%s", exc, exc_info=True)
                                        runtime.write(data_dir,
                                                      error=f"camera reload failed: {exc}")
                                kept.append(have)
                            if not new_cam_list:
                                # EVERY camera switched off. Park them rather
                                # than commit an empty list: this app is built
                                # on pipes[0] existing (HUD, stream, runtime
                                # state, the zone engines), and a pipe-less run
                                # would need every one of those to learn a None
                                # case. Parked = closed and left alone: the
                                # device is released, the recovery ladder skips
                                # it, and switching one back on rebuilds it on
                                # the next reload.
                                #
                                # Committing cam_list here is what ends the
                                # loop. The old guard refused an empty result,
                                # so cam_list never caught up, the lists stayed
                                # unequal, and this block re-ran EVERY tick --
                                # closing cameras the recovery ladder had just
                                # reopened, for ever.
                                for p in pipes:
                                    p.close()
                                    p.parked = True
                                cam_list = new_cam_list
                                log.info("every camera switched off - "
                                         "devices released")
                            elif kept:
                                pipes = kept
                                by_id = {p.id: p for p in pipes}
                                # A camera added by this reload has no
                                # publish thread yet; one that went
                                # simply stops having frames to send,
                                # and its thread ends with the app.
                                _start_publishers()
                                primary = pipes[0]
                                cam_list = new_cam_list
                                cam_cfg = dict(pipes[0].cfg)
                                runtime.write(data_dir, camera=primary.cam.label,
                                              error=primary.cam.error)

                    # -- zones ------------------------------------------------
                    # Zones and functions are rebuilt PER CAMERA: each pipe
                    # takes only the ones that name it, so a zone drawn on one
                    # camera never starts counting on another.
                    # Templates are documents, not devices: re-reading them
                    # costs nothing and needs no rebuild.

                    # Tracker knobs: applied in place, never by rebuilding
                    # the tracker. A fresh Tracker would drop every live id,
                    # and dwell/travel/cycle timers keyed on those ids would
                    # restart mid-measurement -- an operator nudging a
                    # threshold must not silently reset the counts.
                    new_tracker_raw = new_cfg.get("tracker") or {}
                    if new_tracker_raw != tracker_raw:
                        # retune, not poke: this block used to assign
                        # attributes from a Tracker API two generations old
                        # (iou_min/radius/ttl_s), so every tracker edit
                        # KeyErrored here and the new settings never landed
                        # until a full detection restart.
                        applied = _tracker_settings(new_cfg)
                        for pipe in pipes:
                            pipe.tracker.retune(**applied)
                        tracker_raw = new_tracker_raw
                        log.info("tracker: %s", applied)

                    new_zones_raw = new_cfg.get("zones")
                    new_functions_raw = new_cfg.get("functions")
                    if new_zones_raw != zones_raw or new_functions_raw != functions_raw:
                        anchor_now = new_det_cfg.get("anchor", "bottom_center")
                        try:
                            for pipe in pipes:
                                pipe.rebuild_rules(new_cfg, anchor_now)
                        except ValueError as exc:
                            log.error("zone reload FAILED, keeping "
                                      "current: %s", exc, exc_info=True,
                                      extra=faults.event(
                                          faults.SYS_CONFIG_INVALID))
                            runtime.write(data_dir, error=f"zone reload failed: {exc}")
                        else:
                            zones_raw = new_zones_raw
                            functions_raw = new_functions_raw
                            fn_engine = primary.fn_engine
                            log.info("rules reloaded: %s",
                                     ", ".join(f"{p.id}={len(p.zone_list)}z/"
                                               f"{len(p.fn_engine.functions)}f"
                                               for p in pipes))

                    # -- the AI detection switch ---------------------------
                    # The detector and the cameras above already followed it
                    # (ai_config rewrote both). What is left is which rules
                    # run: applied AFTER the rules, because a rebuilt engine
                    # and a camera added by switching the AI on start with
                    # nothing inactive.
                    new_ai = ai_on(new_cfg)
                    ai_inactive = list(new_cfg.get("_ai_inactive") or [])
                    if new_ai != ai_active:
                        ai_active = new_ai
                        ai_frames.clear()
                        # Re-pace now rather than at the next balance check:
                        # the AI-off rate must not linger on live cameras.
                        next_balance = 0.0
                        if restart_half is None:
                            # The watchdog's own halves are its one line.
                            log.info("AI detection switched %s", "on" if ai_active
                                     else "off - camera and detector released, "
                                          "the rest of the workflow keeps running")
                            if ai_active:
                                watch.arm(time.time())
                    if not user_ai:
                        # The user's Stop: the watchdog stands down and
                        # nothing is held any more -- it is plain AI off.
                        watch.disarm(time.time())
                        held = set()
                    elif restart_phase == "paused":
                        # Stopped for a missing camera: plain AI off, the
                        # detection-fed rules held by the switch itself.
                        held = set()
                    elif restart_half == "up":
                        # Restarted: the lost clock counts from now, and the
                        # reconnect ladder may try at once on the fresh
                        # cameras (a USB camera re-enumerated elsewhere).
                        watch.arm(time.time())
                        for p in pipes:
                            p.cam_fails = 0
                            p.next_recover = 0.0
                    # ...and the rules behind a Class Filter the
                    # dashboard paused (class_pause), the same way.
                    base_inactive = inactive_ids(new_cfg)
                    base_gated = list(new_cfg.get("_gated") or ())
                    # An output an ON/OFF point lets back in forgets what it
                    # last sent, so its branch's fresh fire goes out even
                    # with the same value as before the gap.
                    now_outs = set(new_cfg.get("_gated_outputs") or ())
                    outputs_mod.forget(gated_outs - now_outs)
                    gated_outs = now_outs
                    held &= {p.id for p in pipes}
                    _apply_inactive()
                    # The ON/OFF points follow both switches from here.
                    functions_mod.set_states(ai_active,
                                             paused_filters(new_cfg))
                    # [video] feed, after the cameras and the overlay mode
                    # above: a camera added, removed, re-pointed or parked,
                    # and a switch either way, all land here.
                    _sync_views(new_cfg)

            per_cam = []                # [(pipe, frame, detections, result, counts, triggers)]
            if gst_mode and gst is not None:
                # The pipeline produced the frame AND the detections together;
                # read the latest of both. No frame yet (pipeline warming up, or
                # an error) -> idle briefly rather than spin. GStreamer owns
                # capture itself, so it drives the FIRST camera only.
                frame, _ = gst.latest()
                if frame is None:
                    if gst.error and gst.error != last_gst_error:
                        last_gst_error = gst.error
                        log.error("gstreamer: %s", gst.error,
                                  extra=faults.event(
                                      faults.AI_SERVICE_UNAVAILABLE))
                        runtime.write(data_dir, error=gst.error)
                    time.sleep(0.05)
                    continue
                last_gst_error = None
                ai_frames[primary.id] = now
                # No _pre() here on purpose: the gstreamer path runs inference
                # INSIDE the pipeline, so anything done to the frame at this
                # point happens after the detector has already seen it, not
                # before. A system on that path gets no software
                # pre-processing rather than a setting that silently does
                # nothing to the detections.
                primary.frame_h, primary.frame_w = frame.shape[:2]
                detections = _gst_detections(gst, primary.frame_w, primary.frame_h)
                result, counts, triggers = _analyse(primary, frame, detections)
                per_cam.append((primary, frame, detections, result, counts, triggers))
            else:
                # Every camera's frame is READ first, then all of them go to
                # the SHARED detector in one call: that is what lets the Hailo
                # detector keep them all in flight at once, and a two-camera
                # system stops paying two full round trips per loop. Reading
                # then inferring camera by camera was the ~7 fps ceiling on a
                # chip that does four times that. Results come back in order,
                # so each camera is analysed against its own frame as before.
                for _p in pipes:
                    # A Snapshot camera sleeps, wakes or reads slowly between
                    # snaps (app/snapcam.py); any other camera is untouched.
                    snapgate_mod.sync(_p)
                    snapcam_mod.tick(_p, now)
                frames = [(pipe, _pre(pipe, _read_frame(pipe, now)))
                          for pipe in pipes]
                # A detector that wants a BATCH (a multi-context Hailo model:
                # see hailo.py, batch 8 measured at 31.7 frames/s against
                # 14.7 one at a time) is fed ceil(batch / cameras) frames per
                # camera per loop. Each extra round waits for the grab
                # thread's sequence number to move, so a batch is never the
                # same picture twice, and the wait is bounded by the camera's
                # own period so a stalled camera cannot stall the loop.
                live = [p for p in pipes if not p.parked and not p.opening] or pipes
                want = max(1, int(getattr(det, "batch", 1) or 1))
                rounds = max(1, -(-want // max(1, len(live))))
                # Snapshot cameras collect no extra batch rounds beyond what
                # they are still owed: waiting a camera period per round for
                # frames that will be dropped put a fixed ~2 s between a press
                # and the snap on a batch-8 model.
                gates = [snapgate_mod.sync(p) for p in live]
                if gates and all(g is not None for g in gates):
                    owed = max(g.left for g in gates)
                    rounds = min(rounds, max(1, owed))
                for _r in range(1, rounds):
                    # Every camera at once, not one after another: a round is
                    # bounded by the SLOWEST camera's period, not their sum.
                    seqs = {p.id: (p.cam.latest()[1] if hasattr(p.cam, "latest") else 0)
                            for p in live}
                    longest = max(1.0 / max(1.0, float(getattr(p.cam, "fps", 10) or 10))
                                  for p in live)
                    deadline = time.time() + longest * 1.5
                    waiting = set(seqs)
                    while waiting and time.time() < deadline:
                        for p in live:
                            if (p.id in waiting and hasattr(p.cam, "latest")
                                    and p.cam.latest()[1] != seqs[p.id]):
                                waiting.discard(p.id)
                        if waiting:
                            time.sleep(0.003)
                    for pipe in live:
                        frames.append((pipe, _pre(pipe,
                                                  _read_frame(pipe, time.time()))))
                for pipe, f in frames:
                    if f is not None or getattr(pipe.cam, "asleep", False):
                        # A camera asleep between snaps is healthy: the
                        # watchdog must not call it lost.
                        ai_frames[pipe.id] = now
                # A camera whose Detection Stream is set to Snapshot gets only
                # the frames it is still owed; the rest are None, which the
                # inference path answers with no detections and no call to the
                # chip. Taken AFTER the line above on purpose: the camera
                # watchdog must still see that the camera is delivering.
                frames, snap_done = snapgate_mod.filter_frames(frames)
                for src in snap_done:
                    # The snap has its frames: the source goes back to false so
                    # it stops and is ready for the next true.
                    if src["kind"] == "inject":
                        functions_mod.reset_inject(src["fn"])
                    elif src["kind"] == "trigger":
                        functions_mod.hold_low(src["key"])
                if want > 1:
                    # Pipelined: the chip has been running the PREVIOUS batch
                    # while this one was collected. Take that answer, hand
                    # this batch over, and analyse the previous frames -- one
                    # batch of latency, and the pass is max(infer, collect +
                    # draw) instead of the sum. Nothing to analyse on the very
                    # first pass; per_cam simply stays empty.
                    ready = _infer_collect()
                    _infer_submit(frames)
                else:
                    ready = (frames, _infer_batch([f for _p, f in frames]))
                if ready is not None:
                    for (pipe, frame), detections in zip(*ready):
                        gate = pipe.snap
                        if gate is not None:
                            # Between snaps this camera was not looked at:
                            # keep its last picture and its last result, so a
                            # zone does not read empty and an "area empty" rule
                            # does not fire because nobody asked for a frame.
                            gate.idle_now = frame is None
                            if frame is None:
                                frame = pipe.last_frame
                                detections = list(pipe.last_dets or [])
                        result, counts, triggers = _analyse(pipe, frame, detections)
                        if gate is not None:
                            # An Anomaly node's GOOD / NG belongs to the SNAP,
                            # not to the last frame the engine was fed.
                            vkeys = [k for k in counts
                                     if k.startswith("fn_") and k.endswith("_verdict")]
                            held = gate.observe(not gate.idle_now,
                                                {k: counts[k] == 1 for k in vkeys})
                            for k in vkeys:
                                counts.pop(k, None)
                            counts.update(held)
                        per_cam.append((pipe, frame, detections, result, counts, triggers))
                    # Every camera between snaps: nothing here waits on the
                    # chip, so without this the loop would spin. The function
                    # engine still ticks (it has to see the next trigger).
                    if pipes and all(p.snap is not None and p.snap.idle_now
                                     for p in pipes):
                        time.sleep(0.02)

            # -- camera watchdog: a camera silent for camera_lost_restart_s
            # restarts detection (app/camwatch.py; the restart itself is the
            # reload above, see restart_phase). Only while the user has the
            # AI on and no restart half is pending -- a parked camera is the
            # user's, and is never waited for. Here, before this pass's
            # numbers are merged, so the pass that decides already holds them.
            #
            # While an episode lasts the cameras are also looked for (a stat,
            # a TCP connect, off the loop): a restart that found NO camera
            # of the system on the box stops detection instead of trying
            # again ("pause"), and a stopped one starts again by itself the
            # moment one is back (the "paused" branch below).
            if ai_active and restart_phase is None and not args.once:
                now_w = time.time()
                watched = ([primary] if gst_mode
                           else [p for p in pipes if not p.parked])
                ep0 = watch.episode
                seen = (presence.poll(now_w, {
                    p.id: cam_srcs.get(p.id, str(p.cfg.get("source") or ""))
                    for p in watched}, since=ep0["at"])
                    if ep0 is not None and ep0["attempts"] else {})
                act = watch.step(now_w, True, [p.id for p in watched],
                                 ai_frames,
                                 opening={p.id for p in watched if p.opening},
                                 present=seen)
                if act and act[0] == "pause":
                    gone_ids = act[1]["cameras"]
                    log.warning("camera %s not found - AI detection stopped "
                                "(camera and accelerator released); it starts "
                                "again by itself when the camera is plugged "
                                "back in", ", ".join(gone_ids),
                                extra=faults.event(faults.CAM_DISCONNECTED,
                                                   camera=gone_ids[0]))
                    # This pass's numbers are absent too; the reload below
                    # (next pass) makes it plain AI off.
                    held = set(gone_ids)
                    restart_phase = "stop"
                    next_cfg_check = 0.0
                    _apply_inactive()
                elif act and act[0] == "restart":
                    _w, scope, lost_ids = act
                    ep = watch.episode
                    held = set(lost_ids)
                    log.warning("camera %s is not giving frames - %s "
                                "(attempt %d, next try in %.0fs if it is "
                                "still missing)", ", ".join(lost_ids),
                                "restarting AI detection" if scope == "full"
                                else "reopening that camera; the others "
                                     "keep detecting",
                                ep["attempts"], ep["next_at"] - now_w,
                                extra=faults.event(faults.CAM_DISCONNECTED,
                                                   camera=lost_ids[0],
                                                   attempt=ep["attempts"]))
                    if scope == "full":
                        restart_phase = "down"
                        next_cfg_check = 0.0
                    else:
                        # The lost camera(s) only: released and opened
                        # afresh off the loop, the same reopen a changed
                        # camera setting gets. Detector untouched.
                        for p in watched:
                            if p.id in held:
                                try:
                                    p.cam.close()
                                    p.cam = camera_mod.Camera(p.cfg)
                                    p.cam_fails = 0
                                    p.next_recover = 0.0
                                    p.open_async()
                                except Exception as exc:    # noqa: BLE001
                                    log.error("camera %s reopen failed: %s",
                                              p.id, exc)
                    _apply_inactive()
                elif act and act[0] == "recovered":
                    log.info("camera %s is back after %d restart(s) - "
                             "detection running", ", ".join(act[1]["cameras"]),
                             act[1]["attempts"])
                    held = set()
                    _apply_inactive()
            elif restart_phase == "paused" and not args.once:
                # Stopped for a missing camera: look for it every few
                # seconds, nothing more -- no chip, no model, no open. A
                # camera that cannot be told (None) counts as back.
                now_w = time.time()
                seen = presence.poll(now_w, cam_srcs,
                                     since=(watch.hold or {}).get("since"))
                if not cam_srcs or (seen and any(v is not False
                                                 for v in seen.values())):
                    back = sorted(c for c, v in seen.items() if v is not False)
                    still = sorted(c for c, v in seen.items() if v is False)
                    watch.resume(now_w, back, still)
                    log.info("camera %s is back - starting AI detection again",
                             ", ".join(back) or "list changed")
                    # A camera still missing keeps its numbers absent.
                    held = set(still)
                    restart_phase = "up"
                    next_cfg_check = 0.0

            # Measured processing rate (EMA), for the HUD and the dashboard.
            now2 = time.time()
            def cams_this_pass():
                """How many distinct CAMERAS this pass touched.

                One place, because two call sites needed it and they must not
                be allowed to disagree: the HUD rate divides by it, and the
                chip balancer weights this app by it.
                """
                return len({id(p) for p, *_r in per_cam})

            dt = now2 - t_prev
            t_prev = now2
            if dt > 0:
                # Frames per CAMERA per second: a batched loop handles several
                # frames of each camera per pass, and a HUD that counted passes
                # would report a third of the rate the chip actually delivers.
                per_pass = max(1, len(per_cam)) / max(1, cams_this_pass())
                inst = per_pass / dt
                fps_ema = inst if fps_ema == 0 else fps_ema * 0.9 + inst * 0.1
                for pipe, *_rest in per_cam:
                    pipe.fps_ema = fps_ema

            # Chip load balancing: every few seconds, heartbeat this app and
            # split the accelerator's FPS BUDGET evenly across every live
            # CAMERA using it -- not every process. A four-camera system does
            # four times the inference of a one-camera system, so counting
            # processes would hand it four times the share.
            if now2 >= next_balance:
                next_balance = now2 + 5.0
                # Bound the app's own log while it runs -- a box that stays up
                # for months must not grow app.log without limit. Cheap: one
                # getsize per check, rotation only past the cap.
                runtime.trim_log(data_dir, max_log_bytes)
                # Passthrough pulls a restarted gateway forgot, put back.
                # No call at all when this app holds none.
                views.tick()
                uses_chip = det.name in ("hailo", "hailo-multi", "gstreamer")
                # CAMERAS, not entries. Since the loop began batching, per_cam
                # holds one entry per FRAME -- rounds x cameras -- so len() is
                # the batch size, not the camera count. A two-camera system on
                # batch 8 heartbeated a weight of 8, _fps_share saw "more than
                # four cameras" and handed back the 10 fps floor, and the
                # balancer then paced both cameras at 10. That is the whole of
                # "the fps dropped after the update": the chip was ~90% idle
                # and the loop was waiting on a grab thread it had told to go
                # slowly.
                live_cams = max(1, cams_this_pass())
                try:
                    camera_mod.heartbeat_app(uses_chip, weight=live_cams)
                    # Heal the camera registry: heartbeat saves from other
                    # apps can clobber this app's ownership entries (one
                    # unlocked JSON, many writers), and a lost entry is a
                    # second system failing on a device this one holds.
                    for pipe_ in pipes:
                        try:
                            pipe_.cam.reaffirm()
                        except Exception:                   # noqa: BLE001
                            pass
                    share = camera_mod.active_chip_apps() if uses_chip else live_cams
                except Exception:                           # noqa: BLE001
                    share = live_cams
                # Fully automatic pacing: _fps_share says what this many
                # cameras may each have, and the camera's MEASURED delivery
                # rate is the ceiling -- a camera that cannot produce the
                # allowance is left at what it produces, because no amount of
                # asking makes it faster. Four or fewer cameras get no cap at
                # all: they run as fast as they deliver.
                #
                # [app].fps_budget, when a deployment sets one, keeps the older
                # even-split behaviour instead -- an explicit budget is a
                # deliberate choice about this box's chip and outranks the
                # table.
                cap = (fps_budget / max(1, share)) if fps_budget \
                    else _fps_share(share)
                # The fair-share balancer, when the box switch is on: a cap
                # PER CAMERA in place of the one cap above (None = no cap).
                # Off, unreadable or inconsistent, it answers None and `cap`
                # stands exactly as before. Read live, so a flip lands
                # within a tick or two without a restart.
                ceilings, rates = {}, {}
                for pipe, *_rest in per_cam:
                    real_fps = float(getattr(pipe.cam, "measured_fps", 0.0) or 0.0)
                    ceilings[pipe.id] = real_fps if real_fps > 0 else pipe.base_fps
                    rates[pipe.id] = float(getattr(pipe, "fps_ema", 0.0) or 0.0)
                try:
                    fair = chip_bal.tick(
                        now2, chipshare_mod.switch_on(),
                        [(cid_, ceil_, rates[cid_])
                         for cid_, ceil_ in sorted(ceilings.items())],
                        slug=_slug, model=det_cfg.get("model") or det.name,
                        cost=chipshare_mod.model_cost(det_cfg.get("model"),
                                                      det_cfg.get("chip_cost")),
                        chip=uses_chip, det_key=id(det))
                except Exception:                           # noqa: BLE001
                    fair = None
                slowest = 0.0
                applied = {}
                for pipe, *_rest in per_cam:
                    ceiling = ceilings[pipe.id]
                    cap_ = cap if fair is None else fair.get(pipe.id, cap)
                    if uses_chip and cap_ is not None:
                        target = max(MIN_CAMERA_FPS, cap_)
                        if ceiling > 0:
                            target = min(target, ceiling)
                    else:
                        target = max(MIN_CAMERA_FPS, ceiling)
                    try:
                        pipe.cam.fps = target
                    except Exception:                       # noqa: BLE001
                        pass
                    applied[pipe.id] = target
                    slowest = target if slowest == 0 else min(slowest, target)
                chip_bal.note_applied(applied)
                # One loop, so it paces at the slowest camera's rate: reading
                # faster than the slowest camera can deliver only burns CPU.
                interval = 1.0 / max(1.0, slowest or 1.0)

            # Draw the boxes AND the zones onto each camera's live stream, so
            # every viewer -- the Detection page, the hub Live Preview -- sees
            # the same annotated feed, topped with a HUD naming the model and
            # the rate. Skipped where there is no real frame to draw on.
            model_label = _model_label(det_cfg, det)
            for pipe, frame, detections, *_rest in per_cam:
                _draw(pipe, frame, detections, model_label)

            # Try to come back from a fallback. Rebuilding is cheap compared
            # with a system that silently detects nothing, and if the chip is
            # still busy the next failure simply re-arms the timer.
            if (detector_retry_at and time.time() >= detector_retry_at
                    and det.name in ("none", "mock")
                    and (det_cfg.get("kind") or "mock") not in ("none", "mock")):
                detector_retry_at = 0.0
                try:
                    rebuilt = _build_det(det_cfg)
                except Exception as exc:                    # noqa: BLE001
                    # Still failing: back off toward the ceiling. A missing
                    # HEF fails every time and deserves a quiet minute; the
                    # transient chip faults recover on the first, fast try
                    # and never reach it.
                    detector_retry_delay = min(DETECTOR_RETRY_S,
                                               detector_retry_delay * 2)
                    detector_retry_at = _retry_after(detector_retry_delay)
                    # Refreshed, not left as whatever the FIRST failure said:
                    # "device busy" can become "model file missing" between
                    # retries, and the stale sentence would say why it fell
                    # back once and then quietly stop being true.
                    detector_error = (f"{det_cfg.get('kind') or 'configured'} "
                                      f"detector failed to load: {exc}")
                    log.warning("detector retry failed, trying again in "
                                "~%.0fs: %s", detector_retry_delay, exc,
                                extra=faults.event(
                                    faults.detector_event(exc)))
                else:
                    _infer_drain()
                    try:
                        det.close()
                    except Exception:                       # noqa: BLE001
                        pass
                    det = rebuilt
                    detect_fails = 0
                    detector_retry_delay = DETECTOR_RETRY_MIN_S
                    detector_error = None
                    log.info("detector recovered: %s", det.name)
                    runtime.write(data_dir, detector=det.name, error=None)

            # Events, per camera: each camera decides on ITS OWN detections
            # and zone result, so an event says which camera raised it and a
            # rule on camera 2 cannot fire from camera 1's objects.
            merged = {}
            for pipe, frame, detections, result, counts, triggers in per_cam:
                # Published in the event: a name, never a password.
                cam_name = (faults.mask_label(str(cam_cfg.get("source", "camera")))
                            if gst_mode else (pipe.label() or pipe.id))
                if pipe.id in held:
                    # A camera the watchdog is waiting for: what it would
                    # have measured is absent, not zero (as with the AI
                    # off), and its zone gates keep their state -- an empty
                    # picture is not a keep-out zone clearing. Its held
                    # rules' keys too: on the pass that decided, they had
                    # already run on the empty picture.
                    gone = tuple(f"fn_{i}_" for i in held_ids)
                    counts = {k: v for k, v in counts.items()
                              if not (k == "compliance"
                                      or k.startswith(_FRAME_KEY_PREFIXES)
                                      or (gone and k.startswith(gone)))}
                # Debounced to TRANSITIONS (entry / bounded heartbeat / clear):
                # a condition that persists does not write a row per frame.
                gated = [] if (pipe.id in held or (pipe.snap is not None
                                                   and pipe.snap.idle_now)) else _gated_events(
                    pipe, detections, result, now2, heartbeat_s, clear_hold_s)
                for kind, relevant, extra_detail in gated:
                    seq += 1
                    event = _make_event(kind, relevant, seq)
                    # Counts ride along on the event so a reviewer sees the
                    # state at the moment it fired, not just the boxes.
                    # Camera/model/zone are stamped too, so the event store can
                    # filter and group by them.
                    event["detail"]["counts"] = counts
                    event["detail"]["camera"] = cam_name
                    event["detail"]["camera_id"] = pipe.id
                    # Per-detection model tag when the multi detector supplied
                    # one (the event's boxes may span models); the config-level
                    # name(s) otherwise.
                    event["detail"]["model"] = (
                        next((d["model"] for d in relevant or []
                              if isinstance(d, dict) and d.get("model")), None)
                        or _model_label(det_cfg, det))
                    for key, value in (extra_detail or {}).items():
                        if value is not None:
                            event["detail"][key] = value
                    store.append(event)
                    # No image here. Every event used to save one, which filled
                    # the store with frames nobody asked for. Snapshots are now
                    # only ever taken on purpose -- a zone action or a workflow
                    # Snapshot node, both arriving through outputs_mod.fire.
                    log.info("[%s] %s: %s", pipe.id, event["type"],
                             event["label"])
                    # Fire any GPIO/MQTT/Modbus output bound to this event (a
                    # violation turning on the tower light). Best-effort.
                    outputs_mod.fire(outputs, event, capture_hooks, counts)

                # User-defined function triggers (thresholds crossing
                # false->true, rate-limited by each function's cooldown). Each
                # becomes its own event, so it shows in the feed and can drive
                # GPIO/MQTT/Modbus exactly like a violation does.
                for trig in triggers:
                    seq += 1
                    event = _make_event(
                        (trig.get("type", "alert"), trig.get("severity", "warn"),
                         trig.get("message", "function triggered")),
                        detections, seq)
                    event["detail"]["counts"] = counts
                    event["detail"]["camera"] = cam_name
                    event["detail"]["camera_id"] = pipe.id
                    event["detail"]["function"] = trig.get("id")
                    if trig.get("value") is not None:
                        event["detail"]["value"] = trig["value"]
                    # Entry/exit stamps and anything else a function wants on
                    # the record travel in `detail` -- merged rather than
                    # replaced, so counts/function/value above stay intact.
                    if isinstance(trig.get("detail"), dict):
                        event["detail"].update(trig["detail"])
                        d_ = trig["detail"]
                        if d_.get("exited_at") is not None and d_.get("duration_s") is not None:
                            _snap_finish(trig.get("id"), d_.get("track"), d_["duration_s"])
                            _fire_parked(trig.get("id"), d_.get("track"), d_)
                    # WHICH object raised it, for a Snapshot to tag and to time
                    # (a once-per-OID Trigger names it here, not in `detail`).
                    if trig.get("source_oid") is not None:
                        event["detail"].setdefault("track", trig["source_oid"])
                    store.append(event)
                    log.info("[%s] %s: %s", pipe.id, event["type"],
                             event["label"])
                    # A Trigger set to wait for the timer: its Data Table rows are
                    # completed when the stay ends; everything else fires now.
                    parked = [o for o in outputs if o.get("wait_end") and o.get("enabled")
                              and o.get("function") == trig.get("id")]
                    outputs_mod.fire([o for o in outputs if not any(o is q for q in parked)]
                                     if parked else outputs, event, capture_hooks, counts)
                    if parked:
                        _park_outputs(pipe, trig, event, counts, parked)

                # Merge this camera's numbers into the system-wide set. Zone and
                # function keys are unique per system (each belongs to exactly
                # one camera), so they simply coexist; the two AGGREGATES are
                # genuinely per-camera and are summed, with a per-camera
                # breakdown beside them.
                for key, value in counts.items():
                    if key in ("objects_in_frame", "violations_now"):
                        merged[key] = merged.get(key, 0) + value
                    elif key in merged and isinstance(value, (int, float)):
                        # The same key from a SECOND camera means a rule that
                        # runs on all of them ("*"). Fold the two together by
                        # what the metric means -- last-one-wins would report
                        # whichever camera happened to be read last.
                        merged[key] = _merge_metric(key, merged[key], value)
                    else:
                        merged[key] = value
                    # Always keep the per-camera value too, so a card can show
                    # ONE camera of a shared rule.
                    if key.startswith(("fn_", "zone_", "line_")):
                        merged[f"cam_{pipe.id}_{key}"] = value
                if pipe.id not in held:
                    merged[f"cam_{pipe.id}_objects"] = counts.get("objects_in_frame", 0)
                    merged[f"cam_{pipe.id}_violations"] = counts.get("violations_now", 0)

            if not ai_active:
                # AI OFF: publish NOTHING a camera would have measured. A zone
                # count of 0 is a real reading to a chart, a card, Blynk and
                # an "area empty" rule -- with the camera released it is a
                # guess, so the key is simply absent: history records a gap,
                # and the pages say "AI off" (builder.ai_off_keys). A free
                # rule's per-camera copy (cam_<id>_fn_...) is kept.
                keep_cam = tuple(f"cam_{p_.id}_fn_" for p_ in pipes)
                merged = {k: v for k, v in merged.items()
                          if not (k == "compliance"
                                  or (k.startswith(_FRAME_KEY_PREFIXES)
                                      and not k.startswith(keep_cam)))}
            if snap_stat["s"] is not None:
                merged["snap_detect_s"] = snap_stat["s"]
            counts = merged
            last_merged = merged
            # What this pass published may start a snapshot on the next one.
            for p_ in pipes:
                if p_.snap is not None:
                    p_.snap.update(merged)
            # Telemetry publishers: send current values to MQTT on their own
            # schedule, independently of whether anything alerted.
            # Every camera's tracked objects this frame, so a clock-paced Data
            # Table can record WHICH object it counted and not just how many.
            live_dets = [d for p in pipes for d in (getattr(p, "last_dets", None) or [])]
            outputs_mod.publish(outputs, counts, hooks=capture_hooks,
                                dets=live_dets)
            # What each OUTPUT did, carried on the SAME channel as every other
            # value -- state.json counts, the master, the canvas -- because a
            # second file would be a second thing to keep in step and a second
            # thing to be stale. Folded in after publish() so a card shows
            # this frame's result and not the previous frame's, and never
            # before it: a telemetry key is ABOUT a send, it is not something
            # to send.
            counts.update(outputs_mod.telemetry(outputs))
            # ...and whether each MQTT Connection is up, on the same channel,
            # for the broker dot on every MQTT node (app/mqtt_bus.py).
            counts.update(mqtt_bus.telemetry())
            # The Modbus listener follows the CONFIG, checked here rather than
            # only on reload: start() compares a signature and returns
            # immediately when nothing that needs a socket has changed, so a
            # connected PLC is never dropped by a poll. Adding a register is a
            # datastore write, not a new listener.
            try:
                modbus_srv.start(cfg.get("modbus_server") or {})
                # Every tick, even with nothing pending: an empty batch is
                # what zeroes the registers whose outputs the AI switch just
                # pruned, and the status register says which mode this is.
                modbus_srv.publish(_modbus_pending, detection_active=ai_active)
            except Exception as exc:                        # noqa: BLE001
                # A fieldbus that will not come up must never stop the box
                # watching. The reason reaches the UI through state() below.
                log.error("modbus server: %s", exc, exc_info=True)
            finally:
                _modbus_pending.clear()
            model_now = _model_label(det_cfg, det)
            # ...and the same names as a SET, so a rule can tell whether
            # the model it was bound to is one this system still runs. A
            # rule naming a model that is gone rejects every detection and
            # reads zero for ever otherwise (functions.Function.accepts).
            Function.running_models = frozenset(
                m.strip() for m in str(model_now or "").split("+") if m.strip())
            extra = {}
            if gst_mode and gst is not None:
                try:
                    extra["infer_fps"] = gst.infer_fps      # per-model chip rate
                except Exception:                           # noqa: BLE001
                    pass
            # The singular camera/zones/error fields stay as the FIRST camera's,
            # so every existing reader (the hub table, the fleet adapter, the
            # Detection page) keeps working; `cameras` carries the full picture.
            #
            # THIS WRITE RUNS EVERY FRAME, and until it named `detector_error`
            # it wrote `error=primary.cam.error` alone -- so a detector that
            # fell back at startup (a Hailo chip already held by another
            # system, a missing HEF) had its explanation overwritten on the
            # very first frame after it was recorded, and every frame after.
            # Video flowing, ai_running true, every rule reading zero, and the
            # ONE field built to say why stayed empty. detector_control's
            # detector_error() reads exactly this field. Camera error still
            # wins when both exist -- it names camera 1 specifically, which is
            # what these singular fields already promise to report.
            # The pid rides on EVERY heartbeat, not only on the first
            # write. runtime.write merges into whatever the file holds, so
            # a state that lost its pid (an older instance shutting down
            # after this one started -- see the stop write below) heals on
            # the next tick instead of reading as stopped for ever.
            # The "AI running" dot: GREEN only for a real detector with a
            # camera actually giving frames; otherwise the sentence why. A
            # detector that crashed mid-run has fallen back to "none" by now
            # (the failure ladder), so it goes red here while every other
            # rule keeps running.
            ai_error = None
            paused_now = watch.paused and restart_phase in ("stop", "paused")
            if paused_now:
                # Stopped for a missing camera: the dot says so, and that it
                # comes back by itself.
                ai_error = camwatch_mod.paused_text(
                    [cam_words.get(c, c) for c in watch.hold["cameras"]])
            elif watch.restarting and (ai_active or restart_phase):
                # Plain words for the red dot while the episode lasts.
                ai_error = (f"{camwatch_mod.REASON} "
                            f"({', '.join(cam_words.get(c, c) for c in watch.episode['cameras'])})")
            elif ai_active:
                # Every camera detection runs must be giving frames -- one
                # dead camera of three is not "AI running". The gstreamer
                # pipeline drives the first camera only.
                expected = [primary] if gst_mode else \
                    [p for p in pipes if not p.parked]
                stale = [p for p in expected
                         if now2 - ai_frames.get(p.id, 0.0) >= AI_FRAME_FRESH_S]
                if detector_error:
                    ai_error = str(detector_error)
                elif detect_fails:
                    # Failing but not yet fallen back: frames are flowing and
                    # nothing is being detected -- red, not green.
                    ai_error = f"inference failed: {infer_error[0]}"
                elif det.name == "none":
                    ai_error = "no AI model is running"
                elif stale or not expected:
                    ai_error = "; ".join(
                        f"no frames from camera {cam_words.get(p.id, p.id)}"
                        + (f" ({p.cam.error})" if p.cam.error else "")
                        for p in stale) or "no camera is running"
            # Paced: in memory every frame, on disk about once a second and
            # at once when running/pid/error/the AI dot changes
            # (runtime.heartbeat). --once still writes its one frame.
            runtime.heartbeat(data_dir, force=bool(args.once),
                          running=True, pid=os.getpid(),
                          # The user's switch: a watchdog restart's AI-off
                          # half is not the user switching it off.
                          mode="unified",
                          ai_on=bool(ai_active or restart_phase),
                          ai_ok=bool(ai_active and ai_error is None),
                          ai_error=ai_error,
                          # ...and while the BOX has it stopped because no
                          # camera is there, why (app/camwatch.py PAUSED).
                          ai_paused=(camwatch_mod.PAUSED if paused_now
                                     else None),
                          ai_inactive=(sorted(
                              {f.id for p_ in pipes if p_.id in held
                               # _all: the held ones are exactly the
                               # ones `functions` no longer lists.
                               for f in getattr(p_.fn_engine, "_all", ())
                               if f.id in set(held_ids)})
                              if ai_active else ai_inactive),
                          cam_watch=watch.report(),
                          # ...and which of the published numbers are those
                          # rules' KEPT tallies: real, but held, not live.
                          ai_kept=([] if ai_active and not held else sorted(
                              {k for p_ in pipes
                               for k0 in p_.fn_engine.kept_keys()
                               for k in (k0, f"cam_{p_.id}_{k0}")
                               if k in counts})),
                          camera=primary.cam.label,
                          detector=det.name, model=model_now,
                          # A parked camera's leftover fault is not live:
                          # with the AI off nothing is being captured.
                          error=((primary.cam.error or detector_error)
                                 if ai_active else detector_error),
                          counts=counts, zones=primary.overlays,
                          cameras=[{"id": p.id,
                                    "name": faults.mask_label(p.cfg.get("name") or ""),
                                    # Masked like `source` below (Camera.label).
                                    "label": p.cam.label,
                                    # Password masked (faults.redact): state.json
                                    # is read by every master route that reports
                                    # the app, and no reader needs the secret --
                                    # the config is where the source lives.
                                    "source": faults.redact(str(p.cfg.get("source", ""))),
                                    # A Snapshot camera between snaps detects
                                    # nothing: 0, not the rate the idle loop spins at.
                                    "fps": (0 if p.snap is not None and p.snap.idle_now
                                            else round(p.fps_ema)),
                                    "measured_fps": round(
                                        float(getattr(p.cam, "measured_fps", 0.0) or 0.0)),
                                    "error": p.cam.error,
                                    # Set to Snapshot: the page shows what was
                                    # snapped (Camera Setup, Camera snaps).
                                    **({"snap": True} if p.snap is not None else {}),
                                    # {"feed", "why"} when [video] feed asks
                                    # for passthrough; never an address.
                                    **({"view": p.view} if p.view else {}),
                                    "zones": p.overlays,
                                    "objects": (merged.get(f"cam_{p.id}_objects", 0))}
                                   for p in pipes],
                          fps=(0.0 if pipes and all(p.snap is not None and p.snap.idle_now
                                                    for p in pipes) else round(fps_ema, 1)),
                          # Which board terminals THIS app has a claim on.
                          # Only this process can say: libgpiod calls every
                          # lgpio consumer "lg", so from the hub a pin our own
                          # rule drives looks exactly like one Node-RED owns.
                          io_held=_held_ports(),
                          # Whether the fieldbus came up, and why not. The app
                          # keeps running either way, so without this a Modbus
                          # server that never bound its port looks exactly like
                          # one no PLC has got round to polling.
                          modbus=modbus_srv.state(),
                          **extra)
            if args.once:
                break
            # Pace to the target rate NET of the work already done this tick.
            # Sleeping the full interval ON TOP of inference time halves the
            # real frame rate exactly when the chip is busiest (several models
            # sharing the NPU) -- the loop then falls behind the camera and the
            # feed looks delayed.
            elapsed = time.time() - now
            # AI off: nothing to capture, so the free rules tick at a steady,
            # cheap rate instead of a parked camera's.
            pace = interval if ai_active else max(interval, 1.0 / AI_OFF_FPS)
            time.sleep(max(0.005, pace - elapsed))
    finally:
        _stop.set()
        # A Modbus Write set to keep sending stops with the app, including a
        # repeat already queued for the write worker.
        try:
            outputs_mod.stop_repeats()
        except Exception:                                   # noqa: BLE001
            pass
        server.shutdown()
        # The gateway's pulls go with the app: a pull left behind would keep
        # serving the camera with nobody detecting on it, and would refuse
        # a legacy publish under the same name on the next start.
        try:
            views.close()
        except Exception:                                   # noqa: BLE001
            pass
        for p in pipes:
            # Last flush: a count earned in the final seconds must not be lost
            # because the operator pressed stop.
            if p.fn_engine is not None:
                p.fn_engine.flush_daily(force=True)
            p.close()
        if gst_mode:
            cam.close()             # gstreamer keeps its own Camera object
        # The batch in flight finishes first: releasing the accelerator under
        # a running batch is a fault, not a clean exit -- and through the
        # service an unclean exit is what the OTHER system on the chip pays
        # for. Guarded, so a release that faults anyway cannot skip the
        # "stopped" line below and leave a dead pid reading as running.
        try:
            _infer_drain()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            det.close()
        except Exception as exc:                            # noqa: BLE001
            log.warning("detector release failed on exit: %s", exc)
        # ONLY if the state still describes THIS process. A restart
        # overlaps: the new app writes its pid and starts detecting while
        # the old one is still releasing the accelerator, and the old
        # one's "stopped" write then cleared the new one's pid. The file
        # said running with no pid, runtime.status() calls that dead, and
        # every page reported the AI stopped while it was detecting cars.
        if int((runtime.read(data_dir) or {}).get("pid") or 0) in (0, os.getpid()):
            runtime.write(data_dir, running=False, pid=None)
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
