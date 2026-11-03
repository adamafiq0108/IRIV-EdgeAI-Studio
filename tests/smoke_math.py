# The Math family: one node per operation, and any value may reach one.
#
# The Formula was a single node with 34 operations and an input-count field.
# Its face could not say what it did until both were chosen, and finding
# "average" meant knowing to look for `avg` in a list that also held atan2.
# Each operation is its own palette entry now, and the node that lands is
# already the shape it will keep.
#
# NOTHING NEW IS COMPUTABLE. Every entry names operations that already existed
# and compiles through the same operand machinery to the same `expr` function
# -- the engine's whitelist is untouched. What this pins is that the split did
# not change a single answer, and that the wiring rule stopped drawing a line
# the operator could not see.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def wire(src, tgt, handle=None, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{target_handle or ''}", "source": src,
            "target": tgt, "sourceHandle": handle, "targetHandle": target_handle}


def counter(nid, name):
    return {"id": nid, "type": "analytics.violations", "position": {"x": 0, "y": 0},
            "data": {"fnId": nid, "name": name, "zone": "bay", "mode": "realtime"}}


BASE = [{"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 0, "y": 0},
         "data": {"zone": "bay"}},
        counter("c1", "People"), counter("c2", "Carts")]
BASE_EDGES = [wire("src", "c1"), wire("z", "c1", None, "zone"),
              wire("src", "c2"), wire("z", "c2", None, "zone")]


def compile_(node, edges=(), zones=ZONES):
    g = wf.clean_graph({"nodes": BASE + [node], "edges": BASE_EDGES + list(edges)})
    return wf.compile_graph(g, zones)


def expr_of(c, fid):
    return next((f["params"]["expr"] for f in c["functions"] if f["id"] == fid), None)


# -- every Math node is real, and knows exactly one job ----------------------
for mtype, m in wf.MATH_NODES.items():
    spec = wf.REGISTRY[mtype]
    assert spec["fn_type"] == "expr", mtype
    assert spec["metrics"] == ["value"], mtype
    # Only the retired ones (a Custom Formula function now) may be deprecated.
    assert bool(spec.get("deprecated")) == bool(m.get("deprecated")), mtype
    # A node with ONE operation has no picker at all -- there is nothing to
    # choose, and an inspector field with a single option is furniture.
    assert ("ops" in spec) == (len(m["ops"]) > 1), mtype
    for op in m["ops"]:
        assert op in wf.FORMULA_OPS, (mtype, op)
    # The node ARRIVES the shape it will keep: the handles it declares are the
    # inputs its operation takes, so Round lands with one and Clamp with three.
    # A handle nothing can be wired to invites a wire the compiler rejects.
    want = (2 if m.get("arity") is None
            else m["arity"] if isinstance(m["arity"], int)
            else wf.FORMULA_OPS[m["ops"][0]]["arity"])
    assert len(spec["handles"]) == want, (mtype, spec["handles"])
    if m.get("handles"):
        assert spec["port_labels"] == m["handles"], mtype
# The Formula they replace still loads, deprecated -- never deleted.
assert wf.REGISTRY["analytics.formula"]["deprecated"]
print("== thirteen Math nodes, each fixed to its own operation")

# -- the operation cannot drift from the node --------------------------------
# A stale `op` left by a retype or a hand-edited file must not make an Average
# subtract: the node's own list is the authority.
assert wf.math_op({"type": "math.average", "data": {"op": "sub"}}) == "avg"
assert wf.math_op({"type": "math.arith", "data": {"op": "mul"}}) == "mul"
assert wf.math_op({"type": "math.arith", "data": {}}) == "add"
print("== a node's operation is clamped to what that node can do")

# Set Variable's first input IS the variable: no number typed there could
# stand in. With one operation the node has no `ops` map for the form to read
# that off, so the entry says it -- otherwise the inspector offers a number box
# for the variable and the compiler refuses what it accepts.
assert wf.REGISTRY["math.setvar"].get("wire_only") is True
assert not wf.REGISTRY["math.arith"].get("wire_only")
print("== Set Variable declares its wire-only input on the entry itself")

# -- it computes what the Formula computed ------------------------------------
c = compile_({"id": "m", "type": "math.arith", "position": {"x": 1, "y": 0},
              "data": {"fnId": "sum", "op": "add", "inputs": 2}},
             [wire("c1", "m", None, "a"), wire("c2", "m", None, "b")])
assert not c["errors"], c["errors"]
assert expr_of(c, "sum") == "(fn_c1_count + fn_c2_count)", expr_of(c, "sum")
print("== Arithmetic compiles the expression the Formula did")

# -- a chaining node grows; a fixed one does not ------------------------------
c = compile_({"id": "m", "type": "math.average", "position": {"x": 1, "y": 0},
              "data": {"fnId": "mean", "inputs": 2}},
             [wire("c1", "m", None, "a"), wire("c2", "m", None, "b")])
# n-ary on purpose: an average folded pairwise is a different number.
assert expr_of(c, "mean") == "avg(fn_c1_count, fn_c2_count)", expr_of(c, "mean")
# Clamp takes three, and `inputs` cannot talk it into eight however chainable
# the operations table thinks it is.
c = compile_({"id": "m", "type": "math.clamp", "position": {"x": 1, "y": 0},
              "data": {"fnId": "cl", "inputs": 8, "b_value": 0, "c_value": 10}},
             [wire("c1", "m", None, "a")])
assert not c["errors"], c["errors"]
assert expr_of(c, "cl") == "clamp(fn_c1_count, 0, 10)", expr_of(c, "cl")
print("== chaining nodes chain; fixed-arity nodes keep their shape")

