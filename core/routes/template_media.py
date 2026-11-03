# -*- coding: utf-8 -*-
"""
template_media.py -- serve a built-in template's preview media.

The Hub home's template preview (frontend TemplateGallery.jsx) shows the
pictures and video listed for each template (core/services/
template_media.py). Read-only and on every box, so a customer box shows the
media its signed template update brought; writing is the developer hub's
(core/dev/template_media.py).

Any signed-in account may read, like the template list itself. A file is
served only when the key's list names it, with the content type of its
validated kind and nosniff, so a browser never second-guesses the type.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from core.services import template_media

router = APIRouter()


@router.get("/api/hub/template-media/{key}/{name}", name="hub.template_media")
def template_media_file(key: str, name: str, request: Request):
    if not getattr(request.state, "user", None):
        raise HTTPException(status_code=401, detail="sign in first")
    found = template_media.path_of(key, name)
    if not found or not found[0].is_file():
        raise HTTPException(status_code=404, detail="no such preview file")
    path, ctype = found
    # Content-named, so a file never changes under its name: cache it.
    return FileResponse(path, media_type=ctype, headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=604800, immutable",
        "Content-Disposition": "inline"})
