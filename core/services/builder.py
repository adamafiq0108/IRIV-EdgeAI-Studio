# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
builder.py -- declarative analytics widgets, composed in the UI.

A system package declares widgets in Python (`WidgetSpec.data_fn`). That is the
powerful path and it stays. This is the second path: widgets described as DATA,
so an operator can build a dashboard by dragging cards around instead of
writing a package.

**No code is ever stored, generated-and-executed, or eval'd here.** A layout is
JSON: each widget names a `source` and a `key`, and `compute()` reads those out
of the same `stats`/`events` every other widget sees. That is the difference
between a builder and a remote code execution hole -- a published layout is
untrusted input, and the only safe untrusted thing is data.

Layouts live in `<config_dir>/layouts/<slug>.json`, one file per system, so a
master with no database still builds and serves them (docs/master-db.md).
"""

import datetime
import json
import os
import re
import time
from urllib.parse import quote

from core import config as config_mod
from core.services import dashboard_controls as controls_service
from core.services import overlay_view

KINDS = ("tile", "line", "bar", "area", "spline", "donut", "pie", "radar",
         "gauge", "table", "timeline", "video", "status", "verdict", "heatmap",
         # The CONTROLS (services/dashboard_controls.py): an Inject's button,
         # the AI model's classes, the named Class Filters that run.
         "button", "ai_classes", "class_filters",
         # STATIC cards (Tok Aba, 2026-10-14): words, or a picture the
         # operator uploaded. They read nothing -- see STATIC_KINDS.
         "text_card", "image_card",
         # The newest camera snaps, paged with Previous / Next (Tok Aba,
         # 2026-10-26). The widget reads the snapshot list itself.
         "snap_gallery")
#: Media cards: they draw a picture, bind no key and are never marked AI-off.
MEDIA_KINDS = ("video", "snap_gallery")
#: How many snaps the gallery pages through at most, and by default.
SNAP_GALLERY_MAX = 20
SNAP_GALLERY_DEFAULT = 10
CONTROL_KINDS = ("button", "ai_classes", "class_filters")    # bind no key
#: Cards that show what the operator put ON them -- a block of words, an
#: uploaded picture -- and read no value at all: no key, no source data, no
#: AI-off or paused marking. Named "*_card" so neither is confused with a
#: tile's own `text` constant. The picture itself lives beside the store
#: (services/builder_assets.py); the widget keeps only its asset id.
STATIC_KINDS = ("text_card", "image_card")
#: The words a Text card may hold, and how they may be set.
MAX_TEXT_BODY = 2000
TEXT_CARD_SIZES = ("small", "medium", "large")
TEXT_CARD_ALIGNS = ("left", "center", "right")
IMAGE_FITS = ("contain", "cover")


#: Where a STAT sits in its card (Tok Aba, 2026-10-14: "allow user to choose
#: the position and alignment of the stat"): the value block -- number, unit,
#: comparison and caption, or a status pill / verdict word with its line --
#: aligned left / center / right (`align`, the Text card's own field name and
#: words) and placed top / middle / bottom (`valign`). Kept only when set, so
#: every card saved before is drawn exactly where it was: a tile's block on
#: the left and in the middle, a status pill and a verdict centred.
STAT_LAYOUT_KINDS = ("tile", "status", "verdict")
STAT_VALIGNS = ("top", "middle", "bottom")


def _stat_layout(kind, w):
    """`align` / `valign` of a stat card, when set to a known value."""
    if kind not in STAT_LAYOUT_KINDS:
        return {}
    out = {}
    if (w or {}).get("align") in TEXT_CARD_ALIGNS:
        out["align"] = w["align"]
    if (w or {}).get("valign") in STAT_VALIGNS:
        out["valign"] = w["valign"]
    return out


def title_shown_by_default(kind):
    """Whether a card of `kind` shows its title bar when nobody said
    (`show_title` absent): every kind does, as it always has, except the
    Text and Image cards, which are their own content."""
    return kind not in STATIC_KINDS


def _show_title(w):
    """The card's "Show title" switch (Tok Aba, 2026-10-14) -- only when it
    was SET (a bool), so every widget saved before it existed is stored and
    drawn exactly as before. The title text itself is never touched: hidden,
    it is still the name the Builder lists the card by."""
    value = (w or {}).get("show_title")
    return {"show_title": value} if isinstance(value, bool) else {}

#: The kinds that draw a SERIES rather than a single number -- they take
#: labels+values where a tile takes one value. Mirrors workflow.CHART_KINDS.
CHART_KINDS = ("line", "area", "spline", "bar", "donut", "pie", "radar")

#: How a widget is DRESSED, independently of what it draws. `kind` decides the
#: shape (a line, a donut, one number); `template` decides the treatment.
#:
#: Deliberately one flat list across tiles and charts rather than a per-kind
#: set: a template a kind does not understand falls back to "plain" at render
#: time (Widget.jsx), so switching a widget's kind can never leave it carrying
#: a template that renders as nothing.
#:
#:   plain     what every widget has always looked like -- the default, and
#:             what an existing layout keeps meaning without being rewritten.
#:   accent    filled with the widget's own colour, text inverted (a KPI tile
#:             that has to read from across a room).
#:   outline   the colour on the border and the value only, fill left alone.
#:   soft      a faint tint of the colour -- present, not loud.
#:   markers   charts: draw the point on every sample, not just the line.
#:   dashed    charts: dashed stroke, for a target/goal series.
#:   gradient  charts: fill fading to nothing under the line.
#:   legend    donut/pie: centre total plus a labelled row per slice.
#:
#: And four richer chart treatments, each the whole look rather than one
#: tweak of it -- these are what "pick a template for this graph" means:
#:
#:   stacked   series stacked into one filled shape, gradient fills, the
#:             supporting series dashed and the primary one solid: shares of
#:             a total, which separate lines cannot show.
#:   glow      one series, gradient fill, a lit dot on every sample: the
#:             treatment for a headline chart with few points.
#:   stripe    gradient fill with a diagonal hatch over it -- readable in
#:             print and to anyone who cannot separate two fills by hue.
#:   forecast  a solid line over a hatched band, for a projection: the hatch
#:             says "modelled, not measured" without a second legend.
TEMPLATES = ("plain", "accent", "outline", "soft",
             "markers", "dashed", "gradient", "legend",
             "stacked", "glow", "stripe", "forecast")

#: How a card ARRIVES, and how it moves when its numbers change. Separate from
#: `template`, which is how it is dressed: a stacked area chart may fade in or
#: slide in without either choice touching the other.
#:
#: Three families, and the difference matters when picking one:
#:
#:   appear   the whole card moves into place (fade, scale, slide, rotate).
#:            Works for every widget, chart or not, because it is the CARD
#:            that moves rather than anything inside it.
#:   draw     the shapes themselves are drawn on: bars grow out of the axis,
#:            a line draws itself, slices sweep round. Charts only -- there is
#:            nothing to draw on a number -- and `stagger` is this one series
#:            at a time.
#:   update   what happens when new numbers arrive: the shapes travel to their
#:            new positions (morph), the card blinks (fade_out, opacity), or
#:            the colours cross-fade. These are the ones an operator watching
#:            a live dashboard actually sees, over and over.
#:
#: "" is no animation at all, and stays the default: a wall display that
#: redraws every few seconds must not be made to move by an upgrade.
ANIMATIONS = ("", "fade_in", "fade_out", "scale_in", "scale_out",
              "slide_up", "slide_down", "slide_left", "slide_right",
              "rotate", "grow", "stagger", "morph", "opacity", "color",
              "enter_update_exit")

#: How a TILE's value reads. One whitelist for the whole product -- the
#: validator, compute() and the workflow compiler all read this list, and a
#: shorter copy in any of them is a format an operator can pick, save and
#: never see.
#:
#:   ""          the value itself, as it arrives.
#:   yesno /     a 0-or-1 state said in words, which is what a `found` output
#:   onoff /     wants a card to say.
#:   truefalse
#:   text        the value is a WORD and is shown as one: no rounding, no
#:               thousands separator, no "0" when it will not parse as a
#:               number. Needed once a stat could carry text (main.text_stat),
#:               because a tile bound to fn_<id>_value now receives "People"
#:               where every other tile receives a number.
VALUE_FORMATS = ("yesno", "onoff", "truefalse", "text")

#: The page the Dashboard renders. It is an ordinary Builder page -- that is the
#: whole point: the Dashboard is composed, not hardcoded, so an operator decides
#: whether the video feed shows at all. Reserved: it cannot be renamed or
#: deleted, because the Dashboard would then have nothing to render.
MAIN_PAGE_ID = "main"

#: An ANALYTICS page the operator added ("New page" on the Analytics page) has
#: an id that starts with this. Analytics shows each as a page of its own, and
#: the workflow's sweep of builder pages leaves them alone; every other page
#: that is not the Dashboard or the Overview is still folded into the Overview.
ANALYTICS_PAGE_PREFIX = "an_"


def is_analytics_page(page_id):
    return str(page_id or "").startswith(ANALYTICS_PAGE_PREFIX)
MAIN_PAGE_NAME = "Dashboard"

#: The verdict widget's default binding. `compliance` is published by
#: core/services/overview.py (1 ok / 0 violating / -1 no object).
COMPLIANCE_KEY = "compliance"

# The three widget families, by how they consume data:
#   single      -- latest value of one key (tile, gauge, status)
#   comparison  -- several keys at ONE point in time (pie/donut/bar/radar with
#                  a `series` list; each series contributes its latest value)
#   timeseries  -- how values move over time (line/area/spline with a `series`
#                  list, range, interval and per-series aggregation, read from
#                  the history sampler)
# Widgets without a `series` list keep their original single-key behaviour, so
# every stored layout renders unchanged.
TIMESERIES_KINDS = ("line", "area", "spline")

#: Charts whose x axis can span days, and so can be coloured a day at a time.
#: The bar chart joins the line ones here: a bar per day is the plainest
#: per-day chart there is, and it takes the colouring as a Cell per bar.
DAY_COLOUR_KINDS = ("line", "area", "spline", "bar")
COMPARISON_KINDS = ("pie", "donut", "bar", "radar")
#: Charts that draw threshold bands: the line shapes colour the line by
#: level, a bar chart paints each bar by the band its value is in (per-camera
#: thresholds, owner 2026-10-13).
BAND_KINDS = TIMESERIES_KINDS + ("bar",)
MAX_SERIES = 8
# "query" reads the persisted event store (core/repos/events.py) -- real
# aggregations over history, filters and time buckets -- while stat/bucket/events
# read the live rolled-up stats. "history" reads the master's stat sampler
# (core/services/history.py) -- a real time series of any live stat key. A
# widget stores a SPEC (data, never code), the same safety rule everywhere.
#: "dataset" reads a table the operator DEFINED (a workflow Dataset node,
#: core/repos/datasets.py) rather than one the product ships. Its column names
#: are not a constant, so the widget carries the declaration it was built
#: against and the repo re-checks every name against it -- see _clean_dataset.
#: "query" is the EVENT-store aggregator (see _query_data) and predates the
#: Query node; "sql" is the operator's own statement, run through the
#: read-only role in services/sqlconsole.py.
SOURCES = ("stat", "bucket", "events", "query", "history", "dataset", "sql")
#: "today" is DAY-ALIGNED -- local midnight to now, growing through the shift
#: (history.span_of). "page" is not a span at all: the chart follows whatever
#: period the Analytics page's own picker holds (today/week/month), resolved
#: at render time in compute() -- one chart, one date choice, no second picker
#: to keep in agreement.
class _Vocab(tuple):
    """A whitelist that iterates as its presets (what a picker offers) and
    also ACCEPTS whatever `extra` says is valid -- the chart time axis's
    custom steps and its longer ranges (services/timeaxis.py), so every
    `value in X` check in the product takes them without a second list."""

    def __new__(cls, items, extra):
        obj = super().__new__(cls, items)
        obj._extra = extra
        return obj

    def __contains__(self, value):
        if tuple.__contains__(self, value):
            return True
        try:
            return bool(value) and bool(self._extra(value))
        except Exception:                                   # noqa: BLE001
            return False


from core.services import timeaxis as _timeaxis              # noqa: E402

HISTORY_RANGES = _Vocab(("page", "today", "5m", "15m", "30m", "1h", "6h",
                         "12h", "24h", "7d", "30d"), _timeaxis.clean_range)

#: What "page" means for each Analytics period. Custom windows fall back to
#: 24h: the history rings hold samples, not an archive, so an arbitrary
#: past-dates window has nothing to read.
_PAGE_RANGE = {"today": "today", "week": "7d", "month": "30d"}
HISTORY_INTERVALS = _Vocab(("", "10s", "1m", "5m", "15m", "1h", "1d"),  # "" = auto
                          _timeaxis.clean_step)
#: "unique" (Count Difference Value) joined with the chart builder: the
#: sampler counts the different sampled values per cell, the stores
#: COUNT(DISTINCT) per bucket -- never added up across buckets (chart_series).
#: "changes" (Count Value Changes) likewise: per cell, the times the value
#: differs from the one before it in the whole ordered series.
SERIES_AGGS = ("avg", "sum", "min", "max", "last", "count", "unique", "changes")
STATUS_OPS = (">", ">=", "<", "<=", "==", "!=")
#: "latest" is not a SQL aggregate -- it is the newest recorded value, which
#: is what a live tile means by "the value". max() is a different question
#: (the biggest ever, usually some spike from last Tuesday).
#: "changes" is how many times a column took a new value, in time order --
#: the question a live-state table is recorded to answer. Distinct from both
#: neighbours: count() would score a row that said the same thing again, and
#: unique() would score somebody who left and came back only once.
#: "count_nonnull" (chart builder refactor, 2026-10-13) is the rows where a
#: column holds a value; "count" is every row ("Count records").
AGGREGATIONS = ("count", "count_nonnull", "sum", "avg", "min", "max", "unique",
                "changes", "latest")
#: The fixed sub-hour resolutions first, then the calendar buckets -- the
#: order a picker reads finest to coarsest. The repos also accept any
#: "<n>s"/"<n>m" (events_repo.bucket_seconds); these are the presets.
BUCKETS = _Vocab(("10s", "1m", "5m", "15m", "hour", "day", "week", "month"),
                _timeaxis.clean_step)
RANGES = {"today": 1, "week": 7, "month": 30}

#: "All time" for a statement that insists on a window. Not None: a bound
#: parameter has to be something, and a card set to all time must still run.
_EPOCH_ISO = "1970-01-01T00:00:00+00:00"

#: What a recorded-data query may put in `range`: a fixed period, or "page" --
#: follow the Analytics date pick, resolved at render in compute(). Same idea
#: as HISTORY_RANGES' "page", for the database-backed widgets.
QUERY_RANGES = _Vocab(("page",) + tuple(RANGES), _timeaxis.clean_range)

#: What the previous window is CALLED on a comparison tile. The caption reads
#: "Vs yesterday: 12,400", so it has to name the period, not the length.
_PREV_LABEL = {"today": "yesterday", "week": "last week", "month": "last month"}

MAX_WIDGETS = 24
#: Tables one Query node may join across. A bound rather than a rule: the
#: views are cheap, but a widget carrying an unbounded list is a layout that
#: takes unbounded work to render.
MAX_QUERY_TABLES = 8
MAX_TABLE_ROWS = 50


def path_for(config_dir, slug):
    # Builder layouts are USER-generated, so they live in the persistent store
    # OUTSIDE the repo (config_mod.layouts_dir) -- a git pull never loses an
    # operator's dashboard. config_dir is kept for signature compatibility.
    return os.path.join(str(config_mod.layouts_dir()), f"{slug}.json")


def _legacy_path(config_dir, slug):
    """The old in-repo layout location, read as a fallback so a layout saved
    before the store existed is not lost."""
    return os.path.join(config_mod.root(config_dir), "layouts", f"{slug}.json")


# -- persistence -------------------------------------------------------------

MAX_PAGES = 20


#: How tall one grid row is, by name. The GRID itself does not change -- it is
#: twelve columns however this is set -- so a card that spans 3x2 keeps meaning
#: 3x2; only the pixels each row is worth change. That is what makes the
#: setting safe on a page somebody already arranged: nothing moves, everything
#: grows or shrinks together.
#:
#: Saved with the PAGE rather than kept as a browser preference, because the
#: builder and the finished dashboard have to agree -- "what I arranged is not
#: what I got" is the one thing the two grids exist to prevent.
PAGE_SIZES = ("small", "medium", "big")


#: How big the words on a card are, as a multiplier on their normal size.
#: Two settings rather than one: the TITLE and the note under a card are its
#: chrome, and the number is the thing the card exists to show -- a wall
#: display wants a huge number with small chrome, and a dense page of small
#: cards wants the opposite. One control could not say either.
TEXT_SIZES = ("small", "medium", "large", "huge")


def _as_text_size(value):
    """One of TEXT_SIZES, medium by default -- which is the size every card
    was drawn at before this existed, so a layout that says nothing is
    unchanged."""
    text = str(value or "").strip().lower()
    return text if text in TEXT_SIZES else "medium"


def _as_size(value):
    """One of PAGE_SIZES. Anything else -- and every page saved before this
    existed -- is medium, which is the size those pages were built at."""
    text = str(value or "").strip().lower()
    return text if text in PAGE_SIZES else "medium"


def _as_pages(data):
    """Normalise any stored/legacy shape to {pages: [{id, name, widgets}]}.

    The old format was a single flat {widgets: [...]}; migrate it to one page so
    an existing layout keeps working. Never raises -- a broken layout renders as
    an empty first page, not a 500."""
    if isinstance(data.get("pages"), list) and data["pages"]:
        pages = data["pages"]
    else:
        pages = [{"id": "main", "name": "Dashboard", "widgets": data.get("widgets") or []}]
    out = []
    for i, p in enumerate(pages[:MAX_PAGES]):
        out.append({"id": str(p.get("id") or f"p{i + 1}")[:40],
                    "name": str(p.get("name") or f"Page {i + 1}")[:60],
                    "size": _as_size(p.get("size")),
                    "widgets": p.get("widgets") or []})
    return out


def load(config_dir, slug):
    """The saved layout as {slug, title, pages:[{id,name,widgets}]}. A missing or
    corrupt file is an empty single-page layout, never an error."""
    data = {}
    for path in (path_for(config_dir, slug), _legacy_path(config_dir, slug)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            break
        except (OSError, ValueError):
            continue
    templates = _as_templates(data)
    colors = _as_colors(data)
    accents = _as_accents(data)
    fonts = _as_fonts(data)
    names = _as_names(data)
    pages = _as_pages(data)
    # The overrides are applied ON THE WAY IN, so every consumer -- the
    # Dashboard route, Analytics, the editors, compute() -- sees the dressed
    # widget without having to know overrides exist.
    if templates or colors or accents or fonts or names:
        for p in pages:
            for w in p.get("widgets") or []:
                wid = str((w or {}).get("id", ""))
                t = templates.get(wid)
                if t:
                    w["template"] = t
                c = colors.get(wid)
                if c:
                    w["bg"] = c
                a = accents.get(wid)
                if a:
                    w["color"] = a
                title = names.get(wid)
                if title:
                    w["title"] = title
                f = fonts.get(wid)
                if f:
                    if f.get("label"):
                        w["label_size"] = f["label"]
                    if f.get("value"):
                        w["value_size"] = f["value"]
    return {"slug": slug, "title": str(data.get("title") or "").strip()[:80],
            "pages": pages, "hidden": _as_hidden(data),
            "placements": _as_placements(data), "templates": templates,
            "colors": colors, "accents": accents, "fonts": fonts,
            "names": names}


MAX_HIDDEN = 200


def _as_hidden(data):
    """Widget ids the operator has removed from view.

    A system package's widgets are declared in Python, so there is no layout
    entry to delete -- but "I don't want that card" is a normal thing to want.
    Storing the removal instead of the widget keeps the package the source of
    what EXISTS and the layout the source of what is SHOWN, and makes the
    removal reversible.
    """
    raw = data.get("hidden")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for wid in raw[:MAX_HIDDEN]:
        wid = str(wid).strip()[:40]
        if wid and wid not in seen:
            seen.add(wid)
            out.append(wid)
    return out


def _as_placements(data):
    """{page_id: {widget_id: grid}} -- WHERE a package widget sits.

    Package widgets are declared in Python, so their existence and data are the
    package's; but where they sit on the page is the operator's, and that has
    to live somewhere they can write. Same split as `hidden`: the package says
    what exists, the layout says how it is shown.
    """
    raw = data.get("placements")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for pid, entries in list(raw.items())[:MAX_PAGES]:
        if not isinstance(entries, dict):
            continue
        clean = {}
        for wid, grid in list(entries.items())[:MAX_WIDGETS]:
            wid = str(wid).strip()[:40]
            grid = _clean_grid(grid)
            if wid and grid:
                clean[wid] = grid
        if clean:
            out[str(pid).strip()[:40]] = clean
    return out


def _as_templates(data):
    """{widget_id: template} -- how a COMPILED (wf_*) card is dressed.

    Same split as `hidden` and `placements`: a wf_* widget's existence and
    data belong to its workflow node and are rewritten on every compile, so a
    template edited onto the widget itself was discarded by the next merge.
    The dressing is the operator's, so it lives where the operator's other
    decisions about compiled cards already live -- beside the widget, keyed by
    id, surviving any number of recompiles. The node's own template picker
    sets the default; an entry here overrides it.
    """
    raw = data.get("templates")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for wid, t in list(raw.items())[:MAX_WIDGETS * MAX_PAGES]:
        wid = str(wid).strip()[:40]
        if wid and t in TEMPLATES:
            out[wid] = t
    return out


def _as_fonts(data):
    """{widget_id: {"label": size, "value": size}} -- how big a COMPILED card's
    words are.

    Same override as `_as_templates`, for the same reason: a wf_* card's row is
    rewritten on every compile, so a size set on the widget itself would be
    thrown away by the next one -- and most of the cards on a real dashboard
    are compiled ones, so without this the control would appear to work and
    then quietly forget.
    """
    raw = data.get("fonts")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for wid, f in list(raw.items())[:MAX_WIDGETS * MAX_PAGES]:
        wid = str(wid).strip()[:40]
        if not wid or not isinstance(f, dict):
            continue
        entry = {}
        for field in ("label", "value"):
            if f.get(field):
                entry[field] = _as_text_size(f.get(field))
        # Only entries that SAY something: a row of two mediums is the default
        # written down, and it would keep the map growing for no effect.
        if any(v != "medium" for v in entry.values()):
            out[wid] = entry
    return out


def _as_colors(data):
    """{widget_id: "#rrggbb"} -- a CARD's own background, overriding whatever
    its template would otherwise paint it.

    Same override as `_as_templates`, same reason: a wf_*/package card's row
    is rewritten (or simply not editable at all) outside the operator's
    control, so a colour picked on the card itself would be discarded on the
    next compile. This is the CARD's colour, distinct from `color` (the
    accent used for a chart's stroke or a tile's accent/outline/soft
    treatment) -- an operator who wants "this one tile is blue" regardless of
    which template it uses needs a knob the template dressing does not gate.
    """
    raw = data.get("colors")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for wid, c in list(raw.items())[:MAX_WIDGETS * MAX_PAGES]:
        wid = str(wid).strip()[:40]
        c = _color(c)
        if wid and c:
            out[wid] = c
    return out


def _as_names(data):
    """{widget_id: title} -- what a card the operator does not own is CALLED.

    The fourth override, and the one whose absence was loudest: renaming a card
    is the first thing anybody does to a dashboard, and on a compiled or
    package card the new name was typed in, written onto a widget row, and
    thrown away by the next compile. A compiled card now sends its name back to
    its NODE (workflow.card_style), so this map is for the cards that have no
    node to send it to -- the package widgets declared in Python -- and for a
    compiled one whose node has since been deleted.
    """
    raw = data.get("names")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for wid, title in list(raw.items())[:MAX_WIDGETS * MAX_PAGES]:
        wid = str(wid).strip()[:40]
        title = str(title or "").strip()[:60]
        if wid and title:
            out[wid] = title
    return out


def _as_accents(data):
    """{widget_id: "#rrggbb"} -- a card's ACCENT: a chart's stroke, a tile's
    accent/outline/soft paint.

    The third override, for the same reason as the other two: a wf_* card's
    row is rewritten by every compile and a package card's row is not editable
    at all, so an accent picked on the widget itself was thrown away by the
    next merge -- which is exactly what "the accent colour does nothing in the
    Analytics Builder" was. Distinct from `colors`, which paints the card's
    BACKGROUND; an operator commonly wants one without the other.
    """
    raw = data.get("accents")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for wid, c in list(raw.items())[:MAX_WIDGETS * MAX_PAGES]:
        wid = str(wid).strip()[:40]
        c = _color(c)
        if wid and c:
            out[wid] = c
    return out


def save(config_dir, slug, layout):
    """Validate every page's widgets then write atomically. Accepts the multi-
    page shape or a legacy {widgets}. Raises ValueError -> route answers 400."""
    pages_in = _as_pages(layout)
    pages, seen = [], set()
    for i, p in enumerate(pages_in):
        pid = p["id"]
        if pid in seen:
            pid = f"{pid}_{i}"
        seen.add(pid)
        pages.append({"id": pid, "name": p["name"], "size": p["size"],
                      "widgets": validate(p["widgets"])})
    out = {"slug": slug, "title": str(layout.get("title") or "").strip()[:80],
           "pages": pages, "hidden": _as_hidden(layout),
           "placements": _as_placements(layout),
           "templates": _as_templates(layout), "colors": _as_colors(layout),
           "accents": _as_accents(layout), "fonts": _as_fonts(layout),
           "names": _as_names(layout)}
    target = path_for(config_dir, slug)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    # The pictures the layout names before this save, so a card deleted or a
    # picture replaced takes its file with it (builder_assets.after_save).
    try:
        before = load(config_dir, slug)
    except Exception:                                       # noqa: BLE001
        before = {}
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    os.replace(tmp, target)
    try:
        from core.services import builder_assets            # noqa: PLC0415
        builder_assets.after_save(slug, before, out)
    except Exception:                                       # noqa: BLE001
        pass                    # a cleanup miss is swept next save, never a 500
    return out


def merge_partial(stored, incoming):
    """The stored layout with only the OPERATOR's widgets taken from `incoming`.

    A layout save sends the whole document, so two writers race: an operator
    dragging a card on the Analytics page reads the layout, edits it in the
    browser, and PUTs it back seconds later -- and a workflow recompile in
    between (a graph save, another tab, another operator) has written its
    `wf_*` widgets into the same file. The full-document write then clobbers
    them, and the cards vanish until the next recompile.

    So a partial save carries only what the browser actually owns: per page,
    the non-`wf_` widgets replace the stored ones and the stored `wf_*` widgets
    are kept exactly as they are. `hidden` and `placements` are whole-document
    state the editor does own, so they replace wholesale. Pages the request does
    not mention are untouched.
    """
    # No `pages` at all (a request that only deletes a page, say) names no page:
    # _as_pages would otherwise answer with a blank Dashboard to merge in.
    pages = ({p["id"]: p for p in _as_pages(incoming)}
             if (incoming.get("pages") or incoming.get("widgets")) else {})
    # An Analytics page the operator deleted. Only their own pages can be named
    # here: the Dashboard and the Overview are not theirs to remove.
    gone = {str(i) for i in (incoming.get("delete_pages") or [])
            if is_analytics_page(i)}
    out = []
    for p in stored.get("pages") or []:
        if p["id"] in gone:
            pages.pop(p["id"], None)
            continue
        sent = pages.pop(p["id"], None)
        if sent is None:
            out.append(p)
            continue
        compiled = [w for w in (p.get("widgets") or [])
                    if str((w or {}).get("id", "")).startswith("wf_")]
        own = [w for w in (sent.get("widgets") or [])
               if not str((w or {}).get("id", "")).startswith("wf_")]
        out.append({"id": p["id"], "name": sent.get("name") or p["name"],
                    # The size is the operator's, like the name: a partial save
                    # from the editor carries it, and a page the request does
                    # not mention keeps what it had.
                    "size": _as_size(sent.get("size") or p.get("size")),
                    "widgets": own + compiled})
    # A page the request adds is new; it has no compiled widgets to protect.
    for p in pages.values():
        out.append({**p, "widgets": [w for w in (p.get("widgets") or [])
                                     if not str((w or {}).get("id", "")).startswith("wf_")]})
    if len(out) > MAX_PAGES:
        raise ValueError(f"a layout holds at most {MAX_PAGES} pages")
    return {"slug": stored.get("slug"),
            "title": incoming.get("title", stored.get("title") or ""),
            "pages": out,
            "hidden": incoming.get("hidden") if "hidden" in incoming
                      else stored.get("hidden") or [],
            "placements": incoming.get("placements") if "placements" in incoming
                          else stored.get("placements") or {},
            "templates": incoming.get("templates") if "templates" in incoming
                         else stored.get("templates") or {},
            "colors": incoming.get("colors") if "colors" in incoming
                      else stored.get("colors") or {},
            "fonts": incoming.get("fonts") if "fonts" in incoming
                     else stored.get("fonts") or {},
            "accents": incoming.get("accents") if "accents" in incoming
                       else stored.get("accents") or {},
            "names": incoming.get("names") if "names" in incoming
                     else stored.get("names") or {}}


def seed_main_widgets(shaped):
    """The Dashboard a system starts with, from its zones' actions.

    A brand-new dashboard that is blank is a dead end -- the operator has to
    guess what to drag before they know what the builder can do. So the first
    render is composed FOR them, from what the system already declares: the live
    feed, a violation indicator when any zone has a violation action, and a tile
    per value card. Every one of them is an ordinary widget: move it, resize it,
    delete the feed if this board has no screen. Nothing here is special-cased
    downstream.

    `shaped` is core/services/overview.zone_overview()'s output.
    """
    widgets = [{
        "id": "seed_feed", "title": "Live feed", "kind": "video", "source": "stat",
        "key": "", "span": 2, "grid": {"x": 0, "y": 0, "w": 7, "h": 4},
    }]
    if shaped.get("has_violation_action"):
        widgets.append({
            "id": "seed_verdict", "title": "Status", "kind": "verdict",
            "source": "stat", "key": COMPLIANCE_KEY, "span": 1,
            "grid": {"x": 7, "y": 0, "w": 5, "h": 2},
        })
    x, y = 7, 2 if shaped.get("has_violation_action") else 0
    for z in shaped.get("zones") or []:
        for card in z.get("cards") or []:
            if len(widgets) >= MAX_WIDGETS:
                return widgets
            widgets.append({
                "id": f"seed_{card['key']}"[:40],
                "title": card["label"], "kind": "tile", "source": "stat",
                "key": card["key"], "span": 1,
                "unit": "s" if card.get("kind") == "seconds" else "",
                "grid": {"x": x, "y": y, "w": 5, "h": 2},
            })
            y += 2
            if y >= 8 and x == 7:       # second column once the first fills
                x, y = 0, 4
    return widgets


def ensure_main_page(config_dir, slug, shaped):
    """Make sure the layout has the Dashboard page, seeding it once.

    Written ONCE, on first read: after that the page is the operator's, and a
    system whose zones change later must not have its dashboard rearranged
    underneath it. A layout that already has a `main` page is adopted as-is.

    Nothing is written until the system actually has something to show. Seeding
    a bare video tile on the very first page load would spend the one-shot seed
    on an unconfigured system, and the zone tiles drawn later would never
    appear -- the operator would be left believing the dashboard does not build
    itself.
    """
    layout = load(config_dir, slug)
    pages = layout.get("pages") or []
    if any(p.get("id") == MAIN_PAGE_ID for p in pages):
        return layout
    if not any(z.get("cards") for z in shaped.get("zones") or []):
        return layout
    seeded = {"id": MAIN_PAGE_ID, "name": MAIN_PAGE_NAME,
              "widgets": seed_main_widgets(shaped)}
    layout["pages"] = [seeded] + pages
    try:
        return save(config_dir, slug, layout)
    except (ValueError, OSError):       # noqa: BLE001
        return layout                   # render it even if it cannot be stored


def main_page(layout):
    """The Dashboard page from a layout, or None."""
    for p in layout.get("pages") or []:
        if p.get("id") == MAIN_PAGE_ID:
            return p
    return None


def all_widgets(layout):
    """Every widget across every page -- for publish/export which stay flat."""
    return [w for p in layout.get("pages", []) for w in p.get("widgets", [])]


# -- validation --------------------------------------------------------------

def validate(widgets, strict=True):
    """Return the cleaned widget list, or raise ValueError.

    Every field is clamped rather than trusted. A layout arrives from a browser
    session and gets rendered for every operator afterwards, so "the UI would
    never send that" is not a check.

    `strict` separates what may be STORED from what may be LOOKED AT, and only
    series rows read it (see chart_series.migrate). Save, publish and template
    apply keep the default and still refuse a half-finished row. The preview
    route passes strict=False, because the builder previews the whole page on
    every keystroke: one row mid-edit used to raise, the route turned that into
    a 400, and every other card on the page went blank while the operator was
    still choosing a table. Unfinished now previews as an empty, named series.
    """
    if not isinstance(widgets, list):
        raise ValueError("widgets must be a list")
    if len(widgets) > MAX_WIDGETS:
        raise ValueError(f"at most {MAX_WIDGETS} widgets")

    seen, out = set(), []
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            raise ValueError(f"widget {i} is not an object")
        kind = w.get("kind")
        if kind not in KINDS:
            raise ValueError(f"widget {i}: kind must be one of {KINDS}")
        source = w.get("source") or ("events" if kind == "table" else "stat")
        if source not in SOURCES:
            raise ValueError(f"widget {i}: source must be one of {SOURCES}")
        series = _clean_series(w.get("series"), source, strict, w)
        # A video widget renders the MJPEG preview client-side; it reads no data
        # at all, so it is exempt from the key requirement. A widget with a
        # `series` list gets its keys from there instead.
        key = str(w.get("key") or "").strip()
        # A verdict widget always reads the same derived stat, so it needs no
        # key from the operator -- asking for one would only be a chance to get
        # it wrong.
        if kind == "verdict" and not key:
            key = COMPLIANCE_KEY
        # A tile carrying its own WORDS needs no key either: there is nothing
        # to look up, the value is on the widget. (Only a tile -- a bucket or
        # a history widget draws a series over time, and words have none.)
        has_text = bool(str(w.get("text") or "").strip()) and source == "stat"
        if kind not in MEDIA_KINDS + CONTROL_KINDS[1:] + STATIC_KINDS and not series \
                and not has_text and source in ("stat", "bucket", "history") and not key:
            raise ValueError(f"widget {i}: a {source} widget needs a key")

        wid = str(w.get("id") or f"w{i + 1}").strip()[:40]
        if wid in seen:
            raise ValueError(f"duplicate widget id {wid!r}")
        seen.add(wid)

        # A widget with a series list is validated THROUGH its series and the
        # widget-level requirements below do not apply -- the same exemption
        # the key check above has always had, for the same reason. A dataset
        # series carries its table and columns per ROW (chart_series._entry
        # checks each one), so there is nothing at widget level to find, and
        # demanding it rejected every multi-series dataset chart there is.
        ds = _clean_dataset(w) if source == "dataset" else {}
        if source == "dataset" and not ds and not series:
            raise ValueError(f"widget {i}: a dataset widget needs a dataset "
                             "and its columns")

        # sql is deliberately not a series source (chart_series' docstring says
        # why), so a statement widget cannot be validated through its rows the
        # way a dataset one can. It is exempted on the same condition anyway:
        # a widget carrying series is drawn by chart_series -- _data reaches
        # the resolver before the sql path -- so the statement is not what
        # renders it and must not be what gates it.
        sql = _clean_sql(w) if source == "sql" else {}
        if source == "sql" and not sql and not series:
            raise ValueError(f"widget {i}: a query widget needs a SELECT "
                             "statement and the tables it may read")

        out.append({
            "id": wid,
            "title": str(w.get("title") or wid).strip()[:60],
            "kind": kind,
            "source": source,
            "key": key[:60],
            "span": min(4, max(1, int(w.get("span") or 1))),
            "capability": str(w.get("capability") or "").strip()[:40],
            "color": _color(w.get("color")),
            # The CARD's own background -- distinct from `color` above, which
            # is the accent a chart's stroke or a tile's accent/outline/soft
            # treatment reads. Blank means "let the template decide", exactly
            # as before this field existed.
            "bg": _color(w.get("bg")),
            # Unknown/absent -> "plain": a layout written before templates
            # existed keeps looking exactly as it did.
            "template": (w.get("template") if w.get("template") in TEMPLATES
                         else "plain"),
            # How it arrives and how it moves. Unknown -> "" (still), so a
            # layout written before this existed keeps its stillness.
            "anim": (w.get("anim") if w.get("anim") in ANIMATIONS else ""),
            # Whether an ARRIVAL animation plays again every time the numbers
            # change. Off by default and deliberately so: a dashboard polls
            # every few seconds, and a card that slides in on every poll is a
            # card nobody can read.
            "anim_replay": bool(w.get("anim_replay")),
            # A tile may show WORDS, by either of two routes, and they are
            # not interchangeable:
            #
            #   * THIS field -- a text CONSTANT on the canvas. It is a fixed
            #     word that nothing publishes and nothing measures, so there
            #     is no key to look up and it travels on the widget itself.
            #     Set, it IS the tile's value; blank, nothing changes.
            #   * a text STAT (source "stat" with a key). A rule whose result
            #     is a word now publishes it like any other value and the
            #     tile reads it through the stats dict, live, per poll. That
            #     dict was numbers-only for a reason -- the charts, the
            #     history sampler and the Blynk pusher all read it -- so each
            #     of those still refuses a string rather than charting it
            #     (history.sample, blynk.push, chart_series).
            #
            # The constant wins where both exist: a fixed word is a decision
            # someone typed onto this card.
            "text": str(w.get("text") or "")[:120],
            "unit": str(w.get("unit") or "").strip()[:12],
            # Tile only: how the value READS. "" = the value itself; the
            # rest are VALUE_FORMATS, which is also what compute() carries
            # to the page and what the workflow compiler whitelists.
            "format": (w.get("format")
                       if w.get("format") in VALUE_FORMATS
                       else ""),
            "sub": str(w.get("sub") or "").strip()[:60],
            # How big the card's words are. `label_size` is the title and the
            # note under the value -- the card's chrome, on every kind of card
            # including a chart. `value_size` is the number or the word the
            # card exists to show, which only the kinds that HAVE one read.
            "label_size": _as_text_size(w.get("label_size")),
            "value_size": _as_text_size(w.get("value_size")),
            # Video widget only: which of the system's cameras to show. Blank
            # (the single-camera case, and every non-video widget) means the
            # app's first camera, exactly as before.
            "camera": str(w.get("camera") or "").strip()[:32],
            "limit": min(MAX_TABLE_ROWS, max(1, int(w.get("limit") or 15))),
            # A statement-backed card keeps a period and nothing else: it
            # has no aggregation to clamp (the statement IS the aggregation),
            # but it does have a window, and that window is what "%(from)s"
            # and "%(to)s" are bound to when it runs.
            # ds.get, not ds[...]: a dataset widget drawn from a series list
            # has no widget-level dataset at all, so there is no query here to
            # clamp and {} is the honest answer.
            "query": (ds.get("query", {}) if source == "dataset"
                      else _clean_query(w.get("query")) if source == "query"
                      else {"range": _as_query_range(w.get("query"))}
                      if source == "sql" else {}),
            # Dataset widgets only: which operator-defined table, and the
            # column declaration that bounds every name in the query above.
            "dataset": ds.get("dataset", ""),
            "columns": ds.get("columns", []),
            # Query widgets only: the operator's statement, and the tables it
            # is allowed to see. The tables travel WITH the widget for the
            # same reason `columns` does -- the renderer must be able to make
            # the views the statement reads without going back to the canvas.
            "sql": sql.get("sql", ""),
            "tables": sql.get("tables", []),
            # Grid placement for the freestyle dashboard: 12-column cells.
            # Absent on older layouts -- the frontend auto-flows those.
            "grid": _clean_grid(w.get("grid")),
            # Status widget: ok/alert pill from a comparison on the bound key.
            "status": _clean_status(w.get("status")) if kind == "status" else {},
            # Verdict widget: optional custom value->state mapping, so a
            # workflow value can drive the big card with MORE than ok/alert
            # (0 idle, 1 warning, 2 violation...). Empty = the legacy
            # compliance mapping.
            "states": _clean_states(w.get("states")) if kind == "verdict" else [],
            # The WINDOW and the bucket size a time chart is drawn on. Kept
            # for every time-series kind now, not only a history widget: a
            # chart whose series come from the event store and a dataset
            # still has to say what span it covers and how finely, and that
            # answer cannot live on any one series -- the whole point is that
            # they share one axis.
            "range": (w.get("range") if w.get("range") in HISTORY_RANGES
                      else "24h") if source == "history"
                     or kind in TIMESERIES_KINDS else "",
            # Which workflow NODE this widget was dragged from ("" = none).
            # The link, not a copy: every workflow apply refreshes a linked
            # widget's body from that node's fresh compile, so editing the
            # node updates every card made from it -- a copy went stale the
            # moment it was dropped, which is how a verdict kept saying
            # VIOLATION after its labels were changed on the canvas.
            "node": re.sub(r"[^A-Za-z0-9_-]", "", str(w.get("node") or ""))[:40],
            # Button widget only: which Inject it presses, and whether that
            # node is still on the canvas (workflow.apply_artifacts marks it).
            "inject": _clean_inject(w.get("inject")) if kind == "button" else {},
            # Threshold bands, charts that draw a LINE only. N thresholds cut
            # the value axis into N+1 coloured bands, and the line takes the
            # colour of the band it is passing through -- so "we went over the
            # limit at 14:20" is read off the picture rather than off the axis.
            # Empty (the default) is the plain single-colour line every chart
            # drew before this existed.
            "bands": (clean_bands(w.get("bands"))
                      if kind in BAND_KINDS else {}),
            # One colour per DAY on a dated axis, the newest day keeping the
            # card's accent. Off by default: a week of hourly buckets drawn in
            # one colour is what every chart did before this existed, and a
            # card that recolours itself the day somebody widens the range is
            # a card that broke.
            "by_day": (bool(w.get("by_day")) if kind in DAY_COLOUR_KINDS
                       else False),
            # Multi-series config (comparison + time-series widgets).
            "series": series,
            "interval": (w.get("interval") if w.get("interval") in HISTORY_INTERVALS
                         else "") if source == "history"
                        or kind in TIMESERIES_KINDS else "",
            # "Pick dates...": the exact window a chart draws, when its range
            # is "custom" (services/timeaxis.py). "" when unset.
            **_range_ends(w),
            # The chart builder model (services/chartmodel.py): X, Y,
            # interval, date range, Date column, as the Builders edit them.
            # Only on a chart that has one, so every older widget is stored
            # byte for byte as before.
            **_chart_model(kind, w),
            # How it is drawn: axis titles, a bar's orientation and style,
            # values on the chart (chartmodel.LOOK_FIELDS). Only when set.
            **_chart_look(kind, w),
            # A compiled card showing a live rule's value: which counter it
            # reads (its node's one Date range). Only when not the live
            # value, so every other widget is stored as before.
            **({"window": w["window"]}
               if w.get("window") in ("follow", "today", "week", "month")
               and kind in ("tile", "gauge", "status", "verdict") else {}),
            # A Text or Image card's own fields; only on those kinds, so every
            # other widget is stored byte for byte as before.
            **_static_fields(kind, w),
            **_gallery_fields(kind, w),
            # "Show title", only when set.
            **_show_title(w),
            # Where the stat sits (tile, status, verdict), only when set.
            **_stat_layout(kind, w),
        })
    return out


def _gallery_fields(kind, w):
    """How many snaps a Camera snaps card pages through: 1 to 20, default 10."""
    if kind != "snap_gallery":
        return {}
    try:
        n = int(w.get("count"))
    except (TypeError, ValueError):
        n = SNAP_GALLERY_DEFAULT
    return {"count": max(1, min(SNAP_GALLERY_MAX, n))}


def _static_fields(kind, w):
    """What a Text or an Image card carries, cleaned: plain words cut to
    length, every choice from its whitelist, a picture by its asset id only
    (never a path or a URL). {} for every other kind."""
    from core.services import builder_assets                   # noqa: PLC0415
    if kind == "text_card":
        body = str(w.get("body") or "").replace("\r\n", "\n").replace("\r", "\n")
        # Control characters other than newline and tab are dropped: the
        # words are drawn as text, and a stray NUL or escape is never meant.
        body = "".join(c for c in body if c in "\n\t" or ord(c) >= 32)
        return {"body": body[:MAX_TEXT_BODY],
                "text_size": (w.get("text_size") if w.get("text_size") in TEXT_CARD_SIZES
                              else "medium"),
                "align": w.get("align") if w.get("align") in TEXT_CARD_ALIGNS else "left",
                "bold": bool(w.get("bold")),
                # Above / Middle / Below in the card -- only when set, so a
                # Text card saved before keeps sitting at the top.
                **({"valign": w["valign"]} if w.get("valign") in STAT_VALIGNS else {})}
    if kind == "image_card":
        asset = str(w.get("asset") or "")
        return {"asset": asset if builder_assets.is_asset_id(asset) else "",
                "alt": " ".join(str(w.get("alt") or "").split())[:120],
                "fit": w.get("fit") if w.get("fit") in IMAGE_FITS else "contain",
                "caption": " ".join(str(w.get("caption") or "").split())[:120]}
    return {}


def _chart_render(w):
    """The axis titles and look a chart is drawn with, for the page."""
    from core.services import chartmodel
    if w.get("kind") not in chartmodel.CHART_KINDS:
        return {}
    return {"axis": chartmodel.axis_titles(w),
            **chartmodel.clean_look(w, w.get("kind"))}


def _chart_look(kind, w):
    from core.services import chartmodel
    return chartmodel.clean_look(w, kind)


def _chart_model(kind, w):
    """{"chart": model} for a chart widget carrying the builder model, {}
    otherwise. The renderer reads the LOWERED fields (query, range, interval,
    each series row), which the editors write alongside; this is what they
    read back, so a chart reopens showing exactly what was set."""
    from core.services import chartmodel
    if kind not in chartmodel.CHART_KINDS:
        return {}
    model = chartmodel.clean_widget(w.get("chart"))
    return {"chart": model} if model else {}


def _range_ends(d):
    """{range_from, range_to} for a "custom" range, cleaned; {} otherwise, so
    every other widget's stored shape is byte-identical to what it was."""
    d = d if isinstance(d, dict) else {}
    if d.get("range") != "custom":
        return {}
    return {"range_from": _timeaxis.clean_moment(d.get("range_from")),
            "range_to": _timeaxis.clean_moment(d.get("range_to"))}


