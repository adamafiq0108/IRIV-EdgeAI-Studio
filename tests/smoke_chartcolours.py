# A chart's colours are set in the Builders only, and a canvas save keeps them.
#
# Owner request (2026-10-12): "all colours for the graph need to be set at the
# Analytics or Dashboard Builder only. So need to separate it from the CARD."
# The workflow node's panel no longer offers a chart's colours (a line's
# colour, a threshold band's colour -- cardFields.showsColours); the Builders
# still do, and write them onto the node through card-style.
#
# What could go wrong, and is pinned here against the real compiler:
#   1. a Builder colour edit (series colour, band colours) lands on the node
#      and survives a recompile into the dashboard row;
#   2. a canvas save whose graph carries NO colours (or stale ones) keeps the
#      stored colours -- the canvas cannot set, clear or reset them -- while
#      the canvas's own edits in the same fields (a series' name, a threshold
#      value) still land;
#   3. a card's colours are not touched by the rule (both panels edit them);
#   4. a node the store does not know keeps what it arrived with, and a node
#      saved before this change keeps rendering its colours.
import copy
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service             # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class FakeFns:
    def get_functions(self, systems_dir, slug):
        return []

    def set_functions(self, systems_dir, slug, functions):
        return functions

    def __getattr__(self, name):
        from core.services import functions as real
        return getattr(real, name)


class FakeDC:
    def get_outputs(self, systems_dir, slug):
        return []

    def set_outputs(self, systems_dir, slug, outs):
        pass

    def set_commands(self, systems_dir, slug, commands):
        pass

    def set_watches(self, systems_dir, slug, watches):
        pass

    def set_logic(self, systems_dir, slug, frames_free):
        pass

    def set_preprocess(self, systems_dir, slug, entries):
        pass

    def cameras(self, systems_dir, slug):
        return []

    def __getattr__(self, name):
        from core.services import detector_control as real
        return getattr(real, name)


ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "live_counting", "type": "number"},
    {"name": "peak", "type": "number"}]}}
CONFIG = tempfile.mkdtemp()
SYSTEMS = tempfile.mkdtemp()
SLUG = "smoke"


def node(nid, ntype, data, x=0):
    return {"id": nid, "type": ntype, "position": {"x": x, "y": 0}, "data": data}


def graph_nodes():
    return [
        node("s", "source.detection", {}),
        node("fr", "analytics.frame", {"fnId": "in_frame", "name": "In frame",
                                       "mode": "realtime"}, 1),
        node("ds", "output.dataset", {"dataset": "counting", "name": "Counting",
                                      "mode": "change", "sources": {}}, 2),
        # Two inputs on one chart: the screenshot's "All 2 series".
        node("g", "dashboard.graph", {"name": "Counting trend", "kind": "spline"}, 3),
        # One live rule as a line, with thresholds: the bands half.
        node("h", "dashboard.graph", {"name": "In frame trend", "kind": "line",
                                      "range": "24h"}, 3),
        # A verdict card: its tones are what it SAYS, edited on both sides.
        node("v", "dashboard.card", {"name": "Bay", "kind": "verdict",
                                     "ok_color": "#00aa00",
                                     "alert_color": "#aa0000"}, 3),
    ]


EDGES = [
    {"id": "e1", "source": "s", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "ds", "targetHandle": "c_live_counting"},
    {"id": "e3", "source": "ds", "sourceHandle": "o_live_counting",
     "target": "g", "targetHandle": "s1"},
    {"id": "e4", "source": "ds", "sourceHandle": "o_peak",
     "target": "g", "targetHandle": "s2"},
    {"id": "e5", "source": "fr", "target": "h"},
    {"id": "e6", "source": "fr", "target": "v"},
]


def store_of(nodes, edges=EDGES):
    return {"workflows": [{"id": "w1", "name": "Main", "enabled": True,
                           "graph": {"nodes": nodes, "edges": edges}}]}


def compiled_of(store):
    c = wf.compile_store(store, ZONES, tables=TABLES)
    assert not c["errors"], c["errors"]
    c.pop("tables", None)
    return c


def recompile(store):
    real = (wf.functions_service, wf.dc)
    wf.functions_service, wf.dc = FakeFns(), FakeDC()
    try:
        wf.apply_artifacts(SYSTEMS, CONFIG, SLUG, compiled_of(store))
    finally:
        wf.functions_service, wf.dc = real


def rows():
    layout = builder_service.load(CONFIG, SLUG)
    return {w["id"]: w for p in layout["pages"] for w in p["widgets"]}


