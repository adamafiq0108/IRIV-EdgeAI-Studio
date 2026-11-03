# A chart's CATEGORIES can come from a text column: one bar or slice per
# distinct value, the value being how many rows hold it, in name order.
#
# Most of the road already existed -- a single text column wired into a bar
# or a donut grouped by itself, and the stores answer GROUP BY. What did not:
#
#   - a text column wired into a SEVERAL-series chart drew one total per
#     line (every row counted once), never one bar per value
#   - the series row offered "average" and "total" of a word, which the
#     store refuses and the line then drew nothing
#   - the order was whatever the store used: Postgres by its collation, Python
#     by byte order, where every capital comes before every lower-case letter
#   - past 30 groups the several-series chart kept the LAST 30 by name, so
#     the front of the alphabet silently fell off the chart
#
# What is pinned here:
#   1. builder.name_key: case-insensitive, digits as numbers
#   2. several text columns into a bar chart -> each series groups by itself,
#      counted; with a live rule beside them it does not (no labels to align)
#   3. the row can still say "one total" (blank), and a word cannot be averaged
#   4. the several-series resolver sorts the union by name and keeps the
#      FIRST 30 names -- but the LATEST 30 days
#   5. the single-source chart paths (dataset and event store) put grouped
#      bars in the same name order
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder                               # noqa: E402
from core.services import chart_series                          # noqa: E402
from core.services import workflow as wf                        # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {
    "defects": {"key": "defects", "name": "Defects", "columns": [
        {"name": "defect_type", "type": "text"},
        {"name": "weight", "type": "number"}]},
    "rejects": {"key": "rejects", "name": "Rejects", "columns": [
        {"name": "reason", "type": "text"},
        {"name": "shift_day", "type": "date"}]},
}


def check(label, cond, detail=None):
    assert cond, f"{label}: {detail!r}"
    print("  ok  ", label)


# 1. NAME ORDER ------------------------------------------------------------
names = ["crack", "Dent", "a10", "A2", "scratch", "a1", "Blister", "dent"]
ordered = sorted(names, key=builder.name_key)
check("names sort case-insensitively, digits as numbers",
      ordered == ["a1", "A2", "a10", "Blister", "crack", "Dent", "dent",
                  "scratch"], ordered)
check("...where plain sorted() put every capital first",
      sorted(names)[0] == "A2" and sorted(names).index("crack") > 3)
check("a missing value sorts first, numbers as numbers, then words",
      sorted([None, "b", 10, 3], key=builder.name_key) == [None, 3, 10, "b"],
      sorted([None, "b", 10, 3], key=builder.name_key))


# 2. SEVERAL TEXT COLUMNS INTO ONE CATEGORY CHART ----------------------------
def table(nid, key, y=0):
    return {"id": nid, "type": "output.dataset", "position": {"x": 0, "y": y},
            "data": {"dataset": key}}


def chart(kind="bar", series=None):
    return {"id": "g", "type": "dashboard.graph", "position": {"x": 3, "y": 0},
            "data": {"name": "Why", "kind": kind, "series": series or {}}}


def compile_(nodes, edges):
    out = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                           ZONES, tables=TABLES)
    widget = next((w for w in out["widgets"] if w["id"] == "wf_g"), None)
    return out, widget


WIRES = [{"id": "w1", "source": "t1", "target": "g", "targetHandle": "in",
          "sourceHandle": "o_defect_type"},
         {"id": "w2", "source": "t2", "target": "g", "targetHandle": "s2",
          "sourceHandle": "o_reason"}]
NODES = [table("t1", "defects"), table("t2", "rejects", y=1)]

for kind in ("bar", "donut", "pie", "radar"):
    out, w = compile_(NODES + [chart(kind)], WIRES)
    check(f"{kind}: compiles", not out["errors"] and w, out["errors"])
    got = [(s["field"], s["agg"], (s.get("query") or {}).get("group_by"))
           for s in w["series"]]
    check(f"{kind}: each text column counts per value of itself",
          got == [("defect_type", "count", "defect_type"),
                  ("reason", "count", "reason")], got)

# ...the SAME wires on a line chart follow the time axis, as they always did.
out, w = compile_(NODES + [chart("line")], WIRES)
check("a time chart does not regroup a text column",
      all(not (s.get("query") or {}).get("group_by") for s in w["series"]),
      w["series"])

# ...and with a LIVE RULE beside them, no series groups: the rule has one
# number and no labels, and would draw as an empty line with a warning.
rule = {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 2},
        "data": {"fnId": "in_frame", "name": "In frame", "mode": "realtime"}}
src = {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 3},
       "data": {}}
out, w = compile_(NODES + [chart("bar"), rule, src], WIRES + [
    {"id": "e0", "source": "s", "target": "fr"},
    {"id": "w3", "source": "fr", "target": "g", "targetHandle": "s3"}])
check("beside a live rule a text column is one total again",
      not any((s.get("query") or {}).get("group_by") for s in w["series"]),
      w["series"])

# ...and a NUMBER column is not regrouped by its own values (two bars, 0/1).
out, w = compile_([table("t1", "defects"), table("t2", "rejects", y=1),
                   chart("bar")],
                  [{**WIRES[0], "sourceHandle": "o_weight"}, WIRES[1]])
