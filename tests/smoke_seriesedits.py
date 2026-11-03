# A chart INPUT's "Work out" picked in a Builder survives the workflow save.
#
# Owner screenshots (2026-10-12, Analytics Builder, graph Inputs, row "CAM 1"):
# before -- Where from "A column of a data table", Data table pcb_sensata,
# Work out "total rows", One point per "each status value", Period "follow
# the Analytics range"; after saving the workflow the SAME row read Work out
# "average value" with "Of which column = total_panel" back.
#
# Why. The input is a wire from a Data Table's total_panel column, so the
# compiler's own answer is "average of total_panel" (series_entry: a number
# column is averaged). The Builder's row edits the STORE question
# (query.agg), but seriesStyle sent the row's FOLD (`agg`, which the row
# blanks when Work out changes) -- so the node was told "" (= the compiler's
# guess) and the next recompile put "average value" back. And `series` had no
# stale-tab protection: a canvas tab opened before the edit re-sent the old
# row on its next save.
#
# Pinned here, through the real compile and the real layout store:
#   1. the payload the Builder sent before this fix loses the edit (the bug)
#   2. the payload it sends now (agg "count" on the node's series entry)
#      survives a recompile, and the stored row reads "total rows"
#   3. a canvas tab opened BEFORE the edit cannot put "average value" back,
#      while a canvas edit of that same row still wins
#   4. Work out back to "average value" -> the column (total_panel) is the
#      wire's and is there again
#   5. a series entry with no explicit agg (derived from the wire) is
#      untouched by an unrelated edit and by the stale-tab rule
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
TABLES = {"pcb_sensata": {"key": "pcb_sensata", "name": "pcb_sensata", "columns": [
    {"name": "status", "type": "text"},
    {"name": "zone", "type": "text"},
    {"name": "total_panel", "type": "number"}]}}
CONFIG = tempfile.mkdtemp()
SYSTEMS = tempfile.mkdtemp()
SLUG = "smoke"


def node(nid, ntype, data, x=0):
    return {"id": nid, "type": ntype, "position": {"x": x, "y": 0}, "data": data}


def table_node(nid, name):
    return node(nid, "output.dataset", {"dataset": "pcb_sensata", "name": name,
                                        "mode": "change", "sources": {}}, 2)


def graph_nodes(series):
    return [
        node("s", "source.detection", {}),
        node("fr", "analytics.frame", {"fnId": "in_frame", "name": "In frame",
                                       "mode": "realtime"}, 1),
        table_node("ds1", "CAM 1 table"), table_node("ds2", "CAM 2 table"),
        node("g", "dashboard.graph", {"name": "Panels", "kind": "line",
                                      "series": series}, 3),
    ]


EDGES = [{"id": "e1", "source": "s", "target": "fr"}]
for t in ("ds1", "ds2"):
    for col in ("status", "zone", "total_panel"):
        EDGES.append({"id": f"{t}_{col}", "source": "fr", "target": t,
                      "targetHandle": f"c_{col}"})
EDGES += [
    {"id": "w1", "source": "ds1", "sourceHandle": "o_total_panel",
     "target": "g", "targetHandle": "in"},
    {"id": "w2", "source": "ds2", "sourceHandle": "o_total_panel",
     "target": "g", "targetHandle": "s2"}]

# The row as the screenshot shows it, before Work out was touched: named CAM 1,
# one point per status value, following the Analytics range -- and NO agg of
# its own, so the compiler's guess (average of the number column) stands.
SERIES = {"s1": {"label": "CAM 1", "query": {"group_by": "status", "range": "page"}},
          "s2": {"label": "CAM 2"}}


def store_of(series=None):
    return {"workflows": [{"id": "w1", "name": "Main", "enabled": True,
                           "graph": {"nodes": graph_nodes(copy.deepcopy(series or SERIES)),
                                     "edges": EDGES}}]}


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


def row():
    layout = builder_service.load(CONFIG, SLUG)
    return next(w for p in layout["pages"] if p["id"] == "overview"
                for w in p["widgets"] if w["id"] == "wf_g")


def s1(w=None):
    w = w or row()
    return next(s for s in w["series"] if s["id"] == "s1")


