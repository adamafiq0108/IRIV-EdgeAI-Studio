# -*- coding: utf-8 -*-
"""
repos/events.py -- SQL for the queryable event store (migration 0003).

SQL only, no business logic (docs/conventions.md sec 2). Reads tolerate a
Postgres outage (return empty); the master keeps its in-memory ring as the
live fallback, so analytics degrade to "no history" rather than erroring.

The device sends the minimal contract envelope; `expand()` here lifts the
common attributes out of it into real columns so they can be filtered and
aggregated, keeping the whole `detail` blob for anything not promoted.

Filtering/aggregation take a WHITELISTED column name only -- the column set is
fixed by the schema, never interpolated from a client string, so there is no
SQL-injection surface even though group-by/order-by cannot be parameterised.
"""

import datetime
import re
import time

from core.db import WRITE_TIMEOUT_MS, DbUnavailable, connect

#: How far a row count will actually count before answering "at least this
#: many". An exact count(*) reads every matching row, so on a year of history
#: the pager cost more than the page it was drawn on -- and it ran on every
#: ten-second refresh of the event table.
COUNT_CAP = 20000

#: Rows one prune statement deletes. A year's backlog in a single unbounded
#: DELETE holds its transaction, locks and WAL open for the whole run; in
#: batches, an interrupted sweep has still made progress.
PRUNE_BATCH = 5000

#: Columns a client may filter, order or group by. A name outside this set is
#: rejected before it reaches SQL -- these go into the query string unquoted
#: (identifiers can't be bound), so the whitelist is the safety boundary.
FILTERABLE = (
    "type", "severity", "label", "camera", "zone", "confidence",
    "track_id", "person_id", "object_id", "detection_label", "direction",
    "model", "status", "host_serial", "device_label",
    # Which analytics function raised it. Lets a dashboard card count the
    # events ONE variable produced rather than every event of that type.
    "function",
)
GROUPABLE = FILTERABLE + ("ts",)
ORDERABLE = ("ts", "confidence", "type", "severity", "camera", "zone", "status")

# Every real column, in insert order.
_COLUMNS = (
    "event_id", "ts", "system_slug", "host_serial", "device_label",
    "type", "severity", "label", "snapshot_id",
    "camera", "zone", "confidence", "track_id", "person_id", "object_id",
    "detection_label", "direction", "model", "function", "detail", "status", "notes",
)


