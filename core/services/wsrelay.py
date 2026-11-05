# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
wsrelay.py -- a WebSocket through the hub's /s/<slug>/ relay (webproxy.py).

NOT IN USE: the hub closes every /s/ WebSocket upgrade with 1008 until a
master needs one (Pipi, 2026-10-07; webproxy.HubProxy). No page uses a
WebSocket today (the SPA polls). This is kept, and tested, so the day one
does, the relay is one line to turn on -- with an Origin == Host check. The
hub
speaks ASGI to the browser (uvicorn decodes the frames) and plain RFC 6455
to the master: an upgrade request with the browser's cookie and our
forwarded headers, then whole messages each way, masked as a client must.
Ping is answered here, close is passed on with its code.

Needs a WebSocket implementation in the hub's uvicorn (the `websockets` or
`wsproto` package) to accept the browser's side at all; without one uvicorn
refuses the upgrade before this runs, exactly as it would on a master.
No new package is required for the product: nothing depends on this yet.
"""

import asyncio
import base64
import hashlib
import os
import struct

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC11B65"
HEAD_S = 10.0
MAX_MESSAGE = 16 * 1024 * 1024
TEXT, BINARY, CLOSE, PING, PONG = 0x1, 0x2, 0x8, 0x9, 0xA


def accept_for(key):
    """Sec-WebSocket-Accept for a Sec-WebSocket-Key (bytes)."""
    return base64.b64encode(hashlib.sha1(key + GUID).digest())


def _mask(data, key):
    n = len(data)
    if not n:
        return b""
    pad = (key * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(pad, "big")).to_bytes(n, "big")


def encode(opcode, payload, mask=True):
    """One FIN frame. A client's frames are masked (RFC 6455 5.3)."""
    n = len(payload)
    bit = 0x80 if mask else 0
    if n < 126:
        head = struct.pack("!BB", 0x80 | opcode, bit | n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | opcode, bit | 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | opcode, bit | 127, n)
    if not mask:
        return head + payload
    key = os.urandom(4)
    return head + key + _mask(payload, key)


async def read_frame(reader):
    """(fin, opcode, payload) of the next frame."""
    b0, b1 = await reader.readexactly(2)
    n = b1 & 0x7F
    if n == 126:
        n = struct.unpack("!H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack("!Q", await reader.readexactly(8))[0]
    if n > MAX_MESSAGE:
        raise ValueError("frame too large")
    key = await reader.readexactly(4) if b1 & 0x80 else None
    payload = await reader.readexactly(n)
    return bool(b0 & 0x80), b0 & 0x0F, (_mask(payload, key) if key else payload)


def handshake(target, headers, subprotocols, key):
    """The upgrade request to the master, as bytes."""
    lines = [b"GET " + target + b" HTTP/1.1"]
    lines += [n + b": " + v for n, v in headers]
    lines += [b"upgrade: websocket", b"connection: Upgrade",
              b"sec-websocket-key: " + key, b"sec-websocket-version: 13"]
    if subprotocols:
        lines.append(b"sec-websocket-protocol: "
                     + ", ".join(subprotocols).encode("latin-1"))
    return b"\r\n".join(lines) + b"\r\n\r\n"


def parse_head(raw):
    """(status, {name: value}) of the master's answer to the upgrade."""
    lines = raw.split(b"\r\n")
    try:
        status = int(lines[0].split(b" ")[1])
    except (IndexError, ValueError):
        return 0, {}
    out = {}
    for line in lines[1:]:
        name, sep, value = line.partition(b":")
        if sep:
            out[name.strip().lower()] = value.strip()
    return status, out


async def relay(scope, receive, send, port, target, headers):
    """Accept the browser's socket only once the master accepted ours."""
    first = await receive()
    if first.get("type") != "websocket.connect":
        return
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), HEAD_S)
    except (OSError, asyncio.TimeoutError):
        return await send({"type": "websocket.close", "code": 1011})
    key = base64.b64encode(os.urandom(16))
    try:
        writer.write(handshake(target, headers, scope.get("subprotocols") or [], key))
        await writer.drain()
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_S)
        status, head = parse_head(raw)
        if status != 101 or head.get(b"sec-websocket-accept") != accept_for(key):
            return await send({"type": "websocket.close", "code": 1011})
        proto = head.get(b"sec-websocket-protocol")
        await send({"type": "websocket.accept",
                    "subprotocol": proto.decode("latin-1") if proto else None})
        up = asyncio.ensure_future(_up(receive, writer))
        down = asyncio.ensure_future(_down(reader, writer, send))
        done, pending = await asyncio.wait({up, down},
                                           return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError,
            asyncio.LimitOverrunError, ValueError):
        pass
    finally:
        writer.close()


async def _up(receive, writer):
    """Browser -> master."""
    while True:
        msg = await receive()
        if msg["type"] == "websocket.disconnect":
            code = int(msg.get("code") or 1000)
            writer.write(encode(CLOSE, struct.pack("!H", code)))
            await writer.drain()
            return
        if msg.get("text") is not None:
            writer.write(encode(TEXT, msg["text"].encode("utf-8")))
        elif msg.get("bytes") is not None:
            writer.write(encode(BINARY, msg["bytes"]))
        await writer.drain()


async def _down(reader, writer, send):
    """Master -> browser, fragments joined into whole messages."""
    kind, parts = None, []
    while True:
        fin, opcode, payload = await read_frame(reader)
        if opcode == PING:
            writer.write(encode(PONG, payload))
            await writer.drain()
            continue
        if opcode == PONG:
            continue
        if opcode == CLOSE:
            code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1000
            await send({"type": "websocket.close", "code": code})
            return
        if opcode in (TEXT, BINARY):
            kind, parts = opcode, [payload]
        else:
            parts.append(payload)
        if sum(len(p) for p in parts) > MAX_MESSAGE:
            raise ValueError("message too large")
        if fin and kind is not None:
            data = b"".join(parts)
            await send({"type": "websocket.send", **({"text": data.decode("utf-8")}
                        if kind == TEXT else {"bytes": data})})
            kind, parts = None, []
