# -*- coding: utf-8 -*-
"""
template_copy.py -- a built-in template's own preview words. Owner's box only.

The System Templates page's "What you get" editor (frontend
TemplateCopyEditor.jsx) writes the lines shown in the Hub home's template
preview (`features`) and the sentence under its title (`blurb`). Both live in
the template's formats.toml entry (core/services/template_preview.py
set_copy), so Publish templates signs them with the rest of the template and
a box's template update installs them -- no signing of their own.

Empty = the automatic text, which is what "Use automatic text" sends.
Developer tier per route, like everything in core/dev. Reading needs no
route here: GET /api/hub/templates carries both to any admin.
"""

from fastapi import APIRouter, HTTPException, Request

from core.routes.hub import _require_developer
from core.services import template_preview

router = APIRouter()


@router.put("/api/hub/formats/{key}/copy", name="formats.copy")
async def save_copy(key: str, request: Request):
    """Body {features: [line...], blurb: text, heading: text}; all empty =
    automatic."""
    _require_developer(request, "editing a template's preview text")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        saved = template_preview.set_copy(
            key, body.get("features") or [], body.get("blurb") or "",
            request.app.state.config_dir, heading=body.get("heading") or "")
    except (ValueError, TypeError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "key": key, **saved}
