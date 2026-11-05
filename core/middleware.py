# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
middleware.py -- one log line per request, and the id that ties it to its error.

Without this a bug report is "it failed around three": the ring (core/services/
logs.py) only ever held unhandled exceptions, so a 404 that should have been a
200, or a 403 the operator did not expect, left no trace at all. Status codes
are the cheapest diagnosis this product has -- 401 is a session, 403 a role, 404
a route, 502 a device -- and none of them were being written down.

Every request gets a short id. It rides the access line, the error line and the
`X-Request-Id` response header, so "this screen showed an error" becomes one
substring search on the Logs page instead of a timestamp hunt.

Registered LAST in each server, which makes it the OUTERMOST http middleware:
it sees the status the client actually got (including the session wall's 401s,
which are issued by a middleware inside it), and it sets the id before anything
further in can want one.

IT NO LONGER WRITES ITS OWN LINE FOR A FAILURE. The access line and the error
line used to be two separate records for one event, with the route on one and
the cause on the other. Both halves now go through core/services/logs.py keyed
by request id, and exactly one of the two emits:

  * a handled failure (HTTPException, ClientDisconnect) is turned into a
    response INSIDE this middleware, so by the time control returns here the
    error handler's half is already noted and this middleware emits the row;
  * an unhandled exception propagates OUT of this middleware to Starlette's
    ServerErrorMiddleware, which is further out still, so here we only note
    and the 500 handler emits.

Emitting in both places would restore the duplicate; emitting in neither loses
the failure entirely, so the two sites are deliberately asymmetric and each
says which it is.
"""

import time
import uuid

from core.services import logs as logs_service

#: Polled paths that would drown a 2000-line ring: the hub health-checks each
#: master every few seconds, the SPA re-fetches its own data, and a video feed
#: is one request that never ends. Only their *successful* responses are
#: dropped -- a 4xx/5xx on any of these is exactly the line worth keeping.
#: "/api/logs": the Logs page polls itself every few seconds, so an open page
#: filled its own list with its own reads (Tok Aba, 2026-10-14). Its writes
#: (level, clear, purge) each record their own line with the user, so a
#: quiet success loses nothing there.
QUIET_PREFIXES = ("/api/health", "/static/", "/assets/", "/video_feed",
                  "/system-static/", "/api/logs")


# The status -> severity mapping that used to live here now comes from the
# catalog entry the status maps to (logs_service.code_for_status), so a 403
# carries the same level and the same wording whether it arrived as a raise or
# as a status on a response.


def install_access_log(app):
    """Attach the access log. Call once per app, after every other middleware."""

    @app.middleware("http")
    async def _access_log(request, call_next):
        # Scope-backed, so the error handlers below see the same id on the same
        # request even though they build their own Request object.
        request.state.request_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # NOTE ONLY. The 500 handler runs outside this middleware and has
            # the traceback; it emits the single row, picking these request
            # facts up by id. Logging here as well is what produced two lines
            # for one failure.
            logs_service.note_failure(
                request.state.request_id,
                took_ms=(time.perf_counter() - started) * 1000,
                user=getattr(request.state, "user", None),
                session=getattr(request.state, "session_sid", None))
            raise

        took_ms = (time.perf_counter() - started) * 1000
        status = response.status_code
        # `quiet_access` is a route asking for the same, for one request: the
        # Logs page's Clear Log writes its own trace record and an access
        # line beside it would make the "one record left" two.
        if status < 400 and (request.url.path.startswith(QUIET_PREFIXES)
                             or getattr(request.state, "quiet_access", False)):
            # Still hand the id back -- a client reporting a slow page can quote
            # it even when the line itself was too boring to keep.
            response.headers["X-Request-Id"] = request.state.request_id
            return response

        # EMITS. Any failure the app turned into a response was handled inside
        # here, so its half is already waiting to be merged in.
        logs_service.log_request(
            request.method, str(request.url.path), status, took_ms,
            request_id=request.state.request_id,
            user=getattr(request.state, "user", None),
            session=getattr(request.state, "session_sid", None))
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    return app
