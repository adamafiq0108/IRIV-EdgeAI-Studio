# The chart builder model (services/chartmodel.py): one X axis, one Y
# calculation, one interval and one date range per chart, every camera input
# sharing them unless it overrides one.
#
# Owner brief (2026-10-13), "Refactor the Chart and Graph Builder": charts vs
# time-series graphs, explicit X (database column or Time) and Y (count
# records, count non-null, sum, average, min, max), time settings only when X
# is Time, one shared config for every camera, a visibility toggle, and "values
# from one camera must never be assigned to another camera's series".
#
# Pinned through the real compile (workflow.compile_graph) and the real render
# (builder.compute) against a fake store keyed by TABLE, so a value that
# crossed from one camera's table to the other's line would show:
#
#   1. normalize_node: the new shape, idempotent, an older chart untouched
#   2. the per-input cleaners: show, count_nonnull, the stale-tab guard
#   3. a saved legacy chart and the same chart under the model draw the same
#   4. the matrix: chart kind x X type x Y calculation, data level
#   5. empty sets, nulls and missing steps
#   6. Week / Month / Custom ranges
#   7. series isolation: identical labels, equal values, two cameras
#   8. hidden inputs: listed, not drawn, not asked
#   9. the Date column steps and filters on the model path
#  10. Count records vs Count non-null values, in SQL and in memory
#  11. a Builder edit (card-style) lands in the same clean shape
import copy
import datetime
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                  # noqa: E402
from core.services import builder as builder_service             # noqa: E402
from core.services import chartmodel                             # noqa: E402
from core.services import timeaxis                               # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. normalize_node ---------------------------------------------------------
legacy = {"kind": "bar", "range": "24h", "bucket": "", "interval": "5m",
          "group_by": "status", "window": "today",
          "series": {"s1": {"agg": "sum", "query": {"range": "week"}}}}
before = copy.deepcopy(legacy)
chartmodel.normalize_node(legacy)
check("a chart saved before the model is left byte for byte as it was",
      legacy == before, legacy)

d = {"kind": "line", "x_type": "time", "interval": "5m", "bucket": "",
     "range": "7d", "agg": "sum", "ts_field": "made_on",
     "series": {"s1": {"agg": "sum", "query": {"range": "7d"}, "show": True,
                       "label": "CAM 1"},
                "s2": {"agg": "avg", "query": {"range": "today"}, "ts_field": "made_on"},
                "s3": {"show": False}}}
chartmodel.normalize_node(d)
check("the old Time step folds into the one step and is cleared",
      d["bucket"] == "5m" and d["interval"] == "", (d["bucket"], d["interval"]))
check("an interval preset is derived for a step that is not a preset",
      d["time_interval"] == "custom", d["time_interval"])
check("overrides that repeat the chart are dropped, real ones kept, a name kept",
      d["series"]["s1"] == {"label": "CAM 1"}
      and d["series"]["s2"] == {"agg": "avg", "query": {"range": "today"}}
      and d["series"]["s3"] == {"show": False}, d["series"])
again = copy.deepcopy(d)
chartmodel.normalize_node(again)
check("normalize_node is idempotent", again == d, (again, d))

d = {"kind": "pie", "x_type": "column", "group_by": "status", "range": "page",
     "window": "live", "agg": "latest"}
chartmodel.normalize_node(d)
check("a comparison chart's Count over follows its date range",
      d["window"] == "follow", d["window"])
check("a legacy Y answer is kept as saved, never rewritten",
      d["agg"] == "latest", d["agg"])
d = {"kind": "line", "x_type": "time", "group_by": "status", "bucket": "1h"}
chartmodel.normalize_node(d)
check("X = Time clears a column X (the two are alternatives)",
      d["group_by"] == "" and d["time_interval"] == "hour", d)
d = {"kind": "bar", "x_type": "sideways"}
chartmodel.normalize_node(d)
check("an x_type that is not one is dropped (back to the legacy path)",
      "x_type" not in d, d)