def _clean_inject(raw):
    """A button widget's Inject: {fn, ptype, removed}. The FUNCTION id is
    what the page shows the runs of; the node the press goes to is the
    widget's own `node` link, and the route re-checks both against what is
    deployed, so nothing here is trusted to fire anything."""
    raw = raw if isinstance(raw, dict) else {}
    ptype = str(raw.get("ptype") or "")
    return {"fn": re.sub(r"[^A-Za-z0-9_-]", "", str(raw.get("fn") or ""))[:60],
            "ptype": ptype if ptype in ("string", "number", "boolean", "json",
                                        "timestamp") else "",
            # A repeating Inject: its button is Start / Stop.
            "repeat": bool(raw.get("repeat")),
            "removed": bool(raw.get("removed"))}


def _clean_series(series, widget_source="stat", strict=False, widget=None):
    """Clamp a widget's series list. Empty in, empty out -- single-key widgets
    carry no series.

    An entry now names its own SOURCE, so one chart can draw a sampled stat
    against an event query against a dataset column. An entry without one
    means the widget's source, which is what every entry stored before this
    existed meant -- so the migration is a read and no layout has to be
    re-saved. The shape, the whitelists and the cap all live in
    services/chart_series.py, where the resolver that reads them lives.

    `strict` is passed straight through: True (save/publish) refuses a row
    that names nothing askable, False (preview) keeps it as an incomplete
    marker that draws empty and says so. `widget` is the card itself, so an
    entry reading the card's source can read the card's table and question
    (chart_series._inherit) -- the inspector keeps those on the card for such
    a row, and a resolver that never saw the card called the row unfinished."""
    from core.services import chart_series
    return chart_series.migrate(series, widget_source, strict, widget)


