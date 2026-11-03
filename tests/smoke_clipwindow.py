# A clip has to be WATCHABLE, and it has to include the run-up.
#
# Two faults, both found on a live box:
#
#   * every clip was written with the `mp4v` fourcc -- an MPEG-4 Part 2 stream
#     that no browser plays. The file downloaded, opened in the player, and sat
#     at 0:00 for ever: a recording that exists and cannot be watched.
#   * and it began at the trigger, so how the forklift GOT there was never in
#     it. That needs a rolling buffer, which is a real cost and is why it is
#     off until asked for.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import capture as cap                                    # noqa: E402
from core.services import detector_control as dc                  # noqa: E402
from core.services import workflow as wf                          # noqa: E402

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
    def __init__(self):
        self.saved = {}

    def save_snapshot(self, sid, data):
        self.saved[sid] = data


def new_capture():
    return cap.Capture(tempfile.mkdtemp(), FakeStore(), fps=10, tag="cam1")


# -- the clip is written as something a browser can play --------------------
src = read("systems", "demo", "app", "capture.py")
check("clips are written H.264", 'VideoWriter_fourcc(*"avc1")' in src)
check("...with mp4v only as a fallback that says so",
      "no H.264 encoder" in src and 'VideoWriter_fourcc(*"mp4v")' in src)
# The header's frame rate decides playback speed: a 44fps header over frames
# that arrived at 12 plays five seconds of event as a one-second blur.
c = new_capture()
c.set_fps(12.4)
check("the clip is written at the rate frames actually arrive", c.fps == 12)
c.set_fps(0)
check("...and a camera with no measurement yet keeps the last known rate",
      c.fps == 12)

# -- the run-up costs nothing until it is asked for -------------------------
c = new_capture()
check("no run-up by default", not c.wants_frames())
c.want_preroll(2)
check("asking for one means the frames are needed every tick", c.wants_frames())
c.want_preroll(0)
check("...and turning it off frees the buffer at once",
      not c.wants_frames() and not c._buffer)

# The buffer holds only the seconds asked for -- an unbounded ring on a Pi is
# a memory leak with a feature name.
c.want_preroll(1)
now = time.time()
for i in range(30):                       # 3 seconds at 10fps
    c._remember(_FRAME := None, now + i * 0.1)
check("a frame that cannot be kept is not kept", not c._buffer)

# -- an armed clip is armed for as long as it was asked for -----------------
c = new_capture()
c.record(after_s=2, tag="t")
check("recording is armed forward", c.armed())
check("...and capped: a trigger every frame cannot record for ever",
      c._record_until - time.time() <= cap.MAX_CLIP_S + 0.1)

# -- the object id reaches the file name and the picture --------------------
c = new_capture()
sid = c.snap(b"not-a-jpeg", tag="x", oid="42")
check("a snapshot's id carries the object", "oid42" in (sid or ""), str(sid))
check("...and one with no object is named as it always was",
      "oid" not in (new_capture().snap(b"x", tag="y") or "oid"))
check("the picture itself is stamped", "_oid_text" in src and "OID {oid}" in src)

# -- the node and the output agree on the two halves ------------------------
spec = wf.REGISTRY["output.record"]["data"]
check("the node offers before and after",
      spec.get("before_s") == 0 and spec.get("after_s") == 5, str(spec))
check("...and the run-up is OFF by default -- it is not free",
      spec.get("before_s") == 0)
out = dc._validate_output({"id": "r1", "type": "record", "trigger": "alert",
                           "before_s": 99, "after_s": 99})
check("both halves are capped at 5s",
      (out["before_s"], out["after_s"]) == (5.0, 5.0), str(out))
# An output written before the split carries only duration_s, and it meant
# "after the trigger" -- so that is exactly what it keeps meaning.
old = dc._validate_output({"id": "r2", "type": "record", "trigger": "alert",
                           "duration_s": 3})
check("an older output records the same clip it always did",
      (old["after_s"], old["before_s"]) == (3.0, 0.0), str(old))
check("...and duration_s stays in step for an older app build",
      old["duration_s"] == 3.0, str(old))

graph = {"version": 9, "nodes": [
    {"id": "rec", "type": "output.record", "position": {"x": 0, "y": 0},
     "data": {"duration_s": 4, "mode": "once"}}], "edges": []}
check("a saved graph migrates", wf.migrate_clip_window(graph) is True)
data = graph["nodes"][0]["data"]
check("...to the same clip: after = the old duration, before = none",
      (data["after_s"], data["before_s"]) == (4.0, 0), str(data))
check("...and says which schema it now is", graph["version"] == 10)
check("running it twice changes nothing", wf.migrate_clip_window(graph) is False)

# -- the object id rides on the EVENT, not on a wire ------------------------
fns = read("systems", "demo", "app", "functions.py")
check("a rule's alarm names the object it is about", '"source_oid"' in fns)
check("...an intrusion names the intruder", '"source_oid": ti' in fns)
run_py = read("systems", "demo", "app", "run.py")
check("the capture hooks are given the event",
      "def _snap(o, event=None)" in run_py and "def _record(o, event=None)" in run_py)
check("...and stamp only when the output asked to",
      'if not o.get("oid_tag")' in run_py)
outs = read("systems", "demo", "app", "outputs.py")
check("the output engine passes the event along", "hook(o, event or {})" in outs)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
