# Variable node: pick a value, wire it into a formula, refer to it by name.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def build(nodes, edges):
    return wf.clean_graph({"nodes": nodes, "edges": edges})


# Detection -> Zone -> Occupancy, plus a Variable node reading the occupancy,
# plus a Formula that uses BOTH by name.
graph = build([
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay_now", "name": "Bay now"}},
    {"id": "ref", "type": "variable.ref", "position": {"x": 2, "y": 1},
     "data": {"name": "Shift target", "key": "objects_in_frame"}},
    {"id": "f", "type": "analytics.expr", "position": {"x": 3, "y": 0},
     "data": {"fnId": "gap", "name": "Gap", "expr": "shift_target - bay_now"}},
], [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "f"},
    {"id": "e4", "source": "ref", "target": "f"},
])

compiled = wf.compile_graph(graph, ZONES)
print("errors:  ", compiled["errors"])
assert not compiled["errors"], compiled["errors"]
fns = {f["id"]: f for f in compiled["functions"]}
for fid, f in sorted(fns.items()):
    print(f"   {fid:14s} {f['type']:10s} {f['params']}")

# 1. the Variable node creates NOTHING -- it only holds
assert "shift_target" not in fns, \
    "the Variable node minted a second variable instead of holding one"
assert sorted(fns) == ["bay_now", "gap"], sorted(fns)
print("== Variable node publishes nothing of its own")

# 2. the formula's NAMES were replaced with the real stat keys, and the
#    Variable resolved to the key it HOLDS
gap = fns["gap"]["params"]["expr"]
print("== formula compiled to:", gap)
assert gap == "objects_in_frame - fn_bay_now_count", gap
print("== the Variable resolved to the value it holds, not a copy")

# 3. a name that is NOT wired in is left exactly as typed
graph2 = build([
    {"id": "f", "type": "analytics.expr", "position": {"x": 0, "y": 0},
     "data": {"fnId": "g2", "name": "G2", "expr": "fn_typed_key_value * 2"}},
], [])
c2 = wf.compile_graph(graph2, ZONES)
assert not c2["errors"], c2["errors"]
assert c2["functions"][0]["params"]["expr"] == "fn_typed_key_value * 2"
print("== a hand-typed key is never rewritten")

# 4. an empty Variable node is a clear error, not a silent zero
graph3 = build([
    {"id": "r", "type": "variable.ref", "position": {"x": 0, "y": 0},
     "data": {"name": "Empty", "key": ""}},
], [])
c3 = wf.compile_graph(graph3, ZONES)
assert c3["errors"] and "choose which value" in c3["errors"][0]["message"], c3["errors"]
print("== an unset Variable node says so:", c3["errors"][0]["message"])

# 5. wiring rules: formulas take it; ref-into-ref is refused; a wired STORE
ok, why = wf.edge_allowed("variable.ref", "analytics.expr")
assert ok, why
ok2, why2 = wf.edge_allowed("variable.ref", "variable.ref")
assert not ok2, "holder-into-holder was allowed"
print("== ref into ref refused:", why2)

# 6. WIRED = STORE: the output lands in the named variable and publishes.
gs = build([
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay_now", "name": "Bay now"}},
    {"id": "st", "type": "variable.ref", "position": {"x": 3, "y": 0},
     "data": {"name": "Total Violation", "key": ""}},
], [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "st"},
])
cs = wf.compile_graph(gs, ZONES)
assert not cs["errors"], cs["errors"]
stored = next(f for f in cs["functions"] if f["id"] == "total_violation")
print("== wired store compiles:", stored["id"], stored["params"])
assert stored["params"]["expr"] == "fn_bay_now_count"
print("== the output lands in fn_total_violation_value")

# 6b. a wired store with NO name is a readable error
gs2 = build([n if n["id"] != "st" else
             {**n, "data": {"name": "", "key": ""}} for n in gs["nodes"]],
            gs["edges"])