def expand(event, system_slug, host_serial=None, device_label=None):
    """One device event (contract envelope) -> a wide row dict for `events`.

    Promotes the common detection attributes out of `detail` into columns; the
    full `detail` is kept as-is. Absent attributes become NULL. `detail` may
    hold a `detections` list -- the strongest detection's fields represent the
    event (a per-frame event is about its most confident object)."""
    detail = event.get("detail") or {}
    dets = detail.get("detections") or []
    top = max(dets, key=lambda d: d.get("confidence", 0), default={}) if dets else {}

    def pick(*keys):
        for src in (event, detail, top):
            for k in keys:
                v = src.get(k)
                if v not in (None, ""):
                    return v
        return None

    confidence = pick("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None

    return {
        "event_id": str(event.get("id") or event.get("event_id") or ""),
        "ts": event.get("ts"),
        "system_slug": system_slug,
        "host_serial": host_serial or event.get("host_serial") or event.get("device"),
        "device_label": device_label,
        "type": event.get("type"),
        "severity": event.get("severity"),
        "label": event.get("label"),
        "snapshot_id": event.get("snapshot_id"),
        "camera": pick("camera", "camera_name"),
        "zone": pick("zone", "entered_zone"),
        "confidence": confidence,
        "track_id": _as_text(pick("track_id", "tracking_id")),
        "person_id": _as_text(pick("person_id")),
        "object_id": _as_text(pick("object_id")),
        "detection_label": pick("detection_label", "object_class") or (top.get("label") if top else None),
        "direction": pick("direction"),
        "model": pick("model", "ai_model"),
        # Set when a function's trigger raised this event (app run.py writes
        # detail.function). NULL for plain detections.
        "function": _as_text(pick("function")),
        "detail": detail or None,
        "status": event.get("status") or "open",
        "notes": event.get("notes"),
    }


def _as_text(v):
    return None if v is None else str(v)


def insert_many(rows, config_dir=None, upsert_keys=None):
    """Upsert expanded event rows. Idempotent on event_id, so re-delivering an
    event (a re-synced cursor) never double-counts. Returns count written; 0 on
    PG-down so ingestion never blocks the sync loop.

    `upsert_keys`: see _fan_out_datasets. Only the local-ingest path passes it."""
    rows = [r for r in rows if r.get("event_id") and r.get("ts")]
    if not rows:
        return 0
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return 0
    try:
        import json
        import psycopg2.extras                                # noqa: PLC0415
        cols = ", ".join(_COLUMNS)
        placeholders = "(" + ", ".join(["%s"] * len(_COLUMNS)) + ")"
        values = []
        for r in rows:
            row = dict(r)
            if row.get("detail") is not None:
                row["detail"] = json.dumps(row["detail"])
            values.append(tuple(row.get(c) for c in _COLUMNS))
        # ON CONFLICT DO NOTHING: an event id already stored is the same event.
        sql = (f"INSERT INTO events ({cols}) VALUES %s "
               "ON CONFLICT (event_id) DO NOTHING")
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, values, template=placeholders)
            written = cur.rowcount
        conn.commit()
        _fan_out_datasets(rows, config_dir, upsert_keys)
        return written
    except Exception:
        return 0
    finally:
        conn.close()


def _fan_out_datasets(rows, config_dir=None, upsert_keys=None):
    """Rows a Dataset output wrote also land in `dataset_rows`.

    The detection app has no database access, so a dataset row travels as an
    ordinary event carrying detail.dataset; this is where it forks off into its
    own table. Done HERE rather than at each call site so both ingestion paths
    -- the paired-agent sync and LocalIngest -- get it from one place.

    "Update the existing row" is honoured only for the LOCAL app's events
    (host '__local__', stamped by master_server._apply_local), and only on
    the table's identity column as `upsert_keys` names it -- read by the
    master from the stored outputs (services/datasets.upsert_keys). A synced device's
    event asking for an update is a plain insert (Pipi N-1).

    Never raises: a dataset write failing must not lose the event that carried
    it, and both inserts are idempotent, so the next replay retries the fork."""
    try:
        from core.repos import datasets as datasets_repo      # noqa: PLC0415
        fan = [d for d in (datasets_repo.expand(
                   r, r.get("system_slug"),
                   upsert_keys if r.get("host_serial") == "__local__" else None)
                   for r in rows) if d]
        if fan:
            datasets_repo.insert_many(fan, config_dir)
    except Exception:                                         # noqa: BLE001
        pass


_AVAILABLE_TTL_S = 15.0
_available_cache = {"ts": 0.0, "ok": False}


def available(config_dir=None):
    """True when the event store is reachable. Callers with an in-memory
    fallback (builder query widgets) use this to decide which source to
    aggregate, instead of mistaking PG-down's empty answer for zero events.

    Cached briefly: with Postgres down each probe costs the full connect
    timeout, and an analytics page holds many query widgets -- probing per
    widget would turn one outage into a multi-second page."""
    import time
    now = time.monotonic()
    if now - _available_cache["ts"] < _AVAILABLE_TTL_S:
        return _available_cache["ok"]
    try:
        conn = connect(config_dir)
        try:
            conn.close()
        except Exception:                                   # noqa: BLE001
            pass
        ok = True
    except DbUnavailable:
        ok = False
    _available_cache["ts"] = now
    _available_cache["ok"] = ok
    return ok