def canvas_save(store, sent_graph):
    """What PUT /api/workflow does with the graph a browser sends."""
    w = store["workflows"][0]
    graph = wf.clean_graph(copy.deepcopy(sent_graph))
    wf.keep_card_look(graph, w["graph"])
    wf.keep_chart_colours(graph, w["graph"])
    wf.keep_builder_edits(graph, w["graph"])
    w["graph"] = graph
    recompile(store)


def data_of(store, nid):
    return next(n for n in store["workflows"][0]["graph"]["nodes"]
                if n["id"] == nid)["data"]


def series_colours(row):
    return {s["id"]: s.get("color") for s in row.get("series") or []}


# -- placed by the operator ---------------------------------------------------
store = store_of(graph_nodes())
first = {w["id"]: w for p in compiled_of(store)["pages"] for w in p["widgets"]}
builder_service.save(CONFIG, SLUG, {"pages": [
    {"id": "overview", "name": "Overview", "widgets": [
        {**first["wf_g"], "grid": {"x": 0, "y": 0, "w": 6, "h": 3}},
        {**first["wf_h"], "grid": {"x": 0, "y": 3, "w": 6, "h": 3}},
        {**first["wf_v"], "grid": {"x": 6, "y": 0, "w": 3, "h": 2}}]}]})
recompile(store)
check("the two-input chart compiles with one series per wire",
      [s["id"] for s in rows()["wf_g"].get("series") or []] == ["s1", "s2"],
      repr(rows()["wf_g"].get("series")))

# -- 1. the Builder sets the colours; they survive a recompile ------------------
n = wf.apply_card_style(store, [
    {"id": "wf_g", "series": {"s1": {"color": "#ff8800"},
                              "s2": {"color": "#0088ff", "label": "Peak"}}},
    {"id": "wf_h", "bands": {"at": [10, 50],
                             "colors": ["#ffff00", "#ff0000", "#550000"]}}])
check("the Builder's chart colours land on the nodes", n >= 2, f"changed {n}")
recompile(store)
check("(e) series colours survive the recompile into the dashboard row",
      series_colours(rows()["wf_g"]) == {"s1": "#ff8800", "s2": "#0088ff"},
      repr(rows()["wf_g"].get("series")))
check("(e) band colours survive the recompile",
      rows()["wf_h"].get("bands") == {"at": [10.0, 50.0],
                                      "colors": ["#ffff00", "#ff0000", "#550000"]},
      repr(rows()["wf_h"].get("bands")))
check("the colour fields are ones the Builders may write",
      "series" in wf.CARD_STYLE_FIELDS and "bands" in wf.CARD_STYLE_FIELDS
      and "color" in wf.CARD_STYLE_FIELDS and "bg" in wf.CARD_STYLE_FIELDS)

# -- 2. a canvas save that sends no colours -----------------------------------
# What the node panel now sends: the series' names and the limits, with every
# colour stripped (the strictest case -- the browser normally echoes them).
sent = copy.deepcopy(store["workflows"][0]["graph"])
gd = data_of({"workflows": [{"graph": sent}]}, "g")
gd["series"] = {"s1": {"label": "Live"}}               # name s1, s2 not sent
hd = data_of({"workflows": [{"graph": sent}]}, "h")
hd["bands"] = {"at": [10, 50]}                         # limits, no colours
canvas_save(store, sent)
check("(d) a canvas save with no colours keeps the stored series colours",
      data_of(store, "g")["series"]["s1"].get("color") == "#ff8800"
      and data_of(store, "g")["series"]["s2"].get("color") == "#0088ff",
      repr(data_of(store, "g").get("series")))
check("...and the canvas's own edit in the same field lands",
      data_of(store, "g")["series"]["s1"].get("label") == "Live",
      repr(data_of(store, "g").get("series")))
check("...and the band colours", data_of(store, "h")["bands"].get("colors")
      == ["#ffff00", "#ff0000", "#550000"], repr(data_of(store, "h").get("bands")))
check("...and the dashboard row still draws them",
      series_colours(rows()["wf_g"]) == {"s1": "#ff8800", "s2": "#0088ff"}
      and rows()["wf_h"]["bands"]["colors"] == ["#ffff00", "#ff0000", "#550000"],
      repr((rows()["wf_g"].get("series"), rows()["wf_h"].get("bands"))))

# A STALE tab: loaded before the Builder's colours, it echoes the old ones.
stale = store_of(graph_nodes())["workflows"][0]["graph"]
data_of({"workflows": [{"graph": stale}]}, "g")["series"] = {
    "s1": {"color": "#111111"}, "s2": {"color": ""}}
data_of({"workflows": [{"graph": stale}]}, "h")["bands"] = {
    "at": [10, 50], "colors": ["#16a34a", "#f59e0b", "#dc2626"]}
