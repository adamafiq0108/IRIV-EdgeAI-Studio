# The Event Card is folded into the Chart and the Card.
#
# Three nodes answered "show me a number or a chart on the dashboard", and the
# only thing the third added was WHERE the number came from: the recorded
# event store, or a table named by hand, instead of a wire. That is a source,
# not a node -- so the Card and the Chart now carry it as `source_mode`, the
# Event Card leaves the palette, and every canvas that has one is rewritten on
# open (migrate_merged_nodes) into whichever of the two draws its shape.
#
# What is pinned here:
#   1. the palette no longer offers the Event Card; the type still LOADS
#   2. every Event Card shape migrates to Chart (bar/line/area/spline/donut/
#      pie/radar) or Card (tile/gauge/verdict/status/table)
#   3. the migrated node compiles to the SAME widget the Event Card compiled
#      to before this change -- checked against a snapshot taken from the
#      pre-change compiler (smoke_eventcardmerge_baseline.json), not against
#      the deprecated path, which could drift along with the new one
#   4. an Event Card wired into a Chart as a series keeps drawing that series
#      after the card becomes a Card/Chart
#   5. an unwired events Chart compiles on its own
#   6. the starter dashboards adopt their cards as Card/Chart, not Event Card
#
# Run with --write-baseline ONLY against the pre-change compiler.
import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

BASELINE = os.path.join(ROOT, "tests", "smoke_eventcardmerge_baseline.json")
ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"defects": {"key": "defects", "name": "Defects", "columns": [
    {"name": "weight", "type": "number"},
    {"name": "defect_type", "type": "text"},
    {"name": "line", "type": "text"}]}}


def rule(nid, fn_id, name):
    return {"id": nid, "type": "analytics.frame", "position": {"x": 1, "y": 0},
            "data": {"fnId": fn_id, "name": name, "mode": "realtime"}}


def card(nid, **data):
    return {"id": nid, "type": "dashboard.events",
            "position": {"x": 4, "y": 0}, "data": data}


