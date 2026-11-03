# The Inject node: a button on the canvas that pushes a value into the flow.
#
# Node-RED's inject, in this product's terms. It is filed under Common but
# WIRES as a variable, like the Schedule, so it is both halves of what an
# inject is for:
#
#   a value   its `value` holds the last payload -- a number or 1/0 a Math,
#             Compare or card can use, text/JSON an MQTT Publish or a
#             Webhook sends on
#   a when    wired into an output (or a Modbus Write's trigger port) it
#             fires it once per inject, through `fired`, exactly as a
#             Schedule does through `due`
#
# and it injects three ways: the button (master -> app, through a small file
# in the app's data directory), once after N seconds at start, and on a
# repeat interval.
#
# What is pinned here:
#   1. the palette: first in Common, not deprecated, wired as a variable
#   2. payload types: string / number / boolean / JSON / timestamp, cleaned
#      and refused the same way on both sides
#   3. the compile: arms an output by itself, feeds a Modbus Write trigger
#      and a value port, turns repeat N+unit into interval_s
#   4. the engine: once-after, repeat, a press, a stale press ignored, a
#      save of another node does not re-fire it
#   5. the route: gated on workflow.edit, refuses an unsaved / undeployed
#      node and a stopped app, and writes the press the engine reads
#   6. the dashboard button: offered in the catalogue as kind "button",
#      validated, rendered with its runs, marked when its node is deleted
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service           # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from core.services import functions as functions_service       # noqa: E402
from core.services import workflow as wf                       # noqa: E402
from app import functions as fx                                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. the palette -----------------------------------------------------------
reg = wf.registry_public()
spec = reg.get("common.inject") or {}
check("the Inject is in the registry", bool(spec))
check("...filed under Common", (spec.get("palette") or {}).get("cat") == "common",
      spec.get("palette"))
check("...offered, not deprecated or hidden",
      not spec.get("deprecated") and not spec.get("hidden"))
check("...and wired as a variable, so it reaches the engine",
      spec.get("category") == "variable" and spec.get("fn_type") == "inject")
common = [t for t, s in reg.items()
          if (s.get("palette") or {}).get("cat", s.get("category")) == "common"
          and not s.get("deprecated") and not s.get("hidden")]
check("...first in the Common drawer", common[:1] == ["common.inject"], common)
# Its own stage since 2026-10-05 (Tok Aba): first, above Input.
check("the Common drawer is in the palette order, first",
      'const PALETTE_ORDER = ["common",' in read("frontend", "src", "pages",
                                                 "Workflow.jsx"))

# -- 2. payload types ---------------------------------------------------------
clean = functions_service.clean_inject_payload
check("a string is kept as typed", clean("string", " hi ") == ("string", " hi "))
check("a number is checked", clean("number", "4.5") == ("number", "4.5"))
check("true/false is normalised", clean("boolean", "1") == ("boolean", "true"))
check("JSON is kept as typed", clean("json", '{"a": 1}') == ("json", '{"a": 1}'))
check("a timestamp carries no payload", clean("timestamp", "x") == ("timestamp", ""))
for ptype, payload, word in (("number", "abc", "not a number"),
                             ("json", "{a:1}", "does not parse"),
                             ("boolean", "maybe", "true or false"),
                             ("buffer", "", "must be one of")):
    try:
        clean(ptype, payload)
        check(f"{ptype} {payload!r} is refused", False, "accepted")
    except ValueError as exc:
        check(f"{ptype} {payload!r} is refused: {exc}", word in str(exc), str(exc))

# The app's own copy of the rule, evaluated when it fires.
check("app: number -> int where whole", fx.inject_value("number", "5", 0) == 5)
check("app: number -> float", fx.inject_value("number", "2.5", 0) == 2.5)
check("app: boolean -> 1/0", (fx.inject_value("boolean", "true", 0),
                              fx.inject_value("boolean", "false", 0)) == (1, 0))
check("app: JSON object -> compact text",
      fx.inject_value("json", '{ "a" : [1, 2] }', 0) == '{"a":[1,2]}')
check("app: JSON number -> the number", fx.inject_value("json", "7", 0) == 7)
check("app: timestamp -> ms at fire time",
      fx.inject_value("timestamp", "", 1700000000.25) == 1700000000250)
