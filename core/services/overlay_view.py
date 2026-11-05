# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""overlay_view.py -- who draws a system's live overlay, as the master sees it.

[video] overlay in the system's config (systems/<slug>/config.toml):
"server" (the default) burns boxes, zones and the HUD into the video frame,
as it always has; "client" sends the plain picture and lets the browser draw
the overlay on a canvas from the app's /overlay messages, saving the board a
full-frame paint per published frame (app/overlay_feed.py).

The page learns the mode from GET /api/detector/webrtc (the question every
live feed already asks before it shows a picture) and, for a dashboard video
tile, from that widget's own data -- never from a new poll. In server mode
neither answer changes and no page opens anything new.

The parsing mirrors app/overlay_feed.settings: the app cannot import core/,
so the rule lives twice and tests/smoke_overlayclient.py pins them together.

Framework-free (docs/conventions.md sec 2): the route owns the HTTP.
"""

import urllib.parse

from core.services import video_saving

MODES = ("server", "client")
DEFAULT_MODE = "server"
DEFAULT_HZ = 10.0
MAX_HZ = 30.0


def settings(cfg):
    """{"mode", "hz", "delay_ms"} from a system's merged config. Anything
    unreadable is the default, exactly as the app reads it."""
    video = (cfg or {}).get("video") or {}
    if not isinstance(video, dict):
        video = {}
    mode = str(video.get("overlay") or DEFAULT_MODE).strip().lower()
    if mode not in MODES:
        mode = DEFAULT_MODE
    try:
        hz = float(video.get("overlay_hz") or DEFAULT_HZ)
    except (TypeError, ValueError):
        hz = DEFAULT_HZ
    try:
        delay = int(float(video.get("overlay_delay_ms") or 0))
    except (TypeError, ValueError):
        delay = 0
    return {"mode": mode, "hz": min(MAX_HZ, max(1.0, hz)),
            "delay_ms": min(5000, max(0, delay))}


def page_info(cfg):
    """What a page needs to draw the overlay, or {} in server mode -- so a
    server-mode answer is byte-for-byte what it was before this existed.

    The [video] saving switches ride along (video_saving.page_info), only
    while one is on. So "is this system in client mode" is the `overlay`
    member, never the answer's truthiness."""
    s = settings(cfg)
    out = {}
    if s["mode"] == "client":
        out["overlay"] = {"mode": "client", "delay_ms": s["delay_ms"]}
    out.update(video_saving.page_info(cfg))
    return out


def for_system(systems_dir, slug):
    """page_info for an installed system, read from its config. Never
    raises: a tile that cannot tell keeps the server-drawn picture."""
    if not systems_dir or not slug:
        return {}
    try:
        from core.services import detector_control as dc     # noqa: PLC0415
        return page_info(dc._merged(dc._pkg_dir(systems_dir, slug)))
    except Exception:                                        # noqa: BLE001
        return {}


def upstream_path(cameras):
    """The app's /overlay path for these camera ids ("" = the first)."""
    seen = []
    for cam in cameras or [""]:
        cam = str(cam or "").strip()
        if cam not in seen:
            seen.append(cam)
    return "/overlay?" + "&".join(
        "camera=" + urllib.parse.quote(c, safe="") for c in seen)


def on_tls(scheme, forwarded_proto=None):
    """True when the browser really is on TLS, directly or via a proxy that
    terminated it and said so (the rule core/routes/authtls.py applies on
    the HTTPS branch, restated here so this module stays framework-free)."""
    if str(scheme or "").lower() == "https":
        return True
    return str(forwarded_proto or "").split(",")[0].strip().lower() == "https"


def cross_site(origin, fetch_site, host, https=False):
    """True when a browser on ANOTHER site asked. The overlay is read with a
    GET, which the session wall's CSRF check (unsafe methods only) does not
    look at; this is the same Origin rule applied to a read, plus the
    browser's own Sec-Fetch-Site verdict. No header at all is a non-browser
    caller and is allowed, as there. The hub and the masters are one site on
    different ports, which "same-site" covers.

    Over TLS the asking page must be https too (Pipi M3, 2026-10-10): a
    plain http:// page on this host can be forged by anyone on the LAN, and
    an older browser does not count the scheme in "same-site"."""
    if str(fetch_site or "").strip().lower() == "cross-site":
        return True
    origin = str(origin or "").strip()
    if not origin:
        return False
    if origin.lower() == "null":
        return True
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return True
    hostname = parts.hostname
    if not hostname or hostname.lower() != str(host or "").lower():
        return True
    return bool(https) and parts.scheme.lower() != "https"
