# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
dbimport.py -- read back what dbexport.py wrote.

The export has existed for a while: one system carved out of the shared
Postgres database into a SQLite file that opens anywhere. There was no way
back in. So a box could hand you its data and no box could take it, which
makes an export a souvenir rather than a move -- and "we bought another box"
is exactly the case the system export was built for.

WHAT COMES ACROSS
    tables          the Data Table definitions, so a column's TYPE survives.
                    Merged, never dropped: a table this system already has
                    keeps its own shape (services/datasets.install_tables).
    events          every event the file carries
    dataset_rows    every Data Table row

WHAT DOES NOT: snapshots and clips, because the export never carried them.
An imported event keeps its `snapshot_id`, so it still names the picture it
refers to -- that picture simply is not on this box.

THE EVENT ID IS THE WHOLE PROBLEM, and it is worth being explicit about.
`events.event_id` and `dataset_rows.event_id` are PRIMARY KEYS, global across
the box rather than per system, because a device's event id is unique by
contract. Importing a system's data into a DIFFERENT system on the SAME box
therefore collides with every row of the original, and `ON CONFLICT DO
NOTHING` would silently import nothing at all -- the worst possible outcome,
since it looks like it worked.

So rows are RE-KEYED when the target slug differs from the file's: a uuid5 of
(target slug, original id). Deterministic, so importing the same file twice is
idempotent rather than duplicating; distinct per target, so the same file can
seed two systems. When the slugs MATCH -- restoring a system onto itself, or
onto its namesake on another box -- ids are left alone, because that is a
restore and a restore should be exactly what was taken.

