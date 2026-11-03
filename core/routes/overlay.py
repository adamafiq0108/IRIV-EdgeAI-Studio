# -*- coding: utf-8 -*-
"""
overlay.py -- GET /api/detector/overlay: the live overlay as server-sent
events, for a system whose browser draws it ([video] overlay = "client").

A relay of the detection app's loopback /overlay, the same way
/api/detector/preview relays its MJPEG: the app port stays on 127.0.0.1 and
this route is the only door, behind the session wall every master route is
behind, with a cross-site read refused on top (core/services/overlay_view.py).

SSE rather than a WebSocket: the master's uvicorn ships with no WebSocket
implementation (core/services/wsrelay.py; the hub closes /s/ upgrades
besides), the traffic is one-way, and a streaming response is exactly what
the MJPEG relay already is -- same Relay, same way of ending when the
browser goes. One stream per PAGE carries every camera it shows (repeat
`camera`), so the overlay costs a page one of its few HTTP connections, not
one per tile.

404 in server mode: a server-mode system never holds one of these open.
"""

import http.client

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.routes.models import _system_context
from core.services import detector_control as detector_control_service
from core.services import overlay_view

router = APIRouter()


@router.get("/api/detector/overlay", name="detector.overlay")
def detector_overlay(request: Request, camera: list[str] = Query([""])):
    """Relay this system's overlay messages for the cameras named (none or
    "" = the first camera). Each event's data is one JSON message, as built
    by the app's overlay_feed.build."""
    if overlay_view.cross_site(request.headers.get("Origin"),
                               request.headers.get("Sec-Fetch-Site"),
                               request.url.hostname,
                               overlay_view.on_tls(
                                   request.url.scheme,
                                   request.headers.get("X-Forwarded-Proto"))):
        raise HTTPException(status_code=403, detail="cross-site request refused")
    systems_dir, slug = _system_context(request)
    cfg = detector_control_service._merged(
        detector_control_service._pkg_dir(systems_dir, slug))
    if overlay_view.settings(cfg)["mode"] != "client":
        raise HTTPException(status_code=404,
                            detail="this system draws its overlay on the server")
    port = detector_control_service.stream_port(systems_dir, slug)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", overlay_view.upstream_path(camera[:16]))
        upstream = conn.getresponse()
    except OSError as e:
        raise HTTPException(status_code=502,
                            detail="the detection app is not answering") from e
    if upstream.status != 200:
        conn.close()
        # The app's 404 is its verdict (no such camera, or not in client mode
        # yet after a config edit); anything else is the app misbehaving.
        raise HTTPException(status_code=404 if upstream.status == 404 else 502,
                            detail="no overlay for this camera")
    from starlette.background import BackgroundTask          # noqa: PLC0415
    from core.services.preview_relay import Relay            # noqa: PLC0415
    # 30 s read timeout: the app writes a keepalive every 10 s of silence,
    # so a longer quiet is a dead app, not a still scene.
    link = Relay(conn, upstream, read_timeout=30)
    return StreamingResponse(link.chunks(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store",
                                      "X-Accel-Buffering": "no"},
                             background=BackgroundTask(link.close))
