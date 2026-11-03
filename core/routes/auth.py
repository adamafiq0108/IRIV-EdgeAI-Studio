# -*- coding: utf-8 -*-
"""
auth.py -- Login, logout, and user management endpoints.

The require() middleware factory is applied by hub_server and master_server:
pages bounce to /login, APIs get a 401. User management is admin-only and
lives behind /api/users -- the Settings page's Users tab drives it.
"""

import asyncio
import threading
import urllib.parse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core import config, net
# The scheme rules (TLS, which cookie, CSRF), re-exported under the names
# this file and the tests have always used (authtls.py, Pipi M3/L1).
from core.routes.authtls import (_cookie_name, _cross_site, _is_https,  # noqa: F401
                                 _session_cookie, end_both_cookies)
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import caps as caps_service
from core.services import email as email_service
from core.services import monitor_account
from core.services import hubport
from core.services import sysconfig as sysconfig_service
from core.services import inbox as inbox_service
from core.services import log_catalog as catalog
from core.services import panel_reset as panelreset_service
from core.services import nav as nav_service
from core.services import neighmac
from core.services import ratelimit as ratelimit_service
from core.services import reset_link as resetlink_service
from core.services import reset_requests as resetreq_service

router = APIRouter()


def _client_ip(request):
    """Who is calling, as an address that is at least AN address.

    X-Forwarded-For used to be returned verbatim whenever present. Two
    holes: the rate limiters key on this, so rotating the header rotated
    the bucket; and the forgot flow now writes it into a mail sent from the
    box's own sender, so a header carrying a paragraph of text was a
    phishing mail to master's real mailbox. The header is honoured only
    when the direct peer is this box (the hub proxying to a master), its LAST hop
    is taken, and it has to parse as an IP or the peer's address is used.
    """
    # The last hop only, through the one helper (core/net.py client_ip).
    return net.client_ip(request)


def _device(request):
    """Coarse "Chrome · Windows" words for the sessions list.

    Deliberately lossy: the raw User-Agent is a fingerprint and is never
    stored -- two words are enough for an operator to recognise which of
    their own logins a row is.
    """
    ua = request.headers.get("User-Agent", "")
    browser = next((name for probe, name in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
        ("Chrome/", "Chrome"), ("Safari/", "Safari"), ("curl/", "curl"),
    ) if probe in ua), "Browser")
    system = next((name for probe, name in (
        ("Windows", "Windows"), ("Android", "Android"),
        ("iPhone", "iPhone"), ("iPad", "iPad"), ("Mac OS", "macOS"),
        ("CrOS", "ChromeOS"), ("Linux", "Linux"),
    ) if probe in ua), "")
    return f"{browser} · {system}" if system else browser


def _device_cookie_name(request):
    """The registered-device cookie's name for this request's scheme
    (auth_service.DEVICE_COOKIE_SECURE says why there are two)."""
    return (auth_service.DEVICE_COOKIE_SECURE if _is_https(request)
            else auth_service.DEVICE_COOKIE)


def _device_cookie(request):
    """The device id this browser presents, or "" (shape-checked only;
    whether it is registered is the service's question)."""
    raw = (getattr(request, "cookies", None) or {}).get(_device_cookie_name(request))
    return raw if auth_service.valid_device_id(raw) else ""


def _set_device_cookie(response, request, raw):
    """HttpOnly (no script ever reads it), SameSite=Lax (the session
    cookie's policy), Secure exactly when the page is on TLS, Path=/ and no
    Domain: a host-only cookie, which the browser sends to every PORT of
    this host -- the hub and every master share it, as they share the
    session cookie -- but never to another name for the box."""
    response.set_cookie(
        _device_cookie_name(request), raw,
        httponly=True, samesite="lax", secure=_is_https(request), path="/",
        max_age=auth_service.DEVICE_COOKIE_MAX_AGE_S)


def _clear_device_cookie(response, request):
    response.delete_cookie(_device_cookie_name(request), path="/",
                           httponly=True, samesite="lax",
                           secure=_is_https(request))


#: device hash -> when this process last re-sent its cookie. The cookie is
#: refreshed on use, but once a day is "on use" enough for a 400-day cookie.
_DEVICE_REFRESHED = {}
DEVICE_REFRESH_S = 24 * 3600


def _device_cookie_upkeep(request, response, config_dir):
    """At every gated contact: a cookie naming a removed or expired device is
    cleared (that browser must sign in and register again), and a live one is
    refreshed now and then so an active browser never ages out of it."""
    raw = _device_cookie(request)
    name = _device_cookie_name(request)
    if not raw:
        if (getattr(request, "cookies", None) or {}).get(name):
            _clear_device_cookie(response, request)        # malformed value
        return
    if not auth_service.device_known(raw, config_dir):
        _clear_device_cookie(response, request)
        return
    import time as _time                                    # noqa: PLC0415
    key, now = auth_service.device_hash(raw), _time.time()
    if now - _DEVICE_REFRESHED.get(key, 0) >= DEVICE_REFRESH_S:
        if len(_DEVICE_REFRESHED) > 4096:
            _DEVICE_REFRESHED.clear()
        _DEVICE_REFRESHED[key] = now
        _set_device_cookie(response, request, raw)


def _local_hosts():
    """Names/addresses that are genuinely THIS board.

    Cheap and cached by the OS; used to decide whether a Host header may be
    trusted in an emailed link.
    """
    import socket
    out = {"localhost", "127.0.0.1", "::1", "[::1]"}
    try:
        name = socket.gethostname()
        out.add(name.lower())
        out.add(f"{name.lower()}.local")
        for info in socket.getaddrinfo(name, None):
            out.add(str(info[4][0]))
    except OSError:
        pass
    return out


def _reset_base(request):
    """Base URL for an emailed reset link -- NOT the raw Host header.

    Every other link in the product deliberately follows the host the operator
    typed (core/net.py), because DHCP moves the board. An emailed password
    reset is the one place that rule is dangerous: the Host header is supplied
    by whoever calls /api/auth/forgot, so a stranger could have the reset link
    for THIS box's admin built to point at their own domain, and the operator
    who clicks it hands over the token.

    So the host is honoured only when it is provably this board (or a host the
    deployment configured); anything else falls back to the address the socket
    is actually reachable on.
    """
    configured = str((config.auth(request.app.state.config_dir)
                      .get("session", {}) or {}).get("public_url") or "").strip()
    if configured:
        return configured.rstrip("/")
    host = net.request_host(request) or ""
    port = request.url.port
    if host.strip("[]").lower() in _local_hosts():
        return f"{request.url.scheme}://{host}" + (f":{port}" if port else "")
    # Untrusted Host: rebuild from the socket this process is answering on.
    server = (request.scope.get("server") or ("127.0.0.1", port))
    return f"{request.url.scheme}://{server[0]}" + (f":{server[1]}" if server[1] else "")


def _reset_sender(request, cfg):
    """The SMTP settings a password-reset email goes out through -- see
    reset_link.sender; the request only says which system's config to
    borrow when the box has no default mailbox."""
    return resetlink_service.sender(cfg, _email_slug(request))


def _email_slug(request):
    """Which system's email config sends this deployment's admin mail.

    Email config is per-system now (one sender account each). But the password
    reset and the account-setup page live on auth, which the hub mounts too --
    and the hub is no system. A master answers with its own system; the hub,
    having none of its own, borrows the first configured system's sender (the
    lowest `order`, the registry's own sort). None only if a hub runs with zero
    systems, which is a misconfiguration the reset simply can't mail through.
    """
    state = request.app.state
    system = getattr(state, "system", None)
    if system is not None:
        return system.slug
    for spec in state.registry:
        return spec.slug
    return None


#: Paths that never need a session. /api/health stays open on purpose -- the
#: hub probes masters with it, and "down" vs "locked out" must stay separable.
#: The recovery pages are open by nature: their audience has no session.
#: SPA mode: /api/public + the open /api/auth/* endpoints let the React login
#: view render and submit before any session exists; /assets serves the built
#: bundle. Non-/api page paths are served the SPA shell by the catch-all (the
#: middleware lets them through), so no page path needs listing here.
OPEN_PREFIXES = ("/login", "/static", "/assets", "/system-static",
                 "/api/public", "/api/auth/login", "/api/auth/logout",
                 # The Monitor's access token, exchanged for a session
                 # by routes/monitor.py: open because it IS a sign-in.
                 "/api/auth/monitor-token",
                 # First run: open here, closed in the route with a 409 once
                 # the owner's password is on file.
                 "/api/auth/first-run",
                 "/api/auth/forgot", "/api/auth/reset", "/api/auth/panel-reset",
                 "/favicon",
                 "/forgot", "/reset-password",
                 # The detection app asking this box's master to run a Query
                 # it stores in a Variable. The app is a local process with no
                 # session and no database of its own, so it cannot pass this
                 # wall -- and giving it a session would mean a credential in
                 # a compiled document.
                 #
                 # Open HERE, closed IN THE ROUTE: query_value refuses any
                 # caller whose address is not this machine, so what this line
                 # actually admits is a local process, and the route then
                 # allows it to name a compiled function id and nothing else
                 # (no SQL, no table list). Nothing forwards arbitrary paths
                 # to a master -- the hub calls named endpoints -- so a remote
                 # caller cannot borrow a local address.
                 "/api/workflow/query-value",
                 # ...and the same app asking for a CSV node's rows as a FILE,
                 # for exactly the same reason and behind exactly the same
                 # wall: query_csv refuses any caller that is not this machine,
                 # and what it accepts is an output id -- never a statement.
                 #
                 # Missing this line was invisible in the worst way: the fetch
                 # 401'd, the app treats an unavailable table as "send the
                 # message anyway" (a report that says the table was missing
                 # beats no report), so the email arrived with a literal
                 # {csv} in it and no attachment, and nothing anywhere said
                 # why. The BROWSER's download button is a different path
                 # (/api/workflow/csv-file) and stays behind the session wall.
                 "/api/workflow/query-csv",
                 # ...and the same app asking which chats a Telegram node in
                 # "all" / "selected" mode goes to, at send time. Same wall:
                 # resolve_recipients refuses any caller not on this machine
                 # and takes an output id only. The PEOPLE list the inspector
                 # reads (/api/telegram/recipients, no /resolve) stays behind
                 # the session wall and workflow.edit.
                 "/api/telegram/recipients/resolve")

#: REAL routes that do not live under /api/. The SPA fallthrough below exists
#: so a client-side page path renders the React shell instead of a 401 -- but
#: it used to hand EVERY non-/api path to the router, and the master mounts
#: real endpoints out here: the device video relay and the footage
#: list/download/export/DELETE proxy (core/routes/proxy.py). They ran with no
#: session at all -- anyone routable to the board could watch a paired
#: camera and delete its recordings.
#:
#: Anything under these prefixes is an ENDPOINT, not a page, so it gets the
#: same 401 an /api/ path gets. Keep this list in step with every non-/api
#: router a server mounts.
PROTECTED_PREFIXES = ("/video_feed", "/footage")

