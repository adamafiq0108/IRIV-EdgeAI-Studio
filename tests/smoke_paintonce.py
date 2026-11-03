"""One camera frame is painted once, however many ways it is watched.

The WebRTC publish (_publish_loop) and every MJPEG viewer (_annotated_jpeg)
painted the same overlay onto the same grabbed frame on their own -- with
WebRTC and one MJPEG tab open, two full renders per camera frame on a 4-core
CM5 that four cameras already run at ~98%. _painted keeps the last render per
camera, keyed by the grabbed frame, its sequence, the overlay snapshot and
the zone colours, and both paths read it.

The pixels must be EXACTLY what the old per-caller _paint produced: this test
paints the same frame and overlay both ways and compares the arrays.

    python tests/smoke_paintonce.py
"""
import os
import sys
import tempfile
import threading
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

# The paint path is OpenCV's; without it annotate takes the PIL fallback,
# which is not what a board runs. Same skip as smoke_overlaycost.
try:
    import cv2                                                 # noqa: E402,F401
    import numpy as np                                         # noqa: E402
except ImportError:
    print("SKIP: needs opencv (python3-opencv on a board); not a pip "
          "requirement, so a CI runner has none")
    sys.exit(77)

from app import camera as camera_mod                           # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


rng = np.random.default_rng(7)
FRAME = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
PRISTINE = FRAME.copy()


def zone(zid, color, pts):
    return {"id": zid, "name": zid, "kind": "polygon", "enabled": True,
            "violation": False, "color": color, "points_px": pts,
            "labels": [], "facing": "forward"}


def overlay():
    """Everything a busy camera draws: boxes (one violating, one coloured by
    an alert), two zones, a link, a bubble, a trail and the HUD."""
    return {
        "detections": [
            {"label": "person", "confidence": 0.91, "box": [40, 60, 200, 400],
             "track_id": 3},
            {"label": "person", "confidence": 0.77, "box": [300, 80, 420, 360],
             "track_id": 4, "violation": True, "zone": "z1"},
            {"label": "car", "confidence": 0.66, "box": [450, 200, 620, 330],
             "box_color": "#22ccaa"},
        ],
        "zone": None,
        "overlays": [zone("z1", "#00ff00", [(250, 50), (500, 60), (480, 420), (240, 400)]),
                     zone("z2", "#3366ff", [(10, 300), (200, 300), (200, 470), (10, 470)])],
        "links": [(120, 230, 360, 220, True, 240.0)],
        "bubbles": [(280, 60, 440, 380, True)],
        "trails": [([(100, 400), (110, 390), (125, 380), (140, 372)], True)],
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


def pipe(frame, seq, ov):
    return types.SimpleNamespace(
        overlay=ov, cam=Cam(frame, seq), cfg={}, jpeg=None,
        view_seq=-1, view_jpeg=None, view_lock=threading.Lock(),
        paint_key=None, paint_frame=None, paint_lock=threading.Lock())


# Count real renders through the module's own _paint.
real_paint = apprun._paint
paints = []


def counting_paint(ov, frame, hud=True):
    paints.append(hud)
    return real_paint(ov, frame, hud=hud)


apprun._paint = counting_paint

# -- 1. the same pixels as before ---------------------------------------------
ov = overlay()
old = real_paint(ov, FRAME.copy())
p = pipe(FRAME, 11, ov)
new = apprun._painted(p, FRAME, 11)
check("the shared paint is pixel-identical to the old per-caller _paint",
      new.shape == old.shape and np.array_equal(new, old),
      f"max diff {int(np.abs(new.astype(int) - old.astype(int)).max())}")
check("...and actually draws (not the bare frame)",
      not np.array_equal(new, FRAME))
check("the grabbed frame itself is never written to",
      np.array_equal(FRAME, PRISTINE) and FRAME.flags.writeable)
check("the shared array is read-only", not new.flags.writeable)
check("...and still encodes", bool(camera_mod.encode_jpeg(new)))

# -- 2. painted once for both watchers ----------------------------------------
paints.clear()
again = apprun._painted(p, FRAME, 11)
check("the same grab and overlay is not painted twice", again is new and not paints)
p.view_seq, p.view_jpeg = -1, None
jpeg = apprun._annotated_jpeg(p)
check("an MJPEG viewer reuses the publish's paint", not paints, paints)
check("...and serves exactly that picture",
      jpeg == camera_mod.encode_jpeg(old))

# -- 3. anything that changes the pixels repaints -----------------------------
paints.clear()
frame2 = FRAME.copy()
frame2[:40] = 0
fresh = apprun._painted(p, frame2, 12)
check("a new grab repaints", len(paints) == 1
      and np.array_equal(fresh, real_paint(ov, frame2)))
paints.clear()
apprun._painted(p, frame2.copy(), 12)
check("a different frame under the same sequence (camera reopened) repaints",
      len(paints) == 1)
paints.clear()
ov2 = overlay()
ov2["detections"] = ov2["detections"][:1]
p.overlay = ov2
got = apprun._painted(p, FRAME, 11)
check("a new overlay (the loop's next _draw) repaints",
      len(paints) == 1 and np.array_equal(got, real_paint(ov2, FRAME)))
paints.clear()
# _stamp_colors recolours zone dicts IN PLACE on the loop thread.
ov2["overlays"][0]["color"] = "#ff0000"
got = apprun._painted(p, FRAME, 11)
check("a zone recoloured in place repaints, in the new colour",
      len(paints) == 1 and np.array_equal(got, real_paint(ov2, FRAME)))

# -- 4. what stays OUT of the shared paint ------------------------------------
p.overlay = None
paints.clear()
bare = apprun._painted(p, FRAME, 13)
check("no overlay yet: the bare frame, untouched and still writable",
      bare is FRAME and FRAME.flags.writeable and not paints)
p.overlay = ov
apprun._painted(p, FRAME, 11)
key_before = p.paint_key
steps = [{"op": "grayscale"}]
p.cfg = {"preprocess_preview": True, "preprocess": steps}
p.view_seq, p.view_jpeg = -1, None
jpeg = apprun._annotated_jpeg(p)
from app import preprocess as preprocess_mod                   # noqa: E402
want = camera_mod.encode_jpeg(real_paint(ov, preprocess_mod.apply(FRAME, steps)))
check("preprocess_preview paints its own (processed) picture",
      jpeg == want and jpeg != camera_mod.encode_jpeg(old))
check("...and leaves the shared paint alone", p.paint_key is key_before)
p.cfg = {}

# -- 5. many threads, one grab ------------------------------------------------
p = pipe(FRAME, 21, ov)
out, errs = [], []


def hammer():
    try:
        for _i in range(20):
            out.append(apprun._painted(p, FRAME, 21))
    except Exception as exc:                                   # noqa: BLE001
        errs.append(exc)


threads = [threading.Thread(target=hammer) for _i in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("8 threads x 20 reads: every one is the old picture",
      not errs and len(out) == 160 and all(np.array_equal(a, old) for a in out),
      errs)

apprun._paint = real_paint
print(f"\n{'ALL PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