check("app and core agree on the types",
      fx.INJECT_TYPES == functions_service.INJECT_TYPES)


# -- 3. the compile -----------------------------------------------------------
def inject_node(nid="i1", **data):
    d = {**wf.REGISTRY["common.inject"]["data"], "name": "Kick", **data}
    return {"id": nid, "type": "common.inject", "position": {"x": 0, "y": 0},
            "data": d}


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 1, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], tables={})


g, out = compile_([inject_node(ptype="number", payload="5", repeat="interval",
                               every=2, every_unit="minutes", once=True,
                               once_delay_s=3),
                   node("m1", "output.mqtt")],
                  [{"id": "e1", "source": "i1", "target": "m1"}])
check("inject -> MQTT compiles", not out["errors"], out["errors"])
fn = out["functions"][0] if out["functions"] else {}
check("...as an inject function", fn.get("type") == "inject", fn)
check("...repeat 2 minutes is interval_s 120",
      fn.get("params", {}).get("interval_s") == 120, fn.get("params"))
check("...once after 3 s is kept",
      fn.get("params", {}).get("once") is True
      and fn.get("params", {}).get("once_delay_s") == 3, fn.get("params"))
check("...and it arms the output by itself, on `fired`",
      [t["metric"] for t in fn.get("triggers") or []] == ["fired"]
      and out["outputs"][0]["function"] == fn.get("id"), fn.get("triggers"))

_g, out = compile_([inject_node(), node("m1", "output.mqtt")],
                   [{"id": "e1", "source": "i1", "target": "m1"}])
check("a timestamp inject compiles", not out["errors"], out["errors"])

_g, out = compile_([inject_node(ptype="json", payload="{bad")], [])
check("bad JSON is a node error",
      any(e["node"] == "i1" and "JSON" in e["message"] for e in out["errors"]),
      out["errors"])
_g, out = compile_([inject_node(repeat="interval", every=0)], [])
check("a zero repeat is refused",
      any("repeat" in e["message"] for e in out["errors"]), out["errors"])

# Into a VALUE port: a Math node reads what it last injected.
math_type = next(t for t in wf.REGISTRY if t.startswith("math.")
                 and not wf.REGISTRY[t].get("deprecated"))
ok, why = wf.edge_allowed("common.inject", math_type, "a")
check(f"an Inject feeds {math_type}", ok, why)
ok, why = wf.edge_allowed("common.inject", "dashboard.card")
check("...and a card", ok, why)
ok, why = wf.edge_allowed("common.inject", "output.modbus_write", "trig")
check("...and a Modbus Write trigger port", ok, why)
_g, out = compile_([inject_node(ptype="number", payload="3"),
                    node("c1", "dashboard.card")],
                   [{"id": "e1", "source": "i1", "target": "c1"}])
check("a card shows its value", not out["errors"]
      and out["widgets"][0]["key"].endswith("_value"), out)

# The canvas's own copy of the wiring rules knows it too: it wires as a
# variable, which the client rules already let feed outputs and values.
js = read("frontend", "src", "workflow", "nodes.jsx")
check("the canvas draws an Inject with its own button",
      '"common.inject": InjectNode' in js and "onInject" in js)

# -- 4. the engine ------------------------------------------------------------
state = tempfile.mkdtemp()


def engine(params, fid="kick"):
    fx._inject_state.pop(fid, None)
    f = fx.Function({"id": fid, "name": "Kick", "type": "inject",
                     "enabled": True, "params": params,
                     "triggers": [{"metric": "fired", "op": ">", "value": 0,
                                   "severity": "info", "cooldown_s": 0}]})
    return fx.FunctionEngine([f], state_dir=state)


def tick(eng, now):
    fired = eng.update({"crossing_events": []}, [], {}, {}, 640, 480, now=now)
    return eng.stats(), fired


eng = engine({"ptype": "number", "payload": "5", "once": True,
              "once_delay_s": 2})
st, fired = tick(eng, 1000.0)
check("nothing before the start delay", st["fn_kick_fired"] == 0
      and st["fn_kick_value"] == 0 and not fired, st)
