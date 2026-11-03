# The five Switch shapes, and what each one's ports mean.
#
#   compare  IF / ELSE IF / ELSE -- each condition reads its OWN wired value
#            and leaves by its own output; first one that holds wins
#   switch   one value in, one case per output, exact match
#   range    one value in, numeric bands, first that fits
#   boolean  one value in, on / off
#   multi    several values in, ONE combined AND/OR answer
#
# The contract that must not move: branch ids stay <base>_b<index> in row
# order, so a card or an output bound to a branch survives the rows growing.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import workflow as wf                       # noqa: E402

SRC = {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
       "data": {}}


def counter(nid, fid):
    return {"id": nid, "type": "analytics.frame", "position": {"x": 1, "y": 0},
            "data": {"fnId": fid, "name": fid, "mode": "realtime"}}


def switch(data):
    return {"id": "sw", "type": "logic.switch", "position": {"x": 3, "y": 0},
            "data": {"fnId": "route", "name": "Route", **data}}


def compile_with(sw_data, wires):
    """wires: [(counter fn id, target handle or None)]"""
    nodes = [SRC, switch(sw_data)]
    edges = []
    for i, (fid, handle) in enumerate(wires):
        nid = f"c{i}"
        nodes.append(counter(nid, fid))
        edges.append({"id": f"es{i}", "source": "s", "target": nid})
        e = {"id": f"e{i}", "source": nid, "target": "sw"}
        if handle:
            e["targetHandle"] = handle
        edges.append(e)
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), [])


def exprs(c):
    return {f["id"]: f["params"]["expr"]
            for f in c["functions"] if f["id"].startswith("route_b")}


# 1. COMPARE: three conditions, three separate inputs, three outputs + else
c = compile_with(
    {"ctype": "compare", "otherwise": True,
     "rules": [{"input": "in1", "op": ">", "value": 10},
               {"input": "in2", "op": "==", "value": 3},
               {"input": "in3", "op": "<", "value": 5}]},
    [("a", "in1"), ("b", "in2"), ("d", "in3")])
assert not c["errors"], c["errors"]
got = exprs(c)
assert set(got) == {"route_b0", "route_b1", "route_b2", "route_b3"}, sorted(got)
assert got["route_b0"] == "(fn_a_count > 10)", got["route_b0"]
# ...and every later branch is exclusive: "mine holds AND no earlier one did"
assert got["route_b1"] == "(fn_b_count == 3) and not (fn_a_count > 10)", got["route_b1"]
assert "not (fn_b_count == 3)" in got["route_b2"], got["route_b2"]
# the ELSE is "none of them"
assert got["route_b3"] == ("not (fn_a_count > 10) and not (fn_b_count == 3) "
                          "and not (fn_d_count < 5)"), got["route_b3"]
print("== Compare: one condition per input, ordered, exclusive, plus ELSE")

# 1b. a condition may compare against ANOTHER wired value, not just a number
c = compile_with(
    {"ctype": "compare", "otherwise": False,
     "rules": [{"input": "in1", "op": ">", "rhs_input": "in2"}]},
    [("a", "in1"), ("b", "in2")])
assert not c["errors"], c["errors"]
assert exprs(c)["route_b0"] == "(fn_a_count > fn_b_count)", exprs(c)
print("== Compare: a condition can read both sides from wires")

# 1c. a condition pointing at an empty port is refused, not silently zero
c = compile_with(
    {"ctype": "compare",
     "rules": [{"input": "in1", "op": ">", "value": 1},
               {"input": "in4", "op": ">", "value": 1}]},
    [("a", "in1")])
assert [e for e in c["errors"] if "nothing wired" in e["message"]], c["errors"]
print("== Compare: a condition with no wire on its port is an error")

# 1d. BACKWARD COMPATIBILITY: a Compare saved before per-row inputs -- one
#     rule, no `input`, one wire with no handle -- compiles to exactly the
#     true/false pair it always did, under the same two ids.
c = compile_with({"ctype": "compare", "rules": [{"op": ">", "value": 2}]},
                 [("a", None)])
assert not c["errors"], c["errors"]
got = exprs(c)
assert got["route_b0"] == "(fn_a_count > 2)", got
assert got["route_b1"] == "not (fn_a_count > 2)", got
print("== Compare: a node saved before this compiles byte-for-byte as before")

# 1e. A row carries MORE tests: "if in1 > 0 AND in2 == 0" is one condition,
#     one output -- and the whole compound term is what later branches
#     exclude.
c = compile_with(
    {"ctype": "compare", "otherwise": True,
     "rules": [{"input": "in1", "op": ">", "value": 0,
                "more": [{"link": "and", "input": "in2", "op": "==",
                          "value": 0}]},
               {"input": "in2", "op": ">", "value": 0}]},
    [("a", "in1"), ("b", "in2")])
assert not c["errors"], c["errors"]
got = exprs(c)
assert got["route_b0"] == "((fn_a_count > 0) and (fn_b_count == 0))", got
assert got["route_b1"] == ("(fn_b_count > 0) and not "
                           "((fn_a_count > 0) and (fn_b_count == 0))"), got
print("== a compound row is one condition; later branches exclude all of it")

# 1f. ...OR links fold left, and a sub-test can read a wired right-hand side.
c = compile_with(
    {"ctype": "compare", "otherwise": False,
     "rules": [{"input": "in1", "op": ">", "value": 5,
                "more": [{"link": "or", "input": "in2", "op": ">",
                          "rhs_input": "in1"}]}]},
    [("a", "in1"), ("b", "in2")])
