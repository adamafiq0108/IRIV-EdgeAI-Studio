# A graph with duplicate node ids must still OPEN, and must stop being made.
#
# One system reached four identical Alert nodes sharing one id and its
# Workflow page died outright -- "internal error - see Logs", a blank canvas,
# and the only tool that could have repaired the graph was the editor that
# would not open. Two separate faults, fixed separately:
#
#   the cause      migrate_inline_triggers derives the Alert's id from the
#                  rule carrying the trigger. Deriving it is right -- that is
#                  what stops every load minting another Alert -- but it
#                  appended without checking, so a second pass over a graph
#                  that still had an inline trigger produced a second node
#                  under the SAME id.
#
#   the blast      clean_graph RAISED on a duplicate, and the raise reached
#                  the route as a 500. A malformed graph should cost the
#                  operator the bad node, not the page.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import workflow as wf                      # noqa: E402


def rule(nid, trigger=None):
    d = {"classes": ["person"]}
    if trigger:
        d["trigger"] = trigger
    return {"id": nid, "type": "analytics.object_zone",
            "position": {"x": 0, "y": 0}, "data": d}


TRIG = {"metric": "count", "op": ">", "value": 0, "severity": "warn",
        "message": "object in Zone 1", "cooldown_s": 2}

# 1. A duplicate id no longer takes the whole graph down: the first wins, the
#    rest are dropped, exactly as an unknown node type already is.
g = wf.clean_graph({
    "nodes": [rule("a"), rule("a"), rule("b")],
    "edges": [],
})
ids = [n["id"] for n in g["nodes"]]
assert ids == ["a", "b"], f"duplicates must be dropped, not fatal: {ids}"

# 2. A blank id is dropped too, for the same reason.
g = wf.clean_graph({"nodes": [rule(""), rule("b")], "edges": []})
assert [n["id"] for n in g["nodes"]] == ["b"]

# 3. Edges left dangling by a dropped node go with it, rather than pointing at
#    something no longer there.
g = wf.clean_graph({
    "nodes": [rule("a"), rule("")],
    "edges": [{"id": "e1", "source": "a", "target": ""}],
})
assert g["edges"] == [], "an edge to a dropped node must not survive"

# 4. The migration is IDEMPOTENT: running it twice must not mint a second
#    Alert under the same id. This is the exact shape that produced four.
graph = {"nodes": [rule("zn_x", TRIG)], "edges": []}
assert wf.migrate_inline_triggers(graph) is True, "first pass must migrate"
after_one = len(graph["nodes"])
assert after_one == 2, "the rule plus its new Alert"

# Put the trigger back -- which is what kept happening -- and run again.
graph["nodes"][0]["data"]["trigger"] = dict(TRIG)
wf.migrate_inline_triggers(graph)
ids = [n["id"] for n in graph["nodes"]]
assert len(ids) == len(set(ids)), f"a second pass duplicated an id: {ids}"
assert len(graph["nodes"]) == after_one, \
    "a second pass must not add another Alert"

# 5. ...and that second pass still CLEARS the inline trigger, or the rule
#    raises its event twice: once inline, once through the Alert.
assert "trigger" not in graph["nodes"][0]["data"], \
    "the inline trigger must be cleared even when the Alert already exists"

# 6. Clearing alone counts as a change, so the caller saves it.
graph2 = {"nodes": [rule("zn_y", TRIG)], "edges": []}
wf.migrate_inline_triggers(graph2)
graph2["nodes"][0]["data"]["trigger"] = dict(TRIG)
assert wf.migrate_inline_triggers(graph2) is True, \
    "a cleared trigger is a change the caller must persist"

# 7. Whatever the migration produces must survive clean_graph -- the two
#    disagreeing is what put a system in this state.
wf.clean_graph(graph)

print("smoke_dupnodes OK")
