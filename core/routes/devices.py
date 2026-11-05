# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
devices.py -- master-owned device metadata (display name/description/notes/
folder) + the device list used by global search. Admin-only for writes.

Renaming a device here is presentation only: it never touches device ID,
token, url, auth, licensing or hardware identity (those live in
slaves.local.toml / on the device). Thin route -> inventory service -> repo.
"""

from fastapi import APIRouter, HTTPException, Request

from core.db import DbUnavailable
from core import net
from core.services import log_catalog as catalog
from core.services import auth as auth_service
from core.services import audit as audit_service
from core.services import inventory as inventory_service

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _ip(request):
    return net.client_ip(request)      # never the header verbatim (core/net.py)


@router.patch("/api/devices/{host_serial}", name="devices.update")
async def update(host_serial: str, request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    cfg = request.app.state.config_dir
    try:
        inventory_service.update_device(host_serial, body, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DbUnavailable as e:
        # The `{e}` here was a psycopg2 connection error, which quotes the
        # DSN it failed on -- user, host, database and, on a box configured
        # with one, the password. A Postgres outage put that string in a
        # browser and on the Logs page.
        raise catalog.http_error("DB_UNAVAILABLE", cause=e,
                                 host_serial=host_serial) from e
    audit_service.record("device.rename",
                         username=getattr(request.state, "user", None),
                         host_serial=host_serial, source_ip=_ip(request),
                         detail={k: body[k] for k in
                                 ("display_name", "description", "notes", "folder_id")
                                 if k in body},
                         config_dir=cfg)
    return {"ok": True}


@router.get("/api/devices", name="devices.list")
def device_list(request: Request):
    """Live device feed for global search/filter AND the dashboard cards'
    auto-refresh -- one shaping path (main.build_devices) so the JSON matches
    the server-rendered cards exactly. Token-free (app.state.slaves strips it).
    """
    from core import config
    from core.services import health as health_service
    from core.services import main as main_service
    thr = health_service.thresholds(config.monitoring(request.app.state.config_dir))
    folders = {f["id"]: f["name"] for f in (request.app.state.folders or [])}
    devices = main_service.build_devices(request.app.state.slaves, thr)
    for d in devices:
        d["display_name"] = d["label"]
        d["folder"] = folders.get(d.get("folder_id"), "Unassigned")
    return {"ok": True, "devices": devices}
