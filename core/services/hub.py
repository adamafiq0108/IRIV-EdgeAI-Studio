# -*- coding: utf-8 -*-
"""
hub.py -- Hub card assembly.

One card per discovered system, carrying the state of that system's master. The
hub owns no devices: device counts come from each master's /api/health, because
the master is the only process that knows its own slaves.

No SQL here and no framework imports: this takes a registry, a config dict and
a health dict, and returns dicts (docs/conventions.md sec 2).
"""

import os

from core import config as config_mod
from core.services import auth as auth_service

# -- system cover images ------------------------------------------------------
# One image per system, uploaded on the hub and stored in the persistent store
# (config.system_images_dir), so the picture survives redeploys and shows on
# every device that opens the hub -- it lives on the board, not in a browser.

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def image_path(slug):
    """The stored cover image for `slug`, or None. Any allowed extension."""
    directory = str(config_mod.system_images_dir())
    for ext in IMAGE_EXTS:
        path = os.path.join(directory, f"{slug}{ext}")
        if os.path.exists(path):
            return path
    return None


def save_image(slug, filename, blob):
    """Store one cover image for `slug`. The stored name is always
    <slug>.<ext> -- rebuilt from the slug, never trusted from the client,
    because this writes to disk. Replaces any previous image regardless of its
    extension. Returns the stored filename; raises ValueError on bad input."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in IMAGE_EXTS:
        raise ValueError(f"unsupported image type {ext or '(none)'}; "
                         f"use one of {', '.join(IMAGE_EXTS)}")
    if not blob:
        raise ValueError("empty file")
    if len(blob) > MAX_IMAGE_BYTES:
        raise ValueError(f"image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
    delete_image(slug)                  # one image per system, any extension
    stored = f"{slug}{ext}"
    path = os.path.join(str(config_mod.system_images_dir()), stored)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)
    return stored


def delete_image(slug):
    """Remove `slug`'s cover image (every extension variant). Returns count."""
    removed = 0
    directory = str(config_mod.system_images_dir())
    for ext in IMAGE_EXTS:
        try:
            os.remove(os.path.join(directory, f"{slug}{ext}"))
            removed += 1
        except OSError:
            pass
    return removed


def _image_url(slug):
    """The card's image URL, cache-busted by the file's mtime so a replaced
    image shows without a hard refresh; None when no image is stored."""
    path = image_path(slug)
    if not path:
        return None
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        version = 0
    return f"/api/hub/systems/{slug}/image?v={version}"


def visible_to(username, slug, masters, config_dir=None):
    """May this account SEE this system on the hub's pages?

    THE one scoping rule, shared by every hub surface -- the Systems grid,
    Live Preview, System Control, Performance. It used to live only in
    build_cards, so the grid showed an account its own systems while the
    Live Preview picker and the Loaded Networks table cheerfully listed
    everything the coarse grant allowed -- other users' systems included.

    One test:
      * auth.allowed -- the account's systems grant ("*", or a listed slug)

    username falsy = no session context (internal callers): show all,
    preserving the old behaviour for callers that do not scope.
    """
    if not username:
        return True
    if not auth_service.allowed(username, slug, config_dir):
        return False
    return True


def build_cards(registry, masters, health, username=None, config_dir=None):
    """Return [{slug, name, icon, cover, url, status, summary}] for the hub.

    `masters` is config/systems.toml's [masters.<slug>] table.
    `health`  is {slug: health_dict_or_None} from services/masters.probe_all.

    Scoped to the caller: a card is emitted only for systems the account may
    open (auth.allowed). Admin and "*" operators see every card; a single-system
    operator sees only theirs -- so the hub never shows a card that click-through
    would 401. Passing username=None (no session context) shows all, preserving
    the old behaviour for callers that don't scope.

    Three states a card can be in, and they are different problems:
      - no [masters.<slug>] block  -> the deployment never configured it
      - block, but no answer       -> the master is down or the URL is wrong
      - answer                     -> show its device counts
    """
    cards = []
    for system in registry:
        # The account's systems grant lives in visible_to(), shared with
        # every other hub surface.
        if not visible_to(username, system.slug, masters, config_dir):
            continue
        entry = masters.get(system.slug) or {}
        url = entry.get("url")
        state = health.get(system.slug)
        cards.append({
            "slug": system.slug,
            "owner": entry.get("owner", ""),
            "name": system.display_name,
            "subtitle": system.subtitle,
            "icon": system.icon,
            "cover": system.cover,
            "image": _image_url(system.slug),
            "url": url,
            "status": _status(url, state),
            "summary": _summary(url, state),
        })
    return cards


