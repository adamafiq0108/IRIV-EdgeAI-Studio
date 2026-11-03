# Several rules writing ONE Variable: the one that is firing supplies the
# value, and the variable keeps what it has when none of them is.
#
# Reported from a live board on 2026-09-24. An operator wanted one card that
# reads "Chair Detected", "People Detected" or "No Object Detected". They
# built an If / Else if, gave each branch a Set Variable, and funnelled those
# setters into one Variable feeding the card -- which is what the shape
#
#     if chair:      what_is_there = "Chair Detected"
#     elif person:   what_is_there = "People Detected"
#     else:          what_is_there = "No Object Detected"
#
# looks like when it is drawn instead of typed.
#
# The compiler took the first wire it resolved and dropped the rest in
# silence. So the card aliased the chair setter alone, and a Set Variable
# keeps its old value when its branch is not holding -- which is right -- so
# the card read "Chair Detected" for ever with an empty frame. Nothing on the
# canvas, in Problems or in the log said a wire had been ignored, which is
# what made it cost an afternoon rather than a minute.
#
# A variable with several writers now compiles to the branch chain it stands
# for: whichever writer is FIRING supplies the value, and the variable keeps
# it when none of them is, so a word put up by a one-frame event stays on the
# card instead of blinking once. When two writers fire in the same frame the
# last one wins, matching how several Set Variables SHARING one variable
# already fold -- an operator can draw this logic either way, and the two
# pictures have to answer the same.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(msg, passed, detail=""):
    global bad
    if passed:
        print("ok   " + msg)
    else:
        print("FAIL " + msg + ("\n      " + str(detail) if detail else ""))
        bad += 1


def build(nodes, edges):
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def setter(nid, fnid, word):
    return {"id": nid, "type": "math.setvar", "position": {"x": 3, "y": 0},
            "data": {"fnId": fnid, "op": "set", "value_type": "text",
                     "a_value": 0, "b_value": word, "c_value": 0,
                     "name": "Set Variable"}}


def holder(nid, key, io="in", name=None):
    d = {"key": key, "io": io, "value_type": "text"}
    if name:
        d["name"] = name
    return {"id": nid, "type": "variable.ref", "position": {"x": 4, "y": 0},
            "data": d}


def wire(src, tgt, handle=None, sh=None):
    return {"id": "e_%s_%s_%s" % (src, tgt, handle or "in"),
            "source": src, "target": tgt,
            "sourceHandle": sh, "targetHandle": handle or "in"}


def front(rules=None, otherwise=True):
    """The operator's front end: a stream, two watched classes, one branch."""
    return [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "cf", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person", "chair"]}},
        {"id": "obj", "type": "analytics.object", "position": {"x": 2, "y": 0},
         "data": {"fnId": "od", "name": "Object Detection"}},
        {"id": "br", "type": "branch.cases", "position": {"x": 3, "y": 1},
         "data": {"fnId": "ife", "ctype": "compare", "name": "If / Else if",
                  "rules": rules or [{"input": "in1", "op": ">", "value": 0},
                                     {"input": "in2", "op": ">", "value": 0}],
                  "otherwise": otherwise}},
    ], [
        wire("s", "cf"), wire("cf", "obj"),
        wire("obj", "br", "in1", "chair"), wire("obj", "br", "in2", "person"),
    ]


# ===========================================================================
# 1. THE OPERATOR'S GRAPH, as drawn: three setters funnelled into one Variable
# ===========================================================================
nodes, edges = front()
nodes += [setter("sv1", "sv_chair", "Chair Detected"),
          setter("sv2", "sv_people", "People Detected"),
          setter("sv3", "sv_none", "No Object Detected"),
          holder("h1", "fn_chair_word_value"),
          holder("h2", "fn_people_word_value"),
          holder("h3", "fn_none_word_value"),
          holder("store", "fn_what_value", io="auto", name="what"),
          {"id": "card", "type": "dashboard.card", "position": {"x": 6, "y": 0},
           "data": {"name": "What is there", "kind": "tile", "format": "text"}}]
edges += [wire("h1", "sv1", "a"), wire("h2", "sv2", "a"), wire("h3", "sv3", "a"),
          wire("br", "sv1", "when", "out0"),
          wire("br", "sv2", "when", "out1"),
          wire("br", "sv3", "when", "out2"),
          wire("sv1", "store"), wire("sv2", "store"), wire("sv3", "store"),
          wire("store", "card")]
c = wf.compile_graph(build(nodes, edges), [])

check("the graph an operator draws compiles clean",
      not c["errors"] and not c["warnings"], (c["errors"], c["warnings"]))
fns = {f["id"]: f for f in c["functions"]}
expr = fns["what"]["params"]["expr"]
check("...and the variable answers from every writer, not just the first",
      expr.count("iff(") == 3, expr)
for key in ("fn_chair_word_value", "fn_people_word_value", "fn_none_word_value"):
    check("...including " + key, key in expr, expr)
check("...and keeps its own value when no writer is firing",
      "fn_what_value" in expr, expr)
# An unset TEXT variable must read as empty text, not as the 0 an unknown
# name is, or the first frame shows "0" on a card that only ever holds words.
check("...as empty text before anything has written it",
      "text(fn_what_value)" in expr, expr)
