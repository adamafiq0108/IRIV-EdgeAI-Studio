# Two wiring truths the dashboard compile has to hold:
#
# 1. Renaming a table column must not freeze the dashboard. The old wire
#    (o_<old name>) is a leftover, not intent: when the operator has already
#    wired the new column, the new wire wins with a warning; when every wire
#    is stale, the card waits switched off. Erroring here blocked
#    apply_artifacts for the WHOLE canvas -- one renamed column and no card
#    updated again.
#
# 2. A chart can draw more than one value. Several rules wired into a
#    line/area/spline/bar/pie become one series each; a tile still insists on
#    exactly one wire, because it shows one number.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "live_counting", "type": "number"},
    {"name": "frame_pid", "type": "text"}]}}


def compile_(nodes, edges):
    g = {"nodes": nodes, "edges": edges}
    return wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)


BASE = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "in_frame", "name": "In frame", "mode": "realtime",
              "pid": True}},
    {"id": "ds", "type": "output.dataset", "position": {"x": 2, "y": 0},
     "data": {"dataset": "counting", "name": "Counting", "mode": "change",
              "sources": {}}},
]
BASE_EDGES = [
    {"id": "e1", "source": "s", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "ds", "targetHandle": "c_live_counting"},
    {"id": "e3", "source": "fr", "sourceHandle": "pid", "target": "ds",
     "targetHandle": "c_frame_pid"},
]


def card(id_="card"):
    return {"id": id_, "type": "dashboard.card", "position": {"x": 3, "y": 0},
            "data": {"name": "Total People Counting", "kind": "tile",
                     "metric": ""}}


# 1. the user's exact scene: the OLD o_pid wire still attached, the new
#    o_frame_pid wired beside it. The card must follow the live column.
c = compile_(BASE + [card()], BASE_EDGES + [
    {"id": "old", "source": "ds", "sourceHandle": "o_pid", "target": "card"},
    {"id": "new", "source": "ds", "sourceHandle": "o_frame_pid", "target": "card"},
])
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["source"] == "dataset" and w["query"]["field"] == "frame_pid", w
warns = [x["message"] for x in c["warnings"] if x["node"] == "card"]
assert any("'pid'" in m and "stale" in m for m in warns), warns
print("== a stale wire loses to the rewired column, with a warning:", warns[0])

# 2. EVERY wire stale: the card waits, switched off -- no error, so the rest
#    of the canvas still applies.
c = compile_(BASE + [card()], BASE_EDGES + [
    {"id": "old", "source": "ds", "sourceHandle": "o_pid", "target": "card"},
])
assert not c["errors"], c["errors"]
assert not any(x["id"] == "wf_card" for x in c["widgets"])
warns = [x["message"] for x in c["warnings"] if x["node"] == "card"]
assert any("rewire" in m for m in warns), c["warnings"]
print("== a card whose only wire is stale goes quiet instead of freezing "
      "the dashboard")

# 3. two rules into a LINE chart: one series each, read from history
two = BASE + [
    {"id": "fr2", "type": "analytics.frame", "position": {"x": 1, "y": 1},
     "data": {"fnId": "helmets", "name": "Helmets", "mode": "realtime"}},
    {"id": "g", "type": "dashboard.graph", "position": {"x": 3, "y": 1},
     "data": {"name": "Both counts", "kind": "line"}},
]
edges = BASE_EDGES + [
    {"id": "x1", "source": "s", "target": "fr2"},
    {"id": "x2", "source": "fr", "target": "g"},
    {"id": "x3", "source": "fr2", "target": "g"},
]
c = compile_(two, edges)
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert w["source"] == "history" and w.get("range"), w
keys = [s["key"] for s in w["series"]]
assert keys == ["fn_in_frame_count", "fn_helmets_count"], keys
assert [s["label"] for s in w["series"]] == ["In frame", "Helmets"], w["series"]
print("== two rules, one chart, two named series:", keys)

# ...it survives the layout validator with its series intact
from core.services import builder as builder_service           # noqa: E402
stored = builder_service.validate([w])[0]
assert [s["key"] for s in stored["series"]] == keys, stored
print("== and the validator keeps both series")

# 4. a comparison shape compares the latest values instead of history
for n in two:
    if n["id"] == "g":
        n["data"] = {**n["data"], "kind": "pie"}
c = compile_(two, edges)
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert w["source"] == "stat" and len(w["series"]) == 2, w
print("== a pie of the same wires compares their latest values")