check("interval_of: hour, day, month (30 days of days), week, month, custom",
      [chartmodel.interval_of(s, r) for s, r in
       (("1h", "24h"), ("day", "7d"), ("1d", "30d"), ("week", "90d"),
        ("month", "365d"), ("", "24h"), ("6h", "7d"))]
      == ["hour", "day", "month", "week", "month", "custom", "custom"])
check("clean_widget: a chart with no model has none; junk is cleaned",
      chartmodel.clean_widget({"y_agg": "sum"}) == {}
      and chartmodel.clean_widget({"x_type": "column", "x_field": "a;drop",
                                   "y_agg": "bogus", "step": "7s",
                                   "range": "nope", "ts_field": "@row"})
      == {"x_type": "column", "x_field": "", "y_agg": "", "y_field": "",
          "time_interval": "custom", "step": "", "range": "", "ts_field": "@row"})

# -- 2. per-input cleaners -----------------------------------------------------
out = wf.clean_card_series({"s1": {"show": False, "agg": "count_nonnull"},
                            "s2": {"show": True, "label": "CAM 2"}},
                           {"s2": {"show": False}})
check("clean_card_series keeps a hidden flag and count_nonnull; shown is absence",
      out == {"s1": {"show": False, "agg": "count_nonnull"},
              "s2": {"label": "CAM 2"}}, out)
stored = {"nodes": [{"id": "g", "type": "dashboard.graph",
                     "data": {"kind": "bar", "series": {"s2": {"show": False}}}}]}
store = {"workflows": [{"graph": copy.deepcopy(stored)}]}
wf.apply_card_style(store, [{"id": "wf_g", "series": {"s2": {"show": False}}}])
store2 = {"workflows": [{"graph": {"nodes": [{"id": "g", "type": "dashboard.graph",
                                              "data": {"kind": "bar"}}]}}]}
wf.apply_card_style(store2, [{"id": "wf_g", "series": {"s2": {"show": False}}}])
node2 = store2["workflows"][0]["graph"]["nodes"][0]["data"]
stale = {"nodes": [{"id": "g", "type": "dashboard.graph",
                    "data": {"kind": "bar", "series": {"s2": {}}}}]}
wf.keep_builder_edits(stale, {"nodes": [{"id": "g", "type": "dashboard.graph",
                                         "data": node2}]})
check("a Builder hiding an input is not undone by a canvas tab opened before",
      stale["nodes"][0]["data"]["series"]["s2"].get("show") is False,
      stale["nodes"][0]["data"])

# -- the fake store -------------------------------------------------------------
TABLES = {
    "cam1": {"key": "cam1", "name": "PCB-SENSATA-CAM1", "columns": [
        {"name": "status", "type": "text"}, {"name": "object_id", "type": "text"},
        {"name": "total_panel", "type": "number"}]},
    "cam2": {"key": "cam2", "name": "PCB-SENSATA-CAM2", "columns": [
        {"name": "status", "type": "text"}, {"name": "object_id", "type": "text"},
        {"name": "total_panel", "type": "number"}]},
    "dated": {"key": "dated", "name": "Dated", "columns": [
        {"name": "status", "type": "text"}, {"name": "made_on", "type": "datetime"},
        {"name": "total_panel", "type": "number"}]},
}
CALLS = []
#: What each table answers, per question: {table: {"groups": [...], "steps": n}}
ANSWERS = {}
ROWS = {}


def _steps(n, value, since):
    """n hourly/daily buckets ending now, keyed the way Postgres keys them."""
    now = time.time()
    out = []
    for i in range(n):
        epoch = timeaxis.floor(now - (n - 1 - i) * 3600, "1h")
        out.append({"key": timeaxis.iso(epoch), "value": value})
    return out


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None, field_window=None):
    CALLS.append({"dataset": dataset, "group_by": group_by, "bucket": bucket,
                  "since": since, "until": until, "agg": agg, "field": field,
                  "field_window": field_window})
    ans = ANSWERS.get(dataset, {})
    if group_by:
        return {"buckets": [dict(b) for b in ans.get("groups", [])]}
    if bucket:
        return {"buckets": [dict(b) for b in ans.get("steps", [])]}
    return {"buckets": [{"key": None, "value": ans.get("total", 0.0)}]}


