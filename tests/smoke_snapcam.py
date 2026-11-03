# A Snapshot camera sleeps between snaps; every other camera is untouched.
#
# The decisions live in app/snapcam.py and are tested here with a fake camera:
#   * which idle mode a camera gets (network stream keeps, the rest close, the
#     node can force either, a file or the synthetic camera is left alone)
#   * it sleeps only after the idle grace, wakes the moment a snap is owed, and
#     the first frames after a wake are held back so the snap is the press
#   * a camera that is not Snapshot is never slept or slowed, and one that was
#     is brought back
#   * another system reading its /raw feed keeps it awake
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import snapcam, snapgate                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class Cam:
    def __init__(self, source="0", **kw):
        self.source = source
        self.synthetic = kw.get("synthetic", False)
        self.file_path = kw.get("file_path")
        self.held_still = False
        self.asleep = False
        self.low_rate = False
        self.seq = 1
        self.log = []

    def latest(self):
        return object(), self.seq

    def sleep(self):
        self.log.append("sleep")
        self.asleep = True

    def wake(self):
        self.log.append("wake")
        self.asleep = False


class Pipe:
    def __init__(self, cam, snapshot=None):
        self.id = "main"
        self.cam = cam
        self.cfg = {"snapshot": snapshot} if snapshot else {}
        self.snap = None
        self.parked = False
        self.opening = False
        self.reopened = 0

    def open_async(self):
        self.reopened += 1


SNAP = {"sources": [{"kind": "inject", "key": "fn_k_fired", "fn": "k", "frames": 1}]}


def settle(pipe):
    """Let a started sleep/wake thread finish."""
    gate = pipe.snap
    for _ in range(200):
        if not gate.cam_state.get("busy"):
            return
        time.sleep(0.005)


def run(pipe, now):
    snapgate.sync(pipe)
    snapcam.tick(pipe, now)
    if pipe.snap is not None:
        settle(pipe)


# -- the mode ------------------------------------------------------------------
check("a USB camera closes between snaps", snapcam.mode_of(Pipe(Cam("0"), SNAP)) == "close")
check("an RTSP camera keeps open", snapcam.mode_of(Pipe(Cam("rtsp://10.0.0.5/s"), SNAP)) == "keep")
forced = {**SNAP, "idle": "keep"}
check("the node can force keep on a USB camera", snapcam.mode_of(Pipe(Cam("0"), forced)) == "keep")
forced = {**SNAP, "idle": "close"}
check("...or close on an RTSP camera", snapcam.mode_of(Pipe(Cam("rtsp://x/y"), forced)) == "close")
check("a file or the synthetic camera is left alone",
      snapcam.mode_of(Pipe(Cam("a.mp4", file_path="a.mp4"), SNAP)) == "off"
      and snapcam.mode_of(Pipe(Cam("synthetic", synthetic=True), SNAP)) == "off")

# -- close: sleep after the grace, wake on a snap, hold the first frames -------
pipe = Pipe(Cam("0"), SNAP)
t = 1000.0
run(pipe, t)
run(pipe, t + 1.0)
check("it does not sleep inside the idle grace", not pipe.cam.asleep)
run(pipe, t + snapcam.IDLE_GRACE_S + 0.1)
check("it sleeps once idle long enough", pipe.cam.asleep and pipe.cam.log == ["sleep"], pipe.cam.log)
check("...and holds no frames while idle", pipe.snap.hold is False)

pipe.snap.left = 1                       # a snap is owed
run(pipe, t + 10)
check("it wakes the moment a snap is owed", pipe.cam.log == ["sleep", "wake"] and not pipe.cam.asleep,
      pipe.cam.log)
run(pipe, t + 10.1)
check("the first frames after a wake are held back", pipe.snap.hold is True)
pipe.cam.seq += snapcam.WARM_FRAMES
run(pipe, t + 10.2)
check("...and released once the camera has warmed", pipe.snap.hold is False)
run(pipe, t + 10.3)
check("a warm camera is not woken twice", pipe.cam.log == ["sleep", "wake"], pipe.cam.log)

pipe2 = Pipe(Cam("0"), SNAP)
run(pipe2, t)
pipe2.snap.left = 1
run(pipe2, t + 0.1)
pipe2.cam.seq = 1
run(pipe2, t + 0.2)
run(pipe2, t + 0.2 + snapcam.WARM_MAX_S + 0.1)
check("a camera that never warms is released after the limit", pipe2.snap.hold is False)

# -- the gate honours the hold ---------------------------------------------------
pipe3 = Pipe(Cam("0"), SNAP)
snapgate.sync(pipe3)
pipe3.snap.left = 1
pipe3.snap.hold = True
out, _ = snapgate.filter_frames([(pipe3, "frame")])
check("a held gate spends no frame", out[0][1] is None and pipe3.snap.left == 1)
pipe3.snap.hold = False
out, _ = snapgate.filter_frames([(pipe3, "frame")])
check("...and spends it once released", out[0][1] == "frame" and pipe3.snap.left == 0)

# -- keep: slow reads, back to full rate on a snap -------------------------------
pk = Pipe(Cam("rtsp://10.0.0.5/s"), SNAP)
run(pk, t)
run(pk, t + snapcam.IDLE_GRACE_S + 0.1)
check("a kept camera is read slowly when idle", pk.cam.low_rate and not pk.cam.asleep)
pk.snap.left = 1
run(pk, t + 20)
check("...and read at full rate again when a snap is owed", not pk.cam.low_rate)
check("...with the stale frame held back", pk.snap.hold is True)

# -- not a Snapshot camera: nothing changes --------------------------------------
pv = Pipe(Cam("0"))
for i in range(5):
    run(pv, t + i * 10)
check("a Video camera is never slept or slowed",
      not pv.cam.asleep and not pv.cam.low_rate and pv.cam.log == [] and pv.reopened == 0)
back = Pipe(Cam("0"))
back.cam.asleep = True
back.cam.low_rate = True
run(back, t)
check("a camera that stops being Snapshot is brought back",
      back.cam.low_rate is False and back.cam.asleep is False and back.reopened == 1)

# -- another system drinking the feed --------------------------------------------
ps = Pipe(Cam("0"), SNAP)
run(ps, t)
snapcam.RAW_SEEN["main"] = t + 5
run(ps, t + 6)
check("a /raw viewer keeps the camera awake", not ps.cam.asleep, ps.cam.log)
snapcam.RAW_SEEN.clear()

# -- the source, wired in ----------------------------------------------------------
run_src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"), encoding="utf-8").read()
cam_src = open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"), encoding="utf-8").read()
check("the loop ticks every camera before reading", "snapcam_mod.tick(_p, now)" in run_src)
check("a sleeping camera is not run through the recovery ladder",
      'if getattr(pipe.cam, "asleep", False):' in run_src)
check("a sleeping camera is not called lost by the watchdog",
      'f is not None or getattr(pipe.cam, "asleep", False)' in run_src)
check("a /raw reader is noted", "snapcam_mod.RAW_SEEN[pipe.id]" in run_src)
check("the camera can sleep and wake and reads healthy while asleep",
      "def sleep(self):" in cam_src and "def wake(self):" in cam_src
      and "if self.asleep:\n            return 0.0" in cam_src)
check("the slow read is one frame a second", "keep_gap = 1.0 if self.low_rate" in cam_src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
