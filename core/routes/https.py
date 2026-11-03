# -*- coding: utf-8 -*-
"""
https.py -- the HTTPS card on the Network page (core/services/https.py,
httpsrun.py; the server itself is core/tlsserve.py).

MASTER OR DEVELOPER ONLY, the download included, like the Software port
card beside it: HTTPS is a fact about the BOX, and turning it on, moving
its port or replacing the root changes what every client on the site has
to trust. Every change is audited. Nothing here needs a developer account,
so a customer box (deploy mode "customer") has all of it.

Unlike the port and the hostname, nothing waits for a restart: HTTPS is
an EXTRA door, so opening, moving or closing it moves no address a browser
is using -- except the HTTPS page itself, which the card warns about.

THE ONLY FILE SERVED IS THE PUBLIC ROOT CERTIFICATE, built from the parsed
certificate object (never a path, never the stored text), as PEM (.crt) or
DER (.cer, the form some phones prefer). No key leaves the service.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from core import net
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import https as https_service
from core.services import httpsrun, tlscert

router = APIRouter()
#: The answer a client trusts a certificate file by on every OS we document.
CERT_TYPE = "application/x-x509-ca-cert"


def _require_owner(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="only master may change HTTPS")


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


def _audit(request, action, result, detail):
    """Best-effort, as every box-setting route does."""
    try:
        audit_service.record(action=action, result=result,
                             username=getattr(request.state, "user", None),
                             source_ip=net.client_ip(request), detail=detail,
                             config_dir=request.app.state.config_dir)
    except Exception:                                        # noqa: BLE001
        pass


async def _status(request):
    out = await asyncio.to_thread(httpsrun.status, request.app.state.config_dir)
    out["page_https"] = request.url.scheme == "https"
    out["ok"] = True
    return out


async def _apply(request):
    listener = getattr(request.app.state, "https", None)
    if listener is not None:
        await listener.apply()


@router.get("/api/hub/https", name="https.status")
async def https_status(request: Request):
    _require_owner(request)
    return await _status(request)


@router.post("/api/hub/https", name="https.save")
async def https_save(request: Request):
    """{enabled?, port?}: saved and applied at once. Turning HTTPS on also
    sends browsers to it; there is no second switch."""
    _require_owner(request)
    body, cfg = await _body(request), request.app.state.config_dir
    changed = {k: body[k] for k in ("enabled", "port") if k in body}
    try:
        if "port" in body:
            running = httpsrun.runtime()["port"]
            await asyncio.to_thread(https_service.set_port, body["port"], running, cfg)
        if "enabled" in body:
            await asyncio.to_thread(https_service.set_enabled,
                                    body["enabled"] is True, cfg)
    except https_service.HttpsError as exc:
        _audit(request, "https.save", "fail", {**changed, "error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))
    await _apply(request)
    _audit(request, "https.save", "ok", changed)
    return await _status(request)


@router.post("/api/hub/https/regenerate", name="https.regenerate")
async def https_regenerate(request: Request):
    """{"what": "leaf"} a new certificate (clients notice nothing), or
    {"what": "root", "confirm": true} a new ROOT, which every client must
    trust again -- refused without the explicit confirm."""
    _require_owner(request)
    body, cfg = await _body(request), request.app.state.config_dir
    what = str(body.get("what") or "")
    if what == "root" and body.get("confirm") is not True:
        raise HTTPException(status_code=400,
                            detail="a new root needs confirm: every client must "
                                   "trust the box again")
    try:
        done = await asyncio.to_thread(https_service.regenerate, what, cfg)
    except https_service.TooSoon as exc:
        raise HTTPException(status_code=429, detail=str(exc))
    except https_service.HttpsError as exc:
        _audit(request, f"https.new_{what or 'unknown'}", "fail", {"error": str(exc)})
        raise HTTPException(status_code=400, detail=str(exc))
    await _apply(request)
    _audit(request, f"https.new_{what}", "ok", {"reason": done.get("reason")})
    return await _status(request)


@router.get("/api/hub/https/root.crt", name="https.root")
async def https_root(request: Request, format: str = "pem"):  # noqa: A002
    """The PUBLIC root certificate, to trust once per client."""
    _require_owner(request)
    try:
        ca = await asyncio.to_thread(https_service.root_cert,
                                     request.app.state.config_dir)
    except https_service.HttpsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if ca is None:
        raise HTTPException(status_code=404,
                            detail="no root certificate yet - switch HTTPS on first")
    der = format == "der"
    body = tlscert.cert_der(ca) if der else tlscert.cert_pem(ca).encode("ascii")
    name = "iriv-edge-ai-root." + ("cer" if der else "crt")
    return Response(body, media_type=CERT_TYPE, headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