def fake_query(slug, dataset, columns, filters=None, since=None, until=None,
               order="ts", descending=True, limit=200, offset=0, config_dir=None,
               field_window=None):
    CALLS.append({"dataset": dataset, "query": True, "order": order,
                  "field_window": field_window})
    return {"rows": [dict(r) for r in ROWS.get(dataset, [])], "total": 0}


datasets_repo.aggregate = fake_aggregate
datasets_repo.query = fake_query
ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def compile_chart(data, wires=(("cam1", "total_panel"), ("cam2", "total_panel"))):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("g", "dashboard.graph", {"name": "Panels", **data})]
    edges = [{"id": "e1", "source": "s", "target": "fr"}]
    for key in ("cam1", "cam2", "dated"):
        nodes.append(node(f"t_{key}", "output.dataset",
                          {"dataset": key, "name": TABLES[key]["name"],
                           "mode": "change", "sources": {}}))
        for col in TABLES[key]["columns"]:
            edges.append({"id": f"{key}{col['name']}", "source": "fr",
                          "target": f"t_{key}", "targetHandle": f"c_{col['name']}"})
    for i, (key, col) in enumerate(wires):
        edges.append({"id": f"w{i}", "source": f"t_{key}", "sourceHandle": f"o_{col}",
                      "target": "g",
                      "targetHandle": "in" if i == 0 else f"s{i + 1}"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES,
                         tables=TABLES)
    assert not c["errors"], c["errors"]
    w = next(x for x in c["widgets"] if x["id"] == "wf_g")
    return builder_service.validate([w])[0], c


def render(w):
    CALLS.clear()
    return builder_service.compute([w], [], {}, slug="smoke")[0]["data"]


def store_calls():
    return sorted((c["dataset"], c.get("group_by"), c.get("bucket"), c.get("agg"),
                   c.get("field")) for c in CALLS if not c.get("query"))


# -- 3. legacy and model draw the same -------------------------------------------
ANSWERS.update({
    "cam1": {"groups": [{"key": "OK", "value": 7.0}, {"key": "VIOLATION", "value": 2.0}],
             "steps": _steps(3, 4.0, None), "total": 11.0},
    "cam2": {"groups": [{"key": "OK", "value": 5.0}, {"key": "VIOLATION", "value": 1.0}],
             "steps": _steps(3, 6.0, None), "total": 13.0},
})
STATUS = (("cam1", "status"), ("cam2", "status"))
PAIRS = [
    # (what it is, legacy node data, the same chart as the editors migrate it, wires)
    ("bar per day, two cameras, total rows",
     {"kind": "bar", "bucket": "1d", "range": "7d", "agg": "count"},
     {"kind": "bar", "x_type": "time", "time_interval": "day", "bucket": "1d",
      "range": "7d", "agg": "count"}, STATUS),
    ("bar of each status word (the screenshot's 'each status value')",
     {"kind": "bar", "range": "24h"},
     {"kind": "bar", "x_type": "column", "group_by": "status", "range": "",
      "agg": "count"}, STATUS),
    ("bar per status with an explicit per-input grouping and period",
     {"kind": "bar", "range": "24h", "series": {
         "s1": {"query": {"group_by": "status", "range": "page"}},
         "s2": {"query": {"group_by": "status", "range": "page"}}}},
     {"kind": "bar", "x_type": "column", "group_by": "status", "range": "page",
      "agg": "count"}, STATUS),
    ("area per hour of total_panel, sum per input",
     {"kind": "area", "bucket": "1h", "range": "24h",
      "series": {"s1": {"agg": "sum"}, "s2": {"agg": "sum"}}},
     {"kind": "area", "x_type": "time", "time_interval": "hour", "bucket": "1h",
      "range": "24h", "agg": "sum"}, (("cam1", "total_panel"), ("cam2", "total_panel"))),
    ("pie of status words over the Analytics range",
     {"kind": "pie", "range": "page"},
     {"kind": "pie", "x_type": "column", "group_by": "status", "range": "",
      "agg": "count"}, STATUS),
]
for what, old, new, wires in PAIRS:
    w_old, _ = compile_chart(old, wires)
    d_old = render(w_old)
    calls_old = store_calls()
    w_new, _ = compile_chart(new, wires)
    d_new = render(w_new)
    calls_new = store_calls()
    same_picture = (d_old.get("labels") == d_new.get("labels")
                    and [x["values"] for x in d_old.get("datasets") or []]
                    == [x["values"] for x in d_new.get("datasets") or []]
                    and d_old.get("values") == d_new.get("values"))
    check(f"legacy == model: {what} (same picture)", same_picture,
          (d_old.get("labels"), d_old.get("datasets"), d_new.get("labels"),
           d_new.get("datasets")))
    check(f"legacy == model: {what} (same store questions)",
          [c[:3] + c[4:] for c in calls_old] == [c[:3] + c[4:] for c in calls_new],
          (calls_old, calls_new))
    check(f"{what}: only the model chart carries the model for the Builders",
          "chart" not in w_old and w_new.get("chart", {}).get("x_type") == new["x_type"],
          (w_old.get("chart"), w_new.get("chart")))

# -- 4. the matrix ----------------------------------------------------------------
CALCS = ("count", "count_nonnull", "unique", "changes", "sum", "avg", "min", "max")
NUM = (("cam1", "total_panel"), ("cam2", "total_panel"))
matrix_bad = []
for kind in ("bar", "line", "spline", "area", "pie", "donut", "radar"):
    xs = ("time", "column") if kind in chartmodel.AXES_KINDS else ("column",)
    for x in xs:
        for calc in CALCS:
            data = {"kind": kind, "x_type": x, "agg": calc,
                    "group_by": "status" if x == "column" else "",
                    "bucket": "1h" if x == "time" else "",
                    "time_interval": "hour" if x == "time" else "",
                    "range": "24h"}
            w, _ = compile_chart(data, NUM)
            d = render(w)
            asked = [c for c in CALLS if not c.get("query")]
            want_agg = {"count": "count", "count_nonnull": "count_nonnull"}.get(calc, calc)
            problems = []
            if len(asked) != 2 or {c["dataset"] for c in asked} != {"cam1", "cam2"}:
                problems.append(("each camera asked once", asked))
            if any(c["agg"] != want_agg for c in asked):
                problems.append(("Y calculation", [c["agg"] for c in asked], want_agg))
            if x == "column" and any(c["group_by"] != "status" for c in asked):
                problems.append(("X column", [c["group_by"] for c in asked]))
            if x == "time" and any(c["bucket"] not in ("1h", "hour", "3600s")
                                   or c["group_by"] for c in asked):
                problems.append(("X time step", [c["bucket"] for c in asked]))
            if any(c["since"] is None for c in asked):
                problems.append(("one date range on every input", asked))
            if len(d.get("datasets") or []) != 2:
                problems.append(("two series drawn", d))
            if problems:
                matrix_bad.append((kind, x, calc, problems))
check(f"matrix: 7 kinds x X type x 8 calculations, each asked and drawn right "
      f"({sum(2 if k in chartmodel.AXES_KINDS else 1 for k in chartmodel.CHART_KINDS) * len(CALCS)} cases)",
      not matrix_bad, matrix_bad[:3])

# pie / donut / radar with no category: one slice per input (camera)
w, _ = compile_chart({"kind": "donut", "x_type": "column", "group_by": "",
                      "agg": "sum", "range": "today"}, NUM)
d = render(w)
check("a donut with no category: one slice per camera, named by its table",
      d.get("labels") == ["PCB-SENSATA-CAM1", "PCB-SENSATA-CAM2"]
      and d.get("values") == [11.0, 13.0], d)
# a text column summed: counted instead, and said
w, c = compile_chart({"kind": "bar", "x_type": "time", "bucket": "1d",
                      "range": "7d", "agg": "sum"}, STATUS)
check("a text column cannot be summed: counted instead, with a warning",
      all(s["agg"] == "count" for s in w["series"])
      and any("can only be counted" in x["message"] for x in c["warnings"]),
      (w["series"], c["warnings"]))

# -- 5. empty sets, nulls, missing steps -------------------------------------------
ANSWERS["cam2"] = {"groups": [], "steps": [], "total": 0.0}
w, _ = compile_chart({"kind": "bar", "x_type": "time", "bucket": "1h",
                      "range": "6h", "agg": "count"}, NUM)
d = render(w)
cam2 = next(x for x in d["datasets"] if x["id"] == "s2")
check("an empty camera on a count: a zero on every step of the window",
      len(cam2["values"]) >= 6 and all(v == 0 for v in cam2["values"]), cam2)
check("...steps in time order, the whole window on the axis",
      d.get("keys") == sorted(d.get("keys") or []) and len(d["labels"]) >= 6, d.get("keys"))
w, _ = compile_chart({"kind": "line", "x_type": "time", "bucket": "1h",
                      "range": "6h", "agg": "avg"}, NUM)
d = render(w)
cam2 = next(x for x in d["datasets"] if x["id"] == "s2")
check("an empty camera on an average: gaps, never a made-up zero",
      all(v is None for v in cam2["values"]), cam2)
ANSWERS["cam1"]["steps"] = [{"key": None, "value": 3.0},
                            {"key": "not a time", "value": 4.0}] + _steps(1, None, None)
d = render(w)
check("null keys and null values do not break the chart",
      isinstance(d.get("datasets"), list) and len(d["datasets"]) == 2, d)
ANSWERS["cam1"]["steps"] = _steps(3, 4.0, None)
w, _ = compile_chart({"kind": "bar", "x_type": "column", "group_by": "status",
                      "range": "", "agg": "count"}, STATUS)
d = render(w)
check("an empty camera on a column X: its bars are empty, the other camera's stay",
      d["labels"] == ["OK", "VIOLATION"]
      and next(x for x in d["datasets"] if x["id"] == "s2")["values"] == [None, None]
      and next(x for x in d["datasets"] if x["id"] == "s1")["values"] == [7.0, 2.0], d)
ANSWERS["cam2"] = {"groups": [{"key": "OK", "value": 5.0}, {"key": "VIOLATION", "value": 1.0}],
                   "steps": _steps(3, 6.0, None), "total": 13.0}

# -- 6. Week / Month / Custom --------------------------------------------------------
now = time.time()
for what, data, step, days in (
        ("Week, weekly aggregation", {"time_interval": "week", "bucket": "week",
                                      "range": "90d"}, "week", 90),
        ("Week, daily view", {"time_interval": "week", "bucket": "1d",
                              "range": "7d"}, "day", 7),
        ("Month, calendar months", {"time_interval": "month", "bucket": "month",
                                    "range": "365d"}, "month", 365),
        ("Month, daily view", {"time_interval": "month", "bucket": "1d",
                               "range": "30d"}, "day", 30)):
    w, _ = compile_chart({"kind": "bar", "x_type": "time", "agg": "count", **data}, NUM)
    d = render(w)
    asked = [c for c in CALLS if not c.get("query")]
    since = datetime.datetime.fromisoformat(asked[0]["since"]).timestamp()
    check(f"{what}: every camera stepped by {step} over {days} days",
          all(c["bucket"] == step for c in asked)
          and abs((now - since) / 86400 - days) < 1.01
          and len({c["since"][:19] for c in asked}) == 1, asked)
w, _ = compile_chart({"kind": "line", "x_type": "time", "agg": "count",
                      "time_interval": "custom", "bucket": "6h", "range": "custom",
                      "range_from": "2026-10-01", "range_to": "2026-10-03"}, NUM)
d = render(w)
asked = [c for c in CALLS if not c.get("query")]
lo = timeaxis.parse_moment("2026-10-01")
hi = timeaxis.parse_moment("2026-10-03") + 86400
check("Custom: the picked start and end (the end day included), every 6 hours",
      all(c["bucket"] == "21600s" for c in asked)
      and all(abs(datetime.datetime.fromisoformat(c["since"]).timestamp() - lo) < 1
              and abs(datetime.datetime.fromisoformat(c["until"]).timestamp() - hi) < 1
              for c in asked)
      and len(d["labels"]) == 12, (asked, d.get("labels")))

# -- 7. series isolation ----------------------------------------------------------------
same = [{"key": "OK", "value": 5.0}, {"key": "VIOLATION", "value": 5.0}]
ANSWERS["cam1"] = {"groups": [{"key": "OK", "value": 9.0}, {"key": "VIOLATION", "value": 1.0}]}
ANSWERS["cam2"] = {"groups": [{"key": "OK", "value": 2.0}, {"key": "VIOLATION", "value": 8.0}]}
model = {"kind": "bar", "x_type": "column", "group_by": "status", "range": "",
         "agg": "count", "series": {"s1": {"label": "CAM 1"}, "s2": {"label": "CAM 2"}}}
w, _ = compile_chart(model, STATUS)
d = render(w)
by_id = {x["id"]: x for x in d["datasets"]}
check("isolation: each camera's values on its own series (identical labels)",
      by_id["s1"]["label"] == "CAM 1" and by_id["s1"]["values"] == [9.0, 1.0]
      and by_id["s2"]["label"] == "CAM 2" and by_id["s2"]["values"] == [2.0, 8.0], d)
check("isolation: the compiled inputs read their own tables",
      [(s["id"], s["dataset"]) for s in w["series"]] == [("s1", "cam1"), ("s2", "cam2")],
      w["series"])
# swap the wires on the canvas: the values follow the TABLE, not the slot
w, _ = compile_chart(model, (("cam2", "status"), ("cam1", "status")))
d = render(w)
by_id = {x["id"]: x for x in d["datasets"]}
check("isolation: rewired the other way round, the values follow their table",
      by_id["s1"]["values"] == [2.0, 8.0] and by_id["s2"]["values"] == [9.0, 1.0], d)
# equal values and identical labels: still one call per table, each line its own
ANSWERS["cam1"] = {"groups": [dict(x) for x in same]}
ANSWERS["cam2"] = {"groups": [dict(x) for x in same]}
w, _ = compile_chart(model, STATUS)
d = render(w)
check("isolation: equal values, identical labels - one store call per table, "
      "never one answer shared between two cameras",
      sorted(c["dataset"] for c in CALLS) == ["cam1", "cam2"]
      and [x["id"] for x in d["datasets"]] == ["s1", "s2"], (CALLS, d))
# the per-step path, equal steps
ANSWERS["cam1"] = {"steps": _steps(3, 4.0, None)}
ANSWERS["cam2"] = {"steps": _steps(3, 4.0, None)[:2]}
w, _ = compile_chart({"kind": "line", "x_type": "time", "bucket": "1h",
                      "range": "6h", "agg": "count"}, NUM)
d = render(w)
by_id = {x["id"]: x for x in d["datasets"]}
check("isolation on the time axis: camera 2's missing last hour is ITS zero",
      by_id["s1"]["values"][-1] == 4.0 and by_id["s2"]["values"][-1] == 0
      and by_id["s2"]["values"][-2] == 4.0, by_id)

# -- 8. hidden inputs ---------------------------------------------------------------------
ANSWERS["cam1"] = {"groups": [{"key": "OK", "value": 9.0}]}
ANSWERS["cam2"] = {"groups": [{"key": "OK", "value": 2.0}]}
w, _ = compile_chart({**model, "series": {"s2": {"show": False}}}, STATUS)
d = render(w)
check("a hidden input is still in the compiled list (the editors list it)",
      [s.get("show", True) for s in w["series"]] == [True, False], w["series"])
check("...and is neither drawn nor asked",
      [x["id"] for x in d["datasets"]] == ["s1"]
      and [c["dataset"] for c in CALLS] == ["cam1"], (d, CALLS))
w, _ = compile_chart({**model, "series": {"s1": {"show": False}, "s2": {"show": False}}},
                     STATUS)
d = render(w)
check("every input hidden: an empty chart, nothing asked",
      d.get("datasets") == [] and not CALLS, (d, CALLS))

# -- 9. the Date column ----------------------------------------------------------------------
today = datetime.datetime.now().astimezone().replace(hour=9, minute=0, second=0,
                                                     microsecond=0)
ROWS["dated"] = [{"made_on": (today - datetime.timedelta(days=1)).isoformat(),
                  "total_panel": 2},
                 {"made_on": (today - datetime.timedelta(days=1)).isoformat(),
                  "total_panel": 3},
                 {"made_on": today.isoformat(), "total_panel": 5},
                 {"made_on": None, "total_panel": 7}]
w, _ = compile_chart({"kind": "bar", "x_type": "time", "bucket": "1d", "range": "7d",
                      "agg": "count", "time_interval": "day"},
                     (("dated", "total_panel"),))
d = render(w)
q = [c for c in CALLS if c.get("query")]
check("X = Time picks the table's date column automatically, and filters on it",
      w["series"][0].get("ts_field") == "made_on"
      and q and q[0]["field_window"][0] == "made_on" and q[0]["field_window"][1], (w["series"], q))
check("...and steps the rows by that column (a row with no date is no step)",
      d["datasets"][0]["values"][-2:] == [2, 1], d["datasets"][0]["values"])
w, _ = compile_chart({"kind": "bar", "x_type": "time", "bucket": "1d", "range": "7d",
                      "agg": "count", "ts_field": "@row"}, (("dated", "total_panel"),))
d = render(w)
check("Date column = when the row was saved: the store steps by the saved time",
      w["series"][0].get("ts_field") == "" and not any(c.get("query") for c in CALLS),
      (w["series"], CALLS))
w, _ = compile_chart({"kind": "line", "bucket": "1d", "range": "7d", "agg": "count"},
                     (("dated", "total_panel"), ("cam1", "total_panel")))
d = render(w)
check("a chart saved before the model keeps the saved time on the step path",
      not any(c.get("query") for c in CALLS), CALLS)

# -- 10. Count records vs Count non-null --------------------------------------------------
rows = [{"x": 1}, {"x": None}, {"x": ""}, {"y": 2}]
check("in memory: count = every row, count_nonnull = rows holding a value",
      builder_service._agg_value(rows, "count", "x") == 4
      and builder_service._agg_value(rows, "count_nonnull", "x") == 1)
SQL = []


class _Cur:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        SQL.append(sql)

    def fetchall(self):
        return []


class _Conn:
    def cursor(self):
        return _Cur()

    def close(self):
        pass


import importlib                                                      # noqa: E402
real = importlib.reload(datasets_repo)
real.connect = lambda config_dir=None: _Conn()
cols = [{"name": "status", "type": "text"}, {"name": "n", "type": "number"}]
real.aggregate("s", "t", cols, agg="count", field="status", group_by="status")
real.aggregate("s", "t", cols, agg="count_nonnull", field="status", group_by="status")
real.aggregate("s", "t", cols, agg="count_nonnull", field="n", bucket="day",
               field_window=("status", "2026-10-01", "2026-10-02"))
check("SQL: Count records is count(*) even with a column named",
      "count(*) AS value" in SQL[0], SQL[0])
check("SQL: Count non-null values skips NULL and blank",
      "count(NULLIF((cols->>'status')::text, ''))" in SQL[1], SQL[1])
check("SQL: a Date column window is in the WHERE of the aggregate",
      "cols->>'status' >= %s" in SQL[2] and "cols->>'status' < %s" in SQL[2], SQL[2])
from core.repos import events as events_repo                            # noqa: E402
events_repo.connect = lambda config_dir=None: _Conn()
SQL.clear()
events_repo.aggregate("s", agg="count", field="camera", group_by="camera")
events_repo.aggregate("s", agg="count_nonnull", field="camera", group_by="camera")
check("event store: count is rows, count_nonnull is count(<column>) without blanks",
      "count(*)" in SQL[0] and "count(NULLIF((camera)::text, ''))" in SQL[1], SQL)
check("count_nonnull is a calculation every whitelist accepts",
      "count_nonnull" in builder_service.AGGREGATIONS
      and builder_service._clean_query({"agg": "count_nonnull"})["agg"] == "count_nonnull")
datasets_repo = importlib.reload(datasets_repo)
datasets_repo.aggregate = fake_aggregate
datasets_repo.query = fake_query

# -- 11. a Builder edit lands in the same clean shape ---------------------------------------
store = {"workflows": [{"graph": {"nodes": [{"id": "g", "type": "dashboard.graph",
    "data": {"kind": "bar", "range": "24h", "interval": "5m",
             "series": {"s1": {"query": {"group_by": "status"}}}}}]}}]}
changed = wf.apply_card_style(store, [{"id": "wf_g", "x_type": "column",
                                       "group_by": "status", "range": "",
                                       "agg": "count", "time_interval": "",
                                       "ts_field": ""}])
nd = store["workflows"][0]["graph"]["nodes"][0]["data"]
check("card-style writes the model and stamps it against a stale tab",
      changed and nd["x_type"] == "column" and nd["group_by"] == "status"
      and "x_type" in nd.get("builder_edit", {}).get("was", {}), nd)
check("...normalized: the old step folded, the override that repeats X dropped",
      nd["interval"] == "" and nd["series"] == {} and nd["window"] == "live", nd)
graph = {"nodes": [{"id": "g", "type": "dashboard.graph", "data": copy.deepcopy(nd)}]}
wf.normalize_chart_nodes(graph)
check("the canvas save writes the same shape (idempotent across both routes)",
      graph["nodes"][0]["data"] == nd, (graph["nodes"][0]["data"], nd))

# -- 12. saved then reopened: the model survives the server ---------------------------
w, _ = compile_chart({"kind": "bar", "x_type": "column", "group_by": "status",
                      "agg": "count", "range": "page", "ts_field": "@row",
                      "series": {"s2": {"agg": "max", "query": {"range": "today"}}}},
                     STATUS)
check("a compiled chart carries its node's model and overrides for the Builders",
      w.get("chart", {}).get("x_field") == "status"
      and w["chart"].get("ts_field") == "@row"
      and w["chart"].get("overrides") == {"s2": {"agg": "max", "range": "today"}},
      w.get("chart"))
hand = {"id": "h1", "title": "Hand", "kind": "line", "source": "history", "range": "7d",
        "interval": "1d", "series": [{"id": "s1", "source": "history", "key": "k",
                                      "show": False}],
        "chart": {"x_type": "time", "time_interval": "day", "step": "1d", "range": "7d",
                  "y_agg": "avg", "ts_field": "", "x_field": "status",
                  "overrides": {"s1": {"agg": "max", "bogus": 1}, "x;y": {"agg": "sum"}}}}
saved = builder_service.validate([hand])[0]
check("a Builder chart's model is stored cleaned (a column X dropped on Time, junk out)",
      saved["chart"] == {"x_type": "time", "x_field": "", "y_agg": "avg", "y_field": "",
                         "time_interval": "day", "step": "1d", "range": "7d",
                         "ts_field": "", "overrides": {"s1": {"agg": "max"}}}
      and saved["series"][0].get("show") is False, saved.get("chart"))
again = builder_service.validate([saved])[0]
check("...and reopened and saved again, byte for byte the same",
      again == saved, (again, saved))
old = builder_service.validate([{k: v for k, v in hand.items() if k != "chart"}])[0]
check("a chart saved before the model stores no `chart` at all", "chart" not in old)

print("all good" if not bad else f"{bad} check(s) failed")
sys.exit(1 if bad else 0)
