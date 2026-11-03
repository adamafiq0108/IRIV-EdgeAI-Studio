# A Custom Formula grows one input port per name its expression refers to.
#
# The wire used to be matched by the SOURCE NODE's own name: whatever was
# wired in became usable in the expression under whatever that node happened
# to be called. Which meant renaming a node three drawers away silently turned
# `capacity` into a word nothing published -- and an unknown name reads as
# zero, for ever, with nothing on either face to show it had happened.
#
# A named port moves the name onto the FORMULA, where the operator typed it.
# The old behaviour stays for every wire already drawn: those record no handle
# at all, and match by name exactly as before.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, typ, **data):
    return {"id": nid, "type": typ, "position": {"x": 0, "y": 0}, "data": data}


def edge(src, tgt, th=None):
    return {"id": f"e{src}{tgt}{th or ''}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": th}


# Two counters and a formula over both. `head` is wired to the port called
# head, `tail` to the port called tail -- and NEITHER node is named that, so
# the only thing that can be binding them is the port.
def graph(port_a="p_head", port_b="p_tail", expr="head / tail * 100"):
    return {"nodes": [
        node("m1", "source.detection"),
        node("f1", "filter.class", classes=["person"]),
        node("c1", "analytics.frame", fnId="alpha", name="Alpha", mode="realtime"),
        node("c2", "analytics.frame", fnId="beta", name="Beta", mode="realtime"),
        node("x1", "analytics.expr", fnId="pct", name="Share", expr=expr),
        node("d1", "dashboard.card", title="Share"),
    ], "edges": [
        edge("m1", "f1"), edge("f1", "c1"), edge("f1", "c2"),
        edge("c1", "x1", th=port_a), edge("c2", "x1", th=port_b),
        edge("x1", "d1"),
    ]}


def compiled_expr(g):
    out = wf.compile_graph(g, [], ["person"], ["person"])
    assert not out["errors"], out["errors"]
    for f in out["functions"]:
        if f["id"] == "pct":
            return f["params"]["expr"]
    return None


got = compiled_expr(graph())
check("a name on a PORT binds that name, whatever the source node is called",
      got == "fn_alpha_count / fn_beta_count * 100", got)

# The same two wires, ports swapped. If the binding were by source name this
# would compile to the same statement; it must not.
got = compiled_expr(graph(port_a="p_tail", port_b="p_head"))
check("...so swapping the two wires swaps the two values",
      got == "fn_beta_count / fn_alpha_count * 100", got)

# Renaming the node that feeds a port changes nothing: that is the bug this
# fixes. `alpha` keeps its fnId (the published key) and gets a new label.
g = graph()
for n in g["nodes"]:
    if n["id"] == "c1":
        n["data"]["name"] = "Renamed Half Way Through A Shift"
got = compiled_expr(g)
check("...and renaming the node that feeds it cannot break the formula",
      got == "fn_alpha_count / fn_beta_count * 100", got)

# Every wire drawn before the ports existed carries no handle, and still
# resolves by the source's name, exactly as it always did.
got = compiled_expr(graph(port_a=None, port_b=None, expr="alpha / beta * 100"))
check("a wire with no port at all still matches by name, as every saved one does",
      got == "fn_alpha_count / fn_beta_count * 100", got)

# An expression naming something nothing is wired to is left ALONE rather than
# rewritten: a half-typed formula is a normal state, and quietly substituting
# a near-miss would be worse than leaving the operator's own words in place.
got = compiled_expr(graph(expr="head / missing"))
check("a name with nothing on its port is left as the operator wrote it",
      got == "fn_alpha_count / missing", got)

# -- the face -------------------------------------------------------------------------
ed = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "ExprEditor.jsx"),
             encoding="utf-8").read()
nodes_js = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
                   encoding="utf-8").read()
check("the names are read off the expression in one place, shared with the node",
      "export function exprNames(" in ed and "export const EXPR_RESERVED" in ed
      and "exprNames(d.expr)" in nodes_js)
check("...and the engine's own words never become ports",
      'FUNCTIONS.map((f) => f.key.split("(")[0])' in ed and '"and", "or", "not",' in ed)
check("the plain port stays FIRST, so every wire already drawn still lands on it",
      '[{ id: "in", label: "any" }, ...exprPorts]' in nodes_js)

# -- the wire follows the name --------------------------------------------------------
# Clicking a name under "Wired in - use these names" grows a port for it, and
# the wire that already carries that value was left on the plain `any` port --
# so one value sat in two places on one face, the named port looking unwired
# and the unnamed one looking like it fed something anonymous.
page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
               encoding="utf-8").read()
check("an unnamed wire retargets itself onto the port for the name it answers to",
      "if (n.type !== \"analytics.expr\") continue;" in page
      and "moves.set(e.id, slot);" in page
      and 'const slot = `p_${alias.name}`;' in page)
check("...only when the port is free, so it never steals another wire's place",
      'if (edges.some((x) => x.target === n.id && x.targetHandle === slot)) continue;'
      in page)
check("...and only from the unnamed port, so a moved wire settles",
      'if (held && held !== "in") continue;' in page)
check("what a node answers to is worked out in ONE place, shared with the inspector",
      page.count("function inputAlias(") == 1
      and page.count("inputAlias(n, registry)") >= 1
      and "inputAlias(byId.get(e.source), registry)" in page)

# -- and a Function Start hands its parameters OUT ------------------------------------
# A parameter is what the BODY takes. The value for it arrives at the Call,
# which is where an operator asks for the function, so there is nothing to
# wire into the definition itself -- a port offering to receive one could only
# be misread.
start = nodes_js.split('if (props.type === "function.start") {', 1)[1]
start = start.split("// A Call.", 1)[0]
check("the body takes a parameter from a port on the RIGHT of the Function Start",
      'type="source" position={Position.Right} id={prm.id}' in start)
check("...and the Start offers nothing to wire INTO",
      'type="target"' not in start and "targetHandles={[]}" in start)
check("...under a heading that says what they are",
      "<SectionLabel color={color}>Parameters</SectionLabel>" in start)
check("a Call's ports are headed Arguments", 'targetHeading="Arguments"' in nodes_js)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
