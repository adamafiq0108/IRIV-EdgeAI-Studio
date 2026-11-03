# -*- coding: utf-8 -*-
"""
authtls.py -- the session wall's view of the SCHEME: is this request on
TLS, which session cookie belongs to it, and is a write cross-site.

Lifted out of core/routes/auth.py (which re-exports every name here, the
names hub_server, master_server and the tests know) so that file stays
under its line ceiling when Pipi's HTTPS review (2026-10-10) M3 and L1 made
these rules scheme-aware. No routes here.

WHY THE SCHEME MATTERS NOW. Since the box serves HTTPS beside HTTP on the
SAME host, "same host" no longer means "same trust": a plain http:// page
on that host can be rewritten by anyone on the LAN who can spoof ARP, and
cookies ignore scheme and port, so such a page can also plant a cookie the
HTTPS side would read. So over TLS (M3) a write must come from an https
page, and (L1) the HTTPS side reads only its own __Secure- cookie -- which
a plain-HTTP response can never set -- and its sign-out ends the plain
cookie too when it is the same account. Plain HTTP behaves as before.
"""

import urllib.parse

from core.services import auth as auth_service


def _is_https(request):
    """True when the browser really is on TLS -- directly or via a proxy that
    terminated it and said so."""
    if request.url.scheme == "https":
        return True
    return request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() \
        .lower() == "https"


def _cookie_name(request):
    """The session cookie's name for this scheme. Over TLS it is its own
    __Secure- cookie: a Secure cookie under the plain name would block every
    plain-HTTP sign-in on the same host (a browser never lets http://
    replace a Secure cookie), and HTTP stays open beside the box's HTTPS."""
    return auth_service.SECURE_COOKIE if _is_https(request) else auth_service.SESSION_COOKIE


def _session_cookie(request):
    """The session cookie this request carries: over TLS the __Secure- one
    ONLY. Falling back to the plain name there was session fixation (Pipi
    L1): an http:// response forged on the LAN plants a plain cookie for the
    host, and a not-yet-signed-in HTTPS visitor silently works inside the
    attacker's account. The cost: a sign-in over HTTP is not carried onto
    the HTTPS address -- that browser signs in once more there."""
    return request.cookies.get(_cookie_name(request))


def end_both_cookies(request, resp, user, config_dir):
    """A sign-out over TLS ends BOTH session cookies when they are the SAME
    account (Pipi L1): someone signed in on http and later on https who
    signs out on https must be out, not back in on the plain cookie the next
    time this browser goes to http://. Each is revoked (its token dies
    everywhere) and the plain one deleted here; auth.py logout deletes the
    __Secure- one. Another account's cookie is left alone, as before."""
    if not (_is_https(request) and user):
        return
    for name in (auth_service.SECURE_COOKIE, auth_service.SESSION_COOKIE):
        token = request.cookies.get(name) or ""
        if not token or auth_service.verify_session(token, config_dir) != user:
            continue
        sid = auth_service.session_sid(token)
        if sid:
            auth_service.revoke_session(sid, config_dir=config_dir)
        if name == auth_service.SESSION_COOKIE:
            resp.delete_cookie(name, path="/", httponly=True, samesite="lax", secure=False)


def _cross_site(request):
    """True when a state-changing request came from another origin.

    The session cookie is SameSite=Lax, which already blocks cookie-bearing
    cross-site POSTs in a current browser -- but "the browser will handle it"
    is not a server-side control, and Lax still allows cross-site top-level
    GETs. Checking Origin/Referer costs nothing and does not need a token
    round-trip through every form in the SPA.

    A request with NEITHER header is allowed: that is a CLI/curl caller, which
    is not a browser and therefore not a CSRF vector (it has no ambient
    cookie), and the fleet's own tooling calls these endpoints. "Origin:
    null" is NOT that: a browser sends it for a sandboxed frame, a file://
    page or a cross-site redirect, none of which is this product (the kiosk
    Chromium browses the hub by http URL), so it is refused (Pipi, 2026-10-05).

    Over TLS the page must be https as well (Pipi M3, 2026-10-10): an
    injected http:// page on this very host could otherwise post a
    text/plain form whose body is JSON, with the Lax __Secure- cookie
    riding along in browsers without schemeful same-site.
    """
    origin = request.headers.get("Origin") or ""
    if not origin:
        referer = request.headers.get("Referer") or ""
        if not referer:
            return False
        origin = referer
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return True
    if not parts.hostname:
        return origin.strip().lower() == "null"
    host = request.url.hostname or ""
    # Same host = same deployment. The PORT deliberately does not have to
    # match: the hub (5100) and every master (511x) are one product to the
    # operator, and the SPA links across them by design (core/net.py).
    if parts.hostname.lower() != host.lower():
        return True
    return _is_https(request) and parts.scheme.lower() != "https"
