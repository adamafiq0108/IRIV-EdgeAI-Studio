# Three production-audit defects, pinned so they cannot come back.
#
#   1. Detection events that failed to reach Postgres were logged at DEBUG
#      under an INFO root, so the record was dropped before any handler saw
#      it. On a board that is an `except: pass`: the live tiles keep
#      animating off the in-memory ring while the Event Table, Analytics and
#      every history chart return nothing, and the Logs page stays clean --
#      so the first person to notice is the customer, weeks of detections
#      later.
#   2. The login route took the posted username with no length cap and wrote
#      it to the audit table on every failure. Audit rows are never pruned by
#      design, and the throttle cannot bound this because its bucket is keyed
#      ON the username: a fresh value is a fresh bucket. An unauthenticated
#      caller could grow the table until the disk went.
#   3. Nothing in the tree set X-Frame-Options, a CSP or nosniff. The CSRF
#      check refuses a cross-origin write, but a page that FRAMES the
#      dashboard produces requests whose Origin IS the board, so they pass
#      it -- the operator's clicks land on our page with their live cookie.
import asyncio
import io
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

CFG = tempfile.mkdtemp()

# fastapi is a master-only dependency and is not installed on this dev box;
# the logic under test is plain python, so stub the import surface (the same
# trick as tests/smoke_security.py). JSONResponse is the one stub that needs
# real behaviour: this test is ABOUT the headers on it.
import types                                               # noqa: E402