st, fired = tick(eng, 1002.5)
check("once after 2 s: fires, value 5", st["fn_kick_fired"] == 1
      and st["fn_kick_value"] == 5 and len(fired) == 1, (st, fired))
st, fired = tick(eng, 1003.0)
check("...for one tick only", st["fn_kick_fired"] == 0 and not fired, st)
st, _ = tick(eng, 1100.0)
check("...and only once", st["fn_kick_runs"] == 1, st)

# A save that rebuilds the engine without touching THIS node does not re-arm.
f2 = fx.Function({"id": "kick", "name": "Kick", "type": "inject",
                  "enabled": True, "params": {"ptype": "number", "payload": "5",
                                              "once": True, "once_delay_s": 2}})
eng2 = fx.FunctionEngine([f2], state_dir=state)
st, _ = tick(eng2, 1200.0)
check("a rebuilt engine does not fire 'once' again", st["fn_kick_fired"] == 0
      and st["fn_kick_runs"] == 1, st)

eng = engine({"ptype": "boolean", "payload": "true", "interval_s": 10})
fires = []
for t in range(0, 35):
    st, _ = tick(eng, 5000.0 + t)
    fires.append(st["fn_kick_fired"])
check("repeat every 10 s fires three times in 35 s", sum(fires) == 3, fires)
check("...value 1 for true", st["fn_kick_value"] == 1, st)

# A PRESS from the master: the file the route writes.
eng = engine({"ptype": "string", "payload": "go", "topic": "line/2"})
tick(eng, 9000.0)
with open(os.path.join(state, fx.INJECT_FILE), "w", encoding="utf-8") as f:
    json.dump({"kick": {"seq": 1, "ts": 9000.5}}, f)
st, fired = tick(eng, 9001.0)
check("a press fires it", st["fn_kick_fired"] == 1 and st["fn_kick_value"] == "go"
      and len(fired) == 1, (st, fired))
check("...and says it passed, and when (the green circle)",
      st["fn_kick_ok"] == 1 and st["fn_kick_last_at"] == 9001.0, st)
check("...and publishes the topic", st.get("fn_kick_topic") == "line/2", st)
st, _ = tick(eng, 9001.5)
check("...once per press", st["fn_kick_fired"] == 0, st)
os.utime(os.path.join(state, fx.INJECT_FILE), (9002, 9002))
with open(os.path.join(state, fx.INJECT_FILE), "w", encoding="utf-8") as f:
    json.dump({"kick": {"seq": 2, "ts": 9100.0}}, f)
# A different mtime from the first write, whatever the clock resolution: two
# writes inside one filesystem tick otherwise read as "unchanged" (a flake).
os.utime(os.path.join(state, fx.INJECT_FILE), (9003, 9003))
st, _ = tick(eng, 9100.3)
check("a second press fires again", st["fn_kick_fired"] == 1
      and st["fn_kick_runs"] == 2, st)

# A press left in the file from before a restart is not a press now.
eng = engine({"ptype": "string", "payload": "go"})
with open(os.path.join(state, fx.INJECT_FILE), "w", encoding="utf-8") as f:
    json.dump({"kick": {"seq": 7, "ts": 100.0}}, f)
st, _ = tick(eng, 20000.0)
check("a stale press is not replayed on start", st["fn_kick_fired"] == 0, st)
check("...and the drop is said (the red circle), with no fire time",
      st.get("fn_kick_ok") == 0 and "fn_kick_last_at" not in st, st)
eng = engine({"ptype": "string", "payload": "go"}, fid="fresh")
st, _ = tick(eng, 20001.0)
check("an Inject nobody has pressed says nothing about passing or failing",
      "fn_fresh_ok" not in st and "fn_fresh_last_at" not in st, st)


# -- 5. the route -------------------------------------------------------------
# The rules live in workflow.inject_target (this laptop has no web stack to
# drive the route with); the route adds only WHO may press.
saved = {"nodes": [
    {"id": "i1", "type": "common.inject", "data": {"fnId": "kick"}},
    {"id": "i2", "type": "common.inject", "data": {"fnId": "gone"}}],
    "edges": []}
deployed = [{"id": "kick", "type": "inject", "enabled": True}]


