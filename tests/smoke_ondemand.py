# -*- coding: utf-8 -*-
"""[video] on_demand: the app pauses a camera's legacy view work while nobody
watches it, against a STUB video gateway on loopback.

The real gateway (MediaMTX) is not on a dev box, so a stdlib http.server
stands in for v3/paths/list, the one call the watch makes. Covered:

  1. settings(): both switches off by default, view_hold_s 30 by default and
     clamped 2..120, anything unreadable is the default.
  2. readers(): the reader count per path; unknown (None) for an API that is
     down, answers 500 or garbage; a path with no readers list is left out.
  3. Gate (fake clock): zero readers pause after the grace, a reader or an
     MJPEG viewer wakes at once, unknown keeps it awake, exempt never pauses.
  4. Evidence: with the lazy shot a snapshot at the alert moment still has
     the boxes -- pixel-identical to the eager paint, and the moment's own
     overlay even after the next tick swapped it; OFF keeps the eager paint.
  5. The app loop for real (main with a fake publisher): readers 0 -> the
     camera drops to a plain trickle after the grace while a measurement and
     a passthrough camera never pause; a reader -> key-frame request and full
     rate; the gateway erroring -> keeps publishing; an MJPEG viewer keeps it
     awake; switch OFF -> no gateway request at all, full rate throughout.

    python tests/smoke_ondemand.py
"""
import http.server
import itertools
import json
import logging
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
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import view_gate as vg                                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. settings --------------------------------------------------------------
check("default: both off, hold 30 s",
      vg.settings({}) == {"on_demand": False, "view_on_detect": False,
                          "view_hold_s": 30})
check("on and a hold read from TOML",
      vg.settings({"video": {"on_demand": True, "view_on_detect": "true",
                             "view_hold_s": 45}})
      == {"on_demand": True, "view_on_detect": True, "view_hold_s": 45})
check("hold clamped 2..120, junk is the default",
      vg.settings({"video": {"view_hold_s": 0}})["view_hold_s"] == 2
      and vg.settings({"video": {"view_hold_s": 999}})["view_hold_s"] == 120
      and vg.settings({"video": {"view_hold_s": "x"}})["view_hold_s"] == 30)
check("a non-table [video] or a junk switch is off",
      vg.settings({"video": "x"})["on_demand"] is False
      and vg.settings({"video": {"on_demand": "maybe"}})["on_demand"] is False)

# -- 2. readers() against the stub ----------------------------------------------


class Stub:
    mode = "ok"                     # ok | 500 | garbage | noreaders
    readers = {}                    # path -> count
    hits = 0


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if not self.path.startswith("/v3/paths/list"):
            self.send_error(404)
            return
        Stub.hits += 1
        if Stub.mode == "500":
            self.send_error(500)
            return
        if Stub.mode == "garbage":
            body = b"<html>nope"
        else:
            items = []
            for name, n in sorted(Stub.readers.items()):
                item = {"name": name, "ready": True}
                if Stub.mode != "noreaders":
                    item["readers"] = [{"type": "webRTCSession", "id": str(i)}
                                       for i in range(n)]
                items.append(item)
            body = json.dumps({"itemCount": len(items), "pageCount": 1,
                               "items": items}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}/v3/paths/list?itemsPerPage=1000"

Stub.readers = {"lot__a": 0, "lot__b": 2}
check("readers: the count per path", vg.readers(URL) == {"lot__a": 0, "lot__b": 2})
Stub.mode = "500"
check("readers: an API error is unknown", vg.readers(URL) is None)
Stub.mode = "garbage"
check("readers: an answer that does not parse is unknown", vg.readers(URL) is None)
Stub.mode = "noreaders"
check("readers: a path with no readers list is left out (unknown)",
      vg.readers(URL) == {})
Stub.mode = "ok"
dead = socket.socket()
dead.bind(("127.0.0.1", 0))
dead_port = dead.getsockname()[1]
dead.close()
check("readers: a gateway that is not there is unknown",
      vg.readers(f"http://127.0.0.1:{dead_port}/v3/paths/list", timeout=0.5) is None)

