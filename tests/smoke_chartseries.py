# A Chart draws SEVERAL series, one per input port, from ANY source.
#
# The compile has emitted a series per wired rule for a long time; what it
# lacked was a way to say so before the wires were drawn (one unnamed port), a
# way to name and colour each line, and any source other than a live rule. So
# the node grows labelled series ports the way a Formula grows operands, and
# every wire is judged on its own: a rule publishes a stat key, an Event Card
# carries its question about recorded events, a Data Table column carries the
# column.
#
# What is pinned here:
#   1. one wire on the plain `in` port compiles EXACTLY as it did -- a
#      one-key widget, not a series list
#   2. two rules on two named ports -> two history series with stable ids
#   3. an Event Card wire -> a source "query" series carrying its question
#   4. a Data Table column wire -> a source "dataset" series with
#      dataset/field/ts_field
#   5. the per-port label and colour on the NODE ride into the widget
#   6. the ninth wire is refused with the warning that already existed
#   7. the migration spreads a saved chart's stacked fan-in over the ports,
#      changing nothing about what it draws
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
    {"name": "shift", "type": "text"}]},
    # A table whose rows are ABOUT a moment of their own, which is the axis
    # its column should be drawn along rather than when the row was written.
    "runs": {"key": "runs", "name": "Runs", "columns": [
        {"name": "started", "type": "timestamp"},
        {"name": "parts", "type": "number"}]}}


def compile_(nodes, edges):
    g = {"nodes": nodes, "edges": edges}
    return wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)


def rule(nid, fn_id, name, y=0):
    return {"id": nid, "type": "analytics.frame", "position": {"x": 1, "y": y},
            "data": {"fnId": fn_id, "name": name, "mode": "realtime"}}


def chart(data=None):
    return {"id": "g", "type": "dashboard.graph", "position": {"x": 3, "y": 0},
            "data": {"name": "Line", "kind": "line", **(data or {})}}


BASE = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    rule("fr", "in_frame", "In frame"),
    rule("fr2", "helmets", "Helmets", y=1),
]
BASE_EDGES = [
    {"id": "e1", "source": "s", "target": "fr"},
    {"id": "e2", "source": "s", "target": "fr2"},
]

# 1. ONE WIRE ON `in`: the chart every operator already has. It must compile
#    to the same one-key widget it compiled to yesterday -- the new path is
#    for charts with more than one input, and a saved graph must not change
#    shape because the node grew ports.
c = compile_(BASE + [chart()], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g"}])
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert w["key"] == "fn_in_frame_count" and "series" not in w, w
assert w["source"] == "history", w
print("== one wire on the plain port still compiles to one key:", w["key"])

# 2. TWO RULES ON TWO NAMED PORTS: two history series, ids per PORT so a
#    colour cannot drift onto another line when the wiring changes.
c = compile_(BASE + [chart()], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "fr2", "target": "g", "targetHandle": "s2"}])
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert [s["id"] for s in w["series"]] == ["s1", "s2"], w["series"]
assert [s["key"] for s in w["series"]] == ["fn_in_frame_count",
                                           "fn_helmets_count"], w["series"]
assert all(s["source"] == "history" for s in w["series"]), w["series"]
assert [s["label"] for s in w["series"]] == ["In frame", "Helmets"], w["series"]
print("== two ports, two history series, ids s1/s2")

# ...and the SAME two rules wired the old way (both on `in`) give the same
#    two series in the same order: the ports are a way of saying it, not a
#    different answer.
c_old = compile_(BASE + [chart()], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g"},
    {"id": "w2", "source": "fr2", "target": "g"}])
w_old = next(x for x in c_old["widgets"] if x["id"] == "wf_g")
assert w_old == w, (w_old, w)
print("== and a chart still wired the old way compiles to exactly the same "
      "widget")

# ...a rule on a named port ALONE is still a series chart: dropping a wire on
#    "Series 2" is the operator naming a line on purpose.
c = compile_(BASE + [chart()], BASE_EDGES + [
    {"id": "w2", "source": "fr2", "target": "g", "targetHandle": "s2"}])
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert [s["id"] for s in w["series"]] == ["s2"], w["series"]
print("== one wire on a NAMED port is a series of its own:", w["series"][0]["id"])

