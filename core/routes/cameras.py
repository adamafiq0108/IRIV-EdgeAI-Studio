# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
cameras.py -- fleet camera-usage endpoint (v2.0), and the per-system
"is the camera there" look.

Thin: read app.state -> cameras service -> JSON. The Manage page uses this to
show which camera IPs are already assigned to another slave (so the operator
can tell shared cameras apart at a glance). The conflict CHECK on assignment
lives in core/routes/control.py's camera apply; this endpoint is the read side.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.services import cameras as cameras_service

router = APIRouter()


def _system_context(request):
    """(systems_dir, slug) for the master serving this system, or a 400."""
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        raise HTTPException(status_code=400,
                            detail="cameras belong to a system; open a system's dashboard")
    return systems_dir, system.slug


@router.get("/api/detector/control/cameras/presence", name="detector.cameras_presence")
def detector_cameras_presence(request: Request):
    """Is each of this system's cameras on the box RIGHT NOW: {id: true /
    false / null (cannot be told)}. The Camera Setup page asks while
    detection is stopped, so the rail says Live / Not found / Unreachable
    from a real look rather than from the last run's memory. Sync def: the
    probes run side by side in the threadpool, about a second at most,
    and the page draws without waiting for them."""
    from core.services import camera_notice                    # noqa: PLC0415
    from core.services import detector_control as dc           # noqa: PLC0415
    systems_dir, slug = _system_context(request)
    cams = dc.live_cameras(systems_dir, slug)
    return {"ok": True,
            "cameras": camera_notice.presence(dc._pkg_dir(systems_dir, slug), cams)}


@router.get("/api/cameras/usage", name="cameras.usage")
async def cameras_usage(request: Request):
    # Probing every online slave's control channel is blocking HTTP -> threadpool.
    usage = await asyncio.to_thread(cameras_service.usage_map, request)
    return {"ok": True, "usage": usage}
