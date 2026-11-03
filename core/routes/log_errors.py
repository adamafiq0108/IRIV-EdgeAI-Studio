# -*- coding: utf-8 -*-
"""
log_errors.py -- the app-wide error handlers behind the Logs page.

Lifted out of core/routes/logs.py (which re-exports install_error_handlers,
the name hub_server, master_server and the tests know) so that file stays
under its line ceiling. No routes here; nothing changed in the move.

`install_error_handlers` is the other half of "good error handling": every
unhandled exception becomes one recorded line and one honest JSON body, instead
of a 500 whose only trace is on a terminal nobody is watching.

THE HANDLERS NO LONGER WRITE THEIR OWN LINE. A failing request used to produce
two records -- this module's error line and the access middleware's access line
-- and the operator saw the same event twice with the cause in neither. The
handlers now NOTE their half against the request id and let whichever of the
two runs last emit the single row. Which one that is depends on where Starlette
hangs the handler, and the split is not obvious:

    ServerErrorMiddleware        <- the Exception handler lives here, OUTSIDE
      access log middleware      <- core/middleware.py
        ExceptionMiddleware      <- HTTPException and ClientDisconnect, INSIDE
          the route

So `_http_error` and `_client_gone` note and return; the access middleware
emits. `_unhandled` is the other way round: the access middleware notes on its
way past and this module emits. Both paths fall back to emitting directly when
there is no request id, so a server without the access middleware installed
(the tests, a bare embedded app) still records the failure rather than
silently swallowing it.
"""

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from core import net
from core.services import log_catalog as catalog
from core.services import power_state
from core.services import logs as logs_service


def install_error_handlers(app):
    """Return consistent JSON errors and record unexpected failures."""

    def _rid(request):
        return getattr(request.state, "request_id", None)

    def _who(request):
        return (getattr(request.state, "user", None) or "",
                getattr(request.state, "session_sid", "") or "")

    def _body(exc, rid, entry):
        """The JSON the browser gets. The catalog's wording, the id it can
        quote to support, and nothing technical -- `error` is rendered into a
        toast, and an errno in a toast helps nobody standing at a machine."""
        return {"ok": False, "error": entry.message, "event_id": entry.event_id,
                "action": entry.action, "rid": rid}

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        rid = _rid(request)
        user, session = _who(request)
        # Raised through catalog.http_error, so the code and the technical
        # cause came with it; anything else is mapped from its status.
        code = getattr(exc, "event_code", None)
        entry = catalog.entry_of(code) if code else \
            catalog.entry_of(logs_service.code_for_status(exc.status_code))
        detail = getattr(exc, "event_cause", None)
        if detail is None and not code:
            # A legacy raise site: its `detail` IS the technical text. It
            # keeps going to the browser for now (converting all 344 of them
            # is a follow-up) but it is also the only cause the record has.
            detail = str(exc.detail)
        # A raise site that chose a level handled the failure itself: one
        # line at that level, no traceback (see catalog.http_error).
        level = getattr(exc, "event_level", None)
        facts = {"code": entry.code, "detail": detail, "user": user,
                 "session": session, "meta": getattr(exc, "event_meta", None),
                 "level": level,
                 "exc": exc if exc.status_code >= 500 and not level else None}
        if rid:
            logs_service.note_failure(rid, **facts)
        else:
            logs_service.log_request(request.method, str(request.url.path),
                                     exc.status_code, code=entry.code,
                                     detail=detail, user=user, session=session,
                                     level=level)
        headers = dict(getattr(exc, "headers", None) or {})
        if rid:
            headers["X-Request-Id"] = rid
        # Legacy raise sites still answer with their own wording; a
        # catalogued one answers with the entry's. Same shape either way, so
        # the frontend does not need to know which it got.
        body = _body(exc, rid, entry) if code else \
            {"ok": False, "error": exc.detail, "event_id": entry.event_id,
             "action": entry.action, "rid": rid}
        if getattr(exc, "event_details", None):
            body["details"] = exc.event_details
        return JSONResponse(status_code=exc.status_code, content=body,
                            headers=headers or None)

    @app.exception_handler(power_state.StoppingError)
    async def _still_stopping(request: Request, exc: power_state.StoppingError):
        # A Start while the previous run is still releasing the camera and
        # the chip (core/services/power_state.py). A conflict, not a fault:
        # one line in the access log, no traceback, and the words the
        # service chose go straight to the toast.
        rid = _rid(request)
        user, session = _who(request)
        logs_service.log_request(request.method, str(request.url.path), 409,
                                 request_id=rid, user=user, session=session,
                                 detail=str(exc))
        return JSONResponse(status_code=409,
                            content={"ok": False, "error": str(exc), "rid": rid},
                            headers={"X-Request-Id": rid} if rid else None)

    @app.exception_handler(ClientDisconnect)
    async def _client_gone(request: Request, exc: ClientDisconnect):
        rid = _rid(request)
        user, session = _who(request)
        if rid:
            logs_service.note_failure(rid, code="API_CLIENT_DISCONNECTED",
                                      detail="the client closed the connection",
                                      user=user, session=session)
        else:
            logs_service.log_request(request.method, str(request.url.path), 499,
                                     code="API_CLIENT_DISCONNECTED",
                                     user=user, session=session)
        entry = catalog.CATALOG["API_CLIENT_DISCONNECTED"]
        return JSONResponse(status_code=499,        # nginx's "client closed"
                            content=_body(exc, rid, entry))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # OUTSIDE the access middleware, so this one emits rather than notes:
        # the access log has already been past and left its half behind.
        rid = _rid(request)
        user, session = _who(request)
        logs_service.log_request(
            request.method, str(request.url.path), 500, request_id=rid,
            user=user, session=session, code="API_UNHANDLED", exc=exc,
            detail=f"{type(exc).__name__}: {exc}",
            client=net.client_ip(request))
        entry = catalog.CATALOG["API_UNHANDLED"]
        return JSONResponse(status_code=500, content=_body(exc, rid, entry),
                            headers={"X-Request-Id": rid} if rid else None)
