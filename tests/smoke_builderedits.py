# A card's QUESTION picked in a Builder survives the next workflow save.
#
# Owner report (2026-10-12): "Work out" set to "total rows" on a chart in the
# Analytics builder was back to "average value" after the workflow was saved.
#
# Why it happened. A compiled card (wf_<node>) is rebuilt from its NODE on
# every compile. The Builders write a card's LOOK back onto the node
# (/api/workflow/card-style), but its question -- "Work out", "One bar per",
# the period, the shape -- travelled only on the widget row, and:
#   * the layout save is partial: builder.merge_partial keeps the STORED wf_*
#     row and drops the edited one the browser sent, and
#   * card-style carried only the look (CARD_STYLE_FIELDS had no `agg`), so
#   * the next recompile (apply_artifacts) rebuilt the row from a node that
#     had never heard of the edit -- and a LINKED COPY on the Dashboard, whose
#     row did keep the edit, was refreshed from that node too.
#
# The fix writes the question onto the node like the look (one place, both
# editors read it), and stamps the write so a canvas tab opened before it
# cannot put the old answer back (keep_builder_edits).
#
# Pinned here, against the real compiler and the real layout store:
#   1. the old payload loses the edit (the bug, reproduced)
#   2. the question fields reach the node and survive a recompile -- graph
#      and card, several fields, a linked copy included
#   3. a stale canvas tab does not undo it; a canvas edit made after it wins
#   4. a node removed and re-added, and a brand-new node, start clean
#   5. a graph saved before the stamp existed, and junk stamps, do not crash
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

    # compile_graph reads these through the real module; only the writes are
    # faked, so nothing lands in a system directory.
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
    {"name": "shift", "type": "text"}]}}
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
        # The owner's chart: a table column as a bar chart, "Work out" left
        # at the default.
        node("g", "dashboard.graph", {"name": "Counting per day", "kind": "bar",
                                      "agg": "avg"}, 3),
        node("c", "dashboard.card", {"name": "Total", "kind": "tile"}, 3),
        # A chart of a live rule's samples: the Time range and Interval half.
        node("h", "dashboard.graph", {"name": "In frame trend", "kind": "line",
                                      "range": "24h"}, 3),
    ]


EDGES = [
    {"id": "e1", "source": "s", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "ds", "targetHandle": "c_live_counting"},
    {"id": "e3", "source": "ds", "sourceHandle": "o_live_counting", "target": "g"},
    {"id": "e4", "source": "ds", "sourceHandle": "o_live_counting", "target": "c"},
    {"id": "e5", "source": "fr", "target": "h"},
]


def store_of(nodes, edges=EDGES):
    return {"workflows": [{"id": "w1", "name": "Main", "enabled": True,
                           "graph": {"nodes": nodes, "edges": edges}}]}


def compiled_of(store):
    c = wf.compile_store(store, ZONES, tables=TABLES)
    assert not c["errors"], c["errors"]
    # The table registry is a database write; not what this test is about.
    c.pop("tables", None)
    return c


def recompile(store):
    """What /api/workflow's _recompile does after a save or a card-style."""
    real = (wf.functions_service, wf.dc)
    wf.functions_service, wf.dc = FakeFns(), FakeDC()
    try:
        wf.apply_artifacts(SYSTEMS, CONFIG, SLUG, compiled_of(store))
    finally:
        wf.functions_service, wf.dc = real


def rows():
    layout = builder_service.load(CONFIG, SLUG)
    return {p["id"]: {w["id"]: w for w in p["widgets"]} for p in layout["pages"]}


def canvas_save(store, sent_graph):
    """What PUT /api/workflow does with the graph a browser sends."""
    w = store["workflows"][0]
    graph = wf.clean_graph(copy.deepcopy(sent_graph))
    wf.keep_card_look(graph, w["graph"])
    wf.keep_builder_edits(graph, w["graph"])
    w["graph"] = graph
    recompile(store)


def data_of(store, nid):
    return next(n for n in store["workflows"][0]["graph"]["nodes"]
                if n["id"] == nid)["data"]


