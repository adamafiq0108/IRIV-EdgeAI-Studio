# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
panel.py -- the front panel from the browser: read it, edit it, see it.

The panel runs perfectly well with nobody looking (that is the point of a
panel), so none of this is required for it to work. What it buys is the thing
a headless daemon cannot give you: seeing the screen while editing what it
says, from wherever you are, instead of walking to the box, changing a
string, restarting, and walking back.

`frame` and `preview` return the framebuffer as scanlines -- the panel's own
bytes, not a re-drawing of them in HTML -- so what the browser paints is what
the glass shows, wrong pixels included.

ADMIN ONLY. The read names the box's serial and every address it holds, and
the writes turn a screen off, silence a buzzer and can shut the machine down.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.services import log_catalog as catalog
from core.services import panel as panel_service

router = APIRouter()

#: Powering the box down is not a front-panel feature -- the panel just happens
#: to be where the button is. It rides on its own router so a master can mount
#: it too: the sign-out menu offers Reboot and Shut down on every port, and a
#: system page asking the hub instead would be a cross-origin call carrying a
#: session. Every process here runs as the same service user, and the sudoers
#: rule that permits `shutdown now` is the box's, not the hub's.
power_router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _require_site_admin(request):
    """Master, developer, OR a site admin -- for POWER only.

    The rest of this file stays the box owner's: reading the panel names the
    box's serial and every address it holds, and writing to it changes what
    the glass says to whoever walks up. Turning the machine off is different.
    Whoever runs the operation is the person standing in front of it when a
    camera wedges, and "ask the owner to reboot" is an outage with a phone
    call in the middle of it. It is also the one action here that anybody in
    the room can already take by holding the button in.
    """
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _config_dir(request):
    return request.app.state.config_dir


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


# panel_service.status runs systemctl (and, the first time, hailortcli), and
# start/stop/preview drive the I2C screen: none of it on the hub's event loop
# (perf item L5) -- every handler below hands that work to a worker thread.

@router.get("/api/hub/panel", name="panel.status")
async def panel_status(request: Request):
    _require_admin(request)
    return {"ok": True,
            **await asyncio.to_thread(panel_service.status, _config_dir(request))}


@router.post("/api/hub/panel/settings", name="panel.save")
async def panel_save(request: Request):
    _require_admin(request)
    body = await _body(request)
    config_dir = _config_dir(request)

    def apply():
        was_on = panel_service.settings(config_dir)["enabled"]
        saved = panel_service.save_settings(body, config_dir)
        # Switching it ON has to actually start the thread, and OFF has to
        # let go of the screen -- a setting that only takes effect after a
        # restart is a setting that gets reported as broken.
        if saved["enabled"] and not was_on:
            panel_service.start(config_dir)
        elif not saved["enabled"] and was_on:
            panel_service.stop()
        return panel_service.status(config_dir)
    return {"ok": True, **await asyncio.to_thread(apply)}


@router.post("/api/hub/panel/preview", name="panel.preview")
async def panel_preview(request: Request):
    """Render a page from UNSAVED settings, so the editor can show its work."""
    _require_admin(request)
    body = await _body(request)
    page = str(body.get("page") or "about")
    overrides = body.get("settings") if isinstance(body.get("settings"), dict) else {}
    return {"ok": True, **await asyncio.to_thread(
        panel_service.preview, page, _config_dir(request), overrides)}


@router.post("/api/hub/panel/page", name="panel.page")
async def panel_page(request: Request):
    """Move the real screen, for proving the wiring from a desk."""
    _require_admin(request)
    body = await _body(request)
    running = panel_service.current()
    if not running:
        raise HTTPException(status_code=409, detail="the front panel is off")
    if body.get("next"):
        running.next_page()
    elif body.get("index") is not None:
        try:
            running.set_page(int(body["index"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="index must be a number")
    return {"ok": True, "frame": running.frame()}


@router.post("/api/hub/panel/beep", name="panel.beep")
async def panel_beep(request: Request):
    _require_admin(request)
    running = panel_service.current()
    if not running:
        raise HTTPException(status_code=409, detail="the front panel is off")
    body = await _body(request)
    try:
        ms = int(body.get("ms") or panel_service.BEEP_PRESS_MS)
    except (TypeError, ValueError):
        ms = panel_service.BEEP_PRESS_MS
    running.beep(max(10, min(2000, ms)))
    return {"ok": True}


@power_router.post("/api/hub/panel/power", name="panel.power")
async def panel_power(request: Request):
    """Shut the box down or reboot it -- the dashboard half of the flow.

    Kept behind an explicit `action`, with no default: a power endpoint that
    does something sensible when called with an empty body is a power
    endpoint that eventually gets called with an empty body.
    """
    _require_site_admin(request)
    body = await _body(request)
    action = str(body.get("action") or "").lower()
    if action not in ("shutdown", "reboot"):
        raise HTTPException(status_code=400,
                            detail='action must be "shutdown" or "reboot"')
    running = panel_service.current()
    if running:
        ok = running.shutdown() if action == "shutdown" else running.reboot()
    else:
        # No panel: still a legitimate request, just without the beep and the
        # farewell screen.
        import subprocess                                    # noqa: PLC0415
        argv = ["sudo", "shutdown", "now"] if action == "shutdown" \
            else ["sudo", "reboot", "now"]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            ok = True
        except OSError as exc:
            # detail=str(exc) on a subprocess failure is "[Errno 2] No such
            # file or directory: 'sudo'", which reads as a broken request.
            raise catalog.http_error("SYS_POWER_OFF_FAILED", cause=exc,
                                     action=action) from exc
    if not ok:
        raise catalog.http_error("SYS_POWER_OFF_BUSY", action=action)
    return {"ok": True, "action": action}


router.include_router(power_router)
