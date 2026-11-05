# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mock_slave.py -- A conforming Contract v1 slave, backed by nothing.

Two jobs:
  1. Reference implementation. When contract-v1.md is ambiguous, read this.
  2. Lets dashboard work happen with no Pi, no camera, no Postgres on the desk.

Run:
    python tools/mock_slave.py                    # port 5101, generates events
    python tools/mock_slave.py --port 5199 --token abc123
    python tools/mock_slave.py --skew 3600        # pretend the clock is an hour off

Then:
    python tools/contract_check.py http://127.0.0.1:5101 --token <token>
"""

import argparse
import datetime
import hmac
import itertools
import random
import threading
import time

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

API_VERSION = "1.3"
SLUG = "mock"

app = FastAPI(title="IRIV EdgeAI Studio mock slave", docs_url=None, redoc_url=None)

_STARTED = time.time()
_state = {
    "token": "mock-token",
    "paired": False,
    "master_serial": None,
    "skew_s": 0,
}

# Events accumulate in memory. The cursor is the list index + 1, which satisfies
# every cursor rule in contract-v1.md sec 2 *only* because this process never
# restarts meaningfully -- a real adapter must derive the cursor from durable
# storage instead (see docs/contract-v1.md sec 2, "Cursor rules").
_events = []
_events_lock = threading.Lock()
_ids = itertools.count(1)


def _now():
    """Local time, shifted by --skew so clock-drift handling can be tested."""
    return datetime.datetime.now().astimezone() + datetime.timedelta(seconds=_state["skew_s"])


def _make_event():
    n = next(_ids)
    violation = random.random() < 0.4
    return {
        "id": f"{SLUG}-{n}",
        "ts": _now().isoformat(),
        "type": "violation" if violation else "info",
        "severity": "warn" if violation else "ok",
        "label": "not_holding" if violation else "person_detected",
        "device": random.choice(["cam1", "cam2"]),
        "snapshot_id": f"{SLUG}-snap-{n}" if violation else None,
        "detail": {"direction": random.choice(["up", "down"]),
                   "risk_score": round(random.uniform(0, 1), 2)},
    }


def _generator_loop(interval):
    while True:
        time.sleep(interval)
        with _events_lock:
            _events.append(_make_event())


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def require_token(request: Request):
    header = request.headers.get("Authorization", "")
    presented = header[7:] if header.startswith("Bearer ") else ""
    # Accept current OR pending (mid-rotation) token -- keep-both-valid (#11).
    for expected in (_state["token"], _state.get("pending_token")):
        if expected and hmac.compare_digest(presented, expected):
            return
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.exception_handler(StarletteHTTPException)
async def _http_error(request, exc):
    return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)


# ---------------------------------------------------------------------------
# Contract v1
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {
        "ok": True,
        "api_version": API_VERSION,
        "uptime_s": int(time.time() - _STARTED),
    }


@app.get("/api/agent/info")
def info():
    return {
        "host_serial": "mock000000000001",
        "system_slug": SLUG,
        "display_name": "Mock System",
        "api_version": API_VERSION,
        "capabilities": ["stream", "footage", "snapshots", "analytics.daily", "email",
                         "control.device", "control.mqtt", "control.storage"],
        "camera": {"label": "Mock Camera", "running": True},
        "app_running": True,
        "paired": _state["paired"],
    }


@app.post("/api/agent/pair")
async def pair(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    token = body.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="token required")
    # A real agent refuses this once paired, unless an operator opened an unlock
    # window -- otherwise any LAN host can seize the device (contract-v1.md sec 2).
    # The mock always accepts so tests can re-pair freely.
    _state["token"] = token
    _state["master_serial"] = body.get("master_serial")
    _state["paired"] = True
    return {"ok": True, "host_serial": "mock000000000001"}


@app.post("/api/agent/unpair", dependencies=[Depends(require_token)])
def unpair():
    _state["paired"] = False
    _state["token"] = "mock-token"          # reset so a stale token can't auth
    _state.pop("pending_token", None)
    return {"ok": True, "host_serial": "mock000000000001", "paired": False}


@app.post("/api/agent/rotate", dependencies=[Depends(require_token)])
def rotate():
    import secrets as _s
    _state["pending_token"] = _s.token_urlsafe(32)
    return {"ok": True, "token": _state["pending_token"]}


@app.post("/api/agent/rotate/commit", dependencies=[Depends(require_token)])
def rotate_commit():
    pending = _state.pop("pending_token", None)
    if pending:
        _state["token"] = pending
    return {"ok": True}


@app.get("/api/summary", dependencies=[Depends(require_token)])
def summary(since: str | None = None):
    try:
        start = int(since) if since else 0
    except ValueError:
        raise HTTPException(status_code=400, detail=f"bad cursor: {since}")

    with _events_lock:
        page = _events[start:start + 200]
        total = len(_events)

    next_cursor = str(start + len(page)) if page else (since or "0")
    today = _now().date().isoformat()

    with _events_lock:
        today_events = [e for e in _events if e["ts"].startswith(today)]
        violations = [e for e in _events if e["type"] == "violation"]
        violations_today = [e for e in today_events if e["type"] == "violation"]

    return {
        "host_serial": "mock000000000001",
        "system_slug": SLUG,
        "api_version": API_VERSION,
        "now": _now().isoformat(),
        "next_cursor": next_cursor,
        "events": page,
        "stats": {
            "events_today": len(today_events),
            "events_total": total,
            "violations_today": len(violations_today),
            "violations_total": len(violations),
            "uptime_s": int(time.time() - _STARTED),
            "camera_running": True,
            "camera_label": "Mock Camera",
        },
        # v1.3 reference telemetry (own field, never in stats).
        "telemetry": {
            "cpu_percent": round(random.uniform(5, 40), 1),
            "mem_percent": round(random.uniform(30, 70), 1),
            "disk_percent": round(random.uniform(20, 80), 1),
            "cpu_temp_c": round(random.uniform(40, 60), 1),
            "load_avg": [round(random.uniform(0, 1), 2) for _ in range(3)],
            "net_up_bps": random.randint(1000, 200000),
            "net_down_bps": random.randint(1000, 500000),
            "platform_version": "1.0.0",
            "installed_apps": ["mock"],
        },
    }


@app.get("/api/footage", dependencies=[Depends(require_token)])
def footage_list(date_from: str | None = Query(None, alias="from"),
                 date_to: str | None = Query(None, alias="to")):
    with _events_lock:
        items = [{
            "id": e["snapshot_id"],
            "ts": e["ts"],
            "type": e["type"],
            "device": e["device"],
            "bytes": 1024,
            "mime": "image/jpeg",
        } for e in _events if e.get("snapshot_id")]
    return {"items": items[-200:]}


@app.get("/api/footage/{fid:path}", dependencies=[Depends(require_token)])
def footage_get(fid: str):
    # Smallest valid JPEG-ish payload -- enough to prove byte streaming works.
    return Response(content=_FAKE_JPEG, media_type="image/jpeg")


@app.get("/video_feed", dependencies=[Depends(require_token)])
def video_feed():
    def gen():
        while True:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + _FAKE_JPEG + b"\r\n")
            time.sleep(0.1)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


_FAKE_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    + "08" * 64
    + "ffd9"
)


# ---------------------------------------------------------------------------
# Contract v1.2 control channel (reference shapes for master development)
# ---------------------------------------------------------------------------

_control = {
    "mqtt": {"host": "localhost", "port": 1883, "username": "",
             "password": "", "base_topic": "mock",
             "topics": [{"id": 1, "topic": "mock/light/red", "mode": "publish",
                         "function": "red_zone", "on_payload": "1",
                         "off_payload": "0"}]},
    "storage": {"max_storage_gb": 20, "keep_months": 6},
}
_control_log = []          # actions run, for tests to introspect


def _mask(value):
    return "********" if value else ""


def _control_envelope(domain):
    if domain == "device":
        return {
            "settings": {},
            "status": {"app_running": True, "uptime_s": int(time.time() - _STARTED)},
            "actions": [
                {"name": "device.reboot", "label": "Reboot", "destructive": True, "params": []},
                {"name": "device.shutdown", "label": "Shut down", "destructive": True, "params": []},
                {"name": "device.update", "label": "Update (git pull)", "destructive": True, "params": []},
                {"name": "app.start", "label": "Start detection", "destructive": False,
                 "params": [{"name": "input", "label": "Camera input", "required": True}]},
                {"name": "app.stop", "label": "Stop detection", "destructive": False, "params": []},
            ],
            "meta": {},
        }
    if domain == "mqtt":
        cfg = dict(_control["mqtt"])
        cfg["password"] = _mask(cfg["password"])
        return {
            "settings": cfg,
            "status": {"connected": False, "error": "mock has no broker"},
            "actions": [{"name": "mqtt.test", "label": "Test publish", "destructive": False,
                         "params": [{"name": "topic", "label": "Topic", "required": True},
                                    {"name": "payload", "label": "Payload", "required": False}]}],
            "meta": {"functions": [{"id": "none", "label": "None"},
                                   {"id": "red_zone", "label": "Red light"}]},
        }
    if domain == "storage":
        return {
            "settings": dict(_control["storage"]),
            "status": {"used_gb": 3.2, "free_gb": 96.8, "snapshots": 128},
            "actions": [{"name": "storage.purge", "label": "Purge old footage",
                         "destructive": True,
                         "params": [{"name": "keep_months", "label": "Keep months",
                                     "required": False}]}],
            "meta": {},
        }
    raise HTTPException(status_code=404, detail=f"unsupported control domain: {domain}")


@app.get("/api/control", dependencies=[Depends(require_token)])
def control_index():
    return {"ok": True, "api_version": API_VERSION,
            "domains": {d: _control_envelope(d)
                        for d in ("device", "mqtt", "storage")}}


@app.post("/api/control/action/{name}", dependencies=[Depends(require_token)])
async def control_action(name: str, request: Request):
    try:
        params = await request.json()
    except Exception:
        params = {}
    known = {"device.reboot", "device.shutdown", "device.update",
             "app.start", "app.stop", "mqtt.test", "storage.purge"}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"unknown action: {name}")
    if name == "app.start" and not (params or {}).get("input"):
        raise HTTPException(status_code=400, detail="input required")
    _control_log.append({"name": name, "params": params})
    if name in ("device.reboot", "device.shutdown"):
        return {"ok": True, "scheduled": True}
    if name == "storage.purge":
        return {"ok": True, "deleted_files": 0, "deleted_rows": 0}
    return {"ok": True}


@app.post("/api/control/{domain}", dependencies=[Depends(require_token)])
async def control_set(domain: str, request: Request):
    if domain not in ("mqtt", "storage"):
        raise HTTPException(status_code=404, detail=f"unsupported control domain: {domain}")
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="settings body must be an object")
    if domain == "mqtt" and "port" in payload:
        try:
            port = int(payload["port"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="port must be an integer")
        if not 1 <= port <= 65535:
            raise HTTPException(status_code=400, detail="port must be 1-65535")
        payload["port"] = port
    # Masked secrets never overwrite the stored value.
    if payload.get("password") == "********":
        payload.pop("password")
    _control[domain].update({k: v for k, v in payload.items()
                             if k in _control[domain]})
    return {"ok": True, "domain": domain, **_control_envelope(domain)}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5101)
    p.add_argument("--token", default="mock-token")
    p.add_argument("--interval", type=float, default=3.0, help="seconds between generated events")
    p.add_argument("--skew", type=int, default=0, help="clock offset in seconds, for skew tests")
    p.add_argument("--seed", type=int, default=30, help="events to pre-generate")
    args = p.parse_args()

    _state["token"] = args.token
    _state["skew_s"] = args.skew

    for _ in range(args.seed):
        _events.append(_make_event())

    threading.Thread(target=_generator_loop, args=(args.interval,), daemon=True).start()

    print(f"\n  Mock slave  : http://{args.host}:{args.port}")
    print(f"  Token       : {args.token}")
    print(f"  Seeded      : {len(_events)} events, +1 every {args.interval}s")
    if args.skew:
        print(f"  Clock skew  : {args.skew}s")
    print(f"\n  python tools/contract_check.py http://{args.host}:{args.port} --token {args.token}\n")

    uvicorn.run(app, host=args.host, port=args.port)
