# Every column of a Database Entity node has TWO ports:
#
#     o---- column_name ----o
#     ^                     ^
#     write into it         read it back out
#
# The left half was the original node. This file is about the right half --
# the one that makes a table a SOURCE as well as a sink, so a card can show
# what was actually recorded rather than only what is live.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service          # noqa: E402
from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
COLUMNS = [{"name": "confidence", "type": "number"},
           {"name": "defect_type", "type": "text"},
           {"name": "recorded_at", "type": "datetime"}]
TABLES = {"inspection": {"key": "inspection", "name": "Inspection result",
                         "columns": COLUMNS}}


def graph(card=None, op="insert", read=None):
    """Detection -> Zone -> Occupancy (-> Alert), into the Entity (-> card)."""
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
        {"id": "al", "type": "output.alert", "position": {"x": 3, "y": 0},
         "data": {"metric": "count", "op": ">", "value": 2}},
        {"id": "db", "type": "output.dataset", "position": {"x": 4, "y": 0},
         "data": {"dataset": "inspection", "name": "Inspection result",
                  "op": op, "mode": "alert",
                  "sources": {"defect_type": {"src": "event",
                                              "field": "detection_label"}}}},
    ]
    # Nothing connects to the table but its COLUMNS. The Alert sits on the
    # RULE; which alert records a row is derived from what feeds the columns.
    edges = [{"id": "e1", "source": "s", "target": "z"},
             {"id": "e2", "source": "z", "target": "occ"},
             {"id": "e3", "source": "occ", "target": "al"},
             # the WRITE half: a value dropped on `confidence`
             {"id": "e5", "source": "occ", "target": "db",
              "targetHandle": "c_confidence"}]
    if card is not None:
        nodes.append({"id": "card", "type": "dashboard.events",
                      "position": {"x": 6, "y": 0}, "data": card})
        # the READ half: a column's right port into a card
        edges.append({"id": "e6", "source": "db", "target": "card",
                      "sourceHandle": f"o_{read}"})
    return {"nodes": nodes, "edges": edges}


def compile_(g, tables=None):
    return wf.compile_graph(wf.clean_graph(g), ZONES,
                            tables=TABLES if tables is None else tables)


# 1. THE WIRING RULE. Every other output is terminal -- nothing may come out of
#    it. A Data Table is the one node that is both, because a column's read
#    port has to reach a card.
ok, why = wf.edge_allowed("output.dataset", "dashboard.events")
assert ok, why
print("== a Data Table may feed a dashboard, unlike every other output")

# ...and it is still not a general-purpose source: it cannot feed an analytics
# rule, which would need the detection app to read Postgres. It cannot.
ok, why = wf.edge_allowed("output.dataset", "analytics.formula")
assert not ok, "a Data Table fed a live rule, which cannot read the database"
print("== but not a live rule, which would need the app to query Postgres")

# 2. a card wired to `confidence` becomes a widget over THAT column, with no
#    configuration: the wire already said which table and which column
c = compile_(graph({"kind": "gauge"}, read="confidence"))
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["source"] == "dataset" and w["dataset"] == "inspection", w
assert w["query"]["field"] == "confidence", w
print("== confidence -> gauge needed no setup:", w["kind"], w["query"])

# a number is averaged; a text column has no average, so it is counted
c = compile_(graph({"kind": "pie"}, read="defect_type"))
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["query"]["agg"] == "count", w["query"]
assert w["query"]["group_by"] == "defect_type", w["query"]
print("== defect_type -> pie counts and groups by itself:", w["query"])

# ...and the widget survives the layout validator, which is what renders it
stored = builder_service.validate([w])[0]
assert stored["source"] == "dataset" and stored["dataset"] == "inspection"
assert stored["query"]["group_by"] == "defect_type", stored["query"]
print("== and it survives the layout validator unchanged")

