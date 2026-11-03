"""Switch routes on any wired value, not only a raw analytics function.

Before this, Switch's input resolution required the wired source to have
compiled to a fn_meta entry -- which excluded variable.ref (a Variable node
read straight through, unwired), the one node whose whole job is "read a
named value that already exists". Wiring one into a Switch failed with
"needs exactly one value wired in (has 0)" even though a value plainly was.

Fixed by resolving through input_alias, the same general resolver a Formula's
wired operand or a Running Total already uses -- so this also pins that
named-metric-port wiring (Frame Counter's "new", a zone's "pid") still picks
the right metric rather than always the first one.

    python tests/smoke_switchinput.py
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


def switch_node(rules=None):
    return {"id": "sw", "type": "logic.switch", "position": {"x": 2, "y": 0},
            "data": {"fnId": "route", "name": "Route",
                     "rules": rules or [{"op": ">", "value": 5}],
                     "otherwise": True}}


# -- a wired analytics function still works, unchanged --------------------------
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "c", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "realtime"}},
    switch_node(),
], "edges": [{"id": "e1", "source": "s", "target": "c"},
             {"id": "e2", "source": "c", "target": "sw"}]}), [])
check("a wired analytics function compiles clean", c["errors"], [])
route = next(f for f in c["functions"] if f["id"] == "route_b0")
check("...on its own metric key", "fn_crowd_count" in route["params"]["expr"], True)

# -- the fix: a Variable (read an existing value, unwired) now works too --------
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "v", "type": "variable.number", "position": {"x": 0, "y": 0},
     "data": {"fnId": "thresh", "name": "THRESHOLD", "value": 4}},
    {"id": "r", "type": "variable.ref", "position": {"x": 1, "y": 0},
     "data": {"key": "fn_thresh_value", "io": "in"}},
    switch_node(),
], "edges": [{"id": "e1", "source": "r", "target": "sw"}]}), [])
check("a Variable wired into Switch compiles clean (used to be refused)",
      c["errors"], [])
route = next(f for f in c["functions"] if f["id"] == "route_b0")
check("...routing on the value it reads",
      "fn_thresh_value" in route["params"]["expr"], True)

# -- a named metric port still picks the right metric, not the default ----------
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "c", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "distinct", "pid": True}},
    switch_node(),
], "edges": [{"id": "e1", "source": "s", "target": "c"},
             {"id": "e2", "source": "c", "target": "sw", "sourceHandle": "new"}]}),
    [])
check("a wire on a named port compiles clean", c["errors"], [])
route = next(f for f in c["functions"] if f["id"] == "route_b0")
check("...routing on THAT metric, not count (the default)",
      "fn_crowd_new" in route["params"]["expr"], True)

# -- still exactly one wire required ---------------------------------------------
c = wf.compile_graph(wf.clean_graph({"nodes": [switch_node()], "edges": []}), [])
# Nothing wired at all is a DRAFT now -- switched off and dim, not red. The
# "needs a value wired in" error still exists for a node that IS wired but
# wired wrong (two values into a one-value switch, below).
check("nothing wired is switched off, not refused", c["errors"], [])
check("...and says so", any("switched off" in w["message"] for w in c["warnings"]),
      True)

# -- SEVERAL wires: not a count to report, a shape to point at ------------------
# Compare/switch/range/boolean each route ONE value, so two wires into one is
# the operator asking for the shape that COMBINES them -- the message has to
# name that shape, or the only visible way out is deleting a wire.
def crowd(nid, fid):
    return {"id": nid, "type": "analytics.frame", "position": {"x": 1, "y": 0},
            "data": {"fnId": fid, "name": fid, "mode": "realtime"}}


SRC = {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
       "data": {}}
two = wf.compile_graph(wf.clean_graph({
    "nodes": [SRC, crowd("c1", "one"), crowd("c2", "two"), switch_node()],
    "edges": [{"id": "e0", "source": "s", "target": "c1"},
              {"id": "e1", "source": "s", "target": "c2"},
              {"id": "e2", "source": "c1", "target": "sw"},
              {"id": "e3", "source": "c2", "target": "sw"}]}), [])
check("two wires into a one-value Switch is refused", len(two["errors"]), 1)
check("...and the message names the shape that takes several",
      "Multi-condition" in two["errors"][0]["message"], True)

# ...and that shape compiles with both wired in, joined by AND.
sw = switch_node()
sw["data"].update({"ctype": "multi", "join": "and",
                   "rules": [{"input": "in1", "op": ">", "value": 0},
                             {"input": "in2", "op": ">", "value": 0}]})
multi = wf.compile_graph(wf.clean_graph({
    "nodes": [SRC, crowd("c1", "one"), crowd("c2", "two"), sw],
    "edges": [{"id": "e0", "source": "s", "target": "c1"},
              {"id": "e1", "source": "s", "target": "c2"},
              {"id": "e2", "source": "c1", "target": "sw", "targetHandle": "in1"},
              {"id": "e3", "source": "c2", "target": "sw", "targetHandle": "in2"}]}), [])
check("Multi-condition takes both values", multi["errors"], [])
passed = [f for f in multi["functions"] if f["id"] == "route_b0"]
check("...joining them with AND",
      bool(passed) and " and " in passed[0]["params"]["expr"], True)


# -- two Switches, both still called "Switch" -----------------------------------
# They used to compile to the same branch ids (the stem was a slug of the
# NAME), so the second one broke the whole save with "duplicate function id".
# New nodes carry a minted fnId; an OLD graph with two unnamed ones must still
# compile, and whoever claimed a stem first keeps it so nothing already bound
# to a card or to history moves.
def bare_switch(nid):
    return {"id": nid, "type": "logic.switch", "position": {"x": 3, "y": 0},
            "data": {"name": "Switch", "ctype": "compare",
                     "rules": [{"op": ">", "value": 0}], "otherwise": True}}


dup = wf.compile_graph(wf.clean_graph({
    "nodes": [SRC, crowd("c1", "one"), crowd("c2", "two"),
              bare_switch("sw1"), bare_switch("sw2")],
    "edges": [{"id": "e0", "source": "s", "target": "c1"},
              {"id": "e1", "source": "s", "target": "c2"},
              {"id": "e2", "source": "c1", "target": "sw1"},
              {"id": "e3", "source": "c2", "target": "sw2"}]}), [])
check("two same-named Switches still compile", dup["errors"], [])
ids = sorted(f["id"] for f in dup["functions"] if f["id"].startswith("switch"))
check("...as separate rules", len(set(ids)) == len(ids) and len(ids) >= 4, True)
check("...the first keeping the plain stem", "switch_b0" in ids, True)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
