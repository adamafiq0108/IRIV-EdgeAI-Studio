# -*- coding: utf-8 -*-
"""
agent.py -- Serves Contract v1 for whatever adapter it is handed.

Runs on every edge device, port 5101, as its OWN process. Not inside the
detection app: that app gets killed by updates and operators, and the agent
must outlive it so the master can report "detection stopped" instead of the
device dropping off the dashboard entirely.

Run:
    python -m agent.agent --adapter <package>.adapters.<system>:<System>Adapter

Conformance:
    python tools/contract_check.py http://127.0.0.1:5101 --token <token>

Endpoints are sync `def`, not `async def`. Adapters do blocking work -- SQLite
reads, file stats -- so FastAPI runs them in a threadpool. An `async def`
adapter call would stall the event loop and take /api/health down with it,
which is the one endpoint that must always answer.
"""

import argparse
import importlib
import logging
import secrets
import sys
import threading
import time
import urllib.request

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent import control, identity, rotate
from agent.pairing import PairingStore

# v1.1: additive -- credential pairing + remote unpair.
# v1.2: additive -- opt-in control channel (agent/control.py).
# v1.3: additive -- optional `telemetry` field in /api/summary (CPU/mem/disk/
# temp/net + platform_version/installed_apps). Adapters default to {}.
# Every version is additive; v1 masters are unaffected.
API_VERSION = "1.3"

# Levelled and timestamped, so `journalctl -u cytron-agent` reads like the rest
# of the product. The agent keeps no log ring of its own (it has no page to show
# one on, and it must stay small enough to outlive the app it reports about) --
# journald is the store, uvicorn's own access log records the status codes.
logger = logging.getLogger(__name__)

app = FastAPI(title="IRIV Edge AI Studio Agent", docs_url=None, redoc_url=None)

_adapter = None
_pairing = None
_serial = None
_auth_backend = None       # callable(username, password) -> bool, or None
_STARTED = 0.0

#: Credential-pair lockout: 5 failures within 15 min from one source IP
#: blocks further attempts (429) until the window drains.
PAIR_FAIL_LIMIT = 5
PAIR_FAIL_WINDOW_S = 15 * 60
_pair_fails = {}           # source ip -> [monotonic timestamps]


