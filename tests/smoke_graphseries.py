# A Chart with MORE THAN ONE input draws each input as its own series on one
# shared, aligned time axis.
#
# The owner's pcb_sensata canvas: two Data Tables (one per camera), both
# wired from their `status` column into two Charts -- "Total Violation
# (Day/Days)" (bar, count, one bar per day, follow the Analytics range) and
# "Total Violation (Week)" (spline, forecast, one point per day, this week).
# With one input both charts were right. With the second wired in:
#
#   - the chart's own "one bar per day", its fold and its period (set while it
#     had one input, kept on the node) were not read at all by the series
#     compile: the bar chart counted each STATUS WORD over all time (one bar
#     labelled VIOLATION), the spline went onto the sampler's 24h grid
#   - on that grid a count was folded as "count" -- the number of store
#     buckets in a cell -- so every hour with any rows read exactly 1
#   - "week" is not a history range, so the spline drew 24h, not the week
#   - both lines were called "status", so the legend could not say which
#     camera was which
#   - a series set to "follow the Analytics range" was never resolved and
#     read all time
#
# What is pinned here, with series 1 holding rows on days 1-5 and series 2 on
# days 3-4 (and a variant where series 2 has no rows at all):
#   1. the compile carries the chart's period/fold/range onto every table
#      series, names the lines after their Data Table nodes, and leaves the
#      Card and the single-input chart exactly as they were
#   2. the render puts both series on the union of days, each value on its
#      own day, zeros where a camera recorded nothing -- never NaN, never a
#      shifted day, never a dropped series
#   3. "page" on a series follows the Analytics date pick
#   4. a count on the sampler grid folds as a sum, and "week" is 7 days
import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                 # noqa: E402
from core.services import builder                               # noqa: E402
from core.services import history as history_service            # noqa: E402
from core.services import workflow as wf                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


COLS = [{"name": "event_id", "type": "text"}, {"name": "ts", "type": "datetime"},
        {"name": "datetime", "type": "text"}, {"name": "zone", "type": "text"},
        {"name": "total_panel", "type": "number"},
        {"name": "status", "type": "text"}]
TABLES = {"pcb_sensata": {"key": "pcb_sensata", "name": "pcb_sensata",
                          "columns": COLS},
          "pcb_sensata_cam2": {"key": "pcb_sensata_cam2",
                               "name": "pcb_sensata_cam2", "columns": COLS}}


def table(nid, key, name, y):
    return {"id": nid, "type": "output.dataset", "position": {"x": 0, "y": y},
            "data": {"dataset": key, "name": name, "mode": "alert"}}


BAR = {"id": "g1", "type": "dashboard.graph", "position": {"x": 4, "y": 0},
       "data": {"name": "Total Violation (Day/Days)", "kind": "bar",
                "agg": "count", "bucket": "day", "range": "page",
                "window": "follow", "unit": "violation", "group_by": "",
                "metric": ""}}
SPLINE = {"id": "g2", "type": "dashboard.graph", "position": {"x": 4, "y": 2},
          "data": {"name": "Total Violation (Week)", "kind": "spline",
                   "template": "forecast", "range": "week", "bucket": "day",
                   "window": "week", "agg": ""}}
CARD = {"id": "c2", "type": "dashboard.card", "position": {"x": 4, "y": 4},
        "data": {"name": "Total Violation CAM 2 (Day/Days)"}}
NODES = [table("t1", "pcb_sensata", "PCB-SENSATA", 0),
         table("t2", "pcb_sensata_cam2", "PCB-SENSATA-CAM2", 2)]


def wire(eid, src, tgt, port):
    return {"id": eid, "source": src, "target": tgt,
            "sourceHandle": "o_status", "targetHandle": port}


EDGES = [wire("a1", "t1", "g1", "in"), wire("a2", "t2", "g1", "s2"),
         wire("b1", "t1", "g2", "in"), wire("b2", "t2", "g2", "s2"),
         wire("c1", "t2", "c2", "in")]


def compile_(nodes, edges):
    out = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                           [], tables=TABLES)
    return out, {w["id"]: w for w in out["widgets"]}


# -- 1. the compile ----------------------------------------------------------
out, widgets = compile_(NODES + [BAR, SPLINE, CARD], EDGES)
check("the owner's canvas compiles", not out["errors"], out["errors"])
bar, spline, card = widgets.get("wf_g1"), widgets.get("wf_g2"), widgets.get("wf_c2")
check("both charts compile to two series",
      bar and spline and len(bar["series"]) == 2 and len(spline["series"]) == 2,
      (bar, spline))