#: What a master still on the shipped password may reach (UAT-103): the setup
#: screen and the calls it is built from, the shell's own bootstrap (which is
#: how the SPA learns to go there), and keepalive so a tab parked on the form
#: is not signed out under it. Everything else under /api/ or
#: PROTECTED_PREFIXES answers 409 until setup is done. OPEN_PREFIXES never
#: reach the gate. /api/auth/password stays open because it is the other
#: honest way off the shipped password (and refuses it as the new one).
SETUP_OPEN = ("/api/auth/account-setup", "/api/auth/password", "/api/auth/me",
              "/api/auth/keepalive", "/api/bootstrap")
#: ...and what it may READ: the update state and job status, so the System
#: tab can say what version the box runs. GET only -- check, token, apply and
#: rollback change the box or reach GitHub, and stay gated.
SETUP_OPEN_READ = ("/api/hub/update",)


#: Paths open as THEMSELVES only, never as a prefix (Pipi L1, 2026-10-07).
#: /api/health was a prefix, which quietly opened anything a later route put
#: under it -- /api/health/box was opened that way. Now each open health path
#: is named here, and any future /api/health/<x> is behind the session wall
#: unless somebody adds it to this set on purpose:
#:   /api/health      the hub probes masters with it; "down" vs "locked out"
#:   /api/health/box  the Wi-Fi switch page's probe (core/routes/network.py):
#:                    a hashed box id and one word of switch state, and the
#:                    one response on the hub readable cross-origin
OPEN_EXACT = frozenset(("/api/health", "/api/health/box"))


def is_open(path):
    """True if `path` never needs a session: an exact OPEN_EXACT path, or
    one under an OPEN_PREFIXES prefix (boundary-anchored, _under)."""
    return path in OPEN_EXACT or _under(path, OPEN_PREFIXES)


def _under(path, prefixes):
    """True if `path` is one of `prefixes` or sits inside it.

    Boundary-anchored on purpose. The old test ended in a bare
    `startswith(p)`, so a future route merely BEGINNING with an open prefix
    (`/api/publicity`, `/api/healthz-admin`, `/loginsessions`) would have been
    silently unauthenticated.
    """
    for p in prefixes:
        if path == p or path.startswith(p + "/") or path.startswith(p + "?"):
            return True
    return False


#: Methods that change something. A GET may be triggered cross-site by an
#: <img> or a link; these cannot be, unless a browser is told to allow it.
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: Longest string that can be an account name here -- the rule lives in
#: auth_service.set_admin_username(). Mirrored rather than imported so the
#: login route stays free of a service import at module scope; if the service
#: ever relaxes it, this is the other half.
USERNAME_MAX = 32


def _harden(response):
    """Framing + content-type defences, on every response this box serves.

    The CSRF check refuses a cross-origin write, but a page that FRAMES the
    dashboard is not cross-origin as far as the request is concerned: the
    operator's own clicks land on our page, with our Origin and their live
    cookie. Nothing in this product is ever embedded -- there is not one
    <iframe> in the tree, the video feed and footage views are <img>/<video>
    on our own pages -- so DENY costs nothing and closes clickjacking.

    The CSP is deliberately frame-ancestors ONLY. A script-src here would
    have to be kept in step with whatever the Vite build emits, and a CSP
    that breaks the SPA on a customer board is worse than no CSP.

    nosniff stops a browser second-guessing a declared content type, which is
    how an uploaded file served back from /api gets run as script.
    """
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    return response


def require(slug=None):
    """Starlette middleware enforcing a session (SPA mode).

    The frontend is a single-page app served by the catch-all. So an
    unauthenticated PAGE request is served the shell (index.html) and the React
    app renders its own /login view -- the middleware only 401s the API. Static
    assets, the open /api/auth/* endpoints and /api/public need no session.
    """
    async def middleware(request, call_next):
        path = request.url.path
        # CSRF: refuse a cross-origin write before anything looks at the
        # session, so a page on another site cannot act as a logged-in
        # operator. Applies to the open endpoints too -- forced logout and
        # mail-flooding /api/auth/forgot are both cross-site tricks.
        if request.method in UNSAFE_METHODS and _cross_site(request):
            from fastapi.responses import JSONResponse
            return _harden(JSONResponse(
                {"ok": False, "error": "cross-site request refused"},
                status_code=403))
        if is_open(path):
            return _harden(await call_next(request))

        config_dir = request.app.state.config_dir
        # Per-tab identity first: a tab that presents X-Session IS that user,
        # whatever the browser-wide cookie says. A header that fails to verify
        # is a 401, never a fall-through to the cookie -- falling back would
        # silently turn a stale tab into the cookie's user, which is the exact
        # bug the header exists to fix.
        token = request.headers.get("X-Session") or ""
        # A program (the central dashboard) sends `Authorization: Bearer ...`:
        # either the session its sign-in returned, or the Monitor's access
        # token itself. A bearer is its own identity, like X-Session: it never
        # falls back to the cookie.
        bearer = ""
        if not token:
            auth_h = request.headers.get("Authorization") or ""
            if auth_h[:7].lower() == "bearer ":
                bearer = auth_h[7:].strip()[:300]
        user = None
        if bearer.startswith(monitor_account.TOKEN_PREFIX):
            # The access token on every request: same throttle as the sign-in,
            # and a refused token counts against the caller's address.
            request.state.auth_via = "bearer"
            ip = _client_ip(request) or "?"
            bucket = f"{ip}|monitor-token"
            if ratelimit_service.check("login", bucket):
                from fastapi.responses import JSONResponse
                return _harden(JSONResponse(
                    {"ok": False, "error": "too many attempts"}, status_code=429))
            found = monitor_account.verify_token(bearer, config_dir, ip=ip)
            if found:
                user, token = found[0], ""
            else:
                ratelimit_service.record_failure("login", bucket)
        else:
            if bearer:
                token = bearer
                request.state.auth_via = "bearer"
            else:
                request.state.auth_via = "header" if token else "cookie"
                if not token:
                    token = _session_cookie(request)
            user = auth_service.verify_session(token, config_dir,
                                               ip=_client_ip(request) or "")
        if user and auth_service.allowed(user, slug, config_dir):
            request.state.user = user
            request.state.session_sid = auth_service.session_sid(token)
            # The Monitor account never reads the configuration routes of
            # pages it has no business on (monitor_account.BLOCKED_*).
            if (path.startswith("/api/") and monitor_account.is_monitor(user, config_dir)
                    and monitor_account.blocked(path)):
                from fastapi.responses import JSONResponse
                return _harden(JSONResponse(
                    {"ok": False, "error": "the Monitor account cannot open this"},
                    status_code=403))
            # First-run gate (UAT-103): the box behaves as not-set-up until
            # the master leaves the shipped password. Pages still get the
            # shell (the SPA then routes to /account-setup); endpoints get a
            # 409 the client recognises by its header. The verify behind the
            # answer is PBKDF2, so a cache miss runs it off the event loop.
            if ((path.startswith("/api/") or _under(path, PROTECTED_PREFIXES))
                    and not _under(path, SETUP_OPEN)
                    and not (request.method == "GET"
                             and _under(path, SETUP_OPEN_READ))):
                pending = auth_service.setup_pending_cached(user, config_dir)
                if pending is None:
                    pending = await asyncio.to_thread(
                        auth_service.setup_pending, user, config_dir)
                if pending:
                    from fastapi.responses import JSONResponse
                    return _harden(JSONResponse(
                        {"ok": False, "error": "finish account setup first",
                         "needs_account_setup": True},
                        status_code=409,
                        headers={"X-Account-Setup-Required": "1",
                                 "Cache-Control": "no-store"}))
            response = await call_next(request)
            # Session-only content must never survive logout in the browser's
            # back/forward cache. Hashed assets stay cacheable -- they are in
            # OPEN_PREFIXES and never reach this line.
            response.headers["Cache-Control"] = "no-store"
            # Media (<img>, <video>, downloads) cannot send X-Session, so it
            # rides the COOKIE -- and the cookie has a fixed expiry nothing
            # renews, while a tab's header token rotates. On a dashboard left
            # open past the idle window that split showed as live numbers
            # beside a dead video: every API call worked (header) and every
            # stream 401d (cookie), for ever. So a header-authenticated
            # request whose cookie is DEAD re-arms the cookie with its own
            # token. A VALID cookie -- any user's -- is never touched: "the
            # streams follow the most recent login" stays true, this only
            # replaces a corpse.
            if request.state.auth_via == "header":
                cookie = _session_cookie(request)
                if not cookie or not auth_service.verify_session(cookie,
                                                                 config_dir):
                    response.set_cookie(
                        _cookie_name(request), token,
                        httponly=True, samesite="lax",
                        secure=_is_https(request), path="/",
                        max_age=auth_service.idle_minutes(config_dir) * 60)
            _device_cookie_upkeep(request, response, config_dir)
            return _harden(response)

        # Not authenticated (or not allowed this system).
        if path.startswith("/api/") or _under(path, PROTECTED_PREFIXES):
            from fastapi.responses import JSONResponse
            reason = "not allowed for this system" if user else "login required"
            # Tag ONLY the session-level 401 so the SPA knows to bounce to
            # /login. Endpoint-level 401s (bad pairing creds, failed re-auth)
            # lack this header and are surfaced to the user instead.
            refused = JSONResponse({"ok": False, "error": reason}, status_code=401,
                                   headers={"X-Session-Required": "1"})
            # A removed device's sessions were revoked, so this is where its
            # browser lands next: the dead cookie goes here too.
            _device_cookie_upkeep(request, refused, config_dir)
            return _harden(refused)
        # Any other path is a client route: serve the SPA shell, let React show
        # its login view. No server-side redirect (the SPA owns /login).
        # The shell is the page an attacker would want to frame, so it is
        # hardened on the logged-out path too.
        return _harden(await call_next(request))
    return middleware


# -- JSON auth API (React SPA) -------------------------------------------------
# The SPA renders its own /login, /forgot, /reset-password views and drives them
# through these. Same session cookie + services as the form routes below, so a
# live session survives the migration. The open ones are in OPEN_PREFIXES.

def _hub_url(request):
    """This box's Systems hub, as the browser should reach it -- or "" when
    this process IS the hub, so nothing ever bounces to itself.

    Only the PORT is deployment policy ([hub].port, the Software port card,
    5100 out of the box -- core/services/hubport.py); the host follows the
    request, so a link lands on the address the operator is actually
    browsing rather than a stale IP or a localhost their laptop cannot reach.
    """
    st = request.app.state
    if getattr(st, "system", None) is None:
        return ""
    return hubport.browser_url(request, st.config_dir)


