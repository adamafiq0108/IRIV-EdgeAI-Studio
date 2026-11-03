# -*- coding: utf-8 -*-
"""
analytics.py -- Running a system's widget data_fns.

A WidgetSpec (core/system_spec.py) declares `data_fn(events, stats) -> dict`;
this is the one place that actually calls it, against the events the sync
worker has accumulated and the stats tiles already show. A package's data_fn
is arbitrary code from a system package, so one bad widget must not take the
page down for every other widget.

No framework imports here -- this takes plain values and returns plain dicts
(docs/conventions.md sec 2).
"""

import datetime
import logging

logger = logging.getLogger(__name__)

#: Page ranges -> days of history. Devices pre-aggregate 30 days of per-day
#: buckets (the adapters' _ANALYTICS_DAYS), so "month" is the whole window.
RANGES = {"today": 1, "week": 7, "month": 30}

#: An explicit from/to window. The named ranges answer "recently"; a report or
#: an incident review asks about a specific fortnight in March, which no fixed
#: set of buttons covers.
CUSTOM = "custom"


def _iso(value):
    """A YYYY-MM-DD string, or "" for anything that is not one. Never raises --
    a malformed date from a query string is a wider window, not a 500."""
    text = str(value or "")[:10]
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return ""
    return text


def window(range_key, start="", end=""):
    """(floor, ceiling) as ISO dates for a range. `ceiling` is "" for the named
    ranges, which always run up to today."""
    if range_key == CUSTOM:
        lo, hi = _iso(start), _iso(end)
        if lo and hi and lo > hi:
            lo, hi = hi, lo
        if lo or hi:
            return lo or "0000-01-01", hi
    days = RANGES.get(range_key, 30)
    return (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat(), ""


def scope_stats(stats, range_key, start="", end=""):
    """A copy of the roll-up cut down to the chosen range.

    Per-day buckets (any key ending in _by_day) are filtered to the window,
    and violations_total/events_total become window sums when their bucket
    exists -- the tiles then agree with the chart above them. Systems without
    buckets keep their all-time totals; a wrong number is worse than a wider
    window.
    """
    floor, ceiling = window(range_key, start, end)

    out = dict(stats)
    for key, value in stats.items():
        if key.endswith("_by_day") and isinstance(value, dict):
            windowed = {d: n for d, n in value.items()
                        if d >= floor and (not ceiling or d <= ceiling)}
            out[key] = windowed
            # Anything that publishes a per-day bucket gets its TOTAL cut to
            # the same window: a tile reading "1,204 objects" above a chart
            # showing this week is two different questions answered as one.
            # Covers violations_by_day/events_by_day and every counting
            # function's fn_<id>_by_day the same way.
            total_key = key[:-len("_by_day")] + "_total"
            if isinstance(stats.get(total_key), (int, float)) \
                    and not isinstance(stats.get(total_key), bool):
                out[total_key] = sum(windowed.values())
    return out


def scope_events(events, range_key, start="", end=""):
    floor, ceiling = window(range_key, start, end)
    return [e for e in events
            if floor <= str(e.get("ts", ""))[:10]
            and (not ceiling or str(e.get("ts", ""))[:10] <= ceiling)]


def day_counts(events, start="", end=""):
    """{YYYY-MM-DD: how many events that day} across [start, end].

    Feeds the day strip's dots: which days hold something is the difference
    between picking a day worth opening and opening each one to find out.
    Bounded to the span the page is showing, so it costs one pass.
    """
    out = {}
    for e in events or ():
        day = str(e.get("ts", ""))[:10]
        if len(day) != 10:
            continue
        if start and day < start:
            continue
        if end and day > end:
            continue
        out[day] = out.get(day, 0) + 1
    return out


def compute_pages(widgets, events, stats, config_dir, slug, capabilities=(),
                  systems_dir=None, page_range="", page_start="", page_end=""):
    """Analytics as ONE page: "Overview".

    The Overview is the system PACKAGE's own widgets -- declared in Python, so
    there is no layout file to edit and they used to be immovable. They are
    treated as DEFAULTS instead: the layout's `hidden` list removes any of them,
    and a Builder page with the same id contributes additions. So the Overview
    customises like every other page without the package having to change.

    Analytics used to grow a tab per Builder page, which meant widgets lived
    wherever they happened to be created and "where do I edit this?" had no
    answer. Every other page is FOLDED into the Overview instead of dropped:
    folding means a system with stray pages loses nothing on the way here.
    """
    from core.services import builder as builder_service

    layout = builder_service.load(config_dir, slug)
    hidden = set(layout.get("hidden") or ())
    extra = {p["id"]: p for p in layout.get("pages", [])}

    # Where a card SITS and whether it shows at all belong to the operator,
    # whoever authored the card. Builder rows (the workflow's wf_* cards
    # included) go through the same hidden/placements treatment as package
    # cards -- filtering only the package set is how "I removed that card"
    # bounced back and "I moved that card" showed the editor one layout and
    # the live page another.
    placed = layout.get("placements", {}).get("overview", {})

    def _builder_widgets(page_id):
        p = extra.get(page_id)
        if not p:
            return []
        rows = builder_service.compute(p.get("widgets"), events, stats,
                                       capabilities, config_dir, slug,
                                       systems_dir=systems_dir,
                                       page_range=page_range,
                                       page_start=page_start,
                                       page_end=page_end)
        rows = [w for w in rows if w["id"] not in hidden]
        for w in rows:
            if w["id"] in placed:
                w["grid"] = placed[w["id"]]
        return rows

    pages = []
    pkg = [w for w in compute(widgets, events, stats) if w["id"] not in hidden]
    # Package widgets are declared in Python, so their dressing has nowhere to
    # live but the layout's override map -- the same place a compiled card's
    # lives. Builder-page widgets already arrive dressed (load() applies the
    # map to stored rows); these are computed fresh and need it applied here.
    dressed = layout.get("templates") or {}
    coloured = layout.get("colors") or {}
    accented = layout.get("accents") or {}
    fonts = layout.get("fonts") or {}
    named = layout.get("names") or {}
    for w in pkg:
        if w["id"] in placed:
            w["grid"] = placed[w["id"]]
        if w["id"] in dressed:
            w["template"] = dressed[w["id"]]
        if w["id"] in coloured:
            w["bg"] = coloured[w["id"]]
        # ...and the accent, and the text sizes. Every override the layout can
        # hold, applied here -- listing three of five was how a package card
        # took a new colour on the editor and rendered in the old one.
        if w["id"] in accented:
            w["color"] = accented[w["id"]]
        # ...and what it is CALLED. A package card has no workflow node to send
        # a rename to, so the map is the only place its title can live.
        if w["id"] in named:
            w["title"] = named[w["id"]]
        if w["id"] in fonts:
            for field, key in (("label", "label_size"), ("value", "value_size")):
                if fonts[w["id"]].get(field):
                    w[key] = fonts[w["id"]][field]
    overview = pkg + _builder_widgets("overview")
    # Fold every other Builder page in. The Dashboard page ("main") is its own
    # page in the nav, so it stays out -- showing it here would render the same
    # widgets twice.
    seen = {w["id"] for w in overview}
    for p in layout.get("pages", []):
        if p["id"] in (builder_service.MAIN_PAGE_ID, "overview"):
            continue
        for w in _builder_widgets(p["id"]):
            if w["id"] in hidden or w["id"] in seen:
                continue
            seen.add(w["id"])
            overview.append(w)
    # The row height the operator chose, carried out of the SAVED page onto
    # the computed one -- the widgets here are computed fresh every request,
    # but how tall their rows are is a layout decision and lives in the file.
    saved = extra.get("overview") or {}
    pages.append({"id": "overview", "name": "Overview",
                  "size": builder_service._as_size(saved.get("size")),
                  "widgets": overview})
    return pages


def hidden_catalogue(widgets, config_dir, slug):
    """The package widgets currently hidden, so the UI can offer them back.

    Hiding is reversible by design: a removal an operator cannot undo is one
    they will not risk making.
    """
    from core.services import builder as builder_service
    layout = builder_service.load(config_dir, slug)
    hidden = set(layout.get("hidden") or ())
    out = [{"id": w.id, "title": w.title} for w in widgets if w.id in hidden]
    # Builder-page widgets (the workflow's wf_* cards included) can be hidden
    # too; their docs stay in the layout precisely so they can be offered back.
    seen = {r["id"] for r in out}
    for p in layout.get("pages", []):
        for w in p.get("widgets") or []:
            wid = w.get("id")
            if wid in hidden and wid not in seen:
                seen.add(wid)
                out.append({"id": wid, "title": w.get("title") or wid})
    return out


def compute(widgets, events, stats):
    """Return [{id, title, kind, span, data}] -- data is None if the widget raised.

    A widget with a bug renders as an empty card, not a 500 that hides every
    other system's numbers along with it.
    """
    out = []
    for w in widgets:
        try:
            data = w.data_fn(events, stats)
        except Exception:
            logger.exception("[ANALYTICS] widget %r raised", w.id)
            data = None
        out.append({"id": w.id, "title": w.title, "kind": w.kind, "span": w.span, "data": data})
    return out