# -- 3. Gate, with a fake clock ---------------------------------------------------
now = [1000.0]
g = vg.Gate(grace_s=10, clock=lambda: now[0])
check("a camera never seen is wanted", g.wanted("a"))
check("zero readers: still wanted inside the grace",
      g.update([("a", 0, 0, False)]) == [] and g.wanted("a"))
now[0] += 10.5
check("zero readers past the grace: paused",
      g.update([("a", 0, 0, False)]) == [("a", False)] and not g.wanted("a"))
now[0] += 1
check("a reader wakes it at once",
      g.update([("a", 1, 0, False)]) == [("a", True)] and g.wanted("a"))
now[0] += 11
g.update([("a", 0, 0, False)])
check("...and it pauses again a grace after the reader left", not g.wanted("a"))
check("an MJPEG viewer wakes it", g.update([("a", 0, 1, False)]) == [("a", True)])
now[0] += 11
g.update([("a", 0, 0, False)])
check("unknown (None) readers wake it and keep it awake",
      g.update([("a", None, 0, False)]) == [("a", True)]
      and (now.__setitem__(0, now[0] + 60) or True)
      and g.update([("a", None, 0, False)]) == [] and g.wanted("a"))
g2 = vg.Gate(grace_s=10, clock=lambda: now[0])
for _ in range(5):
    now[0] += 30
    g2.update([("pt", 0, 0, True)])
check("an exempt camera is never paused", g2.wanted("pt"))
g.update([("a", 0, 0, False)])
now[0] += 11
g.update([("a", 0, 0, False)])
check("reset() wakes every paused camera", g.reset() == ["a"] and g.wanted("a"))
g.update([("a", 0, 0, False), ("b", 0, 0, False)])
g.update([("a", 0, 0, False)])
check("a removed camera is forgotten", "b" not in g._wanted)

# -- 4. evidence with the lazy shot ------------------------------------------------
try:
    import cv2                                                 # noqa: F401
    import numpy as np
except ImportError:
    np = None
    print("SKIP sections 4-5: need opencv + numpy (python3-opencv on a board)")

if np is not None:
    from app import run as apprun                              # noqa: E402

    FRAME = np.random.default_rng(3).integers(0, 255, (240, 320, 3), dtype=np.uint8)
    ALERT = {"detections": [{"label": "person", "confidence": 0.9,
                             "box": [40, 30, 160, 200], "violation": True}],
             "zone": None, "overlays": [], "links": [], "bubbles": [],
             "trails": [], "hud": [("model", "m")]}
    NEXT = dict(ALERT, detections=[])
    p = types.SimpleNamespace(overlay=ALERT, shot=None, shot_src=None)
    eager = apprun._paint(ALERT, FRAME)
    got = apprun._keep_shot(p, FRAME, lazy=False)
    check("OFF: the eager paint, kept and returned as before",
          got is p.shot and p.shot_src is None and np.array_equal(got, eager))
    p.shot, p.shot_src = None, None
    got = apprun._keep_shot(p, FRAME, lazy=True)
    check("ON: nothing painted on the tick, only the ingredients kept",
          got is None and p.shot is None and p.shot_src[0] is FRAME
          and p.shot_src[1] is ALERT)
    p.overlay = NEXT                    # the next tick's _draw swapped it
    lazy = apprun._shot_frame(p)
    check("ON: the snapshot at the alert moment carries the boxes",
          lazy is not None and not np.array_equal(lazy, FRAME))
    check("...pixel-identical to the eager paint of that moment",
          np.array_equal(lazy, eager))
    check("...and the JPEG a snapshot saves is the same bytes",
          apprun.camera_mod.encode_jpeg(lazy) == apprun.camera_mod.encode_jpeg(eager))
    p2 = types.SimpleNamespace(overlay=None, shot=None, shot_src=None)
    check("nothing kept yet: no picture (the snapshot falls back as before)",
          apprun._shot_frame(p2) is None)
    src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
               encoding="utf-8").read()
    check("_snap reads the evidence through _shot_frame",
          "shot_img = _shot_frame(p)" in src)
    check("_draw keeps it lazily only while on_demand is on",
          "stamped = _keep_shot(pipe, frame, lazy=on_demand)" in src)
    check("clips still paint their own frame when one is due (unchanged)",
          "pipe.capture.offer(_paint(pipe.overlay, frame, hud=False), now_j)" in src)
    check("depth cameras are exempt (no depth camera on a dev box: pinned)",
          'getattr(p.cam, "_orbbec", None) is not None' in src
          and "def depth_frame(self):" in open(os.path.join(
              ROOT, "systems", "demo", "app", "camera.py"), encoding="utf-8").read())

