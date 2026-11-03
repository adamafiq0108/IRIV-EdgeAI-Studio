# EVERY shape in the "Show as" list, against the four chart/card fixes.
#
# Owner (2026-10-12): "you updated the graph but not all graph; that is why
# when I use area chart I get the problem." So this walks the whole list the
# dropdown offers and pins, per shape, through the real compile and the real
# layout store:
#
#   A. a4cfdd31  a card's question set in a Builder survives the workflow
#                save, and a canvas tab opened before it cannot undo it
#   B. 9ff90457  a chart INPUT's "Work out" set in a Builder does the same
#   C. bec13514  several table inputs line up on the chart's own period
#   D. 983e49f0  a chart's colours are the Builders' (a canvas save keeps
#                the stored ones), a card's colours are left alone
#
# Shapes no workflow node draws (timeline, live video, and the heatmap, which
# comes from the Heatmap analytics node's palette entry, not a dashboard node)
# are hand-made Builder rows: nothing recompiles them, so what is pinned is
# that a workflow save leaves them exactly as the operator saved them.
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


#: The "Show as" list as the owner's screenshot shows it, by stored value.
DROPDOWN = ["tile", "line", "bar", "area", "spline", "donut", "pie", "radar",
            "gauge", "table", "timeline", "video", "status", "verdict", "heatmap"]
CHARTS = list(wf.CHART_KINDS)
CARDS = ["tile", "gauge", "verdict", "status"]
check("every shape in the dropdown is a stored kind the server knows",
      all(k in builder_service.KINDS for k in DROPDOWN))

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"pcb_sensata": {"key": "pcb_sensata", "name": "pcb_sensata", "columns": [
    {"name": "status", "type": "text"}, {"name": "zone", "type": "text"},
    {"name": "total_panel", "type": "number"}]}}
SLUG = "smoke"


def node(nid, ntype, data, x=0):
    return {"id": nid, "type": ntype, "position": {"x": x, "y": 0}, "data": data}


BASE = [node("s", "source.detection", {}),
        node("fr", "analytics.frame", {"fnId": "in_frame", "name": "In frame",
                                       "mode": "realtime"}, 1),
        node("ds1", "output.dataset", {"dataset": "pcb_sensata", "name": "CAM1",
                                       "mode": "change", "sources": {}}, 2),
        node("ds2", "output.dataset", {"dataset": "pcb_sensata", "name": "CAM2",
                                       "mode": "change", "sources": {}}, 2)]
BASE_EDGES = [{"id": "e1", "source": "s", "target": "fr"}] + [
    {"id": f"{t}_{c}", "source": "fr", "target": t, "targetHandle": f"c_{c}"}
    for t in ("ds1", "ds2") for c in ("status", "zone", "total_panel")]


def store_of(card, edges):
    return {"workflows": [{"id": "w1", "name": "Main", "enabled": True,
                           "graph": {"nodes": copy.deepcopy(BASE) + [card],
                                     "edges": BASE_EDGES + edges}}]}


def compiled_of(store):
    c = wf.compile_store(store, ZONES, tables=TABLES)
    assert not c["errors"], c["errors"]
    c.pop("tables", None)
    return c


class Box:
    """One system: a config dir with a layout, and the recompile route."""

    def __init__(self, store):
        self.store, self.config, self.systems = store, tempfile.mkdtemp(), tempfile.mkdtemp()

    def recompile(self):
        real = (wf.functions_service, wf.dc)
        wf.functions_service, wf.dc = FakeFns(), FakeDC()
        try:
            wf.apply_artifacts(self.systems, self.config, SLUG, compiled_of(self.store))
        finally:
            wf.functions_service, wf.dc = real

    def place(self, extra=()):
        first = [w for p in compiled_of(self.store)["pages"] for w in p["widgets"]]
        builder_service.save(self.config, SLUG, {"pages": [
            {"id": "overview", "name": "Overview",
             "widgets": [{**w, "grid": {"x": 0, "y": 0, "w": 6, "h": 3}} for w in first]
             + list(extra)}]})
        self.recompile()

    def row(self, wid="wf_c"):
        layout = builder_service.load(self.config, SLUG)
        return next(w for p in layout["pages"] for w in p["widgets"] if w["id"] == wid)

    def canvas_save(self, sent):
        w = self.store["workflows"][0]
        graph = wf.clean_graph(copy.deepcopy(sent))
        wf.keep_card_look(graph, w["graph"])
        wf.keep_chart_colours(graph, w["graph"])
        wf.keep_builder_edits(graph, w["graph"])
        w["graph"] = graph
        self.recompile()

    def data(self):
        return next(n for n in self.store["workflows"][0]["graph"]["nodes"]
                    if n["id"] == "c")["data"]


COLUMN = [{"id": "w1", "source": "ds1", "sourceHandle": "o_total_panel",
           "target": "c", "targetHandle": "in"}]
TWO = COLUMN + [{"id": "w2", "source": "ds2", "sourceHandle": "o_total_panel",
                 "target": "c", "targetHandle": "s2"}]