def press(node_id, running=True):
    try:
        return 200, wf.inject_target(saved, deployed, node_id, running)
    except wf.InjectRefused as exc:
        return exc.status, str(exc)


code, body = press("i1")
check("a press of a deployed Inject names its function",
      (code, body) == (200, "kick"), (code, body))
code, body = press("nope")
check("an unsaved node is refused", code == 404 and "save" in body, body)
code, body = press("i2")
check("a saved but not deployed one is refused", code == 409, (code, body))
code, body = press("i1", running=False)
check("a stopped app is refused", code == 409 and "not running" in body, body)

route = read("core", "routes", "workflow.py")
body_of = route[route.index('@router.post("/api/workflow/inject"'):]
body_of = body_of[:body_of.index("\ndef _send_one")]
check("the route is gated on workflow.edit",
      'perm.require(request, "workflow.edit")' in body_of)
check("...answers with inject_target's status",
      "workflow_service.inject_target(" in body_of
      and "status_code=exc.status" in body_of)
builder_route = read("core", "routes", "builder.py")
# The gate is asked by the route (it knows who is looking) and stamped by
# dashboard_controls, which holds every control's capability in one table.
controls = read("core", "services", "dashboard_controls.py")
check("the dashboard tells a button whether this viewer may press it",
      "perm.require(request, cap)" in builder_route
      and "mark_gates(widgets, can)" in builder_route
      and '"button": "workflow.edit"' in controls
      and '"can_press"' in controls)

# The press itself: written where the app reads it, one sequence per press.
data_dir = tempfile.mkdtemp()
real = (dc._pkg_dir, dc._data_dir_cached)
dc._pkg_dir = lambda sd, slug: "/nowhere/demo"
dc._data_dir_cached = lambda pkg: data_dir
try:
    first = dc.request_inject("/nowhere", "demo", "kick")
    second = dc.request_inject("/nowhere", "demo", "kick")
    with open(os.path.join(data_dir, dc.INJECT_FILE), encoding="utf-8") as f:
        written = json.load(f)
finally:
    dc._pkg_dir, dc._data_dir_cached = real
check("each press is a new sequence", (first, second) == (1, 2),
      (first, second))
check("...in the file the app reads",
      written["kick"]["seq"] == 2 and dc.INJECT_FILE == fx.INJECT_FILE, written)

# ...and the app fires the master's own write.
eng = engine({"ptype": "number", "payload": "9"})
eng._state_dir = data_dir
import time as _time                                          # noqa: E402
now = _time.time()
tick(eng, now)
dc._pkg_dir = lambda sd, slug: "/nowhere/demo"
dc._data_dir_cached = lambda pkg: data_dir
try:
    dc.request_inject("/nowhere", "demo", "kick")
finally:
    dc._pkg_dir, dc._data_dir_cached = real
# A real press comes a human moment after the last; this one comes within the
# same 4 ms kernel tick as the writes above, which on Linux leaves the mtime
# unchanged. Moved on, as time would have moved it.
_inj = os.path.join(data_dir, dc.INJECT_FILE)
_ns = os.stat(_inj).st_mtime_ns + 10 ** 9
os.utime(_inj, ns=(_ns, _ns))
st, _ = tick(eng, now + 1)
check("the app fires a press the master wrote", st["fn_kick_fired"] == 1
      and st["fn_kick_value"] == 9, st)

# -- 6. the dashboard button --------------------------------------------------
_g, out = compile_([inject_node(ptype="number", payload="1")], [])
entry = next((c for c in out["catalog"] if c.get("kind") == "button"), None)
check("the Inject is offered to the dashboard as a button", entry is not None
      and entry["origin"] == "control" and entry["surfaces"] == ["dashboard"],
      entry)
widget = {"id": "b1", "title": "Kick", "kind": "button", "source": "stat",
          "key": entry["key"], "node": entry["node"], "inject": entry["inject"]}
clean_w = builder_service.validate([widget])[0]
check("...it survives the layout validator",
      clean_w["kind"] == "button" and clean_w["node"] == "i1"
      and clean_w["inject"]["fn"] == entry["inject"]["fn"], clean_w)