# -- a missing input is named by its PORT --------------------------------------
c = compile_({"id": "m", "type": "math.power", "position": {"x": 1, "y": 0},
              "data": {"fnId": "p", "a_value": None, "b_value": None}}, [])
msg = " ".join(e["message"] for e in c["errors"])
assert "Base" in msg or "Exponent" in msg, msg
print("== an empty input is reported by its port name, not by a letter")

# -- UNIVERSAL VALUE WIRING ----------------------------------------------------
# A count into an Average was refused before this: the category rule let a
# value reach a Formula and nothing else, which is a line nobody could see.
for target in ("math.average", "math.arith", "math.clamp", "analytics.expr",
               "analytics.total"):
    ok, why = wf.edge_allowed("analytics.violations", target)
    assert ok, (target, why)
# A branch port and a Trigger are answers too -- 0/1 is a number.
for src in ("branch.compare", "branch.multi", "output.trigger"):
    ok, why = wf.edge_allowed(src, "math.arith")
    assert ok, (src, why)
# ...and one Math node into the next, which is what makes them composable
# instead of one node that must hold the whole sum.
ok, why = wf.edge_allowed("math.arith", "math.round")
assert ok, why
print("== any value may feed any Math node, including another one")

# -- but an identity is not a number -------------------------------------------
# A zone reference, a class filter, a raw stream: none of these is a quantity,
# and arithmetic on one produces a plausible-looking wrong answer.
for src in ("zone.ref", "filter.class", "source.detection"):
    ok, _why = wf.edge_allowed(src, "math.arith")
    assert not ok, f"{src} reached a Math node"
print("== a zone, a filter and the raw stream still cannot feed one")

# -- two Math nodes in a row compile as one chain ------------------------------
c = compile_({"id": "m2", "type": "math.round", "position": {"x": 2, "y": 0},
              "data": {"fnId": "rounded", "op": "round"}},
             [wire("c1", "m2", None, "a")])
assert not c["errors"], c["errors"]
assert expr_of(c, "rounded") == "round(fn_c1_count)", expr_of(c, "rounded")
print("== Round wraps whatever is wired into it")

# -- MIGRATION: a saved Formula becomes its node -------------------------------
g = {"version": 4, "nodes": [
    {"id": "f1", "type": "analytics.formula", "position": {"x": 0, "y": 0},
     "data": {"fnId": "mean", "name": "Average", "op": "avg", "inputs": 3}},
    {"id": "f2", "type": "analytics.formula", "position": {"x": 0, "y": 0},
     "data": {"fnId": "cl", "op": "clamp", "inputs": 2}},
    # A comparison has no Math node -- that is a branch's job now -- so it is
    # left exactly where it works.
    {"id": "f3", "type": "analytics.formula", "position": {"x": 0, "y": 0},
     "data": {"fnId": "cmp", "op": "gt", "inputs": 2}}],
    "edges": []}
assert wf.migrate_math_family(g) is True
types = {n["id"]: n["type"] for n in g["nodes"]}
assert types["f1"] == "math.average", types
assert types["f2"] == "math.clamp", types
assert types["f3"] == "analytics.formula", types
# The chaining node keeps its input count; the fixed one drops a field nothing
# will ever read again.
assert g["nodes"][0]["data"]["inputs"] == 3, g["nodes"][0]
assert "inputs" not in g["nodes"][1]["data"], g["nodes"][1]
print("== a saved Formula becomes the node for its operation, storage intact")

# -- and it still computes the same thing --------------------------------------
old = wf.clean_graph({"nodes": BASE + [
    {"id": "f", "type": "analytics.formula", "position": {"x": 1, "y": 0},
     "data": {"fnId": "sum", "op": "add", "inputs": 2}}],
    "edges": BASE_EDGES + [wire("c1", "f", None, "a"), wire("c2", "f", None, "b")]})
before = wf.compile_graph(old, ZONES)
wf.migrate_math_family(old)
after = wf.compile_graph(wf.clean_graph(old), ZONES)
assert not after["errors"], after["errors"]
assert expr_of(before, "sum") == expr_of(after, "sum"), (before, after)
print("== the migrated node compiles the identical expression")

# -- and the CANVAS draws them -------------------------------------------------
# A registry entry nothing renders is a node in the palette that lands on the
# canvas with the Formula's 34-operation picker behind it. These pin that the
# node face and the inspector both read the family's own flags.
import io                                                        # noqa: E402

def src(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


nodes_js = src("frontend", "src", "workflow", "nodes.jsx")
assert "const isMath = !!spec.math;" in nodes_js
# Whether it chains is the NODE's property, not the operation's -- Clamp must
# not grow an eighth input because `clamp` looks chainable.
assert "const chains = isMath" in nodes_js and "!!spec.chain" in nodes_js
assert "spec.port_labels" in nodes_js, "the face ignores the real port names"

page = src("frontend", "src", "pages", "Workflow.jsx")
assert '(node.type === "analytics.formula" || spec.math) && (' in page, \
    "a Math node's inspector renders nothing"
assert "const isMath = !!spec.math;" in page
# One form for both, because a Math node IS a Formula with its operation fixed.
assert page.count("function FormulaFields(") == 1

reg_js = src("frontend", "src", "workflow", "registry.js")
assert "export function isValueSink(" in reg_js
assert "registry[tgtType]?.math || isBranch(tgtType)" in reg_js, \
    "the client still lets a zone reach a Math node the server refuses"
print("== the node face, the inspector and the client wiring rule all know")
print("ALL OK")