BASE = [{"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        rule("fr", "in_frame", "In frame")]
BASE_EDGES = [{"id": "e0", "source": "s", "target": "fr"}]


def fixtures():
    """{name: (nodes, edges, the id of the node that was an Event Card)}.

    One per shape the Event Card was used in on a real box, plus the wire
    into a Chart, which is the one Event Card behaviour that is not a widget
    of its own."""
    fx = {}
    # A tile counting events with conditions, UNWIRED: the whole store.
    fx["tile_events_conditions"] = (
        [card("ev", name="Helmet alerts", kind="tile", agg="count",
              filters={"type": "violation"}, range="today",
              conditions=[{"field": "camera", "value": "cam2"},
                          {"field": "severity", "value": "alert"},
                          {"field": "", "value": "ignored"}])], [], "ev")
    # ...and the same tile scoped by a wired rule.
    fx["tile_events_wired"] = (
        BASE + [card("ev", name="Raised by In frame", kind="tile",
                     agg="unique", field="camera", range="week")],
        BASE_EDGES + [{"id": "w", "source": "fr", "target": "ev"}], "ev")
    # A bar grouped by camera, bucketed per hour.
    fx["bar_camera_hour"] = (
        [card("ev", name="Per camera", kind="bar", agg="count",
              group_by="camera", bucket="hour", range="today",
              color="#ff8800")], [], "ev")
    # A donut in DATASET mode: a table named, not wired.
    fx["donut_dataset"] = (
        [card("ev", name="Defect mix", kind="donut", source_mode="dataset",
              dataset="defects", agg="sum", field="weight",
              group_by="defect_type", range="month",
              conditions=[{"field": "line", "value": "L2"}])], [], "ev")
    # A recent-events TABLE.
    fx["table_events"] = (
        [card("ev", name="Latest", kind="table", limit=25)], [], "ev")
    # ...and the old Events Table node, which became an Event Card first.
    fx["legacy_events_table"] = (
        [{"id": "ev", "type": "dashboard.table", "position": {"x": 4, "y": 0},
          "data": {"agg": "", "range": "", "limit": 15}}], [], "ev")
    # A VERDICT in metric mode on a wired rule with no trigger of its own --
    # the Event Card defaulted that to `active`, the plain Card would not.
    fx["verdict_metric"] = (
        BASE + [card("ev", name="State", kind="verdict", source_mode="metric",
                     ok_label="CLEAR", alert_label="BUSY")],
        BASE_EDGES + [{"id": "w", "source": "fr", "target": "ev"}], "ev")
    # A line chart in metric mode.
    fx["line_metric"] = (
        BASE + [card("ev", name="In frame over time", kind="line",
                     source_mode="metric", metric="count", range="6h")],
        BASE_EDGES + [{"id": "w", "source": "fr", "target": "ev"}], "ev")
    # An UNWIRED, UNNAMED events chart.
    fx["line_events_unnamed"] = (
        [card("ev", kind="line", agg="count", bucket="15m",
              filters={"type": "alert"})], [], "ev")
    # An Event Card wired into a Chart as its second series.
    chart = {"id": "g", "type": "dashboard.graph", "position": {"x": 6, "y": 0},
             "data": {"name": "Mixed", "kind": "line",
                      "series": {"s2": {"label": "Alerts", "color": "#00aa00"}}}}
    fx["series_into_chart"] = (
        BASE + [chart, card("ev", name="Violations", kind="tile", agg="count",
                            bucket="hour", range="today",
                            conditions=[{"field": "zone", "value": "zone_a"}])],
        BASE_EDGES + [
            {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
            {"id": "w2", "source": "ev", "target": "g", "targetHandle": "s2"}],
        "ev")
    # ...and a DATASET-mode card as a chart series.
    fx["dataset_series_into_chart"] = (
        BASE + [copy.deepcopy(chart),
                card("ev", name="Weights", kind="bar", source_mode="dataset",
                     dataset="defects", agg="avg", field="weight")],
        BASE_EDGES + [
            {"id": "w1", "source": "fr", "target": "g", "targetHandle": "in"},
            {"id": "w2", "source": "ev", "target": "g", "targetHandle": "s2"}],
        "ev")
    return fx


def compile_(nodes, edges, migrate=False):
    g = copy.deepcopy({"nodes": nodes, "edges": edges})
    if migrate:
        wf.migrate_merged_nodes(g)
    out = wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)
    return g, out


def summary(out):
    """What a compile PRODUCED: widgets, functions, the problems it named."""
    return {"widgets": out["widgets"], "functions": out["functions"],
            "errors": out["errors"], "warnings": out["warnings"]}


if "--write-baseline" in sys.argv:
    snap = {}
    for name, (nodes, edges, _nid) in fixtures().items():
        _g, out = compile_(nodes, edges, migrate=True)
        snap[name] = summary(out)
    with open(BASELINE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(snap, f, indent=1, sort_keys=True)
        f.write("\n")
    print("baseline written:", len(snap), "fixtures")
    sys.exit(0)

with open(BASELINE, encoding="utf-8") as f:
    BEFORE = json.load(f)

# 1. THE PALETTE. Deprecated is what the palette filters on (Workflow.jsx and
#    ContextMenu.jsx both drop `deprecated`), and the type must still be in the
#    registry or a canvas saved last week fails to load at all.
reg = wf.registry_public()
assert "dashboard.events" in reg, "the Event Card type was deleted"
assert reg["dashboard.events"].get("deprecated"), "the Event Card is still offered"
for live in ("dashboard.card", "dashboard.graph"):
    assert not reg[live].get("deprecated"), live
    assert "source_mode" in reg[live]["data"], f"{live} has no source switch"
print("== the palette offers Card and Chart; the Event Card only loads")

# 2 + 3. EVERY SHAPE MIGRATES, AND COMPILES TO WHAT IT DID.
#
# Unnamed, the widget was titled after the retired node ("Event Card"); that
# placeholder is the one thing allowed to change, and it changes to "Events".
RETITLED = {"line_events_unnamed", "legacy_events_table"}
WANT_TYPE = {"tile_events_conditions": "dashboard.card",
             "tile_events_wired": "dashboard.card",
             "bar_camera_hour": "dashboard.graph",
             "donut_dataset": "dashboard.graph",
             "table_events": "dashboard.card",
             "legacy_events_table": "dashboard.card",
             "verdict_metric": "dashboard.card",
             "line_metric": "dashboard.graph",
             "line_events_unnamed": "dashboard.graph",
             "series_into_chart": "dashboard.card",
             "dataset_series_into_chart": "dashboard.graph"}
for name, (nodes, edges, nid) in fixtures().items():
    g, out = compile_(nodes, edges, migrate=True)
    moved = next(n for n in g["nodes"] if n["id"] == nid)
    assert moved["type"] == WANT_TYPE[name], (name, moved["type"])
    assert moved["data"].get("source_mode") in wf.EVENT_SOURCE_MODES, (name, moved)
    assert not any(n["type"] == "dashboard.events" for n in g["nodes"]), name
    # Same wires, untouched: ids, ends and handles.
    assert g["edges"] == edges, (name, g["edges"])
    got, want = summary(out), copy.deepcopy(BEFORE[name])
    if name in RETITLED:
        for w in want["widgets"]:
            if w["id"] == f"wf_{nid}":
                assert w["title"] == "Event Card", w
                w["title"] = "Events"
    # JSON round trip: the snapshot holds lists where a compile may hold
    # tuples, and the question is the value, not the container.
    got = json.loads(json.dumps(got))
    if name == "dataset_series_into_chart":
        # The one deliberate change against the snapshot: a dataset card
        # wired in as a series now carries its QUESTION (review item 1). The
        # snapshot recorded the bug -- no query, so one all-time total.
        s2 = next(s for s in got["widgets"][0]["series"] if s["id"] == "s2")
        assert s2.pop("query") == {"agg": "avg"}, s2
    assert got == want, (name, json.dumps(got, indent=1, sort_keys=True),
                         json.dumps(want, indent=1, sort_keys=True))
    print(f"== {name}: {moved['type']} ({moved['data']['source_mode']}), "
          f"same widget as before")

# ...and a migration run twice is a migration run once.
g, _out = compile_(*fixtures()["bar_camera_hour"][:2], migrate=True)
again = copy.deepcopy(g)
assert wf.migrate_merged_nodes(again) is False, "the migration is not idempotent"
assert again == g
print("== running the migration again changes nothing")

# 4. THE SERIES WIRE survives on its own terms too: after the move the
#    Chart's second line is still the card's question, with the node's own
#    label and colour for it.
_g, out = compile_(*fixtures()["series_into_chart"][:2], migrate=True)
chart = next(w for w in out["widgets"] if w["id"] == "wf_g")
s2 = next(s for s in chart["series"] if s["id"] == "s2")
assert s2["source"] == "query" and s2["label"] == "Alerts", s2
assert s2["query"]["filters"] == {"zone": "zone_a"}, s2
assert s2["query"]["bucket"] == "hour", s2
print("== a card wired into a chart still draws its question as a series")

# ...and a Card switched back to its wire has nothing to hand the chart: the
#    line is dropped and NAMED, the graph still compiles.
nodes, edges, _nid = fixtures()["series_into_chart"]
g = copy.deepcopy({"nodes": nodes, "edges": edges})
wf.migrate_merged_nodes(g)
next(n for n in g["nodes"] if n["id"] == "ev")["data"]["source_mode"] = ""
# ...showing the rule wired into it, so the card itself is a working card.
g["edges"].append({"id": "w3", "source": "fr", "target": "ev"})
out =wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)
assert not out["errors"], out["errors"]
assert any(w["node"] == "ev" and "no question to give the chart" in w["message"]
           for w in out["warnings"]), out["warnings"]
print("== a card showing a live value wired into a chart is named, not drawn")

# ...and the wire itself is still one the canvas may draw.
for src in ("dashboard.card", "dashboard.graph", "dashboard.events"):
    ok, why = wf.edge_allowed(src, "dashboard.graph")
    assert ok, (src, why)
ok, _why = wf.edge_allowed("dashboard.card", "dashboard.card")
assert not ok, "a card into a card means nothing"
print("== Card/Chart -> Chart is a legal wire; Card -> Card is not")

# 5. A NEW CHART ON RECORDED EVENTS, drawn today rather than migrated: no
#    wire, and it compiles to its own question.
fresh = {"id": "c1", "type": "dashboard.graph", "position": {"x": 0, "y": 0},
         "data": {**wf.REGISTRY["dashboard.graph"]["data"], "name": "Alerts",
                  "kind": "bar", "source_mode": "events", "group_by": "camera",
                  "filters": {"type": "alert"}, "range": "week"}}
out = wf.compile_graph(wf.clean_graph({"nodes": [fresh], "edges": []}),
                       ZONES, tables=TABLES)
assert not out["errors"], out["errors"]
w = out["widgets"][0]
assert (w["kind"], w["source"]) == ("bar", "query"), w
assert w["query"] == {"agg": "count", "group_by": "camera", "range": "week",
                      "filters": {"type": "alert"}}, w["query"]
print("== an unwired Chart on recorded events compiles:", w["query"])

# ...and a new Card as a recent-events table.
fresh = {"id": "c2", "type": "dashboard.card", "position": {"x": 0, "y": 0},
         "data": {**wf.REGISTRY["dashboard.card"]["data"], "name": "Log",
                  "kind": "table", "source_mode": "events", "limit": 20}}
out = wf.compile_graph(wf.clean_graph({"nodes": [fresh], "edges": []}),
                       ZONES, tables=TABLES)
assert not out["errors"], out["errors"]
w = out["widgets"][0]
assert (w["kind"], w["source"], w["limit"]) == ("table", "events", 20), w
print("== a Card on recorded events can be the recent-events table")

# ...while a plain Card, with nothing chosen, is exactly the card it was:
#    unwired it still asks for a wire.
plain = {"id": "c3", "type": "dashboard.card", "position": {"x": 0, "y": 0},
         "data": dict(wf.REGISTRY["dashboard.card"]["data"])}
out = wf.compile_graph(wf.clean_graph({"nodes": [plain], "edges": []}),
                       ZONES, tables=TABLES)
assert out["errors"] and "needs exactly one" in out["errors"][0]["message"], out
print("== a Card left on its wire still asks for one")

# 6. THE STARTER DASHBOARDS adopt their baked-in cards as Card/Chart.
pkg = tempfile.mkdtemp()
os.makedirs(os.path.join(pkg, "demo"))
with open(os.path.join(pkg, "demo", "layout.json"), "w", encoding="utf-8") as f:
    json.dump({"widgets": [
        {"id": "a", "title": "Total", "kind": "tile", "source": "query",
         "query": {"agg": "count", "filters": {"type": "violation"}}},
        {"id": "b", "title": "Per hour", "kind": "bar", "source": "query",
         "query": {"agg": "count", "bucket": "hour"}},
        {"id": "c", "title": "Log", "kind": "table", "source": "events"}]}, f)
adopted = wf.adopt_package_widgets(pkg, "demo")
assert [n["type"] for n in adopted] == ["dashboard.card", "dashboard.graph",
                                        "dashboard.card"], adopted
assert all(n["data"]["source_mode"] == "events" for n in adopted), adopted
out = wf.compile_graph(wf.clean_graph({"nodes": adopted, "edges": []}),
                       ZONES, tables=TABLES)
assert not out["errors"], out["errors"]
assert [(w["kind"], w["source"]) for w in out["widgets"]] == [
    ("tile", "query"), ("bar", "query"), ("table", "events")], out["widgets"]
print("== a starter dashboard's cards arrive as Card/Chart on recorded events")

# -- review fixes (Yaya, 2026-09-29) ----------------------------------------


def recorded_chart(nid, **data):
    return {"id": nid, "type": "dashboard.graph", "position": {"x": 2, "y": 0},
            "data": {**wf.REGISTRY["dashboard.graph"]["data"], **data}}


def bar_chart(nid="g"):
    return {"id": nid, "type": "dashboard.graph", "position": {"x": 6, "y": 0},
            "data": {**wf.REGISTRY["dashboard.graph"]["data"],
                     "name": "Why", "kind": "bar"}}


def run(nodes, edges):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                            ZONES, tables=TABLES)


# R1. A DATASET CARD WIRED IN AS A SERIES CARRIES ITS WHOLE QUESTION.
out = run([recorded_chart("a", name="Mix", kind="bar", source_mode="dataset",
                          dataset="defects", group_by="defect_type",
                          range="month",
                          conditions=[{"field": "line", "value": "L2"}]),
           recorded_chart("b", name="Weight", kind="bar", source_mode="dataset",
                          dataset="defects", group_by="line", range="today",
                          agg="sum", field="weight"),
           bar_chart()],
          [{"id": "w1", "source": "a", "target": "g", "targetHandle": "in"},
           {"id": "w2", "source": "b", "target": "g", "targetHandle": "s2"}])
assert not out["errors"], out["errors"]
g = next(w for w in out["widgets"] if w["id"] == "wf_g")
s1, s2 = g["series"]
assert s1["query"] == {"agg": "count", "group_by": "defect_type",
                       "range": "month", "filters": {"line": "L2"}}, s1
assert s2["query"] == {"agg": "sum", "group_by": "line", "range": "today"}, s2
assert s2["field"] == "weight" and s2["agg"] == "sum", s2
# ...and the widget survives the validator that renders it, question intact.
from core.services import builder as builder_service              # noqa: E402
from core.services import chart_series                            # noqa: E402
entries = chart_series.migrate(g["series"], g["source"], widget=g)
assert entries[0]["query"]["group_by"] == "defect_type", entries[0]
assert entries[1]["query"]["agg"] == "sum" \
    and entries[1]["query"]["range"] == "today", entries[1]
print("== R1 a dataset card as a series keeps group_by, range, agg, filters")

# R2. A RECORDED CHART WITH TWO RULES STILL WIRED is scoped to the first and
#     names the other, instead of counting the whole store.
rule2 = rule("fr2", "helmets", "Helmets")
out = run(BASE + [rule2, recorded_chart("g", name="Raised", kind="bar",
                                        source_mode="events",
                                        group_by="camera")],
          BASE_EDGES + [{"id": "e2", "source": "s", "target": "fr2"},
                        {"id": "w1", "source": "fr2", "target": "g",
                         "targetHandle": "s2"},
                        {"id": "w2", "source": "fr", "target": "g",
                         "targetHandle": "in"}])
assert not out["errors"], out["errors"]
w = next(x for x in out["widgets"] if x["id"] == "wf_g")
assert w["query"]["filters"] == {"function": "in_frame"}, w["query"]
msgs = [x["message"] for x in out["warnings"] if x["node"] == "g"]
assert any("'In frame' raised" in m and "'Helmets'" in m for m in msgs), msgs
assert not any("wire a function into it" in m for m in msgs), msgs
print("== R2 two rules into a recorded chart: scoped to the first, the other named")

# R3. ONE CARD ON A CHART'S PLAIN PORT is a series, not an error.
out = run([card("ev", name="Alerts", kind="tile", agg="count",
                bucket="hour"), bar_chart()],
          [{"id": "w1", "source": "ev", "target": "g"}])
assert not out["errors"], out["errors"]
g = next(w for w in out["widgets"] if w["id"] == "wf_g")
assert [s["source"] for s in g["series"]] == ["query"], g
print("== R3 a single card on the plain port draws as a series")
# ...and a card showing a live value there switches the chart off, saying so.
live = {"id": "c", "type": "dashboard.card", "position": {"x": 3, "y": 0},
        "data": dict(wf.REGISTRY["dashboard.card"]["data"])}
out = run(BASE + [live, bar_chart()],
          BASE_EDGES + [{"id": "w0", "source": "fr", "target": "c"},
                        {"id": "w1", "source": "c", "target": "g"}])
assert not out["errors"], out["errors"]
assert not any(w["id"] == "wf_g" for w in out["widgets"]), out["widgets"]
assert any(x["node"] == "g" and "nothing to draw" in x["message"]
           for x in out["warnings"]), out["warnings"]
print("== R3 ...a live-value card there switches the chart off with a reason")

# R9. MALFORMED BANDS are no bands, not a crash.
assert builder_service.clean_bands([1, 2]) == builder_service.clean_bands({})
out = run([card("ev", name="X", kind="tile", bands=[5, 10])], [])
assert not out["errors"], out["errors"]
print("== R9 a list where bands belong compiles as no bands")
print("ALL OK")
