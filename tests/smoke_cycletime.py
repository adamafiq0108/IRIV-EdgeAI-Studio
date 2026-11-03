# Cycle Time measures TIME, and an Alert may hand its result to a Formula.
#
# Two changes, one idea: a node should answer the question it is named for,
# and nothing else.
#
# Cycle Time published `current` -- how many objects are inside right now --
# and published it FIRST, which made it the metric an Alert bound to by
# default. So "alert when somebody has been in the zone a while" silently
# became "alert when two people are in the zone", firing the instant the
# second walked in. Counting a zone is what the Zone Counter and Object in
# Zone are for. `current` is gone from the node's outputs, and a graph still
# pointing at it is REFUSED rather than quietly re-aimed: the comparison the
# operator typed was about a head count, and repointing it at a clock keeps
# the number and changes the subject.
#
# The other half: an Alert may now feed a Formula, so the logic after an alarm
# lives where logic is written instead of being rebuilt as a second condition.
# The alert arrives as its rule's `active` -- 1 while the condition holds.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402
from core.services import functions as fsvc                    # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def compile_(nodes, edges):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                            ZONES)


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, **kw):
    return {"id": f"e_{src}_{tgt}_{kw.get('targetHandle', '')}",
            "source": src, "target": tgt, **kw}


print("== Cycle Time publishes times, and nothing else")
check("the engine contract drops the head count",
      "current" not in fsvc.OUTPUTS["zone_dwell"],
      str(fsvc.OUTPUTS["zone_dwell"]))
check("live_s is first, so an Alert defaults to a clock",
      fsvc.OUTPUTS["zone_dwell"][0] == "live_s",
      str(fsvc.OUTPUTS["zone_dwell"]))
check("every one of them is a time",
      all(m.endswith("_s") for m in fsvc.OUTPUTS["zone_dwell"]),
      str(fsvc.OUTPUTS["zone_dwell"]))
spec = wf.REGISTRY["analytics.dwell"]
check("the node offers no count either", "current" not in spec["metrics"],
      str(spec["metrics"]))
for name, mode in spec["modes"].items():
    check("mode %r offers no count" % name,
          "current" not in (mode.get("metrics") or []),
          str(mode.get("metrics")))
# The modes that measure the PLACE keep their own vocabulary untouched.
check("Occupancy Timer still reports whether it is occupied",
      "active" in spec["modes"]["occupancy"]["metrics"])

print("== a graph still watching `current` is refused, not re-aimed")
BASE = [node("s", "source.detection"),
        node("z", "zone.ref", zone="zone_a"),
        node("cy", "analytics.dwell", fnId="cyc", name="Cycle time",
             mode="dwell")]
EDGES = [wire("s", "cy", targetHandle="in"), wire("z", "cy", targetHandle="zone")]

stale = compile_(BASE + [node("al", "output.alert", metric="current", op=">",
                              value=1, message="two in the bay")],
                 EDGES + [wire("cy", "al", targetHandle="in")])
msgs = " ".join(e["message"] for e in stale["errors"])
check("it errors", bool(stale["errors"]), str(stale["warnings"]))
check("and names the nodes that DO count",
      "Zone Counter" in msgs and "Object in Zone" in msgs, msgs)
# The MEASUREMENT is not the problem and still compiles -- it is the condition
# that has to be re-answered. It compiles with no trigger, and the error stops
# apply_artifacts, so nothing reaches the box until the operator has chosen.
cyc = next((f for f in stale["functions"] if f["id"] == "cyc"), None)
check("the measurement itself still compiles", cyc is not None)
check("but carries no trigger",
      cyc is not None and not cyc.get("trigger") and not cyc.get("triggers"),
      str(cyc))

# ...and the loader leaves it alone, so the error survives to be seen. Left to
# migrate_condition_metrics it would come back as live_s > 1 -- "anybody
# inside for one second" -- from a node nobody touched.
g = {"nodes": BASE + [node("al", "output.alert", metric="current", op=">",
                           value=1)],
     "edges": EDGES + [wire("cy", "al", targetHandle="in")]}
wf.migrate_condition_metrics(g)
al = next(n for n in g["nodes"] if n["id"] == "al")
check("the loader does not silently repoint it",
      al["data"]["metric"] == "current", str(al["data"]))