def _where(system_slug, filters, since, until):
    """Build a parameterised WHERE clause. Column names come only from the
    FILTERABLE whitelist (validated by the caller), values are always bound."""
    clauses = ["system_slug = %s"]
    params = [system_slug]
    for col, val in (filters or {}).items():
        if col not in FILTERABLE:
            continue
        if isinstance(val, (list, tuple)):
            if not val:
                continue
            clauses.append(f"{col} = ANY(%s)")
            params.append(list(val))
        else:
            clauses.append(f"{col} = %s")
            params.append(val)
    if since:
        clauses.append("ts >= %s")
        params.append(since)
    if until:
        # Half-open on purpose (start <= ts < end), matching the in-memory
        # fallback in builder._query_local. Inclusive here meant an event
        # stamped exactly at midnight was counted into the PRIOR day by the
        # database and into the next one by the fallback -- the same chart
        # disagreeing with itself depending on whether Postgres was up.
        clauses.append("ts < %s")
        params.append(until)
    return " AND ".join(clauses), params


def query(system_slug, filters=None, since=None, until=None,
          order="ts", descending=True, limit=200, offset=0, config_dir=None):
    """Rows for the event table. Returns {"rows": [...], "total": n}."""
    if order not in ORDERABLE:
        order = "ts"
    limit = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {"rows": [], "total": 0, "capped": False}
    try:
        where, params = _where(system_slug, filters, since, until)
        direction = "DESC" if descending else "ASC"
        with conn.cursor() as cur:
            # Bounded: stop counting at the cap and report "20000+". Nobody
            # pages to row 20000, and an exact total is not worth reading a
            # year of rows to produce, ten seconds apart, for ever.
            cur.execute("SELECT count(*) AS n FROM (SELECT 1 FROM events "
                        f"WHERE {where} LIMIT {COUNT_CAP + 1}) t", params)
            total = cur.fetchone()["n"]
            capped = total > COUNT_CAP
            if capped:
                total = COUNT_CAP
            cur.execute(
                f"SELECT * FROM events WHERE {where} "
                f"ORDER BY {order} {direction} NULLS LAST LIMIT %s OFFSET %s",
                params + [limit, offset])
            rows = [dict(r) for r in cur.fetchall()]
        return {"rows": rows, "total": total, "capped": capped}
    except Exception:
        return {"rows": [], "total": 0, "capped": False}
    finally:
        conn.close()


_AGGREGATES = {
    # "count" is ROWS (count(*), see aggregate): the chart builder calls it
    # "Count records". "count_nonnull" is the rows where the column holds a
    # value -- the brief's "Count non-null values" -- and needs a column.
    "count": "count(*)",
    "count_nonnull": "count(NULLIF(({col})::text, ''))",
    "sum": "sum({col})",
    "avg": "avg({col})",
    "min": "min({col})",
    "max": "max({col})",
    # Different values of the column, nulls and blanks skipped.
    "unique": "count(DISTINCT NULLIF(({col})::text, ''))",
}
_BUCKETS = {"hour": "hour", "day": "day", "week": "week", "month": "month"}

