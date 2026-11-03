# The ON and OFF points of a Detection Stream and a Class Filter.
#
# Tok Aba, 2026-10-09: "right now detection stream just got option logic for
# on only, then i want to add logic when it off then what will happen" -- a
# Detection Stream's state is whether the AI runs, a Class Filter's whether
# it is switched on on the dashboard; a node wired to ON acts when the state
# is (or becomes) on, one wired to OFF when it is (or becomes) off, once at
# start-up and once per change. Then, same day: the state is a value the
# logic nodes can read, and a point may be wired into ANY node, which then
# runs only while that state matches (a node that needs detections is shown
# off with "needs AI detection" rather than refused).
# core/services/state_points.py says how; this pins:
#
#   1. old graphs compile key for key as before (a point's function exists
#      only once it is wired or a node names its 1/0 value; then it runs
#      with the AI off and is not in the dashboard palette)
#   2. ON/OFF into a Trigger, an MQTT Publish, a Board Output compile like an
#      Inject's wire, and none of them waits for detection or for a pause
#   3. any node after a point: a Formula and its card gated (runs_when), a
#      Class Filter fed only by OFF switched off with "needs AI detection",
#      no compile error
#   4. the engine: nothing until told the state, fire once at start for the
#      state found, once per change, not every tick, not again on a rebuild,
#      again when its wiring changes; Class Filter pause; a disabled node is
#      OFF
#   5. with the AI OFF the points and what is after OFF run (ai_config +
#      class_pause + point_gates), what is after ON is held; with it ON the
#      reverse; the same for a paused Class Filter
#   6. an app copy from before the points gets none (no "unknown type" log)
#   7. the canvas: the function id recipe matches, the node draws ON/OFF
#   8. EXACTLY two connections (Tok Aba, after v1.0.31): the old Detections
#      wires move onto ON (load + compile) and compile key for key as before;
#      ON into a detection reader is the detections wire, into anything else
#      (Debug included) the state; OFF keeps its handle through a Link
#
#   py -3 tests/smoke_statepoints.py
import io
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as functions_service      # noqa: E402
from core.services import state_points                         # noqa: E402
from core.services import workflow as wf                       # noqa: E402
from app import functions as fm                                # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def edge(src, tgt, handle=None, src_handle=None):
    e = {"id": f"e_{src}_{tgt}_{handle or ''}_{src_handle or ''}",
         "source": src, "target": tgt}
    if handle:
        e["targetHandle"] = handle
    if src_handle:
        e["sourceHandle"] = src_handle
    return e


def compile_(nodes, edges):
    store = {"version": 3, "workflows": [{"id": "w", "enabled": True,
                                          "graph": {"nodes": nodes,
                                                    "edges": edges}}]}
    return wf.compile_store(store, [], None, None, None, {}, None)


def fns(out, kind=None):
    return [f for f in out["functions"]
            if kind is None or f["type"] == kind]


BASE = [node("src", "source.detection"),
        node("flt", "filter.class", classes=["person"], name="people"),
        node("cnt", "analytics.object"),
        node("seen", "analytics.formula", name="Seen"),
        node("bz", "output.buzzer")]
BASE_E = [edge("src", "flt"), edge("flt", "cnt"), edge("cnt", "seen", "a"),
          edge("seen", "bz")]

# -- 1. old graphs ------------------------------------------------------------
out = compile_(json.loads(json.dumps(BASE)), json.loads(json.dumps(BASE_E)))
real_split = state_points.split
state_points.split = lambda g, *_a: g            # the compiler before this
before = compile_(json.loads(json.dumps(BASE)), json.loads(json.dumps(BASE_E)))
state_points.split = real_split
check("an old graph compiles clean", not out["errors"], out["errors"])
check("...to the same rules, outputs and cards as before",
      fns(out, "object_detect") == fns(before, "object_detect")
      and [f for f in out["functions"] if f["type"] != "state_watch"]
      == before["functions"]
      and out["outputs"] == before["outputs"] and out["pages"] == before["pages"],
      (out["functions"], before["functions"]))
