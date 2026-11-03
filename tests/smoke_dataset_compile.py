# The Dataset node: the operator's own table, compiled to one output document.
#
# Every other output sends a MESSAGE about a number. A Dataset records a ROW,
# so its columns have to resolve at compile time -- a wired column becomes the
# stat key it was dragged from, and the runtime never sees the graph.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]

#: The table this node writes into. Columns belong to the TABLE (designed on
#: the Database page); the node only decides what feeds each one.
COLUMNS = [{"name": "people", "type": "number"},
           {"name": "zone", "type": "text"},
           {"name": "shift", "type": "text"},
           {"name": "dwell", "type": "number"}]
TABLES = {"bay_log": {"key": "bay_log", "name": "Bay log", "columns": COLUMNS}}


def graph(sources=None, mode="alert", trigger=True, wires=(), key="bay_log"):
    """Detection -> Zone -> Occupancy (-> Alert) -> Data Table.

    `wires` names the COLUMNS dragged onto, which is the whole point of the
    handle convention: a wire lands on c_<column>, so it fills that column.
    """
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
        {"id": "ds", "type": "output.dataset", "position": {"x": 4, "y": 0},
         "data": {"dataset": key, "name": key, "mode": mode,
                  "sources": sources or {}}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "z"},
             {"id": "e2", "source": "z", "target": "occ"}]
    for column in wires:
        edges.append({"id": f"w{column}", "source": "occ", "target": "ds",
                      "targetHandle": f"c_{column}"})
    if trigger:
        # An Alert ON THE RULE, not on the table. Nothing connects to the
        # Data Table but its columns -- the alerting rule is derived from
        # whatever feeds them.
        nodes.append({"id": "al", "type": "output.alert", "position": {"x": 3, "y": 0},
                      "data": {"metric": "count", "op": ">", "value": 2}})
        edges.append({"id": "e3", "source": "occ", "target": "al"})
    return {"nodes": nodes, "edges": edges}


def compile_(g, tables=None):
    return wf.compile_graph(wf.clean_graph(g), ZONES,
                            tables=TABLES if tables is None else tables)


def only_error(g):
    c = compile_(g)
    assert c["errors"], "that compiled when it should not have"
    return c["errors"][0]["message"]


# 1. the happy path: a wire, and the three configured sources
c = compile_(graph({"zone": {"src": "event", "field": "zone"},
                    "shift": {"src": "const", "value": "night"},
                    "dwell": {"src": "stat", "key": "fn_bay_avg_s"}},
                   wires=("people",)))
assert not c["errors"], c["errors"]
doc = c["outputs"][0]
assert doc["dataset"] == "bay_log" and doc["mode"] == "alert"
assert doc["trigger"] == "alert" and doc["function"] == "bay", doc
cols = {x["name"]: x for x in doc["columns"]}
print("== one document, four columns filled four ways:")
for name in ("people", "zone", "shift", "dwell"):
    print(f"   {name:8s} {cols[name]}")

# 2. THE point of the handle convention: a wire dropped on a column fills THAT
#    column. Handles are named c_<column>, not numbered, so reordering the
#    table cannot silently re-point a wire already drawn on the canvas.
assert cols["people"]["src"] == "wire", cols["people"]
assert cols["people"]["key"] == "fn_bay_count", cols["people"]
assert cols["people"]["handle"] == "c_people", cols["people"]
print("== a wire on 'people' filled people, from", cols["people"]["key"])

# ...and it still lands on `people` when the table is reordered underneath it
reordered = {"bay_log": {**TABLES["bay_log"],
                         "columns": list(reversed(COLUMNS))}}
c = compile_(graph({}, wires=("people",)), tables=reordered)
assert not c["errors"], c["errors"]
cols2 = {x["name"]: x for x in c["outputs"][0]["columns"]}
assert cols2["people"]["key"] == "fn_bay_count", cols2
print("== reordering the table leaves that wire pointing at the same column")

# 3. the COLUMN LIST is the table's. A node cannot rename or retype one, and
#    the type it records is always the table's.
assert cols["dwell"]["type"] == "number" and cols["zone"]["type"] == "text"
assert set(cols) <= {c_["name"] for c_ in COLUMNS}
print("== every recorded column and type came from the table")

# 4. a column with nothing attached is simply not written by this node. Two
#    nodes may each fill part of one table, so that is a state, not a fault.
c = compile_(graph({}, wires=("people",)))
assert not c["errors"], c["errors"]
assert [x["name"] for x in c["outputs"][0]["columns"]] == ["people"], \
    c["outputs"][0]["columns"]
print("== columns nothing feeds are left out, not errored over")

# ...and a node attached to NOTHING is a DRAFT: switched off and dim, not an
# error. It was a red node for as long as it took to wire one up, which is
# the operator's own pace reported as a fault (see smoke_switch.py). It still
# records nothing, and the warning says why.
c = compile_(graph({}))
assert not c["errors"], c["errors"]
assert any("switched off" in w["message"] for w in c["warnings"]), c["warnings"]
print("== a table with nothing attached is switched off, not an error")

