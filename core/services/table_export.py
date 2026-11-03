# -*- coding: utf-8 -*-
"""
table_export.py -- one table card's rows as a CSV file, for a date range and
the columns the person picked (Tok Aba, 2026-10-14: "put CSV directly at the
top right of the TABLE widget; allow a custom date range and choosing which
columns").

It replaces the Analytics page's own CSV button, which downloaded what the
browser happened to be SHOWING -- each table's first 15 rows, for the page's
range. This asks the store again, for the card the person clicked:

    the card     found by its widget id in this system's saved layout, and
                 only a table card that reads rows: a Data Table (dataset),
                 the event log (events) or a Query (sql). Nothing else.
    the range    a named one (today, 7d, 30d, all) or a from / to pair of
                 dates, parsed strictly, at most MAX_SPAN_DAYS long; pushed
                 into the store's own SQL, never filtered here afterwards.
    the columns  by the names the card SHOWS, whitelisted against the card's
                 real columns -- never a column name, let alone SQL, taken
                 from the request as it stands.
    the rows     newest first: every row in the range, up to ROW_CAP, a ceiling
                 that exists so one download cannot fill the box's memory (said
                 in X-Truncated when it is reached). A big export takes time and
                 the dialog says so.
    each cell    a text cell that starts like a formula (= + - @, tab, CR) is
                 prefixed with an apostrophe, so a spreadsheet shows it rather
                 than running it. Numbers are left as numbers.

No byte-order mark: the workflow's own CSV attachments carry none either, and
two exports from one box should open the same way.
"""

import csv
import datetime
import io
import re

#: The most rows one download carries. It was 10,000, then the whole table was
#: asked for (Tok Aba, 2026-10-19): this is only the safety ceiling, held in
#: memory as one text, so a download cannot take the box down with it.
ROW_CAP = 1000000
#: Rows asked of the store per round (the repos cap a single read at 1000).
PAGE = 1000
#: The longest custom range, in days.
MAX_SPAN_DAYS = 366
#: The named ranges the dialog offers ("page" arrives as from / to dates).
NAMED = ("today", "7d", "30d", "all")
#: The event log's columns, as its table card shows them.
EVENT_COLUMNS = (("Datetime", "ts"), ("Type", "type"), ("Severity", "severity"),
                 ("Event", "label"), ("Device", "device"))
#: Old labels a request may still send (a dialog opened before the rename, a
#: script) -> the label the card shows now. The time column was "When" until
#: Tok Aba asked for "Datetime" (2026-10-14), matching the Database page.
LEGACY_LABELS = {"When": "Datetime"}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class ExportError(ValueError):
    """A request this card cannot answer, said in words for the dialog."""


# -- the range -----------------------------------------------------------------------

def _day(text):
    if not isinstance(text, str) or not _DATE_RE.match(text):
        raise ExportError("dates must look like 2026-10-14")
    try:
        return datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise ExportError("that is not a real date") from exc


def window(spec, today=None):
    """(since, until, from_label, to_label) for a range spec: {"range": one of
    NAMED} or {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"}. since / until are
    ISO timestamps for the store (until exclusive), None for "all"."""
    today = today or datetime.date.today()
    spec = spec if isinstance(spec, dict) else {}
    named = spec.get("range")
    if named not in (None, "") and named not in NAMED:
        raise ExportError("unknown date range")
    if named == "all":
        return None, None, "all", "time"
    if named in ("today", "7d", "30d"):
        days = {"today": 0, "7d": 6, "30d": 29}[named]
        start, end = today - datetime.timedelta(days=days), today
    else:
        start, end = _day(spec.get("from")), _day(spec.get("to"))
    if end < start:
        raise ExportError("the end date is before the start date")
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        raise ExportError(f"a range can be at most {MAX_SPAN_DAYS} days")
    # Local midnights, with the box's offset, as the cards' own windows are.
    since = datetime.datetime.combine(start, datetime.time.min).astimezone().isoformat()
    until = datetime.datetime.combine(end + datetime.timedelta(days=1),
                                      datetime.time.min).astimezone().isoformat()
    return since, until, start.isoformat(), end.isoformat()


# -- the card ----------------------------------------------------------------------------

def find_card(layout, widget_id):
    """The saved widget with this id on any page of the layout, or None."""
    wid = str(widget_id or "")
    if not wid:
        return None
    for page in (layout or {}).get("pages") or []:
        for w in (page or {}).get("widgets") or []:
            if isinstance(w, dict) and str(w.get("id") or "") == wid:
                return w
    return None


def columns_of(card, systems_dir=None, slug=None):
    """[(label, key)] a table card can export, in the order it shows them.
    ExportError when the card is not a table that reads rows."""
    if not card or card.get("kind") != "table":
        raise ExportError("only a table card can be downloaded")
    source = card.get("source") or "events"
    if source == "events":
        return list(EVENT_COLUMNS)
    if source == "dataset":
        cols = [c for c in card.get("columns") or [] if isinstance(c, dict) and c.get("name")]
        if systems_dir and slug:
            try:
                from core.services import datasets as datasets_service   # noqa: PLC0415
                live = datasets_service.definition(systems_dir, slug, card.get("dataset") or "")
                if live:
                    keep = {c["name"] for c in live.get("columns") or []}
                    cols = [c for c in cols if c["name"] in keep]
            except Exception:                                   # noqa: BLE001
                pass
        return [("Datetime", "ts")] + [(c["name"].replace("_", " "), c["name"]) for c in cols]
    if source == "sql":
        return []            # the statement decides; known only once it runs
    raise ExportError("this table cannot be downloaded")


