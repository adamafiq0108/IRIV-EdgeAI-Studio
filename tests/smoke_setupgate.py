# Account setup is a gate on the server, not a redirect in the browser.
#
# UAT-103 (Ochobot, first run on board "iriv", 2026-10-04; Pipi MED): the
# login answered needs_account_setup and the SPA went to the setup screen,
# but with the same token every API -- /api/hub, /api/users, the network page
# with the AP key in it -- answered 200 while the box was still on iriv1234.
#
# What this pins, driving the real middleware (fastapi is stubbed: it is not
# installed on a dev box):
#
#   * the master on the shipped password gets 409 + X-Account-Setup-Required
#     on /api/* and the PROTECTED_PREFIXES, and the setup calls stay open;
#   * an operator is never gated (they cannot change the master's password);
#   * /api/auth/password refuses the shipped string as the NEW one, and a
#     real change opens the box: the same call answers 200 straight after;
#   * the answer is cached on auth.local.toml's mtime (PBKDF2 is ~0.15 s on a
#     Pi) and the cache drops on our own write AND on another process's;
#   * both bootstraps carry the flag, the shell and the client act on it.
#
#     py -3 tests/smoke_setupgate.py
import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _JSONResponse:
    def __init__(self, content=None, status_code=200, headers=None):
        self.content, self.status_code = content, status_code
        self.headers = dict(headers or {})

    def set_cookie(self, *a, **k):
        pass


if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail, self.headers = status_code, detail, headers
    fa.HTTPException = HTTPException
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = _JSONResponse
    resp.RedirectResponse = resp.Response = resp.StreamingResponse = \
        resp.FileResponse = _JSONResponse
    sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402
from core import config                                      # noqa: E402
from core.routes import auth as auth_routes                  # noqa: E402
from core.services import auth as auth_service               # noqa: E402

SHIPPED = auth_service.SHIPPED_PASSWORD
CFG = tempfile.mkdtemp()
auth_service.set_master_password(SHIPPED, CFG)
ADMIN = auth_service.admin_username(CFG)
STATE = types.SimpleNamespace(config_dir=CFG)


def login(name):
    token = auth_service.issue_session(name, CFG)
    auth_service.register_session(name, auth_service.session_sid(token),
                                  device="test", ip="127.0.0.1", config_dir=CFG)
    return token


def request(path, token, method="GET", body=None):
    async def _json():
        return body or {}
    return types.SimpleNamespace(
        url=types.SimpleNamespace(path=path, hostname="box", scheme="http"),
        method=method, headers={"X-Session": token}, cookies={},
        client=types.SimpleNamespace(host="127.0.0.1"),
        app=types.SimpleNamespace(state=STATE), state=types.SimpleNamespace(),
        json=_json, body=_json)


MIDDLEWARE = auth_routes.require()


def hit(path, token, method="GET"):
    async def call_next(req):
        return _JSONResponse({"ok": True}, 200)
    return asyncio.run(MIDDLEWARE(request(path, token, method), call_next))


tok = login(ADMIN)

# -- gated while on the shipped password ---------------------------------------
r = hit("/api/hub", tok)
check("the master on the shipped password gets 409 on /api/hub",
      r.status_code == 409, r.status_code)
check("...tagged so the client knows to go to setup",
      r.headers.get("X-Account-Setup-Required") == "1"
      and (r.content or {}).get("needs_account_setup") is True
      and (r.content or {}).get("error") == "finish account setup first",
      (r.headers, r.content))
for p in ("/api/users", "/api/hub/network", "/footage/list", "/video_feed/x"):
    check(f"{p} is gated too", hit(p, tok).status_code == 409)
for p in ("/api/auth/account-setup", "/api/auth/password", "/api/auth/me",
          "/api/auth/keepalive", "/api/bootstrap", "/api/public",
          "/api/auth/logout", "/api/health"):
    check(f"{p} stays open for the setup screen", hit(p, tok).status_code == 200)
check("a page path still gets the shell (the SPA routes to setup)",
      hit("/systems", tok).status_code == 200)

