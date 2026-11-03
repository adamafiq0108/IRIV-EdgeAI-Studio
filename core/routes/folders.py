# -*- coding: utf-8 -*-
"""
folders.py -- folder CRUD + device-to-folder assignment. Admin-only.

Thin (conventions sec 2): parse -> inventory service -> repo. Every mutation
is audited. Writes surface DbUnavailable (PG-down) as 503 and validation as
400. The cached folder snapshot on app.state is refreshed after each change
so the sidebar reflects it without waiting for the next sync cycle.
"""

from fastapi import APIRouter, HTTPException, Request

from core.db import DbUnavailable
from core import net
from core.services import log_catalog as catalog
from core.services import audit as audit_service
from core.services import inventory as inventory_service
from core.services import auth as auth_service

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _ip(request):
    return net.client_ip(request)      # never the header verbatim (core/net.py)


async def _body(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def _system_slug(request):
    return getattr(getattr(request.app.state, "system", None), "slug", None)


def _refresh_folders(request):
    request.app.state.folders = inventory_service.folder_snapshot(_system_slug(request))


def _do(request, fn, action, detail=None, host_serial=None):
    cfg = request.app.state.config_dir
    try:
        result = fn()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DbUnavailable as e:
        # The `{e}` here was a psycopg2 connection error, which quotes the
        # DSN it failed on -- user, host, database and, on a box configured
        # with one, the password. A Postgres outage put that string in a
        # browser and on the Logs page.
        raise catalog.http_error("DB_UNAVAILABLE", cause=e,
                                 folder_action=action) from e
    _refresh_folders(request)
    audit_service.record(action, username=getattr(request.state, "user", None),
                         host_serial=host_serial, source_ip=_ip(request),
                         detail=detail, config_dir=cfg)
    return {"ok": True, **({"result": result} if result else {})}


@router.post("/api/folders", name="folders.create")
async def create(request: Request):
    _require_admin(request)
    b = await _body(request)
    cfg = request.app.state.config_dir
    return _do(request,
               lambda: inventory_service.create_folder(b.get("name"),
                                                       b.get("parent_id"),
                                                       _system_slug(request), cfg),
               "folder.create", detail={"name": b.get("name")})


@router.patch("/api/folders/{folder_id}", name="folders.update")
async def update(folder_id: int, request: Request):
    _require_admin(request)
    b = await _body(request)
    cfg = request.app.state.config_dir
    if "name" in b:
        return _do(request,
                   lambda: inventory_service.rename_folder(folder_id, b["name"], cfg),
                   "folder.rename", detail={"id": folder_id, "name": b.get("name")})
    if "parent_id" in b:
        return _do(request,
                   lambda: inventory_service.move_folder(folder_id, b["parent_id"], cfg),
                   "folder.move", detail={"id": folder_id, "parent_id": b.get("parent_id")})
    raise HTTPException(status_code=400, detail="name or parent_id required")


@router.delete("/api/folders/{folder_id}", name="folders.delete")
async def delete(folder_id: int, request: Request):
    _require_admin(request)
    cfg = request.app.state.config_dir
    return _do(request,
               lambda: inventory_service.delete_folder(folder_id, cfg),
               "folder.delete", detail={"id": folder_id})


@router.post("/api/devices/{host_serial}/folder", name="folders.assign")
async def assign(host_serial: str, request: Request):
    _require_admin(request)
    b = await _body(request)
    cfg = request.app.state.config_dir
    return _do(request,
               lambda: inventory_service.assign_device(host_serial, b.get("folder_id"), cfg),
               "device.move", host_serial=host_serial,
               detail={"folder_id": b.get("folder_id")})
