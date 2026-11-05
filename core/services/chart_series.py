# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
chart_series.py -- one chart, several sources, one axis.

A chart used to be able to draw several LINES only if they all came from the
same place: several sampler keys, or one event query grouped by a column. The
question an operator actually asks is "zone A's count against the alarms the
gate raised against the weights the scale logged", and that is three stores.

What makes this a service rather than a few more branches in builder._data is
ALIGNMENT. Each store answers on its own time base -- the sampler every 10
seconds, the event store on whatever bucket it was asked for, a dataset on its
own timestamp column -- and three answers on three bases drawn together are
not one chart, they are three charts overlaid, with the peaks in the wrong
places. So the grid is derived ONCE (history.grid) and every series is
resampled onto it. A bucket a series has no answer for stays null: a zero
draws a line to the floor and claims something was measured there, which is
the difference between "the gate was quiet" and "the gate was not asked".

WHAT IT COSTS. Every series is its own store round trip -- worst case
MAX_SERIES (8) aggregates per widget per poll, each opening and closing its
own connection, because that is what the single-source path does too and
there is no query cache under it (events_repo caches only `available()` and
the filter vocabulary). Two things hold that down: identical specs inside one
widget are resolved once (see `memo` below), and the cap is enforced HERE and
not only in the inspector, because a widget can also arrive from a workflow
compile or an installed package that never met the validator. A dashboard
polling every 5s with four 8-series charts is ~38 aggregates a poll; that is
real load on a Pi, and it is the ceiling, not the normal case.

