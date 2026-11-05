# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
change_log.py -- every change a signed-in person makes, in the audit log.

A route that writes its own audit row says the most about what happened
("builder.publish", "logs.clear"), but only about a quarter of the routes that
change something ever did. This middleware is the floor under all of them:
every POST, PUT, PATCH and DELETE that a signed-in account makes, on the hub
and on each master, becomes one `change` row -- who, from where, which
operation, the status it ended with, and what was sent (secrets masked).

Two rules keep the log honest without making it noise:

  * A request whose route already wrote an audit row is NOT recorded again.
    audit.record() marks the request (mark_recorded) and this layer looks
    before writing, so a login is still one `login` row, not two.
  * Requests that only READ or preview are skipped (SKIP below): a preview
    fires on every keystroke and a CSV export changes nothing.

Pure ASGI on purpose: the body is copied as it streams past, never consumed,
so uploads and every route downstream behave exactly as before, whatever the
Starlette version. Only JSON and form bodies are copied, and only up to
BODY_CAP; a file upload is recorded by its route and size, not its content.
"""
import asyncio
import contextvars
import json
import re

from core.services import audit as audit_service

#: Methods that change something.
CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

#: POST routes that only read, preview or check -- nothing on the box changes.
SKIP = re.compile(
    r"(/preview$|^/api/auth/keepalive$|^/api/inbox/read$|^/api/datasets/sql$"
    r"|/csv$|/table-csv$|/export$|^/api/detector/footage/download$"
    r"|^/api/functions/validate-expr$|/scan$|^/api/remote/check$)")

#: How much of a request body is kept for the summary.
BODY_CAP = 16 * 1024

#: Field names whose VALUE is never written down, whatever it is.
#: A bare "key" is not here: it is how a table, a setting or a row is named
#: far more often than it is a secret; "api_key", "secret_key", "ap_key" are.
SECRET = re.compile(r"pass|secret|token|psk|api_?key|.+_key$|cookie|credential|private|signature|otp",
                    re.I)

#: The row the request is building, shared with audit.record() through the
#: task's context: a list, so a write from a worker thread still reaches it.
_MARK = contextvars.ContextVar("change_log_mark", default=None)


def mark_recorded():
    """Called by audit.record(): this request already has its own row."""
    box = _MARK.get()
    if box is not None:
        box[0] = True


def _summarise(value, depth=0):
    """A short, safe picture of what was sent: secrets masked, long strings and
    lists cut, nesting bounded."""
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 25:
                out["..."] = f"{len(value) - 25} more"
                break
            out[k] = "***" if SECRET.search(str(k)) and v not in (None, "") else _summarise(v, depth + 1)
        return out
    if isinstance(value, list):
        items = [_summarise(v, depth + 1) for v in value[:10]]
        if len(value) > 10:
            items.append(f"... {len(value) - 10} more")
        return items
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    return value


def _sent(ctype, body):
    if not body:
        return None
    try:
        if "json" in ctype:
            return _summarise(json.loads(body.decode("utf-8")))
        if "x-www-form-urlencoded" in ctype:
            from urllib.parse import parse_qsl                   # noqa: PLC0415
            return _summarise(dict(parse_qsl(body.decode("utf-8"))))
    except (ValueError, UnicodeDecodeError):
        return None
    return None


def _client_ip(scope, headers):
    fwd = headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else None


class ChangeLog:
    """ASGI middleware: one audit row per change a signed-in account makes."""

    def __init__(self, app, config_dir=None):
        self.app = app
        self.config_dir = config_dir

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http" or scope.get("method") not in CHANGING
                or SKIP.search(scope.get("path") or "")):
            return await self.app(scope, receive, send)

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers") or []}
        ctype = headers.get("content-type", "")
        copy = "json" in ctype or "x-www-form-urlencoded" in ctype
        body = bytearray()
        size = [0]
        status = [None]

        async def receive_copy():
            msg = await receive()
            if msg.get("type") == "http.request":
                chunk = msg.get("body", b"")
                size[0] += len(chunk)
                if copy and len(body) < BODY_CAP:
                    body.extend(chunk[:BODY_CAP - len(body)])
            return msg

        async def send_watch(msg):
            if msg.get("type") == "http.response.start":
                status[0] = msg.get("status")
            await send(msg)

        box = [False]
        token = _MARK.set(box)
        try:
            await self.app(scope, receive_copy, send_watch)
        finally:
            _MARK.reset(token)
            user = (scope.get("state") or {}).get("user")
            if user and not box[0] and status[0] is not None:
                route = scope.get("route")
                detail = {"what": getattr(route, "name", None) or "",
                          "method": scope.get("method"),
                          "path": scope.get("path"),
                          "status": status[0]}
                sent = _sent(ctype, bytes(body)) if copy else None
                if sent not in (None, {}, []):
                    detail["sent"] = sent
                elif size[0] and not copy:
                    detail["upload_bytes"] = size[0]
                result = ("ok" if status[0] < 400
                          else "refused" if status[0] in (401, 403) else "failed")
                try:
                    await asyncio.to_thread(
                        audit_service.record, "change", username=user, result=result,
                        source_ip=_client_ip(scope, headers), detail=detail,
                        config_dir=self.config_dir)
                except Exception:                            # noqa: BLE001
                    pass       # best-effort, like every audit write
