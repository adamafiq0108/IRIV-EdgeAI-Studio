# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
blynk.py -- push live stat values to the Blynk IoT cloud.

The operator pastes a device auth token on the Blynk page, maps stat keys
(zone counts, line tallies, fn_* function outputs) to virtual pins, and the
master pushes current values on an interval over Blynk's plain HTTPS API:

    GET https://{server}/external/api/batch/update?token=...&V1=...&V2=...

stdlib urllib only -- no SDK, no MQTT session to babysit; the master is the
internet-facing tier and already holds the freshest aggregate stats.

Config lives in <store>/blynk.json (persistent, OUTSIDE the repo, same home as
Builder layouts) because it carries a credential: it must never reach git, and
a redeploy must not log the device out of its cloud.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from core import config as config_mod

DEFAULT_SERVER = "blynk.cloud"
MIN_INTERVAL_S = 1
MAX_INTERVAL_S = 3600
MAX_MAPPINGS = 32

_lock = threading.Lock()
_status = {"last_push_ts": None, "last_status": None, "last_error": None,
           "pushes": 0, "failures": 0}
_backoff_until = 0.0
_fail_streak = 0


def _path():
    return os.path.join(str(config_mod.store_dir()), "blynk.json")


def _clean_pin(pin):
    pin = str(pin or "").strip().upper()
    if not re.fullmatch(r"V\d{1,3}", pin):
        raise ValueError(f"virtual pin must look like V0..V255, got {pin!r}")
    return pin


def clean_config(raw):
    """Validated config dict, or ValueError."""
    raw = raw or {}
    server = str(raw.get("server") or DEFAULT_SERVER).strip()
    # Hostname only -- a path or scheme here would redirect the credential.
    if not re.fullmatch(r"[a-z0-9.-]+(:\d+)?", server, re.IGNORECASE):
        raise ValueError("server must be a hostname, e.g. blynk.cloud")
    try:
        interval = int(raw.get("interval_s", 5))
    except (TypeError, ValueError):
        raise ValueError("interval_s must be a number")
    interval = min(MAX_INTERVAL_S, max(MIN_INTERVAL_S, interval))
    mappings, seen = [], set()
    for m in (raw.get("mappings") or [])[:MAX_MAPPINGS]:
        key = str((m or {}).get("key") or "").strip()[:80]
        if not key:
            continue
        pin = _clean_pin((m or {}).get("pin"))
        if pin in seen:
            raise ValueError(f"pin {pin} is mapped twice")
        seen.add(pin)
        mappings.append({"key": key, "pin": pin})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "token": str(raw.get("token") or "").strip()[:64],
        "server": server,
        "interval_s": interval,
        "mappings": mappings,
    }


def load():
    try:
        with open(_path(), "r", encoding="utf-8") as f:
            return clean_config(json.load(f))
    except (OSError, ValueError, TypeError):
        return clean_config({})


def save(raw):
    """Validate and persist. An empty incoming token keeps the stored one, so
    the UI can save settings without ever seeing the full credential back."""
    cfg = clean_config(raw)
    if not cfg["token"]:
        cfg["token"] = load()["token"]
    target = _path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, target)
    return cfg


def public_config(cfg=None):
    """The config with the token masked -- what the API returns. The full
    token never travels back to a browser."""
    cfg = cfg or load()
    token = cfg.get("token") or ""
    out = dict(cfg)
    out["token"] = ""
    out["token_set"] = bool(token)
    out["token_hint"] = f"…{token[-4:]}" if len(token) >= 8 else ""
    return out


def status():
    with _lock:
        return dict(_status)


def _get(url, timeout=5):
    req = urllib.request.Request(url, headers={"User-Agent": "cytron-dashboard"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.status, (res.read(200) or b"").decode("utf-8", "replace")


def test(token=None, server=None):
    """Reach Blynk and ask whether the device is known/connected -- the page's
    'check token' button. Returns {ok, connected, error}."""
    cfg = load()
    token = (token or "").strip() or cfg["token"]
    server = (server or "").strip() or cfg["server"]
    if not token:
        return {"ok": False, "connected": False, "error": "no token set"}
    url = (f"https://{server}/external/api/isHardwareConnected?"
           + urllib.parse.urlencode({"token": token}))
    try:
        code, body = _get(url)
    except urllib.error.HTTPError as e:
        detail = (e.read(200) or b"").decode("utf-8", "replace").strip()
        return {"ok": False, "connected": False,
                "error": f"HTTP {e.code}: {detail or 'invalid token?'}"}
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"ok": False, "connected": False, "error": f"unreachable: {e}"}
    return {"ok": code == 200, "connected": body.strip().lower() == "true",
            "error": None if code == 200 else f"HTTP {code}"}


def push(stats, now=None):
    """One batch update of every mapped value. Called from the master's Blynk
    loop; never raises. Exponential backoff after failures so a bad token does
    not hammer the cloud (and get the box rate-limited)."""
    global _backoff_until, _fail_streak
    now = time.time() if now is None else now
    cfg = load()
    if not cfg["enabled"] or not cfg["token"] or not cfg["mappings"]:
        return False
    if now < _backoff_until:
        return False

    params = [("token", cfg["token"])]
    sent = 0
    for m in cfg["mappings"]:
        value = (stats or {}).get(m["key"])
        if isinstance(value, dict):
            value = sum(v for v in value.values()
                        if isinstance(v, (int, float)))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        params.append((m["pin"], repr(round(float(value), 4))))
        sent += 1
    if not sent:
        return False

    url = (f"https://{cfg['server']}/external/api/batch/update?"
           + urllib.parse.urlencode(params))
    try:
        code, body = _get(url)
        error = None if code == 200 else f"HTTP {code}: {body.strip()}"
    except urllib.error.HTTPError as e:
        detail = (e.read(200) or b"").decode("utf-8", "replace").strip()
        code, error = e.code, f"HTTP {e.code}: {detail or 'rejected'}"
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        code, error = 0, f"unreachable: {e}"

    with _lock:
        _status["last_push_ts"] = now
        _status["last_status"] = code
        _status["last_error"] = error
        if error:
            _status["failures"] += 1
        else:
            _status["pushes"] += 1
    if error:
        _fail_streak = min(_fail_streak + 1, 8)
        _backoff_until = now + min(300, 2 ** _fail_streak)
    else:
        _fail_streak = 0
        _backoff_until = 0.0
    return not error


def interval_s():
    return load()["interval_s"]