cs2 = wf.compile_graph(gs2, ZONES)
assert cs2["errors"] and "choose which variable" in cs2["errors"][0]["message"], \
    cs2["errors"]
print("== an unnamed store says so:", cs2["errors"][0]["message"])

# 5. substitution is whole-word: a longer name is not eaten by a shorter one
preds = [(None, {"type": "analytics.occupancy",
                 "data": {"fnId": "count", "name": "Count"}}),
         (None, {"type": "analytics.occupancy",
                 "data": {"fnId": "count_total", "name": "Count total"}})]
out = wf._expr_with_inputs("count_total - count", preds)
print("== substitution:", out)
assert out == "fn_count_total_count - fn_count_count", out

# A VARIABLE never reaches the editors' palette. It is a value parked on the
# canvas for a formula to read -- plumbing, not a measurement -- so listing it
# as something to chart doubles the palette with names that mean nothing
# outside the graph they were drawn in.
g = wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "Bay"}},
    {"id": "v", "type": "variable.ref", "position": {"x": 3, "y": 0},
     "data": {"name": "Parked", "_wired": True}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "v"},
]})
wf.ensure_fn_ids(g)
c = wf.compile_graph(g, [{"id": "zone_a", "name": "Bay", "kind": "area",
                          "enabled": True}])
labels = [e["label"] for e in c["catalog"]]
assert any("Bay" in l for l in labels), labels
assert not any("Parked" in l for l in labels), labels
print("== the measurement is offered to the editors, the variable is not:",
      labels)

# A variable is called by its plain name in a formula (test), never only by its
# id (fn_test_value); the id keeps working for formulas written before.
src_ = {"id": "v", "type": "variable.ref", "data": {"key": "fn_test_value", "name": ""}}
ins_ = wf.formula_inputs("f", [({"source": "v"}, src_)])
assert ins_.get("test") == "fn_test_value" and ins_.get("fn_test_value") == "fn_test_value", ins_
page_ = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"), encoding="utf-8").read()
assert "const declareVariable = useCallback(" in page_ and "That name is already used" in page_
assert page_.count("onCreateVar={declareVariable}") == 3
assert "function plainName(key)" in page_
print("== a variable answers to its plain name; a taken name is refused")

# A Variable that already held a chosen variable (key only, no name of its own)
# and then gets a wire stores into THAT variable instead of asking again.
g_ = wf.clean_graph({"nodes": [
    {"id": "i", "type": "common.inject", "position": {"x": 0, "y": 0},
     "data": {"fnId": "inj", "name": "Inj"}},
    {"id": "v", "type": "variable.ref", "position": {"x": 2, "y": 0},
     "data": {"key": "fn_violation_status_value", "name": "", "io": "out"}},
], "edges": [{"id": "e", "source": "i", "target": "v"}]})
c_ = wf.compile_graph(g_, [])
assert not [e for e in c_["errors"] if "choose which variable" in e["message"]], c_["errors"]
assert any(f["id"] == "violation_status" for f in c_["functions"]), [f["id"] for f in c_["functions"]]
print("== a Variable keeps the variable it had picked when a wire arrives")

# A constant name holds ONE value: two constant nodes with the same name are the
# same constant drawn twice, not a duplicate-name error.
g2_ = wf.clean_graph({"nodes": [
    {"id": "a", "type": "variable.number", "position": {"x": 0, "y": 0},
     "data": {"fnId": "max_score", "name": "Max Score", "value": 7, "value_type": "number"}},
    {"id": "b", "type": "variable.number", "position": {"x": 0, "y": 1},
     "data": {"fnId": "max_score", "name": "Max Score", "value": 7, "value_type": "number"}},
], "edges": []})
c2_ = wf.compile_graph(g2_, [])
assert not c2_["errors"], c2_["errors"]
assert [f["id"] for f in c2_["functions"]].count("max_score") == 1
print("== two constant nodes of one name are one constant")
print("ALL OK")
