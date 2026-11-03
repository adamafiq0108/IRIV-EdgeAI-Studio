"""The zone overlay is built once, not once per frame.

py-spy on a live 3-camera box put 26% of the whole detection loop inside
_poly_grad: a linspace, a zeroed mask, a fillPoly, a full-size tint array and
two float32 conversions of the region, rebuilt for every zone on every frame.
The accelerator was idle waiting for the drawing to finish -- which is what
"the fps is divided between the cameras" actually was.

A zone does not move between frames and neither does its colour, so
everything about the overlay except the pixels underneath is the same picture
every time. Built once and blended in OpenCV rather than numpy: 30ms -> 3ms
per zone per frame on the same shape.

    python tests/smoke_overlaycost.py
"""
import os
import sys
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

import numpy as np                                             # noqa: E402

# OpenCV reaches a board as the apt package python3-opencv, not as a wheel in
# requirements.txt (scripts/install.sh), so a CI runner that installs only the
# pip requirements genuinely has no cv2. That is not a failure to report as
# one: it is the store-signing-key situation over again, a precondition this
# machine does not have. Exit 77 and the runner records a skip by name.
try:
    import cv2                                                 # noqa: E402,F401
except ImportError:
    print("SKIP: needs opencv (python3-opencv on a board); not a pip "
          "requirement, so a CI runner has none")
    sys.exit(77)

from app import camera as cam                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


FRAME = np.full((1080, 1920, 3), 40, dtype=np.uint8)
PTS = [(600, 150), (1300, 170), (1290, 1040), (560, 980)]
COLOR = (238, 211, 34)


def rebuilt(frame):
    """What it used to do: everything, every frame."""
    arr = np.array(PTS, dtype=np.int32)
    x1, y1 = int(arr[:, 0].min()), int(arr[:, 1].min())
    x2, y2 = int(arr[:, 0].max()), int(arr[:, 1].max())
    mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
    cv2.fillPoly(mask, [arr - np.array([x1, y1], dtype=np.int32)], 1)
    cam._grad_fill(frame, x1, y1, x2, y2, COLOR, mask=mask)


# -- 1. it draws the same zone ------------------------------------------------
cam._GRAD_CACHE.clear()
a, b = FRAME.copy(), FRAME.copy()
cam._poly_grad(a, PTS, COLOR)
rebuilt(b)
worst = int(np.abs(a.astype(int) - b.astype(int)).max())
check("the cached overlay is the same picture",
      worst <= 1,
      f"worst pixel differs by {worst} -- the fade, the shape and the colour "
      f"must be what they were; 1 is OpenCV's rounding against numpy's")
check("...and it is actually drawn, not skipped",
      not np.array_equal(a, FRAME),
      "a fill that no longer fills would also be 'the same as before' to a "
      "test that only compared two broken things")

# -- 2. drawn again, it does not rebuild --------------------------------------
before = len(cam._GRAD_CACHE)
c = FRAME.copy()
cam._poly_grad(c, PTS, COLOR)
check("the second frame reuses the first frame's overlay",
      len(cam._GRAD_CACHE) == before == 1,
      f"cache holds {len(cam._GRAD_CACHE)} entries after two identical draws")
check("...and draws the same thing from the cache",
      np.array_equal(a, c))

# -- 3. a moved, recoloured or resized zone is a different overlay ------------
cam._poly_grad(FRAME.copy(), [(p[0] + 20, p[1]) for p in PTS], COLOR)
cam._poly_grad(FRAME.copy(), PTS, (0, 0, 255))
small = np.full((480, 640, 3), 40, dtype=np.uint8)
cam._poly_grad(small, [(100, 50), (500, 60), (490, 400), (90, 380)], COLOR)
check("a zone that moved, changed colour or changed frame size rebuilds",
      len(cam._GRAD_CACHE) == 4,
      f"{len(cam._GRAD_CACHE)} entries -- an overlay keyed too loosely draws "
      "yesterday's zone")

# -- 4. the cache is bounded --------------------------------------------------
for i in range(cam._GRAD_CACHE_MAX + 5):
    cam._poly_grad(FRAME.copy(), [(10 + i, 10), (200 + i, 12), (198 + i, 300),
                                  (8 + i, 295)], COLOR)
check("it cannot grow for ever",
      len(cam._GRAD_CACHE) <= cam._GRAD_CACHE_MAX,
      f"{len(cam._GRAD_CACHE)} entries -- a zone being dragged mints a new key "
      "per frame, and the app runs for months")

# -- 5. and it is faster, which is the whole point ---------------------------
cam._GRAD_CACHE.clear()


def timed(fn, n=25):
    fn()
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n * 1000.0


now = timed(lambda: cam._poly_grad(FRAME.copy(), PTS, COLOR))
was = timed(lambda: rebuilt(FRAME.copy()))
print(f"     ({now:.1f} ms per zone per frame, was {was:.1f} ms)")
check("...measurably so",
      now < was / 2,
      f"{now:.1f} ms against {was:.1f} ms -- this test exists because the "
      "drawing, not the accelerator, was setting the frame rate")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
