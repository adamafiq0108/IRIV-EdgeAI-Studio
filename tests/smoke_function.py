# A function on the canvas: a Start that hands out parameters, an End that
# takes what comes back, and a Call for every place it is used.
#
# The body is whatever sits BETWEEN the Start and the End on the MAIN canvas,
# which is the whole reason it is drawn this way rather than in a nested
# editor: every node in the palette works inside a function, with its own
# ports, its own inspector and its own wiring rules, because it is the same
# canvas it always was.
#
# Each Call gets its own COPY of the body -- two Calls of a function holding a
# Data Table write two sets of rows, exactly as inlining the body twice would.
# That is what makes a function here mean what it means in a program, and it
# is why every id inside a copy carries the Call's id: the fnId a node
# publishes under, and the name a Link out jumps to, would otherwise collide
# between copies and the two calls would quietly become one.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402

page_src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "frontend", "src", "pages", "Workflow.jsx"),
                   encoding="utf-8").read()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, typ, **data):
    return {"id": nid, "type": typ, "position": {"x": 0, "y": 0}, "data": data}


def edge(src, tgt, sh=None, th=None):
    return {"id": f"e_{src}_{sh or ''}_{tgt}_{th or ''}", "source": src,
            "target": tgt, "sourceHandle": sh, "targetHandle": th}


# A function `rate` of one parameter `n`: round it. Called twice, from two
# different counters, into two different cards.
def graph(calls=2):
    nodes = [
        node("m1", "source.detection"),
        node("f1", "filter.class", classes=["person"]),
        node("c1", "analytics.frame", fnId="one", name="One", mode="realtime"),
        node("c2", "analytics.frame", fnId="two", name="Two", mode="realtime"),
        # -- the function ------------------------------------------------
        node("s1", "function.start", fn="rate", params=["n"]),
        node("r1", "math.round", op="round", fnId="rnd", name="Rate"),
        node("x1", "function.end", fn="rate"),
    ]
    edges = [
        edge("m1", "f1"), edge("f1", "c1"), edge("f1", "c2"),
        edge("s1", "r1", sh="p_n", th="a"),
        edge("r1", "x1"),
    ]
    for i in range(calls):
        src = ["c1", "c2"][i]
        nodes.append(node(f"k{i}", "function.call", fn="rate"))
        nodes.append(node(f"d{i}", "dashboard.card", title=f"Card {i}"))
        edges += [edge(src, f"k{i}", th="p_n"), edge(f"k{i}", f"d{i}")]
    return {"nodes": nodes, "edges": edges}


# -- the splice, before anything is compiled ------------------------------------------
flat = wf.resolve_functions(graph())
types = [n["type"] for n in flat["nodes"]]
check("no Start, End or Call survives the splice",
      not [t for t in types if t.startswith("function.")], types)
check("...nor the body itself: a definition is a recipe, not a run",
      "r1" not in {n["id"] for n in flat["nodes"]},
      [n["id"] for n in flat["nodes"]])
rounds = [n for n in flat["nodes"] if n["type"] == "math.round"]
check("two Calls leave two copies of the body", len(rounds) == 2,
      [n["id"] for n in rounds])
check("...and the copies are wired to their OWN caller",
      sorted(e["source"] for e in flat["edges"]
             if e["target"].endswith("__r1")) == ["c1", "c2"],
      [(e["source"], e["target"]) for e in flat["edges"] if "__r1" in e["target"]])
check("...and each hands its value to its OWN card",
      sorted((e["source"].split("__")[0], e["target"]) for e in flat["edges"]
             if e["target"].startswith("d")) == [("k0", "d0"), ("k1", "d1")],
      [(e["source"], e["target"]) for e in flat["edges"]
       if e["target"].startswith("d")])
check("the argument lands on the parameter's port inside the body, not on port 'in'",
      all(e.get("targetHandle") == "a" for e in flat["edges"]
          if e["target"].endswith("__r1") and e["source"] in ("c1", "c2")),
      [e.get("targetHandle") for e in flat["edges"] if e["target"].endswith("__r1")])

