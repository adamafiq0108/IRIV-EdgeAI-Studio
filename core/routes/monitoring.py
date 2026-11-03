# -*- coding: utf-8 -*-
"""
monitoring.py -- Live Monitoring page + its JSON feeds (v2.0 #6).

Thin (conventions sec 2): read app.state -> monitoring/health services ->
render/return. Session-gated by the master middleware. The page auto-refreshes
by polling /api/monitoring/live and /api/monitoring/bandwidth.
"""

from fastapi import APIRouter, Request

from core import config
from core.services import health as health_service
from core.services import monitoring as monitoring_service
from core.services import nav as nav_service

router = APIRouter()


def _thr(request):
    return health_service.thresholds(config.monitoring(request.app.state.config_dir))


# The Live Monitoring page is a React route now (frontend/src/pages/Monitoring.jsx);
# it polls the two JSON feeds below.

@router.get("/api/monitoring/live", name="monitoring.live")
def monitoring_live(request: Request):
    return {"ok": True,
            "rows": monitoring_service.live_rows(request.app.state.slaves, _thr(request))}


@router.get("/api/monitoring/bandwidth", name="monitoring.bandwidth")
def monitoring_bandwidth(request: Request):
    return {"ok": True,
            "series": monitoring_service.bandwidth_series(
                request.app.state.bandwidth_history)}
