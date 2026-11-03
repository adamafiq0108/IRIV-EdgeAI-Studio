# -*- coding: utf-8 -*-
"""
tlsserve.py -- the hub's second front door: the same app, on HTTPS, beside
the HTTP one (Settings -> Network -> HTTPS; core/services/https.py says why
and with which certificate).

TLS ENDS AT THE HUB. It is the box's one front door: in reverse-proxy mode
every system is reached through it under /s/<slug>/ and its relay tells the
master the browser was on https (X-Forwarded-Proto), so the master's links
and cookies follow while it stays plain HTTP on loopback. No Caddy or
nginx: nothing new to install, keep alive or update.

A SECOND uvicorn.Server IN THE SAME PROCESS, SAME EVENT LOOP, SAME APP, so
sessions, the session wall, the relay and every route are shared, not
duplicated. It is started from the HTTP server's lifespan, which is also
what stops it, and it never installs signal handlers of its own (SIGTERM
belongs to the HTTP server; uvicorn.run in hub_server.main is unchanged).
Its socket is bound HERE, not by uvicorn: uvicorn answers a failed bind
with sys.exit, which inside this process would take HTTP down with it.
Everything fails closed and quiet -- a missing or damaged certificate, a
port already taken -- the listener stays down, the card and the admins'
Inbox say why, and HTTP carries on.

Enabled, disabled, moved or given a new certificate while the box runs:
apply() starts or stops it, and a new certificate is taken by the very next
connection (the SNI callback hands it the current context; httpsrun.py).
"""

import asyncio
import contextlib
import logging
import re
import socket

import uvicorn

from core.services import https, httpsrun

logger = logging.getLogger(__name__)

STOP_S = 10.0
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+$|^\[[0-9A-Fa-f:.]+\]$")
_LOOPBACK = ("127.0.0.1", "::1", "localhost")


class _Server(uvicorn.Server):
    """A uvicorn server that leaves the process's signals alone."""

    def capture_signals(self):                     # uvicorn >= 0.29
        return contextlib.nullcontext()

    def install_signal_handlers(self):             # uvicorn < 0.29
        return None


def _bind(host, port):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.set_inheritable(True)
    except OSError:
        sock.close()
        raise
    return sock


