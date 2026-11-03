# Thresholds per camera, through the compile, the render and every save path
# (owner, 2026-10-13: "Each camera should have its own customizable threshold
# rules ... These settings should apply only to the corresponding camera's
# data series").
#
#   1. an input's own bands (data.series[<sid>].bands) ride its compiled
#      entry and reach the page on THAT series' dataset only -- CAM 1 below
#      10 yellow / over 10 red, CAM 2 below 50 green / over 50 blue -- keyed
#      by series id, so swapped wires never swap thresholds
#   2. an input with none follows the chart's shared bands (nothing on its
#      dataset); a chart saved before has none anywhere
#   3. card-style takes them (cleaned; null = back to the shared ones), a
#      canvas save keeps the stored colours while its limit edits land, and a
#      stale canvas tab cannot undo a Builder's per-camera edit
#   4. a hand-made Builder chart keeps a row's own bands when stored
import copy
import os
import sys
import tempfile

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


YEL, RED, GRN, BLU = "#ffff00", "#ff0000", "#00aa00", "#0000ff"
CAM1 = {"at": [10], "colors": [YEL, RED]}
CAM2 = {"at": [50], "colors": [GRN, BLU]}
ANSWER = {"cam1": 15.0, "cam2": 40.0}


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None, field_window=None):
    return {"buckets": [{"key": "2026-10-13T08:00:00+08:00", "value": ANSWER[dataset]}]}


datasets_repo.aggregate = fake_aggregate
TABLES = {k: {"key": k, "name": k.upper(), "columns": [{"name": "total_panel", "type": "number"}]}
          for k in ("cam1", "cam2")}


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def compile_chart(series, swap=False, shared=None):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("g", "dashboard.graph", {"name": "Panels", "kind": "bar", "x_type": "time",
                                           "time_interval": "hour", "bucket": "1h",
                                           "range": "24h", "agg": "sum", "series": series,
                                           **({"bands": shared} if shared else {})})]
    edges = [{"id": "e1", "source": "s", "target": "fr"}]
    order = ("cam2", "cam1") if swap else ("cam1", "cam2")
    for i, key in enumerate(order):
        nodes.append(node(f"t{key}", "output.dataset", {"dataset": key, "name": key.upper(),
                                                        "mode": "change", "sources": {}}))
        edges.append({"id": f"c{key}", "source": "fr", "target": f"t{key}",
                      "targetHandle": "c_total_panel"})
        edges.append({"id": f"w{key}", "source": f"t{key}", "sourceHandle": "o_total_panel",
                      "target": "g", "targetHandle": "in" if i == 0 else "s2"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                         [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}],
                         tables=TABLES)
    assert not c["errors"], c["errors"]
    return builder_service.validate([next(x for x in c["widgets"] if x["id"] == "wf_g")])[0]


def render(w):
    return builder_service.compute([w], [], {}, slug="smoke")[0]


# -- 1. each camera its own ----------------------------------------------------------
w = compile_chart({"s1": {"label": "CAM 1", "bands": CAM1}, "s2": {"label": "CAM 2", "bands": CAM2}})
page = render(w)
by = {d["id"]: d for d in page["data"]["datasets"]}
check("each compiled input carries its own bands",
      w["series"][0]["bands"] == CAM1 and w["series"][1]["bands"] == CAM2, w["series"])
check("...and each camera's dataset reaches the page with ITS bands only",
      by["s1"]["bands"] == CAM1 and by["s2"]["bands"] == CAM2
      and by["s1"]["label"] == "CAM 1" and by["s2"]["label"] == "CAM 2", by)
w = compile_chart({"s1": {"label": "CAM 1", "bands": CAM1}, "s2": {"label": "CAM 2", "bands": CAM2}},
                  swap=True)
by = {d["id"]: d for d in render(w)["data"]["datasets"]}
check("wires swapped: the thresholds stay with the series (s1 keeps CAM 1's), never cross",
      by["s1"]["bands"] == CAM1 and by["s2"]["bands"] == CAM2
      and w["series"][0]["dataset"] == "cam2", (w["series"], by))

