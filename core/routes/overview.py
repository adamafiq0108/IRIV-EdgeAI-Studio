# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
overview.py -- Platform Overview + Camera Overview pages + JSON (v2.0 #8,#17).

Thin (conventions sec 2). Session-gated by the master middleware. The
overview page auto-refreshes via /api/overview; global search/filter is
client-side over /api/devices (core/routes/devices.py).
"""

from fastapi import APIRouter, Request

from core import config
from core.services import health as health_service
from core.services import overview as overview_service

router = APIRouter()


def _thr(request):
    return health_service.thresholds(config.monitoring(request.app.state.config_dir))


# Platform + Cameras pages are React routes now (frontend/src/pages/Platform.jsx,
# Cameras.jsx); their data is served by /api/overview here and /api/cameras in
# core/routes/spa.py.

@router.get("/api/overview", name="overview.stats")
def overview_stats(request: Request):
    st = request.app.state
    return {"ok": True,
            "stats": overview_service.platform_stats(st.slaves, st.events, _thr(request))}
