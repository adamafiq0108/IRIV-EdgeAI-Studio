# -*- coding: utf-8 -*-
"""
boxrestart.py -- "Restart now" on the Software port and Hostname cards.

ONE request restarts the box for whatever those two cards saved (either or
both): core/services/boxpending.py says why the scope is a reboot, and the
hub's first start in the new boot applies the change. Master or developer
only, like both cards. Refused when nothing waits for a restart -- this is
not a second power button; the front panel and the Network page have that.

The reply leaves before the box goes down (boxpending.reboot delays), and
carries `after`: where the dashboard will be, by name and by address.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import boxpending

router = APIRouter()


@router.post("/api/hub/restart", name="boxrestart.restart")
async def box_restart(request: Request):
    cfg = request.app.state.config_dir
    if not auth_service.is_admin(getattr(request.state, "user", None), cfg):
        raise HTTPException(status_code=403,
                            detail="only master or a developer may restart the box "
                                   "for a port or hostname change")
    st = await asyncio.to_thread(boxpending.status, request, cfg)
    if not st["any"]:
        raise HTTPException(status_code=400,
                            detail="nothing is waiting for a restart")
    if not st["can_reboot"]:
        raise HTTPException(status_code=409,
                            detail="this box cannot restart itself from the page - "
                                   "switch it off and on again, and the change "
                                   "applies then")
    try:
        audit_service.record(action="hub.restart", result="ok",
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request),
                             detail={"port": st["port"], "hostname": st["hostname"]},
                             config_dir=cfg)
    except Exception:                                        # noqa: BLE001
        pass                    # best-effort, as every box-setting route
    boxpending.reboot()
    return {"ok": True, "restarting": True, "after": st["after"]}
