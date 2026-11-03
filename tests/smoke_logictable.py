# The Logic Table: rows of "when the inputs look like this, the answer is
# that", compiled to the same whitelisted expression everything else uses.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import workflow as wf                    # noqa: E402
from core.services import functions as core_fx              # noqa: E402
from app import functions as app_fx                         # noqa: E402


def table(rows, default=0, inputs=("a",)):
    """A blueprint: one Value per wired input -> the table -> the Output."""
    nodes = [{"id": "out", "type": "bp.out"},
             {"id": "t", "type": "bp.table",
              "data": {"rows": rows, "default": default}}]
    edges = [{"source": "t", "target": "out", "targetHandle": "v"}]
    for key in inputs:
        nodes.append({"id": f"v_{key}", "type": "bp.stat",
                      "data": {"key": f"in_{key}"}})
        edges.append({"source": f"v_{key}", "target": "t", "targetHandle": key})
    return {"nodes": nodes, "edges": edges}


def value(expr, **vars_):
    return app_fx.eval_expr(app_fx.compile_expr(expr), vars_)


# 1. a simple lookup: first match wins, top to bottom
expr = wf.blueprint_to_expr(table(
    [{"a": 1, "a_op": "==", "then": 10},
     {"a": 2, "a_op": "==", "then": 20}], default=99))
core_fx.validate_expr(expr)                  # must survive the sandbox
assert value(expr, in_a=1) == 10
assert value(expr, in_a=2) == 20
assert value(expr, in_a=7) == 99, "the default did not apply"
print("== rows match top to bottom, and the default catches the rest")

# 2. comparisons, not just equality
expr = wf.blueprint_to_expr(table(
    [{"a": 10, "a_op": ">=", "then": 1}], default=0))
assert value(expr, in_a=12) == 1 and value(expr, in_a=3) == 0
print("== a row can compare, not only equal")

# 3. several inputs on one row are ANDed; a BLANK cell ignores that input
expr = wf.blueprint_to_expr(table(
    [{"a": 1, "a_op": "==", "b": 1, "b_op": "==", "then": 5},
     {"b": 1, "b_op": "==", "then": 3}],          # a is blank: any a
    default=0, inputs=("a", "b")))
assert value(expr, in_a=1, in_b=1) == 5, "the two-cell row did not match"
assert value(expr, in_a=9, in_b=1) == 3, "a blank cell did not mean 'ignore'"
assert value(expr, in_a=1, in_b=0) == 0
print("== cells on a row are ANDed; a blank cell means 'ignore this input'")

# 4. a row that tests NOTHING would match everything and silently shadow every
#    row below it, so it is refused rather than quietly swallowing the table.
try:
    wf.blueprint_to_expr(table([{"then": 1}], default=0))
except ValueError as exc:
    assert "blank" in str(exc), exc
    print("== a completely blank row is refused:", exc)
else:
    raise AssertionError("a blank row was accepted")

# 5. nothing wired in is a mistake worth naming
try:
    wf.blueprint_to_expr({"nodes": [{"id": "out", "type": "bp.out"},
                                    {"id": "t", "type": "bp.table",
                                     "data": {"rows": [{"a": 1, "then": 1}]}}],
                          "edges": [{"source": "t", "target": "out",
                                     "targetHandle": "v"}]})
except ValueError as exc:
    assert "nothing wired" in str(exc), exc
    print("== an unwired table says so:", exc)
else:
    raise AssertionError("an unwired table compiled")

# 6. an unknown comparison cannot smuggle anything into the expression
try:
    wf.blueprint_to_expr(table([{"a": 1, "a_op": "; import os", "then": 1}]))
except ValueError as exc:
    assert "comparison" in str(exc), exc
    print("== an unknown comparison is refused:", exc)
else:
    raise AssertionError("an arbitrary operator was accepted")

# 7. a table big enough to blow the engine's expression cap says SO, naming
#    the blueprint -- not "expression too long" about a string nobody typed.
big = [{"a": i, "a_op": "==", "then": i} for i in range(40)]
try:
    wf.blueprint_to_expr(table(big, default=-1))
except ValueError as exc:
    assert "too big to compile" in str(exc), exc
    print("== an oversized table fails with the fix in the message:")
    print("  ", str(exc)[:100])
else:
    raise AssertionError("an oversized blueprint compiled")

# ...and a table that FITS still validates and evaluates.
ok = [{"a": i, "a_op": "==", "then": i} for i in range(8)]
expr = wf.blueprint_to_expr(table(ok, default=-1))
core_fx.validate_expr(expr)
assert value(expr, in_a=5) == 5 and value(expr, in_a=30) == -1
print("== a table within the cap validates and evaluates")
print("ALL OK")