# 5. an event field the app cannot find would log blanks for ever in silence
# Wired, deliberately: an unwired node is a draft and is not compiled at all
# now, so the wire is what makes this a question about the FIELD.
msg = only_error(graph({"zone": {"src": "event", "field": "operator_name"}},
                       wires=("people",)))
assert "operator_name" in msg, msg
print("== an unknown event field is refused:", msg)

# 6. WHICH alert is DERIVED, not wired. The columns already point at the rules
#    that feed them, and "write a row when something alerts" already says when,
#    so a trigger port would only restate the dropdown -- and could disagree
#    with it. Here the wired rule has an Alert, and no trigger is connected:
c = compile_(graph({"shift": {"src": "const", "value": "x"}}, wires=("people",)))
assert not c["errors"], c["errors"]
assert c["outputs"][0]["function"] == "bay", c["outputs"][0]
print("== the alerting rule is worked out from the wired column:",
      c["outputs"][0]["function"])

# ...and when nothing feeding it alerts, there is genuinely no moment to
# record, which is said in terms of what to do about it
msg = only_error(graph({"shift": {"src": "const", "value": "x"}},
                       wires=("people",), trigger=False))
assert "no moment to record" in msg, msg
print("== with nothing alerting it says so:", msg)

c = compile_(graph({"shift": {"src": "const", "value": "x"}},
                   mode="every", trigger=False))
assert not c["errors"], c["errors"]
assert c["outputs"][0]["trigger"] == "any" and c["outputs"][0]["interval_s"] >= 5
print("== on a timer it compiles with no trigger, interval",
      c["outputs"][0]["interval_s"])

# 7. the interval floor holds: these rows travel through events.jsonl, which
#    every restart re-reads from the top
g = graph({"shift": {"src": "const", "value": "x"}}, mode="every", trigger=False)
g["nodes"][-1]["data"]["interval_s"] = 0.1
assert compile_(g)["outputs"][0]["interval_s"] == 5.0
print("== a sub-second interval is clamped to 5.0 seconds")

# 8. a node pointed at a table nobody made is refused by name
msg = only_error(graph({"a": {"src": "const", "value": "1"}}, key="ghost",
                       wires=("people",)))
assert "ghost" in msg, msg
print("== a node naming a table that does not exist is refused:", msg)


# -- and the other end: a card that reads the table -------------------------
def with_card(card):
    g = graph({"zone": {"src": "event", "field": "zone"}}, wires=("dwell",))
    g["nodes"].append({"id": "card", "type": "dashboard.events",
                       "position": {"x": 5, "y": 2},
                       "data": {"source_mode": "dataset", **card}})
    return g


c = compile_(with_card({"dataset": "bay_log", "kind": "bar", "agg": "avg",
                        "field": "dwell", "group_by": "zone"}))
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["source"] == "dataset" and w["dataset"] == "bay_log", w
assert w["query"]["field"] == "dwell" and w["query"]["group_by"] == "zone", w
print("== a card compiles to a dataset widget carrying the declaration:",
      w["dataset"], [c_["name"] for c_ in w["columns"]])

from core.services import builder as builder_service          # noqa: E402
stored = builder_service.validate([w])[0]
assert stored["source"] == "dataset" and stored["query"]["group_by"] == "zone"
print("== and it survives the layout validator unchanged")

c = compile_(with_card({"dataset": "bay_log", "kind": "bar", "agg": "sum",
                        "field": "salary", "group_by": "ssn"}))
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["query"].get("field", "") == "" and w["query"].get("group_by", "") == ""
print("== a column the table does not have never reaches the widget")

msg = only_error(with_card({"dataset": "not_a_table", "kind": "bar"}))
assert "does not exist" in msg, msg
print("== a card naming a missing table is refused:", msg)

# A chart can ask for THE LATEST VALUE, not only totals and averages.
# Grouped, that is "where each machine stands right now" -- a question neither
# max (the biggest ever, usually a spike from last Tuesday) nor count can
# answer. It was offered on a card reading one column and nowhere else, so a
# bar chart of the same table could total and average but not show the state.
c = compile_(with_card({"dataset": "bay_log", "kind": "bar", "agg": "latest",
                        "field": "dwell", "group_by": "zone"}))
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["query"]["agg"] == "latest", w["query"]
assert builder_service.validate([w])[0]["query"]["agg"] == "latest"
print("== a bar chart can chart the latest value of a column, per group")

# ...but not per TIME BUCKET: "latest" is not a per-bucket aggregate, and the
# repo would fall back to counting rows -- which charts write rate, not the
# value. max is the honest end-of-bucket reading, and it is what the
# single-column path already substitutes.
c = compile_(with_card({"dataset": "bay_log", "kind": "line", "agg": "latest",
                        "field": "dwell", "bucket": "hour"}))
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["query"]["agg"] == "max", w["query"]
print("== over time it becomes max, not a row count")
print("ALL OK")