check("...the same waits and Class Filter plan",
      out["waits_for_detection"] == before["waits_for_detection"]
      and out["class_filters"] == before["class_filters"],
      (out["waits_for_detection"], out["class_filters"]))
check("...and nothing of the points: no function, no gate, no label",
      out["functions"] == before["functions"] and not fns(out, "state_watch")
      and out["state_gates"] == [] and out["runs_when"] == {},
      (out["functions"], out["state_gates"]))

# A logic node that NAMES a point's value (picked in a value picker) mints it.
nodes = json.loads(json.dumps(BASE)) + [
    node("gate", "analytics.formula", name="AI up", expr="fn_st_src_on_value",
         mode="expr")]
out = compile_(nodes, json.loads(json.dumps(BASE_E)))
check("a node naming fn_st_src_on_value mints that point, nothing else",
      [f["id"] for f in fns(out, "state_watch")] == ["st_src_on"]
      and not out["errors"], (fns(out, "state_watch"), out["errors"]))
check("...named for the pickers, running with the AI off",
      fns(out, "state_watch")[0]["name"] == "Detection Stream is ON"
      and "st_src_on" in out["frames_free"]["functions"], out["frames_free"])
check("...and not offered in the dashboard palette",
      not any("st_" in str(c.get("key")) for c in out["catalog"]),
      out["catalog"])

# -- 2. ON / OFF into what an Inject feeds ------------------------------------
nodes = json.loads(json.dumps(BASE)) + [
    node("t2", "output.trigger", name="AI back"), node("b2", "output.buzzer"),
    node("pub", "output.mqtt_publish", topic="ai/state"),
    node("do", "output.io_out", port="DO0")]
edges = json.loads(json.dumps(BASE_E)) + [
    edge("src", "t2", src_handle="on"), edge("t2", "b2"),
    edge("src", "pub", src_handle="off"),
    edge("flt", "do", src_handle="off")]
out = compile_(nodes, edges)
check("ON into a Trigger, OFF into an MQTT Publish and a Board Output compile",
      not out["errors"], out["errors"])
by_out = {o["id"]: o for o in out["outputs"]}
check("...the Trigger's buzzer fires on the ON point",
      by_out["wf_b2"].get("function") == "st_src_on", by_out.get("wf_b2"))
check("...the Publish sends the OFF value on each OFF fire",
      by_out["wf_pub"].get("mode") == "fire"
      and by_out["wf_pub"]["key"] == "fn_st_src_off_value"
      and by_out["wf_pub"]["fired"] == "fn_st_src_off_fired", by_out["wf_pub"])
check("...the Board Output fires on the Class Filter's OFF point",
      by_out["wf_do"].get("function") == "st_flt_off", by_out["wf_do"])
check("...the OFF functions exist because they are wired",
      {"st_src_off", "st_flt_off"} <= {f["id"] for f in fns(out, "state_watch")},
      fns(out, "state_watch"))
check("...none of them waits for detection",
      not {"t2", "b2", "pub", "do"} & set(out["waits_for_detection"])
      and {"wf_b2", "wf_pub", "wf_do"} <= set(out["frames_free"]["outputs"]),
      (out["waits_for_detection"], out["frames_free"]))
held = next(c for c in out["class_filters"] if c["node"] == "flt")
check("...and pausing the filter does not hold its own OFF consumer back",
      "wf_do" not in held["outputs"] and "st_flt_off" not in held["functions"],
      held)
check("...while the detection rule behind it still waits",
      "cnt" in out["waits_for_detection"] and "wf_bz" in held["outputs"],
      (out["waits_for_detection"], held))

# -- 3. any node after a point -----------------------------------------------
nodes = json.loads(json.dumps(BASE)) + [
    node("f2", "filter.class", classes=["car"], name="cars"),
    node("fx", "analytics.formula", name="Twice"),
    node("card", "dashboard.card"),
    node("b3", "output.buzzer")]
edges = json.loads(json.dumps(BASE_E)) + [
    edge("src", "f2", src_handle="off"),
    edge("src", "fx", handle="a", src_handle="off"), edge("fx", "card"),
    edge("flt", "b3", src_handle="on")]
