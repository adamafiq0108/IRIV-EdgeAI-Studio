# In Snapshot mode the live view is the last snapped picture, held.
#
# A Detection Stream set to Snapshot looks at nothing between triggers, so a
# live view that still painted boxes onto every new camera frame, encoded it for
# every viewer and published a moving video would spend the CPU the mode exists
# to save. What the viewer sees instead is the picture that was last snapped
# (with its boxes), held:
#
#   * the MJPEG viewer path answers the held JPEG without grabbing, painting or
#     encoding anything (and a camera in Video mode is exactly as before)
#   * viewers are paced at two frames a second, not at camera speed
#   * the WebRTC publish sends one held frame a second
#   * the idle passes between snaps do not repaint the kept picture
#   * only a Snapshot camera is affected: with no gate, nothing here applies
#
# The behaviour is run where app.run can be imported (the box); the source is
# checked everywhere.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"), encoding="utf-8").read()

# -- the source, everywhere ----------------------------------------------------
check("the MJPEG viewer path answers the held snap",
      'getattr(pipe, "snap", None) is not None and getattr(pipe, "snap_jpeg", None) is not None:' in src
      and "return pipe.snap_jpeg" in src)
check("viewers of a Snapshot camera are paced at four a second",
      "4.0 if p.snap is not None else" in src)
check("the WebRTC publish sends one held frame a second in Snapshot mode",
      "SNAPSHOT MODE: the live view is the last snapped picture," in src
      and "_publish(pipe, held)" in src and "pipe.snap_event.wait(1.0)" in src)
check("idle passes between snaps do not repaint the kept picture",
      "if pipe.snap is not None and pipe.snap.idle_now and pipe.shot is not None:" in src)
check("an idle Snapshot camera adds no batch rounds (press-to-snap latency)",
      "rounds = min(rounds, max(1, owed))" in src
      and "gates = [snapgate_mod.sync(p) for p in live]" in src)
check("before the first snap the live view says Waiting for snapshot",
      "Waiting for snapshot" in src and "_waiting_jpeg(pipe)" in src and "_waiting_frame(cam_frame)" in src)
check("a Snapshot camera's WebRTC publish makes every frame a key-frame",
      "gop=1" in src and "snap_mode" in src
      and "pub.snapshot" not in src and 'getattr(pub, "snapshot", False) != snap_mode' in src)
check("a snapped picture is remembered for the live view",
      "p.snap_jpeg = jpeg" in src and '"snap", "snap_jpeg",' in src)

# -- the behaviour, where the app can be imported -------------------------------
try:
    from app import run as apprun                               # noqa: E402
except ImportError as exc:
    print(f"skip the behaviour: app.run cannot be imported here ({exc})")
    if bad:
        sys.exit(1)
    print("ALL OK (source only)")
    sys.exit(0)


class Cam:
    stalled_s = 0.0
    fps = 25.0
    measured_fps = 25.0

    def __init__(self):
        self.touched = 0

    def latest(self):
        self.touched += 1
        return None, 0

    def last_jpeg(self):
        self.touched += 1
        return b"live"


class Gate:
    idle_now = True


class Pipe:
    def __init__(self, snap, snap_jpeg=None):
        self.cam = Cam()
        self.snap = snap
        self.snap_jpeg = snap_jpeg
        self.overlay = None
        self.jpeg = b"cached"
        self.cfg = {}


held = Pipe(Gate(), b"SNAPPED")
got = apprun._annotated_jpeg(held)
check("a Snapshot camera's viewer gets the held snapped picture", got == b"SNAPPED", got)
check("...without touching the camera at all", held.cam.touched == 0, held.cam.touched)

try:
    import cv2                                                 # noqa: F401
except ImportError:
    cv2 = None                      # the waiting picture is drawn with opencv
if cv2 is None:
    print("skip before the first snap the viewer gets the waiting picture (no opencv here)")
else:
    before_first = Pipe(Gate(), None)
    check("before the first snap the viewer gets the waiting picture",
          (apprun._annotated_jpeg(before_first) or b"")[:2] == bytes([255, 216]))

video = Pipe(None, b"SNAPPED")
got = apprun._annotated_jpeg(video)
check("a Video camera never gets the held picture", got != b"SNAPPED", got)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
