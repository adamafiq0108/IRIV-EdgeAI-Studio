# -*- coding: utf-8 -*-
"""
cameras.py -- fleet camera-usage map (v2.0): which camera IP is assigned to
which slave, so the master can warn when the same physical camera is about to
be assigned to a second device.

Only the master sees every slave, so this lives here. It reads each ONLINE
slave's Contract v1.2 `camera` control snapshot (rtsp_url + cam2/cam3 snapshot
URLs), extracts the host/IP, and inverts to {ip: [{host_serial, label}]}. The
result is cached briefly on app.state -- probing N devices is HTTP work and the
Manage page asks for it on render + on every camera save.

Never blocks an assignment: the map only feeds a warning; the route lets the
operator override (they may legitimately share one camera across slaves while
testing).
"""

import re
import time
import urllib.parse

from core.services import control as control_service

_TTL_S = 15
_IP_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")

# camera-address keys a control `camera` domain may carry. Tolerant of extra or
# renamed keys via the substring check in ips_from_settings, so a system that
# names its camera setting something else still resolves.
_ADDR_HINTS = ("rtsp", "snap", "url", "ip", "host")


def host_of(value):
    """Extract a host/IP from a camera value (URL or bare IP), or None."""
    if not value:
        return None
    v = str(value).strip()
    if "://" in v:
        try:
            h = urllib.parse.urlparse(v).hostname
            if h:
                return h.lower()
        except ValueError:
            pass
    m = _IP_RE.search(v)
    return m.group(1) if m else None


def ips_from_settings(settings):
    """Set of camera hosts/IPs referenced by a camera settings dict."""
    out = set()
    for key, val in (settings or {}).items():
        k = key.lower()
        if any(h in k for h in _ADDR_HINTS):
            host = host_of(val)
            if host:
                out.add(host)
    return out


def _fleet(request):
    """{host_serial: {"label":.., "ips": set()}} for ONLINE slaves only.
    Offline slaves are skipped -- probing them would pay the control timeout
    each and they can't be reassigned mid-test anyway."""
    st = request.app.state
    out = {}
    for s in st.slaves:
        if not s.get("online"):
            continue
        hs = s.get("host_serial")
        tok = st.slave_tokens.get(hs)
        if not tok:
            continue
        device = {"host_serial": hs, "url": tok["url"], "token": tok["token"],
                  "label": s.get("label")}
        try:
            status, body = control_service.fetch_control(device)
        except Exception:
            continue
        ips = set()
        if status == 200 and isinstance(body, dict):
            cam = (body.get("domains") or {}).get("camera") or {}
            ips = ips_from_settings(cam.get("settings"))
        out[hs] = {"label": s.get("display_name") or s.get("label") or hs, "ips": ips}
    return out


def usage_map(request):
    """{ip: [{host_serial, label}]} across the fleet, cached ~15s on app.state."""
    st = request.app.state
    now = time.time()
    cached = getattr(st, "_cam_usage", None)
    if cached and now - cached[0] < _TTL_S:
        fleet = cached[1]
    else:
        fleet = _fleet(request)
        st._cam_usage = (now, fleet)
    inv = {}
    for hs, info in fleet.items():
        for ip in info["ips"]:
            inv.setdefault(ip, []).append({"host_serial": hs, "label": info["label"]})
    return inv


def conflicts_for(request, host_serial, ips):
    """For each candidate IP, the OTHER devices already using it. Empty = clear."""
    usage = usage_map(request)
    out = []
    for ip in ips:
        others = [u for u in usage.get(ip, []) if u["host_serial"] != host_serial]
        if others:
            out.append({"ip": ip, "devices": others})
    return out
