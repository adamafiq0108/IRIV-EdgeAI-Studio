# -*- coding: utf-8 -*-
"""
repos/datasets.py -- SQL for user-defined tables (migration 0006).

SQL only, no business logic (docs/conventions.md sec 2). Reads tolerate a
Postgres outage (return empty) exactly like repos/events.py, so a dataset
widget degrades to "no rows" rather than erroring the analytics page.

THE SAFETY BOUNDARY. `events` has a fixed column set, so its whitelist is a
constant (events.FILTERABLE). A dataset's columns are named by the operator, so
there is no constant to check against -- and the names still cannot be bound as
parameters, because they become `cols->>'name'` fragments and identifiers do not
bind. Two checks stand in for the constant, and BOTH must pass:

  1. the name matches COL_RE -- letters, digits, underscore only, so nothing a
     quote or comment could hide in ever reaches the query string;
  2. the name appears in the dataset's DECLARED columns, which every caller
     passes in from the compiled workflow on the server. A client naming a
     column the operator never declared is refused before SQL.

Callers must pass `columns`; there is deliberately no "skip validation" path.
"""

import datetime
import re

from core.db import WRITE_TIMEOUT_MS, DbUnavailable, connect

#: A legal dataset or column name. Deliberately narrow -- these are interpolated
#: into SQL, so the character set is the first half of the injection defence.
COL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: Column value types a table may declare, and what each one MEANS here.
#:
#: There are eight because that is what an operator recognises, not because
#: the storage needs eight: `cols` is JSONB, so the type is a promise about
#: the value rather than a column definition. What it decides is how a value
#: is normalised on the way in, how it sorts, and which questions it can
#: answer -- see _ref and aggregate below.
#:
#:   number    -- cast for arithmetic aggregation
#:   text      -- counted, filtered, grouped
#:   boolean   -- true/false; counted and grouped, never summed
#:   date      -- a day, ISO-8601 (YYYY-MM-DD)
#:   timestamp -- a moment, ISO-8601. Both store text on purpose: ISO already
#:                compares chronologically, and Postgres has no try_cast, so
#:                one malformed value would break every query over the table.
#:   json      -- a nested value kept whole. Not filtered or aggregated: there
#:                is no single thing to compare.
#:   image     -- a path or URL to a picture, rendered as one
#:   uuid      -- an identifier, compared exactly
COL_TYPES = ("number", "text", "boolean", "date", "timestamp",
             "json", "image", "uuid")

#: Types that behave as plain text in SQL: exact match, chronological or
#: lexical sort, groupable. Everything except `number` and `json`.
_TEXTUAL = ("text", "boolean", "date", "timestamp", "image", "uuid")

#: Older names, kept readable rather than migrated. A stored table written
#: before the type set was widened says "datetime"; rewriting every config on
#: disk to say "timestamp" would be a migration whose only benefit is that one
#: word, so the old name is simply understood.
_TYPE_ALIASES = {"datetime": "timestamp"}

#: Types that store an ISO-8601 string and therefore sort chronologically as
#: plain text. See _ref for why they are never cast.
TIME_TYPES = ("date", "timestamp")

_AGGREGATES = {
    # "count" counts ROWS ("Count records"); count({col}) is the separate
    # "Count non-null values" question (chart builder refactor, 2026-10-13).
    "count": "count(*)",
    "count_nonnull": "count(NULLIF(({col})::text, ''))",
    "sum": "sum({col})",
    "avg": "avg({col})",
    "min": "min({col})",
    "max": "max({col})",
    # "Count Difference Value": how many DIFFERENT values the column holds
    # in the bucket -- nulls and blanks are no value, as for count_nonnull.
    "unique": "count(DISTINCT NULLIF(({col})::text, ''))",
    #: Not a plain aggregate -- it needs to see the row BEFORE each row, so
    #: aggregate() builds it as a window query instead of substituting here.
    #: Listed anyway so it passes the same validation as the rest.
    "changes": None,
}

#: Aggregations that are answered by a window function rather than a plain
#: GROUP BY, and so need a `field` to look back along.
_WINDOW_AGGS = ("changes",)
#: Aggregations that need arithmetic, so a `text` column cannot take them --
#: Postgres would raise on the cast rather than return a wrong answer.
_NUMERIC_AGGS = ("sum", "avg", "min", "max")
_BUCKETS = {"hour": "hour", "day": "day", "week": "week", "month": "month"}

