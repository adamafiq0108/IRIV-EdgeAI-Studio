"""The arrow on a faced line is the way IN, drawn as a movement.

It used to start ON the line and point off into the room: a label saying
"this side is called in". An operator reading a doorway wants the other
question answered -- which way must somebody walk to be counted -- so the
arrow now travels from outside INTO the line, tip on the line, the way a
person crossing it goes.

Same geometry underneath: the IN side is still the positive side of the drawn
direction (zones.py `_side_of_line`), and reversing the face still swaps the
two without redrawing the line. Only which end of the arrow is the tip
changed.

    python tests/smoke_facearrow.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import camera as cam                                  # noqa: E402
from app import zones as zn                                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# A vertical line drawn top to bottom, in pixels.
LINE = [(500, 100), (500, 900)]
MID = (500, 500)


def side_of(point):
    """Which side zones.py puts a pixel on, in the same normalized space it
    works in (the shape of the answer is all that matters here)."""
    a = (LINE[0][0] / 1000.0, LINE[0][1] / 1000.0)
    b = (LINE[1][0] / 1000.0, LINE[1][1] / 1000.0)
    return zn._side_of_line((point[0] / 1000.0, point[1] / 1000.0), a, b)


for facing in ("forward", "reverse"):
    tail, tip = cam._face_arrow(LINE, facing)
    in_side = zn.Zone({"id": "l", "name": "L", "kind": "line",
                       "points": [[0.5, 0.1], [0.5, 0.9]],
                       "facing": facing}).way_of(side_of(tail))
    check(f"[{facing}] the arrow's TIP is on the line",
          abs(tip[0] - MID[0]) <= 1 and abs(tip[1] - MID[1]) <= 1,
          f"tip at {tip}, line midpoint {MID} -- an arrow whose tip is off in "
          "the room is a label, not a movement")
    check(f"[{facing}] ...and its tail is on the side you come FROM",
          in_side == "out",
          f"the tail sits where way_of says {in_side!r}: an object travelling "
          "along this arrow must be one the line counts as IN")
    check(f"[{facing}] ...so the arrow points at the IN side",
          zn.Zone({"id": "l", "name": "L", "kind": "line",
                   "points": [[0.5, 0.1], [0.5, 0.9]],
                   "facing": facing}).way_of(-side_of(tail)) == "in")

f_tail, _f_tip = cam._face_arrow(LINE, "forward")
r_tail, _r_tip = cam._face_arrow(LINE, "reverse")
check("reversing the face flips the arrow, not the line",
      (f_tail[0] - MID[0]) == -(r_tail[0] - MID[0]),
      f"forward tail {f_tail}, reverse tail {r_tail}")
check("a plain line has no arrow at all",
      cam._face_arrow(LINE, "none") is None,
      "no face, no direction to claim")

# -- the editor draws the same movement ---------------------------------------
canvas = io.open(os.path.join(ROOT, "frontend", "src", "components",
                              "ZoneCanvas.jsx"), encoding="utf-8").read()
check("the editor's arrow runs from outside in too",
      "const sx = mx - nx * L, sy = my - ny * L;" in canvas
      and "<line x1={sx} y1={sy} x2={mx} y2={my}" in canvas,
      "the editor and the live picture must not point different ways")
check("...with the head at the line end",
      "points={`${mx},${my} ${bx + hx},${by + hy}" in canvas)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