# A stale metric on any OTHER rule is still repaired on load, as before.
g2 = {"nodes": [node("s", "source.detection"), node("z", "zone.ref", zone="zone_a"),
                node("zc", "analytics.violations", fnId="zc", name="Counter",
                     mode="realtime"),
                node("a2", "output.alert", metric="nonsense", op=">", value=1)],
      "edges": [wire("s", "zc", targetHandle="in"), wire("z", "zc", targetHandle="zone"),
                wire("zc", "a2", targetHandle="in")]}
check("an ordinary stale metric is still repaired",
      wf.migrate_condition_metrics(g2)
      and next(n for n in g2["nodes"] if n["id"] == "a2")["data"]["metric"] == "count")

print("== and an alert that never chose a metric is written down first")
# The nastiest case: no metric chosen means "the rule's first one", and the
# first one moved from a head count to a clock. Left alone, "> 1" would keep
# its number and change its subject -- two objects inside becomes one second
# inside -- on a node nobody touched.
old = {"version": 1,
       "nodes": BASE + [node("al", "output.alert", op=">", value=1)],
       "edges": EDGES + [wire("cy", "al", targetHandle="in")]}
check("the old default is stamped on", wf.migrate_dwell_count_alerts(old))
al = next(n for n in old["nodes"] if n["id"] == "al")
check("as the metric that no longer exists, so it is refused",
      al["data"]["metric"] == "current", str(al["data"]))
check("and the graph is marked done -- its own step", old["version"] == 2)
check("running it again changes nothing",
      wf.migrate_dwell_count_alerts(old) is False)
# An alert minted TODAY against a Cycle Time means the NEW default honestly,
# which is why this is gated on the schema version rather than on the shape.
fresh = {"version": 2,
         "nodes": BASE + [node("al", "output.alert", op=">", value=10)],
         "edges": EDGES + [wire("cy", "al", targetHandle="in")]}
check("a graph already on this version is left alone",
      wf.migrate_dwell_count_alerts(fresh) is False
      and "metric" not in fresh["nodes"][-1]["data"], str(fresh["nodes"][-1]))
# Only Cycle Time moved. An unset metric anywhere else still means what it did.
other = {"version": 1,
         "nodes": [node("s", "source.detection"), node("z", "zone.ref", zone="zone_a"),
                   node("zc", "analytics.violations", fnId="zc", name="C",
                        mode="realtime"),
                   node("a2", "output.alert", op=">", value=1)],
         "edges": [wire("s", "zc", targetHandle="in"),
                   wire("z", "zc", targetHandle="zone"),
                   wire("zc", "a2", targetHandle="in")]}
wf.migrate_dwell_count_alerts(other)
check("a Zone Counter's alert is untouched",
      "metric" not in other["nodes"][-1]["data"], str(other["nodes"][-1]))

print("== a clock compiles, which is what the node is for")
good = compile_(BASE + [node("al", "output.alert", metric="live_s", op=">",
                             value=10, message="loitering")],
                EDGES + [wire("cy", "al", targetHandle="in")])
check("no errors", not good["errors"], str(good["errors"]))
fn = next((f for f in good["functions"] if f["id"] == "cyc"), None)
check("the rule is a dwell", fn and fn["type"] == "zone_dwell", str(fn))
check("and its trigger watches the live clock",
      fn and fn["triggers"][0]["metric"] == "live_s", str(fn and fn.get("triggers")))
check("with the operator's own threshold",
      fn and fn["triggers"][0]["value"] == 10, str(fn and fn.get("triggers")))

print("== an Alert may feed a Formula")
ok, why = wf.edge_allowed("output.alert", "analytics.formula")
check("Alert -> Formula is allowed", ok, why)
ok, _ = wf.edge_allowed("output.alert", "analytics.expr")
check("Alert -> Custom Formula is allowed", ok)
ok, _ = wf.edge_allowed("output.alert", "analytics.total")
check("Alert -> Running Total is allowed", ok)
ok, why = wf.edge_allowed("output.snapshot", "analytics.formula")
check("but a Snapshot is not -- it publishes no value", not ok, why)
check("and the refusal says why", "only a Condition can feed a Formula" in why, why)
# The other direction was always legal and stays legal: write the condition in
# a Formula, fire the Alert on it.
ok, why = wf.edge_allowed("analytics.formula", "output.alert")
check("Formula -> Alert still works", ok, why)

