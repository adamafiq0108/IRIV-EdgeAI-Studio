"""The master's side of [video] overlay = "client".

The page learns who draws the overlay from answers it already gets -- GET
/api/detector/webrtc (every live feed asks it first) and a dashboard video
tile's own data -- and reads the overlay through GET /api/detector/overlay,
a relay of the app's loopback /overlay events. This pins:

  1. the master and the app read [video] the same way (the app cannot
     import core/, so the rule lives twice)
  2. server mode (the default) changes neither answer by a single key
  3. the relay: 404 in server mode, 403 for a cross-site read, the app's own
     404 passed through, 502 for a dead app, and the event bytes relayed
     as they arrive, as text/event-stream

    python tests/smoke_overlayroute.py
"""
import http.server
import os
import socket
import sys
import tempfile
import threading
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from _asgi_stream import PageStream, stub_streaming_classes   # noqa: E402

# The dev box runs the suite without FastAPI; the routes only need its names
# (the same stub smoke_previewerror uses, with a StreamingResponse that keeps
# what it was given).
if "fastapi" not in sys.modules:
    try:
        import fastapi                                         # noqa: F401
    except ImportError:
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
        fa.Request = fa.UploadFile = object
        fa.Query = fa.File = fa.Form = lambda *a, **k: None
        fa.__path__ = []
        sys.modules["fastapi"] = fa
        resp = types.ModuleType("fastapi.responses")

        resp.StreamingResponse, _BG = stub_streaming_classes(types)
        resp.JSONResponse = resp.RedirectResponse = resp.FileResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp
        st = types.ModuleType("starlette")
        st.__path__ = []
        bg = types.ModuleType("starlette.background")
        bg.BackgroundTask = _BG
        sr = types.ModuleType("starlette.requests")
        sr.ClientDisconnect = type("ClientDisconnect", (Exception,), {})
        sys.modules.update({"starlette": st, "starlette.background": bg,
                            "starlette.requests": sr})

from fastapi import HTTPException                              # noqa: E402

from app import overlay_feed as app_overlay                    # noqa: E402
from core.routes import models as models_routes                # noqa: E402
from core.routes import overlay as overlay_routes              # noqa: E402
from core.services import builder as builder_service           # noqa: E402
from core.services import overlay_view                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. one rule, read twice ----------------------------------------------------
CASES = [{}, {"video": {}}, {"video": "nope"}, {"video": {"overlay": "client"}},
         {"video": {"overlay": "CLIENT", "overlay_hz": "4", "overlay_delay_ms": 120}},
         {"video": {"overlay": "server", "overlay_hz": 999, "overlay_delay_ms": -5}},
         {"video": {"overlay": "x", "overlay_hz": "fast", "overlay_delay_ms": "slow"}}]
check("the master reads [video] exactly as the app does",
      all(overlay_view.settings(c) == app_overlay.settings(c) for c in CASES),
      [(overlay_view.settings(c), app_overlay.settings(c)) for c in CASES])
check("default is server, hz 10, no delay",
      overlay_view.settings({}) == {"mode": "server", "hz": 10.0, "delay_ms": 0})
check("page_info: nothing at all in server mode",
      overlay_view.page_info({}) == {}
      and overlay_view.page_info({"video": {"overlay": "server"}}) == {})
check("page_info: the mode and delay in client mode",
      overlay_view.page_info(CASES[4]) == {"overlay": {"mode": "client", "delay_ms": 120}})

# -- 2. the answers a page already gets ------------------------------------------
CFG = {"value": {}}
dc = models_routes.detector_control_service
dc._merged = lambda pkg: CFG["value"]
dc._pkg_dir = lambda d, s: os.path.join(d, s)
from core.services import streaming as streaming_service       # noqa: E402
streaming_service.mode = lambda cfg, slug, cam: "webrtc"
streaming_service.gateway_running = lambda: True
streaming_service.stream_ready = lambda slug, cam: True
REQ_APP = types.SimpleNamespace(state=types.SimpleNamespace(
    system=types.SimpleNamespace(slug="demo"), systems_dir=tempfile.mkdtemp()))
req = types.SimpleNamespace(app=REQ_APP, headers={},
                            url=types.SimpleNamespace(hostname="10.0.0.5"))
before = models_routes.detector_webrtc_state(req, camera="cam1")
check("server mode: /api/detector/webrtc answers exactly the old keys",
      sorted(before) == ["configured", "gateway", "mode", "path", "ready"], before)
CFG["value"] = {"video": {"overlay": "client", "overlay_delay_ms": 80}}
after = models_routes.detector_webrtc_state(req, camera="cam1")
check("client mode: ...plus the overlay mode and delay",
      after.get("overlay") == {"mode": "client", "delay_ms": 80}
      and {k: v for k, v in after.items() if k != "overlay"} == before, after)