# -- the CHART shapes: A, B, C, D each ---------------------------------------
for kind in CHARTS:
    # A: one table column, "Work out" from the Builder.
    box = Box(store_of(node("c", "dashboard.graph", {"name": "C", "kind": kind}), COLUMN))
    box.place()
    stale = copy.deepcopy(box.store["workflows"][0]["graph"])
    wf.apply_card_style(box.store, [{"id": "wf_c", "agg": "sum"}])
    box.canvas_save(stale)
    check(f"{kind}: A  card 'Work out' survives a save and a stale tab",
          box.row()["query"]["agg"] == "sum", repr(box.row().get("query")))
    # B: two inputs, the input's own "Work out".
    box = Box(store_of(node("c", "dashboard.graph", {"name": "C", "kind": kind}), TWO))
    box.place()
    stale = copy.deepcopy(box.store["workflows"][0]["graph"])
    wf.apply_card_style(box.store, [{"id": "wf_c", "series": {"s1": {"agg": "count"}}}])
    box.canvas_save(stale)
    s1 = next(s for s in box.row()["series"] if s["id"] == "s1")
    check(f"{kind}: B  input 'Work out' survives a save and a stale tab",
          (s1.get("query") or {}).get("agg") == "count", repr(s1))
    # C: the chart's own period lines both inputs up.
    box = Box(store_of(node("c", "dashboard.graph",
                            {"name": "C", "kind": kind, "bucket": "day"}), TWO))
    box.place()
    check(f"{kind}: C  both inputs on the chart's period",
          all((s.get("query") or {}).get("bucket") == "day"
              for s in box.row()["series"]), repr(box.row()["series"]))
    # D: a colour picked in the Builder survives a canvas save that has none.
    box = Box(store_of(node("c", "dashboard.graph", {"name": "C", "kind": kind}), TWO))
    box.place()
    stale = copy.deepcopy(box.store["workflows"][0]["graph"])
    wf.apply_card_style(box.store, [{"id": "wf_c",
                                     "series": {"s1": {"color": "#22c55e"}}}])
    box.canvas_save(stale)
    s1 = next(s for s in box.row()["series"] if s["id"] == "s1")
    check(f"{kind}: D  the Builder's colour survives a canvas save",
          s1.get("color") == "#22c55e", repr(s1))

# -- the SINGLE-VALUE shapes ----------------------------------------------------
EDITS = {"tile": {"agg": "max"}, "gauge": {"agg": "avg"},
         "verdict": {"ok_label": "Free"}, "status": {"op": "<", "value": 3}}
for kind in CARDS:
    box = Box(store_of(node("c", "dashboard.card", {"name": "C", "kind": kind}), COLUMN))
    box.place()
    stale = copy.deepcopy(box.store["workflows"][0]["graph"])
    n = wf.apply_card_style(box.store, [{"id": "wf_c", **EDITS[kind]}])
    box.canvas_save(stale)
    r = box.row()
    if kind == "verdict":
        ok = (r.get("status") or {}).get("ok_label") == "Free" \
            or any(st.get("label") == "Free" for st in r.get("states") or [])
    elif kind == "status":
        ok = (r.get("status") or {}).get("op") == "<" \
            and (r.get("status") or {}).get("value") == 3
    else:
        ok = r["query"]["agg"] == EDITS[kind]["agg"]
    check(f"{kind}: A  its Builder setting survives a save and a stale tab",
          n >= 1 and ok, repr({k: r.get(k) for k in ("query", "status", "states")}))
    # D does not apply: a card's colours are what it says, both panels edit
    # them, and keep_chart_colours leaves a card alone.
    sent = copy.deepcopy(box.store["workflows"][0]["graph"])
    next(x for x in sent["nodes"] if x["id"] == "c")["data"]["color"] = ""
    g = wf.clean_graph(sent)
    wf.keep_chart_colours(g, box.store["workflows"][0]["graph"])
    check(f"{kind}: D  is not a chart, so its colours are not held back",
          next(x for x in g["nodes"] if x["id"] == "c")["data"].get("color") == "")

# The rows themselves: a Card on a data table, its row count from the Builder.
box = Box(store_of(node("c", "dashboard.card",
                        {"name": "C", "kind": "table", "source_mode": "dataset",
                         "dataset": "pcb_sensata"}), []))
box.place()
stale = copy.deepcopy(box.store["workflows"][0]["graph"])
wf.apply_card_style(box.store, [{"id": "wf_c", "limit": 30}])
box.canvas_save(stale)
check("table: A  its row count survives a save and a stale tab",
      box.row().get("limit") == 30, repr(box.row().get("limit")))

# -- the shapes no node draws -----------------------------------------------------
HAND = [{"id": "tl1", "title": "Recent", "kind": "timeline", "source": "events",
         "key": "", "span": 2, "grid": {"x": 0, "y": 3, "w": 6, "h": 3}},
        {"id": "vid1", "title": "Feed", "kind": "video", "source": "stat", "key": "",
         "camera": "cam2", "span": 2, "grid": {"x": 6, "y": 3, "w": 6, "h": 3}},
        {"id": "heat1", "title": "Where", "kind": "heatmap", "source": "stat",
         "key": "fn_heat_c", "span": 2, "grid": {"x": 0, "y": 6, "w": 6, "h": 3}}]
box = Box(store_of(node("c", "dashboard.card", {"name": "C", "kind": "tile"}), COLUMN))
box.place(builder_service.validate(HAND))
before = {w["id"]: box.row(w["id"]) for w in HAND}
box.canvas_save(copy.deepcopy(box.store["workflows"][0]["graph"]))
for w in HAND:
    check(f"{w['kind']}: hand-made, untouched by a workflow save",
          box.row(w["id"]) == before[w["id"]], repr(box.row(w["id"])))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