card = [w for w in c["widgets"] if w.get("kind") == "tile"]
check("the card reads that variable, as words",
      card and card[0].get("key") == "fn_what_value"
      and card[0].get("format") == "text", card)

# ===========================================================================
# 2. TWO WRITERS FIRING AT ONCE: the last one wins
# ===========================================================================
# Branch gates are mutually exclusive, so this only shows up when writers are
# gated on conditions that CAN both hold -- pinned here with two rules that
# overlap on purpose. The answer has to match the fold that several Set
# Variables sharing one variable already compile to, or the same logic drawn
# two ways answers differently.
nodes, edges = front(rules=[{"input": "in1", "op": ">=", "value": 0},
                            {"input": "in2", "op": ">=", "value": 0}],
                     otherwise=False)
nodes += [setter("first", "sv_first", "FIRST"),
          setter("second", "sv_second", "SECOND"),
          holder("hf", "fn_first_word_value"),
          holder("hs", "fn_second_word_value"),
          holder("store", "fn_what_value", io="auto", name="what")]
edges += [wire("hf", "first", "a"), wire("hs", "second", "a"),
          wire("br", "first", "when", "out0"),
          wire("br", "second", "when", "out1"),
          wire("first", "store"), wire("second", "store")]
c2 = wf.compile_graph(build(nodes, edges), [])
e2 = {f["id"]: f for f in c2["functions"]}["what"]["params"]["expr"]
check("the last writer is the OUTERMOST test, so it is asked first",
      e2.index("fn_second_word_value") < e2.index("fn_first_word_value"), e2)

# ===========================================================================
# 3. one writer is still a plain copy
# ===========================================================================
# Every canvas in the field has single-writer stores on it. They must compile
# to exactly the alias they always did, not to a one-armed chain.
nodes, edges = front()
nodes += [setter("sv1", "sv_chair", "Chair Detected"),
          holder("h1", "fn_chair_word_value"),
          holder("store", "fn_what_value", io="auto", name="what")]
edges += [wire("h1", "sv1", "a"), wire("br", "sv1", "when", "out0"),
          wire("sv1", "store")]
c3 = wf.compile_graph(build(nodes, edges), [])
e3 = {f["id"]: f for f in c3["functions"]}["what"]["params"]["expr"]
check("a single writer still compiles to the bare key it always did",
      e3 == "fn_chair_word_value", e3)

# ===========================================================================
# 4. an UNGATED writer wins outright
# ===========================================================================
# A writer with nothing on its run port fires every frame, so it is always
# the latest and no writer above it can ever win. Pinned with the ungated one
# second: the gated writer above it is unreachable, and the chain says so by
# collapsing to the ungated key rather than drawing a dead upper half.
nodes, edges = front()
nodes += [setter("sv2", "sv_people", "People Detected"),
          setter("always", "sv_always", "ALWAYS"),
          holder("ha", "fn_always_word_value"),
          holder("h2", "fn_people_word_value"),
          holder("store", "fn_what_value", io="auto", name="what")]
edges += [wire("ha", "always", "a"), wire("h2", "sv2", "a"),
          wire("br", "sv2", "when", "out1"),
          wire("always", "store"), wire("sv2", "store")]
c4 = wf.compile_graph(build(nodes, edges), [])
e4 = {f["id"]: f for f in c4["functions"]}["what"]["params"]["expr"]
check("an ungated writer swallows every writer above it",
      e4 == "fn_always_word_value", e4)

# ===========================================================================
# 5. THE WORDS ACTUALLY TAKE TURNS, through the app's own engine
# ===========================================================================
# A plausible expression is not a working box. This runs the compiled chain
# through the evaluator that runs on the board, in the order the engine
# evaluates its rules, including the case the operator reported: after a chair
# has been seen, an empty frame must go BACK to "No Object Detected".
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app.functions import compile_expr, eval_expr                # noqa: E402

trees = {f["id"]: compile_expr(f["params"]["expr"])
         for f in c["functions"] if f["type"] == "expr"}
order = ["ife_b0", "ife_b1", "ife_b2",
         "chair_word", "people_word", "none_word", "what"]
order = [f for f in order if f in trees] or list(trees)
v = {k: "" for k in ("fn_chair_word_value", "fn_people_word_value",
                     "fn_none_word_value", "fn_what_value")}
seen = []
for chair, person in ((0, 0), (1, 0), (0, 1), (0, 0)):
    v["fn_od_chair"] = chair
    v["fn_od_person"] = person
    for fid in order:
        val = eval_expr(trees[fid], v)
        v["fn_%s_value" % fid] = val if isinstance(val, str) else float(val)
    seen.append(v["fn_what_value"])
check("an empty frame says no object", seen[0] == "No Object Detected", seen)
check("a chair says chair", seen[1] == "Chair Detected", seen)
check("a person says people", seen[2] == "People Detected", seen)
check("...and an empty frame goes BACK to no object, which is the report",
      seen[3] == "No Object Detected", seen)

print()
if bad:
    print("%d check(s) failed" % bad)
    raise SystemExit(1)
print("ALL OK")
