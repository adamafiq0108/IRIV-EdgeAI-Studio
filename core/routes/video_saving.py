# -*- coding: utf-8 -*-
"""
video_saving.py -- GET/POST /api/detector/control/video: the Detection page's
"Video saving" switches ([video] on_demand, view_on_detect, view_hold_s),
and POST /api/detector/control/video/overlay: who draws the boxes
([video] overlay, "client" or "server").

Its own small route rather than a rider on an existing one: the endpoints
next door each own ONE table ([capture], [hud]) and refuse keys outside it,
which is the right rule to keep. Same shape, same config path
(config.local.toml through detector_control.edit_local), same grant as the
other live-picture settings on that page. The rules are in
core/services/video_saving.py.
"""

from fastapi import APIRouter, HTTPException, Request

from core.routes import perm
from core.routes.models import _system_context
from core.services import audit as audit_service
from core.services import video_saving

router = APIRouter()


@router.get("/api/detector/control/video", name="detector.get_video")
def detector_get_video(request: Request):
    """The switches, and who draws the overlay (view_on_detect needs the
    browser to)."""
    systems_dir, slug = _system_context(request)
    return {"ok": True, "video": video_saving.get(systems_dir, slug),
            "needs_client": video_saving.NEEDS_CLIENT}


@router.post("/api/detector/control/video", name="detector.set_video")
async def detector_set_video(request: Request):
    """Save any of on_demand, view_on_detect, view_hold_s. The app applies
    on_demand at its next config check; pages apply the rest on their next
    open of a live tile."""
    perm.require(request, "detection.hud")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        video = video_saving.save(systems_dir, slug, body or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "video": video}


@router.post("/api/detector/control/video/overlay", name="detector.set_overlay")
async def detector_set_overlay(request: Request):
    """Body {"mode": "client"|"server"}. Its own door, and audited, unlike
    the switches above: it changes what every viewer of the system sees,
    and whether a passthrough camera can go passthrough at all. Same grant
    as the rest of the panel. The app applies it at its next config check."""
    perm.require(request, "detection.hud")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    mode = (body or {}).get("mode") if isinstance(body, dict) else None
    try:
        video, before = video_saving.set_overlay(systems_dir, slug, mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    audit_service.record("video.overlay",
                         username=getattr(request.state, "user", None) or "",
                         detail=f"{slug}: boxes drawn {before} -> {video['overlay']}",
                         config_dir=request.app.state.config_dir)
    return {"ok": True, "video": video}