class _Resp:
    """Stands in for a starlette Response: headers are all this test reads."""

    def __init__(self, content=None, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = dict(headers or {})


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
    resp.JSONResponse = _Resp
    resp.RedirectResponse = resp.Response = resp.StreamingResponse = object
    sys.modules["fastapi.responses"] = resp
    tpl = types.ModuleType("fastapi.templating")
    tpl.Jinja2Templates = lambda *a, **k: types.SimpleNamespace(
        env=types.SimpleNamespace(filters={}, globals={}))
    sys.modules["fastapi.templating"] = tpl
    stat = types.ModuleType("fastapi.staticfiles")
    stat.StaticFiles = object
    sys.modules["fastapi.staticfiles"] = stat

from core.routes import auth as auth_routes               # noqa: E402
from core.services import audit as audit_service          # noqa: E402
from core.services import auth as auth_service            # noqa: E402
from core.services import logs as logs_service            # noqa: E402
from core.services import ratelimit as rl                 # noqa: E402

# -- 1. a failed event write has to be VISIBLE --------------------------------
#
# Read the source rather than provoking a Postgres outage: what matters is the
# level on the call, and that is what the field bug was.
src = io.open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
for message in ("event persistence skipped",
                "local event persistence skipped",
                "local ingest failed",
                "history sample failed"):
    call = re.search(r"logger\.(\w+)\(\"\[MASTER\] " + re.escape(message), src)
    assert call, f"the {message!r} log line is gone -- did the handler change?"
    assert call.group(1) in ("error", "exception", "critical"), \
        (f"{message!r} is logged at {call.group(1)}(), which the root INFO "
         f"level discards: the failure is invisible on the Logs page")
    tail = src[call.end():call.end() + 120]
    assert "exc_info" in tail or call.group(1) == "exception", \
        f"{message!r} logs no traceback, so the line says nothing actionable"
print("== every dropped-events path logs at ERROR, with its traceback")

# ...and the mechanism itself: at the shipped root level, DEBUG never reaches
# the ring the Logs page reads. This is why the level, not the message, was
# the bug.
import logging                                             # noqa: E402
logs_service.install()
logging.getLogger().setLevel(logging.INFO)
logs_service.clear()
logging.getLogger("cytron.test").debug("dropped events at debug")
logging.getLogger("cytron.test").error("dropped events at error")
blob = "\n".join(e["message"] for e in logs_service.query(limit=50))
assert "dropped events at debug" not in blob, \
    "DEBUG reached the ring -- this test proves nothing on this box"
assert "dropped events at error" in blob
print("== a DEBUG record never reaches the Logs page; an ERROR one does")

# -- 2. the login username is capped before the bucket and the audit row ------
#
# The cap is not a number picked here: it is what an account may actually be
# called (core/services/auth.py, set_admin_username). If the product ever
# allows longer names, this fails and the route is updated with it.
try:
    auth_service.set_admin_username("a" * (auth_routes.USERNAME_MAX + 1), CFG)
    raise AssertionError("the account rule now allows a longer name than the "
                         "login route caps at -- USERNAME_MAX is stale")
except ValueError:
    pass
print(f"== the route's cap ({auth_routes.USERNAME_MAX}) is the account rule, not a guess")

seen = {}


def _fake_record(action, username=None, **kw):
    seen["username"] = username
    return True


class _Client:
    host = "192.168.1.90"


class _Req:
    """The little of a Request that login_json touches."""

    def __init__(self, body):
        self._body = body
        self.method = "POST"
        self.headers = {}
        self.client = _Client()
        self.app = type("A", (), {"state": type("S", (), {"config_dir": CFG})()})()

    async def json(self):
        return self._body


rl.reset()
auth_service.set_master_password("correct-horse", CFG)
real_record, audit_service.record = audit_service.record, _fake_record
buckets = []
real_check = auth_routes.ratelimit_service.check
auth_routes.ratelimit_service.check = lambda name, bid: (buckets.append(bid), real_check(name, bid))[1]
try:
    flood = "z" * 5000
    for _ in range(2):
        try:
            asyncio.run(auth_routes.login_json(
                _Req({"username": flood, "password": "no"})))
        except Exception:                                  # 401/429, both fine
            pass
finally:
    audit_service.record = real_record
    auth_routes.ratelimit_service.check = real_check

assert seen.get("username") is not None, "the failed login wrote no audit row at all"
assert len(seen["username"]) <= auth_routes.USERNAME_MAX, \
    (f"{len(seen['username'])} characters went into the durable audit table "
     f"from an unauthenticated caller")
assert buckets and all(len(b) <= 64 for b in buckets), \
    f"the throttle bucket is still unbounded: {len(buckets[0])} chars"
print("== an oversized login name is truncated before the bucket and the audit row")

# -- 3. framing and sniffing defences on every response -----------------------


class _MwReq:
    def __init__(self, path="/", method="GET", headers=None):
        self.method = method
        self.headers = headers or {}
        self.cookies = {}
        self.url = type("U", (), {"path": path, "hostname": "192.168.1.63",
                                  "scheme": "http"})()
        self.state = type("St", (), {})()
        self.app = type("A", (), {"state": type("S", (), {"config_dir": CFG})()})()


async def _next(request):
    return _Resp("shell")


mw = auth_routes.require("demo")


def _headers_of(request):
    return asyncio.run(mw(request, _next)).headers


for label, request in (
        # The SPA shell on a logged-out browser -- the page an attacker frames.
        ("logged-out page", _MwReq("/")),
        # A static asset: OPEN_PREFIXES returns early, and nosniff matters most
        # exactly there.
        ("open prefix", _MwReq("/assets/index.js")),
        # And the refusal responses, so no path leaks an unprotected page.
        ("cross-site refusal",
         _MwReq("/api/auth/login", "POST", {"Origin": "http://evil.example"})),
        ("session 401", _MwReq("/api/devices"))):
    headers = _headers_of(request)
    assert headers.get("X-Frame-Options") == "DENY", \
        f"{label}: framable, so the CSRF origin check can be walked around"
    assert "frame-ancestors" in (headers.get("Content-Security-Policy") or ""), \
        f"{label}: no CSP frame-ancestors"
    assert headers.get("X-Content-Type-Options") == "nosniff", \
        f"{label}: a browser may still sniff a declared content type"
print("== every middleware exit carries DENY, frame-ancestors and nosniff")

# The CSP stays framing-only on purpose: a script-src here would have to track
# whatever the Vite build emits, and a CSP that blanks the dashboard on a
# customer board is worse than no CSP at all.
csp = _headers_of(_MwReq("/")).get("Content-Security-Policy")
assert "script-src" not in csp, \
    "a script-src CSP shipped without a build that is verified against it"
print("== the CSP is deliberately frame-ancestors only")

print("ALL OK")