# -- set up: the cards are compiled and placed by the operator ----------------
store = store_of(graph_nodes())
first = {w["id"]: w for p in compiled_of(store)["pages"] for w in p["widgets"]}
check("the chart compiles to a dataset bar chart working out the average",
      first["wf_g"]["source"] == "dataset" and first["wf_g"]["query"]["agg"] == "avg",
      repr(first["wf_g"]))
# Placed on the Overview by the operator, plus a LINKED COPY of the chart on
# the Dashboard (dragged from "From workflow": its own id, `node` the link).
linked = {**copy.deepcopy(first["wf_g"]), "id": "bar_copy", "node": "g",
          "grid": {"x": 0, "y": 0, "w": 4, "h": 3}}
builder_service.save(CONFIG, SLUG, {"pages": [
    {"id": "main", "name": "Dashboard", "widgets": [linked]},
    {"id": "overview", "name": "Overview", "widgets": [
        {**first["wf_g"], "grid": {"x": 0, "y": 0, "w": 6, "h": 3}},
        {**first["wf_c"], "grid": {"x": 6, "y": 0, "w": 3, "h": 2}},
        {**first["wf_h"], "grid": {"x": 0, "y": 3, "w": 6, "h": 3}}]}]})
recompile(store)
check("the placed cards refresh in place", rows()["overview"]["wf_g"]["grid"]["w"] == 6,
      repr(rows()["overview"]["wf_g"]))

# -- 1. the bug, reproduced with the payload the Builders used to send --------
edited = copy.deepcopy(rows()["overview"]["wf_g"])
edited["query"]["agg"] = "count"                # "Work out: total rows"
stored = builder_service.load(CONFIG, SLUG)
merged = builder_service.merge_partial(stored, {"partial": True, "pages": [
    {"id": "overview", "widgets": [edited]}]})
check("(bug) a partial layout save drops the edited wf_* row",
      next(w for p in merged["pages"] if p["id"] == "overview"
           for w in p["widgets"] if w["id"] == "wf_g")["query"]["agg"] == "avg")
old_payload = {"id": "wf_g", "name": "Counting per day", "template": "plain",
               "bands": {}, "series": {}}
wf.apply_card_style(store, [old_payload])
recompile(store)
check("(bug) and the look-only card-style leaves the node at 'average value'",
      rows()["overview"]["wf_g"]["query"]["agg"] == "avg")

# -- 2. the question goes to the node and survives ---------------------------
n = wf.apply_card_style(store, [
    {"id": "wf_g", "agg": "count", "bucket": "week", "group_by": "",
     "range": "month", "kind": "bar"},
    {"id": "wf_c", "agg": "max", "range": "today", "unit": "pcs"},
    {"id": "wf_h", "range": "7d", "interval": "1h"}])
check("the Builder's question lands on the nodes", n == 8, f"changed {n}")
recompile(store)
r = rows()
g = r["overview"]["wf_g"]
check("'Work out: total rows' survives the recompile", g["query"]["agg"] == "count",
      repr(g["query"]))
check("...and 'One bar per: each week' and the period",
      g["query"].get("bucket") == "week" and g["query"].get("range") == "month",
      repr(g["query"]))
check("...placement still the operator's", g["grid"] == {"x": 0, "y": 0, "w": 6, "h": 3})
cw = r["overview"]["wf_c"]
check("the card keeps its own 'Work out', period and unit",
      cw["query"]["agg"] == "max" and cw["query"].get("range") == "today"
      and cw.get("unit") == "pcs", repr(cw))
hw = r["overview"]["wf_h"]
check("a history chart keeps its time range and interval",
      hw.get("range") == "7d" and hw.get("interval") == "1h", repr(hw))
copy_row = r["main"]["bar_copy"]
check("the linked copy on the Dashboard follows the node, placement kept",
      copy_row["query"]["agg"] == "count" and copy_row["grid"]["w"] == 4,
      repr(copy_row))
check("unsent fields are untouched (no shape change on the card)",
      data_of(store, "c").get("kind") == "tile")

