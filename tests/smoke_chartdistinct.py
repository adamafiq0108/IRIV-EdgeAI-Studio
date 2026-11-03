# "Count Difference Value": how many DIFFERENT values a column holds per point
# of a chart (owner, 2026-10-13: "can you add also to count for total
# different value only"; stored as the cards' old "unique").
#
# A distinct count is NOT additive -- the same value in two buckets is one
# value -- so every path must count over the bucket it draws, never add up
# smaller buckets' counts. Pinned with fake stores:
#
#   1. the SQL: COUNT(DISTINCT) of the column, nulls and blanks skipped, in
#      both stores; no column, no count (it used to count rows)
#   2. in memory: repeated values once, nulls and blanks not values
#   3. the sampler: different sampled values per cell; over a whole window
#      (a pie of live values) counted over the window, not summed per cell
#   4. widened steps (a year asked every minute): the store is asked for the
#      WIDENED bucket itself, and two answers on one point are never summed
#   5. the shared time grid: a dataset input is counted from the cell's rows
#      (hourly store buckets added up would count a value twice), an event
#      input asks the store for buckets exactly one cell wide
#   6. the compile: an input's own column, any type, as "unique"
import datetime
import importlib
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                  # noqa: E402
from core.repos import events as events_repo                      # noqa: E402
from core.services import builder as builder_service             # noqa: E402
from core.services import chart_series                           # noqa: E402
from core.services import history as history_service             # noqa: E402
from core.services import timeaxis                               # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. the SQL -------------------------------------------------------------------
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


datasets_repo.connect = lambda config_dir=None: _Conn()
events_repo.connect = lambda config_dir=None: _Conn()
cols = [{"name": "status", "type": "text"}, {"name": "n", "type": "number"}]
datasets_repo.aggregate("s", "t", cols, agg="unique", field="status", bucket="day")
datasets_repo.aggregate("s", "t", cols, agg="unique", field="n", group_by="status")
check("table store: COUNT(DISTINCT) of the column's value, blanks and nulls skipped, per bucket",
      "count(DISTINCT NULLIF((cols->>'status')::text, ''))" in SQL[0] and "GROUP BY" in SQL[0]
      and "count(DISTINCT NULLIF((NULLIF(cols->>'n', '')::double precision)::text, ''))" in SQL[1],
      SQL)
SQL.clear()
res = datasets_repo.aggregate("s", "t", cols, agg="unique", field=None, bucket="day")
check("...with no column there is nothing to count: no answer, no query",
      res == {"buckets": []} and not SQL, (res, SQL))
events_repo.aggregate("s", agg="unique", field="camera", group_by="zone")
res2 = events_repo.aggregate("s", agg="unique", field=None, group_by="zone")
check("event store: the same, and no column no count",
      "count(DISTINCT NULLIF((camera)::text, ''))" in SQL[0] and res2 == {"buckets": []}
      and len(SQL) == 1, SQL)
datasets_repo = importlib.reload(datasets_repo)

# -- 2. in memory ------------------------------------------------------------------------
rows = [{"s": "A"}, {"s": "A"}, {"s": None}, {"s": ""}, {"s": "B"}, {}]
check("in memory: repeated values once, nulls and blanks are no value",
      builder_service._agg_value(rows, "unique", "s") == 2)

# -- 3. the sampler ---------------------------------------------------------------------------
now = time.time()
start = timeaxis.floor(now - 4 * 3600, "1h")
history_service._loaded = True
history_service._fine["k"] = [(start + 60, 1), (start + 120, 1), (start + 180, 2),
                              (start + 3600 + 60, 2), (start + 3600 + 120, 2),
                              (start + 2 * 3600 + 60, 3)]
g = {"start": start, "step": 3600, "buckets": 4}
cells = history_service.values_on_grid("k", "unique", g)
check("sampler: different sampled values per cell (1,1,2 -> 2; 2,2 -> 1; 3 -> 1; none -> 0)",
      cells == [2, 1, 1, 0], cells)
w = {"kind": "pie", "range": "24h"}
entry = {"id": "s1", "source": "history", "key": "k", "agg": "unique", "label": "k", "color": ""}
d = chart_series._value_data(w, [entry], [], {}, None, "smoke", None, [])
check("a pie of a live value over the day: 3 different values, not the cells added (4)",
      d["values"] == [3.0], d)
ent = chart_series.migrate([{"source": "history", "key": "k", "agg": "unique"}], "history")
check("...and 'unique' is a fold a sampled input keeps", ent[0]["agg"] == "unique", ent)

# -- 4. widened steps, and two answers on one point --------------------------------------------
CALLS = []
ANSWER = {}


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None, field_window=None):
    CALLS.append({"dataset": dataset, "agg": agg, "field": field, "bucket": bucket,
                  "group_by": group_by})
    return {"buckets": [dict(b) for b in ANSWER.get(dataset, [])]}


