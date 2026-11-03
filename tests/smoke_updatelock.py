# -*- coding: utf-8 -*-
# The update routes never block the hub's event loop, and two "Check now"
# clicks make one GitHub request.
#
# Board report (v1.0.15): POST /api/hub/update/check and /apply called the
# service inline from an `async def`, so a dead uplink's 60 s timeout held
# the loop -- every page and every API stalled until GitHub failed to
# answer. Pinned, with _api patched to block on an event:
#   1. while a check is out, another coroutine on the same loop is answered
#      (the loop is free: the check runs in a thread)
#   2. a second check during the first returns at once with the cached state
#      marked `checking`, and GitHub was asked ONCE
#   3. the first check's own answer is not `checking`, and once it is home
#      the lock is free (GET state says checking false)
#   4. the timer's check WAITS for a running one instead of skipping its turn
#   5. the routes file calls check / apply / rollback only through
#      asyncio.to_thread, and the panel does not toast "up to date" on a
#      `checking` answer
import asyncio
import os
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()


class _JSONResponse:
    def __init__(self, content=None, status_code=200, headers=None):
        self.content, self.status_code = content, status_code
        self.headers = dict(headers or {})

    def set_cookie(self, *a, **k):
        pass


# The dev box has no fastapi; the same stand-in smoke_setupgate uses.
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

# The gate is auth's business (smoke_setupgate); this test is about the loop.
# routes/update.py takes only the two gate functions from routes/hub.py, and
# loading all of hub.py under the stub is not worth it.
hub_stub = types.ModuleType("core.routes.hub")
hub_stub._require_admin = lambda request, action: None
hub_stub._require_developer = lambda request, action: None
sys.modules["core.routes.hub"] = hub_stub

from core.routes import update as routes                       # noqa: E402
from core.services import update as upd                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


CFG = tempfile.mkdtemp()
GATE = threading.Event()          # released by the test: GitHub "answers"
CALLS = []


class _Resp:
    headers = {"ETag": 'W/"x"'}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        return b'[{"tag_name": "v9.9.9", "body": "", "assets": [], "draft": false, "prerelease": false}]'


def slow_api(path, tok, accept="", headers=None, config_dir=None, channel=None):
    CALLS.append(time.monotonic())
    GATE.wait(10)
    return _Resp()


upd._api = slow_api
upd.token = lambda config_dir=None, channel=None: "ghp_fake"
upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})


def request(body=None):
    async def _json():
        return body or {}
    return types.SimpleNamespace(
        url=types.SimpleNamespace(path="/api/hub/update/check"),
        query_params={}, method="POST", headers={},
        app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)),
        state=types.SimpleNamespace(user="admin"), json=_json)


async def scenario():
    out = {}
    first = asyncio.create_task(routes.check(request({"channel": "stable"})))
    # Give the thread time to reach GitHub (the blocked fake).
    for _ in range(50):
        if CALLS:
            break
        await asyncio.sleep(0.02)
    out["reached"] = bool(CALLS)

    # 1. the loop answers something else meanwhile
    t0 = time.monotonic()
    other = await asyncio.wait_for(
        asyncio.to_thread(lambda: "alive"), timeout=2)
    out["other"] = (other, time.monotonic() - t0, first.done())
    # ...including a plain read of the update state
    st = routes.state(request())
    out["state_mid"] = st["update"]

    # 2. a second "Check now" while the first is out
    t0 = time.monotonic()
    second = await asyncio.wait_for(
        routes.check(request({"channel": "stable"})), timeout=2)
    out["second"] = (second["update"], time.monotonic() - t0, len(CALLS))

    # 3. let GitHub answer; the first comes home
    GATE.set()
    done = await asyncio.wait_for(first, timeout=5)
    out["first"] = done["update"]
    out["state_after"] = routes.state(request())["update"]
    out["calls"] = len(CALLS)
    return out


r = asyncio.run(scenario())
check("the fake GitHub was reached by the first check", r["reached"])
other, took, first_done = r["other"]
check("1. another coroutine is answered while the check is out",
      other == "alive" and took < 1 and not first_done, f"{took:.2f}s done={first_done}")
check("...and GET state answers too, saying a check is out",
      r["state_mid"].get("checking") is True, str(r["state_mid"]))
second, took, calls = r["second"]
check("2. a second check during the first returns at once",
      took < 1, f"{took:.2f}s")
check("...marked checking, with the cached state, not an error",
      second.get("checking") is True and second.get("error") == ""
      and second.get("latest") == "", str(second))
check("...and GitHub was asked once, not twice", calls == 1, str(calls))
check("3. the first check's own answer is the release and not `checking`",
      r["first"].get("latest") == "v9.9.9" and r["first"].get("checking") is False,
      str(r["first"]))
check("...and the lock is free afterwards", r["state_after"].get("checking") is False
      and r["calls"] == 1, str(r["state_after"]))

# 4. the timer waits for a running check instead of skipping its turn
GATE.clear()
CALLS.clear()
got = {}
held = threading.Thread(target=lambda: upd.check(config_dir=CFG, channel="stable"))
held.start()
for _ in range(50):
    if CALLS:
        break
    time.sleep(0.02)
timer = threading.Thread(
    target=lambda: got.setdefault("timer", upd.check(config_dir=CFG, channel="stable",
                                                     wait=True)))
timer.start()
time.sleep(0.2)
check("4. the timer's check is queued behind the running one",
      timer.is_alive() and "timer" not in got and len(CALLS) == 1)
GATE.set()
timer.join(5)
held.join(5)
check("...and runs its own check once that one is home",
      got.get("timer", {}).get("latest") == "v9.9.9"
      and got["timer"].get("checking") is False and len(CALLS) == 2,
      str((got, len(CALLS))))

# 5. source pins
src = read("core", "routes", "update.py")
for name in ("check", "apply", "rollback"):
    check(f"5. the route calls update_service.{name} only through to_thread",
          f"update_service.{name}(" not in src
          and f"update_service.{name}," in src)
check("...three to_thread calls, one per blocking verb",
      src.count("asyncio.to_thread(") == 3, str(src.count("asyncio.to_thread(")))
svc = read("core", "services", "update.py")
loop_src = svc.split("def start_checker")[1].split("_checker = threading.Thread")[0]
check("...the background timer passes wait=True", "wait=True" in loop_src)
panel = read("frontend", "src", "components", "UpdatePanel.jsx")
check("...the panel does not say 'up to date' on a `checking` answer",
      "if (d.update?.checking) {" in panel
      and panel.index("d.update?.checking") < panel.index("This box is up to date"))

print("FAILED" if bad else "ALL OK")
sys.exit(1 if bad else 0)