class Listener:
    """The HTTPS server of one hub app, started and stopped on the setting."""

    def __init__(self, app, config_dir=None, host="0.0.0.0"):
        self.app, self.config_dir, self.host = app, config_dir, host
        self.server = self.task = None

    async def apply(self, at_start=False):
        """Make the listener what the setting says (on, off, on another
        port), then the port 443 redirect. Never raises."""
        cfg = self.config_dir
        try:
            if at_start:                     # key files a crash left (httpsrun.sweep)
                await asyncio.to_thread(httpsrun.sweep, cfg)
            st = await asyncio.to_thread(https.settings, cfg)
            httpsrun.runtime(redirect=st["redirect_http"])
            if self.server and (not st["enabled"]
                                or st["port"] != httpsrun.runtime()["port"]):
                await self.stop()
            if st["enabled"] and not self.server:
                await self._start(st["port"])
            if at_start and st["enabled"] and not self.server:
                _notify(httpsrun.runtime()["error"], cfg)
            note = await asyncio.to_thread(httpsrun.ensure_443, cfg)
            if note:
                logger.warning("[HTTPS] %s", note)
        except Exception:                                    # noqa: BLE001
            logger.exception("[HTTPS] could not apply the HTTPS setting")

    async def _start(self, port):
        try:
            ctx = await asyncio.to_thread(httpsrun.prepare, self.config_dir)
        except Exception as exc:                             # noqa: BLE001
            why = (str(exc) if isinstance(exc, https.HttpsError) else
                   "the certificate could not be loaded (%s)" % exc.__class__.__name__)
            httpsrun.runtime(listening=False, port=port, error=why)
            logger.warning("[HTTPS] not started: %s", why)
            return
        ctx.sni_callback = self._sni
        try:
            sock = _bind(self.host, port)
        except OSError as exc:
            why = "port %d could not be opened (%s)" % (port, exc.strerror or exc)
            httpsrun.runtime(listening=False, port=port, error=why)
            logger.warning("[HTTPS] not started: %s", why)
            return
        config = uvicorn.Config(self.app, host=self.host, port=port, log_config=None,
                                access_log=False, lifespan="off",
                                timeout_graceful_shutdown=5)
        config.load()
        config.ssl = ctx                 # ours: TLS 1.2+, AEAD suites (tlscert)
        self.server = _Server(config)
        httpsrun.runtime(listening=True, port=port, error="")
        self.task = asyncio.ensure_future(self._serve(self.server, sock))
        logger.info("[HTTPS] answering on port %d", port)

    async def _serve(self, server, sock):
        try:
            await server.serve(sockets=[sock])
        except asyncio.CancelledError:
            raise
        except BaseException as exc:      # uvicorn's sys.exit included  # noqa: BLE001
            logger.error("[HTTPS] the listener stopped: %r", exc)
            httpsrun.runtime(error="the HTTPS listener stopped unexpectedly")
        finally:
            httpsrun.runtime(listening=False)
            if self.server is server:
                self.server = self.task = None

    async def stop(self):
        server, task = self.server, self.task
        if server is None:
            return
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(task), STOP_S)
        except asyncio.TimeoutError:
            server.force_exit = True
            task.cancel()
        except Exception:                                    # noqa: BLE001
            pass
        self.server = self.task = None
        httpsrun.runtime(listening=False)
        logger.info("[HTTPS] stopped")

    def _sni(self, sslobj, server_name, ctx):
        """Every handshake: the current certificate (httpsrun.live_context
        re-checks the box's names at most every CHECK_EVERY_S)."""
        try:
            fresh = httpsrun.live_context(self.config_dir, log=logger.info)
            if fresh is not None and fresh is not sslobj.context:
                sslobj.context = fresh
        except Exception:                                    # noqa: BLE001
            logger.exception("[HTTPS] certificate check failed")
        return None


def _notify(why, config_dir):
    if not why:
        return
    try:
        from core.services import inbox                      # noqa: PLC0415
        inbox.notify_admins("https_not_started", "HTTPS not available",
                            "HTTPS is switched on but did not start: %s. The "
                            "dashboard still answers on HTTP." % why, config_dir,
                            page="settings", tab="network")
    except Exception:                                        # noqa: BLE001
        logger.exception("[HTTPS] could not post the notice")


def wants_https(scope):
    """Is this a BROWSER opening a PAGE over plain HTTP from another
    machine? Only those are redirected: never an API call (an operator's
    script, the OTA health gate), never an asset, never this box itself
    (the kiosk browser does not trust the root), never a request a local
    hop forwarded (a tunnel, which knows its own scheme)."""
    if scope.get("type") != "http" or scope.get("scheme") != "http":
        return False
    if scope.get("method") not in ("GET", "HEAD"):
        return False
    if ((scope.get("client") or ("", 0))[0] or "") in _LOOPBACK:
        return False
    path = scope.get("path") or "/"
    from core.services import webproxy                       # noqa: PLC0415
    hit = webproxy.split(path)
    rest = hit[1] if hit else path
    if rest == "/api" or rest.startswith("/api/"):
        return False
    headers = dict(scope.get("headers") or [])
    if b"x-forwarded-for" in headers or b"x-forwarded-proto" in headers:
        return False
    mode = headers.get(b"sec-fetch-mode")
    if mode is not None:
        return mode == b"navigate"
    return b"text/html" in headers.get(b"accept", b"")


