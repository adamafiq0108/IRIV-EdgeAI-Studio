# Pressing Save in a layout builder must not change how any chart draws.
#
# Tok Aba, 2026-10-06, on a box fresh on v1.0.17: the Analytics page had
# "People Detected" (events per hour, today) and "Total Detection (Weekly)"
# (events per day, this week), each under its own name. He opened Edit
# layout, pressed Save, and both became the SAME chart: one 24h line in
# 12-minute buckets, legend and y-axis reading the system's key instead of
# the card's name.
#
# Nothing on disk was lost. The save added a `series` list to each chart
# (builderShared.jsx withSeries, since 1.0.10: "a card saved before series
# existed is one series from its own source, written down on save"), and a
# widget that CARRIES a series list is drawn by the server's series renderer
# (chart_series: one shared time grid at the card's `range`, which an events
# chart never had, so 24h; the series named after its key or id) rather than
# by its own source path (the query's own hour/day buckets over its own
# today/week, under the card's title). The row was lossless as config and not
# as a picture. The materialisation is gone: a save sends the rows exactly as
# they are stored, and a chart gains a series list only when the operator
# edits one.
#
# Pinned here, through the real route (PUT /api/builder as the Analytics
# Builder sends it, partial: true with the stored rows) and the real render
# (analytics.compute_pages, what the Analytics page shows):
#
#   1. four configured charts -- two events charts (per hour today, per day
#      this week, named by their title), two history charts with a custom
#      series name each (a 1h one and a 7d one) -- are stored byte-for-byte
#      the same after the save, and draw the same labels, values and names;
#   2. the old save's rewrite, replayed against the same server, DID change
#      the picture (the reason the rule exists, so a future "harmless"
#      materialisation trips this and not a customer);
#   3. source pins: neither builder maps its rows through a save-side
#      migration any more, and builderShared.jsx no longer has one.
#
#   py -3 tests/smoke_layoutsave.py
import asyncio
import copy
import datetime
import io
import json
import os
import sys
import tempfile
import time
import types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# The dev box runs the suite without FastAPI; the routes only need its names.
if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException = HTTPException
    fa.Request = fa.UploadFile = object
    fa.Query = fa.File = fa.Form = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.FileResponse = \
        resp.StreamingResponse = object
    resp.Response = lambda content=None, media_type=None, **k: content
    sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402

from core.routes import builder as builder_routes             # noqa: E402
from core.services import analytics as analytics_service      # noqa: E402
from core.services import auth                                # noqa: E402
from core.services import builder as builder_service          # noqa: E402
from core.services import history                             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


CFG = tempfile.mkdtemp()
SLUG = "demo"
ADMIN = auth.admin_username(CFG)          # the master account, may edit layouts


def req(body=None):
    async def _json():
        return body if body is not None else {}
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, systems_dir=None,
            system=SimpleNamespace(slug=SLUG))),
        state=SimpleNamespace(user=ADMIN), json=_json)


def call(fn, *a):
    """A route the way FastAPI runs it: its answer, or (status, detail)."""
    try:
        out = fn(*a)
        return asyncio.run(out) if asyncio.iscoroutine(out) else out
    except HTTPException as e:
        return (e.status_code, e.detail)


# -- the data the charts draw ------------------------------------------------
# A sampled key called after the system (the first key a palette drop binds
# is whatever sorts first, and that is what Tok Aba's legend showed), and a
# week of events in the master's own ring: the event store is not reachable
# from a temp config dir, so the query path takes its in-memory fallback, the
# same one a box with Postgres down takes.
NOW = time.time()
for i in range(90):
    history.sample({"demo": 100 + i, "people": 2 + i % 3}, now=NOW - 5400 + i * 60)
EVENTS = []
for d in range(7):
    for h in range(0, 24, 2):
        ts = datetime.datetime.fromtimestamp(NOW - d * 86400 - h * 3600)
        EVENTS.append({"ts": ts.astimezone().isoformat(), "type": "person",
                       "severity": "info", "label": "person"})
STATS = {"demo": 189, "people": 3}
TODAY = datetime.date.today().isoformat()