# -- the whole thing compiles ---------------------------------------------------------
out = wf.compile_graph(graph(), [], ["person"], ["person"])
check("a canvas with a function compiles clean", not out["errors"], out["errors"])
fids = [f["id"] for f in out["functions"]]
check("each copy publishes under its own id, so two calls are two values",
      sorted(f for f in fids if f.endswith("rnd")) == ["k0__rnd", "k1__rnd"], fids)

# -- EVERY node works inside a function, outputs included ------------------------------
# The reason the body is the main canvas rather than a nested editor. A Card
# inside a function called twice is two cards, because a Call is a copy of the
# body and not a share of it -- the same thing inlining the body twice would
# do, which is what makes a function here mean what it means in a program.
def with_card():
    g = graph(calls=2)
    g["nodes"] = [n for n in g["nodes"] if not n["id"].startswith("d")]
    g["edges"] = [e for e in g["edges"] if not e["target"].startswith("d")]
    g["nodes"].append(node("in_d", "dashboard.card", title="Inside"))
    g["edges"].append(edge("r1", "in_d"))
    return g


out = wf.compile_graph(with_card(), [], ["person"], ["person"])
check("a Card INSIDE a function body compiles", not out["errors"], out["errors"])
check("...and two calls put two of it on the dashboard",
      len(out["widgets"]) == 2, out["widgets"])
check("...each reading its own call's value, not the same one twice",
      len({w.get("metric") or w.get("key") or str(w) for w in out["widgets"]}) == 2,
      out["widgets"])

# -- an uncalled function is not a running pipeline -----------------------------------
alone = wf.resolve_functions(graph(calls=0))
check("a function nobody calls compiles to nothing at all",
      not [n for n in alone["nodes"] if n["id"] in ("s1", "r1", "x1")],
      [n["id"] for n in alone["nodes"]])
check("...and the rest of the canvas is untouched by it",
      {n["id"] for n in alone["nodes"]} == {"m1", "f1", "c1", "c2"},
      [n["id"] for n in alone["nodes"]])

# -- half a function is a normal state while wiring -----------------------------------
g = graph(calls=1)
g["nodes"] = [n for n in g["nodes"] if n["id"] != "x1"]      # no End yet
half = wf.resolve_functions(g)
check("a Start with no End yet leaves the canvas alone rather than eating it",
      {"m1", "f1", "c1", "c2", "r1"} <= {n["id"] for n in half["nodes"]},
      [n["id"] for n in half["nodes"]])

# -- recursion stops, it does not hang -------------------------------------------------
rec = {"nodes": [node("s1", "function.start", fn="loop", params=["n"]),
                 node("k9", "function.call", fn="loop"),
                 node("x1", "function.end", fn="loop")],
       "edges": [edge("s1", "k9", sh="p_n", th="p_n"), edge("k9", "x1")]}
done = wf.resolve_functions(rec)
check("a function that calls itself stops expanding instead of hanging",
      isinstance(done.get("nodes"), list))

# -- the boundary takes wires the pipeline rules would refuse --------------------------
ok, _ = wf.edge_allowed("dashboard.card", "function.end")
check("anything at all may be a function's return value", ok)
ok, why = wf.edge_allowed("analytics.frame", "function.start")
check("...but a Start receives nothing: a parameter is filled at the Call",
      not ok and "Call" in why, why)
ok, why = wf.edge_allowed("function.end", "dashboard.card")
check("...and an End gives nothing on: the value comes out of the Call",
      not ok and "Call" in why, why)

# -- a function may give back more than one value --------------------------------------
# A function that works out a count AND how long it took has two things to
# say, and one port could only carry one of them. The End names them the way
# the Start names its parameters, and every Call hands them out under the same
# names. An End that names none keeps the single unnamed pair -- which is what
# every function drawn before this uses, so nothing already on a canvas moves.
def two_returns():
    return {"nodes": [
        node("m1", "source.detection"),
        node("f1", "filter.class", classes=["person"]),
        node("c1", "analytics.frame", fnId="src", name="Src", mode="realtime"),
        node("s1", "function.start", fn="pair", params=["n"]),
        node("r1", "math.round", op="round", fnId="rnd", name="Rounded"),
        node("r2", "math.round", op="ceil", fnId="cei", name="Ceiling"),
        node("x1", "function.end", fn="pair", returns=["rounded", "share"]),
        node("k1", "function.call", fn="pair"),
        node("d1", "dashboard.card", title="A"),
        node("d2", "dashboard.card", title="B"),
    ], "edges": [
        edge("m1", "f1"), edge("f1", "c1"),
        edge("s1", "r1", sh="p_n", th="a"), edge("s1", "r2", sh="p_n", th="a"),
        edge("r1", "x1", th="p_rounded"), edge("r2", "x1", th="p_share"),
        edge("c1", "k1", th="p_n"),
        edge("k1", "d1", sh="p_rounded"), edge("k1", "d2", sh="p_share"),
    ]}


