# One chart, several sources, one axis.
#
# A chart could draw several lines only when they all came from one place:
# several sampler keys, or one event query grouped by a column. The question
# an operator asks is "zone A's count against the alarms the gate raised
# against the weights the scale logged", and that is three stores answering on
# three different time bases -- which drawn together is not one chart, it is
# three charts overlaid with the peaks in the wrong places.
#
# And one long-standing lie: the inspector offered the series editor on a
# BUCKET widget and builder._data ignored the list, so every edit made there
# was dropped on the floor and the card went on drawing its single key.
#
# What is pinned here: a stored layout renders exactly as it did; a mixed
# chart lands on ONE grid with nulls (never zeros) where a series has no
# answer; a dataset series buckets by its own timestamp column; a comparison
# chart takes one value per series; the cap holds server-side; and one broken
# series comes back named and empty instead of blanking the chart.
import datetime
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                 # noqa: E402
from core.repos import events as events_repo                     # noqa: E402
from core.services import builder as bs                          # noqa: E402
from core.services import chart_series as cs                     # noqa: E402
from core.services import history as hs                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def iso(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat()


# -- 1. the migration is a read, not a rewrite --------------------------------
# A legacy entry carries no `source` and means the WIDGET's, which is what it
# meant before entries could carry one. Pure function, so it is pinned here
# on its own before anything touches a store.
legacy = [{"key": "fn_a_count", "agg": "avg", "label": "Zone A", "color": "#04bade"},
          {"key": "fn_b_count", "agg": "sum", "label": "", "color": ""}]
got = cs.migrate(legacy, "history")
check("a legacy entry inherits the widget's source",
      [e["source"] for e in got] == ["history", "history"], got)
check("...and keeps every field it had",
      got[0]["key"] == "fn_a_count" and got[0]["agg"] == "avg"
      and got[0]["label"] == "Zone A" and got[0]["color"] == "#04bade", got)
check("...including the default aggregation",
      cs.migrate([{"key": "k"}], "history")[0]["agg"] == "avg")
check("a stat widget's entries inherit stat",
      cs.migrate(legacy, "stat")[0]["source"] == "stat")
check("a bucket widget's entries inherit bucket",
      cs.migrate(legacy, "bucket")[0]["source"] == "bucket")
# A source that is not a series source at all still has to land somewhere the
# keys it collected mean something.
check("a widget source that is no series source reads as stat",
      cs.migrate(legacy, "sql")[0]["source"] == "stat")
check("an explicit source wins",
      cs.migrate([{"source": "query", "query": {"agg": "count"}}],
                 "history")[0]["source"] == "query")
check("ids are stable and unique",
      [e["id"] for e in cs.migrate(
          [{"key": "a", "id": "keep"}, {"key": "b", "id": "keep"},
           {"key": "c"}], "history")] == ["keep", "s2", "s3"])
check("the migration is pure -- same in, same out",
      cs.migrate(legacy, "history") == got)

# -- 2. the cap is the server's, not the inspector's --------------------------
nine = [{"key": f"k{i}"} for i in range(9)]
check("a ninth series is refused by the migration", len(cs.migrate(nine, "history")) == 8)
saved = bs.validate([{"id": "w1", "title": "T", "kind": "line",
                      "source": "history", "key": "k0", "series": nine}])
check("...and by the validator", len(saved[0]["series"]) == 8, saved[0]["series"])
# A widget can also arrive from a workflow compile or an installed package
# that never met the validator, so the cap has to hold at render time too.
raw = {"id": "w1", "title": "T", "kind": "line", "source": "history",
       "key": "k0", "range": "1h", "series": nine}
check("...and again when the widget never met the validator",
      len(bs._data(raw, [], {})["datasets"]) == 8)

# -- 3. one grid, and nulls where a series has no answer ----------------------
# The sampler runs every 10s; an event query is asked for the grid's own
# interval. The two must land on the SAME buckets, and a bucket a series did
# not answer for must be null: a zero draws a line to the floor and claims
# something was measured there.
T = 1789000000.0                        # a fixed clock, so the axis is pinned
for k in range(6):
    hs.sample({"fn_a_count": 5}, now=T - 1800 + k * 60)
hs._last_sample = 0.0

asked = {}


def fake_aggregate(slug, agg="count", field=None, group_by=None, bucket=None,
                   filters=None, since=None, until=None, limit=500,
                   config_dir=None):
    asked["bucket"], asked["since"], asked["until"] = bucket, since, until
    if group_by is None and bucket is None:
        return {"buckets": [{"key": None, "value": 9}]}
    return {"buckets": [{"key": iso(T - 1800), "value": 3},
                        {"key": iso(T - 900), "value": 7}]}


events_repo.available = lambda config_dir=None: True
events_repo.aggregate = fake_aggregate

mixed = {"id": "m1", "title": "Mixed", "kind": "line", "source": "history",
         "key": "", "range": "1h", "interval": "",
         "_since": T - 3600, "_until": T,
         "series": [{"id": "s1", "source": "history", "key": "fn_a_count",
                     "agg": "avg", "label": "Zone A", "color": ""},
                    {"id": "s2", "source": "query", "label": "Alarms",
                     "query": {"agg": "count", "filters": {}}}]}
out = bs._data(mixed, [], {})
labels, sets = out["labels"], out["datasets"]
check("a mixed chart returns one shared label axis",
      len(sets) == 2 and all(len(d["values"]) == len(labels) for d in sets),
      [len(labels)] + [len(d["values"]) for d in sets])
check("...bucketed at one interval both sources were asked for",
      out["interval_s"] == 30 and asked["bucket"] == "30s", (out["interval_s"], asked))
check("...over the grid's own window, not the series' own range",
      asked["since"] == iso(T - 3600) and asked["until"] == iso(T), asked)
hist, query = sets[0]["values"], sets[1]["values"]
check("the sampled series reads its samples", hist[0] == 5, hist[:4])
check("...and a bucket it never sampled is null, not zero",
      hist[1] is None, hist[:4])
check("the event series lands on the same bucket", query[0] == 3, query[:4])
check("...and a bucket the store did not answer for is null, not zero",
      query[1] is None and query[30] == 7, query[:4])
check("the two series agree about which bucket is which",
      hist[0] == 5 and query[0] == 3, (hist[0], query[0]))
check("ids ride along so a colour survives a reorder",
      [d["id"] for d in sets] == ["s1", "s2"], sets)

# The whole point of the grid floor: a 10-second interval against an hourly
# event bucket would draw one point an hour and 359 holes. The step is never
# finer than the ring feeding it, and the event store is then asked for THAT.
g = hs.grid("24h", "10s", since=T - 3 * 86400, now=T)
check("a multi-day chart is clamped to the rollup's own resolution",
      g["step"] == hs.ROLLUP_S and g["coarse"], g["step"])
check("...and the store is asked in the same units",
      cs._store_bucket(g["step"]) == "300s" and cs._store_bucket(7200) == "hour",
      cs._store_bucket(g["step"]))
# A WEEK at 10 seconds is more cells than the grid holds (1000): the step is
# widened to the nearest preset that fits the whole week, and said -- it used
# to keep 5-minute cells and silently draw only the last 3.5 days.
g = hs.grid("7d", "10s")
check("a week asked every 10 seconds is widened to fit, not cut short",
      g["step"] == 900 and g["widened"] == "15m"
      and g["buckets"] * g["step"] >= 7 * 86400 - 900, (g["step"], g["buckets"]))

# -- 4. a dataset series buckets by its own timestamp column ------------------
# The row's ingest time is what the store can bucket for itself. A `ts_field`
# the operator nominated lives in JSONB, so those rows are bucketed here --
# and it must be THAT column that decides, not the ingest stamp.
def fake_rows(slug, dataset, columns, filters=None, since=None, until=None,
              order="ts", descending=True, limit=200, offset=0, config_dir=None,
              field_window=None):
    return {"rows": [{"ts": iso(T - 10), "weight": 8.0,
                      "reading_at": iso(T - 1800)},
                     {"ts": iso(T - 10), "weight": 4.0,
                      "reading_at": iso(T - 1800)}],
            "total": 2, "capped": False}


datasets_repo.query = fake_rows
scale = {"id": "d1", "title": "Scale", "kind": "line", "source": "history",
         "key": "", "range": "1h", "_since": T - 3600, "_until": T,
         "series": [{"id": "s1", "source": "dataset", "dataset": "scale",
                     "columns": [{"name": "weight", "type": "number"},
                                 {"name": "reading_at", "type": "datetime"}],
                     "field": "weight", "ts_field": "reading_at",
                     "agg": "avg", "query": {"agg": "avg"}}]}
out = bs._data(scale, [], {})
values = out["datasets"][0]["values"]
check("a dataset series buckets by its ts_field", values[0] == 6.0, values[:4])
check("...and not by the row's ingest time",
      all(v is None for v in values[1:]), values[:6])

# -- 5. a comparison chart takes one value per series -------------------------
pie = {"id": "p1", "title": "Split", "kind": "pie", "source": "stat", "key": "",
       "range": "1h", "_since": T - 3600, "_until": T,
       "series": [{"id": "s1", "source": "stat", "key": "a", "label": "Live"},
                  {"id": "s2", "source": "query", "label": "Alarms",
                   "query": {"agg": "count", "filters": {}}}]}
out = bs._data(pie, [], {"a": 4})
check("one value per series, labelled by the series",
      out["labels"] == ["Live", "Alarms"] and out["values"] == [4.0, 9.0], out)
check("...in the shape a comparison widget has always returned",
      "colors" in out and "datasets" not in out, sorted(out))

# A stored comparison widget is untouched by all of this: the legacy shape in,
# the legacy numbers out.
old = bs.validate([{"id": "c1", "title": "Split", "kind": "donut",
                    "source": "stat", "key": "a",
                    "series": [{"key": "a", "label": "A", "color": "#04bade"},
                               {"key": "b", "label": "B"}]}])
out = bs.compute(old, [], {"a": 2, "b": 3})[0]["data"]
check("a stored comparison chart renders exactly as it did",
      out["labels"] == ["A", "B"] and out["values"] == [2.0, 3.0]
      and out["colors"] == ["#04bade", None], out)

# -- 6. the bucket widget's series list finally does something ----------------
# THE BUG: the inspector offered this editor and _data ignored it.
stats = {"days": {"2026-09-01": 2, "2026-09-02": 5},
         "nights": {"2026-09-02": 1}}
buckets = bs.validate([{"id": "b1", "title": "Shifts", "kind": "bar",
                        "source": "bucket", "key": "days",
                        "series": [{"key": "days", "label": "Days"},
                                   {"key": "nights", "label": "Nights"}]}])
out = bs.compute(buckets, [], stats)[0]["data"]
check("a bucket widget draws every series it was given",
      [d["label"] for d in out["datasets"]] == ["Days", "Nights"], out)
check("...on the union of their labels, newest last",
      out["labels"] == ["09-01", "09-02"], out["labels"])
check("...with null (not zero) where one shift has no total",
      out["datasets"][0]["values"] == [2, 5]
      and out["datasets"][1]["values"] == [None, 1], out["datasets"])
# Union rather than intersection: a shift that recorded nothing is still a
# shift somebody asked about by name.
single = bs.validate([{"id": "b2", "title": "Shifts", "kind": "bar",
                       "source": "bucket", "key": "days"}])
out = bs.compute(single, [], stats)[0]["data"]
check("a bucket widget with no series is the card it always was",
      out["labels"] == ["09-01", "09-02"] and out["values"] == [2, 5], out)

# -- 7. one bad series must not blank the chart -------------------------------
broken = {"id": "x1", "title": "Mixed", "kind": "line", "source": "history",
          "key": "", "range": "1h", "_since": T - 3600, "_until": T,
          "series": [{"id": "s1", "source": "history", "key": "fn_a_count",
                      "agg": "avg", "label": "Zone A"},
                     {"id": "s2", "source": "history", "key": "never_sampled",
                      "agg": "avg", "label": "Typo"},
                     {"id": "s3", "source": "dataset", "dataset": "gone",
                      "columns": [{"name": "v", "type": "number"}],
                      "field": "v", "agg": "avg", "label": "Deleted table"}]}
out = bs._data(broken, [], {}, None, "sys", tempfile.mkdtemp())
check("a chart with a broken series still draws the good ones",
      len(out["datasets"]) == 3 and out["datasets"][0]["values"][0] == 5, out["datasets"])
check("...the broken ones come back empty, not missing",
      all(v is None for v in out["datasets"][1]["values"])
      and all(v is None for v in out["datasets"][2]["values"]))
said = {wn["id"]: wn["message"] for wn in out.get("warnings") or []}
check("...and the response says WHICH one is wrong",
      "s2" in said and "s3" in said and "s1" not in said, said)
check("...in words that name the fault",
      "samples" in said["s2"] and "gone" in said["s3"], said)

# -- 8. SQL stays out of it ---------------------------------------------------
# A hand-written statement has no schema to align on: nothing can know which
# of its columns is a timestamp or whether its rows are one per bucket.
src = read("core", "services", "chart_series.py")
check("the reason SQL is not a series source is written down",
      "SQL is deliberately not a series source" in src)
check("a statement is not a series source",
      "sql" not in cs.ENTRY_SOURCES and "sql" not in cs.TIME_SOURCES)
check("...and nothing here calls the statement path",
      "_sql_data(" not in src)
check("the union is explained where somebody would change it",
      "Union, not intersection" in src)
check("so is what eight series per poll costs",
      "WHAT IT COSTS" in src)

print()
print("multi-source chart series: " + ("OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