def render():
    """What the Analytics page draws for the stored layout: id -> the parts
    of the payload a chart is made of."""
    pages = analytics_service.compute_pages(
        [], EVENTS, STATS, CFG, SLUG, (), systems_dir=None,
        page_range="custom", page_start=TODAY, page_end=TODAY)
    out = {}
    for w in pages[0]["widgets"]:
        d = w.get("data") or {}
        out[w["id"]] = {"labels": d.get("labels"), "values": d.get("values"),
                        "datasets": [(s.get("label"), s.get("values"))
                                     for s in d.get("datasets") or []],
                        "title": w.get("title")}
    return out


# -- 1. the layout as a box on 1.0.9 saved it, plus two named-series charts --
CHARTS = [
    # The two from the report: events charts, no series list, a leftover key
    # from the palette drop (the inspector's Source switch never clears it).
    {"id": "c_hour", "title": "People Detected", "kind": "line",
     "source": "query", "key": "demo", "span": 4, "color": "#ff8800",
     "query": {"agg": "count", "bucket": "hour", "range": "today"}},
    {"id": "c_week", "title": "Total Detection (Weekly)", "kind": "bar",
     "source": "query", "key": "demo", "span": 4,
     "query": {"agg": "count", "bucket": "day", "range": "week"}},
    # ...and two history charts whose one series carries a name of its own.
    {"id": "c_1h", "title": "Heads", "kind": "line", "source": "history",
     "key": "people", "range": "1h", "span": 4,
     "series": [{"id": "s1", "key": "people", "label": "Heads in the bay",
                 "color": "#22c55e", "source": "history", "agg": "avg"}]},
    {"id": "c_7d", "title": "Heads this week", "kind": "area",
     "source": "history", "key": "people", "range": "7d", "span": 4,
     "series": [{"id": "s1", "key": "people", "label": "Week of heads",
                 "color": "#3b82f6", "source": "history", "agg": "max"}]},
]
builder_service.save(CFG, SLUG, {"pages": [{"id": "overview", "name": "Overview",
                                            "widgets": CHARTS}]})
before_layout = builder_service.load(CFG, SLUG)
before = render()

check("the hour chart draws its own hourly buckets under its own name",
      before["c_hour"]["labels"] and not before["c_hour"]["datasets"]
      and all(len(l) in (5, 11) for l in before["c_hour"]["labels"]),
      json.dumps(before["c_hour"]))
check("the weekly chart draws seven days",
      len(before["c_week"]["labels"] or []) == 7 and not before["c_week"]["datasets"],
      json.dumps(before["c_week"]))
check("the named charts draw under their series names",
      [s[0] for s in before["c_1h"]["datasets"]] == ["Heads in the bay"]
      and [s[0] for s in before["c_7d"]["datasets"]] == ["Week of heads"],
      json.dumps({k: before[k]["datasets"][:1] for k in ("c_1h", "c_7d")}))

# -- 2. Save, exactly as the Analytics Builder sends it ---------------------
# AnalyticsBuilder.jsx save(): re-read the layout, send the operator's own
# rows (every non-pkg widget, as loaded) under partial: true with the maps
# the page carries. Nothing is mapped over the rows any more.
layout = builder_service.load(CFG, SLUG)
page = next(p for p in layout["pages"] if p["id"] == "overview")
mine = [copy.deepcopy(w) for w in page["widgets"] if not str(w["id"]).startswith("wf_")]
body = {**layout, "partial": True,
        "pages": [{"id": "overview", "name": page["name"], "size": page.get("size"),
                   "widgets": mine}],
        "hidden": layout.get("hidden") or [], "placements": {},
        "templates": layout.get("templates") or {}, "colors": layout.get("colors") or {},
        "accents": layout.get("accents") or {}, "fonts": layout.get("fonts") or {},
        "names": layout.get("names") or {}}
r = call(builder_routes.save_layout, req(body))
check("PUT /api/builder (partial, the Analytics Builder's save) is accepted",
      isinstance(r, dict) and r.get("ok") is True, str(r))

after_layout = builder_service.load(CFG, SLUG)
after = render()
stored_before = {w["id"]: w for w in before_layout["pages"][0]["widgets"]}
stored_after = {w["id"]: w for w in after_layout["pages"][0]["widgets"]}
for wid in ("c_hour", "c_week", "c_1h", "c_7d"):
    check(f"{wid}: stored exactly as before the save",
          stored_after.get(wid) == stored_before.get(wid),
          json.dumps({"before": stored_before.get(wid), "after": stored_after.get(wid)}))
    check(f"{wid}: draws exactly as before the save",
          after.get(wid) == before.get(wid),
          json.dumps({"before": before.get(wid), "after": after.get(wid)}))
