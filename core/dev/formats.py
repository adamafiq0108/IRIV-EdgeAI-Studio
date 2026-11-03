# -*- coding: utf-8 -*-
"""
formats.py -- authoring the built-in formats. Owner's box only.

A format is what a new system STARTS from: its classes, zone guidance, starter
dashboard and workflow. That is product content, not deployment data -- a
customer picks one, they do not write one -- so every route here is developer
gated and the whole module is absent from a customer install (core/deploy.py).

The authoring model is deliberately "build it, then publish it". A format's job
is to be a good starting point, and the only way to know whether it is one is to
have built with it: `workbench` opens the format as a real system, the developer
customises it like any other, and `publish_back` folds the result into the
format. Filling in a form would be easier and would produce worse formats.

Reading formats is NOT here -- the create wizard needs the list, so
GET /api/hub/formats stays in core/routes/hub.py for any admin. Only writing
moved.
"""

import pathlib
import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Request

from core.routes.hub import _rebuild_registry, _require_developer
from core.services import auth as auth_service
from core.services import hub as hub_service
from core.services import provision as provision_service
from core.services import templates as templates_service

router = APIRouter()


def _running_model(systems_dir, slug):
    """Which detection model a system is set to run, or "". Read from the
    package's merged config -- the same place the Models page reads."""
    try:
        from core.services import builtin_models          # noqa: PLC0415
        root = provision_service._pkg_root(systems_dir, slug)
        listed = builtin_models.listing(root, slug)
        return str(listed.get("current") or "")
    except Exception:                                     # noqa: BLE001
        return ""


def apply_format_workflow(st, slug, template_key):
    """Compatibility shim -- the real one moved to core/services/templates.py
    so a customer install (no core/dev) applies a format's workflow too."""
    fmt = provision_service.builtin_formats(st.config_dir).get(template_key)
    return templates_service.apply_format_workflow(
        st.systems_dir, st.config_dir, slug, fmt, key=template_key)


@router.put("/api/hub/formats/{key}", name="hub.save_format")
async def save_format(key: str, request: Request):
    """Create or override one built-in format. Developer only.

    Writes config/formats.toml, which is COMMITTED on purpose -- an authored
    format is product content and is meant to ship (see provision.py).
    """
    _require_developer(request, "editing a built-in format")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        saved = provision_service.save_format(
            key, body or {}, request.app.state.config_dir)
    except (ValueError, TypeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    if saved and not saved.get("draft"):
        templates_service.mirror_template(
            dict(saved, key=key), templates_service.public_templates_root(),
            key)
    return {"ok": True, "key": key, "format": saved}


@router.delete("/api/hub/formats/{key}", name="hub.delete_format")
def delete_format(key: str, request: Request, hide: bool = Query(False)):
    """Remove a format.

    `hide=false` (default) drops a developer's edits: an authored format goes,
    a shipped one reverts to what the product ships. `hide=true` removes a
    SHIPPED format from the picker entirely, via a tombstone -- the code dict
    cannot be edited from a board, so that is the only honest delete for one.
    Either way the last remaining format cannot be removed.
    """
    _require_developer(request, "deleting a built-in format")
    try:
        result = provision_service.delete_format(
            key, request.app.state.config_dir, hide_shipped=hide)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "key": key, **result,
            "formats": provision_service.builtin_formats(
                request.app.state.config_dir)}


@router.post("/api/hub/formats/{key}/restore", name="hub.restore_format")
def restore_format(key: str, request: Request):
    """Bring a hidden shipped format back into the picker."""
    _require_developer(request, "restoring a built-in format")
    try:
        fmt = provision_service.restore_format(key, request.app.state.config_dir)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "key": key, "format": fmt}


