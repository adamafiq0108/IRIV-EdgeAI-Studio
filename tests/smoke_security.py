# The security baseline, end to end: password hashing + migration, the login
# throttle, the session wall (including the non-/api routes that used to fall
# straight through), CSRF origin refusal, and cookie flags.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

CFG = tempfile.mkdtemp()

from core import config                                   # noqa: E402
from core.services import auth as auth_service            # noqa: E402
from core.services import ratelimit as rl                 # noqa: E402

# ---------------------------------------------------------------- passwords
auth_service.set_master_password("correct-horse", CFG)
raw = open(os.path.join(CFG, "auth.local.toml"), encoding="utf-8").read()
assert "correct-horse" not in raw, "the master password is still on disk in clear"
assert "pbkdf2$" in raw, raw[:200]
assert auth_service.verify_login("admin", "correct-horse", CFG) is True
assert auth_service.verify_login("admin", "wrong", CFG) is False
print("== master password stored hashed, not in clear; login still works")

# legacy cleartext deployment upgrades itself on first correct login
local = config.load_local("auth", CFG) or {}
local["master"] = {"password": "legacy-pass"}          # what an old box has
config.save_local("auth", local, CFG)
assert auth_service.verify_login("admin", "legacy-pass", CFG) is True
raw = open(os.path.join(CFG, "auth.local.toml"), encoding="utf-8").read()
assert "legacy-pass" not in raw, "cleartext survived the upgrade login"
assert auth_service.verify_login("admin", "legacy-pass", CFG) is True
assert auth_service.verify_login("admin", "nope", CFG) is False
print("== a legacy cleartext password is retired on first login, still accepted")

# user accounts: pbkdf2 + legacy sha256 migration
auth_service.create_user("alice", "s3cret-pass", "*", config_dir=CFG,
                         role="operator")
assert auth_service.verify_login("alice", "s3cret-pass", CFG) is True
assert auth_service.verify_login("alice", "s3cret-pas", CFG) is False
users = config.load_local("users", CFG)["users"]
assert users["alice"]["password_hash"].startswith("pbkdf2$"), users["alice"]

import hashlib                                            # noqa: E402
legacy_salt = "abcd1234"
users["alice"]["salt"] = legacy_salt
users["alice"]["password_hash"] = hashlib.sha256(
    (legacy_salt + "s3cret-pass").encode()).hexdigest()
config.save_local("users", {"users": users}, CFG)
assert auth_service.verify_login("alice", "s3cret-pass", CFG) is True
after = config.load_local("users", CFG)["users"]["alice"]["password_hash"]
assert after.startswith("pbkdf2$"), after
print("== a legacy sha256 user hash still logs in and is upgraded in place")

# the emailed reset token no longer carries a hash of the live password
token = auth_service.issue_reset_token(CFG)
pw_sha = hashlib.sha256("legacy-pass".encode()).hexdigest()[:12]
assert pw_sha not in token, "reset token still leaks the password hash"
assert auth_service.verify_reset_token(token, CFG) is True
auth_service.set_master_password("brand-new-pass", CFG)
assert auth_service.verify_reset_token(token, CFG) is False
print("== reset token leaks nothing about the password, still dies on change")

# ---------------------------------------------------------------- throttle
rl.reset()
t = 1000.0
for i in range(8):
    assert rl.check("login", "1.2.3.4|admin", now=t + i) == 0, i
assert rl.check("login", "1.2.3.4|admin", now=t + 8) > 0, "9th attempt not throttled"
assert rl.check("login", "1.2.3.4|bob", now=t + 8) == 0, "throttle bled across users"
assert rl.check("login", "9.9.9.9|admin", now=t + 8) == 0, "throttle bled across IPs"
rl.reset()
for i in range(12):
    rl.record_failure("login", "k", now=t)
assert rl.check("login", "k", now=t) > 0, "lockout never triggered"
rl.record_success("login", "k")
assert rl.check("login", "k", now=t) == 0, "success did not clear the lockout"
print("== login throttled per IP+user, locks out on repeated failures, clears on success")

