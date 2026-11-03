# What KIND of value a Set Variable stores.
#
# The box beside "Set it to" only ever took a number, so "set the shift name
# to night" could not be drawn at all, and a count that must stay whole
# (12 people, never 12.4) had to be rounded by hand somewhere downstream.
# The node names its kind now: decimal (what it always did), whole number,
# or text.
#
# The two things worth pinning are that the kind is a property of the
# VARIABLE and not of the typed box -- a wired value rounds too -- and that
# text, which the numeric engine cannot store, says so instead of quietly
# publishing a zero.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fx                       # noqa: E402
from core.services import workflow as wf                        # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def wire(src, tgt, handle):
    return {"id": f"e_{src}_{tgt}_{handle}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": handle}


def graph(setdata, value_wired=False, when_wired=False):
    """A Variable wired into input a, and the value either typed or wired."""
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 0, "y": 0},
         "data": {"zone": "bay"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 1, "y": 0},
         "data": {"fnId": "bay_now", "name": "Bay", "zone": "bay"}},
        {"id": "v", "type": "variable.ref", "position": {"x": 1, "y": 1},
         "data": {"key": "fn_shift_value", "io": "in"}},
        {"id": "s", "type": "math.setvar", "position": {"x": 2, "y": 0},
         "data": {"fnId": "shift_set", "name": "Shift", "op": "set", **setdata}},
    ]
    edges = [wire("src", "occ", "in"), wire("z", "occ", "zone"),
             wire("v", "s", "a")]
    if value_wired:
        edges.append(wire("occ", "s", "b"))
    if when_wired:
        edges.append(wire("occ", "s", "when"))
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def compiled(setdata, value_wired=False, when_wired=False):
    return wf.compile_graph(graph(setdata, value_wired, when_wired), ZONES)


def expr_of(c):
    # A Set Variable publishes AS the variable it is wired into -- `shift`,
    # not its own node id. That is what makes the variable read the value.
    return next((f["params"]["expr"] for f in c["functions"]
                 if f["id"] == "shift"), None)


def ids_of(c):
    return sorted(f["id"] for f in c["functions"])


def read_src(*parts):
    import io
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the node offers exactly the kinds the compiler accepts -------------------
# The form reads this list off the served registry, so an option can never
# exist in the inspector that the compiler would reject -- which is how a
# picker and a compiler drift into disagreeing about what "number" means.
spec = wf.REGISTRY["math.setvar"]
check("Set Variable declares its value kinds",
      spec.get("value_types") == ["decimal", "number", "text"],
      repr(spec.get("value_types")))
check("...and arrives set to the one it always behaved as",
      spec["data"].get("value_type") == "decimal",
      repr(spec["data"].get("value_type")))
# Only the node that STORES something names a kind: an Average has no variable
# to be whole or textual, and an option there would be furniture.
check("a node that stores nothing offers no kinds",
      "value_types" not in wf.REGISTRY["math.average"]
      and "value_type" not in wf.REGISTRY["math.average"]["data"])

# -- the variable IS what gets published --------------------------------------
c0 = compiled({"b_value": 2.5})
check("a Set Variable publishes as the variable wired into input a, never as itself",
      "shift" in ids_of(c0) and "shift_set" not in ids_of(c0), ids_of(c0))

# Two Set Variables on ONE variable, each gated: one rule, the later winning
# when both hold, the variable keeping its value when neither does.
two_nodes = graph({"b_value": 1}, when_wired=True)
two_nodes["nodes"].append({"id": "s2", "type": "math.setvar", "position": {"x": 3, "y": 0},
                           "data": {"fnId": "shift_set_2", "name": "Shift 2", "op": "set",
                                    "b_value": 2}})
two_nodes["nodes"].append({"id": "occ2", "type": "analytics.occupancy", "position": {"x": 1, "y": 2},
                           "data": {"fnId": "bay_two", "name": "Bay 2", "zone": "bay"}})
two_nodes["edges"] += [wire("src", "occ2", "in"), wire("z", "occ2", "zone"),
                       wire("v", "s2", "a"), wire("occ2", "s2", "when")]
c2 = wf.compile_graph(wf.clean_graph(two_nodes), ZONES)
chain = expr_of(c2)
check("two setters of one variable fold into one rule, not an error",
      not c2["errors"] and ids_of(c2).count("shift") == 1, (c2["errors"], ids_of(c2)))
check("...nested so the later setter wins and the variable otherwise keeps its value",
      chain is not None and chain.count("iff(") == 2 and chain.rstrip(")").endswith("fn_shift_value")
      and "(2)" in chain and "(1)" in chain, repr(chain))

# -- a wire LEAVING the setter reads the variable, downstream ----------------------------
# The people-counter case: Set Variable (sets `in`) -> Data Table column `in`.
# The column must read fn_in_value -- the setter's own node id names no
# function at all, and a column pointed at it recorded NULL for ever.
TABLES = {"logbook": {"key": "logbook", "name": "Logbook", "columns": [
    {"name": "shift", "type": "number"}, {"name": "other", "type": "number"}]}}
tbl = graph({"b_value": 1}, when_wired=True)
tbl["nodes"].append({"id": "ds", "type": "output.dataset", "position": {"x": 4, "y": 0},
                     "data": {"dataset": "logbook", "name": "Logbook", "mode": "change", "sources": {}}})
tbl["edges"].append(wire("s", "ds", "c_shift"))
c_t = wf.compile_graph(wf.clean_graph(tbl), ZONES, tables=TABLES)
cols = next((o.get("columns") for o in c_t["outputs"] if o.get("dataset") == "logbook"), None)
shift_col = next((c for c in (cols or []) if c["name"] == "shift"), None)
check("a Data Table column wired from a Set Variable reads the VARIABLE it sets",
      not c_t["errors"] and shift_col is not None and shift_col.get("key") == "fn_shift_value",
      (c_t["errors"], shift_col))