# 3. AN EVENT CARD wired in: its question about recorded events becomes a
#    series of source "query", carrying the very filters the card counts by.
ev = {"id": "ev", "type": "dashboard.events", "position": {"x": 2, "y": 2},
      "data": {"name": "Violations", "agg": "count", "bucket": "hour",
               "range": "today", "source_mode": "events",
               "conditions": [{"field": "severity", "value": "alert"}]}}
c = compile_(BASE + [chart(), ev], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ev", "target": "g", "targetHandle": "s2"}])
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
q = w["series"][1]
assert q["source"] == "query" and q["id"] == "s2", q
assert q["query"]["agg"] == "count" and q["query"]["bucket"] == "hour", q
assert q["query"]["filters"] == {"severity": "alert"}, q
assert q["label"] == "Violations", q
print("== an Event Card wire draws its recorded events as a series:", q["query"])

# ...and the card is still a card of its own beside it.
assert any(x["id"] == "wf_ev" for x in c["widgets"]), c["widgets"]
print("== and the card itself still compiles")

# 4. A DATA TABLE COLUMN wired in: source "dataset", with the column and the
#    row clock to plot it along.
ds = {"id": "ds", "type": "output.dataset", "position": {"x": 2, "y": 3},
      "data": {"dataset": "counting", "name": "Counting", "mode": "change",
               "sources": {}}}
c = compile_(BASE + [chart(), ds], BASE_EDGES + [
    {"id": "e3", "source": "fr", "target": "ds", "targetHandle": "c_live_counting"},
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ds", "sourceHandle": "o_live_counting",
     "target": "g", "targetHandle": "s2"}])
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
col = w["series"][1]
assert col == {"id": "s2", "label": "live counting", "color": "",
               "agg": "avg", "source": "dataset", "dataset": "counting",
               "columns": TABLES["counting"]["columns"],
               "field": "live_counting",
               # Blank is the row's own clock, which every dataset row
               # carries; this table declares no date column of its own.
               "ts_field": ""}, col
print("== a table column wire draws as a dataset series:", col)

# ...and it survives the layout validator, which is where a series that named
#    something unaskable would be dropped on the floor.
from core.services import builder as builder_service           # noqa: E402
stored = builder_service.validate([w])[0]
assert [s["id"] for s in stored["series"]] == ["s1", "s2"], stored["series"]
assert stored["series"][1]["field"] == "live_counting", stored["series"][1]
assert stored["series"][1]["source"] == "dataset", stored["series"][1]
print("== and the stored layout keeps the dataset series whole")

# ...a TEXT column can only be counted; averaging words is a question
#    Postgres would raise on rather than answer.
c = compile_(BASE + [chart(), ds], BASE_EDGES + [
    {"id": "e3", "source": "fr", "target": "ds", "targetHandle": "c_live_counting"},
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ds", "sourceHandle": "o_shift",
     "target": "g", "targetHandle": "s2"}])
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert w["series"][1]["agg"] == "count", w["series"][1]
print("== a text column is counted, not averaged")

# ...a table that DECLARES a moment of its own is drawn along that column;
#    blank above meant "the moment the row was written", which is all the
#    other table can offer.
runs = {"id": "ds", "type": "output.dataset", "position": {"x": 2, "y": 3},
        "data": {"dataset": "runs", "name": "Runs", "mode": "change",
                 "sources": {}}}
c = compile_(BASE + [chart(), runs], BASE_EDGES + [
    {"id": "e3", "source": "fr", "target": "ds", "targetHandle": "c_parts"},
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ds", "sourceHandle": "o_parts",
     "target": "g", "targetHandle": "s2"}])
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert w["series"][1]["ts_field"] == "started", w["series"][1]
print("== a table with its own timestamp column is drawn along it")

# 5. THE PER-SERIES NAME AND COLOUR, off the node. They live on the node (not
#    only on the widget) because a wf_* widget is rebuilt from its node on
#    every compile -- a colour on the widget alone would last until the next
#    save.
c = compile_(BASE + [chart({"series": {
    "s1": {"label": "Zone A count", "color": "#04bade"},
    "s2": {"color": "#f59e0b"}}})], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "fr2", "target": "g", "targetHandle": "s2"}])
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert w["series"][0]["label"] == "Zone A count", w["series"]
assert w["series"][0]["color"] == "#04bade", w["series"]
# An untouched name keeps the rule's own, which is what makes a freshly wired
# chart readable before anybody has typed anything.
assert w["series"][1]["label"] == "Helmets", w["series"]
assert w["series"][1]["color"] == "#f59e0b", w["series"]
print("== per-port label and colour ride into the widget")

