# Formula node: pick an operation, wire operands in (or type numbers).
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


BASE = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay_now", "name": "Bay now"}},
    {"id": "ref", "type": "variable.ref", "position": {"x": 2, "y": 1},
     "data": {"key": "objects_in_frame"}},
]
BASE_E = [{"id": "e1", "source": "s", "target": "z"},
          {"id": "e2", "source": "z", "target": "occ"}]

# 1. two wired operands
g = build(BASE + [
    {"id": "f", "type": "analytics.formula", "position": {"x": 3, "y": 0},
     "data": {"fnId": "sum", "name": "Sum", "op": "add"}},
], BASE_E + [
    {"id": "e3", "source": "occ", "target": "f", "targetHandle": "a"},
    {"id": "e4", "source": "ref", "target": "f", "targetHandle": "b"},
])
c = wf.compile_graph(g, ZONES)
assert not c["errors"], c["errors"]
expr = next(f for f in c["functions"] if f["id"] == "sum")["params"]["expr"]
print("add, both wired      ->", expr)
assert expr == "(fn_bay_now_count + objects_in_frame)", expr

# 2. one wired, one typed number
g = build(BASE + [
    {"id": "f", "type": "analytics.formula", "position": {"x": 3, "y": 0},
     "data": {"fnId": "over", "name": "Over", "op": "gt", "b_value": 3}},
], BASE_E + [{"id": "e3", "source": "occ", "target": "f", "targetHandle": "a"}])
c = wf.compile_graph(g, ZONES)
assert not c["errors"], c["errors"]
expr = next(f for f in c["functions"] if f["id"] == "over")["params"]["expr"]
print("greater-than, typed b->", expr)
assert expr == "(fn_bay_now_count > 3)", expr

# 3. unary op takes ONE operand only
g = build(BASE + [
    {"id": "f", "type": "analytics.formula", "position": {"x": 3, "y": 0},
     "data": {"fnId": "rounded", "name": "Rounded", "op": "round"}},
], BASE_E + [{"id": "e3", "source": "occ", "target": "f", "targetHandle": "a"}])
c = wf.compile_graph(g, ZONES)
assert not c["errors"], c["errors"]
expr = next(f for f in c["functions"] if f["id"] == "rounded")["params"]["expr"]
print("round (unary)        ->", expr)
assert expr == "round(fn_bay_now_count)", expr

# 4. ternary: clamp with a wired value and two typed bounds
g = build(BASE + [
    {"id": "f", "type": "analytics.formula", "position": {"x": 3, "y": 0},
     "data": {"fnId": "capped", "name": "Capped", "op": "clamp",
              "b_value": 0, "c_value": 10}},
], BASE_E + [{"id": "e3", "source": "occ", "target": "f", "targetHandle": "a"}])
c = wf.compile_graph(g, ZONES)
assert not c["errors"], c["errors"]
expr = next(f for f in c["functions"] if f["id"] == "capped")["params"]["expr"]
print("clamp (ternary)      ->", expr)
assert expr == "clamp(fn_bay_now_count, 0, 10)", expr

# 5. a missing operand is a readable error, not a silent zero
g = build(BASE + [
    {"id": "f", "type": "analytics.formula", "position": {"x": 3, "y": 0},
     "data": {"fnId": "bad", "name": "Bad", "op": "add", "b_value": ""}},
], BASE_E + [{"id": "e3", "source": "occ", "target": "f", "targetHandle": "a"}])
c = wf.compile_graph(g, ZONES)
assert c["errors"], "a missing operand compiled anyway"
print("missing operand      ->", c["errors"][0]["message"])

# 6. every operation produces an expression the engine accepts
from core.services import functions as fx                     # noqa: E402
for op in wf.FORMULA_OPS:
    arity = wf.FORMULA_OPS[op]["arity"]
    data = {"fnId": f"t_{op}", "name": op, "op": op,
            "a_value": 2, "b_value": 3, "c_value": 4}
    nodes = [{"id": "f", "type": "analytics.formula",
              "position": {"x": 0, "y": 0}, "data": data}]
    edges = []
    if wf.FORMULA_OPS[op].get("wire_only"):
        # "set to" holds no number of its own -- the value it passes is
        # wired in, so the test wires one the way an operator would.
        nodes.append({"id": "v", "type": "variable.ref",
                      "position": {"x": -1, "y": 0},
                      "data": {"key": "fn_src_value", "io": "in"}})
        edges.append({"id": "ev", "source": "v", "target": "f",
                      "targetHandle": "a"})
    g = build(nodes, edges)
    c = wf.compile_graph(g, ZONES)
    assert not c["errors"], (op, c["errors"])
    expr = next(f for f in c["functions"]
                if f["id"] == f"t_{op}")["params"]["expr"]
    fx.validate_expr(expr)          # the engine's own whitelist
    assert arity == wf.FORMULA_OPS[op]["arity"]
print(f"== all {len(wf.FORMULA_OPS)} operations compile to valid expressions")

# ...and "set to" REFUSES a typed stand-in: the value it passes is a wire.
g = build([{"id": "f", "type": "analytics.formula",
            "position": {"x": 0, "y": 0},
            "data": {"fnId": "t_set2", "name": "set", "op": "set",
                     "a_value": 7}}], [])
c = wf.compile_graph(g, ZONES)
assert c["errors"] and "wire a Variable into input a" in c["errors"][0]["message"],     c["errors"]
print("== set to insists on its variable being wired, never typed")
print("ALL OK")