datasets_repo.aggregate = fake_aggregate
TABLES = {k: {"key": k, "name": k.upper(), "columns": [
    {"name": "status", "type": "text"}, {"name": "object_id", "type": "text"}]}
    for k in ("cam1", "cam2")}


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def compile_chart(data):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("g", "dashboard.graph", {"name": "Objects", **data})]
    edges = [{"id": "e1", "source": "s", "target": "fr"}]
    for i, key in enumerate(("cam1", "cam2")):
        nodes.append(node(f"t{key}", "output.dataset", {"dataset": key, "name": key.upper(),
                                                        "mode": "change", "sources": {}}))
        for c in ("status", "object_id"):
            edges.append({"id": f"{key}{c}", "source": "fr", "target": f"t{key}",
                          "targetHandle": f"c_{c}"})
        edges.append({"id": f"w{i}", "source": f"t{key}", "sourceHandle": "o_object_id",
                      "target": "g", "targetHandle": "in" if i == 0 else "s2"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                         [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}],
                         tables=TABLES)
    assert not c["errors"], c["errors"]
    return builder_service.validate([next(x for x in c["widgets"] if x["id"] == "wf_g")])[0], c


w, c = compile_chart({"kind": "bar", "x_type": "time", "time_interval": "custom",
                      "bucket": "1m", "range": "365d", "agg": "unique"})
check("the compile asks each input for the different values of its own (text) column",
      all(s["query"]["agg"] == "unique" and s["field"] == "object_id" and s["agg"] == "unique"
          for s in w["series"]), w["series"])
edge = timeaxis.floor(now - 86400, "6h")
ANSWER["cam1"] = [{"key": timeaxis.iso(edge), "value": 3.0},
                  {"key": timeaxis.iso(edge + 1), "value": 4.0}]   # one point, two answers
ANSWER["cam2"] = [{"key": timeaxis.iso(edge), "value": 5.0}]
CALLS.clear()
d = builder_service.compute([w], [], {}, slug="smoke")[0]["data"]
check("a year every minute is widened (every 6 hours), and the store is asked for the "
      "WIDENED bucket -- the distinct count is over the merged bucket, not summed",
      [c_["bucket"] for c_ in CALLS] == ["21600s", "21600s"]
      and all(c_["agg"] == "unique" for c_ in CALLS) and "6 hours" in (d.get("step_note") or ""),
      (CALLS, d.get("step_note")))
by = {x["id"]: x for x in d["datasets"]}
idx = d["keys"].index(int(edge * 1000))
check("two answers landing on one point are never added (4, not 7); each camera its own",
      by["s1"]["values"][idx] == 4.0 and by["s2"]["values"][idx] == 5.0
      and by["s1"]["values"][idx - 1] == 0, (by["s1"]["values"][idx - 1:idx + 1],
                                              by["s2"]["values"][idx]))

# -- 5. the shared time grid -------------------------------------------------------------------
QUERIED = []


def fake_query(slug, dataset, columns, filters=None, since=None, until=None, order="ts",
               descending=True, limit=200, offset=0, config_dir=None, field_window=None):
    QUERIED.append({"dataset": dataset, "since": since, "until": until, "order": order})
    base = datetime.datetime.fromtimestamp(start).astimezone()
    stamp = lambda m: (base + datetime.timedelta(minutes=m)).isoformat()   # noqa: E731
    # Hour 1: A, A. Hour 2: A, B, and a blank. All inside one 3-hour cell.
    return {"rows": [{"ts": stamp(10), "status": "A"}, {"ts": stamp(20), "status": "A"},
                     {"ts": stamp(70), "status": "A"}, {"ts": stamp(80), "status": "B"},
                     {"ts": stamp(90), "status": ""}]}


datasets_repo.query = fake_query
CALLS.clear()
entry = {"id": "s1", "source": "dataset", "dataset": "cam1", "field": "status",
         "columns": [{"name": "status", "type": "text"}], "ts_field": "", "agg": "unique",
         "query": {"agg": "unique"}}
pairs = chart_series._dataset_time(entry, "smoke", None, None, "hour",
                                   {"start": start, "step": 10800, "buckets": 2},
                                   timeaxis.iso(start), timeaxis.iso(start + 21600), [])
check("grid cell of 3 hours: A,A then A,B -> 2 different values, counted from the rows "
      "(hourly distincts added up would say 3)",
      pairs and pairs[0][1] == 2 and not CALLS and QUERIED, (pairs, CALLS))
check("...and the cell is folded as a distinct count, never a sum",
      chart_series._store_fold(entry) == "unique"
      and chart_series._fold([2, 3], "unique") == 3)
ASKED = []
real_groups = chart_series._event_groups
chart_series._event_groups = lambda entry, slug, events, config_dir, bucket=None, \
    group_by=None, since=None, until=None: ASKED.append(bucket) or []
qe = chart_series.migrate([{"source": "query", "query": {"agg": "unique", "field": "camera"}}],
                          "query")
chart_series._time_data({"kind": "line", "range": "24h", "interval": "3h"}, qe, [], {},
                        None, "smoke", None, [])
qc = chart_series.migrate([{"source": "query", "query": {"agg": "count"}}], "query")
chart_series._time_data({"kind": "line", "range": "24h", "interval": "3h"}, qc, [], {},
                        None, "smoke", None, [])
chart_series._event_groups = real_groups
check("an event input on the grid asks for buckets exactly one cell wide (10800s) when "
      "distinct, hourly ones otherwise",
      ASKED == ["10800s", "hour"], ASKED)

print("all good" if not bad else f"{bad} check(s) failed")
sys.exit(1 if bad else 0)