for name, w, period in (("bar", bar, "page"), ("spline", spline, "week")):
    qs = [s.get("query") or {} for s in w["series"]]
    check(f"{name}: every input is one point per DAY",
          [q.get("bucket") for q in qs] == ["day", "day"], qs)
    check(f"{name}: ...not one bar per status word",
          not any(q.get("group_by") for q in qs), qs)
    check(f"{name}: ...over the chart's own period ({period})",
          [q.get("range") for q in qs] == [period, period], qs)
    check(f"{name}: ...counting rows",
          [s["agg"] for s in w["series"]] == ["count", "count"], w["series"])
    check(f"{name}: the legend names the cameras' tables, not 'status' twice",
          [s["label"] for s in w["series"]] == ["PCB-SENSATA", "PCB-SENSATA-CAM2"],
          [s["label"] for s in w["series"]])
    check(f"{name}: each series reads its own table",
          [s["dataset"] for s in w["series"]] == ["pcb_sensata", "pcb_sensata_cam2"],
          w["series"])
check("the spline keeps its forecast look", spline["template"] == "forecast",
      spline.get("template"))

# A name typed on the series row is the operator's and is never replaced.
named = {**BAR, "data": {**BAR["data"], "series": {"s2": {"label": "Cam 2"}}}}
_out, w2 = compile_(NODES + [named], EDGES[:2])
check("a typed series name wins",
      [s["label"] for s in w2["wf_g1"]["series"]] == ["status", "Cam 2"],
      [s["label"] for s in w2["wf_g1"]["series"]])

# The Card and a single-input chart compile exactly as before.
check("the card is untouched: one dataset widget counting cam 2's status",
      card and card["source"] == "dataset" and card["dataset"] == "pcb_sensata_cam2"
      and "series" not in card and card["query"]["field"] == "status", card)
_out, single = compile_(NODES + [BAR], [EDGES[0]])
one = single["wf_g1"]
check("a single-input bar chart is the old single-source chart",
      one["source"] == "dataset" and "series" not in one
      and one["query"] == {"agg": "count", "field": "status", "bucket": "day",
                           "range": "page"}, one)

# A chart with NO period keeps the old several-series meaning: a bar chart of
# two word columns counts each word, as smoke_textcategories pins.
plain = {**BAR, "data": {**BAR["data"], "bucket": "", "agg": "", "range": "24h"}}
_out, w3 = compile_(NODES + [plain], EDGES[:2])
check("without a period nothing changes (counted per word)",
      [(s.get("query") or {}).get("group_by") for s in w3["wf_g1"]["series"]]
      == ["status", "status"]
      and not any((s.get("query") or {}).get("bucket") for s in w3["wf_g1"]["series"]),
      w3["wf_g1"]["series"])

# "week" on a time chart's history axis is seven days, not the 24h fallback.
weekly = {**SPLINE, "data": {**SPLINE["data"], "bucket": ""}}
_out, w4 = compile_(NODES + [weekly], EDGES[2:4])
check("a series chart on 'week' charts 7 days", w4["wf_g2"].get("range") == "7d",
      w4["wf_g2"].get("range"))


# -- 2. the render -------------------------------------------------------------
TODAY = datetime.date.today()


def midnight(days_ago):
    d = TODAY - datetime.timedelta(days=days_ago)
    return datetime.datetime(d.year, d.month, d.day).astimezone().isoformat()


# Series 1: rows on days 1-5 (4 days ago .. today). Series 2: days 3-4 only.
ROWS = {"pcb_sensata": {midnight(4): 3, midnight(3): 1, midnight(2): 4,
                        midnight(1): 1, midnight(0): 5},
        "pcb_sensata_cam2": {midnight(2): 2, midnight(1): 6}}
CALLS = []