# -- 5. the app loop for real ------------------------------------------------------
if np is not None:
    from app import faults                                     # noqa: E402,F401
    from app import passthrough as pt_mod                      # noqa: E402
    from app import publisher as publisher_mod                 # noqa: E402

    pushes = []                     # (time, camera, plain?)
    keyframes = []
    built = []
    _frame = np.zeros((240, 320, 3), np.uint8)

    class FakePublisher:
        def __init__(self, url, width, height, fps, name=""):
            built.append(name)
            self.ok, self.error = True, None
            self.width, self.height, self.name = width, height, name

        def push(self, frame):
            pushes.append((time.time(), self.name, frame is _frame))
            return True

        def force_keyframe(self):
            keyframes.append((time.time(), self.name))
            return True

        def close(self):
            self.ok = False

    class FakeViews:
        """Camera "pt" is carried by the gateway (passthrough); no API."""

        def __init__(self, slug, gateway=None, log=None):
            self.status = {}

        def sync(self, wanted, sweep=False):
            return {}

        def passthrough(self, cid):
            return cid == "pt"

        def tick(self):
            pass

        def close(self):
            pass

    _seq = itertools.count(1)
    apprun.camera_mod.Camera.latest = lambda self: (_frame, next(_seq))
    publisher_mod.available = lambda: True
    publisher_mod.ensure_gateway = lambda timeout=2.0: True
    publisher_mod.Publisher = FakePublisher
    apprun.passthrough_mod.Views = FakeViews
    vg.PATHS_URL = URL
    vg.PRESENCE_S = 0.1
    vg.IDLE_GRACE_S = 0.6
    vg.KEEPALIVE_S = 0.5

    lines = []

    class AppLog(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    logging.getLogger().addHandler(AppLog(logging.INFO))
    logging.getLogger().setLevel(logging.INFO)

    def free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def config(on_demand):
        return {
            "app": {"stream_port": free_port(), "stream_bind": "127.0.0.1",
                    "slug": "lot", "stream": "webrtc"},
            "video": {"overlay": "server", "on_demand": on_demand},
            "cameras": [
                {"id": "syn", "source": "synthetic", "width": 320,
                 "height": 240, "fps": 30},
                {"id": "meas", "source": "synthetic", "width": 320,
                 "height": 240, "fps": 30,
                 "measure": {"profiles": [{"label": "box"}]}},
                {"id": "pt", "source": "synthetic", "width": 320,
                 "height": 240, "fps": 30},
            ],
            "detector": {"kind": "mock"},
            "capture": {"snap": False, "record": False},
            "storage": {"keep_snapshots": 5},
        }

    def rate(cam, t0, t1, plain=None):
        n = sum(1 for t, c, pl in pushes if c == cam and t0 <= t < t1
                and (plain is None or pl == plain))
        return n / max(0.001, t1 - t0)

    def run_app(cfg, script):
        """main() on THIS thread (it installs signal handlers) while
        `script()` drives the stub from another and then stops the app."""
        data = tempfile.mkdtemp()
        apprun.config_mod.load = lambda *a, **k: cfg
        apprun.config_mod.data_dir = lambda *a, **k: data
        apprun._stop.clear()
        errs = []

        def drive():
            try:
                script()
            except Exception as exc:                           # noqa: BLE001
                errs.append(exc)
            finally:
                apprun._stop.set()

        threading.Thread(target=drive, daemon=True).start()
        rc = apprun.main([])
        check("the test script itself ran clean", not errs, errs)
        return rc

    marks = {}

    def script_on():
        Stub.mode, Stub.readers = "ok", {"lot__syn": 0, "lot__meas": 0}
        time.sleep(2.5)                         # start, grace, pause
        marks["paused"] = time.time()
        time.sleep(2.0)
        marks["wake"] = time.time()
        Stub.readers = {"lot__syn": 1, "lot__meas": 0}
        time.sleep(1.5)
        marks["awake_end"] = time.time()
        Stub.mode = "500"                       # the gateway errors
        time.sleep(2.0)
        marks["err_end"] = time.time()
        Stub.mode, Stub.readers = "ok", {"lot__syn": 0, "lot__meas": 0}
        time.sleep(1.5)
        marks["mjpeg"] = time.time()
        port = cfg_on["app"]["stream_port"]
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.sendall(b"GET /video_feed?camera=syn HTTP/1.1\r\nHost: x\r\n\r\n")
        t_end = time.time() + 2.0
        while time.time() < t_end:
            try:
                sock.recv(65536)
            except OSError:
                break
        marks["mjpeg_end"] = time.time()
        sock.close()
        time.sleep(0.3)

    cfg_on = config(True)
    rc = run_app(cfg_on, script_on)
    check("the loop ran and stopped cleanly (on_demand on)", rc == 0, rc)
    t_p, t_w = marks["paused"], marks["wake"]
    paused_rate = rate("syn", t_p, t_w)
    paused_painted = rate("syn", t_p, t_w, plain=False)
    check("readers 0: the camera drops to the trickle after the grace",
          0 < paused_rate <= 3.5, paused_rate)
    check("...and the trickle frames are plain (no paint while paused)",
          paused_painted == 0, paused_painted)
    check("...logged once for the box journal",
          any("[syn] video on demand: nobody watching - paused" in ln for ln in lines),
          [ln for ln in lines if "on demand" in ln])
    kf = [t for t, c in keyframes if c == "syn" and t >= t_w]
    check("a reader appears: a key-frame request on wake, within ~0.5 s",
          bool(kf) and kf[0] - t_w < 0.6, (kf[:1], t_w))
    awake = rate("syn", t_w + 0.6, marks["awake_end"])
    check("...and full rate again, painted", awake >= 10
          and rate("syn", t_w + 0.6, marks["awake_end"], plain=True) == 0, awake)
    err = rate("syn", marks["awake_end"] + 0.8, marks["err_end"])
    check("the gateway erroring: keeps publishing at full rate", err >= 10, err)
    mj = rate("syn", marks["mjpeg"] + 0.6, marks["mjpeg_end"])
    check("an MJPEG viewer keeps it awake with zero WebRTC readers", mj >= 10, mj)
    check("the measurement camera is never paused",
          not any("[meas] video on demand" in ln for ln in lines)
          and rate("meas", t_p, t_w) >= 10, rate("meas", t_p, t_w))
    check("the passthrough camera is never published nor paused",
          "pt" not in built and not any("[pt] video on demand" in ln for ln in lines),
          built)

    # -- switch off: no gateway request, full rate throughout
    pushes.clear()
    lines.clear()
    Stub.hits = 0
    Stub.mode, Stub.readers = "ok", {"lot__syn": 0}

    def script_off():
        time.sleep(3.0)
        marks["off_end"] = time.time()

    t0 = time.time()
    rc = run_app(config(False), script_off)
    check("the loop ran (on_demand off)", rc == 0, rc)
    check("OFF: not one gateway request", Stub.hits == 0, Stub.hits)
    off = rate("syn", t0 + 1.5, marks["off_end"])
    check("OFF: full rate with zero readers, past any grace", off >= 10, off)
    check("OFF: no on-demand line in the log",
          not any("on demand" in ln for ln in lines))

srv.shutdown()
print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
