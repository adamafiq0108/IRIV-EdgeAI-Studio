# Recording a clip must not be visible in the live feed.
#
# It was. Two costs sat squarely in the detection loop for as long as a clip
# was open: a full annotated render of a 920x640 frame per tick, and a
# SOFTWARE H.264 encode of it -- cv2's writer, called synchronously. Together
# they took tens of milliseconds out of every iteration, so inference slowed,
# the measured rate fell, and the picture people were watching went choppy
# exactly while something worth recording was happening.
#
# Neither has to happen between two detections. The encode moves to its own
# thread behind a bounded queue, and the render is skipped entirely on ticks
# the clip does not want -- a clip is written at 12fps and the loop can run at
# twice that.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from app import capture as cap                                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class FakeStore:
    def save_snapshot(self, sid, data):
        return None


def new_capture(fps=10):
    return cap.Capture(tempfile.mkdtemp(), FakeStore(), fps=fps, tag="cam1")


src = read("systems", "demo", "app", "capture.py")
run = read("systems", "demo", "app", "run.py")

# -- the loop hands frames over and returns --------------------------------
check("frames are queued, not encoded, on the caller's thread",
      "def _submit(self" in src and "self._qlock.notify()" in src)
check("...to a thread of its own", "name=\"clip-encoder\"" in src
      and "daemon=True" in src)
check("...that never dies on one bad frame",
      "encoder error" in src)
# A blocking queue would move the stall rather than remove it, and an
# unbounded one turns a slow encoder into a memory leak at 1.7 MB a frame.
check("a backlog drops the oldest frame rather than stalling the loop",
      "QUEUE_MAX" in src and "self._dropped += 1" in src)
check("...and never drops the close marker, which would leave a clip open",
      "if item is not None:" in src)
check("...and says so, once, rather than silently losing frames",
      "could not keep up" in src)

# -- the clip is closed only once the queued frames have reached it --------
check("the end of a window is a marker in the queue, not an immediate close",
      "self._writing = False" in src and "self._submit(None)" in src)
check("...and arming tests that flag, not the writer the thread opens later",
      "if not self._writing:" in src)

# -- the render is skipped when the clip does not want a frame ------------
check("the capture says whether a frame is wanted yet", "def due(self" in src)
check("...and the loop asks BEFORE painting one",
      "if pipe.capture.due(now_j):" in run
      and run.index("capture.due(now_j)") < run.index("_paint(pipe.overlay, frame, hud=False)"))
# offer() is what closes a finished clip, so a skipped tick still has to call
# it -- otherwise the writer stays open until the next frame happens to be due.
check("...but still offers on a skipped tick, so a finished clip closes",
      "pipe.capture.offer(None, now_j)" in run)

# -- the rate is capped, and the header agrees with the feed ---------------
c = new_capture()
c.set_fps(25)
check("a fast loop does not write a 25fps clip", c.fps == int(cap.MAX_CLIP_FPS),
      str(c.fps))
check("...and the take-rate matches the header, or playback speed is wrong",
      abs(c._interval - 1.0 / c.fps) < 1e-9, f"{c._interval} vs 1/{c.fps}")

now = time.time()
c._last_taken = now
check("a frame arriving too soon is not wanted", not c.due(now + 0.01))
check("...and one arriving on time is", c.due(now + 1.0 / c.fps))

# -- end to end, with the real thread --------------------------------------
# No cv2 on a dev box, so _write cannot open a real encoder; what is proven
# here is that a frame handed to offer() reaches the writer thread and that
# the caller was never blocked by it.
class _Frame:                      # anything offer() will accept as an image
    shape = (640, 920, 3)


written = []
c = new_capture()
c._write = written.append
c.record(after_s=1, tag="t")
started = time.time()
for i in range(6):
    # 0.12s apart: comfortably due at 12fps, and all six inside the 1s window.
    c.offer(_Frame(), started + i * 0.12)
elapsed = time.time() - started
check("offering frames costs the caller nothing", elapsed < 0.2, f"{elapsed:.3f}s")
deadline = time.time() + 3.0
while len(written) < 6 and time.time() < deadline:
    time.sleep(0.02)
check("...and every one of them reaches the encoder", len(written) == 6,
      str(len(written)))

# Past the window, the marker closes it -- and only after the frames.
c._close = lambda: written.append("CLOSED")
c._writer = object()
c.offer(None, c._record_until + 0.1)
deadline = time.time() + 3.0
while "CLOSED" not in written and time.time() < deadline:
    time.sleep(0.02)
check("the clip closes after the frames it was given, not before",
      written[-1] == "CLOSED" and len(written) == 7, str(written[-3:]))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