#: How far a row count will actually count before answering "this many, at
#: least". See query() -- an exact count over a year of rows is the most
#: expensive part of drawing a page nobody scrolls to the end of.
COUNT_CAP = 20000

#: Rows one prune statement deletes. A year's backlog in a single unbounded
#: DELETE holds a transaction (and its locks, and its WAL) open for as long as
#: it takes; in batches, an interrupted sweep has still made progress.
PRUNE_BATCH = 5000


def col_types(columns):
    """The declared columns -> {name: type}, dropping anything malformed.

    Accepts the compiled definition's list of dicts, or an already-built map.
    This is what turns an operator's declaration into a whitelist."""
    if isinstance(columns, dict):
        items = columns.items()
    else:
        items = [((c or {}).get("name"), (c or {}).get("type"))
                 for c in (columns or [])]
    out = {}
    for name, kind in items:
        name = str(name or "").strip()
        if not COL_RE.match(name):
            continue
        kind = _TYPE_ALIASES.get(kind, kind)
        out[name] = kind if kind in COL_TYPES else "text"
    return out


def _ref(name, kind):
    """A column's SQL expression. `name` MUST already have passed col_types().

    A `datetime` deliberately stays TEXT here rather than being cast to
    timestamptz. ISO-8601 already sorts and compares chronologically as text,
    and Postgres has no try_cast -- one malformed value in one row would make
    the cast raise for the whole query, taking a page down over a typo."""
    if kind == "number":
        # NULLIF: a blank string is a missing number, not a cast error.
        return f"NULLIF(cols->>'{name}', '')::double precision"
    return f"cols->>'{name}'"


#: "Update the existing row": the row in the same table already holding this
#: object's id, NEWEST first. A table written in "add a new row" mode before
#: the switch may hold several rows for one id (pcb_sensata does); only the
#: most recent is refreshed and the older ones are left as history. There is
#: deliberately NO unique index behind this -- creating one would fail on
#: exactly those tables -- so the lock below is what keeps two writers from
#: both missing the row and both inserting.
#:
#: `cols @> {col: id}` rather than `cols->>col = id`: containment is what the
#: jsonb_path_ops GIN index answers (migration 0007), so finding one object
#: in a year of rows is an index probe, not a walk back through the table.
_UPSERT_SQL = (
    "UPDATE dataset_rows SET cols = cols || %s::jsonb, ts = %s "
    "WHERE event_id = ("
    "  SELECT event_id FROM dataset_rows "
    "  WHERE system_slug IS NOT DISTINCT FROM %s AND dataset = %s "
    "    AND deleted_at IS NULL AND cols @> %s::jsonb "
    "  ORDER BY ts DESC, created_at DESC LIMIT 1)")
_INSERT_ONE_SQL = ("INSERT INTO dataset_rows "
                   "(event_id, ts, system_slug, dataset, cols) "
                   "VALUES (%s, %s, %s, %s, %s) "
                   "ON CONFLICT (event_id) DO NOTHING")


def _upsert_rows(cur, rows):
    """Write "update the existing row" rows: refresh that object's row, or
    add it when the table has none. Returns rows written.

    Collapsed to the LAST write per object first. The log is replayed from
    offset 0 on every boot, and a table refreshed every few seconds would
    otherwise cost one statement per historical write on each restart; only
    the final state of each object survives a batch anyway. Safe however the
    replay splits into batches: each batch ends on the object's latest values.

    The update overwrites every column it was given (the identity column is
    one of them, with the value it was found by, so it never changes) and
    moves the row's `ts` to now: the row says when the object was LAST seen.
    The first sighting is still recoverable from the row's event_id, which is
    the event that created it and carries its own millisecond stamp."""
    import json                                              # noqa: PLC0415
    last = {}
    for r in rows:
        col = r["upsert"]
        last[(r.get("system_slug"), str(r["dataset"]), col,
              str(r["cols"].get(col)))] = r
    written = 0
    for (slug, dataset, col, ident), r in last.items():
        # One writer per object at a time, for this transaction. Without a
        # unique key two ingests could both miss the row and both insert it.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"dsrow|{slug}|{dataset}|{col}|{ident}",))
        cur.execute(_UPSERT_SQL, (json.dumps(r["cols"]), r["ts"], slug, dataset,
                                  json.dumps({col: ident})))
        if not cur.rowcount:
            cur.execute(_INSERT_ONE_SQL, (r["event_id"], r["ts"], slug, dataset,
                                          json.dumps(r["cols"])))
        written += max(0, cur.rowcount or 0)
    return written


