# -*- coding: utf-8 -*-
"""
repos/audit.py -- SQL for the centralized audit log. SQL only. Writes are
best-effort (a Postgres outage must never block the action being audited),
so record() swallows DbUnavailable and returns False.
"""

import json

from core.db import DbUnavailable, connect


def record(action, username=None, host_serial=None, result="ok",
           source_ip=None, detail=None, system=None, config_dir=None):
    """Append one audit row. Returns True if written, False if PG-down."""
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log "
                "(action, username, host_serial, result, source_ip, detail, system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (action, username, host_serial, result, source_ip,
                 json.dumps(detail) if detail is not None else None, system))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def query(limit=200, action=None, username=None, system=None, config_dir=None,
          offset=0, exclude_usernames=None):
    """Recent audit rows, newest first, optionally filtered. When `system` is
    given, only that system's rows (each master scopes to its own). [] on PG-down.

    `offset` pages BACKWARDS through the same ordering: the log is append-only
    and read newest-first, so a page boundary stays where it was unless
    something new was written at the head -- and one row appearing while a
    reader is on page three shifts that page by one row, not by a page."""
    try:
        conn = connect(config_dir)
    except DbUnavailable:
        return []
    try:
        clauses, params = [], []
        if system:
            clauses.append("system = %s")
            params.append(system)
        if action:
            clauses.append("action = %s")
            params.append(action)
        if username:
            clauses.append("username = %s")
            params.append(username)
        # Names the caller may not read. Bound as parameters, never
        # interpolated: these are account names, which are operator-supplied.
        hidden = [str(u) for u in (exclude_usernames or []) if str(u)]
        if hidden:
            clauses.append("(username IS NULL OR username <> ALL(%s))")
            params.append(hidden)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # One extra row, never returned: it is how the caller learns there IS
        # a next page without a second COUNT(*) over the whole log.
        params.append(int(limit) + 1)
        params.append(max(0, int(offset)))
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, ts, username, host_serial, action, result, "
                "source_ip, detail, system FROM audit_log" + where +
                " ORDER BY ts DESC LIMIT %s OFFSET %s", params)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []
    finally:
        conn.close()
