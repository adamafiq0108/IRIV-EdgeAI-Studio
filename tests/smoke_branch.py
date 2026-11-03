# The Branch family and the Trigger: every way out is a REAL port.
#
# What this replaces: `logic.when` (Compare) and `output.alert` (Condition)
# published nothing of their own. They were resolved at compile time by
# walking BACKWARDS to the rule behind them -- the "transparent node"
# convention -- so "what value does this port carry?" had no answer on the
# canvas, and a wire out of one meant whatever the compiler later inferred.
#
# Now five branch shapes each publish one 0/1 rule per way out, and a Trigger
# names the moment. The invariant every one of them keeps: exactly ONE output
# is 1 at a time, first match wins, last port is the catch-all.
#
# The migration matters as much as the feature: a graph saved yesterday has
# Conditions and Alerts in it, and it has to keep running -- same branch ids,
# so the cards and history bound to them never move.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def graph(nodes, edges):
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def counter(nid="cnt", name="People"):
    return {"id": nid, "type": "analytics.violations",
            "position": {"x": 0, "y": 0},
            "data": {"fnId": nid, "name": name, "zone": "bay", "mode": "realtime"}}


def zone(nid="z"):
    return {"id": nid, "type": "zone.ref", "position": {"x": 0, "y": 0},
            "data": {"zone": "bay"}}


def source(nid="src"):
    """The Detection Stream. Without one every rule downstream is SWITCHED OFF
    -- and a switched-off chain reports nothing, so a test built without one
    would pass while proving nothing."""
    return {"id": nid, "type": "source.detection", "position": {"x": -1, "y": 0},
            "data": {}}


def wire(src, tgt, handle=None, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{handle or ''}", "source": src, "target": tgt,
            "sourceHandle": handle, "targetHandle": target_handle}


def compile_(nodes, edges):
    return wf.compile_graph(graph(nodes, edges), ZONES)


# -- every branch shape is in the registry, and knows its own shape ----------
SHAPES = {"branch.compare": "compare", "branch.cases": "compare",
          "branch.range": "range", "branch.match": "switch",
          "branch.multi": "multi"}
#: Compare has since been folded into If / Else if, which IS this node with
#: one row -- same inputs, same two ports, same compiled branch ids. It still
#: loads and still compiles; it is off the palette (smoke_compare_merge below).
FOLDED = {"branch.compare", "branch.range", "branch.match"}
for ntype, ctype in SHAPES.items():
    spec = wf.REGISTRY[ntype]
    assert spec.get("switch"), ntype
    assert wf.is_branch(ntype), ntype
    assert wf.branch_ctype({"type": ntype}) == ctype, ntype
    assert bool(spec.get("deprecated")) == (ntype in FOLDED), ntype
# The nodes they replace are still loadable -- never deleted, only deprecated.
for old in ("logic.switch", "logic.when", "output.alert", "output.event"):
    assert wf.REGISTRY[old].get("deprecated"), f"{old} is not marked deprecated"
print("== the branch shapes, fixed by type; the nodes they replace still load")

# -- Compare IS If / Else if with one row ------------------------------------
# One test with a True and a False way out is "if / else": the same words, the
# same two ports, the same compiled ids. Two palette entries for that was
# choosing between a thing and itself.
g = {"version": 8, "nodes": [
    {"id": "c", "type": "branch.compare", "position": {"x": 0, "y": 0},
     "data": {"fnId": "busy", "rules": [{"op": ">", "value": 4}]}}],
    "edges": []}
assert wf.migrate_compare_merge(g) is True
node = g["nodes"][0]
assert node["type"] == "branch.cases", node["type"]
# The False way out is spelled `otherwise` on the merged node. Absent, the
# port would vanish and the wire on it would have nothing to anchor to.
assert node["data"]["otherwise"] is True, node["data"]
assert node["data"]["rules"] == [{"op": ">", "value": 4}], node["data"]
assert g["version"] == 9, g["version"]
print("== a Compare becomes an If / Else if with its one row and both ports")

# -- a Compare publishes TWO real rules, not a trigger on its neighbour ------
c = compile_([source(), zone(), counter(),
              {"id": "cmp", "type": "branch.compare", "position": {"x": 1, "y": 0},
               "data": {"fnId": "busy", "rules": [{"op": ">", "value": 4,
                                                   "input": "in1"}],
                        "otherwise": True}}],
             [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "cmp")])
assert not c["errors"], c["errors"]
ids = [f["id"] for f in c["functions"]]
assert "busy_b0" in ids and "busy_b1" in ids, ids
exprs = {f["id"]: f["params"].get("expr") for f in c["functions"]
         if f["type"] == "expr"}