# 6. THE NINTH WIRE. Eight is builder.MAX_SERIES -- a chart with more lines
#    than that has no legend anybody can read -- and the wire is ignored with
#    the warning that has always said so.
rules = [rule(f"r{i}", f"fn{i}", f"Rule {i}", y=i) for i in range(9)]
edges = [{"id": f"s{i}", "source": "s", "target": f"r{i}"} for i in range(9)]
edges += [{"id": f"c{i}", "source": f"r{i}", "target": "g",
           "targetHandle": wf.GRAPH_SERIES_HANDLES[i]} for i in range(8)]
edges += [{"id": "c8", "source": "r8", "target": "g"}]
c = compile_([BASE[0]] + rules + [chart()], edges)
assert not c["errors"], c["errors"]
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
assert len(w["series"]) == 8, len(w["series"])
msg = next(x["message"] for x in c["warnings"] if x["node"] == "g")
assert "at most 8 series" in msg, msg
print("== the ninth wire is ignored, with the warning that already existed:",
      msg)

# 7. THE MIGRATION. A chart saved before the ports existed has every wire on
#    `in`. They move onto the named ports in the order they were drawn -- the
#    order the compiler already read them in -- so the chart draws exactly
#    what it drew before, on a node that now says which line is which.
saved = {"nodes": BASE + [chart()], "edges": BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g"},
    {"id": "w2", "source": "fr2", "target": "g"}]}
before = next(x for x in compile_(saved["nodes"], saved["edges"])["widgets"]
              if x["id"] == "wf_g")
assert wf.migrate_chart_series(saved) is True
handles = [e.get("targetHandle") for e in saved["edges"] if e["target"] == "g"]
assert handles == [None, "s2"], handles
assert next(n for n in saved["nodes"] if n["id"] == "g")["data"]["series_n"] == 2
after = next(x for x in compile_(saved["nodes"], saved["edges"])["widgets"]
             if x["id"] == "wf_g")
assert after == before, (before, after)
print("== the migration moves the stacked wires onto the ports and changes "
      "nothing it draws")

# ...and it is a one-way street: running it again finds nothing to do.
assert wf.migrate_chart_series(saved) is False
print("== running the migration twice changes nothing")

# ...a chart with ONE wire is not touched at all: that is the one-value
#    chart, and it compiles through a different path entirely.
one = {"nodes": BASE + [chart()], "edges": BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g"}]}
assert wf.migrate_chart_series(one) is False
print("== a one-wire chart is left alone")

# 8. THE WIRE ITSELF must be drawable: an Event Card reaches a Chart and
#    nothing else on the dashboard, because every other card shows one value.
spec = wf.REGISTRY["dashboard.events"]
ok, why = wf.edge_allowed("dashboard.events", "dashboard.graph")
assert ok, why
ok, _why = wf.edge_allowed("dashboard.events", "dashboard.card")
assert not ok
print("== an Event Card may be wired into a Chart, and into nothing else")

# ...and the ports the node offers are the ones the compiler reads.
assert wf.GRAPH_SERIES_HANDLES[0] == "in", wf.GRAPH_SERIES_HANDLES
assert len(wf.GRAPH_SERIES_HANDLES) == 8, wf.GRAPH_SERIES_HANDLES
assert wf.GRAPH_SERIES_IDS[0] == "s1", wf.GRAPH_SERIES_IDS
assert spec["category"] == "dashboard"

# 9. THE STYLE COMES BACK FROM THE BUILDER. A wf_* card is rebuilt from its
#    node on every compile, so a colour picked on the dashboard side has to
#    reach the NODE or it lasts until the next save -- which is what
#    card-style is for, and `series` has to be on its list like every other
#    thing a Builder owns.
assert "series" in wf.CARD_STYLE_FIELDS, wf.CARD_STYLE_FIELDS


def store_of(nodes, edges):
    return {"version": 3, "workflows": [
        {"id": wf.MAIN_ID, "name": wf.MAIN_NAME, "enabled": True,
         "graph": {"nodes": [dict(n) for n in nodes], "edges": edges}}]}


def node_of(store, nid="g"):
    return next(n for n in store["workflows"][0]["graph"]["nodes"]
                if n["id"] == nid)


NODES = BASE + [chart({"series": {"s1": {"label": "Zone A count",
                                        "color": "#04bade"}}})]
EDGES = BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "fr2", "target": "g", "targetHandle": "s2"}]

