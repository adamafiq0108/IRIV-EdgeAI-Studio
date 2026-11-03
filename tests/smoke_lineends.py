"""A counting line counts what crosses IT, not what crosses its extension.

Reported from the developer box, system "Detection": a short line drawn over
one doorway (x = 0.72, from y = 0.25 to y = 0.51) counted people walking past
its x anywhere in the frame -- "it counts all, even from any side". The side
test is a fact about the INFINITE line through the two points: a person three
metres past either end flips sides exactly like one walking through the door.

Now the step from where the track last stood to where it is has to cut the
drawn segment (with a little slack past each end, LINE_END_SLACK) or it is not
a crossing. The new side is still remembered, so walking round the end and
back round it again is not a crossing either way.

    python tests/smoke_lineends.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import zones as zn                                    # noqa: E402

bad = 0
W = H = 1000


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def walk(path, line=((0.72, 0.25), (0.72, 0.51)), facing="forward", tid=1):
    """Walk one tracked person's FEET along `path` ((x, y) normalised) past a
    line. Returns the line's totals {in, out, cross}."""
    spec = [{"id": "l", "name": "Door", "kind": "line", "enabled": True,
             "points": [list(line[0]), list(line[1])], "facing": facing}]
    engine = zn.ZoneEngine(zn.parse_zones(spec), anchor="bottom_center")
    for x, y in path:
        px, py = x * W, y * H
        det = [{"label": "person", "confidence": 0.9, "track_id": tid,
                "box": [px - 20, py - 80, px + 20, py]}]
        engine.update(det, W, H)
    t = engine.totals["l"]
    return {"in": t["in"], "out": t["out"], "cross": t["cross"]}


def across(y, n=8):
    """A walk from x=0.6 to x=0.84 at height y, in n steps -- across the
    line's x, at whatever height."""
    return [(0.6 + (0.24 * i / (n - 1)), y) for i in range(n)]


print("== through the doorway")
t = walk(across(0.40))
check("a walk through the drawn line counts once", t["in"] + t["out"] == 1, str(t))
fwd = walk(across(0.40))
back = walk(across(0.40) + list(reversed(across(0.40))))
check("...and back through it counts the other way",
      back["in"] == 1 and back["out"] == 1, str(back))
# Which word the return leg gets, so the tests below can name it.
ret = "in" if fwd["out"] else "out"

print("== past the ends")
t = walk(across(0.80))
check("a walk past the BOTTOM end, well below the line, counts nothing",
      t["in"] + t["out"] + t["cross"] == 0, str(t))
t = walk(across(0.05))
check("a walk past the TOP end counts nothing", t["in"] + t["out"] + t["cross"] == 0, str(t))
t = walk(across(0.80) + list(reversed(across(0.80))))
check("...round the end and back round it: still nothing",
      t["in"] + t["out"] + t["cross"] == 0, str(t))
t = walk(across(0.80) + list(reversed(across(0.40))))
check("round the end, then back THROUGH the door: one crossing, the way it went",
      t[ret] == 1 and t["in"] + t["out"] == 1, str(t))

print("== the slack at the ends")
t = walk(across(0.525))
check("brushing the end, within 10% of the length, still counts",
      t["in"] + t["out"] == 1, str(t))
t = walk(across(0.60))
check("...a third of the length past the end does not",
      t["in"] + t["out"] + t["cross"] == 0, str(t))

print("== other shapes still work")
t = walk(across(0.5), line=((0.72, 0.0), (0.72, 1.0)))
check("a full-height line is unchanged", t["in"] + t["out"] == 1, str(t))
diag = [(0.3 + 0.4 * i / 7, 0.7 - 0.4 * i / 7) for i in range(8)]
t = walk(diag, line=((0.4, 0.4), (0.6, 0.6)))
check("a diagonal line, crossed through its middle, counts",
      t["in"] + t["out"] == 1, str(t))
# y = 1.4 - x meets y = x at (0.7, 0.7): half the line's length past its end.
t = walk([(0.3 + 0.4 * i / 7, 1.1 - 0.4 * i / 7) for i in range(8)],
         line=((0.4, 0.4), (0.6, 0.6)))
check("...and crossed past its end does not", t["in"] + t["out"] + t["cross"] == 0, str(t))
t = walk(across(0.40), facing="none")
check("a faceless line counts a cross through it",
      t["cross"] == 1 and t["in"] + t["out"] == 0, str(t))
t = walk(across(0.80), facing="none")
check("...and nothing past its end", t["cross"] == 0, str(t))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