#: How far a card's layer may go either way (`grid.z`).
MAX_LAYER = 50


def _clean_grid(g):
    """Clamp a {x,y,w,h} grid cell to the 12-column canvas, or {} when the
    widget predates grid mode (span-flow is the fallback)."""
    if not isinstance(g, dict) or not g:
        return {}
    try:
        x = min(11, max(0, int(g.get("x", 0))))
        w = min(12 - x, max(1, int(g.get("w", 3))))
        y = min(199, max(0, int(g.get("y", 0))))
        h = min(24, max(1, int(g.get("h", 2))))
    except (TypeError, ValueError):
        return {}
    out = {"x": x, "y": y, "w": w, "h": h}
    # The LAYER (Tok Aba, 2026-10-14: bring to front / send to back). Cards
    # on this grid may overlap -- nothing has ever stopped a card being
    # dragged onto another -- and the one drawn on top was simply whichever
    # came later in the list. `z` says it on purpose: higher is in front, a
    # tie keeps the list order. Part of WHERE a card sits, like x / y, so it
    # rides the grid (and a workflow card's placement), never the node; kept
    # only when it is not 0, so every grid saved before is byte for byte the
    # same.
    z = g.get("z")
    if isinstance(z, int) and not isinstance(z, bool) and z:
        out["z"] = min(MAX_LAYER, max(-MAX_LAYER, z))
    return out


