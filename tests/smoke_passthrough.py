# -*- coding: utf-8 -*-
"""[video] feed = "passthrough" against a STUB video gateway on loopback.

The real gateway (MediaMTX v1.20.1) is not on a dev box, so a stdlib
http.server stands in for the parts of its v3 API passthrough uses -- config
path add / replace / delete, the runtime path list and get -- plus its WHEP
endpoint for the master's side. Covered:

  1. Views: add, no-op re-sync, re-point (add -> 400 -> replace), delete on
     a switch back, re-ensure after the gateway forgets, a put that fails
     while the gateway is down and lands on the next tick, the start-up
     sweep of a crashed run's pull (and never of a legacy publish), close().
  2. Path names: the app's rule equals the master's; anything else is
     refused before it reaches a URL.
  3. Credentials: never in a log line, a status, state.json or a master
     answer -- including when the gateway's own error quotes them.
  4. The app loop itself (main --once): a passthrough RTSP camera gets a
     pull and NO legacy publisher; a USB/synthetic camera beside it stays
     legacy and says why; the pull is deleted at exit.
  5. The master: an on-demand pull counts as ready before its first
     viewer, a failed cold start holds it on MJPEG, view_source survives a
     camera save without reaching the answer, the status carries {feed, why}.

    python tests/smoke_passthrough.py
"""
import http.server
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import passthrough as pt                              # noqa: E402
from core.services import streaming as st                      # noqa: E402

SECRET = "s3cr3tPw"
CAM = f"rtsp://admin:{SECRET}@127.0.0.1:9/Streaming/Channels/102"
MAIN = f"rtsp://admin:{SECRET}@127.0.0.1:9/Streaming/Channels/101"

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- the stub gateway ----------------------------------------------------------

class Stub:
    def __init__(self):
        self.conf = {}          # name -> path conf (what add/replace stored)
        self.published = {}     # name -> True: a legacy publish (all_others)
        self.calls = []         # (method, path, body)
        self.whep = (201, "v=0\r\n")
        self.lock = threading.Lock()


