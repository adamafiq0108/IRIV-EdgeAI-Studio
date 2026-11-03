"""A person standing ON the counting line is not crossing it, repeatedly.

Reported as "one object, and it counts four". Measured on the live door before
the fix: track 76 crossed three times in NINE MILLISECONDS, and 15 of the 27
repeat crossings in a day came within 300ms of the one before. Nobody walks
through a doorway three times in a hundredth of a second.

A tracked box does not sit still -- its centre shivers by a few pixels between
frames even when the person is standing still -- and the line was an
infinitely thin boundary, so every shiver across it was another crossing, with
a direction, an event row and a tally.

Two guards, because they answer different faults:

  * LINE_DEADBAND gives the line a thickness. Inside it an object has NO side,
    so it neither counts nor forgets which side it came from; the crossing is
    counted when it leaves the far edge.
  * LINE_CONFIRM_FRAMES makes a flip HOLD before it counts, which is the
    single-frame jump of a detection briefly landing somewhere else -- a
    distance no deadband can be widened to cover without eating real
    crossings.

Neither may cost a real crossing: that is what most of this file checks.

    python tests/smoke_linejitter.py
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
W = H = 100


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def walk(path, facing="forward", tid=1):
    """Walk one tracked object along `path` (x positions, 0..1) across a
    vertical line at x = 0.5. Returns the line's totals."""
    spec = [{"id": "line1", "name": "Door", "kind": "line", "enabled": True,
             "points": [[0.5, 0.0], [0.5, 1.0]], "facing": facing}]
    engine = zn.ZoneEngine(zn.parse_zones(spec))
    last = None
    for x in path:
        px = x * W
        det = [{"label": "person", "confidence": 0.9, "track_id": tid,
                "box": [px - 5, 40, px + 5, 60]}]
        last = engine.update(det, W, H)["crossings"]["line1"]
    return last


# -- 1. the fault, exactly as the camera produced it --------------------------
# Standing on the line, centre shivering by a few pixels either way.
JITTER = [0.2, 0.35, 0.498, 0.502, 0.497, 0.503, 0.499, 0.501, 0.498, 0.502]
r = walk(JITTER)
check("a box shivering ON the line crosses nothing",
      r["total_in"] == 0 and r["total_out"] == 0 and r["total_cross"] == 0,
      f"got in={r['total_in']} out={r['total_out']} -- one person standing "
      f"still, counted {r['total_in'] + r['total_out']} times")

# ...and then they walk through, which must still count exactly once.
r = walk(JITTER + [0.55, 0.62, 0.75, 0.9])
check("...and counts once when they actually walk through",
      r["total_in"] + r["total_out"] == 1,
      f"got in={r['total_in']} out={r['total_out']}")

# -- 2. a single-frame jump of the detection ---------------------------------
r = walk([0.2, 0.25, 0.3, 0.8, 0.3, 0.25, 0.2])
check("a one-frame jump across the line is not a crossing",
      r["total_in"] + r["total_out"] == 0,
      "a box snapping to a reflection or to somebody else for one frame "
      "wrote a crossing and its event row")
r = walk([0.2, 0.25, 0.3, 0.8, 0.85, 0.9, 0.95])
check("...but a jump that HOLDS is one - it is how a fast walker looks",
      r["total_in"] + r["total_out"] == 1,
      f"got in={r['total_in']} out={r['total_out']} -- at 18fps a running "
      "person can clear the line between two frames")

# -- 3. real crossings, both ways, several times ------------------------------
r = walk([0.2, 0.3, 0.7, 0.8, 0.75, 0.3, 0.2, 0.25, 0.7, 0.8])
check("three real passes count three times, with their directions",
      r["total_in"] + r["total_out"] == 3
      and r["total_in"] in (1, 2) and r["total_out"] in (1, 2),
      f"got in={r['total_in']} out={r['total_out']}")

# -- 4. approaching the line and turning back --------------------------------
r = walk([0.1, 0.3, 0.45, 0.49, 0.45, 0.3, 0.1])
check("walking up to the line and turning back counts nothing",
      r["total_in"] + r["total_out"] == 0,
      "the deadband must not latch a side the object never reached")

# -- 5. the band is a band, not a gate ---------------------------------------
check("a point inside the band has no side",
      zn._side_of_line((0.5 + zn.LINE_DEADBAND / 2, 0.5),
                       (0.5, 0.0), (0.5, 1.0), zn.LINE_DEADBAND) == 0)
check("...and one outside it does",
      zn._side_of_line((0.5 + zn.LINE_DEADBAND * 2, 0.5),
                       (0.5, 0.0), (0.5, 1.0), zn.LINE_DEADBAND) != 0)
check("...measured perpendicular, so a diagonal line reads the same",
      zn._side_of_line((0.5, 0.5), (0.0, 0.0), (1.0, 1.0),
                       zn.LINE_DEADBAND) == 0
      and zn._side_of_line((0.9, 0.1), (0.0, 0.0), (1.0, 1.0),
                           zn.LINE_DEADBAND) != 0,
      "dividing by the line's length is what makes the band a distance")
check("no band asked for, no band applied",
      zn._side_of_line((0.5001, 0.5), (0.5, 0.0), (0.5, 1.0)) != 0,
      "the geometry helper is used elsewhere and must not change meaning")

# -- 6. the guards are small enough to be honest ------------------------------
check("the band is roughly a person's shiver, not a person's stride",
      0.004 <= zn.LINE_DEADBAND <= 0.03,
      f"{zn.LINE_DEADBAND} -- too wide and a real crossing near the line is "
      "swallowed; too narrow and the shiver comes back")
check("and a crossing is confirmed in frames, not seconds",
      zn.LINE_CONFIRM_FRAMES == 2,
      "more than two costs a crossing whose walker leaves the frame straight "
      "after it")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
