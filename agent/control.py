# -*- coding: utf-8 -*-
"""
control.py -- Contract v1.2 control channel (the agent's write side).

Contract v1 is deliberately read-only; v1.2 adds an OPT-IN, capability-driven
control surface for deployments that want the master to manage devices:

    GET  /api/control                  domains + current settings snapshot
    POST /api/control/action/{name}    run a named action (domain-prefixed)
    POST /api/control/{domain}         apply settings, returns fresh envelope

A domain is served only when the adapter declares the matching capability
`control.<domain>` AND implements the optional control_* methods on
agent.adapters.base.Adapter. Everything here is bearer-gated (the router is
included with the require_token dependency by agent.py).

Envelope shape per domain:
    {"settings": {...}, "status": {...},
     "actions": [{"name","label","destructive","params"}], "meta": {...}}

Rules (docs/contract-v1.md, control section):
  * `destructive: true` on an action is what drives confirmation UX on the
    master -- never name matching.
  * Reboot-class actions must RESPOND before executing (schedule the work).
  * Secrets in settings are masked on read; an adapter only overwrites a
    secret when the client sends a non-masked value.

Lives in its own module so agent.py stays within the size ceiling; it gets
the adapter through configure() -- no import cycle.
"""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

_get_adapter = None
_api_version = "1.2"


def configure(get_adapter, api_version):
    """Called by agent.create_app(). get_adapter is a zero-arg callable so
    the module always sees the current adapter, never a stale reference."""
    global _get_adapter, _api_version
    _get_adapter = get_adapter
    _api_version = api_version


def _adapter():
    if _get_adapter is None:
        raise HTTPException(status_code=503, detail="agent not configured")
    return _get_adapter()


def _declared(adapter):
    """Domain names the adapter both implements and declares as capabilities."""
    caps = set(getattr(adapter, "capabilities", ()) or ())
    names = list(adapter.control_domains() or ())
    return [n for n in names if f"control.{n}" in caps]


@router.get("/api/control")
def control_index():
    adapter = _adapter()
    domains = {}
    for name in _declared(adapter):
        domains[name] = adapter.control_get(name)
    return {"ok": True, "api_version": _api_version, "domains": domains}


# Declared BEFORE /api/control/{domain}: FastAPI matches in declaration
# order and "action" must never be captured as a domain name.
@router.post("/api/control/action/{name}")
async def control_action(name: str, request: Request):
    adapter = _adapter()
    try:
        params = await request.json()
    except Exception:
        params = {}
    if not isinstance(params, dict):
        params = {}
    header = request.headers.get("Authorization", "")
    context = {"token": header[7:] if header.startswith("Bearer ") else None}
    try:
        result = adapter.control_action(name, params, context=context)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e) or f"unknown action: {name}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **(result or {})}


@router.post("/api/control/{domain}")
async def control_set(domain: str, request: Request):
    adapter = _adapter()
    if domain not in _declared(adapter):
        raise HTTPException(status_code=404, detail=f"unsupported control domain: {domain}")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="settings body must be an object")
    try:
        envelope = adapter.control_set(domain, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "domain": domain, **(envelope or {})}