# a colour for series 2 alone must not disturb series 1
store = store_of(NODES, EDGES)
assert wf.apply_card_style(store, [{"id": "wf_g",
                                    "series": {"s2": {"color": "#f59e0b"}}}]) == 1
assert node_of(store)["data"]["series"] == {
    "s1": {"label": "Zone A count", "color": "#04bade"},
    "s2": {"color": "#f59e0b"}}, node_of(store)["data"]["series"]
print("== a Builder colour for one series merges in and leaves the others "
      "alone")

# ...a name for series 2 afterwards keeps the colour it was just given
assert wf.apply_card_style(store, [{"id": "wf_g",
                                    "series": {"s2": {"label": "Helmets"}}}]) == 1
assert node_of(store)["data"]["series"]["s2"] == {"color": "#f59e0b",
                                                  "label": "Helmets"}
print("== and a name written afterwards keeps the colour")

# ...an id no chart has is ignored, not stored: nothing would ever draw it.
before = dict(node_of(store)["data"]["series"])
assert wf.apply_card_style(store, [{"id": "wf_g", "series": {
    "s9": {"color": "#ffffff"}, "nonsense": {"label": "x"}}}]) == 0
assert node_of(store)["data"]["series"] == before, node_of(store)["data"]
print("== an unknown series id is ignored, not stored")

# ...and a colour that is not a colour clears rather than lands, exactly as
#    the Builder's own save cleans it.
assert wf.apply_card_style(store, [{"id": "wf_g",
                                    "series": {"s2": {"color": "red"}}}]) == 1
assert node_of(store)["data"]["series"]["s2"] == {"label": "Helmets"}
print("== a colour that is not #rrggbb is dropped, not stored")

# THE MALFORMED VALUE. A string or a list is not a series map, and the
# generic "str(value)[:32]" fallback would write its repr straight over the
# whole field -- every other series' name and colour gone, in an edit that
# reported success. It has its own branch, and that branch refuses.
for junk in ("red", ["#fff"], 7, None):
    keep = dict(node_of(store)["data"]["series"])
    assert wf.apply_card_style(store, [{"id": "wf_g", "series": junk}]) == 0, junk
    assert node_of(store)["data"]["series"] == keep, junk
    assert wf.clean_card_series(junk) is None, junk
print("== a malformed series value is refused and the stored style untouched")

# ...and the branch cannot silently disappear: the fallback stringifies, and
#    a stringified dict is not a dict.
assert isinstance(node_of(store)["data"]["series"], dict)
print("== the generic fallback never reaches the series field")

# THE ROUND TRIP, end to end: node -> compile -> widget -> card-style ->
# recompile. This is the whole point -- a colour picked in the Builder has to
# survive the next compile, and the canvas has to be showing the same one.
store = store_of(NODES, EDGES)


def widget_series(store):
    g = store["workflows"][0]["graph"]
    c = compile_(g["nodes"], g["edges"])
    assert not c["errors"], c["errors"]
    w = next(x for x in c["widgets"] if x["id"] == "wf_g")
    return {s["id"]: (s["label"], s["color"]) for s in w["series"]}


assert widget_series(store)["s1"] == ("Zone A count", "#04bade")
assert widget_series(store)["s2"] == ("Helmets", "")
wf.apply_card_style(store, [{"id": "wf_g", "series": {
    "s1": {"color": "#22c55e"},
    "s2": {"label": "Helmet checks", "color": "#f59e0b"}}}])
after = widget_series(store)
assert after["s1"] == ("Zone A count", "#22c55e"), after
assert after["s2"] == ("Helmet checks", "#f59e0b"), after
print("== the Builder's names and colours survive the recompile:", after)

# 10. THE QUESTION THE WIRE LEAVES OPEN.
#
# A row in the canvas's Inputs list can answer what the wire did not: the
# per-bucket fold, and for a table column what to group by, over which period,
# along which column. Those four live on the node beside the label and the
# colour, and the compile had been reading only the two -- so every control
# the panel offered wrote a value nothing ever read.
#
# The rule is the wire's: where it FIXES a field the node value is ignored,
# because a chart rewired from a table column to a rule still holds the old
# row's grouping, and honouring it would group a live key by a column that is
# not in the picture any more.
counting_ds = {"id": "ds", "type": "output.dataset", "position": {"x": 2, "y": 3},
               "data": {"dataset": "counting", "name": "Counting",
                        "mode": "change", "sources": {}}}
