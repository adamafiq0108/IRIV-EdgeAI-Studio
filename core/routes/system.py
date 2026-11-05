# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
system.py -- (React SPA migration) the pages now render client-side.

Every page that used to render here (main, analytics, stream, footage, settings,
email) is a React route in frontend/src/pages/, served by the SPA catch-all in
master_server.py. Their data moved to JSON endpoints in core/routes/spa.py
(/api/main, /api/analytics, /api/cameras, /api/footage, /api/stream,
/api/settings) plus the existing per-feature routers (settings/email/...).

It is now also the home of /api/host/meter -- the hardware footer's sample --
because that is a page-level GET belonging to the system's own pages rather than
to any one feature. The footer sits under Detection, OCR, Zones and Workflow, so
its endpoint has to live on the MASTER: /api/hub/health is the hub's route, and
the hub is a different process on a different port, unreachable from these pages.

This router intentionally never returns HTML.
"""

from fastapi import APIRouter, Request

from core.routes import perm

router = APIRouter()


@router.get("/api/host/meter", name="system.host_meter")
@perm.shared("host_meter")
def host_meter(request: Request):
    """One cheap sample of what the box is doing right now, for the footer.

    Deliberately NOT /api/hub/health's payload. That one blocks 250 ms measuring
    the CPU and shells out to hailortcli twice, which is affordable once for a
    page and ruinous every two seconds under detection and OCR -- a monitor that
    costs 12% of a core is measuring itself. host_meter() samples the CPU from
    counter deltas instead and caches the two 50 ms readings
    (core/services/host_health.py has the numbers).

    Any field the box cannot measure is null, never 0.
    """
    from core.services import host_health as host_health_service
    from core.services import detector_control as detector_control_service

    st = request.app.state
    out = host_health_service.host_meter()
    out["ok"] = True
    # Detection FPS: the one number that says whether analysis is KEEPING UP,
    # as opposed to how hard the box is working to fall behind. One state-file
    # read (see fps_snapshot) -- the full status() call is far too heavy here.
    system = getattr(st, "system", None)
    slug = getattr(system, "slug", None)
    det = {"running": False, "fps": None}
    if slug:
        try:
            det = detector_control_service.fps_snapshot(st.systems_dir, slug)
        except Exception:                               # noqa: BLE001
            pass
    out["fps"] = det.get("fps")
    # The footer polls on THIS: work in flight means a fast poll, an idle box a
    # slow one. Detection running is the honest signal for both things the user
    # watches -- an OCR read goes through the running detection app, and every
    # model on the NPU is one of these apps.
    out["busy"] = bool(det.get("running")) or bool((out.get("npu") or {}).get("models"))
    return out
