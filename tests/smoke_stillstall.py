# A still image used as the camera must not read as a stalled camera.
#
# A picture file is decoded once and held (camera.py _grab_loop: `still`), so
# no new frame ever arrives. The "how old is the frame" clock that viewers use
# (Camera.stalled_s) was only reset by a new grab, so it ran up from the first
# frame: after 5 seconds every viewer was shown "Camera stalled -
# reconnecting..." over a good picture, and after 25 seconds the stream ended.
# A held still is delivering by definition; it must keep its clock fresh.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import camera as camera_mod                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


cv2 = camera_mod.cv2
if cv2 is None:                                             # pragma: no cover
    print("no cv2 on this box: nothing to hold a still with")
    sys.exit(0)

import numpy as np                                          # noqa: E402

path = os.path.join(tempfile.mkdtemp(), "still.png")
img = np.zeros((120, 160, 3), dtype="uint8")
img[:, :80] = (40, 120, 200)
cv2.imwrite(path, img)

cam = camera_mod.Camera({"source": f"file:{path}", "width": 160, "height": 120,
                         "fps": 10})
try:
    cam.open()
    deadline = time.time() + 5
    while cam._latest is None and time.time() < deadline:
        time.sleep(0.05)
    check("the still image is decoded and held", cam._latest is not None, str(cam.error))
    # The publish loop (run.py) resends a held still's one frame; it asks this.
    check("...and the camera says it is a held still", cam.held_still is True, str(cam.held_still))

    # Far longer than any one grab, far shorter than the viewers' 5 s notice:
    # before the fix this grew to ~3 s over the same wait.
    time.sleep(3.0)
    age = cam.stalled_s
    check("a held still is not ageing toward a stall notice", age < 1.0, f"{age:.1f}s")
    check("...and reports no read error", cam.error is None, str(cam.error))
finally:
    cam.close()

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