# 3. reading a column the table no longer has: the card goes quiet with a
#    warning naming the column -- NOT an error, because an error here blocks
#    apply_artifacts for the whole canvas, which froze every other card the
#    moment one column was renamed.
c = compile_(graph({"kind": "tile"}, read="operator_name"))
assert not c["errors"], c["errors"]
assert not any(x["id"] == "wf_card" for x in c["widgets"])
warn = next(x["message"] for x in c["warnings"] if "operator_name" in x["message"])
print("== reading a column that no longer exists says which, as a warning:",
      warn)

# 4. SCHEMA SYNC. Add a column to the table and the node gains it with no edit
#    to the graph -- the node is generated from the table, not stored copies.
grown = {"inspection": {**TABLES["inspection"],
                        "columns": COLUMNS + [{"name": "operator_name",
                                               "type": "text"}]}}
c = compile_(graph({"kind": "tile"}, read="operator_name"), tables=grown)
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert [col["name"] for col in w["columns"]] == \
    ["confidence", "defect_type", "recorded_at", "operator_name"], w["columns"]
print("== a column added to the table appears with no graph edit:",
      [col["name"] for col in w["columns"]])

# 5. SELECT writes nothing. The node still exists on the canvas for its read
#    ports, which is the whole point of it.
c = compile_(graph({"kind": "tile"}, read="confidence", op="select"))
assert not c["errors"], c["errors"]
assert not [o for o in c["outputs"] if o["type"] == "dataset"], c["outputs"]
assert any(x["id"] == "wf_card" for x in c["widgets"]), c["widgets"]
print("== a read-only node records nothing, and still feeds its card")

# ...while insert does write, from the same graph
c = compile_(graph({"kind": "tile"}, read="confidence"))
docs = [o for o in c["outputs"] if o["type"] == "dataset"]
assert len(docs) == 1, docs
filled = {col["name"]: col["src"] for col in docs[0]["columns"]}
assert filled == {"confidence": "wire", "defect_type": "event"}, filled
print("== insert records what is attached:", filled)


# -- EVERY card can read a column, not just the Event Card -------------------
# A column is a value, so any card that can show a value can show it. The
# card's own shape decides how -- a Gauge is a gauge whatever it is fed.
def card(kind_node, data=None, read="confidence"):
    g = graph(None)
    g["nodes"].append({"id": "card", "type": kind_node,
                       "position": {"x": 6, "y": 0}, "data": data or {}})
    g["edges"].append({"id": "e9", "source": "db", "target": "card",
                       "sourceHandle": f"o_{read}"})
    return g


for node_type, expect in (("dashboard.counter", "tile"),
                          ("dashboard.gauge", "gauge"),
                          ("dashboard.verdict", "verdict"),
                          ("dashboard.table", "table"),
                          # A Graph with no shape chosen falls back to the
                          # widget kind it declares; the picker overrides it.
                          ("dashboard.graph", "line")):
    c = compile_(card(node_type))
    assert not c["errors"], (node_type, c["errors"])
    w = next(x for x in c["widgets"] if x["id"] == "wf_card")
    assert w["source"] == "dataset" and w["dataset"] == "inspection", w
    assert w["kind"] == expect, (node_type, w["kind"], expect)
    print(f"== {node_type:20s} reads a column as a {w['kind']}")

# ...and the two cards whose whole point is the shape let it be chosen
for picked in ("bar", "area", "spline", "donut"):
    w = next(x for x in compile_(card("dashboard.graph", {"kind": picked}))["widgets"]
             if x["id"] == "wf_card")
    assert w["kind"] == picked, (picked, w["kind"])
print("== a Graph honours the shape picked on it; a Gauge stays a gauge")

# THE LOGIC PICKER. A number defaults to its latest value -- which is what a
# live tile means by "the value", and is not max() (the biggest ever, usually
# a spike from last Tuesday).
w = next(x for x in compile_(card("dashboard.counter"))["widgets"]
         if x["id"] == "wf_card")
