# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
control.py -- forwards the Manage page's calls to a device's Contract v1.2
control channel, and shapes the control snapshot for rendering.

No framework imports (conventions sec 2): pure urllib forwarders returning
(status, body); the route layer resolves the device token and turns these
into HTTP responses.
"""

import json
import urllib.error
import urllib.request

TIMEOUT_S = 15


def _call(slave, method, path, payload=None):
    """Bearer-authenticated request to the device. Returns (status, body)."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{slave['url']}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {slave['token']}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"ok": False, "error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return 502, {"ok": False, "error": f"device unreachable: {e}"}


def fetch_control(slave):
    """GET the device's control snapshot. Returns (status, body)."""
    return _call(slave, "GET", "/api/control")


def apply_settings(slave, domain, payload):
    return _call(slave, "POST", f"/api/control/{domain}", payload)


def run_action(slave, name, params):
    return _call(slave, "POST", f"/api/control/action/{name}", params or {})


def detail_sections(live):
    """Device Detail (v2.0 #5) from the live slave dict: General / System /
    Network / Camera / AI. Missing values render as n/a; sections a device
    can't populate yet still show their known fields."""
    live = live or {}
    tel = live.get("telemetry") or {}
    stats = live.get("stats") or {}
    cam = live.get("camera") or {}

    def g(v, suffix=""):
        return (str(v) + suffix) if v is not None and v != "" else "n/a"

    return {
        "General": {
            "Device ID": g(live.get("host_serial")),
            "Description": g(live.get("description")),
            "Hostname": g(tel.get("hostname") or live.get("hostname")),
            "IP Address": g((live.get("url") or "").split("://")[-1].split("/")[0].split(":")[0]),
            "Platform Version": g(tel.get("platform_version")),
            "Installed Apps": g(", ".join(tel.get("installed_apps") or []) or None),
        },
        "System": {
            "CPU": g(tel.get("cpu_percent"), "%"),
            "Memory": g(tel.get("mem_percent"), "%"),
            "Disk": g(tel.get("disk_percent"), "%"),
            "CPU Temp": g(tel.get("cpu_temp_c"), "°C"),
            "Load Avg": g(", ".join(str(x) for x in tel.get("load_avg", [])) or None),
            "Uptime (s)": g(stats.get("uptime_s")),
        },
        "Network": {
            "Upload": g(tel.get("net_up_bps"), " bps"),
            "Download": g(tel.get("net_down_bps"), " bps"),
            "Total Sent": g(tel.get("net_sent_bytes"), " B"),
            "Total Recv": g(tel.get("net_recv_bytes"), " B"),
        },
        "Camera": {
            "Camera": g(cam.get("label")),
            "Running": g(cam.get("running")),
            "Count": g(tel.get("camera_count", live.get("camera_count"))),
        },
        "AI": {
            "Detection App": "running" if live.get("app_running") else "stopped",
            "Events Today": g(stats.get("events_today")),
            "Violations Today": g(stats.get("violations_today")),
        },
    }


def page_data(device, status, body, live=None):
    """Shape the control snapshot into what control.html renders.

    device: the configured slave dict (host_serial, label, url, online...).
    status/body: from fetch_control(). A v1.1 device 404s /api/control; an
    offline device 502s -- both surface as a banner, not a crash.
    live: the live slave dict (telemetry/stats) for the Device Detail panels.
    """
    domains = []
    banner = None
    if status == 200 and isinstance(body, dict):
        for name, env in (body.get("domains") or {}).items():
            env = env or {}
            domains.append({
                "name": name,
                "settings": env.get("settings") or {},
                "status": env.get("status") or {},
                "actions": env.get("actions") or [],
                "meta": env.get("meta") or {},
            })
        domains.sort(key=lambda d: d["name"])
        if not domains:
            banner = "This device declares no control domains."
    elif status == 404:
        banner = ("This device's agent predates the control channel "
                  "(Contract v1.2). Update the device to manage it here.")
    else:
        err = body.get("error") if isinstance(body, dict) else None
        banner = err or f"Device not reachable (status {status})."
    return {
        "device": {
            "host_serial": device.get("host_serial"),
            "label": (live or {}).get("display_name") or device.get("label")
            or device.get("host_serial"),
            "online": bool(device.get("online")),
            "health": (live or {}).get("health", ""),
        },
        "detail": detail_sections(live),
        "domains": domains,
        "banner": banner,
    }
