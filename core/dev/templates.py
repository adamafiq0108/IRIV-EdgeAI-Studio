# -*- coding: utf-8 -*-
"""
templates.py -- the saved-template library. Owner's box only.

Saving, editing, exporting and importing reusable templates. This is the
developer's working library -- the place a built-in format gets authored from
(core/dev/formats.py promotes one) -- not something a deployment browses. An
admin meets templates where they USE them: the create wizard's built-in list and
the Template Store, both curated.

That is why LISTING lives elsewhere: GET /api/hub/templates in
core/routes/hub.py serves the create picker and already filters the saved
library out for non-developers. Only the write side moved here, so a customer
install loses the ability to author templates without losing the ability to
create systems from them.
"""

import json

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from core.routes.hub import _require_developer
from core.services import auth as auth_service
from core.services import templates as templates_service

router = APIRouter()


def _require_template_owner(request, category, slug, action):
    """Developer action, and only on templates THEY saved.

    The saved-template library is product work, so the tier gate is developer
    (matching the page, which no other account sees). Ownership still applies
    between developer accounts; an ownerless (legacy) template stays
    manageable -- inventing an owner would orphan it.
    """
    _require_developer(request, action)
    username = getattr(request.state, "user", None) or ""
    if auth_service.is_developer(username, request.app.state.config_dir):
        return
    try:
        doc = templates_service.load_template(category, slug)
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="no such template")
    owner = str(doc.get("owner") or "")
    if owner and owner != username:
        raise HTTPException(status_code=403,
                            detail=f"{action} is only for whoever saved it")


@router.post("/api/hub/templates", name="hub.create_template")
async def create_template(request: Request):
    """Save an existing system as a reusable template. Body: {from_slug, name,
    description, category}. Developer action -- the saved library is where a
    built-in is authored from; the template records who saved it."""
    _require_developer(request, "saving a template")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    st = request.app.state
    creator = getattr(request.state, "user", None) or ""
    slug = str(body.get("from_slug") or "").strip()
    if slug not in [s.slug for s in st.registry]:
        raise HTTPException(status_code=404, detail="no such system")
    try:
        doc = templates_service.snapshot_system(
            st.systems_dir, st.config_dir, slug,
            name=body.get("name") or slug,
            description=body.get("description") or "",
            category=body.get("category") or templates_service.UNCATEGORIZED,
            owner=creator)
        key = templates_service.slugify(body.get("slug") or doc["name"])
        saved = templates_service.save_template(doc["category"], key, doc)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # A developer's saves are additionally archived on the NVMe
    # (IRIV-VS-Admin/template) -- the owner's library of everything authored.
    if auth_service.is_developer(creator, st.config_dir):
        templates_service.mirror_template(
            doc, templates_service.dev_templates_root(), key)
    return {"ok": True, "slug": key, "category": saved["category"],
            "name": saved["name"]}


@router.get("/api/hub/templates/{category:path}/{slug}/export",
            name="hub.export_template")
def export_template(category: str, slug: str, request: Request):
    _require_template_owner(request, category, slug, "exporting a template")
    try:
        doc = templates_service.load_template(category, slug)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(
        doc, headers={"Content-Disposition":
                      f'attachment; filename="template-{slug}.json"'})


@router.put("/api/hub/templates/{category:path}/{slug}", name="hub.update_template")
async def update_template(category: str, slug: str, request: Request):
    """Rename a template, move it to another category, or edit its copy."""
    _require_template_owner(request, category, slug, "editing a template")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        doc = templates_service.load_template(category, slug)
        if body.get("name"):
            doc["name"] = str(body["name"])[:80]
        if body.get("description") is not None:
            doc["description"] = str(body["description"])[:400]
        new_category = body.get("category") or category
        new_slug = templates_service.slugify(body["slug"]) if body.get("slug") else slug
        doc["category"] = "/".join(templates_service._segments(new_category))
        templates_service.save_template(new_category, new_slug, doc)
        if (new_category, new_slug) != (category, slug):
            templates_service.delete_template(category, slug)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "slug": new_slug, "category": doc["category"]}


@router.delete("/api/hub/templates/{category:path}/{slug}", name="hub.delete_template")
def delete_template(category: str, slug: str, request: Request):
    _require_template_owner(request, category, slug, "deleting a template")
    try:
        gone = templates_service.delete_template(category, slug)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not gone:
        raise HTTPException(status_code=404, detail="no such template")
    return {"ok": True}


@router.post("/api/hub/templates/import", name="hub.import_template")
async def import_template(request: Request, file: UploadFile = File(...)):
    """Install a template file someone exported from another box."""
    _require_developer(request, "importing a template")
    raw = await file.read(templates_service.MAX_TEMPLATE_BYTES + 1)
    if len(raw) > templates_service.MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=400, detail="that file is too large to be a template")
    try:
        doc = templates_service.validate_template(json.loads(raw.decode("utf-8", "replace")))
        # The importer becomes the owner -- whoever the FILE names saved it on
        # some other box, and that name means nothing on this one.
        doc["owner"] = getattr(request.state, "user", None) or ""
        key = templates_service.slugify(doc["name"])
        templates_service.save_template(doc["category"], key, doc)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="that file is not valid JSON")
    return {"ok": True, "slug": key, "category": doc["category"], "name": doc["name"]}


@router.post("/api/hub/template-folders", name="hub.create_template_folder")
async def create_template_folder(request: Request):
    _require_developer(request, "creating a folder")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        path = templates_service.create_category(body.get("path"))
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "path": path}


@router.delete("/api/hub/template-folders/{path:path}", name="hub.delete_template_folder")
def delete_template_folder(path: str, request: Request):
    _require_developer(request, "deleting a folder")
    try:
        gone = templates_service.delete_category(path)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not gone:
        raise HTTPException(status_code=404, detail="no such folder")
    return {"ok": True}
