# -*- coding: utf-8 -*-
"""
weblinks.py -- every browser-facing address, in the access method the box
is set to (core/services/webaccess.py).

PORT mode: nothing here does anything; core/net.py rewrite() is what it was.
PROXY mode: one origin with no port. The hub is <origin>/, a system is
<origin>/s/<slug>, and rewrite() asks link() first, so the cards, the back
links, the create wizard, the Edit system answer and the restart answer all
change together without each learning about the mode.

ACTIVE = SET AND LIVE. The setting alone is not enough: an nft table that
somebody flushed (a firewall tool, `nft flush ruleset`) would leave every
link pointing at a port 80 nothing answers on. A background probe asks
127.0.0.1:80 for /api/health every PROBE_S; links take the proxy form only
while the HUB answers there, otherwise they stay port links -- which work
in both modes. The probe is a thread, never the request path: a hub that
blocked its own event loop connecting to itself would wait for ever.

WHY THE OLD PORTS STAY OPEN in proxy mode (the hub on its port, masters on
0.0.0.0): bookmarks, the kiosk's URL, a remote tunnel that targets a
master's port, and the OTA health gate all use them. A browser that opens
a PAGE on an old port is sent to the proxy address instead (direct_
redirect); API calls are answered where they land. Binding masters to
loopback in proxy mode would shrink the LAN surface and break all of that;
it is listed for Pipi as a follow-up, not done here.

WHERE A PAGE IS SERVED FROM: the SPA is built with a relative base, so the
index.html each server returns names its own prefix in <base href> -- "/"
on the hub, "/s/<slug>/" on a master reached through the relay -- and the
frontend reads it back (frontend/src/lib/base.js). The prefix is taken from
X-Forwarded-Prefix only when the peer is this machine and it names THIS
master's own slug: anything else serves "/".
"""

import html
import os
import re
import socket
import threading
import time
import urllib.parse

from core.services import hubport, webaccess

PREFIX = "/s/"
SLUG = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PROBE_S = 15.0
#: ...and while port 80 is NOT answering (the hub still starting, a table
#: just re-made): soon enough that a box coming up finds it in seconds.
RETRY_S = 3.0
_LOOPBACK = ("127.0.0.1", "::1", "localhost")

#: {"live": None|bool, "thread": bool}. None = not probed yet.
_state = {"live": None, "thread": False}
_lock = threading.Lock()


def probe_once(timeout=1.5):
    """Does THE HUB answer on 127.0.0.1:80? The kernel's output rule sends
    a local connect to 80 where a LAN one goes, so this proves the table."""
    try:
        with socket.create_connection(("127.0.0.1", webaccess.HTTP_PORT),
                                      timeout=timeout) as s:
            s.sendall(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            data = b""
            while len(data) < 8192:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return False
    head, _, body = data.partition(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0].split(b" ")[1:2] == [b"200"] \
        and b'"role":"hub"' in body.replace(b" ", b"")


def _loop():
    while True:
        _state["live"] = bool(probe_once())
        time.sleep(PROBE_S if _state["live"] else RETRY_S)


def live():
    """The last probe's answer (starting the prober on first use)."""
    with _lock:
        if not _state["thread"]:
            _state["thread"] = True
            threading.Thread(target=_loop, name="web-access-probe",
                             daemon=True).start()
    return bool(_state["live"])


def start(config_dir=None):
    """Each server's start, in proxy mode: probe from now on, so the first
    page opened on an old port is already redirected."""
    if webaccess.mode(config_dir) == webaccess.PROXY:
        live()


def active(config_dir=None):
    """Proxy links and redirects only while the box is set AND answering."""
    return webaccess.mode(config_dir) == webaccess.PROXY and live()


def _cfg(request):
    try:
        return request.app.state.config_dir
    except AttributeError:
        return None


def _own_ports(config_dir=None):
    """{port: slug or None}: the hub's ports (None) and every master's."""
    out = {p: slug for p, slug in hubport.master_ports(config_dir).items()}
    for p in (hubport.port(config_dir), hubport.running_port()):
        if p:
            out[p] = None
    return out


def origin(request, config_dir=None):
    """scheme://host[:port] the browser should use in proxy mode. The port
    the Host header carries is kept unless it is one of this box's own
    software ports (an old link) or the scheme's default."""
    scheme = request.url.scheme if request.url.scheme in ("http", "https") else "http"
    if request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https":
        scheme = "https"
    from core import net                                       # noqa: PLC0415
    host = net.request_host(request)
    port = request.url.port
    keep = port and port not in (80, 443) and port not in _own_ports(config_dir)
    return f"{scheme}://{host}" + (f":{port}" if keep else "")


def link(url, request, default_port=None):
    """core/net.py rewrite()'s proxy form of `url`, or None (port mode, not
    live, or a port that is neither the hub's nor a system's)."""
    cfg = _cfg(request)
    if not active(cfg):
        return None
    try:
        parts = urllib.parse.urlsplit(url or "")
        port = parts.port or default_port
    except ValueError:
        return None
    ports = _own_ports(cfg)
    if not port or port not in ports:
        return None
    slug = ports[port]
    base = origin(request, cfg) + (f"{PREFIX}{slug}" if slug else "")
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else ""
    tail = urllib.parse.urlunsplit(("", "", path, parts.query, parts.fragment))
    return base + tail


def prefix_of(request, slug):
    """"/s/<slug>" when this request came through the hub's relay for THIS
    system (loopback peer, header names our own slug), else ""."""
    if not slug:
        return ""
    from core import net                                       # noqa: PLC0415
    peer = net.socket_peer(request)     # NOT request.client: uvicorn rewrote it
    want = f"{PREFIX}{slug}"
    if peer in _LOOPBACK and request.headers.get("x-forwarded-prefix", "") == want:
        return want
    return ""


def direct_redirect(request, slug, own_port, config_dir=None):
    """A PAGE opened on an old port, in proxy mode: the proxy address of the
    same page, else None. Only when the Host header names our own port --
    a tunnel or a TLS proxy in front of us carries no such port and is
    answered where it is, or it would loop."""
    if not own_port or prefix_of(request, slug) or not active(config_dir):
        return None
    if request.url.port != int(own_port):
        return None
    base = origin(request, config_dir) + (f"{PREFIX}{slug}" if slug else "")
    query = f"?{request.url.query}" if request.url.query else ""
    return base + (request.url.path or "/") + query


_index_cache = {}


def index_html(path, prefix=""):
    """The SPA's index.html with <base href> first in <head>, cached by
    mtime. `prefix` is "" (the hub, a master reached on its port) or a
    checked "/s/<slug>"; quoted anyway."""
    stamp = os.stat(path).st_mtime_ns
    hit = _index_cache.get(path)
    if not hit or hit[0] != stamp:
        with open(path, encoding="utf-8") as fh:
            hit = (stamp, fh.read())
        _index_cache[path] = hit
    text = hit[1]
    ok = prefix.startswith(PREFIX) and SLUG.match(prefix[len(PREFIX):])
    base = '<base href="%s/">' % html.escape(prefix if ok else "", quote=True)
    at = text.lower().find("<head>")
    if at < 0:
        return base + text
    at += len("<head>")
    return text[:at] + base + text[at:]


def master_port(slug, config_dir=None):
    """The port the relay forwards /s/<slug>/ to, or None."""
    if not SLUG.match(slug or ""):
        return None
    for p, owner in hubport.master_ports(config_dir).items():
        if owner == slug:
            return p
    return None
