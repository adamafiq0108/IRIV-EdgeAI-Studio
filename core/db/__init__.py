# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
core/db -- master PostgreSQL access (the `cytron_dashboard` database).

The master was in-memory only until v2.0; this activates the Postgres block
declared in config/storage.toml for the things that must persist across
restarts: device folders, device metadata, and the audit log.

Layering (docs/conventions.md sec 2): this is an INFRASTRUCTURE tier that
core/repos/*.py import for a connection. Repos hold the SQL; services and
routes never import this. psycopg2 is imported lazily so `import core.*`
never fails on a host without it, and every repo read tolerates
DbUnavailable so a Postgres outage degrades (device metadata falls back to
"Unassigned", audit writes skip) rather than bricking the master -- polling,
telemetry, the control channel and auth are all DB-independent.
"""

from core import config


class DbUnavailable(RuntimeError):
    """Raised when cytron_dashboard cannot be reached. Repos catch this and
    return empty results; write paths surface it as a 503."""


def pg_settings(config_dir=None):
    defaults = {"host": "localhost", "port": 5432, "dbname": "cytron_dashboard",
                "user": "cytron", "password": ""}
    block = (config.storage(config_dir) or {}).get("postgres") or {}
    merged = {**defaults, **block}
    merged["port"] = int(merged["port"])
    return {k: merged[k] for k in defaults}


#: Server-side ceiling on ONE statement, in milliseconds.
#:
#: connect_timeout only bounds the handshake. Without this, a query over a
#: table that has grown for a year -- an unbounded count(*), a GROUP BY with no
#: time window -- blocks its request thread for as long as Postgres is willing
#: to keep working, and an analytics page that refreshes every four seconds
#: stacks those threads until the box stops answering. A dashboard query that
#: takes ten seconds has already failed; failing it here turns a hang into an
#: empty widget, which every repo read already knows how to degrade to.
STATEMENT_TIMEOUT_MS = 10000

#: Writes get longer: a retention prune deletes a year of rows, and being cut
#: off mid-DELETE means the same work again next sweep.
WRITE_TIMEOUT_MS = 120000


def connect(config_dir=None, connect_timeout=3, timeout_ms=None):
    """Open a psycopg2 connection to cytron_dashboard (RealDictCursor).

    Raises DbUnavailable on any connection failure -- callers in core/repos
    translate that to empty reads / 503 writes.

    `timeout_ms` overrides the per-statement ceiling; pass WRITE_TIMEOUT_MS
    from a bulk delete, which legitimately takes longer than any read should.
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as e:  # pragma: no cover
        raise DbUnavailable(f"psycopg2 not installed: {e}") from e

    pg = pg_settings(config_dir)
    ms = int(STATEMENT_TIMEOUT_MS if timeout_ms is None else timeout_ms)
    try:
        return psycopg2.connect(
            host=pg["host"], port=pg["port"], dbname=pg["dbname"],
            user=pg["user"], password=pg["password"],
            cursor_factory=psycopg2.extras.RealDictCursor,
            connect_timeout=connect_timeout,
            # Applied by the server to every statement on this connection.
            options=f"-c statement_timeout={ms}",
        )
    except Exception as e:  # psycopg2.OperationalError and friends
        raise DbUnavailable(str(e)) from e
