"""A declared variable's "constant" face: number, boolean, or text.

Authored from the Variables panel's "+ New variable" -> "Constant" checkbox,
not dragged from the palette -- but it still needs a real node underneath
(variable.number, hidden from the palette) to carry a typed value and, for
number/boolean, publish it as a real stat. This pins what that node compiles
to for each type, and the one deliberate limit: TEXT publishes nothing.

Number and boolean are full citizens -- boolean is 1.0/0.0 under the hood,
same coercion the When/expr engine already applies to a JSON boolean, so it
costs nothing new. Text is different in kind, not just degree: the expr
engine's own constant whitelist refuses anything that is not a bool or a
number ("only numbers are allowed as constants"), and every reader of a
variable's value -- formulas, cards, the stats dict itself -- is numeric to
its core. A text constant is named, visible, and editable; it compiles to
NOTHING, because there is nowhere for a string to go yet.

variable.ref (the wired-holder face) stays a separate type for the same
reason it always was: different mechanism, not just a different label.

    python tests/smoke_createvar.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}")
    if not ok:
        print(f"      wanted {want!r}")
        bad += 1


def graph(var_node):
    return wf.clean_graph({"nodes": [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        var_node,
        {"id": "a", "type": "output.alert", "position": {"x": 2, "y": 0},
         "data": {"metric": "value", "op": ">", "value": 0}},
    ], "edges": [{"id": "e1", "source": "v", "target": "a"}]})


# -- labelling and back-compat --------------------------------------------------
check("not offered on the palette -- made from the Variables panel instead",
      wf.REGISTRY["variable.number"].get("hidden"), True)
check("the type string is unchanged (no migration needed)",
      "variable.number" in wf.REGISTRY, True)
check("variable.ref is hidden the same way, same reason",
      wf.REGISTRY["variable.ref"].get("hidden"), True)
check("number, boolean AND text are offered",
      wf.REGISTRY["variable.number"]["value_types"],
      ["number", "boolean", "text"])
# A node saved before value_type existed has neither key -- must read as a
# plain number, exactly as it always did.
check("an old node with no value_type defaults to number",
      wf.REGISTRY["variable.number"]["data"]["value_type"], "number")

# -- a static number compiles as before ------------------------------------------
c = wf.compile_graph(graph({"id": "v", "type": "variable.number",
                            "position": {"x": 1, "y": 0},
                            "data": {"fnId": "thresh", "name": "Threshold",
                                     "value": 4.5}}), [])
check("a plain number compiles clean", c["errors"], [])
fn = next(f for f in c["functions"] if f["id"] == "thresh")
check("...as its own expression", fn["params"]["expr"], "4.5")

# A whole number is written bare, not as "4.0" -- it is read by a human on
# the canvas and in the config, and 4.0 reads as "someone typed a decimal".
c = wf.compile_graph(graph({"id": "v", "type": "variable.number",
                            "position": {"x": 1, "y": 0},
                            "data": {"fnId": "thresh", "name": "Threshold",
                                     "value": 4}}), [])
fn = next(f for f in c["functions"] if f["id"] == "thresh")
check("a whole number has no trailing .0", fn["params"]["expr"], "4")

# -- boolean is a real value, not a lie ------------------------------------------
c = wf.compile_graph(graph({"id": "v", "type": "variable.number",
                            "position": {"x": 1, "y": 0},
                            "data": {"fnId": "flag", "name": "Enabled",
                                     "value": True, "value_type": "boolean"}}), [])
check("a boolean compiles clean", c["errors"], [])
fn = next(f for f in c["functions"] if f["id"] == "flag")
check("true compiles to 1", fn["params"]["expr"], "1")

c = wf.compile_graph(graph({"id": "v", "type": "variable.number",
                            "position": {"x": 1, "y": 0},
                            "data": {"fnId": "flag", "name": "Enabled",
                                     "value": False, "value_type": "boolean"}}), [])
fn = next(f for f in c["functions"] if f["id"] == "flag")
check("false compiles to 0", fn["params"]["expr"], "0")

# -- text compiles to NOTHING, on purpose -----------------------------------------
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "v", "type": "variable.number", "position": {"x": 0, "y": 0},
     "data": {"fnId": "site", "name": "SITE_NAME", "value": "Bay 3",
              "value_type": "text"}},
], "edges": []}), [])
check("a text constant raises no error", c["errors"], [])
check("...and publishes no function (no stat exists to read it from)",
      [f["id"] for f in c["functions"]], [])

# A number and a text constant must not be able to share a name -- the id is
# still claimed even though a text one builds nothing further.
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "a", "type": "variable.number", "position": {"x": 0, "y": 0},
     "data": {"fnId": "dup", "name": "A", "value": "x", "value_type": "text"}},
    {"id": "b", "type": "variable.number", "position": {"x": 1, "y": 0},
     "data": {"fnId": "dup", "name": "B", "value": 5}},
], "edges": []}), [])
check("a text constant's name still collides with a number's",
      len(c["errors"]), 1)

# -- an unwired holder reads an existing value, unchanged ------------------------
# Two nodes: the value it reads, published for real, and the holder pointing
# at it -- not wired to an Alert, which would test input_alias resolution
# rather than the thing this pins (variable.ref still compiles with hidden=True).
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "src", "type": "variable.number", "position": {"x": 0, "y": 0},
     "data": {"fnId": "thresh", "name": "Threshold", "value": 4}},
    {"id": "v", "type": "variable.ref", "position": {"x": 1, "y": 0},
     "data": {"key": "fn_thresh_value", "io": "auto"}},
], "edges": []}), [])
check("an unwired holder (hidden from the palette) still compiles",
      c["errors"], [])

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
