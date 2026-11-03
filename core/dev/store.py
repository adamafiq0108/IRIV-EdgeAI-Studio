# -*- coding: utf-8 -*-
"""
store.py -- publishing to the store, and issuing the keys that open it.

Owner's hub only. Moved out of core/routes/store.py so a customer install can
simply not have the file: browsing and installing are what a deployment does,
publishing and issuing are what the product owner does, and the two were only
ever in one module because they share a URL prefix.

Issuing needs config/store_signing.pem on top of everything else. That file
exists only on the owner's hub, so these routes are inert anywhere it isn't --
which is the guarantee that matters, since a config flag can be edited and a
private key cannot be guessed.
"""

from fastapi import APIRouter, HTTPException, Request

from core.routes.hub import _require_developer
from core.services import store as store_service

router = APIRouter()


# ------------------------------------------------------------- publishing ---

@router.get("/api/hub/store/publish_token", name="store.publish_token")
def publish_token_status(request: Request):
    _require_developer(request, "reading the publish settings")
    return {"ok": True,
            **store_service.publish_ready(request.app.state.config_dir)}


@router.post("/api/hub/store/publish_token", name="store.set_publish_token")
async def set_publish_token(request: Request):
    _require_developer(request, "setting the publish token")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    store_service.set_publish_token((body or {}).get("token") or "",
                                    request.app.state.config_dir)
    return {"ok": True,
            **store_service.publish_ready(request.app.state.config_dir)}


@router.post("/api/hub/store/publish", name="store.publish")
async def cloud_publish(request: Request):
    """Publish a built-in template's workbench to the online store.
    Body: {key, tier: free|pro}. Background job; poll the status route."""
    _require_developer(request, "publishing to the online store")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body or {}
    st = request.app.state
    try:
        status = store_service.start_cloud_publish(
            st.systems_dir, st.config_dir,
            key=body.get("key"), tier=body.get("tier") or "free")
    except store_service.StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "status": status}


@router.get("/api/hub/store/publish/{key}", name="store.publish_status")
def cloud_publish_status(key: str, request: Request):
    _require_developer(request, "reading the publish state")
    return {"ok": True, "status": store_service.publish_status(key)}


# ---------------------------------------------------------------- issuing ---

@router.get("/api/hub/store/licenses", name="store.licenses")
def licenses(request: Request):
    _require_developer(request, "reading the license ledger")
    return {"ok": True,
            "can_issue": store_service.can_issue(request.app.state.config_dir),
            "issued": store_service.issued_licenses(
                request.app.state.config_dir)}


@router.post("/api/hub/store/licenses/issue", name="store.license_issue")
async def license_issue(request: Request):
    _require_developer(request, "issuing a license key")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body or {}
    try:
        result = store_service.issue_license(
            request.app.state.config_dir,
            customer=body.get("customer"), serial=body.get("serial"),
            tier=body.get("tier"), days=body.get("days"),
            github_token=body.get("github_token"),
            # The board's secure element, when the customer's hub reports one.
            # Optional: a board without the chip still gets a key, bound to
            # its CPU serial alone, and the panel says which kind it issued.
            atecc_serial=body.get("atecc_serial"),
            device_pubkey=body.get("device_pubkey"))
    except store_service.StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/hub/store/licenses/revoke", name="store.license_revoke")
async def license_revoke(request: Request):
    _require_developer(request, "revoking a license key")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body or {}
    try:
        issued = store_service.set_revoked(
            request.app.state.config_dir, body.get("serial"),
            body.get("issued_at"), bool(body.get("revoked", True)))
    except store_service.StoreError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "issued": issued}