def fake_aggregate(slug, dataset, columns, agg="count", field=None,
                   group_by=None, bucket=None, filters=None, since=None,
                   until=None, limit=500, config_dir=None):
    CALLS.append({"dataset": dataset, "agg": agg, "bucket": bucket,
                  "group_by": group_by, "since": since, "until": until})
    rows = ROWS.get(dataset) or {}
    if bucket == "day":
        # Only the days that HAVE rows, as an unbounded GROUP BY answers:
        # the alignment has to come from the resolver, not from the store.
        keys = sorted(rows)
        if since:
            keys = [k for k in keys if k >= since[:10] or
                    datetime.datetime.fromisoformat(k)
                    >= datetime.datetime.fromisoformat(since)]
        return {"buckets": [{"key": k, "value": float(rows[k])} for k in keys]}
    if group_by:
        return {"buckets": [{"key": "VIOLATION", "value": float(sum(rows.values()))}]}
    return {"buckets": [{"key": None, "value": float(sum(rows.values()))}]}


datasets_repo.aggregate = fake_aggregate


def render(widget, **page):
    return builder.compute([widget], [], {}, slug="pcb_sensata", **page)[0]


start = (TODAY - datetime.timedelta(days=6)).isoformat()
end = TODAY.isoformat()
# The WHOLE picked window is on the axis since the chart time axis model
# (services/timeaxis.py): the owner picked six days ago .. today, and the two
# days nobody recorded anything in are zero points, not missing ones.
LABELS = [midnight(d)[5:10] for d in (6, 5, 4, 3, 2, 1, 0)]

for name, w in (("bar", bar), ("spline", spline)):
    CALLS.clear()
    got = render(w, page_range="custom", page_start=start, page_end=end)
    data = got.get("data") or {}
    sets = data.get("datasets") or []
    check(f"{name}: renders one dataset per input", len(sets) == 2, data)
    check(f"{name}: the x axis is the union of days, in order",
          data.get("labels") == LABELS, data.get("labels"))
    check(f"{name}: camera 1 on its own days",
          sets and sets[0]["values"] == [0, 0, 3, 1, 4, 1, 5], sets and sets[0])
    check(f"{name}: camera 2 on ITS days, zero where it recorded nothing",
          len(sets) > 1 and sets[1]["values"] == [0, 0, 0, 0, 2, 6, 0],
          len(sets) > 1 and sets[1])
    check(f"{name}: legend names",
          [s["label"] for s in sets] == ["PCB-SENSATA", "PCB-SENSATA-CAM2"], sets)
    check(f"{name}: every store call asked per day, by row count",
          CALLS and all(c["bucket"] == "day" and c["agg"] == "count"
                        and not c["group_by"] for c in CALLS), CALLS)
    if name == "bar":
        check("bar: 'follow the Analytics range' reads the picked dates",
              CALLS and all(c["since"] and c["since"][:10] == start
                            and c["until"] for c in CALLS), CALLS)

# Variant: camera 2's table has no rows yet (or no table at all).
ROWS["pcb_sensata_cam2"] = {}
got = render(bar, page_range="custom", page_start=start, page_end=end)
sets = (got.get("data") or {}).get("datasets") or []
check("an empty second input draws as zeros beside the first",
      len(sets) == 2 and sets[0]["values"] == [0, 0, 3, 1, 4, 1, 5]
      and sets[1]["values"] == [0] * 7, sets)
check("...and no value anywhere is null or NaN",
      all(isinstance(v, (int, float)) and v == v
          for s in sets for v in s["values"]), sets)


# -- 3. a count folded on the sampler grid ------------------------------------
# A table series without a period on a line chart is drawn on the sampler's
# grid. The store already counted the rows in each store bucket; folding that
# answer as "count" counted store buckets, so 7 rows in an hour read 1.
g = history_service.grid("24h")
cell = datetime.datetime.fromtimestamp(g["start"] + 3 * g["step"]).astimezone().isoformat()


def one_cell(slug, dataset, columns, agg="count", **_kw):
    return {"buckets": [{"key": cell, "value": 7.0}]}


datasets_repo.aggregate = one_cell
line = {"id": "x", "title": "x", "kind": "line", "source": "history",
        "range": "24h",
        "series": [{"id": "s1", "source": "dataset", "dataset": "pcb_sensata",
                    "columns": COLS, "field": "status", "agg": "count"}]}
vals = (render(line).get("data") or {}).get("datasets", [{}])[0].get("values") or []
check("a counted table series on the time grid shows the count, not 1",
      7 in vals and 1 not in vals, [v for v in vals if v is not None])


print(f"\n{bad} failure(s)" if bad else "\nall graph-series checks passed")
sys.exit(1 if bad else 0)
