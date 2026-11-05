# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
webproxy.py -- in reverse-proxy mode the hub relays /s/<slug>/... to that
system's master on 127.0.0.1 (core/services/webaccess.py says why the mode
exists, weblinks.py how the links follow it).

WHY THE HUB, not nginx or a separate process: nothing new to install or
keep alive, the hub already knows every system's port, and the relay is a
byte pipe -- it does not render a master's pages, hold its sessions or read
its data. The master stays the authority for its own system: the session
wall, the per-system grant, CSRF and the setup gate all run THERE, on the
real request. This sits OUTSIDE the hub's own middleware for /s/ (installed
last, so outermost), which is why the hub's wall never sees these paths.

A PIPE, NOT A BUFFER. Live video is one response that never ends and a
model upload is hundreds of megabytes, so both directions stream: request
body chunks are written as they arrive, response chunks are sent as they
are read (h11, which uvicorn already depends on, over asyncio streams), and
a browser that goes away closes the master's side at once. No thread per
stream: a hub that parked a thread for every MJPEG feed would run out of
them with a few tabs open.

WHAT CHANGES ON THE WAY: hop-by-hop headers are dropped; X-Forwarded-For
(the real client), -Proto, -Host and -Prefix (/s/<slug>) are set, never
passed through from the browser; the Host header is kept, so the master
builds links and checks Origin against the address the browser used; a
Location the master answers with ("/login") gets the prefix. Paths a
master serves to local processes only are refused here (LOCAL_ONLY), and
the master refuses any forwarded request for them too (net.local_caller).
"""

import asyncio
import json
import logging
import urllib.parse

import h11

from core import net
from core.services import webaccess, weblinks

logger = logging.getLogger(__name__)

#: A master answers these to processes on this box only; a relayed request
#: comes from 127.0.0.1 too, so the relay must never carry one.
LOCAL_ONLY = ("/api/workflow/query-value", "/api/workflow/query-csv")
HOP = frozenset((b"connection", b"keep-alive", b"proxy-authenticate",
                 b"proxy-authorization", b"te", b"trailer", b"trailers",
                 b"transfer-encoding", b"upgrade", b"expect",
                 b"proxy-connection"))
#: Set by the relay itself; a browser's own copy is dropped, never trusted.
OURS = frozenset((b"x-forwarded-for", b"x-forwarded-proto", b"x-forwarded-host",
                  b"x-forwarded-prefix", b"forwarded", b"x-real-ip"))
CONNECT_S = 5.0
#: How long a master may take to START answering. Not the body: a stream
#: has no end.
HEAD_S = 120.0
CHUNK = 65536


def split(path):
    """(slug, rest) for /s/<slug>[/rest], else None."""
    if not path.startswith(weblinks.PREFIX):
        return None
    slug, _, rest = path[len(weblinks.PREFIX):].partition("/")
    if not weblinks.SLUG.match(slug):
        return None
    return slug, "/" + rest


def refused(rest):
    """Is `rest` (decoded, as the master will route it) local-only?"""
    clean = "/" + rest.lstrip("/")
    return any(clean == p or clean.startswith(p + "/") for p in LOCAL_ONLY)


def upstream_target(scope, slug):
    """The path + query the master is asked for, raw bytes as received, or
    None. The RAW path must itself begin /s/<slug> followed by "/" or its
    end -- the prefix that is sliced off is the one that was checked, so an
    escaped slug ("/s/de%6do/") cannot route one way and slice another
    (Pipi L3)."""
    raw = scope.get("raw_path") or scope["path"].encode("utf-8")
    head = (weblinks.PREFIX + slug).encode("ascii")
    if not raw.startswith(head) or raw[len(head):len(head) + 1] not in (b"", b"/"):
        return None
    raw = raw[len(head):] or b"/"
    query = scope.get("query_string") or b""
    return raw + (b"?" + query if query else b"")


def forward_headers(scope, slug):
    """The request's headers for the master, ours added."""
    out, host, incoming_xff, incoming_proto = [], b"", b"", b""
    for name, value in scope.get("headers") or []:
        name = name.lower()
        if name == b"host":
            host = value
        if name == b"x-forwarded-for":
            incoming_xff = value
        if name == b"x-forwarded-proto":
            incoming_proto = value
        if (name in HOP or name in OURS or name == b"host"
                or name.startswith(b"sec-websocket-")):
            continue                       # the ws relay writes its own
        out.append((name, value))
    peer = (scope.get("client") or ("", 0))[0] or ""
    client = peer
    # Only a hop on this machine (a TLS terminator, a tunnel) may tell us who
    # the client is, and only in the entry IT appended: the last one. The
    # ones before it are the client's own words (Pipi M1, core/net.py).
    if peer in weblinks._LOOPBACK and incoming_xff:
        client = incoming_xff.split(b",")[-1].strip().decode("latin-1")
    proto = scope.get("scheme", "http").replace("ws", "http")
    if peer in weblinks._LOOPBACK and incoming_proto:
        proto = incoming_proto.split(b",")[0].strip().decode("latin-1")
    out = [(b"host", host or b"127.0.0.1")] + out + [
        (b"x-forwarded-for", client.encode("latin-1")),
        (b"x-forwarded-proto", proto.encode("latin-1")),
        (b"x-forwarded-host", host),
        (b"x-forwarded-prefix", f"{weblinks.PREFIX}{slug}".encode("ascii")),
    ]
    return [(n, v) for n, v in out if v]


