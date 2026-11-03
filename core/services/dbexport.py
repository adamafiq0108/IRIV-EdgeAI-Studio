# -*- coding: utf-8 -*-
"""
dbexport.py -- one system's data, as a SQLite file you can open anywhere.

Every system on a box shares ONE Postgres database (`cytron_dashboard`) and is
told apart by a `system_slug` column. That is the right shape for the product
-- one connection, one schema, one place to prune -- and the wrong shape for
somebody who wants to take a single system's data away with them. This carves
one system out of the shared tables and writes it into a file that needs
nothing to read: sqlite3 is in the standard library at both ends.

WHAT IT CARRIES
    events          every event the system recorded
    dataset_rows    every Data Table row, with the JSON columns expanded into
                    a real per-table view so the export is worth opening
    tables          the Data Table definitions, so a column's type survives
    export_meta     what this file is, which system, when, and from where

WHAT IT DOES NOT CARRY, on purpose: snapshots and clips. They are the bulk by
orders of magnitude, Footage already downloads them, and a database file that
takes ten minutes and four gigabytes to produce is one nobody exports twice.
The event rows keep their `snapshot_id`, so an export can still be matched
back to the media it refers to.

Streamed in batches rather than read whole: a system with a year of events is
millions of rows, and building that list in memory to write it out again is
how an export takes the box down instead of just taking a while.
"""

import json
import os
import sqlite3
import tempfile
import time

from core.db import DbUnavailable, connect

#: Rows fetched per round trip. Big enough that the query is not the cost,
#: small enough that peak memory is bounded whatever the table holds.
BATCH = 2000

#: The shared tables that carry a system_slug, and the column that orders them.
#: Kept as data rather than as two hand-written loops so adding a third table
#: later is one line and cannot forget the slug filter -- which is the one
#: mistake that would leak another system's rows into this file.
SOURCES = (
    {"table": "events", "order": "ts"},
    {"table": "dataset_rows", "order": "ts"},
)


def _columns(cur, table):
    """The table's real column names, in ordinal order.

    Read from the catalogue rather than hardcoded: the schema has grown
    columns twice (function, object_id), and an export that names them itself
    would silently stop carrying whatever was added last.
    """
    # core.db hands out a RealDictCursor, so a row is a MAPPING and r[0] is a
    # KeyError, not the first column. Addressed by name throughout for that
    # reason -- and because naming the column is what makes the ordering of
    # the SELECT below irrelevant to correctness.
    # SCHEMA-QUALIFIED. information_schema.columns spans every schema in the
    # database, so a bare table_name match returned each column once per
    # schema holding a table of that name -- which SQLite then rejected as
    # "duplicate column name: event_id". current_schema() is the one the
    # queries below actually read from.
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND table_schema = current_schema() "
        "ORDER BY ordinal_position", (table,))
    return [r["column_name"] for r in cur.fetchall()]


def _sqlite_type(pg_value):
    """SQLite has no dates and no JSON. Text is the honest carrier for both:
    an ISO timestamp sorts and compares correctly as text, and a JSON blob
    stays parseable. Numbers stay numbers so SUM and AVG still work."""
    if pg_value is None:
        return None
    if isinstance(pg_value, (int, float, str)):
        return pg_value
    if isinstance(pg_value, (dict, list)):
        return json.dumps(pg_value, separators=(",", ":"))
    if isinstance(pg_value, (bytes, bytearray)):
        return bytes(pg_value)
    # datetimes, Decimal, UUID and anything else psycopg hands back.
    return str(pg_value)