#: Tones a verdict state may use -- each maps to a colour+glyph client-side.
VERDICT_TONES = ("ok", "alert", "warn", "empty")


def _clean_states(states):
    """Clamp a verdict widget's custom value->state rows:
    [{value, label, tone}]. Empty in, empty out (legacy compliance card)."""
    if not isinstance(states, list):
        return []
    out, seen = [], set()
    for s in states[:6]:
        if not isinstance(s, dict):
            continue
        try:
            value = float(s.get("value", 0))
        except (TypeError, ValueError):
            continue
        if value in seen:
            continue
        seen.add(value)
        row = {"value": value,
               "label": str(s.get("label") or "").strip()[:24] or "STATE",
               "tone": s.get("tone") if s.get("tone") in VERDICT_TONES else "warn"}
        # An explicit colour beats the tone's stock one. Optional: absent
        # means the tone decides, exactly as before this field existed.
        color = _color(s.get("color"))
        if color:
            row["color"] = color
        out.append(row)
    return out


#: At most four thresholds, so at most five bands. Not an arbitrary number:
#: a chart whose line changes colour six times is a chart nobody reads a
#: threshold off -- past three or four the colours stop meaning "this is the
#: level" and start meaning "this is a rainbow".
MAX_BANDS = 4

#: The default ramp, bottom band first: calm, then warning, then alarm, then
#: two darker alarms for the rare four-threshold case. An operator can set
#: every colour; this is what they get before they do, and it is ordered so
#: the common case (one threshold: fine below, hot above) is right untouched.
BAND_RAMP = ("#16a34a", "#f59e0b", "#dc2626", "#7c2d12", "#450a0a")


def clean_bands(b):
    """Clamp a chart's threshold bands: {at: [numbers], colors: [#rrggbb]}.

    The contract the UI depends on, and the reason this is validated rather
    than trusted: `colors` is ALWAYS exactly len(at) + 1 long. N thresholds cut
    the value axis into N+1 bands, and a renderer that has to guess what colour
    a band is when the list runs short draws a chart whose meaning depends on
    how far the operator got through the form. Short lists are filled from the
    ramp; long ones are cut.

    Thresholds are sorted and de-duplicated, because two bands cannot share an
    edge and "below 10, below 10, above 10" is not a question with an answer.
    """
    # Anything but an object -- a hand-edited graph, an old template -- is no
    # bands at all. It crashed the whole compile on .get before.
    b = b if isinstance(b, dict) else {}
    at = []
    for v in (b.get("at") if isinstance(b.get("at"), list) else [])[:MAX_BANDS * 2]:
        try:
            num = float(v)
        except (TypeError, ValueError):
            continue
        # NaN/inf would travel through JSON as a null or a crash, and a
        # threshold nothing can be either side of is not a threshold.
        if num != num or num in (float("inf"), float("-inf")):
            continue
        at.append(num)
    at = sorted(set(at))[:MAX_BANDS]
    if not at:
        return {}
    given = [c for c in (b.get("colors") if isinstance(b.get("colors"), list)
                         else [])]
    colors = []
    for i in range(len(at) + 1):
        picked = _color(given[i]) if i < len(given) else ""
        colors.append(picked or BAND_RAMP[min(i, len(BAND_RAMP) - 1)])
    return {"at": at, "colors": colors}


def _clean_status(s):
    """Clamp a status widget's alert rule: value <op> threshold."""
    s = s or {}
    op = s.get("op") if s.get("op") in STATUS_OPS else ">="
    try:
        value = float(s.get("value", 1))
    except (TypeError, ValueError):
        value = 1.0
    return {"op": op, "value": value,
            "ok_label": str(s.get("ok_label") or "OK").strip()[:40],
            "alert_label": str(s.get("alert_label") or "ALERT").strip()[:40]}


def _as_query_range(q):
    """The period a statement-backed card runs over, or "" for all time."""
    value = str((q or {}).get("range") or "").strip()
    return value if value in QUERY_RANGES else ""


def _clean_query(q):
    """Clamp a query widget's aggregation spec. Column names are validated
    against the event repo's own whitelist -- the repo is the safety boundary,
    this just keeps the stored spec tidy and within the allowed vocabulary."""
    from core.repos import events as events_repo
    q = q or {}
    def col(name):
        v = str(q.get(name) or "").strip()[:40]
        return v if v in events_repo.FILTERABLE or v == "ts" else ""
    filters = {}
    # The repo's whitelist, ASKED FOR rather than copied. A second hand-kept
    # list lived here and had already drifted: `function` -- the column a
    # wired Event Card scopes itself by, so a card counts the events ONE rule
    # raised -- was not in it, so the filter was dropped on save and the card
    # went on to count every matching event on the board. "Violations from the
    # Zone A rule" showing the whole site's total is not a card that looks
    # broken; it is a card that is confidently wrong, which is worse.
    # Sorted, so a saved layout does not churn on the browser's key order.
    for name in sorted((q.get("filters") or {})):
        if name not in events_repo.FILTERABLE:
            continue
        v = str((q.get("filters") or {}).get(name) or "").strip()[:60]
        if v:
            filters[name] = v
    return {
        "agg": q.get("agg") if q.get("agg") in AGGREGATIONS else "count",
        "field": col("field"),
        "group_by": col("group_by"),
        "bucket": q.get("bucket") if q.get("bucket") in BUCKETS else "",
        "range": q.get("range") if q.get("range") in QUERY_RANGES else "",
        **_range_ends(q),
        "filters": filters,
    }


#: Longest statement a Query node may carry. Generous for a hand-written
#: join, far short of anything that could be a payload.
MAX_SQL = 4000


