# Condition says WHEN. Alert says what the alarm looks like.
#
# They were one node, so choosing a threshold meant scrolling past a colour
# picker to reach it, and the node was called "Alert" -- naming the loudest
# thing it can cause rather than the thing it is. Split:
#
#   Condition  metric, comparison, value        -> publishes a bool
#   Alert      severity, message, cooldown,     -> decorates that moment,
#              colour, and true/false              and may read it inverted
#
# The Alert DECORATES rather than gates: the Condition raises its event with
# or without one, so everything already wired after a Condition keeps firing.
# And a Condition publishes its rule's `active` -- 1 while it holds -- so it
# reads as a bool wherever a value is wanted, including a Formula's "only
# when" gate.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 10, "y": 20}, "data": data}


def wire(src, tgt, **kw):
    return {"id": f"e_{src}_{tgt}_{kw.get('targetHandle', '')}",
            "source": src, "target": tgt, **kw}


def compile_(nodes, edges):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                            ZONES)


BASE = [node("s", "source.detection"), node("z", "zone.ref", zone="zone_a"),
        node("zc", "analytics.violations", fnId="zc", name="Counter",
             mode="realtime"),
        node("cond", "output.alert", metric="count", op=">", value=1)]
EDGES = [wire("s", "zc", targetHandle="in"), wire("z", "zc", targetHandle="zone"),
         wire("zc", "cond", targetHandle="in")]


def trigger_of(compiled, fid="zc"):
    fn = next((f for f in compiled["functions"] if f["id"] == fid), None)
    trigs = (fn or {}).get("triggers") or []
    return trigs[0] if trigs else None


print("== a Condition on its own still raises a plain event")
# The split must not make an Alert compulsory: everything already wired after
# a Condition -- a Snapshot, a card, a Data Table -- fires on this trigger.
bare = compile_(BASE, EDGES)
t = trigger_of(bare)
check("it compiles", not bare["errors"], str(bare["errors"]))
check("with the operator's own comparison",
      t and t["op"] == ">" and t["value"] == 1.0, str(t))
# A plain alarm: default severity, and the message the trigger validator has
# always written from the rule's own name when nobody typed one.
check("and a plain alarm", t and t["severity"] == "warn"
      and t["message"] == "Counter triggered", str(t))

print("== an Alert says what that alarm looks like")
ALERT = node("al", "output.event", when="true", severity="crit",
             message="Bay crowded", cooldown_s=60, box_color="#00FF00",
             color_target="zone")
decorated = compile_(BASE + [ALERT], EDGES + [wire("cond", "al", targetHandle="in")])
t = trigger_of(decorated)
check("no errors", not decorated["errors"], str(decorated["errors"]))
check("severity comes from the Alert", t and t["severity"] == "crit", str(t))
check("so does the message", t and t["message"] == "Bay crowded", str(t))
check("so does the cooldown", t and t["cooldown_s"] == 60, str(t))
check("so does the colour and what it paints",
      t and t["box_color"] == "#00FF00" and t["color_target"] == "zone", str(t))
check("the comparison stays the Condition's",
      t and t["op"] == ">" and t["value"] == 1.0, str(t))

print("== ...and may read that moment inverted")
# "Tell me when the bay is NOT occupied" is a real alarm. Written as a second
# Condition it is the same threshold in two places, kept equal by hand.
for op, inverse in ((">", "<="), (">=", "<"), ("<", ">="), ("<=", ">"),
                    ("==", "!="), ("!=", "==")):
    scene = [n for n in BASE if n["id"] != "cond"] + [
        node("cond", "output.alert", metric="count", op=op, value=1),
        node("al", "output.event", when="false")]
    c = compile_(scene, EDGES + [wire("cond", "al", targetHandle="in")])
    t = trigger_of(c)
    check("%s inverts to %s" % (op, inverse), t and t["op"] == inverse, str(t))
check("and the threshold itself never moves", t and t["value"] == 1.0, str(t))

# Compare already carries its own direction, so asking THIS for the false side
# would be two answers to one question.
cmp_scene = [n for n in BASE if n["id"] != "cond"] + [
    node("cmp", "logic.when", metric="count", mode="above", value=1),
    node("al", "output.event", when="false")]
c = compile_([n for n in cmp_scene], [w for w in EDGES if w["target"] != "cond"]
             + [wire("zc", "cmp", targetHandle="in"),
                wire("cmp", "al", targetHandle="in")])
check("a Compare refuses the false side and says why",
      c["errors"] and "Compare already says" in c["errors"][0]["message"],
      str(c["errors"]))

