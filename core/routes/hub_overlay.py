# -*- coding: utf-8 -*-
"""
hub_overlay.py -- GET /api/hub/overlay: the Live Wall's overlay events.

The wall's tiles pull video straight from each system's master; for a system
in [video] overlay = "client" the boxes come as events from that master,
which a hub page cannot reach itself (mixed content on HTTPS, CORS, the
per-origin connection cap). So the hub relays the EVENTS -- never the video
-- as ONE stream per page for every system on the wall
(core/services/hub_overlay.py has the why and the wire format).

    GET /api/hub/overlay?slugs=a,b&camera=a:cam1&camera=b:

Guarded as tightly as the master's own /api/detector/overlay: the session
wall (hub middleware), a cross-site or downgraded-scheme read refused, and
the Live Wall's own scoping rule (hub_service.visible_to) per system. An
unknown or hidden system is a 404 for the whole request, so the answer does
not say which systems exist. 404 too when none of them draws in the browser:
a server-mode wall never holds one of these open.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.services import hub as hub_service
from core.services import hub_overlay
from core.services import overlay_view
from core.services import provision as provision_service

router = APIRouter()


@router.get("/api/hub/overlay", name="hub.overlay")
def hub_overlay_events(request: Request, slugs: str = "",
                       camera: list[str] = Query([])):
    """Relay the overlay events of the named systems, each tagged with its
    slug. `camera` repeats as <slug>:<camera id> ("<slug>:" = its first)."""
    if overlay_view.cross_site(request.headers.get("Origin"),
                               request.headers.get("Sec-Fetch-Site"),
                               request.url.hostname,
                               overlay_view.on_tls(
                                   request.url.scheme,
                                   request.headers.get("X-Forwarded-Proto"))):
        raise HTTPException(status_code=403, detail="cross-site request refused")
    username = getattr(request.state, "user", None) or ""
    if not username:
        # visible_to() shows everything to "no user"; this route never may.
        raise HTTPException(status_code=401, detail="login required")
    try:
        wants = hub_overlay.parse_wants(slugs, camera)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    st = request.app.state
    known = {s.slug for s in st.registry}
    feeds = {}
    for slug, cams in wants.items():
        if slug not in known or not hub_service.visible_to(
                username, slug, st.masters, st.config_dir):
            raise HTTPException(status_code=404, detail="no such system")
        # The master's URL from the hub's OWN registry -- never the browser's.
        # Only this checked slug and URL-quoted camera ids reach a master.
        url = (st.masters.get(slug) or {}).get("url")
        pkg = provision_service._pkg_root(st.systems_dir, slug)
        # The `overlay` member, not the answer's truthiness: a server-mode
        # system with a video saving switch on answers non-empty too.
        if url and overlay_view.for_system(pkg, slug).get("overlay"):
            feeds[slug] = (url.rstrip("/"), cams)
    if not feeds:
        raise HTTPException(status_code=404,
                            detail="these systems draw their overlay on the server")
    mux = hub_overlay.Mux(feeds, cookie=request.headers.get("cookie"),
                          token=request.headers.get("X-Session"))
    first = mux.start()
    if 200 not in first.values():
        mux.close()
        all_gone = all(s == 404 for s in first.values())
        raise HTTPException(status_code=404 if all_gone else 502,
                            detail="no overlay from these systems")
    from starlette.background import BackgroundTask          # noqa: PLC0415
    return StreamingResponse(mux.events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"},
                             background=BackgroundTask(mux.close))