# ---------------------------------------------------------------- the wall
# fastapi is a master-only dependency and is not installed on this dev box;
# the logic under test here is plain python, so stub the import surface.
import types                                               # noqa: E402

if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), patch=lambda *a, **k: (lambda f: f),
        delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    fa.Query = lambda *a, **k: None
    sys.modules["fastapi"] = fa
    fa.__path__ = []                     # make it a package for submodules
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.Response = \
        resp.StreamingResponse = object
    sys.modules["fastapi.responses"] = resp
    tpl = types.ModuleType("fastapi.templating")
    tpl.Jinja2Templates = lambda *a, **k: types.SimpleNamespace(
        env=types.SimpleNamespace(filters={}, globals={}))
    sys.modules["fastapi.templating"] = tpl
    stat = types.ModuleType("fastapi.staticfiles")
    stat.StaticFiles = object
    sys.modules["fastapi.staticfiles"] = stat

from core.routes import auth as auth_routes                # noqa: E402

# the bypass that let /video_feed and /footage run with no session
for path in ("/video_feed/abc", "/footage/abc", "/footage/abc/delete"):
    assert auth_routes._under(path, auth_routes.PROTECTED_PREFIXES), path
    assert not auth_routes._under(path, auth_routes.OPEN_PREFIXES), path
# open paths stay open, and only at a boundary
assert auth_routes.is_open("/api/health") and auth_routes.is_open("/api/health/box")
# Pipi L1 (2026-10-07): the health paths are open by name, not as a prefix, so
# a future /api/health/<x> is gated unless it is added to OPEN_EXACT on purpose
assert not auth_routes.is_open("/api/health/anything-new"), "health is a prefix again"
assert not auth_routes._under("/api/health/anything-new", auth_routes.OPEN_PREFIXES)
assert auth_routes._under("/assets/index-abc.js", auth_routes.OPEN_PREFIXES)
assert not auth_routes._under("/api/healthz-admin", auth_routes.OPEN_PREFIXES), \
    "unanchored prefix match is back"
assert not auth_routes._under("/loginsessions", auth_routes.OPEN_PREFIXES)
assert not auth_routes._under("/api/publicity", auth_routes.OPEN_PREFIXES)
print("== wall: protected non-/api routes named; open prefixes boundary-anchored")


# ---------------------------------------------------------------- CSRF
class _Req:
    def __init__(self, headers, host="192.168.1.63", scheme="http", cookies=None):
        self.headers = headers
        self.cookies = cookies or {}
        self.url = type("U", (), {"hostname": host, "scheme": scheme, "port": 5100})()


assert auth_routes._cross_site(_Req({"Origin": "http://evil.example"})) is True
assert auth_routes._cross_site(_Req({"Origin": "http://192.168.1.63:5100"})) is False
# the hub and a master are different PORTS of one deployment
assert auth_routes._cross_site(_Req({"Origin": "http://192.168.1.63:5126"})) is False
assert auth_routes._cross_site(_Req({"Referer": "http://evil.example/x"})) is True
assert auth_routes._cross_site(_Req({})) is False, "a curl/CLI caller must still work"
# "null" is a browser's word for a sandboxed frame, a file:// page or a
# cross-site redirect: a page, not ours (Pipi, 2026-10-05)
assert auth_routes._cross_site(_Req({"Origin": "null"})) is True
assert auth_routes._cross_site(_Req({"Origin": " NULL "})) is True
print("== cross-origin writes refused; same-deployment and CLI callers unaffected")

# Pipi M3 (2026-10-10): over TLS a write must come from an https page -- an
# injected http:// page on the SAME host is refused; plain HTTP is unchanged
_cs = auth_routes._cross_site
assert _cs(_Req({"Origin": "http://192.168.1.63:5100"}, scheme="https")) is True,     "an http page on the same host may write to the HTTPS side"
assert _cs(_Req({"Referer": "http://192.168.1.63/x"}, scheme="https")) is True
assert _cs(_Req({"Origin": "https://192.168.1.63:8443"}, scheme="https")) is False
assert _cs(_Req({"Origin": "https://192.168.1.63"}, scheme="https")) is False
assert _cs(_Req({"Origin": "https://evil.example"}, scheme="https")) is True
assert _cs(_Req({}, scheme="https")) is False, "a CLI caller over TLS must still work"
# through the hub's relay a master sees http + X-Forwarded-Proto: https
assert _cs(_Req({"Origin": "http://192.168.1.63", "X-Forwarded-Proto": "https"})) is True
assert _cs(_Req({"Origin": "https://192.168.1.63", "X-Forwarded-Proto": "https"})) is False
# plain HTTP: an https page of the same host may still write (as before)
assert _cs(_Req({"Origin": "https://192.168.1.63:8443"})) is False
print("== over TLS a same-host http:// page is cross-site; plain HTTP unchanged")