flat = wf.resolve_functions(two_returns())
pairs = sorted((e["source"].split("__")[-1], e["target"]) for e in flat["edges"]
               if e["target"] in ("d1", "d2"))
check("each named return comes out of the Call on its own port",
      pairs == [("r1", "d1"), ("r2", "d2")], pairs)
check("...and the two are not crossed or merged",
      len(pairs) == 2 and pairs[0][0] != pairs[1][0], pairs)
out = wf.compile_graph(two_returns(), [], ["person"], ["person"])
check("a two-value function compiles clean", not out["errors"], out["errors"])
check("...and puts both values on the dashboard", len(out["widgets"]) == 2,
      out["widgets"])

# The single unnamed return, unchanged: the plain port on the End meets the
# plain port on the Call whichever of null / "" / "in" / "out" each holds.
flat = wf.resolve_functions(graph(calls=1))
check("an End that names no returns still hands its one value to the Call",
      [(e["source"].split("__")[-1], e["target"]) for e in flat["edges"]
       if e["target"] == "d0"] == [("r1", "d0")],
      [(e["source"], e["target"]) for e in flat["edges"] if e["target"] == "d0"])

face = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
               encoding="utf-8").read()
check("the End draws a port per named return, and the Call hands them out",
      "<SectionLabel color={color}>Returns</SectionLabel>" in face
      and "sourceHandles={rets.length ? rets : undefined}" in face)
check("...read off the End, so a Call cannot disagree with what it returns",
      "returns: (end?.data?.returns || []).filter(Boolean)" in page_src)

# -- renaming a function renames the whole function -------------------------------------
# Start, End and Call are tied by NAME and nothing else -- no id links them, so
# that copying a pair and retyping the name makes a SECOND function. The cost
# is that renaming one half orphans the other: the bracket stops closing, the
# body stops existing, every Call looks for something that is no longer there,
# and nothing errors. So the Start carries the rest with it.
fn = page_src.split("function renameFunction(", 1)[1]
fn = fn.split(chr(10) + "}", 1)[0]
check("renaming a Function Start renames its End and every Call of it",
      '["function.end", "function.call"].includes(n.type)' in fn
      and "fn: to" in fn)
check("...only from the Start: a name typed on a Call means call a DIFFERENT one",
      'before.type !== "function.start"' in fn)
check("...and never onto a name another Start already declares",
      "const taken = nodes.some((n) => n.type === \"function.start\"" in fn
      and "if (taken) return nodes;" in fn)
check("...and the rename runs wherever a node's fields are written",
      "return renameFunction(next, was, fields);" in page_src)

# -- a function you cannot find is one that gets built twice ---------------------------
# The Control drawer lists the functions THIS canvas defines, the way the
# Filters drawer lists the zones drawn on this box: dragging one out is a Call
# already pointed at it, rather than a blank Call with a name to retype.
page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
                encoding="utf-8").read()
menu = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "ContextMenu.jsx"),
                encoding="utf-8").read()
check("the palette lists every function defined on this canvas",
      "const fnHere = Object.values(fnDefs || {})" in page
      and 'cat === "logic" && fnHere.length > 0' in page
      and 'dragProps("function.call", { fn: f.name })' in page)
check("...counted in the drawer's own number, so the two cannot disagree",
      'cat === "logic" ? fnHere.length : 0' in page)
check("...searchable by name and by parameter, like everything else there",
      "f.name.toLowerCase().includes(search)" in page
      and "(f.params || []).some((p) => p.toLowerCase().includes(search))" in page)
check("...and an unfinished one is still listed, saying what it needs",
      '"no Function End yet"' in page)
check("the right-click Insert menu offers the same list",
      'cat === "logic"' in menu and 'type: "function.call"' in menu
      and "extra: { fn: f.name }" in menu)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