# -- 2. inheritance -------------------------------------------------------------------------
shared = {"at": [100], "colors": ["#16a34a", "#dc2626"]}
w = compile_chart({"s1": {"label": "CAM 1", "bands": CAM1}, "s2": {"label": "CAM 2"}}, shared=shared)
page = render(w)
by = {d["id"]: d for d in page["data"]["datasets"]}
check("a camera with none of its own carries none (it follows the chart's shared bands)",
      "bands" not in by["s2"] and by["s1"]["bands"] == CAM1 and page["bands"] == shared, by)
w = compile_chart({"s1": {"label": "CAM 1"}, "s2": {"label": "CAM 2"}})
page = render(w)
check("a chart saved before: no per-camera bands anywhere",
      all("bands" not in d for d in page["data"]["datasets"])
      and all("bands" not in s for s in w["series"]))

# -- 3. the save paths ------------------------------------------------------------------------
out = wf.clean_card_series({"s1": {"bands": {"at": [10, "x", 5], "colors": [YEL]}},
                            "s2": {"bands": None}}, {"s2": {"bands": CAM2, "label": "CAM 2"}})
check("card-style cleans an input's bands (sorted, N+1 colours); null = the shared ones",
      out["s1"]["bands"]["at"] == [5.0, 10.0] and len(out["s1"]["bands"]["colors"]) == 3
      and out["s2"] == {"label": "CAM 2"}, out)
stored = {"nodes": [{"id": "g", "type": "dashboard.graph",
                     "data": {"kind": "bar", "series": {"s1": {"label": "CAM 1", "bands": CAM1}}}}]}
canvas = {"nodes": [{"id": "g", "type": "dashboard.graph",
                     "data": {"kind": "bar", "series": {"s1": {"label": "CAM 1",
                                                               "bands": {"at": [10]}}}}}]}
wf.keep_chart_colours(canvas, stored)
check("a canvas save without colours keeps CAM 1's stored colours",
      canvas["nodes"][0]["data"]["series"]["s1"]["bands"] == {"at": [10], "colors": [YEL, RED]},
      canvas["nodes"][0]["data"])
canvas = {"nodes": [{"id": "g", "type": "dashboard.graph",
                     "data": {"kind": "bar", "series": {"s1": {"label": "CAM 1",
                              "bands": {"at": [20], "colors": [YEL, RED]}}}}}]}
wf.keep_chart_colours(canvas, stored)
check("...and its limit edit (10 -> 20) lands, the colours moving with it",
      canvas["nodes"][0]["data"]["series"]["s1"]["bands"] == {"at": [20], "colors": [YEL, RED]})
store = {"workflows": [{"graph": copy.deepcopy(stored)}]}
wf.apply_card_style(store, [{"id": "wf_g", "series": {"s1": {"bands": {"at": [10], "colors": [YEL, BLU]}}}}])
nd = store["workflows"][0]["graph"]["nodes"][0]["data"]
stale = copy.deepcopy(stored)
wf.keep_chart_colours(stale, {"nodes": [{"id": "g", "type": "dashboard.graph", "data": nd}]})
wf.keep_builder_edits(stale, {"nodes": [{"id": "g", "type": "dashboard.graph", "data": nd}]})
check("a Builder's per-camera colour reaches the node and a stale canvas tab cannot undo it",
      nd["series"]["s1"]["bands"]["colors"] == [YEL, BLU]
      and stale["nodes"][0]["data"]["series"]["s1"]["bands"]["colors"] == [YEL, BLU],
      (nd, stale["nodes"][0]["data"]))

# -- 4. a hand-made chart ---------------------------------------------------------------------
hand = builder_service.validate([{
    "id": "h", "title": "H", "kind": "line", "source": "history", "range": "24h",
    "series": [{"id": "a", "source": "history", "key": "k", "bands": CAM1},
               {"id": "b", "source": "history", "key": "k2"}]}])[0]
check("a hand-made chart keeps a row's own bands, and only that row's",
      hand["series"][0]["bands"] == CAM1 and "bands" not in hand["series"][1], hand["series"])

print("all good" if not bad else f"{bad} check(s) failed")
sys.exit(1 if bad else 0)
