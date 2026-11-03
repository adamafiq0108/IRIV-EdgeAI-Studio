# The formula library: every operation the picker offers must compile through
# the core validator AND evaluate in the app engine, and nothing in it may
# crash the detection loop whatever it is fed.
import math
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

# 1. The two whitelists must be identical. They are duplicated on purpose (the
#    app imports nothing from core), which is exactly why they drift.
assert set(core_fx._ALLOWED_CALLS) == set(app_fx._ALLOWED_CALLS), (
    "validator and engine disagree: "
    f"{set(core_fx._ALLOWED_CALLS) ^ set(app_fx._ALLOWED_CALLS)}")
assert set(core_fx._CONSTANTS) == set(app_fx._CONSTANTS)
print(f"== validator and engine agree on {len(core_fx._ALLOWED_CALLS)} functions "
      f"and {len(core_fx._CONSTANTS)} constants")

# 2. Every operation the picker offers compiles and evaluates.
VALUES = {"a": 8.0, "b": 2.0, "c": 3.0}
for key, op in wf.FORMULA_OPS.items():
    expr = op["tmpl"].format(a="a", b="b", c="c")
    names = core_fx.validate_expr(expr)          # raises if not whitelisted
    assert set(names) <= {"a", "b", "c"}, (key, names)
    tree = app_fx.compile_expr(expr)
    out = app_fx.eval_expr(tree, VALUES)
    assert isinstance(out, float) and math.isfinite(out), (key, out)
    assert op["group"] in wf.FORMULA_GROUPS, (key, op["group"])
    assert op["arity"] in (1, 2, 3), (key, op["arity"])
    assert op.get("hint"), f"{key} has no hint -- the picker cannot search it"
print(f"== all {len(wf.FORMULA_OPS)} operations compile, evaluate and are searchable")

# 3. Spot-check the maths is actually right, not merely finite.
def val(expr, **v):
    return app_fx.eval_expr(app_fx.compile_expr(expr), v)

assert round(val("log10(x)", x=1000), 6) == 3.0
assert round(val("log2(x)", x=8), 6) == 3.0
assert round(val("ln(x)", x=math.e), 6) == 1.0
assert round(val("log(x, y)", x=81, y=3), 6) == 4.0
assert round(val("exp(x)", x=1), 6) == round(math.e, 6)
assert round(val("sqrt(x)", x=9), 6) == 3.0
assert round(val("cbrt(x)", x=27), 6) == 3.0
assert round(val("cbrt(x)", x=-27), 6) == -3.0      # real cube root of a negative
assert round(val("hypot(x, y)", x=3, y=4), 6) == 5.0
assert round(val("degrees(pi)", pi=math.pi), 6) == 180.0
assert round(val("sin(radians(x))", x=90), 6) == 1.0
assert round(val("pct(x, y)", x=25, y=200), 6) == 12.5
assert round(val("scale(x, lo, hi)", x=5, lo=0, hi=10), 6) == 0.5
assert round(val("lerp(a, b, t)", a=0, b=10, t=0.25), 6) == 2.5
assert round(val("avg(a, b)", a=3, b=7), 6) == 5.0
assert val("sign(x)", x=-4) == -1 and val("sign(x)", x=9) == 1
assert val("trunc(x)", x=-2.7) == -2 and val("floor(x)", x=-2.7) == -3
print("== the maths is right: logs, roots, trig, percentages, scaling")

# 4. Named constants resolve, and are not reported as variables to bind.
assert round(val("pi"), 6) == round(math.pi, 6)
assert round(val("e"), 6) == round(math.e, 6)
assert core_fx.validate_expr("pi * r * r") == ["r"], \
    "pi is being reported as a variable the rule reads"
print("== pi / e / tau resolve as constants, not as unbound variables")

# 5. NOTHING here may raise, whatever it is fed. This runs once per frame
#    inside the detection loop: a domain error is a stopped camera.
HOSTILE = (0.0, -1.0, 1e308, -1e308, 2.0, 0.5, -0.5, 1e-308)
for key, op in wf.FORMULA_OPS.items():
    tree = app_fx.compile_expr(op["tmpl"].format(a="a", b="b", c="c"))
    for x in HOSTILE:
        for y in HOSTILE:
            out = app_fx.eval_expr(tree, {"a": x, "b": y, "c": y})
            assert isinstance(out, float) and math.isfinite(out), (key, x, y, out)
print("== every operation survives 0, negatives, huge and tiny inputs")

# 6. The sandbox is still shut: new functions must not have opened a door.
#    A SHORT text literal is allowed now (a variable may hold a word); a long
#    one, and everything that is code rather than a value, still is not.
for bad in ("__import__('os')", "open('x')", "().__class__", "a.b",
            "[1,2][0]", "'" + "x" * 121 + "'", "lambda: 1", "eval('1')", "a if b else c",
            "{1:2}", "f'{a}'", "min(a, key=b)"):
    try:
        app_fx.compile_expr(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"the expression sandbox accepted {bad!r}")
    try:
        core_fx.validate_expr(bad)
    except ValueError:
        pass
    else:
        raise AssertionError(f"the core validator accepted {bad!r}")
print("== attribute access, subscripts, strings, lambdas and kwargs still refused")
print("ALL OK")