SQL is deliberately not a series source. A hand-written statement has no
schema to align on -- nothing here can know which of its columns is a
timestamp, which is a value, or whether its rows are even one per bucket --
so _sql_data stays single-series and untouched.
"""

import datetime
import json
import time

#: Entry sources. "bucket" is here for one reason: a bucket widget's legacy
#: series entries inherit the widget's source, and the inspector has always
#: offered the series editor on a bucket widget (it just did nothing -- the
#: bug this module also fixes). It is not offered as a NEW entry source.
ENTRY_SOURCES = ("history", "stat", "query", "dataset", "bucket")

#: Sources that can be drawn against time. A bucket stat is a {label: value}
#: map with no timestamps in it, so it can only ever be a label-set series.
TIME_SOURCES = ("history", "stat", "query", "dataset")

#: Sources that produce a LABEL SET (one value per group) rather than one
#: value: a bucket map, or a query/dataset series grouped by a column.
#: Aligned by union of labels, see _label_data.
_GROUP_SOURCES = ("query", "dataset", "bucket")

#: Most labels a grouped chart will draw. Matches the cap the single-source
#: bucket card has always had: 40 bars is not a chart anybody reads a value
#: off, and 8 series x 500 groups is a payload a Pi sends every poll.
MAX_LABELS = 30

#: Rows pulled for the expensive dataset path (a series bucketed by its own
#: ts_field rather than the row's ingest time). The store cannot bucket on a
#: JSONB column, so those rows are bucketed here -- capped, and the chart
#: says so rather than quietly drawing a partial window.
MAX_TS_ROWS = 1000

#: How an entry's per-bucket fold is derived from what the STORE was asked
#: for, when the entry does not pin one. Two hourly counts folded into one
#: two-hour cell are a sum, not an average; two averages are an average.
_FOLD_FOR = {"count": "sum", "count_nonnull": "sum", "sum": "sum", "avg": "avg",
             "min": "min", "max": "max", "unique": "unique", "changes": "sum",
             "latest": "last"}


# -- shape -------------------------------------------------------------------

def migrate(series, widget_source, strict=False, widget=None):
    """Normalise a widget's `series` list to self-describing entries. PURE:
    no store, no clock, no I/O -- so it can run in the validator, in a test,
    and (identically) at render time on a widget that never met the validator.

    A legacy entry is {key, agg, label, color} with no `source`, and it means
    the WIDGET's source, which is exactly what it meant before entries could
    carry one. Every stored layout therefore renders unchanged: the migration
    is a read, not a rewrite, and nothing has to be re-saved for a chart to
    keep working.

    `widget` is the card the list belongs to, when the caller has it. An entry
    that reads the widget's source reads the widget's DATA too -- its table,
    its columns, its question -- because that is where the inspector keeps
    them for such a row (see _inherit). Without the widget the entry is taken
    as it is, which is all the pure tests need.

    `strict` is the difference between STORING a layout and LOOKING at one.
    A row is incomplete for as long as the operator is typing it -- "a column
    of a data table" is picked before the table is -- and the builder previews
    the whole page on every keystroke. Raising there turned one half-typed row
    into a 400 that blanked every other card on the page, which is the bug this
    argument exists for. So:

      strict=False (preview, and render of anything already stored): an entry
      that names nothing askable is KEPT as an incomplete marker. It resolves
      to an empty series carrying a warning, so the chart says which row is
      unfinished and the other seven keep drawing.

      strict=True (save, publish, template apply): the same entry raises
      ValueError, so a half-typed row never reaches a stored layout or a
      published package.

    What strict can NOT check is existence: this function is pure, and whether
    a table is still in the store needs the store. A row naming a table that
    has since been deleted is caught at render (see _dataset_columns), where
    it is a named empty series rather than a page that will not load.

    Returns at most MAX_SERIES entries. The cap is applied here because here
    is the one place every caller passes through.
    """
    from core.services import builder
    if not isinstance(series, list):
        return []
    out, used = [], set()
    for i, s in enumerate(series[:builder.MAX_SERIES]):
        if not isinstance(s, dict):
            continue
        source = s.get("source")
        if source not in ENTRY_SOURCES:
            # The legacy meaning. A widget source that is not a series source
            # at all (sql, events) reads as "stat": a series editor was shown
            # on it, and the keys it collected are stat keys.
            source = widget_source if widget_source in ENTRY_SOURCES else "stat"
            s = _inherit(s, source, widget)
        entry = _entry(s, source)
        if not entry:
            if strict:
                raise ValueError(f"series {i + 1}: {_missing(source)}")
            entry = _incomplete(s, source)
        # The id is what a colour survives a reorder by, so it has to be
        # stable per entry and unique per widget. A duplicate (or an absent
        # one) falls back to the position, which is where the palette looks
        # anyway.
        wid = "".join(c for c in str(s.get("id") or "") if c.isalnum() or c in "_-")[:24]
        if not wid or wid in used:
            wid = f"s{i + 1}"
        used.add(wid)
        entry["id"] = wid
        out.append(entry)
    return out


#: What a row that reads THE CARD's source takes from the card, per source.
#: A row's own value always wins; these fill only what the row does not say.
_INHERITED = {"dataset": ("dataset", "columns", "query", "ts_field"),
              "history": ("key",), "stat": ("key",), "bucket": ("key",)}


def _inherit(s, source, widget):
    """The entry a row that names no source of its own really means.

    The inspector offers such a row as "Reads this card's data table (set
    below)" and keeps the CARD's own table editor on screen for it, so the
    table, its columns and the question all live on the widget and the row
    carries none of them. The resolver read row fields only, so a table
    column dragged out of the palette as a chart previewed (strict=False kept
    the row as an incomplete marker) and then could not be saved: "series 1:
    pick the data table this column comes from", about a table that was set,
    one control further down. The same widget with no series list at all
    saved fine, which is how the fault hid behind "the workflow did not sync".

    A stat row was never bitten because the editor copies the card's key onto
    it (widgetSeries); the key is taken here as well so the two sources read
    the same way. The dataset row's FOLD is derived, not kept: an inheriting
    row shows no per-bucket control, so its `agg` is the blind default the
    editor stamps on every fresh row, and the fold has to follow the card's
    question (latest -> last, count -> sum) exactly as _entry derives it for
    a row picked by hand.
    """
    if not isinstance(s, dict) or not isinstance(widget, dict):
        return s
    taken = {k: widget.get(k) for k in _INHERITED.get(source, ())
             if not s.get(k) and widget.get(k)}
    if not taken:
        return s
    s = {**s, **taken}
    if source == "dataset" and "dataset" in taken:
        q = widget.get("query") if isinstance(widget.get("query"), dict) else {}
        if not s.get("field") and q.get("field"):
            s["field"] = q["field"]
        s["agg"] = ""
    return s


def _missing(source):
    """What an unfinished row of this source is still missing, in the words the
    inspector puts on the control -- the operator has to be able to find the
    row the message is about."""
    if source == "dataset":
        return "pick the data table this column comes from"
    return "pick the value this series draws"


def _incomplete(s, source):
    """A row the operator has not finished, kept so the chart can name it.

    It carries only what the card DRAWS with (label, colour) plus the reason,
    and deliberately no query of any kind: nothing downstream may treat an
    incomplete entry as askable, so every resolver branches on `incomplete`
    before it touches a spec field that is not there.
    """
    from core.services import builder
    return {"source": source,
            "label": str(s.get("label") or "").strip()[:40],
            "color": builder._color(s.get("color")),
            "incomplete": _missing(source)}


def _entry(s, source):
    """One cleaned entry, or None when it names nothing askable.

    Every name in here is clamped to the same whitelist the single-source
    path uses -- event columns against events_repo.FILTERABLE, dataset
    columns against the table's own declaration -- because a series spec
    reaches SQL by the same road a widget query does, and "the inspector
    would not send that" is not a check.
    """
    from core.repos import datasets as datasets_repo
    from core.services import builder
    common = {
        "source": source,
        "label": str(s.get("label") or "").strip()[:40],
        "color": builder._color(s.get("color")),
        # The input's OWN threshold bands (per camera, owner 2026-10-13):
        # drawn on this series only; absent, it follows the chart's.
        **({"bands": builder.clean_bands(s.get("bands"))}
           if builder.clean_bands(s.get("bands")) else {}),
        # A HIDDEN input (chart builder: the row's visibility toggle) is
        # kept in the list -- the editors still show it -- and never drawn
        # or asked. Only the hidden state is written, so every stored entry
        # that never had the flag is byte-identical.
        **({"show": False} if s.get("show") is False else {}),
    }
    agg = s.get("agg") if s.get("agg") in builder.SERIES_AGGS else ""

    if source in ("history", "stat", "bucket"):
        key = str(s.get("key") or "").strip()[:60]
        if not key:
            return None
        # "avg" unpinned, exactly as _clean_series has always defaulted it.
        return {**common, "key": key, "agg": agg or "avg"}

    if source == "query":
        q = {**builder._clean_query(s.get("query")),
             **_window_of(s.get("query"))}
        return {**common, "query": q, "agg": agg or _FOLD_FOR.get(q["agg"], "last")}

    if source == "dataset":
        name = str(s.get("dataset") or "").strip()
        if not datasets_repo.COL_RE.match(name):
            return None
        types = datasets_repo.col_types(s.get("columns"))
        field = str(s.get("field") or "").strip()
        field = field if field in types else ""
        # The row's own timestamp column, when the operator has one. Blank
        # means the ingest time (`ts`), which is what every dataset card
        # charts today and what the store can bucket for itself.
        # Against the repo's OWN vocabulary, not the word the inspector uses:
        # col_types normalises "datetime" to "timestamp", so checking for the
        # inspector's word silently threw every ts_field away.
        ts_field = str(s.get("ts_field") or "").strip()
        ts_field = ts_field if types.get(ts_field) in datasets_repo.TIME_TYPES else ""
        raw = s.get("query") or {}
        store_agg = raw.get("agg") if raw.get("agg") in builder.AGGREGATIONS else \
            ("latest" if agg == "last" else agg or "count")
        group_by = str(raw.get("group_by") or "").strip()
        filters = {}
        for name_, value in (raw.get("filters") or {}).items():
            if name_ in types and str(value or "").strip():
                filters[name_] = str(value).strip()[:60]
        query = {"agg": store_agg,
                 "group_by": group_by if group_by in types else "",
                 "range": raw.get("range") if raw.get("range")
                 in builder.QUERY_RANGES else "",
                 **builder._range_ends(raw),
                 "filters": filters}
        # A calendar period per point -- the chart's own axis, which a
        # workflow chart hands every table input it draws (see
        # _bucket_data). Only written when set, so an entry that never had
        # one is byte-identical to what it was.
        if raw.get("bucket") in builder.BUCKETS:
            query["bucket"] = raw["bucket"]
        query.update(_window_of(raw))
        return {**common,
                "dataset": name,
                "columns": [{"name": n, "type": t} for n, t in types.items()],
                "field": field,
                "ts_field": ts_field,
                # Set by the chart builder model: the Date column also steps
                # and filters the per-step and per-column paths (older
                # entries keep the saved time there, as they always drew).
                **({"by_ts_field": True}
                   if s.get("by_ts_field") and ts_field else {}),
                "agg": agg or _FOLD_FOR.get(store_agg, "last"),
                "query": query}
    return None


def _window_of(raw):
    """The resolved date window builder.compute() stamps on a series whose
    period is "page" (the Analytics date pick, as two ISO stamps). Carried
    through the clean-up rather than dropped by it: without it a series set
    to "follow the Analytics range" read ALL TIME, because "page" itself is
    not a window any store understands. Only ever ISO text the server wrote;
    it reaches SQL as a bound parameter."""
    raw = raw if isinstance(raw, dict) else {}
    return {k: str(raw[k])[:40] for k in ("_since", "_until")
            if isinstance(raw.get(k), str) and raw.get(k)}


def _since_until(q):
    """(since, until) a store question covers: the resolved Analytics window
    when there is one, else the start of its named period (None = all time)."""
    from core.services import builder
    from core.services import timeaxis
    q = q or {}
    since = q.get("_since") or builder._range_floor(q.get("range"))
    until = q.get("_until") or None
    if since and not until and q.get("range") in timeaxis.RANGE_KEYS:
        # A named window ends NOW, said in the query (ts < now) rather than
        # left open, so the store and the axis agree on the right edge.
        until = timeaxis.iso(time.time())
    return since, until


# -- resolving ---------------------------------------------------------------

def resolve(w, entries, events, stats, config_dir=None, slug=None,
            systems_dir=None):
    """The widget's data dict, or None when these entries are not something
    this module draws (a tile with a series list, say -- the caller's own
    paths still own that).

    Returns the shape the renderer already knows: `{labels, datasets:
    [{id, label, color, values}]}` for a chart with an axis, `{labels, values,
    colors}` for a comparison chart where each series is ONE value. Plus
    `warnings`: a series naming a deleted dataset or a key the sampler never
    saw comes back empty and named, because one broken series must not blank
    the other seven -- the operator has to be able to see WHICH one is wrong,
    and a chart that renders nothing says only "something".
    """
    from core.services import builder
    entries = list(entries or ())[:builder.MAX_SERIES]
    kind = w.get("kind")
    # Hidden inputs are listed in the editors and never drawn: dropped here,
    # before any store is asked, so a hidden camera costs nothing per poll.
    shown = [e for e in entries if e.get("show") is not False]
    if entries and not shown and kind in builder.CHART_KINDS:
        return _dress(w, {"labels": [], "datasets": [], "hidden": len(entries)}, [])
    entries = shown
    out = _resolve(w, entries, events, stats, config_dir, slug, systems_dir, kind)
    # Each series' own threshold bands ride its dataset, keyed by the
    # series' id -- never by position, so no camera draws another's limits.
    own = {e["id"]: e["bands"] for e in entries if e.get("bands")}
    if own and isinstance(out, dict):
        for d in out.get("datasets") or []:
            if d.get("id") in own:
                d["bands"] = own[d["id"]]
    return out


def _resolve(w, entries, events, stats, config_dir, slug, systems_dir, kind):
    """resolve(), once the hidden inputs are out."""
    from core.services import builder
    # Only a chart has an axis to align on. A tile, a table, a gauge with a
    # series list is not this module's shape, and answering for one would
    # replace a working card with a chart payload it cannot draw.
    if not entries or kind not in builder.CHART_KINDS:
        return None
    warnings = []
    entries = _one_x_axis(entries, warnings)

    # A CALENDAR AXIS SHARED BY STORE QUESTIONS: every input is a table or an
    # event query asking "per day" (or hour/week/month). Taken first, on any
    # chart shape, because that is what the operator asked the chart for --
    # one bar (or point) per day per input -- and neither the sampler grid
    # below nor the one-total comparison path draws it.
    if _bucketed(entries):
        return _bucket_data(w, entries, events, config_dir, slug, systems_dir,
                            warnings)

    timed = [e for e in entries if e["source"] in TIME_SOURCES]
    grouped = [e for e in entries
               if e["source"] == "bucket"
               or (e["source"] in _GROUP_SOURCES and (e.get("query") or {}).get("group_by"))]

    # Every input grouped by a column (a graph whose X is a database column,
    # chart builder model): a label set on any shape, never the time grid --
    # the grid would draw the groups as if their grouping did not exist.
    asked = [e for e in entries if not e.get("incomplete")]
    if asked and all(e in grouped and e["source"] in ("query", "dataset")
                     for e in asked):
        return _label_data(w, entries, events, stats, config_dir, slug,
                           systems_dir, warnings)

    # A chart with a time axis draws against the clock. The second clause is
    # compatibility, not taste: a bar/pie/radar widget whose SOURCE is history
    # has always been drawn as a time series by _data, and a stored layout
    # doing that must keep doing it.
    if timed and (kind in builder.TIMESERIES_KINDS
                  or (w.get("source") == "history"
                      and all(e["source"] in ("history", "stat") for e in entries))):
        return _time_data(w, entries, events, stats, config_dir, slug,
                          systems_dir, warnings)
    if grouped:
        return _label_data(w, entries, events, stats, config_dir, slug,
                           systems_dir, warnings)
    if kind in builder.COMPARISON_KINDS:
        return _value_data(w, entries, events, stats, config_dir, slug,
                           systems_dir, warnings)
    return None


def _one_x_axis(entries, warnings):
    """One X axis for every store input, never a silent mix.

    An input's X is its own field ("One point per <column>", query.group_by)
    when one is set, else time (services/timeaxis.py precedence: an assigned
    field wins over the time grouping). When the inputs disagree, the FIRST
    input's choice is the chart's, the others follow it where they can, and
    the chart says so -- it used to draw a grouped input along the time grid
    as if its grouping did not exist."""
    store = [e for e in entries
             if e.get("source") in ("query", "dataset") and not e.get("incomplete")]
    xs = [(e.get("query") or {}).get("group_by") or "" for e in store]
    if len(set(xs)) < 2:
        return entries
    first = xs[0]
    first_bucket = (store[0].get("query") or {}).get("bucket") or ""
    out = []
    for e in entries:
        q = dict(e.get("query") or {})
        if e in store and (q.get("group_by") or "") != first:
            if not first:
                q.pop("group_by", None)
                if first_bucket:
                    q["bucket"] = first_bucket
            elif any(c.get("name") == first for c in e.get("columns") or ())\
                    or e.get("source") == "query":
                q["group_by"] = first
                q.pop("bucket", None)
            e = {**e, "query": q}
        out.append(e)
    warnings.append({"id": "", "label": "", "message": (
        "The inputs disagree on the X axis - using the first input's "
        + (f"'{first}'" if first else "time") + " for all of them.")})
    return out


#: Store answers where a period with no rows really is zero of them: a count
#: of nothing is 0. An average or a latest value of nothing is not a number.
_ZERO_WHEN_EMPTY = ("count", "count_nonnull", "sum", "unique", "changes")


def _bucketed(entries):
    """True when every askable entry is a store question (an event query or a
    table) carrying a calendar `bucket` and no `group_by` -- the per-day /
    per-hour chart a workflow chart with several table inputs compiles to.
    A sampled key has no calendar buckets to offer, so one of those beside
    them keeps the chart on the sampler's grid, as before."""
    asked = [e for e in entries if not e.get("incomplete")]
    return bool(asked) and all(
        e["source"] in ("query", "dataset")
        and (e.get("query") or {}).get("bucket")
        and not (e.get("query") or {}).get("group_by")
        for e in asked)


