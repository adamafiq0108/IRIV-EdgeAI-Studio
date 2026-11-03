# -*- coding: utf-8 -*-
"""
history.py -- the stat sampler's read API (core/services/history.py).

GET /api/history?keys=a,b&range=1h|24h|7d returns aligned time series for any
stat keys the master has been sampling. Read-only; every signed-in operator
may chart what the dashboard already shows live.
"""

from fastapi import APIRouter, Query, Request

from core.services import history as history_service

router = APIRouter()


@router.get("/api/history", name="history.series")
def get_series(request: Request,
               keys: str = Query(""),
               range: str = Query("24h")):
    wanted = [k.strip() for k in keys.split(",") if k.strip()][:12]
    range_key = range if range in history_service.RANGES else "24h"
    return {"ok": True, "keys": history_service.keys(),
            **history_service.series(wanted, range_key)}
