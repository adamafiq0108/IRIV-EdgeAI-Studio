# Measuring objects with the depth camera: length, width, height, distance,
# angle -- checked against a scene whose true sizes are KNOWN, because the
# arithmetic has to be right before a real box goes under the camera.
#
# The scene is rendered the way the camera would see it: a floor plane at a
# known distance (tilted, because a mounted camera never is square), and a box
# of known millimetres standing on it, projected through the Gemini 336's own
# intrinsics. If the maths recovers the numbers that built the scene, it will
# recover a real box's.
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

import numpy as np                                          # noqa: E402

from app import measure                                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def close(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol


# The Gemini 336's colour intrinsics, as the SDK reported them on .63.
INTR = {"fx": 689.37, "fy": 689.77, "cx": 645.91, "cy": 359.40}
W, H = 1280, 720


def render(floor_z=2000.0, tilt_deg=4.0, box=None, angle_deg=0.0, noise=0.0,
           disc=None):
    """A depth picture of a tilted floor with a box on it.

    `box` = (length_mm, width_mm, height_mm, centre_x_mm, centre_y_mm), the
    footprint centred where stated on the floor and rotated by `angle_deg`.
    Rendered by ray-casting each pixel: floor depth where the ray meets the
    plane, box-top depth where it lands inside the footprint.
    """
    us, vs = np.meshgrid(np.arange(W), np.arange(H))
    # Ray direction per pixel (x/z, y/z, 1).
    rx = (us - INTR["cx"]) / INTR["fx"]
    ry = (vs - INTR["cy"]) / INTR["fy"]
    # Floor plane tilted about the X axis: normal (0, sin t, cos t) pointing
    # to the camera, through (0, 0, floor_z).
    t = math.radians(tilt_deg)
    n = np.array([0.0, math.sin(t), math.cos(t)])
    d = -n[2] * floor_z
    # Ray p = z * (rx, ry, 1); on the plane when n . p + d = 0.
    z_floor = -d / (n[0] * rx + n[1] * ry + n[2])
    grid = z_floor.copy()
    if box:
        L, Wd, Hh, cxm, cym = box
        # The top plane is Hh closer to the camera along the normal, so its d
        # is d + Hh. Which pixels see the top is decided where the ray meets
        # THAT plane -- not where it meets the floor. The top is closer, so it
        # spans more pixels than the same footprint painted on the floor
        # would; deciding on the floor hit rendered a top 6% too small at 2m
        # and the measurement was blamed for it.
        z_top = -(d + Hh) / (n[0] * rx + n[1] * ry + n[2])
        x = rx * z_top
        y = ry * z_top
        a = math.radians(angle_deg)
        xr = (x - cxm) * math.cos(a) + (y - cym) * math.sin(a)
        yr = -(x - cxm) * math.sin(a) + (y - cym) * math.cos(a)
        inside = (np.abs(xr) <= L / 2) & (np.abs(yr) <= Wd / 2)
        grid = np.where(inside, z_top, grid)
    if disc:
        # (radius_mm, height_mm, centre_x_mm, centre_y_mm): a round thing.
        R, Hh, cxm, cym = disc
        z_top = -(d + Hh) / (n[0] * rx + n[1] * ry + n[2])
        x = rx * z_top
        y = ry * z_top
        inside = np.hypot(x - cxm, y - cym) <= R
        grid = np.where(inside, z_top, grid)
    if noise:
        grid = grid + np.random.default_rng(1).normal(0, noise, grid.shape)
    return np.clip(grid, 1, 60000).astype(np.uint16)


# -- the floor -------------------------------------------------------------------
empty = render()
floor = measure.fit_floor(empty, 1.0, INTR)
check("an empty scene fits a floor plane", floor is not None)
check("...whose distance at the picture centre is the floor's",
      close(floor["centre_mm"], 2000.0, 5.0), floor["centre_mm"])
check("...with the residual of a clean render near zero",
      floor["rms_mm"] < 2.0, floor["rms_mm"])
tilt_found = math.degrees(math.acos(abs(floor["normal"][2])))
check("...and the camera's tilt recovered, so height is not read as slope",
      close(tilt_found, 4.0, 0.3), tilt_found)
check("a scene too empty to fit is refused, not fitted",
      measure.fit_floor(np.zeros((H, W), np.uint16), 1.0, INTR) is None)

# A cable at the edge must not tilt the whole floor.
littered = empty.copy()
littered[:, :40] = 1500
floor2 = measure.fit_floor(littered, 1.0, INTR)
check("clutter at the edge of the empty scene does not tilt the floor",
      close(floor2["centre_mm"], 2000.0, 8.0)
      and close(math.degrees(math.acos(abs(floor2["normal"][2]))), 4.0, 0.5),
      (floor2["centre_mm"], math.degrees(math.acos(abs(floor2["normal"][2])))))

# -- from here on, OpenCV -----------------------------------------------------------
# measure.measure_box imports cv2 lazily; everything above fits planes with
# numpy alone. OpenCV reaches a board as the apt package python3-opencv rather
# than a pip requirement (scripts/install.sh), so a CI runner has none.
#
# The skip is taken HERE, not at the top of the file, so the floor-fitting
# checks still run on the machine that runs this suite automatically. A
# failure already found above is still reported as a failure -- a skip must
# never swallow one.
try:
    import cv2                                                 # noqa: E402,F401
except ImportError:
    if bad:
        print()
        print(f"{bad} check(s) failed")
        sys.exit(1)
    print("SKIP: the floor fitting above ran; the box measurements below need "
          "opencv (python3-opencv on a board), which is not a pip requirement")
    sys.exit(77)

# -- a box, square to the camera ---------------------------------------------------
BOX = (400.0, 250.0, 120.0, 0.0, 0.0)
scene = render(box=BOX)
# A detection box round it, generous, as a detector would draw it.
det = [640 - 120, 360 - 90, 640 + 120, 360 + 90]
m = measure.measure_box(scene, 1.0, INTR, det, floor)
check("a box on the floor is measured", m is not None)
check("...length within 2% (footprint at the top's own depth)",
      close(m["length_mm"], 400.0, 8.0), m["length_mm"])
check("...width within 2%", close(m["width_mm"], 250.0, 5.0), m["width_mm"])
check("...height within 5mm (distance above the floor PLANE, not a number)",
      close(m["height_mm"], 120.0, 5.0), m["height_mm"])
check("...distance is to the top surface the camera sees",
      close(m["distance_mm"], 2000.0 - 120.0, 15.0), m["distance_mm"])
check("...and a box lying left-to-right reads angle 0",
      close(abs(m["angle_deg"]), 0.0, 2.0) or close(abs(m["angle_deg"]), 180.0, 2.0),
      m["angle_deg"])

# -- the same box, turned -- the angle comes out automatically -------------------
turned = render(box=BOX, angle_deg=30.0)
det2 = [640 - 170, 360 - 130, 640 + 170, 360 + 130]
m2 = measure.measure_box(turned, 1.0, INTR, det2, floor)
check("a box turned 30 degrees is still 400x250",
      close(m2["length_mm"], 400.0, 10.0) and close(m2["width_mm"], 250.0, 8.0),
      (m2["length_mm"], m2["width_mm"]))
check("...and the angle is found, no user input",
      close(abs(m2["angle_deg"]), 30.0, 3.0), m2["angle_deg"])
check("...height unchanged by the turn", close(m2["height_mm"], 120.0, 5.0), m2["height_mm"])

# -- a further, smaller box: the depth is what fixes the scale ---------------------
far = render(floor_z=2800.0, box=(200.0, 200.0, 60.0, 300.0, 200.0))
floor_far = measure.fit_floor(render(floor_z=2800.0), 1.0, INTR)
# Where does (300, 200, floor) land in pixels? u = cx + fx*x/z.
zc = 2800.0 - 60.0
uc = INTR["cx"] + INTR["fx"] * 300.0 / zc
vc = INTR["cy"] + INTR["fy"] * 200.0 / zc
det3 = [int(uc - 60), int(vc - 60), int(uc + 60), int(vc + 60)]
m3 = measure.measure_box(far, 1.0, INTR, det3, floor_far)
check("a 200mm box at 2.8m reads 200mm, not fewer pixels",
      close(m3["length_mm"], 200.0, 8.0) and close(m3["width_mm"], 200.0, 8.0),
      (m3["length_mm"], m3["width_mm"]))

# -- refusals ------------------------------------------------------------------
check("a box over bare floor measures nothing",
      measure.measure_box(empty, 1.0, INTR, det, floor) is None)
check("no floor calibration, no measurement",
      measure.measure_box(scene, 1.0, INTR, det, None) is None)
hole = scene.copy()
hole[270:450, 520:760] = measure.UNMEASURED
check("an object the stereo could not see is not invented",
      measure.measure_box(hole, 1.0, INTR, det, floor) is None)

# -- noise like the real sensor's ---------------------------------------------------
noisy = render(box=BOX, noise=4.0)
floor_n = measure.fit_floor(render(noise=4.0), 1.0, INTR)
mn = measure.measure_box(noisy, 1.0, INTR, det, floor_n)
check("with 4mm of depth noise the box is still within 3%",
      mn is not None and close(mn["length_mm"], 400.0, 12.0)
      and close(mn["width_mm"], 250.0, 8.0) and close(mn["height_mm"], 120.0, 8.0),
      None if mn is None else (mn["length_mm"], mn["width_mm"], mn["height_mm"]))

# -- manual points -----------------------------------------------------------------
# The four corners of the square box, tapped in order. Each tap is a pixel
# with a depth under it -- a real 3D point -- so the sides are true lengths.
def px(xm, ym):
    """Where a point on the box TOP lands in the picture.

    The top is a tilted plane, so its depth changes with y: projecting every
    corner at one constant depth put the top-right tap a pixel onto the
    floor, and one tap at 2000mm among three at 1880mm read as a 426mm side.
    """
    t = math.radians(4.0)
    n_y, n_z = math.sin(t), math.cos(t)
    d_top = -n_z * 2000.0 + 120.0
    z = -(d_top + n_y * ym) / n_z
    return (INTR["cx"] + INTR["fx"] * xm / z, INTR["cy"] + INTR["fy"] * ym / z)

corners = [px(-200, -125), px(200, -125), px(200, 125), px(-200, 125)]
# Nudged TWO pixels inward so the tap lands on the top, not on the edge. A
# pixel at 1.88m is 2.7mm, so this costs 11mm a side, inside the tolerance;
# three pixels a side, as this first did, took 16mm off and the maths was
# blamed for the test's own thumb.
corners = [(u + (2 if u < 640 else -2), v + (2 if v < 360 else -2)) for u, v in corners]
mp = measure.measure_points(scene, 1.0, INTR, corners, floor)
check("four tapped corners give the footprint",
      close(mp["length_mm"], 400.0, 14.0) and close(mp["width_mm"], 250.0, 14.0),
      (mp.get("length_mm"), mp.get("width_mm"), mp["segments_mm"]))
check("...and each tap reports its height above the floor",
      all(close(p["height_mm"], 120.0, 5.0) for p in mp["points"]),
      [p["height_mm"] for p in mp["points"]])
two = measure.measure_points(scene, 1.0, INTR, [corners[0], corners[1]])
check("two taps give one length", len(two["segments_mm"]) == 1
      and close(two["segments_mm"][0], 400.0, 14.0), two["segments_mm"])
dark = measure.measure_points(hole, 1.0, INTR, corners)
check("a tap in a hole too wide to look past is reported, not guessed",
      any(p["depth_mm"] is None for p in dark["points"])
      and "length_mm" not in dark)
# A stereo HOLE of a few pixels right under one tap of a side -- the common
# case at a dark edge. The depth is read from a little along the side, the
# length is still between the taps as placed, and the answer says so.
left_edge, right_edge = px(-197, 0), px(197, 0)                # the box top's two long-side edges
pit = scene.copy()
_lu, _lv = int(left_edge[0]), int(left_edge[1])
pit[_lv - 14:_lv + 14, _lu - 12:_lu + 24] = measure.UNMEASURED  # a 36px hole at the left tap
edge_pair = [left_edge, right_edge]
pitted = measure.measure_sides(pit, 1.0, INTR, edge_pair, floor)
check("a small hole under a tap is looked past, along the side, and the side is still measured",
      pitted["segments_mm"][0] is not None and close(pitted["segments_mm"][0], 394.0, 14.0),
      pitted["segments_mm"])
check("...marked approximate, with the reason",
      pitted["approx"] == [True] and pitted["notes"] and "along the side" in pitted["notes"][0],
      (pitted["approx"], pitted["notes"]))
clean_pair = measure.measure_sides(scene, 1.0, INTR, edge_pair, floor)
check("...while a side with depth under both taps is exact and says nothing",
      clean_pair["approx"] == [False] and not clean_pair["notes"])

# -- every side, not just two -------------------------------------------------------
# The outline is what lets the operator tick the sides they care about. A box
# has four; each is a true length; and side i runs from vertex i to i+1 so a
# label on the picture and a number in the list mean the same edge.
ol = m["outline"]
check("the auto scan finds the object's outline", ol is not None and len(ol["vertices"]) == 4,
      None if ol is None else len(ol["vertices"]))
sides = sorted(ol["sides_mm"])
check("...with every side in millimetres: two of 250, two of 400",
      close(sides[0], 250.0, 10.0) and close(sides[1], 250.0, 10.0)
      and close(sides[2], 400.0, 10.0) and close(sides[3], 400.0, 10.0), sides)
check("the outline carries the dense silhouette beside its straight sides",
      len(ol.get("silhouette") or []) >= 4
      and all(0 <= p[0] <= 1280 and 0 <= p[1] <= 720 for p in ol["silhouette"]),
      len(ol.get("silhouette") or []))
check("...and a perimeter that is their sum",
      close(ol["perimeter_mm"], 1300.0, 30.0), ol["perimeter_mm"])
check("...the turned box too, at its own angle",
      len(m2["outline"]["vertices"]) == 4
      and close(sorted(m2["outline"]["sides_mm"])[-1], 400.0, 12.0),
      m2["outline"]["sides_mm"])

# A FUZZY edge, as stereo really draws one: a skirt three pixels wide round
# the box reading anywhere between top and floor (flying pixels), plus a
# few millimetres of noise everywhere. The tightened mask has to give back
# four sides of the right length, not ten sides of a skirt.
import cv2 as _cv2skirt                                          # noqa: E402
_rng = np.random.default_rng(3)
_obj = (scene.astype(np.int64) < empty.astype(np.int64) - 1).astype(np.uint8)
_skirt = _cv2skirt.dilate(_obj, np.ones((7, 7), np.uint8)).astype(bool) & ~_obj.astype(bool)
fuzzy = scene.copy()
_top = int(scene[_obj.astype(bool)].min())
_mix = _rng.uniform(0.0, 1.0, int(_skirt.sum()))
fuzzy[_skirt] = (empty[_skirt].astype(np.float64) * (1 - _mix) + _top * _mix).astype(np.uint16)
fuzzy = (fuzzy.astype(np.int64) + _rng.integers(-4, 5, fuzzy.shape)).clip(1, 60000).astype(np.uint16)
fz = measure.measure_box(fuzzy, 1.0, INTR, det, None, background=empty)
check("a skirt of in-between depth round the object is not counted as object",
      fz is not None and close(fz["length_mm"], 400.0, 16.0) and close(fz["width_mm"], 250.0, 16.0),
      None if fz is None else (fz["length_mm"], fz["width_mm"]))
check("...and the outline keeps the box's four sides, not the skirt's stairs",
      fz is not None and fz["outline"] is not None and len(fz["outline"]["vertices"]) == 4,
      None if fz is None or fz["outline"] is None else fz["outline"]["sides_mm"])

# -- the outline from COLOUR, the millimetres from depth ---------------------------
# The scene remembered in colour: a grey bench. The object: a dark rectangle
# drawn EXACTLY where the box's top projects, at the detector's 640x360. The
# depth picture is the fuzzy one from above -- and the outline still comes
# back with the box's four sides at their true size, because colour drew it.
_cbg = np.full((360, 640, 3), (120, 118, 116), np.uint8)
_cframe = _cbg.copy()
_ys, _xs = np.nonzero(_obj)                                      # the box's top, grid pixels
_cv2skirt.rectangle(_cframe, (int(_xs.min() / 2), int(_ys.min() / 2)),
                    (int(_xs.max() / 2), int(_ys.max() / 2)), (40, 42, 60), -1)
# A SHADOW beside it: darker, same hue. Must not become object.
_cv2skirt.rectangle(_cframe, (int(_xs.max() / 2) + 4, int(_ys.min() / 2)),
                    (int(_xs.max() / 2) + 40, int(_ys.max() / 2)), (84, 83, 81), -1)
cm = measure.colour_mask(_cframe, _cbg)
check("colour subtraction sees the object and not the shadow beside it",
      cm[(int(_ys.mean() / 2), int(_xs.mean() / 2))]
      and not cm[(int(_ys.mean() / 2), int(_xs.max() / 2) + 20)])
col = measure.measure_box(fuzzy, 1.0, INTR, det, None, background=empty,
                          colour=_cframe, colour_bg=_cbg)
check("with the scene in colour, the outline is the colour silhouette: four sides, true size",
      col is not None and col["outline"] is not None and len(col["outline"]["vertices"]) == 4
      and close(col["length_mm"], 400.0, 10.0) and close(col["width_mm"], 250.0, 10.0),
      None if col is None else (col["length_mm"], col["width_mm"],
                                col["outline"] and col["outline"]["sides_mm"]))
check("...height and distance still come from depth",
      col is not None and close(col["height_mm"], 120.0, 8.0) and close(col["distance_mm"], 1880.0, 40.0),
      None if col is None else (col["height_mm"], col["distance_mm"]))
# OBJECT EXTRACTION: no colour scene at all, a busy bench (stripes), and the
# object cut out of the picture by GrabCut round where depth says it stands.
_busy = _cbg.copy()
for _x in range(0, 640, 24):
    _cv2skirt.rectangle(_busy, (_x, 0), (_x + 11, 359), (150, 140, 130), -1)
_busyframe = _busy.copy()
_cv2skirt.rectangle(_busyframe, (int(_xs.min() / 2), int(_ys.min() / 2)),
                    (int(_xs.max() / 2), int(_ys.max() / 2)), (40, 42, 60), -1)
_prior = (fuzzy.astype(np.int64) < empty.astype(np.int64) - 15)[det[1]:det[3], det[0]:det[2]]
ext = measure.extract_object(_busyframe, det, fuzzy.shape, depth_prior=_prior)
# A box that CLIPS the object by a few pixels on one side still gets the
# whole silhouette back: the margin round the box is only probably
# background, and the cut may reach into it.
clipped = [det[0] + 24, det[1], det[2], det[3]]              # ~65mm of the box cut off
clip_m = measure.measure_box(fuzzy, 1.0, INTR, clipped, None, background=empty,
                             colour=_busyframe, quality="best")
check("a clipped detection box does not clip the extraction: the whole length comes back",
      clip_m is not None and close(clip_m["length_mm"], 400.0, 14.0),
      None if clip_m is None else clip_m["length_mm"])
check("object extraction cuts the object's own silhouette out of a busy picture",
      ext is not None and ext.shape == (det[3] - det[1], det[2] - det[0]) and 0.2 < ext.mean() < 0.9,
      None if ext is None else (ext.shape, round(float(ext.mean()), 3)))
ex_m = measure.measure_box(fuzzy, 1.0, INTR, det, None, background=empty, colour=_busyframe)
check("...and measured from it, with no colour scene remembered: four true sides",
      ex_m is not None and ex_m["outline"] is not None and len(ex_m["outline"]["vertices"]) == 4
      and close(ex_m["length_mm"], 400.0, 12.0) and close(ex_m["width_mm"], 250.0, 12.0),
      None if ex_m is None else (ex_m["length_mm"], ex_m["width_mm"],
                                 ex_m["outline"] and ex_m["outline"]["sides_mm"]))
# Colour alone is not believed: a colour change with NO depth behind it (a
# drawing on the bench) measures nothing.
flat = measure.measure_box(empty, 1.0, INTR, det, None, background=empty,
                           colour=_cframe, colour_bg=_cbg)
check("a colour change with nothing standing there is not an object", flat is None)
# Each outline corner is converted at its OWN depth, read just inside the
# edge -- so a corner nearer the camera does not borrow the far corner's
# distance.
check("outline corners are converted at their own depth, sampled inside the edge",
      measure._vertex_depth(scene, 1.0, 640 - 70, 360 - 40, (640, 360), 1880.0) < 1900
      and measure._vertex_depth(None, 1.0, 0, 0, (1, 1), 1880.0) == 1880.0)

# -- a round thing lying down: the width is tangent to tangent ------------------------
# A bottle 90mm across on a level floor 2m away, axis along y. Its silhouette
# is made of tangent points at the AXIS distance, 45mm behind its nearest
# surface. Converted at the nearest depth it read 82mm on the real bottle;
# converted at the edge depth it is 90.
def render_cylinder(r_mm=45.0, length_mm=220.0, floor_z=2000.0):
    us, vs = np.meshgrid(np.arange(W), np.arange(H))
    rx = (us - INTR["cx"]) / INTR["fx"]
    ry = (vs - INTR["cy"]) / INTR["fy"]
    z_axis = floor_z - r_mm
    a = rx * rx + 1.0
    disc = z_axis * z_axis - a * (z_axis * z_axis - r_mm * r_mm)
    z_hit = np.where(disc >= 0, (z_axis - np.sqrt(np.maximum(disc, 0))) / a, floor_z)
    on = (disc >= 0) & (np.abs(ry * z_hit) <= length_mm / 2)
    return np.where(on, z_hit, floor_z).clip(1, 60000).astype(np.uint16)

# Close, as the real bottle was (0.6m): far away it is 30 pixels wide and
# there is no band to speak of.
cyl_empty = np.full((H, W), 600, np.uint16)
cyl = render_cylinder(floor_z=600.0)
cyl_det = [640 - 90, 360 - 170, 640 + 90, 360 + 170]
cyl_2d = measure.measure_box(cyl, 1.0, INTR, cyl_det, None, background=cyl_empty, dims="2d")
check("a round thing's width is measured tangent to tangent, not at its nearest surface (2D)",
      cyl_2d is not None and close(cyl_2d["width_mm"], 90.0, 5.0) and close(cyl_2d["length_mm"], 220.0, 10.0),
      None if cyl_2d is None else (cyl_2d["width_mm"], cyl_2d["length_mm"], cyl_2d.get("edge_distance_mm")))
check("...its nearest distance and its edge distance both reported, the edge well behind the crest",
      cyl_2d and 20.0 <= cyl_2d["edge_distance_mm"] - cyl_2d["distance_mm"] <= 50.0,
      cyl_2d and (cyl_2d["distance_mm"], cyl_2d["edge_distance_mm"]))
cyl_3d = measure.measure_box(cyl, 1.0, INTR, cyl_det, None, background=cyl_empty, dims="3d")
check("...and in 3D, from its points tangent to tangent",
      cyl_3d is not None and close(cyl_3d["width_mm"], 90.0, 6.0) and close(cyl_3d["length_mm"], 220.0, 10.0),
      None if cyl_3d is None else (cyl_3d["width_mm"], cyl_3d["length_mm"]))
check("...while a flat box still reads its top", close(measure.measure_box(
    scene, 1.0, INTR, det, floor)["width_mm"], 250.0, 10.0))

# -- the operator's corners ----------------------------------------------------------------
# Corners set by hand are measured as set: the shape is theirs, the depth is
# the camera's. The box's true corners, tapped 3px inside, give its sides.
hand = [px(-197, -122), px(197, -122), px(197, 122), px(-197, 122)]
hand_m = measure.measure_box(scene, 1.0, INTR, [500, 250, 780, 470], floor,
                             mask_override=measure.polygon_mask(scene.shape, hand),
                             vertices_override=hand, dims="3d")
check("corners set by hand are measured as set: four sides of the box",
      hand_m is not None and hand_m["outline"]["vertices"] == [[round(u, 1), round(v, 1)] for u, v in hand]
      and close(sorted(hand_m["outline"]["sides_mm"])[-1], 394.0, 12.0)
      and close(sorted(hand_m["outline"]["sides_mm"])[0], 244.0, 12.0),
      None if hand_m is None else hand_m["outline"]["sides_mm"])
check("...with their angles and the height under them",
      hand_m and all(close(a, 90.0, 5.0) for a in hand_m["outline"]["corner_angles_deg"])
      and close(hand_m["height_mm"], 120.0, 8.0), hand_m and hand_m["outline"]["corner_angles_deg"])

# An OUTLINE profile at run time: the operator's corners, re-placed inside
# each detection's box, measured with the depth under them.
out_prof = measure.Measurer([{"id": "o", "cls": "box", "tool": "outline", "dims": "2d",
                              "anchor_box": [det[0] / 1280, det[1] / 720, det[2] / 1280, det[3] / 720],
                              "points": [[u / 1280, v / 360 / 2] for u, v in hand]}], floor)
out_dets = [{"label": "box", "box": [v / 2.0 for v in det], "confidence": 0.9, "track_id": 61}]
out_prof.stamp(scene, 1.0, INTR, out_dets, 640, 360, now=900.0)
check("an outline profile measures the operator's corners on the live object",
      out_dets[0].get("measure", {}).get("sides_mm")
      and close(sorted(out_dets[0]["measure"]["sides_mm"])[-1], 394.0, 14.0)
      and len(out_dets[0]["measure"]["outline"]["vertices"]) == 4,
      out_dets[0].get("measure", {}).get("sides_mm"))

# -- 2D and 3D ----------------------------------------------------------------------
# 2D measures the silhouette flat at the top's distance. 3D fits the top's
# depth points: on the 4-degree floor the box top is tilted 4 degrees to the
# camera, so 3D reports that tilt, true length and width, and the volume.
two_d = measure.measure_box(scene, 1.0, INTR, det, floor, dims="2d")
three_d = measure.measure_box(scene, 1.0, INTR, det, floor, dims="3d")
check("2D and 3D both give the box its size",
      two_d and three_d and close(two_d["length_mm"], 400.0, 12.0)
      and close(three_d["length_mm"], 400.0, 12.0) and close(three_d["width_mm"], 250.0, 12.0),
      (two_d and two_d["length_mm"], three_d and (three_d["length_mm"], three_d["width_mm"])))
check("...and say which they are", two_d["dims"] == "2d" and three_d["dims"] == "3d")
check("3D reports the top's tilt - level to the floor here, so about 0",
      three_d.get("tilt_deg") is not None and three_d["tilt_deg"] < 2.5, three_d.get("tilt_deg"))
no_floor_3d = measure.measure_box(scene, 1.0, INTR, det, None, background=empty, dims="3d")
check("...and against the camera when there is no floor: the 4-degree render",
      no_floor_3d and close(no_floor_3d["tilt_deg"], 4.0, 1.5), no_floor_3d and no_floor_3d.get("tilt_deg"))
check("...the bounding volume, L x W x H, within 5%",
      close(three_d["volume_cm3"], 400 * 250 * 120 / 1000.0, 600.0), three_d.get("volume_cm3"))
check("...and it says how many depth points it stood on", three_d.get("points_3d", 0) > 1000)
check("every corner carries its angle: a box is four right angles",
      len(three_d["outline"]["corner_angles_deg"]) == 4
      and all(a is not None and close(a, 90.0, 6.0) for a in three_d["outline"]["corner_angles_deg"]),
      three_d["outline"]["corner_angles_deg"])
check("...in 2D too", all(close(a, 90.0, 6.0) for a in two_d["outline"]["corner_angles_deg"]),
      two_d["outline"]["corner_angles_deg"])
dims_meas = measure.Measurer([{"id": "c", "cls": "box", "tool": "auto", "dims": "3d"}], floor)
dims_dets = [{"label": "box", "box": [v / 2.0 for v in det], "confidence": 0.9, "track_id": 51}]
dims_meas.stamp(scene, 1.0, INTR, dims_dets, 640, 360, now=800.0)
check("a 3D profile carries tilt and volume on the detection",
      dims_dets[0].get("measure", {}).get("dims") == "3d" and dims_dets[0].get("volume_cm3") is not None
      and dims_dets[0].get("tilt_deg") is not None, dims_dets[0].get("measure"))

# -- a round thing ---------------------------------------------------------------
# "Length 300, width 300" of a 300mm disc is true and useless. The auto path
# has to notice the corners of its rectangle are EMPTY and say diameter.
round_scene = render(disc=(150.0, 80.0, 0.0, 0.0))
mc = measure.measure_box(round_scene, 1.0, INTR, [640 - 110, 360 - 110, 640 + 110, 360 + 110], floor)
check("a disc is recognised as round, not reported as a square box",
      mc is not None and mc["shape"] == "circle", None if mc is None else mc["shape"])
check("...with its diameter within 2%", mc and close(mc["diameter_mm"], 300.0, 6.0),
      mc and mc.get("diameter_mm"))
check("...radius and area to match", mc and close(mc["radius_mm"], 150.0, 3.0)
      and close(mc["area_mm2"], math.pi * 150.0 ** 2, 0.03 * math.pi * 150.0 ** 2),
      mc and (mc.get("radius_mm"), mc.get("area_mm2")))
check("...height still measured above the floor", mc and close(mc["height_mm"], 80.0, 5.0),
      mc and mc.get("height_mm"))
check("...while the box from before is still a box", m["shape"] == "box")
check("a disc's silhouette is a curve - many points - while its sides stay few",
      len(mc["outline"]["silhouette"]) >= 12 and len(mc["outline"]["vertices"]) <= 12,
      (len(mc["outline"]["silhouette"]), len(mc["outline"]["vertices"])))

# Three taps on the rim, as the manual tool takes them.
def rim(theta_deg, r=150.0, h=80.0):
    t = math.radians(4.0)
    n_y, n_z = math.sin(t), math.cos(t)
    d_top = -n_z * 2000.0 + h
    xm, ym = (r - 4) * math.cos(math.radians(theta_deg)), (r - 4) * math.sin(math.radians(theta_deg))
    z = -(d_top + n_y * ym) / n_z
    return (INTR["cx"] + INTR["fx"] * xm / z, INTR["cy"] + INTR["fy"] * ym / z)

three = measure.circle_from_points(round_scene, 1.0, INTR, [rim(0), rim(120), rim(240)], floor)
check("three taps on a rim give the circle",
      "error" not in three and close(three["diameter_mm"], 2 * 146.0, 6.0),
      three)
check("...its height above the floor", close(three.get("height_mm"), 80.0, 5.0),
      three.get("height_mm"))
five = measure.circle_from_points(round_scene, 1.0, INTR,
                                  [rim(a) for a in (10, 80, 150, 220, 300)], floor)
check("more taps are fitted, and the fit reports its own scatter",
      close(five["diameter_mm"], 2 * 146.0, 6.0) and five["fit_rms_mm"] < 3.0,
      (five.get("diameter_mm"), five.get("fit_rms_mm")))
check("two taps are not a circle",
      "error" in measure.circle_from_points(round_scene, 1.0, INTR, [rim(0), rim(180)]))
check("a tap over nothing is refused rather than fitted",
      "error" in measure.circle_from_points(hole, 1.0, INTR, [(640, 360), rim(120), rim(240)]))

# -- two taps, the way a person with a tape would take it ---------------------------
# Across the disc, rim to rim: the diameter. Centre to rim: the radius. The
# operator says which; the maths does not guess.
across = measure.circle_from_two(round_scene, 1.0, INTR, rim(180), rim(0), "diameter", floor)
check("two taps across a disc give its diameter",
      "error" not in across and close(across["diameter_mm"], 2 * 146.0, 6.0), across)
check("...and its height above the floor", close(across.get("height_mm"), 80.0, 5.0),
      across.get("height_mm"))
centre_px = (INTR["cx"], INTR["cy"])
out = measure.circle_from_two(round_scene, 1.0, INTR, centre_px, rim(0), "radius", floor)
check("centre-to-rim taps give the radius, not half of it",
      close(out["radius_mm"], 146.0, 6.0) and close(out["diameter_mm"], 292.0, 12.0),
      (out.get("radius_mm"), out.get("diameter_mm")))
check("...with a ring to draw, in picture pixels at the centre's depth",
      out["radius_px"] > 40 and out["centre"] == [round(centre_px[0], 1), round(centre_px[1], 1)],
      (out.get("radius_px"), out.get("centre")))
check("a tap over nothing is refused, not fitted",
      "error" in measure.circle_from_two(hole, 1.0, INTR, (640, 360), rim(0)))

# Sides: two taps each, and they share nothing.
c = [px(-200, -125), px(200, -125), px(200, 125), px(-200, 125)]
c = [(u + (2 if u < 640 else -2), v + (2 if v < 360 else -2)) for u, v in c]
sides_out = measure.measure_sides(scene, 1.0, INTR, [c[0], c[1], c[1], c[2]], floor)
check("two pairs of taps are two independent sides",
      len(sides_out["segments_mm"]) == 2
      and close(sides_out["segments_mm"][0], 400.0, 14.0)
      and close(sides_out["segments_mm"][1], 250.0, 14.0), sides_out["segments_mm"])
check("...and an unfinished side (one tap) is left out rather than guessed",
      len(measure.measure_sides(scene, 1.0, INTR, [c[0], c[1], c[2]], floor)["segments_mm"]) == 1)
check("...each drawn back where it was tapped",
      sides_out["segments"][0][0] == [float(c[0][0]), float(c[0][1])])

# -- background subtraction: the empty scene remembered, no plane needed ----------
# The same box, measured against the EMPTY render of the same scene rather
# than a fitted plane. And on a scene that is NOT a floor -- a wall of
# things, tilted 40 degrees, where the plane fit is refused -- the box is
# still found, because it stands in front of whatever was there.
bg = measure.measure_box(scene, 1.0, INTR, det, None, background=empty)
check("with the empty scene remembered, a box is measured without any floor plane",
      bg is not None and close(bg["length_mm"], 400.0, 12.0) and close(bg["width_mm"], 250.0, 12.0)
      and close(bg["height_mm"], 120.0, 8.0),
      None if bg is None else (bg["length_mm"], bg["width_mm"], bg["height_mm"]))
# A scene that is NOT a floor: a shelf 300mm closer across the left third.
steep_empty = empty.copy(); steep_empty[:, :400] -= 300
steep_box = scene.copy(); steep_box[:, :400] -= 300
steep_fit = measure.fit_floor(steep_empty, 1.0, INTR)
check("...a scene with a shelf in it is refused as a floor plane",
      steep_fit is None or steep_fit["rms_mm"] > 25, None if steep_fit is None else steep_fit["rms_mm"])
steep = measure.measure_box(steep_box, 1.0, INTR, det, None, background=steep_empty)
check("...yet background subtraction still finds the box on it, its top the right size",
      steep is not None and close(steep["length_mm"], 400.0, 24.0) and close(steep["width_mm"], 250.0, 24.0),
      None if steep is None else (steep["length_mm"], steep["width_mm"]))
check("a background of another size is ignored, not indexed",
      measure.measure_box(scene, 1.0, INTR, det, floor, background=empty[:100, :100]) is not None
      and measure.measure_box(scene, 1.0, INTR, det, None, background=empty[:100, :100]) is None)
# The remembered scene round-trips through the PNG the master writes.
import tempfile                                                 # noqa: E402
_tmp = tempfile.mkdtemp()
import cv2 as _cv2                                              # noqa: E402
os.makedirs(os.path.join(_tmp, "measure"))
_cv2.imwrite(os.path.join(_tmp, "measure", "main_background.png"), empty)
loaded = measure.load_background(_tmp, {"file": "measure/main_background.png"})
check("the remembered scene loads back from the package folder, every millimetre intact",
      loaded is not None and loaded.dtype == np.uint16 and np.array_equal(loaded, empty))
check("...and a path that leaves the folder loads nothing",
      measure.load_background(_tmp, {"file": "../measure/main_background.png"}) is None
      and measure.load_background(_tmp, {"file": "measure/missing.png"}) is None)
bgm = measure.Measurer([{"id": "c", "cls": "box", "tool": "auto"}], None, background=empty)
bgd = [{"label": "box", "box": [v / 2.0 for v in det], "confidence": 0.9, "track_id": 31}]
bgm.stamp(scene, 1.0, INTR, bgd, 640, 360, now=600.0)
check("the run-time measurer measures from the remembered scene alone",
      close(bgd[0].get("length_mm"), 400.0, 12.0), bgd[0].get("length_mm"))

# -- at run time: the saved profiles meet the live detections -------------------
# The detector saw a 640x360 frame; the depth grid is 1280x720. A box in the
# detector's pixels has to be scaled -- the top-left-quarter bug otherwise.
half = [v / 2.0 for v in det]
meas = measure.Measurer([{"id": "carton", "cls": "box", "tool": "auto",
                          "fields": ["length_mm", "width_mm", "height_mm"]}], floor)
dets = [{"label": "box", "box": half, "confidence": 0.9, "track_id": 7},
        {"label": "person", "box": [10, 10, 60, 120], "confidence": 0.8, "track_id": 8}]
stats = meas.stamp(scene, 1.0, INTR, dets, 640, 360, now=100.0)
check("every detection gets a depth, profile or not",
      dets[0].get("depth_mm") is not None and dets[1].get("depth_mm") is not None,
      [d.get("depth_mm") for d in dets])
check("...the box's being the top surface, not the floor",
      dets[0]["depth_mm"] < 1900, dets[0].get("depth_mm"))
check("the profiled class carries its size, flat, by name",
      close(dets[0].get("length_mm"), 400.0, 12.0) and close(dets[0].get("width_mm"), 250.0, 12.0)
      and close(dets[0].get("height_mm"), 120.0, 6.0),
      (dets[0].get("length_mm"), dets[0].get("width_mm"), dets[0].get("height_mm")))
check("...and only the numbers the profile ticked",
      "angle_deg" not in dets[0]["measure"] and "length_mm" in dets[0]["measure"],
      sorted(dets[0]["measure"]))
check("...while the unprofiled class carries none", "measure" not in dets[1])
check("the nearest profiled object's numbers are published as metrics",
      close(stats.get("measure_carton_length_mm"), 400.0, 12.0)
      and stats.get("measure_carton_count") == 1, {k: v for k, v in stats.items() if "carton" in k})
check("...and the nearest/farthest depth in frame, for distance rules",
      0 < stats["depth_nearest_mm"] <= stats["depth_farthest_mm"],
      (stats["depth_nearest_mm"], stats["depth_farthest_mm"]))
# Throttled: the same track a tenth of a second later is not re-measured,
# but still carries last time's numbers.
before = meas._cache[7][0]
dets2 = [{"label": "box", "box": half, "confidence": 0.9, "track_id": 7}]
meas.stamp(scene, 1.0, INTR, dets2, 640, 360, now=100.1)
check("a track is re-measured at most every REMEASURE_S, and keeps its numbers between",
      meas._cache[7][0] == before and close(dets2[0].get("length_mm"), 400.0, 12.0),
      (meas._cache[7][0], dets2[0].get("length_mm")))
meas.stamp(scene, 1.0, INTR, dets2, 640, 360, now=101.0)
check("...and is re-measured once it is due", meas._cache[7][0] == 101.0)
none = measure.Measurer([{"id": "x", "cls": "", "tool": "auto"}], None)
fresh = [{"label": "box", "box": half, "confidence": 0.9, "track_id": 9}]
none.stamp(scene, 1.0, INTR, fresh, 640, 360, now=102.0)
check("no floor: depth still stamped, sizes honestly absent",
      fresh[0].get("depth_mm") is not None and "measure" not in fresh[0], fresh[0])
empty_stats = measure.Measurer([], None).stamp(scene, 1.0, INTR, [], 640, 360, now=0.0)
check("nothing in frame reads as -1, the sentinel every proximity rule already uses",
      empty_stats["depth_nearest_mm"] == -1)
# In the app the measuring runs on a worker thread: stamp() queues the due
# objects and stamps whatever is finished -- so the first frame carries no
# numbers, and a later one does, and the loop never waited.
import time as _time                                            # noqa: E402
bg_meas = measure.Measurer([{"id": "carton", "cls": "box", "tool": "auto"}], floor,
                           background_thread=True)
async_dets = [{"label": "box", "box": half, "confidence": 0.9, "track_id": 41}]
bg_meas.stamp(scene, 1.0, INTR, async_dets, 640, 360, now=700.0)
check("with a worker, the first frame is stamped without waiting for the measurement",
      "length_mm" not in async_dets[0])
deadline = _time.time() + 5.0
while _time.time() < deadline:
    async_dets = [{"label": "box", "box": half, "confidence": 0.9, "track_id": 41}]
    bg_meas.stamp(scene, 1.0, INTR, async_dets, 640, 360, now=700.1)
    if "length_mm" in async_dets[0]:
        break
    _time.sleep(0.05)
check("...and a later frame carries the worker's numbers",
      close(async_dets[0].get("length_mm"), 400.0, 12.0), async_dets[0].get("length_mm"))

# A tap ON THE EDGE of the box top reads the object, not the floor behind it.
edge_u = int(_xs.min()) + 1
edge_v = int((_ys.min() + _ys.max()) / 2)
edge_z = measure.depth_at_point(scene, 1.0, edge_u, edge_v)
check("a tap on an object's edge reads the object's depth, not the bench behind it",
      edge_z is not None and edge_z < 1900, edge_z)
edge_sides = measure.measure_sides(scene, 1.0, INTR,
                                   [(int(_xs.min()) + 1, edge_v), (int(_xs.max()) - 1, edge_v)], floor)
check("...so a side tapped edge to edge is the object's length",
      close(edge_sides["segments_mm"][0], 400.0, 12.0), edge_sides["segments_mm"])

# A named profile outranks a blank one for its own class.
both = measure.Measurer([{"id": "any", "cls": "", "tool": "auto"},
                         {"id": "carton", "cls": "box", "tool": "auto"}], floor)
check("a profile for the class wins over 'every object'",
      both.profile_for("box")["id"] == "carton" and both.profile_for("cup")["id"] == "any")

# What is written under the box on the live picture.
from app import camera as camera_mod                            # noqa: E402
check("a measured box says its size and distance under the label",
      camera_mod.size_text({"measure": {"shape": "box", "length_mm": 400.2, "width_mm": 250,
                                        "height_mm": 120}, "depth_mm": 1880})
      == "400x250x120 mm  1.88 m")
check("...a circle its diameter", camera_mod.size_text(
    {"measure": {"shape": "circle", "diameter_mm": 300.4}}) == "dia 300 mm")
check("...a plain depth reading its distance",
      camera_mod.size_text({"depth_mm": 2350}) == "2.35 m")
check("...and nothing at all when there is nothing to say",
      camera_mod.size_text({"label": "person"}) == "")

# The LINES on the object. Geometry is carried in the detector frame's
# pixels -- half the grid's here -- so a side drawn on the 640-wide picture
# lands on the object and not two pictures away.
full = measure.Measurer([{"id": "carton", "cls": "box", "tool": "auto", "sides": [0, 2]}], floor)
dd = [{"label": "box", "box": half, "confidence": 0.9, "track_id": 11}]
full.stamp(scene, 1.0, INTR, dd, 640, 360, now=200.0)
segs, ring, sil = camera_mod.measure_lines(dd[0])
check("the live feed gets the silhouette in the frame's pixels",
      len(sil) >= 4 and all(0 <= p[0] <= 640 and 0 <= p[1] <= 360 for p in sil), len(sil))
check("an auto profile with two ticked sides draws exactly those two, with sizes",
      len(segs) == 2 and all(t.endswith(" mm") for *_a, t in segs), segs)
check("...in the detector frame's pixels, on the object",
      all(0 <= p[0] <= 640 and 0 <= p[1] <= 360 for a, b, _t in segs for p in (a, b))
      and all(abs(p[0] - 320) < 130 and abs(p[1] - 180) < 100 for a, b, _t in segs for p in (a, b)),
      segs)
check("...and no ring for a box", ring is None)
two = measure.Measurer([{"id": "cap", "cls": "cup", "tool": "circle", "mode": "diameter",
                         "points": [[rim(180)[0] / 1280, rim(180)[1] / 720],
                                    [rim(0)[0] / 1280, rim(0)[1] / 720]]}], floor)
cup = [{"label": "cup", "box": [640 / 2 - 60, 360 / 2 - 60, 640 / 2 + 60, 360 / 2 + 60],
        "confidence": 0.9, "track_id": 12}]
two.stamp(round_scene, 1.0, INTR, cup, 640, 360, now=300.0)
segs, ring, _sil = camera_mod.measure_lines(cup[0])
# A profile anchored to the box it was taught on FOLLOWS the object. Taught
# with two taps down the middle of a detection at one place; the same class
# detected elsewhere gets the taps re-placed inside ITS box.
taught = [half[0] / 640, half[1] / 360, half[2] / 640, half[3] / 360]      # normalised
follow = measure.Measurer([{"id": "h", "cls": "box", "tool": "sides",
                            "anchor_box": taught,
                            "points": [[(taught[0] + taught[2]) / 2, taught[1] + 0.3 * (taught[3] - taught[1])],
                                       [(taught[0] + taught[2]) / 2, taught[3] - 0.3 * (taught[3] - taught[1])]]}],
                          floor)
moved_scene = render(box=(400.0, 250.0, 120.0, -300.0, 0.0))           # same box, 300mm left
moved_det = [{"label": "box", "box": [half[0] - 55, half[1], half[2] - 55, half[3]],
              "confidence": 0.9, "track_id": 21}]
follow.stamp(moved_scene, 1.0, INTR, moved_det, 640, 360, now=400.0)
segs_f, _r, _s = camera_mod.measure_lines(moved_det[0])
inside = lambda p, b: b[0] - 1 <= p[0] <= b[2] + 1 and b[1] - 1 <= p[1] <= b[3] + 1   # noqa: E731
check("an anchored profile re-places its taps inside the object's box wherever it is",
      len(segs_f) == 1 and all(inside(p, moved_det[0]["box"]) for p in segs_f[0][:2]),
      (segs_f, moved_det[0]["box"]))
check("...and the side measured there is a real length, not a '?'",
      segs_f and segs_f[0][2].endswith(" mm") and moved_det[0]["measure"]["sides_mm"]
      and 150 < moved_det[0]["measure"]["sides_mm"][0] < 260, segs_f)
fixed = measure.Measurer([{"id": "h", "cls": "box", "tool": "sides",
                           "points": [[0.5, 0.4], [0.5, 0.6]]}], floor)
away = [{"label": "box", "box": [10, 10, 100, 100], "confidence": 0.9, "track_id": 22}]
fixed.stamp(scene, 1.0, INTR, away, 640, 360, now=500.0)
check("...while an unanchored profile stays a fixed spot and measures nothing elsewhere",
      "measure" not in away[0])

check("a two-tap circle profile draws its ring and the span it measured",
      ring is not None and ring[2].startswith("dia ") and len(segs) == 1
      and abs(ring[0][0] - 320) < 6 and abs(ring[0][1] - 180) < 6
      and 20 < ring[1] < 60, (ring, segs))

# -- 3D zones: a depth range on a zone -------------------------------------------
from app import zones as zones_mod                              # noqa: E402
near = zones_mod.Zone({"id": "near", "kind": "area", "match": "center",
                       "points": [[0, 0], [1, 0], [1, 1], [0, 1]], "depth_max_mm": 1500})
engine = zones_mod.ZoneEngine([near])
no_depth_det = {"label": "person", "box": [500, 100, 600, 300], "depth_mm": None, "track_id": 3}
res = engine.update([
    {"label": "person", "box": [100, 100, 200, 300], "depth_mm": 1200, "track_id": 1},
    {"label": "person", "box": [300, 100, 400, 300], "depth_mm": 2500, "track_id": 2},
    no_depth_det,
], 640, 480)
check("a zone with a far bound counts the near object and not the far one",
      res["zones"]["near"]["count"] == 2, res["zones"]["near"]["count"])
check("...and a box with NO depth reading still counts - the range narrows, never blinds",
      "near" in (no_depth_det.get("zones") or []), no_depth_det.get("zones"))
check("the bound survives the round trip to the overlay",
      near.as_dict()["depth_max_mm"] == 1500 and "depth_min_mm" in near.as_dict())
plain = zones_mod.Zone({"id": "p", "kind": "area", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]})
check("...and a zone without one carries nothing new",
      "depth_max_mm" not in plain.as_dict() and plain.in_range(99999))
swapped = zones_mod.Zone({"id": "s", "kind": "area", "points": [[0, 0], [1, 0], [1, 1], [0, 1]],
                          "depth_min_mm": 3000, "depth_max_mm": 1000})
check("near and far the wrong way round are swapped, not a zone that sees nobody",
      swapped.in_range(2000) and not swapped.in_range(500))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
