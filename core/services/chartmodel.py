"""The chart builder's ONE model: X axis, Y axis, time interval, date range.

WHY THIS MODULE. A chart used to be configured through six overlapping
controls -- "Group by time", "Period", "Count over", "Time range", "Date
column", "One point per" -- and each camera input repeated four of them
(owner brief, 2026-10-13: "Refactor the Chart and Graph Builder"). The
refactor answers four questions once per chart:

    X axis       a database column, or Time
    Y axis       a calculation (count records, count non-null, sum, average,
                 minimum, maximum) and, where it needs one, a column
    Interval     Hour / Day / Week / Month / Custom -- only when X is Time
    Date range   one window for every input (and, with a column on X, an
                 "Only include rows from" filter)

THE STORED SHAPE stays FLAT on the workflow node, beside the keys every
older reader already understands, for three reasons: the Builders write a
compiled card's question back field by field (workflow.CARD_QUESTION_FIELDS),
the stale-tab guard stamps field by field (keep_builder_edits), and an OTA
rollback to the previous release must still draw a chart saved by this one.

    x_type         "time" | "column"           NEW -- marks the new model
    group_by       the X column ("" = one point per input)
    agg            the Y calculation (CALCS; older values still read)
    field          the Y column (recorded-data mode)
    time_interval  "hour" | "day" | "week" | "month" | "custom"   NEW
    bucket         the grouping step (services/timeaxis.py grammar)
    range, range_from, range_to   the one date range
    ts_field       "" automatic | ROW_CLOCK | a date column       NEW
    series[sid]    label, color, show (NEW), and the per-input overrides
                   agg, ts_field, query.group_by, query.range

A node WITHOUT x_type is a chart saved before the refactor and compiles on
the legacy path exactly as before; the editors read it through the legacy
rules (frontend chartModel.js) and write this shape on the first edit.

A Builder widget carries the same model as one `chart` object (clean_widget),
next to the lowered legacy fields its renderer reads.

Pure: no store, no clock, no I/O.
"""

import re

from core.services import timeaxis

#: The two categories "Show as" offers (owner: by purpose, not by how many
#: inputs). The single-value and list/media groups are CARD kinds and keep
#: their own groups; none of this module applies to them.
CHARTS = ("bar", "pie", "donut", "radar")
GRAPHS = ("line", "spline", "area")
CHART_KINDS = CHARTS + GRAPHS

#: Kinds drawn with conventional X and Y axes, and the ones drawn without
#: (a category + a value).
AXES_KINDS = ("bar",) + GRAPHS
CATEGORY_KINDS = ("pie", "donut", "radar")

X_TYPES = ("time", "column")

#: The Y calculations a chart offers, in the brief's order. "count" is every
#: row ("Count records"); "count_nonnull" the rows where the column holds a
#: value.
CALCS = ("count", "count_nonnull", "unique", "changes", "sum", "avg", "min",
         "max")
#: ("unique" is "Count Difference Value": how many different values the
#: column holds per point -- owner, 2026-10-13. It was a card-only answer.)
#: Older Y answers still read on a saved chart (shown "as saved").
#: ("changes" is "Count Value Changes": how many times the column took a new
#: value, in time order -- the cards' old "value changes".)
LEGACY_CALCS = ("latest", "last")
#: Calculations that need a NUMBER column.
NUMERIC_CALCS = ("sum", "avg", "min", "max")

INTERVALS = ("hour", "day", "week", "month", "custom")

#: Each interval preset's default (range, step), and the option it offers.
#: Week and Month default to a DAILY view (the brief: "a seven-day daily
#: view", "a 30-day daily view") with a coarser aggregation one click away.
PRESETS = {
    "hour": {"range": "24h", "step": "1h"},
    "day": {"range": "7d", "step": "1d"},
    "week": {"range": "7d", "step": "1d", "alt": {"range": "90d", "step": "week"}},
    "month": {"range": "30d", "step": "1d", "alt": {"range": "365d", "step": "month"}},
    "custom": {"range": "custom", "step": ""},
}

#: The chart-level "Date column" meaning "when the row was saved" -- an
#: explicit choice, apart from "" (automatic: the table's declared date
#: column, else the saved time). Never a column name: COL_RE rejects "@".
ROW_CLOCK = "@row"

_COL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")

#: "Count over" (window) a single-rule comparison chart reads, from its range
#: (workflow._windowed_metric picks the rule's counter for it).
_WINDOW_OF = {"page": "follow", "today": "today", "week": "week", "7d": "week",
              "month": "month", "30d": "month"}


