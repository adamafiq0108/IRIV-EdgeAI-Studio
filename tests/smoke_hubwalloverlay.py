"""The hub's Live Wall overlay: GET /api/hub/overlay.

The owner, on a real box (2026-10-10): with [video] overlay = "client" a
system's own page drew its boxes, but the hub's Live Wall showed that
system's video with NO boxes, zones or HUD -- the wall is a hub page and the
overlay events live on each master, which an HTTPS hub page cannot fetch.
The hub now relays the EVENTS (never the video), one stream per page for
every system on the wall. Pinned here, against real HTTP masters on
loopback:

  1. the wall's data route says which systems draw in the browser (and adds
     nothing at all in server mode)
  2. refusals: no session (401), a cross-site read and an http page on an
     HTTPS hub (403), a bad or over-long request (400), an unknown or hidden
     system (404, the same answer for both), and a 404 with no master asked
     when none of the systems is in client mode
  3. the hub->master hop: the master's own route, the cameras the tiles
     named, the HTTPS cookie renamed to the plain name (and the stale plain
     one dropped), the tab's X-Session passed on, no Origin
  4. the events: tagged with their system, one stream for two systems, a
     master that is down does not take the other with it
  5. bounded: a slow page keeps the newest messages, not a backlog; a quiet
     stream sends keepalives; past MAX_STREAMS the oldest stream is closed
  6. release: closing the page's stream (the response's background task)
     closes every upstream at once, the master sees its connection go, and
     a response parked waiting for events returns

    python tests/smoke_hubwalloverlay.py
"""
import http.client
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from _asgi_stream import PageStream, stub_streaming_classes   # noqa: E402

# The dev box runs the suite without FastAPI; the routes only need its names
# (the stub smoke_overlayroute uses).
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
        sys.modules.update({"starlette": st, "starlette.background": bg})

from fastapi import HTTPException                              # noqa: E402

