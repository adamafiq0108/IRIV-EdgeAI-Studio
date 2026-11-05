# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
board_hub.py -- the board's ports with their live state, for the About page.

  GET /api/hub/board    every connector on the IRIV Edge AI CM5 and what is on
                        it right now: the screw terminals (inputs read back,
                        outputs energised, LED drivers on, RS485 answering),
                        the two ethernet jacks (link, speed, address), the USB
                        sockets (which camera, which systems use it) and the
                        Wi-Fi radio (hotspot or joined network).

The hub-wide twin of the master's /api/board/io + /api/board/ports: no system
in context, so a terminal held by any of our own apps is ours, not a
stranger's (board_io.ports with slug=None). Any signed-in account may read it;
nothing here changes the box.
"""
import asyncio

from fastapi import APIRouter, Request

from core.services import board_io as board_io_service
from core.services import detector_control as detector_control_service

router = APIRouter()


def _wifi():
    """The radio, in two facts: the hotspot it broadcasts (if any) and the
    network it has joined (if any). Cheap reads only -- no scan."""
    try:
        from core.services import netconf                   # noqa: PLC0415
        if not netconf.available():
            return {"available": False}
        ap = netconf.ap_active() or ""
        joined = ""
        for d in netconf.devices():
            if d.get("type") == "wifi" and d.get("state") == "connected":
                joined = d.get("connection") or ""
        return {"available": True, "ap": ap, "joined": "" if ap else joined,
                "radio": bool(netconf.radio_on())}
    except Exception:                                        # noqa: BLE001
        return {"available": False}


def _read(systems_dir):
    inv = board_io_service.inventory(None)
    for port in inv["ports"]:
        if port["kind"] == "input":
            port["value"] = board_io_service.read_port(port["id"])
    try:
        usb = detector_control_service.usb_ports(systems_dir)
    except Exception:                                        # noqa: BLE001
        usb = []
    return {**inv, "eth": board_io_service.eth_ports(), "usb": usb,
            "wifi": _wifi()}


@router.get("/api/hub/board", name="hub.board")
async def hub_board(request: Request):
    systems_dir = getattr(request.app.state, "systems_dir", None)
    return {"ok": True, **await asyncio.to_thread(_read, systems_dir)}
