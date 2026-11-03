# -*- coding: utf-8 -*-
"""
proxy.py -- Relays a slave's stream and footage through this master.

The browser never sees a slave's bearer token: it hits this master, which
attaches the token server-side and relays bytes. Footage stays on the device
that recorded it (contract-v1.md sec 2) -- this proxies on demand, it copies
nothing.

Routes take host_serial, not a URL: proxying an arbitrary caller-supplied URL
would make this master an open relay. Only devices in this master's own
config/slaves.toml can be reached.

These paths do NOT start with /api/, which is what the session wall keys its
401 on -- so for a long time they were reachable with no session at all: live
camera video and a footage DELETE, to anyone routable to the board. The wall
now names them (core/routes/auth.py PROTECTED_PREFIXES) AND every handler
here re-checks the session itself. Two independent checks on purpose: this
router is the one that pays for a mistake in the other one.
"""

import json
import urllib.error
import urllib.request

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from core.services import auth as auth_service

router = APIRouter()


def _require_session(request):
    """The session the wall should already have proved. Belt and braces: a
    relay that leaks a camera must not depend on one line of middleware."""
    if not getattr(request.state, "user", None):
        raise HTTPException(status_code=401, detail="login required")


def _require_admin(request):
    """Destructive device operations (delete, bulk export) are admin work."""
    _require_session(request)
    if not auth_service.is_admin(request.state.user,
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _slave_or_404(request, host_serial):
    _require_session(request)
    slave = request.app.state.slave_tokens.get(host_serial)
    if slave is None:
        raise HTTPException(status_code=404, detail="unknown device")
    return slave


#: What a device may say its bytes are (Pipi, 2026-10-07); anything else
#: (text/html, a script) is a download, never rendered on this origin.
_SAFE = ("image/", "video/", "application/zip", "application/octet-stream",
         "multipart/x-mixed-replace")       # the MJPEG feed


def _safe(ctype):                 # (media_type, extra headers) for a device's type
    if ctype.split(";")[0].strip().lower().startswith(_SAFE):
        return ctype, {}
    return "application/octet-stream", {"Content-Disposition": "attachment"}


def _relay(upstream):
    def gen():
        try:
            while True:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()
    ctype, extra = _safe(upstream.headers.get(
        "Content-Type", "multipart/x-mixed-replace; boundary=frame"))
    return StreamingResponse(gen(), media_type=ctype, headers=extra)


@router.get("/video_feed/{host_serial}", name="proxy.video_feed")
def video_feed(request: Request, host_serial: str):
    slave = _slave_or_404(request, host_serial)
    req = urllib.request.Request(f"{slave['url']}/video_feed")
    req.add_header("Authorization", f"Bearer {slave['token']}")
    try:
        upstream = urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"device unreachable: {e}")
    return _relay(upstream)


@router.get("/footage/{host_serial}", name="proxy.footage_list")
def footage_list(request: Request, host_serial: str,
                 date_from: str | None = Query(None, alias="from"),
                 date_to: str | None = Query(None, alias="to"),
                 limit: int | None = Query(None)):
    slave = _slave_or_404(request, host_serial)
    qs = "&".join(f"{k}={v}" for k, v in
                  (("from", date_from), ("to", date_to), ("limit", limit)) if v)
    req = urllib.request.Request(f"{slave['url']}/api/footage" + (f"?{qs}" if qs else ""))
    req.add_header("Authorization", f"Bearer {slave['token']}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"device unreachable: {e}")


@router.post("/footage/{host_serial}/export", name="proxy.footage_export")
async def footage_export(request: Request, host_serial: str):
    """Relay a ZIP-export request to the device and stream the archive back, so
    the operator can download selected snapshots. The image bytes are zipped on
    the device; this master copies nothing to disk."""
    _require_admin(request)      # bulk exfiltration of recorded footage
    slave = _slave_or_404(request, host_serial)
    body = await request.body()
    req = urllib.request.Request(f"{slave['url']}/api/footage/export", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {slave['token']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype, _ = _safe(r.headers.get("Content-Type", "application/zip"))
            return Response(content=r.read(), media_type=ctype, headers={
                "Content-Disposition": "attachment; filename=iriv_footage.zip"})
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=e.code, detail=e.read().decode(errors="replace") or "export failed")
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"device unreachable: {e}")


@router.post("/footage/{host_serial}/delete", name="proxy.footage_delete")
async def footage_delete(request: Request, host_serial: str):
    """Relay a delete request to the device. Declared before the {footage_id}
    GET so the literal /delete path is unambiguous."""
    _require_admin(request)      # destroys recordings on the device
    slave = _slave_or_404(request, host_serial)
    body = await request.body()
    req = urllib.request.Request(f"{slave['url']}/api/footage/delete", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {slave['token']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=e.code, detail=e.read().decode(errors="replace") or "delete failed")
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"device unreachable: {e}")


@router.get("/footage/{host_serial}/{footage_id:path}", name="proxy.footage_get")
def footage_get(request: Request, host_serial: str, footage_id: str):
    slave = _slave_or_404(request, host_serial)
    req = urllib.request.Request(f"{slave['url']}/api/footage/{footage_id}")
    req.add_header("Authorization", f"Bearer {slave['token']}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype, extra = _safe(r.headers.get("Content-Type", "application/octet-stream"))
            return Response(content=r.read(), media_type=ctype, headers=extra)
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=e.code, detail="not found")
    except (urllib.error.URLError, OSError) as e:
        raise HTTPException(status_code=502, detail=f"device unreachable: {e}")
