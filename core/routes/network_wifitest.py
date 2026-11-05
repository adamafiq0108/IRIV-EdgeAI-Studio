# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
network_wifitest.py -- POST /api/hub/network/wifi/test, the Network page's
"Test connection": wlan0 leaves the access point, tries the network as a
trial (lease required), and comes back as the access point with the
outcome in words (core/services/wifitest.py).

"Test and switch" is NOT here: it is the ordinary switch (POST
/api/hub/network/ap with a typed network), which runs the same trial with
keep on. A route of its own because routes/network.py sits at its line cap.
ADMIN ONLY, like every write there, and refused while a switch is under way
(network._net). Answers at once with the full status, payload["switch"]
"switching"; the page polls it, as for a switch.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.routes.network import _answer, _body, _net, _require_admin, _via
from core.services import netconf, wifitest

router = APIRouter()


@router.post("/api/hub/network/wifi/test", name="network.wifi_test")
async def network_wifi_test(request: Request):
    _require_admin(request)
    body = await _body(request)
    devices = await asyncio.to_thread(netconf.devices)
    via = _via(request, {"devices": devices})["via"]
    try:
        await _net(wifitest.start, str(body.get("ssid") or ""),
                   str(body.get("password") or ""), hidden=body.get("hidden") is True,
                   via=via, config_dir=request.app.state.config_dir)
    except netconf.NetError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return await _answer(request)