def insert_many(rows, config_dir=None):
    """Upsert dataset rows. Idempotent on event_id, so replaying the event log
    (which the master does from offset 0 on every boot) never duplicates a row.
    Returns count written; 0 on PG-down so ingestion never blocks the sync.

    A row carrying `upsert` (a column name) comes from a table set to "update
    the existing row" and goes through _upsert_rows instead, in the same
    transaction. Every other row is inserted exactly as it always was."""
    clean = []
    for r in rows or []:
        if not (r.get("event_id") and r.get("ts") and r.get("dataset")):
            continue
        if not isinstance(r.get("cols"), dict):
            continue
        clean.append(r)
    if not clean:
        return 0
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return 0
    try:
        import json
        import psycopg2.extras                                # noqa: PLC0415
        sql = ("INSERT INTO dataset_rows "
               "(event_id, ts, system_slug, dataset, cols) VALUES %s "
               "ON CONFLICT (event_id) DO NOTHING")
        plain = [r for r in clean if not r.get("upsert")]
        keyed = [r for r in clean if r.get("upsert")]
        values = [(r["event_id"], r["ts"], r.get("system_slug"),
                   str(r["dataset"]), json.dumps(r["cols"]))
                  for r in plain]
        written = 0
        with conn.cursor() as cur:
            if values:
                psycopg2.extras.execute_values(cur, sql, values)
                written = cur.rowcount
            if keyed:
                written += _upsert_rows(cur, keyed)
        conn.commit()
        return written
    except Exception:                                         # noqa: BLE001
        return 0
    finally:
        conn.close()


#: (dataset, key) pairs whose refused upsert hint has been logged already, so
#: a replay of a long log says it once, not once per event. Bounded.
_refused_logged = set()


def _refuse_upsert(name, key):
    if (name, key) in _refused_logged:
        return
    if len(_refused_logged) >= 200:
        _refused_logged.clear()
    _refused_logged.add((name, key))
    import logging                                           # noqa: PLC0415
    logging.getLogger(__name__).debug(
        "[DATASET] upsert hint %r on table %r ignored (not that table's "
        "identity column, or the event is not from the local app); "
        "inserted as a new row", key, name)


def expand(event, system_slug, upsert_keys=None):
    """One event carrying detail.dataset -> a dataset_rows dict, or None.

    Every event passes through here on ingest; only the ones a Dataset output
    wrote carry `dataset`, so this is the filter as well as the transform.

    `upsert_keys` is {table: {identity column, ...}} read from the system's
    OWN stored outputs (services/datasets.py upsert_keys), and only the local
    app's events are given it (repos/events _fan_out_datasets). Without it --
    every paired device's event -- a hint is a plain insert (Pipi N-1, 1.0.38
    gate): a paired device could otherwise rewrite the newest row matching
    any column of any table. The table's current on_repeat is deliberately
    not consulted: see services/datasets.upsert_keys_from on log replay."""
    detail = event.get("detail") or {}
    if not isinstance(detail, dict):
        return None
    name = str(detail.get("dataset") or "").strip()
    cols = detail.get("cols")
    if not COL_RE.match(name) or not isinstance(cols, dict):
        return None
    event_id = str(event.get("id") or event.get("event_id") or "")
    if not event_id or not event.get("ts"):
        return None
    row = {"event_id": event_id, "ts": event.get("ts"),
           "system_slug": system_slug, "dataset": name, "cols": cols}
    # "Update the existing row": detail.upsert names the identity column the
    # app keyed this write on. Honoured only when it is a legal column name
    # AND the row really holds an id in it -- a row about nobody in
    # particular has no existing row to be, so it is inserted as before. A
    # hint the stored outputs do not back is refused the same way: a
    # plain insert, never an error, so the event is never lost.
    key = str(detail.get("upsert") or "").strip()
    if key and COL_RE.match(key) and cols.get(key) not in (None, ""):
        if key in ((upsert_keys or {}).get(name) or ()):
            row["upsert"] = key
        else:
            _refuse_upsert(name, key)
    return row