STUB = Stub()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj=None, ctype="application/json", headers=None):
        body = (obj if isinstance(obj, str) else json.dumps(obj or {})).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode() if n else ""

    def _item(self, name):
        if name in STUB.conf:
            return {"name": name, "confName": name, "ready": False,
                    "source": {"type": "rtspSource", "id": ""}, "readers": []}
        if name in STUB.published:
            return {"name": name, "confName": "all_others", "ready": True,
                    "source": {"type": "rtspSession", "id": "x"}, "readers": []}
        return None

    def do_GET(self):
        with STUB.lock:
            STUB.calls.append(("GET", self.path, ""))
            if self.path.startswith("/v3/config/global/get"):
                return self._send(200, {})
            if self.path.startswith("/v3/paths/list"):
                names = sorted(set(STUB.conf) | set(STUB.published))
                return self._send(200, {"items": [self._item(n) for n in names]})
            if self.path.startswith("/v3/paths/get/"):
                it = self._item(self.path.rsplit("/", 1)[1])
                return self._send(200, it) if it else self._send(404, {"error": "no"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._body()
        with STUB.lock:
            STUB.calls.append(("POST", self.path, body))
            p = self.path
            if p.startswith("/v3/config/paths/add/"):
                name = p.rsplit("/", 1)[1]
                if name in STUB.conf:
                    return self._send(400, {"error": "path already exists"})
                STUB.conf[name] = json.loads(body)
                return self._send(200, {"status": "ok"})
            if p.startswith("/v3/config/paths/replace/"):
                name = p.rsplit("/", 1)[1]
                if name not in STUB.conf:
                    return self._send(404, {"error": "path not found"})
                STUB.conf[name] = json.loads(body)
                return self._send(200, {"status": "ok"})
            if p.endswith("/whep"):
                code, text = STUB.whep
                hdr = {"Location": f"{p}/0123456789abcdef"} if code == 201 else {}
                return self._send(code, text, "application/sdp", hdr)
        self._send(404, {"error": "not found"})

    def do_DELETE(self):
        with STUB.lock:
            STUB.calls.append(("DELETE", self.path, ""))
            if self.path.startswith("/v3/config/paths/delete/"):
                name = self.path.rsplit("/", 1)[1]
                if STUB.conf.pop(name, None) is None:
                    return self._send(404, {"error": "path not found"})
                return self._send(200, {"status": "ok"})
        self._send(404, {"error": "not found"})


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}/v3"


def calls(method=None, prefix=""):
    with STUB.lock:
        return [c for c in STUB.calls
                if (method is None or c[0] == method) and c[1].startswith(prefix)]


def reset():
    with STUB.lock:
        STUB.conf.clear()
        STUB.published.clear()
        STUB.calls.clear()
        STUB.whep = (201, "v=0\r\n")


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


log = logging.getLogger("test.passthrough")
log.propagate = False
cap = Capture()
log.addHandler(cap)
log.setLevel(logging.DEBUG)

# -- 1. Views against the stub --------------------------------------------------

views = pt.Views("counting", gateway=pt.Gateway(BASE), log=log)
NAME = pt.path_name("counting", "cam1")
want = {"cam1": ("passthrough", "", MAIN),
        "cam2": ("legacy", "not an RTSP camera", None)}
status = views.sync(want)
adds = calls("POST", "/v3/config/paths/add/")
check("one add, for the passthrough camera only",
      [c[1] for c in adds] == [f"/v3/config/paths/add/{NAME}"], adds)
body = json.loads(adds[0][2]) if adds else {}
check("add body: source, on demand, close-after, start timeout, tcp",
      body == pt.path_conf(MAIN), body)
check("status says passthrough / legacy with the reason",
      status == {"cam1": {"feed": "passthrough", "why": ""},
                 "cam2": {"feed": "legacy", "why": "not an RTSP camera"}}, status)
check("views.passthrough flags the right camera",
      views.passthrough("cam1") and not views.passthrough("cam2"))

n0 = len(calls())
views.sync(want)
check("same config again: no gateway call at all", len(calls()) == n0,
      calls()[n0:])

MAIN2 = MAIN.replace("127.0.0.1:9", "127.0.0.1:10")
views.sync({"cam1": ("passthrough", "", MAIN2)})
check("re-pointed: add answered 400, then replace",
      [c[1].split("/")[4] for c in calls("POST")][-2:] == ["add", "replace"]
      and STUB.conf[NAME]["source"] == MAIN2, calls("POST")[-2:])

with STUB.lock:
    STUB.conf.clear()           # the gateway restarted and forgot
views.tick()
check("tick re-adds a path the gateway forgot",
      NAME in STUB.conf and STUB.conf[NAME]["source"] == MAIN2)
n0 = len(calls())
views.tick()
check("tick with nothing to do is ONE list call",
      [c[:2] for c in calls()[n0:]] == [("GET", "/v3/paths/list?itemsPerPage=1000")],
      calls()[n0:])

views.sync({"cam1": ("legacy", "overlay is server", None)})
check("switched back to legacy: the pull is deleted",
      NAME not in STUB.conf and calls("DELETE")[-1][1]
      == f"/v3/config/paths/delete/{NAME}")
check("...and the camera is no longer passthrough", not views.passthrough("cam1"))
n0 = len(calls())
views.tick()
check("tick holding nothing makes no call", len(calls()) == n0)

# gateway down: the put fails, status says waiting, the next tick lands it
down = pt.Views("counting", gateway=pt.Gateway("http://127.0.0.1:9/v3", timeout=0.5),
                log=log)
st_down = down.sync({"cam1": ("passthrough", "", MAIN)})
check("gateway down: still passthrough (no legacy fight), waiting",
      st_down["cam1"] == {"feed": "passthrough",
                          "why": "waiting for the video gateway"}
      and down.passthrough("cam1"), st_down)
down.gw = pt.Gateway(BASE)
down.tick()
check("gateway back: the next tick takes the path",
      NAME in STUB.conf and down.status["cam1"]["why"] == "")
down.close()
check("close() deletes what it held", NAME not in STUB.conf)

# start-up sweep: a crashed run's pull under a now-legacy camera goes;
# a legacy PUBLISH (all_others) under another name is never touched
reset()
STUB.conf[pt.path_name("counting", "cam3")] = {"source": MAIN}
STUB.published[pt.path_name("counting", "cam4")] = True
v2 = pt.Views("counting", gateway=pt.Gateway(BASE), log=log)
v2.sync({"cam3": ("legacy", "", None), "cam4": ("legacy", "", None)})
check("no sweep on an ordinary sync",
      pt.path_name("counting", "cam3") in STUB.conf and not calls("DELETE"))
v2.sync({"cam3": ("legacy", "", None), "cam4": ("legacy", "", None)}, sweep=True)
check("sweep deletes a stale pull under a legacy camera's name",
      pt.path_name("counting", "cam3") not in STUB.conf)
check("sweep never deletes a legacy publish",
      [c[1] for c in calls("DELETE")]
      == [f"/v3/config/paths/delete/{pt.path_name('counting', 'cam3')}"],
      calls("DELETE"))

# -- 2. path names ---------------------------------------------------------------

for slug, cam in (("counting", "main"), ("a/b", "Bay 3 · east"), ("x", ""),
                  ("", "cam2"), ("s_1", "c-2"), ("../..", "../v3/config")):
    check(f"app path_name == master path_name for {slug!r}/{cam!r}",
          pt.path_name(slug, cam) == st.path_name(slug, cam))
check("a traversal camera id cannot leave its path",
      "/" not in pt.path_name("../..", "../v3/config")
      and "." not in pt.path_name("../..", "../v3/config"))
n0 = len(calls())
gw = pt.Gateway(BASE)
r1 = gw.put("../v3/config/global/patch", MAIN)
r2 = gw.delete("a/b")
r3 = gw.put("x y", MAIN)
check("a name that is not a path name is refused before any request",
      r1[0] is False and r2 is False and r3[0] is False and len(calls()) == n0,
      (r1, r2, r3, calls()[n0:]))

# -- 3. credentials ----------------------------------------------------------------

reset()


class EchoBad(Handler):
    """A gateway whose error quotes the source back (some versions do)."""

    def do_POST(self):
        body = self._body()
        src = json.loads(body).get("source", "") if body else ""
        self._send(400, {"error": f"invalid source '{src}'"})


bad_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), EchoBad)
threading.Thread(target=bad_srv.serve_forever, daemon=True).start()
echo = pt.Views("counting", gateway=pt.Gateway(
    f"http://127.0.0.1:{bad_srv.server_address[1]}/v3"), log=log)