fid = entry["inject"]["fn"]
rendered = builder_service.compute([clean_w], [], {f"fn_{fid}_runs": 3,
                                                   f"fn_{fid}_value": 1})[0]
check("...and renders its runs and last value",
      rendered["data"]["runs"] == 3 and rendered["data"]["value"] == 1
      and rendered["data"]["node"] == "i1"
      and rendered["data"]["removed"] is False, rendered["data"])

# Its node deleted and the workflow saved: the button is kept and MARKED.
cfg = tempfile.mkdtemp()
builder_service.save(cfg, "demo", {"title": "", "pages": [
    {"id": builder_service.MAIN_PAGE_ID, "name": "Dashboard",
     "widgets": [clean_w]}]})
real_set = (functions_service.set_functions, dc.get_outputs, dc.set_outputs,
            dc.set_commands, dc.set_watches)
functions_service.set_functions = lambda *a, **k: None
real_get = functions_service.get_functions
functions_service.get_functions = lambda *a, **k: []
dc.get_outputs = lambda *a, **k: []
dc.set_outputs = lambda *a, **k: None
dc.set_commands = lambda *a, **k: None
dc.set_watches = lambda *a, **k: None
try:
    empty = wf.compile_graph(wf.clean_graph({"nodes": [], "edges": []}), [],
                             tables={})
    try:
        wf.apply_artifacts("/nowhere", cfg, "demo", empty)
    except Exception as exc:                                  # noqa: BLE001
        # Past the layout write, the rest of apply touches a real box.
        print("      (apply stopped after the layout:", type(exc).__name__, ")")
    layout = builder_service.load(cfg, "demo")
    btn = next(w for p in layout["pages"] for w in p["widgets"]
               if w["id"] == "b1")
    check("a button whose Inject was deleted is kept and marked removed",
          btn["inject"]["removed"] is True, btn)
    shown = builder_service.compute([btn], [], {})[0]["data"]
    check("...and says so when drawn", shown["removed"] is True, shown)
finally:
    (functions_service.set_functions, dc.get_outputs, dc.set_outputs,
     dc.set_commands, dc.set_watches) = real_set
    functions_service.get_functions = real_get

shared = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("a button is a live control, Dashboard only",
      'CONTROL_KINDS = ["button",' in shared
      and '"video", "snap_gallery", "status", "verdict", ...CONTROL_KINDS' in shared)


# -- 7. Start / Stop of a repeating Inject ------------------------------------
def write_press(seq, ts, action):
    path = os.path.join(state, fx.INJECT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"kick": {"seq": seq, "ts": ts, "action": action}}, f)
    # A new mtime every write, so the engine's cheap stat always sees it.
    os.utime(path, (ts, ts))


REPEAT = {"ptype": "number", "payload": "1", "interval_s": 10}
eng = engine(dict(REPEAT))
st, _ = tick(eng, 30000.0)
check("a repeat starts with the app by default (today's behaviour)",
      st["fn_kick_running"] == 1, st)
write_press(1, 30001.0, "stop")
st, _ = tick(eng, 30001.2)
check("Stop halts it", st["fn_kick_running"] == 0, st)
fires = [tick(eng, 30001.5 + t)[0]["fn_kick_fired"] for t in range(40)]
check("...and a stopped repeat does not fire", sum(fires) == 0, fires)

# A save of ANOTHER node rebuilds the engine: the Stop holds.
f2 = fx.Function({"id": "kick", "name": "Kick", "type": "inject",
                  "enabled": True, "params": dict(REPEAT)})
eng = fx.FunctionEngine([f2], state_dir=state)
st, _ = tick(eng, 30050.0)
check("a save of another node keeps it stopped", st["fn_kick_running"] == 0, st)

write_press(2, 30060.0, "start")
st, fired = tick(eng, 30060.3)
check("Start runs it, first inject immediately",
      st["fn_kick_running"] == 1 and st["fn_kick_fired"] == 1, st)
fires = [tick(eng, 30060.5 + t)[0]["fn_kick_fired"] for t in range(25)]
check("...then on the rhythm (every 10 s)", sum(fires) == 2, fires)
write_press(3, 30090.0, "press")
st, _ = tick(eng, 30090.3)
check("a plain press still injects once while it runs",
      st["fn_kick_fired"] == 1 and st["fn_kick_running"] == 1, st)

