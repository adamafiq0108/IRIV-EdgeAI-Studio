# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
perm.py -- the capability gate, in one place for every route file to use.

Each route module keeps its own `_require_admin` (they predate this and say
"master or developer", which several routes still mean). What must NOT be
copied per file is the capability check: it decides what a site admin may
change, and four private versions of it is four places for one of them to
drift into agreeing with a checkbox nobody enforces.

See core/services/caps.py for the vocabulary itself.
"""

import functools

from fastapi import HTTPException

from core.services import auth as auth_service
from core.services import caps as caps_service
from core.services import monitor_account
from core.services import pollshare


def slug_of(request):
    """The system this master serves, or "" on the hub."""
    system = getattr(request.app.state, "system", None)
    return getattr(system, "slug", "") or ""


def held(request):
    """Which capabilities the caller holds here, as a list in catalogue order.

    Sent on the status endpoints the pages read, so a control an account may
    not use is never drawn: a button that 403s is a support call, and the page
    already knows enough not to offer it. Master and developer hold the whole
    vocabulary -- they are never held by a grant somebody below them wrote.
    """
    granted = auth_service.cap_grants(getattr(request.state, "user", None),
                                      slug_of(request),
                                      request.app.state.config_dir)
    return list(caps_service.ALL_CAPS) if granted is None else \
        [c for c in caps_service.ALL_CAPS if c in granted]


def editable(request, prefix):
    """Whether the caller may change ANYTHING under one page's prefix -- what
    the pages have always called `editable`, answered from the grant rather
    than from the tier. Master reads True exactly as before, and an ordinary
    account reads False exactly as before: every route behind a capability was
    master-only until the grant existed."""
    return any(c.startswith(prefix) for c in held(request))


def require_any(request, caps):
    """The caller holds ANY of these capabilities (footage download is allowed
    by its own capability or by the wider export one)."""
    for cap in caps[:-1]:
        try:
            require(request, cap)
            return
        except HTTPException:
            continue
    require(request, caps[-1])


def require(request, cap):
    """One capability on this system: master and developer always, a site admin
    where master ticked it, nobody else -- and the Monitor account, for the
    two it is fixed to."""
    user = getattr(request.state, "user", None)
    cfg = request.app.state.config_dir
    if auth_service.is_admin(user, cfg):
        return
    if cap in caps_service.MONITOR_CAPS and monitor_account.is_monitor(user, cfg):
        return
    if not (auth_service.is_site_admin(user, cfg)
            and auth_service.may(user, slug_of(request), cap, cfg)):
        raise HTTPException(status_code=403, detail=caps_service.denial(cap))


def shared(endpoint, params=(), after=None):
    """Decorate a sync GET handler `def h(request)` so that, with
    [system] share_polls on, requests entitled to the identical answer share
    one computation of it (core/services/pollshare.py).

    Sits INSIDE the handler, so the session wall, CSRF and the role checks
    have all run before it; a request without an account (never one that
    passed the wall, but checked anyway) is never shared. The key is the
    endpoint, the whitelisted query `params` the handler reads -- nothing
    else from the request, no header, no cookie, no body -- and the
    caller's authorisation key. `after(request, value)` runs on EVERY
    request, shared or not, for what depends on the request itself (the
    hub's cards are rewritten onto the host the browser used); it must copy
    what it changes, because a shared value belongs to every caller.
    """
    allowed = frozenset(params)

    def wrap(handler):
        @functools.wraps(handler)
        def serve(request):
            st = request.app.state
            user = getattr(request.state, "user", None) or ""
            if not user:
                value = handler(request)
            else:
                value = pollshare.lookup(endpoint, lambda: _share_key(
                    request, user, allowed), lambda: handler(request),
                    st.config_dir)
            return after(request, value) if after else value
        return serve
    return wrap


def _share_key(request, user, allowed):
    """(query key, authorisation key) for perm.shared -- read only when the
    switch is on. The hub (no system) adds the systems the caller may see."""
    st, slug = request.app.state, slug_of(request)
    hub = not slug
    return (pollshare.query_key(request.query_params.multi_items(), allowed),
            pollshare.authz_key(user, slug, st.config_dir,
                                registry=st.registry if hub else None,
                                masters=st.masters if hub else None))
