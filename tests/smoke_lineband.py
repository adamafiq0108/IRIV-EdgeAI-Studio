"""A line's dead band is the operator's to set, and it is the same width
whichever way the line is drawn.

Reported from the live door (wf_5126, Line 2): one person walking through
yielded an IN and an OUT -- the tracked box shivers, and on that camera the
shiver is wider than the fixed 0.012 band, so the anchor point cleared the far
edge, held two frames, and came back. The band was a module constant: nothing
on the Zones page, nothing on the counter, nothing in the zone document could
widen it for one bad camera without widening it for every good one.

A second fault sat underneath: the band was measured in NORMALISED units, and
a picture is not square. The same 0.012 was 23 px on an upright line and 13 px
on a flat one at 1920x1080 -- half the thickness the constant was tuned for,
so a horizontal counting line shivered twice as easily as the header comment
promised. The band is now a percentage of the picture WIDTH whatever the
line's angle.

What this file pins, in order of what it would cost to lose:

  * at the default, every decision is byte-for-byte what LINE_DEADBAND made
    yesterday -- a seeded set of walks, and the linejitter paths with and
    without the field;
  * the operator's symptom goes away at 3% and no real crossing is lost;
  * a flat line and an upright line read the same PIXELS on a 16:9 frame;
  * the field survives the hub (validator, list view, round-trip), and the
    list view now also carries facing and depth -- which a Zones page
    load-then-save used to wipe.

    python tests/smoke_lineband.py
"""
import io
import math
import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import zones as zn                                    # noqa: E402
from core.services import detector_control as dc               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def walk(path, facing="forward", tid=1, band_pct=None, W=100, H=100,
         line=((0.5, 0.0), (0.5, 1.0)), anchor="bottom_center"):
    """Walk one tracked object along `path` -- x positions, or (x, y) pairs,
    normalised -- and return the line's totals. The box is 10 px wide and
    20 px tall with its feet at the path point, so the bottom_center anchor
    IS the point."""
    spec = {"id": "line1", "name": "Door", "kind": "line", "enabled": True,
            "points": [list(line[0]), list(line[1])], "facing": facing}
    if band_pct is not None:
        spec["band_pct"] = band_pct
    engine = zn.ZoneEngine(zn.parse_zones([spec]), anchor=anchor)
    last = None
    for p in path:
        x, y = (p, 0.6) if not isinstance(p, (tuple, list)) else p
        px, py = x * W, y * H
        det = [{"label": "person", "confidence": 0.9, "track_id": tid,
                "box": [px - 5, py - 20, px + 5, py]}]
        last = engine.update(det, W, H)["crossings"]["line1"]
    return last


def crossings(r):
    return r["total_in"] + r["total_out"] + r["total_cross"]


# -- (a) a line without the field is exactly the line it was ------------------
z = zn.Zone({"id": "l", "kind": "line", "points": [[0.5, 0.0], [0.5, 1.0]]})
check("no band_pct -> no override, and band() is the module constant",
      z.band_pct is None and z.band() == zn.LINE_DEADBAND,
      f"band_pct={z.band_pct!r} band()={z.band()!r}")
check("as_dict() of an untouched line is key-for-key what it was",
      set(z.as_dict()) == {"id", "name", "camera", "kind", "points", "violation",
                           "labels", "color", "enabled", "match", "facing"},
      f"{sorted(z.as_dict())} -- a new key here reaches the overlay, the "
      "Zones canvas and every consumer of the zone dict")

