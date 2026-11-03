# -*- coding: utf-8 -*-
"""
workflow.py -- the visual pipeline editor's backend: graph storage + compiler.

The Workflow page lets an operator wire the detection pipeline as nodes:

    Detection Stream -> Class Filter -> Zone -> Analytics -> Dashboard / Output

The graph is an AUTHORING format, never an execution format. On save it is
COMPILED into the artifacts the platform already runs:

    analytics nodes  -> [[functions]] documents (core/services/functions.py
                        validate_function is the contract; the Pi app engine
                        interprets them unchanged)
    output nodes     -> [[outputs]] documents, each bound to its function via
                        the `function` field (app/outputs.py fire())
    dashboard nodes  -> Builder widgets on a dedicated "Workflow" layout page

so the detection app stays standalone and documents stay DATA. This module is
pure dict-in/dict-out where it matters (validate/compile take a graph, return
artifacts + errors) so it unit-tests with no server.

Graphs persist as JSON at store_dir()/workflows/<slug>.json (same doctrine as
Builder layouts: user-generated, outside the repo, atomic tmp+replace).

Conventions the compiler owns:
  - fn ids come from node.data.fnId, minted ONCE at node creation and never
    regenerated on rename -- stat keys fn_<id>_*, history series and widget
    bindings survive edits.
  - compiled outputs and widgets carry a "wf_" id prefix; anything without the
    prefix is hand-made and is preserved on merge.
  - the widgets land on the Builder page {id: "workflow"}; the operator's own
    pages are never touched. Builder caps 24 widgets per page.
  - compile errors do NOT block saving the graph (auto-save must never lose
    canvas work); they block the artifact writes and surface as node badges.
"""

import copy
import hashlib
import json
import os
import re
import urllib.parse

from core import config as config_mod
from core.repos import datasets as datasets_repo
from core.services import builder as builder_service
from core.services import timeaxis as _timeaxis
from core.services import chartmodel as _chartmodel
from core.services import chart_series
from core.services import detector_control as dc
from core.services import functions as functions_service
from core.services import class_filter as class_filter_service
from core.services import (publish_follow, rule_camera, state_points,
                           trigger_out, trigger_paint)
from core.services import telegram_bots as _tg_bots
from core.services import telegram_commands as tg_commands
from core.services import alert_watch
from core.services import telegram_photos as _tg_photos

#: 1 -> 2: Cycle Time stopped publishing `current`, so the metric an Alert
#: falls back to when none is chosen changed from a head count to a clock.
#: See migrate_dwell_count_alerts for what the bump is used for.
#: 2 -> 3: the alarm's own settings (severity, message, cooldown, colour) left
#: the Condition for an Alert node of its own. See migrate_split_alerts.
SCHEMA_VERSION = 10
MAX_NODES = 200
MAX_EDGES = 400

#: Who a Telegram message may be addressed to, when a node names someone
#: instead of taking the bot's own chat. Three real shapes: a person's id
#: (positive), a group or supergroup (negative, -100…), and a public channel
#: by @name. Anything else is a typo that Telegram answers with "chat not
#: found" into a log nobody is reading.
#: Defined once in telegram_bots, because the canvas, the footage export and
#: the Settings test all have to agree about what an address IS -- one of them
#: being stricter than another is a node that compiles and then cannot send.
_CHAT_ID_RE = _tg_bots.CHAT_ID_RE

#: Function kinds that consume NO detections, and so run whether or not a
#: Detection Stream is wired to them: a constant, a formula over other stats,
#: a number fetched from an HTTP endpoint. Everything else watches the camera
#: and stays switched off until a stream reaches it (see has_detection_source
#: in compile_graph) -- an unconnected node on the canvas is a draft, and a
#: draft must not count, alert or write history.
STANDALONE_FN_TYPES = ("expr", "api_poll", "mqtt_sub", "total", "board_input",
                       "modbus_read", "schedule", "inject", "state_watch")

#: Functions that are a WHEN of their own -- the moment IS the decision, so
#: an output wired after one needs no Condition -- and the metric that is 1 for
#: the one tick that moment arrives on.
#: A Detection Stream's or Class Filter's ON/OFF point (state_points.py) is
#: one too: the moment the state changes IS the decision.
CLOCK_FN_METRIC = {"schedule": "due", "inject": "fired", "state_watch": "fired"}

#: Seconds per unit of an Inject's repeat, as the inspector names them.
INJECT_UNITS = {"seconds": 1, "minutes": 60, "hours": 3600}

#: Function types that read the camera's detections by what they ARE, wired
#: or not: a zone count with no picture reads zero, and zero is an answer to
#: an alarm. Everything else is judged by the graph (frames_fed). "custom" (a
#: Python node) is judged by the graph too: code with nothing from a camera
#: wired in has no frame to read.
FRAME_FN_TYPES = tuple(t for t in functions_service.TYPES
                       if t not in functions_service.LOGIC_TYPES
                       and t != "custom")

#: A stat key only a camera fills: zone_*/line_* come from the zone engine,
#: objects_*/violations_* and compliance from the detections, cam_* from the
#: other cameras' pipelines.
FRAME_KEY_PREFIXES = ("zone_", "line_", "objects_", "violations_", "cam_")
FRAME_KEYS = ("compliance",)

#: A stat-key TOKEN inside an expression or a code string. Only ever applied
#: to places that hold keys (see function_keys / output_keys) -- never to a
#: topic, a name or a label, which is how "plant/line_2/state" once read as a
#: line counter.
_KEY_TOKEN_RE = re.compile(
    r"\b(?:fn|zone|line|objects|violations|cam)_[A-Za-z0-9_]+|\bcompliance\b")

#: A quoted string inside an expression: text, never a key.
_STR_LITERAL_RE = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")

#: Bumped when what logic_artifacts decides changes, so a box whose stored
#: [logic] predates the change recompiles at the next master start.
#: 4: MQTT Connections -- a box updated from v1.0.12 held MQTT Subscribe
#: logins in its compiled [[functions]] until somebody saved the workflow;
#: the recompile at start (app_supervisor.ensure_compiled) migrates the
#: graph and rewrites them without (Yaya, review of a34da37, M2).
#: 5: a rule's camera (rule_camera.py, trigger_paint.stamp_camera).
LOGIC_VERSION = 5


def function_keys(doc):
    """The stat keys a function READS: tokens of its expression, the key a
    running total follows, and -- for a Python node -- the keys its code
    names. Its own triggers read its own metrics, so they add nothing."""
    p = (doc or {}).get("params") or {}
    keys = set()
    if isinstance(p.get("expr"), str):
        # A quoted word in an expression is TEXT -- iff(x, "line_1 down",
        # "ok") says nothing about a line counter -- so literals go first.
        keys.update(_KEY_TOKEN_RE.findall(_STR_LITERAL_RE.sub('""', p["expr"])))
    if isinstance(p.get("code"), str):
        # In Python code a quoted key IS how a stat is read
        # (ctx["stats"]["zone_bay_count"]), so code keeps its strings.
        keys.update(_KEY_TOKEN_RE.findall(p["code"]))
    if isinstance(p.get("key"), str) and p["key"].strip():
        keys.add(p["key"].strip())
    return keys


def output_keys(o):
    """The stat keys an output READS: its value key, its message
    placeholders, and a Data Table's stat/wire columns. Topics, subjects,
    payloads and labels are text, not keys, and are never read here."""
    keys = set()
    if isinstance(o.get("key"), str) and o["key"].strip():
        keys.add(o["key"].strip())
    for value in (o.get("vars") or {}).values() \
            if isinstance(o.get("vars"), dict) else ():
        if isinstance(value, str) and value.strip():
            keys.add(value.strip())
    for col in o.get("columns") or ():
        if isinstance(col, dict) and isinstance(col.get("key"), str) \
                and col["key"].strip():
            keys.add(col["key"].strip())
    return keys


def key_owner(key, fids):
    """Which function publishes `key` (fn_<id>_<metric>), longest id first --
    ids may themselves hold underscores. None when no function here does."""
    if not key.startswith("fn_"):
        return None
    best = None
    for fid in fids:
        if key.startswith(f"fn_{fid}_") and (best is None or len(fid) > len(best)):
            best = fid
    return best


def key_waits(key, needs, fids):
    """Does reading `key` wait for detection? A camera key does; a function's
    key does when that function waits -- or when NO function here publishes
    it: an unpublished key reads as 0, and a rule firing on a 0 that nobody
    measured is exactly the false alarm logic mode must never raise."""
    if key in FRAME_KEYS or key.startswith(FRAME_KEY_PREFIXES):
        return True
    if key.startswith("fn_"):
        owner = key_owner(key, fids)
        return owner is None or owner in needs
    return False


def frames_fed(nodes, edges):
    """Ids of every node a Detection Stream reaches, directly or through any
    chain of wires. On the RESOLVED graph, so a Link is the wire it stood for
    and a function Call is its body."""
    succ = {}
    for e in edges or ():
        succ.setdefault(e.get("source"), []).append(e.get("target"))
    todo = [n["id"] for n in nodes or () if n.get("type") == "source.detection"]
    seen = set(todo)
    while todo:
        for nxt in succ.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def canvas_waits(canvas_nodes, canvas_edges, waiting):
    """The DRAWN nodes that wait for detection, for the canvas to mark.

    `waiting` holds RESOLVED ids (logic_artifacts' `nodes`): a node inside a
    function copy is "<call>__<id>", and a Link pair is gone. So a copy that
    waits marks both the Call and the body node it was copied from, and a
    Link out/in waits when what feeds it does -- relayed to a fixpoint, so a
    chain of jumps settles however long it is.
    """
    ids = {n["id"] for n in canvas_nodes or ()}
    out = {nid for nid in waiting if nid in ids}
    for rid in waiting:
        if "__" in rid:
            call, _sep, orig = rid.partition("__")
            for part in (call, orig.split("__")[-1]):
                if part in ids:
                    out.add(part)
    by_id = {n["id"]: n for n in canvas_nodes or ()}
    while True:
        before = len(out)
        for e in canvas_edges or ():
            tgt = by_id.get(e.get("target")) or {}
            if tgt.get("type") == "common.link_out" and e.get("source") in out:
                out.add(tgt["id"])
        fed_links = {str((n.get("data") or {}).get("link") or "")
                     for n in canvas_nodes or ()
                     if n.get("type") == "common.link_out" and n["id"] in out}
        for n in canvas_nodes or ():
            if n.get("type") == "common.link_in" \
                    and str((n.get("data") or {}).get("link") or "") in fed_links:
                out.add(n["id"])
        if len(out) == before:
            return out


def logic_plan(functions, outputs, fed, fn_meta, cond_meta, switch_meta, nodes):
    """The STRUCTURAL half of the decision, for one canvas: which functions
    and outputs wait for detection by what they are or where they are wired,
    plus the maps back to the drawn nodes. What they READ is decided in
    logic_decide, over the whole system's rules at once -- a Formula on one
    canvas may read a rule on another. JSON-shaped, so compile_store can
    carry it."""
    fids = {f["id"] for f in functions}
    base = set()
    node_fns = {}
    metas = list(fn_meta.items()) + list(cond_meta.items()) \
        + [(nid, doc) for (nid, _h), doc in switch_meta.items()]
    for nid, doc in metas:
        if not doc:
            continue
        node_fns.setdefault(nid, set()).add(doc["id"])
        if nid in fed or doc.get("type") in FRAME_FN_TYPES:
            base.add(doc["id"])
    # ...and every node by the id it publishes under: a Variable written by a
    # Set Variable has its function minted by the writer's path, not in
    # fn_meta under the Variable's own node.
    for n in nodes or ():
        own = _slug((n.get("data") or {}).get("fnId") or "")
        if own in fids:
            node_fns.setdefault(n["id"], set()).add(own)
    for doc in functions:
        code = str((doc.get("params") or {}).get("code") or "")
        if doc.get("type") in FRAME_FN_TYPES or (
                doc.get("type") == "custom"
                and re.search(r"\b(detections|tracks)\b", code)):
            base.add(doc["id"])
    out_node = {f"wf_{n['id']}"[:40]: n["id"] for n in nodes or ()}
    out_base = {o.get("id") for o in outputs
                if out_node.get(o.get("id")) in fed
                or o.get("trigger") == "violation"
                or o.get("mode") == "crossing"}
    return {"base": sorted(base), "out_base": sorted(out_base),
            "node_fns": {k: sorted(v) for k, v in node_fns.items()},
            "out_node": out_node, "fed": sorted(fed)}


def logic_decide(functions, outputs, base, out_base):
    """The READ half: from the structural waits, which further functions
    wait because they read a key that waits (key_waits), to a fixpoint --
    judged against EVERY rule given, so pass the whole system's. Returns
    (waiting function ids, waiting output ids)."""
    fids = {f["id"] for f in functions}
    needs = set(base)
    for _ in range(len(functions) + 1):         # a chain settles one link at a time
        grew = False
        for doc in functions:
            if doc["id"] not in needs and any(
                    key_waits(k, needs, fids) for k in function_keys(doc)):
                needs.add(doc["id"])
                grew = True
        if not grew:
            break
    waiting = set()
    for o in outputs:
        if (o.get("id") in out_base
                or (o.get("function") and (o["function"] in needs
                                           or o["function"] not in fids))
                or any(key_waits(k, needs, fids) for k in output_keys(o))):
            waiting.add(o.get("id"))
    return needs, waiting


def plan_waiting_nodes(plan, needs, waiting_outputs):
    """The resolved node ids that wait, from one canvas's plan and the
    system-wide decision -- what canvas_waits maps onto the drawn canvas."""
    nodes = set(plan["fed"])
    for nid, ids in plan["node_fns"].items():
        if set(ids) & needs:
            nodes.add(nid)
    for oid, nid in plan["out_node"].items():
        if oid in waiting_outputs:
            nodes.add(nid)
    return nodes


def logic_artifacts(functions, outputs, fed, fn_meta, cond_meta, switch_meta,
                    nodes):
    """What runs WITHOUT a camera: {"functions", "outputs", "version"} --
    what the app runs with its AI switched off (app/run.py ai_config) -- plus
    "nodes", the resolved node ids that WAIT, which is what the canvas marks
    (canvas_waits), and "_plan", which compile_store uses to decide again
    over the whole system. One decision, read from both sides, so the canvas
    can never say a node runs that the app with the AI off skips.

    A function waits when its node is fed by a Detection Stream, when its type
    reads detections by nature (FRAME_FN_TYPES), when it reads a key that
    waits (key_waits: a camera key, a waiting rule's key, a key nobody
    publishes), or when it is Python code that reads the detections. MIXED
    inputs therefore wait: a Math of a zone count AND an Inject is not
    computed with a zero standing in for the count. An output waits when its
    node is fed, when what fires it waits, when it reads a key that waits, or
    when it answers detections themselves (violations, line crossings).
    """
    plan = logic_plan(functions, outputs, fed, fn_meta, cond_meta,
                      switch_meta, nodes)
    needs, waiting_outputs = logic_decide(functions, outputs, plan["base"],
                                          plan["out_base"])
    return {"functions": [f["id"] for f in functions if f["id"] not in needs],
            "outputs": [o["id"] for o in outputs
                        if o.get("id") not in waiting_outputs],
            "version": LOGIC_VERSION,
            "nodes": sorted(plan_waiting_nodes(plan, needs, waiting_outputs)),
            "_plan": plan}


class InjectRefused(ValueError):
    """A press that cannot fire, with the HTTP status the route answers."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


#: What a press of an Inject's button may ask. Start and Stop only mean
#: something to an Inject with a repeat.
INJECT_ACTIONS = ("press", "start", "stop")


def inject_target(graph, deployed, node_id, app_running, action="press"):
    """The function id a press of Inject `node_id` fires, or InjectRefused.

    Only what is DEPLOYED can be pressed: the node is looked up in the SAVED
    graph, and its function among the functions the app is actually running.
    A node dragged on but not saved, or saved with an error, has nothing on
    the box to fire -- and saying so beats a press that silently does nothing.
    Kept here rather than in the route so the rules are testable without a
    web stack; the route only adds who may press (workflow.edit).
    """
    node = next((n for n in (graph or {}).get("nodes") or []
                 if n.get("id") == node_id and n.get("type") == "common.inject"),
                None)
    if node is None:
        raise InjectRefused(404, "that Inject is not saved yet - save the "
                                 "workflow first")
    fn_id = str((node.get("data") or {}).get("fnId") or "")
    fn = {f.get("id"): f for f in deployed or ()}.get(fn_id)
    if not fn or fn.get("type") != "inject" or not fn.get("enabled", True):
        raise InjectRefused(409, "this Inject is not running on the box - "
                                 "fix any error on it and save again")
    if action not in INJECT_ACTIONS:
        raise InjectRefused(400, "an Inject can be pressed, started or "
                                 "stopped")
    if action != "press" and not (fn.get("params") or {}).get("interval_s"):
        raise InjectRefused(400, "only an Inject that repeats can be started "
                                 "or stopped - set its Repeat to an interval")
    if not app_running:
        raise InjectRefused(409, "the detection app is not running, so there "
                                 "is nothing to inject into")
    return fn_id

#: Ceiling on one Python node's source. A node is a step in a pipeline; past
#: this it is a module, and a module belongs in a template's signed code.
MAX_PYTHON_BYTES = 20000

#: --- Dataset node -----------------------------------------------------------
#: When a Dataset writes a row.
#:   alert  -- one row each time the wired trigger fires (log every incident)
#:   every  -- one row on a timer, alert or not (a trend table)
#:   change -- one row whenever any column's value differs from the last row
#:   crossing -- one row each time an object crosses the wired Line Counter's
#:             line, whichever way (the direction is an event field). Rides
#:             the per-crossing event path, never the clock, so no change
#:             detector can swallow a second person through the same door.
DATASET_MODES = ("alert", "every", "change", "crossing")

#: What a Data Table node does to its table.
#:
#:   insert -- add a row (the original behaviour, and the common one)
#:   select -- read only: the node writes nothing, and exists for its columns'
#:             read ports, so a card can show what the table holds
#:
#: update, upsert and delete are NOT here yet, and the reason is worth
#: knowing: a row reaches the database by travelling through events.jsonl,
#: which the master replays from offset 0 on every boot. Modifying a row
#: therefore has to be an EVENT too ("set defect_type where serial_no = X"),
#: applied in log order so a replay reproduces the same final state, and it
#: needs a key column nominated on the node to say which row is meant. That
#: is a coherent design and a real piece of work; shipping the word "update"
#: on a menu before it exists would be worse than its absence.
DATASET_OPS = ("insert", "select")

#: Where one column's value comes from.
#:   wire  -- dragged in from another node (resolved to a stat key at compile)
#:   stat  -- a published key picked from a list, no wire on the canvas
#:   event -- context from the detection that triggered the row
#:   const -- a fixed string or number, so the row is self-describing
DATASET_COL_SOURCES = ("wire", "stat", "event", "const")

#: Event context a column may capture. These are read app-side out of the
#: event and its `detail`, so the list must stay in step with what
#: systems/demo/app/outputs.py::_event_field knows how to find.
DATASET_EVENT_FIELDS = ("ts", "label", "type", "severity", "camera", "zone",
                        # Which way a line was crossed -- in / out on a faced
                        # line, cross on a plain one. Stamped by run.py from
                        # the zone engine's crossing_events; blank on any row
                        # not written by a crossing. _event_field's generic
                        # walk already resolves it off the detail.
                        "direction",
                        "track_id", "detection_label", "model", "value",
                        # Per-stay context from Time in Zone's OID events:
                        # how long the object stayed, and when it came and went.
                        "duration_s", "entered_at", "exited_at",
                        # How far past the limit the stay went, on a Trigger set
                        # to wait for the timer to end (0 when it did not).
                        "exceeded_s",
                        # The live per-person clock (zone-scoped timer port).
                        "in_zone_s",
                        # What the depth camera measured on the object, when
                        # its class has a measurement profile (app/measure.py
                        # Measurer stamps them flat on the detection).
                        "depth_mm", "length_mm", "width_mm", "height_mm",
                        "distance_mm", "diameter_mm")

#: A dataset is a table an operator reads, not a warehouse: past this many
#: columns it stops fitting on a screen and stops being a table.
MAX_DATASET_COLS = 16

#: What a Trigger paints in when it is told to paint and not told what in.
#: Red is what a violation has always been drawn as. Mirrors
#: DEFAULT_BOX_COLOR in frontend/src/pages/Workflow.jsx, which is what the
#: colour picker offers before anything is chosen.
DEFAULT_BOX_COLOR = "#FF0000"

#: What a new Python Function node starts with -- a working example rather
#: than an empty box, because the contract (what is in `ctx`, what to return)
#: is the part nobody can guess.
PYTHON_NODE_TEMPLATE = '''\
# Runs once per frame. Return a number, or a dict of numbers.
#
#   ctx["detections"]  [{"label", "conf", "x", "y", "w", "h", "track_id"}, ...]
#   ctx["tracks"]      {track_id: track}
#   ctx["zones"]       the system's zones
#   ctx["stats"]       every value published so far this frame
#   ctx["frame_w"], ctx["frame_h"], ctx["now"]
#   ctx["state"]       a dict that survives between frames - counters go here
#
# An exception benches this node for the run and publishes fn_<id>_error = 1;
# it never stops detection.

def evaluate(ctx):
    people = [d for d in ctx["detections"] if d.get("label") == "person"]
    return len(people)
'''

#: What a Formula node can do, in palette order. `arity` is how many operands
#: it takes, which decides how many input handles the node shows; `tmpl` builds
#: the expression the engine already knows how to evaluate, so this whole
#: feature is a way of WRITING an expression, not a second maths engine.
#: Function names are the ones functions._ALLOWED_CALLS whitelists.
#: The order categories are listed in. Arithmetic first because it is what
#: almost every formula needs; the specialised ones sit below rather than
#: making the common case scroll.
FORMULA_GROUPS = ("Arithmetic", "Rounding", "Compare", "Logic",
                  "Powers & roots", "Logs & growth", "Trigonometry",
                  "Ratios & scaling", "Choose")

#: Every operation a Formula node offers.
#:
#:   group   which section of the picker it appears under
#:   arity   how many operands (drives the node's input handles)
#:   tmpl    the expression it compiles to
#:   hint    what it is FOR, in the operator's terms -- also what the picker
#:           searches, so "percent" finds pct and "average" finds avg without
#:           anyone knowing the function name
#:
#: Every tmpl must use only the calls in functions._ALLOWED_CALLS; the smoke
#: test compiles all of them to prove it.
FORMULA_OPS = {
    # -- Arithmetic ---------------------------------------------------------
    "add":   {"group": "Arithmetic", "label": "+  add", "arity": 2,
              "tmpl": "({a} + {b})", "hint": "sum, plus, total of two values"},
    "sub":   {"group": "Arithmetic", "label": "−  subtract", "arity": 2,
              "tmpl": "({a} - {b})", "hint": "difference, minus, take away"},
    "mul":   {"group": "Arithmetic", "label": "×  multiply", "arity": 2,
              "tmpl": "({a} * {b})", "hint": "product, times"},
    "div":   {"group": "Arithmetic", "label": "÷  divide", "arity": 2,
              "tmpl": "({a} / {b})", "hint": "ratio, per, quotient"},
    "mod":   {"group": "Arithmetic", "label": "%  remainder", "arity": 2,
              "tmpl": "({a} % {b})", "hint": "modulo, what is left over"},
    "abs":   {"group": "Arithmetic", "label": "abs  size", "arity": 1,
              "tmpl": "abs({a})", "hint": "magnitude, drop the minus sign"},
    "sign":  {"group": "Arithmetic", "label": "sign  −1 / 0 / +1", "arity": 1,
              "tmpl": "sign({a})", "hint": "direction, is it up or down"},
    "min":   {"group": "Arithmetic", "label": "min  smaller of", "arity": 2,
              "tmpl": "min({a}, {b})", "hint": "lowest, smallest"},
    "max":   {"group": "Arithmetic", "label": "max  larger of", "arity": 2,
              "tmpl": "max({a}, {b})", "hint": "highest, biggest, peak"},
    "avg":   {"group": "Arithmetic", "label": "avg  average of", "arity": 2,
              "tmpl": "avg({a}, {b})", "hint": "mean, average of two values"},
    # -- Rounding -----------------------------------------------------------
    "round": {"group": "Rounding", "label": "round  nearest", "arity": 1,
              "tmpl": "round({a})", "hint": "to the nearest whole number"},
    "ceil":  {"group": "Rounding", "label": "ceil  round up", "arity": 1,
              "tmpl": "ceil({a})", "hint": "always up, ceiling"},
    "floor": {"group": "Rounding", "label": "floor  round down", "arity": 1,
              "tmpl": "floor({a})", "hint": "always down"},
    "trunc": {"group": "Rounding", "label": "trunc  drop decimals", "arity": 1,
              "tmpl": "trunc({a})", "hint": "cut the fraction off, toward zero"},
    # -- Compare ------------------------------------------------------------
    "gt":    {"group": "Compare", "hidden": True, "label": ">  greater than", "arity": 2,
              "tmpl": "({a} > {b})", "hint": "above, more than, over"},
    "lt":    {"group": "Compare", "hidden": True, "label": "<  less than", "arity": 2,
              "tmpl": "({a} < {b})", "hint": "below, under, fewer"},
    "gte":   {"group": "Compare", "hidden": True, "label": ">=  at least", "arity": 2,
              "tmpl": "({a} >= {b})", "hint": "minimum, not below"},
    "lte":   {"group": "Compare", "hidden": True, "label": "<=  at most", "arity": 2,
              "tmpl": "({a} <= {b})", "hint": "maximum, not above"},
    # "=" the question, not "=" the assignment. This op COMPARES: it reads
    # 1 while both sides are the same and 0 while they differ. Storing a
    # value INTO a variable (code's var_a = var_b) is not an operation here
    # at all -- wire the value into a Variable node and it holds it.
    "eq":    {"group": "Compare", "hidden": True, "label": "==  is equal to", "arity": 2,
              "tmpl": "({a} == {b})",
              "hint": "same as, is exactly, matches; 1 while equal, 0 while "
                      "not. To STORE a value into a variable, wire it into a "
                      "Variable node instead"},
    "neq":   {"group": "Compare", "hidden": True, "label": "≠  is not equal to", "arity": 2,
              "tmpl": "({a} != {b})", "hint": "not equal, changed, different"},
    # -- Logic --------------------------------------------------------------
    "and":   {"group": "Logic", "label": "and  both", "arity": 2,
              "tmpl": "({a} and {b})", "hint": "both conditions hold"},
    "or":    {"group": "Logic", "label": "or  either", "arity": 2,
              "tmpl": "({a} or {b})", "hint": "either condition holds"},
    "not":   {"group": "Logic", "label": "not  invert", "arity": 1,
              "tmpl": "(not {a})", "hint": "opposite, negate, flip"},
    # -- Powers & roots -----------------------------------------------------
    "pow":   {"group": "Powers & roots", "label": "pow  to the power", "arity": 2,
              "tmpl": "pow({a}, {b})", "hint": "exponent, squared, cubed"},
    "sqrt":  {"group": "Powers & roots", "label": "sqrt  square root", "arity": 1,
              "tmpl": "sqrt({a})", "hint": "square root"},
    "cbrt":  {"group": "Powers & roots", "label": "cbrt  cube root", "arity": 1,
              "tmpl": "cbrt({a})", "hint": "cube root"},
    "hypot": {"group": "Powers & roots", "label": "hypot  √(a²+b²)", "arity": 2,
              "tmpl": "hypot({a}, {b})", "hint": "distance, pythagoras, diagonal"},
    # -- Logs & growth ------------------------------------------------------
    "ln":    {"group": "Logs & growth", "label": "ln  natural log", "arity": 1,
              "tmpl": "ln({a})", "hint": "logarithm base e"},
    "log":   {"group": "Logs & growth", "label": "log  log to a base", "arity": 2,
              "tmpl": "log({a}, {b})", "hint": "logarithm of a to base b"},
    "log10": {"group": "Logs & growth", "label": "log10  log base 10", "arity": 1,
              "tmpl": "log10({a})", "hint": "decibels, orders of magnitude"},
    "log2":  {"group": "Logs & growth", "label": "log2  log base 2", "arity": 1,
              "tmpl": "log2({a})", "hint": "doublings, binary orders"},
    "exp":   {"group": "Logs & growth", "label": "exp  e to the power", "arity": 1,
              "tmpl": "exp({a})", "hint": "exponential growth"},
    # -- Trigonometry -------------------------------------------------------
    "sin":   {"group": "Trigonometry", "label": "sin", "arity": 1,
              "tmpl": "sin({a})", "hint": "sine, radians"},
    "cos":   {"group": "Trigonometry", "label": "cos", "arity": 1,
              "tmpl": "cos({a})", "hint": "cosine, radians"},
    "tan":   {"group": "Trigonometry", "label": "tan", "arity": 1,
              "tmpl": "tan({a})", "hint": "tangent, radians"},
    "asin":  {"group": "Trigonometry", "label": "asin  inverse sine", "arity": 1,
              "tmpl": "asin({a})", "hint": "arcsine"},
    "acos":  {"group": "Trigonometry", "label": "acos  inverse cosine", "arity": 1,
              "tmpl": "acos({a})", "hint": "arccosine"},
    "atan":  {"group": "Trigonometry", "label": "atan  inverse tangent", "arity": 1,
              "tmpl": "atan({a})", "hint": "arctangent"},
    "atan2": {"group": "Trigonometry", "label": "atan2  angle of (y, x)", "arity": 2,
              "tmpl": "atan2({a}, {b})", "hint": "bearing, heading, direction"},
    "degrees": {"group": "Trigonometry", "label": "degrees  radians → °", "arity": 1,
                "tmpl": "degrees({a})", "hint": "convert to degrees"},
    "radians": {"group": "Trigonometry", "label": "radians  ° → radians", "arity": 1,
                "tmpl": "radians({a})", "hint": "convert to radians"},
    # -- Ratios & scaling ---------------------------------------------------
    "pct":   {"group": "Ratios & scaling", "label": "pct  a as % of b", "arity": 2,
              "tmpl": "pct({a}, {b})", "hint": "percent, percentage, share, rate"},
    "scale": {"group": "Ratios & scaling", "label": "scale  0..1 between", "arity": 3,
              "tmpl": "scale({a}, {b}, {c})",
              "hint": "normalise, where does a sit between b and c"},
    "lerp":  {"group": "Ratios & scaling", "label": "lerp  blend a→b by t", "arity": 3,
              "tmpl": "lerp({a}, {b}, {c})", "hint": "interpolate, mix, blend"},
    "clamp": {"group": "Ratios & scaling", "label": "clamp  keep between", "arity": 3,
              "tmpl": "clamp({a}, {b}, {c})", "hint": "limit, cap, bound"},
    # -- Choose -------------------------------------------------------------
    # Node-RED's Change node, said in this engine's grammar: a = the
    # VARIABLE being set (wire it in), b = the value to set it to. Output
    # b; wire the output back into that Variable and it stores. With a
    # condition on "when true" the else-branch is a ITSELF -- the variable
    # KEEPS its current value until the condition sets it, instead of
    # snapping to 0 between conditions.
    "set":   {"group": "Choose", "label": "=  set to", "arity": 2,
              "tmpl": "({b})", "wire_only": True,
              "hint": "set the wired variable (input a) to a value (input "
                      "b); with a condition on 'when true' it only sets "
                      "while that holds and the variable keeps its current "
                      "value otherwise"},
    "iff":   {"group": "Choose", "label": "iff  if / else", "arity": 3,
              "tmpl": "iff({a}, {b}, {c})",
              "hint": "if this then that, conditional, switch between two"},
}

#: The input handles a Formula node shows, by operand position. Three for a
#: fixed-arity operation; a CHAINABLE one may grow up to the full eight.
FORMULA_HANDLES = ("a", "b", "c", "d", "e", "f", "g", "h")

#: Operations that CHAIN: any two-operand operation may take more than two
#: inputs, folding left to right -- and EACH JOIN picks its own operation
#: (`ops` on the node: the operation between input k+1 and the running
#: result), so "a and b or c" is one node. A uniform min/max/avg chain goes
#: n-ary instead (avg folded pairwise would be a different number). The
#: node's `inputs` field (2..8) says how many handles it shows; a
#: fixed-arity operation (iff, clamp, not...) ignores it.
FORMULA_CHAINABLE = frozenset(
    k for k, v in FORMULA_OPS.items() if v["arity"] == 2)
FORMULA_MAX_INPUTS = len(FORMULA_HANDLES)

#: The SERIES input handles a Chart node shows, one per series, in the order
#: they are drawn down the node's left side.
#:
#: The first one is the plain `in` port, not `s1`, and deliberately so: every
#: chart already drawn in the field is wired on `in`, React Flow attaches an
#: id-less edge to it, and renaming it would orphan those wires on a handle
#: that no longer exists. So `in` IS series 1 -- the compile gives it the id
#: "s1" -- and the ports the operator adds afterwards are named for the series
#: they carry.
#:
#: The id is per HANDLE rather than per position, which is what lets a colour
#: survive rewiring: unplug series 2 and plug something else into the same
#: port and it is still "s2", still that colour, on a chart whose legend did
#: not shuffle underneath the reader.
#:
#: Eight, because that is builder.MAX_SERIES -- a chart with more lines than
#: that has no legend anybody can read.
GRAPH_SERIES_HANDLES = ("in", "s2", "s3", "s4", "s5", "s6", "s7", "s8")

#: ...and what the compile CALLS those handles: series 1 is "s1" whether its
#: wire sits on `in` or on a port named for it. This is the id space a stored
#: per-series style is keyed by, so it is derived from the handles rather than
#: typed out again -- one list to grow if a chart ever draws nine.
GRAPH_SERIES_IDS = tuple(f"s{i + 1}" for i in range(len(GRAPH_SERIES_HANDLES)))

#: A Chart's Range is one setting read two ways: the history axis speaks
#: 7d/30d, the recorded-data stores week/month (the single-column chart's
#: Period picker writes the latter). Each map translates the other's word so
#: neither falls back to a default the operator never chose.
_HISTORY_RANGE_OF = {"week": "7d", "month": "30d"}
_QUERY_RANGE_OF = {"7d": "week", "30d": "month"}

#: Where a Card or a Chart reads from when the answer is not simply "the wire".
#:
#:   events   the recorded event store: unwired, every matching event; with a
#:            rule wired in, only what that rule raised (filters.function)
#:   dataset  a table a Data Table node records, NAMED rather than wired --
#:            several cards commonly read one table
#:   metric   the wired rule's published value. Only a migrated Event Card
#:            carries it: it is what "" means on a Card, but the Event Card
#:            resolved a few defaults differently (a verdict reads `active`
#:            whatever the rule), and keeping the word keeps that card's
#:            widget byte-identical. The inspector shows it as "".
#:
#: "" -- the default -- is every other path: a rule, a column, a Query, a
#: whole table, a text constant, whatever is wired in decides.
EVENT_SOURCE_MODES = ("events", "dataset", "metric")

#: The fields that question needs, on both nodes. One dict so the Card and
#: the Chart cannot grow two spellings of it.
_EVENT_SOURCE_DATA = {"source_mode": "", "dataset": "", "field": "",
                      "filters": {}, "conditions": [], "limit": 15}


def asks_recorded(node):
    """True when a dashboard node asks its OWN question of recorded data --
    an Event Card, or a Card/Chart switched to one of EVENT_SOURCE_MODES --
    rather than showing what a wire hands it.

    Such a node is also the one dashboard node that may feed another: wired
    into a Chart, its question becomes one series there."""
    t = (node or {}).get("type")
    if t == "dashboard.events":
        return True
    return (t in ("dashboard.card", "dashboard.graph")
            and ((node or {}).get("data") or {}).get("source_mode")
            in EVENT_SOURCE_MODES)


#: The MATH family: one node per operation instead of one node with a
#: thirty-four-entry picker.
#:
#: The Formula node asked two questions at once -- "which of 34 operations?"
#: and "how many inputs?" -- and its face could not answer either until both
#: were set. Dragging "Average" out of a drawer says both, and the node that
#: lands is already the shape it will keep.
#:
#: Nothing new can be computed. Every entry names operations that already
#: exist in FORMULA_OPS and compiles through the same operand machinery to the
#: same `expr` function; the whitelist in functions.py is untouched. This is a
#: split of one palette entry into eleven, not new capability.
#:
#:   ops    -- what this node may be set to (one entry = no picker at all)
#:   arity  -- fixed input count, or None to chain (2..8, `inputs` on the node)
#:   labels -- what the picker calls each op, in this node's own words: the
#:             Formula's "×  multiply" was written for a list of 34 where the
#:             symbol did the disambiguating
MATH_NODES = {
    "math.arith": {
        "label": "Arithmetic", "badge": "MATH", "group": "Math",
        "ops": ["add", "sub", "mul", "div", "mod"], "arity": None,
        "labels": {"add": "Add", "sub": "Subtract", "mul": "Multiply",
                   "div": "Divide", "mod": "Remainder"},
        "description": "Add, subtract, multiply or divide the wired values."},
    "math.extreme": {
        "deprecated": True,
        "label": "Highest / Lowest", "badge": "MNMX", "group": "Math",
        "ops": ["max", "min"], "arity": None,
        "labels": {"max": "Highest", "min": "Lowest"},
        "description": "The largest or the smallest of the wired values."},
    # Kept apart from Arithmetic on purpose: an average is not another
    # operator in that list, it is a different computation over ALL the
    # inputs at once (folded pairwise it is a different number).
    "math.average": {
        "deprecated": True,
        "label": "Average", "badge": "AVG", "group": "Math",
        "ops": ["avg"], "arity": None,
        "description": "The mean of the wired values."},
    "math.round": {
        "deprecated": True,
        "label": "Round", "badge": "RND", "group": "Math",
        "ops": ["round", "ceil", "floor", "trunc"], "arity": 1,
        "labels": {"round": "Nearest", "ceil": "Up", "floor": "Down",
                   "trunc": "Cut decimals"},
        "description": "Round the wired value."},
    "math.clamp": {
        "deprecated": True,
        "label": "Clamp", "badge": "CLMP", "group": "Math",
        "ops": ["clamp"], "arity": 3, "handles": ["Value", "Min", "Max"],
        "description": "Keep the value between a floor and a ceiling."},
    "math.power": {
        "deprecated": True,
        "label": "Power", "badge": "POW", "group": "Math",
        "ops": ["pow"], "arity": 2, "handles": ["Base", "Exponent"],
        "description": "Raise one value to the power of another."},
    "math.root": {
        "deprecated": True,
        "label": "Root", "badge": "ROOT", "group": "Math",
        "ops": ["sqrt", "cbrt"], "arity": 1,
        "labels": {"sqrt": "Square root", "cbrt": "Cube root"},
        "description": "The square or cube root of the wired value."},
    "math.percent": {
        "deprecated": True,
        "label": "Percent", "badge": "PCT", "group": "Math",
        "ops": ["pct"], "arity": 2, "handles": ["Part", "Whole"],
        "description": "One value as a percentage of another."},
    "math.scale": {
        "deprecated": True,
        "label": "Scale to 0-1", "badge": "SCAL", "group": "Math",
        "ops": ["scale"], "arity": 3, "handles": ["Value", "Min", "Max"],
        "description": "Where the value sits between two ends, as 0 to 1."},
    "math.blend": {
        "deprecated": True,
        "label": "Blend", "badge": "BLND", "group": "Math",
        "ops": ["lerp"], "arity": 3, "handles": ["A", "B", "Amount"],
        "description": "Mix between two values by an amount from 0 to 1."},
    # ONE bundled node, deliberately. Low traffic, and an audience reaching
    # for atan2 is not helped by fifteen drawer entries -- they already know
    # which function they want and expect to pick it by name.
    "math.advanced": {
        "deprecated": True,
        "label": "Advanced Math", "badge": "FNX", "group": "Math",
        "ops": ["sin", "cos", "tan", "asin", "acos", "atan", "atan2",
                "ln", "log", "log10", "log2", "exp", "degrees", "radians",
                "hypot"],
        "arity": "op",              # whatever the chosen operation takes
        "description": "Trigonometry, logarithms and growth."},
    # -- Logic --------------------------------------------------------------
    "math.andor": {
        "label": "Logic", "badge": "LOGIC", "group": "Logic",
        "ops": ["and", "or", "not"], "arity": None,
        # Only And and Or take a growing list of inputs; Not takes the one.
        "chain_ops": ["and", "or"],
        "labels": {"and": "And", "or": "Or", "not": "Not"},
        "description": "And: 1 when every wired answer is true. Or: 1 when "
                       "any is. Not: flips a 0/1 answer over."},
    "math.not": {
        "deprecated": True,
        "label": "Not", "badge": "NOT", "group": "Logic",
        "ops": ["not"], "arity": 1,
        "description": "Flips a 0/1 answer over."},
    # -- State --------------------------------------------------------------
    # Pulled out of the Formula entirely: "set to" was an operation in a list
    # of arithmetic, and it is not arithmetic -- it WRITES somewhere. Its own
    # node says so on the canvas.
    "math.setvar": {
        "label": "Change Variable", "badge": "SET", "group": "State",
        "ops": ["set"], "arity": 2, "handles": ["Variable", "Value"],
        # WHAT KIND of value is being stored. The box beside "Set it to" only
        # ever took a number, so "set the shift name to night" could not be
        # drawn at all and a count that must stay whole (12 people, never
        # 12.4) had to be rounded by hand somewhere downstream.
        #   decimal -- the value as it is, fractions kept (what this node
        #              always did, so it stays the default and no saved
        #              graph changes meaning).
        #   number  -- rounded to a whole number, wired input included: the
        #              type says what the variable IS, not merely what may
        #              be typed into one box.
        #   text    -- a string. Named and visible, but the expression engine
        #              is numeric end to end (functions.py), so it publishes
        #              nothing -- exactly like a text constant. Warned about
        #              at compile time rather than failing the graph.
        "value_types": ["decimal", "number", "text"],
        "description": "Store a value in a Variable. Wire the Variable into "
                       "the first input and what to store into the second."},
}


def math_ops(node_type):
    """The operations a Math node may be set to, or () for anything else."""
    return tuple((MATH_NODES.get(node_type) or {}).get("ops") or ())


def math_op(node):
    """Which FORMULA_OPS operation this Math node computes.

    Clamped to the node's own list: a stale `op` left by a retype (or a
    hand-edited graph) can never make an Average node subtract.
    """
    ops = math_ops((node or {}).get("type"))
    if not ops:
        return None
    want = str(((node or {}).get("data") or {}).get("op") or "")
    return want if want in ops else ops[0]

#: The Counter's LINE port: a line wired here makes the Counter count
#: crossings (it compiles as analytics.entry_exit). Mirrors COUNTER_LINE_PORT
#: in frontend/src/workflow/registry.js.
COUNTER_LINE_PORT = "line"

#: The single source of truth for node types. Served to the frontend
#: (GET /api/workflow) so the palette, inspector forms and client-side edge
#: validation never drift from what the compiler accepts.
#:
#: rank drives edge validation: an edge may only go rank N -> rank N+1
#: (plus filter->filter chaining and analytics->expr references).
REGISTRY = {
    # A note on the canvas. It compiles to nothing and accepts no edges -- it
    # exists because a graph that took an afternoon to reason about is
    # unreadable a month later without the reasoning written next to it.
    # -- common ------------------------------------------------------------
    # INJECT: a button on the canvas that fires the pipeline now, and
    # optionally once at start and on a repeat. Filed FIRST in Common, because
    # "push a value in by hand" is the first thing reached for while building
    # and testing a graph -- and it is the one Common node that does reach the
    # engine: its WIRING category is variable, like the Schedule's, so it
    # feeds a formula, a card, a Compare or an output with no new edge rules,
    # and it arms an output by itself (fired, like the Schedule's due).
    "common.inject": {
        "palette": {"cat": "common", "group": ""},
        "category": "variable", "rank": 3, "label": "Inject",
        "badge": "INJ",
        "description": "A button that injects a value into the flow: a "
                       "string, number, true/false, JSON or the current "
                       "time. Press it on the canvas, or let it fire once "
                       "at start and repeat on an interval. Wired into an "
                       "output, each inject fires it once.",
        "fn_type": "inject", "zone_kind": None,
        "metrics": ["value", "fired", "runs"],
        # ptype/payload are the typed input; repeat "interval" with a number
        # and a unit is written down as interval_s at compile time, so the
        # engine has one number to read.
        "data": {"fnId": "", "name": "Inject", "ptype": "timestamp",
                 "payload": "", "topic": "", "once": False,
                 "once_delay_s": 0.1, "repeat": "none", "every": 1,
                 "every_unit": "seconds",
                 # A repeat starts with the app (Start/Stop on its button
                 # take over from there); off, it waits for Start.
                 "autostart": True,
                 # How many frames a Detection Stream in Snapshot mode detects
                 # per inject. Meaningful only while this Inject is wired into
                 # such a stream, and the canvas shows it only then.
                 "frames": 1}},
    # Canvas furniture: things that help you READ and WIRE a graph rather than
    # compute anything. None of them reach the engine.
    "note.comment": {
        "category": "common", "rank": -1, "label": "Comment",
        "badge": "NOTE",
        "description": "A note on the canvas. Explains the graph; changes nothing.",
        # `layer`: BEHIND the nodes it explains, or in front of them. Behind by
        # default (and for every note saved before this existed): a note is
        # background for the part of the graph it describes, and a big one
        # drawn over three nodes used to swallow their clicks. In front is
        # for a note meant to cover something -- a "do not wire this" label.
        "data": {"text": "", "width": 220, "height": 120, "color": "",
                 "layer": "back"}},
    # A wire that jumps instead of being drawn. On a graph with three cameras
    # and a dozen rules, the line from a zone on the left to an output on the
    # right crosses everything in between; a named jump says the same thing
    # without the crossing. Resolved before compilation (resolve_links), so
    # the compiler only ever sees the real connection.
    "common.link_out": {
        "category": "common", "rank": 9, "label": "Link out",
        "badge": "LOUT",
        "description": "Send onward to the Link in with the same name - a "
                       "wire that jumps instead of crossing the canvas.",
        "data": {"link": ""}},
    "common.link_in": {
        "category": "common", "rank": 0, "label": "Link in",
        "badge": "LIN",
        "description": "Receives from every Link out with this name.",
        "data": {"link": ""}},
    # A FUNCTION, the way a canvas can show one: a Start that hands out the
    # parameters, an End that takes what comes back, and a Call for every
    # place it is used. The body is whatever sits BETWEEN the Start and the
    # End on the main canvas -- which is the whole point of doing it this way
    # rather than in a nested editor: every node in the palette works inside
    # a function, with its own inspector, its own ports and its own wiring
    # rules, because it is the same canvas it always was.
    #
    # Membership is REACHABILITY, not geometry: a node is in the body when it
    # is reachable from the Start AND reaches the End. Dragging a node an inch
    # to the left must never change what a function does.
    #
    # None of the three reaches the compiler. resolve_functions() splices a
    # fresh copy of the body in at every Call before anything is validated,
    # exactly as resolve_links() replaces a jump with the wire it stood for --
    # so the compiler, the validator and every artifact below know nothing
    # about functions at all.
    "function.start": {
        "palette": {"cat": "logic", "group": "Function"},
        "category": "common", "rank": 0, "label": "Function Start",
        "badge": "FN(",
        "description": "Names a function and hands out its parameters. "
                       "Everything wired from here to the matching Function "
                       "End is its body.",
        "data": {"fn": "", "params": []}},
    "function.end": {
        "palette": {"cat": "logic", "group": "Function"},
        "category": "common", "rank": 9, "label": "Function End",
        "badge": "FN)",
        "description": "What the function gives back. Name one port per "
                       "value it returns, or leave the list empty for a "
                       "function that returns a single thing.",
        "data": {"fn": "", "returns": []}},
    "function.call": {
        "palette": {"cat": "logic", "group": "Function"},
        "category": "common", "rank": 3, "label": "Call Function",
        "badge": "CALL",
        "description": "Run a function here. One input per parameter, and "
                       "the body runs as its own copy for every Call.",
        "data": {"fn": ""}},
    # Watch one value while building. Publishes nothing and changes nothing --
    # it is a probe, and the Debug panel is where it reads out.
    # DEPRECATED: the Debug button on the toolbar already shows every value on
    # the canvas at once, so a node whose whole job was to watch ONE of them
    # was a worse version of a thing already there. Existing ones keep working
    # (they compile to nothing, as they always did).
    #
    # BACK IN THE PALETTE. The retirement above assumed the probe and the
    # drawer answer the same question, and they do not. The drawer lists what
    # the graph PUBLISHES, so an operator reading it has to know which stat
    # key belongs to the node in front of them -- and for a Set Variable or an
    # Email that key is not the node's own, or does not exist at all. The
    # probe answers "what is coming down THIS wire", which is the question
    # somebody dragging a wire actually has, and it answers it on the canvas
    # where the wire is. It still compiles to nothing (category "common" is
    # stripped before validation), so nothing it is wired to can change what
    # runs.
    "common.debug": {
        "category": "common", "rank": 9, "label": "Debug",
        "badge": "DBG",
        "description": "Shows whatever is wired into it, live, on the canvas: "
                       "a rule's number, a variable's word, or whether an "
                       "Email, a Telegram or a board output actually fired. "
                       "Publishes nothing and changes nothing.",
        "data": {"metric": ""}},
    "source.detection": {
        "category": "source", "rank": 0, "label": "Detection Stream",
        "badge": "DET",
        "description": "The AI model's detected objects, every frame. With "
                       "several models enabled, pick one to use only ITS "
                       "detections; blank means all models. On a multi-camera "
                       "system, pick the camera this branch reads; blank means "
                       "the system's first camera.",
        # `capture` is "video" (every frame, as always) or "snapshot": the
        # stream then takes a SNAP input and detects only the few frames a
        # trigger asks for. Absent means video, so every graph saved before
        # this compiles key for key.
        "data": {"model": "", "camera": "", "capture": "video",
                 # What the camera does between snaps: auto (close it, unless
                 # it is a network stream), close, or keep (read slowly).
                 "camera_idle": "auto"}},
    "filter.class": {
        "palette": {"cat": "filter", "group": "Class"},
        "category": "filter", "rank": 1, "label": "Class Filter",
        "badge": "FLT",
        "description": "Keep only the chosen object classes (person, car, ...).",
        "data": {"classes": []}},
    # The ON / OFF point of a Detection Stream or a Class Filter, as the
    # compiler sees it. Never drawn and never in the palette (`hidden`): the
    # canvas draws the two points as handles on those nodes, and
    # state_points.split() turns each wire from one into a wire from this
    # node before anything is validated. Its WIRING category is the Inject's
    # (variable, rank 3), so it may feed exactly what an Inject feeds, and its
    # function is a clock like an Inject's (CLOCK_FN_METRIC).
    "state.watch": {
        "category": "variable", "rank": 3, "label": "ON/OFF connection",
        "badge": "ONOFF", "hidden": True,
        "description": "Fires when the Detection Stream's AI or the Class "
                       "Filter is switched ON (or OFF), and once at start for "
                       "the state it finds. Its value is 1 while it matches.",
        "fn_type": "state_watch", "zone_kind": None,
        "metrics": ["value", "fired", "runs"],
        "data": {"fnId": "", "name": "", "watch": "ai", "want": "on"}},
    # Lives in INPUT beside the two filters for the same reason the Zone
    # Filter does: a Class Filter says WHICH objects, a Zone Filter says
    # WHERE, and this says what the PICTURE looks like before either question
    # can be asked. All three are things you say before any rule runs.
    #
    # Its WIRING category is "filter" as well, which is what earns it the
    # place in the chain it needs for nothing: source -> filter -> anything is
    # already legal, so a Pre-process drops in front of a Class Filter, a Zone
    # or a rule without a single new pair in the matrix. What IS new is the
    # per-type rule in edge_allowed -- only a Detection Stream may feed one,
    # because the node adjusts the frames of ONE camera and the stream is what
    # names that camera.
    #
    # PER-STREAM, not per-graph: the app applies the chain per camera, so the
    # node belongs to the stream it hangs off. Two of them on one stream is a
    # compile error rather than a merge -- see the compile pass for why.
    "filter.preprocess": {
        "palette": {"cat": "filter", "group": "Picture"},
        "category": "filter", "rank": 1, "label": "Pre-process",
        "badge": "PRE",
        "description": "Adjust the picture before the detector sees it: "
                       "grayscale, exposure, sharpness, blur, edges.",
        "data": {"steps": [], "preview": False}},
    # Lives in INPUT, beside the Class Filter, because that is what it is: a
    # Class Filter narrows to WHICH objects, this narrows to WHERE. Both are
    # things you say before any rule runs, and keeping them in one drawer is
    # how "restrict this to people, inside that area" reads as one thought.
    #
    # Its WIRING category stays "zone" -- that is what edge_allowed reasons
    # about, and moving it would change which nodes may legally meet, on
    # every graph already saved. `palette` decides where it is offered and
    # how it is drawn; `category` decides what it may connect to. Only the
    # first of those is a presentation choice.
    "zone.ref": {
        "palette": {"cat": "filter", "group": "Zone"},
        "category": "zone", "rank": 2, "label": "Zone Filter",
        "badge": "ZONE",
        "description": "A zone from the Zone Manager; narrows a rule to the "
                       "objects inside that area. Wire it to any rule that "
                       "measures a place.",
        "data": {"zone": ""}},
    # DEPRECATED: folded into analytics.violations (the Zone Counter), where
    # it is the "realtime" mode -- "how many in the zone NOW" and "how many
    # have entered" are the same question over a different span, exactly the
    # split the Frame Counter already models as a mode. Two nodes named
    # "Zone Functionality" and "Zone Counter" answered it as two things to
    # drag, and reading which was which took the description. Kept so an old
    # canvas loads; migrate_merged_nodes rewrites it on open.
    "analytics.occupancy": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Zone Functionality",
        "badge": "ZFN",
        "deprecated": True,
        "description": "How many filtered objects are inside the zone right now.",
        "fn_type": "zone_count", "zone_kind": "area", "metrics": ["count"],
        "data": {"fnId": "", "name": "Occupancy"}},
    # Whole-frame counting. No zone: the question is "what is on camera", not
    # "what is in that place", and making an operator draw a zone around the
    # entire view to ask it was the wrong shape.
    #
    # ONE node, two modes. They were two nodes for a day and it was already
    # the wrong shape: "how many now" and "how many altogether" are the same
    # question over a different span, so choosing between them is a setting,
    # not a different thing to drag. Same reasoning as the Graph node, whose
    # chart type is data rather than four node types.
    # -- "is this object here?" --------------------------------------------
    #
    # A counter answers HOW MANY; these two answer WHETHER, which is what an
    # alert, a snapshot or a Data Table row is usually waiting for.
    #
    # WHAT to watch for comes from Class Filters wired in front, the same way
    # every other rule on the canvas names its objects -- and more than one may
    # feed a watch, their classes adding together, so "a person OR a forklift
    # is in the bay" is two filters into one node rather than two nodes.
    # ONE object watch. "Object Detection" watched the frame and "Object in
    # Zone" watched an area -- the same rule, which has always read its zone
    # from the WIRING (see the object_detect compile branch: it accepts a zone
    # on either node). Two entries said otherwise.
    #
    # PRESENCE, and nothing else. It published `count` and `arrived` as well,
    # which made it a third counter competing with the two that count -- and
    # the one whose answer is a 0/1 flag is the one an alarm, a light or a
    # branch actually wants. Counting is the Counter's job now. Both keys are
    # still published by the engine, so a card already bound to one keeps
    # reading it; they are simply no longer offered.
    #
    # The per-class ports are the point of the node: one port per class the
    # Class Filters name, so "is there a forklift AND a person" is two wires
    # rather than a metric picker.
    "analytics.object": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Object Detection", "badge": "AI",
        "description": "Is one of the objects a Class Filter names HERE? Wire "
                       "a Zone to watch that area, or leave the port empty to "
                       "watch the whole view. One port per class, so each "
                       "answers on its own.",
        "fn_type": "object_detect", "zone_kind": None, "zone_optional": "area",
        "handles": [{"id": "in", "label": "In"},
                    {"id": "zone", "label": "Zone (optional)"}],
        "metrics": ["found"],
        # OID: also hand out WHICH object arrived (its track id), for a Data
        # Table row per object -- the switch the counters already carry.
        "data": {"fnId": "", "name": "Object Detection", "OID": False,
                 "enter_after_s": 0, "leave_after_s": 0}},
    # GOOD / NG for a Snapshot Detection Stream (and, on a Video stream, live).
    # The same watch as Object Detection with one answer: 0 = GOOD (nothing the
    # Class Filter names was found), 1 = NG (something was). On a camera set to
    # Snapshot the answer belongs to the SNAP -- NG when any of its frames found
    # something -- and is held until the next snap; nothing is published before
    # the first snap, so a card reads NO DATA rather than a GOOD nobody checked.
    # Wired to a Verdict Card it reads GOOD / NG by default.
    "analytics.anomaly": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Anomaly", "badge": "NG",
        "description": "GOOD or NG for each snapshot: NG when the snapshot found "
                       "any object the Class Filter names (a defect, a person "
                       "where nobody should be), GOOD when it found none. Wire "
                       "the Verdict port to a Verdict Card. Works with a "
                       "Detection Stream set to Snapshot; on a Video stream it "
                       "follows the live view.",
        "fn_type": "object_detect", "zone_kind": None, "zone_optional": "area",
        "handles": [{"id": "in", "label": "In"},
                    {"id": "zone", "label": "Zone (optional)"}],
        "metrics": ["verdict"],
        "data": {"fnId": "", "name": "Anomaly", "enter_after_s": 0,
                 "leave_after_s": 0}},
    "analytics.object_frame": {
        "palette": {"cat": "zone", "group": "Objects"},
        # DEPRECATED: merged into analytics.object, whose zone port is
        # optional. Still compiles, and keeps publishing count/arrived.
        "deprecated": True,
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Object Detection",
        "badge": "AI",
        "description": "Watches the whole camera view for the objects a Class "
                       "Filter names (wire in as many as you like - they add "
                       "together). `found` is 1 while at least one is on "
                       "camera and 0 when none are, `count` is how many, and "
                       "`arrived` counts the ones that appeared this moment "
                       "- wire it to a Condition, a Snapshot or a Data Table.",
        "fn_type": "object_detect", "zone_kind": None,
        "metrics": ["found", "count", "arrived"],
        "data": {"fnId": "", "name": "Object in frame"}},
    "analytics.object_zone": {
        "palette": {"cat": "zone", "group": "Zones"},
        # DEPRECATED: the same watch with a zone wired in -- which is what the
        # merged node does when its optional port is used.
        "deprecated": True,
        "category": "analytics", "group": "Zones", "rank": 3,
        "label": "Object in Zone",
        "badge": "OBZ",
        "description": "The same watch, narrowed to one area zone: `found` is "
                       "1 while an object a Class Filter names is inside it, "
                       "`count` is how many are, and `arrived` counts the ones "
                       "that just entered.",
        "fn_type": "object_detect", "zone_kind": "area",
        "metrics": ["found", "count", "arrived"],
        "data": {"fnId": "", "name": "Object in zone"}},
    # ONE counter. The Object Counter counted the frame, the Zone Counter
    # counted an area, and they were the same rule over a different surface --
    # same modes, same timing settings, same OID output, two palette entries
    # and two inspectors to keep in step. WHERE it counts is now read off the
    # wiring: a zone on the optional port narrows it, nothing there counts the
    # whole frame, and the canvas SHOWS which it is instead of hiding it in a
    # node's name.
    #
    # No limit field. "Only count past four inside" was a condition baked into
    # a counter -- the one thing the Branch family exists to stop -- so a
    # counter counts and a branch decides. A saved node that used one keeps the
    # deprecated Zone Counter, which still compiles it (migrate_counter_merge).
    #
    # ...and the Line Counter joined it on the CANVAS (Tok Aba, 2026-10-13):
    # one node called Counter whose wiring says what it counts -- a Zone on
    # the Zone port counts inside it, a Line on the Line port counts
    # crossings, nothing counts the whole frame. On DISK they stay two types:
    # the canvas turns this node into analytics.entry_exit the moment a line
    # lands on the Line port, and back when the line is taken off
    # (frontend/src/workflow/registry.js counterToLine / lineToCounter). That
    # keeps every saved graph compiling exactly as before -- no old node is
    # rewritten -- and lower_counter_lines() is the backstop for a graph
    # built elsewhere with a line on this node's Line port.
    "analytics.counter": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Counter", "badge": "CNT",
        # "line counter" and "zone counter" are spelled out so a palette
        # search for the node somebody remembers still lands here.
        "description": "Counts objects. Wire a Zone to count inside it (zone "
                       "counter), a Line to count what crosses it (line "
                       "counter), or neither to count the whole view.",
        # The DEFAULT rule, for a counter with no zone wired. With one wired
        # the compiler swaps to the zone rule of the same shape -- see
        # ZONE_RULE_OF, which is what makes the zone port optional.
        "fn_type": "frame_count", "zone_kind": None,
        # A zone is ACCEPTED here and refused on the whole-frame counters. The
        # kind it takes when one is wired, for the client's "needs an area"
        # check.
        "zone_optional": "area",
        # The Line port is where a line goes to be COUNTED ACROSS. A line on
        # the Zone port is still read as a thin place ("at the line"), which
        # is what every Counter saved that way has always meant.
        "handles": [{"id": "in", "label": "In"},
                    {"id": "zone", "label": "Zone"},
                    {"id": COUNTER_LINE_PORT, "label": "Line"}],
        "metrics": ["count"],
        "modes": {
            "realtime": {"label": "In zone now", "fn_type": "frame_count",
                         "metrics": ["count"]},
            # Kept under the old key so every saved counter means exactly what
            # it meant: `mode` is stored, and renaming the value would re-decide
            # what a node counts on upgrade.
            "distinct": {"label": "Total entered", "fn_type": "frame_unique",
                         "metrics": ["total", "new", "today", "week", "month"]},
        },
        "data": {"fnId": "", "name": "Counter", "mode": "realtime",
                 "OID": False, "enter_after_s": 0, "leave_after_s": 0}},
    "analytics.frame": {
        "palette": {"cat": "zone", "group": "Objects"},
        # DEPRECATED: merged into analytics.counter, which is this node with
        # the zone port made optional. Still compiles; out of the palette.
        "deprecated": True,
        "category": "analytics", "group": "Frames", "rank": 3, "label": "Object Counter",
        "badge": "CNT",
        "description": "Counts objects on camera. Choose what it counts: how "
                       "many are in view right now, or how many distinct ones "
                       "have been seen (once each, by OID - tallied per day "
                       "on disk, so today / this week / this month survive a "
                       "restart).",
        "fn_type": "frame_count", "zone_kind": None, "metrics": ["count"],
        # Each mode is a different compiled rule, so it carries its own
        # function type and its own metrics. compile reads these; the palette
        # and the inspector show one node.
        # The span, named the way an operator says it. All but the first are
        # the same rule (distinct objects, counted once by OID); they differ
        # in which of its numbers the node PUBLISHES FIRST, so a card wired to
        # it shows the period that was picked without configuring anything.
        # TWO modes, because there are two measurements. Everything the picker
        # used to offer beyond them -- daily, weekly, monthly -- was the same
        # rule publishing a different number first, which is a question about
        # what to SHOW. That belongs to the card's Period setting, not here:
        # the rule tallies all of them either way, and putting the choice on
        # the counter meant a second counter to see the same count over a
        # different week.
        "modes": {
            "realtime": {"label": "in view now", "fn_type": "frame_count",
                         "metrics": ["count"]},
            # Hidden from the picker, still compiled -- same rule as the Zone
            # Counter's distinct mode: existing graphs keep it, new counts of
            # distinct objects come from the supported paths (Entry/Exit, a
            # Data Table row per object).
            "distinct": {"label": "distinct objects (by OID)", "hidden": True,
                         "fn_type": "frame_unique",
                         "metrics": ["total", "new", "today", "week", "month"]},
        },
        # `OID` adds an output that carries WHICH object was counted, for a
        # Data Table column to record. Off by default: most counters want the
        # number, and a port that is always there is a port always in the way.
        "data": {"fnId": "", "name": "In frame", "mode": "realtime",
                 "OID": False, "enter_after_s": 0, "leave_after_s": 0}},
    # The two nodes the one above replaced. Kept so a graph saved with them
    # still compiles and still runs; hidden from the palette so nobody builds
    # anything new on them. migrate_frame_nodes() rewrites them in place.
    "analytics.frame_count": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3, "label": "In Frame (now)",
        "badge": "IFN",
        "deprecated": True,
        "description": "How many filtered objects are on camera right now.",
        "fn_type": "frame_count", "zone_kind": None, "metrics": ["count"],
        "data": {"fnId": "", "name": "In frame now"}},
    "analytics.frame_total": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3, "label": "In Frame Total",
        "badge": "IFT",
        "deprecated": True,
        "description": "Distinct objects seen on camera, counted once each by "
                       "OID.",
        "fn_type": "frame_unique", "zone_kind": None,
        "metrics": ["total", "new", "today", "week", "month"],
        "data": {"fnId": "", "name": "In frame total"}},
    "analytics.dwell": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3,
        "label": "Cycle Time",
        "badge": "CYC",
        "description": "Time, measured three ways: how long objects stay "
                       "(Time present), a budget each arrival spends "
                       "(Countdown), or one clock that runs while the place is "
                       "occupied at all (Occupied clock). Wire a Zone to "
                       "measure inside it; Time present and Countdown also "
                       "work on the whole view, and Occupied clock needs a "
                       "zone - there is no such thing as an unoccupied frame.",
        "fn_type": "zone_dwell", "zone_kind": None, "zone_optional": "area",
        "handles": [{"id": "in", "label": "In"},
                    {"id": "zone", "label": "Zone (optional)"}],
        # TIMES only, and live_s first. This node exists to measure a CLOCK --
        # anything else it published was a second way to ask a question another
        # node already answers, and being FIRST made it the default an Alert
        # bound to. See OUTPUTS["zone_dwell"] in services/functions.py for what
        # that cost. live_s is the one that answers "how long has this object
        # been in there" while it is still standing there; max_s keeps the
        # record of someone long gone, so a threshold on it never re-arms.
        "metrics": ["live_s", "avg_s", "max_s"],
        # THREE modes, because there are three measurements. It offered five:
        # "Zone Dwell Time", "OID Timer" and "OID Measure Time" were one rule
        # publishing the same three numbers, differing only in what the node
        # ADDED -- a default trigger, or an event per completed stay. Those are
        # a checkbox ("track individual objects") and a delivery choice, not
        # three things to choose between before knowing they are the same one.
        # migrate_cycle_modes folds the old three into `present` and turns what
        # they added into the settings that say it.
        #
        # `present` stays FIRST, so a mode-less node still compiles to the
        # dwell rule with the same metrics -- metrics[0] is what a widget or an
        # unlabelled wire binds to, and moving it would re-aim them.
        "modes": {
            "present": {"label": "Time present",
                        "metrics": ["live_s", "avg_s", "max_s"]},
            "countdown": {"label": "Countdown",
                          "fn_type": "zone_countdown",
                          "metrics": ["remaining_s", "used_s", "entries"]},
            "occupancy": {"label": "Occupied clock",
                          "fn_type": "zone_occupancy",
                          "metrics": ["occupied_s", "last_s", "total_s",
                                      "sessions", "active"]},
            # The three it replaced, still loadable so a graph that reaches the
            # compiler before the migration does still means what it meant.
            "dwell": {"label": "Time present", "hidden": True,
                      "metrics": ["live_s", "avg_s", "max_s"]},
            "timer": {"label": "Time present · per object", "hidden": True,
                      "metrics": ["live_s", "avg_s", "max_s"]},
            "stamps": {"label": "Time present · record each stay",
                       "hidden": True,
                       "metrics": ["live_s", "avg_s", "max_s"]},
        },
        # `OID` here means "tell me WHO": every COMPLETED stay is stamped as
        # an event carrying the OID, the seconds and the entry/exit times --
        # the record a Data Table needs to save cycle time per object. The
        # aggregate numbers above cannot carry identity (one value per system,
        # not per object), so the per-stay event is the only honest carrier.
        # `delivery` is what the old "OID Measure Time" mode was: with objects
        # tracked, "live" hands out the clock of the stay running now, "once"
        # stamps each COMPLETED stay as an event carrying the id, the seconds
        # and the entry/exit times -- the record a Data Table row is written
        # from. Live is the default (Standing Rule 7).
        "data": {"fnId": "", "name": "Cycle time", "OID": False,
                 "mode": "present", "delivery": "live",
                 "start_s": 60, "deduct_s": 1}},
    # DEPRECATED: this was the Zone Counter with no minimum -- same rule,
    # same metrics. Zone Counter with limit 0 counts every entrant once.
    "analytics.visitors": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Unique Visitors",
        "badge": "UNQ",
        "deprecated": True,
        "description": "Distinct tracked objects that entered the zone - each counts once, no line needed.",
        "fn_type": "zone_unique", "zone_kind": "area", "metrics": ["total"],
        "data": {"fnId": "", "name": "Unique visitors"}},
    # The Counter with a line wired: what analytics.counter becomes on the
    # canvas when a line lands on its Line port (see there). `hidden` takes it
    # out of the palette -- the Counter is the one thing to drag -- and keeps
    # it fully supported: every Line Counter already saved loads, draws and
    # compiles as it did. Its one input besides In is the LINE, and the handle
    # id stays `zone` because that is what every saved wire holds.
    "analytics.entry_exit": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3,
        "label": "Counter", "hidden": True,
        "badge": "CNT",
        "handles": [{"id": "in", "label": "In"},
                    {"id": "zone", "label": "Line"}],
        "description": "A Counter with a Line wired: counts objects crossing "
                       "the line. Say which direction "
                       "counts - in, out, or both. A line drawn with no "
                       "facing has no in and out, and publishes crossings "
                       "only.",
        "fn_type": "line_count", "zone_kind": "line",
        "metrics": ["in", "out", "net", "cross"],
        # A LIVE counter's In/Out are the objects on the line right now, so
        # the running tally rides beside them as ports of their own: total
        # in, total out, inside (in - out) and crossings -- so a card or a
        # Math node reads the total straight off the node instead of
        # summing a table. Live only: on a running-total counter In and
        # Out ARE the totals.
        "live_metrics": ["total_in", "total_out", "total_net", "total_cross"],
        # WHICH WAY counts. The rule tallies every direction either way -- this
        # says which of them the node leads with, and therefore which ports it
        # offers and what an unlabelled wire (or an alert with no metric set)
        # means. "in" is first, so a counter saved before this existed keeps
        # publishing `in` first, exactly as it did.
        #
        # `cross` is in every mode: it is the only tally a line with no facing
        # can honestly publish, and on a faced line it is in + out.
        "modes": {
            "in": {"label": "In only", "metrics": ["in", "cross"]},
            "out": {"label": "Out only", "metrics": ["out", "cross"]},
            "both": {"label": "Both directions",
                     "metrics": ["in", "out", "net", "cross"]},
        },
        # `OID` adds the port carrying WHICH object crossed, for a Data
        # Table column to record -- the same switch every other counter
        # that knows its objects has. `distinct` is the other half of the
        # question: with it on, each object is counted ONCE per direction,
        # so somebody pacing back and forth through a doorway all morning
        # is one visitor in and one visitor out rather than forty.
        # `momentary` -- LIVE, and the default for a new counter: the count
        # is how many crossed on this frame (1 in, 1 out, 2 together), 0
        # again the next. That is the shape a Data Table logs row by row
        # and sums for a total, instead of logging a running total it can
        # only subtract across rows. Saved graphs carry their own value,
        # so a counter drawn as a running total stays one.
        "data": {"fnId": "", "name": "Line counter", "mode": "in",
                 "OID": False, "distinct": False, "momentary": True,
                 "totals": False}},
    "analytics.travel": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Travel Time (A → B)",
        "badge": "TRVL",
        "description": "Line A to line B, per object: how long it took, or "
                       "how fast it was going.",
        "fn_type": "line_travel", "zone_kind": "line",
        # Named ports: which line is the way IN and which is the way OUT is
        # the whole meaning of the measurement, and "line_a" said neither.
        # The handle ids are untouched -- every saved wire holds them.
        "handles": [{"id": "line_a", "label": "Entrance line"},
                    {"id": "line_b", "label": "Exit line"}],
        # All of them, always. The old "time / speed" mode changed nothing
        # but the ORDER of this list -- both numbers were published either
        # way -- so it was a setting that looked like a measurement choice
        # and answered nothing. Speed is the real choice, and it is a real
        # one: it needs a distance nobody can guess, so it is opt-in below.
        "metrics": ["trips", "last_s", "avg_s", "max_s", "min_s",
                    "last_speed", "avg_speed"],
        # The two old modes are HIDDEN, not deleted. A node set to one of them
        # published a different metric FIRST, and first is what a card with no
        # metric picked and what an alert with none bound to -- so dropping
        # them would silently re-aim every one of those. They are off the
        # picker; a node that has one keeps it and keeps meaning it.
        "modes": {
            "all": {"label": "every trip number",
                    "metrics": ["trips", "last_s", "avg_s", "max_s", "min_s",
                                "last_speed", "avg_speed"]},
            "time": {"label": "time taken", "hidden": True,
                     "metrics": ["last_s", "avg_s", "max_s", "min_s", "trips",
                                 "last_speed", "avg_speed"]},
            "speed": {"label": "speed", "hidden": True,
                      "metrics": ["last_speed", "avg_speed", "trips", "last_s",
                                  "avg_s", "max_s", "min_s"]},
        },
        # `speed` off means no distance question and no speed ports; on, the
        # unit says which. Kept OFF for a new node -- a speed with a made-up
        # distance is a confident wrong answer.
        "data": {"fnId": "", "name": "Travel time", "speed": "off",
                 "distance_m": 10, "speed_unit": "kmh"}},
    # DEPRECATED: Travel Time with its metrics in a different order. It is a
    # mode on that node now.
    "analytics.speed": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Speed (A → B)",
        "badge": "SPD",
        "deprecated": True,
        "description": "Speed between two lines, from the real-world distance.",
        "fn_type": "line_travel", "zone_kind": "line",
        "handles": ["line_a", "line_b"],
        "metrics": ["last_speed", "avg_speed", "trips", "last_s"],
        "data": {"fnId": "", "name": "Speed",
                 "distance_m": 10, "speed_unit": "kmh"}},
    # Travel time's sibling: the two ends are a ZONE OR A LINE, in any mix, so
    # "how long from the drop-off area to the pack station" is one node instead
    # of a formula over two rules. No distance/speed here -- an area has no
    # single point to measure from, so the honest answer is time only.
    # DEPRECATED: this was A-to-B timing, which is what Travel Time already
    # is -- two entries for one idea, and the name suggested it answered "how
    # long has this person been in the zone", which is Time in Zone. Existing
    # graphs keep compiling and running it; it is out of the palette so the
    # question has one obvious answer: A to B -> Travel Time, time spent in a
    # place -> Time in Zone.
    "analytics.cycle_time": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Cycle Time (A → B)",
        "badge": "CYAB",
        "deprecated": True,
        "description": "Time from one place to another, per object. Use "
                       "Travel Time for A → B, or Time in Zone for how long "
                       "each object stays somewhere.",
        "fn_type": "zone_travel", "zone_kind": None,
        "handles": ["start", "end"],
        "metrics": ["trips", "last_s", "avg_s"],
        "data": {"fnId": "", "name": "Cycle time"}},
    # DEPRECATED: a Crowd Limit was Zone Occupancy with a baked-in alert --
    # exactly the two-places-for-one-condition mess the Alert node exists to
    # avoid. standardize_alerts() rewrites existing ones to Occupancy + Alert;
    # the entry stays so an old export still imports, but the palette hides it.
    "analytics.crowd": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Crowd Limit",
        "badge": "CRWD",
        "deprecated": True,
        "description": "Counts objects in the zone and raises a violation/alert "
                       "when more than the limit are inside.",
        "fn_type": "zone_count", "zone_kind": "area", "metrics": ["count"],
        "data": {"fnId": "", "name": "Crowd limit", "limit": 4,
                 "severity": "warn", "message": "", "cooldown_s": 30}},
    # ONE zone counter, two modes -- the same shape the Frame Counter has.
    # "How many are in the zone right now" (was its own node, Zone
    # Functionality) and "how many distinct objects have entered" are the
    # same question over a different span: a setting, not two things to drag.
    "analytics.violations": {
        "palette": {"cat": "zone", "group": "Zones"},
        "category": "analytics", "group": "Zones", "rank": 3, "label": "Zone Counter",
        "badge": "ZN",
        # DEPRECATED: merged into analytics.counter, whose zone port is
        # optional. A node carrying a `limit` stays here on purpose --
        # the merged Counter has no such field, and rewriting one would
        # silently change what it counts (migrate_counter_merge).
        "deprecated": True,
        "description": "Counts objects in the zone. Choose what it counts: "
                       "how many are inside right now, or each entrant once "
                       "by OID (limit 0 = everything that enters counts; "
                       "higher = only past that occupancy). Wire a Condition "
                       "after it to raise the alarm.",
        "fn_type": "zone_unique", "zone_kind": "area", "metrics": ["total", "new"],
        # Same order and same default as the Frame Counter -- realtime first
        # -- so the two counters read as the same node over a different
        # surface. That makes the first-key fallback WRONG for a Zone Counter
        # saved before modes existed (those are distinct counters), so
        # migrate_merged_nodes stamps mode="distinct" onto any without one;
        # after a load no violations node is ever mode-less.
        "modes": {
            "realtime": {"label": "in the zone now",
                         "fn_type": "zone_count", "metrics": ["count"]},
            # Hidden from the picker, still compiled: every pre-mode Zone
            # Counter IS one of these, and a distinct count is reachable the
            # supported ways now (Entry/Exit, a Data Table row per entrant).
            # A node already set to it keeps showing and keeps its setting.
            "distinct": {"label": "each entrant once (by OID)", "hidden": True,
                         "fn_type": "zone_unique", "metrics": ["total", "new"]},
        },
        # Which PERIOD a distinct count is shown over is the card's Period
        # setting -- the rule tallies today/this week/this month regardless.
        # `enter_after_s` / `leave_after_s`: WHEN an object counts. 0 and 0 is
        # real-time counting -- what is inside this frame -- which is what a
        # counter has always done and stays the default.
        "data": {"fnId": "", "name": "Zone counter", "mode": "realtime",
                 "limit": 0, "OID": False,
                 "enter_after_s": 0, "leave_after_s": 0}},
    "analytics.proximity": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3, "label": "Object Distance",
        "badge": "DIST",
        "description": "Distance between two object classes, drawn as a link "
                       "line on the feed; alerts when they come too close. "
                       "Same class on both sides measures nearest neighbours.",
        "fn_type": "proximity", "zone_kind": None,
        "metrics": ["pairs", "nearest_px", "active"],
        "data": {"fnId": "", "name": "Proximity",
                 "class_a": "", "class_b": "", "model_a": "", "model_b": "",
                 "max_distance": 0.15, "draw_link": "always"}},
    "analytics.guard": {
        "palette": {"cat": "zone", "group": "Objects"},
        # Renamed from "Object Surrounding Zone", which described the geometry
        # and left the reader to work out what it was FOR. A label, so no
        # graph moves: fn_type, data and every published key are untouched.
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Safety Bubble",
        "badge": "OSZ",
        "description": "A highlighted safety bubble that FOLLOWS each object "
                       "of the chosen classes; another object coming too close "
                       "counts as an intrusion - either its centre inside the "
                       "bubble, or the two bubbles overlapping by a set amount.",
        "fn_type": "guard", "zone_kind": None,
        "metrics": ["intrusions", "inside_now", "overlap_pct", "active"],
        "data": {"fnId": "", "name": "Guard zone",
                 "classes": [], "margin_pct": 50, "alert_classes": [],
                 "touch": "center", "min_overlap_pct": 0, "draw": True}},
    # WHERE things happen, rather than how many or how long. The map is one
    # stat per cell (fn_<id>_c<row>_<col>), which the Heatmap dashboard widget
    # reads by prefix -- the same shape an object watch's per-class keys have,
    # so it needs no new transport.
    "analytics.heatmap": {
        "palette": {"cat": "zone", "group": "Objects"},
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Heatmap", "badge": "HEAT",
        "description": "Where objects actually go. Builds a map of the view, "
                       "cell by cell, from where each object is STANDING. "
                       "Wire a Zone to map that area only. Put it on a "
                       "dashboard with the Heatmap widget.",
        "fn_type": "heatmap", "zone_kind": None, "zone_optional": "area",
        "handles": [{"id": "in", "label": "In"},
                    {"id": "zone", "label": "Zone (optional)"}],
        # `hot` and `spots` are numbers about the MAP -- the busiest patch, and
        # how much of the view has been used -- so it can feed a card or a
        # branch without a widget. The cells themselves are not metrics: there
        # are up to 768 of them and they are a picture, not a reading.
        "metrics": ["hot", "spots"],
        "data": {"fnId": "", "name": "Heatmap", "cols": 12, "rows": 8,
                 "interval_s": 1, "hours": 0},
        # Widget kind the dashboard offers for it, so "put this on a page"
        # lands on the one that can draw it rather than on a tile showing the
        # busiest cell.
        "widget_kind": "heatmap"},
    "analytics.trails": {
        "palette": {"cat": "zone", "group": "Objects"},
        # Renamed from "Motion Trail". The old name described the DRAWING --
        # the dotted line left on the feed -- while the node's actual job is
        # following each object, which is also what its numbers report
        # (tracked / active / idle). The trail is what you see it do, not what
        # it is for. `fn_type` and every saved node's data are untouched: this
        # is a label, so no graph anywhere needs migrating.
        "category": "analytics", "group": "Frames", "rank": 3,
        "label": "Object Motion Tracking",
        "badge": "TRK",
        "description": "Follows each tracked object and leaves a dotted "
                       "trail of its movement on the feed. Choose which "
                       "classes leave a trail and how many seconds of "
                       "history to keep; the trail fades as the object "
                       "stops or leaves.",
        "fn_type": "trails", "zone_kind": None,
        "metrics": ["tracked", "active", "idle"],
        "data": {"fnId": "", "name": "Object motion tracking",
                 "classes": [], "length_s": 10, "idle_s": 5, "draw": True}},
    "analytics.block": {
        "palette": {"cat": "analytics", "group": "Mathematics"},
        "category": "analytics", "group": "Mathematics", "rank": 3, "label": "Function",
        "badge": "FN",
        # Pulled from the palette for now -- not deprecated (an existing
        # canvas keeps loading and compiling exactly as before), just not
        # offered for new use while custom-code-node design is undecided
        # (Python vs JS, sandboxing). Drop this flag to bring it back.
        "hidden": True,
        "description": "A function built visually - double-click to open its "
                       "blueprint and wire values, arithmetic, if/else, switch, "
                       "range and filters as nodes.",
        "fn_type": "expr", "zone_kind": None, "metrics": ["value"],
        "data": {"fnId": "", "name": "Function block",
                 "blueprint": {"nodes": [], "edges": []}}},
    # The accumulator. "Total violations" kept being attempted as a formula
    # that assigns to itself (total == new + total), which the expression
    # language rejects by design -- there is no assignment, and re-evaluating
    # it per frame would add thirty times a second. A total is a MEMORY, so it
    # is a node: wire a value in, choose whether to count its firings or sum
    # its growth, and the node remembers.
    # Filed in the MATH drawer, beside Average and Highest/Lowest: all three
    # are aggregates, and the one that aggregates over TIME was the odd one
    # out in a Functions drawer full of things that measure a place or a
    # frame. Its category and rank are untouched -- this moves where it is
    # found, not what it is.
    "analytics.total": {
        "palette": {"cat": "math", "group": "Math"},
        "category": "analytics", "group": "Math", "rank": 3, "label": "Total Value",
        "badge": "TOT",
        "description": "Accumulates the value wired in: how many times it "
                       "fired, or how much it grew. Optionally starts over "
                       "each day.",
        "fn_type": "total", "zone_kind": None, "metrics": ["total"],
        # Two inputs: the value to accumulate, and an optional Reset (a 0/1
        # wire: when it goes from 0 to 1 the total starts again from zero).
        "handles": [{"id": "in", "label": "Value"},
                    {"id": "reset", "label": "Reset"}],
        "data": {"fnId": "", "name": "Running total", "mode": "count",
                 "reset": "never", "debounce_s": 0, "resume": "zero"}},
    # Python, written in the browser, running in the detection loop. That is
    # remote code execution by design, so it is gated twice and both gates are
    # server-side: only a DEVELOPER may write it (routes), and the box must
    # opt in (config/workflow.toml [python].enabled, which needs shell access
    # to set). A customer's admin can run code that shipped with a template;
    # they cannot author new code from a stolen session.
    # DEPRECATED: removed from the palette. Browser-authored Python is a
    # remote-code-execution surface that has to be gated twice (a developer
    # account AND a shell-only opt-in), and a node offering it to everyone --
    # greyed out on almost every box -- advertised a capability the deployment
    # does not have. Existing nodes still compile and run where the box has
    # opted in; nothing new can be built on it from the palette.
    "analytics.python": {
        "category": "analytics", "group": "Mathematics", "rank": 3, "label": "Python Function",
        "badge": "PY",
        "deprecated": True,
        "description": "Compute a value with your own Python. Master only, "
                       "and only on a box that has opted in.",
        "fn_type": "custom", "zone_kind": None, "metrics": ["value"],
        "data": {"fnId": "", "name": "Python", "code": PYTHON_NODE_TEMPLATE},
        "python": True},
    "analytics.expr": {
        "palette": {"cat": "math", "group": "Formula"},
        "category": "analytics", "group": "Mathematics", "rank": 3, "label": "Custom Formula",
        "badge": "CFX",
        "description": "Any maths over your values: add, round, clamp, power, root, percent, average, scale, blend, trig. Pick a function from the list in the editor.",
        "fn_type": "expr", "zone_kind": None, "metrics": ["value"],
        "data": {"fnId": "", "name": "Formula", "expr": ""}},
    # A value TYPED once and named -- the "constant" face of a declared
    # variable. Not dragged from the palette (hidden below, like variable.ref):
    # the operator makes one from "+ New variable" in the Variables panel,
    # ticking "constant" and picking a type there. This node is what that
    # dialog creates on the canvas, so an existing constant stays a real,
    # visible, editable thing rather than an entry in a list somewhere.
    #
    # boolean is a NUMBER underneath (1.0/0.0, same coercion the When/expr
    # engine already applies to a JSON boolean), so it publishes a real stat
    # exactly like a plain number does -- formulas and cards can read it with
    # no special-casing anywhere else.
    #
    # text is a genuine third case, and a narrower one: the expr engine's own
    # constant whitelist refuses anything that is not a bool or a number
    # ("only numbers are allowed as constants"), and every existing reader of
    # a variable's value -- formulas, cards, the stats dict itself -- is
    # numeric to its core. A text constant therefore publishes NO stat; it
    # compiles to nothing at all (see the value_type == "text" branch below).
    # It still exists, is named, is visible on the canvas and editable in the
    # inspector -- there is simply no consumer wired up to read it back yet.
    "variable.number": {
        "category": "variable", "rank": 3, "label": "Variable (constant)",
        "badge": "CNST",
        "hidden": True,
        "description": "A named value - a threshold or constant for formulas, "
                       "cards and charts. Number, on/off, or text.",
        "fn_type": "expr", "zone_kind": None, "metrics": ["value"],
        "value_types": ["number", "boolean", "text"],
        "data": {"fnId": "", "name": "Constant", "value": 0,
                 "value_type": "number"}},
    # DEPRECATED: identical to Custom Formula under a second name, which meant
    # the palette offered "Formula", "Custom Formula", "Formula Variable" and
    # "Function Block" for what an operator experiences as one idea. Old
    # graphs still compile; the palette no longer offers it.
    "variable.formula": {
        "category": "variable", "rank": 3, "label": "Formula Variable",
        "badge": "FVAR",
        "deprecated": True,
        "description": "A named value computed from other variables and stats.",
        "fn_type": "expr", "zone_kind": None, "metrics": ["value"],
        "data": {"fnId": "", "name": "Variable", "expr": ""}},
    # A HOLDER, not a maker. It publishes nothing and computes nothing: it
    # names a value that already exists so a formula can be wired to it
    # instead of having fn_z_zone_..._count typed into a text box from memory,
    # where one wrong character reads zero for ever with nothing to see.
    #
    # Deliberately has no fnId: minting one would make this a second variable
    # holding a copy of the first, which is the opposite of the point.
    "variable.ref": {
        "category": "variable", "rank": 3, "label": "Variable",
        "badge": "VAR",
        # Reached through "Create Variable" -> kind = a holder filled by a
        # wire, not dragged on its own -- one entry point for the concept
        # instead of two palette items an operator has to already know are
        # the same idea. Fully functional either way; only the drag target
        # is gone. See onRetype in Workflow.jsx.
        "hidden": True,
        "description": "Holds a value: read one that already exists, store a "
                       "rule's output under a name, or both.",
        "zone_kind": None, "metrics": [],
        # Which SIDES the node offers. It used to be inferred purely from
        # whether anything happened to be wired in, which meant the node's
        # meaning changed under the operator as they built -- and there was no
        # way to say "this one is for reading" before wiring it.
        #
        #   in    an INPUT to the graph: reads a value that already exists.
        #   out   an OUTPUT of it: stores what is wired in, under a name.
        #   both  reads a value AND passes it on under its own name.
        #
        # "auto" keeps the original behaviour (wired = store, unwired = read)
        # so every canvas built before this still means what it meant.
        "io_modes": ["auto", "in", "out", "both"],
        # q_* apply only when a QUERY is wired in: a query answers with a
        # table and a variable holds one number, so which column and which
        # row have to be said rather than guessed. q_interval_s is how often
        # the statement runs -- seconds, not frames, because each run is a
        # database round trip on the box that is also running detection.
        # value_type: what the variable HOLDS -- text, decimal or whole
        # number -- shown on the node and inherited by every Set Variable
        # that writes it. "auto" reads it off what is stored.
        "data": {"key": "", "io": "auto", "value_type": "auto",
                 "q_column": "", "q_pick": "first", "q_interval_s": 60}},
    # Arithmetic as a NODE: pick the operation, wire the operands in. The
    # typed Custom Formula stays for anything long or unusual, but the common
    # case -- add these two, is this above that -- should not require writing
    # fn_z_zone_..._count by hand into a text box.
    # DEPRECATED: split into the MATH_NODES family, one node per operation.
    # Kept in the registry so an old canvas still LOADS and compiles -- its
    # `op` + `inputs` storage is exactly what the new nodes use, so a Formula
    # and the node that replaced it are the same document under two names.
    "analytics.formula": {
        "deprecated": True,
        "palette": {"cat": "analytics", "group": "Mathematics"},
        "category": "analytics", "group": "Mathematics", "rank": 3,
        "label": "Formula (old)",
        "badge": "FX",
        "description": "Pick a maths operation and wire the values into it: "
                       "arithmetic, comparisons, powers, logs, trigonometry, "
                       "percentages and if/else.",
        "fn_type": "expr", "zone_kind": None, "metrics": ["value"],
        "handles": list(FORMULA_HANDLES),
        # hint travels too: it is what the picker's search matches on, so an
        # operator can find `pct` by typing "percent".
        "ops": {k: {"label": v["label"], "arity": v["arity"],
                    "group": v["group"], "hint": v.get("hint", ""),
                    # chain = the operation folds over as many inputs as the
                    # node's `inputs` field asks for (2..8).
                    **({"chain": True} if k in FORMULA_CHAINABLE else {}),
                    # hidden = kept compiling for saved graphs, no longer
                    # offered: comparisons are the Switch's job now.
                    **({"hidden": True} if v.get("hidden") else {}),
                    **({"wire_only": True} if v.get("wire_only") else {})}
                for k, v in FORMULA_OPS.items()},
        "op_groups": list(FORMULA_GROUPS),
        "max_inputs": FORMULA_MAX_INPUTS,
        "data": {"fnId": "", "name": "Formula", "op": "add", "inputs": 2,
                 "a_value": 0, "b_value": 0, "c_value": 0}},
    # A number read FROM somewhere else -- a PLC gateway, an ERP endpoint, a
    # sensor's own web API -- so the workflow can compare the world outside
    # against what the camera sees. Fetched by a background poller; the
    # detection loop only ever reads the last value.
    # The inbound half of MQTT. The product could already PUBLISH to a broker;
    # this reads from one, so a PLC's own number can be compared against what
    # the camera sees. Push, not poll: the broker sends when something
    # changes and a subscriber thread holds the last reading.
    # The CLOCK as a rule. Everything else on the canvas answers "something
    # happened"; this answers "the time came round", which is the one trigger a
    # daily report needs and the one the canvas had no way to say. It is not an
    # Alert and must not be built out of one: an Alert means a condition became
    # true, and 12:00 is not a threshold anybody chose.
    #
    # ONE node with a "Runs" selector rather than Daily/Weekly/Monthly nodes --
    # they are the same clock read three ways, and three nodes would be three
    # inspectors to keep in step.
    "variable.schedule": {
        # Its own palette drawer, in the INPUT stage: a schedule is where a
        # pipeline starts as surely as a camera is. Wiring category stays
        # variable -- it publishes a value like any other reading, so it may
        # feed an output, a formula or a card with no new edge rules.
        "palette": {"cat": "time", "group": ""},
        "category": "variable", "rank": 3, "label": "Schedule",
        "badge": "TIME",
        "description": "Fire on the clock: every few minutes, hourly, daily, "
                       "weekly or monthly. Wire it into an Email or Telegram "
                       "for a report that goes out at a set time.",
        "fn_type": "schedule", "zone_kind": None,
        "metrics": ["due", "runs", "next_s"],
        # `at` is the box's own local time, which is what the operator set on
        # the Settings page and what every other time on the product means.
        # `day` 31 in a short month runs on that month's LAST day rather than
        # skipping the month -- the friendly reading of "the 31st", and the
        # only one that never silently loses a report.
        # hours_from/hours_to/days/from_date/to_date are the "and only then"
        # half: a daily window, which weekdays, an absolute range. They used
        # to sit on the Email and Telegram nodes, one copy each, so three
        # messages off one rule meant the same window typed three times and
        # kept in step by hand. They belong to the clock.
        "data": {"fnId": "", "name": "Schedule", "every": "daily",
                 "interval_min": 15, "minute": 0, "at": "12:00",
                 "weekday": 0, "day": 1, "catch_up": False,
                 "hours_from": "", "hours_to": "", "days": "",
                 "from_date": "", "to_date": ""}},
    "variable.mqtt": {
        # Filed in INTEGRATION, under MQTT beside its publishing twin: the
        # operator looks for "the MQTT nodes" as one family, not for an input
        # half in one drawer and an output half in another. Only where it is
        # found moved -- the wiring category stays variable, so every graph
        # already saved compiles exactly as before.
        "palette": {"cat": "integration", "group": "MQTT"},
        "category": "variable", "rank": 3, "label": "MQTT Subscribe",
        "badge": "MQIN",
        "description": "Read a number from an MQTT topic and use it like any "
                       "other variable.",
        "fn_type": "mqtt_sub", "zone_kind": None,
        "metrics": ["value", "ok", "messages"],
        # No fallback field: the value IS whatever the broker last sent, and
        # `ok` already says whether anything has arrived. A number typed here
        # only ever showed up masquerading as a reading.
        # WHERE is a named MQTT Connection (core/services/mqtt_clients.py):
        # the broker, its login and the client settings, set up once and
        # picked here. A node saved before Connections has no `connection`
        # key and its own host/port/login, which the workflow migrates into
        # a Connection when it is opened (mqtt_clients.migrate_graph).
        "data": {"fnId": "", "name": "MQTT value", "connection": "",
                 "topic": "", "json_path": ""}},
    # -- the box's own terminal block ---------------------------------------
    # An edge AI box in a cabinet is wired to things: a light curtain into DI0,
    # a tower light off DO1, a lamp dimmed on L0. Those were reachable only by
    # publishing MQTT and letting a second runtime (Node-RED) move the pin,
    # which meant the pipeline that decided something could not act on it
    # without a broker, a flow and a second thing to keep running.
    #
    # These three nodes are scoped to THIS board on purpose. The old GPIO node
    # was removed because owning a driver for every board a customer might use
    # is not a product; a port named DO0 on hardware we ship is the opposite
    # case -- the pinout is known, detected, and ours.
    "variable.board_input": {
        "palette": {"cat": "board", "group": "Inputs"},
        "category": "variable", "rank": 3, "label": "Board Input (DI)",
        "badge": "DI",
        "description": "Read a dry contact wired into DI0/DI1 - a light "
                       "curtain, a cycle signal, a foot switch.",
        "fn_type": "board_input", "zone_kind": None,
        "metrics": ["state", "changes", "held_s", "ok"],
        "data": {"fnId": "", "name": "Digital input", "port": "DI0",
                 "invert": False}},
    # DO and PWM are separate nodes and the buzzer is a third: three different
    # verbs (switch a relay, set a level, make a noise) were one node with a
    # port dropdown, and an installer looking for "the buzzer" should not have
    # to know it shares a driver with DO0.
    # An output that flips a ZONE rather than a relay: the wired value decides
    # whether the chosen zone is armed. "Enable the intrusion zone only while
    # the forklift is idle" is this node after an Object Motion Tracking; the zone's own
    # checkbox on the Zones panel is the SAVED state this overrides at runtime.
    "output.zone_switch": {
        "palette": {"cat": "board", "group": "Outputs"},
        "category": "output", "rank": 4, "label": "Zone Switch",
        "badge": "ZSW",
        "description": "Enables or disables a zone from the workflow: pick "
                       "which one the wired value does when it is 1. Runtime "
                       "only - the zone's saved setting is untouched and "
                       "returns on restart.",
        "output_type": "zone_switch", "no_trigger": True,
        "data": {"zone": "", "action": "enable", "metric": ""}},
    "output.io_out": {
        "palette": {"cat": "board", "group": "Outputs"},
        "category": "output", "rank": 4, "label": "Board Output (DO)",
        "badge": "DO",
        "description": "Switch DO0/DO1 when this rule fires - latch it, "
                       "pulse it, or turn it off.",
        "output_type": "io_out",
        "data": {"port": "DO0", "action": "on", "pulse_ms": 0,
                 "active_high": True}},
    "output.buzzer": {
        "palette": {"cat": "board", "group": "Outputs"},
        "category": "output", "rank": 4, "label": "Buzzer",
        "badge": "BUZZ",
        "description": "Sound the onboard buzzer when this rule fires.",
        "output_type": "io_out",
        # The port is fixed -- there is one buzzer -- and the default is a
        # PULSE: a buzzer latched on is an alarm nobody can silence.
        "data": {"port": "BUZZER", "action": "pulse", "pulse_ms": 500,
                 "active_high": True}},
    # The box AS a Modbus device. Its sibling further down READS a PLC's
    # registers; this one answers a PLC that reads OURS. Deliberately a node
    # rather than a table in a settings page: the register map is a design
    # decision about what this system publishes, and it belongs beside the
    # rule that fills it -- "zone 1 count, at address 0" is one sentence.
    #
    # RETIRED FROM THE PALETTE (deprecated, like output.rs485): the Modbus
    # family is now Node-RED's pair, Read and Write on a named server, and a
    # third node that runs the conversation the other way round sat beside
    # them reading as a write. Saved graphs still compile and serve exactly as
    # before. It is NOT migrated to a Read: this node is the box answering a
    # PLC, a Read is the box asking one -- converting it would reverse who
    # talks.
    "output.modbus_reg": {
        "palette": {"cat": "output", "group": "Publish"},
        "category": "output", "rank": 4, "label": "Modbus Register",
        "badge": "MB",
        "deprecated": True,
        "description": "Serve this value at a Modbus address, for a PLC or "
                       "SCADA to poll. Registers are 16-bit whole numbers - "
                       "use the scale for anything with decimals.",
        "output_type": "modbus_reg", "no_trigger": True,
        # `scale` is not decoration: a register is a 16-bit integer, so 2.5 fps
        # would arrive as 2 without it. At scale 10 it arrives as 25 and the
        # PLC divides. Explicit, because only the operator knows what the far
        # end expects.
        "data": {"address": 0, "reg_kind": "holding", "scale": 1, "metric": ""}},
    "output.io_pwm": {
        "palette": {"cat": "board", "group": "Outputs"},
        "category": "output", "rank": 4, "label": "Board PWM (L0/L1)",
        "badge": "PWM",
        "description": "Drive L0/L1 at a level 0–255 - fixed, or scaled from "
                       "the value wired into it.",
        "output_type": "io_pwm",
        # `level` is 0-255, the byte convention every PWM datasheet and
        # Arduino sketch already uses -- an installer's "half brightness" is
        # 128, not 50. `when`: "alert" sets the level when the rule fires;
        # "always" follows the wired value continuously.
        "data": {"port": "L0", "source": "fixed", "level": 128,
                 "when": "alert", "metric": "", "in_min": 0, "in_max": 255,
                 "frequency": 1000}},
    # The WRITE node, kept only so old graphs load. RS485 on this board is a
    # sensor bus in practice -- the reader below is the supported direction.
    "output.rs485": {
        "palette": {"cat": "board", "group": "Outputs"},
        "category": "output", "rank": 4, "label": "RS485 (Modbus)",
        "badge": "485W",
        "deprecated": True,
        "output_type": "modbus",
        "data": {"device": "/dev/ttyACM0", "baudrate": 9600, "unit": 1,
                 "target": "holding", "register": 0, "value": 1}},
    # RS485 as what it actually is on this board: an INPUT. A sensor string or
    # a PLC exposes registers; this polls them on its own clock (standalone,
    # like API Value -- no camera involved) and publishes the readings as
    # variables. Readings land as value (the first register) and r0..rN-1 --
    # `quantity` registers starting at `address`. The serial bus can only ever
    # do one transaction at a time, so every reader on it takes turns behind
    # one lock: "parallel" readers are already sequential at the wire, by
    # physics, not by a setting.
    #
    # WHERE it reads is a named Modbus Server (`server`, the connection the
    # Write node picks too -- TCP or serial, [[modbus_clients]] per system).
    # A node saved before servers existed has no `server` key at all and
    # keeps the older rule: the board's RS485 port at its own `baudrate`.
    # A new node carries the key, so leaving it blank is an error to fix, not
    # a quiet fall back to a port the operator never chose. `unit` blank =
    # the server's Unit-Id.
    "variable.rs485": {
        "palette": {"cat": "integration", "group": "Modbus"},
        "category": "variable", "rank": 3,
        # "Modbus Read", not "RS485 Read (Modbus)": it reads over TCP as well
        # as the RS485 port now, and it pairs with Modbus Write. A label only;
        # the key, and every saved graph, is untouched.
        "label": "Modbus Read",
        "badge": "485R",
        "description": "Read registers or coils from a Modbus device - over "
                       "TCP or the RS485 port, through a Modbus Server - and "
                       "use them like variables.",
        "fn_type": "modbus_read", "zone_kind": None,
        "metrics": ["value", "ok"],
        # fc: the four Modbus READ function codes. 1 coils, 2 discrete
        # inputs, 3 holding registers, 4 input registers.
        "data": {"fnId": "", "name": "RS485 value", "server": "", "fc": 3,
                 "unit": "", "address": 0, "quantity": 1, "interval_s": 5}},
    # Node-RED's "Modbus Write" (node-red-contrib-modbus), field for field, on
    # the same named Modbus Server the Read node uses. Two inputs:
    #
    #   in    the VALUE -- any number (a metric, a variable, Math, a Formula),
    #         or a Python node returning Node-RED's payload shape
    #         {value, fc, unitid, address, quantity}, which then overrides
    #         this node's settings field by field for that write;
    #   trig  WHEN -- a Condition, a Compare or a Trigger. Wired, the value is
    #         written each time it fires. Unwired, the value is written when
    #         it CHANGES (never every frame), paced.
    #
    # `unit` blank = the server's Unit-Id. `delay_start` is Node-RED's "Delay
    # to activate input": ignore what arrives for `start_delay_s` after the
    # app starts, so a restart never writes a stale value onto a machine.
    "output.modbus_write": {
        "palette": {"cat": "integration", "group": "Modbus"},
        "category": "output", "rank": 4, "label": "Modbus Write",
        "badge": "MBW",
        "description": "Write a coil or a register on a Modbus device (FC 5, "
                       "6, 15, 16) through a Modbus Server - when the wired "
                       "trigger fires, or whenever the wired value changes.",
        "output_type": "modbus_write",
        "data": {"name": "", "server": "", "unit": "", "fc": 6,
                 "address": "", "quantity": 1, "delay_start": False,
                 "start_delay_s": 5}},
    # Integration / API: the other network-borne reading, filed with the
    # MQTT and HTTP families (see variable.mqtt). Placement only.
    "variable.api": {
        "palette": {"cat": "integration", "group": "API"},
        "category": "variable", "rank": 3, "label": "API Value (fetch)",
        "badge": "API",
        "description": "Read a number from an HTTP endpoint every few seconds "
                       "and use it like any other variable.",
        "fn_type": "api_poll", "zone_kind": None, "metrics": ["value", "ok"],
        "data": {"fnId": "", "name": "API value", "url": "", "interval_s": 5,
                 "json_path": "", "fallback": 0, "headers": [], "token": ""}},
    # A condition between a value and the things that react to it: "when count
    # is at least 5 for 30 seconds", "when the variable becomes true", "when
    # it leaves 20..80". Outputs wired AFTER this node fire on ITS condition
    # instead of the function's built-in alert. Several When nodes on one
    # function are several independent triggers.
    # A pause between a condition firing and what it sets off. "Open the door,
    # wait five seconds, then photograph what came through" is one wire and a
    # number here; without it the same idea needs a second rule whose only job
    # is to be late.
    #
    # It defines no condition of its own -- it is transparent, exactly like
    # When and Alert -- so the outputs after it still fire on the same
    # function, just later.
    "logic.delay": {
        # Listed in Common only (Tok Aba, 2026-10-19: "remove delay because
        # already got at the common"). `category` stays logic, which is what
        # colours it on the canvas.
        "palette": {"cat": "common", "group": ""},
        "category": "logic", "rank": 3, "label": "Delay",
        "badge": "DLY",
        "description": "Hold what comes next for a set time - the outputs "
                       "wired after this one fire that many seconds later.",
        "data": {"delay_s": 5},
        "delay": True},
    # One condition node, several SHAPES of condition -- `ctype` picks which,
    # the way `mode` picks what a counter counts. Every shape but "multi" is
    # one value in, several ways out; a value leaves by the FIRST branch that
    # matches, so the branches are exclusive and "over 10 / over 5 / anything
    # else" reads top to bottom the way it is written. Each branch compiles to
    # its own 0-or-1 rule, which is what makes the outputs on one branch
    # independent of the others.
    #
    #   compare  one test, true / false outs        (if / else)
    #   switch   N tests, first match wins          (route by value)
    #   range    N bands, each "from x up to y"
    #   boolean  on / off (value != 0)
    #   multi    SEVERAL wired values, one combined AND/OR test, pass / fail
    #
    # The shapes share one storage: `rules` rows carry {op, value} and,
    # for a range band, {op: "between", value, value2}; "multi" adds an
    # `input` per row naming which wired port it tests, plus `join`.
    # A graph saved before ctype existed has none -> "switch", the exact
    # old behaviour, compiling to the same {base}_b{i} function ids.
    # -- the BRANCH family ---------------------------------------------------
    #
    # Five shapes of "decide what happens next", each a node of its own rather
    # than one node with a shape picker. They replace logic.switch (one node,
    # five ctypes, chosen in an inspector nobody opened) and logic.when.
    #
    # HARD INVARIANT, true of every node in this family: exactly ONE output is
    # 1 at any moment and every other is 0. First match wins, in port order,
    # and the last port is the catch-all. Anything reading these ports can
    # rely on that -- it is what makes them safe to sum, to feed a Trigger, or
    # to multiply a value by.
    #
    # Every output is a REAL port with a real published value (an expr rule,
    # `<base>_b<index>`), so a branch can be wired anywhere a number can. That
    # is the break from what came before: logic.when and output.alert
    # published nothing and were resolved at compile time by walking backwards
    # to the rule behind them (the "transparent node" convention). A wire that
    # means nothing until the compiler infers it is a wire an operator cannot
    # reason about, and it made "what is this port's value?" unanswerable on
    # the canvas.
    #
    # They all compile through the SAME first-match machinery -- `ctype` is
    # fixed per type instead of picked -- so this is a split of one node into
    # five faces, not five new evaluators.
    # DEPRECATED: folded into If / Else if, which IS this node with more than
    # one row. One test with a True and a False way out is "if / else" -- the
    # same words, the same ports, the same compiled branch ids -- so two
    # palette entries meant choosing between a thing and itself.
    # migrate_compare_merge rewrites saved ones; the entry stays so a graph
    # that reaches the compiler first still means what it meant.
    "branch.compare": {
        "palette": {"cat": "logic", "group": "Branch"},
        "category": "logic", "rank": 3, "label": "Compare",
        "badge": "CMP",
        "deprecated": True,
        "description": "Test one value: over, under, equal, changed, in range. "
                       "True and False are both real ports - wire whichever "
                       "side you want to act on.",
        "ctype": "compare", "branch": True,
        "ports": ["True", "False"],
        "data": {"fnId": "", "metric": "", "hold_s": 0,
                 "rules": [{"op": ">", "value": 1, "input": "in1"}],
                 "otherwise": True},
        "switch": True},
    "branch.cases": {
        "palette": {"cat": "logic", "group": "Branch"},
        "category": "logic", "rank": 3, "label": "If / Else if",
        "badge": "IFEL",
        "description": "Test a value - over, under, equal, changed, in range "
                       "- and take the way out that holds. Add rows for more "
                       "tests, each on its own value: if / elif / else, the "
                       "first that passes wins.",
        "ctype": "compare", "branch": True,
        "data": {"fnId": "", "metric": "",
                 "rules": [{"op": ">", "value": 1, "input": "in1"}],
                 "otherwise": True},
        "switch": True},
    "branch.range": {
        "palette": {"cat": "logic", "group": "Branch"},
        "category": "logic", "rank": 3, "label": "Range",
        "badge": "RNGE",
        # Pulled from the palette (If / Else if has a "between" test); an
        # existing canvas keeps it.
        "deprecated": True,
        "description": "Sort one number into bands - 0-20, 20-40, above 40 - "
                       "each band its own port.",
        "ctype": "range", "branch": True,
        "data": {"fnId": "", "metric": "",
                 "rules": [{"op": "between", "value": 0, "value2": 20}],
                 "otherwise": True},
        "switch": True},
    "branch.match": {
        "palette": {"cat": "logic", "group": "Branch"},
        # "Cases", not "Match": the node IS the switch/case, and a port per
        # case is what an operator sees on its face. The TYPE stays
        # branch.match -- every graph already saved names it, and a label is
        # not an identifier.
        "category": "logic", "rank": 3, "label": "Cases",
        "badge": "MTCH",
        # Pulled from the palette (If / Else if covers it); an existing
        # canvas keeps it.
        "deprecated": True,
        "description": "Switch/case for one value: a port per case, and a "
                       "default for everything else.",
        "ctype": "switch", "branch": True,
        "data": {"fnId": "", "metric": "",
                 "rules": [{"op": "==", "value": 1}], "otherwise": True},
        "switch": True},
    "branch.multi": {
        "palette": {"cat": "logic", "group": "Branch"},
        "category": "logic", "rank": 3, "label": "Multi",
        "badge": "MULT",
        "description": "Several values, each with its own test, combined with "
                       "AND or OR. Two ways out: Pass and Fail.",
        "ctype": "multi", "branch": True,
        "ports": ["Pass", "Fail"],
        "data": {"fnId": "", "join": "and",
                 "rules": [{"op": ">", "value": 1, "input": "in1"}],
                 "otherwise": True},
        "switch": True},
    # DEPRECATED: split into the five branch nodes above. Kept in the registry
    # so an old canvas still LOADS -- migrate_branch_family rewrites it on
    # open, carrying its ctype to the matching new type and its rules across
    # untouched (the compiled branch ids are unchanged, so cards, history and
    # dashboards bound to them survive).
    "logic.switch": {
        "palette": {"cat": "logic", "group": "Flow"},
        "deprecated": True,
        "category": "logic", "rank": 3, "label": "Switch",
        "badge": "SW",
        "description": "Decide what happens next. Five shapes: IF / ELSE IF "
                       "over separate values, one case per value, numeric "
                       "bands, on/off, or several values combined with AND / "
                       "OR. Every condition, case and band is its own output "
                       "port, and the condition shapes give each row its own "
                       "input too - add one and the ports appear.",
        # A NEW node is the smallest thing that still says what it does: one
        # case on the wired value, plus the default. Everything else is added
        # deliberately, so an unconfigured node is never a wall of empty rows.
        "data": {"fnId": "", "name": "Switch", "metric": "", "ctype": "switch",
                 "rules": [{"op": "==", "value": 1}], "otherwise": True,
                 "join": "and"},
        "switch": True},
    # Fires when the rule wired into it FAILS. Today the things that can fail
    # and say so are the code nodes -- a Python Function or a template's own
    # handler publish fn_<id>_error when they throw -- so that is honestly
    # what this catches: your code broke, tell someone. Wire an Alert or an
    # output after it. It is not a catch-all for every fault in the box; a
    # camera dropping out or a broker refusing a connection are reported by
    # the things that own them.
    "logic.catch": {
        "palette": {"cat": "common", "group": ""},
        "category": "logic", "rank": 3, "label": "Catch (on error)",
        "badge": "CTCH",
        "description": "Fires when the code node wired into it throws. Wire "
                       "a Condition or an output after it so a broken rule is "
                       "noticed instead of silently reading zero.",
        "data": {"severity": "warn", "message": "", "cooldown_s": 60},
        "catch": True},
    # "Compare", not "When": it EVALUATES a condition on the wired value --
    # threshold, on/off, change, edge, range -- and what follows it fires
    # while that evaluation is true. The type stays logic.when: the node id
    # is a storage format, and renaming it would orphan every saved canvas
    # for the sake of a label the operator never sees. `mode` is already the
    # extensible comparison-mode list this node was asked to have; a new mode
    # is one entry in WHEN_MODES (client) plus its compile branch.
    # DEPRECATED: became branch.compare, which publishes True and False as
    # real ports instead of being resolved backwards at compile time.
    # migrate_branch_family rewrites it on open.
    "logic.when": {
        "deprecated": True,
        "category": "logic", "rank": 3, "label": "Compare (old)",
        "badge": "CMP",
        "description": "True while the wired value passes the test: "
                       "threshold, on/off, change, edge, or range - "
                       "optionally only after it holds for N seconds. What "
                       "is wired after this fires while it is true.",
        "data": {"metric": "", "mode": "above", "value": 1, "value2": 0,
                 "hold_s": 0, "severity": "warn", "message": "",
                 "cooldown_s": 10}},
    # ONE card node, not one per shape. Counter, Gauge and Verdict were the
    # same node three times: same wiring, same inspector, same everything but
    # `widget_kind`. The shape is data, the way the Chart node has always
    # treated it -- and the names now match what the Dashboard and Analytics
    # editors call the same widgets, so "which of these is the stat tile" has
    # one answer instead of two vocabularies.
    "dashboard.card": {
        "palette": {"cat": "dashboard", "group": "Cards"},
        "category": "dashboard", "rank": 4, "label": "Card",
        "badge": "CARD",
        "description": "One value: a stat tile, a gauge, or a verdict state.",
        "widget_kind": "tile", "widget_source": "stat",
        # `name` is the card's TITLE, and naming one also lists it in the
        # editors' "From workflow" palette. Unnamed, it is titled after the
        # function feeding it, as it always was.
        "data": {"name": "", "metric": "", "kind": "tile", "color": "", "unit": "",
                 # The caption: the small line under a tile's value. Identity,
                 # like the name -- edited on the node and in the Builders,
                 # stored here (see CARD_IDENTITY_FIELDS).
                 "sub": "",
                 "window": "live", "agg": "", "range": "",
                 "template": "plain", "bg": "", "show": [],
                 # How big the card's words are. Edited on either side -- the
                 # node's inspector or the Analytics card -- and stored HERE,
                 # so the two cannot drift apart.
                 "label_size": "medium", "value_size": "medium",
                 # Verdict shape only: what each value MEANS.
                 "ok_label": "COMPLIANT", "alert_label": "VIOLATION",
                 "ok_color": "", "alert_color": "",
                 "states": [], "op": ">=", "value": 1,
                 # WHERE the value comes from when it is not a wire (see
                 # EVENT_SOURCE_MODES): "" follows the wire, "events" asks the
                 # recorded event store, "dataset" a table named here. What
                 # the retired Event Card was, as a setting of the card.
                 **_EVENT_SOURCE_DATA}},
    # DEPRECATED: folded into dashboard.card. Kept in the registry so an old
    # canvas still LOADS -- migrate_merged_nodes rewrites it on open.
    "dashboard.counter": {
        "palette": {"cat": "dashboard", "group": "Cards"},
        "category": "dashboard", "rank": 4, "label": "Counter Card",
        "badge": "CTR",
        "deprecated": True,
        "description": "A live number tile on the dashboard.",
        "widget_kind": "tile", "widget_source": "stat",
        "data": {"metric": "", "color": "", "unit": "", "window": "live",
                 "agg": "", "range": ""}},
    "dashboard.gauge": {
        "palette": {"cat": "dashboard", "group": "Cards"},
        "category": "dashboard", "rank": 4, "label": "Gauge",
        "badge": "GAU",
        "deprecated": True,
        "description": "A gauge dial of the live value.",
        "widget_kind": "gauge", "widget_source": "stat",
        "data": {"metric": "", "color": "", "window": "live",
                 "agg": "", "range": "", "unit": ""}},
    "dashboard.verdict": {
        "palette": {"cat": "dashboard", "group": "Cards"},
        "category": "dashboard", "rank": 4, "label": "Verdict Card",
        "badge": "VRD",
        "deprecated": True,
        "description": "The big status card.",
        "widget_kind": "verdict", "widget_source": "stat",
        "data": {"metric": "", "ok_label": "COMPLIANT",
                 "alert_label": "VIOLATION", "states": [],
                 "agg": "", "range": "", "op": ">=", "value": 1}},
    # DEPRECATED: the Chart node before it had a shape picker.
    "dashboard.chart": {
        "palette": {"cat": "dashboard", "group": "Charts"},
        "category": "dashboard", "rank": 4, "label": "Chart",
        "badge": "CHT",
        "deprecated": True,
        "description": "The value over time (history line chart).",
        "widget_kind": "line", "widget_source": "history",
        "data": {"metric": "", "color": "", "range": "24h"}},
    # The chart: every shape a chart can be, chosen as data. Named "Chart"
    # rather than "Graph" to match what the editors call it.
    "dashboard.graph": {
        "palette": {"cat": "dashboard", "group": "Charts"},
        "category": "dashboard", "rank": 4, "label": "Chart",
        "badge": "CHRT",
        "description": "The value over time or by group: line, bar, area, "
                       "spline, donut, pie or radar.",
        "widget_kind": "line", "widget_source": "history",
        "data": {"name": "", "metric": "", "kind": "area", "color": "", "unit": "",
                 "template": "plain", "bg": "",
                 "label_size": "medium", "value_size": "medium",
                 "range": "24h", "agg": "", "group_by": "", "bucket": "",
                 # WHAT each series input is called and what colour it draws
                 # in: {"s1": {"label": ..., "color": ...}, "s2": {...}},
                 # keyed by the series id the compile mints per handle. Held
                 # on the NODE rather than on the widget for the same reason
                 # `template` and the text sizes are: a wf_* widget is rebuilt
                 # from its node on every compile, so anything living only on
                 # the widget is gone at the next save.
                 # `series_n` is how many series ports the node SHOWS (2..8),
                 # exactly as a Formula's `inputs` is -- wires decide what
                 # compiles, this decides what invites a wire.
                 "series": {}, "series_n": 2,
                 # The period lives on the CARD, on every card, because it is
                 # a question about what to show rather than what to measure.
                 "window": "live",
                 # Same source switch as the Card's. A Chart in "events" or
                 # "dataset" mode asks one question of recorded data, so its
                 # series ports give way to the one wire that scopes it.
                 **_EVENT_SOURCE_DATA}},
    # DEPRECATED: folded into the Card and the Chart, as their `source_mode`.
    # The Event Card was those two nodes with a different answer to "where
    # does the number come from", and a third node for one setting is a third
    # thing to find. Kept compilable so a canvas saved with one still runs
    # until it is opened; migrate_merged_nodes rewrites it on open.
    "dashboard.events": {
        "palette": {"cat": "dashboard", "group": "Cards"},
        "category": "dashboard", "rank": 4, "label": "Event Card",
        "badge": "EVT",
        "deprecated": True,
        "description": "A number or chart from what the wired function raised "
                       "(events) or published (metric). Unwired it counts "
                       "EVERY recorded event that matches its filters.",
        "widget_kind": "tile", "widget_source": "query", "standalone": True,
        "data": {"name": "", "kind": "tile", "agg": "count", "field": "",
                 "template": "plain", "bg": "",
                 "label_size": "medium", "value_size": "medium",
                 "group_by": "", "bucket": "", "range": "", "filters": {},
                 "conditions": [], "sub": "", "color": "", "limit": 15,
                 # With a function wired in: "events" counts what it raised,
                 # "metric" charts what it publishes (live/history value).
                 # "dataset" reads a table a Dataset node records, which needs
                 # no wire at all -- the table is named, not connected.
                 "source_mode": "events", "metric": "", "dataset": "",
                 # Wired to a whole TABLE: which of its columns to show.
                 # Empty means all of them, so a table that gains a column
                 # later still shows it.
                 "show": []}},
    # DEPRECATED: an Event Card with kind "table" IS this. Two nodes, one
    # result -- and the Event Card can also filter, which this never could.
    "dashboard.table": {
        "palette": {"cat": "dashboard", "group": "Tables"},
        "category": "dashboard", "rank": 4, "label": "Events Table",
        "badge": "ETBL",
        "deprecated": True,
        "description": "The latest events raised by this pipeline.",
        "widget_kind": "table", "widget_source": "events",
        "data": {"agg": "", "range": "", "limit": 15}},
    # THE trigger node, and the only one. A rule used to be able to raise an
    # event two ways -- a `trigger` set inside the rule, or this node wired
    # after it -- which is one behaviour under two names, the first of them
    # invisible unless you opened the node that had it. The checkbox is gone
    # and every graph that used one is migrated to this
    # (migrate_inline_triggers).
    #
    # So it belongs in CONTROL, beside Compare and Switch: this is where a
    # measurement becomes a decision, not where a message is sent. Its wiring
    # category stays "output" -- that is what edge_allowed reasons about, and
    # moving it would change which nodes may legally meet on every graph
    # already saved.
    # The named moment. Wire a Branch output into it, give it a name, and that
    # name is what the rest of the canvas -- and the events log -- calls this
    # happening: "Overcrowded", "Door left open".
    #
    # It is the ONE node that keeps a name field, and deliberately: every
    # other node is named after what it measures, which it knows and the
    # operator does not have to type. A business condition has no such
    # default. "Trigger 1" would be a name that says nothing on a dashboard,
    # in an alert message, and in a year-old event row.
    #
    # An alarm is OPTIONAL here, which is the change from the Alert it
    # replaces. With nothing ticked this is a named pass-through: it raises
    # its event and anything wired after it fires, with no severity, no
    # message and no colour -- which is exactly what gating a Data Table write
    # wants, and what "Alert" could never be talked out of being.
    #
    # There is no true/false setting: the operator wires the Branch port they
    # mean. An inversion setting on this node was a second place to say the
    # same thing, and the two could disagree.
    "output.trigger": {
        "palette": {"cat": "logic", "group": "Branch"},
        "category": "output", "rank": 4, "label": "Trigger",
        "badge": "TRIG",
        "description": "Names the moment a branch becomes true, and raises it. "
                       "Optionally makes it an alert or colours the objects "
                       "that caused it.",
        "output_type": None,
        # `name` is required and user-typed -- see above. `alert` and `draw`
        # gate the two optional halves, so an untouched Trigger carries no
        # alarm semantics at all.
        # box_color paints the objects that tripped it; zone_color paints the
        # zone the rule watches, for as long as it holds. Separate because
        # they answer separate questions -- "which thing is wrong" and "where
        # is wrong" -- and the single colour + "colour what" picker this
        # replaces could only ever say one of them.
        # `repeat` is HOW an alarm that keeps holding is raised again:
        # "cooldown" every cooldown_s (the only way there was), or
        # "new_object" -- once, and then only when a DIFFERENT object joins
        # the violation. The second needs to know who, so it reads the rule's
        # OID port, wired into this node's own `oid` port.
        # The Advanced output (services/trigger_out.py), all off when empty:
        # payload_type/payload and idle_type/idle_payload are the Inject's
        # typed value, rate ""/cooldown/once_per_oid with rate_s, and the
        # hold, out_hold realtime/interval/hold + out_hold_s (see hold_mode).
        # `dup_guard` offers the Current OID / Current value ports (the
        # duplicate guard, _trigger_guard); `dup_threshold` is how far the
        # value must move before the same object is pushed again (timer).
        "data": {"name": "", "alert": False, "severity": "warn", "message": "",
                 "cooldown_s": 30, "repeat": "cooldown", "draw": False,
                 "box_color": "", "zone_color": "", "color_target": "box",
                 "payload_type": "", "payload": "", "idle_type": "",
                 "idle_payload": "", "rate": "", "rate_s": 10,
                 "out_hold": "", "out_hold_s": 0, "box_in_zone": False,
                 "dup_guard": False, "dup_threshold": 0,
                 # `wait_end`: the moment is raised at once, but the Data Table
                 # row and the Snapshot photo after it are completed when the
                 # timer behind it ends (the object left the zone or was lost),
                 # so they carry the real total and how far past the limit.
                 "wait_end": False}},
    # DEPRECATED: Condition + Alert became Branch + Trigger. Both stay in the
    # registry so an old canvas still LOADS; migrate_branch_family rewrites a
    # Condition into a Compare + Trigger pair and folds an Alert's severity,
    # message, cooldown and colour into that Trigger.
    "output.alert": {
        "deprecated": True,
        "palette": {"cat": "logic"},
        # On screen it is a CONDITION, because that is what an operator
        # comes here to write: "when the count is over 4". Calling it an Alert
        # named the loudest of the things it can cause rather than the thing
        # it IS, and left people asking why a Snapshot needed an "alert".
        # The type stays output.alert -- a node id is a storage format, and
        # renaming it would orphan every saved canvas for the sake of a label.
        # Same trade as logic.when, which has read "Compare" for a long time.
        "category": "output", "rank": 4, "label": "Condition",
        "badge": "COND",
        "description": "The moment: raises an event when a rule's value "
                       "passes a threshold. Wire it after any rule, then wire "
                       "what should happen after it.",
        "output_type": None,
        # The alarm's own settings -- severity, message, cooldown, box colour
        # -- moved to the Alert node. They stay in `data` because every graph
        # already saved carries them, and a Condition with no Alert after it
        # still compiles from exactly these. migrate_split_alerts lifts them
        # onto an Alert node once, so new work only ever meets one of the two.
        "data": {"metric": "", "op": ">", "value": 1, "severity": "warn",
                 "message": "", "cooldown_s": 30, "box_color": "",
                 "color_target": "box"}},
    # The alarm itself, separated from the condition that raises it. A
    # Condition answers WHEN; this answers what the operator is told and how
    # the picture looks while it holds -- two questions that were one panel,
    # so choosing a threshold meant scrolling past a colour picker.
    #
    # It DECORATES a Condition rather than gating it: wire it after one and
    # the event that Condition already raises gains this severity, this text,
    # this pacing, this colour. Anything wired after the Alert still fires on
    # the same rule, so Condition -> Alert -> Snapshot is one chain about one
    # moment.
    "output.event": {
        "deprecated": True,
        "palette": {"cat": "logic"},
        "category": "output", "rank": 4, "label": "Alert (old)",
        "badge": "ALRT",
        "description": "What the alarm says: severity, message, how often it "
                       "may repeat, and the colour objects are drawn in. Wire "
                       "it after a Condition.",
        "output_type": None,
        # `when`: fire while the Condition is true (the ordinary case), or
        # while it is FALSE -- "tell me when the bay is NOT occupied" is a
        # real alarm, and writing it as a second inverted Condition means
        # maintaining the same threshold in two places.
        #
        # box_color: what the objects that trip the rule are drawn in. Empty
        # means red -- the colour a violation has always been -- so the
        # default needs no config and an old graph keeps its look.
        "data": {"when": "true", "severity": "warn", "message": "",
                 "cooldown_s": 30, "box_color": "", "color_target": "box"}},
    # Retired from the palette: an alert that must reach a broker is the
    # Publish node after an Alert. Old graphs still compile.
    "output.mqtt": {
        "palette": {"cat": "output", "group": "Publish"},
        "category": "output", "rank": 4, "label": "MQTT",
        "badge": "MQTT",
        "deprecated": True,
        "output_type": "mqtt",
        "data": {"connection": "", "host": "127.0.0.1", "port": 1883,
                 "topic": "cytron/alert", "payload": "1"}},
    # Publishes the wired value WHEN what is wired in says so: a Trigger's
    # fire, hold and interval, an Inject's fire, a value's change. No clock
    # of its own (Tok Aba 2026-10-08, services/publish_follow.py).
    "output.mqtt_publish": {
        "palette": {"cat": "integration", "group": "MQTT"},
        "category": "output", "rank": 4, "label": "MQTT Publish",
        "badge": "MQPB",
        "description": "Publish the wired value to an MQTT topic when the "
                       "Trigger or Inject in front of it says so. Nothing "
                       "wired in, it publishes nothing.",
        "output_type": "mqtt_publish", "no_trigger": True,
        # The body is always the bare value: that is what a PLC tag or a
        # broker consumer reads, and a format dropdown was one more question
        # between "wire the number" and "the number arrives".
        # `connection` names the MQTT Connection; host/port stay listed only
        # so a node saved before Connections, compiled before the workflow
        # has migrated it, still publishes where it did.
        "data": {"connection": "", "host": "127.0.0.1", "port": 1883,
                 "topic": "cytron/value", "metric": "", "retain": False}},
    # HTTP, both directions of it, in Integration beside MQTT -- a message
    # leaving the box for somebody else's system is what that drawer is for.
    "output.webhook": {
        "palette": {"cat": "integration", "group": "HTTP"},
        "category": "output", "rank": 4, "label": "Webhook / REST",
        "badge": "WHK",
        "description": "Call an HTTP endpoint when this analytics alerts.",
        "output_type": "webhook",
        "data": {"url": "", "method": "POST", "payload": "",
                 "headers": [], "token": ""}},
    # The HTTP twin of output.mqtt_publish: same telemetry idea, an endpoint
    # instead of a broker, for the many systems that expose a REST ingest URL
    # and no MQTT at all.
    "output.webhook_publish": {
        "palette": {"cat": "integration", "group": "HTTP"},
        "category": "output", "rank": 4, "label": "Publish to HTTP",
        "badge": "HTPB",
        "description": "POST this value to an HTTP endpoint every few seconds, alert or no alert.",
        "output_type": "webhook_publish", "no_trigger": True,
        "data": {"url": "", "method": "POST", "headers": [], "token": "",
                 "metric": "", "interval_s": 5, "format": "value"}},
    # The only node that records a ROW rather than a number or a message.
    # Everything else here publishes stats (which lose the relationship between
    # them) or fires a message (which leaves nothing to query afterwards); a
    # Data Table is the operator's own table, with their own column names.
    #
    # Called a "dataset" throughout the code because that is what it is; the
    # LABEL says Data Table because this product already has Dataset pages,
    # and those are training photos and clips -- a different thing entirely.
    # Its own palette category. `category` stays "output" -- that is the
    # WIRING contract (a sink, nothing may come out of it) and the compiler
    # depends on it -- while `palette` is only where it appears. Recording to
    # the database is not "an output" the way a siren or an email is: it is
    # where the system's own history comes from.
    "output.dataset": {
        "palette": {"cat": "database", "group": ""},
        "category": "output", "rank": 4, "label": "Data Table",
        "badge": "TBL",
        "description": "Record a row of your own columns into the database, "
                       "then chart or export it from Analytics.",
        "output_type": "dataset",
        # Handles are per COLUMN and therefore per node: every column of the
        # chosen table owns a handle named c_<column>, drawn from the table by
        # the canvas (nodes.jsx), so a value dropped on `dwell` fills dwell --
        # the wire IS the assignment. They used to be positional (c1, c2 ...),
        # which meant reordering the table silently re-pointed every wire.
        #
        # ONE more handle, `trig`, and it is not a column: it says WHICH
        # rule fires the row when "Write a row" is set to "when something
        # alerts".
        #
        # It used to be derived -- every rule wired into a column that could
        # alert armed the table -- on the reasoning that a port would restate
        # the dropdown. It does not. Derivation cannot express "record these
        # three values AT THE MOMENT THIS OTHER THING happens": the moment had
        # to be one of the values, so a table recording speed, lane and
        # temperature when a gate opened needed the gate wired into a column
        # it had no business filling. Wiring the trigger says it once, in the
        # place the canvas already shows it.
        #
        # Derivation REMAINS, as the fallback when nothing is wired here: the
        # graphs already built this way are correct and must keep working.
        "handles": ["trig"],
        # `sources` covers only the columns NOT wired on the canvas:
        # {column: {src: stat|event|const, key|field|value}}.
        # `on_repeat`: what a write does when the table already holds a row
        # for the same object -- "insert" adds another (the original
        # behaviour), "update" refreshes that row. `ids_in_row`: "object" is a
        # row per tracked object (the original), "group" one row listing them
        # all, saved when the set changes. See compile below.
        "data": {"dataset": "", "name": "", "op": "insert", "mode": "alert",
                 "interval_s": 60, "min_interval_s": 0, "on_repeat": "insert",
                 "ids_in_row": "object", "sources": {}}},
    # A Query sits in Storage beside the Data Table because it is the other
    # half of the same idea: the table says what is RECORDED, the query says
    # what is ASKED of it. It is not an output -- nothing leaves the system
    # and nothing fires -- so it gets its own wiring category rather than
    # being a fifth kind of sink.
    "storage.query": {
        "palette": {"cat": "database", "group": ""},
        "category": "query", "rank": 4, "label": "Query",
        "badge": "QRY",
        "description": "Combine columns from your tables into one custom "
                       "result with SQL, then show it on a dashboard card.",
        # One handle in (the tables it may read, wired from Data Table nodes)
        # and the ordinary output handle out to a card. The tables are a WIRE
        # rather than a dropdown so the canvas shows what a query depends on:
        # rename a table and the line still points at it.
        "handles": ["table"],
        "data": {"name": "", "sql": "", "range": ""}},
    # A Query answers with a TABLE. Everything downstream of it until now took
    # one number out of that table -- a card, a variable -- because a message
    # can only say a number. This node is the other answer: keep the rows, as
    # a file, and attach them.
    #
    # Its own node rather than a checkbox on the Email, because "turn this
    # query into a file" is a step, and a step two outputs share should be one
    # thing on the canvas rather than the same box ticked twice. The wire also
    # says WHICH query, which a checkbox could not on a message wired to
    # several.
    "storage.csv": {
        "palette": {"cat": "database", "group": ""},
        "category": "query", "rank": 4, "label": "CSV File",
        "badge": "CSV",
        # {csv} NAMES the file; {csv_text} is what pastes the rows (the old
        # text said {csv} did, which stopped being true when it became the
        # name). Several CSV nodes into one message attach several files.
        "description": "Turn a Query's rows into a CSV file and attach it to "
                       "an Email or a Telegram message. In the message, {csv} "
                       "names the file and {csv_text} puts its rows in the "
                       "text. Wire in several to attach several; each then has "
                       "its own {csv:name} and {csv_text:name}.",
        # `name` becomes the attachment's filename; blank borrows the Query's
        # own name, so the common case needs nothing typed. `max_rows` 0 is
        # every row -- a cap is for keeping a message small, not for safety.
        "data": {"name": "", "max_rows": 0}},
    # Email and Telegram live in INTEGRATION, not Outputs. A siren, a relay and
    # a snapshot happen ON the box; a message leaves it, reaches a person, and
    # -- once commands exist -- gets answered. Their WIRING category stays
    # `output` (that is what the pipeline rules reason about, and moving it
    # would change which nodes may legally meet on every graph already saved);
    # only where they are found changes.
    "output.email": {
        "palette": {"cat": "integration", "group": "Email"},
        "category": "output", "rank": 4, "label": "Email",
        "badge": "MAIL",
        "description": "Email someone when this analytics alerts. Sends through one of the box's mailboxes.",
        "output_type": "email",
        # `account` names one of the box's mailboxes (Settings > Email) by id.
        # Blank means the default one, so a box with a single mailbox never
        # has to choose. The node deliberately carries NO credentials: it used
        # to take a sender and an app password of its own, which put the same
        # secret in as many nodes as used it.
        # No send window here any more. This node used to carry one -- a
        # cooldown, a daily window, weekdays, a date range -- which answered
        # "when" on the node whose job is "what to say", and meant three
        # messages off one rule needed the same window typed three times and
        # kept in step by hand. A Schedule wired in front owns that now.
        "data": {"to": "", "subject": "", "body": "", "account": ""}},
    # THE OTHER DIRECTION. Every Telegram node above shouts; this one answers.
    # Somebody types /summary into the chat, the bot offers this node's
    # buttons, and tapping one runs the Query wired to that button's port over
    # the period the button names.
    #
    # It is an OUTPUT by wiring category and nothing else -- it is fed by
    # Queries, it feeds nothing, and it fires on a person rather than on a
    # rule. Giving it a category of its own would mean teaching the pipeline
    # rules about a fifth kind of thing for one node.
    "integration.command": {
        "palette": {"cat": "integration", "group": "Telegram"},
        "category": "output", "rank": 4, "label": "Command",
        "badge": "CMD",
        "description": "Answer a Telegram command. Names the word people type "
                       "(/summary), and offers buttons - wire a Query into "
                       "each button's port and tapping it runs that statement "
                       "over that button's period.",
        # output_type None: this compiles to a COMMAND document, not an
        # output, so the output pass skips it the way it skips an Alert. A
        # command is not fired by a rule -- it is fired by a person -- so it
        # has no trigger and no function to bind to.
        "output_type": None,
        # A button carries `chart` as well as its period: the same statement
        # over the same bound window, drawn as a table or as a picture. Thirty
        # rows of digits on a phone is a wall; the same rows as a line is a
        # shape somebody reads while walking.
        "data": {"command": "", "title": "", "intro": "",
                 "buttons": [{"label": "Today", "period": "today",
                              "chart": "table"},
                             {"label": "This week", "period": "week",
                              "chart": "bar"}]}},
    # The other half of an answer: a Query button says HOW MANY, this one
    # shows WHICH. The pictures are already on disk -- the app writes them when
    # a rule fires -- so this only chooses among them.
    #
    # Capped at ten (Telegram's own album limit) over at most 24 hours, which
    # is the line between two features rather than an arbitrary limit: a glance
    # at what just happened belongs in a chat, and a shift of evidence belongs
    # in Footage > Export, which zips the ORIGINALS. Telegram recompresses
    # every photo in an album, so what arrives in a chat is not what to keep.
    "integration.photos": {
        "palette": {"cat": "integration", "group": "Telegram"},
        "category": "output", "rank": 4, "label": "Snapshots",
        "badge": "PHOT",
        "description": "Answer a command button with the snapshots from its "
                       "window - up to ten, over at most 24 hours.",
        # Like a Command and a Watch: this is not an output. It answers a
        # button, so it compiles INTO one.
        "output_type": None,
        "data": {"camera": "", "most": 10}},
    # A number in the DATABASE crossing a line, which the alert path on the
    # canvas cannot answer: an analytics node knows what a camera sees this
    # instant, and "more than fifty violations today" is a question about what
    # was recorded. The app has no database, so this runs in the master on a
    # timer -- the same place, and for the same reason, as a Command.
    #
    # Wired Query in, Telegram or Email out: the message stays the node an
    # operator already knows, so a watch adds a trigger rather than a second
    # way to write a message.
    "integration.watch": {
        "palette": {"cat": "integration", "group": "Alerts"},
        "category": "output", "rank": 4, "label": "Watch (threshold)",
        "badge": "WTCH",
        "description": "Run a Query on a timer and send when its number "
                       "crosses a line - more than 50 violations today. Wire a "
                       "Query in and a Telegram or Email node out.",
        # Like a Command: this compiles to a WATCH document, not an output, so
        # the output pass skips it. Nothing fires it -- it fires itself.
        "output_type": None,
        "data": {"name": "", "every_min": 15, "period": "today",
                 "op": ">", "value": 10, "quiet_min": 60}},
    "output.telegram": {
        "palette": {"cat": "integration", "group": "Telegram"},
        "category": "output", "rank": 4, "label": "Telegram",
        "badge": "TG",
        "description": "Send a Telegram message when this analytics alerts.",
        "output_type": "telegram",
        # `account` NAMES one of the box's bots (Settings > Telegram); blank
        # means the default, so a box with one bot never has to choose. The
        # token and chat id stay here only for a graph written before the bot
        # list existed -- a node that carries its own token keeps working, and
        # the moment one is picked the credential leaves the canvas.
        # The send window has moved to the Schedule node -- see output.email.
        "data": {"account": "", "bot_token": "", "chat_id": "", "message": ""}},
    # `camera` names which camera to capture FROM. Blank follows the rule that
    # fires it, which is what a single-camera system means and what a
    # zone-scoped rule means anywhere. The engine has honoured it all along
    # (app/run.py _snap); it simply had no way to be set.
    "output.snapshot": {
        # Both write FOOTAGE to the disk the Footage page reads. Grouped, so
        # they read as the pair they are rather than as two of five unrelated
        # outputs.
        "palette": {"cat": "output", "group": "Footage"},
        "category": "output", "rank": 4, "label": "Snapshot",
        "badge": "SNAP",
        "description": "Save a photo of the annotated frame when a decision fires: a Condition or Compare in front of it, or a Formula straight in. Needs the Snapshot switch ON in System Control.",
        "output_type": "snapshot",
        # `oid_tag` burns the object id of whatever caused THIS firing into the
        # picture and its filename. A soft no-op where the chain cannot say
        # which object it was: the rule behind it may be about the zone rather
        # than about anybody in particular, and a checkbox that errors for
        # asking is worse than one that quietly has nothing to add.
        #
        # `crop` also saves a close-up of each object that VIOLATES (the classes
        # of the rule that fired, in its zone) beside the photo, for the Footage
        # page's gallery and the PDF notice -- not of every box in view. The rest
        # are the words the notice prints: `location` (the place),
        # `violation` (what was detected, e.g. "Not holding the handrail") and
        # `notice_title` (the red heading, "VIOLATION NOTICE" when blank).
        # Blank prints "--" or the default, never an invented value. All of it
        # is only about the Snapshot output; none of it changes the photo.
        #
        "data": {"mode": "once", "interval_s": 5, "camera": "",
                 "oid_tag": False, "crop": False, "location": "",
                 "violation": "", "notice_title": ""}},
    "output.record": {
        # Both write FOOTAGE to the disk the Footage page reads. Grouped, so
        # they read as the pair they are rather than as two of five unrelated
        # outputs.
        "palette": {"cat": "output", "group": "Footage"},
        "category": "output", "rank": 4, "label": "Record Clip",
        "badge": "REC",
        "description": "Record a short video clip when a decision fires: a Condition or Compare in front of it, or a Formula straight in. Records up to 5s BEFORE the moment and 5s after. Needs the Record switch ON in System Control.",
        "output_type": "record",
        # BEFORE and AFTER, not one duration. What an operator wants to see is
        # the run-up -- how the forklift got there -- and a clip that starts at
        # the alarm has already missed it.
        #
        # `before_s` is not free and is not pretended to be: it needs a rolling
        # buffer of rendered frames per camera, kept whether or not anything
        # ever fires (see systems/demo/app/capture.py). 0 keeps the old
        # behaviour exactly -- no buffer, no cost.
        "data": {"before_s": 0, "after_s": 5, "duration_s": 5,
                 "mode": "once", "interval_s": 10, "camera": "",
                 "oid_tag": False}},
    # GPIO and Modbus used to be nodes here. They are gone on purpose: wiring
    # this box directly to a pin or a register meant owning a driver, its
    # timing and its failure modes for every board a customer might use. The
    # supported path is MQTT -- publish what happened, and let Node-RED (or
    # any broker client) drive the hardware, which is what those tools are
    # for and what a factory already runs. The ENGINE still understands a
    # hand-written gpio/modbus output, so a config that predates this keeps
    # working; there is simply no way to draw a new one.
}

# The MATH family's entries, built from MATH_NODES rather than typed out
# thirteen times: every one of them is the same node with a different fixed
# operation, and thirteen hand-written copies is thirteen places for the
# defaults to drift apart.
for _mtype, _m in MATH_NODES.items():
    _ops = list(_m["ops"])
    _arity = _m.get("arity")
    # How many handles the node shows before anything is wired. A chaining
    # node starts at two (which is what "combine some values" means); a fixed
    # one shows exactly what its operation takes; the bundled Advanced node
    # follows whichever function is chosen.
    _fixed = (FORMULA_OPS[_ops[0]]["arity"] if _arity == "op"
              else _arity if isinstance(_arity, int) else 2)
    REGISTRY[_mtype] = {
        "palette": {"cat": "math", "group": _m["group"]},
        "category": "analytics", "group": _m["group"], "rank": 3,
        "label": _m["label"], "badge": _m["badge"],
        "description": _m["description"],
        "fn_type": "expr", "zone_kind": None, "metrics": ["value"],
        "handles": list(FORMULA_HANDLES[:max(1, _fixed)]),
        # Port NAMES where the operation has real ones: "Base"/"Exponent"
        # says which way round a power goes, where a/b does not.
        **({"port_labels": _m["handles"]} if _m.get("handles") else {}),
        # The picker, in this node's own words -- absent entirely when there
        # is only one operation to pick.
        **({"ops": {k: {"label": (_m.get("labels") or {}).get(
                            k, FORMULA_OPS[k]["label"]),
                        "arity": FORMULA_OPS[k]["arity"],
                        "hint": FORMULA_OPS[k].get("hint", "")}
                    for k in _ops}} if len(_ops) > 1 else {}),
        **({"chain": True, "max_inputs": FORMULA_MAX_INPUTS}
           if _arity is None else {}),
        # Which operations of a chaining node actually chain (Logic: And and
        # Or do, Not does not).
        **({"chain_ops": list(_m["chain_ops"])} if _m.get("chain_ops") else {}),
        # Pulled from the palette, still loads and compiles (their job is a
        # Custom Formula function now).
        **({"deprecated": True} if _m.get("deprecated") else {}),
        # Set Variable's first input IS the variable being set -- it comes
        # from a wire by definition, and no number typed there could stand
        # in. Said on the entry because a one-operation node has no `ops`
        # map for the form to read it off.
        **({"wire_only": True} if len(_ops) == 1
           and FORMULA_OPS[_ops[0]].get("wire_only") else {}),
        # The kinds of value this node may store, where it stores one at all
        # (Set Variable). Served so the form offers exactly what the compiler
        # accepts -- the first entry is the default, and it is the behaviour
        # the node had before the choice existed.
        **({"value_types": list(_m["value_types"])}
           if _m.get("value_types") else {}),
        "math": True,
        "data": {"fnId": "", "op": _ops[0],
                 **({"inputs": 2} if _arity is None else {}),
                 **({"value_type": _m["value_types"][0]}
                    if _m.get("value_types") else {}),
                 "a_value": 0, "b_value": 0, "c_value": 0},
    }
del _mtype, _m, _ops, _arity, _fixed

#: What KIND of number a metric is, and therefore how it should be drawn.
#:
#: A raw stat key carries none of this: `fn_x_avg_s` and `fn_x_total` look
#: identical to a chart builder, so today an operator has to know that one wants
#: a seconds-unit line and the other a big number. The compiler DOES know -- it
#: chose the function type -- so it says so, and the palette arrives with the
#: right widget already configured.
#:
#:   gauge_count -- an instantaneous level (how many are in there NOW)
#:   total       -- a monotonic counter (only interesting as a slope)
#:   duration    -- seconds
#:   event_count -- spikes; the level between them means nothing
#:   constant    -- a number the operator typed
_ROLE_WIDGET = {
    "gauge_count": {"kind": "area", "source": "history", "range": "24h"},
    "total":       {"kind": "tile", "source": "stat"},
    "duration":    {"kind": "line", "source": "history", "range": "24h", "unit": "s"},
    "event_count": {"kind": "bar",  "source": "history", "range": "24h"},
    "constant":    {"kind": "tile", "source": "stat"},
}


def metric_role(fn_type, metric):
    """Which of the roles above a function's metric is."""
    if metric.endswith("_s") or metric in ("last_s", "avg_s", "live_s", "max_s"):
        return "duration"
    if fn_type in ("zone_unique", "frame_unique") and metric == "new":
        return "event_count"
    if fn_type == "object_detect":
        # `found` is a 0/1 level and `count` a live level -- both read as an
        # area over time; arrivals are spikes.
        return "event_count" if metric == "arrived" else "gauge_count"
    if fn_type in ("zone_count", "frame_count") and metric == "count":
        return "gauge_count"
    # today/week/month are already period sums; charting them over history
    # would draw a staircase of the same number, so they are tiles.
    if metric in ("today", "week", "month"):
        return "total"
    if fn_type == "zone_dwell" and metric == "current":
        return "gauge_count"
    if fn_type == "line_count" and metric == "occupancy":
        return "gauge_count"
    if fn_type == "expr":
        return "constant"
    return "total"


#: category -> categories an edge may point INTO. filter->filter chains (AND of
#: class sets is meaningless, so it unions); analytics->analytics only for expr
#: (a formula may reference another function's output).
_ALLOWED_TARGETS = {
    # Canvas furniture. A Link in may feed anything a real value feeds,
    # because after resolve_links it IS whatever fed its Link out; per-type
    # rules below stop a Comment being wired at all.
    "common": {"common", "filter", "zone", "analytics", "logic",
               "output", "dashboard", "variable"},
    "source": {"filter", "zone", "analytics", "common"},
    "filter": {"filter", "zone", "analytics", "common"},
    "zone": {"analytics", "common"},
    # analytics -> variable: a rule's output may be wired INTO a Variable,
    # which then holds it under a place on the canvas (the per-type rules
    # below keep this to the holder kind only).
    "analytics": {"dashboard", "output", "analytics", "logic", "common",
                  "variable"},
    # Variables publish values like analytics do: usable on dashboards, in
    # formulas (expr) and behind outputs/alerts.
    "variable": {"dashboard", "output", "analytics", "logic", "common",
                 "variable"},
    # A When condition sits between a value and what reacts to it. logic ->
    # logic so a Delay can follow a When ("when it goes over 5, wait 10s,
    # then send") without needing a rule in between.
    # ...and into analytics/variable, because a BRANCH IS A VALUE: 1 while it
    # holds, 0 otherwise. Without this a branch could only end in an output,
    # so "if the bay is busy, add 1 to the tally" had nowhere to go -- the
    # answer had to be rebuilt as a formula that repeated the condition.
    # ...and "common", which is canvas furniture -- a Link. Every other value
    # category already reaches one, and the canvas has always let this wire be
    # drawn: leaving it out here meant a Link after a When could be drawn and
    # then refused on save, which is the worst place to learn about a rule.
    "logic": {"output", "dashboard", "logic", "analytics", "variable", "common"},
    # A Query READS the tables wired into it. Its result goes to a card, or
    # into a Variable -- which is how a number that only the database knows
    # ("rejects this shift", "parts since the last changeover") becomes
    # something a formula, a When and an alert can all read. The per-type rule
    # below keeps it to the HOLDER kind of variable, like every other value.
    # A CSV node shares this category and reaches an Email or a Telegram, but
    # that is granted by a per-type rule ABOVE the matrix rather than here: a
    # Query itself must not reach a message, because there would be nothing to
    # attach and no way to say which query a message wired to several meant.
    "query": {"dashboard", "common", "variable"},
    "dashboard": set(),
    # An Alert is the trigger, and everything that happens BECAUSE of one is
    # wired after it: a Snapshot, an Email, a Data Table row, a card. The
    # compiler already treats it that way -- upstream_fn is transparent
    # through an Alert, so a node wired after one binds to the rule the Alert
    # fires on -- but the wiring rule said an output may reach nothing at
    # all, so the wire could not be drawn. An Alert with an output port
    # nothing could legally accept is what left Snapshots floating loose
    # beside the graph they belong to.
    #
    # The per-type rule below keeps this to the ALERT. No other output
    # publishes anything to bind to, so a wire out of an Email would draw
    # cleanly and then fail to compile, which is a worse answer than refusing
    # it while the wire is still in your hand.
    # ...and into analytics, so the LOGIC after an alarm can live in a Formula
    # rather than being rebuilt as a second condition. An Alert wired into a
    # Formula stands for its rule's `active` (see _condition_alias), which is
    # what makes "two alarms and a count" one expression instead of three
    # nodes agreeing by hand. The per-type rule below keeps it to a Formula or
    # a Running Total -- the only analytics nodes that take a value in.
    "output": {"output", "dashboard", "common", "analytics"},
}


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def path_for(slug):
    return os.path.join(str(config_mod.workflows_dir()), f"{slug}.json")


def exists(slug):
    return os.path.exists(path_for(slug))


def _blank_store():
    return {"version": 3, "workflows": []}


#: The one workflow a system has. There used to be a list of up to 50, each
#: separately enabled, which meant an operator had to decide WHERE a rule lived
#: before they could write it -- and then remember. One canvas, one place to
#: look; a rule that should not run is disconnected, not filed elsewhere.
MAIN_ID = "main"
MAIN_NAME = "Workflow"

#: Vertical gap between merged graphs, so nothing lands on top of anything.
_MERGE_GAP = 240


def _merge_workflows(entries):
    """Fold several workflows into one graph.

    Node ids are unique per workflow but not across them, so colliding ids are
    re-minted and their edges rewritten. `fnId`s are left ALONE: they name the
    published stat keys, and rewriting one silently breaks every widget, alert
    and history series bound to it. Each graph is offset below the last so the
    merged canvas reads as separate clusters rather than a pile.

    A disabled workflow merges in with its nodes marked `disabled`, which the
    compiler skips. Deleting it instead would destroy work whose only crime was
    being switched off.
    """
    nodes, edges, seen = [], [], set()
    offset = 0.0
    for w in entries:
        graph = w.get("graph") or {}
        w_nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
        w_edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
        disabled = not w.get("enabled", True)
        rename = {}
        for n in w_nodes:
            nid = str(n.get("id") or "")
            if nid in seen:
                new = f"{nid}_{len(seen)}"[:40]
                rename[nid] = new
                nid = new
            seen.add(nid)
            pos = n.get("position") or {}
            data = dict(n.get("data") or {})
            if disabled:
                data["disabled"] = True
            nodes.append({**n, "id": nid, "data": data,
                          "position": {"x": float(pos.get("x") or 0),
                                       "y": float(pos.get("y") or 0) + offset}})
        for e in w_edges:
            src = rename.get(e.get("source"), e.get("source"))
            tgt = rename.get(e.get("target"), e.get("target"))
            eid = str(e.get("id") or f"{src}-{tgt}")
            if eid in seen:
                eid = f"{eid}_{len(seen)}"[:60]
            seen.add(eid)
            edges.append({**e, "id": eid, "source": src, "target": tgt})
        if w_nodes:
            offset = max(n["position"]["y"] for n in nodes) + _MERGE_GAP
    return {"nodes": nodes, "edges": edges}


def load_store(slug):
    """The system's workflow store, always holding exactly ONE workflow:
    {version: 3, workflows: [{id: "main", name, graph, ...}]}.

    Two older shapes migrate on read, because nothing an operator built may be
    lost to a format change:
      * v1 -- the whole file was one bare graph.
      * v2 -- a list of separate workflows; they are merged onto one canvas
        (see _merge_workflows) and the pre-merge file is kept beside it as
        <slug>.v2.bak.json, since the merge is one-way.
    Every shape comes back with its Class Filters named (migrate_store), or
    the first save and the recompile at start (ensure_compiled) refuse it.
    A corrupt file reads as an empty store rather than a 500."""
    try:
        with open(path_for(slug), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    if isinstance(data.get("workflows"), list):
        entries = [w for w in data["workflows"] if isinstance(w, dict)]
        if len(entries) == 1 and entries[0].get("id") == MAIN_ID:  # the saved shape
            return class_filter_service.migrate_store({"version": 3, "workflows": entries})
        if not entries:
            return _blank_store()
        merged = {
            "id": MAIN_ID,
            "name": entries[0].get("name") if len(entries) == 1 else MAIN_NAME,
            "description": entries[0].get("description", "") if len(entries) == 1 else "",
            "enabled": True,
            "author": entries[0].get("author", ""),
            "created": entries[0].get("created", 0),
            "modified": entries[0].get("modified", 0),
            "graph": _merge_workflows(entries),
        }
        if len(entries) > 1:
            _backup_v2(slug, data)
        return class_filter_service.migrate_store({"version": 3, "workflows": [merged]})

    if isinstance(data.get("nodes"), list):
        # v1: the whole file was one graph.
        return class_filter_service.migrate_store({"version": 3, "workflows": [{
            "id": MAIN_ID, "name": MAIN_NAME, "description": "",
            "enabled": True, "author": "", "created": 0, "modified": 0,
            "graph": {"nodes": data.get("nodes"), "edges": data.get("edges") or []},
        }]})
    return None


def _backup_v2(slug, data):
    """Keep the pre-merge store. The merge cannot be undone from the merged
    file, and "my four workflows became one" is not something to discover with
    no way back. Written once -- an existing backup is never overwritten."""
    target = path_for(slug).replace(".json", ".v2.bak.json")
    if os.path.exists(target):
        return
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError:                                         # noqa: BLE001
        pass                        # a missing backup must not block loading


def save_store(slug, store):
    """Atomic write, same tmp+replace pattern as Builder layouts."""
    target = path_for(slug)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    os.replace(tmp, target)


def workflow_meta(w, zones=None):
    """Identity + node counts for the canvas. Status is the route's business
    (it needs app state); this is pure shape."""
    graph = w.get("graph") or {}
    nodes = graph.get("nodes") or []
    by_cat = {}
    for n in nodes:
        spec = REGISTRY.get(n.get("type"))
        if spec:
            by_cat[spec["category"]] = by_cat.get(spec["category"], 0) + 1
    alerts = sum(1 for n in nodes if n.get("type") == "output.alert"
                 or (n.get("data") or {}).get("trigger"))
    return {
        "id": w.get("id"), "name": w.get("name") or w.get("id"),
        "description": w.get("description") or "",
        "enabled": bool(w.get("enabled", True)),
        "author": w.get("author") or "",
        "created": w.get("created") or 0,
        "modified": w.get("modified") or 0,
        "nodes": len(nodes),
        "variables": by_cat.get("analytics", 0),
        "outputs": by_cat.get("output", 0),
        "dashboards": by_cat.get("dashboard", 0),
        "zones": by_cat.get("zone", 0),
        "alerts": alerts,
    }


def compile_store(store, zones, classes=None, active_classes=None, capture=None,
                  tables=None, cameras=None):
    """Compile every ENABLED workflow and merge the artifacts.

    Function ids must be unique across the whole store -- two workflows both
    publishing fn_occupancy_x would silently fight -- so uniqueness is enforced
    globally here. Returns {functions, outputs, pages, errors, warnings};
    errors/warnings carry `workflow` so the table can badge the right row.
    pages is [{workflow_id, name, widgets}] -- one Builder page per workflow
    with dashboard nodes."""
    functions, outputs, pages, catalog = [], [], [], []
    commands, watches = [], []
    # What runs without detection, across every workflow (compile_graph's
    # frames_free), and which drawn nodes wait for it.
    free = {"functions": [], "outputs": [], "version": LOGIC_VERSION}
    waits = []
    plans = []
    #: (drawn graph, plan) per ON/OFF point that reaches anything.
    gate_plans = []
    #: camera -> the workflow whose Pre-process already claimed it. One camera
    #: has one picture, so the "one node per camera" rule compile_graph keeps
    #: inside a canvas has to hold ACROSS canvases too -- otherwise two
    #: workflows both preprocessing camera 2 would come down to which one the
    #: store happens to list first.
    preprocess, pre_owner = [], {}
    #: Snapshot streams, every canvas's: which camera detects only on a snap,
    #: and what starts one. Pooled by camera where it is written
    #: (_snapshot_by_camera), so two canvases may feed one stream.
    snapshot = []
    errors, warnings = [], []
    declared_tables = {}
    seen_fids = {}
    for w in store.get("workflows") or []:
        if not w.get("enabled", True):
            continue
        graph = clean_graph(w.get("graph") or {"nodes": [], "edges": []})
        ensure_fn_ids(graph)
        c = compile_graph(graph, zones, classes, active_classes, capture, tables,
                          cameras)
        for e in c["errors"]:
            errors.append({**e, "workflow": w["id"]})
        for x in c["warnings"]:
            warnings.append({**x, "workflow": w["id"]})
        for fn in c["functions"]:
            if fn["id"] in seen_fids:
                errors.append({"node": "", "workflow": w["id"],
                               "message": f"the name {fn['id']!r} is used by two "
                                          f"nodes (also in workflow "
                                          f"{seen_fids[fn['id']]!r}) - every "
                                          f"variable needs its own name, so "
                                          f"rename one of them"})
                continue
            seen_fids[fn["id"]] = w["id"]
            functions.append(fn)
        outputs.extend(c["outputs"])
        # Each canvas's STRUCTURAL half of the logic-mode decision, kept for
        # the system-wide pass below: a Formula on one canvas may read a rule
        # on another, and judged per canvas it read as "nobody publishes it".
        plans.append((graph, (c.get("frames_free") or {}).get("_plan"),
                      c.get("class_filters") or []))
        gate_plans.extend((graph, g) for g in c.get("state_gates") or [])
        for pre in c.get("preprocess") or []:
            if pre["camera"] in pre_owner:
                errors.append({"node": "", "workflow": w["id"],
                               "message": "another workflow "
                                          f"({pre_owner[pre['camera']]!r}) already "
                                          "pre-processes this camera's picture"})
                continue
            pre_owner[pre["camera"]] = w["id"]
            preprocess.append(pre)
        snapshot.extend(c.get("snapshot") or [])
        commands.extend(c.get("commands") or [])
        watches.extend(c.get("watches") or [])
        catalog.extend(c.get("catalog") or [])
        # Tables are a system-wide registry, not a per-workflow one: two
        # canvases may each fill part of the same table, so their column
        # declarations union rather than overwrite.
        for key, want in (c.get("tables") or {}).items():
            have = declared_tables.setdefault(
                key, {"name": want["name"], "columns": []})
            names = {col["name"] for col in have["columns"]}
            for col in want["columns"]:
                if col["name"] not in names:
                    have["columns"].append(col)
                    names.add(col["name"])
        if c["widgets"]:
            pages.append({"workflow_id": w["id"],
                          "name": w.get("name") or w["id"],
                          "widgets": c["widgets"]})
    # WHAT RUNS WITHOUT DETECTION, decided once for the whole system: the
    # structural waits of every canvas, then what they READ judged against
    # every rule the system compiles (logic_decide).
    base, out_base = set(), set()
    for _g, plan, _cf in plans:
        if plan:
            base.update(plan["base"])
            out_base.update(plan["out_base"])
    needs, waiting_outputs = logic_decide(functions, outputs, base, out_base)
    free["functions"] = [f["id"] for f in functions if f["id"] not in needs]
    free["outputs"] = [o["id"] for o in outputs
                       if o.get("id") not in waiting_outputs]
    for graph, plan, _cf in plans:
        if plan:
            waits.extend(sorted(canvas_waits(
                graph["nodes"], graph["edges"],
                plan_waiting_nodes(plan, needs, waiting_outputs))))
    return {"functions": functions, "outputs": outputs, "pages": pages,
            "commands": commands,
            "watches": watches,
            "preprocess": preprocess,
            # Only when a stream is in Snapshot mode, so every older store
            # compiles to exactly the keys it always did. Without THIS line the
            # snapshot a canvas compiled to was lost at the merge, apply_artifacts
            # found none, and cleared the camera it had just been written to.
            **({"snapshot": snapshot} if snapshot else {}),
            "catalog": catalog, "tables": declared_tables,
            "frames_free": free, "waits_for_detection": waits,
            "class_filters": class_filter_service.decide(  # what a paused one holds
                [p for *_x, cf in plans for p in cf], functions, outputs),
            # What each ON/OFF point gates, widened the same way, and the
            # drawn nodes the canvas labels "runs when ...".
            **_point_gates(gate_plans, functions, outputs),
            "errors": errors, "warnings": warnings}


def _point_gates(gate_plans, functions, outputs):
    """{"state_gates": [{gate, functions, outputs}], "runs_when": {drawn
    node id: "AI detection is off"}} from compile_graph's per-point plans
    (state_points.plans): each widened to whatever READS what it holds
    (class_filter.decide), and mapped back onto the drawn canvas the way the
    waits are (canvas_waits). Empty dicts when no point reaches anything,
    so a graph without a wired point compiles key for key as before."""
    if not gate_plans:
        return {"state_gates": [], "runs_when": {}}
    decided = class_filter_service.decide(
        [p for _g, p in gate_plans], functions, outputs)
    gates, runs_when = [], {}
    for (graph, plan), held in zip(gate_plans, decided):
        gates.append({"gate": plan["gate"], "functions": held["functions"],
                      "outputs": held["outputs"]})
        for nid in sorted(canvas_waits(graph["nodes"], graph["edges"],
                                       plan["nodes"])):
            runs_when.setdefault(nid, [])
            if plan["name"] not in runs_when[nid]:
                runs_when[nid].append(plan["name"])
    return {"state_gates": gates,
            "runs_when": {k: " and ".join(v) for k, v in runs_when.items()}}


# ---------------------------------------------------------------------------
# graph validation
# ---------------------------------------------------------------------------

def _slug(value):
    return "".join(c if c.isalnum() else "_"
                   for c in str(value or "").strip().lower()).strip("_")[:40]


# ---------------------------------------------------------------------------
# blueprint -> expression
#
# The Function Block node holds a NESTED graph (its "blueprint") of primitive
# nodes -- values, arithmetic, comparisons, if/else, switch, clamp, range.
# It compiles to the SAME whitelisted expression the Custom Formula uses, so
# the engine gains no new capability and documents stay data. Anything
# stateful (delay) lives on the block's trigger cooldown, not in here.
# ---------------------------------------------------------------------------

#: The engine's cap on one expression (functions.validate_expr). Duplicated
#: here so a blueprint can fail with a message about BLUEPRINTS rather than
#: about a string the operator never typed.
MAX_EXPR_CHARS = 500

_BP_MATH_INFIX = {"add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%"}
_BP_MATH_CALLS2 = ("min", "max", "pow")
_BP_MATH_CALLS1 = ("abs", "round", "sqrt", "ceil", "floor")
_BP_CMP = (">", ">=", "<", "<=", "==", "!=")


def _bp_num(v, what):
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} needs a number")
    return repr(int(f) if f == int(f) else f)


def blueprint_to_expr(bp):
    """Compile a Function Block's nested graph to one expression string.
    Raises ValueError with a human message on any structural problem."""
    nodes = {str(n.get("id")): n for n in (bp or {}).get("nodes") or []
             if isinstance(n, dict)}
    if not nodes:
        raise ValueError("the blueprint is empty - double-click the node to build it")
    ins = {}
    for e in (bp or {}).get("edges") or []:
        if not isinstance(e, dict):
            continue
        ins.setdefault(str(e.get("target")), {})[e.get("targetHandle") or "in"] = \
            str(e.get("source"))
    outs = [n for n in nodes.values() if n.get("type") == "bp.out"]
    if len(outs) != 1:
        raise ValueError("the blueprint needs exactly one Output node")

    path = set()

    def emit(nid):
        if nid in path:
            raise ValueError("the blueprint has a loop")
        n = nodes.get(nid)
        if n is None:
            raise ValueError("an edge points at a deleted node")
        path.add(nid)
        try:
            t, d, h = n.get("type"), n.get("data") or {}, ins.get(nid, {})

            def req(handle, label=None):
                if handle not in h:
                    raise ValueError(
                        f"'{d.get('label') or t}' is missing its "
                        f"'{label or handle}' input")
                return emit(h[handle])

            if t == "bp.const":
                return _bp_num(d.get("value", 0), "the Number node")
            if t == "bp.stat":
                key = str(d.get("key") or "").strip()
                if not key or not key.replace("_", "a").isalnum():
                    raise ValueError("pick a variable in the Value node")
                return key
            if t == "bp.math":
                op = str(d.get("op") or "add")
                if op in _BP_MATH_INFIX:
                    return f"({req('a')} {_BP_MATH_INFIX[op]} {req('b')})"
                if op in _BP_MATH_CALLS2:
                    return f"{op}({req('a')}, {req('b')})"
                if op in _BP_MATH_CALLS1:
                    return f"{op}({req('a')})"
                raise ValueError(f"unknown math operation {op!r}")
            if t == "bp.compare":
                op = str(d.get("op") or ">")
                if op not in _BP_CMP:
                    raise ValueError("unknown comparison")
                return f"({req('a')} {op} {req('b')})"
            if t == "bp.logic":
                op = str(d.get("op") or "and")
                if op == "not":
                    return f"(not {req('a')})"
                if op in ("and", "or"):
                    return f"({req('a')} {op} {req('b')})"
                raise ValueError("unknown logic operation")
            if t == "bp.if":
                return (f"iff({req('cond', 'condition')}, "
                        f"{req('then')}, {req('else')})")
            if t == "bp.clamp":
                return f"clamp({req('v')}, {req('lo')}, {req('hi')})"
            if t == "bp.between":
                v, lo, hi = req("v"), req("lo"), req("hi")
                return f"(({v} >= {lo}) and ({v} <= {hi}))"
            if t == "bp.switch":
                v = emit(h["v"]) if "v" in h else None
                if v is None:
                    raise ValueError("the Switch node is missing its 'v' input")
                expr = _bp_num(d.get("default", 0), "the Switch default")
                for case in reversed(list(d.get("cases") or [])[:12]):
                    when = _bp_num((case or {}).get("when", 0), "a Switch case")
                    then = _bp_num((case or {}).get("then", 0), "a Switch case")
                    expr = f"iff(({v} == {when}), {then}, {expr})"
                return expr
            if t == "bp.table":
                # A LOGIC TABLE: rows of "when these inputs look like this,
                # the answer is that". The same thing people reach for a
                # spreadsheet to express, and far easier to read than the
                # nest of iff()s it compiles to -- which is exactly what it
                # compiles to, so the engine gains nothing new.
                #
                # Each row tests the wired inputs a/b/c against its own
                # values; a blank cell means "don't care". First match wins,
                # read top to bottom, and `default` is the answer when no row
                # matches. Written innermost-first, hence the reversal.
                srcs = {k: emit(h[k]) for k in ("a", "b", "c") if k in h}
                if not srcs:
                    raise ValueError("the Logic Table has nothing wired into it")
                expr = _bp_num(d.get("default", 0), "the Logic Table default")
                for row in reversed(list(d.get("rows") or [])[:24]):
                    row = row or {}
                    tests = []
                    for key, value in srcs.items():
                        cell = row.get(key)
                        if cell is None or str(cell).strip() == "":
                            continue        # blank cell = this input is ignored
                        op = str(row.get(f"{key}_op") or "==").strip()
                        if op not in _BP_CMP:
                            raise ValueError(f"a Logic Table row has an unknown "
                                             f"comparison {op!r}")
                        tests.append(f"({value} {op} "
                                     f"{_bp_num(cell, 'a Logic Table cell')})")
                    if not tests:
                        # A row that tests nothing matches everything, so it
                        # would silently shadow every row below it.
                        raise ValueError("a Logic Table row is completely "
                                         "blank - fill a cell or remove it")
                    cond = " and ".join(tests)
                    then = _bp_num(row.get("then", 0), "a Logic Table result")
                    expr = f"iff(({cond}), {then}, {expr})"
                return expr
            raise ValueError(f"unknown blueprint node {t!r}")
        finally:
            path.discard(nid)

    root = ins.get(str(outs[0].get("id")), {})
    src = root.get("v") or root.get("in")
    if not src:
        raise ValueError("wire a value into the Output node")
    expr = emit(src)
    # A blueprint compiles to ONE expression, and the engine caps those at 500
    # characters (functions.validate_expr). A big Logic Table or a deep chain
    # reaches that easily, and the operator would otherwise meet "expression
    # too long" at save with nothing pointing back at the blueprint they were
    # building. Say it here, where the fix is: fewer rows, or split the block.
    if len(expr) > MAX_EXPR_CHARS:
        raise ValueError(
            f"this blueprint is too big to compile ({len(expr)} characters, "
            f"limit {MAX_EXPR_CHARS}) - use fewer rows/cases, or split it "
            "into two Function nodes and wire one into the other")
    return expr


#: How many variables one system may declare. Sanity bound, not a resource
#: one -- a list past this is a config mistake.
MAX_VARIABLES = 100


def clean_variables(items):
    """The workflow's DECLARED variables: [{name, var}], validated.

    A declaration is a name and nothing else -- no type, no starting value.
    The engine's stats are dynamically typed exactly like the Python
    underneath them, and a variable nobody has stored into simply is not
    there yet (expressions read it as 0). Declaring is what makes the name
    appear in every picker; a node is only involved when something READS it
    or STORES into it.
    """
    if not isinstance(items, list):
        raise ValueError("variables must be a list")
    if len(items) > MAX_VARIABLES:
        raise ValueError(f"at most {MAX_VARIABLES} variables")
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:60]
        var = _slug(item.get("var") or item.get("name"))
        if not name or not var or var in seen:
            continue
        seen.add(var)
        out.append({"name": name, "var": var})
    return out


def clean_graph(graph):
    """Structurally clean a graph from the browser: known node types only,
    clamped sizes, edges that reference real nodes. Raises ValueError on a
    shape that cannot be stored at all; everything softer is a compile error."""
    if not isinstance(graph, dict):
        raise ValueError("graph must be an object")
    nodes_in = graph.get("nodes")
    edges_in = graph.get("edges")
    if not isinstance(nodes_in, list) or not isinstance(edges_in, list):
        raise ValueError("graph needs nodes[] and edges[]")
    if len(nodes_in) > MAX_NODES:
        raise ValueError(f"at most {MAX_NODES} nodes")
    if len(edges_in) > MAX_EDGES:
        raise ValueError(f"at most {MAX_EDGES} edges")

    nodes, seen = [], set()
    for n in nodes_in:
        if not isinstance(n, dict):
            continue
        ntype = str(n.get("type") or "")
        if ntype not in REGISTRY:
            continue                     # an unknown type is dropped, not fatal
        nid = str(n.get("id") or "").strip()[:40]
        # DROPPED, not fatal -- the same treatment the unknown type above
        # gets. Raising here made one malformed graph close the whole editor:
        # the page 500'd with "internal error - see Logs" and the only way to
        # repair the graph was the editor that would not open. A duplicate id
        # is unreachable anyway (every lookup finds the first), so keeping the
        # first and dropping the rest loses nothing that was ever addressable,
        # and the operator gets a canvas they can actually fix.
        if not nid or nid in seen:
            continue
        seen.add(nid)
        pos = n.get("position") or {}
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        nodes.append({
            "id": nid, "type": ntype,
            "position": {"x": float(pos.get("x") or 0), "y": float(pos.get("y") or 0)},
            "data": data,
        })

    ids = {n["id"] for n in nodes}
    edges, eseen = [], set()
    for e in edges_in:
        if not isinstance(e, dict):
            continue
        src, tgt = str(e.get("source") or ""), str(e.get("target") or "")
        if src not in ids or tgt not in ids or src == tgt:
            continue                     # dangling edges are dropped silently
        eid = str(e.get("id") or f"e_{src}_{tgt}")[:80]
        if eid in eseen:
            continue
        eseen.add(eid)
        edges.append({"id": eid, "source": src, "target": tgt,
                      "sourceHandle": str(e.get("sourceHandle") or "") or None,
                      "targetHandle": str(e.get("targetHandle") or "") or None})

    return {"version": SCHEMA_VERSION, "nodes": nodes, "edges": edges}


#: WHO OWNS WHAT on a dashboard node. One card definition, two editors:
#:
#:   the canvas node  -- identity (title, caption, shape) and DATA: the metric,
#:                       the window, the unit, and the threshold bands;
#:   the Builders     -- the LOOK (template, colours, text sizes, animation,
#:                       per-day colouring) and the placement.
#:
#: The node STORES all of it, because the node is what survives a compile. But
#: only one side may WRITE each knob, or the two panels are a question ("which
#: one wins?") whose answer -- whichever was saved last -- nobody can see. So
#: the canvas save keeps the look it was given (keep_card_look) and the
#: Builders write theirs through card-style (apply_card_style).
#:
#: The card's QUESTION (CARD_QUESTION_FIELDS: shape, "Work out", one bar per,
#: period, interval, rows, unit) is offered on both sides too, with the same
#: words; the last edit wins, and keep_builder_edits stops a stale canvas tab
#: from counting as an edit.
#:
#: The bands and a verdict's two words are offered on BOTH sides: a limit is a
#: statement about the metric, and "Empty" / "Full" is what the metric's 0
#: and 1 MEAN, so the canvas edits them beside the metric and the Builders
#: edit them beside the source. Last edit wins, whichever side made it. (The
#: customer's four verdict cards said COMPLIANT / VIOLATION after the node
#: said Empty / Full because they were hand-made tiles with no link to the
#: node at all -- and the Builder had no way to say the words either.)
CARD_LOOK_FIELDS = ("template", "bg", "color", "label_size", "value_size",
                    "anim", "anim_replay", "by_day") + _chartmodel.LOOK_FIELDS
#: `show_title` (Tok Aba, 2026-10-14) is identity too: whether the card's
#: title bar is drawn is offered on the node and in the Builders, beside the
#: Title they both edit.
#: `align` / `valign` likewise: where a stat sits in its card, offered on the
#: node and in the Builders beside the title (builder.STAT_LAYOUT_KINDS).
CARD_IDENTITY_FIELDS = ("name", "sub", "show_title", "align", "valign")
#: `series` is here rather than with the LOOK for the same reason the verdict
#: words are: a line's name and colour is a statement about what that line IS,
#: which belongs beside the metric on the canvas and beside the source in the
#: Builder. Filed with the look instead, keep_card_look would make the canvas
#: unable to write it at all -- and the ports on the chart node are drawn from
#: exactly this field.
def _verdict_words(fn, d):
    """(ok, alert) words of a Verdict Card. The card's own words win; but a
    card still carrying the stock COMPLIANT / VIOLATION behind an Anomaly node
    reads GOOD / NG, because those were never chosen by anybody."""
    ok = str(d.get("ok_label") or "").strip()
    alert = str(d.get("alert_label") or "").strip()
    if bool(((fn or {}).get("params") or {}).get("anomaly")):
        if ok in ("", "COMPLIANT"):
            ok = "GOOD"
        if alert in ("", "VIOLATION"):
            alert = "NG"
    return ok or "COMPLIANT", alert or "VIOLATION"


CARD_DATA_STYLE_FIELDS = ("bands", "ok_label", "alert_label", "series")
#: The QUESTION a card asks, in the node's own field names: its shape ("Show
#: as"), what it works out ("Work out"), of which column, one bar per what,
#: over which period or time range, at what interval, how many rows, in what
#: unit. Both editors show these fields with the same words
#: (frontend cardFields.jsx), so both must be able to WRITE them -- the
#: Builders used to offer them on a compiled card and send them nowhere: the
#: partial layout save keeps the stored wf_* row (builder.merge_partial) and
#: card-style did not carry them, so "Work out: total rows" picked on
#: Analytics was back to "average value" at the next workflow save.
#:
#: The canvas keeps editing them too, so a Builder write is STAMPED on the node
#: (BUILDER_EDIT_KEY) and keep_builder_edits stops a canvas tab opened before
#: that write from putting the old answer back. The wiring is never here: a
#: card's source, its key and its series inputs are the wires into the node.
CARD_QUESTION_FIELDS = ("kind", "agg", "field", "group_by", "bucket", "range",
                        "interval", "limit", "unit",
                        # A status pill's rule, "Alert when <op> <value>"
                        # (its two words are ok_label/alert_label, which
                        # travel with the verdict's). The Builder offered
                        # it on a compiled pill and it reached nothing.
                        "op", "value",
                        # "Pick dates...": a chart's exact window, when its
                        # range is "custom" (services/timeaxis.py).
                        "range_from", "range_to",
                        # The chart builder's model (services/chartmodel.py):
                        # the X axis type, the interval preset and the
                        # chart's Date column. group_by / agg / bucket /
                        # range above are its X field, Y, step and range.
                        "x_type", "time_interval", "ts_field",
                        # A card's ONE Date range, for a card showing a live
                        # rule's value: which counter it reads ("Count over",
                        # _windowed_metric). Recorded cards use `range`.
                        "window")
#: Every field both editors write, and so a stale canvas tab could re-send
#: over a Builder edit: the question, and the card's name, caption, verdict
#: words and threshold values (shape coverage audit, 2026-10-12: a verdict's
#: words set in the Builder went back to COMPLIANT on the next save from a
#: canvas tab opened before). The look is not here -- the canvas cannot write
#: it at all (keep_card_look).
BUILDER_STAMPED_FIELDS = CARD_QUESTION_FIELDS + (
    "name", "sub", "show_title", "align", "valign", "ok_label", "alert_label", "bands",
    # A Class Filter's On / Off, written by the dashboard's Class filter
    # card (class_filter.set_node_running).
    "running")
#: Where a Builder write of a question field is recorded on the node:
#: {"rev": n, "was": {field: [[rev, value before that write], ...]}}. Data the
#: canvas carries round untouched, which is how a save can tell a tab that saw
#: the write (same rev) from one opened before it.
BUILDER_EDIT_KEY = "builder_edit"

#: A card's threshold bands are a picture: they colour the value axis and say
#: nothing to the rest of the graph. But somebody who draws a line at 80 has
#: DECIDED something at 80 -- and had to go and say it a second time, in a
#: Compare or a Watch, keeping one number in two places that drift apart.
#:
#: So a band edge compiles to a rule of its own, named after the card, raising
#: an alert and publishing 1/0 that any formula, branch or card can read. It
#: is live: the rule reads the stat the card reads, so it answers the instant
#: the value crosses rather than on a sampler's timer.
#:
#: `band_alert` on the node decides what the rule DOES:
#:
#:   "value"  the default. The rule exists and publishes 1/0, so it is in the
#:            pickers, the formulas and the palette -- there to be used, and
#:            silent until somebody uses it. Arming an alert nobody asked for,
#:            on a box already running in the field, is not a default's to do.
#:   "alert"  ...and raises an event when the line is crossed.
#:   "off"    no rule at all: a band that really is only a picture.
BAND_ALERT_MODES = ("value", "alert", "off")

#: How long a band alert stays quiet after firing. A value hovering ON the
#: line would otherwise raise an event per frame, which is the one way this
#: feature could make a box unusable.
BAND_ALERT_COOLDOWN_S = 60


def band_alerts(node, data, key, seen):
    """The rules a card's threshold lines stand for. [] when it has none.

    One rule per EDGE, not one per card: N lines cut the axis into N+1 bands,
    and "over 50" and "over 80" are two different things to know. With one
    line the rule simply takes the card's name, because that is what the
    operator would have called it anyway; with several it says which line it
    is, under the same name, so Debug and the events log group them the way a
    branch node's ways out are grouped.
    """
    mode = str((data or {}).get("band_alert") or "value")
    if mode == "off":
        return []
    if not key:
        # No LIVE value to compare. A card fed by a Data Table, a Query or the
        # event store draws rows that were recorded, and the line is drawn
        # across those -- there is no number in the stats dict for a rule to
        # watch. Quietly, on purpose: the card WORKS, the lines colour it as
        # drawn, and a warning on every save of a perfectly good chart is noise
        # about a rule the operator never asked for.
        return []
    edges = (builder_service.clean_bands((data or {}).get("bands")) or {}).get("at") or []
    if not edges:
        return []
    # A chart that AGGREGATES draws a different number from the one the stat
    # holds: per-day totals, an average per bucket, a count per group. The
    # line is drawn across the drawn series, so a rule comparing the live
    # value to it would be a different question wearing the same number --
    # and would fire, or fail to, for reasons nobody could see on the chart.
    # The lines still colour it; there is simply no honest live rule to make,
    # and the chart is not broken for it -- so no warning, same as above.
    if any((data or {}).get(k) for k in ("agg", "bucket", "group_by")):
        return []
    title = str((data or {}).get("name") or "").strip()
    if not title:
        # Unnamed, the card is titled after whatever feeds it, and a rule
        # called "Card" tells nobody anything in an events log. Naming the
        # card is the same opt-in the dashboard palette already asks for.
        # Silent, like the two above: an unnamed card with coloured bands is a
        # working card, and the threshold rule is an extra nobody asked for.
        return []
    out = []
    for i, edge in enumerate(edges):
        num = int(edge) if float(edge) == int(edge) else edge
        fid = f"th_{node['id']}_{i}"[:40]
        if fid in seen:
            continue
        seen.add(fid)
        doc = {
            "id": fid,
            "name": (title if len(edges) == 1 else f"{title} \u00b7 over {num}")[:60],
            "type": "expr", "enabled": True,
            "params": {"expr": f"{key} >= {edge}"},
        }
        # The RULE always exists -- that is what makes it wireable, and what
        # puts it in the pickers and the palette. Whether it SPEAKS is the
        # operator's, and defaults to no: a line drawn on a chart is a thing
        # to measure against, and turning every one already drawn into events
        # on a box running in the field is a decision to make on purpose.
        if mode == "alert":
            doc["trigger"] = {
                "metric": "value", "op": ">", "value": 0,
                "severity": str((data or {}).get("band_severity") or "warn"),
                "message": str((data or {}).get("band_message") or "")
                or f"{title} reached {num}",
                "cooldown_s": int((data or {}).get("band_cooldown_s")
                                  or BAND_ALERT_COOLDOWN_S),
            }
        out.append(doc)
    return out
#: Everything a Builder may write onto a node: its look, plus the title and
#: caption (the same strings the node's own panel edits -- writing them here is
#: the two editors agreeing, not the dashboard reaching into the canvas's
#: business), the threshold bands and the card's question.
CARD_STYLE_FIELDS = (CARD_IDENTITY_FIELDS + CARD_LOOK_FIELDS
                     + CARD_DATA_STYLE_FIELDS + CARD_QUESTION_FIELDS)


def _clean_question(node, field, value):
    """One CARD_QUESTION_FIELDS value as the node may hold it, or None to
    leave the stored one alone (a shape this node cannot draw, junk).

    The whitelists are the compiler's own, so a value that came through here
    compiles exactly as one picked on the canvas would."""
    if field == "kind":
        ntype = node.get("type")
        allowed = (CHART_KINDS if ntype == "dashboard.graph"
                   else CARD_KINDS + ("table",) if ntype == "dashboard.card"
                   else builder_service.KINDS if ntype == "dashboard.events"
                   else ())                # a one-shape node: its type IS it
        return value if value in allowed else None
    if field == "agg":
        value = str(value or "").strip()
        return value if value in ("",) + tuple(builder_service.AGGREGATIONS) \
            else None
    if field in ("field", "group_by"):
        return str(value or "").strip()[:60]
    if field == "bucket":
        value = str(value or "").strip()
        return value if value == "" or value in builder_service.BUCKETS else None
    if field == "range":
        value = str(value or "").strip()
        return value if value == "" or value in builder_service.QUERY_RANGES \
            or value in builder_service.HISTORY_RANGES else None
    if field in ("range_from", "range_to"):
        # A "Pick dates..." range's two ends, ISO text ("" = unset).
        return _timeaxis.clean_moment(value)
    if field == "interval":
        value = str(value or "").strip()
        return value if value in builder_service.HISTORY_INTERVALS else None
    if field == "x_type":
        # "" takes a chart back to the legacy path; nothing else is a type.
        value = str(value or "").strip()
        return value if value in ("",) + _chartmodel.X_TYPES \
            and node.get("type") == "dashboard.graph" else None
    if field == "time_interval":
        value = str(value or "").strip()
        return value if value in ("",) + _chartmodel.INTERVALS else None
    if field == "ts_field":
        return _chartmodel.clean_ts_field(value)
    if field == "window":
        value = str(value or "").strip() or "live"
        return value if value in CARD_WINDOWS else None
    if field == "limit":
        try:
            return max(1, min(builder_service.MAX_TABLE_ROWS, int(value)))
        except (TypeError, ValueError):
            return None
    if field == "unit":
        return str(value or "").strip()[:12]
    if field == "op":
        return value if value in builder_service.STATUS_OPS else None
    if field == "value":
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number == int(number) else number
    return None


def _edit_rev(stamp):
    """The rev a BUILDER_EDIT_KEY stamp carries; 0 for none or junk."""
    try:
        return int((stamp or {}).get("rev") or 0) if isinstance(stamp, dict) else 0
    except (TypeError, ValueError):
        return 0


def _stamp_builder_edit(data, field, before, rev):
    """Record that a Builder changed `field`, and what it held before.

    A few befores per field, each with the rev that replaced it: a canvas tab
    opened at rev 3 is stale only about what changed AFTER rev 3."""
    stamp = data.get(BUILDER_EDIT_KEY)
    was = dict(stamp.get("was") or {}) if isinstance(stamp, dict) \
        and isinstance(stamp.get("was"), dict) else {}
    entries = [e for e in (was.get(field) or [])
               if isinstance(e, list) and len(e) == 2]
    entries.append([rev, "" if before is None else before])
    was[field] = entries[-4:]
    data[BUILDER_EDIT_KEY] = {"rev": rev, "was": was}


#: The parts of ONE chart input's entry (data.series[<sid>]) that are its
#: question rather than its name or colour, stamped as "series.<sid>.<f>".
#: The name is last-edit-wins like a card's title; the colour has its own
#: guard (keep_chart_colours).
SERIES_QUESTION_FIELDS = ("agg", "ts_field", "query.group_by", "query.range",
                          # Shown or hidden (chart builder refactor): an
                          # input switched off in the Builder must not come
                          # back on from a stale canvas tab.
                          "show",
                          # The input's own thresholds (per camera).
                          "bands")


def _series_path(field):
    """("s1", "agg") for a stamp key "series.s1.agg"; None for anything else."""
    parts = str(field).split(".", 2)
    if len(parts) == 3 and parts[0] == "series" and parts[1] in GRAPH_SERIES_IDS \
            and parts[2] in SERIES_QUESTION_FIELDS:
        return parts[1], parts[2]
    return None


def _series_get(data, sid, path):
    """One input's question field, "" when it holds none (absent is the
    compiler's guess -- the one way clean_card_series lets that be said)."""
    series = (data or {}).get("series")
    entry = series.get(sid) if isinstance(series, dict) else None
    if not isinstance(entry, dict):
        return ""
    if path.startswith("query."):
        q = entry.get("query")
        return (q.get(path[6:], "") if isinstance(q, dict) else "") or ""
    if path == "show":
        # A hidden input reads "off"; shown is the default, said by absence.
        return "off" if entry.get("show") is False else ""
    return entry.get(path, "") or ""


def _series_set(data, sid, path, value):
    """Put one input's question field back; "" removes it."""
    series = data.get("series") if isinstance(data.get("series"), dict) else {}
    data["series"] = series
    entry = dict(series.get(sid)) if isinstance(series.get(sid), dict) else {}
    if path.startswith("query."):
        q = dict(entry.get("query")) if isinstance(entry.get("query"), dict) else {}
        if value:
            q[path[6:]] = value
        else:
            q.pop(path[6:], None)
        if q:
            entry["query"] = q
        else:
            entry.pop("query", None)
    elif path == "show":
        if value == "off":
            entry["show"] = False
        else:
            entry.pop("show", None)
    elif value:
        entry[path] = value
    else:
        entry.pop(path, None)
    series[sid] = entry


def keep_builder_edits(graph, stored):
    """A canvas save that cannot undo a Builder edit it never saw.

    The question fields (CARD_QUESTION_FIELDS) are edited on BOTH sides, so
    unlike the look the canvas may write them -- last edit wins. But a tab
    opened before the Builder changed "Work out" still holds the old answer
    and sends the whole graph on its next save, which is a stale copy winning
    a race, not an edit. So: a node whose stored stamp is newer than the one
    the browser sent back keeps the stored value of each field the Builder
    changed since -- unless the browser's value is not one the field held
    before, i.e. somebody changed it on the canvas, and then the canvas wins.
    The stored stamp always rides forward, so a second stale tab is caught
    the same way. A node with no stamp (every graph saved before this) is
    untouched.
    """
    was_nodes = {str(n.get("id") or ""): (n.get("data") or {})
                 for n in (stored or {}).get("nodes") or []}
    for n in graph.get("nodes") or []:
        # The cards, and the Class Filters the dashboard's card switches.
        if not (_is_card_node(n) or n.get("type") == "filter.class"):
            continue
        old = was_nodes.get(n["id"]) or {}
        stamp = old.get(BUILDER_EDIT_KEY)
        if not isinstance(stamp, dict):
            continue
        data = n.setdefault("data", {})
        seen = _edit_rev(data.get(BUILDER_EDIT_KEY))
        if seen < _edit_rev(stamp):
            for field, entries in (stamp.get("was") or {}).items():
                path = _series_path(field)
                if (field not in BUILDER_STAMPED_FIELDS and path is None) \
                        or not isinstance(entries, list):
                    continue
                befores = []
                for e in entries:
                    try:
                        if isinstance(e, list) and len(e) == 2 and int(e[0]) > seen:
                            befores.append(e[1])
                    except (TypeError, ValueError):
                        continue
                if path is not None:
                    # One input's question (series.<sid>.<field>): the same
                    # rule one level down, so a stale tab re-sending the old
                    # row cannot undo the Builder's "Work out" on that input.
                    incoming = _series_get(data, *path)
                    stored = _series_get(old, *path)
                    if incoming in befores and incoming != stored:
                        _series_set(data, *path, stored)
                    continue
                incoming = data.get(field, "")
                if incoming in befores and incoming != old.get(field, ""):
                    if field in old:
                        data[field] = old[field]
                    else:
                        data.pop(field, None)
        data[BUILDER_EDIT_KEY] = stamp
    return graph


def clean_card_series(value, current=None):
    """A chart's per-series style, MERGED onto what the node already holds.

    {"s1": {"label": ..., "color": "#rrggbb", "agg": ..., "ts_field": ...,
    "query": {"group_by": ..., "range": ...}}, ...} -- the shape the chart
    node stores and the compile reads (see GRAPH_SERIES_IDS, series_entry).

    Everything the canvas's own Inputs list writes is accepted here, because
    the two editors write the SAME field on the same node: a fold set in the
    Builder and a fold set on the canvas are one setting, and a card-style
    write that dropped four of the six would quietly undo the other editor.

    An "" agg is a real answer -- "keep the guess the compiler makes from the
    column type" -- and it is said by holding NOTHING. Storing the empty
    string instead would be a second way to say the same thing, and the two
    editors would have to agree about which; absent is the one way.

    Merged per id and per field, because the two editors send what they
    touched: recolouring series 2 must not erase the name somebody gave
    series 1 last week, and setting a colour must not blank the label
    alongside it. An id outside the chart's own space is ignored rather than
    stored -- nothing draws it, and an unbounded map on a node is a place for
    junk to accumulate where no reader will ever meet it.

    Returns None when the value is not a map at all (a string, a list). That
    is the caller's signal to leave the stored field ALONE: the generic
    "str(value)[:32]" fallback would write the repr of a dict over a chart's
    entire series style, which is data loss dressed as an edit.
    """
    if not isinstance(value, dict):
        return None
    out = {k: dict(v) for k, v in (current or {}).items()
           if k in GRAPH_SERIES_IDS and isinstance(v, dict)}
    for sid, spec in value.items():
        if sid not in GRAPH_SERIES_IDS or not isinstance(spec, dict):
            continue
        entry = dict(out.get(sid) or {})
        if "label" in spec:
            entry["label"] = str(spec.get("label") or "").strip()[:40]
        if "color" in spec:
            # The Builder's own cleaner: #rrggbb or nothing, so a colour that
            # arrived here compiles to the same line as one saved in a layout.
            entry["color"] = builder_service._color(spec.get("color"))
        if "agg" in spec:
            want = str(spec.get("agg") or "").strip()
            # count_nonnull is a Y calculation a fold cannot say (it is a
            # store question); kept as itself and lowered by the compile.
            entry["agg"] = want if want in builder_service.SERIES_AGGS \
                or want == "count_nonnull" else ""
        if "show" in spec:
            # Shown is the default and is said by holding nothing; only a
            # hidden input stores the flag (chart builder refactor).
            entry["show"] = spec.get("show") is not False
        if "bands" in spec:
            # The input's OWN thresholds (per camera, owner 2026-10-13);
            # null or no limits = back to the chart's shared ones.
            own = builder_service.clean_bands(spec.get("bands"))
            if own:
                entry["bands"] = own
            else:
                entry.pop("bands", None)
        if "ts_field" in spec:
            # Not checked against a table here: this cleaner is given a field
            # and no wire, so it cannot know which table the series reads.
            # The compile checks it against the column list the wire carries
            # (apply_series_conf), which is the only place both are known.
            if spec.get("ts_field") is None:
                # null: the input stops overriding the chart's Date column
                # (chart builder model) -- removed, not blanked, because a
                # blank here is the explicit "when the row was saved".
                entry.pop("ts_field", None)
            else:
                entry["ts_field"] = str(spec.get("ts_field") or "").strip()[:60]
        if isinstance(spec.get("query"), dict):
            # Merged per FIELD like the rest: the Builder sends the control
            # that moved, and setting a period must not clear a grouping.
            query = dict(entry.get("query") or {})
            if "group_by" in spec["query"]:
                query["group_by"] = str(spec["query"].get("group_by")
                                        or "").strip()[:60]
            if "range" in spec["query"]:
                want = str(spec["query"].get("range") or "").strip()
                query["range"] = want if want in builder_service.QUERY_RANGES \
                    else ""
            query = {k: v for k, v in query.items() if v}
            if query:
                entry["query"] = query
            else:
                entry.pop("query", None)
        # A name cleared, a colour cleared, a fold back to the compiler's
        # guess, "group by nothing", "all time": a series back to its
        # defaults, which is said by holding nothing rather than by holding a
        # row of empty strings.
        #
        # ts_field is the ONE exception, and it has to be: its blank is a
        # choice ("the clock the row arrived on") and not an absence ("pick
        # the date column for me"). The two mean different pictures, so the
        # blank is kept -- and kept HERE as well as on the canvas, or the two
        # editors would disagree about what an empty dropdown means.
        entry = {k: v for k, v in entry.items()
                 if (v and k != "show") or k == "ts_field"
                 or (k == "show" and v is False)}
        if entry:
            out[sid] = entry
        else:
            out.pop(sid, None)
    return out


def _is_card_node(node):
    spec = REGISTRY.get(str(node.get("type") or "")) or {}
    return spec.get("category") == "dashboard"


def keep_card_look(graph, stored):
    """A canvas save with every dashboard node's LOOK taken from the graph
    already stored, never from the browser.

    The node panel stopped OFFERING the look some time ago, but it still SENT
    it: the whole graph goes up on every save, look fields included, so a tab
    opened before the Analytics Builder restyled a card put the old look
    straight back on its next save. That is "node says orange, layout says
    white" -- not a bug in either editor, but a stale copy winning a race it
    should never have been in. The stored node is the only source, so the
    canvas cannot set a look knob at all, stale or otherwise.

    A node the store does not know (new, pasted, duplicated) arrives with no
    look, i.e. the defaults -- the Builder dresses it, as it would any card.
    """
    was = {str(n.get("id") or ""): (n.get("data") or {})
           for n in (stored or {}).get("nodes") or []}
    for n in graph.get("nodes") or []:
        if not _is_card_node(n):
            continue
        data = n.setdefault("data", {})
        old = was.get(n["id"], {})
        for field in CARD_LOOK_FIELDS:
            if field in old:
                data[field] = old[field]
            else:
                data.pop(field, None)
    return graph


def keep_chart_colours(graph, stored):
    """A canvas save that cannot change -- or blank -- a CHART's colours.

    A chart's colours are look: each line's colour (series[<id>].color) and
    each threshold band's (bands.colors). The look is set in the Analytics or
    Dashboard Builder only (owner, 2026-10-12), so the node panel no longer
    offers them (cardFields.showsColours) -- but `series` and `bands` also
    hold what the chart COMPUTES (a line's name and fold, the threshold
    values, which compile to rules), so they cannot simply join
    CARD_LOOK_FIELDS: the canvas must still write those parts. This keeps the
    colour parts only, from the stored node:

      series   every stored colour is put back on its series, whatever the
               browser sent for it (stale, cleared or missing); a colour the
               store does not hold is dropped -- the canvas cannot pin one.
      bands    the same limits as stored keep the stored colours. Changed
               limits keep the colours the canvas's editor carried along with
               them (it moves a band's colour with its band); no colours at
               all borrow the stored ones by position, so a save that omits
               them never repaints the chart in the default ramp.

    A CARD's colours are untouched (a verdict's tones are what it says, and
    both panels edit them). A node the store does not know -- new, pasted,
    duplicated -- keeps what it arrived with, so a copied chart keeps its
    colours. Runs after keep_card_look, on the same save.
    """
    was = {str(n.get("id") or ""): (n.get("data") or {})
           for n in (stored or {}).get("nodes") or []}
    for n in graph.get("nodes") or []:
        if not _is_card_node(n) or n.get("id") not in was:
            continue
        data = n.setdefault("data", {})
        old = was[n["id"]]
        if _card_kind(n, data) not in CHART_KINDS \
                and _card_kind(n, old) not in CHART_KINDS:
            continue
        # -- series colours -------------------------------------------------
        old_series = old.get("series") if isinstance(old.get("series"), dict) else {}
        series = data.get("series")
        if isinstance(series, dict) or old_series:
            series = {k: dict(v) for k, v in (series or {}).items()
                      if isinstance(v, dict)} if isinstance(series, dict) else {}
            for sid in set(series) | set(old_series):
                stored_entry = old_series.get(sid)
                colour = stored_entry.get("color") \
                    if isinstance(stored_entry, dict) else None
                if colour:
                    series.setdefault(sid, {})["color"] = colour
                elif sid in series:
                    series[sid].pop("color", None)
                    if not series[sid]:
                        series.pop(sid)
            if series:
                data["series"] = series
            else:
                data.pop("series", None)
        # -- band colours ---------------------------------------------------
        kept = _kept_band_colours(data.get("bands"), old.get("bands"))
        if kept is not None:
            data["bands"] = kept
        # ...and each input's OWN bands (per camera): the same rule, input by
        # input -- limits edited on the canvas land, the colours stay.
        if isinstance(data.get("series"), dict):
            for sid, entry in data["series"].items():
                was_entry = old_series.get(sid) if isinstance(old_series, dict) else None
                if isinstance(entry, dict) and isinstance(was_entry, dict):
                    kept = _kept_band_colours(entry.get("bands"), was_entry.get("bands"))
                    if kept is not None:
                        entry["bands"] = kept
    return graph


def _kept_band_colours(bands, stored):
    """`bands` as the canvas sent them with the STORED colours put back, or
    None to leave them as sent. The same limits keep the stored colours;
    changed limits keep the colours the canvas editor carried along with
    them, and borrow the stored ones by position when it carried none."""
    old_bands = builder_service.clean_bands(stored)
    if not old_bands or not isinstance(bands, dict) or not bands.get("at"):
        return None
    colours = bands.get("colors")
    new_at = builder_service.clean_bands({"at": bands.get("at")}).get("at")
    if new_at == old_bands["at"] or not isinstance(colours, list) or not colours:
        return {**bands, "colors": list(old_bands["colors"])}
    return None


def normalize_chart_nodes(graph):
    """Every chart node saved under the chart builder model in its clean,
    new shape (services/chartmodel.normalize_node): the old "Time step"
    folded into the step, overrides that repeat the chart's own answer
    dropped, a single-rule comparison chart's "Count over" following its
    date range. A chart without the model is left exactly as it is -- its
    legacy keys are what compile it. Idempotent; runs on the canvas save and
    after a Builder's card-style write, so both write one shape."""
    for n in (graph or {}).get("nodes") or []:
        if n.get("type") == "dashboard.graph" and isinstance(n.get("data"), dict):
            _chartmodel.normalize_node(n["data"], n["data"].get("kind"))
    return graph


def _series_with_own_colours(compiled, stored):
    """The node's series, each wearing the colour its LINKED COPY already has.

    A chart's series (what each line reads, its name) belongs to the node and
    is rewritten on every compile; the colour of a line is dressing, and a copy
    dragged onto a page is dressed on its own row -- a colour picked there
    never reaches the node (card-style only knows "wf_<node>" ids), so taking
    the whole series list from the node put the default colours back on the
    next workflow save. A copy's colour wins when it has one; a blank one takes
    the node's. None when there is nothing to carry (not both lists)."""
    if not isinstance(compiled, list) or not isinstance(stored, list):
        return None
    own = {str(s.get("id")): s for s in stored if isinstance(s, dict)}
    out = []
    for s in compiled:
        mine = own.get(str((s or {}).get("id"))) if isinstance(s, dict) else None
        if mine and str(mine.get("color") or "").strip():
            s = {**s, "color": mine["color"]}
        out.append(s)
    return out


def apply_card_style(store, cards):
    """Write what the Builders own onto the nodes that publish the cards.

    `cards` is a list of {id: "wf_<node>", <CARD_STYLE_FIELDS>...}; a card
    naming a node the store does not hold (a package card, a node since
    deleted) is skipped rather than refused, and a field not sent is not
    touched. Returns how many fields actually changed, so the caller can skip
    the save and the recompile when nothing did. Every value is cleaned the
    way the Builder cleans it on its own save, so a look that came through
    here compiles to the same widget as one saved in the layout.
    """
    nodes = {}
    for w in store.get("workflows") or []:
        for n in ((w.get("graph") or {}).get("nodes") or []):
            nodes[f"wf_{n.get('id')}"[:40]] = n

    changed = 0
    for card in cards[:200]:
        if not isinstance(card, dict):
            continue
        node = nodes.get(str(card.get("id") or ""))
        if node is None:
            continue
        data = node.setdefault("data", {})
        # One rev per write to this node, so every question field this card
        # changes is stamped as one edit (keep_builder_edits).
        rev = _edit_rev(data.get(BUILDER_EDIT_KEY)) + 1
        for field in CARD_STYLE_FIELDS:
            if field not in card:
                continue
            value = card[field]
            if field in CARD_QUESTION_FIELDS:
                value = _clean_question(node, field, value)
                if value is None or data.get(field, "") == value:
                    continue
                _stamp_builder_edit(data, field, data.get(field), rev)
                data[field] = value
                changed += 1
                continue
            if field in ("label_size", "value_size"):
                value = builder_service._as_text_size(value)
            elif field == "name":
                value = str(value or "").strip()[:60]
                if not value:
                    continue          # a card cannot be renamed to nothing
            elif field == "sub":
                value = str(value or "").strip()[:60]
            elif field == "show_title":
                if not isinstance(value, bool):
                    continue          # only a real yes / no is a setting
            elif field == "align":
                if value not in builder_service.TEXT_CARD_ALIGNS:
                    continue
            elif field == "valign":
                if value not in builder_service.STAT_VALIGNS:
                    continue
            elif field in ("ok_label", "alert_label"):
                value = str(value or "").strip()[:24]
                # A node with CUSTOM state rows compiles those, not these two
                # fields -- so the word also lands on the row of the same
                # tone, or the Builder's edit would reach the node and change
                # nothing on the card.
                tone = "ok" if field == "ok_label" else "alert"
                for row in (data.get("states") or []):
                    if isinstance(row, dict) and row.get("tone") == tone \
                            and value and row.get("label") != value:
                        row["label"] = value
                        changed += 1
            elif field == "bands":
                value = builder_service.clean_bands(value)
            elif field == "series":
                # ITS OWN BRANCH, and it must stay one: the fallback below
                # stringifies whatever it is given, and a chart's series style
                # is a map.
                was_series = copy.deepcopy(data.get("series")) \
                    if isinstance(data.get("series"), dict) else {}
                value = clean_card_series(value, data.get("series"))
                if value is None:
                    continue
                # Each input's QUESTION the Builder changed is stamped like a
                # card's (keep_builder_edits), so a canvas tab opened before
                # this write cannot re-send the old "Work out" over it.
                for sid in sorted(set(was_series) | set(value)):
                    for part in SERIES_QUESTION_FIELDS:
                        before = _series_get({"series": was_series}, sid, part)
                        if before != _series_get({"series": value}, sid, part):
                            _stamp_builder_edit(data, f"series.{sid}.{part}",
                                                before, rev)
            elif field == "by_day":
                value = bool(value)
            elif field == "anim":
                value = (value if value in builder_service.ANIMATIONS else "")
            elif field == "anim_replay":
                value = bool(value)
            elif field == "template":
                value = (value if value in builder_service.TEMPLATES else "plain")
            elif field in _chartmodel.LOOK_FIELDS:
                # A chart's axis titles, orientation, bar style and value
                # labels: look, cleaned the way the Builder stores them.
                value = _chartmodel.clean_look_field(field, value)
            else:
                value = str(value or "")[:32]
            if data.get(field) != value:
                if field in BUILDER_STAMPED_FIELDS:
                    _stamp_builder_edit(data, field, data.get(field), rev)
                data[field] = value
                changed += 1
        if node.get("type") == "dashboard.graph":
            # The same clean shape the canvas save writes.
            _chartmodel.normalize_node(data, data.get("kind"))
    return changed


def ensure_fn_ids(graph):
    """Mint data.fnId for any analytics node missing one: slug of the name plus
    a suffix from the node id, so two "Occupancy" nodes never collide. Minted
    ONCE -- an existing fnId is never touched, which is what keeps stat keys
    stable across renames."""
    taken = set()
    for n in graph["nodes"]:
        fid = _slug((n["data"] or {}).get("fnId"))
        if fid:
            taken.add(fid)
    for n in graph["nodes"]:
        if REGISTRY[n["type"]]["category"] not in ("analytics", "variable"):
            continue
        if n["type"] == "variable.ref":
            # Holds a value, publishes none. Any id an earlier build left on
            # one is stripped rather than ignored: a stale fnId still reads as
            # "this node publishes something" everywhere it is seen.
            n["data"].pop("fnId", None)
            continue
        fid = _slug(n["data"].get("fnId"))
        if not fid:
            base = _slug(n["data"].get("name")) or _slug(n["type"].split(".")[-1])
            suffix = _slug(n["id"])[-4:] or "x"
            fid = f"{base}_{suffix}"[:40]
            while fid in taken:
                fid = (fid + "x")[:40]
            n["data"]["fnId"] = fid
        taken.add(fid)
    return graph


#: The two handle ids that carry SEQUENCE rather than a value: a wire between
#: them says "this comes after that" and passes nothing at all.
FLOW_HANDLES = ("link_in", "link_out")


def _is_flow_edge(edge):
    """True for a wire drawn between two flow ports.

    Dropped before anything else looks at the graph, and that is not a
    nicety. `preds` is built with no handle filtering, and several rules
    count their predecessors blind -- a condition needs EXACTLY one value
    wired in. A flow wire left in the edge list is an extra predecessor, so
    drawing one onto a node that was compiling perfectly would fail it with
    "needs exactly one value wired in (has 2)" over a wire the author was
    told passes nothing.

    Latent before this: the Formula node has carried these ports all along,
    and a link wire from one into an ordinary node hit exactly that. It was
    reachable only from one node type, so nobody met it.

    They are dropped from `edges` rather than skipped per rule, so the cycle
    check does not see them either -- a flow wire drawn back to an earlier
    node is an annotation, not a loop in anything that runs.
    """
    return (edge.get("targetHandle") in FLOW_HANDLES
            or edge.get("sourceHandle") in FLOW_HANDLES)


def _has_cycle(nodes, edges):
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
    WHITE, GREY, BLACK = 0, 1, 2
    color = {n["id"]: WHITE for n in nodes}

    def dfs(u):
        color[u] = GREY
        for v in adj.get(u, ()):
            if color.get(v) == GREY:
                return True
            if color.get(v) == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    return any(color[n["id"]] == WHITE and dfs(n["id"]) for n in nodes)


def _feeds_itself(store_id, own_key, preds):
    """Does anything upstream of this store READ the variable it writes?
    Bounded walk through value nodes -- a feedback loop at frame rate is the
    single most common way a total 'hallucinates'."""
    stack, seen = [store_id], set()
    while stack:
        nid = stack.pop()
        if nid in seen:
            continue
        seen.add(nid)
        for _, src in preds.get(nid, ()):
            data = src.get("data") or {}
            if src.get("type") == "variable.ref" \
                    and str(data.get("key") or "") == own_key:
                return True
            if REGISTRY.get(src.get("type"), {}).get("category") in (
                    "analytics", "variable"):
                stack.append(src["id"])
    return False


def is_branch(node_or_type):
    """Whether this is a Branch node (the five shapes, plus the old Switch).

    One predicate, because "does this node publish numbered branch ports"
    is asked at both ends of a compile and by the edge rules, and a list
    that drifted between them would strand a wire the other half accepted.
    """
    t = node_or_type if isinstance(node_or_type, str) \
        else (node_or_type or {}).get("type")
    return bool((REGISTRY.get(t) or {}).get("switch"))


def branch_ctype(node):
    """Which first-match shape this Branch node compiles as.

    Fixed by TYPE for the five branch nodes -- the shape is the node you
    dragged, not a picker inside it. The deprecated logic.switch keeps
    reading its own `ctype`, so an un-migrated graph compiles unchanged.
    """
    spec = REGISTRY.get((node or {}).get("type")) or {}
    if spec.get("ctype"):
        return spec["ctype"]
    return str(((node or {}).get("data") or {}).get("ctype") or "switch")


def switch_bases(graph):
    """{branch node id -> the stem its branch rules are named from}.

    Settled up front, in node order, because two things need it and they run
    at opposite ends of a compile: the analytics loop (a Formula wired to a
    branch has to name that branch's key) and the switch loop itself. A stem
    already taken gets the node's own id appended, first claimant keeping the
    plain one, so ids already bound to cards and history never move.
    """
    out, taken = {}, {}
    for node in graph.get("nodes") or []:
        if not is_branch(node):
            continue
        data = node.get("data") or {}
        base = _slug(data.get("fnId")) or _slug(data.get("name")) or "switch"
        if taken.setdefault(base, node["id"]) != node["id"]:
            base = f"{base}_{_slug(node['id'])[-4:] or 'x'}"
            taken.setdefault(base, node["id"])
        out[node["id"]] = base
    return out


def branch_alias(edge, node, bases):
    """("route_b1", "fn_route_b1_value") for a wire leaving a Branch port.

    A branch publishes a 0/1 rule like any other, so wiring one into a
    Formula reads "1 while this branch is the one that holds" -- which is
    what makes `if busy then this else that` expressible as arithmetic
    instead of as the same condition written twice.
    """
    if not is_branch(node):
        return None
    base = (bases or {}).get(node["id"])
    if not base:
        return None
    handle = str((edge or {}).get("sourceHandle") or "out0")
    if not handle.startswith("out") or not handle[3:].isdigit():
        return None
    fid = f"{base}_b{int(handle[3:])}"[:40]
    return fid, f"fn_{fid}_value"


#: The two OUTPUT nodes that stand for the rule behind them rather than for
#: themselves: a Condition defines its trigger, an Alert decorates it. What
#: is wired after either fires on that same rule.
_TRANSPARENT_OUTPUTS = ("output.alert", "output.event")


#: Nodes that publish no value of their OWN: each defines a trigger ON the
#: rule behind it. Wired into a Formula, one stands for that rule's alert
#: state -- see _condition_alias. A plain Trigger (no Advanced output) is
#: one of them: it names the moment of the branch or Formula behind it.
_CONDITION_NODES = ("output.alert", "output.event", "logic.when",
                    "logic.delay", "logic.catch", "output.trigger")


def _condition_alias(node, preds_map, hops=0, bases=None):
    """(name, stat key) for a Formula fed by an Alert, a Compare or a Trigger.

    "Do maths on my alert" has one honest answer: the rule's `active` stat,
    1 while the condition holds and 0 while it does not. The condition node
    itself has no function id -- it IS a trigger on the rule wired before it --
    so the walk goes back through the transparent nodes to the rule, exactly
    the way upstream_fn does when it decides which rule an output fires on.

    A BRANCH port behind it is a rule too (branch_alias, with `bases`): a
    Trigger after a Compare's True port stands for that branch's active
    state. Without this the walk stopped at the branch and the operand
    compiled to 0 -- the wire was allowed, the value never arrived.
    """
    if hops > 3:
        return None
    for edge, src in preds_map.get(node.get("id"), ()) or ():
        # A Trigger's zone and OID ports name a place and a who, not the
        # moment (the Trigger compile skips them the same way).
        if str((edge or {}).get("targetHandle") or "") in TRIGGER_SIDE_PORTS:
            continue
        got = branch_alias(edge, src, bases)
        if got:
            return got[0], f"fn_{got[0]}_active"
        spec = REGISTRY.get(src.get("type")) or {}
        if spec.get("category") in ("analytics", "variable"):
            fid = _slug((src.get("data") or {}).get("fnId"))
            return (fid, f"fn_{fid}_active") if fid else None
        if src.get("type") in _CONDITION_NODES:
            got = _condition_alias(src, preds_map, hops + 1, bases)
            if got:
                return got
    return None


def input_alias(node, edge=None, bases=None, preds_map=None):
    """The name a node wired into a formula answers to, and the stat key it
    stands for: ("people_in_bay", "fn_people_in_bay_count"), or None.

    A wire into a formula used to be decorative -- the compiler read only the
    typed expression, so the operator still had to spell a key like
    fn_z_zone_msf_a1_person_count from memory, and a typo reads zero for ever
    with no symptom. Wiring a value in now NAMES it.

    With `edge` and `bases` (see switch_bases), a wire leaving a SWITCH
    BRANCH resolves too: the branch's 0/1 rule is a value like any other.
    """
    if edge is not None and bases:
        got = branch_alias(edge, node, bases)
        if got:
            return got
    spec = REGISTRY.get(node.get("type")) or {}
    # A Trigger with an Advanced output publishes it as its own key, stamped
    # before any wire resolves (trigger_out.stamp_out_keys).
    out_key = (node.get("data") or {}).get("_out_key") \
        if node.get("type") == "output.trigger" else ""
    if out_key:
        return _slug((node.get("data") or {}).get("name")) or out_key, out_key
    if spec.get("category") not in ("analytics", "variable"):
        # An Alert or a plain Trigger wired INTO a Formula: the logic after
        # an alarm belongs in the Formula, which needs a number to work on.
        # Only where the caller can see the graph (preds_map) -- everywhere
        # else a condition node still publishes nothing, which is what it
        # does on its own.
        if preds_map is not None and node.get("type") in _CONDITION_NODES:
            return _condition_alias(node, preds_map, bases=bases)
        return None
    data = node.get("data") or {}
    # A Variable node: unwired it stands for a value that already exists and
    # resolves to THAT key; wired it STORES the value under its own name, so
    # downstream reads the stored variable.
    if node.get("type") == "variable.ref":
        if data.get("_wired"):
            fid = _slug(data.get("name"))
            return (fid, f"fn_{fid}_value") if fid else None
        key = str(data.get("key") or "").strip()
        return (key, key) if key else None
    # A Set Variable publishes AS the variable wired into its input a -- the
    # compile stamps that id here (_publish) before any wire is resolved. A
    # wire leaving the node therefore reads THAT variable. Resolving it to a
    # key named after the node (fn_set_variable_mhih_value) pointed every
    # column and card downstream at a function that does not exist: the
    # rows read NULL while the canvas showed a good wire, and the "in"
    # column of a people counter never filled although the crossings were
    # in the log. (The setter's own function was renamed to the variable
    # in af42a8f; this is the read side of the same rename.)
    published = _slug(data.get("_publish")) if data.get("_publish") else ""
    if published:
        return published, f"fn_{published}_value"
    fid = _slug(data.get("fnId"))
    if not fid:
        return None
    # The metric this node's mode publishes first -- for a multi-mode node
    # (In Frame) that differs per node, not per type.
    metric = (node_metrics(node, spec) or ["value"])[0]
    return fid, f"fn_{fid}_{metric}"


def formula_inputs(node_id, preds, bases=None, preds_map=None):
    """{name: stat key} for everything wired into this formula. Both the
    function id and the node's own name work as names, so an operator can
    write whichever they see on the canvas."""
    out = {}
    for _edge, src in preds or []:
        alias = input_alias(src, _edge, bases, preds_map)
        if not alias:
            continue
        fid, key = alias
        out[fid] = key
        friendly = _slug((src.get("data") or {}).get("name"))
        if friendly and friendly not in out:
            out[friendly] = key
        # The plain name of a variable (fn_test_value -> test): the editor
        # shows and inserts that, never the id. The raw key above keeps
        # working for formulas written before.
        plain = _plain_name(key)
        if plain and plain not in out:
            out[plain] = key
    return out


def _adopt_name(data):
    """A Variable that already held a chosen variable (its key) and then gets
    a wire stores INTO that variable: its name is the one it had picked. Only
    when it carries no name of its own (or the default label "variable")."""
    name = _slug(data.get("name"))
    if name and name != "variable":
        return dict(data)
    plain = _plain_name(data.get("key"))
    return {**data, "name": plain} if plain else dict(data)


def _plain_name(key):
    """fn_test_value -> test: what a person calls the variable."""
    m = re.match(r"^fn_(.+)_value$", str(key or ""))
    return m.group(1) if m else ""


#: How often the master is asked for a table column's aggregate feeding a
#: Math input. A database round trip per tick, on a box also running
#: inference -- and a total of recorded rows does not change by the frame.
TABLE_POLL_S = 10

#: The aggregates a Math input may take of a table column. The card's list,
#: plus the newest value; "page" (follow the Analytics range) is a card's
#: notion and has no meaning for a calculation, so a period is a fixed one.
TABLE_INPUT_AGGS = ("latest", "count", "sum", "avg", "min", "max", "unique", "changes")
TABLE_INPUT_RANGES = ("", "today", "week", "month")


def _dataset_operand(node_id, handle, preds_in, data, fid, tables, err,
                     as_text=False):
    """A Math input fed by a TABLE COLUMN's read port: the function that
    turns the column's aggregate into a live number, and the stat key the
    input reads. None when this input is not fed by a table at all;
    (None, None) after err() when it is, wrongly. `as_text` reads the
    newest value as a word (a Trigger's Current OID), never as a number.

    The column's rows live in the database and the app that runs the
    arithmetic has none, so the input compiles to a query variable -- the
    same poller a Query stored in a Variable uses -- pointed at a route on
    this master that runs the very aggregate a card would ("Show": total,
    average, latest...; "Period": today, this week...). The Math node then
    reads fn_<node>_<input>_value like any other stat, and its result feeds
    a card, a Trigger or another Math like any other value.
    """
    for edge, src in preds_in or ():
        if (edge.get("targetHandle") or "a") != handle or src["type"] != "output.dataset":
            continue
        source_handle = str(edge.get("sourceHandle") or "")
        if not source_handle.startswith("o_"):
            err(node_id, "wire a column's READ port (the dot on the right of a "
                         "column) into this input - the table itself is not a "
                         "number")
            return None, None
        column = source_handle[2:]
        key = _slug((src.get("data") or {}).get("dataset"))
        table = (tables or {}).get(key)
        if not table:
            err(node_id, "the table this input reads does not exist - create "
                         "it on the Database page")
            return None, None
        kinds = {c["name"]: c.get("type") for c in table.get("columns") or []}
        if column not in kinds:
            err(node_id, f"'{column}' is not a column of {key} any more - "
                         "rewire this input")
            return None, None
        agg = str(data.get(f"{handle}_agg") or "").strip()
        if agg not in TABLE_INPUT_AGGS:
            agg = "latest" if kinds[column] == "number" else "count"
        # Only a number can be totalled, averaged or ranked -- Postgres would
        # raise on the cast rather than be wrong. Same clamp the cards apply.
        if agg in ("sum", "avg", "min", "max") and kinds[column] != "number":
            agg = "count"
        rng = str(data.get(f"{handle}_range") or "").strip()
        if rng not in TABLE_INPUT_RANGES:
            rng = ""
        qid = f"{fid}_{handle}"[:40]
        doc = {"id": qid, "name": f"{column} ({agg}{', ' + rng if rng else ''})"[:80],
               "type": "query_value", "enabled": True,
               "params": {"dataset": key, "field": column if agg != "count" else column,
                          "agg": agg, "range": rng,
                          "tables": [{"key": key, "name": table.get("name") or key,
                                      "columns": [dict(c) for c in table.get("columns") or []]}],
                          "interval_s": TABLE_POLL_S, "fallback": 0,
                          **({"text": True} if as_text else {}),
                          "url": f"{_master_base()}/api/workflow/query-value"
                                 f"?fn={urllib.parse.quote(qid)}"}}
        try:
            doc = functions_service.validate_function(doc)
        except (ValueError, TypeError) as exc:
            err(node_id, str(exc))
            return None, None
        return doc, f"fn_{qid}_value"
    return None


#: A Trigger's DUPLICATE GUARD ports (Tok Aba, 2026-10-13): what a Data
#: Table already holds for the object -- its OID and its value -- read back
#: off the table's column read ports.
TRIGGER_GUARD_PORTS = ("cur_oid", "cur_value")
#: The ports on a Trigger that name a place, a who, or what is already
#: stored -- never the moment itself. Every walk looking for the moment
#: skips them.
TRIGGER_SIDE_PORTS = ("zone", "oid") + TRIGGER_GUARD_PORTS


def _trigger_guard(node, preds_in, tables, err, warn):
    """The duplicate guard one Trigger compiles to: (query docs, trig keys).

    WHY: a Trigger feeding a Data Table wrote the same object again and
    again -- every cooldown, or every time it re-entered the moment -- when
    the row it would write is already the newest one in the table. Wiring
    the table's OID column into Current OID says "this one is stored";
    Current value plus the threshold say how much the value must move before
    the same object is worth a new row (timer mode only; the runtime reads
    it, systems/demo/app/functions.py _stored_repeat).

    Each port compiles the way a Math input fed by a table does
    (_dataset_operand): a query variable polling the column's newest value.
    Nothing wired is ({}, {}): the Trigger compiles exactly as before.
    """
    d = node.get("data") or {}
    docs, keys = [], {}
    for handle in TRIGGER_GUARD_PORTS:
        wires = [(e, s) for e, s in preds_in or ()
                 if str(e.get("targetHandle") or "") == handle]
        if not wires:
            continue
        word = "Current OID" if handle == "cur_oid" else "Current value"
        if wires[0][1]["type"] != "output.dataset":
            err(node["id"], f"{word} reads what a Data Table already holds - "
                            "wire a column's read port (the dot on the right "
                            "of a column) into it")
            return [], {}
        # The id leads with the port, so a long node id truncated to 40
        # characters cannot give both ports one name.
        got = _dataset_operand(node["id"], handle, wires, {f"{handle}_agg": "latest"},
                               f"tg{handle[4]}_{node['id']}", tables, err,
                               as_text=(handle == "cur_oid"))
        if not got or got[0] is None:
            return [], {}
        # Keyed by the id as validated: a node id may carry a character the
        # slug drops, and the app publishes under the document's own id.
        doc, key = got[0], f"fn_{got[0]['id']}_value"
        if handle == "cur_value":
            kinds = {c.get("name"): c.get("type")
                     for t in doc["params"].get("tables") or ()
                     for c in t.get("columns") or ()}
            if kinds.get(doc["params"].get("field")) != "number":
                err(node["id"], "Current value needs a number column - the "
                                "difference between two words is not a number")
                return [], {}
        docs.append(doc)
        keys[f"{handle}_key"] = key
    if keys and "cur_oid_key" not in keys:
        warn(node["id"], "Current value is only read beside Current OID - wire "
                         "the table's object id column into Current OID too")
        return [], {}
    if keys:
        try:
            keys["dup_diff"] = max(0.0, float(d.get("dup_threshold") or 0))
        except (TypeError, ValueError):
            err(node["id"], "Total difference threshold must be a number, 0 "
                            "or more")
            return [], {}
    return docs, keys


def _operand(handle, preds, literal, bases=None, preds_map=None, as_text=False):
    """What one Formula input evaluates to: the stat key wired into that
    handle, else the number typed beside it. None when it has neither, which
    is an error the operator can see and fix rather than a silent zero.

    `as_text`: the typed value is a WORD whatever it looks like -- a Set
    Variable of text kind storing "12" stores the text "12", not twelve."""
    for edge, src in preds or []:
        if (edge.get("targetHandle") or "a") != handle:
            continue
        alias = input_alias(src, edge, bases, preds_map)
        if alias:
            return alias[1]
    if literal is None or literal == "":
        return None
    if as_text:
        text = str(literal).strip()[:120].replace("\n", " ")
        return repr(text) if text else None
    try:
        value = float(literal)
    except (TypeError, ValueError):
        # A WORD typed beside the input: a text literal the engine carries
        # as text -- "in", "night shift". Only a Set Variable's value box
        # takes free text on the canvas; the number boxes cannot produce
        # one, so a word here is a word that was meant.
        text = str(literal).strip()[:120].replace("\n", " ")
        return repr(text) if text else None
    return repr(int(value) if value == int(value) else value)


#: The shapes each card node may take. One node per FAMILY -- one value, or
#: many -- with the shape as data, because Counter/Gauge/Verdict differed by
#: nothing else and three nodes for one decision is three things to learn.
CARD_KINDS = ("tile", "gauge", "verdict", "status")
CHART_KINDS = ("line", "area", "spline", "bar", "donut", "pie", "radar")


def _card_kind(node, data):
    """The widget kind a dashboard node draws as.

    Falls back to the node's declared widget_kind, which is what the
    deprecated one-shape-per-node types still carry -- so a canvas drawn
    before the collapse compiles to exactly the same widget."""
    spec = REGISTRY.get(node.get("type")) or {}
    allowed = CHART_KINDS if node.get("type") == "dashboard.graph" else CARD_KINDS
    picked = (data or {}).get("kind")
    return picked if picked in allowed else (spec.get("widget_kind") or "tile")


def _text_sizes(data):
    """How big a compiled card's words are, as the widget carries them.

    On the NODE for the same reason `template` is (see below): a wf_* card is
    rebuilt from its node on every compile, so a size that lived only on the
    widget would last until the next save. Putting it here is what lets the
    Analytics editor and the canvas node agree -- one place holds the answer,
    and both read it.
    """
    d = data or {}
    return {"label_size": builder_service._as_text_size(d.get("label_size")),
            "value_size": builder_service._as_text_size(d.get("value_size"))}


def _template(data):
    """The treatment a dashboard node is drawn in.

    Compiled ONTO the widget, because a `wf_*` card is rebuilt from its node on
    every compile and the Builder's partial save deliberately keeps the stored
    compiled copy (see builder._merge_layout). So a template picked in the
    Builder on a workflow card was discarded on the next save, and every
    workflow card rendered "plain" whatever the picker said. The node is where
    the choice has to live, because the node is what survives.
    """
    picked = (data or {}).get("template")
    return picked if picked in builder_service.TEMPLATES else "plain"


def _text_constant(node, preds):
    """{name, text} when a card's ONLY input is a text constant, else None.

    Text constants are the one value on the canvas that publishes nothing --
    not because a word cannot travel as a stat (a rule whose result is text
    publishes one, and a card reads it live), but because a CONSTANT has
    nothing to measure and no key anything would ever write. So a card fed by
    one reads the words off the canvas, which is what this does.

    Exactly one input, and it must be the constant: a card with a rule AND a
    constant wired in is ambiguous, and answering it by picking the constant
    would quietly ignore the rule.
    """
    wires = list(preds.get(node["id"], ()))
    if len(wires) != 1:
        return None
    src = wires[0][1]
    d = src.get("data") or {}
    if src["type"] != "variable.number" or d.get("value_type") != "text":
        return None
    return {"name": str(d.get("name") or "Value")[:60],
            "text": str(d.get("value") or "")[:120]}


def _anim(data):
    """How a dashboard node's card arrives, and how it moves on new numbers.

    Lives on the NODE for the same reason the template does: a wf_* card is
    rebuilt from its node on every compile, so a choice made only in the
    Builder would be discarded by the next save.
    """
    picked = (data or {}).get("anim")
    return picked if picked in builder_service.ANIMATIONS else ""


#: Where the app finds the master that can run its SQL. The master is the
#: only process with a database connection, and it knows its own port from
#: its argv -- master_server stamps it here at startup so a compile can write
#: a working address rather than guessing one.
DEFAULT_MASTER_PORT = 5110


def _master_base():
    try:
        port = int(os.environ.get("CYTRON_MASTER_PORT") or DEFAULT_MASTER_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_MASTER_PORT
    return f"http://127.0.0.1:{port}"


def _query_tables(q_src, preds, tables):
    """The tables a Query node is allowed to read, from the wires into it.

    A wire rather than a dropdown for the reason the Query node already
    documents: rename a table and the line still points at it. Shared by
    everything that has to reproduce a query away from the canvas.
    """
    out = []
    for _edge, src_node in preds.get(q_src["id"], ()) or ():
        if src_node["type"] != "output.dataset":
            continue
        key = _slug((src_node["data"] or {}).get("dataset"))
        table = (tables or {}).get(key)
        if table and not any(t["key"] == key for t in out):
            out.append({"key": key, "name": table.get("name") or key,
                        "columns": [dict(c) for c in table["columns"]]})
    return out


def _csv_spec(csv_node, preds, tables, warn):
    """{csv_sql, csv_tables, csv_name, csv_rows} for a CSV node, or None.

    The statement is COPIED onto whatever carries it, the same way a query
    variable copies it onto its function: what runs has to be what was
    compiled, not whatever the canvas says by the time the message fires.
    """
    cd = csv_node.get("data") or {}
    q_src = next((s for _e, s in preds.get(csv_node["id"], ()) or ()
                  if s["type"] == "storage.query"), None)
    if q_src is None:
        warn(csv_node["id"], "nothing to put in the file - wire a Query into "
                             "this CSV node")
        return None
    statement = str((q_src.get("data") or {}).get("sql") or "").strip()
    wired = _query_tables(q_src, preds, tables)
    if not statement:
        warn(csv_node["id"], "the Query wired in has no statement yet, so "
                             "this file would be empty")
        return None
    if not wired:
        warn(csv_node["id"], "the Query wired in reads no tables - drag a Data "
                             "Table onto its table port")
        return None
    name = _slug(cd.get("name")) or _slug((q_src.get("data") or {}).get("name")) \
        or "query"
    try:
        rows = int(cd.get("max_rows") or 0)
    except (TypeError, ValueError):
        rows = 0
    return {"csv_sql": statement, "csv_tables": wired,
            "csv_name": f"{name}.csv"[:60],
            # 0 = every row the query returns, up to the console's own ceiling.
            # A cap here is for a message that must stay small, not for safety:
            # the ceiling is enforced where the query runs.
            "csv_rows": max(0, min(100000, rows))}


#: What a message's text may say that the app fills by itself (outputs.py
#: _fill): the event's own words, and the attached file(s).
MESSAGE_BUILTINS = ("fn", "value", "label", "message", "type", "time",
                    "csv", "csv_text", "csv_rows")
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_:.\-]+)\}")


def _csv_tokens(files):
    return [f"{kind}:{name}" for name in files
            for kind in ("csv", "csv_text", "csv_rows")]


def unknown_placeholders(texts, wired, files=()):
    """Every {name} in these texts that nothing fills: not a built-in, not a
    value wired into the message, not one of its files. In first-seen order,
    once each. Mirrored by the inspector (msgPlaceholders.js)."""
    known = set(MESSAGE_BUILTINS) | set(wired or ()) | set(_csv_tokens(files))
    out = []
    for text in texts:
        for name in _PLACEHOLDER_RE.findall(str(text or "")):
            if name not in known and name not in out:
                out.append(name)
    return out


def _distance(a, b):
    """Levenshtein distance, small and plain: names are short."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def closest_placeholder(name, candidates):
    """The candidate nearest `name` by edit distance, or "" when nothing is
    near enough to be a likely typo (more than half the name differs)."""
    best, score = "", None
    for cand in candidates:
        d = _distance(name, cand)
        if score is None or d < score:
            best, score = cand, d
    return best if score is not None and score <= max(2, len(name) // 2) else ""


def unknown_placeholder_text(name, candidates):
    """The warning for one unknown {name}, with the nearest wired name."""
    near = closest_placeholder(name, candidates)
    if near:
        return (f"{{{name}}} is not wired into this message. Did you mean "
                f"{{{near}}}?")
    return (f"{{{name}}} is not wired into this message, so it is sent as "
            "written. Wire in the value it should show, or remove it.")


#: Most files one message carries. A shift report with more tables than this
#: is a dashboard, and every file is a database read when the message fires.
MAX_MESSAGE_CSVS = 8


def unique_csv_names(names):
    """The file names a message attaches, each made unique: a second
    "same.csv" becomes "same_2.csv" rather than one file hiding the other
    (and one {csv:name} placeholder naming two files). Mirrored by the
    inspector's chips (frontend msgPlaceholders.js csvNames)."""
    seen, out = set(), []
    for name in names:
        stem = name[:-4] if name.endswith(".csv") else name
        pick, i = f"{stem}.csv", 1
        while pick in seen:
            i += 1
            pick = f"{stem}_{i}.csv"
        seen.add(pick)
        out.append(pick[:60])
    return out


def _csv_list(specs, oid):
    """`csvs` for a message fed by two or more CSV nodes: one inline entry per
    file, in wire order, each fetched from its own address on this box
    (?out=<output>&n=<csv node>). Flat on purpose -- it is written into the
    app's TOML as an array of inline tables (core/config.py _toml_value)."""
    specs = specs[:MAX_MESSAGE_CSVS]
    names = unique_csv_names([s["csv_name"] for _n, s in specs])
    out = []
    for (node, spec), name in zip(specs, names):
        nid = f"wf_{node['id']}"[:40]
        out.append({"name": name, "sql": spec["csv_sql"],
                    "tables": spec["csv_tables"], "rows": spec["csv_rows"],
                    "node": nid,
                    "url": (f"{_master_base()}/api/workflow/query-csv"
                            f"?out={oid}&n={nid}")[:300]})
    return out


def csv_spec_for_node(store, node_id, tables):
    """The CSV spec for ONE node of a saved store, or (None, why).

    A CSV node is worth looking at before it is worth sending. Its file rides
    on the message it feeds -- that is what carries an attachment -- so there
    is no compiled document for a CSV node that has not been wired into an
    Email or a Telegram yet, and asking the outputs for one is a 404 on the
    node's own screen.

    So the Download button comes here instead: the same statement the compiler
    would copy, read from the same saved graph, needing nothing downstream. A
    node with a Query and a table is a node with rows, whatever it does or does
    not go on to feed.
    """
    want = str(node_id or "").strip()
    for w in (store or {}).get("workflows") or []:
        graph = resolve_links(clean_graph(w.get("graph") or {}))
        nodes = {n["id"]: n for n in graph["nodes"]}
        if want not in nodes or nodes[want].get("type") != "storage.csv":
            continue
        preds = {}
        for e in graph["edges"]:
            if e.get("source") in nodes and e.get("target") in nodes:
                preds.setdefault(e["target"], []).append((e, nodes[e["source"]]))
        # _csv_spec reports its refusals through warn(); here they ARE the
        # answer, so they are collected rather than attached to a canvas.
        said = []
        spec = _csv_spec(nodes[want], preds, tables or {},
                         lambda _id, message: said.append(message))
        if spec:
            return spec, ""
        return None, (said[0] if said else "this CSV node has no rows yet")
    return None, "no such CSV node on this canvas"


def _query_value_doc(fid, d, q_src, preds, tables, err):
    """The function doc for a Query stored in a Variable, or None after err().

    The statement and the tables are copied ONTO the function rather than
    looked up later by node id. A compiled function is a document that has to
    keep working after the canvas is edited, and a variable whose meaning
    silently changed when somebody renamed a node is a variable nobody can
    trust. The same copy is what the master reads back to answer the poll --
    so what runs is exactly what was compiled, not whatever the canvas says
    now.
    """
    qd = q_src.get("data") or {}
    statement = str(qd.get("sql") or "").strip()
    if not statement:
        err(q_src["id"], "this Query has no statement - write the SELECT it "
                         "should run before storing its result")
        return None
    # The tables wired into the Query are the ones it may read: exactly the
    # rule the Query's own card compiles under, because it is the same query.
    wired = []
    for _edge, src_node in preds.get(q_src["id"], ()) or ():
        if src_node["type"] != "output.dataset":
            continue
        key = _slug((src_node["data"] or {}).get("dataset"))
        table = (tables or {}).get(key)
        if table and not any(t["key"] == key for t in wired):
            wired.append({"key": key, "name": table.get("name") or key,
                          "columns": [dict(c) for c in table["columns"]]})
    if not wired:
        err(q_src["id"], "nothing is wired into this Query - drag a Data "
                         "Table onto its table port to say which tables it "
                         "may read")
        return None
    doc = {"id": fid, "name": str(d.get("name") or fid),
           "type": "query_value", "enabled": True,
           "params": {"sql": statement, "tables": wired,
                      "column": str(d.get("q_column") or "").strip(),
                      "pick": str(d.get("q_pick") or "first"),
                      "interval_s": d.get("q_interval_s", 60),
                      "fallback": d.get("fallback", 0),
                      "url": f"{_master_base()}/api/workflow/query-value"
                             f"?fn={urllib.parse.quote(fid)}"}}
    try:
        return functions_service.validate_function(doc)
    except (ValueError, TypeError) as exc:
        err(q_src["id"], str(exc))
        return None


def _bands(data):
    """A line card's threshold bands, cleaned by the same rule that cleans them
    at write time -- so a limit set on the canvas and a limit set in the
    Builder produce the identical widget, and the N+1 colour invariant holds
    wherever the card was made.

    Same reason as _anim above: a wf_* card is rebuilt from its node on every
    compile, so anything chosen only in the Builder would be discarded by the
    next save.
    """
    return builder_service.clean_bands((data or {}).get("bands"))


def _by_day(data):
    """Whether a card whose axis spans days paints each day its own colour.

    On the node, like the template and the bands: a wf_* card is rebuilt from
    its node on every compile, so a checkbox ticked only in the Builder would
    survive exactly until the next workflow save.
    """
    return bool((data or {}).get("by_day"))


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _bg(data):
    """A dashboard node's own CARD colour -- validated the same way at
    compile time as builder.save() validates it at write time (builder._color
    is private to that module; the check is one regex, not worth exporting).
    Invalid input compiles to "" rather than a truncated non-colour string
    that would only be caught later, on save."""
    value = str((data or {}).get("bg") or "").strip()
    return value if _HEX_COLOR_RE.match(value) else ""


def setvar_kind(node, preds_in=None):
    """The kind of value a Set Variable stores: decimal, number (whole) or
    text -- the operator's explicit choice when there is one, otherwise read
    off the value itself.

    The kind used to be a required choice, defaulting to decimal: typing
    "Enter" into a node left at decimal stored 0, and the operator had to
    know to open a select first. The value already says what it is -- a wire
    carries a number, "12" is a whole number, "4.5" has decimals, "night
    shift" is text -- so that is what the node reads unless told otherwise.
    A node with a stale or missing kind (older graph, hand-edited file)
    detects too, rather than snapping to decimal.
    """
    d = node.get("data") or {}
    kinds = list((MATH_NODES.get(node.get("type")) or {}).get("value_types") or ())
    if not kinds:
        return ""
    # The VARIABLE's declared type first: the kind is a property of the
    # variable, not of one node that writes it, so a Variable set to text
    # makes every setter of it store text whatever each setter says.
    for edge, src in preds_in or ():
        if str(edge.get("targetHandle") or "a") != "a":
            continue
        if src.get("type") == "variable.ref":
            vt = str((src.get("data") or {}).get("value_type") or "")
            if vt in kinds:
                return vt
    chosen = str(d.get("value_type") or "")
    if chosen in kinds:
        return chosen
    # Wired: a number, whatever is typed underneath -- unless the wire comes
    # from a text constant, whose string is the value.
    for edge, src in preds_in or ():
        if str(edge.get("targetHandle") or "a") != "b":
            continue
        sd = src.get("data") or {}
        if src.get("type") == "variable.number" and sd.get("value_type") == "text":
            return "text"
        return "decimal"
    raw = d.get("b_value")
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return "decimal"
    if re.fullmatch(r"[+-]?\d+", text):
        return "number"
    try:
        float(text)
    except ValueError:
        return "text"
    return "decimal" if text.lower() not in ("nan", "inf", "-inf", "+inf") else "text"


def _value_only(node):
    """A node that publishes a value but fires no row: a Set Variable, a
    Variable, a Math node. Two of these on one table column contradict each
    other, where two RULES on an alert table each write their own row."""
    return str(node.get("type") or "").startswith(("math.", "variable."))


def dataset_columns(node, node_preds, table=None, zone_of=None):
    """A Data Table node's columns -> the definition its output document carries,
    or (None, message) with the first problem stated the way the operator can
    fix it.

    A column resolves ONCE, here, so the runtime never sees the graph: a wired
    column becomes the stat key it was dragged from, and the app just reads
    that key. The names are also the query whitelist later on
    (core/repos/datasets.py), which is why they are checked against the same
    COL_RE rather than a looser rule of this module's own.

    `table` is the stored registry entry, when there is one. Its type WINS for
    a column the table already declares: the table is the source of truth, so
    two nodes writing to one column cannot disagree about what it holds. The
    node's own type only decides a column the table does not have YET, which
    is then created for it (core/services/datasets.py merge_declared).
    """
    declared = (table or {}).get("columns") or []
    if not declared:
        return None, ("this table has no columns yet - add some on the "
                      "Database page")
    data = node.get("data") or {}
    configured = data.get("sources") or {}

    # What has been DRAGGED onto a column. Each column owns a handle named
    # after it, so wiring is the assignment -- drop a value on `dwell` and
    # dwell is what it fills. Handles used to be positional (c1, c2 ...),
    # which meant reordering the table silently re-pointed every wire.
    wired, from_OID, from_timer, from_text = {}, {}, {}, {}
    fed_by, text_wired = {}, set()
    for edge, src_node in node_preds or ():
        handle = str(edge.get("targetHandle") or "")
        if not handle.startswith("c_"):
            continue
        column = handle[2:]
        source = str(edge.get("sourceHandle") or "")
        # ONE wire per column. Two nodes dropped on the same column used to
        # resolve to whichever edge came last in the file, silently: a
        # people counter had its "reset to 0" setter wired over both `in`
        # and `out` beside their own setters, and both columns then read the
        # reset's key. A column holds one value; which one is the operator's
        # call, not the edge order's.
        #
        # The one shape where two wires on a column are meant: an "alert"
        # table with SEVERAL RULES wired in, each firing its own row (see
        # the compile: one output document per rule). Those wires are the
        # rows' triggers as much as their values, so they may share a
        # column; a Set Variable, Variable or Math node fires no row and
        # never gets that pass.
        other = fed_by.get(column)
        if other is not None and other["id"] != src_node["id"]:
            rules_on_alert = (str(data.get("mode") or "") == "alert"
                              and not _value_only(other) and not _value_only(src_node))
            if not rules_on_alert:
                return None, (f"column '{column}' has two wires into it - a "
                              "column holds one value, so remove one of them")
        fed_by[column] = src_node
        # TEXT lands here and nowhere else. A text constant (or a text Set
        # Variable) compiles to no function -- the expression engine is
        # numeric end to end -- so its stat key never exists, and resolving
        # this wire to that key wrote NULL into every row while the canvas
        # showed a perfectly good string wired in. The database is the one
        # consumer that CAN hold a string, so the wire resolves at compile
        # time into the column's fixed value instead: same machinery as a
        # column configured "fixed value" by hand, with the value living on
        # the canvas where it can be seen and edited.
        sd = src_node.get("data") or {}
        # A text CONSTANT stays a fixed value: it never changes, and it
        # compiles to no function. A text Set Variable is a live rule (its
        # word changes with its gate, and the engine carries the word), so
        # it falls through to the wire below and the column records
        # whichever word the variable holds now.
        kind = str(sd.get("_kind") or sd.get("value_type") or "")
        if kind == "text" and src_node["type"] == "variable.number":
            from_text[column] = str(sd.get("value") or "")[:120]
            continue
        if kind == "text":
            # The column will hold a WORD whatever the table says -- forced
            # to text below, same reasoning as the OID column: one string
            # in a number column breaks every query over the whole table.
            text_wired.add(column)
        # A counter's OID port carries WHICH object was counted, not a number.
        # There is no stat key for it -- an id is per-object, and a stat is
        # one value per system -- so it records the id off the event that
        # triggered the row, which is where the app already has it.
        if source == "OID":
            # A ZONE-SCOPED rule's OID means "who is in the zone" -- the
            # column carries the zone id so the row builder can refuse the
            # id of somebody who is merely in frame. A frame rule's OID has
            # no zone and keeps meaning "who was counted".
            from_OID[column] = (zone_of or {}).get(src_node["id"]) or ""
            continue
        if source == "timer":
            # The LIVE per-person clock: seconds this row's object has been
            # inside the rule's zone. Zone-scoped by definition -- a rule
            # with no zone has no inside to time.
            zid = (zone_of or {}).get(src_node["id"]) or ""
            if not zid:
                return None, (f"column '{column}' is wired to a timer port, "
                              "but that rule watches no zone")
            from_timer[column] = zid
            continue
        alias = input_alias(src_node)
        if not alias:
            continue
        # A node that publishes several numbers can hand out any of them by
        # name: `new` on a counter is the +1 the instant a new object is
        # counted, which is a different question from the running total on
        # the default port. Without this the wire always meant metrics[0] and
        # the rest were reachable only through a card's metric picker.
        metrics = node_metrics(src_node, REGISTRY.get(src_node["type"], {}))
        if source in (metrics or ()):
            wired[column] = f"fn_{alias[0]}_{source}"
        else:
            wired[column] = alias[1]

    out = []
    for col in declared[:MAX_DATASET_COLS]:
        name = col.get("name")
        kind = col.get("type")
        kind = kind if kind in datasets_repo.COL_TYPES else "number"
        if not datasets_repo.COL_RE.match(str(name or "")):
            continue
        entry = {"name": name, "type": kind}
        if name in from_OID:
            # A recorded track id is TEXT whatever the table says, because it
            # is run-scoped ("k7m2-3"): the tracker numbers from 1 again on
            # every restart, so a bare number would make two different objects
            # share an id and any distinct count over a day would merge them.
            #
            # Overridden rather than reported as a mistake: the operator ticked
            # a checkbox, they did not choose a storage type. Left as `number`
            # it would be worse than an error -- the query side casts a number
            # column, so ONE text value there breaks every query over the whole
            # table, not just this column.
            out.append({**entry, "type": "text", "src": "event",
                        "field": "track_id", "handle": f"c_{name}",
                        **({"zone": from_OID[name]} if from_OID[name] else {})})
            continue
        if name in from_timer:
            # Seconds are a NUMBER whatever the table says -- same forced-type
            # reasoning as the OID column, pointed the other way.
            out.append({**entry, "type": "number", "src": "event",
                        "field": "in_zone_s", "handle": f"c_{name}",
                        "zone": from_timer[name]})
            continue
        if name in from_text:
            # Forced to text whatever the table says, same reasoning as the
            # OID column: the value IS a string, and one string in a number
            # column breaks every query over the whole table.
            out.append({**entry, "type": "text", "src": "const",
                        "handle": f"c_{name}", "value": from_text[name]})
            continue
        if name in wired:
            # A wire beats a configured source: it is the thing you can SEE on
            # the canvas, so it has to be the thing that wins.
            out.append({**entry, "src": "wire", "handle": f"c_{name}",
                        "key": wired[name],
                        **({"type": "text"} if name in text_wired else {})})
            continue
        cfg = configured.get(name) or {}
        src = cfg.get("src")
        if src == "stat" and str(cfg.get("key") or "").strip():
            out.append({**entry, "src": "stat",
                        "key": str(cfg["key"]).strip()[:60]})
        elif src == "event":
            if cfg.get("field") not in DATASET_EVENT_FIELDS:
                return None, (f"column '{name}' captures '{cfg.get('field')}', "
                              "which is not something an event carries")
            out.append({**entry, "src": "event", "field": cfg["field"]})
        elif src == "const":
            out.append({**entry, "src": "const",
                        "value": str(cfg.get("value") if cfg.get("value") is not None
                                     else "")[:120]})
        # Anything else: the column exists on the table but nothing feeds it
        # here, so this node does not write it. That is a legitimate state --
        # two nodes may each fill part of one table -- not an error.

    if not out:
        return None, ("nothing is attached to any column - drag a value onto "
                      "one, or set a source for it in the inspector")
    return out, None


def _expr_with_inputs(expr, preds, bases=None, preds_map=None):
    """Substitute wired-input names in `expr` with the stat keys they stand
    for. Whole words only, and a name that is already a full stat key is left
    alone -- substitution must never rewrite what the operator typed exactly.

    A wire on a NAMED port (`p_<name>`, the port a Custom Formula grows for
    each name its expression refers to) binds that name outright and wins over
    the older match-by-source-name. That is the point of the port: the name
    lives on the FORMULA, so renaming the node three drawers away that feeds
    it cannot silently turn `capacity` into a word nothing publishes -- which
    read as zero for ever, with nothing on either face to show it.
    """
    expr = str(expr or "")
    names = dict(formula_inputs(None, preds, bases, preds_map))
    for edge, src in preds or []:
        # `edge` is None wherever a caller builds preds by hand rather than
        # from the graph -- there is no wire, so there is no named port on it.
        handle = str((edge or {}).get("targetHandle") or "")
        if not handle.startswith(FUNCTION_PARAM_PREFIX):
            continue
        alias = input_alias(src, edge, bases, preds_map)
        if alias:
            names[handle[len(FUNCTION_PARAM_PREFIX):]] = alias[1]
    if not expr or not names:
        return expr
    # Longest first: a name that contains another must win, or "zone_count"
    # would be half-rewritten by "zone".
    for name in sorted(names, key=len, reverse=True):
        key = names[name]
        if name == key:
            continue
        expr = re.sub(rf"(?<![\w.]){re.escape(name)}(?![\w.])", key, expr)
    return expr


#: The three nodes that bracket a function rather than run in one.
FUNCTION_NODES = ("function.start", "function.end", "function.call")

#: How deep a function may call another. A cap rather than a cycle check
#: because the cap is also the answer to recursion: a function that calls
#: itself stops expanding here, and the Call left over compiles to nothing
#: the way any unwired node does.
FUNCTION_DEPTH = 8

#: The port a parameter is wired on, at both ends: the Start hands it OUT on
#: `p_<name>` and the Call takes it IN on the same id, which is what lets the
#: splice match an argument to a parameter without either end knowing the
#: other's node ids.
FUNCTION_PARAM_PREFIX = "p_"


def function_name(node):
    """The function a Start, End or Call names. Case-folded: an operator
    typing `Rate` into the Call of a function called `rate` means that one."""
    return str((node.get("data") or {}).get("fn") or "").strip().lower()


def function_bodies(graph):
    """{name: (start, end, {body node ids})} for every complete function.

    A node is in the body when the Start reaches it and the End does not.

    "Reaches the End" was the obvious rule and it was wrong: a Card, an Email
    or a Snapshot inside a function is a DEAD END -- it returns nothing, so it
    reaches no End, so the rule left it outside the function it was drawn
    inside, wired to a body node that was about to be cloned away. Outputs
    inside a function are the whole reason the body is the main canvas, so the
    rule has to be the one that keeps them: everything downstream of the
    Start, stopping at the End, and nothing past it. What the function feeds
    AFTERWARDS hangs off the Call, not off the End, so it is never in here.
    """
    nodes = graph.get("nodes") or []
    starts, ends = {}, {}
    for n in nodes:
        name = function_name(n)
        if not name:
            continue
        if n.get("type") == "function.start":
            starts.setdefault(name, n)
        elif n.get("type") == "function.end":
            ends.setdefault(name, n)
    if not starts:
        return {}
    fwd = {}
    for e in graph.get("edges") or []:
        fwd.setdefault(e["source"], []).append(e["target"])

    def walk(seed, adj, stop):
        seen, stack = set(), [seed]
        while stack:
            for nxt in adj.get(stack.pop(), []):
                if nxt == stop or nxt in seen:
                    continue
                seen.add(nxt)
                stack.append(nxt)
        return seen

    out = {}
    for name, start in starts.items():
        end = ends.get(name)
        if end is None:
            continue
        out[name] = (start, end,
                     walk(start["id"], fwd, end["id"])
                     - walk(end["id"], fwd, start["id"]) - {end["id"]})
    return out


def resolve_functions(graph):
    """Replace every Call with its own copy of the function's body.

    Each Call gets a COPY, not a share: two Calls of a function holding a Data
    Table write two sets of rows, the way inlining the body twice would. That
    is what makes a function on this canvas mean what it means in a program,
    and it is why every id inside a copy is prefixed with the Call's own id --
    including the fnId a node publishes its value under, and the name a Link
    out jumps to, both of which would otherwise collide between copies.

    The definition itself does not run: a function is a recipe, so the Start,
    the End and the body are removed whether or not anything called them.
    """
    bodies = function_bodies(graph)
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    if not bodies and not any(n.get("type") == "function.call" for n in nodes):
        return graph
    origin = {n["id"]: n for n in nodes}

    def port(handle):
        """One name for the port a wire sits on. React Flow stores the default
        handle as null, as "" and -- where a node named its single port -- as
        "in" or "out", and the Start's plain output has to meet the Call's
        plain input whichever of the four each end happens to hold. That
        unnamed pair is the function's FLOW wire: it is what brackets a body
        with no parameters at all."""
        h = str(handle or "")
        return "" if h in ("in", "out") else h

    def clone(call, name):
        """The nodes and edges one Call expands to."""
        start, end, body = bodies[name]
        pre = f"{call['id']}__"
        out_n, out_e = [], []
        for nid in sorted(body):
            n = copy.deepcopy(origin[nid])
            n["id"] = pre + nid
            d = dict(n.get("data") or {})
            if d.get("fnId"):
                d["fnId"] = f"{pre}{d['fnId']}".replace(".", "_")
            if d.get("link"):
                d["link"] = f"{pre}{d['link']}"
            n["data"] = d
            out_n.append(n)
        for e in edges:
            if e["source"] in body and e["target"] in body:
                out_e.append({**e, "id": f"{pre}{e.get('id') or ''}",
                              "source": pre + e["source"],
                              "target": pre + e["target"]})
        # An ARGUMENT: whatever is wired into the Call's p_<name> port is
        # wired to whatever the Start's p_<name> port fed inside the body.
        args = {}
        for e in edges:
            if e["target"] == call["id"]:
                args.setdefault(port(e.get("targetHandle")), []).append(e)
        for e in edges:
            if e["source"] != start["id"] or e["target"] not in body:
                continue
            for a in args.get(port(e.get("sourceHandle")), []):
                out_e.append({"id": f"{pre}arg{a['source']}{e['target']}",
                              "source": a["source"],
                              "sourceHandle": a.get("sourceHandle"),
                              "target": pre + e["target"],
                              "targetHandle": e.get("targetHandle")})
        # The RETURN, port for port. A function may give back more than one
        # value -- a count AND the time it took -- named on the End the way
        # the parameters are named on the Start, and handed out of every Call
        # under the same names. An End that names none keeps the single
        # unnamed pair, which is what every function drawn so far uses:
        # port() folds null / "" / "in" / "out" onto one key, so the plain
        # port on the End meets the plain port on the Call without either
        # end knowing which of the four spellings the other holds.
        rets = {}
        for e in edges:
            if e["target"] == end["id"] and e["source"] in body:
                rets.setdefault(port(e.get("targetHandle")), []).append(e)
        takes = {}
        for e in edges:
            if e["source"] == call["id"]:
                takes.setdefault(port(e.get("sourceHandle")), []).append(e)
        for slot, outs in takes.items():
            for r in rets.get(slot, []):
                for t in outs:
                    out_e.append({"id": f"{pre}ret{slot}{r['source']}{t['target']}",
                                  "source": pre + r["source"],
                                  "sourceHandle": r.get("sourceHandle"),
                                  "target": t["target"],
                                  "targetHandle": t.get("targetHandle")})
        return out_n, out_e

    # Expand until no Call is left. A body may hold Calls of its own, and its
    # copies carry copies of those, so this settles rather than runs once.
    for _ in range(FUNCTION_DEPTH):
        calls = [n for n in nodes if n.get("type") == "function.call"]
        if not calls:
            break
        gone = {c["id"] for c in calls}
        add_n, add_e = [], []
        for call in calls:
            name = function_name(call)
            if name in bodies:
                a, b = clone(call, name)
                add_n += a
                add_e += b
        nodes = [n for n in nodes if n["id"] not in gone] + add_n
        edges = [e for e in edges
                 if e["source"] not in gone and e["target"] not in gone] + add_e

    # The definition is a recipe, not a run: drop the brackets and the body.
    spent = set()
    for start, end, body in bodies.values():
        spent |= {start["id"], end["id"]} | body
    nodes = [n for n in nodes if n["id"] not in spent]
    live = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in live and e["target"] in live]
    return {**graph, "nodes": nodes, "edges": edges}


def resolve_links(graph):
    """Replace Link out -> Link in jumps with the real edges they stand for.

    A jump is a way of DRAWING a connection, not a different kind of one, so
    it is resolved before anything validates or compiles: whatever fed the
    Link out is wired straight to whatever the matching Link in feeds, and
    both link nodes disappear. The compiler therefore needs to know nothing
    about them, and an illegal jump reports against the real pair of nodes
    rather than against a node the operator would have to trace by hand.

    Unnamed or unmatched links simply carry nothing -- a half-built jump is a
    normal state while wiring, not an error.
    """
    nodes = graph.get("nodes") or []
    links = {n["id"]: n for n in nodes
             if n.get("type") in ("common.link_out", "common.link_in")}
    if not links:
        return graph
    edges = graph.get("edges") or []

    def name_of(node):
        return str((node.get("data") or {}).get("link") or "").strip().lower()

    ins = {}
    for n in nodes:
        if n.get("type") == "common.link_in" and name_of(n):
            ins.setdefault(name_of(n), []).append(n["id"])

    feeds = {}          # link_out id -> [(source node id, its ON/OFF handle)]
    for e in edges:
        if e["target"] in links and links[e["target"]]["type"] == "common.link_out":
            # A Detection Stream's or Class Filter's ON/OFF connection keeps
            # its handle through the jump: OFF is the state, not detections
            # (state_points.split). Every other handle drops, as it did.
            hd = str(e.get("sourceHandle") or "")
            feeds.setdefault(e["target"], []).append(
                (e["source"], hd if hd in state_points.HANDLES else None))
    takes = {}          # link_in id -> [(target id, handle)]
    for e in edges:
        if e["source"] in links and links[e["source"]]["type"] == "common.link_in":
            takes.setdefault(e["source"], []).append(
                (e["target"], e.get("targetHandle")))

    jumped = []
    for out_id, sources in feeds.items():
        for in_id in ins.get(name_of(links[out_id]), ()):
            for target, handle in takes.get(in_id, ()):
                for source, shd in sources:
                    jumped.append({"id": f"lk_{source}_{target}_{handle or ''}"
                                         f"{'_' + shd if shd else ''}"[:80],
                                   "source": source, "target": target,
                                   "sourceHandle": shd, "targetHandle": handle})

    kept = [e for e in edges
            if e["source"] not in links and e["target"] not in links]
    seen, merged = set(), []
    for e in kept + jumped:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        merged.append(e)
    return {**graph,
            "nodes": [n for n in nodes if n["id"] not in links],
            "edges": merged}


def prune_stale_column_wires(graph, tables):
    """Drop wires anchored to a Data Table COLUMN that no longer exists.

    Renaming a column removes its ports from the node, so an edge drawn to
    the old name has nothing to anchor to: the canvas cannot draw it, so the
    operator cannot delete it -- an invisible wire that kept a warning alive
    for ever. Pruned here, where the graph is loaded, rather than tolerated
    at compile time only.

    Only columns of a table that STILL EXISTS are judged: a node whose whole
    table is missing keeps its wires, because that is a table problem the
    operator has to see, not a rename to tidy up after.

    Mutates `graph`; returns how many edges were removed.
    """
    nodes = {n.get("id"): n for n in graph.get("nodes") or []}

    def stale(node, handle, prefix):
        if (node or {}).get("type") != "output.dataset" \
                or not handle.startswith(prefix):
            return False
        key = _slug(((node.get("data") or {}).get("dataset")) or "")
        table = (tables or {}).get(key)
        if not table:
            return False
        return handle[len(prefix):] not in {c.get("name")
                                            for c in table.get("columns") or []}

    kept, dropped = [], 0
    for e in graph.get("edges") or []:
        if stale(nodes.get(e.get("source")),
                 str(e.get("sourceHandle") or ""), "o_") \
                or stale(nodes.get(e.get("target")),
                         str(e.get("targetHandle") or ""), "c_"):
            dropped += 1
            continue
        kept.append(e)
    if dropped:
        graph["edges"] = kept
    return dropped


#: Every node that TAKES a value on an ordinary input: the Expression, a
#: Running Total, the old Formula, and each node of the Math family. Named
#: once because four separate rules ask the same question ("may this number go
#: in here?"), and four copies of the list is four chances to add a node to
#: three of them.
#: Outputs that take SEVERAL rules on one input. Each wired rule fires the
#: node on its own moment (one output document per rule, like a Data
#: Table's writers), and a publisher sends each wired value on its own
#: clock. Fan-in is drawable for every output on the canvas already; this
#: is what the compiler makes of it. The level-followers (Zone Switch, a
#: PWM set to follow, the Modbus Register) are not here -- two levels into
#: one lamp have no single answer -- and the message nodes take many wires
#: for their own reason (see upstream_fn).
FAN_IN_OUTPUTS = ("io_out", "io_pwm", "gpio", "mqtt", "mqtt_publish",
                  "webhook", "webhook_publish", "snapshot", "record")

VALUE_SINKS = ("analytics.expr", "analytics.formula",
               "analytics.total") + tuple(MATH_NODES)


#: The port a Math node (and the Formula it grew out of) offers for the FLOW
#: rather than for a number: "run this here". It is the same handle the
#: optional gate always used -- a condition wired to it still gates the
#: arithmetic -- but anything at all may land on it now, because arithmetic is
#: logic that belongs somewhere in a pipeline and had no way to say where.
#: Wired with something that carries no value (a Detection Stream, a Class
#: Filter, a zone), it is sequence alone and compiles to nothing.
RUN_HANDLE = "when"


def _takes_run_wire(tgt_type):
    return tgt_type == "analytics.formula" or tgt_type in MATH_NODES


#: The Detection Stream's snapshot trigger input (handle id).
SNAP_HANDLE = "snap"

#: Most frames one snapshot may detect: a safety rail, not a feature -- a
#: typo of 100000 must not turn a snapshot into an hour of video.
MAX_SNAP_FRAMES = 600


def edge_allowed(src_type, tgt_type, tgt_handle=""):
    """Category-order rule for one edge. Returns (ok, reason)."""
    s, t = REGISTRY.get(src_type), REGISTRY.get(tgt_type)
    if not s or not t:
        return False, "unknown node type"
    # A Comment explains the graph; it is not part of it.
    if "note.comment" in (src_type, tgt_type):
        return False, "a Comment is a note on the canvas - it takes no wires"
    # A PROBE MAY WATCH ANYTHING. Checked before every pipeline-order rule
    # below, because those rules ask "may a value flow from here to there" and
    # nothing flows into a probe: it is deleted with the rest of the common
    # category before validation, so the wire reaches the engine as nothing.
    # Asked in pipeline order instead, the most useful probe of all -- one on
    # a Telegram or a board output, to answer "did that actually send?" --
    # was refused by the "an output is not something to fire on" rule, and a
    # probe on a dashboard card was refused because a card feeds nothing.
    # The dead-end half is kept exactly as it was: nothing reads a probe.
    if src_type == "common.debug":
        return False, f"{s['label']} takes no wire on that side"
    if tgt_type == "common.debug":
        return True, ""
    # A function BOUNDARY takes and gives anything, because it is not a stage
    # in the pipeline -- it is a bracket around one. Every rule below asks
    # "may a node of this rank meet a node of that rank", and by the time the
    # compiler asks it these three are gone: resolve_functions() has replaced
    # each Call with a copy of the body, and the body's own wires are what get
    # validated. Refusing an edge here would refuse the function itself.
    if src_type in FUNCTION_NODES or tgt_type in FUNCTION_NODES:
        # ...except backwards through a boundary. A Start receives nothing --
        # its parameters are filled at the Call, which is where the operator
        # asked for the function -- and an End gives nothing on, for the same
        # reason: the value comes out of the Call.
        if tgt_type == "function.start":
            return False, ("a Function Start receives nothing - wire the "
                           "value into the Call instead")
        if src_type == "function.end":
            return False, ("a Function End gives nothing on - the value comes "
                           "out of the Call")
        return True, ""
    # A Debug probe is a dead end, and a Link in starts a jump rather than
    # receiving one.
    if tgt_type == "common.link_in" or src_type == "common.debug":
        return False, (f"{s['label'] if src_type == 'common.debug' else t['label']}"
                       " takes no wire on that side")
    # THE RUN PORT takes anything. Every rule below asks "is this a NUMBER fit
    # for arithmetic", which is the right question for an operand and the
    # wrong one here: this port says WHERE in the flow the calculation sits,
    # and a Detection Stream is exactly what one wants to say that with.
    if str(tgt_handle or "") == RUN_HANDLE and _takes_run_wire(tgt_type):
        return True, ""
    # A Trigger's ZONE port: which area to recolour while it holds. A zone
    # reaches nothing in the output stage otherwise -- and rightly, since it
    # is a place rather than a value -- but this port asks for a place, and
    # naming it on the wire is what lets a Trigger paint an area its own
    # rule does not happen to watch.
    if str(tgt_handle or "") == "zone" and tgt_type == "output.trigger" \
            and src_type == "zone.ref":
        return True, ""
    # ...and its DUPLICATE GUARD ports: what a Data Table already holds for
    # the object (Current OID, Current value), read off a column's read port
    # (_trigger_guard). The table is a sink everywhere else on this side.
    if str(tgt_handle or "") in TRIGGER_GUARD_PORTS \
            and tgt_type == "output.trigger" and src_type == "output.dataset":
        return True, ""
    # An Alert node behaves like a When for wiring: it DEFINES the function's
    # trigger, so a card after it may bind to that function's alert state
    # (Alert -> Verdict Card is the natural "show my alert" chain).
    # ...and a Data Table takes that same alert as its "write a row now"
    # signal, which is the one output that legitimately sits behind another.
    if src_type in _TRANSPARENT_OUTPUTS and (t["category"] == "dashboard"
                                             or tgt_type == "output.dataset"):
        return True, ""
    # A Trigger IS the moment, so everything that reacts to one sits after it:
    # a card showing the state, a Data Table row, a Snapshot, a Publish. It
    # keeps the wiring category "output" (that is what the pipeline rules
    # reason about, and moving it would change which nodes may meet on every
    # graph already saved), so the reach it needs is named here.
    if src_type == "output.trigger" and (t["category"] in ("dashboard", "output")
                                         or tgt_type in VALUE_SINKS):
        return True, ""
    # An Alert into a Formula: the logic that follows an alarm belongs where
    # logic is written. The alert arrives as its rule's `active` -- 1 while the
    # condition holds -- so "this alarm AND that one", "count the minutes it
    # was on", "average of the two bays" are all one expression.
    if src_type in _TRANSPARENT_OUTPUTS and tgt_type in VALUE_SINKS:
        return True, ""
    # Every other output still reaches nothing analytical: an Email publishes
    # no value, so a Formula behind one would have nothing to read.
    # ...and a MATH node: "the total of this column today, divided by the
    # shift's hours" is a calculation on recorded rows, and the recorded rows
    # are only ever reachable through the table's read ports. The compile
    # turns such an input into a query variable (see _dataset_operand), so
    # the arithmetic itself runs on a live number like any other.
    if src_type == "output.dataset" and tgt_type.startswith("math."):
        return True, ""
    if s["category"] == "output" and t["category"] == "analytics":
        return False, (f"{s['label']} publishes no value to calculate with - "
                       "only a Condition can feed a Formula")
    # A Data Table is the one node that is BOTH a sink and a source: every
    # column has a write port and a read port. The read side feeds a card, so
    # the "output goes nowhere" rule cannot apply to it -- it is the only way
    # a dashboard shows what was actually recorded rather than what is live.
    if src_type == "output.dataset" and t["category"] == "dashboard":
        return True, ""
    # A CARD OR CHART THAT ASKS RECORDED DATA, into a CHART. The one wire
    # between two dashboard nodes, and it is not a card feeding a card: a
    # Card/Chart on "recorded events" or "a data table" (asks_recorded) states
    # a QUESTION -- "violations per hour on camera 2" -- and a chart that can
    # draw several series has no other way to be given one. The source keeps
    # working as a card; wiring it in says "draw what you count as a line on
    # that chart too". The retired Event Card is the same node by its old name.
    #
    # Allowed by TYPE, because this check sees no data: a Card showing a live
    # value has no output port on the canvas, and one switched back after it
    # was wired is named by the compile rather than refused here (a refusal
    # would stop the whole graph compiling over one stale line).
    #
    # Kept to the Chart deliberately. Every other dashboard node shows ONE
    # value, so a wire into one would have nowhere to put a second question.
    if src_type in ("dashboard.events", "dashboard.card", "dashboard.graph") \
            and tgt_type == "dashboard.graph":
        return True, ""
    # ...and for the same reason it may feed a Query: a Query reads tables,
    # and the Data Table node IS how a table is named on the canvas. Wiring
    # it in is what says which tables the statement is allowed to see.
    if src_type == "output.dataset" and tgt_type == "storage.query":
        return True, ""
    # A Query's rows become a file HERE, and the file goes to a message. Both
    # halves are named explicitly: the shared "query" category would otherwise
    # let a Query feed a Query, and a CSV feed a card, neither of which means
    # anything.
    if src_type == "storage.query" and tgt_type == "storage.csv":
        return True, ""
    # A Query answers a BUTTON. Same shape as the CSV wire and for the same
    # reason: the statement is what the button runs, and the wire is what says
    # which button runs which.
    if src_type == "storage.query" and tgt_type == "integration.command":
        return True, ""
    # ...and a Snapshots node answers one the same way. Two kinds of answer,
    # one kind of wire: whichever is on the port is what the button sends.
    if src_type == "integration.photos" and tgt_type == "integration.command":
        return True, ""
    if tgt_type == "integration.photos":
        return False, (f"{s['label']} has nothing to add to a Snapshots node "
                       "- it reads the pictures this system already saved")
    if src_type == "integration.photos":
        return False, ("Snapshots answers a command button - wire it to the "
                       "button that should send the pictures")
    if tgt_type == "integration.command":
        return False, (f"{s['label']} cannot answer a command - wire a Query "
                       "into the button you want it to answer")
    if src_type == "integration.command":
        return False, ("a Command is answered where it is asked, in the chat "
                       "- it feeds nothing on the canvas")
    # A Watch reads a Query on a timer and sends through a message node. Both
    # halves are named explicitly, for the same reason the Command wires are:
    # the message node is an OUTPUT, and output-to-output means nothing
    # anywhere else on the canvas.
    if src_type == "storage.query" and tgt_type == "integration.watch":
        return True, ""
    if tgt_type == "integration.watch":
        return False, (f"{s['label']} is not something to watch - wire a "
                       "Query in, and the Watch runs it on a timer")
    if src_type == "integration.watch":
        if tgt_type in ("output.email", "output.telegram"):
            return True, ""
        return False, ("a Watch sends a message when its number crosses - "
                       "wire it into a Telegram or an Email node")
    if src_type == "storage.csv":
        if tgt_type in ("output.email", "output.telegram"):
            return True, ""
        return False, (f"a CSV file attaches to an Email or a Telegram "
                       f"message - {t['label']} cannot carry one")
    if tgt_type == "storage.csv":
        return False, (f"{s['label']} has no rows to write - a CSV file is "
                       "made from a Query")
    # A PRE-PROCESS works on the PICTURE, before the detector runs, so the one
    # thing that may feed it is the stream whose camera it adjusts. A Class
    # Filter or a Zone wired in would read as "filter the detections, then
    # change the image they came from", which is backwards in time -- the
    # detections do not exist yet. (What comes AFTER is unrestricted: the
    # category is "filter", so it feeds a filter, a zone or a rule like any
    # other narrowing of the stream.)
    #
    # Two in a row is refused here rather than at compile, so the wire is
    # simply not drawable: the steps inside one node are an ordered list the
    # operator owns, and a second node would mean a second list with no
    # visible order between the two. Add the steps to the first node.
    if src_type == "filter.preprocess" and tgt_type == "filter.preprocess":
        return False, ("one Pre-process per camera - add the extra steps to "
                       "the chain on the first one, where their order is "
                       "visible")
    # A Link out is allowed in because it IS whatever fed its Link in: jumps
    # are how a graph with several cameras keeps its wires readable, and
    # resolve_links has already replaced the pair by the time the compiler
    # looks. A jump carrying something other than a stream is caught there.
    # A Detection Stream in Snapshot mode takes a SNAP input: when something
    # goes true it detects a few frames and then stops. Anything that publishes
    # a value or a moment may drive it -- an Inject, a Trigger, a sensor read.
    # Checked before the pipeline-order rules, which say nothing flows INTO a
    # source. Only the snap port: every other port of a source stays closed.
    if tgt_type == "source.detection" and str(tgt_handle or "") == SNAP_HANDLE:
        if src_type in ("common.inject", "output.trigger") or s["category"] in ("variable", "logic"):
            return True, ""
        return False, (f"{s['label']} cannot start a snapshot - wire an "
                       "Inject, a Trigger or a sensor into the Detection "
                       "Stream's snap input")
    if tgt_type == "filter.preprocess" \
            and src_type != "source.detection" and s["category"] != "common":
        return False, (f"{s['label']} cannot feed a Pre-process - it "
                       "adjusts the picture BEFORE anything is detected, "
                       "so wire the Detection Stream of the camera it "
                       "should apply to")
    if t["category"] not in _ALLOWED_TARGETS[s["category"]]:
        return False, (f"{s['label']} cannot connect to {t['label']} - "
                       "the pipeline flows source → filter → zone → analytics → dashboard/output")
    # A Variable holds a value for a formula to read. It publishes nothing of
    # its own, so a card or an alert wired after it would have no function to
    # bind to -- point those at the thing it names instead.
    # variable.ref may feed anything a value feeds: a WIRED one publishes a
    # real variable (it stores), and an unwired one wired to a card is caught
    # at compile with a message on the card -- the wiring rule cannot see
    # which of the two it is.
    # Only the HOLDER kind of variable takes an input; a constant or an API
    # reading makes its own value and a wire into one would mean nothing.
    # ...except a Modbus Read's TRIGGER port (interval 0): what arrives there
    # is WHEN to read, not a value, so an Inject, a Schedule or a rule may.
    if tgt_type == "variable.rs485" and str(tgt_handle or "") == "trig" \
            and s["category"] in ("variable", "analytics", "logic"):
        return True, ""
    if t["category"] == "variable" and tgt_type != "variable.ref":
        return False, (f"{t['label']} makes its own value - only a Variable "
                       "(the holder) takes a wire in")
    if src_type == "variable.ref" and tgt_type == "variable.ref":
        return False, "a Variable into a Variable stores nothing new - wire " \
                      "the rule itself into the second one"
    # A branch is a DECISION, not a reading. Its ports answer "which way did
    # this go", and storing that in a Variable keeps the verdict while losing
    # the number the verdict was about -- so what the Variable then publishes
    # is a 1 that nothing can be recomputed from. Store the value being
    # tested, and branch on the Variable instead; or, if the moment itself is
    # what matters, wire the port into a Trigger, which is the node for it.
    if is_branch(src_type) and tgt_type == "variable.ref":
        return False, (f"{s['label']} is a decision, not a value to keep - "
                       "wire the number it tests into the Variable and branch "
                       "on that, or wire this port into a Trigger")
    # The whole-frame counters count the WHOLE frame; a Zone wired in would be
    # silently ignored, which reads as a broken node rather than a wrong wire.
    if s["category"] == "zone" and tgt_type in ("analytics.frame",
                                                "analytics.frame_count",
                                                "analytics.frame_total"):
        return False, (f"{t['label']} counts the whole frame - wire the "
                       "Detection Stream (through a Class Filter) instead "
                       "of a Zone")
    # Only the Alert may be followed. Every other output DOES something --
    # sends, saves, switches -- and publishes no value, so a node wired after
    # one would have nothing to bind to.
    if s["category"] == "output" and src_type not in _TRANSPARENT_OUTPUTS:
        return False, (f"{s['label']} is what happens when a rule fires, not "
                       "something to fire on - wire this after the "
                       "Condition instead")
    # UNIVERSAL VALUE WIRING. Anything whose output is a plain number or a 0/1
    # answer may feed anything that takes a value -- every Math node, the
    # Expression, a Running Total. A measured value, a stored variable, a
    # branch port and another Math node's result are all just numbers by the
    # time they leave, and a category rule that let a count reach a Formula but
    # not an Average drew a line the operator could not see.
    #
    # What stays refused is what is NOT a number: an Object ID, a zone or line
    # reference, a Data Table's whole-table port. Each of those is an identity
    # or a handle, and arithmetic on one is a mistake with a plausible-looking
    # answer -- they are caught by their own rules (the OID/timer port guard,
    # zone_kind, and the dataset rules above).
    # A zone or a line is a PLACE, not a quantity. It reaches a measurement
    # node on that node's zone port, where it says where to measure -- but
    # arithmetic on one has no meaning, and the wire would compile to a
    # plausible-looking wrong number rather than to nothing.
    if s["category"] in ("zone", "source", "filter") \
            and (tgt_type in MATH_NODES or is_branch(tgt_type)):
        return False, (f"{t['label']} works on NUMBERS. "
                       + ("A Zone says where to measure"
                          if s["category"] == "zone"
                          else "Detections are objects, not a quantity")
                       + " - wire this into a measuring node, and wire that "
                         "node's value in here")
    if t["category"] == "analytics" and s["category"] in ("analytics", "variable") \
            and tgt_type not in VALUE_SINKS:
        return False, ("only a Math node, an Expression or a Running Total can "
                       "take another analytics/variable as input")
    return True, ""


# ---------------------------------------------------------------------------
# compile
# ---------------------------------------------------------------------------

#: The Pre-process chain's vocabulary: op -> {param: (min, max, default)}.
#: Declared here rather than only in the app because the compiler is the
#: boundary -- the app reads a config file it did not write, and a gain of
#: 900 or a kernel of 400 reaches it as a frame rate nobody can explain. The
#: ranges are the same ones the inspector's sliders offer, so a value that
#: arrives outside them came from a hand-edited graph, not from the editor.
#:
#: `blur` also carries `kind` (box | gaussian), which is a choice rather than
#: a number and so is checked separately.
#: `mask` carries no numbers at all -- it carries ZONES, resolved to polygons
#: at compile (see _preprocess_mask).
#:
#: These ranges are systems/demo/app/preprocess.py's, exactly: the app clamps
#: to them, and a compiler that allowed a wider one would write a number the
#: box silently corrects -- an inspector reading 40 over a picture adjusted
#: by 25.
PREPROCESS_OPS = {
    "grayscale": {},
    "exposure": {"gain": (0.05, 4.0, 1.0), "gamma": (0.1, 5.0, 1.0)},
    "sharpness": {"amount": (0.0, 3.0, 1.0), "ksize": (3, 9, 5)},
    "blur": {"ksize": (3, 31, 5), "sigma": (0.0, 25.0, 0.0)},
    "sobel": {"dx": (0, 2, 1), "dy": (0, 2, 1), "ksize": (1, 7, 3),
              "scale": (0.05, 8.0, 1.0)},
    # A mask's own ksize is the blur it fills WITH, when `fill` is "blur".
    "mask": {"ksize": (9, 31, 21)},
}

#: Blur kinds, in the order the inspector offers them. First is the default:
#: gaussian, because a box blur of the same radius reads as a mistake beside
#: it, and this is a picture a person will look at.
PREPROCESS_BLUR_KINDS = ("gaussian", "box")

#: What a mask does to everything OUTSIDE (or inside, inverted) its polygons.
#: First is the default. "blur" rather than black where a camera watches a
#: public path beside the area it is actually for: the detector stops seeing
#: it, and a person reviewing the footage can still tell the picture is live.
PREPROCESS_MASK_FILLS = ("black", "blur")

#: How many zones one mask step may name. A mask is "the area this camera is
#: for", which is one shape or a few; a list longer than this is a polygon
#: budget being spent on every frame.
MAX_MASK_ZONES = 8

#: Params that are a KERNEL SIZE: an odd whole number, because every kernel
#: this uses is centred on the pixel it is replacing and an even one has no
#: centre. Rounded up rather than refused -- 4 is a typo for 5, not a mistake
#: worth stopping a save for.
_PREPROCESS_ODD = ("ksize",)

#: How long a chain may be. Each step is work on every frame of every camera,
#: and a chain longer than this is not a picture adjustment any more -- it is
#: a reason the detector is slow, with nothing on the canvas to say so.
MAX_PREPROCESS_STEPS = 8


def _preprocess_step(raw):
    """One step of a Pre-process chain, cleaned. Returns None for an unknown op.

    The operator's ORDER is the caller's business; this only fixes up one
    step's params. Every param the op declares is emitted, filled from its
    default when missing, so the app never has to ask whether a key is there
    -- it reads a complete step or none at all.

    A `mask` comes back still naming its ZONES: turning those into polygons
    needs the system's zone list and the camera this chain belongs to, and
    neither is one step's business. The compile pass does it.
    """
    if not isinstance(raw, dict):
        return None
    op = str(raw.get("op") or "").strip().lower()
    spec = PREPROCESS_OPS.get(op)
    if spec is None:
        return None
    # Switching a step OFF is not deleting it: a chain gets tuned by trying it
    # without one step, and having to delete the step (and its numbers) to do
    # that is why people keep a second copy of the node. Absent means on, so
    # every chain saved before this existed reads exactly as it did.
    step = {"op": op, "enabled": bool(raw.get("enabled", True))}
    if op == "mask":
        zones, seen = [], set()
        for z in (raw.get("zones") or [])[:MAX_MASK_ZONES]:
            zid = str(z or "").strip()
            if zid and zid not in seen:
                seen.add(zid)
                zones.append(zid)
        step["zones"] = zones
        step["invert"] = bool(raw.get("invert"))
        fill = str(raw.get("fill") or "").strip().lower()
        step["fill"] = fill if fill in PREPROCESS_MASK_FILLS \
            else PREPROCESS_MASK_FILLS[0]
    if op == "blur":
        kind = str(raw.get("kind") or "").strip().lower()
        step["kind"] = kind if kind in PREPROCESS_BLUR_KINDS \
            else PREPROCESS_BLUR_KINDS[0]
    if op == "sobel":
        # Whether the edge map is stretched to fill 0..255. A flag rather than
        # a number, so it sits outside the range loop below.
        step["normalize"] = bool(raw.get("normalize"))
    for name, (low, high, default) in spec.items():
        try:
            value = float(raw[name]) if name in raw and raw[name] is not None \
                else float(default)
        except (TypeError, ValueError):
            value = float(default)
        value = min(max(value, low), high)
        # Whole numbers stay whole: a kernel of 3.0 and a dx of 1.0 are a
        # kernel of 3 and a dx of 1, and the TOML they land in should say so.
        if isinstance(default, int):
            value = int(round(value))
            if name in _PREPROCESS_ODD and value % 2 == 0:
                value = min(value + 1, int(high))
        step[name] = value
    return step


def _preprocess_mask(step, zone_by_id, camera):
    """A mask step's ZONE IDS resolved to polygons. Returns (step, error, warning).

    The operator draws the area once, in the Zone Manager, and points the mask
    at it -- so the node stores zone ids and the compile resolves them, the
    same way every rule on the canvas takes its zone by reference. The APP is
    handed points and nothing else: it opens a camera and multiplies a
    polygon by the frame size, and giving it a zone id would mean teaching the
    frame loop to look zones up and to notice when one is redrawn.

    The points are already NORMALISED 0..1 in storage -- see
    detector_control._zone_list, which is why the same shape works at any
    resolution and why a zone belongs to exactly one camera. Nothing is
    divided here; they are clamped and rounded, and that is all.
    """
    zones, polygons, missing = step.get("zones") or [], [], []
    for zid in zones:
        z = zone_by_id.get(zid)
        if not z:
            missing.append(zid)
            continue
        # A zone's points mean something only over the picture they were
        # drawn on. On another camera they are a shape in the wrong place --
        # which masks the wrong part of the frame and looks like a detector
        # that stopped working, so it is an error rather than a warning.
        drawn_on = str(z.get("camera") or "")
        if drawn_on and camera and drawn_on != camera:
            return step, (f"the mask names zone {z.get('name') or zid!r}, "
                          f"which was drawn on another camera - its shape "
                          f"means nothing over this one"), None
        if drawn_on and camera == functions_service.ALL_CAMERAS:
            return step, (f"the mask names zone {z.get('name') or zid!r}, "
                          f"which was drawn on one camera - a stream set to "
                          f"every camera cannot use it"), None
        points = [[round(min(max(float(x), 0.0), 1.0), 6),
                   round(min(max(float(y), 0.0), 1.0), 6)]
                  for x, y in (z.get("points") or [])]
        if len(points) < 3:
            return step, (f"zone {z.get('name') or zid!r} is a "
                          f"{z.get('kind') or 'line'} - it has no inside, so "
                          f"there is nothing for a mask to keep"), None
        polygons.append(points)
    out = {"op": "mask",
           # A mask that resolved to nothing is compiled OFF. An empty polygon
           # list is not "no mask": it is "keep nothing" or "keep everything"
           # depending on `invert`, and both are a detector that silently
           # stopped seeing what it was watching.
           "enabled": bool(step.get("enabled")) and bool(polygons) and not missing,
           "polygons": polygons,
           "invert": bool(step.get("invert")),
           "fill": step.get("fill") or PREPROCESS_MASK_FILLS[0],
           # The blur the mask fills WITH, carried whatever `fill` says: a
           # radius dialled in and then switched to black must still be there
           # when it is switched back.
           "ksize": step.get("ksize", PREPROCESS_OPS["mask"]["ksize"][2])}
    if missing:
        names = ", ".join(repr(m) for m in missing[:4])
        return out, None, (f"the mask names zone {names}, which this system no "
                           f"longer has - the step is switched off until it is "
                           f"re-picked, so the picture goes through unmasked")
    if not zones:
        return out, None, ("this mask names no zone - pick the area the "
                           "detector should see in the inspector")
    return out, None, None


def _check_classes(node, spec, d, known, err, warn, active=None):
    """Flag class names the ACTIVE model cannot emit.

    Swapping the model swaps the vocabulary: a rule left bound to a class the
    new model never emits matches nothing, forever, with no symptom other than
    a number that stays at zero. So a node whose classes are ALL unknown is a
    hard error -- it cannot possibly fire and the operator must re-pick -- and
    a partially-unknown one is a warning, since the rest of it still works.

    `known` is None when the vocabulary could not be read (no labels file);
    nothing is checked then rather than guessing.
    """
    if not known:
        return
    fields = {
        "filter.class": ("classes",),
        "analytics.proximity": ("class_a", "class_b"),
        "analytics.guard": ("classes", "alert_classes"),
        "analytics.trails": ("classes",),
    }.get(node["type"])
    if not fields:
        return
    for field in fields:
        raw = d.get(field)
        picked = ([str(raw).strip()] if isinstance(raw, str) and raw.strip()
                  else [str(c).strip() for c in (raw or []) if str(c).strip()])
        if not picked:
            continue
        missing = [c for c in picked if c not in known]
        if not missing:
            # The model HAS these classes, but the keep-filter on the AI Models
            # page may be dropping them before any rule sees them -- a rule
            # that can never fire, with a number stuck at zero to show for it.
            if active is not None:
                filtered = [c for c in picked if c not in active]
                if filtered:
                    names = ", ".join(f"'{c}'" for c in filtered[:6])
                    warn(node["id"],
                         f"{names} {'is' if len(filtered) == 1 else 'are'} switched "
                         "off by 'Classes to keep' on the AI Models page, so this "
                         "will never fire until they are ticked there")
            continue
        names = ", ".join(f"'{c}'" for c in missing[:6])
        if len(missing) == len(picked):
            err(node["id"],
                f"{names} {'is' if len(missing) == 1 else 'are'} not a class the "
                "current AI model emits - the model changed, so pick the class "
                "again on this node")
        else:
            warn(node["id"],
                 f"{names} no longer exists in the current AI model and will "
                 "never match - pick the class again")


def node_mode(node, spec):
    """The chosen mode of a multi-mode node, always one the spec declares."""
    modes = spec.get("modes") or {}
    if not modes:
        return ""
    want = str(((node.get("data") or {}).get("mode") or "")).strip().lower()
    return want if want in modes else next(iter(modes))


#: The opposite of each comparison, for an Alert asked to fire while its
#: Condition is FALSE. Inverting the operator keeps one threshold in one
#: place; the alternative -- a second Condition with the opposite sign -- is
#: two copies of a number that have to be kept equal by hand.
_INVERSE_OPS = {">": "<=", ">=": "<", "<": ">=", "<=": ">",
                "==": "!=", "!=": "=="}


#: How long counting timing may be set to, in seconds. An hour is already far
#: past anything a counter is for; past it the operator wants a Cycle Time.
MAX_COUNT_TIMING_S = 3600


def _count_timing(node, data, params, warn):
    """Compile the counters' Advanced timing onto the rule.

    Two settings, one question -- WHEN does an object count:

      * `enter_after_s` -- it must have been there this long first, so a
        forklift driving through a zone does not read as one that stopped in
        it. 0 is real-time counting, and the default.
      * `leave_after_s` -- it keeps counting for this long after it is last
        seen, so a tracker dropping an object for a frame, or a person
        stepping a foot over the line, does not make the number flicker.

    Both need TRACK IDS -- what says an object is the same one it was a frame
    ago. With tracking off the engine falls back to counting what the frame
    shows, which is the honest answer to "who has been here two seconds" when
    nothing knows who anybody is. The inspector says so where the setting is
    typed; the compiler does not warn, because it cannot see whether tracking
    is on and a warning that fires either way is noise.
    """
    for key in ("enter_after_s", "leave_after_s"):
        raw = data.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            warn(node["id"], f"{key} is not a number - ignored")
            continue
        value = max(0.0, min(float(MAX_COUNT_TIMING_S), value))
        if value:
            params[key] = value


#: The whole-frame rule -> the same rule narrowed to a zone. What makes a
#: zone port OPTIONAL: the node keeps one fn_type per mode, and wiring a zone
#: swaps it for the zone-scoped rule that publishes the same metrics.
#: Every node that watches for objects and answers PER CLASS -- the merged
#: watch and the two it replaced. Named once because the per-class ports are
#: made by stamping the watched classes onto the node before any wire
#: resolves, and a node missing from that stamp has no ports at all.
OBJECT_WATCHES = ("analytics.object", "analytics.object_frame",
                  "analytics.object_zone")

ZONE_RULE_OF = {
    "frame_count": "zone_count",
    "frame_unique": "zone_unique",
}

#: The Counter's three modes, as the canvas words them. Read off the WIRING:
#: what is plugged in is what it counts, so the setting cannot disagree with
#: the picture.
COUNTER_BOTH_WIRED = ("A Counter counts inside a Zone OR across a Line, not "
                      "both - remove one of the two wires")


def lower_counter_lines(graph):
    """A Counter with a line on its Line port compiles as the line counter it
    is. Returns (graph, {node id: problem}).

    The canvas never saves that shape: it turns the node into
    analytics.entry_exit as the line lands (registry.js counterToLine), so
    every graph it writes compiles untouched by this. This is the backstop
    for a graph that reached the compiler another way -- a template, an
    import, the API -- so the same picture means the same thing wherever it
    was drawn. The rewrite is exactly the canvas's: same id, same fnId, the
    line moved onto the line counter's own port, the direction taken from
    `line_mode` (what the canvas keeps there while the node is a Counter) or
    the Line Counter's default.

    A Zone AND a Line on one Counter is refused rather than guessed: "count
    inside this area" and "count across this line" are two answers, and
    picking one in silence would publish a number the operator did not ask
    for. Pure: `graph` is not mutated.
    """
    nodes = list(graph.get("nodes") or [])
    by_id = {n.get("id"): n for n in nodes}
    lined, zoned = {}, set()
    for e in graph.get("edges") or []:
        tgt = by_id.get(e.get("target"))
        src = by_id.get(e.get("source"))
        if not tgt or tgt.get("type") != "analytics.counter" \
                or not src or src.get("type") != "zone.ref":
            continue
        if str(e.get("targetHandle") or "") == COUNTER_LINE_PORT:
            lined.setdefault(tgt["id"], []).append(id(e))
        else:
            zoned.add(tgt["id"])
    if not lined:
        return graph, {}
    problems = {nid: COUNTER_BOTH_WIRED for nid in lined if nid in zoned}
    line_spec = REGISTRY["analytics.entry_exit"]
    out_nodes = []
    for n in nodes:
        if n.get("id") not in lined or n["id"] in problems:
            out_nodes.append(n)
            continue
        d = dict(n.get("data") or {})
        way = str(d.pop("line_mode", "") or "")
        d["mode"] = way if way in line_spec["modes"] else line_spec["data"]["mode"]
        for key in ("distinct", "momentary", "totals"):
            d.setdefault(key, line_spec["data"][key])
        out_nodes.append({**n, "type": "analytics.entry_exit", "data": d})
    # By object identity, not edge id: a hand-built graph may carry wires
    # with no id at all, and two of those must not be mistaken for one.
    moved = {eid for nid, eids in lined.items() if nid not in problems
             for eid in eids}
    out_edges = [({**e, "targetHandle": "zone"} if id(e) in moved else e)
                 for e in graph.get("edges") or []]
    return {**graph, "nodes": out_nodes, "edges": out_edges}, problems


def node_fn_type(node, spec):
    """Which RULE this node compiles to.

    Usually one per node type. A multi-mode node (In Frame: now / total) picks
    per node, because the modes are the same question over a different span --
    a setting, not a different thing to drag onto the canvas.
    """
    mode = node_mode(node, spec)
    if mode:
        return (spec["modes"][mode].get("fn_type") or spec["fn_type"])
    return spec["fn_type"]


def node_metrics(node, spec):
    """The metrics this node publishes, for the mode it is in.

    An object watch also answers per CLASS -- the compiler stamps the watched
    classes onto the node (data._classes) before anything resolves wires, so
    a wire dropped on a class port reads fn_<id>_<class>."""
    mode = node_mode(node, spec)
    base = (list(spec["modes"][mode].get("metrics") or spec["metrics"])
            if mode else list(spec.get("metrics") or []))
    # A live Line Counter also offers its running totals as ports.
    if spec.get("live_metrics") and (node.get("data") or {}).get("momentary"):
        base = base + list(spec["live_metrics"])
    extra = [_slug(c)
             for c in (node.get("data") or {}).get("_classes") or ()]
    return base + [e for e in extra if e]


#: What a dashboard card can be asked to COUNT OVER.
#:
#:   live    the value as it is right now -- an occupancy, a level, a total
#:           since the app started. The original behaviour, and the default.
#:   follow  the period the ANALYTICS page is showing. Analytics narrows any
#:           total that has a per-day tally to its Today/Week/Month picker
#:           (analytics.scope_stats), so the same card answers for whichever
#:           period the operator is looking at. On the Dashboard, which has no
#:           picker, it reads as the all-time total.
#:   today / week / month
#:           a FIXED period, whatever page the card is on. This is what a
#:           wall display wants: "today's count" must mean today even when
#:           someone else has Analytics open on last month.
CARD_WINDOWS = ("live", "follow", "today", "week", "month")

#: Metrics that carry the per-day tally the windows are computed from. A
#: function that does not publish these cannot answer "how many today".
_PERIOD_METRICS = ("today", "week", "month")


def _windowed_metric(node, data, fn, metric, metrics, warn):
    """The metric a card should actually read, given its window setting.

    A period is not a different NUMBER, it is a different metric: the counting
    functions publish `today`/`week`/`month` beside `total`, and `total`
    itself is what Analytics narrows to its date range. So choosing a window
    is choosing which of those keys the card binds to -- no new plumbing on
    the render side, and the card stays an ordinary widget.
    """
    window = str(data.get("window") or "live").strip().lower()
    if window in ("", "live") or window not in CARD_WINDOWS:
        return metric
    if not all(m in metrics for m in _PERIOD_METRICS):
        warn(node["id"],
             f"{fn['name']} does not count per day, so it cannot answer "
             f"'{window}' - showing its live value instead. Use a Zone "
             "Counter or an In Frame Total for period counts.")
        return metric
    if window == "follow":
        # `total` is the one Analytics rewrites to the chosen range; on a page
        # with no range it stays the all-time figure.
        return "total"
    return window


# "(fn_<cycle time>_live_s > 20)", the first test of an If / Else if on it.
_DWELL_LIMIT_RE = re.compile(
    r"^\(fn_(.+?)_live_s (>|>=) ([0-9.]+(?:e[+-]?[0-9]+)?)\)(?: and not .*)?$")


def _hold_blocker(fn, functions):
    """Why a Snapshot behind this rule cannot HOLD its photo until the timer
    ends, or "" when it can. Only a rule that measures a stay can say when it
    is over: a Cycle Time that records each stay (the stay's end is what
    releases the photo), a rule of its own that tests one's live_s against a
    limit, or a counter with a minimum stay. Anything else would hold the
    photo for no reason."""
    p = fn.get("params") or {}
    clock = fn if fn.get("type") == "zone_dwell" else None
    if clock is None and fn.get("type") == "expr":
        m = _DWELL_LIMIT_RE.match(str(p.get("expr") or ""))
        clock = next((f for f in functions if m and f.get("id") == m.group(1)), None)
    if clock is None:
        try:
            if float(p.get("enter_after_s") or 0) > 0:
                return ""
        except (TypeError, ValueError):
            pass
        return ("Wait for the timer to end needs a timer: wire this Trigger "
                "after a Cycle Time (through an If / Else if on its time) or "
                "a node with a minimum stay. It was switched off")
    if not (clock.get("params") or {}).get("stamp_events"):
        return ("Wait for the timer to end needs the Cycle Time to record each "
                "stay: tick OID and set 'Per-object time arrives' to 'Once, "
                "when they leave'. It was switched off")
    return ""


def compile_graph(graph, zones, classes=None, active_classes=None, capture=None,
                  tables=None, cameras=None):
    """Compile a cleaned graph into artifacts.

    `zones` is the system's zone list ([{id, name, kind, enabled}, ...] from
    detector_control._zone_list). `classes` is every class the ACTIVE model can
    emit (functions_service.variables()["classes"]); None skips that check.
    `cameras` is the system's camera ids; None skips the stale-camera check.
    Pure function: returns
        {functions, outputs, widgets, errors: [{node, message}], warnings}
    Artifacts are only trustworthy when errors == [].
    """
    known = {str(c) for c in classes} if classes else None
    # What survives the keep-filter. None = not reported, so nothing is warned.
    active = {str(c) for c in active_classes} if active_classes else None
    # Comments are canvas furniture and disabled nodes are switched off, so
    # neither reaches validation or compilation. Dropping them HERE rather than
    # special-casing them at every step is what keeps the compiler readable --
    # everything below this line is a node that genuinely runs.
    # Functions first: a Call stands for a copy of a body, and the copies
    # hold jumps and wires of their own, so they have to exist before either
    # is resolved. Both passes are the same idea -- a way of DRAWING a graph
    # is turned into the graph it stood for, and nothing below this line
    # knows either feature exists.
    # The canvas as DRAWN, kept for the one question asked of it at the end:
    # which of the nodes the operator can see wait for detection.
    canvas_nodes = list(graph.get("nodes") or [])
    canvas_edges = list(graph.get("edges") or [])
    graph = resolve_functions(graph)
    # Jumps next: a Link out/in pair is a way of drawing a wire, so by the
    # time anything is validated the graph holds the wire it stood for.
    graph = resolve_links(graph)
    # A Counter with a line on its Line port is a line counter -- the canvas
    # saves it as one already, so this only ever changes a graph drawn
    # somewhere else (lower_counter_lines says why).
    graph, counter_problems = lower_counter_lines(graph)
    # ...and the pid -> OID rename, here as well as on load. The load path
    # rewrites the stored file, but the compiler is reached from more than one
    # place, and a graph that arrives spelling it the old way must compile the
    # same either way. Cheap, and it makes "does an old graph still work" a
    # property of the compiler rather than of how it was called.
    migrate_pid_to_oid(graph)
    graph = {**graph, "nodes": list(graph["nodes"]), "edges": list(graph["edges"])}
    publish_follow.migrate(graph)       # an MQTT Publish's own clock, as on load
    # SNAP WIRES. A wire into a Detection Stream's snap input says WHEN to
    # detect, not what to detect: nothing below (model and camera lookups,
    # the pipeline-order walks, the frames-fed pass) may follow it back to the
    # Inject or Trigger as though that were an upstream camera. They are taken
    # out here and read again, by name, where the snapshot config is written.
    det_ids = {n["id"] for n in graph["nodes"] if n.get("type") == "source.detection"}
    snap_wires = [e for e in graph["edges"]
                  if str(e.get("targetHandle") or "") == SNAP_HANDLE
                  and e.get("target") in det_ids]
    if snap_wires:
        graph = {**graph, "edges": [e for e in graph["edges"]
                                    if e not in snap_wires]}
    # The ON/OFF points of every Detection Stream and Class Filter: a wire
    # from one becomes a wire from a hidden clock node, so nothing below
    # reads it as a detection wire (state_points.py says why).
    graph = state_points.split(graph, edge_allowed)
    # ...and a point may be wired into ANY node: one that cannot take it
    # (a Line Counter, a Class Filter) loses that wire here and, with nothing
    # else wired in, is off with the reason said on it. The wire still GATES
    # what is behind it (state_points.plans).
    graph, gate_only, point_off = state_points.refuse(
        graph, edge_allowed, REGISTRY, STANDALONE_FN_TYPES)
    dead = {n["id"] for n in graph["nodes"]
            if (n.get("data") or {}).get("disabled")} | set(point_off)
    # Switching a node off switches off what it FED. Without this, disabling a
    # chain left the operator's Alert downstream with nothing wired in, which
    # is a compile ERROR -- so turning one rule off stopped the whole canvas
    # from applying. A node whose every input is off is not misconfigured, it
    # is off. Fixpoint, because a chain settles one link at a time.
    if dead:
        srcs = {}
        for e in graph["edges"]:
            srcs.setdefault(e["target"], []).append(e["source"])
        for _ in range(len(graph["nodes"]) + 1):
            grew = False
            for target, sources in srcs.items():
                if target in dead:
                    continue
                if sources and all(s in dead for s in sources):
                    dead.add(target)
                    grew = True
            if not grew:
                break
    # Canvas furniture never compiles: a Comment explains the graph and a
    # Debug probe watches it, and neither is part of what runs.
    live = [n for n in graph["nodes"]
            if REGISTRY[n["type"]]["category"] != "common" and n["id"] not in dead]
    nodes = {n["id"]: n for n in live}
    edges = [e for e in graph["edges"]
             if e["source"] in nodes and e["target"] in nodes
             and not _is_flow_edge(e)]
    graph = {**graph, "nodes": live, "edges": edges}
    errors, warnings = [], []
    warnings += [{"node": nid, "message": msg}
                 for nid, msg in sorted(point_off.items())]

    #: Nodes with NOTHING wired into them, filled once `preds` exists below.
    #: A node in here cannot produce a compile ERROR -- see err().
    drafts = set()
    #: Nodes the compiler is NOT running, and everything hanging off them. A
    #: condition or output whose only input is off is off too -- not a mistake
    #: to report, simply not running, and the warning on the node that started
    #: it already says why.
    off_nodes = set()

    def err(nid, msg):
        # A node nobody has wired anything into is a DRAFT, not a mistake.
        # Dragging a Snapshot out and wiring it a minute later is how this page
        # is used, and every one of those minutes used to paint the canvas with
        # "compile errors" over a node whose only fault was being new.
        #
        # So the complaint is kept -- it is still true, and still the thing to
        # fix -- but delivered as the warning that dims the node instead of the
        # error that reddens it. The moment something IS wired in, the same
        # message is an error again, because wiring something up wrong is a
        # real mistake and that is when the compiler should say so.
        #
        # Done HERE rather than by skipping draft nodes wholesale, because
        # plenty of them compile perfectly well with no input: a Data Table on
        # a timer, a card bound to a stat key by hand. Those raise no error, so
        # this never touches them.
        if nid in drafts:
            warnings.append({"node": nid,
                             "message": f"{msg} - switched off until then"})
            off_nodes.add(nid)
            return
        errors.append({"node": nid, "message": msg})

    def warn(nid, msg):
        warnings.append({"node": nid, "message": msg})

    # A Counter wired to a Zone AND a Line: refused before anything else, in
    # the words the canvas uses, rather than as the generic "needs exactly one
    # Zone input" the zone rule would say a few steps later.
    for nid, msg in sorted(counter_problems.items()):
        if nid in nodes:
            err(nid, msg)
    # Edge sanity + cycles first: a graph with a bad wire compiles nothing.
    for e in edges:
        ok, reason = edge_allowed(nodes[e["source"]]["type"],
                                  nodes[e["target"]]["type"],
                                  str(e.get("targetHandle") or ""))
        if not ok:
            err(e["target"], reason)
    # A Trigger's duplicate guard reads BACK the table it writes (Trigger ->
    # Data Table -> Current OID): a poll of stored rows, not a value flowing
    # within a frame, so that loop is not a cycle.
    if _has_cycle(graph["nodes"], [
            e for e in edges
            if str(e.get("targetHandle") or "") not in TRIGGER_GUARD_PORTS]):
        err("", "the graph has a cycle - detections flow one way")
    # Stale class names, checked for EVERY node that names one -- a Class
    # Filter included, since a filter on a class the model dropped silences
    # everything downstream of it.
    for n in graph["nodes"]:
        _check_classes(n, REGISTRY[n["type"]], n.get("data") or {},
                       known, err, warn, active)
    if errors:
        return {"functions": [], "outputs": [], "widgets": [], "catalog": [],
                "frames_free": {"functions": [], "outputs": [],
                                "version": LOGIC_VERSION},
                "waits_for_detection": [],
                "errors": errors, "warnings": warnings}

    preds = {}                                   # node id -> [(edge, src node)]
    succs = {}                                   # node id -> [(edge, dst node)]
    for e in edges:
        preds.setdefault(e["target"], []).append((e, nodes[e["source"]]))
        succs.setdefault(e["source"], []).append((e, nodes[e["target"]]))

    # The PER-OBJECT ports -- `OID` (which object) and `timer` (how long THAT
    # object has been inside) -- have no stat key, and cannot have one: a stat
    # is one value per system and these are one value per object. Only a Data
    # Table column can hold them, which is why they exist.
    #
    # Everywhere else, input_alias resolves a wire by its SOURCE NODE and never
    # looks at which port it left by -- so an OID dragged onto a card compiled
    # to that node's number instead. A Zone Counter's id port read as its
    # count; a Cycle Time's read as a duration. Silent, and exactly wrong: the
    # card showed a plausible figure that answered a different question.
    for e in edges:
        handle = str(e.get("sourceHandle") or "")
        if handle not in ("OID", "timer"):
            continue
        tgt = nodes[e["target"]]
        if tgt["type"] == "output.dataset" \
                and str(e.get("targetHandle") or "").startswith("c_"):
            continue
        # ...and a Trigger's own OID port, the one other place a "which
        # object" wire means something: it is how a Trigger set to re-raise
        # per NEW object learns who is new (see the Trigger compile).
        if handle == "OID" and tgt["type"] == "output.trigger" \
                and str(e.get("targetHandle") or "") == "oid":
            continue
        src = nodes[e["source"]]
        what = ("names WHICH object" if handle == "OID"
                else "times each object separately")
        err(e["target"],
            f"the {handle} output of {(src.get('data') or {}).get('name') or 'this rule'} "
            f"{what}, so it can only be recorded a row at a time - drag it "
            f"onto a Data Table column instead. {REGISTRY[tgt['type']]['label']} "
            "reads one value for the whole system, and would show the rule's "
            "number rather than the object. To react to it, wire a value the "
            "rule publishes for the whole system: this node's own count port, "
            "or a Cycle Time rule's longest stay")
    # Switch branch stems, settled before ANYTHING compiles: a Formula wired
    # to a branch names that branch's key, and the formula loop runs long
    # before the switch loop does.
    sw_bases = switch_bases(graph)

    #: Set Variable nodes that have already produced the function for their
    #: variable, by variable id. A second setter of the same variable is
    #: folded into that function rather than refused -- see the compile.
    setters = {}

    def _set_target_fid(node):
        """The variable a setter writes: the Variable wired into input a.

        A Set Variable node ALWAYS writes the variable wired into it -- that
        is its whole meaning, and the node had a "publish as a new value
        named after this node" default that meant the variable it was
        pointed at was never filled: fn_out_value read 0 for ever while the
        canvas showed a Set Variable wired straight into `out`. The older
        Formula node's "set to" operation keeps its choice.
        """
        data = node.get("data") or {}
        if node.get("type") == "math.setvar":
            pass
        elif node.get("type") != "analytics.formula" \
                or str(data.get("op") or "") != "set" \
                or str(data.get("output_mode") or "self") != "variable":
            return None
        for edge, src in preds.get(node["id"], ()) or ():
            if str(edge.get("targetHandle") or "a") != "a":
                continue
            if src["type"] == "variable.ref":
                key = str((src.get("data") or {}).get("key") or "")
                if key.startswith("fn_") and key.endswith("_value"):
                    return _slug(key[3:-6])
            if src["type"] == "variable.number":
                return _slug((src.get("data") or {}).get("fnId"))
        return None

    # Stamped BEFORE the store loop below. A Variable that stores a Set
    # Variable's output copies the key its wire resolves to, and that key
    # must already be the variable the setter publishes as -- the store
    # used to copy fn_set_variable_xxxx_value, which nobody publishes, and
    # read 0 for ever beside a setter that was working.
    for n in graph["nodes"]:
        claimed = _set_target_fid(n)
        if claimed:
            n["data"] = {**(n["data"] or {}), "_publish": claimed}
    trigger_out.stamp_out_keys(graph, preds, sw_bases, _slug, branch_alias)

    # A Variable fed by a WIRE is a STORE: the wired output lands in the
    # variable whose name this node carries. Its key is filled from the rule
    # wired in and the node is marked, so the compile loop below turns it
    # into a published variable rather than a silent alias. The wire wins
    # over a stale picked key -- what is drawn is what is meant.
    for n in graph["nodes"]:
        if n["type"] != "variable.ref":
            continue
        # An explicit "in" means READ ONLY, however the canvas is wired: the
        # operator has said what this node is for, and a stray wire must not
        # silently turn their reader into a store. "auto" keeps the original
        # inference (wired = store) so existing canvases are unchanged.
        if str((n["data"] or {}).get("io") or "auto").lower() == "in":
            continue
        # SEVERAL WRITERS INTO ONE VARIABLE, which is the shape an operator
        # draws for "one card, three possible words". Each writer is gated by
        # its own branch, so at most one of them has anything to say on any
        # given frame, and the variable holds whichever spoke last -- exactly
        # `if A: x = 1` / `elif B: x = 2` , which is how several Set Variables
        # SHARING one variable already compile (see the fold further down).
        #
        # Until 2026-09-24 this loop took the first wire that resolved and
        # dropped the rest in silence. Reported from a live board: three Set
        # Variables -- "Chair Detected", "People Detected", "No Object
        # Detected" -- funnelled into one Variable feeding a card, and the
        # card read "Chair Detected" for ever with an empty frame, because
        # only the chair setter's wire survived and a setter keeps its old
        # value when its branch is not holding. Nothing said a wire had been
        # ignored.
        #
        # Writers are collected in NODE order, not edge order, so the chain
        # reads top to bottom like the statements it stands for and does not
        # change meaning when a wire is redrawn.
        rank = {nid: i for i, nid in enumerate(n2["id"] for n2 in graph["nodes"])}
        writers = []
        for _edge, src in sorted(preds.get(n["id"], ()) or (),
                                 key=lambda p: rank.get(p[1]["id"], 0)):
            if src["type"] == "variable.ref":
                continue                 # holder-of-holder resolves to nothing
            # A Query is a store with no key to copy: it publishes nothing for
            # the variable to alias, because the number does not exist until
            # the statement runs. Marked as wired anyway -- it IS a store --
            # and the compile builds a poller for it instead of an alias.
            if src["type"] == "storage.query":
                n["data"] = {**_adopt_name(n["data"] or {}), "key": "",
                             "_wired": True}
                writers = []
                break
            alias = input_alias(src, _edge, sw_bases)
            if not alias:
                continue
            # What gates this writer: the condition on its own run port. A
            # writer with none writes every frame, so it wins outright and
            # anything collected before it could never be read -- the chain
            # builder below restarts on one, which is what makes an ungated
            # writer behave the way an ungated statement does.
            gate = ""
            for w_edge, w_src in preds.get(src["id"], ()) or ():
                if str(w_edge.get("targetHandle") or "") != "when":
                    continue
                g = input_alias(w_src, w_edge, sw_bases, preds)
                if g:
                    gate = g[1]
                    break
            writers.append({"key": alias[1], "gate": gate, "src": src["id"]})
        if writers:
            n["data"] = {**_adopt_name(n["data"] or {}), "key": writers[0]["key"],
                         "_wired": True, "_writers": writers}

    zone_by_id = {z["id"]: z for z in (zones or [])}

    def upstream_classes(node_id):
        """Union of classes from every filter.class on any path back from this
        node to the source. Empty set + reached-source = 'all classes'.

        Called with the RULE, never with the Zone wired into it. It used to be
        given the zone, which worked only while a zone sat in series -- Class
        Filter into Zone into rule, so walking back from the Zone passed
        through the filter. A Zone is standalone now and the filter arrives at
        the rule's own input, so a walk starting at the zone begins at a node
        with nothing behind it: it finds no filter, and every rule reports "no
        Class Filter upstream -- every detected class counts" over a filter
        that is wired and plainly visible.

        From the rule it is right for both shapes: parallel finds the filter
        directly, series still finds it by passing through the zone on the
        way.
        """
        classes, saw_filter, stack, visited = set(), False, [node_id], set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            for _, src in preds.get(nid, ()):
                if src["type"] == "filter.class":
                    saw_filter = True
                    classes.update(str(c) for c in (src["data"].get("classes") or []))
                stack.append(src["id"])
        return classes, saw_filter

    # Stamp each object watch with the classes it watches (its own list plus
    # every Class Filter upstream), BEFORE any wire resolves: node_metrics
    # reads the stamp, which is what makes the per-class output ports real.
    for _n in graph["nodes"]:
        if _n["type"] in OBJECT_WATCHES:
            _own = {str(c).strip() for c in ((_n.get("data") or {}).get("classes")
                                             or ()) if str(c).strip()}
            _up, _ = upstream_classes(_n["id"])
            _all = sorted(_own | set(_up or ()))
            if _all:
                _n["data"] = {**(_n.get("data") or {}), "_classes": _all}

    def upstream_model(node_id):
        """The one model the sources on any path into this node are scoped to.
        Empty when no source names a model, or when two sources name DIFFERENT
        models (then the function must accept both -- any-model)."""
        models, stack, visited = set(), [node_id], set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            node = nodes.get(nid)
            if node and node["type"] == "source.detection":
                m = str((node["data"] or {}).get("model") or "").strip()
                if m:
                    models.add(m)
            for _, src in preds.get(nid, ()):
                stack.append(src["id"])
        return models.pop() if len(models) == 1 else ""

    def upstream_camera(node_id):
        """The one camera the sources on any path into this node are scoped to.

        Exactly upstream_model's shape, for the other thing a Detection Stream
        names. Empty when no source names a camera, or when two sources name
        DIFFERENT ones -- there is no single answer then, so the engine's
        default (the system's first camera) stands rather than a guess."""
        cams, stack, visited = set(), [node_id], set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            node = nodes.get(nid)
            if node and node["type"] == "source.detection":
                c = str((node["data"] or {}).get("camera") or "").strip()
                if c:
                    cams.add(c)
            for _, src in preds.get(nid, ()):
                stack.append(src["id"])
        return cams.pop() if len(cams) == 1 else ""

    def fed_by_off_node(node):
        return any(src["id"] in off_nodes for _, src in preds.get(node["id"], ()))

    #: Categories that legitimately start a chain and so need nothing wired in:
    #: the stream itself, a zone, a class filter, a variable that makes its own
    #: value, a comment, a link.
    _STARTS_A_CHAIN = ("source", "zone", "filter", "variable", "note", "common")

    # Which nodes are DRAFTS: nothing wired in, and not the kind that starts a
    # chain. err() turns their complaints into warnings (see there); nothing
    # else changes, so a node that compiles fine with no input -- a Data Table
    # on a timer, a card bound to a stat key by hand -- goes on compiling.
    for _n in graph["nodes"]:
        _spec = REGISTRY.get(_n["type"]) or {}
        if _spec.get("category") in _STARTS_A_CHAIN:
            continue
        if _spec.get("fn_type") in STANDALONE_FN_TYPES:
            continue                    # a constant, a formula, an API reading
        # A CARD is bound by a field, not only by a wire: it can name a stat
        # key or a table by hand and work perfectly with nothing wired to it.
        # "You named a table that does not exist" is a real mistake on a card
        # nobody has wired, so cards keep their errors.
        if _spec.get("category") == "dashboard":
            continue
        if not preds.get(_n["id"]):
            drafts.add(_n["id"])

    def has_detection_source(node_id):
        """Is there a Detection Stream anywhere upstream of this node?

        Same walk as upstream_model/upstream_camera -- through Zone nodes,
        Class Filters, anything -- because what matters is only whether frames
        reach this rule at all.
        """
        stack, visited = [node_id], set()
        while stack:
            nid = stack.pop()
            if nid in visited:
                continue
            visited.add(nid)
            node = nodes.get(nid)
            if node and node["type"] == "source.detection":
                return True
            for _, src in preds.get(nid, ()):
                stack.append(src["id"])
        return False

    def zone_inputs(node, handle=None):
        """The zone.ref predecessors of an analytics node (optionally one
        target handle). Returns [(zone_node, zone_dict_or_None)]."""
        out = []
        for e, src in preds.get(node["id"], ()):
            if src["type"] != "zone.ref":
                continue
            # A zone may arrive on the node's dedicated `zone` port or, on
            # every graph saved before that port existed, in series on `in`.
            # Both are the same statement -- "this rule applies inside that
            # area" -- so both answer here. When a specific handle is asked
            # for (the two-line and two-end rules, which need to know WHICH
            # end a zone belongs to), a wire on `zone` still answers, because
            # a node with one zone port cannot be ambiguous about it.
            got = e.get("targetHandle") or "in"
            if handle and got != handle and got != "zone":
                continue
            out.append((src, zone_by_id.get(str(src["data"].get("zone") or ""))))
        return out

    # -- analytics nodes -> function documents -------------------------------
    functions, fn_meta = [], {}                  # node id -> fn doc
    di_nodes = {}                                # DI port -> node name
    catalog = []                                 # ready-made widgets to drag in
    seen_fids = set()
    # A constant name holds ONE value: several constant nodes with the same
    # name are the same constant drawn in several places. The first one
    # compiles; the others are copies of it, never a second definition.
    const_fids = {}
    # Variables a wired STORE writes into. A Create Variable placeholder with the same
    # name is a PLACEHOLDER -- made in the Variables panel, name first, value
    # later -- and the store claiming it is the value arriving. Without this,
    # "create the variable, then wire the output into it" (the natural order)
    # tripped over its own placeholder as a duplicate id.
    store_fids = set()

    for n in graph["nodes"]:
        if n["type"] == "variable.ref" and (n["data"] or {}).get("_wired"):
            fid = _slug((n["data"] or {}).get("name"))
            if fid:
                store_fids.add(fid)
        # A set-to that publishes AS the variable owns that id the same way a
        # wired store does: the constant that declared the variable is the
        # placeholder, and this is the value arriving.
        claimed = _set_target_fid(n)
        if claimed:
            store_fids.add(claimed)
        if n["type"] in MATH_NODES and (MATH_NODES[n["type"]].get("value_types")):
            n["data"] = {**(n["data"] or {}),
                         "_kind": setvar_kind(n, preds.get(n["id"]))}
    for n in graph["nodes"]:
        spec = REGISTRY[n["type"]]
        if spec["category"] not in ("analytics", "variable"):
            continue
        if n["type"] == "variable.ref":
            d = n["data"] or {}
            # WIRED: an output was wired in, so this node STORES it -- the
            # value is published again under the variable's own name, which
            # is what "pass the output into the variable" means. The name is
            # therefore required: an unnamed store has no key to store under.
            if d.get("_wired"):
                fid = _slug(d.get("name"))
                # The classic accumulator trap: a formula that READS this very
                # variable feeding the store that WRITES it. That re-evaluates
                # thirty times a second -- "total = now + total" adds ~30 per
                # second of violation, which reads as hallucination. Warn with
                # the fix by name; the graph still compiles, because a slower
                # deliberate feedback (a smoothed value) is legitimate.
                own_key = f"fn_{fid}_value" if fid else ""
                if own_key and _feeds_itself(n["id"], own_key, preds):
                    warn(n["id"],
                         f"this stores a formula that reads {own_key} back - "
                         "it re-adds every frame (~30x a second), not once "
                         "per event. For a total, use a Running Total node "
                         "instead")
                # "variable" is the node's own default label, not a choice --
                # older saves carry it, and silently creating a variable
                # called `variable` is how nobody finds their number again.
                if not fid or fid == "variable":
                    err(n["id"], "Variable: choose which variable stores "
                                 "the wired output")
                    continue
                if fid in seen_fids:
                    err(n["id"], f"duplicate variable name {fid!r} - two "
                                 "nodes cannot store into the same variable")
                    continue
                # A QUERY wired in is a different kind of store. Every other
                # source publishes a stat the engine already has, so the
                # variable is an expr that copies it. A Query publishes
                # nothing: it is SQL, and the number does not exist until
                # somebody runs it. Nor can the detection app run it -- it has
                # no database at all (it emits events; core writes them) -- so
                # the variable compiles into a poller that asks THIS box's
                # master, which does have the database, on an interval.
                q_src = next((s for _e, s in preds.get(n["id"], ()) or ()
                              if s["type"] == "storage.query"), None)
                if q_src is not None:
                    doc = _query_value_doc(fid, d, q_src, preds, tables, err)
                    if doc is None:
                        continue
                    seen_fids.add(fid)
                    functions.append(doc)
                    fn_meta[n["id"]] = doc
                    continue
                # One writer is a copy of that writer's key, which is what a
                # store has always been. SEVERAL are the branch chain: the
                # last one whose gate holds is the value, and when none of
                # them holds the variable keeps what it has. Built here rather
                # than where the writers were collected because a writer's
                # text-ness (_kind) is stamped by the loop just above this
                # one, and an unset TEXT variable has to read as empty text
                # rather than as the 0 an unknown name is.
                writers = d.get("_writers") or []
                if len(writers) > 1:
                    wordy = any((nodes[w["src"]].get("data") or {}).get("_kind")
                                == "text" for w in writers if w["src"] in nodes)
                    expr = f"fn_{fid}_value"
                    if wordy:
                        expr = f"text({expr})"
                    # TRIGGER DRIVEN, not recomputed from scratch: whichever
                    # writer is FIRING supplies the value, and when none of
                    # them is the variable keeps what it was last given. That
                    # is what makes a word put up by a one-frame event stay on
                    # the card afterwards instead of blinking once.
                    #
                    # Order is only the tie-break for the one frame where two
                    # writers fire at once, and there the LAST one wins. Not
                    # an arbitrary pick: several Set Variables sharing a
                    # variable already fold that way (see the fold further
                    # down, `if A: x = 1` then `if B: x = 2`), and an operator
                    # can express this logic either by sharing one variable or
                    # by funnelling into one. The two shapes have to agree, or
                    # the same picture drawn two ways answers differently.
                    for w in writers:
                        # An ungated writer fires every frame, so nothing
                        # ABOVE it can ever be the latest: it replaces the
                        # chain built so far rather than wrapping it.
                        expr = (f"iff(({w['gate']} != 0), ({w['key']}), {expr})"
                                if w["gate"] else w["key"])
                else:
                    expr = str(d.get("key") or "")
                doc = {"id": fid, "name": str(d.get("name") or fid),
                       "type": "expr", "enabled": True,
                       "params": {"expr": expr}}
                try:
                    doc = functions_service.validate_function(doc)
                except (ValueError, TypeError) as exc:
                    err(n["id"], str(exc))
                    continue
                seen_fids.add(fid)
                functions.append(doc)
                fn_meta[n["id"]] = doc
                continue
            # UNWIRED: a holder, not a function -- it names a value that
            # already exists, and compiling one would publish a second copy
            # of it. Its only job happens where it is wired (formula_inputs).
            if not str(d.get("key") or "").strip():
                err(n["id"], "Variable: choose which value this holds, or "
                             "wire an output into it")
            continue
        d = n["data"] or {}
        fid = _slug(d.get("fnId"))
        if not fid:
            err(n["id"], "analytics node has no function id (save again)")
            continue
        # A placeholder claimed by a wired STORE steps aside before the id
        # book-keeping -- consuming the id here and skipping later is how the
        # claim read as "duplicate variable name" against the very store
        # doing the claiming.
        if n["type"] == "variable.number" and fid in store_fids:
            continue
        const_def = (str(d.get("value_type") or "number"), str(d.get("value", 0)))
        if n["type"] == "variable.number" and const_fids.get(fid) == const_def:
            continue                    # the same constant, drawn again
        if fid in seen_fids:
            err(n["id"], f"duplicate function id {fid!r} - rename one of the nodes")
            continue
        seen_fids.add(fid)
        if n["type"] == "variable.number":
            const_fids[fid] = const_def

        # A text constant publishes no numeric stat -- see the long comment on
        # variable.number above. Its id is still claimed above (a text
        # constant and a number constant must not share a name either), but
        # nothing is built from it: no expr, no function doc, no trigger
        # machinery. Read directly off the canvas by whatever picks it, not
        # through the stats dict.
        if n["type"] == "variable.number" and d.get("value_type") == "text":
            continue

        params = {}
        fn_type = node_fn_type(n, spec)
        # ZONE-OPTIONAL nodes read WHERE they measure off the wiring rather
        # than off a setting: a Counter with a zone on its zone port counts
        # that area, one with nothing there counts the frame. Same question,
        # same settings, same published metrics -- only the rule underneath
        # differs, and it is picked here so the two are one node on the canvas.
        if spec.get("zone_optional") and zone_inputs(n):
            fn_type = ZONE_RULE_OF.get(fn_type, fn_type)

        # Nothing that WATCHES runs until it is wired to a Detection Stream.
        # A node dropped on the canvas and left unconnected is a draft, not a
        # rule: compiling it would start counting, alerting and writing history
        # for something the operator has not finished building. Skipped rather
        # than errored -- an unfinished node is not a mistake, and the rest of
        # the graph must still compile and run.
        #
        # Standalone kinds are exempt by definition: a constant, a formula over
        # other stats and an API reading have no detections to consume.
        if fn_type not in STANDALONE_FN_TYPES and not has_detection_source(n["id"]):
            warn(n["id"], f"{spec['label']} is not connected to a Detection "
                          "Stream, so it is switched off - wire one in to "
                          "start it")
            off_nodes.add(n["id"])
            continue

        if fn_type in ("zone_count", "zone_dwell", "zone_unique",
                       "zone_countdown", "zone_occupancy"):
            zin = zone_inputs(n)
            # A CLOCK does not need a place: "how long has this been on
            # camera" and "how long did each arrival get" are the same
            # measurements over the whole view, and the rule reads them the
            # same way with no zone to be inside of. The Occupied clock is the
            # exception it sounds like -- "the frame is occupied" is true
            # whenever the camera is on -- so that mode asks for a zone
            # instead of measuring something meaningless.
            zone_free = (not zin and spec.get("zone_optional")
                         and fn_type in ("zone_dwell", "zone_countdown"))
            if not zone_free:
                if len(zin) != 1:
                    if not zin and fn_type == "zone_occupancy":
                        warn(n["id"],
                             f"{spec['label']}: the Occupied clock needs a "
                             "Zone - wire one in, or switch it to Time "
                             "present, which works on the whole view too")
                        off_nodes.add(n["id"])
                        continue
                    err(n["id"], f"{spec['label']} needs exactly one Zone input "
                                 f"(has {len(zin)})")
                    continue
                znode, z = zin[0]
                if z is None:
                    # UNBOUND, not broken. This is the normal state of a
                    # template that just landed -- _unbind_places strips the
                    # site out on purpose -- and an ERROR here stopped
                    # apply_artifacts for the whole canvas, so one waiting zone
                    # kept every zone-free chain (frame counters, datasets,
                    # their cards) from applying too. The chain is switched off
                    # instead, exactly like a chain whose zone was disabled,
                    # and wakes when a zone is picked.
                    warn(znode["id"], "waiting for a zone - pick one on this "
                                      "Zone node; the chain is switched off "
                                      "until then")
                    off_nodes.add(n["id"])
                    continue
                # A line is accepted silently: the engine reads it as a thin
                # place ("at the line" -- a narrow band around it), which is a
                # feature, and a WARNING for using a feature reads as a fault.
                # The inspector's own text explains the semantics.
                if not z.get("enabled", True):
                    warn(n["id"],
                         f"zone '{z['name']}' is disabled - this will read 0")
                params["zone"] = z["id"]
            # ...and a node with no `delivery` key at all is one saved before
            # the choice existed, where ticking OID alone stamped the stays.
            # That is what its Data Table is reading, so absent means "once".
            # New nodes carry "live" explicitly (Standing Rule 7).
            # TOLERANCE: how long past a limit tested on this clock still counts
            # as fine. Written on the rule; the If / Else if on live_s is
            # shifted by it below, so the alert and the photo wait for
            # limit + tolerance while the photo still reports the limit.
            if fn_type == "zone_dwell":
                try:
                    tol = max(0.0, min(3600.0, float(d.get("tolerance_s") or 0)))
                except (TypeError, ValueError):
                    tol = 0.0
                if tol:
                    params["tolerance_s"] = tol
            if fn_type == "zone_dwell" and (
                    (d.get("OID")
                     and str(d.get("delivery") or "once") == "once")
                    or node_mode(n, spec) == "stamps"):
                # OID ticked on Cycle Time (or the OID Measure Time mode,
                # which IS that record): stamp each completed stay as an
                # event (OID + duration + entry/exit). That event is what a
                # Data Table row per object is written from -- see the
                # dataset arming below, which fires on these when no Alert
                # threshold is set.
                params["stamp_events"] = True
            if fn_type == "zone_countdown":
                # The mode's own settings ride on the node.
                try:
                    params["start_s"] = max(1.0, min(604800.0,
                                                     float(d.get("start_s") or 60)))
                except (TypeError, ValueError):
                    params["start_s"] = 60.0
                try:
                    params["deduct_s"] = max(0.0, min(86400.0,
                                                      float(d.get("deduct_s", 1))))
                except (TypeError, ValueError):
                    params["deduct_s"] = 1.0
                if str(d.get("reset") or "") == "never":
                    params["reset"] = "never"
            # min_present belongs to the DISTINCT rule (zone_unique): "count
            # an entrant only past this occupancy". The realtime mode compiles
            # to zone_count, which has no such knob -- handing it the param
            # anyway would fail that rule's validation.
            if (n["type"] == "analytics.violations"
                    and node_fn_type(n, spec) == "zone_unique"):
                try:
                    params["min_present"] = max(0, int(d.get("limit", 1)))
                except (TypeError, ValueError):
                    params["min_present"] = 1
            _count_timing(n, d, params, warn)
            classes, saw_filter = upstream_classes(n["id"])
            if classes:
                params["classes"] = sorted(classes)
            elif not saw_filter:
                warn(n["id"], "no Class Filter upstream - every detected class counts")

        elif fn_type == "object_detect":
            # One rule, two nodes: with a Zone wired in it watches that zone,
            # with none it watches the whole frame. The zone is read from the
            # WIRING rather than a setting, so the node on the canvas shows
            # which it is.
            # A watch may be told WHEN an object counts as present, the same
            # two settings the counters have: "must stay this long first" and
            # "keep counting for this long after it is gone". Both 0 -- the
            # default -- is real-time presence, needs no tracker, and is
            # exactly what every watch did before the setting existed.
            _count_timing(n, d, params, warn)
            if n["type"] == "analytics.anomaly":
                params["anomaly"] = True
            zin = zone_inputs(n)
            if n["type"] == "analytics.object_zone" or zin:
                if len(zin) != 1:
                    err(n["id"], f"{spec['label']} needs exactly one Zone "
                                 f"input (has {len(zin)})")
                    continue
                znode, z = zin[0]
                if z is None:
                    warn(znode["id"], "waiting for a zone - pick one on this "
                                      "Zone node; the chain is switched off "
                                      "until then")
                    off_nodes.add(n["id"])
                    continue
                if not z.get("enabled", True):
                    warn(n["id"], f"zone '{z['name']}' is disabled - "
                                  "this will read 0")
                params["zone"] = z["id"]
            # The class walk starts at the RULE, always -- never at the Zone.
            #
            # It used to start at the Zone, which worked only while a zone sat
            # in SERIES: Class Filter into Zone into rule, so walking back from
            # the Zone reached the filter through it. A Zone is standalone now
            # and the filter arrives at the rule's own input, so that walk
            # started at a node with nothing behind it and reported "needs a
            # Class Filter" over a filter that was plainly wired in.
            #
            # From the rule it is right for both shapes: parallel finds the
            # filter directly, and series still finds it by passing through
            # the Zone on the way.
            upstream_of = n["id"]
            # WHAT to watch for is named by the Class Filters wired in --
            # several may feed one watch and their classes add together, which
            # is how "a person OR a forklift" is one node. Required, not
            # defaulted: "anything at all is here" is a Frame Counter, and a
            # watch that silently means it would alert on every passing pallet.
            # (`data.classes` is still honoured for nodes minted while the
            # picker briefly lived on the node itself.)
            chosen = {str(c).strip() for c in (d.get("classes") or ())
                      if str(c).strip()}
            classes, _saw_filter = upstream_classes(upstream_of)
            both = chosen | set(classes or ())
            if both:
                params["classes"] = sorted(both)
            else:
                err(n["id"], f"{spec['label']} needs a Class Filter - wire "
                             "one in and pick the objects to watch for (more "
                             "than one filter is fine; they add together)")
                continue

        elif fn_type in ("frame_count", "frame_unique"):
            # Whole-frame counters take the stream (through a Class Filter if
            # the operator wants one) and nothing else -- a Zone wired in
            # would be silently ignored, so say so instead.
            if zone_inputs(n):
                err(n["id"], f"{spec['label']} counts the WHOLE frame - "
                             "remove the Zone input, or use a zone counter "
                             "instead")
                continue
            _count_timing(n, d, params, warn)
            classes, saw_filter = upstream_classes(n["id"])
            if classes:
                params["classes"] = sorted(classes)
            elif not saw_filter:
                warn(n["id"], "no Class Filter upstream - every detected class counts")

        elif fn_type == "line_count":
            zin = zone_inputs(n)
            if len(zin) != 1:
                err(n["id"], f"{spec['label']} needs exactly one Zone (line) input")
                continue
            znode, z = zin[0]
            if z is None:
                warn(znode["id"], "waiting for a line - pick one on this "
                                  "Zone node; the chain is switched off "
                                  "until then")
                off_nodes.add(n["id"])
                continue
            if z["kind"] != "line":
                err(n["id"], f"{spec['label']} needs a line - '{z['name']}' is an area")
                continue
            if not z.get("enabled", True):
                warn(n["id"], f"line '{z['name']}' is disabled - this will read 0")
            params["line"] = z["id"]
            # Count each object once per direction, by object id. The
            # crossings themselves are unchanged -- the events still name
            # every one of them -- this decides what the TALLY counts.
            if d.get("distinct"):
                params["distinct"] = True
            # The count of the moment instead of the running total.
            if d.get("momentary"):
                params["momentary"] = True
                if d.get("totals"):
                    params["totals"] = True
            classes, saw_filter = upstream_classes(n["id"])
            if classes:
                params["classes"] = sorted(classes)
            elif not saw_filter:
                warn(n["id"], "no Class Filter upstream - every detected class counts")

        elif fn_type == "line_travel":
            got, all_classes, any_filter = {}, set(), False
            for handle in ("line_a", "line_b"):
                zin = zone_inputs(n, handle)
                if len(zin) != 1:
                    err(n["id"], f"{spec['label']} needs one line on its "
                                 f"'{handle}' input")
                    break
                znode, z = zin[0]
                if z is None:
                    # Unbound, as on every other rule: a deleted line (or a
                    # template not yet bound) switches this chain off with a
                    # warning rather than stopping the whole canvas applying.
                    warn(znode["id"], "waiting for a line - pick one on this "
                                      "Zone node; the chain is switched off "
                                      "until then")
                    off_nodes.add(n["id"])
                    break
                if z["kind"] != "line":
                    err(n["id"], f"'{handle}' must be a line zone")
                    break
                got[handle] = z["id"]
                classes, saw = upstream_classes(n["id"])
                all_classes |= classes
                any_filter |= saw
            if len(got) != 2:
                continue
            if got["line_a"] == got["line_b"]:
                err(n["id"], "line A and line B must be different lines")
                continue
            params.update({"line_a": got["line_a"], "line_b": got["line_b"],
                           "speed_unit": d.get("speed_unit") or "kmh"})
            # SPEED needs a real-world distance between the two lines, and
            # nothing on the box can work that out -- so it is asked for, and
            # only when speed is switched on. A node saved before the switch
            # existed carries a distance and no `speed` key, and was
            # publishing speeds from it: absent means on, or the numbers on
            # its cards would go to zero on upgrade.
            speed_on = str(d.get("speed") or "on").lower() not in ("off", "0",
                                                                   "false", "")
            params["distance_m"] = (d.get("distance_m") or 0) if speed_on else 0
            if all_classes:
                params["classes"] = sorted(all_classes)
            elif not any_filter:
                warn(n["id"], "no Class Filter upstream - every detected class counts")

        elif fn_type == "zone_travel":
            # Same two-ended shape as line_travel, but EITHER end may be an
            # area or a line -- the kind is read off the zone the operator
            # wired in rather than fixed by the node, and travels to the engine
            # as start_kind/end_kind so it knows which test to run per end.
            got, all_classes, any_filter = {}, set(), False
            for handle in ("start", "end"):
                zin = zone_inputs(n, handle)
                if len(zin) != 1:
                    err(n["id"], f"{spec['label']} needs one Zone on its "
                                 f"'{handle}' input")
                    break
                znode, z = zin[0]
                if z is None:
                    warn(znode["id"], "waiting for a zone - pick one on this "
                                      "Zone node; the chain is switched off "
                                      "until then")
                    off_nodes.add(n["id"])
                    break
                if not z.get("enabled", True):
                    warn(n["id"], f"zone '{z['name']}' is disabled - this will read 0")
                got[handle] = (z["id"], z["kind"])
                classes, saw = upstream_classes(n["id"])
                all_classes |= classes
                any_filter |= saw
            if len(got) != 2:
                continue
            if got["start"][0] == got["end"][0]:
                err(n["id"], "start and end must be different zones")
                continue
            params.update({"start": got["start"][0], "end": got["end"][0],
                           "start_kind": got["start"][1],
                           "end_kind": got["end"][1]})
            if all_classes:
                params["classes"] = sorted(all_classes)
            elif not any_filter:
                warn(n["id"], "no Class Filter upstream - every detected class counts")

        elif fn_type == "proximity":
            # No alert params here: the node MEASURES. Wiring an Alert or When
            # node after it is how the operator says "and trigger on it" --
            # one place a condition lives, not two.
            params.update({
                "class_a": str(d.get("class_a") or "").strip(),
                "class_b": str(d.get("class_b") or "").strip(),
                "max_distance": d.get("max_distance", 0.15),
                "draw_link": d.get("draw_link") or "always",
            })
            for side in ("model_a", "model_b"):
                if str(d.get(side) or "").strip():
                    params[side] = str(d[side]).strip()
            if not params["class_a"] or not params["class_b"]:
                err(n["id"], "Proximity needs both classes - pick class A and class B")
                continue

        elif fn_type == "guard":
            params.update({
                "margin_pct": d.get("margin_pct", 50),
                "alert_classes": [str(c) for c in (d.get("alert_classes") or [])],
                "touch": d.get("touch") or "center",
                "min_overlap_pct": d.get("min_overlap_pct", 0),
                "draw": bool(d.get("draw", True)),
            })
            # The node's own class list says who gets a bubble; a Class Filter
            # upstream works too (its classes become the bubble owners).
            own = [str(c) for c in (d.get("classes") or []) if str(c).strip()]
            if not own:
                up, _saw = upstream_classes(n["id"])
                own = sorted(up)
            if own:
                params["classes"] = own
            if not params["alert_classes"]:
                warn(n["id"], "no intruder classes - the bubble is drawn but nothing counts as an intrusion")

        elif fn_type == "trails":
            # Without this branch the node's settings never reached the
            # engine: the class picker, the trail length and the idle
            # threshold all sat in the node's data while the app ran on its
            # defaults -- which is every class, so trails were drawn behind
            # objects the operator had deselected.
            params.update({
                "length_s": d.get("length_s", 10),
                "idle_s": d.get("idle_s", 5),
                "draw": bool(d.get("draw", True)),
            })
            # The node's own picker says who leaves a trail; a Class Filter
            # wired in works too, and the two add together (same rule as
            # every other watch node). Empty = every class, deliberately:
            # "trail everything" is a legitimate thing to ask for.
            own = {str(c).strip() for c in (d.get("classes") or ())
                   if str(c).strip()}
            up, _saw = upstream_classes(n["id"])
            both = own | set(up or ())
            if both:
                params["classes"] = sorted(both)

        elif fn_type == "heatmap":
            # The zone is OPTIONAL and read off the WIRING, like every other
            # zone-optional node: one wired in maps that area, none maps the
            # whole view. It cannot go through ZONE_RULE_OF because there is no
            # second rule to switch to -- the map is the same map either way,
            # only smaller -- so the zone is picked up here instead.
            zin = zone_inputs(n)
            if zin:
                znode, z = zin[0]
                if z is None:
                    warn(znode["id"], "waiting for a zone - pick one on this "
                                      "Zone node; the chain is switched off "
                                      "until then")
                    off_nodes.add(n["id"])
                    continue
                if not z.get("enabled", True):
                    warn(n["id"],
                         f"zone '{z['name']}' is disabled - this will read 0")
                params["zone"] = z["id"]
            params.update({
                "cols": d.get("cols", 12),
                "rows": d.get("rows", 8),
                "interval_s": d.get("interval_s", 1),
                "hours": d.get("hours", 0),
            })
            # No class picker on this node: a Class Filter upstream is how you
            # say "map only forklifts", and with none the map is of everything
            # the camera sees, which is what a heatmap is usually asked for.
            up, _saw = upstream_classes(n["id"])
            if up:
                params["classes"] = sorted(up)

        elif fn_type == "expr":
            if n["type"] == "analytics.block":
                try:
                    params["expr"] = blueprint_to_expr(d.get("blueprint"))
                except ValueError as exc:
                    err(n["id"], f"Function Block: {exc}")
                    continue
            elif n["type"] == "analytics.formula" or n["type"] in MATH_NODES:
                # One compile path for the Formula and for the family that
                # replaced it: a Math node IS a Formula with its operation
                # fixed by the drawer it came out of, so operands, typed
                # fallbacks, chaining and per-join operations are all the same
                # code. Splitting it would be two implementations of one
                # semantics, and they would drift.
                math_spec = MATH_NODES.get(n["type"])
                op = math_op(n) if math_spec else str(d.get("op") or "add")
                spec_op = FORMULA_OPS.get(op)
                if spec_op is None:
                    err(n["id"], f"{spec['label']}: unknown operation {op!r}")
                    continue
                # The kind of value this node stores, clamped to the ones its
                # own entry offers -- a stale `value_type` left by a retype or
                # a hand-edited graph can never make an Average store text.
                v_types = list((math_spec or {}).get("value_types") or ())
                v_type = setvar_kind(n, preds.get(n["id"])) if v_types else ""
                # TEXT is a value like any other now: the engine carries a
                # word (functions.py eval_expr), so a text Set Variable
                # compiles to the same iff(gate, "in", keep) rule a number
                # does, the variable holds the word, and whatever reads the
                # variable -- a Data Table column, a card, a Compare against
                # a text constant -- reads the word. (It used to compile to
                # nothing and could only land in a table column as a fixed
                # string, so a status variable read 0 on the canvas for
                # ever and could not drive anything.)
                # A chainable operation folds over as many inputs as asked
                # for; everything else keeps its fixed arity. A Math node
                # chains only where its own entry says so -- Clamp taking
                # eight inputs is not a thing, however chainable `clamp`
                # looks from the operations table.
                arity = spec_op["arity"]
                chains = (math_spec.get("arity") is None
                          and (not math_spec.get("chain_ops")
                               or op in math_spec["chain_ops"])
                          if math_spec else op in FORMULA_CHAINABLE)
                if chains:
                    try:
                        arity = max(2, min(FORMULA_MAX_INPUTS,
                                           int(d.get("inputs") or 2)))
                    except (TypeError, ValueError):
                        arity = 2
                operands, missing = [], []
                for i in range(arity):
                    handle = FORMULA_HANDLES[i]
                    # The extra chain handles (d..h) predate nothing: a node
                    # minted before them has no <h>_value key, and the UI
                    # shows 0 in that box -- so 0 is what an unwired one means.
                    fallback = d.get(f"{handle}_value")
                    if fallback is None and i >= 3:
                        fallback = 0
                    if spec_op.get("wire_only") and handle == "a":
                        # "set to"'s input a IS the variable being set: it
                        # comes from a wire by definition, so a typed
                        # leftover must not stand in. b is the value, and a
                        # typed number is a fine value.
                        fallback = None
                    # A TABLE COLUMN wired in: its aggregate becomes a query
                    # variable this input reads (see _dataset_operand).
                    from_table = _dataset_operand(n["id"], handle,
                                                  preds.get(n["id"]) or [],
                                                  d, fid, tables, err)
                    if from_table is not None:
                        q_doc, q_key = from_table
                        if q_doc is None:
                            # err() said what is wrong; a placeholder keeps
                            # this node from also complaining "input a needs
                            # a value" about the same wire.
                            operands.append("0")
                            continue
                        if q_doc["id"] not in seen_fids:
                            seen_fids.add(q_doc["id"])
                            functions.append(q_doc)
                        operands.append(q_key)
                        continue
                    text = _operand(handle, preds.get(n["id"]) or [],
                                    fallback, sw_bases, preds,
                                    as_text=(v_type == "text" and handle == "b"))
                    if text is None:
                        missing.append(handle)
                    operands.append(text)
                if missing:
                    if spec_op.get("wire_only") and "a" in missing:
                        err(n["id"],
                            "set to needs the variable it sets - wire a "
                            "Variable into input a")
                    else:
                        # Named by the PORT where a Math node has real port
                        # names -- "input Base needs a value" is the fix,
                        # where "input a" is a lookup.
                        labels = (REGISTRY[n["type"]].get("port_labels")
                                  or list(FORMULA_HANDLES))
                        words = [labels[FORMULA_HANDLES.index(h)]
                                 if FORMULA_HANDLES.index(h) < len(labels) else h
                                 for h in missing]
                        err(n["id"],
                            f"{spec['label']}: input {', '.join(words)} needs a "
                            "value - wire something in or type a number")
                    continue
                if chains and arity > 2:
                    # Each JOIN may name its own operation: node data `ops`
                    # holds the operation between the running result and
                    # input c, d, ... (the first join is the node's own op).
                    # An unknown or non-two-operand entry falls back to the
                    # main op, so a stale key can never invent arity.
                    raw = d.get("ops") if isinstance(d.get("ops"), list) else []

                    def _join_op(k):
                        cand = str(raw[k - 1]) if 0 < k <= len(raw) else ""
                        c = FORMULA_OPS.get(cand)
                        return cand if (c and c["arity"] == 2) else op

                    joins = [_join_op(k) for k in range(arity - 1)]
                    if joins == [op] * len(joins) and op in ("min", "max", "avg"):
                        # n-ary on purpose: avg folded pairwise is a
                        # different number, and the engine's calls take
                        # any number of operands anyway.
                        params["expr"] = f"{op}({', '.join(operands)})"
                    else:
                        expr = operands[0]
                        for k, extra in enumerate(operands[1:]):
                            expr = FORMULA_OPS[joins[k]]["tmpl"].format(
                                a=expr, b=extra)
                        params["expr"] = expr
                else:
                    params["expr"] = spec_op["tmpl"].format(
                        **dict(zip(FORMULA_HANDLES, operands)))
                # A WHOLE number is a property of the variable, not of the box
                # somebody typed into: a wired value rounds too, or "people in
                # the bay" would be whole when typed and 12.4 when wired.
                # Wrapped here, BEFORE the gate below, so the else-branch
                # (keep the current value) is left exactly as it was rather
                # than being rounded on every frame it does nothing.
                if v_type == "number":
                    params["expr"] = f"round({params['expr']})"
                # THE RUN PORT, in its gating face: a condition wired onto it
                # makes this "if condition { formula }" -- the formula computes
                # while the condition holds and reads 0 otherwise.
                # Two Formulas gated by different branches of one If/Else-if
                # ARE the code shape `if c {A} else if d {B}`, each active on
                # its own branch. Unwired, nothing changes.
                for w_edge, w_src in preds.get(n["id"]) or []:
                    if str(w_edge.get("targetHandle") or "") != "when":
                        continue
                    # preds so a CONDITION resolves here: it publishes a bool
                    # -- its rule's `active`, 1 while it holds -- which is the
                    # most natural thing to gate on and the one thing this
                    # port could not read. "Only when the bay is crowded" was
                    # a Switch branch or nothing.
                    alias = input_alias(w_src, w_edge, sw_bases, preds)
                    if not alias:
                        # SEQUENCE ONLY. What is wired here carries no value --
                        # a Detection Stream, a Class Filter, a zone -- so
                        # there is nothing to gate on and nothing was meant to
                        # be: the wire says WHERE this calculation sits in the
                        # flow. It used to be an error, which is why a Math
                        # node could not be placed in a pipeline at all.
                        # `continue`, not `break`: a stream wired for sequence
                        # and a condition wired to gate can both land here,
                        # and the condition must still be found.
                        continue
                    keep = operands[0] if op == "set" else "0"
                    if op == "set" and v_type == "text":
                        # A text variable nobody has set yet must read as
                        # empty text, not as the number 0 an unknown name
                        # is: text() turns 0 into "" and leaves a word alone.
                        keep = f"text({keep})"
                    params["expr"] = (f"iff(({alias[1]} != 0), "
                                      f"({params['expr']}), {keep})")
                    break
                if "expr" not in params:
                    continue
                # Output mode: publish as THIS node, or AS the variable being
                # set. As the variable, the rule takes the variable's own id
                # -- everything already bound to fn_<var>_value follows it,
                # and the else-branch (the wired input a, the same key) is
                # what makes "keep the current value" literal.
                if n["type"] == "math.setvar" \
                        or str(d.get("output_mode") or "self") == "variable":
                    target = _set_target_fid(n)
                    if not target:
                        err(n["id"], "Set Variable: wire the Variable to set "
                                     "into input a")
                        continue
                    if target in seen_fids:
                        prior = setters.get(target)
                        if prior is None:
                            err(n["id"], f"two rules both write the variable "
                                         f"{target!r} - keep one setter")
                            continue
                        # SEVERAL SETTERS OF ONE VARIABLE fold into one rule,
                        # the way `if A: x = 1` followed by `if B: x = 2`
                        # reads: the later statement wins when both hold, and
                        # when neither does the variable keeps its value. The
                        # keep-branch of this node's gate (its trailing
                        # fn_<var>_value) is replaced by everything compiled
                        # so far, so the chain nests -- iff(B, 2, iff(A, 1,
                        # x)). An UNGATED second setter would write every
                        # frame and make the first one dead: said so.
                        keep = operands[0]
                        expr = params["expr"]
                        at = expr.rfind(keep) if keep else -1
                        if not expr.startswith("iff(") or at < 0:
                            warn(n["id"], f"this sets {target!r} every frame, so "
                                          "the other Set Variable writing it "
                                          "never shows - gate it with a "
                                          "condition on its run port")
                            merged = expr
                        else:
                            merged = (expr[:at] + "(" + prior["params"]["expr"] + ")"
                                      + expr[at + len(keep):])
                        try:
                            functions_service.validate_expr(merged)
                        except (ValueError, TypeError) as exc:
                            err(n["id"], str(exc))
                            continue
                        prior["params"]["expr"] = merged
                        fn_meta[n["id"]] = prior
                        continue
                    seen_fids.add(target)
                    fid = target

            elif n["type"] == "variable.number":
                # A constant IS an expression: "4". The engine re-evaluates it
                # per tick like any formula -- zero new engine capability.
                # (A placeholder claimed by a wired store never reaches here:
                # it steps aside before the id book-keeping above.)
                if d.get("value_type") == "boolean":
                    # 1.0/0.0, not True/False: the expr AST only allows a
                    # bool or number constant (functions.py), and every
                    # reader downstream (formulas, cards, stats) already
                    # expects a float. This IS the boolean, not a stand-in
                    # for it -- there is no separate boolean type to lose.
                    params["expr"] = "1" if d.get("value") else "0"
                else:
                    try:
                        v = float(d.get("value", 0))
                    except (TypeError, ValueError):
                        v = 0.0
                    params["expr"] = repr(int(v) if v == int(v) else v)
            else:
                # A formula dropped on the canvas and left empty AND unwired is
                # a draft, like an unconnected rule above -- not a mistake. As
                # an error it refused to apply the WHOLE workflow ("empty
                # expression"), so every other node saved with it, a Variable
                # wired to an Object Detection included, never reached the app
                # and read "..." on the canvas with nothing pointing at why.
                if (n["type"] == "analytics.expr"
                        and not str(d.get("expr") or "").strip()
                        and not preds.get(n["id"]) and not succs.get(n["id"])):
                    warn(n["id"], "Custom Formula is empty and not wired, so it "
                                  "is switched off - type a formula, or delete it")
                    off_nodes.add(n["id"])
                    continue
                params["expr"] = _expr_with_inputs(str(d.get("expr") or ""),
                                                   preds.get(n["id"]) or [],
                                                   sw_bases, preds)

        elif fn_type == "custom" and n["type"] == "analytics.python":
            code = str(d.get("code") or "")
            if not code.strip():
                err(n["id"], "Python Function: write some code, or delete the node")
                continue
            try:
                compile(code, f"<{fid}>", "exec")
            except SyntaxError as exc:
                err(n["id"], f"Python Function: line {exc.lineno}: {exc.msg}")
                continue
            if len(code) > MAX_PYTHON_BYTES:
                err(n["id"], f"Python Function: at most "
                             f"{MAX_PYTHON_BYTES // 1024} KB of code")
                continue
            # The handler name IS the function id, so the file written beside
            # the app and the rule that calls it can never drift apart.
            params["handler"] = f"wf_{fid}"

        elif fn_type == "total":
            inputs, reset_in = [], []
            for e_, src in preds.get(n["id"]) or []:
                alias = input_alias(src, e_, sw_bases)
                if not alias:
                    continue
                # The Reset port is its own wire: a 0/1 answer, never the
                # value being accumulated.
                (reset_in if (e_.get("targetHandle") or "in") == "reset"
                 else inputs).append(alias)
            if len(inputs) != 1:
                err(n["id"], f"Running Total needs exactly one value wired in "
                             f"(has {len(inputs)})")
                continue
            if len(reset_in) > 1:
                err(n["id"], "Running Total takes ONE wire on Reset - combine "
                             "several with a Logic node (Or) first")
                continue
            params["key"] = inputs[0][1]
            if reset_in:
                params["reset_key"] = reset_in[0][1]
            if str(d.get("resume") or "zero") == "last":
                params["resume"] = True
            params["mode"] = "sum" if d.get("mode") == "sum" else "count"
            if d.get("reset") == "daily":
                params["reset"] = "daily"
            # A detection that blinks (person missed for a frame) makes the
            # wired gauge bounce 1 -> 0 -> 1, and every re-rise adds again.
            # The debounce makes an increase count only after it HOLDS.
            try:
                debounce = float(d.get("debounce_s") or 0)
            except (TypeError, ValueError):
                debounce = 0.0
            if debounce > 0:
                params["debounce_s"] = min(60.0, debounce)

        elif fn_type == "mqtt_sub":
            topic = str(d.get("topic") or "").strip()
            if not topic:
                err(n["id"], "MQTT Subscribe: enter the topic to subscribe to")
                continue
            # WHERE: the named MQTT Connection -- only its id travels; the
            # broker and the login stay in [[mqtt_clients]], out of every
            # compiled function a workflow viewer can read. A node saved
            # before Connections keeps its own host/port/login (the key's
            # PRESENCE is the test, the Modbus Server rule).
            # None picked is not an error: it is this box's broker, what
            # every MQTT node meant before Connections (the inspector hints).
            conn = str(d.get("connection") or "").strip()
            if conn:
                params["conn"] = conn
            elif "connection" in d:
                params["host"] = "127.0.0.1"
                params["port"] = 1883
            else:
                params["host"] = str(d.get("host") or "127.0.0.1").strip()
                params["port"] = d.get("port", 1883)
                for field in ("username", "password"):
                    if str(d.get(field) or "").strip():
                        params[field] = str(d[field]).strip()
            params["topic"] = topic
            params["fallback"] = d.get("fallback", 0)
            if str(d.get("json_path") or "").strip():
                params["json_path"] = str(d["json_path"]).strip()

        elif fn_type == "board_input":
            port = str(d.get("port") or "").upper()
            if not port:
                err(n["id"], "Board Input: choose which input this reads (DI0 or DI1)")
                continue
            # Handed to another program: an input we promised not to read.
            # A WARNING, not an error -- the graph is not wrong, the box just
            # does not own that terminal at the moment.
            if port in _released_ports():
                warn(n["id"],
                     f"{port} has been handed to another program on the "
                     f"Board I/O page - this node is idle until it is taken "
                     f"back")
                continue
            # ONE node per terminal (client UAT 2026-10-04, item 6: "only
            # one hardware node"). Two nodes reading DI0 are one reading
            # drawn twice; the first node's output feeds as many nodes as
            # the canvas wants, so the second is refused by name.
            if port in di_nodes:
                err(n["id"], f"{port} is already read by '{di_nodes[port]}' - "
                             "one Board Input per terminal; wire that node's "
                             "output here instead, it can feed as many nodes "
                             "as you like")
                continue
            di_nodes[port] = str(d.get("name") or spec["label"])
            params["port"] = port
            if d.get("invert"):
                params["invert"] = True

        elif fn_type == "modbus_read":
            # WHERE is a named Modbus Server -- the connection lives once, in
            # [[modbus_clients]], and every Read and Write on that device
            # names it. A node saved before servers existed has no `server`
            # key and keeps the old rule, which is why the key's PRESENCE is
            # what is tested: the device is deliberately not on the node, the
            # board has one RS485 port and the backend resolves it
            # (/dev/ttyACM0 by default), at the node's own baud rate.
            params["fc"] = d.get("fc", 3)
            params["address"] = d.get("address", 0)
            params["quantity"] = d.get("quantity", 1)
            params["interval_s"] = d.get("interval_s", 5)
            # Interval 0 = read ON A TRIGGER (client UAT 2026-10-04, item
            # 5): the node reads once each time the wire into its trigger
            # port rises -- an Inject's press, a Schedule coming due, or a
            # rule's alert -- and never on its own clock. Resolved here, as a
            # (function, metric) pair the app watches for a rising edge: a
            # Condition is not a function of its own but a trigger ON the
            # rule before it, and that rule's alert is the edge, so the wire
            # is followed back through the logic nodes to the rule.
            try:
                read_every = float(d.get("interval_s", 5) or 0)
            except (TypeError, ValueError):
                read_every = 5.0
            if read_every <= 0:
                trig_in = [(e_, src) for e_, src in preds.get(n["id"]) or []
                           if (e_.get("targetHandle") or "in") == "trig"]
                if len(trig_in) != 1:
                    err(n["id"], "interval 0 means read on a trigger - wire "
                                 "an Inject, a Schedule or a Condition into "
                                 "its trigger port, or set an interval"
                        if not trig_in else
                        "Modbus Read takes ONE trigger - unwire the others")
                    continue
                trig_src = trig_in[0][1]
                trig_cat = REGISTRY[trig_src["type"]]["category"]
                hops = 0
                while trig_cat == "logic" and not is_branch(trig_src) \
                        and hops < 8:
                    back = [s_ for _e, s_ in preds.get(trig_src["id"]) or []
                            if REGISTRY[s_["type"]]["category"]
                            in ("logic", "analytics", "variable")]
                    if len(back) != 1:
                        break
                    trig_src, hops = back[0], hops + 1
                    trig_cat = REGISTRY[trig_src["type"]]["category"]
                trig_fid = _slug((trig_src.get("data") or {}).get("fnId"))
                if trig_cat not in ("analytics", "variable") or not trig_fid:
                    err(n["id"], "what is wired to Modbus Read's trigger "
                                 "never fires - wire an Inject, a Schedule "
                                 "or a Condition on a rule into it")
                    continue
                trig_type = node_fn_type(trig_src, REGISTRY[trig_src["type"]])
                params["trigger"] = {
                    "fn": trig_fid,
                    # Through a Condition it is that rule's ALERT; wired
                    # straight, the clock's own "it happened" key, else the
                    # rule's primary value going truthy.
                    "metric": ("alert" if hops else CLOCK_FN_METRIC.get(
                        trig_type, (functions_service.OUTPUTS.get(trig_type)
                                    or ("value",))[0]))}
            if "server" in d:
                server = str(d.get("server") or "").strip()
                if not server:
                    err(n["id"], "Modbus Read: pick a Server - or "
                                 "add one with +")
                    continue
                params["server"] = server
                # Blank = the server's own Unit-Id (Node-RED's rule).
                if d.get("unit") not in (None, ""):
                    params["unit"] = d.get("unit")
            else:
                params["unit"] = d.get("unit", 1)
                params["baudrate"] = d.get("baudrate", 9600)

        elif fn_type == "schedule":
            every = str(d.get("every") or "daily").strip().lower()
            if every not in functions_service.SCHEDULE_EVERY:
                err(n["id"], "Schedule: choose how often it runs")
                continue
            params["every"] = every
            if every == "minutes":
                params["interval_min"] = d.get("interval_min", 15)
            elif every == "hourly":
                params["minute"] = d.get("minute", 0)
            else:
                try:
                    hour, minute = functions_service.parse_hhmm(d.get("at"))
                except ValueError as exc:
                    err(n["id"], f"Schedule: {exc}")
                    continue
                params["at"] = f"{hour:02d}:{minute:02d}"
                if every == "weekly":
                    params["weekday"] = d.get("weekday", 0)
                elif every == "monthly":
                    params["day"] = d.get("day", 1)
            if d.get("catch_up"):
                params["catch_up"] = True
            # ...and the window it is allowed to run in. Cleaned by the same
            # function that validates the stored document, so a window typed
            # on the canvas and one written into a config mean the same thing.
            params.update(functions_service.clean_window(d))

        elif fn_type == "inject":
            try:
                ptype, payload = functions_service.clean_inject_payload(
                    d.get("ptype"), d.get("payload"))
            except ValueError as exc:
                err(n["id"], f"Inject: {exc}")
                continue
            params["ptype"] = ptype
            params["payload"] = payload
            if str(d.get("topic") or "").strip():
                params["topic"] = str(d["topic"]).strip()
            if d.get("once"):
                params["once"] = True
                params["once_delay_s"] = d.get("once_delay_s", 0.1)
            # Written only when OFF: every repeating Inject saved before
            # Start/Stop existed started with the app, and still does.
            if d.get("autostart") is False:
                params["autostart"] = False
            if d.get("repeat") == "interval":
                unit = INJECT_UNITS.get(d.get("every_unit"), 1)
                try:
                    every = float(d.get("every") or 0)
                except (TypeError, ValueError):
                    every = 0
                seconds = every * unit
                if not (functions_service.MIN_INJECT_INTERVAL_S <= seconds
                        <= functions_service.MAX_INJECT_INTERVAL_S):
                    err(n["id"], "Inject: repeat every 1 second to 24 hours")
                    continue
                params["interval_s"] = seconds

        elif fn_type == "state_watch":
            params.update(state_points.params(
                d, [t["id"] for _e, t in succs.get(n["id"], ())]))

        elif fn_type == "api_poll":
            url = str(d.get("url") or "").strip()
            if not url:
                err(n["id"], "API Value: enter the address to read from")
                continue
            params["url"] = url
            params["interval_s"] = d.get("interval_s", 5)
            params["fallback"] = d.get("fallback", 0)
            if str(d.get("json_path") or "").strip():
                params["json_path"] = str(d["json_path"]).strip()
            if d.get("headers"):
                params["headers"] = d["headers"]
            if str(d.get("token") or "").strip():
                params["token"] = str(d["token"]).strip()

        # OID on an Object Detection: an event per ARRIVAL, naming the object
        # (track id + class) -- what a Data Table row per object is written
        # from, exactly as Cycle Time's stamped stays are (the dataset arming
        # takes stamp_events as "fires by itself").
        if fn_type == "object_detect" and d.get("OID"):
            params["stamp_events"] = True

        # Which model's detections this function consumes: taken from the
        # Detection Stream node(s) upstream. One named model scopes the
        # function to it; blank or disagreeing sources keep any-model.
        # A formula and an API reading consume no detections at all, so neither
        # takes a camera or a model.
        if fn_type not in ("expr", "api_poll", "board_input", "modbus_read",
                           "schedule", "inject", "state_watch"):
            model = upstream_model(n["id"])
            if model:
                params["model"] = model
            # ...and which camera's frames, taken the same way. A zone already
            # BELONGS to a camera, so a source naming a different one is a
            # contradiction the operator has to resolve -- silently picking
            # either side would produce a rule that reads nothing, forever.
            camera = upstream_camera(n["id"])
            zone_cams = {str((z or {}).get("camera") or "").strip()
                         for _znode, z in zone_inputs(n)}
            zone_cams.discard("")
            zone_cam = next(iter(zone_cams)) if len(zone_cams) == 1 else ""
            # "*" (every camera) is only meaningful for a rule that names no
            # place: a zone exists on ONE camera's picture, so "this zone on
            # all cameras" cannot be honoured and is refused rather than
            # quietly counting nothing on three of them.
            if camera == functions_service.ALL_CAMERAS and zone_cams:
                err(n["id"], "a rule using a Zone runs on that zone's camera - "
                             "'All cameras' needs a rule with no zone "
                             "(proximity, guard, a class count)")
            elif camera and zone_cam and zone_cam != camera:
                err(n["id"], f"this rule reads camera '{camera}' but its "
                             f"zone is on camera '{zone_cam}'")
            # A zone IMPLIES its camera. Without this a rule on camera 2's zone
            # compiled with no camera, so the engine handed it to camera 1 --
            # whose zone engine does not have that zone, so the rule read zero
            # forever with nothing to show why.
            if not camera:
                camera = zone_cam
            if camera:
                params["camera"] = camera

        doc = {"id": fid, "name": str(d.get("name") or fid)[:80],
               "type": fn_type, "enabled": True, "params": params}

        # Trigger: from the node itself, or from a connected Alert node (the
        # Alert node wins -- it is the visible one on the canvas).
        trig = d.get("trigger") if isinstance(d.get("trigger"), dict) else None
        # Legacy baked-in alerts (pre-standardize_alerts graphs and freshly
        # imported old files): still compiled, but only while the node data
        # actually carries one -- migrated nodes alert via an Alert node.
        if (n["type"] == "analytics.violations" and d.get("severity") is not None
                and node_fn_type(n, spec) == "zone_unique"):
            # Fire once per newly counted object: the `new` metric spikes on
            # exactly those frames; the cooldown spaces bursts out.
            limit = d.get("limit", 1)
            trig = {"metric": "new", "op": ">", "value": 0,
                    "severity": "crit" if d.get("severity") == "crit" else "warn",
                    "message": (str(d.get("message") or "").strip()
                                or f"violation in {doc['name']} (more than {limit})"),
                    "cooldown_s": d.get("cooldown_s", 2)}
        if n["type"] == "analytics.crowd" and d.get("severity") is not None:
            # The whole point of this node: more than `limit` inside -> event.
            try:
                limit = max(0, int(d.get("limit", 1)))
            except (TypeError, ValueError):
                limit = 1
            trig = {"metric": "count", "op": ">", "value": limit,
                    "severity": "crit" if d.get("severity") == "crit" else "warn",
                    "message": (str(d.get("message") or "").strip()
                                or f"more than {limit} in {doc['name']}"),
                    "cooldown_s": d.get("cooldown_s", 30)}
        if trig:
            doc["trigger"] = trig

        try:
            doc = functions_service.validate_function(doc)
        except (ValueError, TypeError) as exc:
            err(n["id"], str(exc))
            continue
        functions.append(doc)
        fn_meta[n["id"]] = doc
        if _set_target_fid(n):
            setters[fid] = doc

        # What this node offers to the Dashboard and Analytics palettes. The
        # node's `expose` says which metrics and where; with nothing set, only
        # the node's primary metric is offered, so the palette stays short
        # instead of listing every metric of every function.
        # Variables are offered too, in their OWN group. They used to be left
        # out on the grounds that a value parked on the canvas for a formula to
        # read is plumbing rather than a measurement -- true, but the real
        # objection was that it doubled the length of a flat palette. The
        # picker groups and searches, so the objection is gone, and a running
        # total somebody built by hand is exactly the thing they then want to
        # put on a dashboard.
        expose = d.get("expose") if isinstance(d.get("expose"), dict) else {}
        # An ON/OFF point is not offered to the Dashboard and Analytics
        # palettes: it is drawn on no node of its own, and every system would
        # otherwise grow an entry per Detection Stream and Class Filter.
        metrics = (() if doc["type"] == "state_watch"
                   else functions_service.OUTPUTS.get(doc["type"], ()))
        origin = "variable" if spec["category"] == "variable" else "measure"
        for metric in metrics:
            surfaces = expose.get(metric)
            if surfaces is None:
                primary = ((node_metrics(n, spec) or list(metrics)) or [""])[0]
                # The per-period counts are offered alongside the primary
                # metric. They were not, which is how a card ends up titled
                # "(today)" and bound to `total`: the palette had no `today`
                # to pick, so the key was typed by hand and the label was the
                # only thing that said which period was meant.
                surfaces = ["dashboard", "analytics"] if (
                    metric == primary or metric in _PERIOD_METRICS) else []
            surfaces = [s for s in surfaces if s in ("dashboard", "analytics")]
            if not surfaces:
                continue
            role = metric_role(doc["type"], metric)
            catalog.append({
                "key": f"fn_{doc['id']}_{metric}",
                "label": f"{doc['name']} · {metric}",
                "title": doc["name"] if len(metrics) == 1 else f"{doc['name']} ({metric})",
                "role": role,
                "surfaces": surfaces,
                "node": n["id"],
                # WHERE this came from, for the picker's grouping. A flat list
                # of everything the canvas publishes reads as one long
                # undifferentiated column once a pipeline has more than a few
                # rules -- "a live measurement" and "a card somebody drew" are
                # different things to go looking for.
                "origin": origin,
                **_ROLE_WIDGET[role],
            })

        # A heatmap's cells are not metrics -- there are up to 768 of them and
        # they are a picture, not a reading -- so the loop above offers only
        # its two numbers. The MAP itself is what the operator wants on a page,
        # and it is offered as one entry bound to the function (the widget
        # reads the whole family of cell keys off that prefix).
        if doc["type"] == "heatmap":
            catalog.append({
                "key": f"fn_{doc['id']}_hot",
                "label": f"{doc['name']} · map",
                "title": doc["name"],
                "role": "total",
                "surfaces": ["dashboard", "analytics"],
                "node": n["id"],
                "origin": origin,
                "kind": "heatmap",
                "source": "stat",
            })

        # An INJECT is offered as a BUTTON as well as a value: the press that
        # fires it on the canvas, on the page somebody operates from. The
        # button is linked to the node (`node`), so a rename on the canvas
        # renames it and a deleted node is said on the card rather than
        # leaving a button that presses nothing (apply_artifacts). Dashboard
        # only: a button is a live control, not a reading over a period.
        if doc["type"] == "inject":
            catalog.append({
                "key": f"fn_{doc['id']}_value",
                "label": f"{doc['name']} · button",
                "title": doc["name"],
                "role": "custom",
                "surfaces": ["dashboard"],
                "node": n["id"],
                "origin": "control",
                "kind": "button",
                "source": "stat",
                "inject": {"fn": doc["id"],
                           "ptype": doc["params"].get("ptype", "string"),
                           # A repeating Inject's button is Start / Stop.
                           "repeat": bool(doc["params"].get("interval_s"))},
            })

    # -- data table columns -> palette entries --------------------------------
    #
    # The Dashboard and Analytics EDITORS build their palette from this
    # catalogue. Without these, a table's columns were reachable only by
    # wiring a card on the workflow canvas: the editors offered analytics
    # functions and nothing else, so a card over recorded data could not be
    # added where cards are actually composed.
    #
    # Emitted for every registered table, not only those a node writes to --
    # the rows exist and are queryable either way, and a table nothing
    # currently records into is exactly the one whose history you want to
    # look at.
    for key in sorted(tables or {}):
        table = tables[key]
        for col in table.get("columns") or []:
            numeric = col.get("type") == "number"
            catalog.append({
                # Not a stat key: nothing publishes this live. The editor
                # carries the dataset/query instead, and `key` stays blank.
                "key": "",
                "label": f"{table.get('name') or key} · {col['name']}",
                "title": col["name"].replace("_", " "),
                "role": "gauge_count" if numeric else "total",
                "surfaces": ["dashboard", "analytics"],
                "origin": "table",
                "group": table.get("name") or key,
                "kind": "tile",
                "source": "dataset",
                "dataset": key,
                "columns": [dict(c) for c in table["columns"]],
                # A number is watched as its latest value; a label is counted.
                "query": {"agg": "latest" if numeric else "count",
                          "field": col["name"]},
            })

    # -- When nodes -> extra triggers on their function ------------------------
    #
    # A When node is a CONDITION on an existing function, not a function of its
    # own: it appends to that function's `triggers` list, which the engine
    # already evaluates alongside the built-in `trigger`. Several When nodes on
    # one function are several independent conditions.
    #
    # Outputs wired AFTER a When therefore fire on the FUNCTION's alert stream:
    # the engine does not record which trigger fired, so all of a function's
    # triggers share its outputs. Two Whens on one function with different
    # outputs is not expressible -- use two functions.
    # -- switch nodes -> one exclusive rule per branch -----------------------
    #
    # A branch is "my test passes AND no earlier branch's did", which is what
    # first-match-wins means written out. Each becomes its own 0/1 function so
    # the outputs on one branch are untouched by the others -- attaching
    # several triggers to ONE function would fire every output on any of them.
    switch_meta = {}                             # (node id, handle) -> fn doc

    def _switch_base(node, data):
        """The stem this Switch's branch rules are named from -- read from the
        map settled up front (switch_bases), so a Formula wired to a branch
        and the switch loop itself can never disagree about a name."""
        return sw_bases.get(node["id"]) \
            or _slug(data.get("fnId")) or _slug(data.get("name")) or "switch"

    def _switch_cond(key, rule, keys=None):
        """One rule row -> a 0/1 expression term, or (None, why).

        The right-hand side is a number, or -- when the row names `rhs_input`
        and that port has something wired to it -- another wired value, so a
        condition can read "in1 > in2" and not only "in1 > 10".
        """
        op = str(rule.get("op") or ">")
        if op == "between":
            # A range band: from `value` up to (not including) `value2`, so
            # adjacent bands tile with no gap and no double-count.
            try:
                lo, hi = float(rule.get("value", 0)), float(rule.get("value2", 0))
            except (TypeError, ValueError):
                return None, "every range needs two numbers"
            if hi <= lo:
                return None, "a range's 'to' must be above its 'from'"
            return f"(({key} >= {lo:g}) and ({key} < {hi:g}))", None
        if op not in functions_service.TRIGGER_OPS:
            return None, f"{op!r} is not a comparison"
        rhs_port = str(rule.get("rhs_input") or "")
        if rhs_port:
            rhs = (keys or {}).get(rhs_port)
            if rhs is None:
                return None, ("a condition compares with an input that has "
                              "nothing wired into it")
            return f"({key} {op} {rhs})", None
        try:
            value = float(rule.get("value", 0))
        except (TypeError, ValueError):
            return None, "every branch needs a number to compare with"
        return f"({key} {op} {value:g})", None

    def _wire_keys(node):
        """{input handle -> the stat key wired to it}, or (None, why).

        Shared by every shape that reads MORE than one value (Compare's
        per-condition inputs, AND/OR's operands): a wire with no handle is
        read as in1, which is what every graph saved before ports existed
        means by its single wire.
        """
        keys = {}
        for edge, src in preds.get(node["id"], ()) or ():
            alias = input_alias(src, edge, sw_bases)
            if not alias:
                return None, "a wired node publishes nothing to test"
            key = alias[1]
            shandle = str(edge.get("sourceHandle") or "")
            src_spec = REGISTRY.get(src["type"]) or {}
            if shandle in (node_metrics(src, src_spec) or []):
                key = f"fn_{alias[0]}_{shandle}"
            keys[str(edge.get("targetHandle") or "in1")] = key
        return keys, None

    def _emit_first_match(node, data, conds):
        """One 0/1 rule per branch, plus the catch-all: "my test passes AND no
        earlier one did", which is what first-match-wins means written out.

        Shared by Compare (each row its own input) and by the shapes that route
        one value (cases, bands), because the ORDER contract is the same for
        all of them and the branch ids must stay `<base>_b<index>` either way.
        """
        base = _switch_base(node, data)
        spec = REGISTRY.get(node["type"]) or {}
        # The names an operator reads on the ports, when this shape has fixed
        # ones ("True"/"False", "Pass"/"Fail"). A rule's NAME shows up in the
        # events log and on any card bound to it, so a branch called
        # "Compare · branch 1" where the port says "True" is the same thing
        # under two names.
        port_names = spec.get("ports") or []
        branches = list(enumerate(conds))
        if data.get("otherwise", True):
            branches.append((len(conds), None))     # the catch-all
        for index, cond in branches:
            earlier = "".join(f" and not {c}" for c in conds[:index])
            expr = (f"not {conds[0]}" + "".join(f" and not {c}" for c in conds[1:])
                    if cond is None else f"{cond}{earlier}")
            word = (port_names[index] if index < len(port_names)
                    else "otherwise" if cond is None else f"branch {index + 1}")
            doc = {"id": f"{base}_b{index}"[:40],
                   "name": f"{data.get('name') or spec.get('label') or 'Switch'} · {word}",
                   "type": "expr", "enabled": True, "params": {"expr": expr},
                   "trigger": {"metric": "value", "op": ">", "value": 0,
                               "severity": data.get("severity") or "info",
                               "message": data.get("message") or "",
                               "cooldown_s": data.get("cooldown_s", 0)}}
            try:
                doc = functions_service.validate_function(doc)
            except (ValueError, TypeError) as exc:
                err(node["id"], str(exc))
                continue
            if doc["id"] in seen_fids:
                err(node["id"],
                    f"duplicate function id {doc['id']!r} - rename the Switch")
                continue
            seen_fids.add(doc["id"])
            functions.append(doc)
            switch_meta[(node["id"], f"out{index}")] = doc

    def _dead_case_warning(node, keys, conds):
        """Say so when an If / Else if cannot take a later way out.

        First match wins, so a row that tests exactly what an earlier row
        tests never runs -- and two inputs wired from ONE place are one value
        under two names, which is how that happens without anyone typing a
        test twice. Seen live (asri_test_4 on .49, 2026-10-09): a Class
        Filter's OFF point wired into input1 AND input2, "if input1 == 1"
        stopping the light and "elif input2 == 1" the motor; the motor never
        got its stop. A warning, not an error: the graph is legal, it just
        does not do what it looks like it does."""
        same = {}
        for port, key in sorted(keys.items()):
            same.setdefault(key, []).append(port)
        twins = [ports for ports in same.values() if len(ports) > 1]
        dead = [i for i in range(1, len(conds)) if conds[i] in conds[:i]]
        if not twins and not dead:
            return
        word = lambda p: re.sub(r"^in(\d+)$", r"input\1", p)   # noqa: E731
        if dead:
            first = conds.index(conds[dead[0]])
            msg = (f"branch {dead[0] + 1} never runs: it tests the same "
                   f"value as branch {first + 1}, and the first case that "
                   "passes wins")
        else:
            msg = (f"{word(twins[0][0])} and {word(twins[0][1])} are wired "
                   "from the same value, so they always read the same - a "
                   "later case runs only when every earlier test fails")
        # The fix is the same either way: one If / Else if per action. Two
        # Triggers wired straight onto ONE moment (a point, or one way out)
        # are one rule with one payload -- the second is refused as "both
        # name the same moment" and its output sends the bare 1/0 instead,
        # which on a motor register is a speed nobody chose. Said for the
        # ON/OFF point (the hidden node state_points.split draws for it),
        # because that is where people reach for it.
        if any(src.get("type") == state_points.NODE
               for _e, src in preds.get(node["id"], ())):
            msg += (". To do two things when an ON/OFF point fires, wire the "
                    "point into a separate If / Else if per action, each with "
                    "its own Trigger - two Triggers straight on the point "
                    "would share one payload")
        else:
            msg += (". To do two things on one result, give each action its "
                    "own If / Else if and Trigger")
        warn(node["id"], msg)

    for n in graph["nodes"]:
        if not is_branch(n):
            continue
        d = n["data"] or {}
        # The shape is the NODE for the branch family, and the saved `ctype`
        # only for the Switch they replaced -- see branch_ctype.
        ctype = branch_ctype(n)
        label = (REGISTRY.get(n["type"]) or {}).get("label") or "Switch"

        # -- multi: SEVERAL wired values, one combined AND/OR test ----------
        # Ports are the rows: each condition names the input handle it tests
        # (in1, in2, ...), so adding a condition IS adding a port. Two ways
        # out -- pass and (optionally) fail -- because a combined test has one
        # answer, not one per operand.
        if ctype == "multi":
            wires = list(preds.get(n["id"], ()))
            if not wires:
                if fed_by_off_node(n):
                    off_nodes.add(n["id"])
                else:
                    err(n["id"], "Switch (AND/OR) needs at least one value wired in")
                continue
            keys, why = _wire_keys(n)
            if why:
                err(n["id"], f"Switch: {why}")
                continue
            conds = []
            for row in [r for r in (d.get("rules") or []) if isinstance(r, dict)][:8]:
                key = keys.get(str(row.get("input") or "in1"))
                if key is None:
                    err(n["id"], "Switch: a condition tests an input with "
                                 "nothing wired into it")
                    conds = None
                    break
                term, why = _switch_cond(key, row, keys)
                if term is None:
                    err(n["id"], f"Switch: {why}")
                    conds = None
                    break
                conds.append(term)
            if conds is None:
                continue
            if not conds:
                err(n["id"], "Switch (AND/OR) needs at least one condition")
                continue
            join = " or " if d.get("join") == "or" else " and "
            passed = join.join(conds)
            base = _switch_base(n, d)
            outs = [(0, passed, "pass")]
            if d.get("otherwise", True):
                outs.append((1, f"not ({passed})", "otherwise"))
            for index, expr, word in outs:
                doc = {"id": f"{base}_b{index}"[:40],
                       "name": f"{d.get('name') or 'Switch'} · {word}",
                       "type": "expr", "enabled": True, "params": {"expr": expr},
                       "trigger": {"metric": "value", "op": ">", "value": 0,
                                   "severity": "info", "message": "",
                                   "cooldown_s": 0}}
                try:
                    doc = functions_service.validate_function(doc)
                except (ValueError, TypeError) as exc:
                    err(n["id"], str(exc))
                    continue
                if doc["id"] in seen_fids:
                    err(n["id"], f"duplicate function id {doc['id']!r} - rename the Switch")
                    continue
                seen_fids.add(doc["id"])
                functions.append(doc)
                switch_meta[(n["id"], f"out{index}")] = doc
            continue

        # -- compare: IF / ELSE IF / ELSE, each row on its own input ---------
        #
        # The other first-match shapes route ONE value: "which band is this
        # number in". This one answers a different question -- "which of these
        # separate things is true" -- so each row names the port it reads, the
        # way an AND/OR condition does, and gets its own way out. A row that
        # names no port reads in1, which is what a Compare saved before this
        # (one wire, no handle, one test) already means, so its two branch ids
        # and its true/false ports are unchanged.
        if ctype == "compare":
            keys, why = _wire_keys(n)
            if why:
                err(n["id"], f"Compare: {why}")
                continue
            if not keys:
                if fed_by_off_node(n):
                    off_nodes.add(n["id"])
                else:
                    err(n["id"], "Compare needs a value wired in")
                continue
            rows = [r for r in (d.get("rules") or []) if isinstance(r, dict)][:8]
            if not rows:
                err(n["id"], "Compare needs at least one condition")
                continue
            conds = []
            for row in rows:
                key = keys.get(str(row.get("input") or "in1"))
                if key is None:
                    err(n["id"], "Compare: a condition reads an input with "
                                 "nothing wired into it")
                    conds = None
                    break
                term, why = _switch_cond(key, row, keys)
                if term is None:
                    err(n["id"], f"Compare: {why}")
                    conds = None
                    break
                # A row may carry MORE tests -- "if in1 > 0 AND in2 == 0" is
                # one condition to the person writing it, so it is one row
                # here, folded left the way the Formula chains: each extra
                # test names its own input and its own AND/OR link.
                for sub in [x for x in (row.get("more") or ())
                            if isinstance(x, dict)][:4]:
                    skey = keys.get(str(sub.get("input") or "in1"))
                    if skey is None:
                        err(n["id"], "Compare: a condition reads an input "
                                     "with nothing wired into it")
                        term = None
                        break
                    sterm, why = _switch_cond(skey, sub, keys)
                    if sterm is None:
                        err(n["id"], f"Compare: {why}")
                        term = None
                        break
                    link = " or " if sub.get("link") == "or" else " and "
                    term = f"({term}{link}{sterm})"
                if term is None:
                    conds = None
                    break
                conds.append(term)
            if conds is None:
                continue
            _dead_case_warning(n, keys, conds)
            _emit_first_match(n, d, conds)
            continue

        # -- every other shape: exactly one value in, first match wins -------
        # ANY value-producing node, not only ones that compiled to a fn_meta
        # entry -- that used to exclude a Variable read straight through
        # (variable.ref, unwired), which is exactly "route on this named
        # value" and had no way to reach Switch at all. input_alias is the
        # same resolver a Formula's wired operand or a Running Total uses.
        wires = list(preds.get(n["id"], ()))
        if len(wires) != 1:
            if fed_by_off_node(n):
                off_nodes.add(n["id"])
            elif len(wires) > 1:
                # Cases and bands ROUTE one value -- "which band is this
                # number in" has no meaning for two numbers. The two shapes
                # that read several are named, because that is the fix.
                err(n["id"],
                    f"this Switch shape routes ONE value and {len(wires)} are "
                    "wired in - use \"If / Else if (conditions)\" to test them "
                    "one after another, or \"Multi-condition (AND / OR)\" to "
                    "combine them, or remove a wire")
            else:
                err(n["id"], "Switch needs a value wired in")
            continue
        edge, src = wires[0]
        alias = input_alias(src, edge, sw_bases)
        if not alias:
            err(n["id"], "Switch: the wired node publishes nothing to route on")
            continue
        key = alias[1]
        # A wire dropped on a NAMED metric port (Frame Counter's "new", a
        # zone's "OID" ...) overrides the default first metric, matching the
        # convention everywhere else on the canvas: the wire's own handle is
        # what is meant, not a separate picker that could disagree with it.
        # variable.ref publishes no such ports, so it is left at what
        # input_alias already resolved.
        handle = str(edge.get("sourceHandle") or "")
        src_spec = REGISTRY.get(src["type"]) or {}
        handle_metrics = node_metrics(src, src_spec) if src_spec else []
        if handle in handle_metrics:
            key = f"fn_{alias[0]}_{handle}"
        elif d.get("metric") in handle_metrics:
            # No named handle, but the inspector's own picker named a real
            # metric of this source -- kept for a graph saved before wiring
            # to a named port existed.
            key = f"fn_{alias[0]}_{d.get('metric')}"

        rules = [r for r in (d.get("rules") or []) if isinstance(r, dict)][:8]
        if not rules:
            err(n["id"], "Switch needs at least one branch")
            continue
        conds = []
        for rule in rules:
            term, why = _switch_cond(key, rule)
            if term is None:
                err(n["id"], f"Switch: {why}")
                conds = None
                break
            conds.append(term)
        if conds is None:
            continue

        _emit_first_match(n, d, conds)

    # Alert and When are the SAME thing to the engine: a condition on the
    # upstream function's value. They are compiled together so neither is ever
    # silently ignored -- an Alert used to be read only when it hung DIRECTLY
    # off the analytics node, so "Guard -> When -> Alert" compiled the When and
    # dropped the Alert without a word.
    #
    # A chain resolves through itself (the Alert after a When watches the same
    # function), so this iterates until nothing new resolves.
    cond_meta = {}                               # condition node id -> fn doc
    #: Which Trigger already claimed each rule: id(fn doc) -> that Trigger's
    #: name. A rule carries ONE trigger, so a second Trigger on the same
    #: moment is a conflict to report rather than an overwrite to perform.
    trigger_owner = {}
    wait_nodes = {}         # function id -> the Trigger node that waits for its timer
    pending = [n for n in graph["nodes"]
               if n["type"] in ("logic.when", "output.alert", "logic.delay",
                                "logic.catch")]

    # An Alert decorates ONE moment. Counted up front, because the two places
    # that adopt an Alert -- after a Condition, or after a value that is
    # already true or false -- would otherwise each see only their own wire
    # and the second answer would be dropped without a word.
    ambiguous_alerts = set()
    for n in graph["nodes"]:
        if n["type"] != "output.event":
            continue
        feeds = [src for _e, src in preds.get(n["id"], ())
                 if REGISTRY[src["type"]]["category"] in ("analytics", "variable")
                 or src["type"] in _TRANSPARENT_OUTPUTS
                 or src["type"] == "logic.when" or is_branch(src)]
        if len(feeds) > 1:
            err(n["id"], f"an Alert fires on ONE answer (this has "
                         f"{len(feeds)}) - keep one wire in, or give each its "
                         "own Alert")
            ambiguous_alerts.add(n["id"])

    def wire_metric(node, fn, metrics):
        """The metric named by the PORT a wire into `node` left from.

        Dragging from a named output port IS the choice of metric -- it is
        the only one an operator makes without opening an inspector. A
        Line Counter offers in / out / inside / crossings as four ports,
        and dragging each into its own card drew four wires that all read
        `in`: the sinks consulted their own picker and, finding it unset,
        fell back to the function's FIRST metric. Four cards, four
        labels, one number.

        Only ports this function actually publishes answer, so a flow
        handle (out0, timer, link_out) or a stale port name falls through
        to whatever default the caller had.
        """
        for edge, src in preds.get(node["id"], ()):
            handle = str(edge.get("sourceHandle") or "")
            if handle in metrics and (fn_meta.get(src["id"]) or {}).get("id") \
                    == fn["id"]:
                return handle
        return ""

    def _cond_source(node):
        """(fn doc, how many candidates) for a condition node."""
        fns = []
        for edge, src in preds.get(node["id"], ()):
            cat = REGISTRY[src["type"]]["category"]
            if cat in ("analytics", "variable") and src["id"] in fn_meta:
                fns.append(fn_meta[src["id"]])
            elif is_branch(src):
                got = switch_meta.get(
                    (src["id"], edge.get("sourceHandle") or "out0"))
                if got:
                    fns.append(got)
            elif src["id"] in cond_meta:
                fns.append(cond_meta[src["id"]])
        return (fns[0] if len(fns) == 1 else None), len(fns)

    for _round in range(len(pending) + 1):
        if not pending:
            break
        stuck = []
        for n in pending:
            fn, count = _cond_source(n)
            if fn is None:
                stuck.append(n)
                continue
            d = n["data"] or {}
            # A Delay adds no condition -- it just makes what follows late. So
            # it passes the function straight through and everything after it
            # binds to the same rule, exactly as if it were not there.
            if n["type"] == "logic.delay":
                cond_meta[n["id"]] = fn
                continue
            # A Catch watches the ERROR flag of the rule before it, which is a
            # different value from the one that rule publishes -- so it gets
            # its own tiny function rather than a trigger on its neighbour.
            if n["type"] == "logic.catch":
                doc = {"id": f"catch_{fn['id']}"[:40],
                       "name": f"{fn['name']} failed",
                       "type": "expr", "enabled": True,
                       "params": {"expr": f"fn_{fn['id']}_error > 0"},
                       "trigger": {"metric": "value", "op": ">", "value": 0,
                                   "severity": d.get("severity") or "warn",
                                   "message": (d.get("message")
                                               or f"{fn['name']} stopped working"),
                                   "cooldown_s": d.get("cooldown_s", 60)}}
                try:
                    doc = functions_service.validate_function(doc)
                except (ValueError, TypeError) as exc:
                    err(n["id"], str(exc))
                    continue
                if doc["id"] in seen_fids:
                    err(n["id"], f"two Catch nodes watch {fn['name']!r}")
                    continue
                seen_fids.add(doc["id"])
                functions.append(doc)
                cond_meta[n["id"]] = doc
                continue
            primary = functions_service.OUTPUTS[fn["type"]][0]
            # The WIRE is the truth. An Alert re-wired from one rule to
            # another keeps its old metric in data -- "current" from a Time in
            # Zone, now feeding a Zone Counter that publishes no such thing.
            # Erroring stranded the whole canvas over a leftover the inspector
            # was already displaying as the primary; follow the wire instead,
            # and say so.
            metric = (d.get("metric")
                      or wire_metric(n, fn,
                                     functions_service.OUTPUTS[fn["type"]])
                      or primary)
            # Cycle Time no longer counts. Falling back to its first metric
            # would keep the operator's comparison and silently change its
            # subject -- "current > 1" (two objects inside) coming back as
            # "live_s > 1" (anybody inside for a second). Refused instead, with
            # the two nodes that answer the question it was really asking.
            if fn["type"] == "zone_dwell" and metric == "current":
                err(n["id"],
                    "Cycle Time measures TIME and no longer reports how many "
                    "objects are inside - this condition still watches "
                    "'current'. Pick a time here (live_s, avg_s, max_s), or "
                    "count with a Zone Counter (how many) or Object in Zone "
                    "(whether anything is there)")
                continue
            if metric not in functions_service.OUTPUTS[fn["type"]] + ("value",):
                warn(n["id"], f"'{metric}' is not a value {fn['name']!r} "
                              f"publishes - watching '{primary}' instead")
                metric = primary
            # The Alert node wired after this condition, if any: it carries
            # what the alarm SAYS, and may ask to fire on the condition being
            # false. More than one is ambiguous -- two alarms decorating one
            # moment cannot both win -- so it is refused rather than resolved
            # by document order.
            alerts = [s for _e, s in succs.get(n["id"], ())
                      if s["type"] == "output.event"]
            if len(alerts) > 1:
                err(n["id"], f"{spec['label']} has {len(alerts)} Alerts after "
                             "it and they would contradict each other - keep "
                             "one, or give each its own Condition")
                continue
            # With no Alert node, the alarm's settings are read off this node:
            # that is every graph saved before the two were separated, and a
            # Condition on its own still raises a plain event.
            alarm = (alerts[0].get("data") or {}) if alerts else d
            if n["type"] == "logic.when":
                trig = {"metric": metric,
                        "mode": d.get("mode") or "above",
                        "value": d.get("value", 1),
                        "value2": d.get("value2", 0),
                        "hold_s": d.get("hold_s", 0)}
                if alerts and str(alarm.get("when") or "true") == "false":
                    err(alerts[0]["id"],
                        "Compare already says which way round it reads - set "
                        "its own test instead of asking this Alert for the "
                        "false side")
                    continue
            else:
                op = d.get("op") or ">"
                if str(alarm.get("when") or "true") == "false":
                    # Fire while the condition does NOT hold. Inverting the
                    # comparison keeps ONE threshold in ONE place: the
                    # alternative is a second Condition with the opposite
                    # operator, and two copies of a number that must agree.
                    op = _INVERSE_OPS.get(op, op)
                trig = {"metric": metric,
                        "op": op,
                        "value": d.get("value", 1)}
            trig.update({"severity": alarm.get("severity") or "warn",
                         "message": alarm.get("message") or "",
                         "cooldown_s": alarm.get("cooldown_s", 10),
                         # Drawn colour for whatever trips this rule; the app
                         # resolves it back to the objects through the zone
                         # the function watches.
                         "box_color": alarm.get("box_color") or "",
                         "color_target": alarm.get("color_target") or "box"})
            try:
                trig = functions_service._validate_trigger(trig, fn["type"], fn["name"])
            except (ValueError, TypeError) as exc:
                err(n["id"], str(exc))
                continue
            fn.setdefault("triggers", []).append(trig)
            cond_meta[n["id"]] = fn
            # The Alert stands for the same rule, so anything wired after it
            # -- a Snapshot, a card, a Formula -- binds to that rule and not
            # to nothing. Registered here rather than compiled on its own,
            # because it IS this trigger, seen from the other side.
            for _e, nxt in succs.get(n["id"], ()):
                if nxt["type"] == "output.event"                         and nxt["id"] not in ambiguous_alerts:
                    cond_meta[nxt["id"]] = fn
            # Two conditions in a row is legal but rarely meant: each fires on
            # its own, so the alarm goes off twice.
            if any(src["type"] in ("logic.when", "output.alert")   # not the Alert node
                   for _, src in preds.get(n["id"], ())) \
                    and n["type"] != "logic.catch":
                warn(n["id"], "the condition before this one already raises an "
                              "alert - this adds a SECOND, independent one")
        if len(stuck) == len(pending):
            break
        pending = stuck

    # An Alert takes ANY answer that is already true or false, not only a
    # Condition. A Formula that ANDs two alarms comes out 0 or 1, and so does
    # a Switch branch -- the decision is made, and "fire on it" is the whole
    # remaining question. Requiring a Condition after a Formula would mean
    # writing "> 0" against a value that is already a yes or a no.
    #
    # A MEASUREMENT is still refused. A count or a clock is a quantity, and
    # picking the interesting value of one is what a Condition is for; doing
    # it here would be choosing a threshold on the operator's behalf. Same
    # line the capture outputs draw, and the same message.
    for n in graph["nodes"]:
        if n["type"] != "output.event" or n["id"] in cond_meta                 or n["id"] in ambiguous_alerts:
            continue
        if any(src["type"] in _TRANSPARENT_OUTPUTS and src["id"] in off_nodes
               for _e, src in preds.get(n["id"], ())):
            off_nodes.add(n["id"])          # its condition is off; so is this
            continue
        d = n.get("data") or {}
        sources = []
        for _e, src in preds.get(n["id"], ()):
            cat = REGISTRY[src["type"]]["category"]
            if cat in ("analytics", "variable") and src["id"] in fn_meta:
                sources.append(fn_meta[src["id"]])
            elif is_branch(src):
                got = switch_meta.get((src["id"], _e.get("sourceHandle") or "out0"))
                if got:
                    sources.append(got)
        if not sources:
            if any(src["id"] in off_nodes for _e, src in preds.get(n["id"], ())):
                off_nodes.add(n["id"])
                continue
            warn(n["id"], "an Alert says what an alarm looks like - wire a "
                          "Condition, a Formula or a Switch branch into it, or "
                          "the severity, message and colour here reach nothing")
            off_nodes.add(n["id"])
            continue
        fn = sources[0]
        if any(got is fn for got in cond_meta.values()):
            # The rule's moment is already defined by a Condition somewhere
            # else on the canvas, and this Alert is wired to the RULE rather
            # than to that Condition -- so it would quietly rewrite an alarm
            # whose own panel says nothing about it. One wire fixes it.
            err(n["id"], f"{fn['name']} already has a Condition on it - wire "
                         "this Alert after that Condition rather than beside "
                         "it, so one place says when and one says what")
            continue
        if fn.get("trigger") or fn.get("triggers"):
            # A Switch branch already carries the trigger its own rule
            # defined; this only changes what the alarm says.
            for trig in ([fn["trigger"]] if fn.get("trigger") else fn["triggers"]):
                trig.update({"severity": d.get("severity") or trig.get("severity"),
                             "message": d.get("message") or trig.get("message"),
                             "cooldown_s": d.get("cooldown_s", trig.get("cooldown_s")),
                             "box_color": d.get("box_color") or "",
                             "color_target": d.get("color_target") or "box"})
            if str(d.get("when") or "true") == "false":
                err(n["id"], f"{fn['name']} already says which way round it "
                             "reads - invert it there, not on this Alert")
                continue
            cond_meta[n["id"]] = fn
            continue
        if fn["type"] != "expr":
            err(n["id"],
                f"{fn['name']} measures rather than decides - put a Condition "
                "between it and this Alert and set the threshold there. A "
                "Formula needs none: its answer is already true or false")
            continue
        # A formula's answer, read as a yes or a no. `when: false` fires while
        # it is zero, which is the same one threshold read the other way.
        trig = {"metric": "value",
                "op": "==" if str(d.get("when") or "true") == "false" else ">",
                "value": 0,
                "severity": d.get("severity") or "warn",
                "message": d.get("message") or "",
                "cooldown_s": d.get("cooldown_s", 10),
                "box_color": d.get("box_color") or "",
                "color_target": d.get("color_target") or "box"}
        try:
            trig = functions_service._validate_trigger(trig, fn["type"], fn["name"])
        except (ValueError, TypeError) as exc:
            err(n["id"], str(exc))
            continue
        fn.setdefault("triggers", []).append(trig)
        cond_meta[n["id"]] = fn

    # -- the Trigger node ---------------------------------------------------
    #
    # Names a moment and raises it. Everything it can be wired to is already
    # a decision -- a Branch port, a Formula's 0/1 answer -- so there is no
    # threshold here: the operator picked the port they meant.
    #
    # The alarm is optional and off by default. Untouched, this raises a
    # named event with severity "info", no message and no colour, which is a
    # pass-through something like a Data Table write can be gated on without
    # anybody being alarmed. Ticking "Raise as an alert" is what turns it
    # into one.
    # SETTINGS FIRST. Where two Triggers share one moment the one carrying
    # settings defines it and the bare one relays (see the conflict handling
    # below), and that must not depend on which was dropped on the canvas
    # first: a rule whose colours survive only when the nodes happen to be in
    # the right order is a rule nobody can rely on.
    def _carries_settings(node):
        dd = node.get("data") or {}
        return bool(dd.get("alert") or dd.get("draw") or dd.get("zone_color")
                    or str(dd.get("message") or "").strip()
                    or trigger_out.is_set(dd)
                    or any(str(e.get("targetHandle") or "") in TRIGGER_GUARD_PORTS
                           for e, _s in preds.get(node["id"], ())))

    for n in sorted((x for x in graph["nodes"]
                     if x["type"] == "output.trigger"),
                    key=lambda x: not _carries_settings(x)):
        d = n.get("data") or {}
        name = str(d.get("name") or "").strip()
        wires = list(preds.get(n["id"], ()))
        if not wires:
            if fed_by_off_node(n):
                off_nodes.add(n["id"])
            else:
                err(n["id"], "a Trigger names a moment - wire a branch "
                             "(True, Pass, a case) into it")
            continue
        sources = []
        for edge, src in wires:
            # The zone and OID ports name a place and a who, not the moment:
            # a Zone Counter wired to the OID port is telling this Trigger
            # who is inside, and must not read as a second moment to name.
            if str(edge.get("targetHandle") or "") in TRIGGER_SIDE_PORTS:
                continue
            if is_branch(src):
                got = switch_meta.get(
                    (src["id"], edge.get("sourceHandle") or "out0"))
                if got:
                    sources.append(got)
            elif src["id"] in cond_meta:
                sources.append(cond_meta[src["id"]])
            elif REGISTRY[src["type"]]["category"] in ("analytics", "variable") \
                    and src["id"] in fn_meta:
                sources.append(fn_meta[src["id"]])
        if len(sources) > 1:
            err(n["id"], f"a Trigger names ONE moment (this has {len(sources)} "
                         "wired in) - keep one wire, or give each its own "
                         "Trigger")
            continue
        if not sources:
            if any(src["id"] in off_nodes for _e, src in wires):
                off_nodes.add(n["id"])
                continue
            err(n["id"], "the node wired into this Trigger raises nothing to "
                         "name - wire a branch port into it")
            continue
        fn = sources[0]
        if not name:
            # Required, and not defaulted: this is the one name on the canvas
            # nothing else can guess, and it is what the event log, the alert
            # text and any card bound to this moment will read for years.
            err(n["id"], "a Trigger needs a name - what IS this moment? "
                         "(\"Overcrowded\", \"Door left open\")")
            continue
        # An ON/OFF point (state_points.py) is already a decision: its value
        # is 1 while the state matches, so the Trigger holds exactly then.
        if fn["type"] not in ("expr", "state_watch"):
            # A measurement is a quantity, and choosing the interesting value
            # of one is a Branch's job. Doing it here would be picking a
            # threshold on the operator's behalf.
            err(n["id"],
                f"{fn['name']} measures rather than decides - put a Compare "
                "(or any branch) between it and this Trigger, and set the "
                "test there")
            continue
        alerting = bool(d.get("alert"))
        drawing = bool(d.get("draw"))
        trig = {"metric": "value", "op": ">", "value": 0,
                "severity": (d.get("severity") or "warn") if alerting else "info",
                # The Trigger's NAME is the event's text when the operator has
                # not written one: an event log full of blank messages was the
                # thing naming this node was meant to fix.
                "message": (d.get("message") or name) if alerting else name,
                "cooldown_s": d.get("cooldown_s", 30) if alerting else 0,
                # Two colours, each its own question: what the OBJECTS that
                # tripped this are drawn in, and what the ZONE it watches
                # turns while it holds. `draw` gates only the box half --
                # a zone colour is chosen by picking one, and nothing else.
                # Ticked with no colour chosen means the default one, not
                # "no colour": an empty box_color compiles to nothing, so
                # the setting read as ON while painting nothing at all --
                # which is exactly how a graph in the field ended up with
                # "Recolour the bounding box" ticked and orange boxes.
                "box_color": (d.get("box_color") or DEFAULT_BOX_COLOR)
                             if drawing else "",
                "zone_color": d.get("zone_color") or "",
                "color_target": (d.get("color_target") or "box") if drawing else "box"}
        # The Advanced output -- payload, idle, hold, how often -- or None
        # for a Trigger that uses none of it (every one saved before it).
        try:
            extra = trigger_out.advanced(d)
        except ValueError as exc:
            err(n["id"], str(exc))
            continue
        rate = (extra or {}).get("rate", "")
        # WHICH zone the colour paints, when one is wired to this Trigger's
        # own zone port. Without it the paint follows the zone the rule
        # happens to watch, which is right for "the bay went red because the
        # bay is crowded" and useless for anything else: a rule about a
        # doorway may well want the whole floor lit, and a count over the
        # frame watches no zone at all and so could paint nothing.
        painted = ""
        for edge, src in preds.get(n["id"], ()) or ():
            if str(edge.get("targetHandle") or "") != "zone" \
                    or src["type"] != "zone.ref":
                continue
            painted = str((src.get("data") or {}).get("zone") or "")
            if painted and painted not in zone_by_id:
                err(n["id"], "the zone wired into this Trigger no longer "
                             "exists - pick another on the Zones page")
                painted = ""
            break
        if painted:
            trig["zone_paint"] = painted
        if drawing:     # which boxes: the rule's classes, and the zone if ticked
            trigger_paint.narrow(trig, d, n["id"], preds, zone_by_id, warn)
        # ...and on which camera's picture (a branch rule names none).
        trigger_paint.stamp_camera(trig, upstream_camera(n["id"]), preds,
                                   n["id"], zone_by_id)
        # WHO is in the violation, when a rule's OID port is wired to this
        # Trigger's `oid` port. The rule it names is the one whose objects
        # the runtime watches; the Trigger's own moment may sit two branches
        # away from it, which is why the who is a wire of its own rather than
        # read off the moment.
        oid_source = ""
        oid_off = False
        for edge, src in preds.get(n["id"], ()) or ():
            if str(edge.get("targetHandle") or "") != "oid":
                continue
            oid_off = src["id"] in off_nodes
            if str(edge.get("sourceHandle") or "") != "OID":
                err(n["id"], "the wire on this Trigger's OID port does not "
                             "carry an object id - drag it from a rule's "
                             "OID output (tick OID on the rule first)")
                break
            if src["id"] in fn_meta:
                oid_source = str(fn_meta[src["id"]]["id"])
            elif src["id"] not in off_nodes:
                err(n["id"], "the rule wired into this Trigger's OID port "
                             "did not compile - fix it first")
            break
        if rate == "once_per_oid" and not oid_source:
            # Once per object reads the who from the rule upstream with OID
            # ticked; no wire is needed, the tick is what the panel offers on.
            who = trigger_out.oid_rule_upstream(
                n["id"], preds, lambda s: s["id"] in fn_meta or s["id"] in off_nodes)
            if who is not None and who["id"] in off_nodes:
                continue                    # a switched-off chain waits with it
            if who is None:
                err(n["id"], "this Trigger fires once per object, so it must "
                             "know which object - tick OID on the detection "
                             "rule it watches")
                continue
            oid_source = str(fn_meta[who["id"]]["id"])
        if oid_source:
            trig["oid_source"] = oid_source
        if rate:
            # How often is ONE question: the Advanced setting answers it for
            # the event and the payload alike, so the alarm's own "raise
            # again" is not a second answer beside it.
            trig["cooldown_s"] = int(extra.get("rate_s") or 0)
        if extra:
            trig.update(extra)
        # Re-raise per NEW OBJECT: an alarm about a crowd fires once for the
        # crowd and again for each person who joins it, and never again for
        # the people already in it. It has no meaning without knowing who,
        # so the OID wire is required rather than defaulted around.
        if alerting and not rate and str(d.get("repeat") or "cooldown") == "new_object":
            if not oid_source and oid_off:
                # The rule that would say who is there is switched off
                # (waiting for a zone, say), so this Trigger is part of a
                # switched-off chain: it waits with it, and the rule's own
                # warning already says what to do. An ERROR here refused the
                # whole canvas -- a People Counting created with blank zone
                # bindings landed with none of its rules running.
                continue
            if not oid_source:
                err(n["id"], "this Trigger re-raises per new object, so it "
                             "must be told who is there - tick OID on the rule "
                             "it watches and wire that OID port into this "
                             "Trigger's OID port")
                continue
            trig["repeat"] = "new_object"
        if trig["zone_color"] and not painted \
                and not (fn.get("params") or {}).get("zone"):
            # A colour with nothing to put it on. Said once, here, rather
            # than leaving a setting that silently does nothing.
            warn(n["id"], "this Trigger recolours a zone, but neither it nor "
                          "the rule wired into it names one - wire a Zone "
                          "into its Zone port")
        # The duplicate guard: what the table already holds for the object
        # (Current OID, Current value), read back off its columns. Nothing
        # wired adds nothing, and the trigger is exactly what it was.
        guard_docs, guard = _trigger_guard(n, preds.get(n["id"]) or [],
                                           tables, err, warn)
        if "cur_value_key" in guard \
                and any(got is fn for got in switch_meta.values()):
            # The value a Trigger carries is its moment's own: a Formula's
            # number, but a branch's yes (1). Compared with a stored
            # reading that is a difference of nothing in particular.
            warn(n["id"], "Current value is compared with the value this "
                          "Trigger carries, and a branch only carries 1 - "
                          "wire the Formula that works out the value into "
                          "the Trigger to compare real numbers")
        trig.update(guard)
        if d.get("wait_end"):
            trig["wait_end"] = True
            wait_nodes[fn["id"]] = n["id"]
        try:
            trig = functions_service._validate_trigger(trig, fn["type"], fn["name"])
        except (ValueError, TypeError) as exc:
            err(n["id"], str(exc))
            continue
        # TWO TRIGGERS ON ONE MOMENT.
        #
        # A rule carries exactly ONE trigger, and a second Trigger on the same
        # branch port used to overwrite the first: last one processed won, and
        # everything the other said (severity, message, colours) vanished with
        # no sign on the canvas that it ever had it.
        #
        # But drawing a second one is a REASONABLE thing to do, and operators
        # keep doing it: a Trigger is where outputs hang, and a second tap
        # point off one branch is how you fan out to a card here and a
        # Snapshot there without a wire crossing the canvas. So the question
        # is not "one or two nodes" but "do two of them DISAGREE".
        #
        # A Trigger that carries nothing of its own -- no alarm, no colours,
        # no message -- says nothing that could be thrown away. It relays the
        # moment: everything wired after it fires exactly as if it hung off
        # the first, and no second event is raised. Only a Trigger with real
        # settings conflicts, because then two answers exist for one question
        # and picking either would be inventing the operator's intent.
        rich = bool(alerting or drawing or d.get("zone_color") or d.get("wait_end")
                    or str(d.get("message") or "").strip() or painted or extra
                    or guard)
        owner_name, owner_rich = trigger_owner.get(id(fn), (None, False))
        if owner_name is not None and not rich:
            # A relay. It is not silently ignored -- the event log will carry
            # the OTHER Trigger's name, and that is worth knowing before
            # somebody goes looking for this one's.
            warn(n["id"], f"'{name}' relays the same moment as '{owner_name}' "
                          f"- everything wired after it fires with that one, "
                          f"and events are logged under that name")
            cond_meta[n["id"]] = fn
            continue
        if owner_name is not None and owner_rich:
            err(n["id"], f"'{name}' and '{owner_name}' both name the same "
                         f"moment and both carry settings - one rule raises "
                         f"one event, so one set (severity, message, colours) "
                         f"would be thrown away. Keep the settings on one of "
                         f"them; the other can stay as a plain tap point")
            cond_meta[n["id"]] = fn
            continue
        trigger_owner[id(fn)] = (name, rich)
        for q_doc in guard_docs:
            if q_doc["id"] not in seen_fids:
                seen_fids.add(q_doc["id"])
                functions.append(q_doc)
        # A branch rule arrives carrying the plain trigger _emit_first_match
        # gave it (fire whenever this branch holds, no alarm). This Trigger is
        # that same moment, now named and possibly alarmed -- so it REPLACES
        # that trigger rather than adding a second one, which would raise two
        # events for one thing happening.
        fn["trigger"] = trig
        fn.pop("triggers", None)
        cond_meta[n["id"]] = fn
        if extra:
            # What everything wired after this Trigger reads (trig_out_key).
            n["data"] = {**(n["data"] or {}), "_out_key": f"fn_{fn['id']}_out"}

    # Switched-off spreads along the chain -- Guard(off) -> When -> Alert --
    # so this repeats until nothing new goes off. One pass would depend on node
    # order and report the far end of a chain as broken.
    while True:
        spread = [n for n in pending
                  if n["id"] not in off_nodes and fed_by_off_node(n)]
        if not spread:
            break
        off_nodes.update(n["id"] for n in spread)
    for n in pending:
        if n["id"] in off_nodes:
            # Its function is switched off, so this condition is too. Saying
            # "needs a value wired in" here would be wrong twice over: one IS
            # wired in, and the operator already has the warning that explains
            # the real state.
            continue
        _fn, count = _cond_source(n)
        err(n["id"], f"{REGISTRY[n['type']]['label']} needs exactly one value "
                     f"wired in (has {count})")

    def upstream_delay(node_id, _seen=None):
        """Total seconds of Delay between this node and the rule feeding it.

        Delays ADD along a chain: "wait 5, then wait 10" is a fifteen-second
        wait, which is what wiring two of them in a row plainly says. Cycles
        cannot happen (the compiler rejects them) but the visited set keeps
        this total even if one ever did.
        """
        seen = set() if _seen is None else _seen
        total = 0.0
        for _, src in preds.get(node_id, ()):
            if src["id"] in seen:
                continue
            seen.add(src["id"])
            if src["type"] == "logic.delay":
                try:
                    total = max(total, float((src["data"] or {}).get("delay_s", 5))
                                + upstream_delay(src["id"], seen))
                except (TypeError, ValueError):
                    continue
            elif REGISTRY[src["type"]]["category"] == "logic" \
                    or src["type"] in _TRANSPARENT_OUTPUTS \
                    or src["type"] == "output.trigger":
                total = max(total, upstream_delay(src["id"], seen))
        return total

    def upstream_fn(node, handle=None, prefer_trigger=False):
        """The single analytics function feeding a sink node, or None.

        When, Alert and Delay nodes are transparent here: they publish no
        value of their own, so a card or output wired after one binds to the
        function whose trigger they define.

        `handle` restricts the search to one named input. A Dataset takes many
        wires at once -- one per column -- so "exactly one function feeds this
        node" is false there; its trigger arrives on a dedicated handle and
        only that handle may answer.

        `prefer_trigger` is for the MESSAGE outputs, which take many wires for
        a different reason: an Email or Telegram node may be wired to several
        rules so its text can quote all of their values ({people_count},
        {cycle_time}), while still firing on ONE of them. When the wires
        include exactly one thing that defines a trigger -- an Alert, a When,
        a switch branch -- that is unambiguously the one it fires on, and the
        plain analytics wires beside it are values. Without this the node
        reported "needs exactly one Analytics input" and the second wire had
        to be deleted, which also deleted the placeholder it existed for.
        """
        fns, triggers = [], []
        for edge, src in preds.get(node["id"], ()):
            if handle is not None and (edge.get("targetHandle") or "in") != handle:
                continue
            cat = REGISTRY[src["type"]]["category"]
            if cat in ("analytics", "variable"):
                got = fn_meta.get(src["id"])
                fns.append(got)
                # A SCHEDULE is a "when" as surely as a Condition is: the clock
                # reaching 12:00 is the decision, and it publishes no quantity
                # anybody would quote in a sentence. Without this the one shape
                # the node exists for -- the schedule that says when, the
                # counters that say what -- read as several ordinary values and
                # refused to compile: "wire ONE Condition into it", on a graph
                # whose Condition is the clock.
                # ...and so is an INJECT: the press is the decision.
                if got and got.get("type") in CLOCK_FN_METRIC:
                    triggers.append(got)
            elif is_branch(src):
                # WHICH branch it left by decides what it binds to -- that is
                # the whole point of a switch, so the handle is not optional.
                got = switch_meta.get(
                    (src["id"], edge.get("sourceHandle") or "out0"))
                fns.append(got)
                triggers.append(got)
            elif cat == "logic" or src["type"] in _TRANSPARENT_OUTPUTS                     or src["type"] == "output.trigger":
                # A Trigger IS the named moment, so a Snapshot or a card after
                # one binds to the rule it named -- the same pass-through the
                # Condition it replaces performed.
                got = cond_meta.get(src["id"])
                fns.append(got)
                triggers.append(got)
        fns = [f for f in fns if f]
        triggers = [f for f in triggers if f]
        # Exactly one thing that defines WHEN: unambiguous, whatever else is
        # wired in for its value. Ambiguity still fails -- two Alerts into one
        # Email is a question only the operator can answer.
        if prefer_trigger and len(triggers) == 1:
            return triggers[0]
        return fns[0] if len(fns) == 1 else None

    def out_key_of(src, f):
        """An Advanced Trigger's own output key when `src` is one naming
        rule `f`, else "": what it sends is its payload, not the rule's 0/1
        (services/trigger_out.py). Every other source answers "" and keeps
        the key it always had."""
        key = (src.get("data") or {}).get("_out_key")
        return key if (key and f and src.get("type") == "output.trigger"
                       and cond_meta.get(src["id"]) is f) else ""

    def trig_out_key(node, f, handle=None):
        """out_key_of over every wire into `node` (one port, with `handle`)."""
        for edge, src in preds.get(node["id"], ()):
            if handle is None or (edge.get("targetHandle") or "in") == handle:
                if out_key_of(src, f):
                    return out_key_of(src, f)
        return ""

    def fan_in_fns(node, handle=None):
        """EVERY rule wired into an output, in wire order, each once: the
        candidates upstream_fn calls ambiguous. A Trigger, When or Alert
        stands for the rule behind it, a branch for its port; the zone and
        OID ports name a place and a who, not a moment, and are skipped.
        `handle` keeps to one named input (a Modbus Write's value port)."""
        seen, out = set(), []
        for edge, src in preds.get(node["id"], ()):
            if str(edge.get("targetHandle") or "") in TRIGGER_SIDE_PORTS:
                continue
            if handle is not None and (edge.get("targetHandle") or "in") != handle:
                continue
            cat = REGISTRY[src["type"]]["category"]
            got = None
            if cat in ("analytics", "variable"):
                got = fn_meta.get(src["id"])
            elif is_branch(src):
                got = switch_meta.get(
                    (src["id"], edge.get("sourceHandle") or "out0"))
            elif cat == "logic" or src["type"] in _TRANSPARENT_OUTPUTS \
                    or src["type"] == "output.trigger":
                got = cond_meta.get(src["id"])
            if got and got["id"] not in seen:
                seen.add(got["id"])
                out.append(got)
        return out

    def event_query(node, data, announce=False):
        """The question a Card or Chart on "recorded events" asks of the
        event store (the retired Event Card asked it the same way).

        Returns (query, filters) separately, because the card finishes the
        query with a few decisions of its own before the two are joined.

        Shared by the card and by a CHART the card is wired into: one card,
        one question, whichever surface draws the answer -- a filter added to
        the card must not mean two different numbers on two surfaces.
        """
        q = {"agg": data.get("agg") or "count"}
        for key in ("field", "group_by", "bucket", "range"):
            if data.get(key):
                q[key] = data[key]
        filters = dict(data.get("filters") or {}) \
            if isinstance(data.get("filters"), dict) else {}
        # Free-form conditions on any recorded field: camera, zone, class,
        # severity, model... Only columns the event store actually has are
        # accepted -- a filter on a field that does not exist would either
        # error or, worse, be silently ignored and quietly count everything.
        from core.repos import events as events_repo
        for cond in (data.get("conditions") or [])[:8]:
            if not isinstance(cond, dict):
                continue
            field = str(cond.get("field") or "").strip()
            value = str(cond.get("value") or "").strip()
            if field in events_repo.FILTERABLE and value:
                filters[field] = value
        # Wire an analytics node in and the card counts only the events THAT
        # function raised, instead of every event of that type. The events
        # carry which function raised them (run.py detail.function, promoted
        # to a column in migration 0005), so this is a real filter rather than
        # a guess.
        src = upstream_fn(node)
        if not src and asks_recorded(node):
            # SEVERAL rules wired into a card that counts recorded events --
            # a Chart switched to "recorded events" keeps the wires it had as
            # series. upstream_fn calls that ambiguous and scopes nothing, so
            # the card quietly counted the whole store with every rule still
            # wired in. It counts what the FIRST raised (the plain port, then
            # wire order) and names the wires it is not counting.
            wired = []
            for edge, s in sorted(preds.get(node["id"], ()),
                                  key=lambda p: str(p[0].get("targetHandle")
                                                    or "in") != "in"):
                f = series_fn(edge, s)
                if f and all(f["id"] != w["id"] for w in wired):
                    wired.append(f)
            if wired:
                src = wired[0]
                if announce and len(wired) > 1:
                    warn(node["id"],
                         f"this card counts what '{src['name']}' raised - "
                         f"one card on recorded events is scoped by one "
                         f"rule, so the wire from "
                         + ", ".join(f"'{w['name']}'" for w in wired[1:])
                         + " is ignored; delete it, or give it a card of its "
                           "own")
        if src:
            filters["function"] = src["id"]
        return q, filters

    def series_key(edge, src, f):
        """The stat key one wire into a chart reads.

        A wire dropped on a NAMED metric port means that metric, same as
        everywhere else on the canvas.
        """
        handle = str(edge.get("sourceHandle") or "")
        src_spec = REGISTRY.get(src["type"]) or {}
        ms = node_metrics(src, src_spec) if src_spec else []
        metrics = functions_service.OUTPUTS.get(f["type"]) or ("value",)
        metric = handle if handle in (ms or ()) else metrics[0]
        return out_key_of(src, f) or f"fn_{f['id']}_{metric}"

    def series_fn(edge, src):
        """The function one wire into a chart stands for, or None.

        The same walk the several-rules chart has always done: analytics and
        variable nodes publish a value, a switch branch publishes the branch
        it left by, and a When/Alert/Trigger stands for the rule behind it.
        """
        cat = REGISTRY[src["type"]]["category"]
        if cat in ("analytics", "variable"):
            return fn_meta.get(src["id"])
        if is_branch(src):
            return switch_meta.get((src["id"],
                                    edge.get("sourceHandle") or "out0"))
        if (cat == "logic" or src["type"] in _TRANSPARENT_OUTPUTS
                or src["type"] == "output.trigger"):
            return cond_meta.get(src["id"])
        return None

    def series_ts_field(table):
        """The column a dataset series is drawn ALONG, or "" for the row's own
        clock.

        Blank is not a missing answer: every dataset row carries `ts`, the
        moment it was recorded, and that is what a dataset card already
        charts. A declared date/timestamp column is the operator saying the
        row is ABOUT a different moment than the one it was written at (a
        shift date, a due date), so it wins where there is one -- which is
        exactly the reading chart_series._entry gives a blank.
        """
        from core.repos import datasets as datasets_repo
        for col in table["columns"]:
            if col.get("type") in datasets_repo.TIME_TYPES \
                    or col.get("type") == "datetime":
                return col["name"]
        return ""

    def series_agg(agg):
        """One of the six aggregations a series may ask for.

        Narrower than the event store's own list on purpose: these are what
        builder.SERIES_AGGS names, and a series asking for "unique" or
        "changes" would be a question the series resolver cannot answer.
        "latest" is the same question as "last" under the dashboard's name
        for it.
        """
        return (agg if agg in builder_service.SERIES_AGGS
                else "last" if agg == "latest" else "count")

    def series_entry(sid, edge, src, by_value=False):
        """ONE series of a chart, from the wire that feeds it.

        Returns (entry, problem). The entry is the contract the server
        resolves a chart against -- id, label, colour, agg, and then whichever
        of key / query / dataset its SOURCE needs:

            history / stat  a live rule's stat key, over time or as it stands
            query           an Event Card's question about recorded events
            dataset         one column of a table the system records

        The WIRE is the only thing that says which. The chart draws what it is
        given, from wherever it came, and nobody picks a "source type"
        anywhere.

        `by_value` is the chart saying it compares CATEGORIES (a bar, donut,
        pie or radar whose every input is a table column): a TEXT column then
        counts each of its distinct values, one bar or slice per value --
        "how many of each defect type" -- rather than one total of rows.
        """
        sd = src["data"] or {}
        handle = str(edge.get("sourceHandle") or "")
        # A DATA TABLE COLUMN, off its read port -- the same wire that feeds a
        # single card its column, here as one line among several.
        if src["type"] == "output.dataset" and handle.startswith("o_"):
            column = handle[2:]
            key = _slug(sd.get("dataset"))
            table = (tables or {}).get(key)
            if not table:
                return None, ("the table this chart reads from does not exist "
                              "- create it on the Database page")
            kinds = {c["name"]: c.get("type") for c in table["columns"]}
            if column not in kinds:
                return None, (f"'{column}' is not a column of {key} any more "
                              "- rewire this series")
            # A WORD column on a category chart: each value it holds is a
            # category, and how many rows hold it is the value. Grouped by
            # the column itself, which the "One point per" row shows as its
            # default and can still change -- blank there means one total.
            # Words are the types that name a THING: not a number, a moment,
            # a nested json value or a picture path.
            words = by_value and kinds[column] in ("text", "boolean", "uuid")
            return {"id": sid,
                    "label": column.replace("_", " ")[:40],
                    "color": "",
                    # Only a number can be averaged; a label can only be
                    # counted, and Postgres would raise on the cast rather
                    # than be quietly wrong.
                    "agg": "avg" if kinds[column] == "number" else "count",
                    "source": "dataset",
                    "dataset": key,
                    # The column declaration travels WITH the series, for the
                    # reason it travels with a dataset WIDGET: it is the
                    # whitelist every name in the series is checked against,
                    # and the renderer must not have to come back to the
                    # canvas to find out what the table looks like.
                    "columns": [dict(c) for c in table["columns"]],
                    "field": column,
                    "ts_field": series_ts_field(table),
                    **({"query": {"group_by": column}} if words else {})}, ""
        # A CARD OR CHART ASKING RECORDED DATA (asks_recorded), the only node
        # that states a question about RECORDED events. Which of its modes it
        # is in decides what the series reads -- the card itself makes
        # exactly the same choice.
        if asks_recorded(src):
            mode = sd.get("source_mode") or "events"
            if mode == "dataset":
                key = _slug(sd.get("dataset"))
                table = (tables or {}).get(key)
                if not table:
                    return None, ("this series reads a table that does not "
                                  "exist - create it on the Database page, or "
                                  "pick another")
                kinds = {c["name"]: c.get("type") for c in table["columns"]}
                # THE CARD'S OWN QUESTION, carried whole -- what it works out,
                # what one bar is, over which period, and where -- the same
                # way an events card's question rides its series. Without it
                # every dataset card wired into a chart drew one all-time
                # total, whatever the card itself was grouped by.
                store_agg = (sd.get("agg") if sd.get("agg")
                             in builder_service.AGGREGATIONS else "count")
                query = {"agg": store_agg}
                if sd.get("group_by") in kinds \
                        and kinds[sd["group_by"]] != "json":
                    query["group_by"] = sd["group_by"]
                if sd.get("range") in builder_service.QUERY_RANGES:
                    query["range"] = sd["range"]
                cond = {}
                for c in (sd.get("conditions") or [])[:8]:
                    if isinstance(c, dict):
                        name = str(c.get("field") or "").strip()
                        value = str(c.get("value") or "").strip()
                        if name in kinds and value:
                            cond[name] = value
                if cond:
                    query["filters"] = cond
                if sd.get("bucket") and "group_by" not in query:
                    # A period per bar is the CHART's axis, shared by every
                    # line on it; one series cannot bring its own.
                    warn(src["id"], f"'per {sd['bucket']}' is this card's own "
                                    f"axis - wired into a chart, the chart's "
                                    f"axis decides the periods")
                return {"id": sid,
                        "label": str(sd.get("name") or table["name"])[:40],
                        "color": str(sd.get("color") or ""),
                        # How several store buckets in one chart cell fold:
                        # derived from the question (two counts add up).
                        "agg": chart_series._FOLD_FOR.get(store_agg, "last"),
                        "source": "dataset",
                        "dataset": key,
                        "columns": [dict(c) for c in table["columns"]],
                        "field": sd.get("field") if sd.get("field") in kinds
                                 else "",
                        "ts_field": series_ts_field(table),
                        "query": query}, ""
            ev_fn = upstream_fn(src)
            if mode == "metric" and ev_fn:
                # The card charts what its function PUBLISHES, so the series
                # reads the very stat key the card reads.
                metrics = functions_service.OUTPUTS[ev_fn["type"]]
                metric = (sd["metric"] if sd.get("metric") in metrics
                          else (wire_metric(src, ev_fn, metrics) or metrics[0]))
                return {"id": sid,
                        "label": str(sd.get("name") or ev_fn["name"])[:40],
                        "color": str(sd.get("color") or ""),
                        "agg": "avg",
                        "source": "history",
                        "key": trig_out_key(src, ev_fn)
                               or f"fn_{ev_fn['id']}_{metric}"}, ""
            q, filters = event_query(src, sd)
            if filters:
                q["filters"] = filters
            return {"id": sid,
                    "label": str(sd.get("name") or "events")[:40],
                    "color": str(sd.get("color") or ""),
                    "agg": series_agg(q.get("agg")),
                    "source": "query",
                    "query": q}, ""
        if src["type"] in ("dashboard.card", "dashboard.graph"):
            # Wired while it asked recorded data, switched back since: it
            # shows a live value now, which is the rule's to hand the chart.
            # A named line that is not drawn, not a graph that will not
            # compile.
            warn(src["id"], "this card shows the value wired into it, so it "
                            "has no question to give the chart it is wired "
                            "into - set its Source to recorded events or a "
                            "data table, or wire the rule into the chart "
                            "instead")
            return None, ""
        f = series_fn(edge, src)
        if not f:
            return None, ""
        return {"id": sid,
                "label": str(f["name"])[:40],
                "color": "",
                "agg": "avg",
                # Left for the caller: a comparison chart compares the latest
                # values (stat), the time shapes read each key's history. One
                # decision, taken once, for every key series on the chart.
                "source": "",
                "key": series_key(edge, src, f)}, ""

    def series_open_fields(entry, edge, src, chart_source):
        """Which of the per-series fields the WIRE leaves for the node to answer.

        Mirrors the Inputs list on the canvas (GraphSeriesRows in
        frontend/src/pages/Workflow.jsx) exactly, because a control the panel
        offers and the compiler ignores is worse than no control -- and one it
        hides while the compiler obeys it is a value nobody can see:

            a rule or a variable   the per-bucket fold, and only on a time
                                   shape (a comparison chart has no buckets)
            a Data Table column    the fold, what to group by, which period,
                                   and -- on a time shape -- which column puts
                                   a row in time
            a Card/Chart asking    its own question is already the answer, so
            recorded data          nothing, except in METRIC mode (a migrated
                                   Event Card) where it is charting a
                                   published value like any rule
        """
        handle = str(edge.get("sourceHandle") or "")
        if src["type"] == "output.dataset" and handle.startswith("o_"):
            open_fields = ["agg", "group_by", "range"]
            if chart_source == "history":
                open_fields.append("ts_field")
            return open_fields
        # Everything else is open only where it publishes a live value over
        # time: a card in events or dataset mode carries a question the
        # node cannot second-guess, and its entry's source says which it is.
        return ["agg"] if entry.get("source") == "history" else []

    def apply_series_conf(node, entry, cfg, open_fields):
        """The node's answers for this series, onto the entry the wire made.

        Written on the NODE (data.series[<id>]) rather than on the widget, for
        the reason the label and the colour are: a wf_* card is rebuilt from
        its node on every compile, so anything held only on the widget lasts
        until the next save.

        A field the wire FIXED is not applied, even when the node holds a
        value for it -- a chart rewired from a table column to a rule keeps
        the old row's group_by in its data, and honouring it would group a
        live key by a column that is not in the picture any more.
        """
        if "agg" in open_fields:
            want = str(cfg.get("agg") or "").strip()
            kinds = {c.get("name"): c.get("type")
                     for c in (entry.get("columns") or ())}
            # "" is a REAL answer: "keep the guess the compiler makes from the
            # column type". It is stored by being absent, so there is exactly
            # one way to say it (see clean_card_series).
            if want in ("avg", "sum", "min", "max", "last") \
                    and entry.get("field") \
                    and kinds.get(entry["field"]) not in (None, "number"):
                # A word has no total or average -- the store refuses the
                # cast and the line draws nothing -- and its "latest" is a
                # word no axis can plot. Counted instead, and said, the same
                # rule the single-column card applies.
                warn(node["id"], f"'{entry['field']}' is text, so it can only "
                                 f"be counted - that line counts rows instead "
                                 f"of '{want}'")
            elif want and want in builder_service.SERIES_AGGS:
                entry["agg"] = want
            elif want:
                warn(node["id"], f"'{want}' is not something a chart can work "
                                 f"out - this line keeps its default instead")
        if "ts_field" in open_fields and "ts_field" in cfg:
            want = str(cfg.get("ts_field") or "").strip()
            kinds = {c.get("name"): c.get("type")
                     for c in (entry.get("columns") or ())}
            if not want:
                # Blank is the operator choosing the ROW CLOCK -- the moment
                # the row was recorded -- over the date column the compiler
                # picks by itself. An answer, not a missing one.
                entry["ts_field"] = ""
            elif kinds.get(want) in datasets_repo.TIME_TYPES \
                    or kinds.get(want) == "datetime":
                entry["ts_field"] = want
            else:
                # NOT blanked: a series drawn against a column that was
                # deleted or retyped must keep drawing, on the clock every row
                # carries, and say why. Blanking the series instead is a chart
                # that loses a line and names nothing.
                warn(node["id"],
                     f"'{want}' is not a date column of this table any more - "
                     f"that line is drawn on the clock each row arrived on "
                     f"until it is picked again")
        raw = cfg.get("query") if isinstance(cfg.get("query"), dict) else {}
        query = {}
        if "group_by" in open_fields and "group_by" in raw:
            want = str(raw.get("group_by") or "").strip()
            kinds = {c.get("name") for c in (entry.get("columns") or ())
                     if c.get("type") != "json"}
            if want and want in kinds:
                query["group_by"] = want
            elif want:
                warn(node["id"],
                     f"'{want}' is not a column of this table any more - that "
                     f"line follows the chart's own axis until it is picked "
                     f"again")
            elif (entry.get("query") or {}).get("group_by"):
                # Blank over a text column's own grouping (series_entry
                # by_value): the operator choosing ONE total over one bar per
                # value. Dropped, not merged -- a merge cannot remove a key.
                entry["query"] = {k: v for k, v in entry["query"].items()
                                  if k != "group_by"}
                if not entry["query"]:
                    del entry["query"]
        if "range" in open_fields and "range" in raw:
            want = str(raw.get("range") or "").strip()
            if want and want in builder_service.QUERY_RANGES:
                query["range"] = want
        # Only when there is something to say. A `query` of two empty strings
        # resolves exactly as no query at all (chart_series._entry), so
        # writing one would change every already-compiled chart's artifacts
        # to mean the same thing.
        if query:
            entry["query"] = {**(entry.get("query") or {}), **query}

    def chart_axis(node_data, entry, column_wire):
        """The CHART's own "one point per period" onto one store series.

        A chart wired to one table column has always asked that column its
        own question -- what to work out, one bar per day, over which period
        -- and the inspector keeps those answers on the node (ColumnLogic).
        The second wire switched the chart to a series list, which read none
        of them: the bar chart fell back to one all-time total per status
        word and the line chart to the sampler's grid, so the owner's "Total
        Violation (Day/Days)" stopped being per day the moment camera 2 was
        wired in. The period is the CHART's axis, shared by every line on it,
        so it goes onto every table or event series that is not grouped by a
        column of its own; for a table column the chart's fold and period go
        with it, as defaults the series row can still override.

        Only when a period is set: a chart without one compiles exactly as
        it always did.
        """
        bucket = node_data.get("bucket")
        if bucket not in builder_service.BUCKETS \
                or entry.get("source") not in ("dataset", "query"):
            return
        q = dict(entry.get("query") or {})
        if column_wire:
            # The period wins over the "count each word" default, the same
            # order the single-column chart applies (a bucket before a group).
            q.pop("group_by", None)
            kinds = {c.get("name"): c.get("type")
                     for c in (entry.get("columns") or ())}
            want = node_data.get("agg")
            want = "last" if want == "latest" else want
            if want in builder_service.SERIES_AGGS:
                if want in ("sum", "avg", "min", "max") \
                        and kinds.get(entry.get("field")) != "number":
                    want = "count"
                entry["agg"] = want
        elif q.get("group_by"):
            return              # a card grouped by a column keeps its groups
        q["bucket"] = bucket
        period = _QUERY_RANGE_OF.get(node_data.get("range"),
                                     node_data.get("range"))
        if not q.get("range") and period in builder_service.QUERY_RANGES:
            q["range"] = period
        entry["query"] = q

    def settle_axis(entry, column_wire):
        """After the series row has had its say: a row grouped by a column of
        its own is a label set, not a line along the chart's periods, so it
        drops the period; and "latest" is no per-period answer (the store
        would count rows), so a table column reads the period's largest
        value -- the single-column chart's rule."""
        q = entry.get("query") or {}
        if not q.get("bucket"):
            return
        if q.get("group_by"):
            entry["query"] = {k: v for k, v in q.items() if k != "bucket"}
            return
        if column_wire and entry.get("agg") == "last":
            kinds = {c.get("name"): c.get("type")
                     for c in (entry.get("columns") or ())}
            entry["agg"] = ("max" if kinds.get(entry.get("field")) == "number"
                            else "count")

    def x_field_window(node_data, entry):
        """An input with its OWN X field ("One point per <column>") is drawn
        along that column, not along time -- but the chart's Time range is a
        date FILTER, independent of the axis, so it still narrows the rows
        (services/timeaxis.py precedence). Only a range the operator set:
        a graph's stored default "24h" with no step was never a filter on a
        table input, and a saved chart must not start being one."""
        q = entry.get("query") or {}
        if entry.get("source") not in ("dataset", "query")                 or not q.get("group_by") or q.get("range"):
            return
        rng = node_data.get("range")
        if not rng or (rng == "24h" and not node_data.get("bucket")):
            return
        rng = _QUERY_RANGE_OF.get(rng, rng)
        if rng in builder_service.QUERY_RANGES:
            entry["query"] = {**q, "range": rng}

    def model_step(node_data, kind):
        """The step a chart under the builder model groups its store inputs
        by, when X is Time. The stored step, or -- left Automatic -- none on a
        graph (the shared sampler grid sizes itself, as before) and the
        legacy "an hour, a day once the range is days" on a bar, which has no
        grid and would otherwise draw one bar."""
        step = _timeaxis.clean_step(node_data.get("bucket"))
        if step:
            return step
        if kind in _chartmodel.GRAPHS:
            return ""
        lo, hi = _timeaxis.window(node_data.get("range") or "24h",
                                  range_from=node_data.get("range_from"),
                                  range_to=node_data.get("range_to"))
        days = (hi - lo) / 86400 if lo is not None else 7
        return "1d" if days >= 2 or node_data.get("range") in (
            "page", "week", "month", "") else "1h"

    def apply_chart_model(node, node_data, entry, column_wire, conf, kind):
        """THE CHART BUILDER MODEL onto one compiled input
        (services/chartmodel.py) -- used instead of chart_axis,
        apply_series_conf, settle_axis and x_field_window for a chart saved
        with `x_type`.

        Every input shares the chart's X axis, Y calculation, date range and
        Date column; an input's own override (data.series[<sid>]) wins where
        it has one. A Card or Chart wired in keeps its OWN question -- it is
        the source, as before -- and only takes the chart's step and range
        where it states none.

        Each input stays its own entry with its own table or key: nothing
        here moves a value from one input to another (series isolation).
        """
        conf = conf if isinstance(conf, dict) else {}
        cq = conf.get("query") if isinstance(conf.get("query"), dict) else {}
        x_type = node_data.get("x_type")
        if conf.get("show") is False:
            entry["show"] = False
        source = entry.get("source")
        if source not in ("dataset", "query"):
            # A live rule (history / stat): the Y calculation is how the
            # samples in one step become one point.
            want = str(conf.get("agg") or "").strip() \
                or _chartmodel.fold_of(node_data.get("agg") or "")
            want = "count" if want == "count_nonnull" else want
            if want in builder_service.SERIES_AGGS:
                entry["agg"] = want
            return
        kinds = {c.get("name"): c.get("type") for c in (entry.get("columns") or ())}
        q = dict(entry.get("query") or {})
        label = entry.get("label") or "this input"
        own_question = not column_wire          # a Card/Chart's question
        # -- X ---------------------------------------------------------------
        group = ""
        own_group = str(cq.get("group_by") or "").strip() if column_wire else ""
        if column_wire:
            q.pop("group_by", None)             # the model decides, not by_value
        if own_group:
            if own_group in kinds and kinds[own_group] != "json":
                group = own_group
            else:
                warn(node["id"], f"'{own_group}' is not a column of {label}'s "
                                 f"table any more - that input follows the "
                                 f"chart's X axis")
        elif own_question and q.get("group_by"):
            group = q["group_by"]               # the card's own grouping
        elif x_type == "column" and node_data.get("group_by"):
            xf = node_data["group_by"]
            from core.repos import events as events_repo
            ok = (xf in events_repo.GROUPABLE if source == "query"
                  else xf in kinds and kinds[xf] != "json")
            if ok:
                group = xf
            else:
                warn(node["id"], f"{label} has no column '{xf}' - it is drawn "
                                 f"as one value")
        if group:
            q["group_by"] = group
            q.pop("bucket", None)
        else:
            q.pop("group_by", None)
            step = model_step(node_data, kind) if x_type == "time" else ""
            if step and not (own_question and q.get("bucket")):
                q["bucket"] = step
            elif not step and not own_question:
                q.pop("bucket", None)
        # -- date range -------------------------------------------------------
        own_range = str(cq.get("range") or "").strip() if column_wire else ""
        rng = own_range or node_data.get("range") or ""
        step_s = _timeaxis.step_seconds(q.get("bucket")) or 0
        if step_s >= 86400 and rng in _QUERY_RANGE_OF:
            # Whole days along "Past week" / "Past month": the day-aligned
            # window (today and the 6 / 29 days before it, from midnight), so
            # the first day on the axis is a whole day -- the reading the
            # per-day chart always had (chart_axis).
            rng = _QUERY_RANGE_OF[rng]
        if own_question and q.get("range"):
            pass                                # the card's own period
        elif rng and rng in builder_service.QUERY_RANGES:
            q["range"] = rng
        else:
            q.pop("range", None)
        # -- Y ----------------------------------------------------------------
        if column_wire:
            number = kinds.get(entry.get("field")) == "number"
            want = str(conf.get("agg") or "").strip() \
                or _chartmodel.fold_of(node_data.get("agg") or "")
            q.pop("agg", None)
            if want == "count_nonnull":
                q["agg"] = "count_nonnull"
                entry["agg"] = "sum"            # store counts add up per cell
            elif want == "unique":
                # Count Difference Value: COUNT(DISTINCT) of the input's own
                # column, any type; never added up across buckets.
                q["agg"] = "unique"
                entry["agg"] = "unique"
            elif want == "changes":
                # Count Value Changes: how many times the input's own column
                # took a new value, in time order; each change belongs to one
                # row, so store counts in one cell add up.
                q["agg"] = "changes"
                entry["agg"] = "sum"
            elif want in ("sum", "avg", "min", "max", "last") and not number:
                warn(node["id"], f"'{entry.get('field')}' is text, so it can "
                                 f"only be counted - {label} counts rows "
                                 f"instead of '{want}'")
                entry["agg"] = "count"
            elif want in builder_service.SERIES_AGGS:
                entry["agg"] = want
            elif want:
                warn(node["id"], f"'{want}' is not something a chart can work "
                                 f"out - {label} keeps its default")
            if q.get("bucket") and entry.get("agg") == "last":
                # "latest" is no per-step answer: the step's largest value.
                entry["agg"] = "max" if number else "count"
        # -- Date column ------------------------------------------------------
        if kinds:
            times = [c for c, t in kinds.items()
                     if t in datasets_repo.TIME_TYPES or t == "datetime"]
            own_ts = conf.get("ts_field") if column_wire and "ts_field" in conf \
                else None
            chart_ts = node_data.get("ts_field") or ""
            if own_ts is not None:
                if own_ts == "" or own_ts in times:
                    entry["ts_field"] = own_ts
                else:
                    warn(node["id"], f"'{own_ts}' is not a date column of "
                                     f"{label}'s table any more - drawn on the "
                                     f"time each row was saved")
                    entry["ts_field"] = ""
            elif chart_ts == _chartmodel.ROW_CLOCK:
                entry["ts_field"] = ""
            elif chart_ts and chart_ts in times:
                entry["ts_field"] = chart_ts
            elif chart_ts:
                warn(node["id"], f"{label}'s table has no date column "
                                 f"'{chart_ts}' - its own date column is used")
            if entry.get("ts_field"):
                # The Date column steps AND filters this input on every path
                # (chart_series honours it only when told, so charts saved
                # before the model keep drawing on the saved time there).
                entry["by_ts_field"] = True
        if q:
            entry["query"] = q
        else:
            entry.pop("query", None)

    def series_table_name(src_node):
        """What a Data Table input is called on a legend: the node's own name,
        else its table's, else the table key."""
        sd = src_node.get("data") or {}
        key = _slug(sd.get("dataset"))
        return str(sd.get("name") or ((tables or {}).get(key) or {}).get("name")
                   or key or "").strip()[:40]

    def series_slots(node):
        """Which wire draws which series.

        Returns ({slot: (edge, src)}, wires with nowhere to go, the slots that
        were asked for BY NAME).

        A wire on a NAMED series port means that series -- that is what the
        named ports are for, and it is what keeps a colour attached to a line
        when the wiring around it changes. Everything else lands on `in`,
        which is series 1 and is also where a chart drawn before the ports
        existed has ALL of its wires: those fill the free slots in wire order,
        so a graph saved yesterday draws exactly the lines it drew yesterday.
        """
        slots, spare, named = {}, [], set()
        for edge, src in preds.get(node["id"], ()):
            handle = str(edge.get("targetHandle") or "in")
            # "s1" is `in` under another name. Nothing mints it, but a graph
            # written by hand (or by a template) may well say it.
            if handle == "s1":
                handle = "in"
            if handle in GRAPH_SERIES_HANDLES[1:] \
                    and GRAPH_SERIES_HANDLES.index(handle) + 1 not in slots:
                slots[GRAPH_SERIES_HANDLES.index(handle) + 1] = (edge, src)
                named.add(GRAPH_SERIES_HANDLES.index(handle) + 1)
            else:
                spare.append((edge, src))
        over = 0
        for edge, src in spare:
            free = next((i for i in range(1, len(GRAPH_SERIES_HANDLES) + 1)
                         if i not in slots), None)
            if free is None:
                over += 1
                continue
            slots[free] = (edge, src)
        return slots, over, named

    # -- dashboard nodes -> widgets ------------------------------------------
    widgets = []
    # Two Queries called the same thing publish two cards called the same
    # thing, which puts the operator back where an unnamed one did: picking
    # between identical rows in the editor and dragging one out to find out
    # which it was. Caught here rather than left to whoever reads the
    # dashboard later.
    _query_names = {}
    for n in graph["nodes"]:
        if n["type"] != "storage.query":
            continue
        # Compared LOOSELY: case, spacing and punctuation are not what tells
        # two cards apart in a list, so "By shift" and "by  Shift" are the
        # same clash. _slug is not enough on its own -- it keeps runs of
        # separators, so a double space would read as a different name.
        name = re.sub(r"[^a-z0-9]+", " ",
                      str((n["data"] or {}).get("name") or "").lower()).strip()
        if not name:
            continue                    # the "needs a name" error covers it
        if name in _query_names:
            err(n["id"], "another Query on this canvas is already called "
                         f"'{(n['data'] or {}).get('name')}' - rename one, or "
                         "their cards cannot be told apart in the editors")
        _query_names[name] = n["id"]
    for n in graph["nodes"]:
        spec = REGISTRY[n["type"]]
        if spec["category"] != "dashboard":
            continue

        # An event card reads the PERSISTED event store, not a live stat --
        # "how many violations today, grouped by camera" is a question about
        # history, which no analytics node answers. Its input is therefore
        # OPTIONAL: unconnected it counts every matching event; wire a variable
        # in and it counts only the ones that variable raised.
        d = n["data"] or {}
        # SEVERAL SERIES ON ONE CHART -- one per wired input, from ANY source.
        #
        # The chart has drawn a series per wired rule for a long time. What it
        # never had was a way to SAY so before the wires were drawn: one
        # unnamed port, and a note under the node that appeared after the
        # second wire was already on it. It also only ever counted RULES, so
        # "violations per hour beside the shift target out of the database"
        # was two cards that could not share an axis.
        #
        # So: a port per series, and every wire judged on its own -- a rule
        # publishes a stat key, a Card/Chart on recorded data carries its
        # question about it, a Data Table column carries the column. The chart
        # draws what it is given, and the compiled entry says which kind of
        # thing each line is (see series_entry).
        #
        # Taken BEFORE the single-source paths below, which each resolve ONE
        # wire and stop at the first they find. A chart with one wire never
        # reaches this branch at all, so every chart already in the field
        # compiles exactly as it did.
        # A Chart asking recorded data draws ITS question, like the Event Card
        # it replaces: a rule wired into it scopes that question to what the
        # rule raised, it is not a second line.
        if n["type"] == "dashboard.graph" and not asks_recorded(n):
            slots, over, named_slots = series_slots(n)
            # A wire on a NAMED series port is the operator saying "this
            # line", so it makes a series chart even on its own. A single wire
            # on the plain port is the old one-value chart, untouched.
            # ...and so does a Card or Chart wired in, even on the plain port:
            # it hands the chart a QUESTION, which only the series list can
            # carry. The one-value path below would ask it for a metric and
            # answer "needs exactly one Analytics input" of a wired chart.
            fed_cards = [s for _e, s in slots.values()
                         if REGISTRY[s["type"]]["category"] == "dashboard"]
            explicit = bool(named_slots) or bool(fed_cards)
            # A chart saved under the builder model draws its table inputs
            # as series even when there is only one: one path, one set of
            # rules for X, Y, the range and the Date column.
            modelled = d.get("x_type") in _chartmodel.X_TYPES
            if modelled and any(
                    s["type"] == "output.dataset"
                    and str(e.get("sourceHandle") or "").startswith("o_")
                    for e, s in slots.values()):
                explicit = True
            kind = _card_kind(n, d)
            chartable = (builder_service.TIMESERIES_KINDS
                         + builder_service.COMPARISON_KINDS)
            if kind in chartable and (len(slots) > 1 or explicit):
                # Comparison shapes compare the latest values; the time shapes
                # read each key's history. Decided once for the whole chart --
                # a pie of "now" and a line of "since midnight" cannot share
                # one set of axes.
                source = ("stat" if kind in builder_service.COMPARISON_KINDS
                          else "history")
                if modelled and kind == "bar" and d.get("x_type") == "time":
                    # A bar along Time reads each rule's history, like a
                    # graph (the brief: a bar chart may use time as X).
                    source = "history"
                conf = d.get("series") if isinstance(d.get("series"), dict) else {}
                series, seen_fns, stop = [], set(), False
                auto_named = []         # table-column lines named by column
                # CATEGORIES FROM WORDS. Only when EVERY input is a table
                # column: one bar per distinct value is a label set, and a
                # live rule beside it has one number and no labels to line up
                # with -- it would draw as an empty series with a warning.
                by_value = not modelled \
                    and kind in builder_service.COMPARISON_KINDS and all(
                    s["type"] == "output.dataset"
                    and str(e.get("sourceHandle") or "").startswith("o_")
                    for e, s in slots.values())
                for slot in sorted(slots):
                    edge, src = slots[slot]
                    # The SAME rule wired in twice -- typically once on its
                    # named out port and once on the plain one, two wires that
                    # overlap on the canvas and read as one. A duplicate, not
                    # a second line. Only for wires that landed on the plain
                    # port: dropping a rule on its own named series port is
                    # the operator asking for that line on purpose.
                    if slot not in named_slots:
                        dup_fn = series_fn(edge, src)
                        if dup_fn and dup_fn["id"] in seen_fns:
                            continue
                        if dup_fn:
                            seen_fns.add(dup_fn["id"])
                    entry, problem = series_entry(f"s{slot}", edge, src,
                                                  by_value=by_value)
                    if problem:
                        err(n["id"], problem)
                        stop = True
                        break
                    if not entry:
                        continue
                    if not entry["source"]:
                        entry["source"] = source
                    column_wire = (src["type"] == "output.dataset"
                                   and str(edge.get("sourceHandle") or "")
                                   .startswith("o_"))
                    if modelled:
                        named = conf.get(f"s{slot}")
                        apply_chart_model(n, d, entry, column_wire, named, kind)
                        if isinstance(named, dict) and str(
                                named.get("label") or "").strip():
                            entry["label"] = str(named["label"]).strip()[:40]
                        elif column_wire:
                            auto_named.append((entry, src))
                        if isinstance(named, dict) and str(
                                named.get("color") or "").strip():
                            entry["color"] = str(named["color"]).strip()[:24]
                        if isinstance(named, dict) \
                                and builder_service.clean_bands(named.get("bands")):
                            # This camera's own thresholds.
                            entry["bands"] = builder_service.clean_bands(named["bands"])
                        series.append(entry)
                        continue
                    chart_axis(d, entry, column_wire)
                    # WHAT THIS LINE IS CALLED AND WHAT COLOUR IT DRAWS IN,
                    # off the node. Held per series id, so it survives the
                    # wire being replaced by another on the same port -- and
                    # held on the NODE, because a wf_* widget is rebuilt from
                    # its node on every compile and a colour living only on
                    # the widget would last until the next save.
                    named = conf.get(f"s{slot}")
                    if isinstance(named, dict):
                        if str(named.get("label") or "").strip():
                            entry["label"] = str(named["label"]).strip()[:40]
                        if str(named.get("color") or "").strip():
                            entry["color"] = str(named["color"]).strip()[:24]
                        if builder_service.clean_bands(named.get("bands")):
                            # This camera's own thresholds.
                            entry["bands"] = builder_service.clean_bands(named["bands"])
                        # ...and the QUESTION the wire left open: the fold, and
                        # for a table column what to group by, over which
                        # period, along which column. Same place, same reason.
                        apply_series_conf(
                            n, entry, named,
                            series_open_fields(entry, edge, src, source))
                    settle_axis(entry, column_wire)
                    x_field_window(d, entry)
                    if column_wire and not (isinstance(named, dict) and str(
                            named.get("label") or "").strip()):
                        auto_named.append((entry, src))
                    series.append(entry)
                # Two inputs that read the SAME column of two tables -- one
                # Data Table per camera, both wired from `status` -- were both
                # called "status", and the legend could not say which camera
                # was which. Where the column name does not tell the lines
                # apart, the table node's own name does. A name the operator
                # typed on the series row is never touched.
                clash = {}
                for entry, _src in auto_named:
                    clash[entry["label"]] = clash.get(entry["label"], 0) + 1
                for entry, src_node in auto_named:
                    if clash[entry["label"]] > 1:
                        entry["label"] = series_table_name(src_node) \
                            or entry["label"]
                if stop:
                    continue
                if over:
                    warn(n["id"], f"a chart draws at most "
                                  f"{builder_service.MAX_SERIES} series - "
                                  "the extra wires are ignored")
                # One line left after the duplicates went: that is the
                # one-value chart the paths below already draw, and its
                # "wired twice" warning belongs to them.
                if fed_cards and not series:
                    # Every wire is a card with no question to hand over
                    # (series_entry named each). Off until one has one, rather
                    # than a misleading "needs an Analytics input" error.
                    warn(n["id"], "the card wired into this chart shows a "
                                  "live value, so there is nothing to draw - "
                                  "set that card's Source to recorded events "
                                  "or a data table; this chart is switched "
                                  "off until then")
                    off_nodes.add(n["id"])
                    continue
                if len(series) > 1 or (explicit and series):
                    w = {"id": f"wf_{n['id']}"[:40],
                         # One table input under the builder model: the card
                         # is called what its one line is, as the one-wire
                         # path always called it.
                         "title": str(d.get("name")
                                      or (series[0]["label"] if modelled
                                          and len(series) == 1 else "")
                                      or spec["label"])[:60],
                         "kind": kind,
                         "template": _template(d),
                         **_text_sizes(d),
                         "anim": _anim(d),
                         "anim_replay": bool(d.get("anim_replay")),
                         "bands": _bands(d),
                         "by_day": _by_day(d),
                         "bg": _bg(d),
                         "source": source,
                         "series": series,
                         "span": 1}
                    if source == "history":
                        # "week"/"month" are the Period words a chart keeps
                        # from its single-input days (ColumnLogic); on the
                        # history axis they are 7d/30d, not "24h".
                        hist_range = _HISTORY_RANGE_OF.get(d.get("range"),
                                                           d.get("range"))
                        w["range"] = (hist_range
                                      if hist_range in builder_service.HISTORY_RANGES
                                      else "24h")
                    if d.get("color"):
                        w["color"] = d["color"]
                    if d.get("unit"):
                        w["unit"] = d["unit"]
                    if d.get("sub"):
                        w["sub"] = str(d["sub"])[:60]
                    widgets.append(w)
                    continue
        # Wired to a COLUMN's read port: the card shows what that column
        # actually holds. This is the read half of the Data Table node --
        # `○──── confidence ────○` into a gauge -- and it applies to EVERY
        # card, not just the Event Card: a column is a value, and any card
        # that can show a value can show it.
        col_src, table_src, query_src, stale_cols = None, None, None, []
        for edge, src_node in preds.get(n["id"], ()):
            handle = str(edge.get("sourceHandle") or "")
            if src_node["type"] == "storage.query":
                query_src = src_node
                break
            if src_node["type"] != "output.dataset":
                continue
            if handle == "table":
                table_src = src_node
                break
            if handle.startswith("o_"):
                # A wire to a column that was RENAMED since it was drawn is a
                # leftover, not the operator's intent -- when a live column is
                # also wired in, that one wins. Erroring on the leftover froze
                # the WHOLE dashboard the moment a column was renamed, even
                # after the operator had already wired the new name.
                src_key = _slug((src_node["data"] or {}).get("dataset"))
                names = {c["name"] for c in
                         ((tables or {}).get(src_key) or {}).get("columns") or []}
                if handle[2:] in names:
                    col_src = (src_node, handle[2:])
                    break
                stale_cols.append(handle[2:])
        if stale_cols and col_src:
            warn(n["id"], f"an old wire still points at "
                          f"'{stale_cols[0]}', which is not a column any "
                          f"more - using '{col_src[1]}'; delete the stale "
                          "wire to clear this")
        elif stale_cols and not (table_src or query_src):
            # EVERY wire names a gone column: the card waits, switched off,
            # rather than freezing the rest of the dashboard behind an error.
            warn(n["id"], f"'{stale_cols[0]}' is not a column of the table "
                          "any more - rewire this card to a current column; "
                          "it is switched off until then")
            off_nodes.add(n["id"])
            continue

        # Wired to a Query: the statement decides what the card shows, so the
        # card contributes only how it is DRAWN. Nothing here inspects the
        # SQL -- it is read by the system's read-only role against its own
        # views (services/sqlconsole.py), and shaped positionally at render
        # time (builder._sql_data), because a hand-written query has no
        # schema to introspect.
        if query_src:
            qd = query_src["data"] or {}
            # A name is REQUIRED, not decorative. The Dashboard and Analytics
            # editors list what a graph publishes by title, and a canvas with
            # three unnamed queries publishes three cards called "Query" --
            # findable only by dragging each one out to see what it was. The
            # statement cannot stand in: it is several lines long, and the two
            # you would tell apart usually differ in the middle.
            if not str(qd.get("name") or "").strip():
                err(query_src["id"],
                    "give this Query a name - it is how you find its card in "
                    "the Dashboard and Analytics editors")
                continue
            statement = str(qd.get("sql") or "").strip().rstrip(";").strip()
            if not statement:
                err(query_src["id"], "this Query has no statement - write a "
                                     "SELECT in the inspector")
                continue
            if not statement.lower().startswith(("select", "with")):
                err(query_src["id"], "a Query may only SELECT - it reads the "
                                     "database, it never changes it")
                continue
            # The tables wired INTO the query are the ones it may read. No
            # wire means no view exists for it to name, which reads as a
            # confusing database error unless it is caught here.
            wired_tables = []
            for edge, src_node in preds.get(query_src["id"], ()):
                if src_node["type"] != "output.dataset":
                    continue
                key = _slug((src_node["data"] or {}).get("dataset"))
                table = (tables or {}).get(key)
                if table and not any(t["key"] == key for t in wired_tables):
                    wired_tables.append({"key": key,
                                         "name": table.get("name") or key,
                                         "columns": [dict(c) for c in
                                                     table["columns"]]})
            if not wired_tables:
                err(query_src["id"], "nothing is wired into this Query - "
                                     "drag a Data Table onto its table port "
                                     "to say which tables it may read")
                continue
            widgets.append({
                "id": f"wf_{n['id']}"[:40],
                # The card may override the title, but the Query's name is
                # what it falls back to -- which is the point of requiring one.
                "title": str(d.get("name") or qd.get("name"))[:60],
                "kind": _card_kind(n, d),
                "source": "sql",
                "sql": statement,
                "tables": wired_tables,
                "span": 2 if _card_kind(n, d) == "table" else 1,
                "limit": int(d.get("limit") or 15),
                "unit": str(d.get("unit") or "")[:12],
                "color": d.get("color") or "",
                # The card's LOOK, from the node, exactly as every other
                # compiled card carries it. Left off here, a Query or table
                # card took a template, an accent or a text size in the
                # Builder and rendered plain again the moment anything
                # recompiled -- the control appeared to work and then forgot.
                "template": _template(d),
                **_text_sizes(d),
                "anim": _anim(d),
                "anim_replay": bool(d.get("anim_replay")),
                "bands": _bands(d),
                "by_day": _by_day(d),
                "bg": _bg(d),
                # The period belongs to the QUERY, not to the card: the same
                # statement drawn twice should ask about the same window, and
                # a Telegram button on it asks about that window too.
                "query": {"range": str(qd.get("range") or "")[:12]},
            })
            continue

        # Wired to the table ITSELF rather than to one of its columns: the
        # card shows the rows. Which columns appear is picked on the card,
        # because that is a question about the card -- the same table is worth
        # showing two ways on two dashboards.
        if table_src:
            key = _slug((table_src["data"] or {}).get("dataset"))
            table = (tables or {}).get(key)
            if not table:
                err(n["id"], "the table this card reads from does not exist "
                             "- create it on the Database page")
                continue
            picked = [c for c in (d.get("show") or []) if isinstance(c, str)]
            columns = [c for c in table["columns"] if c["name"] in picked] \
                if picked else list(table["columns"])
            if not columns:
                err(n["id"], "no columns are ticked for this table card - "
                             "pick at least one in the inspector")
                continue
            widgets.append({
                "id": f"wf_{n['id']}"[:40],
                "title": str(d.get("name") or table.get("name") or key)[:60],
                # A table, whatever shape the card would otherwise draw: rows
                # are not a gauge, and silently rendering one as a number
                # would be a wrong answer rather than a missing one.
                "kind": "table",
                "source": "dataset",
                "dataset": key,
                # The ticked columns ARE the declaration this widget carries,
                # so they are also its query whitelist -- narrower than the
                # table's, which is the safe direction.
                "columns": columns,
                "span": 2,
                "limit": int(d.get("limit") or 15),
                "color": d.get("color") or "",
                # The card's LOOK, from the node, exactly as every other
                # compiled card carries it. Left off here, a Query or table
                # card took a template, an accent or a text size in the
                # Builder and rendered plain again the moment anything
                # recompiled -- the control appeared to work and then forgot.
                "template": _template(d),
                **_text_sizes(d),
                "anim": _anim(d),
                "anim_replay": bool(d.get("anim_replay")),
                "bands": _bands(d),
                "by_day": _by_day(d),
                "bg": _bg(d),
                "query": {"agg": "count",
                          **({"range": d["range"]}
                             if d.get("range") in builder_service.QUERY_RANGES else {})},
            })
            continue

        if col_src:
            src_node, column = col_src
            key = _slug((src_node["data"] or {}).get("dataset"))
            table = (tables or {}).get(key)
            if not table:
                err(n["id"], "the table this card reads from does not exist "
                             "- create it on the Database page")
                continue
            kinds = {c["name"]: c.get("type") for c in table["columns"]}
            if column not in kinds:
                err(n["id"], f"'{column}' is not a column of {key} any more "
                             "- rewire this card")
                continue

            # The card's OWN shape decides how the column is drawn: a Gauge is
            # a gauge whatever it is fed. Only the Event Card and the Graph
            # let the operator pick, because those are the two whose whole
            # point is the shape.
            kind = spec.get("widget_kind") or "tile"
            if n["type"] in ("dashboard.events", "dashboard.graph") \
                    and d.get("kind") in builder_service.KINDS:
                kind = d["kind"]
            elif n["type"] == "dashboard.card" \
                    and d.get("kind") in CARD_KINDS + ("table",):
                # The Card's shape is DATA since Counter/Gauge/Verdict folded
                # into it, and this path still read the node type -- so a
                # gauge fed by a column drew as a tile. "table" is the rows
                # themselves, which the Event Card offered and the Card now
                # does.
                kind = d["kind"]

            agg = d.get("agg") if d.get("agg") in builder_service.AGGREGATIONS \
                else None
            if not agg:
                # A sensible default per column type rather than a blank
                # picker: a number is usually watched as its latest value, and
                # a label is usually counted.
                #
                # ...except on a TILE, where a label means the label. Wiring a
                # text column to a one-value card says "show me what it says"
                # -- "violate", "Zone 1", the current shift -- and counting the
                # rows instead answered a question nobody asked, with a number
                # where a word belonged. A chart still counts: a pie of a text
                # column is how often each value occurs, which is the only
                # thing a shape can draw from words.
                # ...and a COUNTER card counts, whatever it is fed: that is
                # the card's whole name. The plain Card is the one that shows
                # a value.
                if n["type"] != "dashboard.counter"                         and kind in ("tile", "status", "verdict"):
                    agg = "latest"
                else:
                    agg = "latest" if kinds[column] == "number" else "count"
            # Only a number can be totalled, averaged or ranked. Anything else
            # gets counted, which is the question that column can answer --
            # and Postgres would raise on the cast rather than be wrong.
            if agg in ("sum", "avg", "min", "max") and kinds[column] != "number":
                agg = "count"
            q = {"agg": agg, "field": column}
            # A chart needs something to plot ALONG -- and WHICH axis depends
            # on the chart. A pie of defect_type plots along the VALUES, so
            # grouping by the column itself is the natural reading. A LINE
            # plots along TIME: grouped by its own numeric column it became
            # one bucket per distinct value -- "the latest total, for each
            # total", a chart of y = x -- which is how "Total In Zone" showed
            # nothing. The time kinds bucket by the clock instead.
            if kind in builder_service.TIMESERIES_KINDS \
                    and d.get("group_by") in kinds:
                # THE X FIELD THE OPERATOR CHOSE ("One point per <column>")
                # wins over the time grouping (services/timeaxis.py): the
                # axis is that column's own values -- a date column's dates,
                # a status column's words. The Time range still filters.
                q["group_by"] = d["group_by"]
            elif kind in builder_service.TIMESERIES_KINDS:
                q["bucket"] = d.get("bucket") \
                    if d.get("bucket") in builder_service.BUCKETS \
                    else ("day" if d.get("range") in ("week", "month") else "hour")
                # "latest" is not a per-bucket aggregate -- the repo would
                # fall back to counting rows, which charts WRITE RATE, not
                # the value. max is the honest per-bucket reading: the end-
                # of-bucket figure for a running total, the peak for a level.
                if agg == "latest":
                    q["agg"] = agg = "max" if kinds[column] == "number" else "count"
            elif kind not in ("tile", "gauge", "table", "verdict", "status"):
                if d.get("group_by") in kinds:
                    # The X field the operator chose wins over a period
                    # (services/timeaxis.py precedence).
                    q["group_by"] = d["group_by"]
                elif d.get("bucket") in builder_service.BUCKETS:
                    # Asked for a period: one bar per hour/day/week/month.
                    q["bucket"] = d["bucket"]
                elif kinds[column] == "number":
                    # NOT the column itself. Grouping a number by its own
                    # values draws one bar per distinct value -- for a 0/1
                    # column exactly two, labelled 0 and 1, one of them always
                    # empty: a chart of the column against itself, which is
                    # what "total violations" looked like on a real box.
                    # A period is what a bar chart of recorded numbers means.
                    q["bucket"] = "day" if d.get("range") in ("week", "month") \
                        else "hour"
                else:
                    # A LABEL column is the honest exception: a pie of
                    # defect_type plots along its own values, and one slice
                    # per kind of defect is exactly the question.
                    q["group_by"] = column
            if d.get("bucket") and "bucket" not in q and "group_by" not in q:
                q["bucket"] = d["bucket"]
            # ...and wherever a bucket ended up, "latest" is not a per-bucket
            # aggregate: the repo falls back to counting rows, which charts
            # write rate rather than the value. Applied here rather than in
            # the time-chart branch alone, because a bar chart of a number
            # now buckets too.
            if q.get("bucket") and q["agg"] == "latest":
                q["agg"] = agg = "max" if kinds[column] == "number" else "count"
            if d.get("range") in builder_service.QUERY_RANGES:
                q["range"] = d["range"]

            widget = {
                "id": f"wf_{n['id']}"[:40],
                "title": str(d.get("name") or column.replace("_", " "))[:60],
                "kind": kind,
                "template": _template(d),
                **_text_sizes(d),
                "anim": _anim(d),
                "anim_replay": bool(d.get("anim_replay")),
                "bands": _bands(d),
                "by_day": _by_day(d),
                "bg": _bg(d),
                "source": "dataset",
                "dataset": key,
                "columns": table["columns"],
                "span": 1,
                "sub": str(d.get("sub") or "")[:60],
                "color": str(d.get("color") or ""),
                "unit": str(d.get("unit") or "")[:12],
                "limit": int(d.get("limit") or 15),
                "query": q,
            }
            # A Verdict card needs to know what the number MEANS, the same way
            # it does when fed a live rule.
            if n["type"] == "dashboard.verdict" \
                    or (n["type"] == "dashboard.card" and kind == "verdict"):
                # Custom rows win; otherwise the two words, as the live-rule
                # path writes them. The words used to ride only in `status`,
                # which a verdict's row does not keep (builder.validate), so
                # a column-fed verdict said COMPLIANT / VIOLATION whatever
                # the node or the Builder set.
                widget["states"] = [dict(s) for s in (d.get("states") or [])
                                    if isinstance(s, dict)] or [
                    {"value": 0, "label": str(d.get("ok_label") or "COMPLIANT")[:24],
                     "tone": "ok"},
                    {"value": 1, "label": str(d.get("alert_label") or "VIOLATION")[:24],
                     "tone": "alert"}]
                widget["status"] = {"op": d.get("op") or ">=",
                                    "value": _bp_num(d.get("value", 1), "the card threshold"),
                                    "ok_label": str(d.get("ok_label") or "COMPLIANT")[:40],
                                    "alert_label": str(d.get("alert_label") or "VIOLATION")[:40]}
            widgets.append(widget)
            continue

        if asks_recorded(n):
            # A card over an operator's own table. It names the dataset rather
            # than being wired to it: a Dataset is a sink, so a wire out of one
            # would run backwards, and several cards commonly read one table.
            if d.get("source_mode") == "dataset":
                defs = tables or {}
                key = _slug(d.get("dataset"))
                if key not in defs:
                    err(n["id"], "this card reads a table that does not exist "
                                 "- create it on the Database page, or pick "
                                 "another")
                    continue
                columns = defs[key]["columns"]
                names = {c["name"] for c in columns}
                q = {"agg": d.get("agg") or "count"}
                for field in ("field", "group_by"):
                    if d.get(field) in names:
                        q[field] = d[field]
                for field in ("bucket", "range"):
                    if d.get(field):
                        q[field] = d[field]
                # "latest" is the newest value, not a per-bucket aggregate:
                # asked of a time-bucketed chart the repo falls back to
                # counting rows, which charts WRITE RATE rather than the
                # value. max is the honest per-bucket reading -- the
                # end-of-bucket figure for a running total, the peak for a
                # level. Same rule the single-column path applies.
                if q["agg"] == "latest" and q.get("bucket"):
                    q["agg"] = "max"
                filters = {}
                for cond in (d.get("conditions") or [])[:8]:
                    if not isinstance(cond, dict):
                        continue
                    field = str(cond.get("field") or "").strip()
                    value = str(cond.get("value") or "").strip()
                    if field in names and value:
                        filters[field] = value
                if filters:
                    q["filters"] = filters
                widgets.append({
                    "id": f"wf_{n['id']}"[:40],
                    "title": str(d.get("name") or defs[key]["name"])[:60],
                    "kind": d.get("kind") if d.get("kind") in builder_service.KINDS
                            else "table",
                    "template": _template(d),
                **_text_sizes(d),
                    "anim": _anim(d),
                    "anim_replay": bool(d.get("anim_replay")),
                    "bands": _bands(d),
                    "by_day": _by_day(d),
                    "bg": _bg(d),
                    "source": "dataset",
                    "dataset": key,
                    "columns": columns,
                    "span": 1,
                    "sub": str(d.get("sub") or "")[:60],
                    "color": str(d.get("color") or ""),
                    "limit": int(d.get("limit") or 15),
                    "query": q,
                })
                continue
            if fed_by_off_node(n):
                # Wired to a switched-off rule. Compiling it would silently
                # widen the card from "what this function raised" to EVERY
                # event -- a number that looks right and answers a different
                # question.
                off_nodes.add(n["id"])
                continue
            # The question, built once (event_query) because a chart this
            # card is wired into asks the very same one.
            q, filters = event_query(n, d, announce=True)
            src_fn = upstream_fn(n)
            # Two questions about one function: what it RAISED (events, the
            # store) and what it PUBLISHES (metric, the live stat / history
            # series). Same card, so an operator picks the question rather than
            # having to know which node type answers it.
            if src_fn and d.get("source_mode") == "metric":
                metrics = functions_service.OUTPUTS[src_fn["type"]]
                kind = d.get("kind") if d.get("kind") in (
                    "tile", "line", "area", "spline", "bar", "gauge",
                    "verdict") else "tile"
                # A verdict reads the function's alert STATE (fn_<id>_active,
                # 1 while any trigger condition holds) -- the same default the
                # Card node uses, so an Alert wired into either card kind can
                # be shown as a state, not only counted.
                if kind == "verdict":
                    if "active" not in metrics:
                        metrics = tuple(metrics) + ("active",)
                    metric = (d["metric"] if d.get("metric") in metrics
                              else (wire_metric(n, src_fn, metrics)
                                    or "active"))
                else:
                    metric = (d["metric"] if d.get("metric") in metrics
                              else (wire_metric(n, src_fn, metrics)
                                    or metrics[0]))
                source = "stat" if kind in ("tile", "gauge", "verdict") else "history"
                widget = {
                    "id": f"wf_{n['id']}"[:40],
                    "title": str(d.get("name") or src_fn["name"])[:60],
                    "kind": kind,
                    "template": _template(d),
                **_text_sizes(d),
                    "anim": _anim(d),
                    "anim_replay": bool(d.get("anim_replay")),
                    "bands": _bands(d),
                    "by_day": _by_day(d),
                    "bg": _bg(d),
                    "source": source,
                    "key": trig_out_key(n, src_fn)
                           or f"fn_{src_fn['id']}_{metric}",
                    "span": 1,
                }
                if kind == "verdict":
                    # What each value MEANS -- custom rows win, the default
                    # reads the alert convention (0 calm, 1 alerting).
                    states = [st for st in (d.get("states") or [])
                              if isinstance(st, dict)]
                    ok_word, alert_word = _verdict_words(src_fn, d)
                    widget["states"] = states or [
                        {"value": 0,
                         "label": ok_word[:24],
                         "tone": "ok",
                         **({"color": d["ok_color"]} if d.get("ok_color") else {})},
                        {"value": 1,
                         "label": alert_word[:24],
                         "tone": "alert",
                         **({"color": d["alert_color"]} if d.get("alert_color") else {})},
                    ]
                if source == "history":
                    widget["range"] = (d.get("range")
                                       if d.get("range") in builder_service.HISTORY_RANGES
                                       else "24h")
                if d.get("color"):
                    widget["color"] = str(d["color"])
                if d.get("sub"):
                    widget["sub"] = str(d["sub"])[:60]
                widgets.append(widget)
                continue
            # Unwired, this aggregates the WHOLE event store: a number that
            # looks like this pipeline's and is not.
            if not src_fn and "function" not in filters:
                warn(n["id"], "this card counts EVERY matching recorded event - "
                              "wire a function into it to scope it")
            if filters:
                q["filters"] = filters
            widgets.append({
                "id": f"wf_{n['id']}"[:40],
                # Unnamed, the retired node's own name stood in, and it read
                # as a placeholder on the dashboard. "Events" says what it
                # counts.
                "title": str(d.get("name")
                             or (spec["label"] if n["type"] == "dashboard.events"
                                 else "Events"))[:60],
                "kind": d.get("kind") if d.get("kind") in builder_service.KINDS else "tile",
                "template": _template(d),
                **_text_sizes(d),
                "anim": _anim(d),
                "anim_replay": bool(d.get("anim_replay")),
                "bands": _bands(d),
                "by_day": _by_day(d),
                "bg": _bg(d),
                "source": "events" if d.get("kind") == "table" else "query",
                "span": 1,
                "sub": str(d.get("sub") or "")[:60],
                "color": str(d.get("color") or ""),
                "limit": int(d.get("limit") or 15),
                "query": q,
            })
            continue

        fn = upstream_fn(n)
        d = n["data"] or {}
        # A rule AND a text constant wired into one card. The rule wins -- it
        # is the thing that changes, and a card showing a fixed word over a
        # live measurement is not what anybody drew -- but silently is the
        # wrong way to win: the constant is on the canvas, wired, and visibly
        # doing nothing. Checked here rather than in the several-inputs branch
        # below, because a resolved rule never reaches that branch at all.
        if fn is not None and any(
                s["type"] == "variable.number"
                and (s.get("data") or {}).get("value_type") == "text"
                for _e, s in preds.get(n["id"], ())):
            warn(n["id"], "this card shows the rule wired into it; the text "
                          "constant beside it is ignored - a card shows one "
                          "value. Wire the constant into a card of its own to "
                          "show the words.")
        if fn is None:
            # SEVERAL rules wired into one chart: one series each, on the
            # shapes that can draw more than one -- a line per rule on the
            # time charts, a slice/bar per rule on the comparison charts.
            # A tile or a gauge shows ONE value, so those still insist.
            fns, fns_seen = [], set()
            for edge, src in preds.get(n["id"], ()):
                cat = REGISTRY[src["type"]]["category"]
                if cat in ("analytics", "variable"):
                    f = fn_meta.get(src["id"])
                elif is_branch(src):
                    f = switch_meta.get((src["id"],
                                         edge.get("sourceHandle") or "out0"))
                # ...and the TRIGGER, which is the node an operator reaches
                # for when they mean "show me when this fires". It stands for
                # the rule behind it exactly as the old Condition/Alert pair
                # did (cond_meta holds it either way), and leaving it out here
                # is why wiring one into a Verdict answered "needs exactly one
                # Analytics input" -- of a card wired to the very node that
                # names the moment it was meant to show.
                elif (cat == "logic" or src["type"] in _TRANSPARENT_OUTPUTS
                      or src["type"] == "output.trigger"):
                    f = cond_meta.get(src["id"])
                else:
                    continue
                if f and f["id"] not in fns_seen:
                    fns_seen.add(f["id"])
                    fns.append((edge, src, f))
            kind = _card_kind(n, d)
            chartable = (builder_service.TIMESERIES_KINDS
                         + builder_service.COMPARISON_KINDS)
            if len(fns) > 1 and kind == "verdict":
                # SEVERAL rules, ONE verdict: the first wired rule that fires
                # (wire order = priority) decides the state. Compiled as a
                # hidden picker -- iff(rule1, 1, iff(rule2, 2, ... 0)) -- so
                # the card stays an ordinary one-key widget and the states
                # editor maps 1..N to labels: 1 = the first wire's rule, 2 =
                # the second's, 0 = none of them. "A person OR a defect on
                # one card" is exactly this.
                picks = []
                for edge, src, f in fns[:6]:
                    handle = str(edge.get("sourceHandle") or "")
                    src_spec = REGISTRY.get(src["type"]) or {}
                    ms = node_metrics(src, src_spec) if src_spec else []
                    metrics = (functions_service.OUTPUTS.get(f["type"])
                               or ("value",))
                    if handle in (ms or ()):
                        metric = handle
                    else:
                        # Same default the one-wire verdict uses: the alert
                        # state when the rule has one, else its first metric
                        # (object_detect's `found`, a counter's `count`).
                        metric = ("active"
                                  if (f.get("trigger") or f.get("triggers")
                                      or f["type"] in ("proximity", "guard"))
                                  else metrics[0])
                    picks.append((f, out_key_of(src, f)
                                  or f"fn_{f['id']}_{metric}"))
                if len(fns) > 6:
                    warn(n["id"], "a verdict weighs at most 6 wired rules - "
                                  "the extra wires are ignored")
                expr = "0"
                for i in range(len(picks) - 1, -1, -1):
                    expr = f"iff(({picks[i][1]} != 0), {i + 1}, {expr})"
                vid = _slug(f"wf_{n['id']}_pick")[:40]
                doc = {"id": vid,
                       "name": f"{d.get('name') or 'Verdict'} · picker",
                       "type": "expr", "enabled": True,
                       "params": {"expr": expr}}
                try:
                    doc = functions_service.validate_function(doc)
                except (ValueError, TypeError) as exc:
                    err(n["id"], str(exc))
                    continue
                if doc["id"] not in seen_fids:
                    seen_fids.add(doc["id"])
                    functions.append(doc)
                states = [st for st in (d.get("states") or [])
                          if isinstance(st, dict)]
                if not states:
                    # No custom states yet: name each state after the rule
                    # that raises it, so the card reads sensibly before the
                    # operator has touched anything.
                    states = [{"value": 0,
                               "label": str(d.get("ok_label") or "ALL CLEAR")[:24],
                               "tone": "ok"}]
                    for i, (f, _key) in enumerate(picks):
                        states.append({"value": i + 1,
                                       "label": str(f["name"])[:24].upper(),
                                       "tone": "alert" if i == 0 else "warn"})
                w = {"id": f"wf_{n['id']}"[:40],
                     "title": str(d.get("name") or spec["label"])[:60],
                     "kind": "verdict",
                     "template": _template(d),
                **_text_sizes(d),
                     "anim": _anim(d),
                     "anim_replay": bool(d.get("anim_replay")),
                     "bands": _bands(d),
                     "by_day": _by_day(d),
                     "bg": _bg(d),
                     "source": "stat",
                     "key": f"fn_{vid}_value",
                     "states": states,
                     "span": 1}
                if d.get("color"):
                    w["color"] = d["color"]
                if d.get("sub"):
                    w["sub"] = str(d["sub"])[:60]
                widgets.append(w)
                continue
            if len(fns) > 1 and kind in chartable:
                series = []
                for edge, src, f in fns[:builder_service.MAX_SERIES]:
                    # A wire dropped on a NAMED metric port means that metric,
                    # same as everywhere else on the canvas.
                    handle = str(edge.get("sourceHandle") or "")
                    src_spec = REGISTRY.get(src["type"]) or {}
                    ms = node_metrics(src, src_spec) if src_spec else []
                    metrics = (functions_service.OUTPUTS.get(f["type"])
                               or ("value",))
                    metric = handle if handle in (ms or ()) else metrics[0]
                    series.append({"key": out_key_of(src, f)
                                   or f"fn_{f['id']}_{metric}",
                                   "label": f["name"], "agg": "avg"})
                if len(fns) > builder_service.MAX_SERIES:
                    warn(n["id"], f"a chart draws at most "
                                  f"{builder_service.MAX_SERIES} series - "
                                  "the extra wires are ignored")
                w = {"id": f"wf_{n['id']}"[:40],
                     "title": str(d.get("name") or spec["label"])[:60],
                     "kind": kind,
                     "template": _template(d),
                **_text_sizes(d),
                     "anim": _anim(d),
                     "anim_replay": bool(d.get("anim_replay")),
                     "bands": _bands(d),
                     "by_day": _by_day(d),
                     "bg": _bg(d),
                     # Comparison shapes compare the latest values; the time
                     # shapes read each key's history.
                     "source": ("stat" if kind in builder_service.COMPARISON_KINDS
                                else "history"),
                     "series": series,
                     "span": 1}
                if w["source"] == "history":
                    w["range"] = (d.get("range")
                                  if d.get("range") in builder_service.HISTORY_RANGES
                                  else "24h")
                if d.get("color"):
                    w["color"] = d["color"]
                if d.get("unit"):
                    w["unit"] = d["unit"]
                if d.get("sub"):
                    w["sub"] = str(d["sub"])[:60]
                widgets.append(w)
                continue
            if fed_by_off_node(n):
                # The card's function is switched off; it would show a key that
                # never gets published. Leave it out with the rest of that
                # branch rather than reporting a second problem.
                off_nodes.add(n["id"])
                continue
            if len(fns) == 1:
                # TWO WIRES, ONE RULE: the same analytics node wired in twice
                # (typically once on its named `out` port and once on the
                # plain port -- the two wires overlap on the canvas and read
                # as one). That is a duplicate, not an ambiguity: follow the
                # rule and say so, instead of refusing a card whose canvas
                # looks perfectly wired.
                warn(n["id"], "the same rule is wired into this card twice - "
                              "the duplicate wire is ignored; delete one to "
                              "clear this")
                fn = fns[0][2]
            elif len(fns) > 1:
                err(n["id"], f"{spec['label']} shows ONE value - use a "
                             "Graph node to draw several as series (wiring a "
                             "second value into a tile or gauge converts it "
                             "for you), or keep one wire")
                continue
            else:
                # A TEXT CONSTANT on a one-value card: the card shows the
                # words. A constant publishes no stat at all (there is
                # nothing to measure), so it never appeared in `fns` and the
                # card refused with "needs exactly one Analytics input" -- of
                # a card wired to a value that was sitting right there. The
                # text travels ON the widget instead. That is THIS route
                # only: a rule whose result is a word does publish it, and
                # such a card carries a key and reads the stats dict like
                # every other card (builder.VALUE_FORMATS "text").
                const = _text_constant(n, preds)
                if const is not None and kind in ("tile", "status", "verdict"):
                    w = {"id": f"wf_{n['id']}"[:40],
                         "title": str(d.get("name") or const["name"])[:60],
                         "kind": "tile",
                         "template": _template(d),
                **_text_sizes(d),
                         "anim": _anim(d),
                         "anim_replay": bool(d.get("anim_replay")),
                         "bands": _bands(d),
                         "by_day": _by_day(d),
                         "bg": _bg(d),
                         "source": "stat",
                         "key": "",
                         "text": const["text"],
                         "span": 1}
                    if d.get("color"):
                        w["color"] = d["color"]
                    if d.get("sub"):
                        w["sub"] = str(d["sub"])[:60]
                    widgets.append(w)
                    continue
                err(n["id"], f"{spec['label']} needs exactly one Analytics input")
                continue
        # The operator's own name for the card wins; the function's name is
        # the fallback. Naming matters because one function commonly feeds
        # three cards, and three cards all called "Zone 1 person" is a
        # dashboard nobody can read.
        w = {"id": f"wf_{n['id']}"[:40],
             "title": str(d.get("name") or fn["name"])[:60],
             "kind": spec["widget_kind"],
             "template": _template(d),
                **_text_sizes(d),
             "anim": _anim(d),
             "anim_replay": bool(d.get("anim_replay")),
             "bands": _bands(d),
             "by_day": _by_day(d),
             "bg": _bg(d),
             "source": spec["widget_source"],
             "span": 1}
        if spec["widget_source"] in ("stat", "history"):
            metrics = functions_service.OUTPUTS[fn["type"]]
            # An object watch also publishes one count PER CLASS it watches
            # for -- fn_<id>_person -- so a card can show "how many persons"
            # directly instead of routing through a second counter.
            if fn["type"] == "object_detect":
                metrics = tuple(metrics) + tuple(
                    _slug(c) for c in (fn["params"].get("classes") or ()))
            # The Verdict Card wants the function's alert STATE by default:
            # the engine publishes fn_<id>_active (1 while any of its
            # triggers' conditions hold) alongside the regular metrics.
            # WHICH PORT the wire left from is a choice of metric, and it
            # is the only one an operator makes by dragging. A Line
            # Counter offers in / out / inside / crossings as four ports;
            # dragging from each into its own card drew four wires that
            # all read `in`, because this branch consulted the card's own
            # picker and nothing else -- four cards, four labels, one
            # number. The charts and verdicts built from SEVERAL wires
            # already read the handle; the one-wire card was the odd one
            # out.
            #
            # The card's picker still wins where it has been set: it is
            # the later and more specific statement, and it is what the
            # Analytics editor writes when a card is re-aimed from there.
            wired = wire_metric(n, fn, metrics)
            if _card_kind(n, d) == "verdict":
                if "active" not in metrics:
                    metrics = metrics + ("active",)
                # Self-alerting functions (proximity/guard) publish `active`
                # from their own condition, so the card defaults to it even
                # with no Alert/When node in between.
                default = "active" if (fn.get("trigger") or fn.get("triggers")
                                       or fn["type"] in ("proximity", "guard")) \
                    else metrics[0]
                metric = (d.get("metric") if d.get("metric") in metrics
                          else (wired or default))
            else:
                metric = (d.get("metric") if d.get("metric") in metrics
                          else (wired or metrics[0]))
            metric = _windowed_metric(n, d, fn, metric, metrics, warn)
            w["key"] = trig_out_key(n, fn) or f"fn_{fn['id']}_{metric}"
        if _card_kind(n, d) == "verdict":
            # What each value MEANS. Custom rows win; the default reads the
            # alert convention (0 = calm, nonzero/1 = alerting).
            states = [s for s in (d.get("states") or []) if isinstance(s, dict)]
            ok_word, alert_word = _verdict_words(fn, d)
            w["states"] = states or [
                {"value": 0, "label": ok_word[:24],
                 "tone": "ok",
                 **({"color": d["ok_color"]} if d.get("ok_color") else {})},
                {"value": 1, "label": alert_word[:24],
                 "tone": "alert",
                 **({"color": d["alert_color"]} if d.get("alert_color") else {})},
            ]
        # A card's or chart's SHAPE is data, not a node type per shape.
        w["kind"] = _card_kind(n, d)
        # A donut, pie or radar is a comparison, not a time series: it needs
        # values to compare, which the history sampler does not give it.
        if w["kind"] in ("donut", "pie", "radar"):
            w["source"] = "stat"
        if spec["widget_source"] == "history":
            # One whitelist for every history range in the product (builder,
            # workflow, widget validation) -- a shorter copy here silently
            # rewrote "6h" to "24h".
            w["range"] = (d.get("range") if d.get("range") in builder_service.HISTORY_RANGES
                          else "24h")
        if d.get("color"):
            w["color"] = d["color"]
        if d.get("unit"):
            w["unit"] = d["unit"]
        # The caption travelled on every other card path (verdict, chart,
        # constant) but not this one -- the commonest -- so a caption typed
        # on a plain stat tile compiled to nothing.
        if d.get("sub"):
            w["sub"] = str(d["sub"])[:60]
        # One whitelist for every value format in the product, for the
        # same reason as HISTORY_RANGES above: a shorter copy here is
        # an option the inspector offers and the compiler drops in
        # silence. "text" is what a tile bound to a rule that picked a
        # WORD needs.
        if d.get("format") in builder_service.VALUE_FORMATS \
                and w["kind"] == "tile":
            w["format"] = d["format"]
        widgets.append(w)
    # -- threshold lines -> rules --------------------------------------------
    #
    # ONE pass over the finished widgets, not a hook inside the branch that
    # happens to build a live card. A dashboard node reaches `widgets` by half
    # a dozen routes -- a rule wired in, a Data Table column, a Query, the
    # event store, a text constant -- and each of them ends in its own
    # `continue`. Hooked into one of those, a card fed by a table drew its
    # lines and compiled no rule while an identical card fed by a rule did --
    # the same setting meaning two different things depending on which branch
    # built the card. Here every card is asked the same question.
    #
    # `w["key"]` is the stat the card actually reads, after the metric picker,
    # the wire's handle and the window have all had their say -- and the rule
    # has to watch the SAME number the line is drawn across.
    by_node = {f"wf_{n['id']}"[:40]: n for n in graph["nodes"]
               if REGISTRY[n["type"]]["category"] == "dashboard"}
    for w in widgets:
        n = by_node.get(w["id"])
        if n is None:
            continue
        # The bucket size of a sampled chart. One pass here rather than in
        # each history branch for the same reason the band rules are: the
        # Builders offer "Interval" on every history chart and write it onto
        # the node (CARD_QUESTION_FIELDS), and a branch that forgot it would
        # be the Builder's edit lasting until the next compile again.
        nd_axis = n.get("data") or {}
        # THE TIME AXIS (services/timeaxis.py). The node has one "Group by
        # time" (`bucket`): a store question takes it as its bucket where it
        # is built above, and a sampled chart takes it as its grid step here
        # -- one control on the node, whichever source the chart reads.
        step = _timeaxis.clean_step(nd_axis.get("bucket"))
        if w.get("source") == "history" and step and not nd_axis.get("interval"):
            w["interval"] = step
        # "Pick dates...": the exact window travels with every part of the
        # widget that says "custom" -- the chart, its own query, each input.
        if nd_axis.get("range") == "custom":
            ends = {"range_from": _timeaxis.clean_moment(nd_axis.get("range_from")),
                    "range_to": _timeaxis.clean_moment(nd_axis.get("range_to"))}
            if w.get("range") == "custom":
                w.update(ends)
            for part in [w.get("query")] + [s.get("query") for s in
                                             (w.get("series") or [])
                                             if isinstance(s, dict)]:
                if isinstance(part, dict) and part.get("range") == "custom":
                    part.update(ends)
        iv = (n.get("data") or {}).get("interval")
        if w.get("source") == "history" and iv \
                and iv in builder_service.HISTORY_INTERVALS:
            w["interval"] = iv
        # The chart builder model, as the node holds it, for the Builders to
        # show on this compiled card (they edit it and send it back through
        # card-style). Absent for a chart saved before the model.
        model = _chartmodel.node_model(nd_axis) \
            if w.get("kind") in _chartmodel.CHART_KINDS else {}
        if model:
            w["chart"] = model
        # ...and how it is drawn (axis titles, orientation, bar style, value
        # labels), set in the Builders and kept on the node like the rest of
        # the look; only the ones that are set.
        w.update(_chartmodel.clean_look(nd_axis, w.get("kind")))
        # A card showing a live rule's value carries the counter it reads
        # (its node's one Date range, `window`) so the Builders show the
        # same control the node does. Only where it is not the live value.
        if w.get("source") == "stat" and w.get("key") \
                and w.get("kind") in CARD_KINDS \
                and nd_axis.get("window") in CARD_WINDOWS \
                and nd_axis.get("window") != "live":
            w["window"] = nd_axis["window"]
        # ...and the unit, for the same reason: both panels offer "Unit
        # label" on every card that prints a number, but only some branches
        # carried it (a recorded-events or data-table card dropped it).
        unit = str((n.get("data") or {}).get("unit") or "").strip()[:12]
        if unit and not w.get("unit"):
            w["unit"] = unit
        # ...and a status pill's rule. No branch carried one, so every
        # compiled pill said ">= 1 / OK / ALERT" whatever either panel set.
        nd = n.get("data") or {}
        if w.get("kind") == "status" and not w.get("status")                 and any(nd.get(k) not in (None, "")
                        for k in ("op", "value", "ok_label", "alert_label")):
            w["status"] = {"op": nd.get("op") if nd.get("op")
                           in builder_service.STATUS_OPS else ">=",
                           "value": _clean_question(n, "value", nd.get("value"))
                           if _clean_question(n, "value", nd.get("value"))
                           is not None else 1,
                           "ok_label": str(nd.get("ok_label") or "OK")[:40],
                           "alert_label": str(nd.get("alert_label") or "ALERT")[:40]}
        for doc in band_alerts(n, n.get("data") or {}, w.get("key"),
                               seen_fids):
            try:
                functions.append(functions_service.validate_function(doc))
            except (ValueError, TypeError) as exc:
                err(n["id"], f"threshold rule: {exc}")

    if len(widgets) > builder_service.MAX_WIDGETS:
        errors.append({"node": "", "message":
                       f"at most {builder_service.MAX_WIDGETS} dashboard nodes"})

    # -- named dashboard nodes -> palette entries -----------------------------
    #
    # The editors' "From workflow" rail listed FUNCTIONS and table columns:
    # raw measurements, named by whatever the analytics node was called. A
    # card built on the canvas -- "Line 2 rejects, last 7 days, as a donut" --
    # was a thing the operator had already composed and could not reach from
    # the Dashboard or Analytics editor at all; it had to be rebuilt there by
    # hand.
    #
    # Naming the node is what publishes it. An unnamed card is titled after
    # its function and stays off the rail, so this cannot flood the palette
    # with a dozen "Card"s that all look identical -- the operator opts in,
    # one deliberate name at a time.
    # "Show title" (both editors): onto the compiled card only when the node
    # says so, so every card compiled before the switch existed is unchanged.
    titled = {f"wf_{n['id']}"[:40]: n["data"]["show_title"]
              for n in graph["nodes"]
              if REGISTRY[n["type"]]["category"] == "dashboard"
              and isinstance((n.get("data") or {}).get("show_title"), bool)}
    for w in widgets:
        if w.get("id") in titled:
            w["show_title"] = titled[w["id"]]
    # Where a stat sits (align / valign), the same way: only when set, and only
    # on the kinds that have a stat block (builder.STAT_LAYOUT_KINDS).
    placed = {f"wf_{n['id']}"[:40]: n.get("data") or {}
              for n in graph["nodes"]
              if REGISTRY[n["type"]]["category"] == "dashboard"}
    for w in widgets:
        d = placed.get(w.get("id"))
        if d:
            w.update(builder_service._stat_layout(w.get("kind"), d))
    named = {}
    for n in graph["nodes"]:
        if REGISTRY[n["type"]]["category"] != "dashboard":
            continue
        label = str((n.get("data") or {}).get("name") or "").strip()
        if label:
            named[f"wf_{n['id']}"[:40]] = label[:60]
    for w in widgets:
        label = named.get(w["id"])
        if not label:
            continue
        # Built from the COMPILED widget, so a palette drop lands the card the
        # operator actually drew -- its shape, range, unit, filters and all --
        # rather than a stat key that has to be configured back into one.
        # `id` and `span` are the canvas copy's own business.
        entry = {k: v for k, v in w.items() if k not in ("id", "span")}
        entry.update({"key": w.get("key", ""), "label": label, "title": label,
                      "role": "custom", "surfaces": ["dashboard", "analytics"],
                      "origin": "card", "node": w["id"][3:]})
        catalog.append(entry)

    # An If/Else or Formula runs on the camera it reads (rule_camera.py).
    rule_camera.stamp(functions, zone_by_id, FRAME_FN_TYPES, function_keys,
                      key_owner)
    # -- output nodes -> output documents ------------------------------------
    outputs = []
    # What the Data Table nodes asked their tables to look like. Folded into
    # the stored registry after a clean compile (services/datasets.py
    # apply_declared), which is what makes "just draw it on the canvas" work
    # without visiting the Database page first.
    declared_tables = {}
    for n in graph["nodes"]:
        spec = REGISTRY[n["type"]]
        if spec["category"] != "output" or spec["output_type"] is None:
            continue                                 # alert compiled above

        # A Modbus Write takes TWO things on two ports -- the value on `in`,
        # and optionally WHEN on `trig` -- so "exactly one function feeds
        # this" is answered per port, the way a Data Table answers it per
        # column. Compiled on its own terms, before the general path below.
        if spec["output_type"] == "modbus_write":
            d = n["data"] or {}
            value_fns = fan_in_fns(n, handle="in")
            value_fn = value_fns[0] if value_fns else None
            wired_in = [e for e, _s in preds.get(n["id"], ())
                        if (e.get("targetHandle") or "in") == "in"]
            wired_trig = [e for e, _s in preds.get(n["id"], ())
                          if str(e.get("targetHandle") or "") == "trig"]
            # SEVERAL INJECTS on the value port (Boboiboy's rule for client
            # UAT item 5, "handle multiple injections"): with the trigger
            # port unwired and EVERY value wire a clock source (Inject,
            # Schedule), each `fired` tick writes and the newest-fired wire
            # wins -- mode "fire" (app/outputs.py _modbus_write_on_fire).
            # "On change" would drop a re-injected same payload, which is
            # the one thing a second press means. Any other mix of several
            # values is one register with two answers, and is refused.
            clock_only = bool(value_fns) and all(
                f.get("type") in CLOCK_FN_METRIC for f in value_fns)
            if len(value_fns) > 1 and (wired_trig or not clock_only):
                err(n["id"], "Modbus Write writes ONE value (this has "
                             f"{len(value_fns)} wired into its value port) - "
                             "keep one wire, or combine them in a Math node "
                             "first"
                             + ("; several Injects write in turn only while "
                                "the trigger port is unwired" if clock_only
                                else ""))
                continue
            if value_fn is None:
                if fed_by_off_node(n):
                    off_nodes.add(n["id"])
                    continue
                err(n["id"], "Modbus Write needs ONE value wired into its "
                             "value port - a metric, a variable, Math, a "
                             "Formula or a Python node"
                             + (f" (has {len(wired_in)})" if wired_in else ""))
                continue
            metrics = functions_service.OUTPUTS[value_fn["type"]]
            metric = wire_metric(n, value_fn, metrics) or metrics[0]
            doc = {"id": f"wf_{n['id']}"[:40],
                   "label": (f"{d.get('name') or value_fn['name']} "
                             f"→ Modbus Write")[:60],
                   "type": "modbus_write", "enabled": True,
                   "key": trig_out_key(n, value_fn, "in")
                          or f"fn_{value_fn['id']}_{metric}"}
            # A Python node's return may be Node-RED's whole payload,
            # {value, fc, unitid, address, quantity}; each key reaches the
            # app as its own stat of that function, and the write reads them
            # as overrides. Only a custom function can publish those keys.
            if value_fn.get("type") == "custom":
                doc["key"] = f"fn_{value_fn['id']}_value"
                doc["payload_fn"] = value_fn["id"]
            # An Advanced Trigger's payload on a REGISTER write must be a
            # number it can hold; said here, on the node, rather than once
            # per fire in the app's log (trigger_out.register_problem).
            bad_payload = next(
                (trigger_out.register_problem(src.get("data"), d.get("fc"))
                 for e, src in preds.get(n["id"], ())
                 if (e.get("targetHandle") or "in") == "in"
                 and out_key_of(src, value_fn) == doc["key"]), "")
            if bad_payload:
                err(n["id"], bad_payload)
                continue
            for k in ("server", "unit", "fc", "address", "quantity",
                      "delay_start", "start_delay_s"):
                if k in d:
                    doc[k] = d[k]
            if wired_trig:
                trig_fn = upstream_fn(n, handle="trig")
                if trig_fn is None:
                    err(n["id"], "wire ONE Condition, Compare or Trigger into "
                                 "Modbus Write's trigger port - or unwire it "
                                 "to write whenever the value changes")
                    continue
                # A Formula or a Schedule is its own decision (see the
                # general path below); anything else must already be armed.
                if not (trig_fn.get("trigger") or trig_fn.get("triggers")) \
                        and trig_fn.get("type") in ("expr", *CLOCK_FN_METRIC):
                    metric_t = CLOCK_FN_METRIC.get(trig_fn["type"], "value")
                    trig_fn.setdefault("triggers", []).append({
                        "metric": metric_t, "op": ">", "value": 0.0,
                        "severity": "info",
                        "message": trig_fn.get("name") or "Modbus Write",
                        "cooldown_s": 0})
                if not (trig_fn.get("trigger") or trig_fn.get("triggers")):
                    err(n["id"], "what is wired to Modbus Write's trigger "
                                 "never fires - wire a Condition, a Compare "
                                 "or a Trigger into it")
                    continue
                doc.update(trigger="alert", function=trig_fn["id"],
                           mode="trigger")
                wait = upstream_delay(n["id"])
                if wait > 0:
                    doc["delay_s"] = round(wait, 2)
            elif clock_only:
                # No trigger, Injects (or Schedules) only: every fire writes,
                # in turn -- the keys per wire, so the app can tell whose
                # press this tick is.
                doc.update(trigger="any", mode="fire", sources=[
                    {"fired": f"fn_{f['id']}_{CLOCK_FN_METRIC[f['type']]}",
                     "key": f"fn_{f['id']}_"
                            f"{wire_metric(n, f, functions_service.OUTPUTS[f['type']]) or functions_service.OUTPUTS[f['type']][0]}"}
                    for f in value_fns])
            else:
                # No trigger: written whenever the value CHANGES, paced by the
                # app's tick (outputs.publish), never once per frame.
                doc.update(trigger="any", mode="change")
                # ...and again at each fire of the Trigger or Condition in
                # front, as an MQTT Publish does (publish_follow `beat`).
                # "Changed" is remembered per OUTPUT, not per register: a
                # Trigger sending 0 to a motor that another rule set to 100
                # read "no change, 0 was my last write" and wrote nothing
                # (asri_test_4 on .49, 2026-10-09, the stop on the OFF side).
                if value_fn.get("trigger") or value_fn.get("triggers"):
                    doc["beat"] = value_fn["id"]
            try:
                outputs.append(dc._validate_output(doc))
            except (ValueError, TypeError) as exc:
                err(n["id"], str(exc))
            continue

        # A Dataset takes MANY inputs -- one per wired column -- so the
        # "exactly one function feeds this" rule the other outputs live by
        # does not apply, and it is compiled on its own terms.
        if spec["output_type"] == "dataset":
            d = n["data"] or {}
            name = str(d.get("name") or "").strip()
            key = _slug(d.get("dataset") or name)
            if not datasets_repo.COL_RE.match(key or ""):
                err(n["id"], "pick a table for this node - create one on the "
                             "Database page first if there is none yet")
                continue
            # A table is designed on the Database page and then read for
            # months. One invented as a side effect of wiring a node is a
            # table nobody chose the shape of, so the node picks from what
            # exists rather than conjuring it.
            if key not in (tables or {}):
                err(n["id"], f"there is no table called '{key}' - create it on "
                             "the Database page, then pick it here")
                continue
            # A read-only node writes nothing, so it compiles to no output
            # document at all. It still exists on the canvas for its columns'
            # READ ports, which is the whole point of it.
            if d.get("op") == "select":
                continue
            columns, why = dataset_columns(
                n, preds.get(n["id"], ()), (tables or {}).get(key),
                # Node id -> the zone its compiled rule watches, so a OID
                # wire from a zone counter records only who is IN the zone.
                # A LINE is a zone here too: its crossing events carry the
                # line id, so an OID column wired to a Line Counter records
                # who crossed THAT line rather than any id in frame.
                zone_of={nid: ((doc.get("params") or {}).get("zone")
                               or (doc.get("params") or {}).get("line"))
                         for nid, doc in fn_meta.items()
                         if ((doc.get("params") or {}).get("zone")
                             or (doc.get("params") or {}).get("line"))})
            if why:
                if fed_by_off_node(n):
                    off_nodes.add(n["id"])
                    continue
                err(n["id"], why)
                continue
            mode = d.get("mode") if d.get("mode") in DATASET_MODES else "alert"
            doc = {"id": f"wf_{n['id']}"[:40],
                   "label": f"Data Table {key}"[:60],
                   "type": "dataset", "enabled": True,
                   "dataset": key, "name": name or key,
                   "mode": mode, "columns": columns}
            # "When the same object is seen again". Absent or "insert" is
            # today's behaviour and adds NOTHING to the document, so every
            # saved graph compiles byte-for-byte as before. "update" needs a
            # column that names the object (an OID wire, or "from the
            # event: the tracked object's id"); without one there is no
            # "same object" to find, so it is dropped with a plain warning
            # rather than refusing the canvas.
            # "Object IDs in a row": "group" lists every object's id in ONE
            # row and saves it only when the set changes (app/outputs.py
            # _group_rows). Same rule as above: absent or "object" adds
            # nothing; without an identity column there are no ids to list,
            # and a crossing table is one row per crossing by definition --
            # dropped with a warning in both cases. With on_repeat="update"
            # the key is the whole group cell, so the two combine safely.
            if d.get("ids_in_row") == "group":
                if mode == "crossing":
                    warn(n["id"], "lists the whole group in one row, but a "
                                  "table written on each line crossing is "
                                  "one row per crossing - the setting is "
                                  "ignored")
                elif any(c.get("src") == "event" and c.get("field") == "track_id"
                         for c in columns):
                    doc["ids_in_row"] = "group"
                else:
                    warn(n["id"], "set to one row for the whole group, but no "
                                  "column records which object it is - wire "
                                  "an OID into a column. Until then it writes "
                                  "one row as before")
            if d.get("on_repeat") == "update":
                if any(c.get("src") == "event" and c.get("field") == "track_id"
                       for c in columns):
                    doc["on_repeat"] = "update"
                else:
                    warn(n["id"], "set to update the existing row, but no "
                                  "column records which object it is - wire "
                                  "an OID into a column. Until then every "
                                  "write adds a new row")
            if mode == "alert":
                # WHICH alert fires the row. Wired if the operator said so,
                # derived from the column wires otherwise.
                #
                # Explicit wins, and completely: once anything is on `trig`,
                # the columns are values and nothing else. A table that took
                # BOTH would fire on a column rule the operator had already
                # answered the question about, which is the one behaviour
                # nobody could explain from the canvas.
                all_preds = list(preds.get(n["id"], ()))
                on_trig = [(e, s) for e, s in all_preds
                           if str(e.get("targetHandle") or "") == "trig"]
                armed, stays, seen_fn = [], [], set()
                for edge, src_node in (on_trig or all_preds):
                    cat = REGISTRY[src_node["type"]]["category"]
                    if cat in ("analytics", "variable"):
                        cand = fn_meta.get(src_node["id"])
                    elif (cat == "logic"
                          or src_node["type"] in _TRANSPARENT_OUTPUTS
                          or src_node["type"] == "output.trigger"):
                        cand = cond_meta.get(src_node["id"])
                    else:
                        continue
                    if not cand or cand["id"] in seen_fn:
                        continue
                    seen_fn.add(cand["id"])
                    if cand.get("trigger") or cand.get("triggers"):
                        armed.append(cand)
                    elif (cand.get("params") or {}).get("stamp_events"):
                        # Time in Zone with OID on: each completed stay is an
                        # event of its own, so it can arm this table without a
                        # threshold -- "a row per stay" needs no Alert.
                        stays.append(cand)
                # EVERY rule wired in records its own row. One table
                # collecting three zones' cycle times is one node with three
                # wires, not three nodes writing the same two columns -- so
                # the rules that can fire become one output document EACH,
                # sharing this node's table and columns. (Refusing them was
                # the old behaviour: "keep one Alert", which forced a
                # duplicate node per rule.)
                firing = ([(f, "alert") for f in armed]
                          or [(f, "info") for f in stays])
                if not firing:
                    if fed_by_off_node(n):
                        off_nodes.add(n["id"])
                        continue
                    # Two different mistakes, two different sentences. Wiring
                    # something to the trigger port that cannot fire is a
                    # wrong wire, and telling that operator to "add a
                    # Condition to one of the rules wired into it" sends them
                    # to the columns, which are not the problem.
                    err(n["id"],
                        ("what is wired to this table's trigger never fires - "
                         "wire a Condition, an Alert or a Trigger into it, or "
                         "unwire it and let the columns' own rules fire the row")
                        if on_trig else
                        ("nothing feeding this table alerts, so there is no "
                         "moment to record - add a Condition to one of the "
                         "rules wired into it, wire one into the table's "
                         "trigger port, tick OID on a Time in Zone, or set "
                         "'Write a row' to on a timer or on change, or set "
                         "'Write a row' to each time something crosses a line "
                         "for a Line Counter"))
                    continue
                wait = upstream_delay(n["id"])
                triggered = []
                for i, (fn_doc, kind) in enumerate(firing):
                    one = dict(doc)
                    # One id per writer, the first keeping the node's own so
                    # a single-rule table's document never moves.
                    if i:
                        one["id"] = f"{doc['id']}_{i}"[:40]
                        one["label"] = f"{doc['label']} ({fn_doc['name']})"[:60]
                    one["trigger"] = kind
                    one["function"] = fn_doc["id"]
                    if wait > 0:
                        one["delay_s"] = round(wait, 2)
                    triggered.append(one)
            elif mode == "crossing":
                # One row per crossing of a LINE. The row is about the line,
                # not about the counter: two Line Counters on one line (a live
                # one for the card, a running total for the report) must not
                # write two rows for one person, so the documents are one per
                # LINE among the counters wired in, not one per counter.
                # Wired to `trig` if the operator said so, derived from the
                # column wires otherwise -- the same rule alert mode lives by.
                all_preds = list(preds.get(n["id"], ()))
                on_trig = [(e, s) for e, s in all_preds
                           if str(e.get("targetHandle") or "") == "trig"]
                by_line = {}                     # line id -> [line_count docs]
                for edge, src_node in (on_trig or all_preds):
                    cat = REGISTRY[src_node["type"]]["category"]
                    if cat in ("analytics", "variable"):
                        cand = fn_meta.get(src_node["id"])
                    elif (cat == "logic"
                          or src_node["type"] in _TRANSPARENT_OUTPUTS):
                        # A Delay passes its rule straight through.
                        cand = cond_meta.get(src_node["id"])
                    else:
                        continue
                    if not cand or cand.get("type") != "line_count":
                        continue
                    line_id = (cand.get("params") or {}).get("line")
                    if not line_id:
                        continue
                    by_line.setdefault(str(line_id), []).append(cand)
                if not by_line:
                    if fed_by_off_node(n):
                        off_nodes.add(n["id"])
                        continue
                    err(n["id"], "set to each time something crosses a line, "
                                 "but no Line Counter is wired into it - wire "
                                 "one into a column, or into the trigger port")
                    continue
                wait = upstream_delay(n["id"])
                triggered = []
                for i, (line_id, fns) in enumerate(by_line.items()):
                    one = dict(doc)
                    if i:
                        one["id"] = f"{doc['id']}_{i}"[:40]
                        lname = (zone_by_id.get(line_id) or {}).get("name") or line_id
                        one["label"] = f"{doc['label']} ({lname})"[:60]
                    # "info" is the type run.py gives a crossing event; the
                    # line binding is what fire() matches, since a crossing
                    # event names no function.
                    one["trigger"] = "info"
                    one["line"] = line_id
                    # The counters' Class Filters, mirrored so the row obeys
                    # the same label test fn.accepts does. When ANY counter
                    # on the line is unfiltered the table records every
                    # class, which is what that counter counts. The `model`
                    # param is deliberately not mirrored: a row is about the
                    # line, whichever model saw the crossing.
                    class_sets = [set((f.get("params") or {}).get("classes") or ())
                                  for f in fns]
                    if class_sets and all(class_sets):
                        one["classes"] = sorted(set().union(*class_sets))
                    if wait > 0:
                        one["delay_s"] = round(wait, 2)
                    triggered.append(one)
            else:
                # Timed and on-change rows are not about anything going wrong,
                # so they need no trigger -- same reasoning as a telemetry
                # publisher (no_trigger above).
                doc["trigger"] = "any"
                doc["interval_s"] = d.get("interval_s", 60)
                doc["min_interval_s"] = d.get("min_interval_s", 0)
                triggered = [doc]
                # The reported "two rows per crossing": a live Line Counter's
                # in/out is a LEVEL (1 while the object is over the line, 0
                # after), and on-change records both edges. Said here, on the
                # node, because the canvas shows a wire and a mode and nothing
                # that says the two make two rows. A warning, not an error --
                # "sum of the column" is a documented use of the same wiring.
                if mode == "change" and any(
                        (fn_meta.get(s["id"]) or {}).get("type") == "line_count"
                        and ((fn_meta.get(s["id"]) or {}).get("params") or {}).get("momentary")
                        for e, s in preds.get(n["id"], ())
                        if str(e.get("targetHandle") or "").startswith("c_")):
                    warn(n["id"],
                         "this table writes a row when the counter's live level "
                         "rises and again when it falls back to 0 - two rows per "
                         "crossing. For one row per crossing with its direction, "
                         "set Write a row to each time something crosses a line "
                         "and give a column the event field which way it crossed")
            bad = False
            for one in triggered:
                try:
                    outputs.append(dc._validate_output(one))
                except (ValueError, TypeError) as exc:
                    err(n["id"], str(exc))
                    bad = True
                    break
            if bad:
                continue
            # Two nodes may write to one table, each filling some of its
            # columns, so what they declare is UNIONED rather than replaced.
            want = declared_tables.setdefault(
                key, {"name": name or key, "columns": []})
            have = {c["name"] for c in want["columns"]}
            for col in columns:
                if col["name"] not in have:
                    entry = {"name": col["name"], "type": col["type"]}
                    # Marked so the registry knows this one type is not a
                    # preference it may overrule -- see merge_declared.
                    if col.get("field") == "track_id":
                        entry["identity"] = True
                    want["columns"].append(entry)
                    have.add(col["name"])
            continue

        # A message node fed by a WATCH is not an output at all: nothing on
        # the canvas fires it, the master does, on a timer. It compiles as
        # part of that watch below, so compiling it here as well would demand
        # the rule it has not got and put an error on a graph that is right.
        if any(src["type"] == "integration.watch"
               for _e, src in preds.get(n["id"], ()) or ()):
            continue

        # An MQTT Publish with NOTHING wired in publishes nothing: it has no
        # clock of its own any more (services/publish_follow.py), so it says
        # what to wire rather than refusing the whole canvas.
        if spec["output_type"] == "mqtt_publish" and not preds.get(n["id"]):
            warn(n["id"], publish_follow.UNWIRED)
            continue
        # Message outputs may be wired to several rules -- one says WHEN, the
        # rest are values their text can quote. See upstream_fn.
        many_ok = spec.get("output_type") in ("email", "telegram")
        fn = upstream_fn(n, prefer_trigger=many_ok)
        # FAN-IN: several rules wired into one output. Each fires it on its
        # own moment -- one output document per rule, the way a Data Table
        # takes one writer per rule -- and a publisher sends each wired
        # value on its own clock (client UAT 2026-10-04, item 6). The same
        # rule wired in twice is one rule. Not for the level-followers (Zone
        # Switch, a PWM set to follow a value, the Modbus Register): two
        # levels into one lamp have no single answer, so those keep one
        # wire and say so in plain words.
        fan = [fn] if fn else []
        if fn is None and not many_ok:
            cands = fan_in_fns(n)
            level = spec["output_type"] in ("zone_switch", "modbus_reg") or (
                spec["output_type"] == "io_pwm"
                and str((n["data"] or {}).get("when") or "alert") == "always")
            if len(cands) == 1 or (len(cands) > 1 and not level
                                   and spec["output_type"] in FAN_IN_OUTPUTS):
                fan, fn = cands, cands[0]
            elif len(cands) > 1:
                err(n["id"], f"{spec['label']} follows ONE value (this has "
                             f"{len(cands)} wired in) - keep one wire, or "
                             "combine them in a Math node first")
                continue
        if fn is None:
            if fed_by_off_node(n):
                # Its rule is switched off, so this output has nothing to fire
                # on. Never compiled = never sends -- which is the point.
                off_nodes.add(n["id"])
                continue
            if many_ok:
                # The failure is now specifically about WHEN it should fire,
                # because "needs exactly one Analytics input" stopped being
                # true the moment several were allowed.
                # Two things that both say WHEN is a question only the operator
                # can answer, and it is now the likelier of the two mistakes:
                # a second Schedule on one Email is easy to draw and reads as
                # "send it at both times", which is two Emails.
                err(n["id"],
                    f"{spec['label']} needs exactly one input that says WHEN "
                    "to send - one Condition, or one Schedule. Other rules may "
                    "be wired in as well; their values become placeholders.")
                continue
            err(n["id"], f"{spec['label']} needs exactly one Analytics input")
            continue
        # A capture node with its System Control switch OFF cannot write
        # anything, so it is not compiled -- which is what makes it read as
        # unwired on the canvas rather than as a node that looks armed and
        # silently does nothing. Flip the switch back on and it returns; the
        # operator's wire is never touched.
        kind = spec.get("output_type")
        if capture is not None and kind in ("snapshot", "record") \
                and not capture.get("snap" if kind == "snapshot" else "record", True):
            warn(n["id"],
                 f"{spec['label']} is switched off - turn "
                 f"{'Snapshots' if kind == 'snapshot' else 'Clips'} on in "
                 "System Control to arm it")
            off_nodes.add(n["id"])
            continue

        d = n["data"] or {}
        # A Zone Switch whose zone is unpicked, or deleted (a camera removed
        # with its zones), waits like a Zone node does: warned and switched
        # off. As an error it stopped the whole canvas applying over one
        # switch that has nothing to flip.
        if kind == "zone_switch" and str(d.get("zone") or "") not in zone_by_id:
            warn(n["id"], "waiting for a zone - pick which zone this Zone "
                          "Switch flips; it is switched off until then")
            off_nodes.add(n["id"])
            continue
        # A terminal this box has HANDED OVER (to Node-RED, a customer
        # script) is not ours to drive. Treated exactly like a capture node
        # whose switch is off: warned, not compiled, and left on the canvas
        # reading as idle rather than deleted -- taking the operator's node
        # away because somebody changed a box-wide setting would lose work
        # that becomes correct again the moment the terminal comes back.
        if kind in ("io_out", "io_pwm"):
            _port = str(d.get("port") or "").upper()
            if _port and _port in _released_ports():
                warn(n["id"],
                     f"{_port} has been handed to another program on the "
                     f"Board I/O page - this node is idle until it is taken "
                     f"back")
                off_nodes.add(n["id"])
                continue
        # A PWM port asked to FOLLOW a value is a publisher whose subscriber is
        # a lamp: it carries a value key and runs on the tick, not on an alert.
        # Same shape as the telemetry nodes below, chosen per node rather than
        # per type because the same node also does "go to 40% when this fires".
        follows = (spec.get("output_type") == "io_pwm"
                   and str(d.get("when") or "alert") == "always")
        # A telemetry publisher sends the VALUE on a schedule, so it needs no
        # trigger -- requiring one would mean "you may only publish this number
        # while something is wrong", which is the opposite of telemetry.
        if spec.get("no_trigger") or follows:
            for i, fn in enumerate(fan):
                oid = f"wf_{n['id']}"[:40] if not i else f"wf_{n['id']}_{i}"[:40]
                metrics = functions_service.OUTPUTS[fn["type"]]
                metric = (d.get("metric") if d.get("metric") in metrics
                          else (wire_metric(n, fn, metrics) or metrics[0]))
                doc = {"id": oid,
                       "label": f"{fn['name']} → {spec['label']}"[:60],
                       "type": spec["output_type"],
                       "key": trig_out_key(n, fn) or f"fn_{fn['id']}_{metric}",
                       "trigger": "any", "enabled": True}
                doc.update({k: d[k] for k in spec["data"]
                            if k in d and k != "metric"})
                if spec["output_type"] == "mqtt_publish":
                    doc.update(publish_follow.follow(fn, CLOCK_FN_METRIC))
                try:
                    outputs.append(dc._validate_output(doc))
                except (ValueError, TypeError) as exc:
                    err(n["id"], str(exc))
            continue
        for i, fn in enumerate(fan):
            # One document per rule wired in (fan above); the first
            # keeps the node's own id, so a one-rule output never moves.
            oid = f"wf_{n['id']}"[:40] if not i else f"wf_{n['id']}_{i}"[:40]
            # A FORMULA needs no Alert after it. Its answer IS the decision: an
            # AND of two alarms comes out 0 or 1, and "fire while it is not zero"
            # is not a threshold somebody chose, it is what true and false mean.
            # Demanding an Alert there asked the operator to write down a number
            # the graph had already decided -- and they reach a Formula by wiring
            # alarms INTO it, so the answer to "when" was visibly settled two
            # nodes ago.
            #
            # Only expr. A counter's `count` is a quantity, not a verdict: "fire
            # while it is not zero" would be a threshold invented on the
            # operator's behalf, and that is what an Alert is for.
            #
            # cooldown 0 means "every rising edge, never while it holds" (see
            # _check_triggers), so a formula that stays true raises ONE event
            # rather than one per frame -- and the capture node's own pacing
            # (once per violation, per id, interval) still decides how often a
            # photo is actually taken.
            if not (fn.get("trigger") or fn.get("triggers")) and fn.get("type") == "expr":
                fn.setdefault("triggers", []).append({
                    "metric": "value", "op": ">", "value": 0.0,
                    "severity": "info", "message": fn.get("name") or "Formula",
                    "cooldown_s": 0})
            # A SCHEDULE arms itself for the same reason, and more plainly: the
            # clock reaching 12:00 IS the decision. Asking for a Condition after it
            # would mean writing down a threshold on a value that is only ever 1 or
            # 0, and asking for an Alert would say a report is something going
            # wrong. `due` is 1 for one tick, so cooldown 0 sends exactly once.
            # An INJECT the same way: every press, and every repeat, is one tick
            # of `fired` = 1.
            if not (fn.get("trigger") or fn.get("triggers")) \
                    and fn.get("type") in CLOCK_FN_METRIC:
                fn.setdefault("triggers", []).append({
                    "metric": CLOCK_FN_METRIC[fn["type"]], "op": ">", "value": 0.0,
                    "severity": "info",
                    "message": fn.get("name") or ("Schedule"
                                                  if fn["type"] == "schedule"
                                                  else "Inject"),
                    "cooldown_s": 0})
            if not (fn.get("trigger") or fn.get("triggers")):
                # One instruction, because there is now one way. This used to
                # offer two -- "add a trigger to the analytics node OR wire an
                # Alert" -- and the first of those no longer exists: the inline
                # trigger is gone and every graph that had one was migrated to an
                # Alert. An error that tells you to do something impossible is
                # worse than no error at all.
                # A Formula never reaches here (it arms itself, above), so the
                # instruction is about a MEASUREMENT: a count, a clock, a speed.
                # Those need somebody to say which value is the interesting one.
                # Compare is offered beside Alert because it does arm an output
                # and always has -- naming only the Alert made a working shape
                # look illegal.
                err(n["id"], f"{spec['label']} fires on a decision, and "
                             f"{fn.get('name') or 'this rule'} only measures - put "
                             f"a Condition (or a Compare) between it and this "
                             f"{spec['label']} and set the threshold there. A "
                             "Formula needs neither: its answer is already the "
                             "decision.")
                continue
            doc = {"id": oid,
                   "label": f"{fn['name']} → {spec['label']}"[:60],
                   "type": spec["output_type"],
                   # Function triggers raise events of type "alert" (app run.py);
                   # the `function` field narrows firing to THIS function only.
                   "trigger": "alert",
                   "function": fn["id"],
                   "enabled": True}
            doc.update({k: d[k] for k in spec["data"] if k in d})
            # "Follow the rule" is not a runtime guess. A capture node left blank
            # is resolved HERE, to the camera its rule already reads -- the one
            # named on the Detection Stream (or implied by the rule's zone), which
            # compile worked out above and stored on the function. Left blank the
            # app would fall back to the system's FIRST camera, so on a two-camera
            # system a rule watching camera 2 saved a photo of camera 1 and there
            # was nothing on the canvas that said so.
            # "*" (every camera) is left blank on purpose: the rule genuinely runs
            # on all of them, so the app picks the one that fired.
            # A camera the node still NAMES but the system no longer has (swapped
            # for another, deleted) would make the app skip every capture with only
            # a log line to show for it. Cleared here so the capture falls through
            # to the rule's own camera below and keeps working -- quietly, because
            # it does keep working, and a warning on every save of a graph that
            # captures exactly what it should is noise.
            if kind in ("snapshot", "record") and cameras is not None:
                named = str(doc.get("camera") or "").strip()
                if named and named not in cameras:
                    doc["camera"] = ""
            if kind in ("snapshot", "record") and not str(doc.get("camera") or ""):
                rule_cam = str((fn.get("params") or {}).get("camera") or "").strip()
                if rule_cam and rule_cam != functions_service.ALL_CAMERAS:
                    doc["camera"] = rule_cam
            # Any Delay on the path between the rule and this output: the engine
            # holds the send by that long. Carried on the OUTPUT rather than the
            # function, because two outputs after the same rule may legitimately
            # want different waits.
            wait = upstream_delay(n["id"])
            if wait > 0:
                doc["delay_s"] = round(wait, 2)
            # MESSAGE outputs may be wired to several rules at once, and then say
            # so: every value feeding this node becomes a {placeholder} its
            # subject and body can use. Built from the actual wires rather than
            # typed, which is why the editor offers buttons -- a name nothing is
            # wired to would render as empty text in somebody's inbox, and there
            # would be no way to tell that from a value that happened to be zero.
            # A chat typed into the node goes to a PERSON or a group instead of
            # the bot's own saved chat. Checked here because the mistake is silent
            # everywhere else: Telegram answers "chat not found" to a thread the
            # app never surfaces, so a shift report addressed to a mistyped id
            # simply stops arriving with nothing on the canvas to say why.
            # WHO hears a Telegram node (Tok Aba, 2026-10-14): the people
            # linked to its bot -- all of them, or the ones picked -- resolved
            # by the app at SEND time through this box's master, so a person
            # claimed later is included and a removed one never is. Only the
            # mode travels for "all" (no list of ids to go stale); "selected"
            # carries numeric Telegram ids and nothing else. No to_mode is the
            # typed chat id, exactly as before (byte-identical compile).
            tg_mode = (str((n.get("data") or {}).get("to_mode") or "").strip()
                       if spec["output_type"] == "telegram" else "")
            if tg_mode in ("all", "selected"):
                doc["to_mode"] = tg_mode
                doc["to_url"] = (f"{_master_base()}/api/telegram/recipients/"
                                 f"resolve?out={oid}")[:200]
                if tg_mode == "selected":
                    picked = []
                    for pid in (n.get("data") or {}).get("to_people") or []:
                        pid = str(pid or "").strip()
                        if re.fullmatch(r"-?\d{1,20}", pid) and pid not in picked:
                            picked.append(pid)
                    doc["to_people"] = picked[:50]
                    if not picked:
                        warn(n["id"], "Nobody is selected - tick the people "
                                      "this message should go to, or choose "
                                      "All linked people")
            if spec["output_type"] == "telegram" and tg_mode not in ("all", "selected"):
                chat = str(doc.get("chat_id") or "").strip()
                if chat and not _CHAT_ID_RE.match(chat):
                    err(n["id"], _tg_bots.bad_chat(chat))
                    continue
                if not chat:
                    # A WARNING, not an error, and the difference matters: a bot
                    # may still carry a chat of its own, and the compiler cannot
                    # see the bot list (that is filled in afterwards, where the
                    # config is). Refusing here would take a graph that has been
                    # sending for months off the air the next time anybody pressed
                    # Save. So: say it, and let the send report the rest.
                    warn(n["id"], "this Telegram node names no chat - type the "
                                  "chat id of the person, group or channel that "
                                  "should hear it. Bots no longer carry one.")
            if spec["output_type"] in ("email", "telegram"):
                wired = formula_inputs(n["id"], preds.get(n["id"]), sw_bases, preds)
                # ...but never the SCHEDULE that fired it. Its `due` is 1 for the
                # tick the message goes out on, so as a placeholder it is a "1" in
                # a sentence -- an offer of a value where there is no value, on the
                # one node guaranteed to be wired into every scheduled report.
                clock_keys = {f"fn_{f['id']}_{CLOCK_FN_METRIC[f['type']]}"
                              for f in fn_meta.values()
                              if f and f.get("type") in CLOCK_FN_METRIC}
                for name in [k for k, key in wired.items() if key in clock_keys]:
                    wired.pop(name)
                if wired:
                    doc["vars"] = wired
                # A CSV node wired in attaches its rows as a file, and lets {csv}
                # be written in the message. A value placeholder answers "how
                # many", which is the wrong shape for "which ones" -- a shift's
                # rejects are a table, and a table in a sentence is a sentence
                # nobody reads.
                #
                # It is its own NODE rather than a checkbox on the message because
                # "turn this query into a file" is a step, and a step that two
                # outputs share should be one thing on the canvas rather than the
                # same box ticked twice. The wire also says which query, which a
                # checkbox could not on a node wired to several.
                #
                # EVERY CSV wired in, in wire order (Tok Aba, 2026-10-14: the
                # pcb_sensata report had two and arrived with one). The FIRST
                # still fills the old csv_* fields exactly as before, so a
                # message with one file compiles byte-identical and an older
                # app attaches that first file; two or more add `csvs`, one
                # entry per file with its own address (_csv_list).
                specs = []
                for csv_src in [s for _e, s in preds.get(n["id"], ()) or ()
                                if s["type"] == "storage.csv"]:
                    spec_csv = _csv_spec(csv_src, preds, tables, warn)
                    if spec_csv:
                        specs.append((csv_src, spec_csv))
                if specs:
                    csv_src, spec_csv = specs[0]
                    doc.update(spec_csv)
                    # The app has no database, so the document carries the
                    # local address that will render the rows for it -- the
                    # same shape a query variable uses.
                    doc["csv_url"] = (f"{_master_base()}/api/workflow/query-csv"
                                      f"?out={oid}")[:200]
                    # ...and which node on the canvas this file came FROM. The
                    # spec rides on the message, so this is the only way back
                    # from the CSV node an operator has open to the document
                    # holding its statement.
                    doc["csv_node"] = f"wf_{csv_src['id']}"[:40]
                if len(specs) > 1:
                    doc["csvs"] = _csv_list(specs, oid)
                # A {name} the message uses that nothing here fills is sent
                # as written, braces and all (Tok Aba, 2026-10-14: a report
                # arrived saying "{violation_summary}"). Said on the node,
                # with the nearest name that IS wired.
                files = [e["name"] for e in doc.get("csvs") or []] or \
                    ([doc["csv_name"]] if doc.get("csv_name") else [])
                fields = (("subject", "body") if spec["output_type"] == "email"
                          else ("message",))
                for name in unknown_placeholders(
                        [doc.get(f) for f in fields], wired, files):
                    warn(n["id"], unknown_placeholder_text(
                        name, list(wired) + _csv_tokens(files)))
            try:
                doc = dc._validate_output(doc)
            except (ValueError, TypeError) as exc:
                err(n["id"], str(exc))
                continue
            outputs.append(doc)

    # -- commands ------------------------------------------------------------
    # Compiled separately from outputs because they are not outputs: nothing
    # fires them, they answer a person. Each button carries the statement of
    # the Query wired to its port, copied the way a CSV node copies one --
    # what runs has to be what was compiled, not whatever the canvas says by
    # the time somebody taps a button next week.
    commands = []
    seen_words = {}
    for n in graph["nodes"]:
        if n["type"] != "integration.command":
            continue
        d = n.get("data") or {}
        word = tg_commands.clean_command(d.get("command"))
        if not word:
            err(n["id"], "name the command - the word people type after the "
                         "slash, like `summary`. Letters, numbers and "
                         "underscores.")
            continue
        if word in seen_words:
            err(n["id"], f"/{word} is already answered by another Command node "
                         "- two nodes answering one word means whichever "
                         "compiled last wins, silently.")
            continue
        seen_words[word] = n["id"]
        buttons = tg_commands.clean_buttons(d.get("buttons"))
        if not buttons:
            err(n["id"], "a command with no buttons has nothing to offer - "
                         "add at least one.")
            continue
        wired = {}
        for _e, src in preds.get(n["id"], ()) or ():
            if src["type"] in ("storage.query", "integration.photos"):
                # The port says WHICH button this answers.
                handle = str(_e.get("targetHandle") or "").strip()
                if handle.startswith("b"):
                    wired[handle] = src
        out_buttons = []
        for i, b in enumerate(buttons):
            src = wired.get(f"b{i}")
            if src is None:
                warn(n["id"], f"the {b['label']!r} button has nothing wired "
                              "to it, so it has nothing to answer with")
                continue
            if src["type"] == "integration.photos":
                sd = src.get("data") or {}
                # 24 hours, refused HERE where the node is: a button that could
                # ask for a month is a button that always overflows, and the
                # person who can fix it is the one looking at the canvas.
                if not tg_commands.album_period_ok(b["period"]):
                    err(n["id"],
                        f"the {b['label']!r} button sends pictures, and its "
                        f"period is wider than {tg_commands.MAX_ALBUM_HOURS} "
                        "hours - pick a day or less, or use Footage → Export "
                        "for a longer stretch (it sends the originals)")
                    continue
                # 0 means ALL -- kept as 0 rather than expanded here, so a
                # box whose ceiling changes does not need every saved graph
                # recompiled to mean what it said.
                try:
                    most = int(sd.get("most", _tg_photos.MAX_ALBUM))
                except (TypeError, ValueError):
                    most = _tg_photos.MAX_ALBUM
                out_buttons.append({**b, "kind": "photos",
                                    "camera": str(sd.get("camera") or "")[:60],
                                    "most": (_tg_photos.ALL if most <= 0 else
                                             min(_tg_photos.MAX_ALL, most))})
                continue
            statement = str((src.get("data") or {}).get("sql") or "").strip()
            tables_for = _query_tables(src, preds, tables)
            if not statement:
                warn(n["id"], f"the Query on {b['label']!r} has no statement yet")
                continue
            if not tables_for:
                warn(n["id"], f"the Query on {b['label']!r} reads no tables - "
                              "drag a Data Table onto its table port")
                continue
            out_buttons.append({**b, "kind": "query", "sql": statement[:4000],
                                "tables": tables_for})
        if not out_buttons:
            continue
        commands.append({
            "id": f"wf_{n['id']}"[:40],
            "command": word,
            "title": str(d.get("title") or "").strip()[:60] or word.title(),
            "intro": str(d.get("intro") or "").strip()[:400],
            "buttons": out_buttons})

    # -- watches -------------------------------------------------------------
    # Same shape as a command and for the same reason: the master runs it, so
    # what runs has to be what was compiled. The message travels WITH the
    # watch -- an operator writes it on the Telegram or Email node they
    # already know, and this copies it, so a watch never grew a second place
    # to write a message.
    watches = []
    for n in graph["nodes"]:
        if n["type"] != "integration.watch":
            continue
        d = n.get("data") or {}
        cfg = alert_watch.clean_watch(d)
        src = next((x for _e, x in preds.get(n["id"], ()) or ()
                    if x["type"] == "storage.query"), None)
        if src is None:
            err(n["id"], "a Watch runs a Query on a timer - wire the Query "
                         "whose number it should watch into it")
            continue
        statement = str((src.get("data") or {}).get("sql") or "").strip()
        tables_for = _query_tables(src, preds, tables)
        if not statement:
            err(n["id"], "the Query wired to this Watch has no statement yet")
            continue
        if not tables_for:
            err(n["id"], "the Query wired to this Watch reads no tables - "
                         "drag a Data Table onto its table port")
            continue
        sends = []
        for _e, dst in succs.get(n["id"], ()) or ():
            dd = dst.get("data") or {}
            if dst["type"] == "output.telegram":
                chat = str(dd.get("chat_id") or "").strip()
                if chat and not _CHAT_ID_RE.match(chat):
                    err(dst["id"], _tg_bots.bad_chat(chat))
                    continue
                if not chat:
                    warn(dst["id"], "this Telegram node names no chat - type "
                                    "the chat id that should hear the alert")
                sends.append({"kind": "telegram", "chat_id": chat[:40],
                              "message": str(dd.get("message") or "")[:1200]})
            elif dst["type"] == "output.email":
                to = str(dd.get("to") or "").strip()
                if not to:
                    err(dst["id"], "this Email node has nobody to send to")
                    continue
                sends.append({"kind": "email", "to": to[:400],
                              "account": str(dd.get("account") or "")[:40],
                              "subject": str(dd.get("subject") or "")[:200],
                              "body": str(dd.get("body") or "")[:2000]})
        if not sends:
            err(n["id"], "a Watch with nothing wired out sends nowhere - wire "
                         "a Telegram or an Email node after it")
            continue
        watches.append({"id": f"wf_{n['id']}"[:40], **cfg,
                        "sql": statement[:4000], "tables": tables_for,
                        "sends": sends[:alert_watch.MAX_WATCHES]})

    # -- the PICTURE each camera's detector is shown -------------------------
    #
    # A Pre-process compiles to per-CAMERA config rather than to a function:
    # it changes the frames going INTO the model, so there is nothing for a
    # rule to read and nothing for a card to bind to. Which camera comes from
    # the stream wired in, exactly the way a Class Filter's classes reach the
    # rules behind it -- by walking back to the source that names it.
    #
    # ONE node per camera. Two of them are refused by name rather than
    # chained: the steps inside one node are an ordered list the operator
    # arranged, and two nodes hanging off the same stream have no visible
    # order between them -- whichever the compiler picked first would be an
    # answer read off the JSON, not off the canvas. The edge rule already
    # stops them being wired in series; this catches the parallel pair.
    preprocess, pre_by_cam = [], {}
    for n in graph["nodes"]:
        if n["type"] != "filter.preprocess":
            continue
        d = n.get("data") or {}
        raw = d.get("steps") if isinstance(d.get("steps"), list) else []
        if not preds.get(n["id"]):
            # A WARNING, not an error: a node dragged out and not yet wired is
            # how this page is used, and the chain inside it is real work that
            # must not redden the canvas. It simply adjusts nothing until a
            # stream says which camera it belongs to.
            warn(n["id"], "a Pre-process with nothing wired in adjusts no "
                          "picture - wire in the Detection Stream of the "
                          "camera it applies to")
            continue
        if len(raw) > MAX_PREPROCESS_STEPS:
            # Cut to the cap rather than refused: the app stops at the same
            # number (preprocess.MAX_STEPS), so the steps past it were never
            # going to run, and failing the whole save over them would take
            # the rest of the canvas down with a picture setting.
            warn(n["id"], f"a Pre-process runs at most {MAX_PREPROCESS_STEPS} "
                          f"steps on every frame - the ones after that are "
                          f"not applied")
            raw = raw[:MAX_PREPROCESS_STEPS]
        cam = upstream_camera(n["id"])
        steps, bad, masks_bad = [], [], False
        for item in raw:
            step = _preprocess_step(item)
            if step is None:
                bad.append(str((item or {}).get("op")
                               if isinstance(item, dict) else item)[:20])
                continue
            if step["op"] == "mask":
                # Zone ids become polygons HERE, where the system's zones and
                # this chain's camera are both known. The app is handed
                # points: see _preprocess_mask.
                step, mask_err, mask_warn = _preprocess_mask(step, zone_by_id, cam)
                if mask_err:
                    err(n["id"], mask_err)
                    masks_bad = True
                    continue
                if mask_warn:
                    warn(n["id"], mask_warn)
            steps.append(step)
        if masks_bad:
            continue
        if bad:
            # Dropped silently, this is a chain that says one thing on the
            # canvas and does another on the box.
            err(n["id"], f"{', '.join(repr(b) for b in bad[:4])} is not "
                         "something this build can do to a picture - remove "
                         "the step, or update the system")
            continue
        for step in steps:
            if step["op"] == "sobel" and not (step["dx"] or step["dy"]):
                warn(n["id"], "this Sobel step finds edges in no direction - "
                              "set across or down to 1, or it passes the "
                              "picture through")
        if cam and cam != functions_service.ALL_CAMERAS \
                and cameras is not None and cam not in cameras:
            # The camera was removed under the node. Warned rather than
            # errored, and written anyway: the app ignores a chain for a
            # camera it does not have, and a system that will not compile
            # because of a camera somebody unplugged is worse than one that
            # says so.
            warn(n["id"], "this Pre-process names a camera this system no "
                          "longer has - re-pick the camera on the Detection "
                          "Stream wired into it")
        if not steps:
            # An EMPTY chain writes nothing at all. "No steps" and "no node"
            # must reach the app as the same thing, or an operator who
            # emptied a chain would be left with a key claiming to adjust
            # the picture and a node that plainly does not.
            continue
        if cam in pre_by_cam:
            err(n["id"], f"two Pre-process nodes adjust the same camera "
                         f"({pre_by_cam[cam]} and {n['id']}) - one camera has "
                         f"one picture, so put every step on one of them")
            continue
        pre_by_cam[cam] = n["id"]
        preprocess.append({"camera": cam, "preview": bool(d.get("preview")),
                           "steps": steps})
    # An "every camera" chain beside a chain for one camera: the two disagree
    # about that camera and nothing on the canvas says which wins.
    if functions_service.ALL_CAMERAS in pre_by_cam and len(pre_by_cam) > 1:
        err(pre_by_cam[functions_service.ALL_CAMERAS],
            "this Pre-process covers every camera, and another one covers a "
            "single camera as well - pick one or the other")

    # SNAPSHOT STREAMS. A Detection Stream set to Snapshot detects only while
    # something wired into its snap input says so, and only for the number of
    # frames that something asks for. What reaches the app is one entry per
    # stream -- which camera, and which published value starts it -- written to
    # the camera entry like a Pre-process chain (dc.set_snapshot). A graph with
    # no snapshot stream adds nothing, so every older graph compiles key for key.
    snapshot = []
    snap_by_det = {}
    for e in snap_wires:
        snap_by_det.setdefault(e["target"], []).append(e)
    for n in graph["nodes"]:
        if n["type"] != "source.detection":
            continue
        d = n.get("data") or {}
        mode = str(d.get("capture") or "video")
        wires = snap_by_det.get(n["id"], [])
        if mode != "snapshot":
            if wires:
                warn(n["id"], "something is wired into this Detection Stream's "
                              "snap input but its Capture is Video, so it "
                              "detects every frame - set Capture to Snapshot")
            continue
        if not wires:
            warn(n["id"], "this Detection Stream is set to Snapshot but nothing "
                          "is wired into its snap input, so it never detects - "
                          "wire an Inject, a Trigger or a sensor into it")
            continue
        sources = []
        for wire in wires:
            src = nodes.get(wire["source"])
            if src is None:
                continue                    # switched off or deleted: not a source
            sd = src.get("data") or {}
            try:
                frames = int(sd.get("frames") or 1)
            except (TypeError, ValueError):
                frames = 1
            frames = max(1, min(MAX_SNAP_FRAMES, frames))
            if src["type"] == "common.inject":
                fid = _slug(sd.get("fnId"))
                if not fid:
                    warn(src["id"], "this Inject has no id yet - open and save "
                                    "the workflow again")
                    continue
                sources.append({"kind": "inject", "fn": fid,
                                "key": f"fn_{fid}_fired", "frames": frames})
                continue
            got = input_alias(src, None, sw_bases, preds)
            if not got:
                warn(src["id"], f"{REGISTRY[src['type']]['label']} cannot drive "
                                "a snapshot here - it publishes no value to "
                                "start one")
                continue
            kind = "trigger" if src["type"] == "output.trigger" else "level"
            sources.append({"kind": kind, "fn": got[0], "key": got[1],
                            "frames": frames})
        if sources:
            snapshot.append({"camera": str(d.get("camera") or "").strip(),
                             "sources": sources,
                             **({"idle": d["camera_idle"]}
                                if d.get("camera_idle") in ("close", "keep") else {})})
        else:
            warn(n["id"], "nothing usable is wired into this Detection Stream's "
                          "snap input, so it never detects")

    # A TEST OF A CYCLE TIME'S live_s AGAINST A LIMIT ("in the zone more than
    # 20 s") is about each object's own stay. The If / Else if compiles to a
    # rule of its own, so the clock and the limit are written on it: the
    # engine then names only the objects past the limit, and a Snapshot taken
    # on it reports that object's time against the limit (app/functions.py).
    dwell_by_id = {f["id"]: f for f in functions if f.get("type") == "zone_dwell"}
    timed = []
    for f in functions:
        if f.get("type") != "expr":
            continue
        m = _DWELL_LIMIT_RE.match(str((f.get("params") or {}).get("expr") or ""))
        if m and m.group(1) in dwell_by_id:
            timed.append((f, m))
    shifted = set()
    for f, m in timed:
        cid, op, lim = m.group(1), m.group(2), m.group(3)
        f["params"]["timer_fn"] = cid
        f["params"]["timer_s"] = float(lim)
        tol = float((dwell_by_id[cid].get("params") or {}).get("tolerance_s") or 0)
        if tol <= 0 or (cid, op, lim) in shifted:
            if tol > 0:
                f["params"]["timer_tol_s"] = tol
            continue
        # The tolerance moves the line the rule tests: past limit + tolerance
        # is the violation, and "not over the limit" says the same thing.
        shifted.add((cid, op, lim))
        f["params"]["timer_tol_s"] = tol
        old = f"fn_{cid}_live_s {op} {lim}"
        new = f"fn_{cid}_live_s {op} {float(lim) + tol:g}"
        for g in functions:
            gp = g.get("params") or {}
            if g.get("type") == "expr" and old in str(gp.get("expr") or ""):
                gp["expr"] = gp["expr"].replace(old, new)

    # A Trigger set to WAIT FOR THE TIMER: the photo and the Data Table row
    # wired after it are completed when the stay ends. Only behind a rule that
    # measures a stay and can say when it is over, otherwise it is dropped here
    # (and the canvas does not offer it).
    for o in outputs:
        fid = o.get("function")
        fn = next((f for f in functions if f.get("id") == fid), None)
        if fn is None or o.get("type") not in ("snapshot", "dataset") \
                or not (fn.get("trigger") or {}).get("wait_end"):
            continue
        why = _hold_blocker(fn, functions)
        if why:
            warn(wait_nodes.get(fid) or "", why)
        elif o["type"] == "snapshot":
            o["hold"] = True
        else:
            o["wait_end"] = True

    # WHAT RUNS WITHOUT DETECTION. Decided by the graph: whatever a Detection
    # Stream reaches, through any wire, waits for detection; everything else
    # -- an Inject, a Schedule, MQTT, Modbus, the Math and outputs after them
    # -- runs on its own when detection is off (the app with its AI off).
    frames = frames_fed(graph["nodes"], graph["edges"])
    frames_free = logic_artifacts(functions, outputs, frames, fn_meta,
                                  cond_meta, switch_meta, graph["nodes"])
    # The canvas marks what the app will actually skip -- the same
    # decision, not a second one drawn from the wires alone.
    waiting = frames_free.pop("nodes")
    return {"functions": functions, "outputs": outputs, "widgets": widgets,
            "catalog": catalog, "tables": declared_tables,
            "commands": commands, "watches": watches,
            "frames_free": frames_free,
            **({"class_filters": cf} if (cf := class_filter_service.plans(  # only when named
                graph, canvas_nodes, functions, outputs, fn_meta, cond_meta, switch_meta)) else {}),
            # What each ON/OFF point gates (runs only while its state
            # matches), when any point reaches anything.
            **({"state_gates": sg} if (sg := state_points.plans(
                graph, gate_only, functions, outputs, fn_meta, cond_meta,
                switch_meta)) else {}),
            "waits_for_detection": sorted(canvas_waits(canvas_nodes,
                                                       canvas_edges, waiting)),
            # Only when there IS one. A graph drawn before this node existed
            # compiles to exactly the artifacts it always did, key for key --
            # which is the whole of the migration story for a new node type.
            **({"preprocess": preprocess} if preprocess else {}),
            # Only when a stream is in Snapshot mode with something wired in.
            **({"snapshot": snapshot} if snapshot else {}),
            "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# artifact writes
# ---------------------------------------------------------------------------

def _with_mail_settings(outputs, slug, config_dir):
    """Fill each email output's SMTP settings from the system's mail config.

    The detection app sends the mail itself -- it runs standalone and imports
    nothing from core/ -- so the server it should talk to has to travel WITH
    the output document. The operator wiring an Email node therefore only ever
    types recipients; changing mail providers stays one edit on the Email
    settings rather than one per node.

    A node NAMES one of the box's mailboxes (`account`, an id from
    Settings > Email); blank means the default, so a box with one mailbox
    never has to choose. The node itself carries no credentials -- it used to
    take a sender and an app password of its own, which put the same secret in
    as many nodes as used it and made rotating it an archaeology exercise.

    Falls back to the system's own email config when there are no box
    mailboxes at all, so a deployment configured before this existed keeps
    sending while somebody moves it across.

    This does put the SMTP app password in the system's config.local.toml.
    It already sits in plaintext in the mail config on the same box, and MQTT
    passwords have always travelled the same way, so it is not a new class of
    exposure -- but it is worth knowing.
    """
    if not any(o.get("type") == "email" for o in outputs):
        return outputs
    from core.services import mail_accounts as mail_service
    try:
        legacy = {}
        from core.services import email as email_service
        legacy = email_service.load(slug, config_dir)
    except Exception:                                       # noqa: BLE001
        legacy = {}
    out = []
    for o in outputs:
        if o.get("type") != "email":
            out.append(o)
            continue
        acc = None
        try:
            acc, _problem = mail_service.resolve(o.get("account"), config_dir)
        except Exception:                                   # noqa: BLE001
            acc = None
        if acc:
            out.append({**o,
                        "smtp_host": acc["smtp_host"],
                        "smtp_port": int(acc["smtp_port"]),
                        "smtp_user": acc["sender"],
                        "smtp_pass": acc["app_password"],
                        "from_addr": acc["sender"]})
            continue
        out.append({**o,
                    "smtp_host": legacy.get("smtp_host") or "smtp.gmail.com",
                    "smtp_port": int(legacy.get("smtp_port") or 587),
                    "smtp_user": legacy.get("sender") or "",
                    "smtp_pass": legacy.get("app_password") or "",
                    "from_addr": legacy.get("sender") or ""})
    return out


def _with_bot_settings(outputs, slug, config_dir):
    """Fill each Telegram output's token from the bot assigned to this SYSTEM.

    Same seam as _with_mail_settings, for the same reason: the detection app
    runs standalone and imports nothing from core/, so the credential has to
    travel WITH the output document.

    Which bot is no longer the node's decision. A bot belongs to one system and
    a system to one bot (services/telegram_bots.py), so a node's `account` --
    written when every bot on the box was offered in a picker -- is read only
    as a fallback for a graph saved before that. Rotating a token stays one
    edit on the Settings page rather than one per node either way.

    A node that still carries its OWN token (written before the bot list
    existed, or imported from an older template) is left exactly as it is --
    it works, and silently repointing it at another bot would send somebody's
    alerts into a different chat without saying so.
    """
    if not any(o.get("type") == "telegram" for o in outputs):
        return outputs
    from core.services import telegram_bots as tg_service    # noqa: PLC0415
    try:
        assigned = tg_service.for_system(slug, config_dir)
    except Exception:                                        # noqa: BLE001
        assigned = None
    out = []
    for o in outputs:
        if o.get("type") != "telegram":
            out.append(o)
            continue
        if str(o.get("bot_token") or "").strip() and not o.get("account"):
            out.append(o)
            continue
        bot = assigned
        if bot is None:
            # No bot assigned to this system. A graph saved when the node
            # chose its own still names one, and taking that away would stop
            # a deployment that has been sending for months the next time
            # anybody pressed Save.
            try:
                bot, _problem = tg_service.resolve(o.get("account"), config_dir)
            except Exception:                                # noqa: BLE001
                bot = None
        if not bot:
            out.append(o)
            continue
        # The bot supplies the TOKEN always, and the chat only when the node
        # did not name one. A bot is "who this box posts as"; the chat is
        # "who hears it", and those are different questions -- one bot posting
        # into the maintenance group, the shift channel and one supervisor's
        # own chat is the ordinary shape, and before this every node was
        # silently rewritten to the bot's single saved chat.
        chat = str(o.get("chat_id") or "").strip()
        out.append({**o, "bot_token": bot["token"],
                    "chat_id": chat or bot["chat_id"]})
    return out


def python_enabled(config_dir=None):
    """May this box run Python written in the browser?

    Off unless config/workflow.toml says otherwise, and that file is on the
    BOX -- setting it needs shell access, which is the whole point. A stolen
    admin session on a customer's deployment must not be able to turn code
    execution on, and a developer's own board is one line away from it.
    """
    cfg = (config_mod.load("workflow", config_dir) or {}).get("python") or {}
    return bool(cfg.get("enabled"))


def python_nodes(graph):
    """Every Python Function node in a graph. Used by the routes to decide
    whether a save needs the developer tier."""
    return [n for n in (graph.get("nodes") or [])
            if n.get("type") == "analytics.python"]


def write_python_code(systems_dir, config_dir, slug, graph):
    """Write each Python node's source beside the system's app, and remove the
    files of nodes that are gone. Returns the handler names written.

    Nothing is written when the box has not opted in -- so turning the flag
    off and restarting leaves a system that cannot run browser-written code
    even if the graph still contains it.
    """
    from core.services import provision as provision_service   # noqa: PLC0415
    root = provision_service._pkg_root(systems_dir, slug)
    folder = os.path.join(str(root), slug, "app", "wf_code")
    nodes = python_nodes(graph) if python_enabled(config_dir) else []
    wanted = {}
    for n in nodes:
        fid = _slug((n.get("data") or {}).get("fnId"))
        code = str((n.get("data") or {}).get("code") or "")
        if fid and code.strip():
            wanted[f"wf_{fid}"] = code
    try:
        if not wanted and not os.path.isdir(folder):
            return []
        os.makedirs(folder, exist_ok=True)
        init = os.path.join(folder, "__init__.py")
        if not os.path.exists(init):
            with open(init, "w", encoding="utf-8") as f:
                f.write("# Written by the Workflow editor. Do not edit here.\n")
        digests = {}
        for handler, code in wanted.items():
            path = os.path.join(folder, f"{handler}.py")
            tmp = path + ".tmp"
            # BINARY write, and hash exactly these bytes. Text mode would
            # translate newlines on a Windows-hosted hub, so the file on disk
            # would not be the bytes that were hashed and the app would
            # (correctly) refuse to import its own editor's code.
            payload = code.encode("utf-8")
            with open(tmp, "wb") as f:
                f.write(payload)
            os.replace(tmp, path)
            digests[f"{handler}.py"] = hashlib.sha256(payload).hexdigest()
        # A node deleted from the canvas must not leave code running.
        for name in os.listdir(folder):
            if name.startswith("wf_") and name.endswith(".py") \
                    and name[:-3] not in wanted:
                os.remove(os.path.join(folder, name))
        # The manifest the app checks before importing anything from here.
        # Without it (or with a file whose hash does not match) the app
        # refuses to load: dropping a .py into this folder by hand must not
        # be a way to run code in the detection loop, and the folder is a
        # plain directory on a box people have shell access to.
        _write_wf_manifest(folder, digests)
    except OSError as exc:
        print(f"[WF] could not write python code for {slug}: {exc}")
        return []
    return sorted(wanted)


#: Name of that manifest. Mirrored in systems/demo/app/extensions.py, which
#: reads it -- the app imports nothing from core, so the constant is
#: duplicated on purpose (same rule as the expression whitelist).
WF_MANIFEST = ".wf_manifest.json"


def _write_wf_manifest(folder, digests):
    path = os.path.join(folder, WF_MANIFEST)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(digests, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def enforce_python_policy(systems_dir, config_dir, slug):
    """Remove browser-written Python when the box has not opted in.

    write_python_code() already refuses to WRITE while the flag is off, but
    that only runs when someone saves a workflow -- so a box whose flag was
    turned off kept executing yesterday's code until the next save, which is
    the opposite of what a kill switch means. The hub calls this at boot, so
    flipping the flag and restarting is enough.

    Returns True when something was removed.
    """
    if python_enabled(config_dir):
        return False
    from core.services import provision as provision_service   # noqa: PLC0415
    root = provision_service._pkg_root(systems_dir, slug)
    folder = os.path.join(str(root), slug, "app", "wf_code")
    if not os.path.isdir(folder):
        return False
    removed = False
    try:
        for name in os.listdir(folder):
            if name.startswith("wf_") and name.endswith(".py"):
                os.remove(os.path.join(folder, name))
                removed = True
        _write_wf_manifest(folder, {})
    except OSError as exc:
        print(f"[WF] could not clear python code for {slug}: {exc}")
        return False
    if removed:
        print(f"[WF] {slug}: [python].enabled is off - removed "
              "browser-written Python from this system")
    return removed


def _preprocess_by_camera(systems_dir, slug, entries):
    """Compiled chains -> {camera id: {steps, preview}}, cameras resolved.

    A Detection Stream may name no camera ("the system's first") or every
    camera ("*"), and both are answers the COMPILER cannot give: it is a pure
    function of the graph and the camera list it is handed is a set, with no
    first in it. So the two are carried through compile as they were written
    and resolved here, against the system's real, ordered list -- the same
    place set_zones resolves a zone's default camera, and for the same
    reason.
    """
    try:
        cams = [c["id"] for c in dc.cameras(systems_dir, slug)]
    except (OSError, ValueError, KeyError):
        cams = []
    out = {}
    for entry in entries:
        named = str(entry.get("camera") or "").strip()
        if named == functions_service.ALL_CAMERAS:
            targets = list(cams)
        elif named:
            targets = [named]
        else:
            targets = cams[:1]
        for cam in targets:
            out[cam] = {"steps": entry.get("steps") or [],
                        "preview": bool(entry.get("preview"))}
    return out


def _snapshot_by_camera(systems_dir, slug, entries):
    """Compiled snapshot streams -> {camera id: {sources}}, cameras resolved
    the same way a Pre-process chain's are (blank = the first camera). Two
    streams that name one camera pool their sources."""
    try:
        cams = [c["id"] for c in dc.cameras(systems_dir, slug)]
    except (OSError, ValueError, KeyError):
        cams = []
    out = {}
    for entry in entries:
        named = str(entry.get("camera") or "").strip()
        if named == functions_service.ALL_CAMERAS:
            targets = list(cams)
        elif named:
            targets = [named]
        else:
            targets = cams[:1]
        for cam in targets:
            have = out.setdefault(cam, {"sources": []})
            have["sources"].extend(entry.get("sources") or [])
            if entry.get("idle") in ("close", "keep"):
                have["idle"] = entry["idle"]
    return out


def _app_reads_state_points(systems_dir, slug):
    """Does this system's OWN copy of the app know the ON/OFF points
    (function type state_watch)? Every system runs its own copy under the
    store, and one from before them would log "unknown type" for every
    Detection Stream and Class Filter on each config load. No copy at all (a
    test, a paired device) is taken as yes: the documents go out as
    compiled."""
    from core.services import detector_control as real_dc     # noqa: PLC0415
    path = os.path.join(real_dc._pkg_dir(systems_dir, slug), "app",
                        "functions.py")
    try:
        with open(path, encoding="utf-8") as f:
            return '"state_watch"' in f.read()
    except OSError:
        return True


def apply_artifacts(systems_dir, config_dir, slug, compiled):
    """Write the compiled artifacts. Order matters: functions -> outputs ->
    widgets, so a mid-failure leaves the engine consistent (a widget binding a
    missing stat renders empty; the reverse -- an output bound to a function
    that never compiled -- would fire never or wrongly)."""
    # The canvas owns what the canvas compiled -- but NOT the whole function
    # table. A zone action (z_*) written by the Zones page has no node here
    # until sync_zone_actions has mirrored it, and replacing the table
    # wholesale deleted those: draw a zone, add a Count action, save the
    # workflow, and the count silently stopped existing. Compiled ids win
    # (the mirrored node IS that action), and anything else keeps running.
    # The table registry first: an output that records into a table must not
    # be live before the table it writes to is declared, or the first rows
    # land somewhere nothing can read them back from. Additive by design --
    # see services/datasets.py merge_declared for why a table no node writes
    # to is kept rather than swept away.
    if compiled.get("tables"):
        from core.services import datasets as datasets_service  # noqa: PLC0415
        datasets_service.apply_declared(systems_dir, slug, compiled["tables"])

    if any(f.get("type") == "state_watch" for f in compiled["functions"])             and not _app_reads_state_points(systems_dir, slug):
        # An app copy from before the ON/OFF points: they are left out
        # rather than logged as bad functions until the system is updated
        # (anything wired to one could not fire there anyway).
        compiled = {**compiled, "functions": [
            f for f in compiled["functions"] if f.get("type") != "state_watch"]}
    compiled_ids = {f["id"] for f in compiled["functions"]}
    kept_fns = [f for f in functions_service.get_functions(systems_dir, slug)
                if f["id"] not in compiled_ids
                and str(f.get("id") or "").startswith("z_")]
    functions_service.set_functions(systems_dir, slug,
                                    compiled["functions"] + kept_fns)

    # Merge outputs: everything hand-made (no wf_ prefix) is preserved;
    # compiled ones replace the previous compiled set wholesale.
    kept = [o for o in dc.get_outputs(systems_dir, slug)
            if not str(o.get("id", "")).startswith("wf_")]
    dc.set_outputs(systems_dir, slug,
                   kept + _with_bot_settings(
                       _with_mail_settings(compiled["outputs"], slug, config_dir),
                       slug, config_dir))

    # The Telegram commands this system answers. Written wholesale: unlike
    # outputs there is no hand-made set to preserve -- a command exists
    # because a node draws it, and one the canvas no longer has must stop
    # being answered rather than linger in a config nobody is looking at.
    try:
        dc.set_commands(systems_dir, slug, compiled.get("commands") or [])
    except (ValueError, TypeError) as exc:
        # A command that fails the stored-document check is a compiler bug,
        # not an operator mistake, and it must not take the whole save down
        # with it -- the graph and its outputs are already written.
        print(f"[WF] {slug}: commands not written: {exc}")

    # The threshold watches the master runs. Wholesale for the same reason as
    # commands: a watch exists because a node draws it, and one the canvas no
    # longer has must stop firing rather than linger in a config file.
    try:
        dc.set_watches(systems_dir, slug, compiled.get("watches") or [])
    except (ValueError, TypeError) as exc:
        print(f"[WF] {slug}: watches not written: {exc}")

    # Which rules and outputs run WITHOUT detection (logic_artifacts): the
    # app runs exactly these with its AI off. Wholesale, like the watches: the
    # canvas decides, and a rule it no longer has must not linger here.
    try:
        dc.set_logic(systems_dir, slug, compiled.get("frames_free"))
    except (ValueError, TypeError, OSError) as exc:
        print(f"[WF] {slug}: AI-off list not written: {exc}")
    # ...and what each named Class Filter holds back while paused.
    class_filter_service.apply(systems_dir, slug, compiled)
    # ...and what each ON/OFF point gates.
    state_points.apply(systems_dir, slug, compiled)

    # The picture each camera's detector is shown. The chain lands ON the
    # camera entry (beside `controls` and `measure`), because that is where
    # the app reads it from -- run.py hands pipe.cfg["preprocess"] to
    # app/preprocess.py for the camera it is opening.
    #
    # Wholesale for the same reason as commands and watches: a chain exists
    # because a node draws it, and a camera whose node was deleted must go
    # back to its own untouched picture rather than keep yesterday's
    # grayscale forever. dc.set_preprocess writes nothing when nothing
    # changed, so a graph with no Pre-process node touches no camera.
    try:
        dc.set_preprocess(systems_dir, slug,
                          _preprocess_by_camera(systems_dir, slug,
                                                compiled.get("preprocess") or []))
    except (ValueError, TypeError) as exc:
        print(f"[WF] {slug}: preprocess not written: {exc}")

    # Which cameras detect only on a snapshot, and what starts one. Wholesale,
    # like the chain above: a stream switched back to Video, or deleted, must
    # return its camera to every frame rather than keep yesterday's setting.
    # Nothing is written when nothing changed.
    # A system object from before snapshots (an older adapter, a stand-in) has
    # no set_snapshot: it keeps every frame, which is all it ever did.
    set_snapshot = getattr(dc, "set_snapshot", None)
    if set_snapshot is not None:
        try:
            set_snapshot(systems_dir, slug,
                         _snapshot_by_camera(systems_dir, slug,
                                             compiled.get("snapshot") or []))
        except (ValueError, TypeError) as exc:
            print(f"[WF] {slug}: snapshot not written: {exc}")

    # Dashboard nodes land on the ANALYTICS OVERVIEW page -- the one page
    # Analytics shows. They used to get a page of their own per workflow
    # ("workflow_<id>"), which meant a graph could silently add a tab, and the
    # widgets it produced lived somewhere other than where an operator arranges
    # things.
    #
    # Every page but the Dashboard ("main") and the Overview is swept out here.
    # Analytics already FOLDS those pages' widgets into the Overview on read
    # (analytics.compute_pages), so nothing is lost by this write -- it just
    # makes the fold permanent instead of recomputing it on every render.
    #
    # Within the overview page only the compiled widgets (id "wf_*") are
    # replaced; everything the operator added themselves stays. Their GRID
    # CELLS are carried across too: recompiling a graph must not rearrange a
    # layout someone spent an afternoon on.
    layout = builder_service.load(config_dir, slug)
    pages = [p for p in layout["pages"]
             if p["id"] in (builder_service.MAIN_PAGE_ID, "overview")]
    compiled_widgets = [w for wp in compiled.get("pages") or [] for w in wp["widgets"]]

    overview = next((p for p in pages if p["id"] == "overview"), None)
    previous = {w["id"]: w for w in (overview or {}).get("widgets", [])}
    merged = [w for w in (overview or {}).get("widgets", [])
              if not str(w.get("id", "")).startswith("wf_")]
    for w in compiled_widgets:
        was = previous.get(w["id"])
        if was is None:
            # NEVER auto-placed. A compiled card lands in the "From workflow"
            # picker only; the operator decides whether it belongs on the
            # Dashboard, the Analytics Overview, or nowhere. Auto-adding put
            # every new node's card onto pages the operator then had to tidy
            # -- placement is a decision, and it is theirs. (A card already
            # ON a page -- placed by the operator, or carried by a template's
            # authored layout -- keeps refreshing in place below.)
            continue
        new = dict(w)
        new["grid"] = was.get("grid") or w.get("grid") or {}
        # A unit the operator set in the Builder is theirs; the compiler only
        # sets it when the node says so, so an unset compiled unit means "no
        # opinion", not "clear it". The LOOK is not filled in from the row:
        # the Builder writes every look knob onto the node (card-style), so a
        # blank compiled colour is the operator's "reset to default", and
        # refilling it from the stored row was how a cleared card colour came
        # back on the next recompile. Title always comes from the node --
        # renaming a node must rename its card.
        if was.get("unit") and not w.get("unit"):
            new["unit"] = was["unit"]
        merged.append(new)

    # builder_service.validate raises above MAX_WIDGETS, and a raise here would
    # abandon the WHOLE layout write -- losing the operator's own widgets over a
    # graph that added one card too many. Drop the excess compiled cards
    # instead; compile already reported the overflow as a graph error.
    if len(merged) > builder_service.MAX_WIDGETS:
        keep, over = [], len(merged) - builder_service.MAX_WIDGETS
        for w in reversed(merged):
            if over and str(w.get("id", "")).startswith("wf_"):
                over -= 1
                continue
            keep.append(w)
        merged = list(reversed(keep))[:builder_service.MAX_WIDGETS]

    if overview is not None:
        overview["widgets"] = merged
    elif merged:
        pages.append({"id": "overview", "name": "Overview", "widgets": merged})

    # Refresh the LINKED COPIES. A card dragged from the "From workflow"
    # palette is a separate widget with its own id (so it can sit on the
    # Dashboard while the wf_ original sits on the Overview) -- but its BODY
    # belongs to the node it was dragged from, and a copy that never updates
    # is how a verdict kept saying VIOLATION after its labels were changed on
    # the canvas. `node` is the link; the operator's dressing and placement
    # stay theirs.
    by_node = {}
    for w in compiled_widgets:
        wid = str(w.get("id") or "")
        if wid.startswith("wf_"):
            by_node[wid[3:]] = w
    # A linked copy can be dressed differently from the node it was dragged
    # from (that is the point of it being a second widget), so every knob the
    # Builder lets an operator turn on a card belongs here, not just the
    # original four. Missing this was mostly masked -- card-style writes
    # straight onto the node, so the ORIGINAL wf_* card always looked right --
    # but a COPY's own label/value size, animation or bands would have been
    # silently reset to the node's on the next recompile. `title` is the
    # widget's own name field (the node's is `name`, folded into `title`
    # above by the compiler).
    _OPERATORS_OWN = ("id", "grid", "span", "unit", "capability", "sub",
                      "node", "title")
    # The copy's LOOK is kept whenever the row SPELLS IT OUT, blank included.
    # validate() writes every one of these on every save, so a blank here is
    # the operator's answer ("no card colour", "still", "brand accent"), not
    # a gap for the node to fill. Keeping them only when truthy was the bug:
    # an operator cleared the card colour on a copy of "Peak Hour", saved,
    # and the next recompile -- any canvas save -- painted the node's orange
    # straight back over it. Only a row written before a knob existed lacks
    # the key, and that one takes the node's.
    #
    # The BANDS are not here. A threshold is a statement about the metric,
    # not about the dressing (CARD_DATA_STYLE_FIELDS), so it lives on the
    # node and every card of that node -- the wf_* original and each linked
    # copy -- draws the same limit. Kept per copy, a limit moved on the
    # canvas never reached the chart the customer was looking at (a copy),
    # and the Builder's own edit of it went onto the copy's row and never
    # onto the node: two thresholds, each editor seeing its own.
    # A chart's axis titles, orientation, bar style and value labels
    # (chartmodel.LOOK_FIELDS) are look too. They were missing here, so on a
    # linked copy -- the only kind of card a Builder edit cannot send back to
    # its node (card-style speaks to "wf_<node>" ids) -- they were set in the
    # Builder and reset by the next workflow save. The row spells them out only
    # when set, which is exactly the case to keep.
    _OPERATORS_LOOK = ("color", "template", "bg", "label_size", "value_size",
                       "anim", "anim_replay", "by_day", "show_title", "align", "valign"
                       ) + tuple(_chartmodel.LOOK_FIELDS)
    compiled_by_id = {str(w.get("id") or ""): w for w in compiled_widgets}
    # The Inject BUTTONS, by the node they press. A button is not a compiled
    # card -- it is a catalogue entry dropped on a page -- so it is refreshed
    # from the catalogue: the node's current name and function. One whose node
    # is gone is kept and MARKED, so the page says so instead of drawing a
    # button that presses nothing (or failing to draw at all).
    buttons = {str(c.get("node") or ""): c
               for c in (compiled.get("catalog") or [])
               if c.get("kind") == "button"}
    for page in pages:
        rows = page.get("widgets") or []
        for i, w in enumerate(rows):
            wid = str(w.get("id") or "")
            if w.get("kind") == "button":
                entry = buttons.get(str(w.get("node") or ""))
                if entry is None:
                    rows[i] = {**w, "inject": {**(w.get("inject") or {}),
                                               "removed": True}}
                else:
                    rows[i] = {**w, "title": entry["title"],
                               "key": entry["key"],
                               "inject": dict(entry["inject"])}
                continue
            if wid.startswith("wf_"):
                # A compiled card an operator (or a template's authored
                # layout) placed on ANY page refreshes in place -- placement
                # stays a human decision, the body stays the compiler's.
                src = compiled_by_id.get(wid)
                if src is not None:
                    # Placement is the operator's; the LOOK is the node's,
                    # whole. The stored row's template/colour/bg used to
                    # fill in wherever the compiled one was blank -- meant
                    # for a layout dressed before the node held its look --
                    # but the Builder now writes every look knob onto the
                    # node, so a blank compiled colour IS the operator's
                    # choice (reset to default), and refilling it from the
                    # row put the old colour back on every recompile. Only
                    # the unit, which is not a look knob, still fills in.
                    keep = {k: w[k] for k in ("grid", "span") if w.get(k)}
                    if w.get("unit") and not src.get("unit"):
                        keep["unit"] = w["unit"]
                    rows[i] = {**src, **keep}
                continue
            src = by_node.get(str(w.get("node") or ""))
            if src is None:
                continue
            keep = {k: w[k] for k in _OPERATORS_OWN if w.get(k)}
            keep.update({k: w[k] for k in _OPERATORS_LOOK if k in w})
            kept_series = _series_with_own_colours(src.get("series"), w.get("series"))
            if kept_series is not None:
                keep["series"] = kept_series
            rows[i] = {**src, **keep}

    builder_service.save(config_dir, slug,
                         {"title": layout.get("title") or "", "pages": pages,
                          "hidden": layout.get("hidden") or [],
                          "placements": layout.get("placements") or {},
                          # The operator's per-card dressing overrides. Dropped
                          # here, every workflow save would strip the template
                          # -- or the card colour -- picked in the Builder for
                          # compiled cards.
                          "templates": layout.get("templates") or {},
                          "colors": layout.get("colors") or {},
                          "accents": layout.get("accents") or {},
                          # ...and the other two, which were left off: a
                          # package card's text sizes and its NAME live in
                          # these maps and nowhere else, so every workflow save
                          # was quietly resetting them.
                          "fonts": layout.get("fonts") or {},
                          "names": layout.get("names") or {}})


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------

def _package_layout_path(systems_dir, slug):
    return os.path.join(str(systems_dir), slug, "layout.json")


#: Node types whose data historically carried a baked-in alert. One rule now:
#: a measuring node measures, the Alert node alerts.
_ALERT_CARRIERS = ("analytics.crowd", "analytics.violations",
                   "analytics.proximity", "analytics.guard")


#: Node types folded into another node, and the data that preserves what the
#: old one did. Each entry is (new type, data to merge in).
#:
#: Every one of these was two palette entries for one idea, which is a cost
#: paid on every visit: more to read, more to choose between, and a second
#: place for a fix to be forgotten.
_MERGED_NODES = {
    # One question over a different span.
    "analytics.frame_count": ("analytics.frame", {"mode": "realtime"}),
    "analytics.frame_total": ("analytics.frame", {"mode": "distinct"}),
    # Unique Visitors WAS the Zone Counter with no minimum: same rule, same
    # metrics, counting each object once. limit 0 = every entrant counts.
    "analytics.visitors": ("analytics.violations", {"limit": 0, "mode": "distinct"}),
    # Zone Functionality (occupancy) IS the Zone Counter in realtime mode --
    # same zone, same classes, same zone_count rule, so the compiled function
    # and its fn_<id>_count key are byte-identical after the rewrite.
    "analytics.occupancy": ("analytics.violations", {"mode": "realtime"}),
    # Speed was Travel Time with its metrics listed in a different order --
    # same function, same fields. Travel Time already publishes both speeds.
    "analytics.speed": ("analytics.travel", {"mode": "speed"}),
    # Graph is Chart plus a shape picker, so Chart was the same node with one
    # option removed. Kept as a line so an existing card looks unchanged.
    "dashboard.chart": ("dashboard.graph", {"kind": "line"}),
    # Counter, Gauge and Verdict were one node three times -- same wiring,
    # same inspector, different `widget_kind`. Each keeps its own shape, so
    # an existing card looks and behaves exactly as it did.
    "dashboard.counter": ("dashboard.card", {"kind": "tile"}),
    "dashboard.gauge": ("dashboard.card", {"kind": "gauge"}),
    "dashboard.verdict": ("dashboard.card", {"kind": "verdict"}),
    # An Event Card with kind "table" is what this always compiled to -- and
    # the Event Card is then folded on again (see _event_card_node).
    "dashboard.table": ("dashboard.events", {"kind": "table"}),
}


def _event_card_node(n):
    """Rewrite one Event Card as the Card or the Chart that draws its shape.

    The Event Card was those two nodes with its own answer to "where does
    the number come from", so that answer moves onto them as `source_mode`
    (EVENT_SOURCE_MODES) and everything else stays exactly where it was:
    same id, same wires, same data. A chart shape becomes a Chart, anything
    else -- a tile, a verdict, the recent-events table -- a Card, which is
    where the one-value shapes and the table of rows already live.

    The mode is written down even when the card never set one: an unset mode
    MEANT "events" on the Event Card, and means "follow the wire" on a Card.
    """
    d = dict(n.get("data") or {})
    d["source_mode"] = d.get("source_mode") or "events"
    n["type"] = ("dashboard.graph" if d.get("kind") in CHART_KINDS
                 else "dashboard.card")
    n["data"] = d


#: What a saved graph called the object-id output before it was renamed.
_OID_WAS = "pid"


def migrate_pid_to_oid(graph):
    """Carry saved graphs across the pid -> OID rename.

    The name changed in the CODE, not just on screen: the node setting, the
    port and the Data Table column source are all spelled OID now, because a
    reader maintaining this a year from now should meet one word rather than
    two for the same thing. That is a good reason, and it comes with a debt --
    every graph already on a box spells them the old way, and the code no
    longer answers to it.

    Left alone the failure is silent, which is the worst kind: a node whose
    object-id output was switched ON reads `data.pid`, finds nothing under
    `OID`, and simply stops offering the port. The wire drawn from it has
    nothing to attach to, and the Data Table column fed by it goes blank. No
    error anywhere -- the graph looks exactly as it did.

    Three things move, and they are the three that were ever written down:
    the node's own setting, the handle a wire records, and the `source` a
    dataset column stores. Mutates `graph`; returns whether it changed.
    """
    changed = False
    for n in graph.get("nodes") or []:
        d = n.get("data") or {}
        if _OID_WAS in d:
            d["OID"] = d.pop(_OID_WAS)
            n["data"] = d
            changed = True
        # A Data Table records WHERE each column's value comes from; one of
        # those sources is the object id.
        for col in (d.get("columns") or []):
            if isinstance(col, dict) and col.get("source") == _OID_WAS:
                col["source"] = "OID"
                changed = True
    for e in graph.get("edges") or []:
        if e.get("sourceHandle") == _OID_WAS:
            e["sourceHandle"] = "OID"
            changed = True
    return changed


def sync_class_filters(slug, allowed):
    """Drop classes a system no longer detects from its Class Filter nodes.

    "Classes to keep" on the AI Models page decides what the engine emits at
    all. A Class Filter naming something outside that set can never match, so
    the rule behind it reads zero for ever -- and nothing on the workflow page
    says why, because the filter looks perfectly configured. The setting lives
    on one page and its consequence lands on another.

    So changing it here rewrites the filters there. Only ever REMOVES: a
    filter watching "person, forklift" on a system narrowed to people becomes
    "person", never the reverse. Widening would put classes into a rule the
    operator never chose to watch, which is a different rule than the one they
    built.

    A filter emptied completely is left empty rather than deleted. Empty means
    "every class" downstream, and silently turning "only forklifts" into
    "everything" is exactly the kind of quiet widening this avoids -- the
    compiler already reports an object watch with no classes, which is the
    honest place for it to surface.

    Returns the sorted class names actually removed, so the caller can say so.
    """
    keep = {str(c).strip() for c in (allowed or ()) if str(c).strip()}
    if not keep:
        # No restriction configured means every class is emitted; nothing to
        # prune against.
        return []
    try:
        store = load_store(slug)
    except (OSError, ValueError):
        return []
    dropped, touched = set(), False
    for w in store.get("workflows") or []:
        for n in (w.get("graph") or {}).get("nodes") or []:
            if n.get("type") != "filter.class":
                continue
            d = n.get("data") or {}
            had = [str(c) for c in (d.get("classes") or [])]
            kept = [c for c in had if c in keep]
            if len(kept) != len(had):
                dropped.update(c for c in had if c not in keep)
                d["classes"] = kept
                n["data"] = d
                touched = True
    if touched:
        try:
            save_store(slug, store)
        except OSError:
            return []
    return sorted(dropped)


def migrate_inline_triggers(graph):
    """Turn a node's inline trigger into the Alert node it always was.

    A rule could raise an event two ways: a `trigger` set on the rule itself,
    or an Alert wired after it. Both compiled to the same thing, and the Alert
    node's own description is the sentence the checkbox used -- "raise an
    event when the value passes a threshold" -- so the canvas offered one
    behaviour under two names, one of which was invisible unless you opened
    the node that had it.

    Now there is one: the Alert. Every inline trigger becomes an Alert node
    wired to the rule that carried it, holding the same metric, operator and
    threshold, so the rule fires exactly when it fired before -- and the
    reason is on the canvas instead of inside a panel.

    Placed to the RIGHT of its rule and offset down, so a node that already
    has an Alert wired after it does not get a second one landing on top of
    the first. Mutates `graph`; returns whether it changed anything.
    """
    nodes = graph.get("nodes") or []
    # Every id already on the canvas. The Alert's id is DERIVED from the rule
    # that carried the trigger, so a second pass over a graph that still has
    # (or has regained) an inline trigger appends a second node with the same
    # id -- and a graph with duplicate ids does not load. One system reached
    # FOUR identical Alerts under one id this way, which closed its editor
    # entirely. Deriving the id is still right: it is what makes the migration
    # repeatable rather than multiplying Alerts on every load. It just has to
    # check first.
    taken = {str(n.get("id") or "") for n in nodes if isinstance(n, dict)}
    made, wires = [], []
    cleared = False
    for n in nodes:
        d = n.get("data") or {}
        trig = d.get("trigger")
        if not isinstance(trig, dict) or not trig:
            continue
        pos = n.get("position") or {}
        aid = f"n_trig_{n.get('id')}"
        if aid in taken:
            # The Alert is already there from an earlier pass. Clear the
            # inline trigger anyway -- leaving it beside the Alert is what
            # raises the event twice.
            d.pop("trigger", None)
            n["data"] = d
            cleared = True
            continue
        taken.add(aid)
        made.append({
            "id": aid, "type": "output.alert",
            "position": {"x": float(pos.get("x") or 0) + 300,
                         "y": float(pos.get("y") or 0) + 70},
            "data": {
                # The rule's own metric when it named one; blank means the
                # rule's first metric, which is what an inline trigger with
                # no metric meant too.
                "metric": str(trig.get("metric") or ""),
                "op": str(trig.get("op") or ">"),
                "value": trig.get("value", 1),
                "severity": str(trig.get("severity") or "warn"),
                "message": str(trig.get("message") or ""),
                "cooldown_s": trig.get("cooldown_s", 30),
                "box_color": str(trig.get("box_color") or ""),
                "zone_color": str(trig.get("zone_color") or ""),
                "color_target": str(trig.get("color_target") or "box"),
            },
        })
        wires.append({"id": f"e_trig_{n.get('id')}", "source": n.get("id"),
                      "target": aid, "sourceHandle": None, "targetHandle": "in"})
        # Cleared, not left beside the Alert: two of them would raise the
        # event twice.
        d.pop("trigger", None)
        n["data"] = d
    if not made:
        # Nothing new, but a trigger may still have been cleared beside an
        # Alert that already existed -- which the caller must persist, or the
        # rule keeps raising its event twice.
        return cleared
    graph["nodes"] = nodes + made
    graph["edges"] = (graph.get("edges") or []) + wires
    return True


def migrate_series_zones(graph):
    """Rewire a zone that sat in series into one wired alongside.

    A Zone used to be a step the detections passed THROUGH:

        Class Filter -> Zone -> Zone Counter

    which is not what a zone is. It names an area; the rule needs to be told
    both WHICH objects and WHERE, and those are two different things. Rules
    take the zone on a port of its own now, so the same graph is:

        Class Filter ------------> Zone Counter (in)
        Zone --------------------> Zone Counter (zone)

    This rewrites the first shape into the second on load. It matters more
    than tidiness: the class scope of those rules is worked out by walking
    back through their inputs to the Class Filters behind them, and with the
    Zone's input port gone the walk would stop at the zone and every one of
    those rules would silently widen to every class the model detects.

    Only ever ADDS the bypass and drops the wire into the zone -- the zone's
    own wire to the rule is left exactly as it was, so a rule keeps the area
    it was measuring. Mutates `graph`; returns whether it changed anything.
    """
    nodes = {n.get("id"): n for n in (graph.get("nodes") or [])}
    edges = graph.get("edges") or []
    zone_ids = {i for i, n in nodes.items() if n.get("type") == "zone.ref"}
    if not zone_ids:
        return False
    feeds = {}                      # zone id -> [source node ids]
    for e in edges:
        if e.get("target") in zone_ids:
            feeds.setdefault(e["target"], []).append(e.get("source"))
    if not feeds:
        return False
    # Where each zone goes, so what fed it can be pointed there instead.
    added, seen = [], {(e.get("source"), e.get("target"),
                        e.get("targetHandle") or "in") for e in edges}
    for e in edges:
        srcs = feeds.get(e.get("source"))
        if not srcs:
            continue
        for up in srcs:
            key = (up, e.get("target"), "in")
            if up == e.get("target") or key in seen:
                continue
            seen.add(key)
            added.append({"id": f"e_zmig_{up}_{e['target']}",
                          "source": up, "target": e["target"],
                          "sourceHandle": None, "targetHandle": "in"})
    kept = [e for e in edges if e.get("target") not in zone_ids]
    if not added and len(kept) == len(edges):
        return False
    graph["edges"] = kept + added
    return True


def migrate_merged_nodes(graph):
    """Rewrite nodes that were folded into another node.

    A canvas drawn with the old types keeps working exactly as it did: same
    function id, same wires, same position, and data chosen so the compiled
    output does not change. Mutates `graph`; returns whether it changed.
    """
    changed = False
    for n in graph.get("nodes") or []:
        merged = _MERGED_NODES.get(n.get("type"))
        if merged:
            new_type, patch = merged
            n["type"] = new_type
            # The node's OWN data wins: an operator who set a chart kind or a
            # limit keeps it; the patch only fills what the old type implied.
            n["data"] = {**patch, **(n.get("data") or {})} if n.get("data") \
                else dict(patch)
            for key, value in patch.items():
                n["data"].setdefault(key, value)
            changed = True
        # The Event Card, after the entry above has had its say: an Events
        # Table becomes an Event Card first, and that becomes a Card here in
        # the same pass.
        if n.get("type") == "dashboard.events":
            _event_card_node(n)
            changed = True
        # A Zone Counter saved before modes existed IS a distinct counter,
        # but the mode fallback is now realtime (mirroring the Frame
        # Counter). Stamp the mode explicitly, so no violations node is ever
        # mode-less and the fallback can never re-decide what an old graph
        # counts.
        if (n.get("type") == "analytics.violations"
                and not (n.get("data") or {}).get("mode")):
            n.setdefault("data", {})["mode"] = "distinct"
            changed = True
    return changed


def prune_duplicate_wires(graph):
    """Drop a second wire between the same two nodes that MEANS the same
    thing. Mutates `graph`; returns whether anything changed.

    When a rule gained named output ports (a Zone Counter's count / new /
    OID), the wires already drawn kept no handle at all -- and a
    handle-less wire resolves to the node's FIRST metric, which is exactly
    what the `count` port is. Wiring the named port after that left two
    edges saying one thing, drawn on top of each other: the compiler
    reported "the same rule is wired into this card twice" about a wire
    nobody could see, let alone delete.

    The NAMED wire is kept -- it is the one the operator drew deliberately,
    and it survives the source changing which metric it publishes first.
    """
    nodes = {n["id"]: n for n in (graph.get("nodes") or []) if isinstance(n, dict)}
    edges = [e for e in (graph.get("edges") or ()) if isinstance(e, dict)]

    def first_metric(node):
        spec = REGISTRY.get(node.get("type")) or {}
        return (node_metrics(node, spec) or [None])[0]

    # (source, target, target handle) -> the edges that pair them
    groups = {}
    for e in edges:
        key = (e.get("source"), e.get("target"), str(e.get("targetHandle") or ""))
        groups.setdefault(key, []).append(e)

    drop = set()
    for (src_id, _tgt, _th), group in groups.items():
        if len(group) < 2:
            continue
        src = nodes.get(src_id)
        if not src:
            continue
        default = first_metric(src)
        # What each edge actually reads: an unlabelled one reads the first
        # metric, a labelled one reads its own port.
        seen = {}
        for e in group:
            handle = str(e.get("sourceHandle") or "")
            # "out" is the canvas's name for the node's FIRST metric port --
            # the same value a handle-less wire reads -- so the two are one
            # wire wearing two names (nodes.jsx sources: {id: "out"}).
            if not handle or handle == "out":
                handle = default
            if handle in seen:
                # Keep whichever names its port; drop the bare one.
                # Keep the one that NAMES its port; a bare wire is the
                # leftover from before the ports existed.
                keep, other = ((e, seen[handle]) if e.get("sourceHandle")
                               else (seen[handle], e))
                drop.add(id(other))
                seen[handle] = keep
            else:
                seen[handle] = e
    if not drop:
        return False
    graph["edges"] = [e for e in (graph.get("edges") or ())
                      if not (isinstance(e, dict) and id(e) in drop)]
    return True


#: What a Condition's alarm fields mean when nobody has touched them. A node
#: carrying only these said nothing an Alert node would say, so it gets none.
_PLAIN_ALARM = {"severity": "warn", "message": "", "cooldown_s": 30,
                "box_color": "", "color_target": "box"}


def _split_colour(colour, target):
    """One aimed colour -> the two the Trigger carries: (box, zone).

    A Condition/Alert had a single colour and a `color_target` picker saying
    what it painted. A Trigger has a colour per thing, so a migrated node
    lands on the new shape rather than keeping a setting the inspector no
    longer shows -- otherwise "colour the zone" would arrive as a box colour
    the operator never asked for."""
    colour = str(colour or "")
    aim = str(target or "box").strip().lower()
    if not colour:
        return "", ""
    if aim == "zone":
        return "", colour
    if aim == "both":
        return colour, colour
    return colour, ""


def migrate_split_alerts(graph):
    """Lift the alarm's settings off a Condition onto an Alert node.

    Severity, message, cooldown and box colour used to live on the same node
    as the threshold, so choosing when something fires meant scrolling past a
    colour picker to get there. They are an Alert node now -- and a graph
    already saved still carries them on the Condition, where the compiler
    still reads them, which would leave two places to look for one setting for
    ever.

    So each Condition that says something is given the Alert it was already
    describing, wired from it and placed beside it. The fields stay on the
    Condition as well: the compiler prefers the Alert node when there is one,
    and leaving them means this migration is not the only thing standing
    between an old graph and its own alarm settings.

    A Condition carrying only DEFAULTS gets nothing -- there is no alarm to
    describe, and a canvas that grows a node per Condition on upgrade is a
    tidy-up someone has to do by hand.

    Gated on the stored schema version: a Condition minted today has the same
    default fields, and cannot be told apart by looking. Mutates `graph`;
    returns whether anything changed.
    """
    if int(graph.get("version") or 1) >= 3:
        return False
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    ids = {n.get("id") for n in nodes}
    # A Condition that already has an Alert after it has been split by hand.
    decorated = {e.get("source") for e in (graph.get("edges") or ())
                 if isinstance(e, dict)
                 and any(n.get("id") == e.get("target")
                         and n.get("type") == "output.event" for n in nodes)}

    changed = False
    for node in list(nodes):
        if node.get("type") != "output.alert" or node["id"] in decorated:
            continue
        data = node.get("data") or {}
        alarm = {k: data.get(k, v) for k, v in _PLAIN_ALARM.items()}
        if alarm == _PLAIN_ALARM:
            continue                     # nothing said; nothing to carry over
        aid = f"{node['id']}_alert"[:40]
        if aid in ids:
            continue
        pos = node.get("position") or {"x": 0, "y": 0}
        nodes.append({"id": aid, "type": "output.event",
                      "position": {"x": (pos.get("x") or 0) + 210,
                                   "y": (pos.get("y") or 0) + 90},
                      "data": {"when": "true", **alarm}})
        graph.setdefault("edges", []).append(
            {"id": f"e_{node['id']}_{aid}", "source": node["id"],
             "target": aid, "sourceHandle": None, "targetHandle": "in"})
        ids.add(aid)
        changed = True
    graph["nodes"] = nodes
    graph["version"] = max(int(graph.get("version") or 1), 3)
    return changed


#: Old Switch shape -> the Branch node that IS that shape now. The ctype is
#: carried in `data` too, harmlessly: the new type fixes it (branch_ctype),
#: and leaving it means a graph rolled back to an older build still reads.
_SWITCH_SHAPES = {"compare": "branch.cases", "range": "branch.range",
                  "switch": "branch.match", "multi": "branch.multi"}


def migrate_branch_family(graph):
    """Condition/Alert/Compare/Switch -> the Branch family and the Trigger.

    The old shapes published nothing of their own: a Compare and an Alert were
    resolved at compile time by walking BACKWARDS to the rule behind them (the
    "transparent node" convention). Their replacements publish real 0/1 ports.
    That is a different graph, so it has to be written down rather than
    inferred on every load.

    What each becomes:

      logic.switch      -> the branch node for its ctype, rules untouched
      logic.when        -> branch.compare, its test carried across as one rule
      output.alert      -> branch.compare + output.trigger, the threshold on
                           the Compare and the alarm on the Trigger
      output.event      -> folded INTO that Trigger (severity, message,
                           cooldown, colour), and its `when: false` handled by
                           re-wiring the Trigger to the Compare's False port
                           rather than inverting anything

    The compiled branch ids are `<base>_b<index>` before and after, so every
    card, chart, history series and dashboard already bound to one keeps
    reading the same key. Mutates `graph`; returns whether anything changed.
    """
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or ()) if isinstance(e, dict)]
    by_id = {n.get("id"): n for n in nodes}
    ids = set(by_id)
    changed = False

    # -- the shapes that only change type ----------------------------------
    for node in nodes:
        if node.get("type") == "logic.switch":
            data = node.get("data") or {}
            node["type"] = _SWITCH_SHAPES.get(str(data.get("ctype") or "switch"),
                                              "branch.match")
            changed = True
        elif node.get("type") == "logic.when":
            data = node.setdefault("data", {})
            # One node's worth of test becomes one rule row, which is what the
            # compare shape has always taken -- the old node just kept its
            # single test in loose fields instead of a list.
            if not data.get("rules"):
                mode = str(data.get("mode") or "above")
                op = {"above": ">", "below": "<", "equals": "==",
                      "not_equals": "!=", "at_least": ">=", "at_most": "<="
                      }.get(mode, ">")
                row = {"op": op, "value": data.get("value", 1), "input": "in1"}
                if mode in ("between", "outside"):
                    row = {"op": "between", "value": data.get("value", 0),
                           "value2": data.get("value2", 0), "input": "in1"}
                data["rules"] = [row]
            data.setdefault("otherwise", True)
            node["type"] = "branch.compare"
            changed = True

    # -- Condition (+ its Alert) -> Compare + Trigger ------------------------
    # An Alert wired after a Condition described the same moment from the
    # other side, so the pair collapses into ONE Trigger. Its `when: false`
    # is not carried as a setting: the Trigger is wired to the Compare's
    # False port instead, which is the same statement with nothing left to
    # disagree with.
    alerts_by_source = {}
    for e in edges:
        tgt = by_id.get(e.get("target"))
        if tgt is not None and tgt.get("type") == "output.event":
            alerts_by_source.setdefault(e.get("source"), []).append(tgt)

    for node in list(nodes):
        if node.get("type") != "output.alert":
            continue
        data = node.get("data") or {}
        alert = (alerts_by_source.get(node["id"]) or [None])[0]
        alarm = (alert.get("data") or {}) if alert else data
        false_side = str(alarm.get("when") or "true") == "false"
        tid = f"{node['id']}_trig"[:40]
        if tid in ids:
            continue
        pos = node.get("position") or {"x": 0, "y": 0}
        # The Condition BECOMES the Compare (keeping its id, so every wire
        # already drawn into it survives untouched) and the Trigger is the
        # new node after it.
        node["type"] = "branch.compare"
        node["data"] = {
            "fnId": data.get("fnId") or "",
            "metric": data.get("metric") or "",
            "hold_s": 0, "otherwise": True,
            "rules": [{"op": data.get("op") or ">",
                       "value": data.get("value", 1), "input": "in1"}]}
        had_alarm = any(alarm.get(k) not in (None, "", v)
                        for k, v in (("severity", "warn"), ("message", ""),
                                     ("box_color", "")))
        nodes.append({
            "id": tid, "type": "output.trigger",
            "position": {"x": (pos.get("x") or 0) + 200,
                         "y": pos.get("y") or 0},
            "data": {
                # Named from the alarm's own message where there is one --
                # that is the sentence the operator already wrote about this
                # moment -- and from the node otherwise. A Trigger must have
                # a name to compile, and inventing a blank one here would
                # break a graph that ran yesterday.
                "name": (str(alarm.get("message") or "").strip()
                         or str(data.get("name") or "").strip() or "Alert")[:60],
                "alert": bool(had_alarm or alert is not None),
                "severity": alarm.get("severity") or "warn",
                "message": alarm.get("message") or "",
                "cooldown_s": alarm.get("cooldown_s", 30),
                **dict(zip(("box_color", "zone_color"),
                           _split_colour(alarm.get("box_color"),
                                         alarm.get("color_target")))),
                "draw": bool(_split_colour(alarm.get("box_color"),
                                           alarm.get("color_target"))[0])}})
        ids.add(tid)
        # Everything that hung off the Condition -- or off the Alert it had --
        # now hangs off the Trigger, which is where the moment lives. The
        # Alert's own node goes: it described this same moment from the other
        # side, and two nodes for one moment is what this replaces.
        dead = {alert["id"]} if alert is not None else set()
        moved = []
        for e in edges:
            if e.get("target") in dead:
                # A wire INTO the dead Alert -- from the Condition, or from
                # anywhere else -- has nothing left to point at.
                continue
            if e.get("source") in ({node["id"]} | dead):
                e = {**e, "source": tid, "sourceHandle": None}
            if e.get("source") == e.get("target"):
                continue                # never leave a node wired to itself
            moved.append(e)
        # The new wire is added AFTER the rewrite, so the loop above cannot
        # pick it up and redirect the Compare's own output to the Trigger it
        # was just given. False side = the Compare's second port: out0 is the
        # test, out1 the catch-all, which is exactly "while it does not hold".
        moved.append({"id": f"e_{node['id']}_{tid}", "source": node["id"],
                      "sourceHandle": "out1" if false_side else "out0",
                      "target": tid, "targetHandle": "in"})
        edges = moved
        if dead:
            nodes = [n for n in nodes if n.get("id") not in dead]
            by_id = {n.get("id"): n for n in nodes}
        changed = True

    # An Alert with no Condition in front of it (wired straight to a Formula
    # or a Switch branch) is already a naked "fire on this" -- which is what a
    # Trigger is, so it becomes one where it stands.
    for node in nodes:
        if node.get("type") != "output.event":
            continue
        data = node.get("data") or {}
        node["type"] = "output.trigger"
        node["data"] = {
            "name": (str(data.get("message") or "").strip() or "Alert")[:60],
            "alert": True,
            "severity": data.get("severity") or "warn",
            "message": data.get("message") or "",
            "cooldown_s": data.get("cooldown_s", 30),
            **dict(zip(("box_color", "zone_color"),
                       _split_colour(data.get("box_color"),
                                     data.get("color_target")))),
            "draw": bool(_split_colour(data.get("box_color"),
                                       data.get("color_target"))[0])}
        changed = True

    if changed:
        graph["nodes"] = nodes
        graph["edges"] = edges
        graph["version"] = max(int(graph.get("version") or 1), 4)
    return changed


#: operation -> the Math node that IS that operation. Built from MATH_NODES so
#: adding a node to the family cannot forget to migrate the Formulas that were
#: doing its job by hand.
_OP_NODE = {op: mtype for mtype, m in MATH_NODES.items() for op in m["ops"]}
# An operation two nodes offer (Not: the Logic node and the retired Not) goes
# to the node still on the palette.
_OP_NODE.update({op: mtype for mtype, m in MATH_NODES.items()
                 if not m.get("deprecated") for op in m["ops"]})


def migrate_math_family(graph):
    """Formula -> the Math node for its operation.

    The Formula asked two questions before it could say what it did ("which of
    34 operations?" and "how many inputs?"), and its face answered neither
    until both were set. Each operation is its own node now, so the node that
    lands on the canvas is already the shape it will keep.

    The storage is IDENTICAL -- `op`, `inputs`, the typed fallbacks, the
    per-join `ops` list -- because a Math node compiles through the very same
    path. So this rewrites the type and touches nothing else, and the emitted
    function keeps its id, its expression and every card bound to it.

    A Formula whose operation has no home (a comparison: those are a branch's
    job now) is LEFT ALONE. It still compiles, and moving it somewhere it does
    not belong would be worse than leaving it where it works.

    Mutates `graph`; returns whether anything changed.
    """
    changed = False
    for node in graph.get("nodes") or []:
        if node.get("type") != "analytics.formula":
            continue
        data = node.get("data") or {}
        target = _OP_NODE.get(str(data.get("op") or "add"))
        if not target:
            continue
        node["type"] = target
        # A fixed-arity node carries no `inputs`: it shows what its operation
        # takes. Left behind, a stale one would be read by nothing and confuse
        # the next person to open the JSON.
        if MATH_NODES[target].get("arity") is not None:
            data.pop("inputs", None)
        changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 5)
    return changed


def migrate_counter_merge(graph):
    """Object Counter + Zone Counter -> the one Counter.

    They were the same rule over a different surface: same modes, same timing
    settings, same OID output, two palette entries. WHERE a counter counts is
    read off its zone port now, so the merge is a type change and nothing else
    -- `mode`, `fnId`, `OID` and the timing keys all mean what they meant, and
    the compiled function keeps its id, its rule and every card bound to it.

    ONE node is deliberately left behind: a Zone Counter carrying a `limit`
    ("count an entrant only past this occupancy"). The Counter has no such
    field -- that is a condition, and conditions live on a branch now -- so
    rewriting one would silently change what it counts. It stays on the
    deprecated node, which still compiles, and the operator can rebuild it as
    Counter + Compare when they choose to.

    Mutates `graph`; returns whether anything changed.
    """
    changed = False
    for node in graph.get("nodes") or []:
        t = node.get("type")
        if t not in ("analytics.frame", "analytics.violations"):
            continue
        data = node.setdefault("data", {})
        if t == "analytics.violations":
            try:
                limit = int(data.get("limit") or 0)
            except (TypeError, ValueError):
                limit = 0
            if limit > 0:
                continue                    # keeps its own node, still compiles
            data.pop("limit", None)         # 0 == "every entrant", the default
            # A Zone Counter saved before modes existed is a DISTINCT counter
            # (migrate_merged_nodes stamps that); this only fills a gap for a
            # graph that reached here another way.
            data.setdefault("mode", "distinct")
        else:
            data.setdefault("mode", "realtime")
        node["type"] = "analytics.counter"
        changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 6)
    return changed


def migrate_object_merge(graph):
    """Object Detection + Object in Zone -> the one watch.

    They were always one rule: the compiler reads the zone off the wiring, so
    the two nodes differed by which port an operator had drawn to. The merged
    node makes that the whole story -- a zone wired means "in there", none
    means "anywhere on camera".

    A type change only. `fnId`, the watched classes and every per-class port
    keep their names, so the compiled function and everything bound to it are
    untouched. The merged node no longer OFFERS `count` and `arrived` (a watch
    answers presence; counting is the Counter's job), but the engine still
    publishes them, so a card already reading one keeps reading it.

    Mutates `graph`; returns whether anything changed.
    """
    changed = False
    for node in graph.get("nodes") or []:
        if node.get("type") not in ("analytics.object_frame",
                                    "analytics.object_zone"):
            continue
        node["type"] = "analytics.object"
        node.setdefault("data", {})
        changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 7)
    return changed


def migrate_clip_window(graph):
    """A Record Clip's one duration becomes a before/after pair.

    `duration_s` always meant "record this long AFTER the trigger" -- there was
    no before, because there was no rolling buffer to take one from. So it
    becomes `after_s` unchanged and `before_s` starts at 0, which is the same
    clip and the same cost: a saved graph produces exactly the file it produced
    yesterday, and the run-up is something the operator turns on knowing what
    it buys and what it costs.

    Mutates `graph`; returns whether anything changed.
    """
    changed = False
    for node in graph.get("nodes") or []:
        if node.get("type") != "output.record":
            continue
        data = node.setdefault("data", {})
        if "after_s" in data and "before_s" in data:
            continue
        try:
            after = float(data.get("duration_s") or 5)
        except (TypeError, ValueError):
            after = 5.0
        data.setdefault("after_s", min(5.0, max(0.5, after)))
        data.setdefault("before_s", 0)
        changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 10)
    return changed


def migrate_chart_series(graph):
    """Spread a chart's fan-in across its SERIES ports.

    A chart drawn before those ports existed has every one of its wires on the
    plain `in` port: several edges stacked on one dot, which is most of why
    nobody discovered that the chart had been drawing a line per wire all
    along. The first wire stays where it is -- `in` IS series 1, and moving it
    would rename a wire for no gain -- and the rest move onto s2, s3... in the
    order they were drawn, which is the order the compiler already read them
    in. Same lines, same order, same numbers; said on the canvas now, where a
    line can also be named and coloured.

    A chart whose wires are already on the named ports is left alone, and so
    is one with a single wire: that is the one-value chart, and it compiles
    through a different path entirely.

    Mutates `graph`; returns whether anything changed.
    """
    charts = {n.get("id") for n in graph.get("nodes") or []
              if n.get("type") == "dashboard.graph"}
    if not charts:
        return False
    fan = {}
    for edge in graph.get("edges") or []:
        if edge.get("target") in charts:
            fan.setdefault(edge["target"], []).append(edge)
    changed = False
    for node in graph.get("nodes") or []:
        if node.get("type") != "dashboard.graph":
            continue
        # A chart asking recorded data has no series ports: what is wired
        # into it scopes its one question, and moving a wire onto s2 would
        # turn that scope into a line.
        if asks_recorded(node):
            continue
        edges = fan.get(node.get("id")) or []
        if len(edges) < 2:
            continue
        if {str(e.get("targetHandle") or "in") for e in edges} - {"in"}:
            continue                        # already on the named ports
        for i, edge in enumerate(edges[1:], start=1):
            if i >= len(GRAPH_SERIES_HANDLES):
                # Past the eighth wire. Left exactly where it is: the compile
                # ignores it with a warning, and that warning is easier to act
                # on while the wire is still visibly on the chart.
                break
            edge["targetHandle"] = GRAPH_SERIES_HANDLES[i]
            changed = True
        data = node.setdefault("data", {})
        if "series_n" not in data:
            # How many ports the node SHOWS. Enough for the wires it has, so
            # the migrated graph looks like what it does.
            data["series_n"] = max(2, min(len(GRAPH_SERIES_HANDLES),
                                          len(edges)))
            changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 11)
    return changed


def migrate_compare_merge(graph):
    """Compare -> If / Else if, which is the same node with one row.

    A Compare tests one value and offers True and False. An If / Else if with
    a single row tests one value and offers "if <test>" and "else". Same
    inputs, same two ports, same compiled branch ids (`<base>_b0` and the
    catch-all) -- so they were two palette entries for one node, and choosing
    between them was choosing between a thing and itself.

    The ports line up exactly, which is what makes this a type change and
    nothing else: True is out0 and so is the first case; False is out1 and so
    is the else. Every saved wire keeps its handle and its meaning.

    Mutates `graph`; returns whether anything changed.
    """
    changed = False
    for node in graph.get("nodes") or []:
        if node.get("type") != "branch.compare":
            continue
        node["type"] = "branch.cases"
        data = node.setdefault("data", {})
        # A Compare always had a False way out; `otherwise` is how the merged
        # node spells it, and an absent one would drop the port.
        data.setdefault("otherwise", True)
        changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 9)
    return changed


def migrate_cycle_modes(graph):
    """Cycle Time's five modes become three, and two of them become settings.

    "Zone Dwell Time", "OID Timer" and "OID Measure Time" were one rule
    publishing the same three numbers. They differed in what the node ADDED --
    a per-object threshold, or an event per completed stay -- which is a
    checkbox and a delivery choice, not three things to choose between before
    you know they are the same measurement.

    So: all three become `present`, and what they added is written down.

      * `timer`  -- the per-object clock: OID on, delivered LIVE.
      * `stamps` -- the record of each stay: OID on, delivered ONCE, which is
                    the stamp_events the compiler already emits for it.
      * `dwell`  -- the plain aggregate; OID left exactly as it was.

    A node with OID already ticked gets `delivery: "once"` written down too:
    that IS what it was doing (before the choice existed, OID alone stamped
    the stays), and its Data Table is reading those events. Saying so keeps it
    stamping once the compiler starts honouring the new field.

    Mutates `graph`; returns whether anything changed.
    """
    changed = False
    for node in graph.get("nodes") or []:
        if node.get("type") != "analytics.dwell":
            continue
        data = node.setdefault("data", {})
        mode = str(data.get("mode") or "dwell")
        if mode not in ("dwell", "timer", "stamps"):
            continue                    # countdown / occupancy are unchanged
        if mode in ("timer", "stamps"):
            data["OID"] = True
        if data.get("OID") and not data.get("delivery"):
            data["delivery"] = "live" if mode == "timer" else "once"
        data["mode"] = "present"
        changed = True
    if changed:
        graph["version"] = max(int(graph.get("version") or 1), 8)
    return changed


def migrate_dwell_count_alerts(graph):
    """Write down what a Cycle Time alert was aimed at, before the default moved.

    An Alert with no metric chosen means "the rule's first one". Cycle Time's
    first one WAS `current`, a head count; it is now `live_s`, a clock. So
    every Alert already on a canvas with its metric left unset silently changes
    subject on this upgrade -- "more than 1 object inside" becomes "anybody
    inside for more than 1 second", from a node nobody touched, with the
    operator's threshold kept and its meaning replaced. That is the one failure
    mode worth a migration: a rule that keeps running and starts meaning
    something else says nothing at all.

    Stamping the old default explicitly turns it into the refusal the compiler
    already has for the metric that no longer exists -- loud, once, with the
    two nodes that count named in the message.

    Gated on the stored schema version, because the state cannot be told apart
    by looking: an Alert minted TODAY against a Cycle Time also has no metric,
    and means the new default honestly. Mutates `graph`; returns whether
    anything changed.
    """
    if int(graph.get("version") or 1) >= 2:
        return False
    nodes = {n["id"]: n for n in (graph.get("nodes") or []) if isinstance(n, dict)}
    src_of = {}
    for e in graph.get("edges") or ():
        if isinstance(e, dict) and e.get("source") and e.get("target"):
            src_of.setdefault(e["target"], []).append(e["source"])

    changed = False
    for node in nodes.values():
        if node.get("type") not in ("output.alert", "logic.when"):
            continue
        data = node.get("data") or {}
        if str(data.get("metric") or ""):
            continue                     # an explicit choice is already written
        for src_id in src_of.get(node["id"], ()):
            src = nodes.get(src_id)
            spec = REGISTRY.get((src or {}).get("type")) or {}
            if spec and node_fn_type(src, spec) == "zone_dwell":
                node["data"] = {**data, "metric": "current"}
                changed = True
                break
    graph["version"] = max(int(graph.get("version") or 1), 2)
    return changed


def migrate_condition_metrics(graph):
    """Point every Alert / Compare at a metric its rule actually publishes.

    A node keeps the metric it was given, and the rule under it can change
    what it publishes -- a Zone Counter switched from distinct to realtime
    stops publishing `current` and starts publishing `count`. compile_graph
    already falls back to the rule's first metric and WARNS, so the canvas
    kept working while announcing a correction it re-made on every load: a
    permanent amber warning about something already handled, which reads as a
    fault nobody can clear.

    Repaired here instead, once, in the stored graph: same rule the compiler
    applies, written down. Mutates `graph`; returns whether anything changed.
    A metric that IS published, and a node with nothing upstream, are left
    exactly as they are.
    """
    nodes = {n["id"]: n for n in (graph.get("nodes") or []) if isinstance(n, dict)}
    preds = {}
    for e in graph.get("edges") or ():
        if isinstance(e, dict) and e.get("source") and e.get("target"):
            preds.setdefault(e["target"], []).append(e["source"])

    def source_metrics(nid, hops=0):
        """(metrics, fn_type) of the rule feeding this condition, following
        through the transparent nodes (Alert/Compare/Delay publish nothing of
        their own -- they define a trigger ON the rule behind them)."""
        for src_id in preds.get(nid, ()):
            src = nodes.get(src_id)
            if not src:
                continue
            spec = REGISTRY.get(src.get("type")) or {}
            cat = spec.get("category")
            if cat in ("analytics", "variable"):
                return (node_metrics(src, spec) or [], node_fn_type(src, spec))
            if hops < 3 and (cat == "logic"
                             or src.get("type") in _TRANSPARENT_OUTPUTS):
                got, ftype = source_metrics(src_id, hops + 1)
                if got:
                    return got, ftype
        return [], ""

    changed = False
    for node in graph.get("nodes") or ():
        if not isinstance(node, dict):
            continue
        if node.get("type") not in ("output.alert", "logic.when"):
            continue
        data = node.get("data") or {}
        metric = str(data.get("metric") or "")
        if not metric:
            continue                    # unset means "the first one"; fine
        metrics, fn_type = source_metrics(node["id"])
        # `active` is published by every rule that carries a trigger, so it
        # is never stale even when it is not in the declared list.
        if not metrics or metric in metrics or metric == "active":
            continue
        # Cycle Time's `current` was a HEAD COUNT and is gone. Repointing it at
        # this node's first metric would keep the comparison and change what it
        # compares: "> 1" meant two people in the zone, and would come back as
        # "longest stay over one second" -- an alert that fires nearly always,
        # from a node the operator never touched. Left exactly as it is, so the
        # compiler can refuse it and say what to wire instead.
        if fn_type == "zone_dwell" and metric == "current":
            continue
        node["data"] = {**data, "metric": metrics[0]}
        changed = True
    return changed


def standardize_alerts(graph):
    """Rewrite legacy self-alerting nodes to the standard shape: the measure
    node keeps only what it measures, and the alert lives in a connected
    Alert node. Mutates `graph`; returns whether anything changed.

    - Crowd Limit becomes Zone Occupancy + Alert(count > limit) -- it was
      occupancy with a baked-in threshold.
    - Zone Counter keeps its conditional counting (limit = min present)
      but its severity/message/cooldown move into Alert(new > 0).
    - Proximity / Guard Zone lose their alert fields; Alert(active >= 1)
      is created when none is wired yet.

    Runs once per canvas on read (like adopt/seed): after it, every alarm on
    the canvas is visible as an Alert node, none hides inside node data.
    """
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    ids = {n.get("id") for n in nodes}
    # Nodes that already have an Alert/When wired after them keep it -- the
    # operator has standardized by hand; only the hidden config is stripped.
    handled = set()
    for e in edges:
        tgt = next((n for n in nodes if n.get("id") == e.get("target")), None)
        if tgt and tgt.get("type") in ("output.alert", "output.event",
                                       "logic.when"):
            handled.add(e.get("source"))

    changed = False
    created_nodes, created_edges = [], []
    for n in nodes:
        t = n.get("type")
        if t not in _ALERT_CARRIERS:
            continue
        d = dict(n.get("data") or {})
        legacy = any(k in d for k in ("severity", "message", "cooldown_s"))
        if not legacy and t != "analytics.crowd":
            continue
        severity = d.pop("severity", None)
        message = d.pop("message", "")
        cooldown = d.pop("cooldown_s", None)
        if t == "analytics.crowd":
            limit = d.pop("limit", 4)
            n["type"] = "analytics.occupancy"
            trig = {"metric": "count", "op": ">", "value": limit,
                    "cooldown_s": cooldown if cooldown is not None else 30}
        elif t == "analytics.violations":
            trig = {"metric": "new", "op": ">", "value": 0,
                    "cooldown_s": cooldown if cooldown is not None else 2}
        else:
            trig = {"metric": "active", "op": ">=", "value": 1,
                    "cooldown_s": cooldown if cooldown is not None else 10}
        n["data"] = d
        changed = True
        if n.get("id") in handled:
            continue
        alert_id = f"al_{n.get('id')}"[:40]
        if alert_id in ids:
            continue
        ids.add(alert_id)
        pos = n.get("position") or {}
        created_nodes.append({
            "id": alert_id, "type": "output.alert",
            "position": {"x": float(pos.get("x") or 0) + 260,
                         "y": float(pos.get("y") or 0)},
            "data": {**trig, "severity": severity or "warn",
                     "message": str(message or "")},
        })
        created_edges.append({"id": f"e_{alert_id}"[:80],
                              "source": n["id"], "target": alert_id,
                              "sourceHandle": None, "targetHandle": "in"})
    if created_nodes:
        graph["nodes"] = nodes + created_nodes
        graph["edges"] = edges + created_edges
    return changed


def adopt_package_widgets(systems_dir, slug):
    """Turn a generated package's baked-in dashboard widgets into graph nodes.

    A starter dashboard ("intrusion", "ppe", "people") ships its cards inside
    the system PACKAGE. They are data, not code -- event-store aggregations --
    but living in the package made them unreadable and uneditable: an operator
    could see "Total Intrusions" on the Overview with no way to find out what
    it counted, let alone change it.

    So they are adopted ONCE into the canvas as Card/Chart nodes asking the
    recorded event store (source_mode "events" -- what the Event Card they
    used to become was), and the package's layout is emptied so the same card
    cannot render twice. Returns the nodes; the caller places and saves them.
    """
    path = _package_layout_path(systems_dir, slug)
    try:
        with open(path, "r", encoding="utf-8") as f:
            widgets = json.load(f).get("widgets") or []
    except (OSError, ValueError):
        return []
    nodes = []
    for i, w in enumerate(widgets):
        if w.get("source") not in ("query", "events"):
            continue                # stat/bucket cards bind live keys, not events
        q = w.get("query") or {}
        kind = w.get("kind") or "tile"
        nodes.append({
            "id": f"pkg{i}{os.urandom(2).hex()}",
            "type": "dashboard.graph" if kind in CHART_KINDS else "dashboard.card",
            "position": {"x": 40 + (i % 4) * 240, "y": 640 + (i // 4) * 150},
            "data": {
                "name": w.get("title") or w.get("id") or "Card",
                "kind": kind,
                "source_mode": "events",
                "agg": q.get("agg") or "count",
                "field": q.get("field") or "",
                "group_by": q.get("group_by") or "",
                "bucket": q.get("bucket") or "",
                "range": q.get("range") or "",
                "filters": q.get("filters") or {},
                "sub": w.get("sub") or "",
                "color": w.get("color") or "",
                "limit": w.get("limit") or 15,
            },
        })
    if not nodes:
        return []
    # Empty the package so the adopted cards render from the graph only. Best
    # effort: a package we cannot write is a reason to leave it rendering, not
    # to lose the operator's dashboard.
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"widgets": []}, f, indent=2)
    except OSError:
        return []
    return nodes


def example_graph(zones):
    """A worked example: detection stream all the way to a dashboard card.

    A new operator opens the Workflow to a palette and a blank canvas and has
    to guess both what connects to what AND how anything reaches a dashboard.
    Seeding one real, working chain answers both by being read: every node is
    an ordinary node, so it can be renamed, rewired or deleted, and the comment
    beside it says what each step does.

    The chain needs a zone to point at -- an Occupancy node without one is a
    compile error, and a broken example is worse than none -- so when the
    system has no zones yet only the source and the note are seeded, and the
    note says where to go.
    """
    area = next((z for z in (zones or [])
                 if z.get("kind") == "area" and z.get("enabled", True)), None)
    y = 40
    nodes = [{
        "id": "ex_note", "type": "note.comment",
        "position": {"x": 20, "y": y - 40},
        "data": {"width": 260, "height": 150, "text": (
            "EXAMPLE - how a number reaches a dashboard\n\n"
            "Detection Stream feeds a Class Filter, which feeds a Zone, which "
            "feeds an Analytics node. The Analytics node publishes a variable; "
            "wire that into a Dashboard card and it appears on Analytics.\n\n"
            "Everything here is editable. Delete it once it makes sense."
        )},
    }, {
        "id": "ex_src", "type": "source.detection",
        "position": {"x": 320, "y": y}, "data": {},
    }, {
        "id": "ex_filter", "type": "filter.class",
        "position": {"x": 540, "y": y}, "data": {"classes": []},
    }]
    edges = [{"id": "ex_e1", "source": "ex_src", "target": "ex_filter"}]

    if area is None:
        nodes[0]["data"]["text"] += (
            "\n\nNo zones yet - draw one on the Zones page, then wire it in "
            "after the Class Filter."
        )
        return {"nodes": nodes, "edges": edges}

    nodes += [{
        "id": "ex_zone", "type": "zone.ref",
        "position": {"x": 760, "y": y}, "data": {"zone": area["id"]},
    }, {
        "id": "ex_occ", "type": "analytics.occupancy",
        "position": {"x": 980, "y": y},
        "data": {"fnId": "example_count", "name": f"{area.get('name') or 'Zone'} occupancy",
                 # Both surfaces, so the example shows up in the Builder's and
                 # the Analytics palettes as a draggable widget too.
                 "expose": {"count": ["dashboard", "analytics"]}},
    }, {
        "id": "ex_card", "type": "dashboard.counter",
        "position": {"x": 1220, "y": y}, "data": {"metric": "count"},
    }]
    edges += [
        {"id": "ex_e2", "source": "ex_filter", "target": "ex_zone"},
        {"id": "ex_e3", "source": "ex_zone", "target": "ex_occ"},
        {"id": "ex_e4", "source": "ex_occ", "target": "ex_card"},
    ]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Zone actions -> canvas
# ---------------------------------------------------------------------------
#
# The Zones page's actions (Count / Violation / Cycle time) and this canvas
# are two ways to build the same thing, and for a long time they only met
# once -- at migration. An action added afterwards ran, published its stat,
# and was invisible here, so the one place that shows how a number is made
# did not show it.
#
# So every zone action is MIRRORED onto the canvas: its own chain, wired to
# the Detection Stream, carrying the same fnId the action compiles to. The
# canvas then shows the whole picture, and the operator can wire an Alert, a
# dashboard card or a formula onto a zone action like any other node.
#
# The mirror is owned by the Zones page: mirrored nodes carry the ZONE_NODE
# prefix and are rebuilt on every zone save. Anything the operator adds
# around them is their own and is never touched.

#: Node-id prefix for mirrored chains. Short on purpose -- clean_graph caps
#: node ids at 40 characters, and a function id can already be 40.
ZONE_NODE_PREFIX = "zn_"

#: Zone-action function type -> the node that represents it.
_ZONE_FN_NODES = {
    # Both counting rules mirror as the one Counter -- the zone node the
    # chain draws is what tells it to count that zone rather than the frame.
    "zone_count": "analytics.counter",
    "line_count": "analytics.entry_exit",
    "zone_unique": "analytics.counter",
    "zone_dwell": "analytics.dwell",
    "zone_countdown": "analytics.dwell",
    "zone_occupancy": "analytics.dwell",
    "zone_travel": "analytics.cycle_time",
    "line_travel": "analytics.travel",
    # Only the zone-bound half: a frame-wide object watch has no zone to draw
    # a chain from, and _zone_chain skips a function whose params carry none.
    "object_detect": "analytics.object",
}


def _zone_node_id(fn_id, role):
    """A stable, short node id for one part of a mirrored chain. Derived from
    the function id so a rebuild lands on the same ids -- positions the
    operator nudged, and edges they drew onto the analytics node, survive."""
    digest = hashlib.sha1(str(fn_id).encode("utf-8")).hexdigest()[:10]
    return f"{ZONE_NODE_PREFIX}{digest}_{role}"


def sync_zone_actions(slug, zone_fns, zones=None, off_ids=(), captures=None,
                      verdicts=()):
    """Mirror the Zones page's compiled actions onto the stored canvas.

    `zone_fns` is what functions.apply_zone_actions just compiled (the z_*
    documents). `off_ids` names the ones whose zone is switched off.
    `captures` is {function id: ("snapshot", "record")} -- what each action
    asked to save, so the canvas can draw those as nodes too. `verdicts` names
    the rules that are a VIOLATION, which get a Verdict card drawn after their
    Alert: "is this zone being violated right now" is a state, and a state is
    what an operator puts on the dashboard.

    NOTHING ALREADY DRAWN IS EVER DELETED. Switching a zone off, or unticking
    the rule that made a chain, cuts its wire to the Detection Stream and
    greys the chain -- it does not erase it. Deleting canvas work is the
    operator's call, not a side effect of a tick box somewhere else, and a
    chain that vanished took the operator's own wiring (an Alert, a card)
    with it. An orphaned chain is also marked disabled: an unwired analytics
    node is a compile error, and one stale chain must not stop the whole
    canvas from applying.

    Returns True when the stored graph changed.

    No store yet means the Workflow page has never been opened; the first
    visit synthesises the whole graph from the config (migrate_from_config),
    which already covers these -- mirroring here would duplicate that work.
    """
    off = {str(i) for i in (off_ids or ())}
    captures = {str(k): tuple(v) for k, v in (captures or {}).items()}
    verdicts = {str(i) for i in (verdicts or ())}
    store = load_store(slug)
    if not store or not store.get("workflows"):
        return False
    w = store["workflows"][0]
    graph = w.get("graph") or {"nodes": [], "edges": []}
    # Before anything reads it. This function keeps the operator's own
    # settings across a zone save by name -- see the `keep` loop below -- and
    # those names changed with the pid -> OID rename. A stored graph still
    # spelling it the old way would have its OID tick found under neither
    # name and silently dropped, which is the exact bug that loop exists to
    # prevent.
    migrate_pid_to_oid(graph)
    nodes = [n for n in (graph.get("nodes") or [])
             if not str(n.get("id") or "").startswith(ZONE_NODE_PREFIX)]
    # What a previous sync drew, kept so a rebuild can reuse the operator's
    # positions and so a chain whose rule is gone survives as an orphan.
    previous = {str(n.get("id")): n for n in (graph.get("nodes") or [])
                if str(n.get("id") or "").startswith(ZONE_NODE_PREFIX)}
    kept_ids = {n["id"] for n in nodes}
    # Edges the operator drew FROM a mirrored analytics node (to an Alert, a
    # card, a formula) are theirs -- keep any edge whose surviving end is
    # still going to exist. Dangling ones are dropped by clean_graph anyway.
    edges = list(graph.get("edges") or [])

    # One Detection Stream for the whole canvas: reuse the operator's if they
    # have one, so mirrored chains join the graph they already built rather
    # than growing a second, parallel source.
    source = next((n for n in nodes if n.get("type") == "source.detection"), None)
    # The mirror may have drawn the source itself on an earlier sync; it lives
    # in `previous`, so it has to be carried forward explicitly or the rebuild
    # would drop the very node every chain hangs off.
    mirror_source = None
    if source is None:
        mirror_source = next((n for n in previous.values()
                              if n.get("type") == "source.detection"), None)
        source = mirror_source
    source_id = source["id"] if source else _zone_node_id("__source__", "s")

    zone_by_id = {z["id"]: z for z in (zones or [])}
    # Mirrored chains stack below whatever is already on the canvas, so a
    # rebuild never lands on top of the operator's own nodes.
    base_y = 40
    for n in nodes:
        base_y = max(base_y, float((n.get("position") or {}).get("y") or 0) + 140)

    def place(nid, default_x, default_y):
        """Where a mirrored node goes: exactly where it already is, if it has
        been drawn before. A rebuild that reshuffled the canvas would undo
        every tidy-up the operator did."""
        was = previous.get(nid)
        if was and isinstance(was.get("position"), dict):
            return dict(was["position"])
        return {"x": default_x, "y": default_y}

    made = []
    if source is None:
        made.append({"id": source_id, "type": "source.detection",
                     "position": place(source_id, 40, base_y), "data": {}})
    elif mirror_source is not None:
        made.append(dict(mirror_source))

    # DELETIONS ARE REMEMBERED. `zone_drawn` records every mirrored node id
    # ever drawn; one that was drawn and is now gone from the canvas was
    # DELETED by the operator, and a zone save must not resurrect it -- the
    # operator who replaced a mirrored chain with their own build was getting
    # the mirror re-added on every save. Unticking the action clears its ids
    # from the ledger (they stop being candidates below), so unticking and
    # re-ticking is the deliberate way to have the chain drawn again.
    drawn = {str(x) for x in (w.get("zone_drawn") or [])}
    candidates = set()

    def add(node):
        """Append a mirrored node -- unless the operator deleted it before."""
        nid = node["id"]
        candidates.add(nid)
        if nid not in previous and nid in drawn:
            return False
        drawn.add(nid)
        made.append(node)
        return True

    row = 0
    for fn in zone_fns or []:
        ntype = _ZONE_FN_NODES.get(fn.get("type"))
        if not ntype:
            continue                     # proximity/guard configure classes,
                                         # not zones: nothing to draw a chain from
        fid = str(fn.get("id") or "")
        if not fid:
            continue
        if fn.get("type") == "object_detect" \
                and not (fn.get("params") or {}).get("zone"):
            continue                     # a whole-frame watch: no zone to draw
        y = base_y + row * 140
        row += 1

        is_off = fid in off or not fn.get("enabled", True)
        data = {"fnId": fid, "name": fn.get("name") or fid}
        if is_off:
            data["disabled"] = True
        params = fn.get("params") or {}
        if fn.get("trigger"):
            # The action's alert travels with it, so the compiled function is
            # the same whether it came from here or from the Zones page.
            data["trigger"] = dict(fn["trigger"])
        if fn["type"] == "zone_unique":
            # The mirrored node must SAY it is the distinct counter: the Zone
            # Counter's default mode is realtime now, so a mode-less mirror
            # would compile to zone_count and the action's own alert
            # (new > 0) would name a metric that mode does not publish.
            data["mode"] = "distinct"
            if params.get("min_present") is not None:
                data["limit"] = params["min_present"]
        if fn["type"] == "line_travel":
            data["distance_m"] = params.get("distance_m")
            data["speed_unit"] = params.get("speed_unit") or "kmh"
        # The Cycle Time node carries the action's mode, so the canvas and
        # the Zones page describe the same measurement in the same words.
        if fn["type"] == "zone_dwell":
            # One mode, and the two things that used to be modes said as the
            # settings they are: a record of each stay is "track objects,
            # delivered once"; a per-object threshold is "track objects,
            # delivered live".
            data["mode"] = "present"
            if params.get("stamp_events"):
                data["OID"], data["delivery"] = True, "once"
            elif fn.get("trigger"):
                data["OID"], data["delivery"] = True, "live"
        elif fn["type"] == "zone_countdown":
            data["mode"] = "countdown"
            data["start_s"] = params.get("start_s", 60)
            data["deduct_s"] = params.get("deduct_s", 1)
            if params.get("reset") == "never":
                data["reset"] = "never"
        elif fn["type"] == "zone_occupancy":
            data["mode"] = "occupancy"

        an_id = _zone_node_id(fid, "a")
        # The mirror owns what the ZONE decided (name, trigger, mode, limit);
        # what the operator set ON THE NODE survives the redraw -- the OID
        # tick and the palette exposure are canvas decisions the Zones page
        # knows nothing about, and a zone save was silently unticking them.
        was = (previous.get(an_id) or {}).get("data") or {}
        for keep in ("OID", "expose"):
            if keep in was:
                data[keep] = was[keep]
        add({"id": an_id, "type": ntype,
             "position": place(an_id, 820, y), "data": data})

        classes = list(params.get("classes") or [])
        prev_id = source_id
        if classes:
            f_id = _zone_node_id(fid, "f")
            f_data = {"classes": classes, **class_filter_service.kept_name(previous.get(f_id))}
            if is_off:
                f_data["disabled"] = True
            add({"id": f_id, "type": "filter.class",
                 "position": place(f_id, 320, y), "data": f_data})
            # The one wire a switched-off rule does NOT get. Everything after
            # it stays drawn, so re-enabling the zone reconnects a chain the
            # operator can already see rather than conjuring one.
            if not is_off:
                edges.append({"id": f"e{f_id}", "source": source_id,
                              "target": f_id, "sourceHandle": None,
                              "targetHandle": None})
            prev_id = f_id

        # Which places this rule reads. Most read one; a span reads two, and
        # each end gets its own Zone node on its own handle.
        places = []
        if fn["type"] == "zone_travel":
            places = [(params.get("start"), "start"), (params.get("end"), "end")]
        elif fn["type"] == "line_travel":
            places = [(params.get("line_a"), "line_a"),
                      (params.get("line_b"), "line_b")]
        else:
            places = [(params.get("line") or params.get("zone"), None)]

        for i, (place_id, handle) in enumerate(places):
            if not place_id:
                continue
            z_id = _zone_node_id(fid, f"z{i}")
            z_data = {"zone": place_id}
            if is_off:
                z_data["disabled"] = True
            add({"id": z_id, "type": "zone.ref",
                 "position": place(z_id, 570, y + i * 70),
                 "data": z_data})
            # Wired to whatever feeds it -- unless nothing does, because the
            # rule is off and the link back to the source was never made.
            if not (is_off and prev_id == source_id):
                edges.append({"id": f"e{z_id}i", "source": prev_id,
                              "target": z_id, "sourceHandle": None,
                              "targetHandle": None})
            edges.append({"id": f"e{z_id}o", "source": z_id, "target": an_id,
                          "sourceHandle": None, "targetHandle": handle})

        # The ALERT, and whatever it was told to capture, as real nodes.
        #
        # The rule's trigger already travels as data on the measuring node, so
        # the system behaved correctly -- but the canvas showed no Alert, no
        # Snapshot and no Clip, so ticking "Raise an alert" on the Zones page
        # produced a workflow that looked like it did nothing. The whole point
        # of mirroring is that the two pages describe the same thing.
        trig = fn.get("trigger") or {}
        if trig:
            al_id = _zone_node_id(fid, "al")
            al_data = {"metric": trig.get("metric") or "",
                       "op": trig.get("op") or ">",
                       "value": trig.get("value", 1),
                       "severity": trig.get("severity") or "warn",
                       "message": trig.get("message") or "",
                       "cooldown_s": trig.get("cooldown_s", 30)}
            # The box colour is a canvas knob the Zones page has no field for.
            was_al = (previous.get(al_id) or {}).get("data") or {}
            for keep in ("box_color", "zone_color", "color_target"):
                if keep in was_al:
                    al_data[keep] = was_al[keep]
            if is_off:
                al_data["disabled"] = True
            add({"id": al_id, "type": "output.alert",
                 "position": place(al_id, 1100, y), "data": al_data})
            edges.append({"id": f"e{al_id}", "source": an_id, "target": al_id,
                          "sourceHandle": None, "targetHandle": "in"})
            # A photo / a clip hangs off the RULE, beside the Alert -- not off
            # the Alert itself. That mirrors what the Zones page compiles (the
            # capture output binds to the function, firing on its alert), and
            # it is the only wiring the graph allows: an output cannot feed
            # another output.
            # A violation is a STATE, so the chain ends in the card that shows
            # one. Without it, ticking the box produced a chain that stopped at
            # an Alert -- everything worked, nothing was visible, and every
            # operator's first move was to draw this card by hand and wire it
            # up. Drawn once, at the end, where they were putting it anyway.
            if fid in verdicts:
                v_id = _zone_node_id(fid, "v")
                zone_name = ""
                for OID, _handle in places:
                    zone_name = (zone_by_id.get(OID) or {}).get("name") or ""
                    if zone_name:
                        break
                v_data = {**REGISTRY["dashboard.card"]["data"],
                          "name": zone_name or fn.get("name") or fid,
                          "kind": "verdict",
                          # The rule's alert STATE: 1 while its condition
                          # holds, 0 while it does not -- which is exactly the
                          # two things the card's two labels name.
                          "metric": "active"}
                # A card the operator has already dressed -- labels, colours,
                # shape, states -- is THEIRS. The mirror only refreshes the
                # binding; everything else keeps what they set.
                was_v = (previous.get(v_id) or {}).get("data") or {}
                if was_v:
                    v_data = {**v_data, **{k: v for k, v in was_v.items()
                                           if k not in ("disabled",)}}
                if is_off:
                    v_data["disabled"] = True
                add({"id": v_id, "type": "dashboard.card",
                     "position": place(v_id, 1380, y), "data": v_data})
                edges.append({"id": f"e{v_id}", "source": al_id, "target": v_id,
                              "sourceHandle": None, "targetHandle": "in"})
            for i, kind in enumerate(captures.get(fid) or ()):
                cap_id = _zone_node_id(fid, f"c{i}")
                cap_data = ({"mode": "once", "interval_s": 5} if kind == "snapshot"
                            else {"duration_s": 5, "mode": "once",
                                  "interval_s": 10})
                if is_off:
                    cap_data["disabled"] = True
                add({"id": cap_id, "type": f"output.{kind}", "data": cap_data,
                     "position": place(cap_id, 1100, y + (i + 1) * 80)})
                edges.append({"id": f"e{cap_id}", "source": an_id,
                              "target": cap_id, "sourceHandle": None,
                              "targetHandle": "in"})
    del zone_by_id

    # Chains whose rule is gone -- the action was deleted, or the zone's
    # violation tick was cleared. They STAY on the canvas exactly where they
    # were: switched off, and cut from the Detection Stream. Nothing the
    # operator can see is destroyed by a tick box on another page.
    made_ids = {n["id"] for n in made}
    orphans = []
    for nid, node in previous.items():
        if nid in made_ids or nid == source_id:
            continue
        node = dict(node)
        node["data"] = {**(node.get("data") or {}), "disabled": True}
        orphans.append(node)
    orphan_ids = {n["id"] for n in orphans}

    new_ids = kept_ids | made_ids | orphan_ids
    graph = {"version": SCHEMA_VERSION,
             "nodes": nodes + made + orphans,
             "edges": [e for e in edges
                       if e.get("source") in new_ids and e.get("target") in new_ids
                       # The one wire an orphan loses: whatever fed it. Its own
                       # internal wiring stays, so the chain still reads as a
                       # chain and reconnects with a single drag.
                       and not (e.get("source") == source_id
                                and e.get("target") in orphan_ids)]}
    # De-duplicate edges by id: a rebuild re-adds the mirror's own edges, and
    # the operator's edges onto the analytics node are already in the list.
    seen, deduped = set(), []
    for e in graph["edges"]:
        if e.get("id") in seen:
            continue
        seen.add(e.get("id"))
        deduped.append(e)
    graph["edges"] = deduped
    class_filter_service.name_filters(graph, only=made_ids)   # several need a name

    # The deletion ledger keeps only ids the CURRENT rules would draw: an
    # unticked action drops its ids here, so unticking and re-ticking is what
    # brings a deleted chain back on purpose.
    drawn &= candidates
    ledger = sorted(drawn)
    if graph == (w.get("graph") or {}) and ledger == sorted(
            str(x) for x in (w.get("zone_drawn") or [])):
        return False
    w["graph"] = graph
    w["zone_drawn"] = ledger
    try:
        save_store(slug, store)
    except OSError:
        return False
    return True


#: Node-data keys that hold a zone (or line) id -- the same keys
#: templates._unbind_places blanks when a graph leaves its site.
ZONE_REF_KEYS = ("zone", "line", "line_a", "line_b", "start", "end")


def forget_zones(slug, zone_ids, dry_run=False):
    """Unbind every canvas node from zones that no longer exist.

    Called when zones are deleted from somewhere other than the canvas (a
    camera removed with its zones). The NODES stay: a Zone node, a Zone
    Switch or a mask step keeps its place and its wiring, and its binding
    goes blank -- the compiler then says "waiting for a zone" on it and
    switches that chain off, which is the same state a template lands in
    (_unbind_places). A dangling id instead would read 0 for ever, or stop
    a Trigger's zone port from compiling at all.

    Only a value that IS one of the deleted ids is cleared, so a Schedule's
    `start` time or a zone that survived is never touched. Returns the nodes
    affected, [{id, type, label}]; with `dry_run` nothing is written.
    """
    gone = {str(z) for z in (zone_ids or ()) if str(z)}
    if not gone:
        return []
    store = load_store(slug)
    if not store or not store.get("workflows"):
        return []
    affected = []
    for w in store["workflows"]:
        for node in (w.get("graph") or {}).get("nodes") or []:
            data = node.get("data")
            if not isinstance(data, dict):
                continue
            hit = False
            for key in ZONE_REF_KEYS:
                if isinstance(data.get(key), str) and data[key] in gone:
                    data[key] = ""
                    hit = True
            # A Pre-process mask names its zones in a list per step.
            for step in data.get("steps") or []:
                if isinstance(step, dict) and isinstance(step.get("zones"), list):
                    left = [z for z in step["zones"] if str(z) not in gone]
                    if len(left) != len(step["zones"]):
                        step["zones"] = left
                        hit = True
            if not hit:
                continue
            # The copy of the zone's shape a Zone node carries for export is
            # a copy of a zone that no longer exists.
            data.pop("zoneGeometry", None)
            spec = REGISTRY.get(node.get("type")) or {}
            affected.append({"id": str(node.get("id") or ""),
                             "type": str(node.get("type") or ""),
                             "label": str(data.get("name") or spec.get("label")
                                          or node.get("type") or "")})
    if affected and not dry_run:
        save_store(slug, store)
    return affected


def migrate_from_config(systems_dir, slug):
    """Synthesize a graph from the existing [[functions]] + [[outputs]] so the
    first visit to the Workflow page shows the deployment's real pipeline, not
    an empty canvas. Function ids are REUSED as fnIds, so stat keys, history
    series and existing widget bindings all survive the migration."""
    fns = functions_service.get_functions(systems_dir, slug)
    outs = dc.get_outputs(systems_dir, slug)

    nodes, edges = [], []
    col_y = {}                                   # x column -> next free y

    def place(rank):
        x = 40 + rank * 260
        y = col_y.get(rank, 40)
        col_y[rank] = y + 120
        return {"x": x, "y": y}

    def add(ntype, data, rank=None):
        nid = f"m{len(nodes) + 1}"
        nodes.append({"id": nid, "type": ntype,
                      "position": place(REGISTRY[ntype]["rank"] if rank is None else rank),
                      "data": data})
        return nid

    def wire(src, tgt, handle=None):
        edges.append({"id": f"e{len(edges) + 1}", "source": src, "target": tgt,
                      "sourceHandle": None, "targetHandle": handle})

    source_id = add("source.detection", {})

    type_map = {"zone_count": "analytics.occupancy",
                "zone_dwell": "analytics.dwell",
                "zone_countdown": "analytics.dwell",
                "zone_occupancy": "analytics.dwell",
                "zone_unique": "analytics.visitors",
                "line_count": "analytics.entry_exit",
                "line_travel": "analytics.travel",
                "zone_travel": "analytics.cycle_time",
                "expr": "analytics.expr"}

    for fn in fns:
        # A hand-written config may hold a rule this migration has no node for
        # (proximity and guard configure classes, not zones, so they have
        # nothing to draw a chain from). Skipping leaves that function running
        # untouched in the config -- crashing the first visit to the Workflow
        # page over it would be the worse trade.
        if fn["type"] not in type_map:
            continue
        p = fn.get("params") or {}
        data = {"fnId": fn["id"], "name": fn["name"]}
        if fn.get("trigger"):
            data["trigger"] = dict(fn["trigger"])
        if fn["type"] == "line_travel":
            data["distance_m"] = p.get("distance_m")
            data["speed_unit"] = p.get("speed_unit") or "kmh"
        if fn["type"] == "expr":
            data["expr"] = p.get("expr") or ""
        if fn["type"] == "zone_dwell":
            data["mode"] = ("stamps" if p.get("stamp_events")
                            else "timer" if fn.get("trigger") else "dwell")
        elif fn["type"] == "zone_countdown":
            data["mode"] = "countdown"
            data["start_s"] = p.get("start_s", 60)
            data["deduct_s"] = p.get("deduct_s", 1)
        elif fn["type"] == "zone_occupancy":
            data["mode"] = "occupancy"
        an_id = add(type_map[fn["type"]], data)

        def zone_chain(zone_id, handle=None, _an_id=an_id):
            zid = add("zone.ref", {"zone": zone_id})
            classes = p.get("classes") or []
            if classes:
                fid = add("filter.class", {"classes": list(classes)})
                wire(source_id, fid)
                wire(fid, zid)
            else:
                wire(source_id, zid)
            wire(zid, _an_id, handle)

        if fn["type"] in ("zone_count", "zone_dwell", "zone_unique",
                          "zone_countdown", "zone_occupancy"):
            zone_chain(p.get("zone"))
        elif fn["type"] == "line_count":
            zone_chain(p.get("line"))
        elif fn["type"] == "line_travel":
            zone_chain(p.get("line_a"), "line_a")
            zone_chain(p.get("line_b"), "line_b")
        elif fn["type"] == "zone_travel":
            zone_chain(p.get("start"), "start")
            zone_chain(p.get("end"), "end")

        # Outputs already bound to this function (the new binding) come across
        # exactly; legacy severity-matched outputs are left alone -- they are
        # hand-made and keep firing on their coarse trigger.
        for o in outs:
            if o.get("function") == fn["id"] and o.get("type"):
                data = {k: o[k] for k in
                        REGISTRY.get(f"output.{o['type']}", {}).get("data", {})
                        if k in o}
                oid = add(f"output.{o['type']}", data)
                wire(an_id, oid)

    return class_filter_service.name_filters({"version": SCHEMA_VERSION, "nodes": nodes, "edges": edges})


def _released_ports():
    """Board terminals this box has handed to another program.

    Imported lazily and swallowed whole: compiling a graph must not fail
    because a board service is unavailable, and a box with no board has
    nothing released -- the honest answer there is the empty set, not an
    exception halfway through a compile.
    """
    try:
        from core.services import board_io                    # noqa: PLC0415
        return board_io.released()
    except Exception:                                          # noqa: BLE001
        return set()


def registry_public():
    """The registry as served to the frontend (palette, forms, validation)."""
    return {k: dict(v) for k, v in REGISTRY.items()}
