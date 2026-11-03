"""[video] overlay = "client": the app sends plain frames and the overlay as data.

With several cameras running AI the CM5 saturates, and part of that is the
live view: boxes, zones and the HUD painted into every published frame, then
re-encoded. In client mode the browser draws them instead, from JSON the app
builds (app/overlay_feed.py). This pins:

  1. server mode (the default) is BYTE-identical to the old _paint path, for
     the WebRTC publish (_painted) and the MJPEG viewer (_annotated_jpeg)
  2. client mode sends the UNPAINTED frame on both, never a stale painted
     one from the shared cache, and a mode flip clears every cache
  3. the metadata carries every element _paint draws -- boxes (with the
     colour each would be painted in), a violating box, zones (polygon, a
     faced line, the legacy rectangle), a link, a bubble, a trail, the HUD,
     the measurement lines -- normalised to 0..1, with a rising sequence
  4. the Feed builds nothing without a listener, does not re-serialise an
     unchanged overlay, is rate-bounded, and its queue drops the OLDEST
  5. the app's /overlay endpoint: 404 in server mode and for an unknown
     camera, server-sent events otherwise, and a browser that leaves is let go

    python tests/smoke_overlayclient.py
"""
import http.client
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
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

# The paint path is OpenCV's; same skip as smoke_paintonce.
try:
    import cv2                                                 # noqa: E402,F401
    import numpy as np                                         # noqa: E402
except ImportError:
    print("SKIP: needs opencv (python3-opencv on a board); not a pip "
          "requirement, so a CI runner has none")
    sys.exit(77)

from app import camera as camera_mod                           # noqa: E402
from app import overlay_feed as overlay_mod                    # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


rng = np.random.default_rng(11)
W, H = 640, 480
FRAME = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
PRISTINE = FRAME.copy()


def zone(zid, color, pts, kind="polygon", **extra):
    return {"id": zid, "name": zid, "kind": kind, "enabled": True,
            "violation": False, "color": color, "points_px": pts,
            "labels": [], "facing": "forward", **extra}


def overlay():
    """Everything a busy camera draws."""
    return {
        "detections": [
            {"label": "person", "confidence": 0.91, "box": [40, 60, 200, 400],
             "track_id": 3},
            {"label": "person", "confidence": 0.77, "box": [300, 80, 420, 360],
             "track_id": 4, "violation": True, "zone": "z1"},
            {"label": "car", "confidence": 0.66, "box": [450, 200, 620, 330],
             "box_color": "#22ccaa"},
            {"label": "box", "confidence": 0.5, "box": [100, 100, 160, 160],
             "depth_mm": 1500.0,
             "measure": {"shape": "circle", "diameter_mm": 120.0,
                         "centre": [130, 130], "radius_px": 25.0,
                         "segments": [[[100, 100], [160, 100]]],
                         "segments_mm": [80.0],
                         "outline": {"silhouette": [[100, 100], [160, 100],
                                                    [160, 160], [100, 160]]}}},
        ],
        "zone": None,
        "overlays": [zone("z1", "#00ff00", [(250, 50), (500, 60), (480, 420), (240, 400)]),
                     zone("z2", "", [(10, 300), (200, 300), (200, 470), (10, 470)],
                          violation=True),
                     zone("l1", "#3366ff", [(20, 20), (300, 20)], kind="line",
                          facing="reverse"),
                     zone("off", "#ffffff", [(0, 0), (9, 0), (9, 9)], enabled=False)],
        "links": [(120, 230, 360, 220, True, 240.4)],
        "bubbles": [(280, 60, 440, 380, True)],
        "trails": [([(100, 400), (110, 390), (125, 380), (140, 372)], True),
                   ([(500, 400), (510, 410)], False)],
        "hud": [("model", "yolov8s"), ("camera", "cam1"),
                ("hailo fps", "14.2"), ("camera fps", "30.0")],
    }


class Cam:
    def __init__(self, frame, seq):
        self.frame, self.seq = frame, seq
        self.stalled_s = 0.0

    def latest(self):
        return self.frame, self.seq

    def last_jpeg(self):
        return None


def pipe(frame, seq, ov, client=False):
    return types.SimpleNamespace(
        overlay=ov, cam=Cam(frame, seq), cfg={}, jpeg=None, jpeg_at=0.0,
        view_seq=-1, view_jpeg=None, view_lock=threading.Lock(),
        paint_key=None, paint_frame=None, paint_lock=threading.Lock(),
        overlay_client=client)


