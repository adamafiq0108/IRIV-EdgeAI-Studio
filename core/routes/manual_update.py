# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
manual_update.py -- the About page's "Software update" card.

  GET    /api/hub/update/status     what is live, what came before, what waits
  POST   /api/hub/update/upload     the release file, as the raw request body
  DELETE /api/hub/update/upload     throw the waiting file away
  POST   /api/hub/update/install    unpack it and switch; the hub then restarts
  POST   /api/hub/update/rollback   go back to the previous version

Master only, like every other change to what the box runs. The upload is the
request BODY, streamed to disk chunk by chunk and capped as it arrives (no
multipart, so nothing is spooled to /tmp and the browser's file name is never
seen). uvicorn has no body read timeout, so the loop keeps its own: a chunk has
IDLE_S to arrive and the whole body DEADLINE_S, or the slot is given back.
"""
import asyncio
import time

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import manual_update

router = APIRouter()

IDLE_S = 30
DEADLINE_S = 15 * 60


def _admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="updating the software is an admin action")


def _audit(request, action, result, detail=None):
    try:
        audit_service.record(action=action, result=result,
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request), detail=detail or {},
                             config_dir=request.app.state.config_dir)
    except Exception:                                        # noqa: BLE001
        pass                       # best-effort, as every box-setting route


@router.get("/api/hub/update/status", name="update.status")
async def update_status(request: Request):
    _admin(request)
    return {"ok": True, **await asyncio.to_thread(manual_update.status)}


@router.post("/api/hub/update/upload", name="update.upload")
async def update_upload(request: Request):
    _admin(request)
    try:
        path = await asyncio.to_thread(manual_update.begin_upload)
    except manual_update.UpdateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > manual_update.MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="that file is too large to be an update")
    size, started = 0, time.monotonic()
    stream = request.stream().__aiter__()
    try:
        with open(path, "wb") as out:
            while True:
                try:
                    chunk = await asyncio.wait_for(stream.__anext__(), timeout=IDLE_S)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise HTTPException(status_code=408, detail="the upload stalled")
                size += len(chunk)
                if size > manual_update.MAX_UPLOAD:
                    raise HTTPException(status_code=413,
                                        detail="that file is too large to be an update")
                if time.monotonic() - started > DEADLINE_S:
                    raise HTTPException(status_code=408, detail="the upload took too long")
                out.write(chunk)
        found = await asyncio.to_thread(manual_update.inspect, path)
    except manual_update.UpdateError as e:
        manual_update.discard()
        _audit(request, "update.upload", "refused", {"why": str(e)[:200]})
        raise HTTPException(status_code=400, detail=str(e))
    except BaseException:
        manual_update.discard()
        raise
    _audit(request, "update.upload", "ok", {"version": found["version"], "bytes": size})
    return {"ok": True, "staged": found}


@router.delete("/api/hub/update/upload", name="update.discard")
async def update_discard(request: Request):
    _admin(request)
    await asyncio.to_thread(manual_update.discard)
    return {"ok": True}


@router.post("/api/hub/update/install", name="update.install")
async def update_install(request: Request):
    _admin(request)
    try:
        done = await asyncio.to_thread(manual_update.install)
    except manual_update.UpdateError as e:
        _audit(request, "update.install", "refused", {"why": str(e)[:200]})
        raise HTTPException(status_code=400, detail=str(e))
    _audit(request, "update.install", "ok", done)
    return {"ok": True, "restarting": True, **done}


@router.post("/api/hub/update/rollback", name="update.rollback")
async def update_rollback(request: Request):
    _admin(request)
    try:
        await asyncio.to_thread(manual_update.rollback)
    except manual_update.UpdateError as e:
        _audit(request, "update.rollback", "refused", {"why": str(e)[:200]})
        raise HTTPException(status_code=400, detail=str(e))
    _audit(request, "update.rollback", "ok")
    return {"ok": True, "restarting": True}
