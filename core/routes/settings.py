# -*- coding: utf-8 -*-
"""
settings.py -- The Settings page's API: scan, pair, forget.

The page itself renders from core/routes/system.py with every other page; this
router is only the behaviour behind its buttons. Blocking network work (the
sweep, the pair round trip, one immediate poll) runs off the event loop via
asyncio.to_thread -- same reason master_server's sync loop does.

Pairing mutates app.state directly so the new device is live immediately: the
sync loop would pick the config up anyway, but only at the next interval, and
ten minutes of "is it broken?" after a successful pair is a bug in itself.
"""

import asyncio
import socket

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import settings as settings_service
from core.services import sync as sync_service

router = APIRouter()


def _require_admin(request):
    """Fleet membership is an administrative decision.

    These routes were session-only, which meant any account with access to
    the system could pair a device (the URL is fetched server-side, so it is
    also an SSRF primitive), or un-pair one and revoke its token. Neither is
    an operator action.
    """
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _raise(err):
    raise HTTPException(status_code=err.status, detail=str(err))


def _ip(request):
    return net.client_ip(request)      # never the header verbatim (core/net.py)


@router.post("/api/settings/scan", name="settings.scan")
async def scan(request: Request):
    _require_admin(request)     # a LAN sweep from the customer's board
    state = request.app.state
    configured = [s["host_serial"] for s in state.slave_configs]
    devices = await asyncio.to_thread(
        settings_service.scan, state.system.slug, configured
    )
    return {"ok": True, "devices": devices}


@router.post("/api/settings/slaves", name="settings.add")
async def add_slave(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict) or not body.get("url"):
        raise HTTPException(status_code=400, detail="url required")

    state = request.app.state
    master_serial = f"{state.system.slug}@{socket.gethostname()}"
    try:
        entry = await asyncio.to_thread(
            settings_service.pair,
            body["url"], body.get("label", ""), state.system.slug, master_serial,
            body.get("username"), body.get("password"),
        )
        settings_service.save_slave(entry, state.config_dir)
    except settings_service.SettingsError as e:
        _raise(e)

    # Live immediately: replace any stale config for the same device, then
    # poll it once so the page shows real state, not "waiting for sync".
    state.slave_configs = [
        c for c in state.slave_configs if c["host_serial"] != entry["host_serial"]
    ] + [entry]
    state.slave_tokens[entry["host_serial"]] = {
        "url": entry["url"], "token": entry["token"],
    }
    updated, events, cursor = await asyncio.to_thread(
        sync_service.poll_one, entry, None
    )
    public = {k: v for k, v in updated.items() if k != "token"}
    state.slaves = [
        s for s in state.slaves if s.get("host_serial") != entry["host_serial"]
    ] + [public]
    state.events.extend(events)
    state.cursors[entry["host_serial"]] = cursor

    audit_service.record("device.pair", username=getattr(request.state, "user", None),
                         host_serial=entry["host_serial"], source_ip=_ip(request),
                         detail={"label": entry["label"]}, config_dir=state.config_dir)

    return {"ok": True, "device": {
        "host_serial": entry["host_serial"],
        "label": entry["label"],
        "url": entry["url"],
        "online": public.get("online", False),
    }}


@router.delete("/api/settings/slaves/{host_serial}", name="settings.remove")
async def remove_slave(host_serial: str, request: Request,
                       revoke: bool = True):
    """Forget a device. With revoke=true (the default), also tell the agent
    to unpair -- its token dies immediately and the device returns to
    'waiting for master pairing'. Unreachable or pre-v1.1 devices are still
    forgotten locally; the response says whether the device confirmed."""
    _require_admin(request)
    state = request.app.state
    revoked = False
    if revoke:
        creds = state.slave_tokens.get(host_serial) or {}
        revoked = await asyncio.to_thread(
            settings_service.revoke_on_device,
            creds.get("url"), creds.get("token"),
        )
    try:
        await asyncio.to_thread(
            settings_service.remove_slave, host_serial, state.config_dir
        )
    except settings_service.SettingsError as e:
        _raise(e)

    state.slave_configs = [c for c in state.slave_configs
                           if c["host_serial"] != host_serial]
    state.slaves = [s for s in state.slaves
                    if s.get("host_serial") != host_serial]
    state.slave_tokens.pop(host_serial, None)
    state.cursors.pop(host_serial, None)
    audit_service.record("device.forget", username=getattr(request.state, "user", None),
                         host_serial=host_serial, source_ip=_ip(request),
                         detail={"revoked_on_device": revoked}, config_dir=state.config_dir)
    return {"ok": True, "revoked_on_device": revoked}