def set_mode(p, on):
    apprun._Pipe.set_overlay_client(p, on)


# -- 1. server mode: byte-identical ---------------------------------------------
ov = overlay()
old = apprun._paint(ov, FRAME.copy())
p = pipe(FRAME, 5, ov)
got = apprun._painted(p, FRAME, 5)
check("server: the publish frame is pixel-identical to the old _paint",
      np.array_equal(got, old))
p.view_seq, p.view_jpeg = -1, None
check("server: the MJPEG viewer's JPEG is byte-identical to the old path",
      apprun._annotated_jpeg(p) == camera_mod.encode_jpeg(old))
check("server: the mode key defaults off in the config",
      overlay_mod.client_mode({}) is False
      and overlay_mod.client_mode({"video": {"overlay": "nonsense"}}) is False
      and overlay_mod.client_mode({"video": {"overlay": " Client "}}) is True)

# -- 2. client mode: the plain picture --------------------------------------------
pc = pipe(FRAME, 5, ov, client=True)
sent = apprun._painted(pc, FRAME, 5)
check("client: the publish path sends the UNPAINTED frame",
      sent is FRAME and np.array_equal(sent, PRISTINE))
pc.view_seq, pc.view_jpeg = -1, None
check("client: the MJPEG path sends the unpainted frame",
      apprun._annotated_jpeg(pc) == camera_mod.encode_jpeg(PRISTINE))
# A paint cached in server mode for the very same grab and overlay.
p = pipe(FRAME, 7, ov)
apprun._painted(p, FRAME, 7)
p.view_seq, p.view_jpeg = -1, None
apprun._annotated_jpeg(p)
check("(setup) server mode cached a painted frame and JPEG for grab 7",
      p.paint_frame is not None and p.view_seq == 7)
p.overlay_client = True                     # flipped without the cache reset
check("client: the shared paint cache is never served in client mode",
      apprun._painted(p, FRAME, 7) is FRAME)
p.overlay_client = False
set_mode(p, True)
check("a mode flip clears the paint, view and fallback caches",
      p.paint_key is None and p.paint_frame is None and p.view_jpeg is None
      and p.view_seq == -1 and p.jpeg is None)
check("...so the same grab now reaches a viewer plain",
      apprun._annotated_jpeg(p) == camera_mod.encode_jpeg(PRISTINE))
set_mode(p, False)
p.view_seq, p.view_jpeg = -1, None
check("...and back to server it is painted again, byte-identical",
      apprun._annotated_jpeg(p) == camera_mod.encode_jpeg(old))
steps = [{"op": "grayscale"}]
from app import preprocess as preprocess_mod                   # noqa: E402
pc = pipe(FRAME, 9, ov, client=True)
pc.cfg = {"preprocess_preview": True, "preprocess": steps}
check("client: preprocess_preview shows the processed picture, unpainted",
      apprun._annotated_jpeg(pc)
      == camera_mod.encode_jpeg(preprocess_mod.apply(FRAME, steps)))
check("the grabbed frame itself was never written to",
      np.array_equal(FRAME, PRISTINE))
src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
           encoding="utf-8").read()
check("the publish loop and the MJPEG viewer both go through _painted",
      "_publish(pipe, _painted(pipe, frame, seq))" in src
      and "shown = _painted(pipe, frame, seq)" in src)
check("_draw still paints pipe.shot (snapshots) and clips in both modes",
      "stamped = _paint(pipe.overlay, frame)" in src
      # [video] on_demand keeps the moment and paints it when a snapshot
      # reads it (_keep_shot / _shot_frame, tests/smoke_ondemand.py).
      and "pipe.shot, pipe.shot_src = stamped, None" in src
      and "_paint(pipe.overlay, frame, hud=False)" in src)

# -- 3. the metadata ----------------------------------------------------------------
body = overlay_mod.build(ov, W, H)
dets = body.get("dets") or []
check("every box is there", len(dets) == 4, dets)
check("boxes are normalised to the frame",
      dets[0]["box"] == [round(40 / W, 4), round(60 / H, 4),
                         round(200 / W, 4), round(400 / H, 4)])
check("each box carries label, confidence and track id",
      dets[0]["label"] == "person" and dets[0]["conf"] == 0.91
      and dets[0]["id"] == 3 and "id" not in dets[2])
