# -*- coding: utf-8 -*-
"""
Master schema migration runner.

Forward-only *.sql in this directory, applied in sorted filename order, one
transaction per file, recorded in schema_migrations so re-runs skip. Files
must be idempotent-safe (IF NOT EXISTS). A failure rolls back that file and
aborts.

CLI:
    python -m core.db.migrations            # apply pending
    python -m core.db.migrations --status   # list applied/pending

run_migrations() is best-effort friendly: callers at boot wrap it so a
Postgres outage logs and continues rather than aborting the master.
"""

import pathlib
import sys

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent

_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _files():
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def _applied(conn):
    with conn.cursor() as cur:
        cur.execute(_ENSURE_TABLE)
        conn.commit()
        cur.execute("SELECT filename FROM schema_migrations")
        return {row["filename"] for row in cur.fetchall()}


def run_migrations(conn=None, log=print):
    """Apply pending migrations. Returns the list applied. Opens its own
    connection if none is given."""
    from core.db import connect

    own = conn is None
    if own:
        conn = connect()
    applied_now = []
    try:
        done = _applied(conn)
        for path in _files():
            if path.name in done:
                continue
            log(f"[MIGRATE] applying {path.name}")
            try:
                with conn.cursor() as cur:
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO schema_migrations (filename) VALUES (%s)",
                        (path.name,))
                conn.commit()
            except Exception:
                conn.rollback()
                log(f"[MIGRATE] FAILED {path.name} -- rolled back, aborting")
                raise
            applied_now.append(path.name)
        if not applied_now:
            log("[MIGRATE] up to date")
    finally:
        if own:
            conn.close()
    return applied_now


def status(conn=None, log=print):
    from core.db import connect

    own = conn is None
    if own:
        conn = connect()
    try:
        done = _applied(conn)
        for path in _files():
            log(f"  {path.name:<40} {'applied' if path.name in done else 'PENDING'}")
    finally:
        if own:
            conn.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--status" in argv:
        status()
    else:
        run_migrations()
    return 0