@router.post("/api/hub/formats/{key}/draft", name="hub.format_draft")
async def format_draft(key: str, request: Request):
    """Move a template between drafts and built-in. Body: {draft: bool}.

    Built-in = offered to every admin in the create picker. Draft = the
    developer's workshop: editable, workbench-able, invisible to everyone
    else. Publishing work lands as a draft first, so nothing half-built is
    ever offered as a product template by accident.
    """
    _require_developer(request, "moving a built-in template")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        draft = bool((body or {}).get("draft", True))
        fmt = provision_service.set_format_draft(
            key, draft, request.app.state.config_dir)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Keep the NVMe mirror in step: IRIV-VS/template holds exactly what users
    # can build from. Published -> full document lands there; drafted -> gone.
    if fmt:
        if draft:
            templates_service.unmirror_template(
                templates_service.public_templates_root(), key)
        else:
            templates_service.mirror_template(
                dict(fmt, key=key), templates_service.public_templates_root(),
                key)
    return {"ok": True, "key": key, "format": fmt}


@router.post("/api/hub/formats/{key}/workbench", name="hub.format_workbench")
async def open_format_workbench(key: str, request: Request):
    """Open (or create) the WORKBENCH system for a format -- editing a format
    means building with it, not filling a form. The workbench is a normal
    system created from the format; the developer customises it like any
    other, then publishes it back. One workbench per format per box, tracked
    in workbench.local.toml so a rebuilt box just makes a fresh one.
    """
    _require_developer(request, "editing a built-in format in a system")
    st = request.app.state
    cfg = st.config_dir
    fmt = provision_service.builtin_formats(cfg).get(key)
    if fmt is None:
        raise HTTPException(status_code=404, detail="no such format")
    repo_dir = str(pathlib.Path(st.systems_dir).resolve().parent)
    user = getattr(request.state, "user", None) or ""

    slug = provision_service.format_workbench(cfg).get(key)
    if slug and any(x.slug == slug for x in st.registry):
        url = (st.masters.get(slug) or {}).get("url") or ""
        # Best effort: a workbench that sat stopped since last time should
        # come up on click, same as pressing Start on its card.
        try:
            port = int(urllib.parse.urlsplit(url).port or 0)
            if port:
                provision_service.spawn_master(repo_dir, slug, port)
        except (ValueError, OSError):
            pass
        # Masters register on loopback; the browser opening this is not on
        # the board, so swap in the LAN address like the system cards do.
        reachable = "http://" + hub_service._host_of(url) if url else ""
        return {"ok": True, "created": False, "slug": slug, "url": reachable}

    try:
        created = provision_service.create_vision_system(
            st.systems_dir, f"{fmt['label']} (template editor)", cfg,
            template=key, owner=user,
            unlimited=True)      # developer-gated route; workbenches must
                                 # never be refused for the box being full
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    auth_service.grant_system(user, created["slug"], cfg)
    warnings = apply_format_workflow(st, created["slug"], key)
    _rebuild_registry(st)
    provision_service.spawn_master(repo_dir, created["slug"], created["port"])
    provision_service.set_format_workbench(key, created["slug"], cfg)
    created = dict(created)
    if created.get("url"):
        created["url"] = "http://" + hub_service._host_of(created["url"])
    return {"ok": True, "created": True, "warnings": warnings, **created}


@router.post("/api/hub/formats/{key}/publish_back", name="hub.format_publish_back")
async def format_publish_back(key: str, request: Request):
    """Publish a format's workbench system back INTO the format.

    The workbench's dashboard, workflow and class filter become the format's
    new starting point; the format's own label, folder, description and zone
    guidance are kept -- republishing an edit must not wipe the words a
    developer wrote for operators.
    """
    _require_developer(request, "publishing a built-in format")
    st = request.app.state
    cfg = st.config_dir
    fmt = provision_service.builtin_formats(cfg).get(key)
    if fmt is None:
        raise HTTPException(status_code=404, detail="no such format")
    slug = provision_service.format_workbench(cfg).get(key)
    if not slug or not any(x.slug == slug for x in st.registry):
        raise HTTPException(status_code=400,
                            detail="this format has no workbench system - "
                                   "open it in the editor first")
    try:
        doc = templates_service.snapshot_system(
            st.systems_dir, cfg, slug,
            name=fmt["label"], description=fmt.get("hint") or "",
            category=templates_service.UNCATEGORIZED)
        saved = provision_service.promote_template(
            key, doc, cfg, category=fmt.get("category"),
            label=fmt["label"], hint=fmt.get("hint"), zone=fmt.get("zone"),
            model=_running_model(st.systems_dir, slug))
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # A PUBLISHED template's NVMe mirror must track its content.
    if saved and not saved.get("draft"):
        templates_service.mirror_template(
            dict(saved, key=key), templates_service.public_templates_root(),
            key)
    return {"ok": True, "key": key, "format": saved}


