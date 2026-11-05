# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
masters.py -- Probing the per-system masters.

Each system runs its own master. The hub does not read their databases and does
not render their pages: it asks each one `/api/health` and draws a card. That is
the entire coupling between hub and master, and keeping it that thin is what
lets a master be restarted, moved or upgraded without the hub knowing.

No framework imports here -- this takes a config dict and returns dicts
(docs/conventions.md sec 2).
"""

import concurrent.futures
import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

#: A master that cannot answer this fast is reported down. The hub renders on
#: every request, so this is a page-load budget, not a health opinion.
TIMEOUT_S = 1.5

# The session cookie's two names (core/services/auth.py): a page on HTTPS gets
# the `__Secure-` one, a plain-HTTP page the other. Spelled out here because
# this module takes no framework or service imports.
_PLAIN = "cytron_session"
_SECURE = "__Secure-" + _PLAIN


def for_master(cookie):
    """The cookie header to send a master. The hub calls masters over plain
    HTTP, where a master reads only `cytron_session`; a browser on HTTPS sends
    `__Secure-cytron_session`, and forwarded as it came the master answered
    401 and the hub drew every system as "master unreachable". The secure
    value is the same signed token under the plain name, so rename it -- and
    drop any plain pair beside it (an old HTTP sign-in), or the master would
    read the stale one first."""
    if not cookie or _SECURE + "=" not in cookie:
        return cookie
    pairs = [p.strip() for p in cookie.split(";") if p.strip()]
    kept = [p for p in pairs if not p.startswith(_PLAIN + "=")]
    return "; ".join(_PLAIN + p[len(_SECURE):] if p.startswith(_SECURE + "=") else p
                     for p in kept)


def probe_all(masters, timeout=TIMEOUT_S):
    """Return {slug: health_dict}. A master that fails to answer maps to None.

    Probed concurrently: serially, one unplugged master would add its whole
    timeout to the hub's page load, and ten would make the page unusable.
    """
    if not masters:
        return {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(masters), 16)) as pool:
        futures = {
            slug: pool.submit(probe, (cfg or {}).get("url"), timeout)
            for slug, cfg in masters.items()
        }
        return {slug: f.result() for slug, f in futures.items()}


def fetch_json(url, path, cookie=None, timeout=TIMEOUT_S):
    """GET <url><path> AS the calling operator: their session cookie is
    forwarded, and every process on this box validates the same signed cookie
    (core/services/auth.py shares its secret via config). Returns the parsed
    body, or None. Never raises -- an unreachable master is a normal state."""
    if not url:
        return None
    req = urllib.request.Request(url.rstrip("/") + path)
    if cookie:
        req.add_header("Cookie", for_master(cookie))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.debug("[HUB] master at %s: %s failed: %s", url, path, e)
        return None
    return body if body.get("ok") else None


def post_json(url, path, body=None, cookie=None, timeout=TIMEOUT_S):
    """POST <url><path> AS the calling operator, the way fetch_json GETs.
    Returns the parsed body, or None. Never raises -- an unreachable master
    is a normal state, and every caller here is best effort."""
    if not url:
        return None
    req = urllib.request.Request(
        url.rstrip("/") + path, method="POST",
        data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    if cookie:
        req.add_header("Cookie", for_master(cookie))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:                                    # noqa: BLE001
        return None


def stream_devices_all(masters, cookie, timeout=TIMEOUT_S):
    """{slug: [stream-capable devices] | None} across every configured master,
    for the hub's live wall. None = that master didn't answer (different from
    an answering master with no devices). Concurrent for the same reason as
    probe_all."""
    if not masters:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(masters), 16)) as pool:
        futures = {
            slug: pool.submit(fetch_json, (cfg or {}).get("url"), "/api/stream",
                              cookie, timeout)
            for slug, cfg in masters.items()
        }
        out = {}
        for slug, f in futures.items():
            body = f.result()
            out[slug] = None if body is None else (body.get("devices") or [])
        return out


def probe(url, timeout=TIMEOUT_S):
    """GET <url>/api/health. Returns the parsed body, or None if unreachable.

    Never raises. A down master is a normal state -- it is the thing the hub
    exists to show -- so it must render a card, not a 500.
    """
    if not url:
        return None
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        logger.debug("[HUB] master at %s unreachable: %s", url, e)
        return None
    return body if body.get("ok") else None
