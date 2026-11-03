# A Formula's chainable operations (add, and, or, min...) take as many
# inputs as asked for -- "a or b or c or d" is one node, not a tree of
# two-input ones. Fixed-arity operations and existing two-input nodes are
# untouched.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import workflow as wf                       # noqa: E402

assert wf.FORMULA_HANDLES == ("a", "b", "c", "d", "e", "f", "g", "h")
assert "or" in wf.FORMULA_CHAINABLE and "iff" not in wf.FORMULA_CHAINABLE
spec = wf.REGISTRY["analytics.formula"]
assert spec["ops"]["or"].get("chain") is True
assert "chain" not in spec["ops"]["iff"]


def compile_formula(data, extra_nodes=(), extra_edges=()):
    g = {"nodes": [
        {"id": "f1", "type": "analytics.formula", "position": {"x": 2, "y": 0},
         "data": {"fnId": "fx", "name": "F", **data}},
        *extra_nodes,
    ], "edges": list(extra_edges)}
    c = wf.compile_graph(wf.clean_graph(g), [])
    assert not c["errors"], c["errors"]
    return next(f for f in c["functions"] if f["id"] == "fx")["params"]["expr"]


def var(nid, key):
    return {"id": nid, "type": "variable.ref", "position": {"x": 0, "y": 0},
            "data": {"key": key, "io": "in"}}


# Four inputs, two wired and two typed: one OR across all of them.
expr = compile_formula(
    {"op": "or", "inputs": 4, "c_value": 1, "d_value": 0},
    extra_nodes=[var("v1", "fn_x_count"), var("v2", "fn_y_count")],
    extra_edges=[
        {"id": "e1", "source": "v1", "target": "f1", "targetHandle": "a"},
        {"id": "e2", "source": "v2", "target": "f1", "targetHandle": "b"},
    ])
assert expr == "(((fn_x_count or fn_y_count) or 1) or 0)", expr
print("== or chains left to right across four inputs")

# avg goes n-ary -- pairwise folding would be a different number.
expr = compile_formula({"op": "avg", "inputs": 3,
                        "a_value": 3, "b_value": 4, "c_value": 8})
assert expr == "avg(3, 4, 8)", expr
print("== avg compiles n-ary, not pairwise")

# No `inputs` key (every node minted before this): exactly the old expr.
expr = compile_formula({"op": "add", "a_value": 1, "b_value": 2})
assert expr == "(1 + 2)", expr
# A fixed-arity operation ignores `inputs` entirely.
expr = compile_formula({"op": "iff", "inputs": 7,
                        "a_value": 1, "b_value": 2, "c_value": 3})
assert expr == "iff(1, 2, 3)", expr
print("== old nodes and fixed-arity operations are byte-identical")

# Out-of-range asks clamp instead of erroring.
expr = compile_formula({"op": "add", "inputs": 99,
                        **{f"{h}_value": 1 for h in wf.FORMULA_HANDLES}})
assert expr.count("+") == 7, expr
print("== the input count clamps to the eight handles")

# Each join picks its own operation: a and b, then or c -- one node.
expr = compile_formula({"op": "and", "inputs": 3, "ops": ["or"],
                        "a_value": 1, "b_value": 0, "c_value": 1})
assert expr == "((1 and 0) or 1)", expr
# A bogus per-join entry falls back to the main operation, never crashes.
expr = compile_formula({"op": "add", "inputs": 3, "ops": ["nonsense"],
                        "a_value": 1, "b_value": 2, "c_value": 3})
assert expr == "((1 + 2) + 3)", expr
# Mixed joins break the n-ary special case: avg then add folds pairwise.
expr = compile_formula({"op": "avg", "inputs": 3, "ops": ["add"],
                        "a_value": 4, "b_value": 6, "c_value": 1})
assert expr == "(avg(4, 6) + 1)", expr
print("== each join picks its own operation; bad entries fall back")

# The engine actually evaluates a chained expression.
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import functions as afn                               # noqa: E402
fn = afn.Function({"id": "t", "type": "expr",
                   "params": {"expr": "((0 or 0) or 5)"}})   # logic: 1/0
fn2 = afn.Function({"id": "s", "type": "expr",
                    "params": {"expr": "((1 + 2) + 3)"}})
eng = afn.FunctionEngine([fn, fn2])
eng.update({"crossing_events": []}, [], {}, {}, 100, 100, [], now=1000.0)
assert eng.stats()["fn_t_value"] == 1.0, eng.stats()
assert eng.stats()["fn_s_value"] == 6, eng.stats()
print("== the app engine evaluates the folded chain")

print("ALL OK")

# -- the optional gate: "if condition { formula }" -------------------------------
# A condition wired onto the `when` port makes the formula compute only while
# it holds (0 otherwise); unwired, the compiled expr is byte-identical.
gated = compile_formula(
    {"op": "mul", "a_value": 3, "b_value": 4},
    extra_nodes=[var("v1", "fn_gate_value")],
    extra_edges=[{"id": "eg", "source": "v1", "target": "f1",
                  "targetHandle": "when"}])
assert gated == "iff((fn_gate_value != 0), ((3 * 4)), 0)", gated
print("== a condition on the when port gates the formula")

# ...and a MATH node gates the same way. It has to: a branch says "only while
# this holds" about what comes AFTER it, and the `when` port is the only way to
# say it about the arithmetic ITSELF. Without it there is nowhere on a Math
# node for a Control node to land except an operand -- which changes the sum
# instead of gating it, so "add these two while the line is running" could not
# be drawn at all.
g = {"nodes": [
    {"id": "m1", "type": "math.arith", "position": {"x": 2, "y": 0},
     "data": {"fnId": "mx", "name": "M", "op": "add",
              "a_value": 2, "b_value": 5}},
    var("v1", "fn_gate_value"),
], "edges": [{"id": "eg", "source": "v1", "target": "m1",
              "targetHandle": "when"}]}
c = wf.compile_graph(wf.clean_graph(g), [])
assert not c["errors"], c["errors"]
math_expr = next(f for f in c["functions"] if f["id"] == "mx")["params"]["expr"]
assert math_expr == "iff((fn_gate_value != 0), ((2 + 5)), 0)", math_expr
print("== a Math node gates on the same port, so Control can reach it")

# The checkbox that MINTS that port is on the Math nodes too -- the port only
# exists when something asks for it, so hiding the box hid the connection.
page = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
            encoding="utf-8").read()
assert "isMath || !!d.gated || gated" not in page, \
    "the gate checkbox is hidden from Math nodes again -- a Control node then " \
    "has nowhere to land on one but an operand"
print("== the gate checkbox is offered on Math nodes")
