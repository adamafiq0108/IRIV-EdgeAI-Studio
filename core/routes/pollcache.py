# -*- coding: utf-8 -*-
"""
pollcache.py -- the shared poll cache's two framework edges: the read-only
counters route behind the "Shared poll cache (testing)" card, and the hook
that empties the cache after every authenticated write.

The switch itself is saved through POST /api/system/settings like every
other box switch (core/routes/auth.py, core/services/sysconfig.py); the
policy lives in core/services/pollshare.py and the reads opt in with
core/routes/perm.shared.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.routes import perm
from core.services import auth as auth_service
from core.services import pollshare

router = APIRouter()

#: Methods that may change what a shared read returns (auth.UNSAFE_METHODS).
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


@router.get("/api/system/poll-cache", name="system.poll_cache")
def system_poll_cache(request: Request):
    """The switch, and hit / miss / coalesced counters and the entry count.
    Master or developer, like the rest of the System tab. Counts only --
    never a key or a value. On the hub the masters' own counters are added
    (each asked as the caller, so each applies this same check)."""
    st = request.app.state
    me = getattr(request.state, "user", None) or ""
    if not auth_service.is_admin(me, st.config_dir):
        raise HTTPException(status_code=403, detail="master or developer only")
    counts = (pollshare.counters() if perm.slug_of(request) else
              pollshare.box_counters(getattr(st, "masters", None),
                                     request.headers.get("cookie")))
    return {"ok": True, "on": pollshare.enabled(st.config_dir), **counts}


def install(app):
    """Empty the cache after every authenticated write. Call BEFORE the
    session wall is added: Starlette makes the last-added middleware the
    outermost, so this one runs inside the wall and sees request.state.user
    only for a request that passed it (CSRF included). A refused or
    anonymous write never touches the cache."""

    @app.middleware("http")
    async def _drop_shared_on_write(request, call_next):
        try:
            return await call_next(request)
        finally:
            # Before the response leaves (and after a write that failed
            # half-way, too): the writer's next poll must not be handed
            # anything computed before its own write.
            if (request.method in WRITE_METHODS
                    and getattr(request.state, "user", None)):
                await asyncio.to_thread(pollshare.note_write, request.url.path,
                                        request.app.state.config_dir)