check("the events charts still carry no series list",
      not stored_after["c_hour"].get("series") and not stored_after["c_week"].get("series"),
      json.dumps([stored_after["c_hour"].get("series"), stored_after["c_week"].get("series")]))

# ...and a second press, as the Dashboard Builder sends its page: the same
# rows, the same route, still nothing moves.
r = call(builder_routes.save_layout, req({**body, "pages": [
    {"id": "overview", "name": page["name"], "widgets": mine}]}))
check("a second save (the Dashboard Builder's shape) changes nothing either",
      isinstance(r, dict) and r.get("ok") is True
      and {w["id"]: w for w in builder_service.load(CFG, SLUG)["pages"][0]["widgets"]}
      == stored_before and render() == before, str(r))

# -- 3. what the old save did, replayed: the picture changed -----------------
# builderShared.jsx widgetSeries, as the save applied it until today: one
# row from the card's own source and key, no label. The same server, handed
# that, draws the hour chart on a 24h grid named after its id and the weekly
# bar chart as one bar -- which is the report, and why a save sends rows as
# they are.
def materialised(w):
    source = w["source"] if w["source"] in ("history", "stat", "query") else ""
    q = w.get("query") or {}
    row = {"id": "s1", "label": "", "color": w.get("color") or "", "source": source,
           "agg": (q.get("agg") or "avg") if source == "history" else "last"}
    if w.get("key"):
        row["key"] = w["key"]
    if source == "query":
        row["query"] = dict(q)
    return {**w, "series": [row]}


old_shape = [materialised(copy.deepcopy(w)) if not w.get("series") else copy.deepcopy(w)
             for w in mine]
r = call(builder_routes.save_layout, req({**body, "pages": [
    {"id": "overview", "name": page["name"], "widgets": old_shape}]}))
replayed = render()
check("replaying the old save's rewrite is what broke the charts",
      isinstance(r, dict) and r.get("ok") is True
      and replayed["c_hour"] != before["c_hour"]
      and replayed["c_week"] != before["c_week"]
      and [s[0] for s in replayed["c_hour"]["datasets"]] == ["s1"],
      json.dumps({"hour": replayed["c_hour"], "week": replayed["c_week"]}))
# ...and the recovery is removing that list: the card's own fields were
# never lost, so the chart is back the moment the row goes.
damaged = builder_service.load(CFG, SLUG)
for w in damaged["pages"][0]["widgets"]:
    if w["id"] in ("c_hour", "c_week"):
        w.pop("series", None)
builder_service.save(CFG, SLUG, damaged)
recovered = render()
check("deleting the added series list brings the picture back exactly",
      recovered["c_hour"] == before["c_hour"] and recovered["c_week"] == before["c_week"],
      json.dumps({"hour": recovered["c_hour"], "week": recovered["c_week"]}))

# -- 4. source pins ----------------------------------------------------------
shared = read("frontend", "src", "components", "widgets", "builderShared.jsx")
analytics_page = read("frontend", "src", "pages", "AnalyticsBuilder.jsx")
dashboard_page = read("frontend", "src", "pages", "Builder.jsx")
check("builderShared.jsx has no save-side series migration",
      "export function withSeries" not in shared and "widgetSeries(w)" in shared)
check("the Analytics Builder sends its rows as they stand",
      "const mine = widgets.filter((w) => !w.pkg);" in analytics_page
      and ".map(withSeries)" not in analytics_page and "withSeries" not in analytics_page)
check("the Dashboard Builder sends its rows as they stand",
      "const rows = p.widgets || [];" in dashboard_page
      and ".map(withSeries)" not in dashboard_page and "withSeries" not in dashboard_page)
check("the series editor still reads a legacy chart as one derived row",
      "const seriesRows = chartSeries ? widgetSeries(current) : [];" in shared)

print()
print("FAILED" if bad else "ALL OK", f"({bad} failing)" if bad else "")
sys.exit(1 if bad else 0)