check("each box carries the colour _paint draws it in "
      "(amber, red for a violation, an alert's own colour)",
      dets[0]["color"] == "#ffaa00" and dets[1]["color"] == "#ff0000"
      and dets[2]["color"] == "#22ccaa", [d["color"] for d in dets])
check("...and the violation state", dets[1].get("viol") == 1 and "viol" not in dets[0])
m = dets[3].get("measure") or {}
check("a measured box carries its size text and lines",
      dets[3].get("size") == camera_mod.size_text(ov["detections"][3])
      and len(m.get("segs") or [])
      == len(camera_mod.measure_lines(ov["detections"][3])[0]) == 1
      and m["segs"][0][4] == "80 mm" and m.get("ring")
      and len(m.get("sil") or []) == 4, dets[3])
zones = body.get("zones") or []
shapes = camera_mod._zone_shapes(None, ov["overlays"])
check("the zones _paint draws, no more (the switched-off one is left out)",
      len(zones) == len(shapes) == 3, zones)
check("zone colours as painted: its own, red for a violation zone",
      zones[0]["color"] == "#00ff00" and zones[1]["color"] == "#ff0000")
check("a line zone keeps its kind and facing",
      zones[2]["kind"] == "line" and zones[2]["facing"] == "reverse"
      and zones[0]["kind"] == "poly")
legacy = overlay_mod.build({"zone": [10, 20, 110, 220], "overlays": []}, W, H)
check("the legacy rectangle is drawn as the painter draws it",
      legacy["zones"][0]["pts"][0] == [round(10 / W, 4), round(20 / H, 4)]
      and legacy["zones"][0]["color"] == "#00bade")
check("a link (within threshold, distance in pixels)",
      body.get("links") == [[round(120 / W, 4), round(230 / H, 4),
                             round(360 / W, 4), round(220 / H, 4), 1, 240]])
check("a bubble (intruded)", body.get("bubbles") == [[round(280 / W, 4), round(60 / H, 4),
                                                       round(440 / W, 4), round(380 / H, 4), 1]])
check("trails, live and fading", len(body.get("trails") or []) == 2
      and body["trails"][0][1] == 1 and body["trails"][1][1] == 0
      and len(body["trails"][0][0]) == 4)
check("the HUD rows", body.get("hud") == [["model", "yolov8s"], ["camera", "cam1"],
                                          ["hailo fps", "14.2"], ["camera fps", "30.0"]])


