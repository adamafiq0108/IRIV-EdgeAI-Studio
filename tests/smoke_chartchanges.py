# "Count Value Changes": how many times a column took a NEW value, in time
# order, per point of a chart (owner, 2026-10-13: "add this too: total
# different values and every value changes"; stored as the cards' old
# "changes").
#
# The rule decided (design note 2c): along TIME the look-back runs over the
# whole ordered series -- a change at a bucket boundary is counted in the
# bucket where the new value appears, every change belongs to exactly one
# row, and so bucket counts add up exactly when buckets are merged or
# widened. Grouped by a COLUMN (a pie, a bar per status) the look-back stays
# inside each group. Nulls and blanks are no value (3 -> null -> 3 is no
# change). The first value in the window counts. Pinned with fake stores:
#
#   1. the SQL of both stores: LAG over the whole series on a time axis,
#      PARTITION BY the group on a column axis, ordered by the Date column
#      when there is one; no column, no count
#   2. A A B B A across a bucket boundary, with a null: 2 then 1, total 3
#   3. the sampler: the same along samples; a pie over the whole window
#   4. the Date column path and a widened step: recomputed over the rows,
#      and equal to the narrow buckets added up
#   5. the in-memory event ring: the same rule
#   6. the compile: an input's own column, any type
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


# -- 1. the SQL ------------------------------------------------------------------
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
cols = [{"name": "status", "type": "text"}, {"name": "made_on", "type": "datetime"}]
datasets_repo.aggregate("s", "t", cols, agg="changes", field="status", bucket="day")
datasets_repo.aggregate("s", "t", cols, agg="changes", field="status", group_by="status")
datasets_repo.aggregate("s", "t", cols, agg="changes", field="status", bucket="day",
                        field_window=("made_on", "2026-10-01", "2026-10-02"))
check("table store, time axis: LAG over the whole ordered series (no partition)",
      "lag(v) OVER (ORDER BY o)" in SQL[0] and "ts AS o" in SQL[0]
      and "v IS NOT NULL AND (v)::text <> ''" in SQL[0], SQL[0])
check("...column axis: the look-back stays inside each group",
      "lag(v) OVER (PARTITION BY key ORDER BY o)" in SQL[1], SQL[1])
check("...ordered by the chart's Date column when it has one",
      "cols->>'made_on' AS o" in SQL[2], SQL[2])
SQL.clear()
events_repo.aggregate("s", agg="changes", field="camera", bucket="hour")
events_repo.aggregate("s", agg="changes", field="camera", group_by="zone")
none = events_repo.aggregate("s", agg="changes", field=None, bucket="hour")
check("event store: the same window query (it had none: it counted rows), no column no count",
      len(SQL) == 2 and "lag(v) OVER (ORDER BY ts)" in SQL[0]
      and "(camera)::text AS v" in SQL[0]
      and "lag(v) OVER (PARTITION BY key ORDER BY ts)" in SQL[1]
      and none == {"buckets": []}, SQL)
datasets_repo = importlib.reload(datasets_repo)
events_repo = importlib.reload(events_repo)

# -- 2. A A B B A across a bucket boundary ------------------------------------------
now = time.time()
start = timeaxis.floor(now - 5 * 3600, "1h")
iso = lambda s: datetime.datetime.fromtimestamp(start + s).astimezone().isoformat()  # noqa: E731
ROWS = [{"ts": iso(60), "status": "A"}, {"ts": iso(120), "status": "A"},
        {"ts": iso(180), "status": "B"},
        {"ts": iso(3600 + 60), "status": "B"},            # same as before: no change
        {"ts": iso(3600 + 90), "status": None},            # no value
        {"ts": iso(3600 + 120), "status": "A"}]
hour_of = lambda r: timeaxis.floor(chart_series._epoch(r["ts"]), "1h")   # noqa: E731
by_hour = builder_service.changes_by(ROWS, "status", hour_of)
check("A A B | B null A: 2 changes in the first hour (A, then B), 1 in the second (A)",
      [by_hour.get(start), by_hour.get(start + 3600)] == [2, 1], by_hour)
whole = builder_service.changes_by(ROWS, "status", lambda r: "all")
check("...3 over the whole window, the same as the two hours added up (it is additive)",
      whole == {"all": 3})
grouped = builder_service.changes_by(ROWS, "status", lambda r: "x", partition=True)
check("in memory a card still counts within its rows: the first value counts",
      builder_service._agg_value(ROWS, "changes", "status") == 3 and grouped == {"x": 3})

# -- 3. the sampler --------------------------------------------------------------------
history_service._loaded = True
history_service._fine["k"] = [(start + 60, 1), (start + 120, 1), (start + 180, 2),
                              (start + 3660, 2), (start + 3700, None), (start + 3720, 1)]
