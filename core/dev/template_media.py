# -*- coding: utf-8 -*-
"""
template_media.py -- edit a built-in template's preview media. Owner's box only.

The System Templates page's "Preview media" editor (frontend
TemplateMediaEditor.jsx): add a picture or video, reorder, caption, delete.
What it writes is what "Publish templates" sends to boxes, signed
(core/services/template_media_sync.py).

Developer tier per route, like every route in core/dev -- the mount is the
outer lock, this is the inner one. Under /api/hub/formats/<key>/media rather
than /api/hub/templates/..., whose {category:path}/{slug} routes (the saved
library) would otherwise swallow these paths.

Every rule lives in core/services/template_media.py: the kind read from the
file's first bytes, 5 MB a file, 5 slides with at most 1 video a template,
names made from the content hash. Nothing the browser sends becomes a path.
"""

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from core.routes.hub import _require_developer
from core.services import provision as provision_service
from core.services import template_media

router = APIRouter()


def _known(request, key):
    try:
        template_media.check_key(key)
    except template_media.MediaError:
        raise HTTPException(status_code=404, detail="no such template")
    if key not in provision_service.builtin_formats(request.app.state.config_dir,
                                                    include_hidden=True):
        raise HTTPException(status_code=404, detail="no such template")
    return key


def _answer(key, items):
    return {"ok": True, "key": key, "items": items,
            **template_media.counts(items)}


@router.get("/api/hub/formats/{key}/media", name="formats.media")
def list_media(key: str, request: Request):
    _require_developer(request, "editing preview media")
    _known(request, key)
    return _answer(key, template_media.items(key))


@router.post("/api/hub/formats/{key}/media", name="formats.media_add")
async def add_media(key: str, request: Request, file: UploadFile = File(...),
                    caption: str = Form("")):
    _require_developer(request, "editing preview media")
    _known(request, key)
    # One byte past the ceiling is enough to know it is over; the rest is
    # never read into memory.
    blob = await file.read(template_media.MAX_BYTES + 1)
    try:
        items = template_media.add(key, blob, caption, file.filename or "")
    except template_media.MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _answer(key, items)


@router.put("/api/hub/formats/{key}/media", name="formats.media_arrange")
async def arrange_media(key: str, request: Request):
    """Body {order: [file...], captions: {file: text}}."""
    _require_developer(request, "editing preview media")
    _known(request, key)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        items = template_media.arrange(key, body.get("order"), body.get("captions"))
    except template_media.MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _answer(key, items)


@router.delete("/api/hub/formats/{key}/media/{name}", name="formats.media_delete")
def delete_media(key: str, name: str, request: Request):
    _require_developer(request, "editing preview media")
    _known(request, key)
    try:
        items = template_media.remove(key, name)
    except template_media.MediaError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _answer(key, items)