from core.routes import hub_overlay as route                   # noqa: E402
from core.services import hub_overlay                          # noqa: E402
from core.services import overlay_view                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def wait_until(pred, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


# -- fake masters: /api/detector/overlay as an endless event stream ------------
class Master(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                         # noqa: N802
        srv = self.server
        srv.seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        if srv.answer != 200:
            self.send_error(srv.answer)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        srv.open += 1
        try:
            self.wfile.write(b"retry: 3000\n\n")
            n = 0
            while not srv.stopping:
                n += 1
                self.wfile.write(srv.tmpl % n)
                self.wfile.flush()
                time.sleep(0.03)
        except OSError:
            pass
        finally:
            srv.open -= 1

    def log_message(self, *a):
        pass


def master(cam):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Master)
    srv.daemon_threads = True
    srv.seen, srv.answer, srv.open, srv.stopping = [], 200, 0, False
    srv.tmpl = (b'data: {"v":1,"cam":"' + cam.encode()
                + b'","first":1,"seq":%d}\n\n')
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


A, B = master("cam1"), master("door")
s = socket.socket()
s.bind(("127.0.0.1", 0))
DEAD = s.getsockname()[1]
s.close()

MODES = {"a": "client", "b": "client", "c": "server", "hidden": "client"}
MASTERS = {"a": {"url": f"http://127.0.0.1:{A.server_address[1]}/"},
           "b": {"url": f"http://127.0.0.1:{B.server_address[1]}"},
           "c": {"url": "http://127.0.0.1:1"},
           "hidden": {"url": "http://127.0.0.1:1"},
           "down": {"url": f"http://127.0.0.1:{DEAD}"}}
MODES["down"] = "client"
overlay_view.for_system = lambda pkg, slug: (
    {"overlay": {"mode": "client", "delay_ms": 0}}
    if MODES.get(slug) == "client" else {})
route.hub_service.visible_to = lambda u, slug, m, c=None: slug != "hidden"
STATE = types.SimpleNamespace(
    registry=[types.SimpleNamespace(slug=x) for x in MASTERS],
    masters=MASTERS, config_dir=None, systems_dir=tempfile.mkdtemp())


def call(slugs, cameras=(), headers=None, user="op", scheme="http"):
    r = types.SimpleNamespace(
        app=types.SimpleNamespace(state=STATE), headers=headers or {},
        state=types.SimpleNamespace(user=user),
        url=types.SimpleNamespace(hostname="10.0.0.5", scheme=scheme))
    try:
        return route.hub_overlay_events(r, slugs=slugs, camera=list(cameras))
    except HTTPException as exc:
        return exc


def status(got):
    return got.status_code if isinstance(got, HTTPException) else 200


def page(resp):
    """The open page behind a response: ASGI call plus the response's
    background task, public Starlette surface only (see _asgi_stream)."""
    if not hasattr(resp, "_page"):
        resp._page = PageStream(resp)
    return resp._page


def read_until(resp, pred, secs=10.0):
    """Let the page collect the body until pred(bytes so far) or the deadline."""
    return page(resp).read_until(pred, secs)


def envelopes(buf):
    return [json.loads(d) for d in hub_overlay.sse_data(buf)[0]]


# -- 1. the wall's data route --------------------------------------------------
src = open(os.path.join(ROOT, "core", "routes", "hub.py"), encoding="utf-8").read()
body = src[src.index("def hub_streams"):src.index("@router", src.index("def hub_streams"))]
check("the Live Wall data route reports each system's overlay mode",
      "**overlay_view.for_system(" in body and "_pkg_root(st.systems_dir, system.slug)" in body)
check("...and that answer is nothing at all in server mode",
      overlay_view.page_info({}) == {}
      and overlay_view.page_info({"video": {"overlay": "client",
                                            "overlay_delay_ms": 90}})
      == {"overlay": {"mode": "client", "delay_ms": 90}})
hs = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
check("the hub mounts the route", "app.include_router(hub_overlay_router)" in hs)

# -- 2. refusals ---------------------------------------------------------------------
check("no session is a 401", status(call("a", user="")) == 401)
check("another site's Origin is a 403",
      status(call("a", headers={"Origin": "http://evil.example"})) == 403)
check("the browser's own cross-site verdict is a 403",
      status(call("a", headers={"Sec-Fetch-Site": "cross-site"})) == 403)
check("an http page on an HTTPS hub is a 403 (Pipi M3)",
      status(call("a", headers={"Origin": "http://10.0.0.5"}, scheme="https")) == 403
      and status(call("a", headers={"Origin": "http://10.0.0.5",
                                    "X-Forwarded-Proto": "https"})) == 403)
check("five systems in one stream is a 400",
      status(call("a,b,c,d,e")) == 400)
check("a name that is not a slug is a 400",
      status(call("a,../x")) == 400 and status(call("")) == 400)
check("a camera for a system the stream did not name is a 400",
      status(call("a", ["b:cam1"])) == 400 and status(call("a", ["nocolon"])) == 400)
check("an unknown system is a 404", status(call("a,ghost")) == 404)
check("a system this account may not see is the SAME 404",
      status(call("a,hidden")) == 404)
before = len(A.seen) + len(B.seen)
check("server mode only: a 404, and no master is asked",
      status(call("c")) == 404 and len(A.seen) + len(B.seen) == before)

# -- 3 + 4. the hop and the events ----------------------------------------------------
COOKIE = "__Secure-cytron_session=TOK; cytron_session=OLD; theme=dark"
got = call("a,b,c", ["a:cam1", "a:", "b:door"],
           headers={"cookie": COOKIE, "X-Session": "TAB",
                    "Origin": "https://10.0.0.5"}, scheme="https")
check("client mode: a streaming answer", status(got) == 200, got)
if status(got) == 200:
    check("...as uncached text/event-stream",
          got.media_type == "text/event-stream"
          and got.headers.get("Cache-Control") == "no-store")
    path, hdr = A.seen[-1]
    check("the hub asks the master's own overlay route, for the tiles' cameras",
          path == "/api/detector/overlay?camera=cam1&camera=", path)
    check("the HTTPS cookie is renamed for the plain-HTTP hop, the stale one dropped",
          hdr.get("cookie") == "cytron_session=TOK; theme=dark", hdr.get("cookie"))
    check("the tab's X-Session rides along, the browser's Origin does not",
          hdr.get("x-session") == "TAB" and "origin" not in hdr, hdr)
    check("each master is asked only for its own tiles' cameras",
          B.seen[-1][0] == "/api/detector/overlay?camera=door")
    buf = read_until(got, lambda b: {e.get("sys") for e in envelopes(b)} >= {"a", "b"})
    envs = envelopes(buf)
    check("ONE stream carries both systems' events, each tagged with its slug",
          {e["sys"] for e in envs} == {"a", "b"}
          and all(isinstance(e.get("m"), dict) for e in envs), envs[:3])
    check("...with the app's own camera id inside, for the tile to pick",
          any(e["sys"] == "b" and e["m"]["cam"] == "door" for e in envs))
    check("both masters hold one upstream each", A.open == 1 and B.open == 1,
          (A.open, B.open))
    ups = got.background.func.__self__.upstreams
    page(got).close_page()              # the page leaves: cleanup + disconnect
    check("closing the page's stream closes every upstream",
          wait_until(lambda: A.open == 0 and B.open == 0), (A.open, B.open))
    check("...their reader threads end, and the stream leaves the hub's count",
          wait_until(lambda: not any(u.thread.is_alive() for u in ups)
                     and hub_overlay.live_count() == 0))

got = call("down,a")
check("one master down: the other's stream still opens", status(got) == 200, got)
if status(got) == 200:
    buf = read_until(got, lambda b: any(e.get("sys") == "a" for e in envelopes(b)))
    check("...and its events flow", any(e.get("sys") == "a" for e in envelopes(buf)))
    down = [u for u in got.background.func.__self__.upstreams if u.slug == "down"][0]
    check("...while the dead one is retried, not given up",
          down.status == 0 and not down.final and down.thread.is_alive())
    page(got).close_page()
    check("...and closing ends the retry loop too",
          wait_until(lambda: not down.thread.is_alive()))
got = call("down")
check("every requested master down is a 502 (the page retries)",
      status(got) == 502)
A.answer = 404
got = call("a")
check("the master's own 404 (back on server) is a 404 (final for the page)",
      status(got) == 404)
A.answer = 200
check("no stream is left open after refusals", wait_until(
    lambda: hub_overlay.live_count() == 0 and A.open == 0))

# -- 5. bounded -----------------------------------------------------------------------
mux = hub_overlay.Mux({"x": ("http://127.0.0.1:1", ["c1"])})
for i in range(50):
    mux.put(hub_overlay.envelope("x", {"seq": i}))
gen = mux.events()
first = envelopes(next(gen))
check("a slow page gets the newest messages only, oldest dropped",
      [e["m"]["seq"] for e in first] == [46, 47, 48, 49], first)
hub_overlay.KEEPALIVE_S = 0.1
check("a quiet stream sends a keepalive", next(gen) == b": keepalive\n\n")
parked = {}


def park():
    parked["v"] = list(gen)


t = threading.Thread(target=park, daemon=True)
t.start()
time.sleep(0.3)
mux.close()
t.join(2)
check("a response parked waiting for events returns when the page leaves",
      not t.is_alive())
muxes = [hub_overlay.Mux({"x": ("http://127.0.0.1:1", ["c1"])})
         for _ in range(hub_overlay.MAX_STREAMS + 1)]
for m in muxes:
    hub_overlay._register(m)
check("past MAX_STREAMS the oldest stream is closed",
      muxes[0].closed and not any(m.closed for m in muxes[1:])
      and hub_overlay.live_count() == hub_overlay.MAX_STREAMS)
for m in muxes:
    m.close()
check("parse_wants: a system with no camera named gets its first",
      hub_overlay.parse_wants("a,b", ["a:cam1", "a:cam1"]) == {"a": ["cam1"], "b": [""]})
check("an upstream that is not a plain-http master is never dialled",
      hub_overlay.Upstream(mux, "x", "https://evil:1", [""], {}).host is None
      and hub_overlay.Upstream(mux, "x", "file:///etc", [""], {}).host is None)
rsrc = open(os.path.join(ROOT, "core", "routes", "hub_overlay.py"),
            encoding="utf-8").read()
check("the master URL comes from the hub's registry, never the request",
      'url = (st.masters.get(slug) or {}).get("url")' in rsrc
      and "request.query_params" not in rsrc)

# -- an upstream that ends badly is reconnected, never fatal --------------------
# Yaya 2026-10-11: an app restart cut its chunked SSE mid-chunk
# (IncompleteRead) or stop() raced the read inside http.client
# (AttributeError); either escaped Relay.chunks and killed the thread, so that
# system's wall boxes froze for good.
from core.services import preview_relay                        # noqa: E402


class Truncating(http.server.BaseHTTPRequestHandler):
    """HTTP/1.1 chunked SSE: one whole event, then a chunk that promises 64
    bytes and delivers 5 before the socket closes -- an app dying mid-write."""
    protocol_version = "HTTP/1.1"

    def do_GET(self):                                         # noqa: N802
        self.server.hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        ev = (b'data: {"v":1,"cam":"cam1","first":1,"seq":%d}\n\n'
              % self.server.hits)
        self.wfile.write(b"%x\r\n" % len(ev) + ev + b"\r\n")
        self.wfile.write(b"40\r\ndata:")
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, *a):
        pass


T = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Truncating)
T.daemon_threads, T.hits = True, 0
threading.Thread(target=T.serve_forever, daemon=True).start()
conn = http.client.HTTPConnection("127.0.0.1", T.server_address[1], timeout=3)
conn.request("GET", "/api/detector/overlay")
relay = preview_relay.Relay(conn, conn.getresponse(), read_timeout=3)
try:
    got = b"".join(relay.chunks())
    raised = None
except Exception as exc:                                        # noqa: BLE001
    got, raised = b"", exc
check("Relay.chunks: a body cut off mid-chunk ends the relay, no exception",
      raised is None and b'"seq":1' in got and relay.closed, raised)


class RacedResp:
    """http.client's read after a concurrent close: fp is None inside it."""
    def read1(self, n):
        raise AttributeError("'NoneType' object has no attribute 'close'")

    def close(self):
        raise AttributeError("'NoneType' object has no attribute 'close'")


relay = preview_relay.Relay(types.SimpleNamespace(sock=None, close=lambda: None),
                            RacedResp())
try:
    list(relay.chunks())
    raised = None
except Exception as exc:                                        # noqa: BLE001
    raised = exc
check("Relay.chunks: a read raced by close() ends the relay, no exception",
      raised is None and relay.closed, raised)

mux = hub_overlay.Mux({"t": (f"http://127.0.0.1:{T.server_address[1]}", ["cam1"])})
up = mux.upstreams[0]
up.start()
check("the wall reconnects after a truncated stream (and again)",
      wait_until(lambda: T.hits >= 3, 15.0) and up.thread.is_alive(), T.hits)
check("...delivering each attempt's whole event and a reset between",
      wait_until(lambda: sum(1 for x in list(mux._queue) if b'"reset"' in x) >= 1),
      list(mux._queue))
up.stop()
mux.close()


class Exploding:
    """A relay whose pump raises something no except clause names."""
    closed = False

    def chunks(self):
        raise RuntimeError("boom")
        yield b""                                               # noqa: unreachable

    def close(self):
        self.closed = True


mux = hub_overlay.Mux({"x": ("http://127.0.0.1:1", ["c1"])})
up = mux.upstreams[0]
opens = []


def fake_open():
    opens.append(1)
    return 200, Exploding()


up._open = fake_open
up.start()
check("any exception from one attempt is logged and the loop reconnects",
      wait_until(lambda: len(opens) >= 2, 8.0) and up.thread.is_alive(), len(opens))
up.stop()
mux.close()
T.shutdown()

A.stopping = B.stopping = True
print(f"\n{'ALL PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