@router.get("/api/public", name="auth.public")
def public_context(request: Request):
    """Open context for the pre-login screens: brand for the login card, and
    whether a recovery email exists (drives the Forgot-password affordance).

    `hub_url` lets the login view send the user to the Systems hub first (the
    landing gate) instead of straight into this master's dashboard. It is only
    returned for a master (a process that serves a system); the hub itself has
    no system, so it returns "" and never bounces to itself.

    Only the port is taken from config; the host follows the request, so the
    bounce lands on the address the operator is actually browsing."""
    cfg = request.app.state.config_dir
    hub_url = _hub_url(request)
    return {"ok": True, "brand": config.brand(cfg),
            "hub_url": hub_url,
            # Does this box use email at all? The Forgot screen asks for an
            # address only where one can be acted on; with email off it opens
            # on the box instead of on a field that leads nowhere. Open, like
            # the rest of this payload -- the two ways back in are already
            # described to an unauthenticated caller on that screen, so which
            # of them this box offers is not a new fact about it.
            "email": sysconfig_service.email_enabled(cfg)}


@router.get("/api/auth/me", name="auth.me")
def whoami(request: Request):
    """Who the caller is, or 401. Gated (not in OPEN_PREFIXES): the SPA uses a
    401 here / on /api/bootstrap to switch to its login view."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="login required")
    cfg = request.app.state.config_dir
    dev_tools = auth_service.dev_tools_of(request.app.state, cfg)
    return {"ok": True,
            "user": {"name": user,
                     # `is_admin` keeps meaning MASTER, as it always has --
                     # every page written against it means "owns the box".
                     "is_admin": auth_service.is_admin(user, cfg),
                     "is_developer": auth_service.is_developer(user, cfg),
                     # Whether this box carries the owner's tooling; the
                     # developer panels need both this and the account
                     # (core/routes/hub.py bootstrap says why).
                     "dev_tools": dev_tools,
                     # ...and the tier, for the pages that now distinguish a
                     # site admin from the master above them.
                     "tier": auth_service.tier(user, cfg),
                     "is_site_admin": auth_service.is_site_admin(user, cfg),
                     "settings_tabs": auth_service.settings_tabs(
                         user, cfg, dev_tools=dev_tools)}}


@router.post("/api/auth/login", name="auth.login_json")
async def login_json(request: Request):
    body = await _json(request)
    # Cap the name at what an account may actually be called (32 chars --
    # auth_service.set_admin_username). Uncapped, a caller with no session at
    # all wrote an arbitrarily long string into the audit table on every
    # failed attempt, and audit rows are never pruned by design. The throttle
    # below does not bound it either: its bucket is keyed ON the username, so
    # every oversized value gets a fresh bucket and never trips. Anything
    # longer cannot match an account, so truncating loses no legitimate login.
    username = str(body.get("username") or "").strip().lower()[:USERNAME_MAX]
    password = str(body.get("password") or "")
    cfg = request.app.state.config_dir
    # Throttle per (IP, username): a shared NAT must not lock a whole site out
    # because one workstation mistypes, and an attacker guessing at someone
    # else's name from another address must not lock that operator out.
    ip = _client_ip(request) or "?"
    bucket_id = f"{ip}|{username}"
    wait = ratelimit_service.check("login", bucket_id)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in "
                                   f"{int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    # PBKDF2 is deliberately slow (~0.15s on a Pi) -- run it in a thread, or
    # every login stalls the whole event loop for every user on the box.
    if not await asyncio.to_thread(auth_service.verify_login,
                                   username, password, cfg):
        ratelimit_service.record_failure("login", bucket_id)
        audit_service.record("login", username=username, result="fail",
                             source_ip=ip, config_dir=cfg)
        raise HTTPException(status_code=401, detail="Wrong username or password.")
    ratelimit_service.record_success("login", bucket_id)
    # Registered devices (auth_service, "registered sign-in devices"): only
    # NOW, with the password proven, is the browser's device looked at. A
    # known one is the same device signing in again; anything else registers
    # a new one, which a capped account at its limit is refused -- with no
    # session minted at all. The MAC is a one-file read, off the event loop.
    words = _device(request)
    mac = await asyncio.to_thread(neighmac.lookup, ip)
    try:
        reg = await asyncio.to_thread(
            auth_service.register_device, username, _device_cookie(request),
            words, ip, mac, cfg)
    except auth_service.DeviceLimitReached as e:
        _audit_expired(e.expired, cfg)
        audit_service.record("device.refused", username=username, result="fail",
                             detail=f"{words}: limit of {auth_service.MAX_DEVICES} "
                                    f"devices reached",
                             source_ip=ip, config_dir=cfg)
        return JSONResponse({"ok": False, "error": auth_service.DEVICE_LIMIT_MSG,
                             "device_limit": True}, status_code=403)
    _audit_expired(reg["expired"], cfg)
    if reg["evicted"]:
        audit_service.record("device.removed", username=username,
                             detail=f"{reg['evicted'].get('name') or 'a device'} of "
                                    f"{username}: least recently used, made room",
                             source_ip=ip, config_dir=cfg)
    if reg["created"]:
        audit_service.record("device.registered", username=username,
                             detail=words, source_ip=ip, config_dir=cfg)
    audit_service.record("login", username=username,
                         source_ip=ip, config_dir=cfg)
    # Account setup ONLY while the master is on the shipped password: the one
    # predicate the middleware gate and the bootstrap ask too. It also fired on
    # "no recovery email, no opt-out" -- an optional field -- so a finished box
    # went back through first run at its next sign-in (after an update, say).
    needs_setup = auth_service.setup_pending(username, cfg)
    token = auth_service.issue_session(username, cfg)
    auth_service.register_session(username, auth_service.session_sid(token),
                                  device=words, ip=ip, config_dir=cfg,
                                  dev=reg["ref"])
    # The token rides in the BODY too: the SPA keeps it in this tab's
    # sessionStorage and sends it as X-Session, so two accounts can live in
    # two tabs of one browser. The cookie stays -- it is the identity for
    # everything that cannot carry a header (<img> streams, <a> downloads),
    # where the last login in this browser wins.
    # Where this login lands. A master sends every sign-in to the Systems hub
    # (port 5100 unless a deployment says otherwise): a system page is a place
    # you go from the grid, not a place you arrive at. Answered HERE rather
    # than left to the login view's /api/public call, which is a second request
    # that can still be in flight -- or have failed -- when the form is
    # submitted, and a missing answer silently meant "stay on this master".
    resp = JSONResponse({"ok": True, "needs_account_setup": needs_setup,
                         "hub_url": _hub_url(request), "token": token})
    # Secure is set when the request actually arrived over TLS (or a proxy
    # says so). It cannot be unconditional: this product ships as plain HTTP
    # on a LAN, and a Secure cookie there is a cookie the browser never sends
    # back -- nobody could log in at all.
    resp.set_cookie(
        _cookie_name(request), token,
        httponly=True, samesite="lax", secure=_is_https(request), path="/",
        max_age=auth_service.idle_minutes(cfg) * 60,
    )
    _set_device_cookie(resp, request, reg["raw"])
    return resp


def _audit_expired(records, cfg):
    """One device.expired row per record the 30-day rule dropped."""
    for dev in records or ():
        if dev:
            audit_service.record("device.expired", username=dev.get("user") or "",
                                 detail=f"{dev.get('name') or 'a device'}: inactive "
                                        f"for {auth_service.DEVICE_EXPIRY_S // 86400} days",
                                 config_dir=cfg)


@router.post("/api/auth/logout", name="auth.logout_json")
def logout_json(request: Request):
    """Sign out signs out ME -- the identity making this request.

    This route is in OPEN_PREFIXES, so request.state.user is never set here;
    both identities are parsed directly. The current session's registry row
    is revoked (its token dies everywhere it is held), and the browser-wide
    cookie is deleted only when it belongs to the SAME account -- a dev tab
    signing out must not kill the admin's cookie and take down every stream
    in the admin's tabs.
    """
    cfg = request.app.state.config_dir
    header_token = request.headers.get("X-Session") or ""
    cookie_token = _session_cookie(request) or ""
    hu = auth_service.verify_session(header_token, cfg)
    cu = auth_service.verify_session(cookie_token, cfg)
    current = header_token if hu else cookie_token
    sid = auth_service.session_sid(current)
    if sid:
        auth_service.revoke_session(sid, config_dir=cfg)
    audit_service.record("logout", username=hu or cu,
                         source_ip=_client_ip(request), config_dir=cfg)
    # Signing out leaves the same way signing in arrives: on a master, at the
    # hub. A login screen on a system port is a door into one system, and the
    # next person at this browser should be met by the board, not by whichever
    # system the last one happened to have open. Empty from the hub itself.
    resp = JSONResponse({"ok": True, "hub_url": _hub_url(request)})
    if not (hu and cu and hu != cu):
        # Same attributes it was set with, or some browsers keep the cookie: a
        # delete only matches a cookie of the same name AND path/samesite.
        resp.delete_cookie(_cookie_name(request), path="/",
                           httponly=True, samesite="lax",
                           secure=_is_https(request))
    end_both_cookies(request, resp, hu or cu, cfg)
    return resp


def _send_reset_link(request, cfg, username):
    """Mint this account's reset token and mail the link to its address.
    Returns (ok, message). Synchronous -- callers wrap in to_thread. The
    body of it lives in reset_link so the front panel can send the same
    mail with no request in hand."""
    return resetlink_service.send(cfg, username, _reset_base(request),
                                  _email_slug(request))


def _admin_addresses(cfg):
    """Where "a request is waiting" goes: the master's address, and every
    developer account's. Best effort -- the Inbox is the channel that must
    work; the mail is the nudge."""
    out = []
    master_to = auth_service.admin_email(cfg)
    if master_to:
        out.append(master_to)
    for name, u in auth_service.users(cfg).items():
        if u.get(auth_service.DEVELOPER_FLAG) and u.get("email"):
            out.append(str(u["email"]))
    return out


#: The one answer /api/auth/forgot gives. Hit, miss, frozen account, no
#: address, mail down: the same words, the same status, the same speed.
_FORGOT_ANSWER = {"ok": True,
                  "message": "If that email is on an account here, the next step "
                             "is on its way. Check your inbox; a request may need "
                             "an administrator's approval first."}


def _forgot_side_effects(request, cfg, username, email, ip):
    """Everything a forgot DOES, run after the answer has gone out.

    In the background on purpose. The answer is the same for a hit and a
    miss, but a hit used to do an SMTP handshake before answering and a miss
    did not -- hundreds of milliseconds of difference, which is an account
    list for anyone with a stopwatch. And a hit whose mail failed answered
    502 where a miss answered 200. Nothing the caller can observe now depends
    on whether the address was on file. Failures land in the audit log and
    the admins' inbox, which is where the people who can fix them look.
    """
    try:
        if auth_service.is_developer(username, cfg):
            # Developer accounts are not managed over the web, resets
            # included (_refuse_if_developer): the password is changed on the
            # box. Said in the developer's own Inbox, not to the caller.
            inbox_service.post(username, "reset_request",
                               "Password reset requested for your developer account",
                               f"Somebody at {ip} asked for a reset. Developer passwords "
                               "are changed on the box (scripts/dev/make_dev_account.py), "
                               "never by an emailed link.", cfg)
            return
        if auth_service.is_master(username, cfg):
            ok, msg = _send_reset_link(request, cfg, username)
            audit_service.record("password_reset_link", username, source_ip=ip,
                                 result="ok" if ok else "error",
                                 detail="master, sent directly" if ok else msg,
                                 config_dir=cfg)
            if not ok:
                inbox_service.notify_admins(
                    "reset_failed", "Reset link could not be sent",
                    f"A reset was requested for {username} but the mail did not "
                    f"go: {msg}. Check the Email settings.", cfg,
                    page="settings", tab="mail")
            return
        row = resetreq_service.create(username, email, ip, cfg)
        inbox_service.notify_admins(
            "reset_request",
            f"Password reset requested: {username}",
            f"{username} asked for a password reset from {ip}. Approve to email "
            f"them a link, or reject.", cfg, page="settings", tab="accounts",
            request_id=row["id"], user=username)
        audit_service.record("password_reset_requested", username, source_ip=ip,
                             config_dir=cfg)
        admins = _admin_addresses(cfg)
        if admins:
            note = (f"The account '{username}' asked for a password reset from {ip}.\n\n"
                    "Open the dashboard, click the bell, and approve or reject it in "
                    "the Inbox. Nothing is sent until you approve.")
            email_service.send_plain(_reset_sender(request, cfg), admins,
                                     f"Password reset request: {username}", note)
    except Exception as exc:                                # noqa: BLE001
        audit_service.record("password_reset_requested", username, source_ip=ip,
                             result="error", detail=str(exc)[:200], config_dir=cfg)


@router.post("/api/auth/forgot", name="auth.forgot_json")
async def forgot_json(request: Request):
    """Somebody forgot their password. Body: {email}.

    The answer never says whether the address is on file -- the same 200,
    the same words, the same speed for a match, a miss and a frozen account
    -- so this cannot be used to list who has an account. What happens
    behind it depends on WHOSE address it was (_forgot_side_effects):

      * master: the link is mailed now. There is nobody above master to
        ask, and an owner locked out of their own box is the worse outcome.
      * a developer: nothing is mailed; developer passwords are changed on
        the box, and the developer's own Inbox says a reset was asked for.
      * anyone else: a reset REQUEST is recorded. Master and the developer
        see it in the bell's Inbox and get a mail saying it is waiting;
        Approve is what sends the link. A reset link is a takeover of the
        account it lands on, and knowing an email address is not enough to
        earn one for somebody else's account.
    """
    cfg = request.app.state.config_dir
    ip = _client_ip(request) or "?"
    # Unauthenticated AND it sends real mail: without a brake this is both a
    # token mill and a way to get the deployment's SMTP account blacklisted.
    wait = ratelimit_service.check("forgot", ip)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many reset requests - try again in "
                                   f"{int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    body = await _json(request)
    email = str(body.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="enter the email on your account")
    # Every attempt counts against the caller, hit or miss alike: a lockout
    # that only misses could trip would itself say which addresses are real.
    ratelimit_service.record_failure("forgot", ip)
    # Email off for this box: nothing to send and nothing to queue, so the
    # lookup is simply not made. Expressed as a failed LOOKUP rather than as
    # an early return, so there stays exactly one place an attempt is counted
    # and exactly one answer -- a second return here is a second thing that
    # could come to differ from the miss, and the whole point of this route is
    # that a hit and a miss are indistinguishable.
    username = (auth_service.account_for_email(email, cfg)
                if sysconfig_service.email_enabled(cfg) else "")
    if username:
        asyncio.get_running_loop().run_in_executor(
            None, _forgot_side_effects, request, cfg, username, email, ip)
    return dict(_FORGOT_ANSWER)


@router.get("/api/auth/reset-requests", name="auth.reset_requests")
def reset_requests(request: Request):
    """The open requests, for the Inbox. Master and developer only."""
    _require_admin(request)
    cfg = request.app.state.config_dir
    return {"ok": True, "requests": resetreq_service.pending(cfg)}


@router.post("/api/auth/reset-requests/{request_id}/approve",
             name="auth.reset_request_approve")
async def reset_request_approve(request_id: str, request: Request):
    """Send the link. The request goes the moment it is taken, so two admins
    clicking together send one link, not two."""
    _require_admin(request)
    cfg = request.app.state.config_dir
    actor = getattr(request.state, "user", None) or ""
    row = resetreq_service.take(request_id, cfg)
    if row is None:
        raise HTTPException(status_code=404, detail="that request is gone - "
                                                    "answered already, or expired")
    ok, msg = await asyncio.to_thread(_send_reset_link, request, cfg, row["user"])
    if not ok:
        # Put it back only if trying again could WORK. A mail server that was
        # briefly down is worth another click; an account whose address has
        # since been removed is not -- re-creating that row made Approve 502
        # and resurrect itself on every attempt until the request aged out.
        if auth_service.user_email(row["user"], cfg):
            resetreq_service.create(row["user"], row["email"], row.get("ip", ""), cfg)
            raise HTTPException(status_code=502, detail=f"could not send email - {msg}")
        raise HTTPException(status_code=409, detail=(
            f"{row['user']} has no email on file any more - set a new password "
            "for the account instead"))
    audit_service.record("password_reset_approved", actor, source_ip=_client_ip(request),
                         detail=row["user"], config_dir=cfg)
    inbox_service.notify_admins(
        "reset_approved", f"Reset link sent: {row['user']}",
        f"{actor} approved the request; a reset link was emailed to "
        f"{row['user']}.", cfg, page="settings", tab="accounts",
        user=row["user"])
    return {"ok": True, "requests": resetreq_service.pending(cfg)}


@router.post("/api/auth/reset-requests/{request_id}/reject",
             name="auth.reset_request_reject")
def reset_request_reject(request_id: str, request: Request):
    _require_admin(request)
    cfg = request.app.state.config_dir
    actor = getattr(request.state, "user", None) or ""
    row = resetreq_service.take(request_id, cfg)
    if row is None:
        raise HTTPException(status_code=404, detail="that request is gone - "
                                                    "answered already, or expired")
    audit_service.record("password_reset_rejected", actor, source_ip=_client_ip(request),
                         detail=row["user"], config_dir=cfg)
    return {"ok": True, "requests": resetreq_service.pending(cfg)}


@router.get("/api/auth/reset", name="auth.reset_check")
def reset_check(request: Request, token: str = ""):
    # Unauthenticated token oracle: throttled so it cannot be used to search
    # the token space, however unlikely finding a valid HMAC by guessing is.
    if ratelimit_service.check("reset", _client_ip(request) or "?"):
        raise HTTPException(status_code=429, detail="too many attempts")
    who = auth_service.reset_token_user(token, request.app.state.config_dir)
    return {"ok": True, "valid": bool(who), "username": who}


@router.post("/api/auth/reset", name="auth.reset_json")
async def reset_json(request: Request):
    body = await _json(request)
    token = str(body.get("token") or "")
    password = str(body.get("password") or "")
    cfg = request.app.state.config_dir
    ip = _client_ip(request) or "?"
    wait = ratelimit_service.check("reset", ip)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in {int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    # The token says WHOSE password this is. It used to be the master's,
    # always -- there was no other account a link could be for.
    who = auth_service.reset_token_user(token, cfg)
    if not who:
        ratelimit_service.record_failure("reset", ip)
        raise HTTPException(status_code=400, detail="invalid or expired link")
    try:
        if who == auth_service.admin_username(cfg):
            auth_service.set_master_password(password, cfg)
        else:
            auth_service.set_user_password(who, password, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Every live session of the account ends: the reset is how a lost or
    # stolen credential is shut out, and a session the thief already holds
    # must not outlive it.
    auth_service.revoke_sessions_of(who, config_dir=cfg)
    audit_service.record("password_reset", who, source_ip=ip, detail="via emailed link",
                         config_dir=cfg)
    # Told to the account itself, and to the people who approved it. A
    # password change nobody asked for is the thing this notice exists to
    # catch, so it goes to the account even though the account just did it.
    inbox_service.post(who, "password_changed", "Your password was changed",
                       "Your password was reset through an emailed link. If that "
                       "was not you, tell your administrator now.", cfg)
    if who != auth_service.admin_username(cfg):
        inbox_service.notify_admins("password_changed",
                                    f"Password changed: {who}",
                                    f"{who} set a new password through the reset "
                                    f"link (from {ip}).", cfg,
                                    page="settings", tab="accounts", user=who)
    return {"ok": True}


@router.post("/api/auth/panel-reset", name="auth.panel_reset")
async def panel_reset_json(request: Request):
    """Set a new MASTER password with the code on the front panel.

    Body: {code, password}. The other way in when the emailed link cannot
    work -- no address on file, no mailbox -- and the only way in that needs
    nothing but the box. The code appears when somebody holds the USER
    button five seconds (panel.request_master_reset), lives ten minutes, and
    five wrong guesses burn it (panel_reset.consume). Same brake as the link
    reset, same aftermath: every master session ends, the change is audited
    and posted to master's Inbox.
    """
    body = await _json(request)
    code = str(body.get("code") or "")
    password = str(body.get("password") or "")
    cfg = request.app.state.config_dir
    ip = _client_ip(request) or "?"
    wait = ratelimit_service.check("reset", ip)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in {int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    # Password first: a code is single-use, and burning it on a password the
    # box would refuse anyway sends the owner back to the button.
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    if not panelreset_service.consume(code, cfg):
        ratelimit_service.record_failure("reset", ip)
        audit_service.record("password_reset", "master", source_ip=ip, result="error",
                             detail="wrong or expired front-panel code", config_dir=cfg)
        raise HTTPException(status_code=400,
                            detail="that code is wrong or has expired - hold the USER "
                                   "button on the box for 5 seconds to get a new one")
    who = auth_service.admin_username(cfg)
    try:
        auth_service.set_master_password(password, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    auth_service.revoke_sessions_of(who, config_dir=cfg)
    audit_service.record("password_reset", who, source_ip=ip,
                         detail="via front-panel code", config_dir=cfg)
    inbox_service.post(who, "password_changed", "Your password was changed",
                       f"The master password was reset with the code from the box's "
                       f"front panel (from {ip}). If that was not you, somebody had "
                       "their hands on the box.", cfg)
    return {"ok": True, "username": who}


@router.post("/api/auth/password", name="auth.change_own_password")
async def change_own_password(request: Request):
    """Change YOUR OWN password, whoever you are.

    The built-in `admin` is not a row in users.local.toml -- it is the
    platform administrator, its hash lives in auth config, and create_user
    even refuses the name. So the per-user reset route
    (/api/users/{name}/password) 404s for it, and the only way an admin could
    change their own password was the forgot-password email round trip. On a
    box with no SMTP configured that is no way at all.

    Gated on the CURRENT password rather than on the session alone: a session
    is something an unattended browser also has, and a password change is the
    one action that locks everybody else out of the account.
    """
    body = await _json(request)
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    current = str(body.get("current") or "")
    password = str(body.get("password") or "")
    ip = _client_ip(request) or "?"
    # Keyed per (IP, account) like login: several users behind one NAT must
    # not share one bucket and lock each other out of changing a password.
    bucket_id = f"{ip}|{me}"
    wait = ratelimit_service.check("login", bucket_id)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in {int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    cfg = request.app.state.config_dir
    if not await asyncio.to_thread(auth_service.verify_login, me, current, cfg):
        ratelimit_service.record_failure("login", bucket_id)
        raise catalog.http_error("AUTH_WRONG_CURRENT")
    # The same refusal account setup makes. This route is open to a master
    # held at the setup gate (SETUP_OPEN), so without it "change iriv1234 to
    # iriv1234" would be a way past the screen that leaves the box exactly
    # as open as before -- and for any other account the shipped string is
    # just as public.
    if password == auth_service.SHIPPED_PASSWORD:
        raise HTTPException(status_code=400,
                            detail="that is the password the board shipped "
                                   "with - choose another")
    try:
        # Two stores, one action: the platform administrator's hash lives in
        # the auth config, every other account's in users.local.toml.
        if me == auth_service.admin_username(cfg):
            await asyncio.to_thread(auth_service.set_master_password,
                                    password, cfg)
        else:
            await asyncio.to_thread(auth_service.set_user_password,
                                    me, password, cfg)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such account")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # A changed password ends every OTHER session of this account -- the
    # change is how a stolen credential is shut out, so the thief's session
    # must not outlive it. This tab keeps its own.
    auth_service.revoke_sessions_of(
        me, keep_sid=getattr(request.state, "session_sid", ""), config_dir=cfg)
    return {"ok": True}


@router.post("/api/auth/keepalive", name="auth.keepalive")
def keepalive(request: Request):
    """Push this session's expiry back out to a full idle window.

    The session token carries a FIXED expiry stamped at login -- nothing
    renews it -- so a screen somebody is watching all day signs itself out
    mid-shift with no interaction to blame. That is right for a desk and wrong
    for a wall: a dashboard or a live wall is watched, not typed at, and the
    idle timer cannot tell the difference.

    So it is renewed only when an operator asks for it, by holding a page's
    Lock switch on. Deliberately NOT automatic on every request: a background
    poll renewing the session would mean nothing ever times out anywhere,
    which is the timeout deleted rather than configured. The window itself is
    unchanged -- turn the switch off, or leave the page, and the session
    expires the usual number of minutes later.

    Returns the window so the caller can pace itself instead of guessing.
    """
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    cfg = request.app.state.config_dir
    minutes = auth_service.idle_minutes(cfg)
    # SAME session, new window: the sid is kept so the registry row is
    # extended rather than a new row minted per keepalive tick. A LEGACY
    # (pre-registry) session has no sid -- its renewal mints one and
    # registers it, which is how old sessions migrate into the list.
    sid = getattr(request.state, "session_sid", "") or None
    token = auth_service.issue_session(me, cfg, sid=sid)
    if sid:
        auth_service.extend_session(sid, cfg)
    else:
        # Never a registration (no password was typed): the row joins this
        # browser's device only if it already has one for this account.
        auth_service.register_session(me, auth_service.session_sid(token),
                                      device=_device(request),
                                      ip=_client_ip(request) or "", config_dir=cfg,
                                      dev=auth_service.device_ref_for(
                                          _device_cookie(request), me, cfg))
    resp = JSONResponse({"ok": True, "idle_minutes": minutes, "token": token})
    # A header-mode tab renews its OWN token (returned in the body). It must
    # not also rewrite the browser-wide cookie: a locked wall in a dev tab
    # would otherwise flip every other tab's stream identity to dev on a
    # timer.
    if getattr(request.state, "auth_via", "cookie") == "cookie":
        resp.set_cookie(
            _cookie_name(request), token,
            httponly=True, samesite="lax", secure=_is_https(request), path="/",
            max_age=minutes * 60,
        )
    return resp


@router.post("/api/auth/username", name="auth.change_own_username")
async def change_own_username(request: Request):
    """Rename the administrator account.

    `admin` is a DEFAULT, not the identity -- it is what the account is called
    before anybody names it, so that a fresh box can be signed into at all.
    Only the administrator's own name can be changed here: an operator's name
    is the key its systems allowlist is stored under, and moving that key is a
    different job from renaming a login.

    Gated on the current password for the same reason a password change is:
    the name is half the credential, and a session is something an unattended
    browser also has. The session cookie is re-signed under the new name in
    the response -- it carries the username, so without that the rename would
    log the caller straight out of the box they just renamed.
    """
    body = await _json(request)
    me = getattr(request.state, "user", None) or ""
    cfg = request.app.state.config_dir
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    if me != auth_service.admin_username(cfg):
        raise HTTPException(
            status_code=403,
            detail="only the administrator account can be renamed here")
    ip = _client_ip(request) or "?"
    bucket_id = f"{ip}|{me}"
    wait = ratelimit_service.check("login", bucket_id)
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in {int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    if not await asyncio.to_thread(auth_service.verify_login,
                                   me, str(body.get("current") or ""), cfg):
        ratelimit_service.record_failure("login", bucket_id)
        raise catalog.http_error("AUTH_WRONG_CURRENT")
    try:
        name = auth_service.set_admin_username(body.get("username"), cfg)
    except auth_service.UsernameTaken as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit_service.record("account.rename", username=me, detail=f"now {name}",
                         source_ip=ip, config_dir=cfg)
    # The token carries the username, so the session is re-signed under the
    # new name -- in the body for this tab's sessionStorage, and in the
    # cookie only for a cookie-mode caller. The registry row follows the
    # rename; the account's OTHER sessions are ended (their tokens carry the
    # old name and would 401 anyway -- revoking makes it deliberate).
    sid = getattr(request.state, "session_sid", "") or None
    token = auth_service.issue_session(name, cfg, sid=sid)
    # The account's registered devices move with it: a rename is not a reason
    # for every browser it uses to register again.
    auth_service.rename_devices_user(me, name, cfg)
    if sid:
        auth_service.rename_session_user(sid, name, cfg)
        auth_service.revoke_sessions_of(me, keep_sid=sid, config_dir=cfg)
    else:
        auth_service.register_session(name, auth_service.session_sid(token),
                                      device=_device(request),
                                      ip=_client_ip(request) or "", config_dir=cfg,
                                      dev=auth_service.device_ref_for(
                                          _device_cookie(request), name, cfg))
        auth_service.revoke_sessions_of(me, config_dir=cfg)
    resp = JSONResponse({"ok": True, "username": name, "token": token})
    if getattr(request.state, "auth_via", "cookie") == "cookie":
        resp.set_cookie(
            _cookie_name(request), token,
            httponly=True, samesite="lax", secure=_is_https(request), path="/",
            max_age=auth_service.idle_minutes(cfg) * 60,
        )
    return resp


def _check_mailbox(sender, app_password, host):
    """All blank, or an address with its app password; ValueError in plain
    words for anything in between."""
    if (sender or app_password or host.strip()) and not (sender and app_password):
        raise ValueError(f"Add the {'app password' if sender else 'sending address'}"
                         " too, or clear the mailbox fields to set it up later.")
    try:
        sender and auth_service.clean_email(sender)
    except ValueError:
        raise ValueError("The sending address is not a valid email address.") from None
    # App passwords only (2026-10-14): refused here, before anything is
    # written, so a Gmail account password never half-completes the setup.
    if app_password:
        from core.services import mail_accounts as mail_service  # noqa: PLC0415
        mail_service.clean_app_password(app_password, host)


@router.get("/api/auth/account-setup", name="auth.setup_context")
def setup_context(request: Request):
    _require_admin(request)
    cfg = request.app.state.config_dir
    email_cfg = email_service.load(_email_slug(request), cfg)
    # A box mailbox counts as ready too: it is what a reset email will send
    # through from here on, and it is the one the setup form now offers to
    # create. Either being present means "a reset email can leave this box".
    from core.services import mail_accounts as mail_service  # noqa: PLC0415
    try:
        box_ready = bool([a for a in mail_service.load(cfg)
                          if a.get("app_password")])
    except Exception:                                       # noqa: BLE001
        box_ready = False
    return {"ok": True, "current": auth_service.admin_email(cfg) or "",
            "username": auth_service.admin_username(cfg),
            "is_default_username": auth_service.is_default_username(cfg),
            # Whether the form may still offer "blank to keep" on the password.
            # A box on the shipped one may not: that is the whole reason this
            # screen is in the way.
            "password_is_default":
                auth_service.first_run_pending(cfg),
            "smtp_ready": box_ready or bool(email_cfg.get("sender")
                                            and email_cfg.get("app_password"))}


@router.post("/api/auth/account-setup", name="auth.setup_json")
async def setup_json(request: Request):
    """First run: recovery email, and the name and password of the account.

    The administrator ships as `admin` with the password whoever installed the
    box typed -- both are a starting point, and this is the screen where they
    stop being one. Name and password are optional here (blank = keep), so the
    page can still be used later for the email alone; changing either asks for
    the current password, as everywhere else.
    """
    _require_admin(request)
    body = await _json(request)
    cfg = request.app.state.config_dir
    me = getattr(request.state, "user", None) or ""
    name = str(body.get("username") or "").strip().lower()
    password = str(body.get("password") or "")
    renaming = bool(name) and name != auth_service.admin_username(cfg)
    # The board may not leave this screen on the password it was built with.
    # Enforced on the SERVER because the form is not the only way in here, and
    # a gate that only exists in the browser is not a gate. It demands a
    # CHANGE, never a lock-out: the owner is signed in and one field away from
    # finishing, and refusing the shipped string as the new one is what stops
    # the same screen asking again at the next sign-in. Only when the MASTER
    # itself is here: a developer saving the recovery address cannot set the
    # master's password (the check below refuses it), so demanding one of them
    # would be a dead end rather than a gate.
    if (me == auth_service.admin_username(cfg)
            and auth_service.first_run_pending(cfg)):
        if not password:
            raise HTTPException(
                status_code=400,
                detail="choose a password: this board is still on the one it "
                       "shipped with")
        if password == auth_service.SHIPPED_PASSWORD:
            raise HTTPException(
                status_code=400,
                detail="that is the password the board shipped with - choose "
                       "another")
    if (renaming or password) and me != auth_service.admin_username(cfg):
        raise HTTPException(status_code=403,
                            detail="only the administrator can change its own "
                                   "name or password")
    if renaming or password:
        ip = _client_ip(request) or "?"
        if not await asyncio.to_thread(auth_service.verify_login,
                                       me, str(body.get("current") or ""), cfg):
            ratelimit_service.record_failure("login", f"{ip}|{me}")
            raise catalog.http_error("AUTH_WRONG_CURRENT")
    # Every field is checked BEFORE any is written (UAT-107): the address was
    # saved first and the password refused after it, so a submission the form
    # reported as failed had quietly changed the recovery email.
    try:
        if "email" in body:
            auth_service.clean_email(str(body.get("email") or ""),
                                     allow_empty=True)
        if password:
            auth_service.check_password(password)
        if renaming:
            auth_service.check_admin_username(name, cfg)
        # The sending mailbox is optional; half of one is refused, not dropped,
        # or the owner who forgot the password believes it was saved.
        sender = str(body.get("smtp_sender") or "").strip()
        sender_pw = str(body.get("smtp_password") or "")
        _check_mailbox(sender, sender_pw, str(body.get("smtp_host") or ""))
    except auth_service.UsernameTaken as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        # Offered on the same screen as the recovery address: a recovery
        # address with nothing able to mail it reads as a working way back in.
        if sender and sender_pw:
            from core.services import mail_accounts as mail_service  # noqa: PLC0415
            existing = next((a for a in mail_service.load(cfg)
                             if a["sender"].lower() == sender.lower()), None)
            mail_service.save_account(
                {"id": (existing or {}).get("id", ""),
                 "name": (existing or {}).get("name") or "Deployment mailbox",
                 "sender": sender, "app_password": sender_pw,
                 "smtp_host": str(body.get("smtp_host") or "").strip()
                              or mail_service.DEFAULT_HOST,
                 "smtp_port": body.get("smtp_port") or mail_service.DEFAULT_PORT},
                cfg)
        # Password before name: if the rename lands and the password write
        # then fails, the operator is left hunting for which of the two took.
        if password:
            await asyncio.to_thread(auth_service.set_master_password,
                                    password, cfg)
        if renaming:
            name = auth_service.set_admin_username(name, cfg)
        # The recovery address LAST, and only when the body actually CARRIES
        # the field. "" now means "remove the recovery address" rather than
        # being refused, so a caller saving the sending mailbox alone -- or
        # the name and password alone -- would otherwise wipe the master's
        # address as a side effect of not mentioning it.
        if "email" in body:
            auth_service.set_admin_email(str(body.get("email") or ""), cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sid = getattr(request.state, "session_sid", "") or None
    final_name = auth_service.admin_username(cfg)
    token = auth_service.issue_session(final_name, cfg, sid=sid)
    resp = JSONResponse({"ok": True, "username": final_name,
                         **({"token": token} if (renaming or password) else {})})
    if renaming and final_name != me:
        auth_service.rename_devices_user(me, final_name, cfg)
    if (renaming or password) and not sid:
        # A legacy session re-issued under a fresh sid: register it, or the
        # returned token would refuse on its first use.
        auth_service.register_session(final_name, auth_service.session_sid(token),
                                      device=_device(request),
                                      ip=_client_ip(request) or "", config_dir=cfg,
                                      dev=auth_service.device_ref_for(
                                          _device_cookie(request), final_name, cfg))
        sid = auth_service.session_sid(token)
    if renaming and sid:
        auth_service.rename_session_user(sid, final_name, cfg)
    if password and sid:
        # New password, new locks: every other session of this account ends.
        auth_service.revoke_sessions_of(final_name, keep_sid=sid, config_dir=cfg)
        auth_service.revoke_sessions_of(me, keep_sid=sid, config_dir=cfg)
    if renaming and getattr(request.state, "auth_via", "cookie") == "cookie":
        resp.set_cookie(
            _cookie_name(request), token,
            httponly=True, samesite="lax", secure=_is_https(request), path="/",
            max_age=auth_service.idle_minutes(cfg) * 60,
        )
    return resp


_FIRST_RUN_LOCK = threading.Lock()


def _claim_first_run(name, password, email, cfg):
    """Check-and-write as one step, so two browsers finishing the wizard at
    once cannot both pass the "still on the shipped password" test. Returns
    False when the box is already claimed."""
    with _FIRST_RUN_LOCK:
        if not auth_service.first_run_pending(cfg):
            return False
        # Keeping the shipped password is the owner's call: the wizard offers
        # it and it is written like any other (rehashed under a fresh salt).
        auth_service.set_master_password(password, cfg)
        auth_service.mark_first_run_done(cfg)
        if name and name != auth_service.admin_username(cfg):
            auth_service.set_admin_username(name, cfg)
        if email:
            auth_service.set_admin_email(email, cfg)
        return True


@router.get("/api/auth/first-run", name="auth.first_run_context")
async def first_run_context(request: Request):
    """Does this box still need its first setup? Open: the login screen asks
    before anyone has a session, to decide between the wizard and the sign-in.
    One boolean and the shipped name the wizard offers to replace."""
    cfg = request.app.state.config_dir
    pending = await asyncio.to_thread(auth_service.first_run_pending, cfg)
    return {"ok": True, "pending": bool(pending),
            "username": auth_service.admin_username(cfg) if pending else ""}


@router.post("/api/auth/first-run", name="auth.first_run")
async def first_run(request: Request):
    """First setup, phone style: name and password of the administrator, and
    an optional recovery email, BEFORE the first sign-in. Nothing is signed in
    by this call -- the wizard hands over to the login screen.

    Only while the master is on the shipped password. That string is public
    (the manual and the AP key), so what this opens is no more than the login
    it replaces; once the owner's own password is on file it answers 409 for
    ever and the account-setup screen / Settings are the only ways to change
    it, each behind the current password."""
    cfg = request.app.state.config_dir
    ip = _client_ip(request) or "?"
    wait = ratelimit_service.check("login", f"{ip}|first-run")
    if wait:
        raise HTTPException(status_code=429,
                            detail=f"too many attempts - try again in "
                                   f"{int(wait) + 1}s",
                            headers={"Retry-After": str(int(wait) + 1)})
    if not await asyncio.to_thread(auth_service.first_run_pending, cfg):
        raise HTTPException(status_code=409,
                            detail="this box is already set up - sign in")
    body = await _json(request)
    name = str(body.get("username") or "").strip().lower()[:USERNAME_MAX]
    password = str(body.get("password") or "")
    email = str(body.get("email") or "").strip()
    try:
        auth_service.check_password(password)
        # The owner names the account too: no username, no setup. Keeping the
        # shipped name is allowed, but only by typing it.
        if not name:
            raise ValueError("choose a username")
        auth_service.check_admin_username(name, cfg)
        if email:
            auth_service.clean_email(email)
    except ValueError as e:
        ratelimit_service.record_failure("login", f"{ip}|first-run")
        raise HTTPException(status_code=400, detail=str(e))
    try:
        done = await asyncio.to_thread(_claim_first_run, name, password, email, cfg)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not done:
        raise HTTPException(status_code=409,
                            detail="this box is already set up - sign in")
    final = auth_service.admin_username(cfg)
    audit_service.record("first_run", username=final,
                         detail="administrator password set before first sign-in",
                         source_ip=ip, config_dir=cfg)
    return {"ok": True, "username": final}


# -- session management ---------------------------------------------------------
# The registry (core/services/auth.py) is what makes a session a THING with a
# row: these routes are how it is seen and ended. A non-admin sees and ends
# only their own; an admin sees everyone's, because "end that login" is a
# thing an administrator legitimately does to someone else's account.

def _may_handle_owner(me, owner, cfg):
    """May `me` see and end `owner`'s sessions and devices? Their own, always;
    anyone's for master (and a developer). Never a developer's rows for a
    customer: the vendor's account is not managed from the box's web pages
    (auth_service._refuse_if_developer), and that includes its sign-ins."""
    if owner == me:
        return True
    if not auth_service.is_admin(me, cfg):
        return False
    return (auth_service.is_developer(me, cfg)
            or not auth_service.has_developer_flag(owner, cfg))


def _current_device(request, me, cfg):
    """The device this request comes from: its session's device, else the
    device its cookie names for this account. "" when neither."""
    sid = getattr(request.state, "session_sid", "")
    row = auth_service._sessions_table(cfg).get(sid) if sid else None
    if row and row.get("dev") and row.get("dev") in auth_service._devices_table(cfg):
        return str(row["dev"])
    return auth_service.device_ref_for(_device_cookie(request), me, cfg)


def _mac_for(dev):
    """Best-effort MAC for a device's current IP: a live neighbour-table hit,
    else the one stored at sign-in if it was seen at this same IP. Never a
    claim it is verified -- the UI says what it is."""
    live = neighmac.lookup(dev.get("ip") or "")
    if live:
        return live
    if dev.get("mac") and dev.get("mac_ip") and dev.get("mac_ip") == dev.get("ip"):
        return dev["mac"]
    return ""


@router.get("/api/auth/sessions", name="auth.sessions")
def list_sessions(request: Request):
    """Registered devices, each with its sessions -- and the flat session list
    as before, for any caller written against it.

    A device collapses every sign-in of one browser: the eleven "Chrome -
    Windows" rows of one operator signing in again and again are one device
    with its live sessions folded under it."""
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    cfg = request.app.state.config_dir
    admin = auth_service.is_admin(me, cfg)
    mine = getattr(request.state, "session_sid", "")
    rows = [r for r in (auth_service.all_sessions(cfg) if admin
                        else auth_service.sessions_of(me, cfg))
            if _may_handle_owner(me, r["user"], cfg)]
    here = _current_device(request, me, cfg)
    devices = []
    for dev in auth_service.devices_view(cfg):
        if not admin and dev["user"] != me:
            continue
        if not _may_handle_owner(me, dev["user"], cfg):
            continue
        current = (dev["id"] == here and dev["user"] == me) or mine in dev["sids"]
        devices.append({
            "id": dev["id"], "user": dev["user"], "name": dev["name"],
            "ip": dev["ip"], "mac": _mac_for(dev),
            "status": "online" if dev["online"] else "offline",
            "online": dev["online"], "seen": dev["seen"], "first": dev["first"],
            "registered": bool(dev["registered"]), "legacy": dev["legacy"],
            "current": bool(current),
            "sessions_count": len(dev["sessions"]),
            "sessions": [{"sid": s["sid"], "login": s["login"], "seen": s["seen"],
                          "ip": s["ip"], "current": s["sid"] == mine}
                         for s in dev["sessions"]],
        })
    # The cap, per account: what each listed account has used of it.
    counts = {}
    for d in devices:
        if not d["legacy"]:
            counts[d["user"]] = counts.get(d["user"], 0) + 1
    return {"ok": True, "admin": admin,
            # The timeout policy rides with the list it governs.
            "auto_logout": auth_service.auto_logout(cfg),
            "idle_minutes": auth_service.idle_minutes(cfg),
            "sessions": [{**row, "current": row["sid"] == mine}
                         for row in rows],
            "devices": devices,
            "device_counts": counts,
            "device_limit": auth_service.MAX_DEVICES,
            "device_capped": auth_service.device_capped(me, cfg),
            "device_count": counts.get(me, 0),
            "device_expiry_days": auth_service.DEVICE_EXPIRY_S // 86400,
            "device_online_minutes": auth_service.DEVICE_ONLINE_S // 60,
            # A legacy (pre-registry) token has no row to show or revoke; the
            # UI says so instead of rendering an empty list as "no sessions".
            "unlisted": not mine}


@router.get("/api/system/settings", name="system.settings")
def system_settings(request: Request):
    """The box-wide switches, and the facts the page needs to explain them.

    One call, because the page shows them together: whether sessions expire,
    whether this appliance uses email, and -- so the email switch can say what
    turning it off costs -- whether the front panel could still reset the
    master afterwards.
    """
    cfg = request.app.state.config_dir
    me = getattr(request.state, "user", None) or ""
    if not auth_service.is_admin(me, cfg):
        raise HTTPException(status_code=403, detail="master or developer only")
    return {"ok": True,
            **sysconfig_service.settings(cfg),
            "auto_logout": auth_service.auto_logout(cfg),
            "idle_minutes": auth_service.idle_minutes(cfg),
            "panel_reset": auth_service.panel_reset_ready(cfg)}


@router.post("/api/system/settings", name="system.settings_save")
async def system_settings_save(request: Request):
    """Turn a box-wide switch on or off. Master or developer.

    Only the switches NAMED in the body are touched, so a page that knows
    about two settings cannot reset a third somebody adds later -- the same
    reason account-setup writes the recovery address only when the body
    carries it.
    """
    cfg = request.app.state.config_dir
    me = getattr(request.state, "user", None) or ""
    if not auth_service.is_admin(me, cfg):
        raise HTTPException(status_code=403, detail="master or developer only")
    body = await _json(request)
    if "email" in body:
        on = sysconfig_service.set_email_enabled(bool(body.get("email")), cfg)
        audit_service.record("system.settings", username=me,
                             detail=f"email {'on' if on else 'off'}",
                             source_ip=_client_ip(request), config_dir=cfg)
    if "auto_logout" in body:
        on = auth_service.set_auto_logout(bool(body.get("auto_logout")), cfg)
        audit_service.record("session.policy", username=me,
                             detail=f"auto logout {'on' if on else 'off'}",
                             source_ip=_client_ip(request), config_dir=cfg)
    if "chip_balance" in body:      # the apps re-read it within ~10 s
        on = sysconfig_service.set_chip_balance(bool(body.get("chip_balance")), cfg)
        audit_service.record("system.settings", username=me,
                             detail=f"hailo load balancer {'on' if on else 'off'}",
                             source_ip=_client_ip(request), config_dir=cfg)
    if "share_polls" in body:       # every process sees it on its next request
        on = sysconfig_service.set_share_polls(bool(body.get("share_polls")), cfg)
        audit_service.record("system.settings", username=me,
                             detail=f"shared poll cache {'on' if on else 'off'}",
                             source_ip=_client_ip(request), config_dir=cfg)
    # wifi_loss_hotspot (v1.0.37-38) is gone with its fallback: a body that
    # still names it is ignored like any other unknown key.
    return {"ok": True,
            **sysconfig_service.settings(cfg),
            "auto_logout": auth_service.auto_logout(cfg),
            "idle_minutes": auth_service.idle_minutes(cfg),
            "panel_reset": auth_service.panel_reset_ready(cfg)}


@router.post("/api/auth/sessions/policy", name="auth.session_policy")
async def session_policy(request: Request):
    """Auto logout on or off, for the whole box. Master or developer.

    Off is not "never": the window becomes thirty days (auth.NO_AUTO_LOGOUT_
    MINUTES), and applies to sign-ins from now on -- the sessions already
    open keep the expiry they were stamped with.
    """
    me = getattr(request.state, "user", None) or ""
    cfg = request.app.state.config_dir
    if not auth_service.is_admin(me, cfg):
        raise HTTPException(status_code=403, detail="master or developer only")
    body = await _json(request)
    if "auto_logout" not in body:
        raise HTTPException(status_code=400, detail="auto_logout is required")
    on = auth_service.set_auto_logout(bool(body.get("auto_logout")), cfg)
    audit_service.record("session.policy", username=me,
                         detail=f"auto logout {'on' if on else 'off'}",
                         source_ip=_client_ip(request), config_dir=cfg)
    return {"ok": True, "auto_logout": on,
            "idle_minutes": auth_service.idle_minutes(cfg)}


@router.delete("/api/auth/sessions/{sid}", name="auth.revoke_session")
def revoke_session(sid: str, request: Request):
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    cfg = request.app.state.config_dir
    owner = next((s["user"] for s in auth_service.all_sessions(cfg)
                  if s["sid"] == sid), None)
    if owner is None:
        raise HTTPException(status_code=404, detail="no such session")
    if not _may_handle_owner(me, owner, cfg):
        raise HTTPException(status_code=403,
                            detail="only your own sessions can be ended here")
    auth_service.revoke_session(sid, config_dir=cfg)
    audit_service.record("session.revoke", username=me,
                         detail=f"ended a session of {owner}",
                         source_ip=_client_ip(request), config_dir=cfg)
    return {"ok": True}


@router.post("/api/auth/sessions/revoke-others", name="auth.revoke_others")
def revoke_other_sessions(request: Request):
    """End every session of MY account except the one asking."""
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    cfg = request.app.state.config_dir
    n = auth_service.revoke_sessions_of(
        me, keep_sid=getattr(request.state, "session_sid", ""), config_dir=cfg)
    audit_service.record("session.revoke_others", username=me,
                         detail=f"ended {n} other session(s)",
                         source_ip=_client_ip(request), config_dir=cfg)
    return {"ok": True, "revoked": n}


# -- registered devices ---------------------------------------------------------
# "Sign Out" ends one session and leaves the browser registered; "Remove
# Device" unregisters the browser AND ends every session it holds, so it has
# to sign in and register again (and counts toward the cap again when it
# does). Same permission rule as sessions (_may_handle_owner).

@router.delete("/api/auth/devices/{ref}", name="auth.remove_device")
def remove_device(ref: str, request: Request):
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    cfg = request.app.state.config_dir
    # The ref is a public handle, shape-checked before it goes anywhere. An
    # unknown ref and somebody else's ref the caller may not handle answer
    # the SAME 404, so the route cannot be used to learn which refs exist.
    dev = (next((d for d in auth_service.devices_view(cfg) if d["id"] == ref), None)
           if auth_service.valid_device_ref(ref) else None)
    if dev is None or not _may_handle_owner(me, dev["user"], cfg):
        raise HTTPException(status_code=404, detail="no such device")
    out = auth_service.remove_device(ref, config_dir=cfg)
    if out is None:
        raise HTTPException(status_code=404, detail="no such device")
    audit_service.record("device.removed", username=me,
                         detail=f"{out['name'] or 'a device'} of {out['user']}, "
                                f"{out['sessions']} session(s) ended",
                         source_ip=_client_ip(request), config_dir=cfg)
    return {"ok": True, "sessions": out["sessions"]}


@router.post("/api/auth/devices/remove-others", name="auth.remove_other_devices")
def remove_other_devices(request: Request):
    """Every device of MY account except this one: unregistered, signed out."""
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="sign in first")
    cfg = request.app.state.config_dir
    n = auth_service.remove_devices_of(
        me, keep_ref=_current_device(request, me, cfg),
        keep_sid=getattr(request.state, "session_sid", ""), config_dir=cfg)
    audit_service.record("device.remove_others", username=me,
                         detail=f"removed {n} other device(s)",
                         source_ip=_client_ip(request), config_dir=cfg)
    return {"ok": True, "removed": n}


async def _json(request):
    try:
        return await request.json() or {}
    except Exception:
        return {}


# -- legacy Jinja page/form routes removed (React SPA migration).
# /login, /logout, /forgot, /reset-password, /account-setup are React
# routes served by the SPA catch-all; their behaviour is the JSON
# /api/auth/* endpoints above. user management stays JSON below.


# -- user management (admin only) ---------------------------------------------

def _require_admin(request):
    """Master or developer. The name is historical -- see auth.is_admin."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _require_account_admin(request):
    """Master, developer, OR a site admin: the tiers that manage accounts.

    A site admin manages their own staff and nobody else's -- which is not
    something a route-level guard can decide, because it depends on WHICH
    account. That is `_require_over` below; this only says the caller is in
    the business of managing accounts at all.
    """
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _require_over(request, username):
    """The caller must OUTRANK this account. Strictly: an admin editing
    another admin is how one of them deletes the other."""
    me = getattr(request.state, "user", None)
    cfg = request.app.state.config_dir
    if not auth_service.may_manage(me, username, cfg):
        raise HTTPException(
            status_code=403,
            detail=(f"{username} is not yours to manage - an account can only "
                    "be changed by someone above it"))