def _status(url, state):
    """The card pill, in three states an operator can act on:

      down    -- the system is not running at all (red)
      online  -- the system is up, but nothing is detecting yet (blue)
      running -- the system is up AND detecting (green)

    "partial" survives for the fleet case (some paired devices answering,
    some not), which is a genuinely different problem from either.
    """
    if not url:
        return "none"          # discovered system, no master configured
    if state is None:
        return "down"          # master configured but not answering
    # The master's own detection app -- how a deployed single-box system
    # actually runs the AI, so it alone is enough to be "running".
    if state.get("ai_running"):
        return "running"
    online = int(state.get("devices_online") or 0)
    total = int(state.get("devices_total") or 0)
    if total == 0:
        return "online"        # master up, detection stopped
    if online == 0:
        return "online"        # master up, none of its devices detecting
    if online < total:
        return "partial"
    return "running"


#: This board's LAN address, resolved once. The route lookup is a few hundred
#: microseconds but the hub grid asks per card on every 10s poll, and the
#: answer does not change while the process lives.
_lan_ip_cache = []


def _lan_ip():
    """The address OTHER machines reach this board on, or "" if it has none.

    A UDP connect() sends no packet -- it only forces a route lookup, which
    names the interface a device on this LAN would come in through. TEST-NET-1
    (RFC 5737) is unroutable by definition, so nothing can leak.
    """
    if _lan_ip_cache:
        return _lan_ip_cache[0]
    ip = ""
    try:
        import socket                                       # noqa: PLC0415
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0] or ""
        finally:
            s.close()
    except OSError:
        ip = ""
    _lan_ip_cache.append(ip)
    return ip


def _host_of(url):
    """The address an operator would type to reach this system: "192.168.1.68:8001".

    Masters are registered on the LOOPBACK address -- the hub talks to them over
    127.0.0.1, which is correct for the hub and useless on a card, because the
    operator reading it is on another machine. So the port is kept and the host
    is swapped for this board's LAN address. When the board has no LAN address
    to offer, the stored host stands rather than inventing one.
    """
    raw = str(url or "").split("//", 1)[-1].strip("/")
    if not raw:
        return ""
    host, _, port = raw.partition(":")
    if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
        lan = _lan_ip()
        if lan:
            host = lan
    return f"{host}:{port}" if port else host


def readdress(card, configured):
    """Reverse-proxy mode: core/net.py rewrite() turned the card's link into
    http://<box>/s/<slug>, so the address its summary opens with follows,
    instead of "<lan>:<port>" -- a port the operator is no longer meant to
    type (.21, 2026-10-08). Port mode: the link has no /s/ path; unchanged."""
    import urllib.parse                                         # noqa: PLC0415
    parts = urllib.parse.urlsplit(card.get("url") or "")
    if not parts.path.startswith("/s/"):
        return
    old, text = _host_of(configured), card.get("summary") or ""
    if old and text.startswith(old):
        card["summary"] = parts.netloc + parts.path.rstrip("/") + text[len(old):]


def _summary(url, state):
    """The line under a card's name, in the operator's vocabulary.

    Says SYSTEM, not master: "master" is an implementation word for the process
    serving that system, and an operator looking at a grid of systems is asking
    about the system. And when it IS reachable the useful fact is its address,
    not the word "online" -- the status pill beside it already said that.
    """
    if not url:
        return "no master configured"
    if state is None:
        return "system offline"
    host = _host_of(url)
    online = int(state.get("devices_online") or 0)
    total = int(state.get("devices_total") or 0)
    if total == 0:
        if state.get("ai_running"):
            return f"{host} · AI running" if host else "AI running"
        # Up, but not detecting: say which, since the pill's "online" is
        # exactly the state an operator mistakes for "it is working".
        return f"{host} · detection stopped" if host else "detection stopped"
    if online == total:
        return f"{host} · {online} device{'s' if online != 1 else ''} online"
    return f"{host} · {online} of {total} online"
