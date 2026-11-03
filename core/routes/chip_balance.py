# -*- coding: utf-8 -*-
"""
chip_balance.py -- GET /api/system/chip-balance: the live table under the
"Hailo load balancer (testing)" card in Settings > System.

READ-ONLY. The switch itself is saved through POST /api/system/settings like
every other box switch (core/routes/auth.py, core/services/sysconfig.py);
this route only reports what the apps' heartbeats say
(core/services/chip_balance.py). Master or developer, like the rest of the
System tab. The card polls it every 5 s while it is on screen, and never
faster.
"""

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.services import chip_balance
from core.services import sysconfig as sysconfig_service

router = APIRouter()


@router.get("/api/system/chip-balance", name="system.chip_balance")
def system_chip_balance(request: Request):
    """The switch, and per system and camera: model, paced fps, measured
    fps, share of the chip in use, and why it is capped."""
    cfg = request.app.state.config_dir
    me = getattr(request.state, "user", None) or ""
    if not auth_service.is_admin(me, cfg):
        raise HTTPException(status_code=403, detail="master only")
    return {"ok": True, "on": sysconfig_service.chip_balance(cfg),
            **chip_balance.snapshot()}