# -- 3. a canvas tab opened BEFORE the Builder's edit ---------------------------
stale = store_of(graph_nodes())["workflows"][0]["graph"]   # agg avg, no stamp
canvas_save(store, stale)
check("a stale canvas tab does not put 'average value' back",
      rows()["overview"]["wf_g"]["query"]["agg"] == "count"
      and data_of(store, "g")["agg"] == "count", repr(data_of(store, "g")))
check("...nor the old period and unit on the card",
      data_of(store, "c").get("range") == "today"
      and data_of(store, "c").get("unit") == "pcs", repr(data_of(store, "c")))
# A stale tab that DID change the field on the canvas: the canvas wins.
stale2 = copy.deepcopy(stale)
next(x for x in stale2["nodes"] if x["id"] == "g")["data"]["agg"] = "sum"
canvas_save(store, stale2)
check("a canvas edit made in that tab still wins",
      rows()["overview"]["wf_g"]["query"]["agg"] == "sum")
# A tab that loaded AFTER the edit sees the stamp and is the latest word,
# even when it sets the value back to one it held before.
fresh = copy.deepcopy(store["workflows"][0]["graph"])
next(x for x in fresh["nodes"] if x["id"] == "g")["data"]["agg"] = "avg"
canvas_save(store, fresh)
check("a tab opened after the Builder's edit may set it back on purpose",
      rows()["overview"]["wf_g"]["query"]["agg"] == "avg")
# Idempotent: saving the same graph twice changes nothing.
before = copy.deepcopy(rows())
canvas_save(store, copy.deepcopy(store["workflows"][0]["graph"]))
check("saving again changes nothing", rows() == before)

# -- 4. removed and re-added; a brand-new node ----------------------------------
gone = [x for x in copy.deepcopy(store["workflows"][0]["graph"]["nodes"])
        if x["id"] != "c"]
canvas_save(store, {"nodes": gone,
                    "edges": [e for e in EDGES if e["target"] != "c"]})
check("a removed node's card is gone", "wf_c" not in rows()["overview"])
canvas_save(store, {"nodes": gone + [node("c", "dashboard.card",
                                          {"name": "Total", "kind": "tile"}, 3)],
                    "edges": EDGES})
c2 = compiled_of(store)
fresh_c = next(w for p in c2["pages"] for w in p["widgets"] if w["id"] == "wf_c")
check("re-added under the same id it starts from its own defaults",
      fresh_c["query"]["agg"] == "latest" and not fresh_c.get("unit")
      and wf.BUILDER_EDIT_KEY not in data_of(store, "c"), repr(fresh_c))
newbie = node("c9", "dashboard.card", {"name": "New", "kind": "tile"}, 4)
canvas_save(store, {"nodes": store["workflows"][0]["graph"]["nodes"] + [newbie],
                    "edges": EDGES + [{"id": "e9", "source": "ds",
                                       "sourceHandle": "o_live_counting",
                                       "target": "c9"}]})
check("a brand-new node carries no stamp and no override",
      wf.BUILDER_EDIT_KEY not in data_of(store, "c9"))

# -- 5. old stores and junk --------------------------------------------------
legacy = store_of(graph_nodes())
wf.keep_builder_edits(copy.deepcopy(legacy["workflows"][0]["graph"]),
                      legacy["workflows"][0]["graph"])
check("a graph saved before stamps existed passes through untouched", True)
junk = store_of(graph_nodes())
data_of(junk, "g")[wf.BUILDER_EDIT_KEY] = {"rev": "x", "was": {"agg": ["bad", 3]}}
sent = copy.deepcopy(junk["workflows"][0]["graph"])
data_of({"workflows": [{"graph": sent}]}, "g").pop(wf.BUILDER_EDIT_KEY)
wf.keep_builder_edits(sent, junk["workflows"][0]["graph"])
check("a junk stamp is survived", True)
check("junk values are refused, not stored",
      wf.apply_card_style(junk, [{"id": "wf_g", "agg": "median", "kind": "gauge",
                                  "bucket": "fortnight", "limit": "lots"}]) == 0)
check("a one-shape node cannot be reshaped from a Builder",
      wf._clean_question({"type": "dashboard.counter"}, "kind", "bar") is None)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