def _bucket_data(w, entries, events, config_dir, slug, systems_dir, warnings):
    """Several store series on ONE calendar axis: the union of their periods,
    each series' value placed on its own period.

    THE BUG THIS FIXES. A chart with two Data Table inputs and "one bar per
    day" drew neither: the bar shape took the one-value-per-input path (one
    all-time total per status word, the day setting ignored) and the line
    shapes the sampler's grid, where a count folded as "count" became the
    number of store buckets in a cell -- 1 on every day that had rows. Asked
    of the stores per period instead, exactly the question the single-input
    chart asks, so one wire and two wires draw the same numbers.

    Aligned by the period's EPOCH, not its text: both stores key a period as
    an ISO stamp, and two series must meet on the same day even if one came
    back from Postgres and the other from the in-memory fallback. A period
    one series has and another lacks is 0 for a count (nothing recorded IS
    none) and a gap for an average -- so a camera that recorded nothing all
    week draws a flat zero line beside the one that did, never a broken chart.
    """
    from core.services import builder
    from core.services import timeaxis
    # THE STEP AND THE WINDOW (services/timeaxis.py). The step is the chart's
    # "Group by time" -- every series carries it as its bucket -- widened to
    # the nearest preset when the window would hold more than MAX_POINTS of
    # them, and said. The window is each series' own, filtered in the store
    # query itself (WHERE ts >= since AND ts < until).
    asked = [e for e in entries if not e.get("incomplete")]
    step = timeaxis.clean_step((asked[0].get("query") or {}).get("bucket")
                               if asked else "") or "1d"
    windows = [_since_until(e.get("query") or {}) for e in asked]
    since_e = [_epoch(s) for s, _u in windows]
    until_e = [_epoch(u) if u else time.time() for _s, u in windows]
    bounded = bool(asked) and all(s is not None for s in since_e)
    widened = False
    if bounded:
        step, widened = timeaxis.fit(step, max(until_e) - min(since_e))
    bucket = timeaxis.store_bucket(step)
    columns, moments = [], {}
    for entry in entries:
        q = entry.get("query") or {}
        if entry.get("incomplete"):
            _warn(warnings, entry, entry["incomplete"])
            pairs = []
        else:
            since, until = _since_until(q)
            if entry["source"] == "query":
                pairs = _event_groups(entry, slug, events, config_dir,
                                      bucket=bucket, since=since,
                                      until=until)
            else:
                pairs = _dataset_buckets(entry, slug, config_dir, systems_dir,
                                         bucket, since, until, warnings)
        seen = {}
        for key, value in pairs:
            epoch = _epoch(key)
            if epoch is None:
                continue
            # On the step's own edge, whichever store answered: two series
            # meet on one point even if one keyed it a second off.
            epoch = timeaxis.floor(epoch, step)
            seen.setdefault(epoch, []).append(value)
            moments.setdefault(epoch, key)
        # Two store answers on one point (a key a second off the edge) fold
        # the way grid cells do: counts add up, averages average.
        seen = {e: _fold(v, _store_fold(entry)) if len(v) > 1 else v[0]
                for e, v in seen.items()}
        columns.append(seen)
    if bounded:
        # The whole window is on the axis -- a period nobody recorded anything
        # in is a point, zero for a count, a gap for an average.
        order = timeaxis.axis(min(since_e), max(until_e), step)
    else:
        order = sorted(moments)[-timeaxis.MAX_POINTS:]
    keys = [moments.get(e) or timeaxis.iso(e) for e in order]
    datasets = []
    for entry, seen in zip(entries, columns):
        store_agg = (entry.get("query") or {}).get("agg") or "count"
        empty = 0 if (store_agg in _ZERO_WHEN_EMPTY
                      and not entry.get("incomplete")) else None
        datasets.append({"id": entry["id"], "label": _label(entry),
                         "color": entry["color"] or None,
                         "values": [seen.get(e, empty) for e in order]})
    out = {"labels": [_group_label(k) for k in keys],
           "datasets": datasets,
           "bucket": next((e["query"]["bucket"] for e in entries
                           if (e.get("query") or {}).get("bucket")), ""),
           **_axis_info(order, step, widened)}
    return _dress(w, out, warnings)