Streamed in batches, like the export: a year of events is millions of rows.
"""

import json
import os
import sqlite3
import uuid

from core.db import DbUnavailable, connect

#: Rows per INSERT. Same reasoning as dbexport.BATCH -- big enough that the
#: round trip is not the cost, small enough that memory stays bounded.
BATCH = 1000

#: The namespace re-keyed ids are derived in. A fixed value, never
#: time-based: importing the same file twice must produce the same ids or
#: "idempotent" means nothing.
_NS = uuid.UUID("6f1d2f7e-4a52-5c9b-9d31-2a7c4e8b1f60")

#: The shared tables an export carries, and the column that identifies a row.
TABLES = ("events", "dataset_rows")

#: How many rows a single file may bring. A guard against a hand-made SQLite
#: file rather than against our own exports: this runs as the master, and an
#: unbounded insert loop is a way to fill the box's disk from a file upload.
MAX_ROWS = 5_000_000


class NotAnExport(ValueError):
    """The file opened, and it is not one of ours."""


def _connect_file(path):
    """Open the uploaded file read-only, and refuse anything that is not an
    export of ours.

    `immutable=1` rather than a plain open: the file came from outside, and a
    SQLite journal replay on someone else's database is a write we have no
    reason to perform.
    """
    if not os.path.exists(path):
        raise NotAnExport("that file is not there")
    try:
        db = sqlite3.connect(f"file:{path}?immutable=1", uri=True)
    except sqlite3.Error as exc:
        raise NotAnExport(f"that file is not a database: {exc}") from exc
    try:
        have = {r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.DatabaseError as exc:
        db.close()
        raise NotAnExport("that file is not a SQLite database") from exc
    if "export_meta" not in have:
        db.close()
        raise NotAnExport("that file is not an IRIV database export - it has "
                          "no export_meta table. Export one from a Database "
                          "page to see the shape.")
    return db, have


def inspect(path):
    """What is in the file, without changing anything.

    Read BEFORE importing, and shown to the operator, because "import this
    database" is a question they can only answer if they know whose data it is
    and how much of it there is. A file from another system entirely is a
    perfectly valid thing to import and a terrible thing to import by
    accident.
    """
    db, have = _connect_file(path)
    try:
        meta = db.execute(
            "SELECT system_slug, exported_at, source, note FROM export_meta"
        ).fetchone() or ("", "", "", "")
        counts = {}
        for table in TABLES:
            if table in have:
                counts[table] = db.execute(
                    f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            else:
                counts[table] = 0
        return {"ok": True,
                "system_slug": str(meta[0] or ""),
                "exported_at": str(meta[1] or ""),
                "source": str(meta[2] or ""),
                "note": str(meta[3] or ""),
                "counts": counts,
                "tables": _definitions(db, have)}
    finally:
        db.close()


def _definitions(db, have):
    """The Data Table definitions the file carries.

    Two shapes, because two generations of export exist. A file written since
    the export learned to carry `tables` has them with their TYPES. An older
    one has only the flat `table_<key>` views, whose column names survive but
    whose types do not -- so those come back as text, which is the type that
    loses nothing: a number stored as text still reads as a number, where a
    number forced into the wrong type does not come back at all.
    """
    if "tables" in have:
        out = []
        for key, name, cols in db.execute(
                "SELECT key, name, columns FROM tables"):
            try:
                columns = json.loads(cols) if isinstance(cols, str) else []
            except (TypeError, ValueError):
                columns = []
            if key and isinstance(columns, list):
                out.append({"key": str(key), "name": str(name or key),
                            "columns": columns})
        if out:
            return out
    out = []
    for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'table\\_%' ESCAPE '\\'"):
        key = name[len("table_"):]
        cols = [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')
                if r[1] != "ts"]
        if key and cols:
            out.append({"key": key, "name": key,
                        "columns": [{"name": c, "type": "text"} for c in cols]})
    return out


def _pg_columns(cur, table):
    """(names, jsonb names) for a live table.

    Read from the catalogue rather than named here, for the same reason the
    export reads it: the schema has grown columns more than once, and a list
    written by hand stops carrying whatever was added last. The jsonb set
    matters because SQLite carried those columns as TEXT -- handing text to a
    jsonb column is an error, not a cast.
    """
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s AND table_schema = current_schema()",
        (table,))
    rows = cur.fetchall()
    names = [r["column_name"] for r in rows]
    js = {r["column_name"] for r in rows if r["data_type"] == "jsonb"}
    return names, js


def _rekey(target_slug, source_slug, event_id):
    """The id a row lands under. See the module note: same slug, same id."""
    if not event_id:
        return None
    if source_slug and source_slug == target_slug:
        return event_id
    return str(uuid.uuid5(_NS, f"{target_slug}|{event_id}"))


def import_file(path, target_slug, config_dir=None, systems_dir=None,
                mode="append", with_tables=True):
    """Load an export into `target_slug`. Returns a summary dict.

    `mode` is "append" or "replace". Append is the default and the safe one:
    rows already present stay, and re-importing the same file changes nothing.
    REPLACE DELETES this system's existing events and Data Table rows first --
    it is the restore-a-backup case, and it is destructive, so it is never
    inferred from the file and never the default.
    """
    slug = str(target_slug or "").strip()
    if not slug:
        raise ValueError("which system?")
    if mode not in ("append", "replace"):
        raise ValueError("mode is append or replace")

    db, have = _connect_file(path)
    summary = {"ok": True, "mode": mode, "system_slug": slug,
               "source_slug": "", "tables": 0, "written": {}, "warnings": []}
    try:
        meta = db.execute("SELECT system_slug FROM export_meta").fetchone()
        source_slug = str((meta or [""])[0] or "")
        summary["source_slug"] = source_slug
        summary["rekeyed"] = bool(source_slug and source_slug != slug)

        # The DEFINITIONS first: a row belongs to a table, and a table this
        # system does not know about is a row nothing can draw.
        if with_tables and systems_dir:
            from core.services import datasets as datasets_service
            defs = _definitions(db, have)
            if defs:
                try:
                    merged = datasets_service.install_tables(systems_dir, slug, defs)
                    summary["tables"] = len(defs)
                    summary["table_keys"] = sorted(merged)
                except ValueError as exc:
                    # The table cap, usually. The ROWS are still worth having
                    # for the tables that do exist.
                    summary["warnings"].append(
                        f"the table definitions could not all be added: {exc}")

        try:
            conn = connect(config_dir)
        except DbUnavailable:
            summary["ok"] = False
            summary["error"] = "the events database is unreachable"
            return summary

        try:
            with conn, conn.cursor() as cur:
                if mode == "replace":
                    for table in TABLES:
                        cur.execute(
                            f'DELETE FROM "{table}" WHERE system_slug = %s',
                            (slug,))
                for table in TABLES:
                    if table not in have:
                        summary["written"][table] = 0
                        continue
                    try:
                        summary["written"][table] = _load_table(
                            db, cur, table, slug, source_slug)
                    except Exception as exc:                # noqa: BLE001
                        # One table failing must not cost the other: events
                        # without their Data Table rows is a partial import,
                        # and a partial import is worth more than none.
                        summary["written"][table] = 0
                        summary["warnings"].append(f"{table}: {exc}")
                        summary["ok"] = False
        finally:
            try:
                conn.close()
            except Exception:                               # noqa: BLE001
                pass
        return summary
    finally:
        db.close()


def _load_table(db, cur, table, slug, source_slug):
    """Stream one table out of the file and into Postgres. Returns rows written.

    Only the columns BOTH sides have. A file written by an older build is
    missing whatever the schema grew since, and a file from a newer one may
    carry a column this box does not have yet -- neither is a reason to refuse
    the rows that do line up.
    """
    live, jsonb = _pg_columns(cur, table)
    if not live:
        raise ValueError("this box has no such table")
    src = [r[1] for r in db.execute(f'PRAGMA table_info("{table}")')]
    cols = [c for c in src if c in live]
    if "event_id" not in cols or "ts" not in cols:
        raise ValueError("the file's rows carry no id or no timestamp")
    # system_slug is not COPIED, it is SET: that is what importing into this
    # system means, and a file that carried another slug through would put
    # rows in the database that no page on this box can see.
    if "system_slug" not in cols:
        cols.append("system_slug")

    import psycopg2.extras                                    # noqa: PLC0415
    placeholders = "(" + ", ".join(
        f"%s::jsonb" if c in jsonb else "%s" for c in cols) + ")"
    sql = (f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
           f"VALUES %s ON CONFLICT (event_id) DO NOTHING")

    read = [c for c in cols if c != "system_slug"]
    quoted = ", ".join(f'"{c}"' for c in read)
    cursor = db.execute(f'SELECT {quoted} FROM "{table}"')
    written, seen = 0, 0
    while True:
        batch = cursor.fetchmany(BATCH)
        if not batch:
            break
        values = []
        for row in batch:
            seen += 1
            if seen > MAX_ROWS:
                break
            record = dict(zip(read, row))
            event_id = _rekey(slug, source_slug, record.get("event_id"))
            if not event_id or not record.get("ts"):
                continue
            record["event_id"] = event_id
            record["system_slug"] = slug
            values.append(tuple(record.get(c) for c in cols))
        if values:
            psycopg2.extras.execute_values(cur, sql, values,
                                           template=placeholders)
            written += cur.rowcount
        if seen > MAX_ROWS:
            break
    return written