def sends_secret(scope):
    """Is this a request over plain HTTP, from another machine, that carries
    (or is) a credential: a session header, a Bearer token, or a write to
    /api/auth/ (sign-in, token sign-in, password change, reset)? Those are
    refused when HTTPS is the way in, rather than redirected -- a redirect
    would only run after the secret had already crossed the network.
    Loopback and requests a local hop forwarded are exempt, as for the
    redirect (wants_https)."""
    if scope.get("type") != "http" or scope.get("scheme") != "http":
        return False
    if ((scope.get("client") or ("", 0))[0] or "") in _LOOPBACK:
        return False
    headers = dict(scope.get("headers") or [])
    if b"x-forwarded-for" in headers or b"x-forwarded-proto" in headers:
        return False
    if b"authorization" in headers or b"x-session" in headers:
        return True
    from core.services import webproxy                       # noqa: PLC0415
    path = scope.get("path") or "/"
    hit = webproxy.split(path)
    rest = hit[1] if hit else path
    return (rest.startswith("/api/auth/") and rest != "/api/auth/logout"
            and scope.get("method") in ("POST", "PUT", "PATCH", "DELETE"))


HSTS = (b"strict-transport-security", b"max-age=2592000")


def target(scope):
    """Where wants_https() sends the browser, or None (the setting off,
    HTTPS not answering, a Host header that is not a plain host)."""
    raw = (dict(scope.get("headers") or []).get(b"host") or b"").decode("latin-1")
    host = raw.rsplit("]", 1)[0] + "]" if raw.startswith("[") else raw.split(":")[0]
    if not _HOST_RE.match(host):
        return None
    path = (scope.get("raw_path") or (scope.get("path") or "/").encode("utf-8"))
    return httpsrun.redirect_target(host, path.decode("latin-1"),
                                    (scope.get("query_string") or b"").decode("latin-1"))


class HttpsRedirect:
    """Outermost on the hub: sends browsers to HTTPS, refuses a sign-in or a
    token over plain HTTP, and pins browsers to HTTPS (HSTS) -- all while
    HTTPS is the way in (httpsrun.required)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if wants_https(scope):
            go = target(scope)
            if go:
                await send({"type": "http.response.start", "status": 307, "headers": [
                    (b"location", go.encode("latin-1")), (b"content-length", b"0"),
                    (b"cache-control", b"no-store")]})
                return await send({"type": "http.response.body", "body": b""})
        if httpsrun.required():
            if sends_secret(scope):
                body = (b'{"ok":false,"error":"Sign in and API tokens need HTTPS on '
                        b'this box. Use https:// instead of http://."}')
                await send({"type": "http.response.start", "status": 426, "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"upgrade", b"TLS/1.2"), (b"connection", b"Upgrade"),
                    (b"cache-control", b"no-store")]})
                return await send({"type": "http.response.body", "body": body})
            if scope.get("type") == "http" and scope.get("scheme") == "https":
                # Browsers are told to keep using HTTPS (30 days): only while
                # HTTPS is the way in, so a box set back to plain HTTP is
                # never trapped behind a stale pin.
                async def hsts(message):
                    if message["type"] == "http.response.start":
                        message = dict(message)
                        message["headers"] = [*message.get("headers", []), HSTS]
                    await send(message)
                return await self.app(scope, receive, hsts)
        return await self.app(scope, receive, send)


def install(app, config_dir=None, host="0.0.0.0"):
    """Hub only: the redirect middleware (outermost, so /s/<slug>/ pages
    are covered too) and the listener, run from the HTTP server's lifespan."""
    listener = Listener(app, config_dir, host)
    app.state.https = listener
    app.add_middleware(HttpsRedirect)
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with inner(a) as state:
            # In the background: HTTP must not wait for a key or for sudo
            # (the OTA health gate polls it the moment the hub restarts).
            starting = asyncio.ensure_future(listener.apply(at_start=True))
            try:
                yield state
            finally:
                if not starting.done():
                    starting.cancel()
                await listener.stop()
    app.router.lifespan_context = lifespan
    return listener