assert w["query"] == {"agg": "latest", "field": "confidence"}, w["query"]
print("== a number column defaults to its latest value:", w["query"])

for agg in ("sum", "avg", "min", "max", "count", "unique", "latest"):
    w = next(x for x in compile_(card("dashboard.counter", {"agg": agg}))["widgets"]
             if x["id"] == "wf_card")
    assert w["query"]["agg"] == agg, (agg, w["query"])
print("== every aggregation the picker offers survives the compile")

# ...but arithmetic on a TEXT column is refused rather than passed to Postgres,
# which would raise on the cast instead of answering
for agg in ("sum", "avg", "min", "max"):
    w = next(x for x in compile_(card("dashboard.counter", {"agg": agg},
                                      read="defect_type"))["widgets"]
             if x["id"] == "wf_card")
    assert w["query"]["agg"] == "count", (agg, w["query"])
print("== totalling a text column falls back to counting, never to an error")

# a text column defaults to counting, since that is what it can answer
w = next(x for x in compile_(card("dashboard.counter", None, "defect_type"))["widgets"]
         if x["id"] == "wf_card")
assert w["query"]["agg"] == "count", w["query"]
print("== a text column defaults to counting")

# A chart gets something to plot along -- and WHICH axis depends on the
# chart. A TIME chart (the graph node's default is area) buckets by the
# clock: grouped by its own numeric column it was one bucket per distinct
# value, "the latest total for each total", a chart of y = x that read as
# empty. A COMPARISON chart (donut) groups by the column, which is the
# natural reading of "defect_type into a pie". A gauge does neither.
w = next(x for x in compile_(card("dashboard.graph"))["widgets"]
         if x["id"] == "wf_card")
assert w["query"].get("bucket") == "hour", w["query"]
assert "group_by" not in w["query"], w["query"]
# ...and "latest" per bucket is not an aggregate the repo has; the compile
# lands on max, the end-of-bucket figure for a running total.
assert w["query"]["agg"] == "max", w["query"]
# A comparison chart over a NUMBER buckets by time as well. Grouping a number
# by its own values draws one bar per distinct value -- for a 0/1 column,
# exactly two, labelled 0 and 1, one of them always empty. That is a chart of
# the column against itself, and it is what "total violations" looked like on
# a real box (192.168.1.234, pcb_sensata).
w = next(x for x in compile_(card("dashboard.graph", {"kind": "donut"}))["widgets"]
         if x["id"] == "wf_card")
assert w["query"].get("bucket") == "hour", w["query"]
assert "group_by" not in w["query"], w["query"]
# ...unless the operator says otherwise, which the inspector now asks.
w = next(x for x in compile_(card("dashboard.graph",
                                  {"kind": "bar", "group_by": "defect_type"}))["widgets"]
         if x["id"] == "wf_card")
assert w["query"].get("group_by") == "defect_type", w["query"]
assert "bucket" not in w["query"], w["query"]
w = next(x for x in compile_(card("dashboard.gauge"))["widgets"]
         if x["id"] == "wf_card")
assert "group_by" not in w["query"] and "bucket" not in w["query"], w["query"]
print("== a time chart buckets, a comparison chart groups, a gauge does neither")

# the period narrows which rows count
w = next(x for x in compile_(card("dashboard.counter", {"range": "week"}))["widgets"]
         if x["id"] == "wf_card")
assert w["query"]["range"] == "week", w["query"]
print("== the period reaches the widget:", w["query"])

# and every one of these survives the validator that actually renders them
for node_type in ("dashboard.counter", "dashboard.gauge", "dashboard.verdict",
                  "dashboard.table", "dashboard.graph"):
    w = next(x for x in compile_(card(node_type))["widgets"] if x["id"] == "wf_card")
    stored = builder_service.validate([w])[0]
    assert stored["source"] == "dataset", (node_type, stored)
print("== and all of them survive the layout validator")