# The setting: off, a repeat waits for Start.
eng = engine({**REPEAT, "autostart": False})
fires = [tick(eng, 40000.0 + t)[0]["fn_kick_fired"] for t in range(25)]
check("'start with the app' off: it waits", sum(fires) == 0
      and tick(eng, 40030.0)[0]["fn_kick_running"] == 0, fires)
# ...and an app restart goes back to the setting (a Stop is not persisted).
fx._inject_state.clear()
eng2 = fx.FunctionEngine([fx.Function({"id": "kick", "name": "Kick",
                                       "type": "inject", "enabled": True,
                                       "params": dict(REPEAT)})],
                         state_dir=state)
check("after a restart it follows the setting again",
      tick(eng2, 40100.0)[0]["fn_kick_running"] == 1)

# A stale Start (left in the file across a restart) is not a Start now.
eng = engine({**REPEAT, "autostart": False})
write_press(9, 100.0, "start")
st, _ = tick(eng, 50000.0)
check("a stale Start is ignored", st["fn_kick_running"] == 0, st)
# ...and Start/Stop mean nothing to an Inject without a repeat.
eng = engine({"ptype": "number", "payload": "1"})
tick(eng, 60000.0)
write_press(10, 60000.5, "start")
st, _ = tick(eng, 60001.0)
check("an Inject without a repeat is never 'running'",
      st["fn_kick_running"] == 0 and st["fn_kick_fired"] == 0, st)

# The rules and the transport.
check("core keeps autostart only when off",
      functions_service.validate_function(
          {"id": "k", "type": "inject", "params": {"autostart": False}}
      )["params"].get("autostart") is False
      and "autostart" not in functions_service.validate_function(
          {"id": "k", "type": "inject", "params": {}})["params"])
_g, out = compile_([inject_node(repeat="interval", every=5, autostart=False)], [])
check("the compile writes autostart off",
      out["functions"][0]["params"].get("autostart") is False, out["functions"])
rep_deployed = [{"id": "kick", "type": "inject", "enabled": True,
                 "params": {"interval_s": 5}}]
for action, want in (("start", 200), ("stop", 200), ("press", 200),
                     ("reboot", 400)):
    try:
        wf.inject_target(saved, rep_deployed, "i1", True, action)
        code = 200
    except wf.InjectRefused as exc:
        code = exc.status
    check(f"action {action!r} on a repeating Inject -> {want}", code == want,
          code)
try:
    wf.inject_target(saved, deployed, "i1", True, "start")
    check("Start on an Inject without a repeat is refused", False)
except wf.InjectRefused as exc:
    check("Start on an Inject without a repeat is refused",
          exc.status == 400 and "repeat" in str(exc), str(exc))
real = (dc._pkg_dir, dc._data_dir_cached)
dc._pkg_dir = lambda sd, slug: "/nowhere/demo"
dc._data_dir_cached = lambda pkg: data_dir
try:
    dc.request_inject("/nowhere", "demo", "kick", "stop")
    with open(os.path.join(data_dir, dc.INJECT_FILE), encoding="utf-8") as f:
        written = json.load(f)
finally:
    dc._pkg_dir, dc._data_dir_cached = real
check("the press file carries the action", written["kick"]["action"] == "stop",
      written)
check("the route passes the action through",
      'action = str((body or {}).get("action") or "press")' in body_of
      or 'action = str((body or {}).get("action") or "press")' in read(
          "core", "routes", "workflow.py"))
_g, out = compile_([inject_node(repeat="interval", every=5)], [])
entry = next(c for c in out["catalog"] if c.get("kind") == "button")
check("the dashboard button knows it is a repeat", entry["inject"]["repeat"]
      is True, entry)
w_ = builder_service.validate([{**widget, "inject": entry["inject"]}])[0]
d_ = builder_service.compute([w_], [], {f"fn_{entry['inject']['fn']}_running": 1,
                                        f"fn_{entry['inject']['fn']}_runs": 1})[0]
check("...and draws whether it is running", d_["data"]["repeat"] is True
      and d_["data"]["running"] is True, d_["data"])