out = compile_(nodes, edges)
check("a point into a Class Filter, a Formula and a card compiles clean",
      not out["errors"], out["errors"])
warn = {w["node"]: w["message"] for w in out["warnings"]}
check("...the Class Filter fed only by OFF is off: 'needs AI detection'",
      "needs AI detection" in warn.get("f2", "")
      and "switched off" in warn.get("f2", ""), warn)
gates = {(g["gate"]["watch"], g["gate"]["want"]): g for g in out["state_gates"]}
fx_id = next(f["id"] for f in out["functions"] if f["name"] == "Twice")
check("...the Formula after the stream's OFF runs only while the AI is off",
      fx_id in gates[("ai", "off")]["functions"], out["state_gates"])
check("...the buzzer after the filter's ON only while the filter runs",
      gates[("class_filter", "on")]["outputs"] == ["wf_b3"]
      and gates[("class_filter", "on")]["gate"]["filter"] == "flt",
      out["state_gates"])
check("...the canvas says so on each node after a point",
      out["runs_when"].get("fx") == "AI detection is off"
      and out["runs_when"].get("card") == "AI detection is off"
      and out["runs_when"].get("b3") == "people is on", out["runs_when"])
check("...and the Formula reads the point's 1/0",
      "fn_st_src_off_value" in json.dumps(
          next(f for f in out["functions"] if f["id"] == fx_id)), fx_id)

# -- 4. the engine -------------------------------------------------------------
docs = [functions_service.validate_function(
    {"id": f"st_{nid}_{want}", "type": "state_watch",
     "params": {"watch": watch, "want": want, "filter": nid,
                **({"fixed_off": True} if nid == "dis" else {})}})
    for nid, watch, want in (("src", "ai", "on"), ("src", "ai", "off"),
                             ("flt", "class_filter", "on"),
                             ("dis", "class_filter", "on"))]
parsed = fm.parse_functions(docs)
check("the app loads every point the compiler writes",
      len(parsed) == len(docs), [f.id for f in parsed])


def tick(engine):
    engine.update({}, [], {}, {}, 640, 480, zones=[])
    return engine.stats()


eng = fm.FunctionEngine(parsed)
s = tick(eng)
check("nothing is published before the app says what the states are",
      not any(k.startswith("fn_st_") and k.endswith(("_value", "_fired"))
              for k in s), s)
fm.set_states(ai=False, paused=())
s = tick(eng)
check("AI off at start: OFF fires once, ON does not",
      s["fn_st_src_off_fired"] == 1 and s["fn_st_src_off_value"] == 1
      and s["fn_st_src_on_fired"] == 0 and s["fn_st_src_on_value"] == 0, s)
check("...a running filter's ON fires at start, a disabled one reads OFF",
      s["fn_st_flt_on_fired"] == 1 and s["fn_st_dis_on_value"] == 0, s)
s = tick(eng)
check("...and not again on the next tick",
      s["fn_st_src_off_fired"] == 0 and s["fn_st_flt_on_fired"] == 0, s)
eng = fm.FunctionEngine(parsed)                  # a workflow save
s = tick(eng)
check("a rebuilt engine (a save) does not fire again",
      s["fn_st_src_off_fired"] == 0 and s["fn_st_src_off_value"] == 1, s)
fm.set_states(ai=True, paused=())
s = tick(eng)
check("AI switched on: ON fires once, OFF reads 0",
      s["fn_st_src_on_fired"] == 1 and s["fn_st_src_off_value"] == 0
      and s["fn_st_src_off_fired"] == 0, s)
fm.set_states(ai=True, paused=("flt",))
s = tick(eng)
check("the filter paused on the dashboard: its ON reads 0",
      s["fn_st_flt_on_value"] == 0 and s["fn_st_flt_on_fired"] == 0, s)
fm.set_states(ai=False, paused=())
s = tick(eng)
check("AI off again: OFF fires again", s["fn_st_src_off_fired"] == 1, s)
rewired = [dict(d, params=dict(d["params"], feeds=["new"]))
           if d["id"] == "st_src_off" else d for d in docs]