def _where(system_slug, dataset, types, filters, since, until):
    """Parameterised WHERE. Column names are interpolated (identifiers cannot
    bind) but only after passing COL_RE *and* the declared-column check above;
    every VALUE is bound."""
    import json                                              # noqa: PLC0415
    # A deleted row is FLAGGED, never removed: the master replays the event
    # log from offset 0 on every boot, so a real delete would be undone by the
    # next restart (migration 0008).
    clauses = ["system_slug = %s", "dataset = %s", "deleted_at IS NULL"]
    params = [system_slug, str(dataset)]
    for col, val in (filters or {}).items():
        kind = types.get(col)
        if kind is None:                     # not declared -> never reaches SQL
            continue
        values = list(val) if isinstance(val, (list, tuple)) else [val]
        if not values:
            continue
        if kind == "json":
            continue        # no single thing to compare -- see COL_TYPES
        if kind == "number":
            # A number is stored as a JSON number, so containment would demand
            # an exact float repr match ("5" not matching 5.0). Compare the
            # text form instead and accept the scan: an exact-equality filter
            # on a measured number is rare, and wrong-but-indexed is worse.
            ref = _ref(col, "text")
            if len(values) > 1:
                clauses.append(f"{ref} = ANY(%s)")
                params.append([str(v) for v in values])
            else:
                clauses.append(f"{ref} = %s")
                params.append(str(values[0]))
            continue
        # Text and datetime: containment, so the GIN index on `cols` can
        # actually serve it (migration 0007). `cols->>'x' = %s` is equivalent
        # and reads more naturally, but no GIN index can answer it, which made
        # every filter on a big table a sequential scan.
        ors = []
        for value in values:
            ors.append("cols @> %s::jsonb")
            params.append(json.dumps({col: str(value)}))
        clauses.append("(" + " OR ".join(ors) + ")")
    if since:
        clauses.append("ts >= %s")
        params.append(since)
    if until:
        # Half-open (start <= ts < end), same contract as events._where and
        # the in-memory fallback: a row stamped exactly on the boundary
        # belongs to the window that STARTS there, once, everywhere.
        clauses.append("ts < %s")
        params.append(until)
    return " AND ".join(clauses), params


def _with_field_window(where, params, types, field_window):
    """A window on a DECLARED date column (a chart's "Date column"), added to
    a WHERE: ISO text compares in time order, the same reading _ref gives a
    datetime. The name has passed col_types; both ends are bound."""
    if field_window and field_window[0] in types:
        name, lo, hi = field_window
        if lo:
            where += f" AND cols->>'{name}' >= %s"
            params = params + [str(lo)]
        if hi:
            where += f" AND cols->>'{name}' < %s"
            params = params + [str(hi)]
    return where, params


def count_exact(system_slug, dataset, columns, filters=None, since=None, until=None,
                config_dir=None):
    """The EXACT number of rows, or None when the store is down.

    query() stops counting at COUNT_CAP and says "20000+": that bound is what
    keeps a table that is redrawn every few seconds cheap. A person looking at
    one table that is past the cap still wants the number, so the Database page
    asks for it ONCE, here, for the table on screen -- an unbounded count(*),
    on request only, never on a refresh."""
    types = col_types(columns)
    if not types:
        return 0
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return None
    try:
        where, params = _where(system_slug, dataset, types, filters, since, until)
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM dataset_rows WHERE {where}", params)
            return int(cur.fetchone()["n"])
    finally:
        conn.close()