canvas_save(store, stale)
check("a stale canvas tab cannot put old series colours back",
      series_colours(rows()["wf_g"]) == {"s1": "#ff8800", "s2": "#0088ff"},
      repr(rows()["wf_g"].get("series")))
check("...nor old band colours",
      rows()["wf_h"]["bands"]["colors"] == ["#ffff00", "#ff0000", "#550000"],
      repr(rows()["wf_h"].get("bands")))

# A threshold VALUE changed on the canvas (data, still the node's): the value
# lands, the colours ride along with the bands the canvas's editor moved.
moved = copy.deepcopy(store["workflows"][0]["graph"])
data_of({"workflows": [{"graph": moved}]}, "h")["bands"] = {
    "at": [20, 50], "colors": ["#ffff00", "#ff0000", "#550000"]}
canvas_save(store, moved)
check("a threshold value edited on the canvas lands, colours intact",
      rows()["wf_h"]["bands"] == {"at": [20.0, 50.0],
                                  "colors": ["#ffff00", "#ff0000", "#550000"]},
      repr(rows()["wf_h"].get("bands")))
# ...and a threshold ADDED on the canvas without colours borrows the stored
# ones by position rather than resetting the chart to the default ramp.
added = copy.deepcopy(store["workflows"][0]["graph"])
data_of({"workflows": [{"graph": added}]}, "h")["bands"] = {"at": [20, 50, 90]}
canvas_save(store, added)
b = rows()["wf_h"]["bands"]
check("a threshold added with no colours keeps the stored ones by position",
      b["at"] == [20.0, 50.0, 90.0] and b["colors"][:3]
      == ["#ffff00", "#ff0000", "#550000"] and len(b["colors"]) == 4, repr(b))
# The canvas can no longer PIN a series colour either.
pin = copy.deepcopy(store["workflows"][0]["graph"])
data_of({"workflows": [{"graph": pin}]}, "g")["series"]["s1"]["color"] = "#abcdef"
canvas_save(store, pin)
check("a colour the canvas sends for a chart is not stored",
      data_of(store, "g")["series"]["s1"]["color"] == "#ff8800")
# The Builder still can, on top of what the canvas wrote.
wf.apply_card_style(store, [{"id": "wf_g", "series": {"s1": {"color": "#abcdef"}}}])
recompile(store)
check("(e) a second Builder colour edit survives the recompile, the other kept",
      series_colours(rows()["wf_g"]) == {"s1": "#abcdef", "s2": "#0088ff"},
      repr(data_of(store, "g").get("series")))
canvas_save(store, copy.deepcopy(store["workflows"][0]["graph"]))
check("...and the canvas save after it keeps it",
      series_colours(rows()["wf_g"])["s1"] == "#abcdef")

# -- 3. a card's colours are not the rule's business ----------------------------
card = copy.deepcopy(store["workflows"][0]["graph"])
data_of({"workflows": [{"graph": card}]}, "v")["ok_color"] = "#123456"
canvas_save(store, card)
check("a verdict card's colour edited on the canvas still lands",
      data_of(store, "v").get("ok_color") == "#123456",
      repr(data_of(store, "v")))

# -- 4. new nodes and old stores ----------------------------------------------
pasted = node("g2", "dashboard.graph", {
    "name": "Copy", "kind": "bar", "series": {"s1": {"color": "#00ff00"}}}, 4)
canvas_save(store, {"nodes": store["workflows"][0]["graph"]["nodes"] + [pasted],
                    "edges": EDGES + [{"id": "e9", "source": "ds",
                                       "sourceHandle": "o_peak",
                                       "target": "g2", "targetHandle": "s1"}]})
check("a pasted chart keeps the colours it was copied with",
      data_of(store, "g2")["series"]["s1"].get("color") == "#00ff00")
legacy = store_of(graph_nodes())
data_of(legacy, "g")["series"] = {"s1": {"color": "#ff8800", "label": "Old"}}
sent = copy.deepcopy(legacy["workflows"][0]["graph"])
wf.keep_chart_colours(sent, legacy["workflows"][0]["graph"])
check("a node saved before this change keeps its colours on an echo save",
      data_of({"workflows": [{"graph": sent}]}, "g")["series"]
      == {"s1": {"color": "#ff8800", "label": "Old"}})
junk = store_of(graph_nodes())
data_of(junk, "g")["series"] = "nonsense"
data_of(junk, "h")["bands"] = ["bad"]
wf.keep_chart_colours(copy.deepcopy(junk["workflows"][0]["graph"]),
                      junk["workflows"][0]["graph"])
check("junk stored series and bands are survived", True)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