def coords(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("box", "pts", "sil", "segs", "trails", "bubbles", "links", "ring"):
                yield from nums(v, k)
            else:
                yield from coords(v)
    elif isinstance(node, list):
        for v in node:
            yield from coords(v)


def nums(v, key):
    if isinstance(v, list):
        if key in ("bubbles", "links") and v and not isinstance(v[0], list):
            yield from v[:4]
        elif key == "segs" and v and not isinstance(v[0], list):
            yield from v[:4]
        elif key == "ring" and v and not isinstance(v[0], list):
            yield from v[:3]
        else:
            for x in v:
                if isinstance(x, (int, float)) and not isinstance(x, bool):
                    yield x
                else:
                    yield from nums(x, key)


every = list(coords(body))
check("every coordinate is a fraction of the frame",
      every and all(0.0 <= x <= 1.0 for x in every), [x for x in every if not 0 <= x <= 1])
check("nothing to describe is None", overlay_mod.build(None, W, H) is None
      and overlay_mod.build(ov, 0, H) is None)

# -- 4. the feed ----------------------------------------------------------------------
clock = {"t": 1000.0}
feed = overlay_mod.Feed(hz=10, clock=lambda: clock["t"])
builds = []
real_build = overlay_mod.build


def counting_build(*a, **k):
    builds.append(1)
    return real_build(*a, **k)


overlay_mod.build = counting_build
check("no listener: nothing built, nothing sent",
      feed.offer("cam1", ov, W, H) is False and not builds)
li = feed.subscribe({"cam1"})
check("a listener counts as a connection, for its camera only",
      feed.connections("cam1") == 1 and feed.connections("cam2") == 0)
check("with a listener the overlay is sent", feed.offer("cam1", ov, W, H, first=True))
m1 = json.loads(li.get(0.1))
check("the message: version, camera, sequence, timestamp, frame size, first flag",
      m1["v"] == 1 and m1["cam"] == "cam1" and m1["seq"] == 1
      and m1["ts"] == 1000.0 and (m1["w"], m1["h"]) == (W, H) and m1["first"] == 1)
check("...and the body", all(m1[k] == body[k] for k in body))
clock["t"] += 0.05
ov2 = overlay()
ov2["detections"] = ov2["detections"][:1]
check("faster than overlay_hz: held back", feed.offer("cam1", ov2, W, H) is False)
clock["t"] += 0.1
dumps = []
real_dumps = overlay_mod.json.dumps
overlay_mod.json = types.SimpleNamespace(
    dumps=lambda *a, **k: (dumps.append(1), real_dumps(*a, **k))[1])
check("unchanged overlay: not serialised, not sent",
      feed.offer("cam1", overlay(), W, H) is False and not dumps)
check("changed: sent", feed.offer("cam1", ov2, W, H) and len(dumps) == 1)
m2 = json.loads(li.get(0.1))
check("the sequence rises", m2["seq"] == m1["seq"] + 1 and len(m2["dets"]) == 1)
late = feed.subscribe({"cam1"})
check("a browser joining mid-way is primed with the current picture",
      json.loads(late.get(0.1))["seq"] == m2["seq"])
feed.unsubscribe(late)
other = feed.subscribe({"cam2"})
clock["t"] += 0.2
feed.offer("cam1", ov, W, H)
check("a listener gets only its own cameras", other.get(0.05) is None)
feed.unsubscribe(other)
# The queue: bounded per camera, the OLDEST goes.
q = overlay_mod.Listener({"a"})
for i in range(q.queue.maxlen + 3):
    q.put(str(i).encode())
check("a full queue drops the oldest",
      q.dropped == 3 and q.get(0) == b"3" and len(q.queue) == q.queue.maxlen - 1)
check("an empty queue waits (no spin) and answers None",
      overlay_mod.Listener({"a"}).get(0.05) is None)
feed.unsubscribe(li)
builds.clear()
clock["t"] += 1
check("the last browser gone: nothing built again",
      feed.offer("cam1", ov2, W, H) is False and not builds
      and feed.connections() == 0)
lis = [feed.subscribe({"x"}) for _i in range(overlay_mod.MAX_LISTENERS + 1)]
check("past MAX_LISTENERS the oldest listener is closed",
      lis[0].closed and not lis[-1].closed
      and feed.connections() == overlay_mod.MAX_LISTENERS)
feed.close_all()
check("close_all lets every listener go", all(x.closed for x in lis)
      and feed.connections() == 0)
overlay_mod.build = real_build
overlay_mod.json = json

# -- 5. the app's /overlay endpoint ---------------------------------------------------
s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
feed = overlay_mod.Feed(hz=30, resolver=lambda cid: "cam1" if cid in ("", "cam1") else None)
apprun._serve_stream(lambda cid: None, lambda cid: 1.0, PORT,
                     probe=lambda cid: object() if cid == "cam1" else None,
                     overlay_feed=feed)


def get(path):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    conn.request("GET", path)
    return conn, conn.getresponse()


conn, r = get("/overlay?camera=cam1")
check("server mode: /overlay is a 404", r.status == 404)
conn.close()
feed.enabled = True
conn, r = get("/overlay?camera=nope")
check("an unknown camera is a 404", r.status == 404)
conn.close()
conn, r = get("/overlay")
check("client mode: an event stream",
      r.status == 200 and r.getheader("Content-Type") == "text/event-stream")
check("...that opens with the reconnect hint", r.fp.readline() == b"retry: 3000\n")
r.fp.readline()
deadline = time.time() + 3
while feed.connections("cam1") == 0 and time.time() < deadline:
    time.sleep(0.02)
check("the bare request listens to the first camera", feed.connections("cam1") == 1)
feed.offer("cam1", ov, W, H, first=True)
line = r.fp.readline()
check("an offered overlay arrives as one data event",
      line.startswith(b"data: ") and json.loads(line[6:])["cam"] == "cam1")
r.close()                                  # the browser goes away
conn.close()
deadline = time.time() + 5
n = 0
while feed.connections() and time.time() < deadline:
    n += 1
    ov3 = overlay()
    ov3["hud"] = [("tick", str(n))]
    feed.offer("cam1", ov3, W, H)
    time.sleep(0.05)
check("a browser that left is let go (no data built for nobody)",
      feed.connections() == 0)

print(f"\n{'ALL PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
