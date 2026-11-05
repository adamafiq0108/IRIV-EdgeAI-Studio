# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
media.py -- footage a system runs detection against instead of a camera.

Upload a clip or a still, then pick it as the camera source. Everything after
capture is unchanged: the same zones, functions, tracker and outputs run
against it, because the source string is all that differs (`file:<name>`).

Admin-gated on the write side. Uploading decides what the detector looks at,
which is the same authority as pointing it at a different camera.
"""
import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from core.services import auth as auth_service
from core.services import log_catalog as catalog
from core.services import detector_control as dc
from core.services import media as media_service

router = APIRouter()


def _system_context(request):
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        raise HTTPException(
            status_code=400,
            detail="footage belongs to a system; open a system's dashboard")
    return systems_dir, system.slug


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/api/media", name="media.list")
def list_media(request: Request):
    systems_dir, slug = _system_context(request)
    return {"ok": True, "media": dc.list_media(systems_dir, slug),
            "max_bytes": media_service.MAX_BYTES,
            "extensions": list(media_service.EXTS)}


@router.post("/api/media", name="media.upload")
async def upload_media(request: Request, file: UploadFile = File(...)):
    _require_admin(request)
    systems_dir, slug = _system_context(request)
    # Read once, in full: the cap is enforced on what actually arrived rather
    # than on a Content-Length a client is free to misreport.
    blob = await file.read()
    try:
        saved = dc.save_media(systems_dir, slug, file.filename, blob)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except OSError as exc:
        # "could not save: [Errno 28] No space left on device '/opt/cytron/
        # store/...'" told the operator a path they cannot act on and hid the
        # one thing they can: free some space.
        raise catalog.http_error("STO_MEDIA_SAVE_FAILED", cause=exc,
                                 filename=file.filename or "") from exc
    return {"ok": True, "file": saved}


@router.delete("/api/media/{name}", name="media.delete")
def delete_media(request: Request, name: str):
    _require_admin(request)
    systems_dir, slug = _system_context(request)
    if not dc.delete_media(systems_dir, slug, name):
        raise HTTPException(status_code=404, detail="no such file")
    return {"ok": True, "removed": name}


@router.get("/api/media/{name}", name="media.file")
def media_file(request: Request, name: str):
    """The file itself, so the page can show what it is about to run on."""
    systems_dir, slug = _system_context(request)
    path = dc.media_file(systems_dir, slug, name)
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="no such file")
    return FileResponse(path, filename=os.path.basename(path))