print("== one alarm per moment")
two = compile_(BASE + [ALERT, node("al2", "output.event", severity="info")],
               EDGES + [wire("cond", "al", targetHandle="in"),
                        wire("cond", "al2", targetHandle="in")])
check("two Alerts on one Condition is refused",
      two["errors"] and "contradict" in two["errors"][0]["message"],
      str(two["errors"]))

orphan = compile_(BASE + [node("lonely", "output.event", severity="crit")], EDGES)
msgs = " ".join(w["message"] for w in orphan["warnings"])
check("an Alert with no Condition is switched off, not errored",
      not orphan["errors"] and "reach nothing" in msgs, msgs)

print("== what is wired after an Alert fires on the same rule")
sn = node("sn", "output.snapshot", mode="once")
chain = compile_(BASE + [ALERT, sn],
                 EDGES + [wire("cond", "al", targetHandle="in"),
                          wire("al", "sn", targetHandle="in")])
snap = next((o for o in chain["outputs"] if o["type"] == "snapshot"), None)
check("Condition -> Alert -> Snapshot compiles", not chain["errors"],
      str(chain["errors"]))
check("and the Snapshot binds to the rule, not to nothing",
      snap and snap.get("function") == "zc", str(snap))
for pair in (("output.alert", "output.event"), ("output.event", "output.snapshot"),
             ("output.event", "dashboard.card"), ("output.event", "analytics.formula")):
    ok, why = wf.edge_allowed(*pair)
    check("%s -> %s is wirable" % pair, ok, why)

print("== an Alert takes any answer that is already true or false")
# A Formula that ANDs two alarms comes out 0 or 1: the decision is made, and
# asking for a Condition after it would mean writing "> 0" against a value
# that is already a yes or a no.
FXN = node("fx", "analytics.formula", fnId="fx", name="Formula", op="and",
           inputs=2, b_value=1)
fx_edges = EDGES + [wire("cond", "fx", targetHandle="a")]
direct = compile_(BASE + [FXN, ALERT], fx_edges + [wire("fx", "al", targetHandle="in")])
t = trigger_of(direct, "fx")
check("Formula -> Alert compiles", not direct["errors"], str(direct["errors"]))
check("armed on the formula's own answer",
      t and t["metric"] == "value" and t["op"] == ">" and t["value"] == 0.0,
      str(t))
check("wearing the Alert's severity and message",
      t and t["severity"] == "crit" and t["message"] == "Bay crowded", str(t))
inverted = compile_(
    BASE + [FXN, node("al", "output.event", when="false", message="Not both")],
    fx_edges + [wire("fx", "al", targetHandle="in")])
t = trigger_of(inverted, "fx")
check("and firing while it is FALSE reads == 0",
      t and t["op"] == "==" and t["value"] == 0.0, str(t))
sn2 = node("sn", "output.snapshot", mode="once")
after = compile_(BASE + [FXN, ALERT, sn2],
                 fx_edges + [wire("fx", "al", targetHandle="in"),
                             wire("al", "sn", targetHandle="in")])
snap2 = next((o for o in after["outputs"] if o["type"] == "snapshot"), None)
check("and what follows it binds to the formula",
      not after["errors"] and snap2 and snap2.get("function") == "fx",
      str(after["errors"]) + str(snap2))

# A MEASUREMENT is still refused: picking the interesting value of a count is
# what a Condition is for, and doing it here would choose a threshold for the
# operator. Same line the capture outputs draw.
raw = compile_([n for n in BASE if n["id"] != "cond"]
               + [node("al", "output.event", severity="crit")],
               [w for w in EDGES if w["target"] != "cond"]
               + [wire("zc", "al", targetHandle="in")])
msg = " ".join(e["message"] for e in raw["errors"])
check("a counter wired straight into an Alert is refused", bool(raw["errors"]))
check("and told to put a Condition in between", "Condition between" in msg, msg)

# An Alert wired to a RULE that a Condition elsewhere already governs would
# rewrite an alarm whose own panel says nothing about it.
beside = compile_(BASE + [node("al", "output.event", severity="crit")],
                  EDGES + [wire("zc", "al", targetHandle="in")])
msg = " ".join(e["message"] for e in beside["errors"])
check("an Alert beside a Condition rather than after it is refused",
      bool(beside["errors"]) and "rather than beside" in msg, msg)

two_in = compile_(BASE + [FXN, node("al", "output.event")],
                  fx_edges + [wire("fx", "al", targetHandle="in"),
                              wire("cond", "al", targetHandle="in")])