eng = fm.FunctionEngine(fm.parse_functions(rewired))
s = tick(eng)
check("a new wire into a point fires it once for the state it finds",
      s["fn_st_src_off_fired"] == 1, s)
fm.set_states(ai=True, paused=())

# -- 5. the app's switches ---------------------------------------------------
nodes = json.loads(json.dumps(BASE)) + [
    node("fx", "analytics.formula", name="Twice"),
    node("pub", "output.mqtt_publish", topic="ai/off"),
    node("b4", "output.buzzer"), node("b5", "output.buzzer")]
edges = json.loads(json.dumps(BASE_E)) + [
    edge("src", "fx", handle="a", src_handle="off"),
    edge("src", "pub", src_handle="off"),
    edge("src", "b4", src_handle="on"),
    edge("flt", "b5", src_handle="off")]
out = compile_(nodes, edges)
fx_id = next(f["id"] for f in out["functions"] if f["name"] == "Twice")
gate_tbl = {"gates": [{**g["gate"], "functions": g["functions"],
                       "outputs": g["outputs"]} for g in out["state_gates"]]}
cfg = {"functions": out["functions"], "outputs": out["outputs"],
       "logic": {k: out["frames_free"][k] for k in ("functions", "outputs")},
       "state_points": gate_tbl,
       "class_filters": {"paused": [], "nodes": out["class_filters"]}}


def applied(ai, paused=()):
    c = dict(cfg, ai={"on": ai},
             class_filters=dict(cfg["class_filters"], paused=list(paused)))
    c = apprun.point_gates(apprun.class_pause(apprun.ai_config(c)))
    return set(apprun.inactive_ids(c)), {o["id"] for o in c["outputs"]}


off_in, off_out = applied(False)
check("AI OFF: the points and the stream's OFF branch run",
      not {"st_src_on", "st_src_off", "st_flt_on", "st_flt_off", fx_id}
      & off_in and "wf_pub" in off_out, (off_in, off_out))
check("...the running filter's OFF branch is held",
      "wf_b5" not in off_out, off_out)
check("...the ON branch is held and the detection rule sits out",
      "wf_b4" not in off_out and "object_detection_cnt" in off_in
      and "wf_bz" not in off_out, (off_in, off_out))
on_in, on_out = applied(True)
check("AI ON: the OFF branch is held, the ON branch runs",
      fx_id in on_in and "wf_pub" not in on_out and "wf_b4" in on_out
      and "st_src_off" not in on_in, (on_in, on_out))
p_in, p_out = applied(True, paused=("flt",))
check("the filter paused: its OFF branch runs, the points keep running",
      "wf_b5" in p_out and "st_flt_off" not in p_in and "wf_bz" not in p_out,
      (p_in, p_out))
check("...and runs only while it is paused", "wf_b5" not in on_out, on_out)
legacy = apprun.ai_config({"ai": {"on": False}, "functions": out["functions"],
                           "outputs": []})
check("a config with no [logic] list still runs the points with the AI off",
      "st_src_off" not in legacy["_ai_inactive"], legacy["_ai_inactive"])
check("run.py tells the engine the states at start and on every config apply",
      read("systems", "demo", "app", "run.py").count(
          "functions_mod.set_states(") == 2)

# -- 6. an app copy from before the points -----------------------------------
sysdir = tempfile.mkdtemp()
os.makedirs(os.path.join(sysdir, "old", "app"))
with open(os.path.join(sysdir, "old", "app", "functions.py"), "w") as f:
    f.write('TYPES = ("expr", "inject")\n')
os.makedirs(os.path.join(sysdir, "new", "app"))
with open(os.path.join(sysdir, "new", "app", "functions.py"), "w") as f:
    f.write('TYPES = ("inject", "state_watch")\n')
check("an old app copy gets no ON/OFF point, an updated one does",
      not wf._app_reads_state_points(sysdir, "old")
      and wf._app_reads_state_points(sysdir, "new")
      and wf._app_reads_state_points(sysdir, "none"))

# -- 7. the canvas -----------------------------------------------------------