# Pipi L1: over TLS only the __Secure- cookie is the session (no fallback to
# a plain cookie an http:// response could have planted); HTTP reads its own
_both = {auth_service.SECURE_COOKIE: "tls-token", auth_service.SESSION_COOKIE: "plain-token"}
_plain = {auth_service.SESSION_COOKIE: "plain-token"}
assert auth_routes._session_cookie(_Req({}, scheme="https", cookies=_both)) == "tls-token"
assert auth_routes._session_cookie(_Req({}, scheme="https", cookies=_plain)) is None,     "a planted plain cookie is the session on HTTPS (fixation)"
assert auth_routes._session_cookie(_Req({}, cookies=_both)) == "plain-token"
assert auth_routes._session_cookie(_Req({}, cookies={
    auth_service.SECURE_COOKIE: "tls-token"})) is None
print("== over TLS only the __Secure- cookie counts; HTTP reads the plain one")


class _Resp:
    def __init__(self):
        self.deleted = []

    def delete_cookie(self, name, **kw):
        self.deleted.append((name, kw.get("secure")))


# ...and a sign-out over TLS ends BOTH cookies when they are the same account
auth_service.create_user("l1user", "l1-pass-123", "*", config_dir=CFG, role="operator")
auth_service.create_user("l1other", "l1-pass-456", "*", config_dir=CFG, role="operator")


def _signed_in(user):
    tok = auth_service.issue_session(user, CFG)
    auth_service.register_session(user, auth_service.session_sid(tok), config_dir=CFG)
    assert auth_service.verify_session(tok, CFG) == user
    return tok


_tA, _tB, _tC = _signed_in("l1user"), _signed_in("l1user"), _signed_in("l1other")
_r = _Resp()
auth_routes.end_both_cookies(_Req({}, scheme="https", cookies={
    auth_service.SECURE_COOKIE: _tB, auth_service.SESSION_COOKIE: _tA}), _r, "l1user", CFG)
assert auth_service.verify_session(_tA, CFG) is None, "the plain cookie's session lives on"
assert auth_service.verify_session(_tB, CFG) is None
assert _r.deleted == [(auth_service.SESSION_COOKIE, False)], _r.deleted
_tA2 = _signed_in("l1user")
_r = _Resp()
auth_routes.end_both_cookies(_Req({}, scheme="https", cookies={
    auth_service.SESSION_COOKIE: _tC}), _r, "l1user", CFG)
assert auth_service.verify_session(_tC, CFG) == "l1other" and _r.deleted == [],     "another account's plain cookie was signed out"
_r = _Resp()
auth_routes.end_both_cookies(_Req({}, cookies={
    auth_service.SESSION_COOKIE: _tA2}), _r, "l1user", CFG)
assert auth_service.verify_session(_tA2, CFG) == "l1user" and _r.deleted == [],     "plain-HTTP sign-out changed"
print("== TLS sign-out ends both cookies of the same account, never another's")

# ---------------------------------------------------------------- cookie flags
class _R2:
    headers = {"X-Forwarded-Proto": "https"}
    url = type("U", (), {"scheme": "http"})()


class _R3:
    headers = {}
    url = type("U", (), {"scheme": "http"})()


assert auth_routes._is_https(_R2()) is True
assert auth_routes._is_https(_R3()) is False, \
    "Secure would be set on plain HTTP -- nobody could log in"
print("== Secure cookie follows real TLS, never breaks plain-HTTP LAN login")
print("ALL OK")
