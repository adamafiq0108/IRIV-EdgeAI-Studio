# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
net.py -- Make browser-facing URLs follow the host the caller actually used.

The hub and the masters are separate processes on separate ports, so the links
between them (a card that opens a master, a master's "< Systems" back link)
cannot be url_for()'d -- they are absolute URLs pointing at another process.
Historically those hosts came from config, which meant the deployment's LAN IP
was frozen into config/systems.local.toml on first boot and went stale the next
time DHCP moved the board: every link then pointed at an address nobody could
reach any more.

The host is not deployment policy -- it is whatever the operator typed in the
browser. The port IS policy (the hub owns 5100, each master is assigned one), so
that keeps coming from config. rewrite() splits those two concerns: keep the
configured scheme/port/path, swap in the request's host.

...unless the box is in REVERSE PROXY mode (Settings -> Network -> Access,
core/services/webaccess.py): then there is one address with no port, the hub
at / and each system at /s/<slug>, and rewrite() hands back that form
instead (core/services/weblinks.py). Port mode, the default, is unchanged.
"""

import ipaddress
import urllib.parse


def request_host(request):
    """The host the caller reached this process by, bracketed if IPv6, or ""."""
    try:
        host = request.url.hostname or ""
    except Exception:
        host = ""
    if host and ":" in host:            # IPv6 literal -- needs brackets in a URL
        host = f"[{host}]"
    return host


def rewrite(url, request, default_port=None):
    """`url` with its host replaced by the request's.

    An empty `url` plus a `default_port` synthesises one (http://<host>:<port>),
    which is how a deployment that configures nothing at all still gets working
    links. An empty `url` with no default stays empty -- callers render no link
    rather than a broken one. If the host cannot be determined (no Host header),
    `url` is returned untouched.
    """
    proxied = _proxied(url, request, default_port)
    if proxied is not None:
        return proxied
    host = request_host(request)
    if not host:
        return url
    if not url:
        return f"http://{host}:{default_port}" if default_port else ""

    parts = urllib.parse.urlsplit(url)
    port = parts.port or default_port
    netloc = f"{host}:{port}" if port else host
    return urllib.parse.urlunsplit(
        ((parts.scheme or "http"), netloc, parts.path, parts.query, parts.fragment))


def _proxied(url, request, default_port):
    """The same link in the box's reverse-proxy form, or None in port mode.
    Asked FIRST by rewrite(): every browser-facing link already comes through
    here, so the access mode is applied in one place (core/services/
    weblinks.py) instead of at each card, toast and back link."""
    try:
        from core.services import weblinks                    # noqa: PLC0415
        return weblinks.link(url, request, default_port)
    except Exception:                                         # noqa: BLE001
        return None                    # never lose a link to the proxy logic


#: Peers that are this machine.
LOOPBACK = ("127.0.0.1", "::1", "localhost")


def client_ip(request):
    """Who is calling, as an address -- for rate limits and audit rows.

    X-Forwarded-For is honoured only when the direct peer is this machine
    (the hub's /s/ relay, a cloudflared/ngrok tunnel, a local TLS proxy),
    and then only its LAST entry: that is the one the local hop appended
    from the socket it accepted. Everything before it came from the client
    and is whatever the client typed (Pipi, 2026-10-07). It must parse as an
    IP, or the peer is used. Never the header verbatim."""
    peer = (request.client.host if getattr(request, "client", None) else "") or ""
    headers = getattr(request, "headers", None) or {}
    forwarded = headers.get("X-Forwarded-For", "") or headers.get("x-forwarded-for", "")
    if forwarded and peer in LOOPBACK:
        hop = forwarded.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(hop))
        except ValueError:
            pass
    return peer or None


def local_caller(request):
    """A process on THIS machine, talking to this process directly.

    A loopback peer used to be the whole test. Since the hub can relay
    /s/<slug>/ to a master (core/services/webproxy.py), a loopback peer may
    be the hub carrying somebody on the LAN -- so a request that says it was
    forwarded (X-Forwarded-For / X-Forwarded-Prefix, which the relay always
    sets) is NOT local, whatever its socket says. The relay also refuses the
    local-only paths outright; this is the second lock."""
    peer = (request.client.host if getattr(request, "client", None) else "") or ""
    if peer not in LOOPBACK:
        return False
    headers = request.headers
    return "x-forwarded-for" not in headers and "x-forwarded-prefix" not in headers


#: Where asgi() keeps the address the socket was accepted from.
PEER_KEY = "iriv.socket_peer"

#: uvicorn.run() options that go with asgi(): its own proxy-header rewrite
#: is off because asgi() applies the same middleware itself, one step later.
UVICORN = {"proxy_headers": False}


def asgi(app):
    """The app a master is served as: the socket peer recorded, THEN
    uvicorn's ProxyHeadersMiddleware, exactly as uvicorn would have applied it.

    WHY: uvicorn's default (proxy_headers=True, FORWARDED_ALLOW_IPS
    127.0.0.1,::1) rewrites scope["client"] to the X-Forwarded-For address
    whenever the peer is loopback -- which the hub's /s/<slug>/ relay always
    is. So by the time a route ran, request.client was the browser on the LAN
    and weblinks.prefix_of()'s "loopback peer" test failed for every real
    visitor: .21 (v1.0.27) served <base href="/"> under /s/asri_test/ and the
    SPA loaded from the hub's root. A curl ON the box passed (its XFF is
    127.0.0.1), which is why a container rehearsal did not see it. The
    rewrite itself is kept: client_ip, local_caller and the https scheme
    behind a tunnel (auth's Secure cookie) depend on it."""
    import os                                                   # noqa: PLC0415
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # noqa: PLC0415
    inner = ProxyHeadersMiddleware(
        app, trusted_hosts=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1,::1"))

    async def outer(scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            scope[PEER_KEY] = (scope.get("client") or ("", 0))[0] or ""
        await inner(scope, receive, send)
    return outer


def socket_peer(request):
    """The address the connection really came from (asgi() recorded it before
    uvicorn's proxy rewrite), else request.client -- a test client, or a
    server started without asgi()."""
    scope = getattr(request, "scope", None) or {}
    if PEER_KEY in scope:
        return scope[PEER_KEY]
    return (request.client.host if getattr(request, "client", None) else "") or ""
