# Per-Tab Multi-Session: two accounts in two tabs of ONE browser.
#
# The browser holds one session cookie per host, shared by every tab and
# every port -- so a second login used to silently turn every other tab into
# the new user (dev's page becoming the admin page, live). Each tab now keeps
# its own token in sessionStorage and sends it as X-Session, which the server
# trusts BEFORE the cookie. The cookie stays as the identity for what cannot
# carry a header (<img> streams, <a> downloads).
#
# Source-assert style: the contract lives in specific lines of specific
# files, and this pins each one so a refactor cannot quietly drop half of it.
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- backend: the middleware ---------------------------------------------------
SRC = read("core", "routes", "auth.py")
mw = SRC[SRC.index("def require("):]
mw = mw[:mw.index("\n@router")]
# Header first, cookie as fallback...
assert 'request.headers.get("X-Session")' in mw, "middleware never reads X-Session"
# (the cookie is read through _session_cookie, core/routes/authtls.py, which
# picks the scheme's own cookie since HTTPS came)
assert mw.index('X-Session') < mw.index("_session_cookie(request)"), \
    "the cookie is read before the header"
# ...and NEVER the other way when the header is present but dead: falling
# back would silently turn a stale tab into the cookie's user.
assert "if not token:" in mw, mw
assert "auth_via" in mw and "session_sid" in mw, mw
print("== require(): X-Session first, no cookie fallback for a dead header")

# -- login returns the token in the body, and still sets the cookie -----------
login = SRC[SRC.index('name="auth.login_json"'):]
login = login[:login.index("@router.post", 10)]
assert '"token": token' in login, "login does not return the token"
assert "set_cookie" in login, "login stopped setting the cookie (streams die)"
assert "register_session" in login, "login does not register the session"
assert "asyncio.to_thread" in login, \
    "PBKDF2 back on the event loop -- every login stalls every user"
print("== login: token in the body, cookie kept for media, hashing threaded")

# -- keepalive: renews the tab's token, does not stomp the shared cookie -------
ka = SRC[SRC.index('name="auth.keepalive"'):]
ka = ka[:ka.index("@router.post", 10)]
assert '"token": token' in ka, "keepalive does not return the fresh token"
assert 'auth_via", "cookie") == "cookie"' in ka, \
    "a header-mode lock would rewrite the shared cookie on a timer"
assert "register_session" in ka, "a legacy session's renewal is never registered"
print("== keepalive: fresh token in the body, cookie only for cookie-mode")

# -- logout: signs out ME, spares the other person's cookie --------------------
lo = SRC[SRC.index('name="auth.logout_json"'):]
lo = lo[:lo.index("@router.post", 10)]
assert "revoke_session" in lo, "logout does not revoke the registry row"
assert "hu != cu" in lo, "logout deletes the cookie across identities"
print("== logout: revokes this session; the cookie survives a different user")

# -- per-user throttle keys ----------------------------------------------------
pw = SRC[SRC.index('name="auth.change_own_password"'):]
pw = pw[:pw.index("@router.post", 10)]
assert 'f"{ip}|{me}"' in pw, "password throttle is per-IP: one NAT, one bucket"
print("== password/username throttles are per (IP, account)")

# -- frontend ------------------------------------------------------------------
sess = read("frontend", "src", "lib", "session.js")
assert "sessionStorage" in sess and "X-Session" in sess, sess[:200]
assert "window.name" in sess and "export function goWithSession" in sess, \
    "the hand-off does not ride window.name"
assert "?session=" not in sess, "the token must never ride a query string"
assert 'window.name = ""' in sess, "the intake leaves the token in window.name"
code = "\n".join(l for l in sess.splitlines() if not l.lstrip().startswith("//"))
assert "#session" not in code, "the token must never ride the address, fragment included"
print("== session.js: per-tab storage, window.name hand-off, never in the address")

main = read("frontend", "src", "main.jsx")
assert main.index('import "./lib/session.js"') < main.index('import { router'), \
    "the fragment intake runs after the router -- bootstrap races the token"

client = read("frontend", "src", "api", "client.js")
assert "authHeaders()" in client, "the api client never sends the header"
assert "clearToken()" in client, "a dead token keeps 401-ing the tab forever"

login_jsx = read("frontend", "src", "pages", "Login.jsx")
assert "setToken(d.token)" in login_jsx, "login never stores the tab token"
# The bounce target is now `hub` -- the login RESPONSE's hub_url, falling back
# to the /api/public one (smoke_loginlanding.py). What this pins is unchanged:
# it is a cross-PORT move, so the identity has to ride in the fragment.
assert "withSession(hub)" in login_jsx, "master->hub bounce loses the identity"

hub = read("frontend", "src", "pages", "Hub.jsx")
assert "withSession(card.url)" in hub, "opening a system loses the tab identity"

for shell in ("MasterShell", "StandaloneShell"):
    text = read("frontend", "src", "components", f"{shell}.jsx")
    assert "withSession(hubUrl)" in text, f"{shell}: back-to-hub loses identity"

out = read("frontend", "src", "components", "SignOutDialog.jsx")
assert "clearToken()" in out, "sign-out leaves the tab token behind"

print("== frontend: client header, login/sign-out wiring, cross-port links")

# -- sessions UI ---------------------------------------------------------------
panel = read("frontend", "src", "components", "AccountsPanel.jsx")
assert "SessionsPane" in panel and "/api/auth/sessions" in panel, panel[:200]
assert "revoke-others" in panel, "no 'sign out other sessions'"
assert "this session" in panel, "the caller's own row is not marked"
print("== the Accounts page has the Sessions tab, with revoke and revoke-others")
print("ALL OK")