# -- (a2) BYTE-FOR-BYTE: sixty seeded walks decided as they were yesterday ----
# The expected totals were produced by the code BEFORE band_pct existed, so a
# change in the engine's default decision shows up here as a different tally.
# Upright lines on a 16:9 frame are included on purpose: with the y axis
# scaled, a vertical line's perpendicular distance is unchanged, and this
# proves it. Flat and oblique lines are only walked on square frames, where
# the scaling is the identity -- on a non-square frame their band is MEANT to
# change (see (d) below).
EXPECTED = [[0, 0, 1], [0, 0, 1], [1, 0, 0], [1, 0, 0], [1, 0, 0], [0, 0, 1],
            [0, 0, 1], [1, 0, 0], [1, 0, 0], [1, 0, 0], [0, 0, 1], [0, 0, 1],
            [1, 0, 0], [0, 0, 1], [0, 1, 0], [0, 1, 0], [1, 0, 0], [0, 1, 0],
            [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1], [0, 0, 1],
            [1, 0, 0], [0, 1, 0], [0, 1, 0], [0, 1, 0], [1, 0, 0], [0, 1, 0],
            [0, 1, 0], [1, 0, 0], [1, 0, 0], [0, 0, 1], [1, 0, 0], [0, 1, 0],
            [0, 1, 0], [0, 0, 1], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 1, 0],
            [1, 0, 0], [0, 0, 1], [1, 1, 0], [0, 0, 1], [0, 1, 0], [1, 0, 0],
            [0, 0, 1], [0, 0, 1], [0, 0, 1], [1, 0, 0], [0, 1, 0], [1, 0, 0],
            [0, 0, 1], [0, 1, 0], [0, 1, 0], [0, 1, 0], [1, 0, 0], [1, 0, 0]]


def seeded_cases():
    rng = random.Random(20260924)
    for _ in range(60):
        W, H = rng.choice([(100, 100), (1000, 1000), (1920, 1080)])
        facing = rng.choice(["forward", "reverse", "none"])
        if W != H:
            x = round(rng.uniform(0.2, 0.8), 4)
            y0, y1 = sorted((round(rng.uniform(0.0, 0.4), 4),
                             round(rng.uniform(0.6, 1.0), 4)))
            line = ((x, y0), (x, y1))
        else:
            line = ((round(rng.uniform(0.1, 0.9), 4), round(rng.uniform(0.1, 0.9), 4)),
                    (round(rng.uniform(0.1, 0.9), 4), round(rng.uniform(0.1, 0.9), 4)))
        mx, my = (line[0][0] + line[1][0]) / 2, (line[0][1] + line[1][1]) / 2
        ldx, ldy = line[1][0] - line[0][0], line[1][1] - line[0][1]
        ll = (ldx ** 2 + ldy ** 2) ** 0.5 or 1
        nx, ny = -ldy / ll, ldx / ll
        off = rng.uniform(0.15, 0.35) * rng.choice([-1, 1])
        x, y = mx + nx * off, my + ny * off
        path = []
        for k in range(16):
            drift = -off / 6 if k < 9 else rng.uniform(-0.04, 0.04)
            x = min(0.98, max(0.02, x + nx * drift + rng.uniform(-0.03, 0.03)))
            y = min(0.98, max(0.05, y + ny * drift + rng.uniform(-0.03, 0.03)))
            path.append((round(x, 4), round(y, 4)))
        yield W, H, line, facing, path


got = []
for W, H, line, facing, path in seeded_cases():
    r = walk(path, facing=facing, W=W, H=H, line=line)
    got.append([r["total_in"], r["total_out"], r["total_cross"]])
diffs = [i for i, (a, b) in enumerate(zip(got, EXPECTED)) if a != b]
check("sixty seeded walks are decided exactly as before the field existed",
      got == EXPECTED,
      f"cases {diffs} differ: {[(got[i], EXPECTED[i]) for i in diffs]} -- the "
      "default band changed for a line nobody touched")

# -- (b) the linejitter paths, with and without the field ---------------------
JITTER = [0.2, 0.35, 0.498, 0.502, 0.497, 0.503, 0.499, 0.501, 0.498, 0.502]
PATHS = {
    "shiver on the line": JITTER,
    "shiver then walk through": JITTER + [0.55, 0.62, 0.75, 0.9],
    "one-frame jump": [0.2, 0.25, 0.3, 0.8, 0.3, 0.25, 0.2],
    "held jump": [0.2, 0.25, 0.3, 0.8, 0.85, 0.9, 0.95],
    "three passes": [0.2, 0.3, 0.7, 0.8, 0.75, 0.3, 0.2, 0.25, 0.7, 0.8],
    "approach and turn back": [0.1, 0.3, 0.45, 0.49, 0.45, 0.3, 0.1],
}
for name, path in PATHS.items():
    a = walk(path)
    b = walk(path, band_pct=1.2)
    check(f"'{name}' reads the same with band_pct absent and with 1.2",
          (a["total_in"], a["total_out"]) == (b["total_in"], b["total_out"]),
          f"absent in/out={a['total_in']}/{a['total_out']} vs "
          f"1.2 -> {b['total_in']}/{b['total_out']}")

