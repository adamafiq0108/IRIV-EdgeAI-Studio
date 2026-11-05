# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
events.py -- the generic event query API the analytics platform is built on.

Three read endpoints and two writes, all against the queryable event store
(core/repos/events.py). They are GENERIC: no system-specific logic, no per-
template branch. A widget, an event table, or a future AI model all query the
same shape -- filter + time window + (for charts) an aggregation. This is what
lets new AI templates work with no dashboard code change.

Thin by contract (docs/conventions.md sec 2): parse the request, hand the
whitelisted parameters to the repo, return JSON. The repo owns the SQL and the
column whitelist; a Postgres outage degrades to empty results, never a 500.

Scoped to the master's own system: `system_slug` is taken from app.state, never
from the client, so one system can never read another's events.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.repos import events as events_repo
from core.routes import perm
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import events as events_service
from core.services import log_catalog as catalog

router = APIRouter()


def _slug(request):
    system = getattr(request.app.state, "system", None)
    if not system:
        raise HTTPException(status_code=400, detail="no system on this server")
    return system.slug


def _admin(request):
    """Master or developer: who may edit an event or clear the log."""
    return auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir)


def _filters(params):
    """Turn query params into a {column: value|[values]} filter dict, keeping
    only whitelisted columns. Repeated params (?type=a&type=b) become a list."""
    out = {}
    for col in events_repo.FILTERABLE:
        vals = params.getlist(col)
        if not vals:
            continue
        out[col] = vals if len(vals) > 1 else vals[0]
    return out


#: Every query parameter query_events reads -- and so the only ones that may
#: tell two shared answers apart (perm.shared ignores the rest, as it does).
QUERY_PARAMS = (*events_repo.FILTERABLE, "from", "to", "order", "dir",
                "limit", "offset")


@router.get("/api/events", name="events.query")
@perm.shared("events", params=QUERY_PARAMS)
def query_events(request: Request):
    """Rows for the event table / a table widget. Query params:
    any filterable column (repeatable), from, to (ISO), order, dir(asc|desc),
    limit, offset."""
    p = request.query_params
    result = events_repo.query(
        _slug(request),
        filters=_filters(p),
        since=p.get("from"), until=p.get("to"),
        order=p.get("order", "ts"),
        descending=p.get("dir", "desc") != "asc",
        limit=p.get("limit", 200), offset=p.get("offset", 0))
    # Whether Clear events is offered: clear_events' own rule, sent rather
    # than guessed so the button and the route cannot disagree.
    return {"ok": True, **result, "can_clear": _admin(request)}


@router.get("/api/events/aggregate", name="events.aggregate")
def aggregate_events(request: Request):
    """Grouped aggregation for a chart widget. Query params: agg (count|sum|avg|
    min|max|unique), field (column the agg applies to), group_by (a column) OR
    bucket (hour|day|week|month), plus filters + from/to."""
    p = request.query_params
    result = events_repo.aggregate(
        _slug(request),
        agg=p.get("agg", "count"),
        field=p.get("field"),
        group_by=p.get("group_by"),
        bucket=p.get("bucket"),
        filters=_filters(p),
        since=p.get("from"), until=p.get("to"),
        limit=p.get("limit", 500))
    return {"ok": True, **result}


@router.get("/api/events/attributes", name="events.attributes")
def event_attributes(request: Request):
    """Distinct filter values (cameras, zones, types, …) for building the event
    table's filter controls and a widget's data-source picker."""
    return {"ok": True, "attributes": events_repo.attributes(_slug(request)),
            "columns": list(events_repo.FILTERABLE),
            "aggregations": ["count", "sum", "avg", "min", "max", "unique"],
            "buckets": ["hour", "day", "week", "month"]}


@router.patch("/api/events/{event_id}", name="events.update")
async def update_event(event_id: str, request: Request):
    """Operator workflow on an event: set status (open/ack/closed) or a note.
    Admin only; writes surface a Postgres outage as 503."""
    if not _admin(request):
        raise HTTPException(status_code=403, detail="editing an event is an admin action")
    body = await request.json()
    from core.db import DbUnavailable
    try:
        ok = events_repo.set_status(_slug(request), event_id,
                                    status=body.get("status"), notes=body.get("notes"))
    except DbUnavailable as e:
        # Was its own wording ("event store unavailable") for the same outage
        # devices.py and folders.py each described differently again.
        raise catalog.http_error("DB_UNAVAILABLE", cause=e,
                                 event_id=event_id) from e
    if not ok:
        raise HTTPException(status_code=404, detail="no such event")
    return {"ok": True}


@router.post("/api/events/clear", name="events.clear")
async def clear_events(request: Request):
    """EVERY stored event of this system, not the filtered rows (what goes:
    core/services/events.py). Admin only, like Clear Log; session and CSRF
    are the auth middleware's. Postgres down is a 503, never "0 cleared"."""
    if not _admin(request):
        raise catalog.http_error("AUTH_FORBIDDEN", cause="admin only",
                                 path=str(request.url.path))
    st, slug = request.app.state, _slug(request)
    from core.db import DbUnavailable
    try:
        done = await asyncio.to_thread(
            events_service.clear, slug, st.config_dir,
            getattr(st, "systems_dir", None), getattr(st, "events", None))
    except DbUnavailable as e:
        raise catalog.http_error("DB_UNAVAILABLE", cause=e, system=slug) from e
    except Exception as e:                                  # noqa: BLE001
        raise catalog.http_error("DB_QUERY_FAILED", cause=e, system=slug) from e
    user = getattr(request.state, "user", None) or "?"
    # Best effort, like every audit write: the clear has already happened.
    await asyncio.to_thread(
        audit_service.record, "events.clear", username=user,
        source_ip=net.client_ip(request),
        detail={"system": slug, "removed": done["removed"]},
        config_dir=st.config_dir)
    return {"ok": True, "removed": done["removed"]}