def canvas_save(store, sent_graph):
    """What PUT /api/workflow does with the graph a browser sends."""
    w = store["workflows"][0]
    graph = wf.clean_graph(copy.deepcopy(sent_graph))
    wf.keep_card_look(graph, w["graph"])
    for hook in ("keep_chart_colours", "keep_builder_edits"):
        if hasattr(wf, hook):
            getattr(wf, hook)(graph, w["graph"])
    w["graph"] = graph
    recompile(store)


def conf(store, sid="s1"):
    g = next(n for n in store["workflows"][0]["graph"]["nodes"] if n["id"] == "g")
    return (g["data"].get("series") or {}).get(sid) or {}


def work_out(entry):
    """What the Builder's row shows under "Work out": the store question."""
    return (entry.get("query") or {}).get("agg") or "count"


# -- set up: compiled, placed by the operator ----------------------------------
store = store_of()
first = next(w for p in compiled_of(store)["pages"] for w in p["widgets"]
             if w["id"] == "wf_g")
builder_service.save(CONFIG, SLUG, {"pages": [
    {"id": "overview", "name": "Overview", "widgets": [
        {**first, "grid": {"x": 0, "y": 0, "w": 6, "h": 3}}]}]})
recompile(store)
loaded = s1()
check("the row starts as 'average value' of total_panel, per status value",
      work_out(loaded) == "avg" and loaded.get("field") == "total_panel"
      and (loaded.get("query") or {}).get("group_by") == "status"
      and loaded.get("label") == "CAM 1", repr(loaded))

# -- 1. the bug: what the Builder sent before this fix --------------------------
# The row's Work out writes query.agg = "count" and blanks the row's fold
# (agg: ""); seriesStyle then sent the FOLD, i.e. "" -- the compiler's guess.
before_fix = {"s1": {"label": "CAM 1", "color": "", "agg": ""},
              "s2": {"label": "CAM 2", "color": ""}}
wf.apply_card_style(store, [{"id": "wf_g", "series": before_fix}])
recompile(store)
check("(bug) the old payload leaves the row at 'average value'",
      work_out(s1()) == "avg", repr(s1()))

# -- 2. what it sends now ------------------------------------------------------
now = {"s1": {"label": "CAM 1", "color": "", "agg": "count"},
       "s2": {"label": "CAM 2", "color": ""}}
n = wf.apply_card_style(store, [{"id": "wf_g", "series": now}])
check("the input's 'total rows' lands on the node's series entry",
      n >= 1 and conf(store).get("agg") == "count", repr(conf(store)))
recompile(store)
check("'Work out: total rows' survives the recompile", work_out(s1()) == "count",
      repr(s1()))
check("...and the rest of the row is as the operator left it",
      (s1().get("query") or {}).get("group_by") == "status"
      and (s1().get("query") or {}).get("range") == "page"
      and s1().get("label") == "CAM 1", repr(s1()))

# -- 3. a canvas tab opened BEFORE the Builder's edit ----------------------------
stale = store_of()["workflows"][0]["graph"]          # s1 with no agg: the guess
canvas_save(store, stale)
check("a stale canvas tab does not put 'average value' back",
      work_out(s1()) == "count" and conf(store).get("agg") == "count",
      f"row {s1()}\n      node {conf(store)}")
# ...and one that re-sends an explicit old agg (a canvas that HAD one).
old_explicit = store_of({**SERIES, "s1": {**SERIES["s1"], "agg": "avg"}})
store2 = old_explicit
first2 = copy.deepcopy(store2)
wf.apply_card_style(store2, [{"id": "wf_g", "series": now}])
canvas_save(store2, first2["workflows"][0]["graph"])
check("...nor one that re-sends an explicit agg avg",
      conf(store2).get("agg") == "count", repr(conf(store2)))
# A canvas edit of that row, in the stale tab, still wins.
edited = copy.deepcopy(stale)
next(x for x in edited["nodes"] if x["id"] == "g")["data"]["series"]["s1"]["agg"] = "max"
canvas_save(store, edited)
check("a canvas edit of the same row still wins", work_out(s1()) == "max",
      repr(s1()))

# -- 4. back to 'average value': the column is the wire's and comes back ---------
wf.apply_card_style(store, [{"id": "wf_g", "series": {"s1": {"agg": "avg"}}}])
recompile(store)
check("Work out back to 'average value' shows 'Of which column = total_panel'",
      work_out(s1()) == "avg" and s1().get("field") == "total_panel", repr(s1()))