# -- (c) THE SYMPTOM: a shiver wider than the factory band -----------------
# Each 0.02 excursion clears the 0.012 band and holds two frames: at the
# default this is what the door camera did -- one person, in AND out.
SHIVER = [0.3, 0.48, 0.48, 0.52, 0.52, 0.48, 0.48, 0.52, 0.52]
r = walk(SHIVER)
check("a 2%-wide shiver IS counted at the factory band (the reported fault)",
      r["total_in"] + r["total_out"] >= 3,
      f"got in={r['total_in']} out={r['total_out']} -- if this reads 0 the "
      "default band widened and the seeded pin above should have caught it")
r = walk(SHIVER, band_pct=3.0)
check("...and counts nothing at a 3% threshold",
      crossings(r) == 0,
      f"got in={r['total_in']} out={r['total_out']} -- the operator's knob "
      "did not reach the decision")
# The line is drawn top-to-bottom, so its IN side is the LEFT of the frame
# (smoke_lineface): walking left-to-right is one OUT, and nothing else.
r = walk([0.3, 0.48, 0.52, 0.60, 0.60], band_pct=3.0)
check("a real crossing is never lost by a 3% band",
      r["total_out"] == 1 and r["total_in"] == 0,
      f"got in={r['total_in']} out={r['total_out']}")
r = walk([0.1, 0.2, 0.9, 0.9], band_pct=5.0)
check("even the widest band counts a walk from one side to the other",
      crossings(r) == 1, f"got {r}")

# -- (d) ISOTROPY: the same PIXELS give the same answer on a 16:9 frame -------
W16, H16 = 1920, 1080
HORIZ = ((0.1, 0.5), (0.9, 0.5))
VERT = ((0.5, 0.1), (0.5, 0.9))


def approach(line, px_past, W, H):
    """Come from far away, then stand `px_past` PIXELS past the line for
    three frames. Counted only if that stand is outside the band."""
    if line[0][1] == line[1][1]:                                # horizontal
        y = line[0][1]
        path = [(0.5, y - 0.3), (0.5, y - 0.2)] + [(0.5, y + px_past / H)] * 3
    else:                                                       # vertical
        x = line[0][0]
        path = [(x - 0.3, 0.5), (x - 0.2, 0.5)] + [(x + px_past / W, 0.5)] * 3
    return crossings(walk(path, W=W, H=H, line=line))


check("18 px past a HORIZONTAL line on 1920x1080 is still inside the band",
      approach(HORIZ, 18, W16, H16) == 0,
      "the band on a flat line was 13 px -- half the 23 px the header comment "
      "promised, so a flat line shivered twice as easily as an upright one")
check("...and 30 px past it is a side",
      approach(HORIZ, 30, W16, H16) == 1)
check("18 px past a VERTICAL line on 1920x1080 is inside the band",
      approach(VERT, 18, W16, H16) == 0)
check("...and 30 px past it is a side -- same pixels, same answer",
      approach(VERT, 30, W16, H16) == 1)
# The helper itself, so the arithmetic is pinned independently of the walk.
ky = H16 / W16
check("_side_of_line with ky measures the distance in units of frame width",
      zn._side_of_line((0.5, 0.5 + 18 / H16), HORIZ[0], HORIZ[1],
                       zn.LINE_DEADBAND, ky) == 0
      and zn._side_of_line((0.5, 0.5 + 30 / H16), HORIZ[0], HORIZ[1],
                           zn.LINE_DEADBAND, ky) != 0)
# way_of and the face arrow read the SIGN, so scaling one axis must not flip
# it for any point on any line.
rng = random.Random(7)
flips = 0
for _ in range(200):
    p = (rng.random(), rng.random())
    a = (rng.random(), rng.random())
    b = (rng.random(), rng.random())
    s0 = zn._side_of_line(p, a, b)
    s1 = zn._side_of_line(p, a, b, 0.0, ky)
    if s0 != s1 and (s0 == 0) == (s1 == 0):
        flips += 1