print("== and it arrives as the rule's alert state")
scene = compile_(
    BASE + [node("al", "output.alert", metric="live_s", op=">", value=10),
            node("fx", "analytics.formula", fnId="fx", name="Formula",
                 op="add", inputs=2, b_value=0)],
    EDGES + [wire("cy", "al", targetHandle="in"),
             wire("al", "fx", targetHandle="a")])
check("the scene compiles", not scene["errors"], str(scene["errors"]))
fx = next((f for f in scene["functions"] if f["id"] == "fx"), None)
check("the Formula reads fn_<rule>_active -- 1 while the alarm holds",
      fx and "fn_cyc_active" in fx["params"]["expr"],
      str(fx and fx["params"]))

print("== a Formula needs no Alert after it -- its answer IS the decision")
# An AND of two alarms comes out 0 or 1. "Fire while it is not zero" is not a
# threshold somebody chose, it is what true and false mean -- and the operator
# reached the Formula by wiring alarms INTO it, so when was settled two nodes
# ago. Demanding a third Alert asked them to write down a number the graph had
# already decided.
scene = [node("s", "source.detection"), node("z", "zone.ref", zone="zone_a"),
         node("cy", "analytics.dwell", fnId="cyc", name="Cycle", mode="dwell"),
         node("zc", "analytics.violations", fnId="zcnt", name="Counter",
              mode="realtime"),
         node("a1", "output.alert", metric="live_s", op=">", value=10),
         node("a2", "output.alert", metric="count", op=">", value=1),
         node("fx", "analytics.formula", fnId="fx", name="Formula",
              op="mul", inputs=2),
         node("sn", "output.snapshot", mode="once", interval_s=5)]
links = [wire("s", "cy", targetHandle="in"), wire("s", "zc", targetHandle="in"),
         wire("z", "cy", targetHandle="zone"), wire("z", "zc", targetHandle="zone"),
         wire("cy", "a1", targetHandle="in"), wire("zc", "a2", targetHandle="in"),
         wire("a1", "fx", targetHandle="a"), wire("a2", "fx", targetHandle="b"),
         wire("fx", "sn", targetHandle="in")]
direct = compile_(scene, links)
check("Formula -> Snapshot compiles with nothing in between",
      not direct["errors"], str(direct["errors"]))
fx = next((f for f in direct["functions"] if f["id"] == "fx"), None)
check("the AND is what it computes",
      fx and fx["params"]["expr"] == "(fn_cyc_active * fn_zcnt_active)",
      str(fx and fx["params"]))
trig = (fx or {}).get("triggers") or []
check("armed on its own answer being true",
      len(trig) == 1 and trig[0]["metric"] == "value"
      and trig[0]["op"] == ">" and trig[0]["value"] == 0.0, str(trig))
check("with no cooldown -- one event per rise, never while it holds",
      trig and trig[0]["cooldown_s"] == 0, str(trig))
check("and it is not an alarm in its own right",
      trig and trig[0]["severity"] == "info", str(trig))
snap = next((o for o in direct["outputs"] if o["type"] == "snapshot"), None)
check("the photo fires on the Formula -- the combined answer",
      snap and snap.get("function") == "fx" and snap.get("trigger") == "alert",
      str(snap))
check("and keeps its own pacing", snap and snap.get("mode") == "once", str(snap))

# An Alert the operator DID place still wins: nothing is invented over a
# choice already made.
chosen = compile_(
    scene + [node("a3", "output.alert", metric="value", op=">", value=0.5,
                  cooldown_s=60)],
    [w for w in links if w["target"] != "sn"]
    + [wire("fx", "a3", targetHandle="in"), wire("a3", "sn", targetHandle="in")])
fx2 = next((f for f in chosen["functions"] if f["id"] == "fx"), None)
check("an explicit Alert is not overridden",
      fx2 and len(fx2["triggers"]) == 1 and fx2["triggers"][0]["value"] == 0.5,
      str(fx2 and fx2.get("triggers")))