def js_fn(node_id, want):
    """frontend/src/workflow/registry.js statePointFn, step for step."""
    s_ = re.sub(r"[^a-z0-9]", "_", str(node_id).strip().lower())
    s_ = re.sub(r"^_+|_+$", "", s_)[:40]
    return f"st_{s_[:30]}_{want}"


ids = ["src", "nkx3abc12", "ex_src", "Cam-2.stream", "x" * 60, "call__n12"]
check("the canvas works out the same function id as the compiler",
      all(js_fn(i, w) == state_points.fn_id(i, w)
          for i in ids for w in ("on", "off")),
      [(i, js_fn(i, "on"), state_points.fn_id(i, "on")) for i in ids])
reg = read("frontend", "src", "workflow", "registry.js")
check("...with the recipe registry.js actually uses",
      'replace(/[^a-z0-9]/g, "_")' in reg and "st_${s.slice(0, 30)}_${want}" in reg)
nodes_jsx = read("frontend", "src", "workflow", "nodes.jsx")
page = read("frontend", "src", "pages", "Workflow.jsx")
check("the Detection Stream and Class Filter draw ON and OFF only, "
      "headed Connection",
      "Detections" not in nodes_jsx
      and nodes_jsx.count("statePorts(") >= 3
      and nodes_jsx.count('sourceHeading: "Connection"') == 1
      and nodes_jsx.count('sourceHeading="Connection"') == 1
      and "Runs when {runsWhen[id]}" in nodes_jsx)
check("...and the page moves an old Detections wire onto ON as it loads",
      "export function migrateStateEdges(nodes, edges)" in reg
      and "setEdges(migrateStateEdges(d.graph?.nodes," in page)
check("...and the value pickers offer both points' 1/0 before they are wired",
      "export function statePointKeys(nodes)" in reg
      and "(1 = yes, 0 = no)" in reg)
check("...wired into any node, lit by the live poll",
      "isStatePoint(src.type, conn.sourceHandle)" in page
      and "live.filters_paused" in page and "live.runs_when" in page
      and "const stateLabels = statePointKeys(nodes);" in page)
check("no em dash in the new module", chr(0x2014) not in read(
    "core", "services", "state_points.py"))

# -- 8. exactly two connections ---------------------------------------------
old_nodes = json.loads(json.dumps(BASE)) + [
    node("pre", "filter.preprocess"), node("p2", "filter.class",
                                            classes=["car"], name="cars")]
old_edges = json.loads(json.dumps(BASE_E)) + [
    edge("src", "pre"), edge("pre", "p2"), edge("p2", "cnt")]
state_points.split = lambda g, *_a: g            # the compiler before points
before = compile_(json.loads(json.dumps(old_nodes)),
                  json.loads(json.dumps(old_edges)))
state_points.split = real_split
g = {"nodes": json.loads(json.dumps(old_nodes)),
     "edges": json.loads(json.dumps(old_edges))}
# ...and one wire already on ON beside its old twin (drawn twice).
g["edges"].append(edge("src", "flt", src_handle="on"))
moved = state_points.migrate(g)
hd = {(e["source"], e["target"]): e.get("sourceHandle") for e in g["edges"]}
check("load: every old Detections wire of a stream or filter moves onto ON",
      hd[("src", "flt")] == "on" and hd[("flt", "cnt")] == "on"
      and hd[("src", "pre")] == "on" and hd[("p2", "cnt")] == "on", hd)
check("...a Pre-process keeps its own unnamed output",
      hd[("pre", "p2")] is None, hd)
check("...and a twin already on ON is not drawn twice",
      [(e["source"], e["target"]) for e in g["edges"]].count(("src", "flt"))
      == 1 and moved == 4, (moved, g["edges"]))
after = compile_(g["nodes"], g["edges"])
same = ("functions", "outputs", "pages", "errors", "warnings",
        "waits_for_detection", "class_filters", "frames_free", "catalog")
check("a migrated graph compiles key for key as before",
      all(after[k] == before[k] for k in same),
      [k for k in same if after[k] != before[k]])
check("...no point minted, nothing gated or labelled",
      not fns(after, "state_watch") and after["state_gates"] == []
      and after["runs_when"] == {}, after["state_gates"])