check("scaling the y axis never flips which side a point is on",
      flips == 0, f"{flips} of 200 random points changed sign -- in/out and "
      "the face arrow would disagree")

# -- (e) the helper's defaults still mean 'no band, no scaling' ---------------
check("no band asked for, no band applied (smoke_facearrow's contract)",
      zn._side_of_line((0.5001, 0.5), (0.5, 0.0), (0.5, 1.0)) != 0)

# -- (f) the segment gate is untouched by the axis scaling --------------------
# smoke_lineends' short line, on a 16:9 frame: brushing within 10% of the
# line's length counts, a third past its end does not. `t` along the segment
# is invariant under per-axis scaling, so these read as they do on a square.
SHORT = ((0.72, 0.25), (0.72, 0.51))


def across(y, W, H):
    return crossings(walk([(0.6 + i * 0.03, y) for i in range(9)],
                          W=W, H=H, line=SHORT))


check("brushing the end of a short line within 10% still counts on 16:9",
      across(0.53, W16, H16) == 1)
check("...and a third of its length past the end does not",
      across(0.60, W16, H16) == 0,
      "LINE_END_SLACK is a fraction of the drawn length and must stay so")

# -- (g) the field is clamped, never dropped, on the app side -----------------
for raw, want in ((0.05, 0.2), (99, 5.0), (2.5, 2.5), ("2.5", 2.5),
                  ("abc", None), ("", None), (0, None), (-1, None), (None, None)):
    z = zn.Zone({"id": "l", "kind": "line", "points": [[0.5, 0.0], [0.5, 1.0]],
                 "band_pct": raw})
    check(f"band_pct {raw!r} -> {want!r}", z.band_pct == want,
          f"got {z.band_pct!r}")
z = zn.Zone({"id": "l", "kind": "line", "points": [[0.5, 0.0], [0.5, 1.0]],
             "band_pct": 2.55})
check("band_pct is kept to one decimal", z.band_pct in (2.5, 2.6), z.band_pct)
check("band() is the percentage as a fraction of the picture width",
      abs(zn.Zone({"id": "l", "kind": "line", "points": [[0.5, 0.0], [0.5, 1.0]],
                   "band_pct": 2.5}).band() - 0.025) < 1e-12)
check("as_dict() carries band_pct only when it is set",
      "band_pct" in z.as_dict() and z.as_dict()["band_pct"] == z.band_pct)

# -- (h) a real step back through the line is still an in AND an out ----------
# Pinned as INTENDED: the band is one of three guards and there is no cooldown
# after a counted crossing. Somebody adding one by accident to "fix" the
# shiver would silently lose every person who turns round in a doorway.
r = walk([0.2, 0.3, 0.7, 0.8, 0.8, 0.8, 0.3, 0.2], band_pct=2.0)
check("through, wait, and back through is one in and one out",
      r["total_in"] == 1 and r["total_out"] == 1,
      f"got in={r['total_in']} out={r['total_out']}")

# -- (i) source pins ----------------------------------------------------------
src = read("systems", "demo", "app", "zones.py")
check("the engine reads the band off the zone, not the module",
      "zone.band()" in src
      and "_side_of_line(pt, a, b, LINE_DEADBAND)" not in src)
check("LINE_DEADBAND is still the constant the rest of the suite pins",
      zn.LINE_DEADBAND == 0.012)
check("the clamp range is 0.2 .. 5.0 percent",
      zn.LINE_BAND_PCT_MIN == 0.2 and zn.LINE_BAND_PCT_MAX == 5.0)

# =============================================================================
# the hub half: the field survives a save, and so do its neighbours
# =============================================================================
check("the hub mirrors the app's default and range",
      dc.LINE_BAND_DEFAULT_PCT == round(zn.LINE_DEADBAND * 100, 3)
      and dc.LINE_BAND_MIN_PCT == zn.LINE_BAND_PCT_MIN
      and dc.LINE_BAND_MAX_PCT == zn.LINE_BAND_PCT_MAX,
      "detector_control and zones.py disagree about what 'default' means")

