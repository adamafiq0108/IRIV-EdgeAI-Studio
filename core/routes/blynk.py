# -*- coding: utf-8 -*-
"""
blynk.py -- the Blynk IoT page's API.

Thin by contract: config storage, validation and the HTTPS pusher live in
core/services/blynk.py. Writes are admin-only -- the config carries a cloud
credential and controls what leaves the box.

The stored token never travels back to a browser: GET returns a masked config
(token_set + last-4 hint), and PUT with an empty token keeps the stored one.
"""

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.routes import perm
from core.services import blynk as blynk_service
from core.services import main as main_service

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _require_master(request):
    """Blynk is master-only: the page is out of every other
    tier's rail (nav.MASTER_SECTIONS), and the API behind it says the same
    thing, or the rail would be hiding a door that still opens."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                     request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="master only")


def _stat_keys(request):
    st = request.app.state
    try:
        stats = main_service.aggregate_stats(st.slaves, getattr(st, "local_stats", None))
    except Exception:                                       # noqa: BLE001
        return []
    return sorted(k for k, v in stats.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool))


@router.get("/api/blynk", name="blynk.get")
def get_blynk(request: Request):
    _require_master(request)
    return {"ok": True,
            "config": blynk_service.public_config(),
            "status": blynk_service.status(),
            "stat_keys": _stat_keys(request),
            "editable": perm.editable(request, "blynk.")}


@router.put("/api/blynk", name="blynk.save")
async def save_blynk(request: Request):
    _require_master(request)
    perm.require(request, "blynk.edit")
    body = await request.json()
    try:
        cfg = blynk_service.save(body)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "config": blynk_service.public_config(cfg)}


@router.post("/api/blynk/test", name="blynk.test")
async def test_blynk(request: Request):
    """Check a token (the pasted one, or the stored one when blank) against
    the Blynk cloud without saving anything."""
    _require_master(request)
    perm.require(request, "blynk.edit")
    body = await request.json()
    return {"ok": True,
            "result": blynk_service.test(body.get("token"), body.get("server"))}