st_echo = echo.sync({"cam1": ("passthrough", "", MAIN)})
check("a gateway error quoting the URL is logged masked",
      any("invalid source" in ln and "***@" in ln for ln in cap.lines),
      cap.lines[-3:])
check("no captured log line carries the password",
      not any(SECRET in ln for ln in cap.lines),
      [ln for ln in cap.lines if SECRET in ln])
check("no status carries the password or the address",
      SECRET not in json.dumps([status, st_down, st_echo, views.status])
      and "rtsp://" not in json.dumps([status, st_down, st_echo]))
bad_srv.shutdown()

# -- 4. the app loop: main --once with a passthrough and a legacy camera ----------

reset()
from app import faults                                         # noqa: E402
from app import publisher as publisher_mod                     # noqa: E402
from app import run as apprun                                  # noqa: E402

built = []


class FakePublisher:
    def __init__(self, url, width, height, fps, name=""):
        built.append(name)
        self.ok, self.error, self.width, self.height = True, None, width, height
        self.url = url

    def push(self, frame):
        return True

    def close(self):
        self.ok = False


_orig_open = apprun.camera_mod.Camera.open


def _open(self, *a, **k):
    """No camera answers on a dev box, and FFmpeg waits 30 s to say so. The
    view does not need the detect stream to open; the failure text quotes
    the address on purpose, so the log mask is exercised too."""
    if str(self.source).startswith("rtsp://"):
        self.error = "test: camera not opened"
        apprun.camera_mod.log.error("camera %r: test, nothing there", self.source)
        return False
    return _orig_open(self, *a, **k)


apprun.camera_mod.Camera.open = _open
publisher_mod.available = lambda: True
publisher_mod.ensure_gateway = lambda timeout=2.0: True
publisher_mod.Publisher = FakePublisher
apprun.passthrough_mod.API_BASE = BASE

lines = []