@router.post("/api/hub/formats/publish", name="hub.publish_format")
async def publish_format(request: Request):
    """Publish a working SYSTEM as a built-in format.

    This is how a built-in is really authored: build the system normally --
    cameras, zones, workflow, dashboard -- then publish it. A format's job is
    to be a good STARTING POINT, and the only way to know it is a good one is
    to have built with it.

    Nothing is copied that belongs to this box: the snapshot is the same one
    "save as template" takes (layout, workflow, class filter), which already
    excludes credentials, camera sources and recorded data.

    Body: {from_slug, key, label?, hint?, category?}.
    """
    _require_developer(request, "publishing a built-in format")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body or {}
    st = request.app.state
    slug = str(body.get("from_slug") or "").strip()
    system = next((x for x in st.registry if x.slug == slug), None)
    if system is None:
        raise HTTPException(status_code=404, detail="no such system")

    key = str(body.get("key") or slug).strip().lower().replace("-", "_")
    try:
        doc = templates_service.snapshot_system(
            st.systems_dir, st.config_dir, slug,
            name=body.get("label") or system.display_name,
            description=body.get("hint") or "",
            category=templates_service.UNCATEGORIZED)
        fmt = provision_service.promote_template(
            key, doc, st.config_dir, category=body.get("category"))
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "key": key, "format": fmt}


@router.post("/api/hub/formats/promote", name="hub.promote_format")
async def promote_format(request: Request):
    """Turn a SAVED template into a built-in format.

    Body: {category, slug, key?, format_category?} -- `category`/`slug` name
    the saved template, `key` is the format id to write (defaults to the
    template's slug). The template's dashboard becomes the format's starter
    dashboard and its class filter becomes the seed classes.
    """
    _require_developer(request, "promoting a template to a built-in")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body or {}
    try:
        doc = templates_service.load_template(body.get("category") or "",
                                              body.get("slug") or "")
        fmt = provision_service.promote_template(
            body.get("key") or body.get("slug") or "",
            doc, request.app.state.config_dir,
            category=body.get("format_category"))
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "format": fmt}


# ------------------------------------------------ publishing to the source ---
#
# The last step of authoring a built-in template: every system filed under
# "Built-in templates" is re-snapshotted into config/formats.toml and the
# file is committed to the source repository, on the branch customer releases
# are built from. See core/services/source_publish.py for why this is not the
# template store.

@router.get("/api/hub/formats/source", name="formats.source_status")
def source_status(request: Request):
    _require_developer(request, "reading the template publish settings")
    from core.services import source_publish                # noqa: PLC0415
    return {"ok": True, **source_publish.status(request.app.state.config_dir)}


@router.post("/api/hub/formats/source/token", name="formats.source_token")
async def source_token(request: Request):
    """Body: {token}. Empty clears it. Never echoed back -- status reports
    only whether one is set."""
    _require_developer(request, "setting the template publish token")
    from core.services import source_publish                # noqa: PLC0415
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    source_publish.set_token((body or {}).get("token") or "",
                             request.app.state.config_dir)
    return {"ok": True, **source_publish.status(request.app.state.config_dir)}


@router.post("/api/hub/formats/source/publish", name="formats.source_publish")
def source_publish_now(request: Request):
    """Refresh every built-in template from its system and commit
    config/formats.toml to the source repo. Synchronous: one small file, one
    API round trip, and the operator is looking at the button."""
    _require_developer(request, "publishing the templates")
    from core.services import source_publish                # noqa: PLC0415
    st = request.app.state
    try:
        result = source_publish.publish(
            st.systems_dir, st.config_dir,
            running_model=lambda slug: _running_model(st.systems_dir, slug))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