CFG["value"] = {}
w = {"kind": "video", "camera": "cam2"}
builder_service._camera_inactive = lambda *a, **k: False
tile = builder_service._data(w, [], {}, slug="demo", systems_dir=REQ_APP.state.systems_dir)
check("server mode: a dashboard video tile's data is unchanged",
      sorted(tile) == ["camera", "inactive", "url"], tile)
CFG["value"] = {"video": {"overlay": "client"}}
tile = builder_service._data(w, [], {}, slug="demo", systems_dir=REQ_APP.state.systems_dir)
check("client mode: the tile is told to draw the overlay",
      tile.get("overlay") == {"mode": "client", "delay_ms": 0}, tile)

# -- 3. the relay ---------------------------------------------------------------------
check("cross-site: another site's Origin is refused",
      overlay_view.cross_site("http://evil.example", None, "10.0.0.5"))
check("cross-site: the browser's own cross-site verdict is refused",
      overlay_view.cross_site(None, "cross-site", "10.0.0.5"))
check("cross-site: Origin null is refused",
      overlay_view.cross_site("null", None, "10.0.0.5"))
check("same host on another port (the hub) is the same site",
      not overlay_view.cross_site("http://10.0.0.5:5100", "same-site", "10.0.0.5"))
check("no headers at all (curl) is not a browser, allowed",
      not overlay_view.cross_site(None, None, "10.0.0.5"))
check("over TLS: an https page on the host is allowed, an http one is not",
      not overlay_view.cross_site("https://10.0.0.5", None, "10.0.0.5", https=True)
      and overlay_view.cross_site("http://10.0.0.5", None, "10.0.0.5", https=True)
      and not overlay_view.cross_site("http://10.0.0.5", None, "10.0.0.5"))
check("on_tls: the scheme itself, or a proxy's X-Forwarded-Proto",
      overlay_view.on_tls("https") and overlay_view.on_tls("http", "https, http")
      and not overlay_view.on_tls("http", None))
check("the upstream path names each camera once, '' for the first",
      overlay_view.upstream_path(["cam 1", "cam 1", ""])
      == "/overlay?camera=cam%201&camera="
      and overlay_view.upstream_path([]) == "/overlay?camera=")

SEEN = []
EVENT = b'data: {"v":1,"cam":"cam1","seq":1}\n\n'


class Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                         # noqa: N802
        SEEN.append(self.path)
        if "nope" in self.path:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"retry: 3000\n\n" + EVENT)
        self.wfile.flush()

    def log_message(self, *a):
        pass


up = http.server.HTTPServer(("127.0.0.1", 0), Upstream)
threading.Thread(target=up.serve_forever, daemon=True).start()
PORT = {"value": up.server_address[1]}
overlay_routes.detector_control_service.stream_port = lambda d, s: PORT["value"]


def call(cameras, headers=None, scheme="http"):
    r = types.SimpleNamespace(app=REQ_APP, headers=headers or {},
                              url=types.SimpleNamespace(hostname="10.0.0.5",
                                                        scheme=scheme))
    try:
        return overlay_routes.detector_overlay(r, camera=cameras)
    except HTTPException as exc:
        return exc


CFG["value"] = {}
got = call(["cam1"])
check("server mode: the relay is a 404 and the app is never asked",
      isinstance(got, HTTPException) and got.status_code == 404 and not SEEN)
CFG["value"] = {"video": {"overlay": "client"}}
got = call(["cam1"], {"Origin": "http://evil.example"})
check("a cross-site read is a 403", isinstance(got, HTTPException)
      and got.status_code == 403 and not SEEN)
got = call(["cam1"], {"Origin": "http://10.0.0.5"}, scheme="https")
check("over TLS, a plain http:// page on the same host is a 403",
      isinstance(got, HTTPException) and got.status_code == 403 and not SEEN)
got = call(["nope"])
check("the app's 404 (no such camera) is passed through",
      isinstance(got, HTTPException) and got.status_code == 404)
s = socket.socket()
s.bind(("127.0.0.1", 0))
dead = s.getsockname()[1]
s.close()
PORT["value"] = dead
got = call(["cam1"])
check("a dead app is a 502", isinstance(got, HTTPException) and got.status_code == 502)
PORT["value"] = up.server_address[1]
SEEN.clear()
got = call(["cam1", "cam2"])
ok = not isinstance(got, HTTPException)
check("client mode: a streaming answer", ok, got)
if ok:
    check("...as text/event-stream, uncached",
          got.media_type == "text/event-stream"
          and got.headers.get("Cache-Control") == "no-store")
    body = PageStream(got).drain()
    check("...relaying the app's events byte for byte",
          body == b"retry: 3000\n\n" + EVENT, body)
    check("...for the cameras the page named, in one request",
          SEEN == ["/overlay?camera=cam1&camera=cam2"], SEEN)
src = open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
check("the master mounts it", "app.include_router(overlay_router)" in src)

print(f"\n{'ALL PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
