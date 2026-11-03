# How a chart is DRAWN reaches the page: axis titles from the configuration,
# and the operator's own titles, bar orientation, bar style and value labels
# (rendering addendum to the chart builder brief, owner 2026-10-13: "the user
# can see the X and Y axis clearly, and every detail").
#
#   1. builder.compute hands every chart its automatic axis titles, from the
#      chart builder model or -- a chart saved before it -- its query
#   2. the look fields are stored cleaned, only when set (older widgets byte
#      for byte the same), and reach the page
#   3. a compiled chart's look lives on its node: card-style writes it
#      cleaned, the canvas cannot (keep_card_look), the compile carries it
import copy
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

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. automatic titles ------------------------------------------------------------
cases = [
    ({"kind": "bar", "chart": {"x_type": "column", "x_field": "defect_type",
                               "y_agg": "count"}}, ("defect type", "Total rows")),
    ({"kind": "line", "unit": "panels", "chart": {"x_type": "time", "step": "6h",
                                                  "y_agg": "avg", "y_field": "total_panel"}},
     ("Every 6 hours", "Average total panel (panels)")),
    ({"kind": "bar", "chart": {"x_type": "time", "step": "1d", "y_agg": "count_nonnull",
                               "y_field": "object_id"}}, ("Day", "Count of object id")),
    ({"kind": "pie", "chart": {"x_type": "column", "x_field": "", "y_agg": "sum",
                               "y_field": "weight"}}, ("Input", "Sum weight")),
    # Saved before the model: read off the query and the step.
    ({"kind": "bar", "source": "dataset", "query": {"agg": "count", "group_by": "status"}},
     ("status", "Total rows")),
    ({"kind": "area", "source": "history", "interval": "1h", "series": [
        {"id": "s1", "source": "history", "key": "k", "agg": "max"}]}, ("Hour", "Maximum")),
]
for w, (x, y) in cases:
    got = chartmodel.axis_titles(w)
    check(f"{w['kind']}: X '{x}', Y '{y}'", (got["x"], got["y"]) == (x, y), got)

stats = {"k": 3}
w = builder_service.validate([{"id": "c1", "title": "Trend", "kind": "line",
                               "source": "history", "key": "k", "range": "24h",
                               "interval": "1h", "unit": "people"}])[0]
page = builder_service.compute([w], [], stats)[0]
check("the page gets a chart's axis titles", page.get("axis") == {
      "x": "Hour", "y": "Value (people)", "time": True}, page.get("axis"))
tile = builder_service.compute(builder_service.validate([
    {"id": "t1", "title": "T", "kind": "tile", "source": "stat", "key": "k"}]), [], stats)[0]
check("...and a tile does not", "axis" not in tile, tile.keys())

# -- 2. the look, stored and drawn ----------------------------------------------------------
raw = {"id": "b1", "title": "Votes", "kind": "bar", "source": "stat", "key": "k",
       "x_title": "  Party  ", "y_title": "Share of the vote " + "x" * 50,
       "orientation": "horizontal", "bar_style": "lollipop", "data_labels": "on"}
saved = builder_service.validate([raw])[0]
check("a bar's look is stored cleaned (titles trimmed and capped)",
      saved["x_title"] == "Party" and len(saved["y_title"]) == 40
      and saved["orientation"] == "horizontal" and saved["bar_style"] == "lollipop"
      and saved["data_labels"] == "on", {k: saved.get(k) for k in chartmodel.LOOK_FIELDS})
junk = builder_service.validate([{**raw, "orientation": "diagonal", "bar_style": "3d",
                                  "data_labels": "maybe"}])[0]
check("...junk is not stored", not any(k in junk for k in ("orientation", "bar_style",
                                                           "data_labels")), junk)
line = builder_service.validate([{**raw, "kind": "line", "source": "history"}])[0]
check("...orientation and bar style are a bar's only",
      "orientation" not in line and "bar_style" not in line and line["x_title"] == "Party")
plain = builder_service.validate([{"id": "p", "title": "P", "kind": "bar", "source": "stat",
                                   "key": "k"}])[0]
check("a chart with no look stores none of these fields",
      not any(k in plain for k in chartmodel.LOOK_FIELDS))
page = builder_service.compute([saved], [], stats)[0]
check("...and the page gets them", page.get("orientation") == "horizontal"
      and page.get("bar_style") == "lollipop" and page.get("x_title") == "Party")

# -- 3. a compiled chart's look lives on its node ----------------------------------------------
store = {"workflows": [{"graph": {"nodes": [{"id": "g", "type": "dashboard.graph",
                                              "data": {"kind": "bar"}}]}}]}
wf.apply_card_style(store, [{"id": "wf_g", "x_title": "Line", "y_title": "",
                             "orientation": "horizontal", "bar_style": "nope",
                             "data_labels": "off"}])
nd = store["workflows"][0]["graph"]["nodes"][0]["data"]
check("card-style writes a compiled chart's look onto its node, cleaned",
      nd.get("x_title") == "Line" and nd.get("orientation") == "horizontal"
      and nd.get("bar_style") == "" and nd.get("data_labels") == "off", nd)
canvas = {"nodes": [{"id": "g", "type": "dashboard.graph",
                     "data": {"kind": "bar", "x_title": "Stale", "orientation": ""}}]}
wf.keep_card_look(canvas, {"nodes": [{"id": "g", "type": "dashboard.graph",
                                      "data": copy.deepcopy(nd)}]})
check("the canvas cannot change it (a stale tab keeps the Builder's)",
      canvas["nodes"][0]["data"]["x_title"] == "Line"
      and canvas["nodes"][0]["data"]["orientation"] == "horizontal")
nodes = [{"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
         {"id": "fr", "type": "analytics.frame", "position": {"x": 0, "y": 0},
          "data": {"fnId": "f", "name": "F", "mode": "realtime"}},
         {"id": "g", "type": "dashboard.graph", "position": {"x": 0, "y": 0},
          "data": {"name": "Votes", "kind": "bar", **{k: nd[k] for k in chartmodel.LOOK_FIELDS}}}]
edges = [{"id": "e1", "source": "s", "target": "fr"}, {"id": "e2", "source": "fr", "target": "g"}]
c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                     [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}])
w = next(x for x in c["widgets"] if x["id"] == "wf_g")
check("the compile carries the node's look onto its card",
      w.get("x_title") == "Line" and w.get("orientation") == "horizontal"
      and w.get("data_labels") == "off" and "bar_style" not in w, w)

print("all good" if not bad else f"{bad} check(s) failed")
sys.exit(1 if bad else 0)