unmigrated = compile_(json.loads(json.dumps(old_nodes)),
                      json.loads(json.dumps(old_edges)))
check("...and so does the same graph reaching the compiler unmigrated",
      all(unmigrated[k] == before[k] for k in same))

nodes = json.loads(json.dumps(BASE)) + [
    node("dbg", "common.debug"),
    node("tg", "output.mqtt_publish", topic="ai/on"),
    node("lo", "common.link_out", link="j"),
    node("li", "common.link_in", link="j"),
    node("pub2", "output.mqtt_publish", topic="ai/off2")]
edges = [edge("src", "flt", src_handle="on"),
         edge("flt", "cnt", src_handle="on"),
         edge("cnt", "seen", "a"), edge("seen", "bz"),
         edge("src", "dbg", src_handle="on"),
         edge("src", "tg", src_handle="on"),
         edge("src", "lo", src_handle="off"), edge("li", "pub2")]
out = compile_(nodes, edges)
check("ON into a Class Filter and a rule is the detections wire",
      not out["errors"]
      and {"src", "flt", "cnt"} <= set(out["waits_for_detection"])
      and "object_detection_cnt" not in json.dumps(out["state_gates"])
      and "cnt" not in out["runs_when"] and "flt" not in out["runs_when"],
      (out["errors"], out["waits_for_detection"], out["state_gates"]))
by_out = {o["id"]: o for o in out["outputs"]}
check("ON into an MQTT Publish is the state, gated to while ON",
      by_out["wf_tg"].get("fired") == "fn_st_src_on_fired"
      and out["runs_when"].get("tg") == "AI detection is on", by_out["wf_tg"])
check("ON into a Debug mints the point (the probe shows the 1/0)",
      "st_src_on" in {f["id"] for f in fns(out, "state_watch")})
check("OFF keeps its handle through a Link: the state, not detections",
      by_out["wf_pub2"].get("fired") == "fn_st_src_off_fired"
      and "pub2" not in out["waits_for_detection"], by_out["wf_pub2"])

# -- 9. one point into two If / Else if inputs (asri_test_4 on .49) ----------
# The Class Filter's OFF wired into input1 AND input2, "if input1 == 1" ->
# light, "elif input2 == 1" -> motor: the elif can never run, and the canvas
# says so (a warning on the If / Else if; the graph still compiles).
CASES = [node("ie", "branch.cases", name="stop", otherwise=False,
              rules=[{"op": "==", "value": 1, "input": "in1"},
                     {"op": "==", "value": 1, "input": "in2"}]),
         node("tl", "output.trigger", name="light_stop2",
              payload_type="number", payload="96"),
         node("tm", "output.trigger", name="motor_stop2",
              payload_type="number", payload="0"),
         node("pl", "output.mqtt_publish", topic="light"),
         node("pm", "output.mqtt_publish", topic="motor")]
CASES_E = [edge("ie", "tl", src_handle="out0"),
           edge("ie", "tm", src_handle="out1"),
           edge("tl", "pl"), edge("tm", "pm")]


def ie_warning(out):
    return [w["message"] for w in out["warnings"] if w["node"] == "ie"]


out = compile_(json.loads(json.dumps(BASE + CASES)),
               json.loads(json.dumps(BASE_E + CASES_E)) + [
                   edge("flt", "ie", "in1", src_handle="off"),
                   edge("flt", "ie", "in2", src_handle="off")])
got = ie_warning(out)
check("one OFF point into input1 and input2: still compiles",
      not out["errors"], out["errors"])
check("...the If / Else if warns that branch 2 never runs",
      len(got) == 1 and got[0].startswith("branch 2 never runs")
      and "branch 1" in got[0], got)
check("...and says: a separate If / Else if per action, not two Triggers",
      got and "separate If / Else if per action" in got[0]
      and "two Triggers straight on the point" in got[0]
      and "switched off" not in got[0] and chr(0x2014) not in got[0], got)