# 5. a TILE with two wires is refused with the way out named
for i, n in enumerate(two):
    if n["id"] == "g":
        two[i] = {**n, "type": "dashboard.card",
                  "data": {"name": "Both counts", "kind": "tile", "metric": ""}}
c = compile_(two, edges)
msg = next(x["message"] for x in c["errors"] if x["node"] == "g")
assert "Graph node" in msg, msg
print("== a tile still shows one value:", msg)

# 6. the leftover wire is PRUNED when the graph loads: its port no longer
#    exists, so the canvas cannot draw it and the operator cannot delete it.
g = {"nodes": BASE + [card()], "edges": BASE_EDGES + [
    {"id": "old", "source": "ds", "sourceHandle": "o_pid", "target": "card"},
    {"id": "new", "source": "ds", "sourceHandle": "o_frame_pid", "target": "card"},
    {"id": "oldw", "source": "fr", "sourceHandle": "pid", "target": "ds",
     "targetHandle": "c_pid"},
]}
dropped = wf.prune_stale_column_wires(g, TABLES)
assert dropped == 2, dropped
handles = {e.get("sourceHandle") for e in g["edges"]} \
    | {e.get("targetHandle") for e in g["edges"]}
assert "o_pid" not in handles and "c_pid" not in handles, handles
assert any(e.get("sourceHandle") == "o_frame_pid" for e in g["edges"])
print("== both invisible leftovers pruned, the rewired one kept")

# ...but a node whose WHOLE table is gone keeps its wires: that is a table
# problem the operator has to see, not a rename to tidy up after.
g = {"nodes": BASE + [card()], "edges": BASE_EDGES + [
    {"id": "old", "source": "ds", "sourceHandle": "o_pid", "target": "card"}]}
assert wf.prune_stale_column_wires(g, {}) == 0
print("== a missing table prunes nothing")

# 7. the SAME rule wired into one card twice (once on its named `out` port,
#    once on the plain port -- the wires overlap and read as one) is a
#    duplicate, not an ambiguity: the card compiles with a warning.
dup = BASE + [{"id": "t", "type": "dashboard.card", "position": {"x": 9, "y": 9},
               "data": {"name": "Now", "kind": "tile", "metric": ""}}]
edges = BASE_EDGES + [
    {"id": "d1", "source": "fr", "sourceHandle": "out", "target": "t",
     "targetHandle": "in"},
    {"id": "d2", "source": "fr", "sourceHandle": None, "targetHandle": "in",
     "target": "t"},
]
c = compile_(dup, edges)
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_t")
assert w["key"] == "fn_in_frame_count", w
assert any("twice" in x["message"] for x in c["warnings"]), c["warnings"]
print("== a duplicated wire compiles with a warning, not an error")

# 8. TWO RULES, ONE VERDICT: the first wired rule that fires decides the
#    state (wire order = priority), via a hidden picker rule the card binds
#    to. Unnamed states default to the rules' own names, so the card reads
#    before anyone configures it.
two = BASE + [
    {"id": "fr2", "type": "analytics.frame", "position": {"x": 1, "y": 2},
     "data": {"fnId": "helmets", "name": "Helmets", "mode": "realtime"}},
    {"id": "v", "type": "dashboard.card", "position": {"x": 9, "y": 9},
     "data": {"name": "Line status", "kind": "verdict",
              "ok_label": "ALL CLEAR"}}]
edges = BASE_EDGES + [
    {"id": "x1", "source": "s", "target": "fr2"},
    {"id": "v1", "source": "fr", "target": "v"},
    {"id": "v2", "source": "fr2", "target": "v"},
]
c = compile_(two, edges)
assert not c["errors"], c["errors"]
pick = next(f for f in c["functions"] if f["id"].endswith("_pick"))
assert pick["params"]["expr"] == (
    "iff((fn_in_frame_count != 0), 1, iff((fn_helmets_count != 0), 2, 0))"),     pick["params"]["expr"]
w = next(x for x in c["widgets"] if x["id"] == "wf_v")
assert w["kind"] == "verdict" and w["key"] == f"fn_{pick['id']}_value", w
assert [st["label"] for st in w["states"]][0] == "ALL CLEAR", w["states"]
assert len(w["states"]) == 3, w["states"]
print("== two rules weigh into one verdict; first wired wins")