def _axis_info(order, step, widened):
    """What the browser needs to draw a time axis in ITS OWN clock: each
    point's start (epoch ms), the step in seconds and words, and -- when the
    step had to be widened to fit the range -- the grey line saying so."""
    from core.services import timeaxis
    info = {"keys": [int(e * 1000) for e in order],
            "step_s": timeaxis.step_seconds(step) or 0,
            "step": timeaxis.step_words(step)}
    if widened:
        info["step_note"] = (f"Showing {timeaxis.step_words(step).lower()} "
                             "because this range is long")
    return info


def _dataset_buckets(entry, slug, config_dir, systems_dir, bucket, since,
                     until, warnings):
    """[(period key, value)] for one table series, per calendar period. The
    store zero-fills a bounded window itself (datasets_repo.aggregate), so a
    quiet day inside the range comes back as 0 rather than missing."""
    from core.repos import datasets as datasets_repo
    cols = _dataset_columns(entry, slug, systems_dir, warnings)
    if not cols:
        return []
    q = entry.get("query") or {}
    if entry.get("ts_field") and entry.get("by_ts_field"):
        # A nominated Date column: the step path used to bucket on the time
        # each row was SAVED whatever the chart said (chart builder brief:
        # "do not hardcode datetime fields"). The column lives in JSONB, so
        # the rows of the window are read -- filtered on that column in the
        # query, capped -- and stepped here on the same local edges.
        return _rows_by_step(entry, slug, config_dir, cols, bucket, since,
                             until, warnings)
    res = datasets_repo.aggregate(slug, entry["dataset"], cols,
                                  agg=q.get("agg", "count"),
                                  field=entry.get("field") or None,
                                  bucket=bucket, filters=q.get("filters") or {},
                                  since=since, until=until,
                                  config_dir=config_dir)
    return [(b["key"], b["value"]) for b in res.get("buckets") or []]