def _require_tier_grant(request, body):
    """Only master and developer may make (or unmake) a site admin, or cap how
    many systems one may hold. An admin promoting another admin -- who could
    then delete them -- is the flat hierarchy the tier exists to avoid."""
    if body.get("admin") is None and body.get("max_systems") is None:
        return
    if not auth_service.may_set_admin(getattr(request.state, "user", None),
                                      request.app.state.config_dir):
        raise HTTPException(
            status_code=403,
            detail="only the box owner can grant the admin tier or set a "
                   "system limit")


def _require_page_grant(request, body):
    """A per-PAGE grant belongs to master. An admin hands out categories; if
    they could also hand out pages, master's restriction would be a
    suggestion they could lift on themselves' staff."""
    if body.get("pages") is None:
        return
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(
            status_code=403,
            detail="only the box owner can choose which pages an account may "
                   "open - you can grant the categories within them")


def _require_section_grant(request, body):
    """A CATEGORY grant is master's too.

    It began as the admin's own control -- they hand their staff a narrower
    view of the systems they run -- but an admin editing a grant is an admin
    editing THEIR OWN account's grant one careless click later, and the tier
    exists precisely so that what an operation may reach is decided above it.
    All three levels of the access tree answer to the same person now.
    """
    if body.get("sections") is None:
        return
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(
            status_code=403,
            detail="only the box owner can choose what an account may reach")


