# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
hostname.py -- the Hostname card on the Network page.

MASTER OR DEVELOPER ONLY, read included, like the Software port card: the
name is the box's identity on the LAN (<name>.local, the front panel line,
every bookmark that uses it), which is the box owner's call.

Apply SAVES the name for the next restart of the box (core/services/
boxpending.py); the box keeps its name until then. The page names the new
address before it asks for the restart, and says so when the browser is on
<current>.local, the one session a rename drops; an IP address in the
address bar is untouched (core/services/hostname.py).
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import boxpending, hostname, hubport

router = APIRouter()


def _require_owner(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="only master may change the hostname")


def _status(request):
    out = hostname.status()
    # THIS page opened by the box's .local name: a rename takes it away.
    out["via_name"] = hostname.reached_by_name(net.request_host(request), out)
    cfg = request.app.state.config_dir
    out["port"] = hubport.running_port() or hubport.port(cfg)
    out["pending"] = hostname.pending(cfg) or None
    out["restart"] = boxpending.status(request, cfg)
    out["ok"] = True
    return out


@router.get("/api/hub/hostname", name="hostname.status")
async def hostname_status(request: Request):
    _require_owner(request)
    # can_set() forks sudo: off the event loop (perf item L5).
    return await asyncio.to_thread(_status, request)


@router.post("/api/hub/hostname", name="hostname.save")
async def hostname_save(request: Request):
    _require_owner(request)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    name = body.get("name") if isinstance(body, dict) else None
    cfg = request.app.state.config_dir
    try:
        staged = await asyncio.to_thread(hostname.stage, name, boxpending.boot_id(), cfg)
    except hostname.HostnameError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        audit_service.record(action="hub.hostname", result="ok",
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request),
                             detail={"from": hostname.current(), "pending": staged},
                             config_dir=cfg)
    except Exception:                                        # noqa: BLE001
        pass                    # best-effort, as every box-setting route
    return await asyncio.to_thread(_status, request)