# -- 8. with AI detection OFF ------------------------------------------------------
# The one app with its AI switch off ([ai] on = false): no model, no camera,
# the rules a Detection Stream feeds sitting out -- and the Inject, and what
# is wired after it, still work. (The switch itself, toggled in a running
# app, is tests/smoke_aiswitch.py.)
import socket                                                 # noqa: E402
from app import run as apprun                                 # noqa: E402

LOGIC_DATA = tempfile.mkdtemp()
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
free_port = sock.getsockname()[1]
sock.close()
LOGIC_CFG = {
    "ai": {"on": False},
    "app": {"stream_port": free_port, "stream_bind": "127.0.0.1"},
    # A REAL camera and the accelerator, as a production box has them. With
    # the AI off the app must open neither.
    "cameras": [{"id": "cam1", "source": "rtsp://10.0.0.9/stream",
                 "enabled": True}],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
    "zones": [],
    "outputs": [{"id": "wf_m1", "type": "mqtt", "trigger": "alert",
                 "function": "kick", "enabled": True, "host": "127.0.0.1",
                 "port": 1, "topic": "t", "payload": "1"}],
    "functions": [
        {"id": "kick", "name": "Kick", "type": "inject", "enabled": True,
         "params": {"ptype": "number", "payload": "7", "once": True,
                    "once_delay_s": 0},
         "triggers": [{"metric": "fired", "op": ">", "value": 0,
                       "severity": "info", "cooldown_s": 0,
                       "message": "Kick"}]},
        {"id": "dbl", "name": "Double", "type": "expr", "enabled": True,
         "params": {"expr": "fn_kick_value * 2"}},
        # A picture rule: with no detections it would read 0 -- which an
        # "empty" alarm reads as an answer. With the AI off it sits out.
        {"id": "ppl", "name": "People", "type": "frame_count", "enabled": True,
         "params": {}}]}
saved_run = (apprun.config_mod.load, apprun.config_mod.data_dir,
             apprun.camera_mod.Camera.open, apprun.detector_mod.build)
opened, built = [], []
apprun.config_mod.load = lambda *a, **k: json.loads(json.dumps(LOGIC_CFG))
apprun.config_mod.data_dir = lambda *a, **k: LOGIC_DATA
apprun.camera_mod.Camera.open = lambda self, *a, **k: opened.append(self)
real_build = saved_run[3]
apprun.detector_mod.build = lambda c: (built.append((c or {}).get("kind")),
                                       real_build(c))[1]
try:
    rc = apprun.main(["--once"])
finally:
    (apprun.config_mod.load, apprun.config_mod.data_dir,
     apprun.camera_mod.Camera.open, apprun.detector_mod.build) = saved_run
with open(os.path.join(LOGIC_DATA, "state.json"), encoding="utf-8") as f:
    app_state = json.load(f)
counts = app_state.get("counts") or {}
check("AI off: the app runs one cycle", rc == 0, rc)
check("...opens no camera", opened == [], opened)
check("...builds no model (the chip is never touched)",
      built == ["none"], built)
check("...and says it is the unified app with the AI off",
      app_state.get("mode") == "unified" and app_state.get("ai_on") is False
      and app_state.get("ai_ok") is False, app_state)
check("the Inject fires with no frames at all",
      counts.get("fn_kick_fired") == 1 and counts.get("fn_kick_value") == 7,
      counts)
check("...what reads it downstream runs too", counts.get("fn_dbl_value") == 14,
      counts)
check("...and the output wired after it is fired",
      counts.get("out_wf_m1_fired") == 1, counts)
check("a picture rule does not run with the AI off",
      not any(k.startswith("fn_ppl_") for k in counts), sorted(counts))

route_src = read("core", "routes", "workflow.py")
check("a press with no app up brings the app up (AI off) and presses it",
      "app_supervisor.start_for_press(systems_dir, slug)" in route_src
      and '"logic_supported": dc.unified_supported(systems_dir, slug)'
      in route_src)
page = read("frontend", "src", "pages", "Workflow.jsx")
check("the canvas button does not need detection running",
      'return "The detection app is not running"' not in page
      and "meta.logic_supported === false" in page)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all inject checks passed")
