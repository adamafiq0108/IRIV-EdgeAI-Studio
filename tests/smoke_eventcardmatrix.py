# Every Event Card a saved canvas can hold compiles the same after it becomes
# a Card or a Chart -- across the WHOLE matrix, not eleven hand-picked cards.
#
# WHAT COULD GO WRONG. The Event Card was folded into the Card and the Chart
# (data.source_mode), and migrate_merged_nodes rewrites each one on open.
# smoke_eventcardmerge pins eleven fixtures against a snapshot of the
# pre-change compiler. Eleven is not the field: an Event Card could be any of
# fifteen shapes, in three modes, unwired / scoped by a rule / wired into a
# Chart as a series, bucketed, filtered, grouped. The compile now reaches a
# migrated card through different doors than the Event Card used (the several-
# series chart branch skips a recorded Chart; migrate_chart_series skips it;
# the column-fed path picks the shape from CARD_KINDS on a Card) -- and a
# slip in any of them changes a customer's dashboard the first time the
# canvas is opened, with no error to say so.
#
# HOW IT IS PINNED. The deprecated Event Card still compiles, and on
# 2026-09-29 its path was checked equal to the pre-change compiler for all
# 318 cases of this kind of matrix (Yaya, adversarial review of 69e1563). So
# here each case is compiled twice with TODAY's code -- once as the saved
# Event Card, once after the full on-open migration chain the route runs --
# and the two must match, except the one change that was asked for: an
# unnamed card's placeholder title "Event Card" -> "Events".
#
# Deliberately NOT in the matrix: an Event Card fed by a Data Table COLUMN.
# There the Card path reads its shape from CARD_KINDS (the fix in 69e1563:
# a column-fed gauge/verdict used to draw as a tile), so a column-fed verdict
# now gains its states -- pinned below as the fix it is.
#
# Also pinned: the migration is idempotent on every case, keeps every data
# key and every wire, and writes an unset mode down as "events".
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

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"defects": {"key": "defects", "name": "Defects", "columns": [
    {"name": "weight", "type": "number"},
    {"name": "defect_type", "type": "text"},
    {"name": "line", "type": "text"}]}}
BANDS = {"at": [5, 10], "colors": ["#00ff00", "#ffff00", "#ff0000"]}

SRC = {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
       "data": {}}
RULE = {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
        "data": {"fnId": "in_frame", "name": "In frame", "mode": "realtime"}}
BASE, BASE_EDGES = [SRC, RULE], [{"id": "e0", "source": "s", "target": "fr"}]


def ev(**data):
    return {"id": "ev", "type": "dashboard.events",
            "position": {"x": 4, "y": 0}, "data": data}


def wire(src, tgt, handle=None):
    e = {"id": f"w_{src}_{tgt}_{handle}", "source": src, "target": tgt}
    if handle:
        e["targetHandle"] = handle
    return e


def chart():
    return {"id": "g", "type": "dashboard.graph", "position": {"x": 6, "y": 0},
            "data": {"name": "Mixed", "kind": "line",
                     "series": {"s2": {"label": "L2", "color": "#00aa00"}}}}


KINDS = ("tile", "gauge", "verdict", "status", "table", "bar", "line", "area",
         "spline", "donut", "pie", "radar", "timeline", "heatmap", "")