by_fn = {f["id"]: f["params"].get("expr") for f in out["functions"]}
check("...(the compiled elif really is 'X and not X')",
      by_fn.get("stop_b1") == "(fn_st_flt_off_value == 1) and not "
                              "(fn_st_flt_off_value == 1)", by_fn)

out = compile_(json.loads(json.dumps(BASE + CASES)),
               json.loads(json.dumps(BASE_E + CASES_E)) + [
                   edge("flt", "ie", "in1", src_handle="off"),
                   edge("src", "ie", "in2", src_handle="off")])
check("two DIFFERENT points into the two inputs: no warning",
      not out["errors"] and not ie_warning(out), ie_warning(out))

twin = json.loads(json.dumps(BASE + CASES))
twin[len(BASE)]["data"]["rules"] = [{"op": "==", "value": 1, "input": "in1"},
                                    {"op": ">", "value": 0, "input": "in2"}]
out = compile_(twin, json.loads(json.dumps(BASE_E + CASES_E)) + [
    edge("flt", "ie", "in1", src_handle="off"),
    edge("flt", "ie", "in2", src_handle="off")])
got = ie_warning(out)
check("same point, different tests: warns the inputs read the same value",
      len(got) == 1 and got[0].startswith("input1 and input2 are wired from "
                                          "the same value"), got)

# Why not two Triggers straight on the point: one moment, one payload. The
# second Trigger is warned and its output would send the bare 1, not its 0.
two = json.loads(json.dumps(BASE + CASES[1:]))
two[-1] = node("pm", "output.modbus_write", server="mb_1", fc=6, address=0)
out = compile_(two, json.loads(json.dumps(BASE_E)) + [
    edge("flt", "tl", src_handle="off"), edge("flt", "tm", src_handle="off"),
    edge("tl", "pl"), edge("tm", "pm")])
by_out = {o["id"]: o for o in out["outputs"]}
check("two Triggers straight on one point: refused as 'the same moment'",
      any(w["node"] == "tm" and "same moment" in w["message"]
          for w in out["errors"] + out["warnings"]),
      (out["errors"], out["warnings"]))
check("...and the motor's write would carry the bare point value, not 0",
      by_out["wf_pm"]["key"] == "fn_st_flt_off_value", by_out.get("wf_pm"))

# The recommended shape: one If / Else if per action off the same point.
one = lambda nid: node(nid, "branch.cases", name=nid, otherwise=False,  # noqa: E731
                       rules=[{"op": "==", "value": 1, "input": "in1"}])
nodes = json.loads(json.dumps(BASE + CASES[1:])) + [one("ia"), one("ib")]
nodes[-3] = node("pm", "output.modbus_write", server="mb_1", fc=6, address=0)
out = compile_(nodes, json.loads(json.dumps(BASE_E)) + [
    edge("flt", "ia", "in1", src_handle="off"),
    edge("flt", "ib", "in1", src_handle="off"),
    edge("ia", "tl", src_handle="out0"), edge("ib", "tm", src_handle="out0"),
    edge("tl", "pl"), edge("tm", "pm")])
by_out = {o["id"]: o for o in out["outputs"]}
by_fn = {f["id"]: f for f in out["functions"]}
check("one If / Else if per action off one point: clean, no warning",
      not out["errors"] and not [w for w in out["warnings"]
                                 if w["node"] in ("ia", "ib", "tl", "tm")],
      (out["errors"], out["warnings"]))
check("...each action carries its own payload (96 to the light, 0 to the motor)",
      by_out["wf_pl"]["key"] == "fn_ia_b0_out"
      and by_out["wf_pm"]["key"] == "fn_ib_b0_out"
      and by_fn["ia_b0"]["trigger"]["out"]["value"] == "96"
      and by_fn["ib_b0"]["trigger"]["out"]["value"] == "0",
      (by_out.get("wf_pl"), by_out.get("wf_pm")))
check("...and the Modbus Write writes again at each fire (beat), as MQTT does",
      by_out["wf_pm"].get("mode") == "change"
      and by_out["wf_pm"].get("beat") == "ib_b0"
      and by_out["wf_pl"].get("beat") == "ia_b0", by_out.get("wf_pm"))

print("\n" + ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
