# -*- coding: utf-8 -*-
"""
update_local.py -- the update routes that need no GitHub at all.

  POST   /api/hub/update/auto            "Check for updates automatically"
  POST   /api/hub/update/manual          upload an offline update file
  POST   /api/hub/update/manual/install  install the file that is waiting
  DELETE /api/hub/update/manual          cancel: remove the waiting file

Beside core/routes/update.py rather than in it (that file is at its line
ceiling). Every route has the Update button's gate: an admin of the box.
The file routes go through update._gate on the box's own channel, so on a
developer box (where only a developer file installs) the account must be a
developer too, exactly as for the Developer build tab's Update.

The upload is the request BODY, raw (application/octet-stream), streamed to
disk chunk by chunk into the private offline dir and capped while it
arrives; no multipart, so nothing is spooled to /tmp first and the name
the browser gives the file is never seen. uvicorn has no body read timeout,
so the loop keeps its own: a chunk has IDLE_S to arrive and the whole body
DEADLINE_S (update_offline), or the slot is given back. Refusals are plain
sentences in `details` (update_offline.OfflineError), under
SYS_UPDATE_APPLY_FAILED.
"""

import asyncio
import time

from fastapi import APIRouter, Request

from core.routes.hub import _require_admin
from core.routes.update import _gate
from core.services import log_catalog as catalog
from core.services import update as update_service
from core.services import update_auto, update_offline

router = APIRouter()


@router.post("/api/hub/update/auto", name="update.auto")
async def set_auto(request: Request):
    _require_admin(request, "changing automatic update checks")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    on = bool((body or {}).get("enabled"))
    update_auto.set_enabled(on, request.app.state.config_dir)
    return {"ok": True, "auto_check": on}


def _box_channel(request, action):
    """The channel a file must belong to on THIS box, after the gate."""
    cfg = request.app.state.config_dir
    return _gate(request, action, update_service.CHANNEL_DEV
                 if update_service.dev_available(cfg) else update_service.CHANNEL_STABLE)


def _refused(exc, **meta):
    return catalog.http_error("SYS_UPDATE_APPLY_FAILED", cause=exc, level="WARNING",
                              details=str(exc), offline_file=True, **meta)


@router.post("/api/hub/update/manual", name="update.manual_upload")
async def upload(request: Request):
    channel = _box_channel(request, "uploading an update file")
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    try:
        part = update_offline.begin(length)
    except update_offline.OfflineError as e:
        raise _refused(e) from e
    try:
        size, started, chunks = 0, time.monotonic(), request.stream().__aiter__()
        with update_offline.open_part(part) as out:
            while True:
                try:
                    chunk = await asyncio.wait_for(chunks.__anext__(), update_offline.IDLE_S)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise update_offline.OfflineError(
                        "The upload stalled, so it was stopped. Try again.")
                size += len(chunk)
                update_offline.received(size, len(chunk), length, started)
                # Off the loop: a 60 MB file is a thousand writes, and on a
                # slow eMMC each one inline stalled every other request.
                await asyncio.to_thread(out.write, chunk)
        if not size:
            raise update_offline.OfflineError("No file arrived. Choose the update "
                                              "file and try again.")
        info = await asyncio.to_thread(update_offline.accept, part,
                                       request.app.state.config_dir, channel)
    except update_offline.OfflineError as e:
        update_offline.discard()
        raise _refused(e) from e
    except BaseException:
        update_offline.discard()
        raise
    finally:
        update_offline.end()
    return {"ok": True, "offline": info}


@router.post("/api/hub/update/manual/install", name="update.manual_install")
async def install(request: Request):
    channel = _box_channel(request, "installing an update file")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        status = await asyncio.to_thread(
            update_offline.install, (body or {}).get("id") or "",
            bool((body or {}).get("reinstall")), request.app.state.config_dir, channel)
    except update_service.UpdateBusy as e:
        raise catalog.http_error("SYS_UPDATE_BUSY", cause=e) from e
    except (update_offline.OfflineError, update_service.UpdateError) as e:
        raise _refused(e) from e
    return {"ok": True, "status": status}


@router.delete("/api/hub/update/manual", name="update.manual_cancel")
def cancel(request: Request):
    _box_channel(request, "removing an update file")
    return {"ok": True, "removed": update_offline.discard()}
