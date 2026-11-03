# An X field the operator chose wins over the time grouping.
#
# Owner (2026-10-13): "If the user has already dragged fields from the table
# nodes into the X-axis or Y-axis, the chart should use those selected
# fields by default. The system should only apply the datetime-based
# configuration when the user has not manually assigned fields to the axes."
#
# What counts as ASSIGNED (there is no drag-onto-an-axis slot in the
# product; a column reaches a chart by its Data Table wire and is placed on
# an axis by a picker):
#   X  "One point per <column>" -- the node's `group_by` on a one-input
#      chart, an input's own `series[sid].query.group_by`, a Builder row's
#      query.group_by. A saved chart with one counts (back-compat). The
#      compiler's own default for a word column on a pie (count each value)
#      is NOT an assignment: a chosen step still wins over it, as before.
#   Y  "Work out" + "Of which column" (the wired column). Never touched by
#      the time settings.
#
# Pinned through the real compile and render (fake store):
#   1. a date column as X: grouped by that column's own values, no time
#      bucket, and the chart's Time range still filters the rows
#   2. a status column as X: the same, its words on the axis
#   3. no X field: the time step applies (Automatic or chosen)
#   4. several inputs that disagree on X: the first input's choice is the
#      chart's, the others follow, and a grey note says so
#   5. several inputs all with an X field: the Time range filters each
#   6. Y is never changed by the time settings
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
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"pcb": {"key": "pcb", "name": "pcb", "columns": [
    {"name": "status", "type": "text"}, {"name": "shift_day", "type": "text"},
    {"name": "total_panel", "type": "number"}]}}
CALLS = []


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None):
    CALLS.append({"group_by": group_by, "bucket": bucket, "since": since,
                  "until": until, "agg": agg, "field": field})
    if group_by == "shift_day":
        return {"buckets": [{"key": "2026-10-11", "value": 4.0},
                            {"key": "2026-10-12", "value": 6.0}]}
    if group_by == "status":
        return {"buckets": [{"key": "OK", "value": 9.0}, {"key": "VIOLATION", "value": 2.0}]}
    now = datetime.datetime.now().astimezone().replace(minute=0, second=0, microsecond=0)
    return {"buckets": [{"key": now.isoformat(), "value": 5.0}]}


datasets_repo.aggregate = fake_aggregate


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def compile_chart(data, inputs=1):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("t1", "output.dataset", {"dataset": "pcb", "name": "CAM1",
                                           "mode": "change", "sources": {}}),
             node("t2", "output.dataset", {"dataset": "pcb", "name": "CAM2",
                                           "mode": "change", "sources": {}}),
             node("g", "dashboard.graph", {"name": "Panels", **data})]
    edges = [{"id": "e1", "source": "s", "target": "fr"}]
    for t in ("t1", "t2"):
        for col in ("status", "shift_day", "total_panel"):
            edges.append({"id": f"{t}{col}", "source": "fr", "target": t,
                          "targetHandle": f"c_{col}"})
    edges.append({"id": "w1", "source": "t1", "sourceHandle": "o_total_panel",
                  "target": "g", "targetHandle": "in"})
    if inputs == 2:
        edges.append({"id": "w2", "source": "t2", "sourceHandle": "o_total_panel",
                      "target": "g", "targetHandle": "s2"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES,
                         tables=TABLES)
    assert not c["errors"], c["errors"]
    return builder_service.validate([next(x for x in c["widgets"] if x["id"] == "wf_g")])[0]


def render(w):
    CALLS.clear()
    return builder_service.compute([w], [], {}, slug="smoke")[0]["data"]


# -- 1. a date column as X ---------------------------------------------------------
for kind in ("line", "area", "bar"):
    w = compile_chart({"kind": kind, "group_by": "shift_day", "bucket": "6h",
                       "range": "7d", "agg": "sum"})
    q = w["query"]
    check(f"{kind}: a date column as X is grouped by its own values, no time step",
          q.get("group_by") == "shift_day" and not q.get("bucket"), q)
    d = render(w)
    check(f"{kind}: ...the store is asked per value of that column, filtered by the range",
          CALLS and CALLS[0]["group_by"] == "shift_day" and not CALLS[0]["bucket"]
          and CALLS[0]["since"], CALLS[:1])
    check(f"{kind}: ...and the axis shows those dates",
          [str(x) for x in d.get("labels") or []] == ["10-11", "10-12"]
          or (d.get("labels") or [])[:2] == ["2026-10-11", "2026-10-12"], d.get("labels"))
    check(f"{kind}: Y is what Work out says, untouched by the step",
          CALLS[0]["agg"] == "sum" and CALLS[0]["field"] == "total_panel", CALLS[:1])

# -- 2. a status column as X -------------------------------------------------------
w = compile_chart({"kind": "bar", "group_by": "status", "bucket": "1d", "range": "30d",
                   "agg": "count"})
d = render(w)
check("a status column as X draws its words, the step does not apply",
      w["query"].get("group_by") == "status" and not w["query"].get("bucket")
      and d.get("labels") == ["OK", "VIOLATION"], (w["query"], d.get("labels")))

# -- 3. no X field: time grouping ---------------------------------------------------
w = compile_chart({"kind": "line", "bucket": "6h", "range": "7d"})
check("with no X field the chosen step is the axis",
      w["query"].get("bucket") == "6h" and not w["query"].get("group_by"), w["query"])
w = compile_chart({"kind": "line", "range": "24h"})
check("...and with no step, Automatic (the compiler's hour)",
      w["query"].get("bucket") == "hour", w["query"])

# -- 4. several inputs disagreeing on X ------------------------------------------------
w = compile_chart({"kind": "bar", "bucket": "1d", "range": "7d", "series": {
    "s1": {"query": {"group_by": "status"}}, "s2": {}}}, inputs=2)
d = render(w)
note = " ".join(x["message"] for x in d.get("warnings") or [] if not x.get("id"))
check("inputs that disagree: the first input's X field is the chart's, and it is said",
      "first input's 'status'" in note
      and all(c["group_by"] == "status" for c in CALLS), (note, CALLS))
w = compile_chart({"kind": "line", "bucket": "1d", "range": "7d", "series": {
    "s1": {}, "s2": {"query": {"group_by": "status"}}}}, inputs=2)
d = render(w)
note = " ".join(x["message"] for x in d.get("warnings") or [] if not x.get("id"))
check("...and when the first is time, the others follow time",
      "first input's time" in note and all(not c["group_by"] for c in CALLS)
      and d.get("step_s") == 86400, (note, CALLS[:2]))

# -- 5. several inputs, all with an X field: the range filters each -------------------
w = compile_chart({"kind": "bar", "range": "7d", "series": {
    "s1": {"query": {"group_by": "status"}}, "s2": {"query": {"group_by": "status"}}}},
    inputs=2)
check("each input with an X field still takes the chart's range as a filter",
      all((s.get("query") or {}).get("range") for s in w["series"]), w["series"])
d = render(w)
check("...in the store query", CALLS and all(c["since"] for c in CALLS), CALLS)
w = compile_chart({"kind": "bar", "range": "24h", "series": {
    "s1": {"query": {"group_by": "status"}}, "s2": {"query": {"group_by": "status"}}}},
    inputs=2)
check("a chart left at the stored default range is not newly filtered (back-compat)",
      not any((s.get("query") or {}).get("range") for s in w["series"]), w["series"])

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
