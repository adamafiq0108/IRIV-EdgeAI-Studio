# -*- coding: utf-8 -*-
"""
store.py -- Routes for the online template store.

Route parses and returns; the store service owns fetching, verification and
the install pipeline (docs/conventions.md sec 2).

GATE: developer only, every route -- browsing included. The store is the
PRODUCT's own distribution channel, not a deployment's tool: what a customer
box runs is what was installed on it, and a catalogue of other people's
templates in an operator's (or even an admin's) hub is an invitation to
install something nobody chose for that deployment. Browsing was open to any
signed-in user before, which meant the page could be hidden and the endpoint
still answered -- and hiding a page hides the button, not the URL.

This is the CONSUMER half: what a deployment does with the store. Publishing to
it and issuing the keys that open it are the owner's side and live in
core/dev/store.py, which a customer install does not have (core/deploy.py).
"""

from fastapi import APIRouter, HTTPException, Request

from core.routes.hub import _require_developer
from core.services import auth as auth_service
from core.services import store as store_service

router = APIRouter()


@router.get("/api/hub/store/catalog", name="store.catalog")
def catalog(request: Request, force: bool = False):
    _require_developer(request, "browsing the template store")
    try:
        return {"ok": True,
                **store_service.catalog(request.app.state.config_dir,
                                        force=force)}
    except store_service.StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/hub/store/install", name="store.install")
async def install(request: Request):
    _require_developer(request, "installing a store template")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body or {}
    st = request.app.state
    try:
        user = getattr(request.state, "user", "") or ""
        status = store_service.start_install(
            st.systems_dir, st.config_dir,
            key=body.get("key"), name=body.get("name") or "",
            owner=user,
            # The developer's box is a workshop, not a deployment -- the
            # 5-system cap does not apply to them (same rule as creating).
            unlimited=auth_service.is_developer(user, st.config_dir))
    except store_service.StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "status": status}


@router.get("/api/hub/store/install/{key}", name="store.install_status")
def install_status(key: str, request: Request):
    _require_developer(request, "reading a store install's progress")
    status = store_service.install_status(key)
    # A finished install changed the registry on disk; the poller's final
    # request is the natural place to fold it into app state.
    if status.get("state") == "done":
        from core.routes.hub import _rebuild_registry     # noqa: PLC0415
        try:
            _rebuild_registry(request.app.state)
        except Exception:                                 # noqa: BLE001
            pass
    return {"ok": True, "status": status}


# --------------------------------------------------------------- license ---

@router.get("/api/hub/store/license", name="store.license")
def license_status(request: Request):
    _require_developer(request, "reading the license state")
    return {"ok": True,
            "license": store_service.license_status(
                request.app.state.config_dir)}


@router.post("/api/hub/store/license", name="store.license_activate")
async def license_activate(request: Request):
    _require_developer(request, "activating a license key")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        status = store_service.activate_license(
            (body or {}).get("key") or "", request.app.state.config_dir)
    except store_service.StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "license": status}


@router.delete("/api/hub/store/license", name="store.license_clear")
def license_clear(request: Request):
    _require_developer(request, "removing the license key")
    store_service.clear_license()
    return {"ok": True}
