# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
sysport.py -- a system's port, from the hub's create wizard and Edit system.

Tok Aba (2026-10-06). The two reads the port field asks while it is typed
live here, and so does the port half of Edit system's save
(PATCH /api/hub/systems/{slug} stays in routes/hub.py with the rename and
calls move_for_edit). Its own module so hub.py stays at its line ceiling.

Gate: master or developer (hub._require_admin), the same as the rename and
the create. Which port a system answers on is what the deployment IS, not
an operation on it, so it is not opened to site admins or operators.
"""

import asyncio
import pathlib

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import sysport as sysport_service

router = APIRouter()


def _hub():
    from core.routes import hub as hub_routes             # noqa: PLC0415 (cycle)
    return hub_routes


@router.get("/api/hub/ports/check", name="hub.port_check")
def port_check(request: Request, port: str = "", slug: str = ""):
    """{suggested, port, usable, detail}: what Automatic (next free) hands
    out now and, for a typed port, whether this box can give it -- the same
    rules the save enforces. `slug` is the system being edited (its own port
    reads as usable)."""
    _hub()._require_admin(request, "choosing a system's port")
    st = request.app.state
    out = {"ok": True, "suggested": sysport_service.suggest(st.systems_dir,
                                                            st.config_dir)}
    if str(port).strip():
        try:
            out["port"] = sysport_service.check_master_port(
                port, st.systems_dir, st.config_dir, slug=slug or None)
            out["usable"], out["detail"] = True, f"{out['port']} is free"
        except ValueError as exc:
            out["usable"], out["detail"] = False, str(exc)
    return out


@router.get("/api/hub/systems/{slug}/port", name="hub.system_port")
def system_port(slug: str, request: Request):
    """Edit system's port facts: {port, stream_port, suggested, editable,
    reason}; editable false, with the reason in words, for a master systemd
    runs (irivai-master@<slug>) or one on another host."""
    _hub()._require_admin(request, "changing a system's port")
    st = request.app.state
    if slug not in [s.slug for s in st.registry]:
        raise HTTPException(status_code=404, detail="no such system")
    return {"ok": True, **sysport_service.info(st.systems_dir, slug, st.config_dir)}


async def move_for_edit(request, slug, body):
    """The port half of Edit system's save, run BEFORE the rename so a
    refused or undone move saves nothing. None when no port was sent;
    HTTPException 400 with the refusal in words."""
    raw = body.get("port")
    if raw is None or not str(raw).strip():
        return None
    st = request.app.state
    repo_dir = str(pathlib.Path(st.systems_dir).resolve().parent)
    hub = _hub()
    try:
        moved = await asyncio.to_thread(sysport_service.move, st.systems_dir, slug,
                                        raw, repo_dir, st.config_dir)
    except (OSError, ValueError) as exc:
        hub._rebuild_registry(st)          # an undone move rewrote the config
        raise HTTPException(status_code=400, detail=str(exc))
    if moved["moved"]:
        hub._rebuild_registry(st)
    return moved


def moved_fields(request, slug, moved):
    """What the save's answer adds when a port was sent: where the system is
    now (browser-facing, like the card) and whether it was restarted."""
    if not moved:
        return {}
    url = (request.app.state.masters.get(slug) or {}).get("url") or ""
    return {"port": moved["port"], "old_port": moved["old_port"],
            "moved": moved["moved"], "restarted": moved["restarted"],
            "url": net.rewrite(url, request) if url else ""}
