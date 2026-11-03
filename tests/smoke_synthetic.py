# The camera fallback must not break inference.
#
# A USB camera that re-enumerates (replug, hub reset, power blip) makes one
# open fail. The app then falls back to a synthetic source to stay alive --
# and that fallback used to hand the detector a PIL Image, which has no
# .shape, so every inference raised, the failure ladder demoted a healthy
# Hailo to the `none` detector, and detection stayed dead after the camera
# came back. The stand-in has to look like a camera frame.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import camera as camera_mod                        # noqa: E402

cam = camera_mod.Camera({"source": "synthetic", "width": 320, "height": 240})
frame = cam._synthetic_frame()
assert frame is not None, "the synthetic source produced nothing at all"

if camera_mod.cv2 is not None:
    assert hasattr(frame, "shape"), \
        f"synthetic frame is {type(frame).__name__}, which inference cannot read"
    assert frame.shape == (240, 320, 3), frame.shape
    assert str(frame.dtype) == "uint8", frame.dtype
    print("== synthetic frame is a real BGR array:", frame.shape, frame.dtype)
else:                                                       # pragma: no cover
    print("== no cv2 on this box; synthetic falls back to PIL by design")

# It still encodes to JPEG, which is what the live stream serves.
jpeg = camera_mod.encode_jpeg(frame, 70)
assert jpeg and jpeg[:2] == b"\xff\xd8", "synthetic frame does not encode to JPEG"
print("== and still encodes to JPEG for the stream:", len(jpeg), "bytes")

# It moves, so the preview is visibly live rather than a frozen still.
first = cam._synthetic_frame()
for _ in range(5):
    later = cam._synthetic_frame()
if camera_mod.cv2 is not None:
    assert (first != later).any(), "the synthetic frame never changes"
    print("== and it animates, so a stand-in feed looks alive")

# A frame the detector cannot read must not count against the detector: that
# ladder exists for a failing CHIP, and demoting one over a bad input is what
# left the box reporting nothing.
import inspect                                              # noqa: E402

src = inspect.getsource(__import__("app.run", fromlist=["run"]))
# The guard moved with the call: _infer_batch screens every camera's frame
# BEFORE the one detector call, so a bad input answers [] in its slot and
# never reaches the chip -- the same rule, applied to all frames at once.
assert 'if f is not None and hasattr(f, "shape")]' in src, \
    "_infer_batch no longer guards against unreadable frames"
assert src.index('hasattr(f, "shape")') < src.index("det.detect_many("), \
    "the guard must screen frames before the detector is asked"
print("== an unreadable frame is skipped, not counted as an inference failure")
print("ALL OK")
