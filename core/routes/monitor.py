# -*- coding: utf-8 -*-
"""
monitor.py -- the Monitor account's switch, its page choices and its access
token. Thin: parse, guard, call core/services/monitor_account.py.

  GET    /api/hub/monitor          the state                (site admin and up)
  POST   /api/hub/monitor          switch on / off                    (master)
  POST   /api/hub/monitor/pages    which pages it may open  (site admin and up)
  POST   /api/hub/monitor/systems  which systems it may open, all or some
                                   (site admin and up; each one it names is
                                   then kept online)
  POST   /api/hub/monitor/token    make an access token for one named device,
                                   shown once                          (master)
  DELETE /api/hub/monitor/token/{id}   remove one device's token: that device
                                   can no longer connect               (master)
  POST   /api/auth/monitor-token   exchange the token for a session   (open,
                                   throttled; the same session a password
                                   sign-in makes)

Switching it on and off, its credentials and its token are the box owner's: a
site admin may only choose which pages it opens, the same split the other
grants have (master decides what exists, the admin hands it out).
"""

import asyncio
import pathlib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from core.routes import auth as auth_routes
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import monitor_account
from core.services import monitor_online
from core.services import ratelimit as ratelimit_service

router = APIRouter()


def _cfg(request):
    return request.app.state.config_dir


def _me(request):
    return getattr(request.state, "user", None)


def _master(request):
    if not auth_service.is_master(_me(request), _cfg(request)):
        raise HTTPException(status_code=403, detail="admin only")


def _site_admin(request):
    if not auth_service.is_site_admin(_me(request), _cfg(request)):
        raise HTTPException(status_code=403, detail="admin only")