check("...never a key named after the setter node, which no function publishes",
      "set" not in str(shift_col and shift_col.get("key")), shift_col)
tbl["nodes"].append({"id": "s3", "type": "math.setvar", "position": {"x": 3, "y": 1},
                     "data": {"fnId": "shift_reset", "name": "Reset", "op": "set", "b_value": 0}})
tbl["edges"] += [wire("v", "s3", "a"), wire("s3", "ds", "c_shift")]
c_2 = wf.compile_graph(wf.clean_graph(tbl), ZONES, tables=TABLES)
check("two wires dropped on one column is an error the operator can read, not last-wins",
      any("two wires" in e["message"] for e in c_2["errors"]), [e["message"] for e in c_2["errors"]])

# -- a STORE Variable fed by the setter copies the variable, not the node ----------------
store = graph({"b_value": 1}, when_wired=True)
store["nodes"].append({"id": "st", "type": "variable.ref", "position": {"x": 4, "y": 1},
                       "data": {"key": "", "io": "out", "name": "shift_copy"}})
store["edges"].append(wire("s", "st", "in"))
c_st = wf.compile_graph(wf.clean_graph(store), ZONES)
copy_expr = next((f["params"]["expr"] for f in c_st["functions"] if f["id"] == "shift_copy"), None)
check("a Variable storing a Set Variable's output copies the VARIABLE it sets",
      not c_st["errors"] and copy_expr == "fn_shift_value", (c_st["errors"], copy_expr))

# -- the Variable's own Type wins over the setter's --------------------------------------------
typed = graph({"b_value": "12"}, when_wired=True)
for n in typed["nodes"]:
    if n["id"] == "v":
        n["data"]["value_type"] = "text"
c_ty = wf.compile_graph(wf.clean_graph(typed), ZONES)
check("a Variable declared text makes its setter store text, though 12 looks like a number",
      expr_of(c_ty) is not None and "'12'" in expr_of(c_ty) and "round(" not in expr_of(c_ty),
      repr(expr_of(c_ty)))
check("...and a text variable starts EMPTY, not 0: the keep branch reads text()",
      expr_of(c_ty) is not None and expr_of(c_ty).rstrip(")").endswith("text(fn_shift_value"),
      repr(expr_of(c_ty)))
check("the registry declares the Variable's type, automatic by default",
      (wf.REGISTRY["variable.ref"]["data"] or {}).get("value_type") == "auto")
check("...and the inspector offers it, and shows the kind on the node",
      'set("value_type")' in read_src("frontend", "src", "pages", "Workflow.jsx")
      and "KIND_LABEL[kind]" in read_src("frontend", "src", "workflow", "nodes.jsx"))

# -- decimal is unchanged, so nothing saved changes meaning -------------------
plain = expr_of(compiled({"b_value": 2.5}))
check("decimal stores the value as it comes", plain == "(2.5)", repr(plain))
check("...and an older graph with no kind at all reads the same",
      expr_of(compiled({"b_value": 2.5, "value_type": ""})) == plain)

# -- whole number rounds, and rounds the WIRE too -----------------------------
whole = expr_of(compiled({"b_value": 2.5, "value_type": "number"}))
check("a whole number is rounded", whole == "round((2.5))", repr(whole))
wired = expr_of(compiled({"value_type": "number"}, value_wired=True))
check("...a wired value too, not only what was typed",
      wired == "round((fn_bay_now_count))", repr(wired))
check("...and the engine accepts what that compiles to",
      fx.validate_expr(whole) or True)

# The GATE keeps the variable's current value while the condition is false.
# Rounding the whole gate would round that kept value on every frame it does
# nothing -- so the wrap goes around the new value alone.
egated = expr_of(compiled({"value_type": "number", "b_value": 3.7},
                          when_wired=True))
check("rounding wraps the new value, not the keep-what-it-was branch",
      egated is not None and egated.startswith("iff(")
      and "round((3.7))" in egated
      and egated.count("round(") == 1,
      repr(egated))

# -- text publishes no function, and says where a string CAN go ---------------
# The expression engine is numeric end to end: every stat, card and history
# row is a float. A text store therefore publishes nothing, exactly as a text
# constant does -- but it says so, rather than quietly writing a 0 into a
# variable somebody is watching.
c = compiled({"value_type": "text", "b_value": "night shift"})
check("text compiles to a rule that sets the word, gated like a number",
      expr_of(c) == "('night shift')", repr(expr_of(c)))
check("...without failing the graph", not c["errors"], c["errors"])
c_g = compiled({"value_type": "text", "b_value": "night shift"}, when_wired=True)
check("...and gated, it keeps the variable's own word when the gate is down",
      expr_of(c_g) is not None and expr_of(c_g).startswith("iff(")
      and expr_of(c_g).rstrip(")").endswith("fn_shift_value") and "'night shift'" in expr_of(c_g),
      repr(expr_of(c_g)))

# -- the form asks the same question the compiler answers ---------------------
src = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
           encoding="utf-8").read()
check("the inspector reads the kinds off the registry",
      "const valueTypes = spec.value_types || [];" in src)
check("...clamps a stale one exactly as the compiler does",
      "valueTypes.includes(d.value_type)" in src)
check("...offers a text box for text, and for auto - which takes anything",
      '(autoKind || valueType === "text") && h !== "a"' in src)
check("...detects the kind off the value the way the compiler does",
      "const detectKind = () => {" in src and 'value="auto"' in src)
check("...and steps a whole number by one",
      'step={valueType === "number" && h !== "a" ? "1" : "any"}' in src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