runs_ds = {"id": "ds", "type": "output.dataset", "position": {"x": 2, "y": 3},
           "data": {"dataset": "runs", "name": "Runs", "mode": "change",
                    "sources": {}}}


def with_column(table_node, column, series, kind="line"):
    """A chart with one rule on `in` and one table column on s2."""
    return compile_(BASE + [chart({"kind": kind, "series": series}), table_node],
                    BASE_EDGES + [
        {"id": "e3", "source": "fr", "target": "ds",
         "targetHandle": "c_" + column},
        {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
        {"id": "w2", "source": "ds", "sourceHandle": "o_" + column,
         "target": "g", "targetHandle": "s2"}])


def only(c, nid="wf_g"):
    return next(x for x in c["widgets"] if x["id"] == nid)


c = with_column(counting_ds, "live_counting",
                {"s2": {"agg": "sum", "ts_field": "",
                        "query": {"group_by": "shift", "range": "today"}}})
assert not c["errors"], c["errors"]
col = only(c)["series"][1]
assert col["agg"] == "sum", col
assert col["query"] == {"group_by": "shift", "range": "today"}, col
assert col["ts_field"] == "", col
print("== a table column takes its fold, grouping and period off the node")

# ...and they survive the layout validator, which is where a series field
# nothing can ask for gets dropped on the floor. The four are only real if
# they reach the resolver.
kept = builder_service.validate([only(c)])[0]["series"][1]
assert kept["agg"] == "sum", kept
assert kept["query"]["group_by"] == "shift", kept
assert kept["query"]["range"] == "today", kept
assert kept["ts_field"] == "", kept
print("== and the stored layout keeps all four")

# ...and a BLANK timestamp column is an answer, not an absence: it is the
# operator choosing the clock each row arrived on over the date column the
# compiler picks for itself.
assert only(with_column(runs_ds, "parts", {}))["series"][1]["ts_field"] \
    == "started"
assert only(with_column(runs_ds, "parts",
                        {"s2": {"ts_field": ""}}))["series"][1]["ts_field"] == "", \
    "a blank timestamp column must beat the compiler's own pick"
print("== a blank timestamp column is the row clock, chosen on purpose")

# ...a ts_field naming a column the table no longer has WARNS and keeps
# drawing. Blanking the series instead is a chart that loses a line and names
# nothing.
c = with_column(runs_ds, "parts", {"s2": {"ts_field": "nope"}})
assert not c["errors"], c["errors"]
assert any("nope" in x["message"] and x["node"] == "g"
           for x in c["warnings"]), c["warnings"]
assert only(c)["series"][1]["ts_field"] == "started", only(c)["series"][1]
print("== a timestamp column that is gone warns, and the line still draws")

# ...a fold this chart cannot work out is refused the same way.
c = with_column(counting_ds, "live_counting", {"s2": {"agg": "median"}})
assert any("median" in x["message"] for x in c["warnings"]), c["warnings"]
assert only(c)["series"][1]["agg"] == "avg"
print("== a fold nothing can work out is refused, and the default stands")

# A RULE WIRE fixes where it reads and what it is keyed by. The fold is the
# one thing left open -- and only on a time shape, which is the only shape
# with buckets to fold into.
c = compile_(BASE + [chart({"series": {"s2": {
    "agg": "max", "ts_field": "started",
    "query": {"group_by": "shift", "range": "today"}}}})], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "fr2", "target": "g", "targetHandle": "s2"}])
assert not c["errors"], c["errors"]
line = only(c)["series"][1]
assert line["agg"] == "max", line
assert "query" not in line and "ts_field" not in line, \
    "the wire fixes where a rule reads - a grouping from another row must " \
    "not follow it there"
print("== a rule wire takes the fold and ignores what the wire already fixed")

# ...and on a COMPARISON chart there are no buckets, so the canvas offers no
# fold and the compiler must not apply one either.
c = compile_(BASE + [chart({"kind": "bar", "series": {"s2": {"agg": "max"}}})],
             BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "fr2", "target": "g", "targetHandle": "s2"}])