LINE = {"id": "door", "name": "Door", "kind": "line",
        "points": [[0.5, 0.0], [0.5, 1.0]]}
for raw, want in ((1.2, None), (1.24, None), (2.5, 2.5), (50, 5.0), (0.05, 0.2),
                  (0, None), (-1, None), ("abc", None), ("", None), (None, None)):
    cleaned = dc._validate_zone({**LINE, "band_pct": raw})
    check(f"_validate_zone band_pct {raw!r} -> {want!r}",
          cleaned.get("band_pct") == want, f"got {cleaned.get('band_pct')!r}")
area = dc._validate_zone({"id": "a", "name": "A", "kind": "area", "band_pct": 2.5,
                          "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]})
check("an area never carries a band", "band_pct" not in area)
check("the factory value is never written, so old configs round-trip byte-identical",
      dc._validate_zone(LINE) == dc._validate_zone({**LINE, "band_pct": 1.2}))

SYSTEMS = tempfile.mkdtemp()
SLUG = "linebandtest"
os.makedirs(os.path.join(SYSTEMS, SLUG), exist_ok=True)
saved = dc.set_zones(SYSTEMS, SLUG, [{**LINE, "band_pct": 2.5}])[0]
check("set_zones round-trips 2.5 and the app reads 0.025",
      saved.get("band_pct") == 2.5 and abs(zn.Zone(saved).band() - 0.025) < 1e-12,
      saved)
saved = dc.set_zones(SYSTEMS, SLUG, [{**LINE, "band_pct": 1.2}])[0]
check("...and 1.2 comes back as the default band",
      "band_pct" not in saved and zn.Zone(saved).band() == zn.LINE_DEADBAND, saved)

# -- THE WIPE BUG: the list view the Zones page loads and posts back ----------
# _zone_list is what status() returns, what the Zones page seeds from, and
# what it POSTs back whole on the next save. A key missing from it is a key
# the next save deletes: a line saved as "none" came back "forward", and a
# depth range vanished, the first time anybody edited an unrelated zone.
dc.set_zones(SYSTEMS, SLUG, [{**LINE, "facing": "none", "depth_min_mm": 500,
                              "depth_max_mm": 3000, "band_pct": 2.5}])
listed = dc._zone_list(dc._merged(dc._pkg_dir(SYSTEMS, SLUG)))[0]
check("the list view carries facing, depth and band_pct",
      listed.get("facing") == "none" and listed.get("depth_min_mm") == 500
      and listed.get("depth_max_mm") == 3000 and listed.get("band_pct") == 2.5,
      f"{sorted(listed)} -- a Zones page load-then-save wipes whatever is missing")
again = dc._validate_zone(listed)
check("...and a save of that view keeps all four",
      again.get("facing") == "none" and again.get("depth_min_mm") == 500
      and again.get("depth_max_mm") == 3000 and again.get("band_pct") == 2.5,
      again)
dc.set_zones(SYSTEMS, SLUG, [LINE])
plain = dc._zone_list(dc._merged(dc._pkg_dir(SYSTEMS, SLUG)))[0]
check("a line saved without them lists exactly the keys it always did",
      sorted(plain) == ["actions", "camera", "color", "enabled", "id", "kind",
                        "labels", "match", "name", "points", "violation"],
      f"{sorted(plain)} -- compile_store, functions.variables and the "
      "template export all read this shape")

# -- the pages ----------------------------------------------------------------
zones_jsx = read("frontend", "src", "pages", "Zones.jsx")
check("the Zones page offers the threshold on a line",
      "band_pct" in zones_jsx and "Crossing threshold" in zones_jsx)
check("...with the same range the validator clamps to",
      "min={0.2}" in zones_jsx and "max={5}" in zones_jsx,
      "the page has no registry feed for zone fields, so the literals are "
      "mirrored by hand and pinned here")
check("the Line Counter inspector points at it",
      "Crossing threshold" in read("frontend", "src", "pages", "Workflow.jsx"))
check("the canvas draws the slab so the operator can see what 3% means",
      "band_pct" in read("frontend", "src", "components", "ZoneCanvas.jsx"))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