def _clean_sql(w):
    """Clamp a Query widget: the statement, and the tables it may read.

    What makes this safe is NOT this function. The statement runs as the
    system's read-only role, on a search_path holding that system's views
    alone, with default_transaction_read_only and a statement timeout
    (services/sqlconsole.py) -- so Postgres itself refuses a write, a
    cross-system read or a file access, whatever the text says.

    What happens here is only shape: a statement that is obviously not a
    query is rejected early so the operator gets "only SELECT" from the
    editor instead of an error from the database. Returns {} for a widget
    that is not one."""
    from core.repos import datasets as datasets_repo
    text = str(w.get("sql") or "").strip().rstrip(";").strip()
    if not text or len(text) > MAX_SQL:
        return {}
    if not text.lower().startswith(("select", "with")):
        return {}
    tables = []
    for t in (w.get("tables") or [])[:MAX_QUERY_TABLES]:
        key = str((t or {}).get("key") or "").strip()
        if not datasets_repo.COL_RE.match(key):
            continue
        types = datasets_repo.col_types((t or {}).get("columns"))
        if not types:
            continue
        tables.append({"key": key, "name": str(t.get("name") or key)[:60],
                       "columns": [{"name": n, "type": ty}
                                   for n, ty in types.items()]})
    if not tables:
        return {}
    return {"sql": text, "tables": tables}


def _clean_dataset(w):
    """Clamp a dataset widget: which table, its declared columns, and the
    aggregation over them. Returns {} for a widget that is not one.

    The COLUMNS travel with the widget on purpose. An event widget can be
    checked against events_repo.FILTERABLE, a constant; a dataset's columns are
    named by whoever drew the node, so the declaration IS the whitelist and it
    has to be somewhere the renderer can reach. The workflow compiler stamps it
    on every widget it emits, so a recompile re-states it after a rename."""
    from core.repos import datasets as datasets_repo
    key = str(w.get("dataset") or "").strip()
    if not datasets_repo.COL_RE.match(key):
        return {}
    types = datasets_repo.col_types(w.get("columns"))
    if not types:
        return {}
    q = w.get("query") or {}
    filters = {}
    for name, value in (q.get("filters") or {}).items():
        if name in types and str(value or "").strip():
            filters[name] = str(value).strip()[:60]
    return {
        "dataset": key,
        "columns": [{"name": n, "type": t} for n, t in types.items()],
        "query": {
            "agg": q.get("agg") if q.get("agg") in AGGREGATIONS else "count",
            "field": q.get("field") if q.get("field") in types else "",
            "group_by": q.get("group_by") if q.get("group_by") in types else "",
            "bucket": q.get("bucket") if q.get("bucket") in BUCKETS else "",
            "range": q.get("range") if q.get("range") in QUERY_RANGES else "",
            **_range_ends(q),
            "filters": filters,
        },
    }


#: A card's caption is the operator's line and nothing else.
#:
#: Every data builder used to invent one when none was set -- the aggregate
#: ("count"), the column name, the word "latest" -- so a wall of tiles carried
#: a wall of small grey words nobody wrote, some of them and not others,
#: depending on which builder had produced the card. There was no way to turn
#: one off either: clearing the field just handed the fallback back.
#:
#: The UNIT is not a caption either. It used to stand in for one, so a tile
#: with a unit and no caption printed "pcs" underneath -- while the same card
#: with a caption showed the caption and the unit went nowhere at all. The unit
#: now renders beside the number, where a unit belongs, so this is only ever
#: the operator's own line.
def _caption(w):
    """The caption under a card's value: what was typed, or nothing."""
    return str(w.get("sub") or "").strip()[:60]


def _color(value):
    """Accept #rrggbb only; anything else falls back to the theme accent."""
    value = str(value or "").strip()
    if len(value) == 7 and value[0] == "#":
        try:
            int(value[1:], 16)
            return value
        except ValueError:
            pass
    return ""


# -- rendering ---------------------------------------------------------------

def sources(stats):
    """What the palette offers: which keys this fleet actually publishes.

    Built from live stats rather than a hardcoded list, so a system that ships
    its own bucket gets it in the picker with no product change.
    """
    scalars, buckets = [], []
    for key, value in sorted((stats or {}).items()):
        if isinstance(value, dict):
            buckets.append(key)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            scalars.append(key)
    return {"stat": scalars, "bucket": buckets,
            "events": ["ts", "type", "severity", "label", "device"]}