def _require_cap_grant(request, body):
    """A CAPABILITY grant belongs to master, for the same reason the page grant
    does: it is the setting that says what an admin may change, and an admin
    who could edit it would be handing themselves the thing master withheld."""
    if body.get("caps") is None:
        return
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(
            status_code=403,
            detail="only the box owner can choose what an account may change")


def _refuse_builtin(username, request, action):
    """The administrator is listed on the accounts page but is not managed
    through the per-user routes: it has no row for them to find, and the two
    that would "work" -- disable and delete -- are how a deployment locks
    itself out of itself. Its name and password change through the
    self-service routes, which ask for the current password."""
    if (str(username or "").strip().lower()
            == auth_service.admin_username(request.app.state.config_dir)):
        raise HTTPException(
            status_code=400,
            detail=f"the administrator account cannot be {action} here - "
                   "change its name or password from your own account")


@router.get("/api/users", name="users.list")
def list_users(request: Request):
    _require_account_admin(request)
    cfg = request.app.state.config_dir
    masters = config.systems(cfg).get("masters", {})
    # Scoped by the SAME rule as every other hub surface (hub.visible_to): an
    # admin runs a deployment and a developer builds the product, and the
    # systems on either side of that line are not each other's business. This
    # page was the last one still listing all of them, so an admin granting
    # access saw the developer's template systems mixed in with their own --
    # and could hand somebody a grant on one.
    me_now = getattr(request.state, "user", None) or ""
    from core.services import hub as hub_service
    masters = {slug: entry for slug, entry in masters.items()
               if hub_service.visible_to(me_now, slug, masters, cfg)}
    # A system's SLUG is permanent and its display name is not, so the chips
    # have to be labelled from the registry rather than from the slug they were
    # created with -- a renamed system was still showing the name it had on the
    # day it was made. Slugs stay the identity the allowlist is stored under.
    registry = getattr(request.app.state, "registry", None) or []
    names = {}
    for system in registry:
        slug = getattr(system, "slug", None)
        if slug:
            names[slug] = getattr(system, "display_name", "") or slug
    # The DEVELOPER account is invisible to everyone but a developer. It is
    # the product author's account, not part of the deployment an admin
    # runs -- and every control on it is refused anyway (no password reset,
    # no disable, no delete: see AccountsPanel), so listing it to an admin
    # only ever showed a row they could look at and not touch. Filtered here,
    # server-side, because hiding a row in the page hides the row, not the
    # payload it came in.
    me = getattr(request.state, "user", None) or ""
    everyone = auth_service.public_users(cfg)
    if not auth_service.is_developer(me, cfg):
        everyone = [u for u in everyone if not u.get("developer")]
    # A SITE ADMIN sees their own staff and themselves, and nobody above or
    # beside them. The point of the tier is that a client's operations lead
    # runs their own people -- being shown the other admins' accounts would be
    # showing them rows they cannot touch, and naming who else has the box.
    # Filtered server-side: hiding a row in the page hides the row, not the
    # payload it arrived in.
    if not auth_service.is_admin(me, cfg):
        everyone = [u for u in everyone
                    if u["username"] == me
                    or auth_service.may_manage(me, u["username"], cfg)]
    # The administrator FIRST, as a row like any other. Its credential lives in
    # the auth config rather than in users.local.toml, which is a storage
    # detail; it is still the account this box is signed into, and omitting it
    # made the page report "0 accounts" on a working deployment.
    # ...but only to somebody who could act on it: to a site admin the master
    # row is just the name of whoever is above them.
    if auth_service.is_admin(me, cfg):
        everyone = [auth_service.admin_account(cfg)] + everyone
    # Whether the viewer may act on each row, decided by the same rule the
    # routes enforce. Authority runs strictly downward, so this is False on
    # the viewer's OWN row -- which is the answer the page needs, because it
    # lists you in order to show your access, not to let you widen it.
    for row in everyone:
        row["manageable"] = bool(
            not row.get("builtin")
            and auth_service.may_manage(me, row["username"], cfg))
    return {"ok": True,
            "users": everyone,
            # Whether the master could actually be reset at the hardware if
            # its address were removed. The panel ships DISABLED, so on a
            # stock board removing the address leaves no way back in but a
            # shell -- and the page has to say which box this is rather than
            # promise a front panel that is not running.
            # ...to the tier that can actually act on it. A site admin is
            # never offered the Remove button (can_set_tier gates it), so the
            # flag would tell them only one thing: whether the five-second
            # hold is armed on a box they can walk up to. The panel routes
            # already keep that to master and developer.
            "panel_reset": (auth_service.panel_reset_ready(cfg)
                            if auth_service.may_set_admin(me, cfg) else None),
            # Whether this box uses email at all. The row's address controls
            # are drawn from it: on a box with email off the server refuses a
            # new address, and a menu item that always fails is worse than one
            # that is not offered.
            "email": sysconfig_service.email_enabled(cfg),
            # What the VIEWER may do here, so the page offers the controls it
            # has rather than the controls it would fail at.
            "me": me,
            "tier": auth_service.tier(me, cfg),
            "can_set_tier": auth_service.may_set_admin(me, cfg),
            "can_set_pages": auth_service.is_admin(me, cfg),
            "can_set_caps": auth_service.is_admin(me, cfg),
            "can_set_sections": auth_service.is_admin(me, cfg),
            "can_create_systems": auth_service.is_admin(me, cfg),
            "pages": [{"page": p, "label": label}
                      for p, label, _c in (nav_service.PAGES
                                           + nav_service.ADMIN_PAGES)],
            # The categories the pages hang under, and the capabilities that
            # hang under the pages -- the access tree is drawn from the
            # server's own lists so a checkbox cannot outlive what enforces it.
            "sections": [{"key": key, "label": nav_service.SECTION_LABELS[key],
                          "pages": list(nav_service.SECTION_PAGES[key])}
                         for key in nav_service.SECTION_KEYS],
            "capabilities": caps_service.tree(),
            "default_caps": list(caps_service.DEFAULT_ADMIN_CAPS),
            "admin": auth_service.admin_username(cfg),
            "systems": sorted(masters.keys()),
            # slug -> the name it goes by NOW. Missing = show the slug.
            "names": {slug: names.get(slug, slug) for slug in masters},
            "owners": {slug: (entry or {}).get("owner", "")
                       for slug, entry in masters.items()},
            "roles": auth_service.roles(cfg),
            # Which of them are the product's own, so the UI can show that they
            # are not removable rather than failing the attempt.
            "builtin_roles": list(auth_service.BUILTIN_ROLES)}