def _rows_by_step(entry, slug, config_dir, cols, bucket, since, until,
                  warnings):
    """[(step start ISO, value)] for a table input stepped along its own Date
    column (entry["ts_field"]). At most MAX_TS_ROWS rows, said when capped."""
    from core.repos import datasets as datasets_repo
    from core.services import builder
    from core.services import timeaxis
    q = entry.get("query") or {}
    ts_field = entry["ts_field"]
    res = datasets_repo.query(slug, entry["dataset"], cols,
                              filters=q.get("filters") or {},
                              order=ts_field, descending=False,
                              limit=MAX_TS_ROWS, config_dir=config_dir,
                              field_window=(ts_field, since, until))
    rows = res.get("rows") or []
    if len(rows) >= MAX_TS_ROWS:
        _warn(warnings, entry,
              f"only the first {MAX_TS_ROWS} rows were charted by {ts_field}")
    step = timeaxis.clean_step(bucket) or "1d"
    if q.get("agg") == "changes":
        # Count Value Changes over the rows in their date order, each change
        # in the step where the new value appears (builder.changes_by).
        def step_of(row):
            epoch = _epoch(row.get(ts_field))
            return None if epoch is None else timeaxis.floor(epoch, step)
        counts = builder.changes_by(rows, entry.get("field"), step_of)
        return [(_iso(edge), counts[edge]) for edge in sorted(counts)]
    cells = {}
    for row in rows:
        epoch = _epoch(row.get(ts_field))
        if epoch is None:
            continue                    # a blank or unreadable date: no step
        cells.setdefault(timeaxis.floor(epoch, step), []).append(row)
    return [(_iso(edge), builder._agg_value(cells[edge], q.get("agg", "count"),
                                            entry.get("field") or None))
            for edge in sorted(cells)]


def _dress(w, out, warnings):
    """The card's own paint, on every shape this module returns. Left off,
    a colour or a unit chosen in the inspector is stored, previewed and then
    never drawn -- the trap builder.compute() documents at length."""
    out["color"] = w.get("color") or None
    out["unit"] = w.get("unit") or None
    if warnings:
        out["warnings"] = warnings
    return out


def _warn(warnings, entry, message):
    warnings.append({"id": entry.get("id") or "",
                     "label": entry.get("label") or entry.get("key") or "",
                     "message": message})


def _fold(values, agg):
    """Several store buckets landing in ONE grid cell, folded to one number.
    Only reachable when the grid is coarser than the finest bucket the store
    would give (a 2-hour grid asking for hourly events); a grid FINER than a
    source cannot happen, because history.grid floors the step at the
    sampler's own resolution."""
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    if agg == "changes":
        # Value changes DO add up: each change belongs to exactly one row
        # (the stores look back over the whole ordered series).
        return round(sum(nums), 4)
    if agg == "unique":
        # Distinct counts do NOT add up: the same value in two buckets is one
        # value. The paths below ask the store (or the rows) for the merged
        # bucket itself, so this only ever sees one answer per point; the
        # largest is the safe reading if two ever meet -- a lower bound,
        # never an inflated sum.
        return max(nums)
    if agg == "sum":
        return round(sum(nums), 4)
    if agg == "avg":
        return round(sum(nums) / len(nums), 4)
    if agg == "min":
        return min(nums)
    if agg == "max":
        return max(nums)
    if agg == "count":
        return len(nums)
    return nums[-1]                                         # last


def _epoch(key):
    """A store bucket key (an ISO timestamp, from Postgres or the in-memory
    fallback) as a number, or None when it is not a timestamp at all."""
    try:
        return datetime.datetime.fromisoformat(str(key)).timestamp()
    except (TypeError, ValueError):
        return None


def _store_bucket(step):
    """The interval key both stores understand for a grid step. The event
    store takes "<n>s" only below an hour (events_repo.bucket_seconds), so
    coarser grids fall back to the calendar buckets and the extra store
    buckets per cell are folded by _fold."""
    step = int(step)
    if step < 5:
        return "5s"
    if step < 3600:
        return f"{step}s"
    if step < 86400:
        return "hour"
    return "day"


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat()


