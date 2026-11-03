"""A card's NAME and look survive a restart.

Reported as "I rename a card on Analytics or the Dashboard, and after a
restart it is called the old thing again". Three separate holes, all with the
same shape -- an edit written onto a row that is not the operator's to keep:

  1. A wf_* card is rebuilt from its workflow node on every compile. Its look
     was already sent back to the node (workflow.card_style), but its NAME was
     not in that list, so the rename lived on the widget row and was gone at
     the next compile. `name` joins STYLE_FIELDS, and the card's title already
     reads d["name"], so the canvas node and the dashboard now say the same
     thing.

  2. A PACKAGE card is declared in Python and has no node at all. The editors
     folded it in with the compiled ones: they CLEARED its override entries and
     then pushed a style to a route that skips it, so its colour, its text
     sizes and its title were dropped on every save. It gets its own branch,
     writing the maps that are its only home -- including a new `names` map.

  3. apply_artifacts rewrote the layout on every workflow save while carrying
     only three of the five override maps, so `fonts` (and now `names`) were
     reset by a save that had nothing to do with them.

    python tests/smoke_cardedits.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
STORE = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = STORE

from core.services import builder as builder_service               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


CFG = tempfile.mkdtemp()
SLUG = "cardedits"

WIDGET = {"id": "pkg_total", "title": "Total", "kind": "tile",
          "source": "stat", "key": "total", "span": 1}
LAYOUT = {"title": "Dash",
          "pages": [{"id": "main", "name": "Dashboard", "widgets": [WIDGET]}],
          "names": {"pkg_total": "Violations today", "wf_gone": "Renamed"},
          "colors": {"pkg_total": "#112233"},
          "accents": {"pkg_total": "#445566"},
          "templates": {"pkg_total": "accent"},
          "fonts": {"pkg_total": {"value": "large"}}}

saved = builder_service.save(CFG, SLUG, LAYOUT)
check("a rename is stored beside the layout", saved["names"]["pkg_total"] == "Violations today")

loaded = builder_service.load(CFG, SLUG)
w = loaded["pages"][0]["widgets"][0]
check("...and applied to the widget on the way in", w["title"] == "Violations today",
      "every consumer -- the dashboard route, Analytics, compute() -- reads "
      "the dressed widget without knowing overrides exist")
check("...alongside the other four overrides",
      w["bg"] == "#112233" and w["color"] == "#445566"
      and w["template"] == "accent" and w["value_size"] == "large")

# -- a save that does not mention the maps keeps them -------------------------
partial = builder_service.merge_partial(
    loaded, {"pages": [{"id": "main", "name": "Dashboard", "widgets": [WIDGET]}]})
check("a partial save that says nothing about names keeps them",
      partial["names"]["pkg_total"] == "Violations today",
      "the editor sends what it owns; a map it does not mention is not a map "
      "it emptied")
cleared = builder_service.merge_partial(loaded, {"names": {}, "pages": []})
check("...and one that sends an empty map DOES clear it", cleared["names"] == {})

# -- an empty or overlong name is not a name ---------------------------------
odd = builder_service.save(CFG, SLUG + "2", {
    **LAYOUT, "names": {"a": "  ", "b": "x" * 200, "": "no id"}})
check("a blank name is dropped, a long one is cut, an id-less entry ignored",
      "a" not in odd["names"] and len(odd["names"]["b"]) == 60
      and "" not in odd["names"])

# -- the round trip through the workflow node ---------------------------------
# The field list and the per-field cleaning live with the compiler now
# (workflow.apply_card_style); the route is the door.
routes = read("core", "routes", "workflow.py")
svc = read("core", "services", "workflow.py")
check("a dashboard may write the card's NAME back onto its node",
      # Name and caption first; Show title and a stat's Placement joined
      # them on 2026-10-14 (both editors write those too).
      'CARD_IDENTITY_FIELDS = ("name", "sub",' in svc
      and "STYLE_FIELDS = workflow_service.CARD_STYLE_FIELDS" in routes
      and "workflow_service.apply_card_style(store, cards)" in routes,
      "the node is what survives a compile, so the node is where a rename "
      "has to land")
check("...and it may not rename a card to nothing",
      'value = str(value or "").strip()[:60]' in svc
      and "if not value:\n                    continue" in svc)
check("...and the thresholds and the per-day colouring travel with it",
      # ...and a chart's per-series names and colours, which are drawn from
      # the node at compile time exactly as the bands are.
      'CARD_DATA_STYLE_FIELDS = ("bands", "ok_label", "alert_label", "series")'
      in svc
      and "value = clean_card_series(value, data.get(\"series\"))" in svc
      and '"anim", "anim_replay", "by_day")' in svc
      and 'value = builder_service.clean_bands(value)' in svc,
      "both are drawn from the node at compile time, so both had the same "
      "hole as the template did")

wf = read("core", "services", "workflow.py")
check("every compiled card carries the per-day colouring",
      wf.count('"by_day": _by_day(d),') >= 8 and "def _by_day(data):" in wf)
check("a Query card and a table card carry the FULL dressing",
      wf.count('"template": _template(d),') >= 10,
      "they carried none of it: a template or an accent set on either was "
      "discarded by the next compile with no message")
check("a workflow save carries every override map, not three of five",
      '"fonts": layout.get("fonts") or {},' in wf
      and '"names": layout.get("names") or {}})' in wf,
      "a package card's text size and name live in those maps and nowhere "
      "else, so a save about something else was resetting them")

# -- the editors ---------------------------------------------------------------
for page in ("Builder.jsx", "AnalyticsBuilder.jsx"):
    src = read("frontend", "src", "pages", page)
    check(f"{page}: the rename is sent to the node with the rest of the look",
          "name: w.title || \"\"," in src
          and "bands: w.bands || {}, by_day: !!w.by_day," in src)
    check(f"{page}: a package card writes the maps instead",
          "} else if (w.pkg) {" in src
          and "setOverride(named, w.id, w.title," in src
          and "setFont(fonts, w.id, w);" in src,
          "folded in with the compiled branch, it had its entries cleared and "
          "a style pushed to a route that skips it -- so it lost them")
    check(f"{page}: and the names map is written back",
          "names: named" in src)

shared = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("a default REMOVES its override rather than recording it",
      "export function setOverride(map, id, value, fallback) {" in shared
      and "else delete map[id];" in shared,
      "a map that stored the defaults would grow an entry per card per knob "
      "and say nothing")

analytics = read("core", "services", "analytics.py")
check("a package card on the Analytics page is renamed too",
      'named = layout.get("names") or {}' in analytics
      and 'w["title"] = named[w["id"]]' in analytics,
      "those widgets are computed fresh, so load()'s pass never touches them")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