first = w["series"][0]
check("a number column keeps its average and its one total",
      first["agg"] == "avg" and not (first.get("query") or {}).get("group_by"),
      first)


# 3. THE ROW STILL DECIDES --------------------------------------------------
out, w = compile_(NODES + [chart("bar", {
    "s1": {"query": {"group_by": ""}},          # "nothing - one value"
    "s2": {"agg": "avg"}})], WIRES)
s1, s2 = w["series"]
check("blank 'one point per' is one total, not the default grouping",
      not (s1.get("query") or {}).get("group_by"), s1)
check("an average of a word is refused and counted",
      s2["agg"] == "count", s2)
check("...and the refusal is said",
      any("is text, so it can only be counted" in x["message"]
          for x in out["warnings"]), out["warnings"])
# ...and grouping by ANOTHER column still wins.
out, w = compile_(NODES + [chart("bar", {
    "s1": {"query": {"group_by": "weight"}}})], WIRES)
check("another column picked on the row wins",
      w["series"][0]["query"]["group_by"] == "weight", w["series"][0])


# 4. THE SEVERAL-SERIES RESOLVER: NAME ORDER, FIRST 30 ------------------------
def resolve_with(pairs_by_id, kind="bar"):
    entries = chart_series.migrate([
        {"id": sid, "source": "dataset", "dataset": "defects",
         "columns": TABLES["defects"]["columns"], "field": "defect_type",
         "agg": "count", "query": {"group_by": "defect_type"}}
        for sid in pairs_by_id], "dataset")
    real = chart_series._dataset_groups
    chart_series._dataset_groups = \
        lambda entry, *a, **k: list(pairs_by_id[entry["id"]])
    try:
        return chart_series.resolve({"kind": kind, "source": "dataset"},
                                    entries, [], {})
    finally:
        chart_series._dataset_groups = real


data = resolve_with({"s1": [("scratch", 4), ("Crack", 2), ("dent", 1)],
                     "s2": [("Blister", 3), ("crack", 5), ("a10", 1),
                            ("a2", 2)]})
check("the union of every series' values is in name order",
      data["labels"] == ["a2", "a10", "Blister", "Crack", "crack", "dent",
                         "scratch"], data["labels"])
by_label = dict(zip(data["labels"], data["datasets"][0]["values"]))
check("...and each series' counts land on their own names",
      by_label["scratch"] == 4 and by_label["Blister"] is None, by_label)

many = [(f"type {i}", i) for i in range(1, 41)]
data = resolve_with({"s1": many})
check("past 30 names, the FIRST 30 by name are drawn",
      data["labels"][0] == "type 1" and data["labels"][-1] == "type 30"
      and len(data["labels"]) == 30, data["labels"])
check("...and the chart says it cut",
      any("first 30" in x["message"] for x in data.get("warnings") or []),
      data.get("warnings"))
days = [(f"2026-08-{d:02d}", d) for d in range(1, 32)]
data = resolve_with({"s1": days})
check("past 30 DAYS, the latest 30 are drawn",
      data["labels"][-1] == "08-31" and data["labels"][0] == "08-02",
      data["labels"])


# 5. SINGLE-SOURCE CHARTS, THE SAME ORDER ------------------------------------
from core.repos import datasets as datasets_repo               # noqa: E402
from core.repos import events as events_repo                   # noqa: E402

UNSORTED = [{"key": "scratch", "value": 1}, {"key": "Crack", "value": 2},
            {"key": "a10", "value": 3}, {"key": "A2", "value": 4}]
real_agg = datasets_repo.aggregate
datasets_repo.aggregate = lambda *a, **k: {"buckets": [dict(b) for b in UNSORTED]}
try:
    w = {"kind": "donut", "source": "dataset", "dataset": "defects",
         "columns": TABLES["defects"]["columns"],
         "query": {"agg": "count", "group_by": "defect_type"}}
    out = builder._dataset_data(w, None, "demo")
    check("a dataset chart grouped by a text column is in name order",
          out["labels"] == ["A2", "a10", "Crack", "scratch"]
          and out["values"] == [4, 3, 2, 1], out)
    w["query"] = {"agg": "count", "bucket": "hour"}
    out = builder._dataset_data(w, None, "demo")
    check("...a time bucket is left in the store's (time) order",
          out["labels"] == ["scratch", "Crack", "a10", "A2"], out["labels"])
finally:
    datasets_repo.aggregate = real_agg

real_avail, real_eagg = events_repo.available, events_repo.aggregate
events_repo.available = lambda *a, **k: True
events_repo.aggregate = lambda *a, **k: {"buckets": [dict(b) for b in UNSORTED]}
try:
    w = {"kind": "bar", "source": "query",
         "query": {"agg": "count", "group_by": "camera"}}
    out = builder._query_data(w, None, "demo")
    check("an event chart grouped by a field is in name order",
          out["labels"] == ["A2", "a10", "Crack", "scratch"], out["labels"])
finally:
    events_repo.available, events_repo.aggregate = real_avail, real_eagg

