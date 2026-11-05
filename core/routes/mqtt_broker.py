# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mqtt_broker.py -- this box's MQTT broker: status for the Workflow page's
pill, settings and install for Settings > MQTT Broker.

Mounted on the HUB (where Settings lives) and on every MASTER (where the
Workflow page's pill asks, and where an admin opens the same panel in a
dialog without leaving the canvas). One broker per box, so one file, one
config, whichever process answers -- the same arrangement as the Telegram
bots.

STATUS is readable by any signed-in account: "is the broker up, on which
port, is it local-only or secured" is what an operator wiring an MQTT node
needs to know, and it names no credential. It says whether the reader may
MANAGE it, so the pill only offers a button that works.

EVERYTHING ELSE is site admin and above, the same tier as Network: opening a
listener to the plant LAN is a decision about what the box exposes on the
network, and it is audited like starting a remote-access tunnel. The password
travels browser -> here -> the root helper's stdin, and never back: reads say
whether one is set.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import mqtt_broker as broker

router = APIRouter()


def _user(request):
    return getattr(request.state, "user", None)


def _can_manage(request):
    return bool(auth_service.is_site_admin(_user(request),
                                           request.app.state.config_dir))


def _require_signed_in(request):
    if not _user(request):
        raise HTTPException(status_code=401, detail="sign in first")


def _require_admin(request):
    if not _can_manage(request):
        raise HTTPException(status_code=403, detail="admin only")


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


def _audit(request, action, ok, detail):
    """Best-effort, like remote.py: a database outage must not block a save.
    `detail` carries ports and whether a login is set -- never the login."""
    try:
        audit_service.record(action=action, result="ok" if ok else "fail",
                             username=_user(request),
                             source_ip=net.client_ip(request),
                             detail=detail,
                             config_dir=request.app.state.config_dir)
    except Exception:                                        # noqa: BLE001
        pass


@router.get("/api/mqtt/broker/status", name="mqtt_broker.status")
async def broker_status(request: Request):
    _require_signed_in(request)
    s = await asyncio.to_thread(broker.status)
    return {"ok": True, "status": s, "can_manage": _can_manage(request)}


@router.get("/api/mqtt/broker", name="mqtt_broker.get")
async def broker_get(request: Request):
    _require_admin(request)
    data = await asyncio.to_thread(broker.public, request.app.state.config_dir)
    return {"ok": True, **data}


@router.post("/api/mqtt/broker", name="mqtt_broker.save")
async def broker_save(request: Request):
    _require_admin(request)
    body = await _body(request)
    cfg = request.app.state.config_dir
    try:
        ok, message = await asyncio.to_thread(broker.apply, body, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _audit(request, "mqtt.broker_settings", ok,
           {"port": body.get("port"), "ws_port": body.get("ws_port") or None,
            "secured": bool(body.get("secure"))})
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    data = await asyncio.to_thread(broker.public, cfg, True)
    return {"ok": True, "message": message, **data}


@router.post("/api/mqtt/broker/install", name="mqtt_broker.install")
async def broker_install(request: Request):
    _require_admin(request)
    cfg = request.app.state.config_dir
    ok, message = await asyncio.to_thread(broker.install, cfg)
    _audit(request, "mqtt.broker_install", ok, {})
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    data = await asyncio.to_thread(broker.public, cfg, True)
    return {"ok": True, "message": message, **data}


@router.post("/api/mqtt/broker/auto", name="mqtt_broker.auto")
async def broker_auto(request: Request):
    """The panel's "install the broker automatically" switch. Off is what the
    installer's --no-mqtt writes; the hub honours either at its next start."""
    _require_admin(request)
    cfg = request.app.state.config_dir
    enabled = bool((await _body(request)).get("enabled"))
    await asyncio.to_thread(broker.set_auto_install, enabled, cfg)
    _audit(request, "mqtt.broker_settings", True, {"auto_install": enabled})
    data = await asyncio.to_thread(broker.public, cfg)
    return {"ok": True, **data}
