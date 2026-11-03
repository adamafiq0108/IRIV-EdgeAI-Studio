# The hub's Network and Front Panel routes do not block its event loop
# (perf audit item L5, 2026-10-02).
#
# They were `async def` handlers calling nmcli (netconf.status is 12-18 forks)
# and systemctl / hailortcli in place, so while the Network page polled,
# every other request to the hub waited. Now the work runs on worker threads,
# and netconf.status is reused for STATUS_TTL_S, dropped by every write.
import asyncio
import io
import os
import sys
import threading
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException = HTTPException
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.Response = \
        resp.StreamingResponse = resp.FileResponse = object
    sys.modules["fastapi.responses"] = resp

from core.routes import network as net                               # noqa: E402

loop_thread = []
calls = {"status": 0, "join": 0}


def fake_status():
    calls["status"] += 1
    loop_thread.append(threading.current_thread() is threading.main_thread())
    return {"devices": [], "wifi": [], "ap": False}


def fake_join(ssid, password, hidden, autoconnect=True):
    calls["join"] += 1
    loop_thread.append(threading.current_thread() is threading.main_thread())
    return {"devices": [], "ap": False}


net.netconf = types.SimpleNamespace(status=fake_status, join=fake_join,
                                    NetError=Exception,
                                    wifi_mode=lambda *a: "wifi")
net._require_admin = lambda request: None
net._defaults = lambda payload: dict(payload)


def req(body=None):
    async def _json():
        return body or {}
    return types.SimpleNamespace(client=types.SimpleNamespace(host="10.0.0.9"),
                                 json=_json)


async def main():
    a = await net.network_status(req())
    b = await net.network_status(req())
    return a, b

a, b = asyncio.run(main())
check("the status answers as before", a["ok"] and "devices" in a)
check("two polls inside STATUS_TTL_S run nmcli once", calls["status"] == 1, calls)
asyncio.run(net.network_join(req({"ssid": "Plant", "password": "x"})))
asyncio.run(net.network_status(req()))
check("a write drops the cached status: the next poll asks again",
      calls["join"] == 1 and calls["status"] == 2, calls)
check("every netconf call ran OFF the event loop's thread",
      loop_thread and not any(loop_thread), loop_thread)

src = io.open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8").read()
body = src[src.index('@router.get("/api/hub/network"'):]
check("no handler calls netconf in place any more",
      "netconf.status()" not in body.replace("value = netconf.status()", "")
      and " = netconf." not in body and "return netconf." not in body)
panel = io.open(os.path.join(ROOT, "core", "routes", "panel.py"), encoding="utf-8").read()
check("the panel's systemctl / screen work runs on a worker thread",
      "await asyncio.to_thread(panel_service.status" in panel
      and "await asyncio.to_thread(apply)" in panel
      and "panel_service.preview, page" in panel)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