def _day_epoch(day, end=False):
    """Local epoch for a YYYY-MM-DD day: its midnight, or (end=True) the
    midnight AFTER it -- an inclusive end date covers the whole day."""
    try:
        t = datetime.datetime.strptime(str(day)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    epoch = t.replace(hour=0, minute=0, second=0).astimezone().timestamp()
    return epoch + 86400 if end else epoch


def _page_query(q, page_range, page_start, page_end, now):
    """A recorded-data question whose period is "page", resolved against the
    Analytics date pick: an explicit ISO window for a custom pick, else the
    named period it stands for (today when the surface has no picker)."""
    since = _day_epoch(page_start) if page_range == "custom" else None
    if since is not None:
        until = min(_day_epoch(page_end, end=True) or now, now)
        iso = lambda e: (datetime.datetime.fromtimestamp(e)  # noqa: E731
                         .astimezone().isoformat())
        return {**q, "range": "", "_since": iso(since), "_until": iso(until)}
    return {**q, "range": page_range if page_range in RANGES else "today"}


def _custom_query(q, now):
    """A recorded-data question whose period is "Pick dates...", as the
    explicit ISO window the stores filter on (WHERE ts >= from AND ts < to).
    An unreadable start is all time, said by holding no window."""
    lo, hi = _timeaxis.window("custom", now, q.get("range_from"), q.get("range_to"))
    if lo is None:
        return {**q, "range": ""}
    return {**q, "range": "", "_since": _timeaxis.iso(lo), "_until": _timeaxis.iso(hi)}


def compute(widgets, events, stats, capabilities=(), config_dir=None, slug=None,
            systems_dir=None, page_range="", page_start="", page_end=""):
    """Render layout widgets against the same data package widgets see.

    Returns the shape `Widget.jsx` already knows, so the builder needs no new
    frontend rendering code -- a builder widget and a package widget are
    indistinguishable once they reach the page. `config_dir`/`slug` let a
    "query" widget read the persisted event store. `page_range` (with
    `page_start`/`page_end` when it is "custom") is the Analytics page's own
    date pick, which a widget whose range is "page" follows. The Analytics
    page always sends "custom" + explicit dates -- its Today/Week/Month
    buttons are presets inside the date picker, not separate modes.
    """
    now = time.time()
    out = []
    ai = _ai_off_info(systems_dir, slug)
    paused = controls_service.paused_info(systems_dir, slug)   # Class Filters off
    for w in widgets or []:
        if w.get("capability") and w["capability"] not in set(capabilities):
            continue
        if w.get("range") == "page":
            # Resolved at render, so the stored widget keeps saying "follow
            # the page" rather than freezing whichever period was picked the
            # day it was saved. On a surface with no picker (the Dashboard),
            # "page" reads as today -- the day-aligned window is the honest
            # default for a page that shows the present.
            since = _day_epoch(page_start) if page_range == "custom" else None
            until = _day_epoch(page_end, end=True) if page_range == "custom" else None
            if since is not None:
                # An end date in the future (or today) charts up to NOW --
                # the right edge of "until today" is this moment, not
                # tonight's empty hours.
                until = min(until or now, now)
                w = {**w, "range": "custom",
                     "_since": since, "_until": max(until, since + 60)}
            else:
                w = {**w, "range": _PAGE_RANGE.get(page_range or "today", "24h")}
        # "Pick dates...": the chart's own exact window (services/timeaxis),
        # resolved the way a custom Analytics pick is.
        # (A "page" already resolved above is "custom" with its window set.)
        if w.get("range") == "custom" and "_since" not in w:
            lo, hi = _timeaxis.window("custom", now, w.get("range_from"),
                                      w.get("range_to"))
            if lo is not None:
                w = {**w, "_since": lo, "_until": hi}
            else:
                w = {**w, "range": "24h"}
        # The DATABASE-backed twin: a dataset/query widget whose QUERY says
        # "page" follows the same date pick, as an ISO window on the rows.
        q = w.get("query") or {}
        if q.get("range") == "page":
            w = {**w, "query": _page_query(q, page_range, page_start,
                                           page_end, now)}
        elif q.get("range") == "custom":
            w = {**w, "query": _custom_query(q, now)}
        # ...and the same for each SERIES of a chart. A series carries its
        # own question, so a "page" on it was never resolved and the store
        # read all time -- a chart following the Analytics range with two
        # table inputs drew every day ever recorded.
        if isinstance(w.get("series"), list) and any(
                isinstance(s, dict) and isinstance(s.get("query"), dict)
                and s["query"].get("range") in ("page", "custom")
                for s in w["series"]):
            w = {**w, "series": [
                {**s, "query": (_page_query(s["query"], page_range, page_start,
                                            page_end, now)
                                if s["query"].get("range") == "page"
                                else _custom_query(s["query"], now))}
                if isinstance(s, dict) and isinstance(s.get("query"), dict)
                and s["query"].get("range") in ("page", "custom") else s
                for s in w["series"]]}
        try:
            data = _data(w, events, stats, config_dir, slug, systems_dir)
        except Exception:                       # noqa: BLE001
            data = None                         # renders "widget failed"
        if ai and isinstance(data, dict) and w.get("kind") not in (
                MEDIA_KINDS + CONTROL_KINDS + STATIC_KINDS):
            data = _mark_ai_off(w, data, stats, ai)
        if paused and isinstance(data, dict) and not data.get("ai_off") \
                and w.get("kind") not in MEDIA_KINDS + CONTROL_KINDS + STATIC_KINDS:
            data = controls_service.mark_paused(w, data, stats, paused)
        out.append({"id": w["id"], "title": w["title"], "kind": w["kind"],
                    "span": w.get("span", 1), "grid": w.get("grid") or {},
                    # The dressing. This dict is the ONLY shape the pages ever
                    # see -- leaving `template` off here was how a treatment
                    # could be stored, compiled, previewed in the inspector and
                    # still render plain everywhere: Widget.jsx reads it off
                    # the widget, and the widget arrived without it.
                    "template": (w.get("template") if w.get("template") in TEMPLATES
                                 else "plain"),
                    "anim": (w.get("anim") if w.get("anim") in ANIMATIONS
                             else ""),
                    "anim_replay": bool(w.get("anim_replay")),
                    # Same reason as `template` above: the bands are stored on
                    # the widget and read off THIS dict, so leaving them out
                    # here renders a banded chart as a plain one everywhere
                    # except the inspector.
                    "bands": (clean_bands(w.get("bands"))
                              if w.get("kind") in BAND_KINDS else {}),
                    # Same trap as `template` and `bands` above: stored on the
                    # widget, read off THIS dict. Left out here, a chart set to
                    # colour by day renders in one colour everywhere except the
                    # inspector that set it.
                    "by_day": (bool(w.get("by_day"))
                               if w.get("kind") in DAY_COLOUR_KINDS else False),
                    "bg": _color(w.get("bg")),
                    # The ACCENT -- a chart's stroke, a tile's accent paint --
                    # and how big the card's words are. Same trap as
                    # `template` above, and it caught all three: the value was
                    # stored, overridden and previewed correctly, and then this
                    # dict handed the page a widget that never mentioned it, so
                    # every colour and size chosen on Analytics rendered as the
                    # default. Anything a card is DRAWN from has to be listed
                    # here or it does not exist as far as the page is
                    # concerned.
                    "color": _color(w.get("color")),
                    "label_size": _as_text_size(w.get("label_size")),
                    "value_size": _as_text_size(w.get("value_size")),
                    # The same trap as `template`, `bands` and `by_day`, and
                    # it swallowed this field whole: "Show the value as" was
                    # stored, compiled and previewed in the inspector, and
                    # this dict -- the ONLY shape the Dashboard, Analytics
                    # and the Builder preview ever see -- never mentioned it.
                    # So YES / NO, ON / OFF and TRUE / FALSE had never done
                    # anything on a real dashboard, on any page, since the
                    # day they were added.
                    "format": (w.get("format") if w.get("format") in VALUE_FORMATS
                               else ""),
                    # A chart's axes, drawn clearly (rendering addendum): what
                    # they mean, from the configuration, and the look the
                    # operator set -- same trap as every field above, this
                    # dict is all the page ever sees.
                    **_chart_render(w),
                    # A Text / Image card is drawn from its own fields.
                    **_static_fields(w.get("kind"), w),
                    # ...and whether the title bar is drawn: same trap as
                    # every field above, this dict is all the page sees.
                    **_show_title(w),
                    # ...and where the stat sits in the card.
                    **_stat_layout(w.get("kind"), w),
                    "data": data, "builder": True})
    return out


#: Stat keys only a camera and the detector fill (mirrors the app's
#: run._FRAME_KEY_PREFIXES): with the AI off the app publishes none of them.
AI_FRAME_PREFIXES = ("zone_", "line_", "objects_", "violations_", "cam_")


def _ai_off_info(systems_dir, slug):
    """With the AI off and the app up: {inactive, kept, fns, cams} -- the ids
    of the rules sitting out, the keys that are their KEPT tallies, every
    function id and camera id of the system (for exact key ownership).
    None otherwise. Never raises: a dashboard that cannot tell draws what it
    has."""
    if not systems_dir or not slug:
        return None
    try:
        from core.services import detector_control as dc      # noqa: PLC0415
        if not dc.status(systems_dir, slug).get("logic_running"):
            return None
        raw = dc.app_state(systems_dir, slug) or {}
        cfg = dc.system_config(systems_dir, slug) or {}
    except Exception:                                          # noqa: BLE001
        return None
    inactive = frozenset(str(i) for i in (raw.get("ai_inactive") or ()))
    fns = {str(f.get("id") or "") for f in (cfg.get("functions") or [])
           if isinstance(f, dict)} | inactive
    cams = {str(c.get("id") or "") for c in (cfg.get("cameras") or [])
            if isinstance(c, dict)} | {"main"}
    return {"inactive": inactive, "kept": frozenset(raw.get("ai_kept") or ()),
            "fns": frozenset(i for i in fns if i),
            "cams": frozenset(c for c in cams if c)}


def key_owner(key, fns, cams=()):
    """The function a stat key belongs to, or None -- EXACTLY: the longest
    function id whose "fn_<id>_" prefixes the key, so rule "ppl" does not
    own "fn_ppl_extra_count" when a rule "ppl_extra" exists. A per-camera
    copy (cam_<camera>_fn_<id>_...) belongs to the same function."""
    k = str(key or "")
    for cam in sorted(cams, key=len, reverse=True):
        if k.startswith(f"cam_{cam}_fn_"):
            k = k[len(cam) + 5:]
            break
    if not k.startswith("fn_"):
        return None
    best = None
    for fid in fns:
        if k.startswith(f"fn_{fid}_") and (best is None or len(fid) > len(best)):
            best = fid
    return best


def _widget_keys(w):
    keys = [w.get("key")]
    keys += [(s or {}).get("key") for s in (w.get("series") or [])
             if isinstance(s, dict)]
    if w.get("kind") == "verdict" and not w.get("key"):
        keys.append(COMPLIANCE_KEY)
    out = []
    for k in keys:
        k = str(k or "").strip()
        if k and k not in out:
            out.append(k)
    return out


def ai_split(w, stats, ai):
    """(off, kept) keys of widget `w` with the AI off.

    `off`: ABSENT because the AI is off -- a camera key, the compliance
    verdict, or a live value of a rule a Detection Stream feeds. `kept`:
    PRESENT, but a held tally of such a rule (shown as it stands, badged).
    A key a free rule publishes, the master's own event counters, and a key
    nothing publishes yet are neither."""
    off, kept = [], []
    for k in _widget_keys(w):
        owner = key_owner(k, ai["fns"], ai["cams"])
        fed = owner is not None and owner in ai["inactive"]
        if k in (stats or {}):
            if fed or k in ai["kept"]:
                kept.append(k)
            continue
        frame = owner is None and (k == COMPLIANCE_KEY
                                   or k.startswith(AI_FRAME_PREFIXES))
        if fed or frame:
            off.append(k)
    return off, kept


def ai_off_keys(w, stats, ai):
    """The keys of `w` that are absent because the AI is off (ai_split)."""
    return ai_split(w, stats, ai)[0] if ai else []


def _mark_ai_off(w, data, stats, ai):
    """Say "AI OFFLINE" on a card, as much as is true of it.

    Every key gone with the AI -> the whole card is the AI-off state (a
    chart keeps its history under a badge). Some keys, or a kept tally ->
    the real numbers stay, the gone ones are blanked, and a badge says why.
    """
    off, kept = ai_split(w, stats, ai)
    if not off and not kept:
        return data
    keys = _widget_keys(w)
    data = {**data, "ai_off_keys": off, "ai_kept_keys": kept}
    whole = bool(off) and len(off) == len(keys)
    if whole:
        data["ai_off"] = True
        if w.get("kind") in ("verdict", "status"):
            data.update(state="unknown", label="AI OFFLINE", value=None)
    if not whole or w.get("kind") in CHART_KINDS:
        data["ai_badge"] = True
    # A comparison chart's bars for the gone keys: blank, and labelled --
    # never a 0 standing in for "the camera is released".
    if off and isinstance(data.get("values"), list) and isinstance(
            data.get("ids"), list):
        by_id = {str((s or {}).get("id") or ""): str((s or {}).get("key") or "")
                 for s in (w.get("series") or []) if isinstance(s, dict)}
        values, labels = list(data["values"]), list(data.get("labels") or [])
        for i, sid in enumerate(data["ids"]):
            if by_id.get(str(sid)) in off:
                values[i] = None
                if i < len(labels):
                    labels[i] = f"{labels[i]} (AI OFFLINE)"
        data["values"], data["labels"] = values, labels
    return data


_STATUS_CMP = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def _range_floor(range_key):
    """ISO timestamp for the start of today/week/month, or None."""
    days = RANGES.get(range_key)
    if not days:
        # The chart time axis's rolling ranges ("Last 6 hours", "Past 3
        # months", services/timeaxis.py): the window's start, filtered in the
        # query itself. "custom" and "page" are resolved by compute() into
        # _since/_until before anything asks this.
        if range_key in _timeaxis.ROLLING:
            since, _until = _timeaxis.window(range_key)
            return _timeaxis.iso(since)
        return None
    floor = datetime.datetime.now().astimezone() - datetime.timedelta(days=days - 1)
    return floor.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _previous_window(range_key):
    """(since, until) for the period BEFORE `range_key`, or (None, None).

    "Up 3% on last month" needs the same length of time ending where the
    current window starts -- the week before this week, not "some earlier
    rows". Returned as a pair rather than a floor because the previous window
    is the only one in this module that is CLOSED at both ends: everything
    else runs from a floor up to now.

    All-time has no previous period. Saying so with (None, None) is the
    honest answer; comparing all-time against itself would report 0% forever
    and read as "nothing changed".
    """
    days = RANGES.get(range_key)
    if not days:
        return None, None
    now = datetime.datetime.now().astimezone()
    start = (now - datetime.timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (start - datetime.timedelta(days=days)).isoformat(), start.isoformat()


def _delta_is_good(w, pct):
    """Whether a change in this direction is GOOD news.

    Not inferable from the sign. A falling violation count, a falling response
    time and a falling refund total are all improvements, and colouring them
    red because the number went down would report a success as a problem. So
    the widget says which way is up: `lower_is_better`, off by default because
    most things a camera counts (people served, items packed) are better
    higher.
    """
    return (pct < 0) if w.get("lower_is_better") else (pct >= 0)


def _delta(value, was):
    """The change from `was` to `value`, as the tile renders it, or None.

    None whenever the comparison would be a claim rather than a measurement:
    no previous period, or a previous value of zero -- "up from nothing" is
    not a percentage, and reporting it as one (or as a division error) is
    worse than showing no badge at all.
    """
    try:
        now_v, was_v = float(value), float(was)
    except (TypeError, ValueError):
        return None
    if was_v == 0:
        return None
    pct = (now_v - was_v) / abs(was_v) * 100.0
    return {"pct": round(pct, 1),
            # Rounded the way it is displayed, so the badge and the caption
            # can never disagree about which way it went.
            "was": round(was_v, 2) if was_v % 1 else int(was_v)}


def _bucket_key(ts, bucket):
    """Truncate an ISO timestamp to its hour/day/week/month bucket key --
    or to a fixed "<n>s"/"<n>m" interval, floored on the epoch exactly as
    the SQL path floors it, so the fallback buckets agree with Postgres."""
    s = str(ts or "")
    from core.repos import events as events_repo
    secs = events_repo.bucket_seconds(bucket)
    if secs:
        try:
            epoch = datetime.datetime.fromisoformat(s).timestamp()
        except ValueError:
            return s
        # An hour or more floors on LOCAL midnight, as the SQL does
        # (events_repo._floor_expr), so the two stores agree on the edges.
        off = int(time.localtime(epoch).tm_gmtoff or 0) if secs >= 3600 else 0
        return (datetime.datetime
                .fromtimestamp((epoch + off) // secs * secs - off).astimezone()
                .isoformat())
    if bucket == "hour":
        return s[:13] + ":00" if len(s) >= 13 else s
    if bucket == "month":
        return s[:7]
    if bucket == "week":
        try:
            d = datetime.date.fromisoformat(s[:10])
            return (d - datetime.timedelta(days=d.weekday())).isoformat()
        except ValueError:
            return s[:10]
    return s[:10]                       # day (and the safe default)


def _agg_value(rows, agg, field):
    if agg == "count" or not field:
        return len(rows)
    values = [r.get(field) for r in rows if r.get(field) is not None]
    if agg == "count_nonnull":
        # The rows where the column holds a value -- an empty string is no
        # value either, the way a form leaves a field blank.
        return sum(1 for v in values if v != "")
    if agg == "unique":
        # Different values; a blank is no value (the SQL agrees: NULLIF).
        return len({v for v in values if v != ""})
    if agg == "changes":
        # Mirrors the window query in datasets_repo.aggregate: nulls (and
        # blanks) are dropped above rather than counted as a value, and the
        # first value counts -- the first object through is one object, not
        # zero. Within THESE rows; a chart's buckets use changes_by.
        values = [v for v in values if v != ""]
        return sum(1 for i, v in enumerate(values)
                   if i == 0 or v != values[i - 1])
    numbers = [v for v in values
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return 0
    if agg == "sum":
        return sum(numbers)
    if agg == "avg":
        return sum(numbers) / len(numbers)
    if agg == "min":
        return min(numbers)
    if agg == "max":
        return max(numbers)
    return len(rows)


def changes_by(rows, field, key_of, partition=False):
    """{key: changes}: "Count Value Changes" over rows ALREADY in time order,
    each change put in the bucket (key) of the row where the new value
    appears. The look-back runs over the whole series (partition=False, a
    time axis: a change at a bucket boundary counts once, where it happens,
    so merged buckets add up exactly) or inside each key (partition=True,
    a category axis). Nulls and blanks are no value; the first value counts.
    A row whose key is None (no date) is skipped. Mirrors the SQL window
    query of both stores."""
    prev, out = {}, {}
    for r in rows:
        v = r.get(field)
        if v is None or v == "":
            continue
        k = key_of(r)
        if k is None:
            continue
        pk = k if partition else None
        if pk not in prev or prev[pk] != v:
            out[k] = out.get(k, 0) + 1
        prev[pk] = v
    return out


def _query_local(w, events, slug):
    """The same aggregation a query widget asks Postgres for, computed over the
    master's in-memory event ring. The fallback when the event store is down or
    absent -- a dashboard whose detections are visibly flowing must not render
    zeros just because Postgres is."""
    from core.repos import events as events_repo
    q = w.get("query") or {}
    since = q.get("_since") or _range_floor(q.get("range"))
    until = q.get("_until") or None
    filters = q.get("filters") or {}
    rows = []
    for ev in events or []:
        row = events_repo.expand(ev, slug)
        if since and str(row.get("ts") or "") < since:
            continue
        if until and str(row.get("ts") or "") >= until:
            continue
        if any(str(row.get(col)) != str(val) for col, val in filters.items()):
            continue
        rows.append(row)

    agg, field = q.get("agg", "count"), q.get("field") or None
    if w["kind"] == "tile":
        return {"value": _agg_value(rows, agg, field),
                "sub": _caption(w),
                "color": w.get("color") or None,
                "unit": w.get("unit") or None}

    grouped = {}
    if q.get("bucket"):
        for row in rows:
            grouped.setdefault(_bucket_key(row.get("ts"), q["bucket"]), []).append(row)
    elif q.get("group_by"):
        for row in rows:
            key = row.get(q["group_by"])
            if key is not None:
                grouped.setdefault(str(key), []).append(row)
    else:
        grouped = {"": rows}
    # Grouped by a column, the bars are NAMES and go in name order; a time
    # bucket's ISO keys already sort as time.
    keys = sorted(grouped, key=name_key if q.get("group_by")
                  and not q.get("bucket") else None)
    return {"labels": [_short_label(k) for k in keys],
            "values": [_agg_value(grouped[k], agg, field) for k in keys],
            "color": w.get("color") or None, "unit": w.get("unit") or None}


def _query_data(w, config_dir, slug, events=None):
    """A query widget: aggregate the persisted event store. A tile with no
    grouping is a scalar; a chart groups by a column or a time bucket. With no
    store reachable, the in-memory ring answers instead (see _query_local)."""
    from core.repos import events as events_repo
    if not events_repo.available(config_dir):
        return _query_local(w, events, slug)
    q = w.get("query") or {}
    since = q.get("_since") or _range_floor(q.get("range"))
    until = q.get("_until") or None
    filters = q.get("filters") or {}
    if w["kind"] == "tile":
        res = events_repo.aggregate(slug, agg=q.get("agg", "count"), field=q.get("field") or None,
                                    filters=filters, since=since, until=until,
                                    config_dir=config_dir)
        value = res["buckets"][0]["value"] if res.get("buckets") else 0
        return {"value": value, "sub": _caption(w),
                "color": w.get("color") or None}
    res = events_repo.aggregate(slug, agg=q.get("agg", "count"), field=q.get("field") or None,
                                group_by=q.get("group_by") or None, bucket=q.get("bucket") or None,
                                filters=filters, since=since, until=until,
                                config_dir=config_dir)
    buckets = by_name(res.get("buckets") or [], q)
    return {"labels": [_short_label(b["key"]) for b in buckets],
            "values": [b["value"] for b in buckets],
            "color": w.get("color") or None, "unit": w.get("unit") or None}


def _verdict_state(w, value):
    """A verdict card reading a table needs a state, not just a number. Uses
    the card's own value->state list when it has one, else the threshold it
    carries, else nothing -- a card with no rule to apply says so rather than
    picking a colour at random."""
    states = w.get("states") or []
    if states:
        match = next((s for s in states if value is not None
                      and s["value"] == value), None)
        return match["tone"] if match else "unknown"
    rule = w.get("status") or {}
    if not rule or value is None:
        return "unknown"
    try:
        hit = _STATUS_CMP.get(rule.get("op", ">="), _STATUS_CMP[">="])(
            float(value), float(rule.get("value", 1)))
    except (TypeError, ValueError):
        return "unknown"
    return "alert" if hit else "ok"


def _verdict_color(w, value):
    """The matched state's own colour, or "" for "the tone decides"."""
    for s in w.get("states") or []:
        if value is not None and s.get("value") == value and s.get("color"):
            return s["color"]
    return ""


def _verdict_label(w, value):
    states = w.get("states") or []
    if states:
        match = next((s for s in states if value is not None
                      and s["value"] == value), None)
        if match:
            return match["label"]
    state = _verdict_state(w, value)
    rule = w.get("status") or {}
    if state == "alert":
        return rule.get("alert_label") or "ALERT"
    if state == "ok":
        return rule.get("ok_label") or "OK"
    return "NO DATA"


def _numeric(value):
    """`value` as a float, or None if it is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


#: A heatmap cell key: `<prefix>_c<row>_<col>`, published one per cell by the
#: heatmap function (systems/demo/app/functions.py::_update_heatmap).
_HEAT_CELL = re.compile(r"^(?P<prefix>.+)_c(?P<row>\d+)_(?P<col>\d+)$")


def _heat_prefix(key):
    """The stat prefix a heatmap widget's cells share.

    The widget is bound like every other one -- to a KEY -- but a heatmap does
    not read one key, it reads a few hundred. So whatever the operator picked
    is reduced to the family it belongs to: a cell key drops its cell, and the
    map's own two readings (`hot`, `spots`) drop their metric. That way the
    card works whether it was dragged from the canvas (which binds `_hot`) or
    bound by hand from the key picker (which lists the cells).
    """
    key = str(key or "").strip()
    cell = _HEAT_CELL.match(key)
    if cell:
        return cell.group("prefix")
    for suffix in ("_hot", "_spots"):
        if key.endswith(suffix):
            return key[:-len(suffix)]
    return key


def _heat_data(w, stats):
    """A heatmap: the cell stats of one function, as a grid.

    The GRID's shape is read off the keys rather than off the widget, so a map
    resized on the canvas redraws at its new shape without the card being
    rewritten -- the function republishes different keys and this follows.
    Missing cells read 0: the engine publishes every cell, so a gap means the
    map has not been built that far yet, not that the card is broken.
    """
    prefix = _heat_prefix(w.get("key"))
    if not prefix:
        return {"grid": [], "rows": 0, "cols": 0, "max": 0}
    pattern = re.compile(r"^" + re.escape(prefix) + r"_c(\d+)_(\d+)$")
    cells = {}
    for k, v in (stats or {}).items():
        hit = pattern.match(str(k))
        value = _numeric(v) if hit else None
        if value is not None:
            cells[(int(hit.group(1)), int(hit.group(2)))] = value
    if not cells:
        return {"grid": [], "rows": 0, "cols": 0, "max": 0,
                "color": w.get("color") or None}
    rows = max(r for r, _c in cells) + 1
    cols = max(c for _r, c in cells) + 1
    grid = [[cells.get((r, c), 0.0) for c in range(cols)] for r in range(rows)]
    return {"grid": grid, "rows": rows, "cols": cols,
            "max": max(max(row) for row in grid),
            # The two readings ABOUT the map, so the card can caption itself
            # without a second widget beside it.
            "value": _numeric((stats or {}).get(f"{prefix}_hot")),
            "spots": _numeric((stats or {}).get(f"{prefix}_spots")),
            "color": w.get("color") or None,
            "unit": w.get("unit") or None}


def _sql_window(w):
    """What to bind into a statement that names a period, or None.

    None for the statements that name none, and that is not tidiness: psycopg2
    treats a literal % as a format directive the moment ANY parameters are
    passed, so handing {} to a statement containing TO_CHAR(x, 'HH12:MI AM')
    turns a working card into "unsupported format character".

    The window is the card's own Period, resolved the same way a Data Table
    card's is -- compute() has already turned "follow the Analytics range"
    into _since/_until against the date picker by the time this runs. All time
    still binds SOMETHING, because a statement that asks for a period cannot
    run without one: the epoch to now is the honest reading of "all of it".
    """
    from core.services import sqlconsole                      # noqa: PLC0415
    if not sqlconsole.wants_dates(w.get("sql")):
        return None
    q = w.get("query") or {}
    now = datetime.datetime.now().astimezone()
    since = q.get("_since") or _range_floor(q.get("range")) or _EPOCH_ISO
    until = q.get("_until") or (now + datetime.timedelta(days=1)).isoformat()
    return {"from": since, "to": until}


def _sql_data(w, config_dir, slug):
    """A Query widget: run the operator's statement and shape the result.

    The statement decides WHAT the numbers are; the card decides how they are
    drawn. So the shaping here is positional and stated plainly rather than
    guessed at per widget:

        table   every column, as returned
        chart   first column labels, first NUMERIC column after it the values
        tile    the first numeric cell, or the first cell if none is numeric

    Positional because a hand-written query has no schema to introspect --
    `SELECT zone, count(*)` names its columns whatever the author likes, and
    the one thing that is always true is the order they asked for them in.

    A failed query degrades to an empty widget carrying its error, exactly as
    the dataset path degrades when Postgres is down: one broken card must not
    take the dashboard with it."""
    from core.services import sqlconsole                      # noqa: PLC0415
    # sqlconsole works in the registry's shape -- {key: table} -- because that
    # is how the tables are stored; a widget carries a LIST, because a widget
    # is a document and lists round-trip through JSON in a stable order. The
    # conversion belongs here, at the boundary between the two.
    tables = {t["key"]: t for t in (w.get("tables") or []) if t.get("key")}
    res = sqlconsole.run(slug, w.get("sql"), tables, config_dir=config_dir,
                         params=_sql_window(w))
    if not res.get("ok"):
        # Surfaced on the card rather than logged: the person who can fix a
        # bad query is the one looking at it.
        # The colour travels on EVERY branch, this one included. A card whose
        # query failed still has to be the colour it was set to -- dropping it
        # here is how a red tile turned blue the moment its statement broke,
        # which reads as "the colour setting does nothing".
        return {"error": str(res.get("error") or "the query failed")[:200],
                "columns": [], "rows": [], "labels": [], "values": [],
                "value": None, "color": w.get("color") or None,
                "unit": w.get("unit") or None}
    columns = res.get("columns") or []
    rows = res.get("rows") or []
    kind = w.get("kind")

    if kind == "table":
        return {"columns": [c.replace("_", " ") for c in columns],
                "rows": [[r.get(c) for c in columns] for r in rows],
                "truncated": bool(res.get("truncated"))}

    if not columns:
        return {"labels": [], "values": [], "value": None,
                "color": w.get("color") or None,
                "unit": w.get("unit") or None}

    if kind in CHART_KINDS:
        label_col = columns[0]
        value_col = next(
            (c for c in columns[1:]
             if any(_numeric(r.get(c)) is not None for r in rows)),
            columns[1] if len(columns) > 1 else columns[0])
        return {"labels": [str(r.get(label_col)) for r in rows],
                "values": [_numeric(r.get(value_col)) or 0 for r in rows],
                "color": w.get("color") or None,
                "unit": w.get("unit") or None}

    # A tile: one number. The first numeric cell of the first row, because a
    # query written for a tile is "SELECT count(*)" and anything else the
    # author put first is a label for it.
    first = rows[0] if rows else {}
    value, sub = None, _caption(w)
    for c in columns:
        value = _numeric(first.get(c))
        if value is not None:
            # The column's name is NOT a caption. It is the query author's
            # word for a field, and putting it under the number wrote a label
            # nobody chose onto every card built from a statement.
            break
    if value is None:
        value = first.get(columns[0]) if first else None
    return {"value": value, "sub": sub, "color": w.get("color") or None,
            "unit": w.get("unit") or None}


def _dataset_data(w, config_dir, slug, systems_dir=None):
    """A dataset widget: the operator's own table, as rows or as a chart.

    There is no in-memory fallback here (unlike a query widget, which can
    aggregate the master's event ring): dataset rows exist only once ingested,
    so with the store down the honest answer is an empty table."""
    from core.repos import datasets as datasets_repo
    name = w.get("dataset") or ""
    # The widget carries a SNAPSHOT of the table's columns, stamped when it
    # was compiled or dragged in. A column deleted since then is still in
    # that snapshot -- and the rows keep their old keys in `cols` JSONB, so
    # the deleted column went on rendering with its old values for ever.
    #
    # The table's CURRENT definition is the authority, so the snapshot is
    # narrowed to it: a column the table no longer declares is not a column.
    # Without a systems_dir (an older caller) the snapshot stands, which is
    # exactly today's behaviour rather than an empty table.
    cols = w.get("columns") or []
    if systems_dir:
        try:
            from core.services import datasets as datasets_service
            live = datasets_service.definition(systems_dir, slug, name)
            if live:
                keep = {c["name"] for c in live.get("columns") or []}
                cols = [c for c in cols if c.get("name") in keep]
        except Exception:                                   # noqa: BLE001
            pass            # unreadable definition: show what the widget has
    q = w.get("query") or {}
    # An explicit window (the Analytics date pick, injected by compute() for
    # "follow the page" queries) beats the named range.
    since = q.get("_since") or _range_floor(q.get("range"))
    until = q.get("_until") or None
    filters = q.get("filters") or {}

    if w["kind"] == "table":
        res = datasets_repo.query(slug, name, cols, filters=filters, since=since,
                                  until=until,
                                  limit=w.get("limit", 15), config_dir=config_dir)
        names = [c["name"] for c in cols]
        kinds = {c["name"]: c.get("type") for c in cols}

        def cell(row, name):
            value = row.get(name)
            if value is None:
                return ""
            # A datetime is stored ISO so it sorts; a card is read by a person,
            # so drop the T and the sub-second tail that only a machine wants.
            if kinds.get(name) == "datetime":
                return str(value)[:19].replace("T", " ")
            return str(value)

        # "Datetime", as the Database page names the time (Tok Aba, 2026-10-14;
        # was "When"). table_export.columns_of must say the same.
        return {"columns": ["Datetime"] + [n.replace("_", " ") for n in names],
                "rows": [[str(r.get("ts") or "")[:19].replace("T", " ")]
                         + [cell(r, n) for n in names]
                         for r in res.get("rows") or []]}

    agg, field = q.get("agg", "count"), q.get("field") or None

    # "latest" is the newest value, not an aggregate -- see AGGREGATIONS.
    if agg == "latest" and field:
        # Grouped, this is "the current reading for each machine" -- one
        # latest per distinct value, which neither max() nor a plain latest
        # can answer.
        if q.get("group_by"):
            rows = by_name(datasets_repo.latest(slug, name, cols, field,
                                                filters=filters,
                                                since=since, until=until,
                                                group_by=q["group_by"],
                                                config_dir=config_dir) or [], q)
            if w["kind"] == "table":
                return {"columns": [q["group_by"], f"latest {field}"],
                        "rows": [[r["key"], r["value"]] for r in rows]}
            return {"labels": [_short_label(r["key"]) for r in rows],
                    "values": [r["value"] for r in rows],
                    "color": w.get("color") or None, "unit": w.get("unit") or None}
        value = datasets_repo.latest(slug, name, cols, field, filters=filters,
                                     since=since, until=until,
                                     config_dir=config_dir)
        if w["kind"] in ("tile", "gauge", "status", "verdict"):
            return {"value": value, "state": _verdict_state(w, value),
                    "label": _verdict_label(w, value),
                    "state_color": _verdict_color(w, value),
                    "sub": _caption(w),
                    "max": float(w.get("status", {}).get("value") or 100),
                    "unit": w.get("unit") or None,
                    "color": w.get("color") or None}
        return {"labels": ["latest"], "values": [value],
                "color": w.get("color") or None, "unit": w.get("unit") or None}

    if w["kind"] in ("tile", "gauge", "verdict", "status"):
        res = datasets_repo.aggregate(slug, name, cols, agg=agg, field=field,
                                      filters=filters, since=since, until=until,
                                      config_dir=config_dir)
        buckets = res.get("buckets") or []
        value = buckets[0]["value"] if buckets else 0
        out = {"value": value, "state": _verdict_state(w, value),
               "label": _verdict_label(w, value),
               "state_color": _verdict_color(w, value),
               "max": float(w.get("status", {}).get("value") or 100),
               "unit": w.get("unit") or None,
               "sub": _caption(w),
               "color": w.get("color") or None}
        # "vs the period before" -- asked for per widget, because it costs a
        # SECOND aggregate and most tiles do not want one. Only meaningful
        # over a bounded range: all-time has nothing before it.
        if w.get("compare") and q.get("range"):
            prev_since, prev_until = _previous_window(q.get("range"))
            if prev_since:
                prev = datasets_repo.aggregate(
                    slug, name, cols, agg=agg, field=field, filters=filters,
                    since=prev_since, until=prev_until, config_dir=config_dir)
                prev_buckets = prev.get("buckets") or []
                delta = _delta(value, prev_buckets[0]["value"] if prev_buckets else 0)
                if delta:
                    out["delta"] = {**delta, "since": _PREV_LABEL.get(q["range"], "before"),
                                    "good": _delta_is_good(w, delta["pct"])}
        return out
    res = datasets_repo.aggregate(slug, name, cols, agg=agg, field=field,
                                  group_by=q.get("group_by") or None,
                                  bucket=q.get("bucket") or None,
                                  filters=filters, since=since, until=until,
                                  config_dir=config_dir)
    buckets = by_name(res.get("buckets") or [], q)
    return {"labels": [_short_label(b["key"]) for b in buckets],
            "values": [b["value"] for b in buckets],
            "color": w.get("color") or None, "unit": w.get("unit") or None}


#: What a group with NO value -- a NULL key -- is called on an axis.
BLANK_LABEL = "(blank)"


def name_key(key):
    """The order a person reads a list of NAMES in: case-insensitive, and a
    run of digits compared as a number -- "a2" before "a10", "Line 9" before
    "line 10".

    The stores cannot give this. Postgres orders a text key by the database's
    collation (byte order under "C", case-folded under en_US -- a box and a
    laptop can disagree), and Python's sorted() is byte order, where every
    capital comes before every lower-case letter. A chart's categories were
    therefore in whichever order the store happened to use, and a bar chart
    of defect types read "Scratch, crack, dent". One rule, applied in Python
    after the store answers, is what every grouped chart now uses
    (chart_series._label_data too).

    Three ranks, in this order: a MISSING value (a NULL group, drawn as
    "(blank)"), then labels that ARE numbers -- ordered as numbers, so -10
    before -5 before 0 and 1.25 before 1.3 -- then everything else in natural
    order. Ties -- "Dent" and "dent" -- fall back to the raw text, so the order
    is total and a chart never reshuffles between two polls.
    """
    if key is None or str(key).strip() == "":
        return (0, 0.0, [], "")
    s = str(key)
    try:
        num = float(s)
    except ValueError:
        num = None
    # "nan" and "inf" parse, and are words to a person reading an axis.
    if num is not None and num == num and num not in (float("inf"),
                                                      float("-inf")):
        return (1, num, [], s)
    parts = [p for p in re.split(r"(\d+)", s.casefold()) if p]
    return (2, 0.0, [(0, int(p)) if p.isdigit() else (1, p) for p in parts], s)


def by_name(buckets, q):
    """[{key, value}] from a store, in name order when they are grouped by a
    COLUMN. A time bucket is left in the store's order, which is time."""
    if (q or {}).get("group_by") and not (q or {}).get("bucket"):
        return sorted(buckets, key=lambda b: name_key(b.get("key")))
    return buckets


def _short_label(key):
    """One chart label from one group key: a date, a name, or a number.

    Three things arrive here and each reads wrong untouched. An ISO timestamp
    is 26 characters of machine punctuation. A DAY bucket carries a midnight
    nobody asked about ("09-03 00:00" for a whole day). And a number column
    grouped over comes back from the database as a float, so a 0/1 column
    labels its bars "0.0" and "1.0" -- which is how an axis ends up reading
    like a debugger rather than a chart.
    """
    # A NULL group: the rows that have no value in that column. Said, rather
    # than printed as Python's "None" and sorted among the N's.
    if key is None:
        return BLANK_LABEL
    s = str(key)
    # An ISO datetime bucket, trimmed to what an axis can show -- and the time
    # dropped entirely when it is midnight, because that is a DATE.
    if len(s) >= 16 and s[4] == "-" and s[10] in ("T", " "):
        stamp = s[5:16].replace("T", " ")
        return stamp[:5] if stamp.endswith(" 00:00") else stamp
    # A whole number that arrived as a float: 0.0 is a value, "0.0" is a
    # rendering accident.
    if s.replace("-", "", 1).replace(".", "", 1).isdigit():
        try:
            value = float(s)
        except ValueError:
            return s
        if value == int(value):
            return str(int(value))
    return s


def _camera_inactive(systems_dir, slug, camera=""):
    """Is this widget's camera switched off? False whenever we cannot tell.

    Never raises and never guesses: a dashboard tile that claimed "inactive"
    because the camera list could not be read would be worse than one that
    simply shows the feed it was asked for. A blank `camera` means the first
    one, which is the single-camera behaviour everywhere else.
    """
    if not systems_dir or not slug:
        return False
    try:
        from core.services import detector_control as dc     # noqa: PLC0415
        cams = dc.cameras(systems_dir, slug) or []
    except Exception:                                        # noqa: BLE001
        return False
    if not cams:
        return False
    want = str(camera or "").strip()
    for cam in cams:
        if not want or str(cam.get("id")) == want:
            return cam.get("enabled") is False
    return False


def _data(w, events, stats, config_dir=None, slug=None, systems_dir=None):
    source, key = w.get("source"), w.get("key")
    if w.get("kind") == "button":
        # What the button last injected and how often. Whether THIS viewer
        # may press it is the route's to add (routes/builder.py), because only
        # the route knows who is looking.
        inj = w.get("inject") or {}
        runs = (stats or {}).get(f"fn_{inj.get('fn')}_runs") if inj.get("fn") \
            else None
        return {"value": (stats or {}).get(key) if runs else None,
                "runs": runs or 0,
                # Seconds the chip took to detect the last Snapshot frames.
                "detect_s": (stats or {}).get("snap_detect_s"),
                "node": w.get("node") or "",
                "removed": bool(inj.get("removed")),
                "ptype": inj.get("ptype") or "",
                "repeat": bool(inj.get("repeat")),
                # Whether the repeat is going right now (the app's own stat).
                "running": bool((stats or {}).get(f"fn_{inj.get('fn')}_running"))
                           if inj.get("fn") else False}
    if w.get("kind") in CONTROL_KINDS[1:]:
        return controls_service.widget_data(w, systems_dir, slug)
    if w.get("kind") in STATIC_KINDS:
        # Nothing to read: the card is its own words or its own picture.
        return {"static": True}
    if w.get("kind") == "snap_gallery":
        # The page lists the snaps itself (the camera's own, newest first);
        # the card only says whose and how many.
        return {"camera": str(w.get("camera") or "").strip(),
                "count": w.get("count") or SNAP_GALLERY_DEFAULT}
    if w.get("kind") == "video":
        # The MJPEG proxy the Detection page already uses; the browser renders
        # it directly, so this widget carries a URL, not data. A system may run
        # several cameras behind ONE app: naming one scopes the tile to it,
        # blank keeps the first camera (the single-camera behaviour).
        camera = str(w.get("camera") or "").strip()
        # Whether that camera is switched ON. A deactivated camera produces no
        # frames by design, so the tile would sit on a black rectangle
        # retrying a stream that is never coming -- indistinguishable from a
        # camera that has failed. The card says "inactive" instead, which is
        # the one thing that explains it.
        inactive = _camera_inactive(systems_dir, slug, camera)
        # [video] overlay = "client": the tile draws the boxes; {} otherwise.
        overlay = overlay_view.for_system(systems_dir, slug)
        if camera:
            return {"url": f"/api/detector/preview?camera={quote(camera, safe='')}",
                    "camera": camera, "inactive": inactive, **overlay}
        return {"url": "/api/detector/preview", "inactive": inactive, **overlay}
    if w.get("kind") == "verdict":
        raw = (stats or {}).get(key if key else COMPLIANCE_KEY)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        # Custom mapping first: the workflow's Verdict Card declares what each
        # VALUE means (0 idle, 1 warning, 2 violation...), so any function or
        # variable can drive the big card -- not only the compliance stat.
        states = w.get("states") or []
        if states:
            match = next((s for s in states if value is not None
                          and s["value"] == value), None)
            if match:
                return {"state": match["tone"], "value": value,
                        "label": match["label"],
                        # The state's own colour; "" lets the tone decide.
                        "state_color": match.get("color") or ""}
            return {"state": "unknown", "value": value, "label": "NO DATA"}
        if (key or COMPLIANCE_KEY) == COMPLIANCE_KEY:
            # The compliance stat's own convention (1 ok / 0 violating /
            # -1 empty). Three states, not two: "nothing in frame" is
            # genuinely different from "compliant", and reporting it as green
            # is a lie an operator acts on.
            value = int(value) if value is not None else None
            state = {1: "ok", 0: "alert", -1: "empty"}.get(value, "unknown")
            return {"state": state, "value": value,
                    "label": {"ok": "COMPLIANT", "alert": "VIOLATION",
                              "empty": "NO OBJECT"}.get(state, "NO DATA")}
        # Every OTHER key reads the ALERT convention: 0 = calm, anything else
        # = alerting. fn_*_active is 1 WHILE the condition holds -- mapping it
        # through the compliance table above showed a live violation as a
        # green COMPLIANT card.
        if value is None:
            return {"state": "unknown", "value": None, "label": "NO DATA"}
        state = "ok" if value == 0 else "alert"
        return {"state": state, "value": value,
                "label": "COMPLIANT" if state == "ok" else "VIOLATION"}
    if w.get("kind") == "heatmap":
        # Live stats only: the cells are a picture of the present, and the
        # history sampler holds scalars, not maps.
        return _heat_data(w, stats)
    if w.get("kind") == "status":
        value = (stats or {}).get(key)
        if isinstance(value, dict):
            value = sum(value.values())
        try:
            value = float(value or 0)
        except (TypeError, ValueError):
            value = 0.0
        rule = w.get("status") or {}
        alert = _STATUS_CMP.get(rule.get("op", ">="),
                                _STATUS_CMP[">="])(value, float(rule.get("value", 1)))
        return {"state": "alert" if alert else "ok", "value": value,
                "label": (rule.get("alert_label") or "ALERT") if alert
                         else (rule.get("ok_label") or "OK"),
                "color": w.get("color") or None}
    series_cfg = w.get("series") or []
    # -- multi-series charts: every entry names its own source ---------------
    #
    # Comparison charts (one value per series) and time charts (one shared
    # bucket grid) both live in chart_series now, because a chart drawing a
    # sampled key against an event query has to align them, and alignment is
    # not a branch -- it is the whole job. Migration runs HERE and not only in
    # validate(): a widget can arrive from a workflow compile or an installed
    # package that never met the validator, and the MAX_SERIES cap has to
    # hold for those too. None back means "not a shape that module draws"
    # (a tile carrying a series list, say), and the single-source paths below
    # answer exactly as they did before.
    if series_cfg:
        from core.services import chart_series
        resolved = chart_series.resolve(
            w, chart_series.migrate(series_cfg, source, widget=w), events, stats,
            config_dir, slug, systems_dir)
        if resolved is not None:
            return resolved

    # ONE store question along time ("total rows, every 6 hours, past
    # week"): drawn by the same time-axis path as several inputs
    # (chart_series._bucket_data, services/timeaxis.py), so a one-input chart
    # gets the same step, window, zero-filled periods, widening and browser
    # time labels as a two-input one. A chart grouped by a column, a tile and
    # every other shape keep their own paths below.
    q1 = w.get("query") or {}
    if not series_cfg and source in ("dataset", "query") \
            and w.get("kind") in CHART_KINDS and q1.get("bucket") \
            and not q1.get("group_by"):
        from core.services import chart_series
        one = {"id": "s1", "label": w.get("title") or "", "color": "",
               "source": source, "query": dict(q1)}
        if source == "dataset":
            one.update({"dataset": w.get("dataset") or "",
                        "columns": w.get("columns") or [],
                        "field": q1.get("field") or ""})
        entry = chart_series._entry(one, source)
        if entry is not None:
            entry["id"] = "s1"
            entry["query"].setdefault("bucket", q1["bucket"])
            for k in ("_since", "_until"):
                if q1.get(k):
                    entry["query"][k] = q1[k]
            out = chart_series._bucket_data(w, [entry], events, config_dir,
                                            slug, systems_dir, [])
            # The single-source shape ({labels, values}), as these charts
            # have always answered: drawn under the card's own title, and a
            # stored layout's picture unchanged (tests/smoke_layoutsave.py).
            one_set = (out.pop("datasets", None) or [{}])[0]
            out["values"] = one_set.get("values") or []
            return out

    if source == "history":
        from core.services import history as history_service
        # An explicit window (the Analytics date pick, injected by compute()
        # for "follow the page" widgets): now = the window's end.
        h_since = w.get("_since")
        h_until = w.get("_until")
        if series_cfg:
            # Time-series widget: multi-series, per-series aggregation,
            # pinned or auto interval. Read from the history sampler.
            hist = history_service.series_agg(series_cfg, w.get("range") or "24h",
                                              w.get("interval") or "",
                                              now=h_until, since=h_since)
            return {"labels": hist.get("labels") or [],
                    "datasets": [{"label": d.get("label") or d["key"],
                                  "values": d["values"],
                                  "color": d.get("color") or None}
                                 for d in hist.get("datasets") or []],
                    "interval_s": hist.get("interval_s"),
                    # The RESOLVED window ("page" already mapped in compute),
                    # so a tooltip or a test can say what was actually drawn.
                    "range": w.get("range") or "24h",
                    "color": w.get("color") or None, "unit": w.get("unit") or None}
        series = history_service.series([key], w.get("range") or "24h",
                                        now=h_until, since=h_since)
        values = (series.get("series") or {}).get(key) or []
        return {"labels": series.get("labels") or [],
                "values": values,
                "range": w.get("range") or "24h",
                "color": w.get("color") or None, "unit": w.get("unit") or None}
    if source == "query":
        return _query_data(w, config_dir, slug, events)
    if source == "dataset":
        return _dataset_data(w, config_dir, slug, systems_dir)
    if source == "sql":
        return _sql_data(w, config_dir, slug)
    if source == "stat":
        # A tile carrying its own words shows them. Checked before the stats
        # dict is consulted at all: there is no key to look up, because the
        # value never was a number.
        if w.get("text"):
            return {"value": w["text"], "text": True,
                    "sub": _caption(w),
                    "color": w.get("color") or None,
                    "unit": w.get("unit") or None}
        value = (stats or {}).get(key)
        if isinstance(value, dict):
            value = sum(value.values())
        # NOT `or key`: the raw stat key (fn_z_zone_msfi1fdf_zoneviol_count)
        # is not a second name for the tile, it is an implementation detail --
        # the title already says what this is. Blank reads as "nothing set",
        # which is honest; the key reads as a second, worse name for the
        # same thing the title just said.
        return {"value": value, "sub": _caption(w),
                "color": w.get("color") or None,
                # The unit rides with the number so the card can print it
                # beside it. Left off here, "Unit label" was a field that
                # did nothing at all on a live stat tile.
                "unit": w.get("unit") or None}

    if source == "bucket":
        bucket = (stats or {}).get(key) or {}
        if not isinstance(bucket, dict):
            return {"labels": [], "values": []}
        labels = sorted(bucket)[-30:]
        return {"labels": [str(l)[5:] if len(str(l)) == 10 else str(l) for l in labels],
                "values": [bucket[l] for l in labels],
                "color": w.get("color") or None}

    rows = [
        [str(e.get("ts", ""))[:19].replace("T", " "), e.get("type", ""),
         e.get("severity", ""), e.get("label", ""), e.get("device", "") or ""]
        for e in sorted(events or [], key=lambda e: e.get("ts", ""), reverse=True)
    ]
    return {"columns": ["Datetime", "Type", "Severity", "Event", "Device"],
            "rows": rows[:w.get("limit", 15)]}
