# -*- coding: utf-8 -*-
"""
audit.py -- the Audit Log viewer page + its JSON API. Admin-only.

Thin (conventions sec 2): parse query -> audit service -> render/return.
The page and /api/audit are gated to the admin account; the middleware
already requires a session, this adds the admin check on top.
"""

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.services import audit as audit_service
from core.services import nav as nav_service

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


# The Audit Log page is a React route now (frontend/src/pages/Audit.jsx); it reads
# /api/audit below.

@router.get("/api/audit", name="audit.query")
def audit_query(request: Request, action: str = "", username: str = "",
                limit: int = 200, offset: int = 0):
    """One page of the log. `has_more` rather than a total: the table is
    append-only and can be long, and counting all of it on every page turn
    buys a number nobody acts on."""
    _require_admin(request)
    cfg = request.app.state.config_dir
    limit = max(1, min(1000, int(limit)))
    offset = max(0, int(offset))
    # A developer account is invisible to a deployment's admin everywhere else
    # (it is filtered out of /api/users), and its log entries are the same
    # account by another name -- an unfiltered log would list the developer
    # signing in, under a heading claiming to be this deployment's accounts.
    # Filtered in the QUERY, not in the page: hiding rows in the browser hides
    # the rows, not the payload they arrived in, and it would also make the
    # page counts lie.
    hidden = []
    me = getattr(request.state, "user", None) or ""
    if not auth_service.is_developer(me, cfg):
        hidden = [name for name in auth_service.users(cfg)
                  if auth_service.is_developer(name, cfg)]
    entries, has_more = audit_service.page(limit=limit, action=action,
                                           username=username, offset=offset,
                                           exclude_usernames=hidden)
    return {"ok": True,
            "actions": list(audit_service.ACTIONS),
            "entries": entries,
            "limit": limit, "offset": offset, "has_more": has_more}