def query(system_slug, dataset, columns, filters=None, since=None, until=None,
          order="ts", descending=True, limit=200, offset=0, config_dir=None,
          field_window=None):
    """Rows for the dataset table. Returns {"rows": [...], "total": n}.

    Each row is flattened to {ts, event_id, <declared columns>} -- the caller
    gets exactly the shape the operator declared, with undeclared leftovers
    from a since-renamed column dropped rather than leaking into the table."""
    types = col_types(columns)
    if not types:
        return {"rows": [], "total": 0, "capped": False}
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    order_expr = "ts" if order not in types else _ref(order, types[order])
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {"rows": [], "total": 0, "capped": False}
    try:
        where, params = _where(system_slug, dataset, types, filters, since, until)
        where, params = _with_field_window(where, params, types, field_window)
        direction = "DESC" if descending else "ASC"
        with conn.cursor() as cur:
            # A bounded count. An exact count(*) reads every matching row, so
            # on a table with a year of history the pager cost more than the
            # page -- and it ran on every ten-second refresh. Stop at the cap
            # and say "20000+": nobody pages to row 20000, and the difference
            # between "a lot" and the exact number is not worth a full scan.
            cur.execute(
                "SELECT count(*) AS n FROM (SELECT 1 FROM dataset_rows "
                f"WHERE {where} LIMIT {COUNT_CAP + 1}) t", params)
            total = cur.fetchone()["n"]
            capped = total > COUNT_CAP
            if capped:
                total = COUNT_CAP
            cur.execute(
                f"SELECT event_id, ts, cols, edited_at FROM dataset_rows "
                f"WHERE {where} "
                f"ORDER BY {order_expr} {direction} NULLS LAST LIMIT %s OFFSET %s",
                params + [limit, offset])
            rows = []
            for r in cur.fetchall():
                cols = r["cols"] if isinstance(r["cols"], dict) else {}
                row = {"event_id": r["event_id"], "ts": r["ts"],
                       # So the grid can mark what a person typed apart from
                       # what the system measured.
                       "edited": r["edited_at"] is not None}
                for name in types:
                    row[name] = cols.get(name)
                rows.append(row)
        return {"rows": rows, "total": total, "capped": capped}
    except Exception:                                         # noqa: BLE001
        return {"rows": [], "total": 0, "capped": False}
    finally:
        conn.close()


def latest(system_slug, dataset, columns, field, filters=None, since=None,
           until=None, group_by=None, limit=500, config_dir=None):
    """The most recently recorded value of one column.

    Not an aggregate -- SQL has no "newest" operator, and max() is a different
    question (the biggest value, which for a fluctuating reading is usually
    some spike from last Tuesday). This is what a live tile means by the
    value: the last thing actually written.

    With `group_by`, one latest value PER distinct value of that column --
    "the current reading for each machine", which is the question a fleet
    tile asks and which neither max() nor a plain latest can answer. Returns
    a bare value ungrouped, or [{key, value}] grouped.
    """
    types = col_types(columns)
    kind = types.get(field)
    if kind is None or kind == "json":
        return [] if group_by else None
    grouped = group_by in types and group_by != field \
        and types[group_by] != "json"
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return [] if grouped else None
    try:
        where, params = _where(system_slug, dataset, types, filters, since, until)
        with conn.cursor() as cur:
            if grouped:
                # DISTINCT ON keeps the first row of each group, and the ORDER
                # BY decides which that is -- the newest. The alternative (a
                # correlated max(ts) subquery) reads the table twice.
                gref = _ref(group_by, "text")
                cur.execute(
                    f"SELECT DISTINCT ON ({gref}) {gref} AS key, "
                    f"{_ref(field, kind)} AS value FROM dataset_rows "
                    f"WHERE {where} AND cols ? %s "
                    f"ORDER BY {gref}, ts DESC LIMIT %s",
                    params + [field, max(1, min(int(limit or 500), 2000))])
                return [{"key": r["key"], "value": r["value"]}
                        for r in cur.fetchall()]
            cur.execute(f"SELECT {_ref(field, kind)} AS v FROM dataset_rows "
                        f"WHERE {where} AND cols ? %s "
                        "ORDER BY ts DESC LIMIT 1", params + [field])
            row = cur.fetchone()
        return row["v"] if row else None
    except Exception:                                         # noqa: BLE001
        return [] if grouped else None
    finally:
        conn.close()