def layout_of(kind):
    """"axes" (X and Y), "category" (a category + a value), or "" for a kind
    this model does not apply to (the card kinds)."""
    if kind in AXES_KINDS:
        return "axes"
    if kind in CATEGORY_KINDS:
        return "category"
    return ""


def interval_of(step, range_key=""):
    """The preset a stored step reads as, for a chart saved before presets.
    Anything that is not a preset's step (Automatic, every 5 minutes, every
    6 hours, a custom N) is Custom -- which shows the step as it is."""
    s = timeaxis.clean_step(step)
    if s == "1h":
        return "hour"
    if s == "1d":
        return "month" if range_key in ("30d", "month") else "day"
    if s == "week":
        return "week"
    if s == "month":
        return "month"
    return "custom"


def window_for_range(range_key):
    """The "Count over" a single-rule comparison chart reads for a range."""
    return _WINDOW_OF.get(str(range_key or ""), "live")


def clean_ts_field(value):
    value = str(value or "").strip()[:60]
    if value == ROW_CLOCK or value == "" or _COL_RE.match(value):
        return value
    return ""


def clean_calc(value):
    """A Y calculation as it may be stored: one of CALCS, a legacy answer
    (kept, never rewritten), or "" for the compiler's guess."""
    value = str(value or "").strip()
    return value if value in CALCS + LEGACY_CALCS or value == "" else ""


def fold_of(calc):
    """The per-input FOLD (builder.SERIES_AGGS, plus count_nonnull) a Y
    calculation is stored as on an input override."""
    return {"latest": "last"}.get(calc, calc)


def _same_ts(chart_ts, series_ts):
    """Whether an input's own Date column says what the chart's does."""
    if chart_ts == ROW_CLOCK:
        return series_ts == ""
    return bool(chart_ts) and chart_ts == series_ts


def normalize_node(data, kind=None):
    """A chart node's data in the new shape, cleaned. Only a node that HAS
    the model (x_type) is touched -- an older chart is left byte for byte as
    it was, because its legacy keys are what compiles it.

    Idempotent: normalize_node(normalize_node(d)) == normalize_node(d).
    Returns the same dict, changed in place."""
    if not isinstance(data, dict) or data.get("x_type") not in X_TYPES:
        if isinstance(data, dict) and "x_type" in data:
            data.pop("x_type", None)
        return data
    kind = kind or data.get("kind") or "area"
    if kind not in CHART_KINDS:
        return data
    # The history "Time step" is the step now: one field, `bucket`.
    if data.get("interval"):
        if not data.get("bucket"):
            step = timeaxis.clean_step(data["interval"])
            if step:
                data["bucket"] = step
        data["interval"] = ""
    step = timeaxis.clean_step(data.get("bucket"))
    data["bucket"] = step or ""
    if data.get("time_interval") not in INTERVALS:
        data["time_interval"] = interval_of(data["bucket"], data.get("range"))
    data["ts_field"] = clean_ts_field(data.get("ts_field"))
    data["agg"] = clean_calc(data.get("agg"))
    if data["x_type"] == "time":
        # A column X and a time step are alternatives; the model says which.
        data["group_by"] = ""
    # Count over follows the date range on the shapes that read a rule's
    # counter (a comparison of one live rule); a time shape keeps whatever it
    # was saved with -- there it is not a date range at all.
    if kind in CHARTS and data["x_type"] == "column":
        data["window"] = window_for_range(data.get("range"))
    # Overrides that say what the chart says are not overrides.
    series = data.get("series") if isinstance(data.get("series"), dict) else None
    for sid, entry in (series or {}).items():
        if not isinstance(entry, dict):
            continue
        if entry.get("agg") and fold_of(data["agg"]) == entry["agg"]:
            entry.pop("agg")
        q = entry.get("query") if isinstance(entry.get("query"), dict) else None
        if q is not None:
            if q.get("range") and q["range"] == (data.get("range") or ""):
                q.pop("range")
            if q.get("group_by") and data["x_type"] == "column" \
                    and q["group_by"] == data.get("group_by"):
                q.pop("group_by")
            if not q:
                entry.pop("query")
        if "ts_field" in entry and _same_ts(data["ts_field"], entry["ts_field"]):
            entry.pop("ts_field")
        if entry.get("show") is True:
            entry.pop("show")
    if series is not None:
        data["series"] = {k: v for k, v in series.items()
                          if not isinstance(v, dict) or v}
    return data