async def _body(request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad request")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="bad request")
    return body


def _systems(request):
    """Every system on this box, for the picker."""
    return [{"slug": s.slug, "name": getattr(s, "display_name", "") or s.slug}
            for s in getattr(request.app.state, "registry", None) or []]


def _view(request, out):
    return {"ok": True, **out, "available": _systems(request)}


def _bring_up(request):
    """Allowed systems are online from this moment: end a Power off that was
    left on one, and start any master that is not answering."""
    st = request.app.state
    cfg = _cfg(request)
    keep = monitor_online.keep_list(getattr(st, "masters", {}) or {}, cfg)
    try:
        from core.services import detector_control as dc          # noqa: PLC0415
        from core.services import provision as provision_service  # noqa: PLC0415
        for slug in keep:
            dc.set_powered_off(provision_service._pkg_root(st.systems_dir, slug),
                               slug, False)
    except Exception:                                           # noqa: BLE001
        pass
    repo_dir = str(pathlib.Path(st.systems_dir).resolve().parent)
    return monitor_online.ensure_online(getattr(st, "masters", {}) or {},
                                        repo_dir, cfg)


@router.get("/api/hub/monitor", name="monitor.state")
def monitor_state(request: Request):
    _site_admin(request)
    return _view(request, monitor_account.state(_cfg(request)))


@router.post("/api/hub/monitor", name="monitor.set")
async def monitor_set(request: Request):
    _master(request)
    cfg = _cfg(request)
    body = await _body(request)
    try:
        if body.get("enabled"):
            # PBKDF2 off the event loop, like every other password write.
            out = await asyncio.to_thread(
                monitor_account.enable, str(body.get("username") or ""), None, cfg)
            what = "monitor.enabled"
        else:
            out = monitor_account.disable(cfg)
            what = "monitor.disabled"
    except auth_service.UsernameTaken as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_service.record(what, username=_me(request) or "",
                         detail=f"Monitor account {out['username']}",
                         source_ip=auth_routes._client_ip(request) or "",
                         config_dir=cfg)
    if out["enabled"]:
        await asyncio.to_thread(_bring_up, request)
    return _view(request, out)


@router.post("/api/hub/monitor/pages", name="monitor.pages")
async def monitor_pages(request: Request):
    _site_admin(request)
    cfg = _cfg(request)
    body = await _body(request)
    pages = body.get("pages")
    if not isinstance(pages, list):
        raise HTTPException(status_code=400, detail="pages must be a list")
    try:
        out = monitor_account.set_pages(pages, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_service.record("monitor.pages", username=_me(request) or "",
                         detail=", ".join(out["pages"]),
                         source_ip=auth_routes._client_ip(request) or "",
                         config_dir=cfg)
    return _view(request, out)


@router.post("/api/hub/monitor/systems", name="monitor.systems")
async def monitor_systems(request: Request):
    _site_admin(request)
    cfg = _cfg(request)
    body = await _body(request)
    known = [s["slug"] for s in _systems(request)]
    chosen = body.get("systems")
    if not body.get("all") and not isinstance(chosen, list):
        raise HTTPException(status_code=400, detail="systems must be a list")
    # An admin below master hands out only what they can open themselves.
    me = _me(request)
    if not auth_service.is_master(me, cfg):
        mine = {s for s in known if auth_service.allowed(me, s, cfg)}
        if body.get("all") or set(chosen or []) - mine:
            raise HTTPException(status_code=403,
                                detail="you can only allow systems you can open yourself")
    try:
        out = monitor_account.set_systems(bool(body.get("all")), chosen, known, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_service.record("monitor.systems", username=me or "",
                         detail="all systems" if out["systems"]["all"]
                         else ", ".join(out["systems"]["slugs"]),
                         source_ip=auth_routes._client_ip(request) or "",
                         config_dir=cfg)
    await asyncio.to_thread(_bring_up, request)
    return _view(request, out)


@router.post("/api/hub/monitor/token", name="monitor.token_make")
async def monitor_token_make(request: Request):
    _master(request)
    cfg = _cfg(request)
    body = await _body(request)
    try:
        made = monitor_account.make_token(body.get("name"), cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_service.record("monitor.token", username=_me(request) or "",
                         detail=f"access token made for {str(body.get('name'))[:40]}",
                         source_ip=auth_routes._client_ip(request) or "",
                         config_dir=cfg)
    # The one and only time the token is visible.
    resp = JSONResponse({"ok": True, "token": made["token"],
                         "token_id": made["id"],
                         **monitor_account.state(cfg),
                         "available": _systems(request)})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.delete("/api/hub/monitor/token/{token_id}", name="monitor.token_revoke")
def monitor_token_revoke(token_id: str, request: Request):
    _master(request)
    cfg = _cfg(request)
    try:
        out = monitor_account.revoke_token(token_id, cfg)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such token")
    audit_service.record("monitor.token", username=_me(request) or "",
                         detail=f"access token {token_id} removed",
                         source_ip=auth_routes._client_ip(request) or "",
                         config_dir=cfg)
    return _view(request, out)


@router.post("/api/auth/monitor-token", name="monitor.token_login")
async def monitor_token_login(request: Request):
    """A program with the Monitor's access token gets an ordinary session.

    Open (it IS the sign-in), so it carries the same defences the password
    sign-in has: a throttle per caller address, one answer for every failure
    (no hint whether the token, the account or the switch was wrong), and an
    audit line either way."""
    cfg = _cfg(request)
    body = await _body(request)
    token = str(body.get("token") or "")[:200]
    ip = auth_routes._client_ip(request) or "?"
    bucket = f"{ip}|monitor-token"
    wait = ratelimit_service.check("login", bucket)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in {int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    found = monitor_account.verify_token(token, cfg, ip=ip)
    if not found:
        ratelimit_service.record_failure("login", bucket)
        audit_service.record("login", username="monitor-token", result="fail",
                             source_ip=ip, config_dir=cfg)
        raise HTTPException(status_code=401, detail="Wrong access token.")
    name, token_id = found
    ratelimit_service.record_success("login", bucket)
    monitor_account.trim_token_sessions(token_id, cfg)
    session = auth_service.issue_session(name, cfg)
    # The session carries its token's id as its device, so removing the token
    # ends exactly the sessions it opened.
    auth_service.register_session(name, auth_service.session_sid(session),
                                  device=monitor_account.session_device(token_id),
                                  ip=ip, config_dir=cfg)
    audit_service.record("login", username=name, detail="access token",
                         source_ip=ip, config_dir=cfg)
    # `box` is what the connecting program saves next to the token: with the
    # MAC address it can find this box again whatever address DHCP gives it.
    resp = JSONResponse({"ok": True, "token": session, "username": name,
                         "hub_url": auth_routes._hub_url(request),
                         "box": monitor_account.box_identity()})
    resp.headers["Cache-Control"] = "no-store"
    return resp