check("two answers into one Alert is refused",
      two_in["errors"] and "ONE answer" in two_in["errors"][0]["message"],
      str(two_in["errors"]))

print("== a Condition publishes a bool")
CY = node("cy", "analytics.dwell", fnId="cyc", name="Cycle", mode="dwell")
FX = node("fx", "analytics.formula", fnId="fx", name="Formula", op="add",
          inputs=2, b_value=0)
common = BASE + [CY, FX]
base_edges = EDGES + [wire("s", "cy", targetHandle="in"),
                      wire("z", "cy", targetHandle="zone"),
                      wire("cy", "fx", targetHandle="a")]
gated = compile_(common, base_edges + [wire("cond", "fx", targetHandle="when")])
fx = next((f for f in gated["functions"] if f["id"] == "fx"), None)
check("it gates a Formula -- 'only when this is true'",
      not gated["errors"] and fx
      and fx["params"]["expr"] == "iff((fn_zc_active != 0), ((fn_cyc_live_s + 0)), 0)",
      str(fx and fx["params"]) + str(gated["errors"]))
operand = compile_(common, base_edges + [wire("cond", "fx", targetHandle="b")])
fx = next((f for f in operand["functions"] if f["id"] == "fx"), None)
check("and reads as 1/0 wired straight into the maths",
      fx and fx["params"]["expr"] == "(fn_cyc_live_s + fn_zc_active)",
      str(fx and fx["params"]))

print("== the settings move off old graphs once")
old = {"version": 1,
       "nodes": [node("c1", "output.alert", metric="count", op=">", value=1,
                      severity="crit", message="Bay crowded", cooldown_s=60,
                      box_color="#00FF00", color_target="zone"),
                 node("c2", "output.alert", metric="count", op=">", value=1)],
       "edges": []}
check("the older migration runs first and claims only its own step",
      wf.migrate_dwell_count_alerts(old) is False and old["version"] == 2)
check("then the split runs", wf.migrate_split_alerts(old) is True)
# Each migration owns ONE version step and stamps only that step -- so this
# one still stamps 3 now that the schema has moved on to 4 (the branch family,
# smoke_branch.py). A migration that stamped the CURRENT version instead would
# let a graph skip every step minted after it.
check("and marks its own step done", old["version"] == 3)
check("...which is a step behind the schema", wf.SCHEMA_VERSION > 3)
made = [n for n in old["nodes"] if n["type"] == "output.event"]
check("one Alert, for the Condition that had something to say", len(made) == 1,
      str([n["id"] for n in made]))
check("carrying exactly what that node said",
      made and made[0]["data"] == {"when": "true", "severity": "crit",
                                   "message": "Bay crowded", "cooldown_s": 60,
                                   "box_color": "#00FF00",
                                   "color_target": "zone"},
      str(made and made[0]["data"]))
check("wired from it", any(e["source"] == "c1" and e["target"] == made[0]["id"]
                           for e in old["edges"]))
check("and placed beside it, not on top",
      made and made[0]["position"] != old["nodes"][0]["position"])
check("a Condition with nothing to say gets no node",
      not any(e["source"] == "c2" for e in old["edges"]))
check("running it again changes nothing", wf.migrate_split_alerts(old) is False)

hand = {"version": 1,
        "nodes": [node("c1", "output.alert", severity="crit", message="x"),
                  node("mine", "output.event", severity="info")],
        "edges": [wire("c1", "mine", targetHandle="in")]}
wf.migrate_split_alerts(hand)
check("a Condition already split by hand is left alone",
      len([n for n in hand["nodes"] if n["type"] == "output.event"]) == 1,
      str([n["id"] for n in hand["nodes"]]))

print("== the canvas agrees")
page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
               encoding="utf-8").read()
reg = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "registry.js"),
              encoding="utf-8").read()
check("the editor has a panel for the Alert node",
      'node.type === "output.event" && (' in page)
check("with the true/false choice",
      'Fire when the answer is' in page and 'set("when")' in page)
check("and the Condition points at it rather than repeating it",
      "Wire an <strong>Alert</strong> after this" in page)
check("both are transparent client-side",
      'const TRANSPARENT_OUT = ["output.alert", "output.event"];' in page
      and 'const TRANSPARENT_OUT = ["output.alert", "output.event"];' in reg)
check("and server-side",
      '_TRANSPARENT_OUTPUTS = ("output.alert", "output.event")'
      in io.open(os.path.join(ROOT, "core", "services", "workflow.py"),
                 encoding="utf-8").read())

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