def matrix():
    fx = {}
    for k in KINDS:
        fx[f"unwired/{k}"] = ([ev(kind=k, agg="count", name="N",
                                  filters={"type": "alert"})], [])
        fx[f"unnamed-nokeys/{k}"] = ([ev(kind=k)], [])
        fx[f"wired/{k}"] = (BASE + [ev(kind=k, bands=BANDS, bucket="hour",
                                       group_by="camera")],
                            BASE_EDGES + [wire("fr", "ev")])
        fx[f"metric/{k}"] = (BASE + [ev(kind=k, source_mode="metric",
                                        metric="count", range="6h",
                                        states=[{"value": 3, "label": "HI",
                                                 "tone": "alert"}],
                                        ok_label="OK", bands=BANDS)],
                             BASE_EDGES + [wire("fr", "ev")])
        fx[f"metric-unwired/{k}"] = ([ev(kind=k, source_mode="metric")], [])
        fx[f"dataset/{k}"] = ([ev(kind=k, source_mode="dataset",
                                  dataset="defects", agg="sum", field="weight",
                                  group_by="defect_type", range="page")], [])
    for agg in ("count", "sum", "avg", "min", "max", "unique", "changes",
                "latest", ""):
        fx[f"dataset-agg/{agg}"] = ([ev(kind="bar", source_mode="dataset",
                                        dataset="defects", agg=agg,
                                        field="weight", group_by="line",
                                        conditions=[{"field": "line",
                                                     "value": "L1"}])], [])
        fx[f"dataset-bucket/{agg}"] = ([ev(kind="line", source_mode="dataset",
                                           dataset="defects", agg=agg,
                                           field="weight", bucket="day")], [])
        fx[f"events-agg/{agg}"] = ([ev(kind="bar", agg=agg, field="camera",
                                       group_by="zone")], [])
    for b in ("10s", "1m", "5m", "15m", "hour", "day", "week", "month"):
        fx[f"bucket/{b}"] = ([ev(kind="line", bucket=b)], [])
    for r in ("", "page", "today", "week", "month"):
        fx[f"range/{r}"] = ([ev(kind="bar", range=r)], [])
    fx["dataset/missing-table"] = ([ev(kind="bar", source_mode="dataset",
                                       dataset="gone")], [])
    fx["conditions"] = ([ev(kind="tile", conditions=[
        {"field": f, "value": "v"} for f in
        ("camera", "zone", "severity", "model", "direction", "nope")])], [])
    # An Event Card wired into a Chart, in every mode and on every port shape
    # a canvas can hold: a named series port beside a rule, or stacked on the
    # plain port (migrate_chart_series spreads that one).
    for mode in ("", "events", "dataset", "metric"):
        extra = ({"dataset": "defects", "agg": "avg", "field": "weight"}
                 if mode == "dataset" else {"group_by": "camera",
                                            "bucket": "hour"})
        for k in ("tile", "bar", "line", "table"):
            fx[f"series/{mode or 'unset'}/{k}/s2"] = (
                BASE + [chart(), ev(kind=k, source_mode=mode, **extra)],
                BASE_EDGES + [wire("fr", "g", "in"), wire("ev", "g", "s2")])
            fx[f"series/{mode or 'unset'}/{k}/stacked"] = (
                BASE + [chart(), ev(kind=k, source_mode=mode, **extra)],
                BASE_EDGES + [wire("fr", "g"), wire("ev", "g")])
    return fx


# The migrations the workflow route runs on open, in its order.
CHAIN = ("standardize_alerts", "migrate_merged_nodes", "migrate_series_zones",
         "migrate_inline_triggers", "migrate_pid_to_oid",
         "migrate_dwell_count_alerts", "migrate_split_alerts",
         "migrate_condition_metrics", "migrate_branch_family",
         "migrate_math_family", "migrate_counter_merge",
         "migrate_object_merge", "migrate_cycle_modes",
         "migrate_compare_merge", "migrate_clip_window",
         "migrate_chart_series", "prune_duplicate_wires")


def compile_(graph):
    out = wf.compile_graph(wf.clean_graph(copy.deepcopy(graph)), ZONES,
                           tables=TABLES)
    return json.loads(json.dumps(
        {"widgets": out["widgets"], "functions": out["functions"],
         "errors": out["errors"], "warnings": out["warnings"]}))


fx = matrix()
checked = 0
for name, (nodes, edges) in fx.items():
    saved = {"nodes": copy.deepcopy(nodes), "edges": copy.deepcopy(edges)}
    before = compile_(saved)
    for w in before["widgets"]:
        if w.get("title") == "Event Card":
            w["title"] = "Events"

    opened = copy.deepcopy(saved)
    for step in CHAIN:
        getattr(wf, step)(opened)
    moved = next(n for n in opened["nodes"] if n["id"] == "ev")
    want_type = ("dashboard.graph" if moved["data"].get("kind") in wf.CHART_KINDS
                 else "dashboard.card")
    assert moved["type"] == want_type, (name, moved)
    assert moved["data"]["source_mode"] == \
        (nodes[-1]["data"].get("source_mode") or "events"), (name, moved)
    lost = set(nodes[-1]["data"]) - set(moved["data"])
    assert not lost, (name, "data keys dropped by the migration", lost)

    after = compile_(opened)
    assert after == before, (name, json.dumps(before, indent=1, sort_keys=True),
                             json.dumps(after, indent=1, sort_keys=True))

    again = copy.deepcopy(opened)
    assert wf.migrate_merged_nodes(again) is False, name
    assert again == opened, (name, "a second migration changed the graph")
    checked += 1
print(f"== {checked} Event Card shapes compile the same as a Card/Chart")

# THE FIX, pinned as one: a VERDICT fed by a Data Table column keeps its
# states once it is a Card (it drew without them as an Event Card).
ds = {"id": "dt", "type": "output.dataset", "position": {"x": 0, "y": 0},
      "data": {"dataset": "defects"}}
g = {"nodes": [ds, ev(kind="verdict", name="Heavy",
                      states=[{"value": 1, "label": "HEAVY", "tone": "alert"}])],
     "edges": [{"id": "c", "source": "dt", "target": "ev",
                "sourceHandle": "o_weight"}]}
wf.migrate_merged_nodes(g)
w = next(x for x in compile_(g)["widgets"] if x["id"] == "wf_ev")
assert w["kind"] == "verdict" and w["states"] == [
    {"value": 1, "label": "HEAVY", "tone": "alert"}], w
print("== a column-fed verdict keeps its states as a Card")
print("ALL OK")