assert exprs["busy_b0"] == "(fn_cnt_count > 4)", exprs
# The False side is the catch-all: "not the test", which is what makes exactly
# one of the two lit at any moment.
assert exprs["busy_b1"] == "not (fn_cnt_count > 4)", exprs
print("== Compare compiles True and False as two real, opposite rules")

# -- the one-lit-at-a-time invariant, on a shape with several bands ----------
c = compile_([source(), zone(), counter(),
              {"id": "rng", "type": "branch.range", "position": {"x": 1, "y": 0},
               "data": {"fnId": "band",
                        "rules": [{"op": "between", "value": 0, "value2": 5},
                                  {"op": "between", "value": 5, "value2": 10}],
                        "otherwise": True}}],
             [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "rng")])
assert not c["errors"], c["errors"]
bands = {f["id"]: f["params"]["expr"] for f in c["functions"] if f["type"] == "expr"}
assert len(bands) == 3, bands
# Each later band excludes every earlier one; the catch-all excludes them all.
assert "and not" in bands["band_b1"], bands
assert bands["band_b2"].startswith("not "), bands
assert bands["band_b2"].count("not ") == 2, bands
print("== bands tile: first match wins, and the last port takes the rest")

# -- a branch port is a value like any other ---------------------------------
# This is the whole point of deleting transparency: the port carries a number,
# so it can be wired where numbers go.
c = compile_([source(), zone(), counter(),
              {"id": "cmp", "type": "branch.compare", "position": {"x": 1, "y": 0},
               "data": {"fnId": "busy", "rules": [{"op": ">", "value": 4,
                                                   "input": "in1"}]}},
              {"id": "card", "type": "dashboard.card", "position": {"x": 2, "y": 0},
               "data": {"name": "Busy", "kind": "tile", "metric": ""}}],
             [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "cmp"),
              wire("cmp", "card", "out0")])
assert not c["errors"], c["errors"]
assert any(w.get("key") == "fn_busy_b0_value" for w in c["widgets"]), c["widgets"]
print("== a card wired to the True port reads that port's own value")

# -- the Trigger names the moment -------------------------------------------
def with_trigger(data):
    return compile_(
        [source(), zone(), counter(),
         {"id": "cmp", "type": "branch.compare", "position": {"x": 1, "y": 0},
          "data": {"fnId": "busy", "rules": [{"op": ">", "value": 4,
                                              "input": "in1"}]}},
         {"id": "trg", "type": "output.trigger", "position": {"x": 2, "y": 0},
          "data": data}],
        [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "cmp"),
         wire("cmp", "trg", "out0", "in")])


c = with_trigger({"name": "Overcrowded"})
assert not c["errors"], c["errors"]
fn = [f for f in c["functions"] if f["id"] == "busy_b0"][0]
trig = fn.get("trigger") or (fn.get("triggers") or [None])[0]
assert trig, fn
# Untouched, a Trigger is a NAMED PASS-THROUGH: no severity, no cooldown, no
# colour. That is what gating a Data Table write wants, and what the Alert it
# replaces could never be talked out of being.
assert trig["severity"] == "info", trig
assert trig["message"] == "Overcrowded", trig
assert trig.get("cooldown_s", 0) == 0, trig
assert not trig.get("box_color"), trig
# ...and exactly ONE trigger: the branch's own plain one is replaced, not
# joined, or one thing happening would raise two events.
assert not fn.get("triggers"), fn
print("== an untouched Trigger raises a named event and nothing else")

c = with_trigger({"name": "Overcrowded", "alert": True, "severity": "crit",
                  "message": "too many in the bay", "cooldown_s": 45,
                  "draw": True, "box_color": "#ff0000"})
fn = [f for f in c["functions"] if f["id"] == "busy_b0"][0]
trig = fn.get("trigger")
assert trig["severity"] == "crit" and trig["cooldown_s"] == 45, trig
assert trig["message"] == "too many in the bay", trig
# The colour is normalised on the way in, so compare case-blind.
assert trig["box_color"].lower() == "#ff0000", trig
print("== ticking the alarm halves fills in severity, message, pacing, colour")

# -- a Trigger must be named -------------------------------------------------
# The one name on the canvas nothing can guess. "Trigger 1" would be a name
# that says nothing in an alert, on a card, or in a year-old event row.
c = with_trigger({"name": ""})
assert any("needs a name" in e["message"] for e in c["errors"]), c["errors"]
print("== a nameless Trigger is refused, not defaulted")

# -- and it will not pick a threshold on the operator's behalf ---------------
c = compile_([source(), zone(), counter(),
              {"id": "trg", "type": "output.trigger", "position": {"x": 1, "y": 0},
               "data": {"name": "Busy"}}],
             [wire("src", "cnt"), wire("z", "cnt", None, "zone"),
              wire("cnt", "trg", None, "in")])