bar = only(c)["series"][1]
assert bar["source"] == "stat" and bar["agg"] == "avg", bar
print("== a comparison chart has no buckets, so no fold is applied")

# AN EVENT CARD carries its own question, and answering it twice is how two
# editors come to disagree. Nothing on the row reaches the series.
c = compile_(BASE + [chart({"series": {"s2": {
    "agg": "max", "ts_field": "started",
    "query": {"group_by": "shift", "range": "today"}}}}), ev], BASE_EDGES + [
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ev", "target": "g", "targetHandle": "s2"}])
assert not c["errors"], c["errors"]
q = only(c)["series"][1]
assert q["source"] == "query" and q["agg"] == "count", q
assert "group_by" not in q["query"], q
assert "ts_field" not in q, q
print("== an Event Card answers its own question; the row cannot overrule it")


# 11. THE BUILDER WRITES THE SAME FOUR FIELDS.
#
# They are one setting, not two: a fold set on the canvas and a fold set in
# the Builder are the same fold on the same node, and a card-style write that
# accepted only the label and the colour would quietly undo the other editor.
store = store_of(NODES, EDGES)
assert wf.apply_card_style(store, [{"id": "wf_g", "series": {"s2": {
    "agg": "sum", "ts_field": "started",
    "query": {"group_by": "shift", "range": "today"}}}}]) == 1
# A subset, on purpose: this store shares its node data with section 9, which
# already put a name and a colour on this series. That IS the check -- the
# four fields land beside the two older ones rather than over them.
s2 = node_of(store)["data"]["series"]["s2"]
assert {k: s2.get(k) for k in ("agg", "ts_field", "query")} == {
    "agg": "sum", "ts_field": "started",
    "query": {"group_by": "shift", "range": "today"}}, s2
assert s2.get("label") and s2.get("color"), \
    "a fold written in the Builder must not blank the name beside it"
# ...merged per field: a period written afterwards keeps the grouping.
assert wf.apply_card_style(store, [{"id": "wf_g", "series": {
    "s2": {"query": {"range": "week"}}}}]) == 1
assert node_of(store)["data"]["series"]["s2"]["query"] == {
    "group_by": "shift", "range": "week"}
print("== the Builder writes the fold, the grouping and the period too")

# ...and "" is how a row says "keep the compiler's guess": it DELETES the key,
# so absent is the only way that answer is ever stored. The blank timestamp
# column is the exception, and deliberately: its blank names the row clock.
assert wf.apply_card_style(store, [{"id": "wf_g", "series": {"s2": {
    "agg": "", "query": {"group_by": "", "range": ""}}}}]) == 1
s2 = node_of(store)["data"]["series"]["s2"]
assert "agg" not in s2 and "query" not in s2, s2
assert s2["ts_field"] == "started", s2
assert wf.apply_card_style(store, [{"id": "wf_g",
                                    "series": {"s2": {"ts_field": ""}}}]) == 1
assert node_of(store)["data"]["series"]["s2"].get("ts_field") == "", \
    node_of(store)["data"]["series"]["s2"]
print('== "" clears a fold back to the default, and keeps a blank row clock')

# THE END-TO-END PIN: node -> compile -> widget series -> the Builder writes a
# fold -> recompile -> the fold is still there. A wf_* card is rebuilt from
# its node every time, so this is the only way a Builder edit survives at all.
store = store_of(BASE + [chart({"kind": "line"}), counting_ds], BASE_EDGES + [
    {"id": "e3", "source": "fr", "target": "ds",
     "targetHandle": "c_live_counting"},
    {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ds", "sourceHandle": "o_live_counting",
     "target": "g", "targetHandle": "s2"}])


def widget_aggs(store):
    g = store["workflows"][0]["graph"]
    got = compile_(g["nodes"], g["edges"])
    assert not got["errors"], got["errors"]
    return {s["id"]: s["agg"] for s in only(got)["series"]}


assert widget_aggs(store) == {"s1": "avg", "s2": "avg"}, widget_aggs(store)
wf.apply_card_style(store, [{"id": "wf_g",
                             "series": {"s2": {"agg": "sum"}}}])
assert widget_aggs(store)["s2"] == "sum", widget_aggs(store)
print("== a fold picked in the Builder survives the next compile")

print("ALL OK")