def load_callable(spec, what):
    """Import 'package.module:attr'. Used for adapters, auth backends and
    pairing-store factories."""
    if ":" not in spec:
        raise ValueError(f"--{what} must be 'module:attr', got {spec!r}")
    module_name, attr = spec.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def load_adapter(spec):
    """Import an adapter from 'package.module:ClassName' and instantiate it."""
    return load_callable(spec, "adapter")()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_token(request: Request):
    """Bearer check for every endpoint except health and info."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer ") or not _pairing.verify(header[7:]):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.exception_handler(StarletteHTTPException)
async def _http_error(request, exc):
    # FastAPI's default error body is {"detail": ...}. The contract's shape is
    # {"ok": false, "error": ...} (docs/conventions.md sec 8).
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Contract v1
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    # Unauthenticated on purpose: the master must be able to tell "device down"
    # apart from "token wrong".
    return {
        "ok": True,
        "api_version": API_VERSION,
        "uptime_s": int(time.time() - _STARTED),
    }


@app.get("/api/agent/info")
def info():
    return {
        "host_serial": _serial,
        "system_slug": _adapter.slug,
        "display_name": _adapter.display_name,
        "api_version": API_VERSION,
        "capabilities": list(_adapter.capabilities),
        "camera": _adapter.camera(),
        "app_running": _adapter.app_running(),
        "paired": _pairing.paired,
    }


def _lockout_check(ip):
    now = time.monotonic()
    fails = [t for t in _pair_fails.get(ip, []) if now - t < PAIR_FAIL_WINDOW_S]
    _pair_fails[ip] = fails
    return len(fails) >= PAIR_FAIL_LIMIT


def _lockout_record(ip):
    _pair_fails.setdefault(ip, []).append(time.monotonic())


@app.post("/api/agent/pair")
async def pair(request: Request):
    # Parsed by hand rather than with a Pydantic model: a model rejects a
    # malformed body with 422, and the contract says a pair request missing a
    # token is a 400 (docs/contract-v1.md sec 2).
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    username = body.get("username")
    password = body.get("password")

    # -- v2 (contract v1.1): master sends the DEVICE's admin credentials; the
    # device validates them, mints the token itself and returns it. Available
    # only when an auth backend is configured (--auth-backend).
    if username is not None or password is not None:
        if _auth_backend is None:
            raise HTTPException(status_code=400,
                                detail="credential pairing not enabled on this agent")
        ip = request.client.host if request.client else "?"
        if _lockout_check(ip):
            raise HTTPException(status_code=429,
                                detail="too many failed attempts; try again later")
        if not _pairing.may_pair():
            # Single-master rule outranks credentials: an already-paired
            # device refuses a second master no matter who asks.
            raise HTTPException(status_code=409,
                                detail="already paired; disconnect the current "
                                       "master first")
        if not username or not password or not _auth_backend(username, password):
            _lockout_record(ip)
            raise HTTPException(status_code=401, detail="invalid credentials")
        token = secrets.token_urlsafe(32)
        try:
            _pairing.pair(body.get("master_serial"), token, paired_by=username)
        except PermissionError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "host_serial": _serial, "token": token}

    # -- v1 (unchanged): master generated the token and sends it.
    token = body.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    try:
        _pairing.pair(body.get("master_serial"), token)
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "host_serial": _serial}


@app.post("/api/agent/unpair", dependencies=[Depends(require_token)])
def unpair():
    """Remote revoke (v1.1). Token-gated: only the currently paired master
    (or the device's own dashboard, which reads the same store) may sever
    the pairing. After this every previously issued token is dead and the
    device waits for a new pairing."""
    _pairing.unpair()
    return {"ok": True, "host_serial": _serial, "paired": False}


@app.get("/api/summary", dependencies=[Depends(require_token)])
def summary(since: str | None = None):
    try:
        events, next_cursor = _adapter.events_since(since, limit=200)
    except ValueError as e:
        # Garbage cursor is the caller's fault. 4xx, not 5xx -- the master's
        # health logic keys off status codes.
        raise HTTPException(status_code=400, detail=f"bad cursor: {e}")

    return {
        "host_serial": _serial,
        "system_slug": _adapter.slug,
        "api_version": API_VERSION,
        # Mandatory. The master computes skew from this: a Pi that booted
        # without NTP timestamps events in 1970 and would poison the digest.
        "now": _adapter.now(),
        "next_cursor": next_cursor,
        "events": events,
        "stats": _adapter.stats(),
        # v1.3: per-device telemetry rides its OWN field, never stats -- the
        # master sums stats across the fleet, and a summed CPU%/temp is
        # meaningless. Default {} for adapters that don't implement it.
        "telemetry": _adapter.telemetry(),
    }


@app.get("/api/footage", dependencies=[Depends(require_token)])
def footage_list(date_from: str | None = Query(None, alias="from"),
                 date_to: str | None = Query(None, alias="to"),
                 limit: int = Query(200, ge=1, le=2000)):
    # `from` is a Python keyword, hence the alias: the wire name is what the
    # contract says (docs/contract-v1.md sec 2), the parameter name is legal.
    return {"items": _adapter.footage_list(date_from=date_from, date_to=date_to,
                                           limit=limit)}


@app.post("/api/footage/export", dependencies=[Depends(require_token)])
async def footage_export(request: Request):
    """Zip the requested snapshots for download. Generic: it pulls each image
    via the adapter's footage_bytes, so it works for any adapter without an
    export method of its own. Footage never leaves the device except as this
    on-demand download. Declared before the {footage_id} GET so nothing shadows
    it (different method anyway)."""
    import io
    import zipfile
    body = await request.json()
    ids = body.get("image_ids") or body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="image_ids required")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for fid in ids[:2000]:
            blob, mime = _adapter.footage_bytes(str(fid))
            if not blob:
                continue
            ext = "jpg" if ("jpeg" in (mime or "") or "jpg" in (mime or "")) else "bin"
            z.writestr(f"{fid}.{ext}", blob)
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=iriv_footage.zip"})


@app.post("/api/footage/delete", dependencies=[Depends(require_token)])
async def footage_delete(request: Request):
    """Delete the given snapshot ids from the device. Declared before the
    {footage_id} GET; different method, so no shadowing."""
    body = await request.json()
    ids = body.get("image_ids") or body.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="image_ids required")
    return {"ok": True, "deleted": _adapter.footage_delete(ids)}


@app.get("/api/footage/{footage_id:path}", dependencies=[Depends(require_token)])
def footage_get(footage_id: str):
    blob, mime = _adapter.footage_bytes(footage_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="not found")
    return Response(content=blob, media_type=mime or "application/octet-stream")


@app.get("/video_feed", dependencies=[Depends(require_token)])
def video_feed():
    url = _adapter.video_feed_url()
    if not url:
        raise HTTPException(status_code=404, detail="no stream")

    try:
        upstream = urllib.request.urlopen(url, timeout=10)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream unreachable: {e}")

    ctype = upstream.headers.get("Content-Type",
                                "multipart/x-mixed-replace; boundary=frame")

    def relay():
        # Pass bytes straight through. Re-encoding here would cost CPU on a Pi
        # that is already running detection; the master throttles for grid view.
        try:
            while True:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(relay(), media_type=ctype)


# ---------------------------------------------------------------------------

def create_app(adapter, pairing_path=None, serial_fallback=None,
               auth_backend=None, pairing_store=None):
    """Wire up globals. Used by main() and by tests.

    pairing_store: a ready store instance (deployments may supply a
    database-backed one); falls back to the file PairingStore.
    auth_backend: callable(username, password) -> bool enabling v2
    credential pairing.
    """
    global _adapter, _pairing, _serial, _auth_backend, _STARTED
    _STARTED = time.time()
    _adapter = adapter
    _pairing = pairing_store or PairingStore(pairing_path)
    _auth_backend = auth_backend
    _serial = identity.host_serial(fallback=serial_fallback)
    control.configure(lambda: _adapter, API_VERSION)
    rotate.configure(lambda: _pairing)
    return app


# Control channel (v1.2): bearer-gated as a whole; the router itself serves
# only the domains the adapter declares (agent/control.py).
app.include_router(control.router, dependencies=[Depends(require_token)])
# Credential rotation (v1.3): bearer-gated; keep-both-valid so the switch
# never opens a 401 gap (agent/rotate.py).
app.include_router(rotate.router, dependencies=[Depends(require_token)])


def _install_hooks():
    """Make this process's death, and its threads' deaths, log lines.

    Hand-rolled rather than shared with the hub and the masters
    (core/services/log_collect.py) because the agent imports nothing from
    core/: it ships to devices on its own and must outlive the app it reports
    about. journald is its store, so a record here is a logger call and
    nothing else.

    The agent that stops answering /api/health is the one that takes a whole
    device off the dashboard, and until now it could do that without leaving a
    single line saying why.
    """
    def _process(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("[AGENT] stopped on an unhandled %s: %s",
                        exc_type.__name__, exc, exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def _thread(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logger.error("[AGENT] thread %s died on an unhandled %s: %s",
                     getattr(args.thread, "name", "?"), args.exc_type.__name__,
                     args.exc_value,
                     exc_info=(args.exc_type, args.exc_value,
                               args.exc_traceback))

    sys.excepthook = _process
    if hasattr(threading, "excepthook"):                    # 3.8+
        threading.excepthook = _thread


def main():
    p = argparse.ArgumentParser(description="IRIV Edge AI Studio system agent (Contract v1).")
    p.add_argument("--adapter", required=True, help="module:ClassName")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5101)
    p.add_argument("--state", default=None, help="pairing state file")
    p.add_argument("--serial-fallback", default=None,
                   help="dev only; off-device there is no CPU serial to read")
    p.add_argument("--unlock", action="store_true",
                   help="open the pairing window at startup (first install)")
    p.add_argument("--auth-backend", default=None,
                   help="module:callable(username,password)->bool enabling "
                        "v2 credential pairing")
    p.add_argument("--pairing-store", default=None,
                   help="module:factory(state_path)->store|None for a "
                        "deployment-supplied pairing store; falls back to "
                        "the file store when absent or the factory declines")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _install_hooks()

    adapter = load_adapter(args.adapter)

    auth_backend = None
    if args.auth_backend:
        try:
            auth_backend = load_callable(args.auth_backend, "auth-backend")
        except Exception as e:  # noqa: BLE001
            logger.warning("[AGENT] auth backend unavailable (%s) - credential "
                           "pairing disabled, v1 pairing still works", e)

    store = None
    if args.pairing_store:
        try:
            store = load_callable(args.pairing_store, "pairing-store")(args.state)
        except Exception:  # noqa: BLE001
            # The traceback matters: a deployment-supplied factory that throws
            # silently looks identical to one that declined on purpose.
            logger.warning("[AGENT] pairing-store factory raised", exc_info=True)
            store = None
        if store is None:
            logger.warning("[AGENT] pairing-store factory declined - using the "
                           "file store")

    create_app(adapter, pairing_path=args.state,
               serial_fallback=args.serial_fallback,
               auth_backend=auth_backend, pairing_store=store)

    if args.unlock:
        _pairing.unlock()

    # One line, every field the old banner had. An installer still sees it on
    # the console (basicConfig writes to stderr); journald keeps it for the
    # first question every pairing problem starts with -- which serial, and
    # was it already paired.
    logger.info("[AGENT] %s (%s) on http://%s:%d - serial=%s capabilities=%s "
                "paired=%s%s",
                adapter.slug, adapter.display_name, args.host, args.port,
                _serial, ",".join(adapter.capabilities), _pairing.paired,
                "  [unlock window open]" if _pairing.unlocked else "")

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