class AppLog(logging.Handler):
    """What app.log would hold: the app's own formatter, so its masking is
    what is tested."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(faults._Format())

    def emit(self, record):
        lines.append(self.format(record))


root = logging.getLogger()
root.addHandler(AppLog())


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


DATA = tempfile.mkdtemp()
CFG = {
    "app": {"stream_port": _free_port(), "stream_bind": "127.0.0.1",
            "slug": "lot", "stream": "webrtc"},
    "video": {"overlay": "client", "feed": "passthrough"},
    "cameras": [
        {"id": "ip", "source": CAM, "width": 320, "height": 240, "fps": 5},
        {"id": "syn", "source": "synthetic", "width": 320, "height": 240, "fps": 5},
    ],
    "detector": {"kind": "mock"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
}
apprun.config_mod.load = lambda *a, **k: CFG
apprun.config_mod.data_dir = lambda *a, **k: DATA

rc = apprun.main(["--once"])
check("one cycle of the loop with a passthrough camera", rc == 0, rc)
time.sleep(0.6)                 # let the publish threads take a look
ip_name, syn_name = pt.path_name("lot", "ip"), pt.path_name("lot", "syn")
added = [c for c in calls("POST", "/v3/config/paths/add/")]
check("the RTSP camera's pull was added, from its MAIN stream",
      [c[1] for c in added] == [f"/v3/config/paths/add/{ip_name}"]
      and json.loads(added[0][2])["source"] == CAM.replace("/102", "/101"),
      added)
check("the pull was deleted when the app stopped",
      f"/v3/config/paths/delete/{ip_name}" in [c[1] for c in calls("DELETE")]
      and ip_name not in STUB.conf, calls("DELETE"))
print("publishers built:", built)
check("no legacy publisher was built for the passthrough camera",
      "ip" not in built, built)
with open(os.path.join(DATA, "state.json"), encoding="utf-8") as f:
    state_text = f.read()
state = json.loads(state_text)
views_by_id = {c["id"]: c.get("view") for c in state.get("cameras") or []}
check("state.json: ip passthrough, syn legacy with its reason",
      views_by_id.get("ip") == {"feed": "passthrough", "why": ""}
      and views_by_id.get("syn") == {"feed": "legacy", "why": "not an RTSP camera"},
      views_by_id)
# The camera's `label` (and the top-level `camera`) is its raw source and
# predates passthrough: reported separately, not hidden by this test.
pre = dict(state, camera=None,
           cameras=[dict(c, label=None) for c in state.get("cameras") or []])
check("nothing passthrough adds to state.json holds the password",
      SECRET not in json.dumps(pre) and "rtsp://" not in json.dumps(views_by_id))
if SECRET in state_text:
    print("NOTE pre-existing: state.json camera/label carry the raw source")
check("app.log (the app's formatter) never holds the password",
      not any(SECRET in ln for ln in lines),
      [ln for ln in lines if SECRET in ln][:3])
check("app.log says which camera went passthrough",
      any("[VIEW] ip: passthrough" in ln for ln in lines)
      and any("[VIEW] syn: legacy (not an RTSP camera)" in ln for ln in lines),
      [ln for ln in lines if "[VIEW]" in ln])

# default system: no [video] feed -> no view in state, no [VIEW] line, no add
reset()
lines.clear()
DATA2 = tempfile.mkdtemp()
CFG2 = dict(CFG, video={"overlay": "client"},
            app=dict(CFG["app"], stream_port=_free_port()))
apprun.config_mod.load = lambda *a, **k: CFG2
apprun.config_mod.data_dir = lambda *a, **k: DATA2
apprun._stop.clear()          # main() sets it on the way out
rc = apprun.main(["--once"])
with open(os.path.join(DATA2, "state.json"), encoding="utf-8") as f:
    st2 = json.load(f)
check("default feed: no add, no view in state, no [VIEW] line",
      rc == 0 and not calls("POST")
      and all("view" not in c for c in st2.get("cameras") or [])
      and not any("[VIEW]" in ln for ln in lines),
      (calls("POST"), [ln for ln in lines if "[VIEW]" in ln]))

# The publish threads, for real: a few seconds of the loop with every camera
# delivering frames (a synthetic camera publishes no grab, so latest() is
# stood in for). The legacy camera must get its publisher -- that is what
# makes "the passthrough camera got none" mean something.
import itertools                                               # noqa: E402

import numpy as np                                             # noqa: E402

_seq = itertools.count(1)
_frame = np.zeros((240, 320, 3), np.uint8)
apprun.camera_mod.Camera.latest = lambda self: (_frame, next(_seq))
reset()
built.clear()
DATA3 = tempfile.mkdtemp()
CFG3 = dict(CFG, app=dict(CFG["app"], stream_port=_free_port()))
apprun.config_mod.load = lambda *a, **k: CFG3
apprun.config_mod.data_dir = lambda *a, **k: DATA3
apprun._stop.clear()
threading.Timer(3.0, apprun._stop.set).start()
rc = apprun.main([])
time.sleep(0.3)
check("running loop: the legacy camera publishes, the passthrough one never",
      rc == 0 and "syn" in built and "ip" not in built, (rc, built))
check("running loop: pull added once and deleted at exit",
      len(calls("POST", "/v3/config/paths/add/")) == 1
      and ip_name not in STUB.conf, calls())

# -- 5. the master ------------------------------------------------------------------

reset()
st.API_PORT = st.WHEP_PORT = PORT
STUB.conf["lot__ip"] = {"source": MAIN}
STUB.published["lot__syn"] = True
check("an on-demand pull is ready before its first viewer",
      st.stream_ready("lot", "ip") is True)
check("...so the page is offered webrtc for it",
      st.mode({"app": {"stream": "webrtc"}}, "lot", "ip") == "webrtc")
check("a legacy publish keeps the old rule (ready flag)",
      st.stream_ready("lot", "syn") is True)
check("an unknown path is not ready", st.stream_ready("lot", "nope") is False)

STUB.whep = (400, f"source of path 'lot__ip' failed: rtsp://admin:{SECRET}@x/y")
code, text, sess = st.whep_offer("lot", "ip", "v=0")
check("a failed cold start reaches the caller as an error, masked",
      code == 400 and SECRET not in text and "***@" in text, (code, text))
check("...and holds the camera on MJPEG for a while",
      st.stream_ready("lot", "ip") is False
      and st.mode({"app": {}}, "lot", "ip") == "mjpeg")
st._pull_failed["lot__ip"] -= st.PULL_FAIL_HOLD_S + 1
check("...then it is offered again", st.stream_ready("lot", "ip") is True)
STUB.whep = (201, "v=0\r\n")
code, text, sess = st.whep_offer("lot", "ip", "v=0")
check("a working handshake clears the hold",
      code == 201 and sess == "0123456789abcdef" and "lot__ip" not in st._pull_failed,
      (code, sess))

from core.services import detector_control as dc               # noqa: E402
from core.services.passthrough_view import carry_view_sources  # noqa: E402

clean = [{"id": "ip", "source": CAM}, {"id": "moved", "source": "rtsp://b/stream2"}]
stored = {"cameras": [{"id": "ip", "source": CAM, "view_source": MAIN},
                      {"id": "moved", "source": "rtsp://a/stream2",
                       "view_source": "rtsp://a/stream1"}]}
out = carry_view_sources(clean, stored)
check("view_source survives a camera save while the source is unchanged",
      out[0].get("view_source") == MAIN)
check("...and is dropped when the camera was re-pointed",
      "view_source" not in out[1])
check("...and the list the route answers with never gets it",
      all("view_source" not in c for c in clean))
check("legacy [camera] table carries too",
      carry_view_sources([{"id": "main", "source": CAM}],
                         {"camera": {"source": CAM, "view_source": MAIN}})[0]
      .get("view_source") == MAIN)

merged = dc._merge_live_cameras(
    [{"id": "ip", "source": "x"}, {"id": "syn"}],
    [{"id": "ip", "error": None, "measured_fps": 5,
      "view": {"feed": "passthrough", "why": "", "url": MAIN}},
     {"id": "syn", "error": None, "measured_fps": 5}])
check("status carries {feed, why} and nothing else from the view",
      merged[0].get("view") == {"feed": "passthrough", "why": ""}
      and "view" not in merged[1], merged)

# the master's camera list (what every page reads) never has view_source
cams = dc._camera_list({"cameras": [{"id": "ip", "source": CAM,
                                     "view_source": MAIN}]})
check("the master's camera list never carries view_source",
      "view_source" not in cams[0] and MAIN not in json.dumps(cams))

server.shutdown()
print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