def aggregate(system_slug, dataset, columns, agg="count", field=None,
              group_by=None, bucket=None, filters=None, since=None, until=None,
              limit=500, config_dir=None, field_window=None):
    """Grouped aggregation for a chart. Returns {"buckets": [{key, value}]}.

    `field_window` = (column, since, until) filters on a declared date column
    (a chart's "Date column") instead of -- or as well as -- the saved time.

    Arithmetic aggregations are refused on anything but a `number` column: the
    operator asked for the average of something that has no average, and an
    empty answer is honest where a Postgres cast error would just break the
    page. A `datetime` counts and groups; it does not sum."""
    types = col_types(columns)
    if not types:
        return {"buckets": []}
    if agg not in _AGGREGATES:
        agg = "count"

    kind = types.get(field)
    # "changes" counts TRANSITIONS, so it needs a column to look back along --
    # there is no such thing as how many times "the row" changed.
    if agg in _WINDOW_AGGS and kind is None:
        return {"buckets": []}
    if agg in _WINDOW_AGGS:
        expr = None                     # built below, as a window query
    elif agg == "unique" and kind is None:
        # Different values OF WHAT: with no column there is nothing honest to
        # count (it used to count distinct event ids, i.e. rows).
        return {"buckets": []}
    elif agg == "count" or (agg == "count_nonnull" and kind is None):
        # Records, whatever column rides along -- the label says "total
        # rows". It used to be count(<column>), which skipped the rows where
        # that column was empty (chart builder refactor, 2026-10-13).
        expr = "count(*)"
    elif kind is None:
        expr = _AGGREGATES[agg].format(col="event_id")
    else:
        if agg in _NUMERIC_AGGS and kind != "number":
            return {"buckets": []}
        expr = _AGGREGATES[agg].format(col=_ref(field, kind))

    # One interval grammar for both stores: the named calendar buckets plus
    # events_repo's "<n>s"/"<n>m" custom resolutions, so an Event Card and a
    # dataset card can never disagree about what "per 5 minutes" means.
    from core.repos import events as events_repo
    time_expr, step = events_repo.time_bucket_exprs(bucket)
    if time_expr:
        group_expr = time_expr
    elif group_by in types and types[group_by] != "json":
        # Never by a json column: a nested value has no single thing to
        # compare, and grouping by its text makes one bar per whole document.
        group_expr = _ref(group_by, "text")
    else:
        group_expr = None

    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {"buckets": []}
    try:
        where, params = _where(system_slug, dataset, types, filters, since, until)
        where, params = _with_field_window(where, params, types, field_window)
        if agg in _WINDOW_AGGS:
            # How many times this column took a NEW value, in time order.
            #
            # A live counting table logs the object that is in frame, so the
            # count of pid CHANGES is the number of objects that came through
            # -- which is the question, and one no plain aggregate can answer:
            # count() counts rows and would score standing still, unique()
            # counts values and would score somebody who left and came back
            # only once.
            #
            # Nulls are dropped BEFORE the look-back, not treated as a value.
            # A frame the tracker had not confirmed yet sits between two rows
            # of the same object, and counting 3 -> null -> 3 as two changes
            # would turn detector flicker into traffic.
            #
            # "Count Value Changes" on a chart (owner, 2026-10-13): along TIME
            # the look-back runs over the WHOLE ordered series, so a change
            # at a bucket boundary is counted in the bucket where the new
            # value appears, each change belongs to exactly one row, and the
            # counts add up exactly when buckets are merged. Grouped by a
            # COLUMN the look-back stays inside each group (changes per
            # category). Ordered by the chart's Date column when it has one,
            # else by the time the row was saved. Blanks are no value, like
            # nulls. The first value in the window counts.
            key_expr = group_expr or "NULL"
            ref = _ref(field, kind)
            part = "" if time_expr else "PARTITION BY key "
            order = (f"cols->>'{field_window[0]}'"
                     if field_window and field_window[0] in types else "ts")
            sql = (f"SELECT key, count(*) AS value FROM ("
                   f"  SELECT key, v, lag(v) OVER ({part}ORDER BY o)"
                   f"         AS prev"
                   f"  FROM (SELECT {key_expr} AS key, {ref} AS v, {order} AS o"
                   f"        FROM dataset_rows WHERE {where}) a"
                   f"  WHERE v IS NOT NULL AND (v)::text <> '') b"
                   # The first value counts: the first object through is one
                   # object, not zero.
                   f" WHERE prev IS NULL OR v IS DISTINCT FROM prev"
                   f" GROUP BY key ORDER BY key LIMIT %s")
            params = params + [max(1, min(int(limit or 500), 2000))]
        elif group_expr:
            sql = (f"SELECT {group_expr} AS key, {expr} AS value "
                   f"FROM dataset_rows WHERE {where} "
                   f"GROUP BY {group_expr} ORDER BY key")
            # Zero-fill a bounded time window, exactly as events.aggregate
            # does: a quiet hour is a zero on the axis, not a gap.
            fill_until = (until
                          or datetime.datetime.now().astimezone().isoformat())
            if time_expr and since \
                    and events_repo._fill_span_ok(bucket, since, fill_until):
                sql = events_repo.filled_series_sql(sql, bucket, step)
                # Prepended: the series' two placeholders come first in the
                # statement, and placeholders bind in text order.
                params = [since, fill_until] + params
            sql += " LIMIT %s"
            params = params + [max(1, min(int(limit or 500), 2000))]
        else:
            sql = (f"SELECT NULL AS key, {expr} AS value "
                   f"FROM dataset_rows WHERE {where}")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            buckets = []
            for r in cur.fetchall():
                key = r["key"]
                buckets.append({
                    "key": key.isoformat() if hasattr(key, "isoformat") else key,
                    "value": float(r["value"]) if r["value"] is not None else 0})
        return {"buckets": buckets}
    except Exception:                                         # noqa: BLE001
        return {"buckets": []}
    finally:
        conn.close()