# A MEASUREMENT still needs somebody to say which value matters: a count is a
# quantity, not a verdict, and "not zero" there would be a threshold invented
# on the operator's behalf.
raw = compile_([n for n in scene if n["id"] not in ("fx", "a1", "a2", "cy")],
               [w for w in links
                if w["source"] not in ("fx", "a1", "a2", "cy")
                and w["target"] not in ("fx", "a1", "a2", "cy")]
               + [wire("zc", "sn", targetHandle="in")])
msg = " ".join(e["message"] for e in raw["errors"])
check("a counter wired straight to a Snapshot is still refused",
      bool(raw["errors"]), str(raw["warnings"]))
check("and Compare is offered beside Alert -- it arms one too",
      "Compare" in msg, msg)
check("and the message says a Formula needs neither", "Formula" in msg, msg)

print("== a capture output can name its camera")
cam = compile_(scene, links)
snapnode = next(n for n in scene if n["id"] == "sn")
snapnode["data"]["camera"] = "cam_b"
cam = compile_(scene, links)
snap = next((o for o in cam["outputs"] if o["type"] == "snapshot"), None)
check("the choice reaches the engine", snap and snap.get("camera") == "cam_b",
      str(snap))
snapnode["data"]["camera"] = ""
blank = compile_(scene, links)
snap = next((o for o in blank["outputs"] if o["type"] == "snapshot"), None)
check("and blank stays blank -- follow the rule that fired",
      snap and "camera" not in snap, str(snap))
run_py = io.open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
                 encoding="utf-8").read()
check("a camera that is not running costs the picture, not the app",
      "_capture_pipe" in run_py and "is not running" in run_py)

print("== the canvas says the same things the compiler does")
reg = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "registry.js"),
              encoding="utf-8").read()
# Spelled through isValueSink now: the Math family made "a node that takes a
# value" a list of sixteen, thirteen of which the server names, so the client
# asks the registry instead of keeping its own copy (smoke_math.py).
check("the client allows Condition/Alert -> Formula too",
      "TRANSPARENT_OUT.includes(srcType) && isValueSink(registry, tgtType)" in reg
      and '"analytics.expr", "analytics.formula",' in reg
      and '(registry[type] || {}).math' in reg)
check("and refuses every other output",
      "only a Condition can feed a Formula" in reg)
check("the mirrored target map lets an output reach analytics",
      'output: ["output", "dashboard", "common", "analytics"]' in reg)

page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
               encoding="utf-8").read()
# OID adds an OUTPUT, so its switch belongs where it can be seen, not folded
# into Advanced with the plumbing.
head = page[page.index("{spec.description && <InfoHint>"):]
head = head[:head.index("{err &&")]
check("the OID switch sits on the title row",
      "COUNTS_BY_PID.has(node.type)" in head and 'set("OID")' in head, head[:200])
check("at the far right", 'marginLeft: "auto"' in head)
check("and only where an object id exists to give",
      '["countdown", "occupancy"].includes(d.mode)' in head)
# Two Name fields left on the whole page. The Switch's went with the branch
# split -- a branch is named by renaming its header, like every other node --
# and the Trigger asks for an "Output name" instead, which is the one name
# nothing can work out for the operator (smoke_branch.py).
# ...and neither does a Math node: "Add" is an operation on what is wired in,
# not a measurement anyone looks up by name later.
# The third is the Modbus Write's: its field set is Node-RED's, Name first,
# and the name is what its header shows -- four writes to one PLC are told
# apart by what they switch.
# The fourth is the Class Filter's (2026-10-05): the dashboard's Class
# filter card lists filters by name, and several need one each.
check("a Formula asks for no name",
      'node.type !== "analytics.formula" && !spec.math && (' in page
      and page.count('label="Name"') == 4
      and '{node.type === "filter.class" && (() => {' in page
      and '{node.type === "output.modbus_write" && (<>' in page
      # The Trigger's "Output name" lives in its own inspector since the
      # 2026-10-14 redesign (frontend/src/workflow/TriggerInspector.jsx).
      and 'label="Output name"' in io.open(os.path.join(
          ROOT, "frontend", "src", "workflow", "TriggerInspector.jsx"),
          encoding="utf-8").read())

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
