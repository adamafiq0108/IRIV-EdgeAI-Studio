# One frozen camera used to take a whole system's UI with it.
#
# A stalled camera is INVISIBLE at the picture layer: every viewer path falls
# back to the last good frame, so the feed shows a perfectly still room, no
# `error` event reaches the browser, and StreamImg's stall watchdog has already
# stood down after the first frame. The stream then lives for ever. An operator
# clicking around opens fresh previews beside the dead ones until the browser's
# ~6 connections per origin are all held by streams that will never speak
# again -- at which point every OTHER request on the page (stats, controls,
# config) is never sent, and the only cure is going back to the Hub, which
# force-closes the sockets. That is the "camera freezes, whole system dies,
# have to leave and come back" report.
#
# Three halves to the fix, all pinned here: the app SAYS a camera has stalled,
# it ENDS the stream if the stall persists, and the browser re-arms a watchdog
# once it has proved it reports frames one at a time.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


run = read("systems", "demo", "app", "run.py")
cam = read("systems", "demo", "app", "camera.py")
img = read("frontend", "src", "components", "StreamImg.jsx")

# -- the camera can be asked how stale it is -------------------------------
check("a camera reports how long it has delivered nothing",
      "def stalled_s(self)" in cam)
check("...measured from the last GOOD frame, not the last request",
      "self._last_good" in cam.split("def stalled_s(self)")[1][:900])


# The real thing, with the clock moved rather than waited on.
class _FakeCam:
    def __init__(self, last_good):
        self._last_good = last_good

    @property
    def stalled_s(self):
        gap = time.time() - (self._last_good or 0)
        return gap if gap > 0 else 0.0

    def last_jpeg(self):
        return b"stale"

    def latest(self):
        return object(), 1


import time                                                   # noqa: E402

sys.modules.setdefault("cv2", None)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))


class _Pipe:
    def __init__(self, age):
        self.cam = _FakeCam(time.time() - age)
        self.overlay = None
        self.jpeg = b"stale"


# _viewer_stall is the whole decision, and it is pure: import the module's
# source and exercise the function without starting an app.
ns = {"time": time, "camera_mod": type("m", (), {
    "notice_jpeg": staticmethod(lambda text, **kw: b"NOTICE:" + text.encode())})}
src = run[run.index("STALL_NOTICE_S = "):run.index("def _raw_jpeg(")]
exec(compile(src, "run_stall", "exec"), ns)                   # noqa: S102
viewer_stall = ns["_viewer_stall"]

check("a healthy camera is not interfered with",
      viewer_stall(_Pipe(0.0)) is False)
check("a camera stalled a few seconds says so, in the picture",
      viewer_stall(_Pipe(10.0)).startswith(b"NOTICE:"),
      str(viewer_stall(_Pipe(10.0)))[:60])
# None is the existing "camera gone" signal, which _stream already turns into
# an ended connection after 10s -- so the socket closes, the slot comes back,
# and the browser reconnects on its own.
check("a camera stalled for good ends the stream instead",
      viewer_stall(_Pipe(60.0)) is None)
check("no pipe at all still ends it", viewer_stall(None) is None)
check("the notice is rendered once, not per frame per viewer",
      "_STALL_JPEG" in run and "global _STALL_JPEG" in run)

# Both viewer paths, because a SHARED camera that silently freezes is worse,
# not better, for belonging to somebody else.
check("the annotated feed reports a stall", "_viewer_stall(pipe)" in run)
check("...and so does /raw, which another system drinks",
      "def _raw_jpeg(pipe)" in run and "_raw_jpeg(_pipe_for(cid))" in run)

# -- closing a wedged camera must not take the app down --------------------
close = cam[cam.index("    def close(self):"):]
close = close[:close.index("\n    def ", 10)]
check("closing waits long enough for a wedged grab to return",
      "join(timeout=12.0)" in close, close)
check("...and never releases a device the grab thread is still inside",
      "if joined:" in close and "did not stop" in close)

# -- the browser notices a stream that stopped speaking --------------------
check("StreamImg has a mid-stream watchdog", "DEFAULT_MID_STALL_MS" in img)
check("...armed only once the browser proves it reports frames one at a time",
      "frames.current >= 2" in img)
check("...and re-armed per frame, so it fires only on silence",
      "midStallMs" in img.split("onLoad=")[1][:900])
check("...and reset per attempt", "frames.current = 0" in img)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
