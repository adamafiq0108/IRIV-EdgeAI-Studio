# -*- coding: utf-8 -*-
"""
control.py -- the Manage/device page and its control API.

Per-device management for the master. Resolves host_serial -> device token
exactly like proxy.py (never a caller-supplied URL), attaches the bearer
server-side, and forwards to the device's Contract v1.2 control channel.

Session-gated by the master's global auth middleware (master_server.py) --
these paths are not in OPEN_PREFIXES. Destructive actions additionally
require an admin (the admin account or a developer), mirroring how
/api/users is admin-only.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.services import auth as auth_service
from core.services import cameras as cameras_service
from core.services import control as control_service

router = APIRouter()


def _device(request, host_serial):
    """Configured device + its token, or 404."""
    token = request.app.state.slave_tokens.get(host_serial)
    if token is None:
        raise HTTPException(status_code=404, detail="unknown device")
    cfg = next((s for s in request.app.state.slaves
                if s.get("host_serial") == host_serial), {})
    return {"host_serial": host_serial, "url": token["url"],
            "token": token["token"], "label": cfg.get("label"),
            "online": cfg.get("online")}


def _is_admin(request):
    return auth_service.is_admin(getattr(request.state, "user", None),
                                     request.app.state.config_dir)


def _live(request, host_serial):
    return next((s for s in request.app.state.slaves
                 if s.get("host_serial") == host_serial), {})


# The Manage page is a React route now (frontend/src/pages/Control.jsx); it reads
# the snapshot below and posts settings/actions to the routes further down.

@router.get("/api/control/{host_serial}", name="control.snapshot")
async def snapshot(request: Request, host_serial: str):
    device = _device(request, host_serial)
    status, body = await asyncio.to_thread(control_service.fetch_control, device)
    return control_service.page_data(device, status, body, _live(request, host_serial))


@router.post("/api/control/{host_serial}/{domain}", name="control.apply")
async def apply(request: Request, host_serial: str, domain: str):
    # Writing a device's camera / detector / network settings is configuration,
    # not operation. It was session-only while the sibling action route below
    # has always been admin-gated for destructive calls -- the same reasoning
    # applies here: repointing a camera or a network interface can take a
    # device off the air just as effectively.
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="admin only")
    device = _device(request, host_serial)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    # Camera assignment: warn (don't block) if a camera IP is already assigned
    # to another slave. `_force` (master-only) skips the check and is stripped
    # before forwarding to the device -- the operator may deliberately share one
    # camera across slaves while testing.
    if domain == "camera":
        force = bool(payload.pop("_force", False))
        if not force:
            new_ips = cameras_service.ips_from_settings(payload)
            if new_ips:
                conflicts = await asyncio.to_thread(
                    cameras_service.conflicts_for, request, host_serial, new_ips)
                if conflicts:
                    return JSONResponse(
                        {"ok": False, "conflict": True, "used_by": conflicts,
                         "error": "camera already assigned to another device"},
                        status_code=409)

    status, body = await asyncio.to_thread(
        control_service.apply_settings, device, domain, payload)
    if status != 200:
        raise HTTPException(status_code=status,
                            detail=(body or {}).get("error", "apply failed"))
    return body


@router.post("/api/control/{host_serial}/action/{name}", name="control.action")
async def action(request: Request, host_serial: str, name: str):
    device = _device(request, host_serial)
    try:
        params = await request.json()
    except Exception:
        params = {}
    # Destructive actions are admin-only. The device declares which via the
    # `destructive` flag; the master re-checks against the fresh snapshot so
    # a stale page can't smuggle a destructive call past the gate.
    if not _is_admin(request):
        status, body = await asyncio.to_thread(control_service.fetch_control, device)
        destructive = {a.get("name")
                       for d in (body.get("domains") or {}).values()
                       for a in (d or {}).get("actions") or []
                       if a.get("destructive")}
        if name in destructive:
            raise HTTPException(status_code=403,
                                detail="this action requires the admin account")
    status, body = await asyncio.to_thread(
        control_service.run_action, device, name, params)
    if status != 200:
        raise HTTPException(status_code=status,
                            detail=(body or {}).get("error", "action failed"))
    return body