# -- 5. an entry with no explicit agg stays derived from the wire -----------------
check("series 2 never had an agg and still has none on the node",
      "agg" not in conf(store, "s2"), repr(conf(store, "s2")))
s2 = next(s for s in row()["series"] if s["id"] == "s2")
check("...so it still compiles to the wire's own answer",
      work_out(s2) == "avg" and s2.get("field") == "total_panel", repr(s2))

# -- 6. the AREA chart the owner's screenshots were of, and every time shape --
# Area is a time shape like line and spline (TIMESERIES_KINDS): no period set,
# a table input is drawn on the sampler grid (chart_series._dataset_time);
# with the chart's Time axis set it takes the bucket path (bec13514). Both are
# fed by the SAME series entry, so the fold the Builder writes is the one the
# render asks the store for.
import json                                                        # noqa: E402
from core.repos import datasets as datasets_repo                  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "seriessync_fixture.json")
with open(FIXTURE, encoding="utf-8") as f:
    fixture = json.load(f)
CALLS = []


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None):
    CALLS.append({"agg": agg, "field": field, "bucket": bucket})
    import datetime
    from core.services import history as history_service
    if bucket:
        day = datetime.date.today()
        key = datetime.datetime(day.year, day.month, day.day).astimezone().isoformat()
    else:
        g = history_service.grid("24h")
        key = datetime.datetime.fromtimestamp(g["start"] + 3 * g["step"]) \
            .astimezone().isoformat()
    return {"buckets": [{"key": key, "value": 7.0}]}


datasets_repo.aggregate = fake_aggregate
KEEP = ("label", "agg", "field", "ts_field", "query")


def input_row(entry):
    q = entry.get("query") or {}
    return {"label": entry.get("label"), "agg": entry.get("agg"),
            "field": entry.get("field"), "ts_field": entry.get("ts_field"),
            "query": {k: q.get(k) for k in ("agg", "group_by", "range") if q.get(k)}}


for kind in ("area", "line", "spline", "bar"):
    for case, entry in fixture["conf"].items():
        series = {"s1": {**copy.deepcopy(entry), "label": "CAM 1",
                         "query": {"range": "page"}},
                  "s2": {"label": "CAM 2"}}
        st = store_of(series)
        gnode = next(x for x in st["workflows"][0]["graph"]["nodes"] if x["id"] == "g")
        gnode["data"]["kind"] = kind
        w = next(x for p in compiled_of(st)["pages"] for x in p["widgets"]
                 if x["id"] == "wf_g")
        stored = builder_service.validate([w])[0]
        got = input_row(next(s for s in stored["series"] if s["id"] == "s1"))
        if kind == "area":
            want = fixture["rows"][case]
            check(f"area, Work out {case}: the stored row is the one both panels "
                  f"are checked against (tests/check_seriessync.mjs)",
                  got == want, f"got {got}\n      want {want}")
        else:
            check(f"{kind}, Work out {case}: same input row as area",
                  got == fixture["rows"][case], repr(got))

# The owner's flow on an AREA chart: Builder sets total rows, then total value;
# a stale canvas save keeps each; the render asks the store that question.
for case, fold in (("count", "count"), ("sum", "sum")):
    st = store_of({"s1": {"label": "CAM 1"}, "s2": {"label": "CAM 2"}})
    gnode = next(x for x in st["workflows"][0]["graph"]["nodes"] if x["id"] == "g")
    gnode["data"]["kind"] = "area"
    stale_area = copy.deepcopy(st["workflows"][0]["graph"])
    wf.apply_card_style(st, [{"id": "wf_g", "series": {"s1": {"agg": fold}}}])
    canvas_save(st, stale_area)
    check(f"area: Builder 'Work out' {case} survives a stale canvas save",
          conf(st).get("agg") == fold, repr(conf(st)))
    w = next(x for p in compiled_of(st)["pages"] for x in p["widgets"]
             if x["id"] == "wf_g")
    CALLS.clear()
    out = builder_service.compute([builder_service.validate([w])[0]], [], {},
                                  slug="smoke")[0]
    sets = (out.get("data") or {}).get("datasets") or []
    check(f"area: the render asks the store for '{case}' of total_panel",
          CALLS and CALLS[0]["agg"] == case, repr(CALLS))
    check(f"area: and draws the answer (7 in a cell, folded by summing)",
          sets and 7 in [v for v in sets[0].get("values") or [] if v is not None],
          repr(sets[:1]))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