# ...and the in-memory fallback, when the event store is down.
events_repo_available = events_repo.available
events_repo.available = lambda *a, **k: False
try:
    ring = [{"ts": "2026-09-29T10:00:00", "type": "violation", "camera": c}
            for c in ("cam10", "Cam2", "cam2", "cam1")]
    w = {"kind": "bar", "source": "query",
         "query": {"agg": "count", "group_by": "camera"}}
    out = builder._query_data(w, None, "demo", events=ring)
    check("the event-ring fallback groups in the same name order",
          out["labels"] == ["cam1", "Cam2", "cam2", "cam10"], out["labels"])
finally:
    events_repo.available = events_repo_available

# ...and one text column wired into a donut still counts per value of itself.
out, w = compile_([table("t1", "defects"), chart("donut")], [WIRES[0]])
check("one text column into a donut groups by itself, counted",
      w["query"] == {"agg": "count", "field": "defect_type",
                     "group_by": "defect_type"}, w.get("query"))

# -- review fixes (Yaya, 2026-09-29) ----------------------------------------
# R4. Labels that ARE numbers order as numbers: decimals and negatives too.
check("R4 decimals order numerically",
      sorted(["1.3", "1.5", "1.25"], key=builder.name_key)
      == ["1.25", "1.3", "1.5"],
      sorted(["1.3", "1.5", "1.25"], key=builder.name_key))
check("R4 negatives come first",
      sorted(["0", "3", "-5", "-10"], key=builder.name_key)
      == ["-10", "-5", "0", "3"],
      sorted(["0", "3", "-5", "-10"], key=builder.name_key))
check("R4 numbers before words, words in natural order",
      sorted(["b", "2.5", "a10", "a2", "-1"], key=builder.name_key)
      == ["-1", "2.5", "a2", "a10", "b"],
      sorted(["b", "2.5", "a10", "a2", "-1"], key=builder.name_key))
check("R4 'nan' is a word, not a number",
      sorted(["nan", "5"], key=builder.name_key) == ["5", "nan"])

# R5. An 8-digit name is not a date: the FIRST 30 are kept, not the last.
batches = [(str(20260101 + i), i) for i in range(40)]
data = resolve_with({"s1": batches})
check("R5 8-digit numeric names are capped as names (first 30)",
      data["labels"][0] == "20260101" and data["labels"][-1] == "20260130",
      data["labels"][:2] + data["labels"][-2:])

# R6. A NULL group reads "(blank)" and sorts first, everywhere.
data = resolve_with({"s1": [("dent", 1), (None, 4), ("None", 2), ("crack", 3)]})
check("R6 a NULL group is '(blank)', first, apart from the word 'None'",
      data["labels"] == ["(blank)", "crack", "dent", "None"], data["labels"])
check("R6 ...with its own value",
      data["datasets"][0]["values"][0] == 4, data["datasets"][0]["values"])
datasets_repo.aggregate = lambda *a, **k: {"buckets": [
    {"key": "b", "value": 1}, {"key": None, "value": 5}, {"key": "a", "value": 2}]}
try:
    out = builder._dataset_data({"kind": "bar", "source": "dataset",
                                 "dataset": "defects",
                                 "columns": TABLES["defects"]["columns"],
                                 "query": {"agg": "count",
                                           "group_by": "defect_type"}},
                                None, "demo")
    check("R6 a dataset chart labels a NULL group '(blank)', first",
          out["labels"] == ["(blank)", "a", "b"], out["labels"])
finally:
    datasets_repo.aggregate = real_agg

# ...and a json column is never a category.
JSON_TABLES = {"logs": {"key": "logs", "name": "Logs", "columns": [
    {"name": "payload", "type": "json"}, {"name": "kind", "type": "text"}]}}
out = wf.compile_graph(wf.clean_graph({"nodes": [
    table("t1", "logs"), table("t2", "logs", y=1),
    chart("bar", {"s1": {"query": {"group_by": "payload"}}})],
    "edges": [{"id": "w1", "source": "t1", "target": "g", "sourceHandle": "o_payload"},
              {"id": "w2", "source": "t2", "target": "g", "targetHandle": "s2",
               "sourceHandle": "o_kind"}]}), ZONES, tables=JSON_TABLES)
w = next(x for x in out["widgets"] if x["id"] == "wf_g")
check("R6 a json column is not grouped by itself, nor picked as a grouping",
      not (w["series"][0].get("query") or {}).get("group_by")
      and w["series"][1]["query"]["group_by"] == "kind", w["series"])
seen_sql = []


class _Cur:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        seen_sql.append(sql)

    def fetchall(self):
        return []


class _Conn:
    def cursor(self):
        return _Cur()

    def close(self):
        pass


real_connect = datasets_repo.connect
datasets_repo.connect = lambda *a, **k: _Conn()
try:
    datasets_repo.aggregate("demo", "logs", JSON_TABLES["logs"]["columns"],
                            group_by="payload")
finally:
    datasets_repo.connect = real_connect
check("R6 ...and the store refuses to GROUP BY json",
      seen_sql and "GROUP BY" not in seen_sql[0], seen_sql)
print("ALL OK")