#: Sub-hour buckets, as SECONDS: "10s", "1m", "5m", "15m", "30m". Anything
#: matching <digits><s|m> is accepted so a custom resolution is one interval
#: grammar rather than a second vocabulary of named values. Bounded below at
#: 5s (finer than the app even samples) and above at an hour (where
#: date_trunc's calendar-aligned buckets take over).
_SECS_RE = re.compile(r"^(\d{1,7})([smhd])$")
_UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: The longest fixed interval (the chart time axis's custom ceiling,
#: services/timeaxis.MAX_STEP_S).
MAX_BUCKET_S = 90 * 86400


def bucket_seconds(bucket):
    """Seconds for a fixed-interval bucket key, or None for the calendar ones
    (hour/day/week/month) and anything unrecognised.

    An hour or more is accepted too since the chart time axis grew custom
    steps ("every 6 hours", "every 2 days", services/timeaxis.py): those are
    floored on LOCAL midnight (see _floor_expr), so a 6-hour bucket starts
    at 00:00/06:00/... on the box's clock, not on UTC's."""
    m = _SECS_RE.match(str(bucket or ""))
    if not m:
        return None
    secs = int(m.group(1)) * _UNIT_S[m.group(2)]
    return secs if 5 <= secs <= MAX_BUCKET_S else None


def _local_offset():
    """The box's UTC offset in whole seconds, now (an int: safe in SQL)."""
    import time as _time                                     # noqa: PLC0415
    return int(_time.localtime().tm_gmtoff or 0)


def _floor_expr(col, secs):
    """SQL flooring `col` onto a `secs` grid. Below an hour on the epoch, as
    it always was; an hour or more on local midnight, shifted by the box's
    offset. Both numbers are ints computed here, never request text."""
    secs = int(secs)
    if secs < 3600:
        return f"to_timestamp(floor(extract(epoch FROM {col}) / {secs}) * {secs})"
    off = _local_offset()
    return (f"to_timestamp(floor((extract(epoch FROM {col}) + {off}) / {secs})"
            f" * {secs} - {off})")


#: One calendar bucket's width, for stepping a zero-fill series.
_BUCKET_STEP = {"hour": "1 hour", "day": "1 day", "week": "1 week",
                "month": "1 month"}

#: The most buckets a zero-filled series may synthesise. Past this the fill
#: is skipped rather than clamped -- a sparse chart beats a 100k-point one,
#: and the window that big is a mis-pick, not a rendering problem.
MAX_FILLED_BUCKETS = 2000


def time_bucket_exprs(bucket):
    """(group_expr, step_interval) for a time bucket, or (None, None).

    `group_expr` truncates ts to its bucket; both come from validated
    vocabulary (a regex-extracted int, or a fixed map), never from the raw
    string, so they are safe to place in SQL."""
    secs = bucket_seconds(bucket)
    if secs:
        return _floor_expr("ts", secs), f"{secs} seconds"
    if bucket in _BUCKETS:
        return f"date_trunc('{_BUCKETS[bucket]}', ts)", _BUCKET_STEP[bucket]
    return None, None


def filled_series_sql(inner_sql, bucket, step):
    """Wrap a grouped-by-time query so every bucket in the window exists,
    empty ones as zero. The two `%s::timestamptz` below come FIRST in the
    statement, so the caller must PREPEND (since, until) to the inner
    query's own params -- placeholders bind in text order.

    A GROUP BY returns only buckets that contain rows, so a quiet hour
    simply vanished from "detections per hour" -- the chart drew point to
    point across the gap and silence was invisible. LEFT JOIN against
    generate_series puts the zero on the axis. Both ends are truncated to
    the bucket grid so the series' keys land exactly on the GROUP BY's."""
    secs = bucket_seconds(bucket)
    if secs:
        trunc = _floor_expr("%s::timestamptz", secs)
    else:
        trunc = f"date_trunc('{_BUCKETS[bucket]}', %s::timestamptz)"
    return (f"SELECT gs AS key, coalesce(a.value, 0) AS value "
            f"FROM generate_series({trunc}, {trunc}, interval '{step}') gs "
            f"LEFT JOIN ({inner_sql}) a ON a.key = gs ORDER BY gs")


def _fill_span_ok(bucket, since, until):
    """Whether zero-filling this window stays under MAX_FILLED_BUCKETS."""
    try:
        lo = datetime.datetime.fromisoformat(str(since))
        hi = datetime.datetime.fromisoformat(str(until))
    except (TypeError, ValueError):
        return False
    span = (hi - lo).total_seconds()
    if span <= 0:
        return False
    secs = bucket_seconds(bucket) or {"hour": 3600, "day": 86400,
                                      "week": 604800,
                                      "month": 2592000}.get(bucket)
    return bool(secs) and span / secs <= MAX_FILLED_BUCKETS


def aggregate(system_slug, agg="count", field=None, group_by=None, bucket=None,
              filters=None, since=None, until=None, limit=500, config_dir=None):
    """Grouped aggregation for a chart widget. Returns {"buckets": [{key, value}]}.

    `agg` in _AGGREGATES; `field` the column it applies to (COUNT ignores it);
    `group_by` a FILTERABLE column, OR `bucket` a time granularity on ts. Both
    column names are whitelist-validated before reaching SQL."""
    if agg not in _AGGREGATES and agg != "changes":
        agg = "count"
    col = field if (field in FILTERABLE or field == "ts") else "*"
    if agg in ("unique", "changes") and col == "*":
        # No column: no different values, no changes, to count (unique used
        # to count event ids, i.e. rows).
        return {"buckets": []}
    if agg == "changes":
        expr = None                     # a window query, built below
    elif agg == "count" or (agg == "count_nonnull" and col == "*"):
        # Records, whatever column is named: "count" used to run
        # count(<field>) when a field rode along, which counted non-null
        # values under a label that said "total rows" (chart builder
        # refactor, 2026-10-13). count_nonnull with no column has no nulls
        # to skip, so it is the same question.
        expr = "count(*)"
    else:
        if col == "*":
            col = "event_id"
        expr = _AGGREGATES[agg].format(col=col)

    time_expr, step = time_bucket_exprs(bucket)
    if time_expr:
        group_expr = time_expr
        key_select = f"{time_expr} AS key"
    elif group_by in GROUPABLE:
        group_expr = group_by
        key_select = f"{group_by} AS key"
    else:
        # no grouping -> a single scalar
        group_expr = None
        key_select = "NULL AS key"

    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {"buckets": []}
    try:
        where, params = _where(system_slug, filters, since, until)
        if agg == "changes":
            # "Count Value Changes": how many times the column took a new
            # value, in time order -- the same window query as the table
            # store (datasets_repo.aggregate): along time over the whole
            # ordered series (a change at a bucket boundary counts where the
            # new value appears, so merged buckets add up exactly), inside
            # each group when grouped by a column; nulls and blanks are no
            # value; the first value in the window counts.
            part = "" if time_expr else "PARTITION BY key "
            key_expr = time_expr or (group_by if group_expr else "NULL")
            sql = (f"SELECT key, count(*) AS value FROM ("
                   f"  SELECT key, v, lag(v) OVER ({part}ORDER BY ts) AS prev"
                   f"  FROM (SELECT {key_expr} AS key, ({col})::text AS v, ts"
                   f"        FROM events WHERE {where}) a"
                   f"  WHERE v IS NOT NULL AND v <> '') b"
                   f" WHERE prev IS NULL OR v IS DISTINCT FROM prev"
                   f" GROUP BY key ORDER BY key LIMIT %s")
            params = params + [max(1, min(int(limit or 500), 2000))]
        elif group_expr:
            sql = (f"SELECT {key_select}, {expr} AS value FROM events WHERE {where} "
                   f"GROUP BY {group_expr} ORDER BY key")
            # A bounded time window is zero-filled: silence is a reading. An
            # unbounded one (all time) is not -- there is no first bucket to
            # start the series from.
            fill_until = until or datetime.datetime.now().astimezone().isoformat()
            if time_expr and since and _fill_span_ok(bucket, since, fill_until):
                sql = filled_series_sql(sql, bucket, step)
                params = [since, fill_until] + params
            sql += " LIMIT %s"
            params = params + [max(1, min(int(limit or 500), 2000))]
        else:
            sql = f"SELECT {key_select}, {expr} AS value FROM events WHERE {where}"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            buckets = []
            for r in cur.fetchall():
                key = r["key"]
                buckets.append({"key": key.isoformat() if hasattr(key, "isoformat") else key,
                                "value": float(r["value"]) if r["value"] is not None else 0})
        return {"buckets": buckets}
    except Exception:
        return {"buckets": []}
    finally:
        conn.close()


#: Cached attribute vocabularies: slug -> (computed_at, value).
#:
#: Eight DISTINCT scans over `events`, and the comment below once called them
#: small. On a board with real history they are not: 2.8 SECONDS on the dev
#: Pi, and the workflow editor asked for them on every poll -- 2620 times in a
#: day, each one paying the full cost. That single call WAS the workflow
#: page's load time; everything else it does adds up to forty milliseconds.
#:
#: A short TTL rather than invalidation, because of what this is: the list of
#: values a filter dropdown offers. A camera seen for the first time appears
#: in it within a minute instead of instantly, and nothing else in the product
#: depends on it being exact. Invalidating properly would mean every event
#: write knowing about this cache, which is a lot of coupling to buy a minute.
_ATTR_TTL_S = 60.0
_attr_cache = {}

#: The columns a filter dropdown offers values for. Named ONCE: it was written
#: out three times below, and the two that had no index (severity, model) got
#: added to one copy of the list and not to the schema -- see 0009.
_ATTR_COLUMNS = ("type", "severity", "camera", "zone", "detection_label",
                 "model", "status", "function")

#: One column's distinct values, as a SKIP SCAN rather than a DISTINCT.
#:
#: `SELECT DISTINCT camera ... LIMIT 200` reads every index entry for the
#: system and throws away the duplicates: measured, 187,274 entries walked to
#: emit 8 values, 63ms for eight short strings. Postgres has no loose index
#: scan of its own, so the skip is written by hand -- take the smallest value,
#: then repeatedly ask the index for the next one STRICTLY GREATER. That is
#: one descent per distinct value, so the cost follows how many answers there
#: are (single digits here) instead of how many rows the system has recorded.
#:
#: Needs a (system_slug, col) index to skip along, which is what 0009 finally
#: gives severity and model. Without one this shape is WORSE than the DISTINCT
#: -- a sequential scan per step instead of one overall -- so the two changes
#: ship together.
#:
#: NULLs are excluded at both ends: the seed skips them and `> t.v` can never
#: reach one. The recursive term's scalar subquery yields NULL when the column
#: is exhausted, which is the loop's own stop condition, so the outer filter
#: is what keeps that terminator out of the answer.
_ATTR_SQL = """
WITH RECURSIVE walk AS (
    (SELECT {col} AS v FROM events
      WHERE system_slug = %(slug)s AND {col} IS NOT NULL
      ORDER BY {col} LIMIT 1)
    UNION ALL
    SELECT (SELECT e.{col} FROM events e
             WHERE e.system_slug = %(slug)s AND e.{col} > walk.v
             ORDER BY e.{col} LIMIT 1)
      FROM walk WHERE walk.v IS NOT NULL
)
SELECT v FROM walk WHERE v IS NOT NULL LIMIT %(cap)s
"""

#: Values per column. A dropdown nobody can read is not a feature, and the cap
#: also bounds the walk above on a column that turns out to be high-cardinality
#: (a track id mistakenly made filterable, say).
_ATTR_CAP = 200


def attributes(system_slug, config_dir=None, max_age=_ATTR_TTL_S):
    """Distinct values operators filter by (camera/zone/type/…), for building
    the event-table filter UI. Capped per column, and cached: see above.

    `max_age=0` forces a fresh read, for a caller that has just written and
    needs to see its own value.
    """
    now = time.time()
    hit = _attr_cache.get(system_slug)
    if hit and max_age and (now - hit[0]) < max_age:
        return hit[1]
    out = {}
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return {c: [] for c in _ATTR_COLUMNS}
    try:
        with conn.cursor() as cur:
            for col in _ATTR_COLUMNS:
                # The column name is interpolated, the values are bound. Safe
                # because _ATTR_COLUMNS is a literal in this file -- no caller
                # reaches this string.
                cur.execute(_ATTR_SQL.format(col=col),
                            {"slug": system_slug, "cap": _ATTR_CAP})
                out[col] = [r["v"] for r in cur.fetchall()]
        _attr_cache[system_slug] = (now, out)
        return out
    except Exception:
        # A failed read is NOT cached: the next caller should try again rather
        # than be told "no filters available" for a minute because the
        # database blinked once.
        return {}
    finally:
        conn.close()


def prune_before(epoch, config_dir=None):
    """Delete events older than `epoch` -- the retention sweep's 12-month tier
    ([retention].events_months). Returns rows removed; 0 when Postgres is
    down, so retention never blocks on the database. ONLY the events table:
    nothing else in the schema is retention-managed."""
    try:
        conn = connect(config_dir, timeout_ms=WRITE_TIMEOUT_MS)
    except DbUnavailable:
        return 0
    removed = 0
    try:
        cutoff = datetime.datetime.fromtimestamp(
            float(epoch), tz=datetime.timezone.utc)
        with conn.cursor() as cur:
            # Batched, committing as it goes. Until migration 0007 there was
            # no index on ts alone either -- events_system_ts_idx leads with
            # system_slug, which this predicate does not mention -- so this
            # was a full sequential scan every six hours, for ever.
            while True:
                cur.execute("DELETE FROM events WHERE event_id IN ("
                            "SELECT event_id FROM events WHERE ts < %s "
                            f"LIMIT {PRUNE_BATCH})", (cutoff,))
                gone = cur.rowcount
                conn.commit()
                removed += gone
                if gone < PRUNE_BATCH:
                    break
        return removed
    except Exception:
        # Batches that committed before the failure are genuinely gone.
        return removed
    finally:
        conn.close()


def analyze(config_dir=None):
    """Refresh planner statistics on the two growing tables.

    Nothing in the product had ever run this. After a prune deletes a large
    slice, Postgres still believes the row counts and value distributions it
    measured before -- which is how a query that was an index scan quietly
    becomes a sequential one, with no error and no symptom except a dashboard
    that got slow "for no reason". Cheap, and only ever runs after a sweep."""
    try:
        conn = connect(config_dir, timeout_ms=WRITE_TIMEOUT_MS)
    except DbUnavailable:
        return False
    try:
        conn.set_isolation_level(0)       # ANALYZE cannot run inside a txn
        with conn.cursor() as cur:
            cur.execute("ANALYZE events")
            cur.execute("ANALYZE dataset_rows")
        return True
    except Exception:                                       # noqa: BLE001
        return False
    finally:
        conn.close()


def delete_for_system(system_slug, config_dir=None, strict=False):
    """Delete every stored event for ONE system -- the counters reset.

    Scoped to the slug on purpose: an admin resetting one system's numbers
    must not touch another's history. Returns rows removed; 0 when Postgres
    is down, because the reset is best-effort across several stores and a
    missing database is not a reason to fail the ones that did clear.
    `strict=True` raises instead (Events Log's Clear: "0 cleared" must not
    stand for "not reached"). A delete that ran drops the slug's filter cache.
    """
    slug = str(system_slug or "").strip()
    if not slug:
        return 0
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        if strict:
            raise
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE system_slug = %s", (slug,))
            removed = cur.rowcount
        conn.commit()
        _attr_cache.pop(slug, None)
        return removed
    except Exception:                                       # noqa: BLE001
        if strict:
            raise
        return 0
    finally:
        conn.close()


def set_status(system_slug, event_id, status=None, notes=None, config_dir=None):
    """Operator edits an event (ack/close, add a note). Returns True if updated."""
    conn = connect(config_dir)
    try:
        sets, params = [], []
        if status is not None:
            sets.append("status = %s"); params.append(status)
        if notes is not None:
            sets.append("notes = %s"); params.append(notes)
        if not sets:
            return False
        params += [system_slug, event_id]
        with conn.cursor() as cur:
            cur.execute(f"UPDATE events SET {', '.join(sets)} "
                        "WHERE system_slug = %s AND event_id = %s", params)
            found = cur.rowcount
        conn.commit()
        return found > 0
    finally:
        conn.close()