def export_system(system_slug, out_path=None, config_dir=None, tables=None):
    """Write one system's data to a SQLite file. Returns (path, summary).

    `tables` is the system's Data Table definitions (core/services/datasets
    .definitions) when the caller has them -- they turn the generic
    dataset_rows blob into a real table per dataset, which is the difference
    between an export you can query and one you have to unpick.
    """
    slug = str(system_slug or "").strip()
    if not slug:
        raise ValueError("which system?")

    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix=f"{slug}-", suffix=".db")
        os.close(fd)
    # A half-written file must never be served as an export: build it, then
    # let the caller move it. If we die partway, the caller's temp file is
    # discarded rather than downloaded as a truncated database.
    if os.path.exists(out_path):
        os.unlink(out_path)

    counts = {}
    out = sqlite3.connect(out_path)
    try:
        out.execute("PRAGMA journal_mode = OFF")
        out.execute("PRAGMA synchronous = OFF")
        _write_meta(out, slug)

        try:
            conn = connect(config_dir)
        except DbUnavailable:
            # An export of nothing, clearly labelled, beats a 500: the file
            # says the database was unreachable rather than looking like a
            # system that recorded nothing.
            out.execute("UPDATE export_meta SET note = ?",
                        ("the events database was unreachable - this file "
                         "carries the system's definitions only",))
            out.commit()
            return out_path, {"ok": False, "counts": counts,
                              "error": "the events database is unreachable"}

        problems = []
        try:
            with conn, conn.cursor() as cur:
                for src in SOURCES:
                    # One unexportable table must not cost the others: an
                    # export with events and a note about dataset_rows is
                    # worth far more than no file at all.
                    try:
                        counts[src["table"]] = _copy_table(cur, out, slug, src)
                    except Exception as exc:                # noqa: BLE001
                        problems.append(str(exc))
                _expand_datasets(out, tables or [])
                # The DEFINITIONS, not just the flat views. The docstring at
                # the top has always promised these and the file never carried
                # them, which only mattered once something wanted to read the
                # file back: a `table_<key>` view keeps its column NAMES and
                # loses every type, so an import could rebuild the shape of a
                # Data Table but not what its columns were.
                _write_tables(out, tables or [])
        finally:
            try:
                conn.close()
            except Exception:                               # noqa: BLE001
                pass

        out.commit()
        # Only after everything is in, and only if the table made it: the
        # index is a convenience, never the thing that fails an export.
        have = {r[0] for r in out.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "events" in have:
            out.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
        # Whatever went wrong travels IN the file. Someone opening this in a
        # year has no access to the log that explained it.
        if problems:
            out.execute("UPDATE export_meta SET note = note || ?",
                        ("  NOTE: " + "; ".join(problems),))
        out.commit()
    finally:
        out.close()
    return out_path, {"ok": not problems, "counts": counts,
                      "problems": problems}


def _write_meta(out, slug):
    """What this file is, so it is still self-explanatory in a year."""
    out.execute("""CREATE TABLE export_meta (
        system_slug TEXT, exported_at TEXT, source TEXT, note TEXT)""")
    out.execute("INSERT INTO export_meta VALUES (?, ?, ?, ?)",
                (slug, time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "IRIV EdgeAI Studio - cytron_dashboard",
                 "Events and Data Table rows for this system. Snapshots and "
                 "clips are not included; event rows keep their snapshot_id."))


def _write_tables(out, tables):
    """The Data Table definitions, as the operator designed them.

    Stored as JSON in one column rather than as a row per column: a definition
    is a document (key, name, columns, each with a name and a type), and the
    thing reading this back wants it whole. dbimport reads exactly this.
    """
    out.execute("CREATE TABLE IF NOT EXISTS tables "
                "(key TEXT, name TEXT, columns TEXT)")
    if isinstance(tables, dict):
        tables = list(tables.values())
    for t in tables or []:
        if not isinstance(t, dict):
            continue
        key = str(t.get("key") or t.get("name") or "").strip()
        cols = [c for c in (t.get("columns") or []) if isinstance(c, dict)]
        if not key or not cols:
            continue
        out.execute("INSERT INTO tables VALUES (?, ?, ?)",
                    (key, str(t.get("name") or key), json.dumps(cols)))


def _copy_table(cur, out, slug, src):
    """Stream one shared table's rows for this system into the file."""
    table, order = src["table"], src["order"]
    # A table we cannot describe is a table we cannot export, and the REASON
    # matters: swallowing it produced an export with no events in it and
    # nothing anywhere saying why -- indistinguishable from a system that had
    # never recorded one.
    try:
        cols = _columns(cur, table)
    except Exception as exc:                                # noqa: BLE001
        raise RuntimeError(f"could not read the {table} schema: {exc}")
    if not cols:
        raise RuntimeError(f"the {table} table does not exist in this database")
    # The slug filter is in the SQL, never applied afterwards: a filter that
    # runs in Python is one an early `return` can skip, and the failure mode
    # is another system's rows in this customer's file.
    quoted = ", ".join(f'"{c}"' for c in cols)
    out.execute(f'CREATE TABLE "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
    cur.execute(f'SELECT {quoted} FROM "{table}" WHERE system_slug = %s '
                f'ORDER BY "{order}"', (slug,))
    placeholders = ", ".join("?" * len(cols))
    total = 0
    while True:
        rows = cur.fetchmany(BATCH)
        if not rows:
            break
        # Indexed BY NAME: iterating a RealDictCursor row yields its keys, so
        # `for v in row` would have written the column names into every row.
        out.executemany(f'INSERT INTO "{table}" VALUES ({placeholders})',
                        [tuple(_sqlite_type(row[c]) for c in cols)
                         for row in rows])
        total += len(rows)
    return total


def _expand_datasets(out, tables):
    """One real table per Data Table, from the dataset_rows JSON blob.

    dataset_rows stores `cols` as JSON because a Data Table's shape is the
    operator's, not the schema's. That is right for the product and useless in
    a spreadsheet, so each definition also gets a flat table with its own
    columns -- the same rows, in the shape the person who designed them drew.
    """
    have = {r[0] for r in out.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "dataset_rows" not in have:
        return
    # definitions() answers {key: definition}; a caller with a plain list of
    # definitions is just as valid. Iterating the dict directly would walk its
    # KEYS -- strings -- and quietly export no tables at all.
    if isinstance(tables, dict):
        tables = list(tables.values())
    for t in tables or []:
        if not isinstance(t, dict):
            continue
        key = str(t.get("key") or t.get("name") or "").strip()
        if not key:
            continue
        cols = [str(c.get("name")) for c in (t.get("columns") or [])
                if c.get("name")]
        if not cols:
            continue
        # Prefixed, so a Data Table called "events" cannot collide with the
        # events table itself and quietly replace it.
        name = f"table_{key}"[:60]
        out.execute(f'CREATE TABLE "{name}" '
                    f'("ts" TEXT, {", ".join(chr(34) + c + chr(34) for c in cols)})')
        rows = out.execute(
            'SELECT ts, cols FROM dataset_rows WHERE dataset = ? ORDER BY ts',
            (key,)).fetchall()
        flat = []
        for ts, blob in rows:
            try:
                data = json.loads(blob) if isinstance(blob, str) else (blob or {})
            except (TypeError, ValueError):
                data = {}
            flat.append(tuple([ts] + [_sqlite_type(data.get(c)) for c in cols]))
        if flat:
            out.executemany(
                f'INSERT INTO "{name}" VALUES ({", ".join("?" * (len(cols) + 1))})',
                flat)
