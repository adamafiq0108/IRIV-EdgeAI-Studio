# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
sync.py -- Polling every configured slave for one system.

Runs from a background loop in master_server.py, once per config/slaves.toml's
[sync] interval_s (default 10 minutes). Each cycle: health first (unauthenticated,
tells "device down" from "token wrong"), then summary if online, paged from the
cursor this master saved last cycle.

No framework imports here -- this takes plain dicts and returns plain dicts
(docs/conventions.md sec 2). master_server.py owns app.state; this module never
sees a Request or an app.

The master keeps no database yet (core/repos/ is still empty). Events live in
memory only and reset on restart -- safe, because `since` omitted means "from
the beginning" (contract-v1.md sec 2), so a restarted master simply re-syncs
everything a slave still has. What is lost is history a slave has since
trimmed, not anything this master would have shown differently.
"""

import concurrent.futures
import datetime
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

#: Contract v1 sec 2: a device without NTP timestamps events in 1970 and would
#: poison every aggregate that follows. Matches contract_check.py's own bound.
SKEW_LIMIT_S = 60

HEALTH_TIMEOUT_S = 2.0
SUMMARY_TIMEOUT_S = 10.0


def _get(url, token=None, timeout=10.0):
    """GET url. Returns (status, parsed_json_or_None). Never raises."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.info("[SYNC] %s unreachable: %s", url, e)
        return 0, None


def poll_one(slave, cursor):
    """Poll a single slave. Returns (updated_slave_dict, new_events, next_cursor).

    `slave` is one entry from services.slaves.load(); `cursor` is whatever this
    master saved for it last cycle (None on first contact).
    """
    base = {**slave, "online": False, "app_running": False, "capabilities": (),
            "skew_flagged": False, "stats": {}, "telemetry": {},
            "last_heartbeat": None}

    status, health = _get(f"{slave['url']}/api/health", timeout=HEALTH_TIMEOUT_S)
    if status != 200 or not health or not health.get("ok"):
        return base, [], cursor

    status, info = _get(f"{slave['url']}/api/agent/info", timeout=HEALTH_TIMEOUT_S)
    if status == 200 and info:
        base["online"] = True
        base["app_running"] = bool(info.get("app_running"))
        base["capabilities"] = tuple(info.get("capabilities") or ())
        base["label"] = base["label"] or info.get("display_name", "")

    q = f"?since={cursor}" if cursor else ""
    status, summary = _get(f"{slave['url']}/api/summary{q}", token=slave["token"],
                           timeout=SUMMARY_TIMEOUT_S)
    if status != 200 or not summary:
        # Reachable but the summary call failed (bad/rotated token, 5xx). The
        # device still counts as online -- health said so -- but contributes no
        # new events this cycle rather than crashing the whole poll loop.
        logger.info("[SYNC] %s: summary failed (status %s)", slave["host_serial"], status)
        return base, [], cursor

    base["stats"] = summary.get("stats") or {}
    # uptime_s lives in /api/health (contract-v1.md sec 2), not in the
    # adapter's stats -- carry it over so pages read one dict.
    if "uptime_s" in health:
        base["stats"].setdefault("uptime_s", health["uptime_s"])
    # v1.3: per-device telemetry on its own field (never merged into stats).
    base["telemetry"] = summary.get("telemetry") or {}
    # A successful summary is the freshest proof of life; stamp it so the UI
    # can show "last heartbeat" without another call.
    base["last_heartbeat"] = _now_iso()
    base["skew_flagged"] = _skew_flagged(summary.get("now"))
    next_cursor = summary.get("next_cursor", cursor)
    return base, summary.get("events") or [], next_cursor


def ping_health(slave):
    """Light liveness probe: /api/health then /api/agent/info only -- no summary.

    The fast health loop (master_server._health_loop) calls this between full
    sync cycles so a device going up/down surfaces within seconds instead of a
    whole [sync] interval_s. It mirrors poll_one's "online" logic (health ok AND
    agent/info answers) but skips the heavy /api/summary telemetry pull, so it is
    two tiny unauthenticated GETs per device -- near-zero cost. Returns a delta
    dict to merge onto the live slave entry; never raises.
    """
    url = slave.get("url")
    if not url:
        return {"online": False, "app_running": False}

    status, health = _get(f"{url}/api/health", timeout=HEALTH_TIMEOUT_S)
    if status != 200 or not health or not health.get("ok"):
        return {"online": False, "app_running": False}

    status, info = _get(f"{url}/api/agent/info", timeout=HEALTH_TIMEOUT_S)
    if status != 200 or not info:
        return {"online": False, "app_running": False}

    return {"online": True,
            "app_running": bool(info.get("app_running")),
            "capabilities": tuple(info.get("capabilities") or ()),
            "last_heartbeat": _now_iso()}


def ping_all(slaves):
    """Light-probe every slave concurrently. Returns {host_serial: delta_dict}.

    Concurrent for the same reason poll_all is: one unreachable device must not
    add its whole timeout to every other device's wait.
    """
    # A device stored under a reserved or odd name is never contacted
    # (slaves.valid_serial, Pipi N-2).
    slaves = [s for s in slaves or () if not s.get("reserved")]
    if not slaves:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(slaves), 16)) as pool:
        futures = {s["host_serial"]: pool.submit(ping_health, s) for s in slaves}
        return {hs: f.result() for hs, f in futures.items()}


def _now_iso():
    return datetime.datetime.now().astimezone().isoformat()


def _skew_flagged(now_iso):
    if not isinstance(now_iso, str):
        return False
    try:
        slave_now = datetime.datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if slave_now.tzinfo is None:
        return False
    skew = (datetime.datetime.now().astimezone() - slave_now).total_seconds()
    return abs(skew) > SKEW_LIMIT_S


def poll_all(slaves, cursors):
    """Poll every slave concurrently.

    Returns ({host_serial: updated_dict}, {host_serial: [events]}, {host_serial: next_cursor}).
    Concurrent for the same reason services.masters probes concurrently: one
    unreachable device must not add its whole timeout to every other device's
    wait, and a system can have many devices.

    A device stored under a reserved or odd name (slaves.valid_serial) is not
    polled: its events would otherwise arrive stamped with that name, and
    "__local__" is the local app's (Pipi N-2).
    """
    slaves = [s for s in slaves or () if not s.get("reserved")]
    if not slaves:
        return {}, {}, {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(slaves), 16)) as pool:
        futures = {
            s["host_serial"]: pool.submit(poll_one, s, cursors.get(s["host_serial"]))
            for s in slaves
        }
        results = {hs: f.result() for hs, f in futures.items()}

    updated = {hs: r[0] for hs, r in results.items()}
    events = {hs: r[1] for hs, r in results.items()}
    next_cursors = {hs: r[2] for hs, r in results.items()}
    return updated, events, next_cursors