#: The `chart` object a Builder widget carries, field by field, with how each
#: is cleaned.
WIDGET_FIELDS = ("x_type", "x_field", "y_agg", "y_field", "time_interval",
                 "step", "range", "range_from", "range_to", "ts_field")


def clean_widget(raw):
    """A Builder widget's `chart` model, cleaned, or {} when it has none (a
    chart made before the refactor -- its lowered fields are what draw it)."""
    if not isinstance(raw, dict) or raw.get("x_type") not in X_TYPES:
        return {}
    col = lambda v: (str(v or "").strip()[:60]                    # noqa: E731
                     if _COL_RE.match(str(v or "").strip()[:60]) else "")
    step = timeaxis.clean_step(raw.get("step")) or ""
    rng = timeaxis.clean_range(raw.get("range"))
    out = {"x_type": raw["x_type"],
           "x_field": col(raw.get("x_field")) if raw["x_type"] == "column" else "",
           "y_agg": clean_calc(raw.get("y_agg")),
           "y_field": col(raw.get("y_field")),
           "time_interval": raw.get("time_interval")
           if raw.get("time_interval") in INTERVALS else interval_of(step, rng),
           "step": step,
           "range": rng or "",
           "ts_field": clean_ts_field(raw.get("ts_field"))}
    if out["range"] == "custom":
        out["range_from"] = timeaxis.clean_moment(raw.get("range_from"))
        out["range_to"] = timeaxis.clean_moment(raw.get("range_to"))
    overrides = clean_overrides(raw.get("overrides"))
    if overrides:
        out["overrides"] = overrides
    return out


_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


def clean_overrides(raw):
    """Per-input overrides carried inside a widget's `chart`: {sid: {agg,
    field, group_by, range, ts_field}}, at most 8, each value cleaned. The
    editors read them back so a chart reopens with exactly what was set."""
    if not isinstance(raw, dict):
        return {}
    col = lambda v: (str(v or "").strip()[:60]                    # noqa: E731
                     if _COL_RE.match(str(v or "").strip()[:60]) else "")
    out = {}
    for sid, o in list(raw.items())[:8]:
        if not _SID_RE.match(str(sid)) or not isinstance(o, dict):
            continue
        entry = {}
        if clean_calc(o.get("agg")):
            entry["agg"] = clean_calc(o.get("agg"))
        for key in ("field", "group_by"):
            if col(o.get(key)):
                entry[key] = col(o[key])
        if "range" in o and (o["range"] == "" or timeaxis.clean_range(o["range"])):
            entry["range"] = o["range"] or ""
        if "ts_field" in o and o["ts_field"] is not None:
            entry["ts_field"] = clean_ts_field(o["ts_field"])
        if entry:
            out[str(sid)] = entry
    return out


#: How a chart is DRAWN (rendering addendum, owner 2026-10-13: "the user can
#: see the X and Y axis clearly, and every detail"): the axis titles (blank =
#: automatic, from the configuration), a bar chart's orientation and style,
#: and whether values are printed on the chart ("" = automatic: on when there
#: are few points). Look, so the Builders' Appearance section sets them and a
#: compiled card keeps them on its node (workflow.CARD_LOOK_FIELDS).
LOOK_FIELDS = ("x_title", "y_title", "orientation", "bar_style", "data_labels")
ORIENTATIONS = ("vertical", "horizontal")
BAR_STYLES = ("bar", "lollipop")
DATA_LABELS = ("on", "off")


def clean_look_field(field, value):
    """One LOOK_FIELDS value as it may be stored ("" = the default)."""
    value = str(value or "").strip()
    if field in ("x_title", "y_title"):
        return value[:40]
    allowed = {"orientation": ORIENTATIONS, "bar_style": BAR_STYLES,
               "data_labels": DATA_LABELS}.get(field, ())
    return value if value in allowed else ""


def clean_look(w, kind=None):
    """The look fields a chart widget carries, only those that are set -- a
    chart that never had one is stored byte for byte as before."""
    kind = kind or (w or {}).get("kind")
    if kind not in CHART_KINDS or not isinstance(w, dict):
        return {}
    out = {}
    for field in LOOK_FIELDS:
        value = clean_look_field(field, w.get(field))
        if value and (kind == "bar" or field not in ("orientation", "bar_style")):
            out[field] = value
    return out