assert not c["errors"], c["errors"]
assert exprs(c)["route_b0"] ==     "((fn_a_count > 5) or (fn_b_count > fn_a_count))", exprs(c)
print("== OR links fold left; a sub-test compares against a wired value")

# 2. SWITCH: cases on ONE value, exact match, plus default
c = compile_with(
    {"ctype": "switch", "otherwise": True,
     "rules": [{"op": "==", "value": 1}, {"op": "==", "value": 2},
               {"op": "==", "value": 3}]},
    [("a", None)])
assert not c["errors"], c["errors"]
got = exprs(c)
assert len(got) == 4, sorted(got)
assert got["route_b0"] == "(fn_a_count == 1)", got["route_b0"]
assert got["route_b1"] == "(fn_a_count == 2) and not (fn_a_count == 1)", got["route_b1"]
print("== Switch: one case per output, exact match, first match wins")

# 2b. cases route ONE value: two wires is refused, and the message names the
#     two shapes that DO read several
c = compile_with({"ctype": "switch", "rules": [{"op": "==", "value": 1}]},
                 [("a", None), ("b", "in2")])
msg = c["errors"][0]["message"]
assert "If / Else if" in msg and "Multi-condition" in msg, msg
print("== Switch: a second value is refused, naming If/Else if and AND/OR")

# 3. RANGE: bands, first that fits, plus outside-every-range
c = compile_with(
    {"ctype": "range", "otherwise": True,
     "rules": [{"op": "between", "value": 0, "value2": 10},
               {"op": "between", "value": 10, "value2": 20}]},
    [("a", None)])
assert not c["errors"], c["errors"]
got = exprs(c)
assert got["route_b0"] == "((fn_a_count >= 0) and (fn_a_count < 10))", got["route_b0"]
assert len(got) == 3, sorted(got)
print("== Range: bands tile, first fit wins, default for outside")

# 3b. a band whose 'to' is not above its 'from' is refused
c = compile_with({"ctype": "range",
                  "rules": [{"op": "between", "value": 10, "value2": 5}]},
                 [("a", None)])
assert c["errors"], c
print("== Range: an upside-down band is refused")

# 4. BOOLEAN: two outputs, nothing to add
c = compile_with({"ctype": "boolean", "rules": [{"op": "!=", "value": 0}]},
                 [("a", None)])
assert not c["errors"], c["errors"]
got = exprs(c)
assert len(got) == 2 and got["route_b0"] == "(fn_a_count != 0)", got
print("== Boolean: on / off, and only those two")

# 5. MULTI: several inputs, ONE combined answer
c = compile_with(
    {"ctype": "multi", "join": "and", "otherwise": True,
     "rules": [{"input": "in1", "op": ">", "value": 0},
               {"input": "in2", "op": ">", "value": 5},
               {"input": "in3", "op": "<", "value": 9}]},
    [("a", "in1"), ("b", "in2"), ("d", "in3")])
assert not c["errors"], c["errors"]
got = exprs(c)
assert len(got) == 2, sorted(got)          # pass + fail, not one per condition
assert got["route_b0"] == ("(fn_a_count > 0) and (fn_b_count > 5) "
                           "and (fn_d_count < 9)"), got["route_b0"]
assert got["route_b1"].startswith("not ("), got["route_b1"]
print("== Multi-condition: three inputs, one AND answer (and its inverse)")

c = compile_with(
    {"ctype": "multi", "join": "or", "otherwise": False,
     "rules": [{"input": "in1", "op": ">", "value": 0},
               {"input": "in2", "op": ">", "value": 0}]},
    [("a", "in1"), ("b", "in2")])
assert exprs(c)["route_b0"] == "(fn_a_count > 0) or (fn_b_count > 0)", exprs(c)
print("== Multi-condition: ANY joins with OR")

# 5c. A BRANCH IS A VALUE: wired into a Formula it reads 1 while that branch
#     holds, so "if busy then X else Y" is arithmetic, not a repeated test.
g = wf.clean_graph({
    "nodes": [SRC, counter("c0", "a"),
              switch({"ctype": "compare", "otherwise": True,
                      "rules": [{"input": "in1", "op": ">", "value": 10}]}),
              {"id": "fm", "type": "analytics.formula",
               "position": {"x": 5, "y": 0},
               "data": {"fnId": "pick", "name": "Pick", "op": "mul",
                        "b_value": 100}}],
    "edges": [{"id": "e0", "source": "s", "target": "c0"},
              {"id": "e1", "source": "c0", "target": "sw",
               "targetHandle": "in1"},
              {"id": "e2", "source": "sw", "target": "fm",
               "sourceHandle": "out0", "targetHandle": "a"}]})
c = wf.compile_graph(g, [])
assert not c["errors"], c["errors"]
pick = next(f for f in c["functions"] if f["id"] == "pick")
assert pick["params"]["expr"] == "(fn_route_b0_value * 100)", pick["params"]
print("== a branch feeds a Formula as its own 0/1 value")

# 6. The default node is the SMALLEST useful thing: one case and a default.
data = wf.REGISTRY["logic.switch"]["data"]
assert data["ctype"] == "switch" and len(data["rules"]) == 1, data
assert data["rules"][0]["op"] == "==", data
print("== a new Switch starts as one case plus its default")
print("ALL OK")