# -- the EDITORS' palette ----------------------------------------------------
# The Dashboard and Analytics editors build their palette from the compile's
# catalogue. Without dataset entries a table's columns were reachable only by
# wiring a card on the canvas -- the editors offered analytics functions and
# nothing else, so a card over recorded data could not be added where cards
# are actually composed.
c = compile_(graph(None))
entries = [x for x in c["catalog"] if x.get("source") == "dataset"]
assert len(entries) == len(COLUMNS), (len(entries), len(COLUMNS))
by_field = {x["query"]["field"]: x for x in entries}
assert set(by_field) == {"confidence", "defect_type", "recorded_at"}, sorted(by_field)
assert by_field["confidence"]["query"]["agg"] == "latest"
assert by_field["defect_type"]["query"]["agg"] == "count"
for x in entries:
    assert x["dataset"] == "inspection" and x["columns"], x
    assert set(x["surfaces"]) == {"dashboard", "analytics"}, x
print("== every table column is offered to both editors:", sorted(by_field))

# ...and a catalogue entry makes a widget that actually renders. Dropping the
# dataset/query on the way through was why one could be dragged in and then
# show nothing.
w = builder_service.validate([{
    "id": "w1", "kind": "tile", "source": "dataset",
    "dataset": by_field["confidence"]["dataset"],
    "columns": by_field["confidence"]["columns"],
    "query": by_field["confidence"]["query"],
}])[0]
assert w["dataset"] == "inspection" and w["query"]["field"] == "confidence", w
print("== and the widget it makes survives the validator:", w["query"])

# entries exist even with NO node writing to the table: the rows are there to
# read, and a table nothing records into is exactly the one you want to look at
c = compile_({"nodes": [], "edges": []})
assert len([x for x in c["catalog"] if x.get("source") == "dataset"]) == len(COLUMNS)
print("== a table with no node on the canvas is still offered")
print("ALL OK")

# -- the WHOLE TABLE port ----------------------------------------------------
# A column port hands out one value; the port beside the table's name hands
# out the rows. Picking six columns one at a time cannot say "show me this
# table", which is what a table card is for.
def table_card(data=None):
    g = graph(None)
    g["nodes"].append({"id": "card", "type": "dashboard.events",
                       "position": {"x": 6, "y": 0}, "data": data or {}})
    g["edges"].append({"id": "e9", "source": "db", "target": "card",
                       "sourceHandle": "table"})
    return g


c = compile_(table_card())
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
assert w["kind"] == "table" and w["source"] == "dataset", w
assert [x["name"] for x in w["columns"]] == [x["name"] for x in COLUMNS], w["columns"]
print("== the table port shows every column by default:",
      [x["name"] for x in w["columns"]])

# ...ticking narrows it, and the ticked set is what the widget CARRIES, so it
# is also the only thing that widget can read -- narrower than the table,
# which is the safe direction
w = next(x for x in compile_(table_card({"show": ["defect_type"]}))["widgets"]
         if x["id"] == "wf_card")
assert [x["name"] for x in w["columns"]] == ["defect_type"], w["columns"]
print("== ticking narrows both what is shown and what it may read")

# a card wired to the table but with every column unticked would render an
# empty grid for ever, so it says so instead
c = compile_(table_card({"show": ["not_a_column"]}))
assert c["errors"] and "no columns are ticked" in c["errors"][0]["message"], c["errors"]
print("== a table card with nothing to show is refused:",
      c["errors"][0]["message"])

# rows are not a gauge: the card draws as a table whatever shape it carries
w = next(x for x in compile_(table_card({"kind": "gauge"}))["widgets"]
         if x["id"] == "wf_card")
assert w["kind"] == "table", w["kind"]
print("== rows always draw as a table, never as a number")

# and it survives the validator that renders it
assert builder_service.validate([w])[0]["source"] == "dataset"
print("== and survives the layout validator")