# ...and custom states, once set, win over the generated ones.
two[-1] = {**two[-1], "data": {**two[-1]["data"],
           "states": [{"value": 1, "label": "PERSON", "tone": "warn"},
                      {"value": 2, "label": "DEFECT", "tone": "alert"},
                      {"value": 0, "label": "OK", "tone": "ok"}]}}
c = compile_(two, edges)
w = next(x for x in c["widgets"] if x["id"] == "wf_v")
assert {st["label"] for st in w["states"]} == {"PERSON", "DEFECT", "OK"}, w
print("== custom states win over the generated ones")

# 9. SEVERAL RULES, ONE TABLE: every rule wired into a Data Table records its
#    own row -- one output document per firing rule, sharing the node's table
#    and columns. It used to be refused ("keep one Alert"), which forced a
#    duplicate table node per rule.
multi = BASE + [
    {"id": "fr3", "type": "analytics.frame", "position": {"x": 1, "y": 3},
     "data": {"fnId": "second", "name": "Second", "mode": "realtime",
              "trigger": {"metric": "count", "op": ">", "value": 1,
                          "severity": "warn", "message": "", "cooldown_s": 0}}},
    {"id": "ds2", "type": "output.dataset", "position": {"x": 4, "y": 3},
     "data": {"dataset": "counting", "name": "Counting", "mode": "alert"}},
]
medges = BASE_EDGES + [
    {"id": "m0", "source": "s", "target": "fr3"},
    {"id": "m1", "source": "fr", "target": "ds2", "targetHandle": "c_live_counting"},
    {"id": "m2", "source": "fr3", "target": "ds2", "targetHandle": "c_live_counting"},
]
# the first rule needs a trigger too, or it cannot fire a row
for i, n in enumerate(multi):
    if n["id"] == "fr":
        multi[i] = {**n, "data": {**n["data"],
                    "trigger": {"metric": "count", "op": ">", "value": 0,
                                "severity": "warn", "message": "",
                                "cooldown_s": 0}}}
c = compile_(multi, medges)
assert not c["errors"], c["errors"]
writers = [o for o in c["outputs"]
           if o.get("type") == "dataset" and o["id"].startswith("wf_ds2")]
assert len(writers) == 2, writers
assert {w["function"] for w in writers} == {"in_frame", "second"}, writers
assert writers[0]["id"] == "wf_ds2", writers[0]["id"]
assert all(w["dataset"] == "counting" for w in writers), writers
print("== two rules into one table write one row each")

# 3. A card wired to a TRIGGER shows that trigger.
#
# "Show me when this fires" is the whole point of a Verdict, and the Trigger is
# the node that names the moment -- but the card's input resolution knew only
# analytics, variables, branches and the two DEPRECATED outputs it replaced.
# So the obvious wiring answered "needs exactly one Analytics input", of a card
# wired to the very node it was meant to show.
TRIG = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "cnt", "type": "analytics.counter", "position": {"x": 1, "y": 0},
     "data": {"fnId": "bay", "name": "In bay", "mode": "realtime"}},
    {"id": "br", "type": "branch.cases", "position": {"x": 2, "y": 0},
     "data": {"fnId": "busy", "name": "Crowded?",
              "rules": [{"op": ">", "value": 4, "input": "in1"}]}},
    {"id": "tg", "type": "output.trigger", "position": {"x": 3, "y": 0},
     "data": {"name": "Overcrowded", "alert": True, "severity": "warn"}},
    {"id": "vd", "type": "dashboard.verdict", "position": {"x": 4, "y": 0},
     "data": {"name": "Bay state"}},
]
TRIG_EDGES = [
    {"id": "t1", "source": "s", "target": "cnt"},
    {"id": "t2", "source": "cnt", "target": "br", "targetHandle": "in1"},
    {"id": "t3", "source": "br", "sourceHandle": "out0", "target": "tg"},
    {"id": "t4", "source": "tg", "target": "vd"},
]
c = compile_(TRIG, TRIG_EDGES)
assert not c["errors"], c["errors"]
card = next(w for w in c["widgets"] if w["id"].startswith("wf_vd"))
assert card["kind"] == "verdict", card
# Bound to the rule's alert STATE -- 1 while the trigger's condition holds --
# which is what the card is asked to show.
assert card["key"].endswith("_active"), card["key"]
print("== a Verdict wired from a Trigger shows that trigger's state")
print("ALL OK")
