# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
remote.py -- domain + tunnel API.

Reads are admin-only because the public URL and the tunnel log describe how to
reach this master from outside. Writes and start/stop are admin-only and
audited: opening a tunnel publishes the fleet to the internet, and that must be
attributable to a person.
"""

import logging
import os

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import remote as remote_service
from core.services import auth as auth_service

router = APIRouter()
logger = logging.getLogger(__name__)


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _port(request):
    return request.url.port or (443 if request.url.scheme == "https" else 80)


def _own_port():
    """The port THIS master listens on: what a tunnel must target. Never
    the port the browser used -- through the reverse proxy that is 80, which
    the box redirects to the HUB, so the tunnel would publish the hub
    instead of this system (Pipi M1, 2026-10-07)."""
    try:
        return int(os.environ.get("CYTRON_MASTER_PORT") or 0) or None
    except ValueError:
        return None


def _audit(request, action, result, detail=None):
    """Best-effort: a DB outage must not block turning a tunnel off."""
    try:
        audit_service.record(action=action, result=result,
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request),
                             detail=detail or {},
                             config_dir=request.app.state.config_dir)
    except Exception:                                     # noqa: BLE001
        logger.warning("[REMOTE] audit write skipped for %s", action)


@router.get("/api/remote", name="remote.get")
def get_remote(request: Request):
    _require_admin(request)
    settings = remote_service.load(request.app.state.config_dir)
    return {"ok": True,
            "settings": settings,
            "domain": remote_service.check_domain(settings["public_url"], _port(request)),
            "tunnel": remote_service.status(),
            "providers": list(remote_service.PROVIDERS),
            "local_url": str(request.base_url).rstrip("/")}


@router.put("/api/remote", name="remote.save")
async def save_remote(request: Request):
    _require_admin(request)
    body = await request.json()
    try:
        settings = remote_service.save(request.app.state.config_dir, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(request, "remote.settings", "ok", {"public_url": settings["public_url"]})
    return {"ok": True, "settings": settings,
            "domain": remote_service.check_domain(settings["public_url"], _port(request))}


@router.post("/api/remote/check", name="remote.check")
async def check(request: Request):
    """Does the configured name resolve to this host? Answering before the
    customer does is the whole value."""
    _require_admin(request)
    body = await request.json()
    try:
        url = remote_service.normalize_url(body.get("public_url") or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "domain": remote_service.check_domain(url, _port(request))}


@router.post("/api/remote/tunnel/start", name="remote.tunnel_start")
async def tunnel_start(request: Request):
    _require_admin(request)
    body = await request.json()
    port = _own_port()
    if port is None:
        raise HTTPException(status_code=400,
                            detail="a tunnel is started from a system's own master")
    ok, message = remote_service.start(request.app.state.config_dir, port,
                                       body.get("provider"))
    _audit(request, "remote.tunnel_start", "ok" if ok else "fail",
           {"provider": body.get("provider"), "port": port, "message": message})
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "tunnel": remote_service.status()}


@router.post("/api/remote/tunnel/stop", name="remote.tunnel_stop")
def tunnel_stop(request: Request):
    _require_admin(request)
    ok, message = remote_service.stop()
    _audit(request, "remote.tunnel_stop", "ok" if ok else "fail", {"message": message})
    return {"ok": ok, "message": message, "tunnel": remote_service.status()}
