# -*- coding: utf-8 -*-
"""
hubport.py -- the Software port card on the Network page.

MASTER OR DEVELOPER ONLY, read included. Moving the port moves the address
every operator on the site has bookmarked and every link the front panel
prints; that is the box owner's call (Tok Aba, 2026-10-04), not a site
admin's, and the Network page's other cards are site-admin.

Nothing here restarts anything. Apply SAVES the port for the next restart
of the box (core/services/boxpending.py) and answers with where the
dashboard will be; the box keeps answering on the port the browser is
using until it restarts. The restart is POST /api/hub/restart
(core/routes/boxrestart.py), the same "nothing applies implicitly" rule
the rest of the Network page lives by.

TWO WAYS TO REACH THE BOX, ONE CARD (Tok Aba, 2026-10-07): "Custom port"
is the above, unchanged and the default; "Use proxy (port 80)" puts the
hub at http://<box>/ and every system at http://<box>/s/<slug>/ through
the hub's relay (core/services/webaccess.py). Port 80 is fixed for that
choice. The body says which: {"mode": "proxy"} or {"mode": "port",
"port": N}; a body with only a port is the custom port, as before. Both
are saved for the next restart, by the same Apply and the same prompt.

Hub only, like core/routes/network.py: the port belongs to the BOX.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import boxpending, hubport, webaccess

router = APIRouter()


def _require_owner(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="only master or a developer may change the "
                                   "software port")


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


def _audit(request, action, detail):
    """Best-effort, as every other box-setting route does: a database
    outage must not block the save."""
    try:
        audit_service.record(action=action, result="ok",
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request),
                             detail=detail,
                             config_dir=request.app.state.config_dir)
    except Exception:                                        # noqa: BLE001
        pass


def _status(request):
    cfg = request.app.state.config_dir
    out = hubport.status(cfg)
    # The address the browser is on now: the request's host (core/net.py).
    out["url"] = net.rewrite("", request, default_port=out["running"])
    out["restart"] = boxpending.status(request, cfg)
    out["access"] = webaccess.status(cfg)
    out["ok"] = True
    return out


@router.get("/api/hub/port", name="hubport.status")
async def port_status(request: Request):
    _require_owner(request)
    # can_reboot() forks sudo; off the event loop like every nmcli call on
    # the Network page (perf item L5).
    return await asyncio.to_thread(_status, request)


@router.post("/api/hub/port/test", name="hubport.test")
async def port_test(request: Request):
    """The Test button: free, reserved, or already the current port."""
    _require_owner(request)
    body = await _body(request)
    if body.get("mode") == webaccess.PROXY:
        return await asyncio.to_thread(webaccess.check, request.app.state.config_dir)
    try:
        return await asyncio.to_thread(hubport.check, body.get("port"),
                                       request.app.state.config_dir)
    except hubport.PortError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/hub/port", name="hubport.save")
async def port_save(request: Request):
    """Save the port for the next restart. Validated again here -- the Test
    button is advice, this is the gate (and the restart validates once more
    before binding it). The port the hub answers on now cancels a pending
    change. Answers the full status, `restart.after` = where to go."""
    _require_owner(request)
    body = await _body(request)
    cfg = request.app.state.config_dir
    mode = str(body.get("mode") or webaccess.PORT)
    try:
        port = None
        if mode == webaccess.PORT:          # the port first: it is the one that refuses
            port = await asyncio.to_thread(hubport.stage, body.get("port"),
                                           boxpending.boot_id(), cfg)
        access = await asyncio.to_thread(webaccess.stage, mode,
                                         boxpending.boot_id(), cfg)
    except (hubport.PortError, webaccess.AccessError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # None: nothing waits (the value the box already has, or cancelled).
    _audit(request, "hub.port", {"pending": port, "access": access})
    return await asyncio.to_thread(_status, request)