def response_headers(headers, slug, host=b""):
    """The master's answer headers for the browser: hop-by-hop dropped, a
    Location on this box given the prefix -- root-relative ("/login"), or
    absolute on the Host the browser used, which is what Starlette's
    trailing-slash redirect builds (http://<box>/api/x without /s/<slug>
    would land on the hub)."""
    prefix = f"{weblinks.PREFIX}{slug}".encode("ascii")
    out = []
    for name, value in headers:
        name = name.lower()
        if name in HOP:
            continue
        if name == b"location":
            value = _location(value, prefix, host)
        out.append((name, value))
    return out


def _location(value, prefix, host):
    if value.startswith(b"/") and not value.startswith(b"//"):
        return prefix + value
    try:
        parts = urllib.parse.urlsplit(value.decode("latin-1"))
    except ValueError:
        return value
    if (parts.scheme in ("http", "https") and host
            and parts.netloc.lower() == host.decode("latin-1").lower()
            and not (parts.path + "/").startswith(prefix.decode("ascii") + "/")):
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, prefix.decode("ascii") + (parts.path or "/"),
             parts.query, parts.fragment)).encode("latin-1")
    return value


def port_address(scope, hit, config_dir=None):
    """PORT mode, a stale /s/<slug>/ bookmark on the hub's port: that page on
    the system's own port (else the hub's SPA opens on a path it lacks), or
    None: not a GET, unknown system. Port 80 has no table in port mode."""
    port = weblinks.master_port(hit[0], config_dir)
    if port is None or scope.get("method") not in ("GET", "HEAD"):   # a ws has none
        return None
    from starlette.requests import Request                  # noqa: PLC0415
    host = net.request_host(Request(scope)) or "127.0.0.1"
    query = (scope.get("query_string") or b"").decode("latin-1")
    path = urllib.parse.quote(hit[1], safe="/:@!$&'()*+,;=~")
    return f"http://{host}:{port}{path}" + (f"?{query}" if query else "")


async def _redirect(send, location):
    await send({"type": "http.response.start", "status": 307, "headers": [
        (b"location", location.encode("latin-1")), (b"content-length", b"0")]})
    await send({"type": "http.response.body", "body": b""})


async def _json(send, status, error):
    body = json.dumps({"ok": False, "error": error}).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii")),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


async def _next(conn, reader):
    """The next h11 event, reading as much as it needs."""
    while True:
        event = conn.next_event()
        if event is not h11.NEED_DATA:
            return event
        conn.receive_data(await reader.read(CHUNK))