assert any("measures rather than decides" in e["message"] for e in c["errors"]), \
    c["errors"]
print("== a measurement wired straight into a Trigger is refused")

# -- MIGRATION: a graph saved yesterday still runs ---------------------------
# Condition + Alert, the pair this replaces, with an alarm on the Alert.
old = {"version": 3, "nodes": [
    source(), zone(), counter(),
    {"id": "cond", "type": "output.alert", "position": {"x": 1, "y": 0},
     "data": {"fnId": "busy", "metric": "count", "op": ">", "value": 4}},
    {"id": "al", "type": "output.event", "position": {"x": 2, "y": 0},
     "data": {"when": "true", "severity": "crit", "message": "bay full",
              "cooldown_s": 45, "box_color": "#ff0000"}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 3, "y": 0},
     "data": {}}],
    "edges": [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "cond"),
              wire("cond", "al", None, "in"), wire("al", "snap", None, "in")]}
assert wf.migrate_branch_family(old) is True
types = {n["id"]: n["type"] for n in old["nodes"]}
assert types["cond"] == "branch.compare", types
assert "al" not in types, "the Alert survived as its own node"
trig_id = [i for i, t in types.items() if t == "output.trigger"][0]
tdata = [n for n in old["nodes"] if n["id"] == trig_id][0]["data"]
# Everything the operator wrote is carried over, and the message becomes the
# name -- it is the sentence they already wrote about this moment.
assert tdata["name"] == "bay full" and tdata["alert"] is True, tdata
assert tdata["severity"] == "crit" and tdata["cooldown_s"] == 45, tdata
assert tdata["draw"] is True and tdata["box_color"].lower() == "#ff0000", tdata
# What hung off the Alert now hangs off the Trigger.
assert any(e["source"] == trig_id and e["target"] == "snap"
           for e in old["edges"]), old["edges"]
# ...and it still compiles, on the SAME branch ids the old graph published.
c = wf.compile_graph(wf.clean_graph(old), ZONES)
assert not c["errors"], c["errors"]
assert any(f["id"] == "busy_b0" for f in c["functions"]), \
    [f["id"] for f in c["functions"]]
print("== Condition + Alert migrate to Compare + Trigger, ids unchanged")

# -- a `when: false` Alert becomes a wire, not a setting ---------------------
old = {"version": 3, "nodes": [
    source(), zone(), counter(),
    {"id": "cond", "type": "output.alert", "position": {"x": 1, "y": 0},
     "data": {"fnId": "busy", "metric": "count", "op": ">", "value": 0}},
    {"id": "al", "type": "output.event", "position": {"x": 2, "y": 0},
     "data": {"when": "false", "severity": "warn", "message": "bay empty"}}],
    "edges": [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "cond"),
              wire("cond", "al", None, "in")]}
wf.migrate_branch_family(old)
edge = [e for e in old["edges"] if e["target"].endswith("_trig")][0]
assert edge["sourceHandle"] == "out1", edge
print("== 'fire while false' becomes a wire to the False port, not a setting")

# -- and the old Switch keeps its shape and its ids --------------------------
for ctype, expect in (("compare", "branch.cases"), ("range", "branch.range"),
                      ("switch", "branch.match"), ("multi", "branch.multi")):
    g = {"version": 3, "nodes": [
        {"id": "sw", "type": "logic.switch", "position": {"x": 0, "y": 0},
         "data": {"fnId": "route", "ctype": ctype,
                  "rules": [{"op": ">", "value": 1, "input": "in1"}]}}],
        "edges": []}
    wf.migrate_branch_family(g)
    assert g["nodes"][0]["type"] == expect, (ctype, g["nodes"][0]["type"])
print("== each old Switch shape becomes the branch node that IS that shape")

# -- a plain Compare migrates too --------------------------------------------
g = {"version": 3, "nodes": [
    source(), zone(), counter(),
    {"id": "w", "type": "logic.when", "position": {"x": 1, "y": 0},
     "data": {"mode": "above", "value": 3}}],
    "edges": [wire("src", "cnt"), wire("z", "cnt", None, "zone"), wire("cnt", "w")]}
wf.migrate_branch_family(g)
node = [n for n in g["nodes"] if n["id"] == "w"][0]
assert node["type"] == "branch.compare", node
assert node["data"]["rules"] == [{"op": ">", "value": 3, "input": "in1"}], node["data"]
c = wf.compile_graph(wf.clean_graph(g), ZONES)
assert not c["errors"], c["errors"]
print("== an old Compare keeps its test, now as a rule row")
print("ALL OK")