def values(system_slug, dataset, columns, config_dir=None):
    """Distinct values per column, for building the filter UI. Capped per
    column; number columns are skipped (a filter dropdown of floats is noise)."""
    types = col_types(columns)
    out = {}
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {n: [] for n, k in types.items() if k == "text"}
    try:
        with conn.cursor() as cur:
            for name, kind in types.items():
                if kind != "text":
                    continue
                ref = _ref(name, "text")
                cur.execute(
                    f"SELECT DISTINCT {ref} AS v FROM dataset_rows "
                    "WHERE system_slug = %s AND dataset = %s "
                    "AND deleted_at IS NULL "
                    f"AND {ref} IS NOT NULL ORDER BY 1 LIMIT 200",
                    (system_slug, str(dataset)))
                out[name] = [r["v"] for r in cur.fetchall()]
        return out
    except Exception:                                         # noqa: BLE001
        return {}
    finally:
        conn.close()


def insert_row(system_slug, dataset, cols, config_dir=None):
    """Add one row by hand, from the Database tool. Returns its id, or None.

    Given an id of its own (`manual-...`) rather than an event id, because no
    event produced it. That also keeps it clear of the log's namespace, so a
    replay can never collide with a row a person typed."""
    import json                                              # noqa: PLC0415
    import time                                              # noqa: PLC0415
    import uuid                                              # noqa: PLC0415
    if not isinstance(cols, dict):
        return None
    row_id = f"manual-{uuid.uuid4().hex[:20]}"
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dataset_rows "
                "(event_id, ts, system_slug, dataset, cols, edited_at) "
                "VALUES (%s, to_timestamp(%s), %s, %s, %s, now())",
                (row_id, time.time(), system_slug, str(dataset),
                 json.dumps(cols)))
        conn.commit()
        return row_id
    except Exception:                                         # noqa: BLE001
        return None
    finally:
        conn.close()


def update_row(system_slug, dataset, row_id, cols, config_dir=None):
    """Change cells on one row. `cols` is merged, not replaced, so editing one
    cell cannot silently blank the rest of the row. Returns True if it hit."""
    import json                                              # noqa: PLC0415
    if not isinstance(cols, dict) or not cols:
        return False
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return False
    try:
        with conn.cursor() as cur:
            # `||` merges the JSONB objects: the supplied keys win, everything
            # else on the row is left exactly as it was.
            cur.execute(
                "UPDATE dataset_rows SET cols = cols || %s::jsonb, "
                "edited_at = now() WHERE system_slug = %s AND dataset = %s "
                "AND event_id = %s AND deleted_at IS NULL",
                (json.dumps(cols), system_slug, str(dataset), str(row_id)))
            hit = cur.rowcount
        conn.commit()
        return hit > 0
    except Exception:                                         # noqa: BLE001
        return False
    finally:
        conn.close()