def pick_columns(available, wanted):
    """The requested labels, whitelisted, in the card's own order."""
    if not isinstance(wanted, list) or not wanted:
        raise ExportError("choose at least one column")
    known = {label for label, _ in available}
    # An old label maps to today's only when the card has today's column, so
    # "When" never reaches a SQL card's own column of that name by accident.
    asked = [LEGACY_LABELS[w] if w in LEGACY_LABELS and w not in known
             and LEGACY_LABELS[w] in known else w
             for w in wanted if isinstance(w, str)]
    unknown = [w for w in asked if w not in known]
    if unknown or len(asked) != len(wanted):
        raise ExportError("unknown column")
    chosen = set(asked)
    return [(label, key) for label, key in available if label in chosen]


# -- the file ---------------------------------------------------------------------------------

def safe_cell(value):
    """A cell a spreadsheet will show rather than run."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        text = value.isoformat()[:19].replace("T", " ")
    # Leading spaces or quotes do not hide a formula: a spreadsheet still
    # reads ' =1+1' or '"=HYPERLINK(...)' as one (Pipi N-5).
    if text.startswith(_FORMULA_START) or text.lstrip(" \"'").startswith(_FORMULA_START):
        try:
            float(text)
            return text                      # a negative number, not a formula
        except ValueError:
            return "'" + text
    return text


def filename(card, from_label, to_label):
    """<card name>_<from>_<to>.csv, letters, digits, - and _ only."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", str(card.get("title") or card.get("id") or "table"))
    base = base.strip("_")[:40] or "table"
    return f"{base}_{from_label}_{to_label}.csv"


def to_csv(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([safe_cell(h) for h in header])
    for r in rows:
        writer.writerow([safe_cell(c) for c in r])
    return buf.getvalue()


# -- reading the rows -----------------------------------------------------------------------------

def _paged(read):
    """Rows from a repo read(limit, offset), newest first, up to ROW_CAP + 1
    (the one extra says the cap was reached)."""
    rows = []
    offset = 0
    while len(rows) <= ROW_CAP:
        got = (read(PAGE, offset) or {}).get("rows") or []
        rows.extend(got)
        if len(got) < PAGE:
            break
        offset += PAGE
    return rows[:ROW_CAP + 1]


def response_headers(made):
    """The headers a CSV download goes out with (shared by the card route and
    the Database page's per-table route)."""
    return {
        "Content-Disposition": f'attachment; filename="{made["name"]}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Row-Count": str(made["rows"]),
        "X-Truncated": "1" if made["truncated"] else "0",
    }


def export(card, spec, wanted, slug, config_dir=None, systems_dir=None, today=None):
    """{"name", "text", "rows", "truncated"} for one table card, or
    ExportError with the reason."""
    since, until, from_label, to_label = window(spec, today)
    source = (card or {}).get("source") or "events"
    available = columns_of(card, systems_dir, slug)
    if source == "events":
        from core.repos import events as events_repo                    # noqa: PLC0415
        cols = pick_columns(available, wanted)
        rows = _paged(lambda limit, offset: events_repo.query(
            slug, since=since, until=until, limit=limit, offset=offset, config_dir=config_dir))
        body = [[r.get(key) for _, key in cols] for r in rows]
    elif source == "dataset":
        from core.repos import datasets as datasets_repo                # noqa: PLC0415
        cols = pick_columns(available, wanted)
        declared = [c for c in card.get("columns") or [] if isinstance(c, dict)]
        filters = ((card.get("query") or {}).get("filters") or {})
        rows = _paged(lambda limit, offset: datasets_repo.query(
            slug, card.get("dataset") or "", declared, filters=filters, since=since,
            until=until, limit=limit, offset=offset, config_dir=config_dir))
        body = [[r.get(key) for _, key in cols] for r in rows]
    else:                                                    # sql
        from core.services import sqlconsole                             # noqa: PLC0415
        tables = {t["key"]: t for t in card.get("tables") or [] if t.get("key")}
        # The statement's own %(from)s / %(to)s are bound to the chosen range,
        # as the card binds them to the page's (builder._sql_window) -- and
        # nothing is bound to a statement that names no period (a literal %
        # would otherwise break it). All time binds the epoch to tomorrow.
        params = None
        if sqlconsole.wants_dates(card.get("sql")):
            from core.services import builder as builder_service          # noqa: PLC0415
            params = {"from": since or builder_service._EPOCH_ISO,
                      "to": until or (datetime.datetime.now().astimezone()
                                      + datetime.timedelta(days=1)).isoformat()}
        res = sqlconsole.run(slug, card.get("sql"), tables, config_dir=config_dir,
                             params=params)
        if not res.get("ok"):
            raise ExportError(str(res.get("error") or "the query failed")[:200])
        names = res.get("columns") or []
        cols = pick_columns([(n.replace("_", " "), n) for n in names], wanted)
        rows = (res.get("rows") or [])[:ROW_CAP + 1]
        body = [[r.get(key) for _, key in cols] for r in rows]
    truncated = len(body) > ROW_CAP
    body = body[:ROW_CAP]
    return {"name": filename(card, from_label, to_label),
            "text": to_csv([label for label, _ in cols], body),
            "rows": len(body), "truncated": truncated}
