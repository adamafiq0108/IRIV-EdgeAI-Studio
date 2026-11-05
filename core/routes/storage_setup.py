# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
storage_setup.py -- "New storage detected": the popup's poll, the admin's
No, and Format / Use as it is (core/services/storage_setup.py finds and
remembers drives, core/services/storage_job.py runs the root helper).

MASTER only (auth_service.is_admin), like the box's name and port: erasing
a disk is a statement about the hardware, and a site admin is not shown the
popup at all. The drive is named by its id from the box's own list; a path
from the browser is never used. Every start, refusal, outcome and No is
audited; the outcome also goes to the Inbox.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import storage_job
from core.services import storage_setup

router = APIRouter()


def _master(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="setting up a drive is for the master account")


def _audit(request, action, result, detail, user=None):
    try:
        audit_service.record(action=action, result=result,
                             username=user or getattr(request.state, "user", None),
                             source_ip=net.client_ip(request),
                             detail=detail, config_dir=request.app.state.config_dir)
    except Exception:                                        # noqa: BLE001
        pass                    # best-effort, as every box-setting route


async def _can():
    can = await asyncio.to_thread(storage_job.can_setup)
    return {"job": storage_job.job(), "can_setup": can,
            "needs": "" if can else storage_job.needs_installer()}


@router.get("/api/hub/storage/new", name="storage_setup.new")
async def storage_new(request: Request):
    """The popup's poll: the drive to ask about now (or null), the job in
    flight, and whether this box can set one up at all."""
    _master(request)
    dev = await asyncio.to_thread(storage_setup.prompt, request.app.state.config_dir)
    return {"prompt": dev, **(await _can())}


@router.get("/api/hub/storage/devices", name="storage_setup.devices")
async def storage_devices(request: Request):
    """Every drive that could be set up, declined or not: the "Set up
    storage" button on Data Retention."""
    _master(request)
    found = await asyncio.to_thread(storage_setup.devices, 0)
    no = set(storage_setup.declined(request.app.state.config_dir))
    return {"devices": [dict(d, declined=d["id"] in no) for d in found],
            "has_drive": storage_setup._store_has_drive(), **(await _can())}


@router.get("/api/hub/storage/inspect", name="storage_setup.inspect")
async def storage_inspect(request: Request, id: str = ""):
    """Before "use it as it is": does the drive already hold another box's
    IRIV software? (a read-only look by the root helper)."""
    _master(request)
    try:
        return await asyncio.to_thread(storage_setup.inspect, id[:200])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/hub/storage/decline", name="storage_setup.decline")
async def storage_decline(request: Request):
    """The admin's No: this drive is left untouched and unmounted, and not
    asked about again; another drive asks."""
    _master(request)
    body = await request.json()
    try:
        out = await asyncio.to_thread(storage_setup.decline, body.get("id"),
                                      request.app.state.config_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _audit(request, "storage.decline", "ok", {"id": str(body.get("id"))[:200]})
    return {"ok": True, "declined": out}


@router.post("/api/hub/storage/setup", name="storage_setup.setup")
async def storage_setup_start(request: Request):
    """Format (confirm must be FORMAT) or mount what is on it, in the
    background; the popup follows /api/hub/storage/new's `job`."""
    _master(request)
    body = await request.json()
    dev_id, fmt = str(body.get("id") or "")[:200], bool(body.get("format"))
    st = request.app.state
    user = getattr(request.state, "user", None) or ""

    def finished(job):
        # The outcome, audited when the helper ends; the Inbox notice is
        # storage_job's own.
        _audit(request, "storage.setup", job.get("state"),
               {"id": dev_id, "format": fmt,
                "message": str(job.get("message") or "")[:300]}, user)

    try:
        out = await asyncio.to_thread(lambda: storage_job.start(
            dev_id, fmt, str(body.get("confirm") or ""), st.config_dir,
            len(st.registry), user, on_done=finished))
    except ValueError as exc:
        _audit(request, "storage.setup", "refused",
               {"id": dev_id, "format": fmt, "reason": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dev = out.get("device") or {}
    _audit(request, "storage.setup", "started",
           {"id": dev_id, "format": fmt, "disk": dev.get("name"),
            "model": dev.get("model"), "size": dev.get("size")})
    return {"ok": True, "job": out}