async def relay_http(scope, receive, send, port, slug):
    """One request to the master on `port`, streamed both ways."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), CONNECT_S)
    except (OSError, asyncio.TimeoutError):
        logger.warning("[PROXY] %s: nothing answers on port %s", slug, port)
        return await _json(send, 502, f"the system \"{slug}\" is not running")
    conn = h11.Connection(h11.CLIENT)
    headers = forward_headers(scope, slug)
    host = next((v for n, v in headers if n == b"host"), b"")
    names = {n for n, _ in headers}
    framed = b"content-length" in names
    has_body = any(n == b"transfer-encoding" for n, _ in scope.get("headers") or [])
    if has_body and not framed:
        headers.append((b"transfer-encoding", b"chunked"))
    headers.append((b"connection", b"close"))
    try:
        writer.write(conn.send(h11.Request(method=scope["method"],
                                           target=upstream_target(scope, slug),
                                           headers=headers)))
        while True:                              # the request body, as it comes
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            if msg.get("body") and (framed or has_body):
                writer.write(conn.send(h11.Data(data=msg["body"])))
                await writer.drain()
            if not msg.get("more_body"):
                break
        writer.write(conn.send(h11.EndOfMessage()))
        await writer.drain()
        try:
            event = await asyncio.wait_for(_next(conn, reader), HEAD_S)
            while isinstance(event, h11.InformationalResponse):
                event = await asyncio.wait_for(_next(conn, reader), HEAD_S)
        except asyncio.TimeoutError:
            return await _json(send, 504, f"the system \"{slug}\" did not answer in time")
        if not isinstance(event, h11.Response):
            return await _json(send, 502, f"the system \"{slug}\" closed the connection")
        await send({"type": "http.response.start", "status": event.status_code,
                    "headers": response_headers(event.headers, slug, host)})
        pump = asyncio.ensure_future(_pump(conn, reader, send))
        gone = asyncio.ensure_future(_until_disconnect(receive))
        done, pending = await asyncio.wait({pump, gone},
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            task.result()
    except (h11.ProtocolError, ConnectionError) as exc:
        logger.warning("[PROXY] %s: %s", slug, exc)
    finally:
        writer.close()


async def _pump(conn, reader, send):
    while True:
        event = await _next(conn, reader)
        if isinstance(event, h11.Data):
            await send({"type": "http.response.body", "body": bytes(event.data),
                        "more_body": True})
        elif isinstance(event, (h11.EndOfMessage, h11.ConnectionClosed)):
            break
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _until_disconnect(receive):
    while (await receive())["type"] != "http.disconnect":
        pass


class HubProxy:
    """ASGI middleware on the hub: /s/<slug>/... goes to that master while
    the box is in proxy mode; everything else (and every path in port mode,
    so port mode is exactly what it was) goes to the hub's own app."""

    def __init__(self, app, config_dir=None):
        self.app = app
        self.config_dir = config_dir

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        hit = split(scope.get("path") or "") if kind in ("http", "websocket") else None
        if hit is None:
            return await self.app(scope, receive, send)
        if webaccess.mode(self.config_dir) != webaccess.PROXY:
            go = port_address(scope, hit, self.config_dir)
            return await (_redirect(send, go) if go else self.app(scope, receive, send))
        slug, rest = hit
        if kind == "websocket":
            # Closed until a master needs one (Pipi): no page uses a
            # WebSocket, and a relay nobody exercises is surface for free.
            # wsrelay.relay is kept, tested, for the day one does.
            return await send({"type": "websocket.close", "code": 1008})
        port = weblinks.master_port(slug, self.config_dir)
        if port is None:
            return await _json(send, 404, "no such system on this box")
        if upstream_target(scope, slug) is None:
            return await _json(send, 400, "malformed system address")
        if refused(rest):
            return await _json(send, 403, "this path is served to this box only")
        return await relay_http(scope, receive, send, port, slug)
