# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
logs.py -- the Logs page API, and the error handlers behind it.

Admin-only: log lines carry paths, hostnames and stack traces, which is more
than an operator account should see.

`install_error_handlers` lives in core/routes/log_errors.py and is re-exported
here, under the name the servers import.
"""

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import log_catalog as catalog
from core.services import log_collect
from core.services import logs as logs_service
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.routes.log_errors import install_error_handlers  # noqa: F401

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise catalog.http_error("AUTH_FORBIDDEN",
                                 cause="admin only",
                                 path=str(request.url.path))


@router.get("/api/logs", name="logs.list")
def list_logs(request: Request, level: str = "", logger: str = "",
              contains: str = "", since: float = 0, limit: int = 200,
              category: str = "", event_id: str = "", status: str = ""):
    """This process's own records: on a system, its master and its app; on
    the hub, the hub.

    On a real board the cameras, the accelerator and the detection all live
    in the app process, so a master's page adds its app (`merge`; see
    core/services/log_collect.py for how those records travel). The hub no
    longer gathers the systems' records (Tok Aba, 2026-10-14: each log shows
    its own process), and the rule is enforced HERE as well, on whatever
    reached this point: a row whose source is not this process's own is
    dropped, not just hidden by the page.
    """
    _require_admin(request)
    capped = min(1000, max(1, limit))
    entries = logs_service.query(level or None, logger or None,
                                 contains or None, since or None, capped,
                                 category=category or None,
                                 event_id=event_id or None,
                                 status=status or None)
    entries = log_collect.merge(
        entries, request.app.state, level=level or None, contains=contains or None, since=since or None,
        limit=capped, category=category or None, event_id=event_id or None,
        status=status or None)
    # Rows from another process are built against a format this module does
    # not own; normalising here means one definition of a record reaches the
    # page rather than two that nearly agree.
    entries = logs_service.as_records(entries)
    own = log_collect.own_sources(request.app.state)
    entries = [e for e in entries if e.get("source") in own]
    # The badge strip counts the MERGED set, not this process's ring. The
    # ring's own tally is cumulative since the process started and is never
    # decremented on eviction, so it describes neither the page nor the ring
    # -- on the hub it read "0 CRITICAL" beside a table of critical app rows.
    # Overridden here rather than in logs_service because only this route
    # knows what actually reached the page. The page sends no filters (it
    # filters client-side), so these counts are of everything it holds.
    summary = dict(logs_service.summary())
    summary["counts"] = log_collect.level_counts(entries)
    return {"ok": True,
            "entries": entries,
            "summary": summary,
            "levels": logs_service.capture_levels(),
            "categories": list(catalog.CATEGORIES),
            "statuses": list(logs_service.STATUSES),
            # Which processes actually appear in THIS page of rows, rather
            # than every process that could exist: a filter offering a master
            # with nothing on the page is the empty-dropdown bug again.
            "sources": log_collect.sources(entries),
            "capture": logs_service.capture_level(),
            # Whether Clear Log is offered. The same rule purge_logs enforces,
            # sent rather than guessed so the button and the route cannot
            # disagree if this page ever opens below admin.
            "can_clear": auth_service.is_admin(
                getattr(request.state, "user", None),
                request.app.state.config_dir)}


@router.get("/api/logs/catalog", name="logs.catalog")
def list_catalog(request: Request):
    """The whole event catalog, for the page's reference table and filters.

    Served rather than duplicated in the frontend: a second copy of the
    wording in JavaScript is a second copy to forget to update, and then the
    same fault reads one way in a toast and another on this page.
    """
    _require_admin(request)
    return {"ok": True, "events": catalog.as_dicts(),
            "categories": list(catalog.CATEGORIES),
            "severities": list(catalog.SEVERITIES),
            "statuses": list(logs_service.STATUSES),
            # The record's own key list, served rather than written down
            # twice. A column the page expects and the record stopped
            # sending is otherwise a blank cell nobody notices.
            "record_fields": list(logs_service.RECORD_FIELDS)}


@router.post("/api/logs/level", name="logs.level")
async def set_level(request: Request):
    """Set the in-memory log capture level for this process."""
    _require_admin(request)
    try:
        body = await request.json() or {}
    except Exception:                                  # noqa: BLE001
        body = {}
    level = str(body.get("level") or "").upper()
    if level not in logs_service.LEVELS:
        raise HTTPException(status_code=400,
                            detail=f"Choose one of: {', '.join(logs_service.LEVELS)}.")
    logging.getLogger("cytron.logs").info("log capture set to %s by %s",
                                          level, getattr(request.state, "user", "?"))
    return {"ok": True, "capture": logs_service.set_capture(level),
            "capacity": logs_service.capacity(),
            "levels": logs_service.capture_levels()}


@router.post("/api/logs/clear", name="logs.clear")
def clear_logs(request: Request):
    _require_admin(request)
    logs_service.clear()
    logging.getLogger("cytron.logs").info("log ring cleared by %s",
                                          getattr(request.state, "user", "?"))
    return {"ok": True}


@router.post("/api/logs/purge", name="logs.purge")
async def purge_logs(request: Request):
    """Clear Log: every stored record the Logs page reads, then a record
    saying who did it and how many went.

    NOT Clear ring. That one empties only the ring of the process answering
    and leaves everything else the page merges in; this one is the whole
    page's store, wherever it is held:

      * on the HUB: its own ring, and nothing else -- the systems' logs are
        theirs to clear, each from its own page (Tok Aba, 2026-10-14);
      * on a MASTER: its own ring and its app's spool, and app.log is hidden
        above a mark rather than cut (log_collect.purge_app).

    Admin-only, like every route here. The audit trail (core/services/
    audit.py, the Audit page) is a separate store in the database and is
    NOT cleared: logins and admin actions stay accountable after a log is
    cleared, which is the point of keeping the two apart. Clear Log writes
    to it too, as `logs.clear`.

    THE TRACE IS ALWAYS WRITTEN, with no switch to skip it: a skip switch
    is a way to clear a log without a trace. The request's own access line
    is suppressed, so the trace is all that is left.
    """
    _require_admin(request)
    request.state.quiet_access = True
    st = request.app.state
    user = getattr(request.state, "user", None) or "?"
    removed = logs_service.clear()
    unreached = []          # kept in the answer; the hub no longer asks anyone
    cleared = []
    system = getattr(st, "system", None)
    slug = getattr(system, "slug", None)
    if slug and getattr(st, "systems_dir", None):
        removed += await asyncio.to_thread(log_collect.purge_app,
                                           st.systems_dir, slug)
        cleared = [slug]
    logs_service.log_event(
        "SYS_LOG_CLEARED", logger="cytron.logs", user=user,
        session=getattr(request.state, "session_sid", None),
        request_id=getattr(request.state, "request_id", None),
        message=f"Log cleared by {user}, {removed} records removed",
        removed=removed, systems=", ".join(cleared),
        unreached=", ".join(unreached))
    # Best effort, like every audit write: a database that is down must not
    # turn a clear that already happened into an error on the page.
    await asyncio.to_thread(
        audit_service.record, "logs.clear", username=user,
        source_ip=net.client_ip(request),
        detail={"systems": cleared, "removed": removed,
                "unreached": unreached},
        config_dir=st.config_dir)
    return {"ok": True, "removed": removed, "systems": cleared,
            "unreached": unreached}