_STEP_TITLES = {"1h": "Hour", "1d": "Day", "week": "Week", "month": "Month"}
_CALC_WORDS = {"count_nonnull": "Count of", "sum": "Sum", "avg": "Average",
               "min": "Minimum", "max": "Maximum", "latest": "Latest",
               "last": "Latest", "unique": "Count Difference Value of",
               "changes": "Count Value Changes of"}


def axis_titles(w):
    """{"x", "y", "time"}: what a chart's axes MEAN, from its configuration --
    the X column or the time step, the Y calculation and its column, and the
    unit. Mirrors frontend chartModel.autoAxisTitles. A chart saved before the
    builder model is read off its query, its step and its first input."""
    w = w if isinstance(w, dict) else {}
    model = w.get("chart") if isinstance(w.get("chart"), dict) else {}
    q = w.get("query") if isinstance(w.get("query"), dict) else {}
    rows = [s for s in (w.get("series") or []) if isinstance(s, dict)]
    first = rows[0] if rows else {}
    fq = first.get("query") if isinstance(first.get("query"), dict) else {}
    if model.get("x_type") in X_TYPES:
        x_type, x_field = model["x_type"], model.get("x_field") or ""
        step = model.get("step") or ""
        y_agg, y_field = model.get("y_agg") or "", model.get("y_field") or ""
    else:
        x_field = q.get("group_by") or fq.get("group_by") or ""
        step = timeaxis.clean_step(q.get("bucket") or fq.get("bucket")
                                   or w.get("interval")) or ""
        timed = bool(step) or w.get("source") == "history" or w.get("kind") in GRAPHS
        x_type = "column" if x_field or not timed else "time"
        y_agg = q.get("agg") or fq.get("agg") or ""
        if not y_agg and first.get("agg"):
            y_agg = {"last": "latest"}.get(first["agg"], first["agg"])
        y_field = q.get("field") or first.get("field") or ""
    if x_type == "column":
        x = x_field.replace("_", " ") if x_field else "Input"
    else:
        s = timeaxis.clean_step(step)
        x = _STEP_TITLES.get(s) or (timeaxis.step_words(s) if s else "") or "Time"
    col = str(y_field or "").replace("_", " ")
    if y_agg in ("", "count"):
        y = "Total rows" if (x_type == "column" or y_agg == "count"
                             or w.get("source") in ("dataset", "query")) else \
            (col or "Value")
    elif y_agg == "count_nonnull":
        y = f"Count of {col}" if col else "Count non-null"
    elif y_agg == "unique":
        y = f"Count Difference Value of {col}" if col else "Count Difference Value"
    elif y_agg == "changes":
        y = f"Count Value Changes of {col}" if col else "Count Value Changes"
    else:
        words = _CALC_WORDS.get(y_agg, y_agg)
        y = f"{words} {col}".strip() if col else words
    unit = str(w.get("unit") or "").strip()
    if unit:
        y = f"{y} ({unit})"
    return {"x": x[:40], "y": y[:60], "time": x_type == "time"}


def node_model(data):
    """The model a node holds, in the widget's names, for the Builders to
    show on a compiled card ({} for a chart saved before the refactor --
    the Builders then read it through the legacy rules)."""
    data = data if isinstance(data, dict) else {}
    if data.get("x_type") not in X_TYPES:
        return {}
    overrides = {}
    for sid, conf in (data.get("series") or {}).items() \
            if isinstance(data.get("series"), dict) else ():
        if not isinstance(conf, dict):
            continue
        q = conf.get("query") if isinstance(conf.get("query"), dict) else {}
        o = {}
        if conf.get("agg"):
            o["agg"] = "latest" if conf["agg"] == "last" else conf["agg"]
        if q.get("group_by"):
            o["group_by"] = q["group_by"]
        if q.get("range"):
            o["range"] = q["range"]
        if "ts_field" in conf:
            o["ts_field"] = conf.get("ts_field") or ""
        if o:
            overrides[sid] = o
    return clean_widget({
        "overrides": overrides,
        "x_type": data["x_type"], "x_field": data.get("group_by"),
        "y_agg": data.get("agg"), "y_field": data.get("field"),
        "time_interval": data.get("time_interval"),
        "step": data.get("bucket") or data.get("interval"),
        "range": data.get("range"), "range_from": data.get("range_from"),
        "range_to": data.get("range_to"), "ts_field": data.get("ts_field")})