def _time_data(w, entries, events, stats, config_dir, slug, systems_dir,
               warnings):
    """Every series resampled onto one bucket grid."""
    from core.services import builder
    from core.services import history as history_service
    g = history_service.grid(w.get("range") or "24h", w.get("interval") or "",
                             now=w.get("_until"), since=w.get("_since"))
    since_iso, until_iso = _iso(g["start"]), _iso(g["start"] + g["buckets"] * g["step"])
    bucket = _store_bucket(g["step"])
    memo = {}                   # identical specs inside one widget cost once
    datasets = []
    for entry in entries:
        source = entry["source"]
        if entry.get("incomplete"):
            # A row still being filled in. Empty and named, never asked: it
            # has no spec to ask WITH, and the chart has seven other series
            # that must keep drawing while this one is typed.
            _warn(warnings, entry, entry["incomplete"])
            values = [None] * g["buckets"]
        elif source in ("history", "stat"):
            # A stat series and a history series read the SAME ring: the
            # sampler's input is the stats dict, so the key space is one key
            # space. What a stat entry means on a time axis is its history.
            # The values are read either way, so an unknown key draws exactly
            # what it drew before this warning existed -- the warning names
            # the typo, it does not change the picture.
            if not history_service.has(entry["key"]):
                _warn(warnings, entry, "no samples recorded for this key")
            values = history_service.values_on_grid(entry["key"], entry["agg"], g)
        elif source == "query":
            # A distinct count per cell must be counted over the CELL: ask the
            # store for buckets exactly the grid's step wide, not hourly ones
            # that would have to be added up.
            q_bucket = (f"{int(g['step'])}s"
                        if (entry.get("query") or {}).get("agg") == "unique" else bucket)
            values = _on_grid(_memo(memo, _spec_key(entry),
                                    lambda: _event_groups(entry, slug, events,
                                                          config_dir, bucket=q_bucket,
                                                          since=since_iso,
                                                          until=until_iso)),
                              g, _store_fold(entry))
        elif source == "dataset":
            values = _on_grid(_memo(memo, _spec_key(entry),
                                    lambda: _dataset_time(entry, slug, config_dir,
                                                          systems_dir, bucket, g,
                                                          since_iso, until_iso,
                                                          warnings)),
                              g, _store_fold(entry))
        else:                                               # bucket
            # A bucket stat carries labels, not timestamps. Rather than invent
            # a time for each, the series comes back empty and says why.
            _warn(warnings, entry, "a bucket series has no time axis")
            values = [None] * g["buckets"]
        datasets.append({"id": entry["id"], "label": _label(entry),
                         "color": entry["color"] or None, "values": values})
    labels, datasets = history_service.trim_leading(g["labels"], datasets)
    cut = len(g["labels"]) - len(labels)
    picked = w.get("interval") or ""
    out = {"labels": labels, "datasets": datasets, "interval_s": g["step"],
           **_axis_info(g["keys"][cut:], g.get("widened") or picked
                        or "%ds" % int(g["step"]), bool(g.get("widened"))),
           # The RESOLVED window ("page" already mapped in compute), so a
           # tooltip or a test can say what was actually drawn.
           "range": w.get("range") or "24h"}
    return _dress(w, out, warnings)


def _store_fold(entry):
    """How the store values landing in one grid cell fold, for a query or a
    dataset series.

    A store value is ALREADY an aggregate -- a count of rows in that store
    bucket -- so "count" here would count the store buckets, not the rows:
    every cell holding any rows read 1 (or 2 where two hourly buckets shared
    a cell). That is how a table counted per hour showed a flat line of ones.
    Two counts in one cell add up, so a count folds as a sum; every other
    fold already means the same thing on a store value as on a sample."""
    if (entry.get("query") or {}).get("agg") == "unique":
        return "unique"
    return "sum" if entry.get("agg") == "count" else entry.get("agg")


def _memo(memo, key, build):
    """Identical specs inside one widget are read once. Two series differing
    only in colour is not a hypothetical -- it is what a workflow compile
    emits when the same node feeds two cards -- and it would otherwise be two
    round trips to the same store for the same numbers."""
    if key not in memo:
        memo[key] = build()
    return memo[key]


def _spec_key(entry):
    """What makes two series the same QUESTION: everything except the id, the
    label and the colour, which are how it is drawn, not what is asked."""
    return json.dumps({k: v for k, v in entry.items()
                       if k not in ("id", "label", "color")},
                      sort_keys=True, default=str)


