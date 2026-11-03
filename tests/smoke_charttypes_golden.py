# GOLDEN for the chart type picker slice (2026-10-14): the picker and the
# per-type property panels are UI only. A chart saved before them -- every
# kind, legacy (no x_type) and model-shaped, a bar with Time and with a
# column on X -- must compile to the very same widget and normalize to the
# very same node data as it did before the slice.
#
# The golden file was captured BEFORE any code of the slice changed
# (tests/golden_chart_types.json). The next slice (Scatter Plot) touches the
# server; this is what tells it whether an old chart moved.
#
#   py -3 tests/smoke_charttypes_golden.py
#   py -3 tests/smoke_charttypes_golden.py --write-golden   (only before a change)
import copy
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service             # noqa: E402
from core.services import chartmodel                             # noqa: E402
from core.services import workflow as wf                         # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_chart_types.json")
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


COLS = [{"name": "status", "type": "text"}, {"name": "object_id", "type": "text"},
        {"name": "total_panel", "type": "number"}]
TABLES = {k: {"key": k, "name": n, "columns": COLS}
          for k, n in (("cam1", "PCB-SENSATA-CAM1"), ("cam2", "PCB-SENSATA-CAM2"))}
ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def compile_chart(data, wires):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("g", "dashboard.graph", {"name": "Panels", **data})]
    edges = [{"id": "e1", "source": "s", "target": "fr"}]
    for key in TABLES:
        nodes.append(node(f"t_{key}", "output.dataset",
                          {"dataset": key, "name": TABLES[key]["name"],
                           "mode": "change", "sources": {}}))
        for col in COLS:
            edges.append({"id": f"{key}{col['name']}", "source": "fr",
                          "target": f"t_{key}", "targetHandle": f"c_{col['name']}"})
    for i, (key, col) in enumerate(wires):
        edges.append({"id": f"w{i}", "source": f"t_{key}", "sourceHandle": f"o_{col}",
                      "target": "g", "targetHandle": "in" if i == 0 else f"s{i + 1}"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES,
                         tables=TABLES)
    w = next((x for x in c["widgets"] if x["id"] == "wf_g"), None)
    return builder_service.validate([w])[0] if w else None


STATUS = (("cam1", "status"), ("cam2", "status"))
NUM = (("cam1", "total_panel"), ("cam2", "total_panel"))
ONE = (("cam1", "total_panel"),)
#: Old charts, one per picker card they map to, legacy and model-shaped.
CASES = {
    "line legacy, hourly sum": ({"kind": "line", "bucket": "1h", "range": "24h",
                                 "series": {"s1": {"agg": "sum", "label": "CAM 1"},
                                            "s2": {"agg": "sum", "show": False}}}, NUM),
    "spline legacy (Smooth line)": ({"kind": "spline", "range": "24h"}, NUM),
    "area legacy, one column": ({"kind": "area", "range": "week"}, ONE),
    "area model, day": ({"kind": "area", "x_type": "time", "time_interval": "day",
                         "bucket": "1d", "range": "7d", "agg": "count"}, NUM),
    "line model, column X": ({"kind": "line", "x_type": "column", "group_by": "status",
                              "agg": "count"}, STATUS),
    "bar legacy per day (Time-based Bar)": ({"kind": "bar", "bucket": "1d", "range": "7d",
                                             "agg": "count"}, STATUS),
    "bar legacy per status (Comparison Bar)": ({"kind": "bar", "range": "24h",
                                                "series": {"s1": {"query": {"group_by": "status",
                                                                            "range": "page"}}}},
                                               STATUS),
    "bar model time": ({"kind": "bar", "x_type": "time", "time_interval": "hour",
                        "bucket": "1h", "range": "24h", "agg": "sum",
                        "orientation": "horizontal"}, NUM),
    "bar model column, each input": ({"kind": "bar", "x_type": "column", "group_by": "",
                                      "agg": "count", "range": "page", "unit": "pcs"}, NUM),
    "pie legacy": ({"kind": "pie", "range": "page"}, STATUS),
    "donut model": ({"kind": "donut", "x_type": "column", "group_by": "status",
                     "agg": "count", "range": "", "bands": {"at": [5]}}, STATUS),
    "radar legacy": ({"kind": "radar", "series": {"s1": {"label": "A"}}}, STATUS),
}


def snapshot():
    out = {}
    for name, (data, wires) in CASES.items():
        d = copy.deepcopy(data)
        chartmodel.normalize_node(d)
        out[name] = {"widget": compile_chart(copy.deepcopy(data), wires),
                     "normalized": d}
    # Round-trip through JSON so the comparison is on what a file would hold.
    return json.loads(json.dumps(out, sort_keys=True, default=str))


now = snapshot()
if "--write-golden" in sys.argv:
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(now, f, indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

golden = json.load(io.open(GOLDEN, encoding="utf-8"))
check("the golden holds every case", sorted(golden) == sorted(now),
      (sorted(golden), sorted(now)))
for name in sorted(golden):
    want, got = golden[name], now.get(name)
    check(f"{name}: compiles and normalizes exactly as before the picker",
          want == got, json.dumps({"want": want, "got": got})[:600])

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