g = {"start": start, "step": 3600, "buckets": 3}
check("sampler: the same rule along the samples (2, 1, 0)",
      history_service.values_on_grid("k", "changes", g) == [2, 1, 0],
      history_service.values_on_grid("k", "changes", g))
w = {"kind": "pie", "range": "24h"}
e = {"id": "s1", "source": "history", "key": "k", "agg": "changes", "label": "k", "color": ""}
d = chart_series._value_data(w, [e], [], {}, None, "smoke", None, [])
check("a pie of a live value's changes over the day: 3", d["values"] == [3.0], d)

# -- 4. the Date column path and a widened step ---------------------------------------
QUERIES = []


def fake_query(slug, dataset, columns, filters=None, since=None, until=None, order="ts",
               descending=True, limit=200, offset=0, config_dir=None, field_window=None):
    QUERIES.append(order)
    return {"rows": [{"made_on": r["ts"], "ts": r["ts"], "status": r["status"]} for r in ROWS]}


datasets_repo.query = fake_query
entry = {"id": "s1", "source": "dataset", "dataset": "cam1", "field": "status",
         "columns": [{"name": "status", "type": "text"}, {"name": "made_on", "type": "datetime"}],
         "ts_field": "made_on", "by_ts_field": True, "query": {"agg": "changes"}}
narrow = chart_series._rows_by_step(entry, "smoke", None, entry["columns"], "1h",
                                    iso(0), iso(5 * 3600), [])
wide = chart_series._rows_by_step(entry, "smoke", None, entry["columns"], "6h",
                                  iso(0), iso(5 * 3600), [])
check("by the Date column, hourly: 2 then 1",
      [v for _k, v in narrow] == [2, 1] and QUERIES[0] == "made_on", narrow)
check("...widened to 6 hours: recounted over the rows in order (3), equal to the hours added up",
      len(wide) <= 2 and sum(v for _k, v in wide) == 3 == sum(v for _k, v in narrow), wide)
grid = chart_series._dataset_time(entry, "smoke", None, None, "hour",
                                  {"start": start, "step": 10800, "buckets": 2},
                                  iso(0), iso(6 * 3600), [])
check("the shared grid (3-hour cells): 3 in the cell that holds both hours", grid
      and grid[0][1] == 3, grid)
check("...and a cell holding several store answers adds them (each change is one row's)",
      chart_series._fold([2, 1], "changes") == 3)

# -- 5. the in-memory event ring -------------------------------------------------------
events_repo.available = lambda config_dir=None: False
real_expand = events_repo.expand
events_repo.expand = lambda ev, slug: ev
qe = {"query": {"agg": "changes", "field": "status"}}
pairs = chart_series._event_groups(qe, "smoke", [dict(r) for r in reversed(ROWS)], None,
                                   bucket="hour")
events_repo.expand = real_expand
check("with the event store down, the ring answers by the same rule (rows put in time order)",
      [v for _k, v in pairs] == [2, 1], pairs)

# -- 6. the compile --------------------------------------------------------------------
TABLES = {"cam1": {"key": "cam1", "name": "CAM1", "columns": [{"name": "status", "type": "text"}]}}
nodes = [{"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
         {"id": "fr", "type": "analytics.frame", "position": {"x": 0, "y": 0},
          "data": {"fnId": "f", "name": "F", "mode": "realtime"}},
         {"id": "t", "type": "output.dataset", "position": {"x": 0, "y": 0},
          "data": {"dataset": "cam1", "name": "CAM1", "mode": "change", "sources": {}}},
         {"id": "g", "type": "dashboard.graph", "position": {"x": 0, "y": 0},
          "data": {"name": "Changes", "kind": "line", "x_type": "time", "bucket": "1h",
                   "time_interval": "hour", "range": "24h", "agg": "changes"}}]
edges = [{"id": "e1", "source": "s", "target": "fr"},
         {"id": "e2", "source": "fr", "target": "t", "targetHandle": "c_status"},
         {"id": "w", "source": "t", "sourceHandle": "o_status", "target": "g", "targetHandle": "in"}]
c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                     [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}],
                     tables=TABLES)
w = builder_service.validate([next(x for x in c["widgets"] if x["id"] == "wf_g")])[0]
s1 = w["series"][0]
check("the compile asks the input for the changes of its own text column, summed per cell",
      s1["query"]["agg"] == "changes" and s1["field"] == "status" and s1["agg"] == "sum", s1)

print("all good" if not bad else f"{bad} check(s) failed")
sys.exit(1 if bad else 0)