def _on_grid(pairs, g, agg):
    """[(bucket key, value)] from a store, placed on the grid. Anything
    outside the window is dropped rather than clamped into an edge bucket --
    the same rule the sampler uses, for the same reason: a window ending last
    Tuesday must not collect the rest of the week in its final point."""
    start, step, buckets = g["start"], g["step"], g["buckets"]
    cells = [[] for _ in range(buckets)]
    for key, value in pairs or ():
        epoch = _epoch(key)
        if epoch is None:
            continue
        idx = int((epoch - start) // step)
        if 0 <= idx < buckets:
            cells[idx].append(value)
    return [_fold(cell, agg) if cell else None for cell in cells]


def _label(entry):
    return (entry.get("label") or entry.get("key")
            or entry.get("dataset") or entry["id"])


# -- per-source readers ------------------------------------------------------

def _event_groups(entry, slug, events, config_dir, bucket=None, group_by=None,
                  since=None, until=None):
    """[(key, value)] for a query series, from the persisted event store or --
    when it is not reachable -- from the master's in-memory ring, which is the
    same fallback a single-source query widget has. A dashboard whose
    detections are visibly flowing must not draw a flat zero because Postgres
    is down."""
    from core.repos import events as events_repo
    from core.services import builder
    q = entry.get("query") or {}
    filters = q.get("filters") or {}
    agg, field = q.get("agg", "count"), q.get("field") or None
    if events_repo.available(config_dir):
        res = events_repo.aggregate(slug, agg=agg, field=field,
                                    group_by=group_by, bucket=bucket,
                                    filters=filters, since=since, until=until,
                                    config_dir=config_dir)
        return [(b["key"], b["value"]) for b in res.get("buckets") or []]

    rows = []
    for ev in events or []:
        row = events_repo.expand(ev, slug)
        ts = str(row.get("ts") or "")
        if since and ts < since:
            continue
        if until and ts >= until:
            continue
        if any(str(row.get(col)) != str(val) for col, val in filters.items()):
            continue
        rows.append(row)
    if agg == "changes" and field:
        # Count Value Changes in time order (the store's window query, in
        # memory): along time over the whole series, inside each group.
        rows.sort(key=lambda r: str(r.get("ts") or ""))
        counts = builder.changes_by(
            rows, field,
            (lambda r: builder._bucket_key(r.get("ts"), bucket)) if bucket
            else (lambda r: None if r.get(group_by) is None else str(r.get(group_by)))
            if group_by else (lambda r: ""),
            partition=bool(group_by and not bucket))
        return [(k, counts[k]) for k in sorted(counts)]
    grouped = {}
    for row in rows:
        if bucket:
            grouped.setdefault(builder._bucket_key(row.get("ts"), bucket), []).append(row)
        elif group_by:
            key = row.get(group_by)
            if key is not None:
                grouped.setdefault(str(key), []).append(row)
        else:
            grouped.setdefault("", []).append(row)
    return [(k, builder._agg_value(grouped[k], agg, field)) for k in sorted(grouped)]


def _dataset_columns(entry, slug, systems_dir, warnings):
    """The entry's declared columns, narrowed to what the table CURRENTLY
    declares. The declaration is the whitelist every name in the spec was
    checked against, so a table that has been deleted or renamed leaves
    nothing askable -- which is a named empty series, not an exception."""
    cols = entry.get("columns") or []
    if not systems_dir:
        return cols                 # an older caller: the snapshot stands
    try:
        from core.services import datasets as datasets_service
        live = datasets_service.definition(systems_dir, slug, entry["dataset"])
    except Exception:                                       # noqa: BLE001
        return cols                 # unreadable definition: show what we have
    if not live:
        _warn(warnings, entry, f"no table named {entry['dataset']}")
        return []
    keep = {c["name"] for c in live.get("columns") or []}
    narrowed = [c for c in cols if c.get("name") in keep]
    if cols and not narrowed:
        _warn(warnings, entry, "none of this series' columns still exist")
    return narrowed


def _dataset_time(entry, slug, config_dir, systems_dir, bucket, g, since, until,
                  warnings):
    """[(key, value)] for a dataset series on a time axis.

    Two paths, and the cheap one is the default. Bucketing on the row's INGEST
    time is something the store does itself, in SQL. Bucketing on a `ts_field`
    the operator nominated cannot be: the column lives in JSONB, and the
    repo's bucket expressions only speak `ts`. Those rows come back here and
    are bucketed in Python, capped at MAX_TS_ROWS -- expensive, opt-in, and it
    says when it was capped rather than drawing a partial window as if it were
    the whole one.
    """
    from core.repos import datasets as datasets_repo
    from core.services import builder
    cols = _dataset_columns(entry, slug, systems_dir, warnings)
    if not cols:
        return []
    q = entry.get("query") or {}
    field = entry.get("field") or None
    distinct = q.get("agg") == "unique"
    if not entry.get("ts_field") and not distinct:
        res = datasets_repo.aggregate(slug, entry["dataset"], cols,
                                      agg=q.get("agg", "count"), field=field,
                                      bucket=bucket, filters=q.get("filters") or {},
                                      since=since, until=until,
                                      config_dir=config_dir)
        return [(b["key"], b["value"]) for b in res.get("buckets") or []]

    # A distinct count over a grid cell is counted from the cell's ROWS (the
    # store's hourly buckets cannot be added up into a 3-hour cell). With no
    # Date column the rows are placed by the time they were saved.
    ts_field = entry.get("ts_field") or "ts"
    # The window on the row's OWN date column ("Date column"), in the query
    # (datasets_repo.query field_since/field_until), so a picked range reads
    # those rows -- not the first thousand of all time trimmed afterwards.
    if entry.get("ts_field"):
        res = datasets_repo.query(slug, entry["dataset"], cols,
                                  filters=q.get("filters") or {},
                                  order=ts_field, descending=False,
                                  limit=MAX_TS_ROWS, config_dir=config_dir,
                                  field_window=(ts_field, since, until))
    else:
        res = datasets_repo.query(slug, entry["dataset"], cols,
                                  filters=q.get("filters") or {},
                                  since=since, until=until, order="ts",
                                  descending=False, limit=MAX_TS_ROWS,
                                  config_dir=config_dir)
    rows = res.get("rows") or []
    if len(rows) >= MAX_TS_ROWS:
        _warn(warnings, entry,
              f"only the first {MAX_TS_ROWS} rows were charted by {ts_field}")
    start, step, buckets = g["start"], g["step"], g["buckets"]
    if q.get("agg") == "changes":
        # Count Value Changes along the rows' own order, never per cell.
        def cell_of(row):
            epoch = _epoch(row.get(ts_field))
            idx = None if epoch is None else int((epoch - start) // step)
            return idx if idx is not None and 0 <= idx < buckets else None
        counts = builder.changes_by(rows, field, cell_of)
        return [(_iso(start + idx * step), counts[idx]) for idx in sorted(counts)]
    cells = {}
    for row in rows:
        epoch = _epoch(row.get(ts_field))
        if epoch is None:
            continue
        idx = int((epoch - start) // step)
        if 0 <= idx < buckets:
            cells.setdefault(idx, []).append(row)
    return [(_iso(start + idx * step),
             builder._agg_value(cells[idx], q.get("agg", "count"), field))
            for idx in sorted(cells)]


def _dataset_groups(entry, slug, config_dir, systems_dir, warnings):
    """[(group key, value)] for a dataset series grouped by a column."""
    from core.repos import datasets as datasets_repo
    cols = _dataset_columns(entry, slug, systems_dir, warnings)
    if not cols:
        return []
    q = entry.get("query") or {}
    since, until = _since_until(q)
    # A nominated Date column filters on that column, not on the saved time
    # ("Only include rows from", chart builder refactor).
    window = (entry["ts_field"], since, until) \
        if entry.get("ts_field") and entry.get("by_ts_field") \
        and (since or until) else None
    res = datasets_repo.aggregate(slug, entry["dataset"], cols,
                                  agg=q.get("agg", "count"),
                                  field=entry.get("field") or None,
                                  group_by=q.get("group_by") or None,
                                  filters=q.get("filters") or {},
                                  since=None if window else since,
                                  until=None if window else until,
                                  config_dir=config_dir,
                                  # Only when there is one: the call an
                                  # older entry makes is unchanged.
                                  **({"field_window": window} if window else {}))
    return [(b["key"], b["value"]) for b in res.get("buckets") or []]


def _bucket_map(entry, stats, warnings):
    """A bucket stat is {label: value} -- the map a counter keeps per day, per
    zone, per shift. THE BUG THIS FIXES: the inspector has always offered the
    series editor on a bucket widget and builder._data ignored the list, so
    every edit made there was silently dropped and the card went on drawing
    the widget's single key."""
    value = (stats or {}).get(entry["key"])
    if not isinstance(value, dict):
        _warn(warnings, entry, "this key holds no per-label totals")
        return []
    return [(str(k), value[k]) for k in sorted(value)]


# -- grouped (label-set) charts ----------------------------------------------

def _group_label(key):
    """One group's axis label. A bare YYYY-MM-DD is a bucket stat's day key and
    has always been drawn without its year -- a wall card is read across a
    room, and the year is the part nobody is unsure about. Everything else
    goes through the shared label rule."""
    from core.services import builder
    if key is None:
        return builder.BLANK_LABEL
    s = str(key)
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s[5:]
    return builder._short_label(key)


def _is_moment(label):
    """True when a group key is a date or a time, not merely something
    fromisoformat will read. "20260111" parses as a date there, and so an
    8-digit batch or order number was capped as if it were a day. A real
    stamp has its separators: a dash, a T or a colon."""
    s = "" if label is None else str(label)
    return (any(ch in s for ch in "-T:") and not s.lstrip("-").replace(".", "", 1).isdigit()
            and _epoch(s) is not None)


def _cap_labels(labels, warnings):
    """At most MAX_LABELS of a sorted label set -- WHICH ones depends on what
    the labels are.

    Days and hours (a counter's per-day map, a bucketed store) keep the LAST
    of them: the newest, which is what a chart over time is read for. Names --
    defect types, cameras, shifts -- keep the FIRST: the list is in name order
    (builder.name_key), a reader starts at A, and dropping the front of the
    alphabet was a chart whose first bar was somewhere in the middle of the
    list with nothing to say the rest had gone. Either way the cut is said.
    """
    if len(labels) <= MAX_LABELS:
        return labels
    timed = all(_is_moment(label) for label in labels)
    kept = labels[-MAX_LABELS:] if timed else labels[:MAX_LABELS]
    warnings.append({"id": "", "label": "",
                     "message": f"{len(labels)} groups - the chart draws the "
                                f"{'latest' if timed else 'first'} "
                                f"{MAX_LABELS}"})
    return kept


def _label_data(w, entries, events, stats, config_dir, slug, systems_dir,
                warnings):
    """Series that each carry a LABEL SET -- a bucket map, a grouped query --
    aligned on the UNION of their labels.

    Union, not intersection: a zone that raised alarms today and a zone that
    did not are both facts about today, and an intersection would delete the
    quiet one from the chart entirely. The operator asked for that zone by
    name; answering by removing it is how a dashboard hides exactly the thing
    somebody was checking. Where a series has no value for a group the cell is
    null, not 0, for the same reason everywhere else in this file: nothing
    measured is not a measurement of nothing.
    """
    from core.services import builder
    columns, order = [], []
    for entry in entries:
        source = entry["source"]
        if entry.get("incomplete"):
            pairs = []
            _warn(warnings, entry, entry["incomplete"])
        elif source == "bucket":
            pairs = _bucket_map(entry, stats, warnings)
        elif source == "query":
            since, until = _since_until(entry["query"])
            pairs = _event_groups(entry, slug, events, config_dir,
                                  group_by=(entry["query"] or {}).get("group_by"),
                                  since=since, until=until)
        elif source == "dataset":
            pairs = _dataset_groups(entry, slug, config_dir, systems_dir, warnings)
        else:
            # A history/stat series has one value, not a set: it takes the
            # same label everywhere so it reads as a level across the groups.
            pairs = []
            _warn(warnings, entry, "a sampled key has no groups to align on")
        seen = {}
        for key, value in pairs:
            # A NULL group stays None -- not the string "None" -- so it sorts
            # first (builder.name_key) and is labelled "(blank)".
            seen[None if key is None else str(key)] = value
        columns.append(seen)
        order.extend(k for k in seen)

    labels = _cap_labels(sorted(set(order), key=builder.name_key), warnings)
    datasets = [{"id": e["id"], "label": _label(e), "color": e["color"] or None,
                 "values": [col.get(l) for l in labels]}
                for e, col in zip(entries, columns)]
    out = {"labels": [_group_label(l) for l in labels], "datasets": datasets}
    return _dress(w, out, warnings)


# -- comparison charts -------------------------------------------------------

def _value_data(w, entries, events, stats, config_dir, slug, systems_dir,
                warnings):
    """One value per series: a pie slice, a bar, a radar spoke. The labels
    ARE the series labels, which is what a comparison chart compares.

    The shape stays {labels, values, colors} -- the one a comparison widget
    has always returned -- so a stored pie renders identically whether its
    slices come from one stats dict or from four different stores.
    """
    from core.services import builder
    from core.services import history as history_service
    labels, values, colors, ids = [], [], [], []
    shared = {}                 # the grid is one window: built once, if at all
    for entry in entries:
        source = entry["source"]
        if entry.get("incomplete"):
            # 0, not None: this shape is a list of numbers a pie slices, and
            # a null in it is a slice the renderer cannot size. The warning is
            # what tells the operator the slice is empty because the row is
            # unfinished, not because the count really is nothing.
            value = 0.0
            _warn(warnings, entry, entry["incomplete"])
        elif source in ("stat", "bucket"):
            value = (stats or {}).get(entry["key"])
            if isinstance(value, dict):
                value = sum(value.values())
            try:
                value = float(value or 0)
            except (TypeError, ValueError):
                value = 0.0
        elif source == "history":
            # Its aggregate over the widget's window, not a fresh read: a pie
            # beside a line chart showing the same range should add up to the
            # same story.
            g = _memo(shared, "grid",
                      lambda: history_service.grid(w.get("range") or "24h",
                                                   w.get("interval") or "",
                                                   now=w.get("_until"),
                                                   since=w.get("_since")))
            if not history_service.has(entry["key"]):
                _warn(warnings, entry, "no samples recorded for this key")
            if entry["agg"] == "unique":
                # Different values over the WHOLE window: one cell spanning
                # it, never the per-cell counts added up.
                whole = {**g, "step": g["step"] * g["buckets"], "buckets": 1}
                folded = history_service.values_on_grid(entry["key"], "unique",
                                                        whole)[0]
            else:
                folded = _fold(history_service.values_on_grid(entry["key"],
                                                              entry["agg"], g),
                               entry["agg"])
            value = float(folded or 0)
        elif source == "query":
            since, until = _since_until(entry["query"])
            pairs = _event_groups(entry, slug, events, config_dir,
                                  since=since, until=until)
            value = float(pairs[0][1]) if pairs else 0.0
        else:                                               # dataset
            pairs = _dataset_groups(entry, slug, config_dir, systems_dir, warnings)
            value = float(pairs[0][1]) if pairs else 0.0
        labels.append(_label(entry))
        values.append(value)
        colors.append(entry["color"] or None)
        ids.append(entry["id"])
    return _dress(w, {"labels": labels, "values": values, "colors": colors,
                      "ids": ids}, warnings)
