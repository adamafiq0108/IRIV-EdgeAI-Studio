"""The video runs at the camera's rate, whatever the accelerator is doing.

Reported with a screenshot: the status box read "camera fps 56.3" over a
picture that visibly stuttered, with "hailo fps 17.4" beside it. Both numbers
were true. The WebRTC publish was made from inside the detection loop, one
frame per inference, so the video WAS 17 fps -- and arriving in the bursts a
batch comes back in, which reads worse than a steady 17.

The MJPEG path has painted for the viewer since "the video is painted for the
viewer, not by the detection loop": last known boxes on the newest grabbed
frame, off the loop entirely. This is the same contract for the publish --
detection is slow because a chip is shared; video has no reason to be.

    python tests/smoke_webrtcrate.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


src = io.open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
              encoding="utf-8").read()

check("the publish has its own loop",
      "def _publish_loop(pipe):" in src)
check("...and the detection loop no longer makes one",
      "_publish(pipe, stamped)" not in src,
      "publishing from _draw is publishing at the inference rate, which is "
      "the whole fault")
check("...it sends the NEWEST grabbed frame",
      "frame, seq = pipe.cam.latest()" in src
      and "seq == pipe.publish_seq" in src,
      "a frame the loop finished with is one inference old before it is sent")
check("...painted with the last overlay, so the boxes still come from the loop",
      "_publish(pipe, _painted(pipe, frame, seq))" in src
      and "painted = _paint(overlay, frame)" in src,
      "the two viewing paths read the same overlay; they may differ only in "
      "how fresh the image under the boxes is")
check("...and sends the picture even before anything has been detected",
      "if frame is None or not overlay:\n        return frame" in src,
      "a system whose first inference has not landed must not show black")
check("each frame is sent once",
      "pipe.publish_seq = seq" in src and "self.publish_seq = -1" in src,
      "without a cursor the thread either re-sends the same picture or spins "
      "on a stalled camera")
check("the rate follows the camera, not the chip",
      'getattr(pipe.cam, "measured_fps", 0)' in src
      and "1.0 / min(rate, WEBRTC_MAX_FPS)" in src)
check("...bounded, because nobody watches 60fps on a dashboard tile",
      "WEBRTC_MAX_FPS = 30.0" in src,
      "past 30 every extra frame costs a paint, a convert and an encode on a "
      "box that also has an accelerator to feed")
check("a failing publish thread costs the publish and nothing else",
      "webrtc publish thread:" in src,
      "the MJPEG feed is the first way to watch a camera and must survive "
      "this thread dying")
check("it stops with the app",
      "while not _stop.is_set():" in src and 'name=f"publish-{p.id}", daemon=True' in src)
check("a camera added later gets a thread too",
      src.count("_start_publishers()") == 3,
      "defined once, called at startup and again after a camera reload -- a "
      "camera switched on mid-run would otherwise never be published")
check("...and never twice for the same camera",
      "if p.id in _publishing:" in src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