def delete_row(system_slug, dataset, row_ids, config_dir=None):
    """Flag rows deleted. Returns how many.

    A flag rather than a DELETE, because the master replays the event log from
    offset 0 on every boot: a real delete would be undone by the next restart,
    and the operator would find the row back with no explanation."""
    ids = [str(r) for r in (row_ids or []) if str(r or "").strip()][:500]
    if not ids:
        return 0
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE dataset_rows SET deleted_at = now() "
                "WHERE system_slug = %s AND dataset = %s AND event_id = ANY(%s) "
                "AND deleted_at IS NULL",
                (system_slug, str(dataset), ids))
            gone = cur.rowcount
        conn.commit()
        return gone
    except Exception:                                         # noqa: BLE001
        return 0
    finally:
        conn.close()


def table_stats(system_slug, keys, config_dir=None):
    """{key: {rows, capped, first_ts, last_ts}} for the Database page.

    One grouped pass rather than a query per table. The count is EXACT, never
    "20000+" (Tok Aba, 2026-10-21): the table list and the table header show
    the real number of rows. It is one grouped scan per page load, bounded by
    the per-statement timeout in core/db; the pager's own count still stops at
    COUNT_CAP (query()), because that one runs on every refresh."""
    wanted = [str(k) for k in (keys or []) if COL_RE.match(str(k))]
    out = {k: {"rows": 0, "capped": False, "bytes": 0,
               "first_ts": None, "last_ts": None}
           for k in wanted}
    if not wanted:
        return out
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT dataset, count(*) AS n, min(ts) AS first_ts, "
                # Roughly what this table costs on disk. pg_column_size is the
                # compressed width of the JSONB itself -- close enough for the
                # "how big is this getting" question the page asks, and far
                # cheaper than pg_total_relation_size, which would report the
                # whole shared table for every logical one.
                "max(ts) AS last_ts, sum(pg_column_size(cols)) AS bytes "
                "FROM dataset_rows "
                "WHERE system_slug = %s AND dataset = ANY(%s) "
                "  AND deleted_at IS NULL "
                "GROUP BY dataset",
                (system_slug, wanted))
            for row in cur.fetchall():
                key = row["dataset"]
                if key not in out:
                    continue
                count = int(row["n"] or 0)
                out[key] = {
                    "rows": count,
                    "capped": False,
                    "bytes": int(row["bytes"] or 0),
                    "first_ts": row["first_ts"].isoformat() if row["first_ts"] else None,
                    "last_ts": row["last_ts"].isoformat() if row["last_ts"] else None,
                }
        return out
    except Exception:                                         # noqa: BLE001
        return out
    finally:
        conn.close()


def prune_before(epoch, config_dir=None):
    """Delete rows older than `epoch` -- the retention sweep's long tier, run
    alongside events.prune_before so a dataset cannot outlive the log it came
    from. Returns rows removed; 0 when Postgres is down."""
    try:
        conn = connect(config_dir, timeout_ms=WRITE_TIMEOUT_MS)
    except DbUnavailable:
        return 0
    removed = 0
    try:
        cutoff = datetime.datetime.fromtimestamp(
            float(epoch), tz=datetime.timezone.utc)
        with conn.cursor() as cur:
            # Batched, committing as it goes: a year's backlog in one DELETE
            # holds its locks and its WAL for the whole run, and being cut off
            # means starting again from nothing next sweep.
            while True:
                cur.execute(
                    "DELETE FROM dataset_rows WHERE event_id IN ("
                    "SELECT event_id FROM dataset_rows WHERE ts < %s "
                    f"LIMIT {PRUNE_BATCH})", (cutoff,))
                gone = cur.rowcount
                conn.commit()
                removed += gone
                if gone < PRUNE_BATCH:
                    break
        return removed
    except Exception:                                         # noqa: BLE001
        # Whatever batches committed before the failure are genuinely gone, so
        # report them rather than claiming the sweep did nothing.
        return removed
    finally:
        conn.close()


def delete_for_system(system_slug, config_dir=None):
    """Delete every dataset row for ONE system -- the counters reset.

    Must run whenever events.delete_for_system does. These rows are a
    projection of the event log; deleting the log without deleting them would
    strand rows that no replay can ever restore or correct."""
    slug = str(system_slug or "").strip()
    if not slug:
        return 0
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM dataset_rows WHERE system_slug = %s", (slug,))
            removed = cur.rowcount
        conn.commit()
        return removed
    except Exception:                                         # noqa: BLE001
        return 0
    finally:
        conn.close()