@router.post("/api/roles", name="roles.create")
async def create_role(request: Request):
    """Add a role to the platform's vocabulary. A role is a LABEL -- access is
    decided by the systems allowlist and the admin account -- so creating one
    grants nothing to anybody."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        names = auth_service.create_role(body.get("name"),
                                         request.app.state.config_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "roles": names}


@router.delete("/api/roles/{name}", name="roles.delete")
def delete_role(name: str, request: Request):
    _require_admin(request)
    try:
        names = auth_service.delete_role(name, request.app.state.config_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "roles": names}


@router.post("/api/users", name="users.create")
async def create_user(request: Request):
    _require_account_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _require_tier_grant(request, body)
    # Every account has an address from the day it is made: it is where a
    # reset link goes, and an account without one has no recovery path but
    # master. Required here rather than in the service, which the tests and
    # older tools still call without one.
    # An address is OPTIONAL here, as it is in create_user: a box on a closed
    # network has nothing to send a reset link through. A malformed one is
    # still refused -- that is a mistake, where an empty one is a choice.
    if str(body.get("email") or "").strip():
        try:
            auth_service.clean_email(body.get("email"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        await asyncio.to_thread(
            auth_service.create_user,
            body.get("username"), body.get("password"), body.get("systems"),
            request.app.state.config_dir,
            role=body.get("role"),
            admin=bool(body.get("admin")),
            email=body.get("email"),
        )
    except auth_service.UsernameTaken as e:
        # The name is well-formed, just not free: 409, in words the Add User
        # dialog shows as they are.
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.patch("/api/users/{username}", name="users.update")
async def update_user(username: str, request: Request):
    """Edit an account in place: systems allowlist, role, enabled. Disabling
    kills the account's live session on the next request (session verification
    re-checks the account)."""
    _require_account_admin(request)
    _refuse_builtin(username, request, "edited")
    _require_over(request, username)
    try:
        body = await request.json()
    except Exception:
        body = {}
    _require_tier_grant(request, body)
    _require_page_grant(request, body)
    _require_cap_grant(request, body)
    _require_section_grant(request, body)
    # The recovery address is where a reset link goes, so whoever may set it
    # may take the account over. Master and the developer only -- a site
    # admin may manage the accounts below them, but not this.
    if "email" in body:
        _require_admin(request)
        # ...and not at all on a box that does not use email: an address
        # stored where nothing can mail it is the "way back in that is not
        # one" this whole setting exists to avoid. Removing one ("") stays
        # allowed, so turning email off never traps an address on a row.
        wants_address = bool(str(body.get("email") or "").strip())
        if wants_address and not sysconfig_service.email_enabled(
                request.app.state.config_dir):
            raise HTTPException(status_code=409, detail=(
                "this box does not use email - turn it on under "
                "Settings > System first"))
        # Checked here, written below, AFTER the rest of the body has been
        # accepted: an edit that half-applies and then reports failure is
        # worse than one that fails whole.
        try:
            # "" REMOVES the address; the service pops the row. Without
            # allow_empty this raised before set_user_email was ever reached,
            # so Remove email 400'd for every account but the master.
            auth_service.clean_email(body.get("email"), allow_empty=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    try:
        user = auth_service.update_user(
            username,
            systems=body.get("systems"),
            role=body.get("role"),
            enabled=body.get("enabled"),
            sections=body.get("sections"),
            pages=body.get("pages"),
            caps=body.get("caps"),
            admin=body.get("admin"),
            max_systems=body.get("max_systems"),
            config_dir=request.app.state.config_dir)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such user")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if "email" in body:
        try:
            user["email"] = auth_service.set_user_email(
                username, body.get("email"), request.app.state.config_dir)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": user}


@router.post("/api/users/{username}/password", name="users.reset_password")
async def reset_user_password(username: str, request: Request):
    _require_account_admin(request)
    _refuse_builtin(username, request, "reset")
    # A password reset IS a takeover of the account: whoever performs it can
    # then sign in as them. So it needs the same rank as any other edit.
    _require_over(request, username)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        await asyncio.to_thread(auth_service.set_user_password,
                                username, body.get("password"),
                                request.app.state.config_dir)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such user")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # An admin reset ends ALL of the account's sessions: whoever held the old
    # password (or a stolen session) is out until they sign in with the new one.
    auth_service.revoke_sessions_of(username,
                                    config_dir=request.app.state.config_dir)
    return {"ok": True}


@router.delete("/api/users/{username}", name="users.delete")
def delete_user(username: str, request: Request):
    _require_account_admin(request)
    _refuse_builtin(username, request, "deleted")
    _require_over(request, username)
    try:
        auth_service.delete_user(username, request.app.state.config_dir)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such user")
    return {"ok": True}


# -- the inbox ------------------------------------------------------------------
#
# Messages for the signed-in account (services/inbox.py), plus -- for master
# and the developer -- the open password-reset requests, each with its two
# buttons. One payload, because the bell draws one list.

@router.get("/api/inbox", name="inbox.list")
def inbox_list(request: Request):
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="login required")
    cfg = request.app.state.config_dir
    admin = auth_service.is_admin(me, cfg)
    notices = inbox_service.for_user(me, admin, cfg)
    requests_ = resetreq_service.pending(cfg) if admin else []
    return {"ok": True, "notices": notices, "requests": requests_,
            "unread": sum(1 for n in notices if not n["read"]) + len(requests_)}


@router.post("/api/inbox/read", name="inbox.read")
async def inbox_read(request: Request):
    """Body: {ids: [...]} or {} for all of mine."""
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="login required")
    body = await _json(request)
    ids = body.get("ids")
    inbox_service.mark_read(me, list(ids) if isinstance(ids, list) else None,
                            request.app.state.config_dir)
    return {"ok": True}


@router.delete("/api/inbox/{notice_id}", name="inbox.delete")
def inbox_delete(notice_id: str, request: Request):
    me = getattr(request.state, "user", None) or ""
    if not me:
        raise HTTPException(status_code=401, detail="login required")
    cfg = request.app.state.config_dir
    went = inbox_service.delete(me, notice_id, auth_service.is_admin(me, cfg), cfg)
    if not went:
        raise HTTPException(status_code=404, detail="no such message")
    return {"ok": True}