# -- the update state is readable under the gate; nothing about it is writable --
for p in ("/api/hub/update", "/api/hub/update?channel=stable",
          "/api/hub/update/status"):
    check(f"GET {p} is answered under the gate (the System tab's version line)",
          hit(p, tok).status_code == 200)
for p in ("/api/hub/update/check", "/api/hub/update/token",
          "/api/hub/update/apply", "/api/hub/update/rollback"):
    check(f"POST {p} is still gated", hit(p, tok, "POST").status_code == 409)
check("a GET that merely begins with the open prefix is gated",
      hit("/api/hub/updates", tok).status_code == 409)

# -- an operator is never gated --------------------------------------------------
auth_service.create_user("op1", "operator-pass", ["*"], CFG)
check("an operator is not held at the master's setup gate",
      hit("/api/hub", login("op1")).status_code == 200)

# -- the cache: one PBKDF2, not one per request ---------------------------------
calls = {"n": 0}
real = auth_service.master_password_is_default


def counted(cfg=None):
    calls["n"] += 1
    return real(cfg)


auth_service.master_password_is_default = counted
for _ in range(5):
    hit("/api/hub", tok)
check("the gate's answer is cached (no PBKDF2 per request)",
      calls["n"] == 0, calls)

# -- /api/auth/password refuses the shipped string as the new one ---------------
async def change(current, password):
    req = request("/api/auth/password", tok, "POST",
                  {"current": current, "password": password})
    req.state.user = ADMIN
    req.state.session_sid = auth_service.session_sid(tok)
    return await auth_routes.change_own_password(req)

try:
    asyncio.run(change(SHIPPED, SHIPPED))
    check("/api/auth/password refuses the shipped password as the new one", False,
          "it was accepted")
except HTTPException as e:
    check("/api/auth/password refuses the shipped password as the new one",
          e.status_code == 400 and "shipped" in str(e.detail), e.detail)
check("...and the refusal changed nothing", real(CFG) is True)

# -- 409 then 200 ------------------------------------------------------------------
out = asyncio.run(change(SHIPPED, "a-real-password"))
check("a real change goes through", (out or {}).get("ok") is True, out)
check("the same call that was refused answers 200 straight after",
      hit("/api/hub", tok).status_code == 200)
check("...the cache was dropped by our own write and re-derived once",
      calls["n"] == 1, calls)

# -- another process's write is seen by mtime ------------------------------------
local = config.load_local("auth", CFG)
local["master"]["password_hash"] = auth_service._hash(SHIPPED,
                                                      local["master"]["salt"])
config.save_local("auth", local, CFG)            # bypasses _save_local
path = os.path.join(CFG, "auth.local.toml")
st = os.stat(path)
os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
check("another process putting the shipped password back re-arms the gate",
      hit("/api/hub", tok).status_code == 409)
auth_service.master_password_is_default = real

# -- the bootstraps and the client ------------------------------------------------
for f in (("core", "routes", "hub.py"), ("core", "routes", "spa.py")):
    src = read(*f)
    boot = src.split("def bootstrap")[1].split("\n@router")[0]
    check(f"{f[-1]} bootstrap carries needs_account_setup",
          '"needs_account_setup": auth_service.setup_pending(username, cfg)' in boot)
shell = read("frontend", "src", "components", "AppShell.jsx")
check("AppShell sends a pending box to /account-setup",
      "boot.needs_account_setup" in shell
      and '<Navigate to="/account-setup" replace />' in shell)
client = read("frontend", "src", "api", "client.js")
check("the client bounces on the gate's 409 from both fetch paths",
      client.count("if (setupRequired(res, data)) {") == 2
      and 'res.headers.get("X-Account-Setup-Required")' in client
      and "data.needs_account_setup" in client)
check("...and offers callers the same question of a thrown error",
      "export function isSetupRequired(e)" in client)
panel = read("frontend", "src", "components", "UpdatePanel.jsx")
check("UpdatePanel words the refusal itself, with the way there",
      "Finish account setup first" in panel and "isSetupRequired(e)" in panel
      and "Go to account setup" in panel
      and "finish account setup first" not in panel.replace(
          "Finish account setup first", ""))
check("...and reloads a tab the update left on the old bundle",
      "window.location.reload()" in panel
      and "window.location.reload()" in shell)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
