# -*- coding: utf-8 -*-
"""Real-world size from a depth picture: length, width, height, distance.

A camera only knows ANGLES. A box that is 100 pixels wide might be a matchbox
at arm's length or a pallet across the room; nothing in the picture says
which. Depth is what turns an angle into a length:

    real length (mm) = pixel length x depth (mm) / f (pixels)

where f is the lens's focal length in pixels -- the INTRINSICS, which the
Orbbec SDK reports for its colour stream (fx 689, fy 690 on the Gemini 336 at
1280x720). Depth here is aligned to the colour image, so one set of intrinsics
serves both.

THE FLOOR IS A PLANE, NOT A NUMBER. First-time setup captures the empty scene
and fits a plane through it. A single "floor is 2000mm away" would be wrong
the moment the camera is a few degrees off square -- and it always is -- and
the error would read as a slope in every measurement. A plane costs one
least-squares fit and makes height honest from any mount.

WHAT IS THE OBJECT? Inside a detection box, the pixels that stand above the
floor plane by more than a threshold. The floor does not; the object does.
That segmentation is what makes the angle automatic: the tightest rotated
rectangle around those pixels IS the object's footprint, at whatever angle it
happens to be lying.

MANUAL POINTS are the override for when that guess is wrong -- a dark object
the stereo cannot see well, two items touching, a box on a pallet. Each tap on
the picture is a pixel with a depth under it, so it is a real 3D point, and
the distance between two of them is a true length whatever the angle.

Nothing here imports the SDK, cv2 is used for the rotated rectangle alone, and
every function takes plain arrays -- so the whole file is testable against a
synthetic scene, which is how the arithmetic was checked before a real box
ever went under the camera.
"""
import math
import threading
from collections import deque

import numpy as np

#: A depth pixel with nothing measured in it (see orbbec.py).
NO_READING = 0
UNMEASURED = 0xFFFF

#: How far above the floor a pixel must sit to count as OBJECT. Below this is
#: floor noise -- the Gemini's depth jitter at 2m is a few millimetres, and a
#: plane fit through a slightly textured floor leaves residuals of about the
#: same. A 10mm-thick sheet is measured; a scuff mark is not.
MIN_HEIGHT_MM = 15.0

#: How much of a detection box must be object before a measurement is
#: reported. Under this the box is mostly floor -- a mis-detection, or a thing
#: too dark for the stereo to see -- and a confident number from the few
#: pixels that did register would be the worst kind of wrong.
MIN_OBJECT_FRACTION = 0.05


def valid(grid):
    """Mask of pixels that carry a reading."""
    return (grid != NO_READING) & (grid != UNMEASURED)


# -- at run time: every detection, every frame ------------------------------------

#: How often ONE tracked object is re-measured, at most. A measurement is a
#: connected-components pass over the object's patch of a 1280x720 grid --
#: cheap once, not cheap thirty times a second for eight objects on a Pi
#: that is also running inference. An object does not change size between
#: frames; its NUMBERS are carried forward and re-taken twice a second.
REMEASURE_S = 0.5

#: ...and how many objects are measured on any one frame. Past this the rest
#: keep last frame's numbers and take their turn on the next. Two, because
#: an Auto measurement now includes a foreground extraction (extract_object)
#: and two of those is the most a frame period on a Pi will hide.
MAX_PER_FRAME = 2

#: How long a track's numbers are kept after it was last seen, so a
#: detection that flickers for a frame does not lose its size.
KEEP_S = 5.0

#: The numbers a profile may publish, and the shape's result key each comes
#: from. A profile with no `fields` publishes every one its tool produced.
FIELDS = ("length_mm", "width_mm", "height_mm", "distance_mm", "angle_deg",
          "area_mm2", "diameter_mm", "radius_mm", "perimeter_mm",
          "tilt_deg", "volume_cm3")

#: Depth in a detection box: the median of its middle half. Mirrors
#: orbbec.depth_mm, duplicated on purpose -- this module is loaded into the
#: master by file path (detector_control._load_app_module) and imports no
#: sibling.
def box_depth_mm(grid, scale, box, sample=0.5):
    if grid is None or not box:
        return None
    h, w = grid.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box[:4])
    x1, x2 = max(0, min(x1, x2)), min(w, max(x1, x2))
    y1, y2 = max(0, min(y1, y2)), min(h, max(y1, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    ix = int((x2 - x1) * (1.0 - sample) / 2)
    iy = int((y2 - y1) * (1.0 - sample) / 2)
    patch = grid[y1 + iy:y2 - iy or y2, x1 + ix:x2 - ix or x2]
    if patch.size == 0:
        patch = grid[y1:y2, x1:x2]
    good = patch[valid(patch)]
    if good.size == 0 or good.size < patch.size * 0.5:
        return None
    return float(np.median(good)) * float(scale or 1.0)


class Measurer:
    """Applies a camera's saved measurement profiles to its live detections.

    The operator saved, per kind of object, WHICH tool measures it and which
    of its numbers matter (detector_control._clean_measure). This is where
    those profiles meet the frame: every detection gets `depth_mm`; every
    detection whose class has a profile gets that profile's numbers stamped
    on it -- flat (`length_mm`, `width_mm`...) so a Data Table column or an
    event can read them by name, and under `measure` for the drawing --
    and the nearest such object's numbers are published as metrics
    (`measure_<profile>_<field>`) for the Workflow to compare against.

    Stateful for two reasons: the throttle (REMEASURE_S, MAX_PER_FRAME) and
    the carry-over -- a track keeps its last numbers between measurements,
    so the label on the picture never flickers between a size and nothing.
    """

    #: How many objects may wait to be measured at once. Past this the
    #: oldest waits are dropped -- they will be asked for again next frame.
    QUEUE_MAX = 8

    def __init__(self, profiles=None, floor=None, background=None, colour_bg=None,
                 background_thread=False):
        self.configure(profiles, floor, background, colour_bg)
        #: track key -> (measured_at, seen_at, result dict or None)
        self._cache = {}
        self._lock = threading.Lock()
        # OFF THE LOOP. An Auto measurement now includes a foreground
        # extraction (extract_object, ~200ms on the dev box, more on a Pi),
        # and the detection loop is what paces the video and the counts. So
        # in the app the work is handed to one worker thread per camera:
        # stamp() queues the objects that are due and stamps whatever the
        # worker has finished so far -- one measurement late, never a frame
        # late. Synchronous by default for the tests and the master, which
        # want the answer now.
        self._threaded = bool(background_thread)
        self._queue = deque()
        self._queued = set()
        self._wake = threading.Event()
        self._thread = None

    def _ensure_worker(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._work, name="measure",
                                            daemon=True)
            self._thread.start()

    def _work(self):
        while True:
            self._wake.wait(1.0)
            while True:
                with self._lock:
                    if not self._queue:
                        self._wake.clear()
                        break
                    key, job = self._queue.popleft()
                    self._queued.discard(key)
                grid, scale, intr, profile, scaled, gw, gh, frame, now = job
                try:
                    result = self._measure(grid, scale, intr, profile, scaled, gw, gh, frame)
                except Exception:                           # noqa: BLE001
                    result = None
                with self._lock:
                    seen = self._cache.get(key)
                    self._cache[key] = (now, seen[1] if seen else now, result)

    def configure(self, profiles, floor, background=None, colour_bg=None):
        """(Re)load the saved table. Called on every config reload; keeps the
        cache, because a saved profile does not change what the objects
        already in view are. `background` is the remembered empty scene
        (load_background) -- Auto prefers it to the floor plane."""
        self.floor = floor if isinstance(floor, dict) and floor.get("normal") else None
        self.background = background if background is not None and getattr(background, "ndim", 0) == 2 else None
        self.colour_bg = colour_bg if colour_bg is not None and getattr(colour_bg, "ndim", 0) == 3 else None
        rows = []
        for row in (profiles or []):
            if not isinstance(row, dict) or not row.get("enabled", True):
                continue
            rows.append({
                "id": str(row.get("id") or "").strip() or f"m{len(rows) + 1}",
                "cls": str(row.get("cls") or "").strip(),
                "tool": str(row.get("tool") or "auto").strip().lower(),
                "sides": [int(v) for v in (row.get("sides") or [])],
                "fields": [str(f) for f in (row.get("fields") or [])],
                "points": [[float(p[0]), float(p[1])] for p in (row.get("points") or [])],
                "mode": str(row.get("mode") or "diameter"),
                # 2D: the silhouette at one distance. 3D: the object's own
                # points -- true size on a tilt, tilt angle, volume.
                "dims": "3d" if str(row.get("dims") or "").lower() == "3d" else "2d",
                # The detection box (normalised) the taps were placed on when
                # the profile was saved. With one, the taps are RELATIVE to
                # the object and are re-placed inside every detection's box,
                # so a side taught on one bottle is measured on every bottle
                # wherever it stands. Without one they are a fixed spot.
                "anchor_box": ([float(v) for v in row["anchor_box"]]
                               if isinstance(row.get("anchor_box"), (list, tuple))
                               and len(row["anchor_box"]) == 4 else None),
            })
        self.profiles = rows

    @property
    def active(self):
        return bool(self.profiles)

    def profile_for(self, label):
        """The first enabled profile that takes this class: a named one
        before a blank one, so 'carton' wins over 'every object'."""
        blank = None
        for p in self.profiles:
            if p["cls"] == label:
                return p
            if not p["cls"] and blank is None:
                blank = p
        return blank

    def stamp(self, grid, scale, intr, detections, frame_w, frame_h, now, frame=None):
        """Depth on every detection, profile numbers on the ones that have
        one. Returns the metrics to publish this frame.

        Boxes arrive in the DETECTOR's pixels and the grid is the camera's
        own size, so every box is scaled here -- the top-left-quarter bug
        from the depth work is one missed scale away, always.
        """
        stats = {}
        if grid is None:
            return stats
        gh, gw = grid.shape[:2]
        sx = gw / float(frame_w or gw)
        sy = gh / float(frame_h or gh)
        nearest = farthest = None
        for det in detections or []:
            box = det.get("box")
            if not box:
                continue
            scaled = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
            depth = box_depth_mm(grid, scale, scaled)
            det["depth_mm"] = None if depth is None else round(depth, 0)
            if depth is not None:
                nearest = depth if nearest is None else min(nearest, depth)
                farthest = depth if farthest is None else max(farthest, depth)
        # -1 means "nothing measured": the same sentinel the proximity
        # rules use for nearest_px, and what _merge_metric folds correctly.
        stats["depth_nearest_mm"] = round(nearest, 0) if nearest is not None else -1
        stats["depth_farthest_mm"] = round(farthest, 0) if farthest is not None else -1
        if not self.profiles or intr is None:
            return stats

        budget = MAX_PER_FRAME
        seen = set()
        best = {}                       # profile id -> (distance, result, det)
        counts = {}
        for det in detections or []:
            box = det.get("box")
            profile = self.profile_for(str(det.get("label") or ""))
            if not box or profile is None:
                continue
            key = det.get("track_id")
            if key is None:
                # Not yet a confirmed track: keyed by where it is, coarsely,
                # so two frames of the same unconfirmed object share numbers.
                key = ("at", int((box[0] + box[2]) / 64), int((box[1] + box[3]) / 64))
            seen.add(key)
            with self._lock:
                cached = self._cache.get(key)
            result = cached[2] if cached else None
            due = cached is None or (now - cached[0]) >= REMEASURE_S
            if due and budget > 0:
                budget -= 1
                scaled = [box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy]
                if self._threaded:
                    with self._lock:
                        if key not in self._queued:
                            if len(self._queue) >= self.QUEUE_MAX:
                                old_key, _ = self._queue.popleft()
                                self._queued.discard(old_key)
                            self._queue.append((key, (grid, scale, intr, profile, scaled,
                                                      gw, gh, frame, now)))
                            self._queued.add(key)
                        # Mark it asked-for, so the same track is not queued
                        # again on every frame while the worker is busy.
                        self._cache[key] = (now, now, result)
                    self._wake.set()
                    self._ensure_worker()
                else:
                    result = self._measure(grid, scale, intr, profile, scaled, gw, gh, frame)
                    with self._lock:
                        self._cache[key] = (now, now, result)
            elif cached:
                with self._lock:
                    self._cache[key] = (cached[0], now, cached[2])
            counts[profile["id"]] = counts.get(profile["id"], 0) + 1
            if not result:
                continue
            kept = self._select(profile, result)
            # Geometry back to the DETECTOR's pixels: the boxes are drawn on
            # that frame, and a line left in grid pixels lands two frames
            # wide and two frames tall on a 640-wide picture.
            self._to_frame(kept, sx, sy)
            det["measure"] = kept
            for field, value in kept.items():
                if field in FIELDS and value is not None:
                    det[field] = value
            det["measure_profile"] = profile["id"]
            dist = kept.get("distance_mm") or det.get("depth_mm") or 1e9
            if profile["id"] not in best or dist < best[profile["id"]][0]:
                best[profile["id"]] = (dist, kept, det)
        # Forget tracks nobody has seen for a while.
        with self._lock:
            for key in [k for k, v in self._cache.items() if now - v[1] > KEEP_S]:
                del self._cache[key]
        for profile in self.profiles:
            pid = profile["id"]
            stats[f"measure_{pid}_count"] = counts.get(pid, 0)
            picked = best.get(pid)
            for field in FIELDS:
                value = picked[1].get(field) if picked else None
                if value is not None or field in ("length_mm", "width_mm",
                                                  "height_mm", "distance_mm"):
                    stats[f"measure_{pid}_{field}"] = value if value is not None else -1
        return stats

    def _measure(self, grid, scale, intr, profile, box, gw, gh, frame=None):
        """One object, with the profile's tool. None when nothing measurable."""
        tool = profile["tool"]
        pts = profile["points"]
        try:
            if tool != "auto" and len(pts) >= 2:
                anchor = profile.get("anchor_box")
                if anchor and anchor[2] > anchor[0] and anchor[3] > anchor[1]:
                    # Taps FOLLOW the object: each is re-placed at the same
                    # fraction of this detection's box that it had of the box
                    # it was taught on. The depth under the re-placed tap is
                    # this object's, here, now -- which is what makes the
                    # size right at any distance.
                    aw, ah = anchor[2] - anchor[0], anchor[3] - anchor[1]
                    bw, bh = box[2] - box[0], box[3] - box[1]
                    px = []
                    for u, v in pts:
                        ru = min(1.2, max(-0.2, (u - anchor[0]) / aw))
                        rv = min(1.2, max(-0.2, (v - anchor[1]) / ah))
                        px.append((min(gw - 1.0, max(0.0, box[0] + ru * bw)),
                                   min(gh - 1.0, max(0.0, box[1] + rv * bh))))
                else:
                    # Manual taps at a fixed PLACE in the picture (a fixture,
                    # a lane). They belong to this detection only when they
                    # fall on it.
                    px = [(u * gw, v * gh) for u, v in pts]
                    if not any(box[0] <= u <= box[2] and box[1] <= v <= box[3] for u, v in px):
                        return None
                if tool == "circle":
                    if len(px) == 2:
                        out = circle_from_two(grid, scale, intr, px[0], px[1],
                                              profile["mode"], self.floor)
                    else:
                        out = circle_from_points(grid, scale, intr, px, self.floor)
                    return None if "error" in out else out
                if tool == "sides":
                    return measure_sides(grid, scale, intr, px, self.floor)
                if tool == "outline" and len(px) >= 3:
                    # The operator's own corners for this kind of object,
                    # re-placed inside this detection's box: the shape is
                    # theirs, the depth under it is this object's, now.
                    us, vs = [p[0] for p in px], [p[1] for p in px]
                    pad = 6
                    region = [max(0, min(us) - pad), max(0, min(vs) - pad),
                              min(gw, max(us) + pad), min(gh, max(vs) + pad)]
                    return measure_box(grid, scale, intr, region, self.floor,
                                       background=self.background,
                                       mask_override=polygon_mask(grid.shape, px),
                                       vertices_override=px,
                                       dims=profile.get("dims") or "2d")
                out = measure_points(grid, scale, intr, px, self.floor)
                out["shape"] = "line" if len(px) == 2 else "polygon"
                return out
            if self.floor is None and self.background is None:
                return None
            return measure_box(grid, scale, intr, box, self.floor,
                               background=self.background,
                               colour=frame,
                               colour_bg=self.colour_bg,
                               dims=profile.get("dims") or "2d")
        except Exception:                                   # noqa: BLE001
            # A measurement that fails must cost this object its numbers
            # and nothing else -- detection is the box's job.
            return None

    @staticmethod
    def _select(profile, result):
        """The numbers this profile publishes: its `fields` or the tool's
        defaults, plus the outline sides it ticked."""
        out = {"shape": result.get("shape", "box")}
        if result.get("dims"):
            out["dims"] = result["dims"]
        want = set(profile["fields"]) or set(FIELDS)
        for field in FIELDS:
            if field in want and result.get(field) is not None:
                out[field] = result[field]
        outline = result.get("outline") or {}
        sides = outline.get("sides_mm") or []
        if sides:
            picked = [i for i in (profile["sides"] or range(len(sides))) if 0 <= i < len(sides)]
            out["sides_mm"] = [sides[i] for i in picked]
            # The outline itself and WHICH of its sides were ticked, so the
            # live picture can draw exactly those sides with their sizes.
            out["outline"] = {"vertices": outline.get("vertices") or [], "sides": picked,
                              "silhouette": outline.get("silhouette") or [],
                              "corner_angles_deg": outline.get("corner_angles_deg") or []}
            if "perimeter_mm" in want and outline.get("perimeter_mm") is not None:
                out["perimeter_mm"] = outline["perimeter_mm"]
        if result.get("segments_mm"):
            # Aligned with `segments` below, None where a tap had no depth --
            # the drawing needs the pairing, the summary can skip the holes.
            out["segments_mm"] = list(result["segments_mm"])
            out["sides_mm"] = [s for s in result["segments_mm"] if s is not None]
        if result.get("segments"):
            out["segments"] = result["segments"]
        if result.get("corners"):
            out["corners"] = result["corners"]
        if result.get("centre") is not None:
            out["centre"] = result["centre"]
            out["radius_px"] = result.get("radius_px")
            out["mode"] = result.get("mode")
            pts = result.get("points") or []
            if len(pts) == 2 and all(isinstance(p, dict) for p in pts):
                # The two taps of a two-tap circle: the span that was measured.
                out["span"] = [[pts[0]["u"], pts[0]["v"]], [pts[1]["u"], pts[1]["v"]]]
        return out

    @staticmethod
    def _to_frame(kept, sx, sy):
        """Scale every pixel coordinate in `kept` from the depth grid to the
        detector's frame (divide by the grid/frame ratio)."""
        def pt(p):
            return [round(float(p[0]) / sx, 1), round(float(p[1]) / sy, 1)]
        if kept.get("corners"):
            kept["corners"] = [pt(p) for p in kept["corners"]]
        if kept.get("centre"):
            kept["centre"] = pt(kept["centre"])
            if kept.get("radius_px"):
                kept["radius_px"] = round(float(kept["radius_px"]) / sx, 1)
        if kept.get("segments"):
            kept["segments"] = [[pt(a), pt(b)] for a, b in kept["segments"]]
        if kept.get("span"):
            kept["span"] = [pt(p) for p in kept["span"]]
        if kept.get("outline"):
            kept["outline"]["vertices"] = [pt(p) for p in kept["outline"]["vertices"]]
            kept["outline"]["silhouette"] = [pt(p) for p in kept["outline"].get("silhouette") or []]


def to_3d(u, v, depth_mm, intr):
    """A pixel with a depth under it, as a point in millimetres.

    Camera frame: X right, Y down, Z away from the lens. The pinhole model,
    which is what the SDK's intrinsics describe.
    """
    return ((u - intr["cx"]) * depth_mm / intr["fx"],
            (v - intr["cy"]) * depth_mm / intr["fy"],
            float(depth_mm))


def points_3d(grid, scale, intr, mask):
    """Every masked pixel as a 3D point, (N, 3) in millimetres."""
    vs, us = np.nonzero(mask)
    z = grid[vs, us].astype(np.float64) * float(scale)
    x = (us - intr["cx"]) * z / intr["fx"]
    y = (vs - intr["cy"]) * z / intr["fy"]
    return np.column_stack([x, y, z])


# -- the floor ---------------------------------------------------------------------

def fit_floor(grid, scale, intr, step=8, iterations=3, keep=0.8):
    """Fit a plane through the depth picture of an EMPTY scene.

    Returns {"normal": [a, b, c], "d": d, "rms_mm": r, "coverage": f} with the
    plane written as a*X + b*Y + c*Z + d = 0 and the normal pointing TOWARD the
    camera, so that distance_above() is positive for things standing on the
    floor. None when there is too little to fit.

    Every `step`-th pixel rather than all of them: a 1280x720 grid is 921,600
    points and the plane through 14,400 of them is the same plane. Then a few
    rounds of drop-the-outliers-and-refit, which is what keeps a chair leg or
    a cable at the edge of the empty scene from tilting the whole floor.
    """
    sub = grid[::step, ::step]
    mask = valid(sub)
    if mask.sum() < 50:
        return None
    # Sub-sampled intrinsics: the principal point and focal lengths scale with
    # the pixel pitch.
    sub_intr = {"fx": intr["fx"] / step, "fy": intr["fy"] / step,
                "cx": intr["cx"] / step, "cy": intr["cy"] / step}
    pts = points_3d(sub, scale, sub_intr, mask)
    normal, d = None, 0.0
    for _ in range(iterations):
        centroid = pts.mean(axis=0)
        # The plane's normal is the direction of least variance: the smallest
        # singular vector of the centred cloud.
        _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
        normal = vt[-1]
        d = -float(np.dot(normal, centroid))
        residual = np.abs(pts @ normal + d)
        cut = np.quantile(residual, keep)
        inliers = pts[residual <= max(cut, 1.0)]
        if len(inliers) < 50:
            break
        pts = inliers
    if normal is None:
        return None
    # Toward the camera (which sits at the origin): the camera must be on the
    # POSITIVE side of the plane, so a point at the origin has positive
    # distance. If it does not, flip.
    if d < 0:
        normal, d = -normal, -d
    residual = np.abs(pts @ normal + d)
    return {"normal": [float(v) for v in normal], "d": d,
            "rms_mm": float(np.sqrt(np.mean(residual ** 2))),
            "coverage": float(mask.mean()),
            # How far the floor is from the camera along the optical axis, at
            # the picture's centre. The number a person checks with a tape.
            "centre_mm": float(-d / normal[2]) if abs(normal[2]) > 1e-6 else None}


def distance_above(floor, pts):
    """Signed height of points above the floor plane, in millimetres.
    Positive = toward the camera = standing on the floor."""
    n = np.asarray(floor["normal"], dtype=np.float64)
    return pts @ n + floor["d"]


# -- the object ----------------------------------------------------------------------

#: How much closer than the remembered background a pixel must read to count
#: as OBJECT under background subtraction. Two depth readings of the same
#: floor differ by a few millimetres frame to frame; a sheet of card does not
#: hide under this, a shadow does.
MIN_BG_DIFF_MM = 15.0


def load_background(pkg_dir, spec):
    """The remembered empty scene, as a uint16 grid, or None.

    `spec` is the camera's measure.background table ({"file": "measure/
    <cam>_background.png", ...}); the file sits under the system's package
    dir, next to its config, so a system exported as a template carries its
    calibration with it. A file that is missing or unreadable is None -- the
    caller falls back to the floor plane, or measures nothing, and says so.
    """
    import os
    if not isinstance(spec, dict) or not spec.get("file"):
        return None
    name = str(spec["file"]).replace("\\", "/")
    if ".." in name or name.startswith("/"):
        return None
    path = os.path.join(str(pkg_dir), name)
    if not os.path.isfile(path):
        return None
    try:
        import cv2
        grid = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    except Exception:                                       # noqa: BLE001
        return None
    if grid is None or grid.ndim != 2 or grid.dtype != np.uint16:
        return None
    return grid


#: How different a pixel's colour must be from the remembered scene to be
#: object, as a distance in Lab with lightness at less than half weight --
#: so a shadow, which changes lightness and little else, is not an object
#: while a grey bottle on a grey bench still is.
COLOUR_DIFF_MIN = 24.0
COLOUR_L_WEIGHT = 0.4


def load_colour_background(pkg_dir, spec):
    """The remembered empty scene in COLOUR (BGR), or None. Saved beside the
    depth background by the calibration (measure.background.rgb_file)."""
    import os
    if not isinstance(spec, dict) or not spec.get("rgb_file"):
        return None
    name = str(spec["rgb_file"]).replace("\\", "/")
    if ".." in name or name.startswith("/"):
        return None
    path = os.path.join(str(pkg_dir), name)
    if not os.path.isfile(path):
        return None
    try:
        import cv2
        img = cv2.imread(path, cv2.IMREAD_COLOR)
    except Exception:                                       # noqa: BLE001
        return None
    return img if img is not None and img.ndim == 3 else None


def colour_mask(frame, colour_bg):
    """Where the picture differs from the remembered empty scene, as a mask
    in the FRAME's pixels. The background is resized to the frame if the two
    were captured at different sizes."""
    import cv2
    if frame is None or colour_bg is None or frame.ndim != 3:
        return None
    if colour_bg.shape[:2] != frame.shape[:2]:
        colour_bg = cv2.resize(colour_bg, (frame.shape[1], frame.shape[0]),
                               interpolation=cv2.INTER_AREA)
    # A MEDIAN, not a Gaussian: it takes the sensor noise off without
    # smearing the edge. A 5px Gaussian halved the colour difference on the
    # object's outermost pixel, the threshold then cut it, and every side
    # came back one colour pixel -- two depth pixels, 5mm at 2m -- short.
    a = cv2.medianBlur(frame, 3)
    b = cv2.medianBlur(colour_bg, 3)
    la = cv2.cvtColor(a, cv2.COLOR_BGR2LAB).astype(np.float32)
    lb = cv2.cvtColor(b, cv2.COLOR_BGR2LAB).astype(np.float32)
    d = la - lb
    dist = np.sqrt((COLOUR_L_WEIGHT * d[..., 0]) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)
    return dist > COLOUR_DIFF_MIN


#: The crop GrabCut works on is capped at this many pixels on its long side,
#: and how many passes it makes. Extraction cost grows with pixels, not with
#: the object: 320px x 3 passes measured 270ms per object on the dev box,
#: 200px x 2 passes a third of that, and the silhouette moved by under a
#: grid pixel. The live loop pays this per measured object, so cheap wins.
EXTRACT_MAX_SIDE = 200
EXTRACT_PASSES = 2


#: The panel's Auto is one operator waiting for one answer, so it can afford
#: a bigger crop and more passes than the live loop: the silhouette is what
#: they are looking at.
EXTRACT_BEST_MAX_SIDE = 400
EXTRACT_BEST_PASSES = 5


def extract_object(frame, box, grid_shape, depth_prior=None, colour_bg=None,
                   iterations=EXTRACT_PASSES, max_side=EXTRACT_MAX_SIDE, inner=None):
    """The object's own silhouette inside `box` (GRID pixels), by foreground
    extraction (OpenCV GrabCut), as a mask the size of that box. None when
    nothing usable comes out.

    OBJECT EXTRACTION, not differencing: GrabCut models the colours inside
    the box as object-or-background and cuts along the edge where they
    change, so it follows the object's real outline -- the bottle's curve,
    the box's corner -- whatever the scene behind it. It is seeded rather
    than left to guess: the crop's rim is background for certain; the
    middle of the DETECTION box (`inner`, or `box` itself) is probably
    object; wherever depth says the object stands (`depth_prior`, a mask
    the size of `box`) is probably object, and its eroded core certainly
    so; where a remembered colour scene still shows through is probably
    background.

    `box` may be LARGER than the detection (`inner`): a detector's box is
    often a few pixels short on one side, and an extraction confined to it
    came back with that side sheared off. The caller pads the box; the
    silhouette is free to reach into the padding.
    """
    import cv2
    if frame is None or frame.ndim != 3:
        return None
    fh, fw = frame.shape[:2]
    gh, gw = grid_shape[:2]
    sx, sy = fw / float(gw), fh / float(gh)
    x1, y1, x2, y2 = box
    ix1, iy1, ix2, iy2 = inner if inner is not None else box
    # The box, and the detection inside it, in the colour frame's pixels.
    cx1, cy1 = int(math.floor(x1 * sx)), int(math.floor(y1 * sy))
    cx2, cy2 = int(math.ceil(x2 * sx)), int(math.ceil(y2 * sy))
    if cx2 - cx1 < 8 or cy2 - cy1 < 8:
        return None
    # A margin of scene round the box, so the cut has background to learn.
    m = int(0.1 * max(cx2 - cx1, cy2 - cy1)) + 4
    ex1, ey1 = max(0, cx1 - m), max(0, cy1 - m)
    ex2, ey2 = min(fw, cx2 + m), min(fh, cy2 + m)
    crop = frame[ey1:ey2, ex1:ex2]
    if crop.shape[0] < 8 or crop.shape[1] < 8:
        return None
    k = min(1.0, float(max_side) / float(max(crop.shape[:2])))
    if k < 1.0:
        crop = cv2.resize(crop, (max(8, int(crop.shape[1] * k)), max(8, int(crop.shape[0] * k))),
                          interpolation=cv2.INTER_AREA)
    ch, cw = crop.shape[:2]

    def in_crop(gx1, gy1, gx2, gy2):
        """A grid rectangle as a rectangle of the (scaled) crop."""
        a = max(0, int((math.floor(gx1 * sx) - ex1) * k))
        b = max(0, int((math.floor(gy1 * sy) - ey1) * k))
        c = min(cw, int(math.ceil((math.ceil(gx2 * sx) - ex1) * k)))
        d = min(ch, int(math.ceil((math.ceil(gy2 * sy) - ey1) * k)))
        return a, b, c, d
    bx1, by1, bx2, by2 = in_crop(x1, y1, x2, y2)
    rx1, ry1, rx2, ry2 = in_crop(ix1, iy1, ix2, iy2)
    if bx2 - bx1 < 4 or by2 - by1 < 4 or rx2 - rx1 < 4 or ry2 - ry1 < 4:
        return None
    # Certain background only at the crop's rim; everything else is merely
    # PROBABLY background until something says otherwise, so an object the
    # detection clipped can still be given its edge back.
    gc = np.full((ch, cw), cv2.GC_PR_BGD, np.uint8)
    rim = max(2, int(0.3 * max(1, m * k)))
    gc[:rim, :] = cv2.GC_BGD
    gc[-rim:, :] = cv2.GC_BGD
    gc[:, :rim] = cv2.GC_BGD
    gc[:, -rim:] = cv2.GC_BGD
    # The middle of the detection is probably the object: a detector's box
    # is drawn round the thing, not beside it.
    ix, iy = int(0.25 * (rx2 - rx1)), int(0.25 * (ry2 - ry1))
    gc[ry1 + iy:ry2 - iy, rx1 + ix:rx2 - ix] = cv2.GC_PR_FGD
    if colour_bg is not None:
        same = colour_mask(frame, colour_bg)
        if same is not None:
            same = ~same[ey1:ey2, ex1:ex2]
            if k < 1.0:
                same = cv2.resize(same.astype(np.uint8), (cw, ch),
                                  interpolation=cv2.INTER_NEAREST).astype(bool)
            # Still looks like the empty scene: probably background, even in
            # the middle of the box. Only "probably" -- a grey bottle on a
            # grey bench looks like the scene too, and depth outranks this.
            inside = np.zeros((ch, cw), bool)
            inside[by1:by2, bx1:bx2] = True
            gc[same & inside] = cv2.GC_PR_BGD
    if depth_prior is not None and depth_prior.any():
        sure = cv2.resize(depth_prior.astype(np.uint8), (bx2 - bx1, by2 - by1),
                          interpolation=cv2.INTER_NEAREST)
        region = gc[by1:by2, bx1:bx2]
        # Where depth sees the object at all: probably object. Eroded, so
        # the depth skirt is not SWORN to be object: certainly object.
        region[sure.astype(bool)] = cv2.GC_PR_FGD
        sure = cv2.erode(sure, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        region[sure.astype(bool)] = cv2.GC_FGD
    if not ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).any():
        return None
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(crop, gc, None, bgd, fgd, int(iterations), cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return None
    fg = (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)
    fg = _smooth_mask(_clean_mask(fg))[by1:by2, bx1:bx2]
    blob = _largest_blob(fg)
    if blob is None:
        return None
    out = cv2.resize(blob.astype(np.uint8), (max(1, x2 - x1), max(1, y2 - y1)),
                     interpolation=cv2.INTER_NEAREST)
    return out.astype(bool)


def _smooth_mask(mask):
    """Take the pixel stairs off a silhouette's edge without moving it: a
    median filter, then the largest region filled. What the eye reads as
    'the object's outline' rather than 'a mask'."""
    import cv2
    m = mask.astype(np.uint8) * 255
    if min(m.shape[:2]) >= 7:
        m = cv2.medianBlur(m, 5)
    return m > 127


def _colour_object(frame, colour_bg, box, grid_shape):
    """The object inside `box` (GRID pixels) as a mask the size of that box,
    found in colour and scaled to the depth grid. None when colour finds
    nothing there worth the name.

    Colour is where the edge is sharp. A depth edge is a skirt of in-between
    readings; a colour edge is the object's own silhouette against the
    remembered scene, to the pixel. So the OUTLINE comes from here, and
    depth is asked only what colour cannot know: how far away it all is.
    """
    import cv2
    mask = colour_mask(frame, colour_bg)
    if mask is None:
        return None
    fh, fw = mask.shape[:2]
    gh, gw = grid_shape[:2]
    x1, y1, x2, y2 = box
    # The box in the colour frame's pixels.
    cx1, cx2 = int(round(x1 * fw / gw)), int(round(x2 * fw / gw))
    cy1, cy2 = int(round(y1 * fh / gh)), int(round(y2 * fh / gh))
    cx1, cx2 = max(0, cx1), min(fw, max(cx1 + 1, cx2))
    cy1, cy2 = max(0, cy1), min(fh, max(cy1 + 1, cy2))
    patch = _clean_mask(mask[cy1:cy2, cx1:cx2])
    blob = _largest_blob(patch)
    if blob is None:
        return None
    # Back to the grid's size for this box, nearest-neighbour so the mask
    # stays a mask.
    out = cv2.resize(blob.astype(np.uint8), (max(1, x2 - x1), max(1, y2 - y1)),
                     interpolation=cv2.INTER_NEAREST)
    return out.astype(bool)


def _height_map_bg(grid, scale, background, box):
    """How far IN FRONT of the remembered empty scene every pixel in `box`
    reads, mm, NaN where either picture has no reading.

    Background subtraction: the calibration remembered what the camera saw
    with nothing in the scene, pixel by pixel. Anything now reading CLOSER
    than that is an object -- whatever the background is: a floor, a bench
    with fixtures on it, a wall behind a conveyor. No plane has to be
    fitted, so it works with the camera looking across a room as well as
    down at a floor.
    """
    x1, y1, x2, y2 = box
    patch = grid[y1:y2, x1:x2]
    ref = background[y1:y2, x1:x2]
    mask = valid(patch) & valid(ref)
    heights = np.full(patch.shape, np.nan, dtype=np.float64)
    if mask.any():
        heights[mask] = (ref[mask].astype(np.float64)
                         - patch[mask].astype(np.float64)) * float(scale)
    return heights, mask


def _height_map(grid, scale, intr, floor, box):
    """Height above the floor for every pixel in `box`, NaN where unmeasured."""
    x1, y1, x2, y2 = box
    patch = grid[y1:y2, x1:x2]
    mask = valid(patch)
    heights = np.full(patch.shape, np.nan, dtype=np.float64)
    if not mask.any():
        return heights, mask
    vs, us = np.nonzero(mask)
    z = patch[vs, us].astype(np.float64) * float(scale)
    x = (us + x1 - intr["cx"]) * z / intr["fx"]
    y = (vs + y1 - intr["cy"]) * z / intr["fy"]
    heights[vs, us] = distance_above(floor, np.column_stack([x, y, z]))
    return heights, mask


def polygon_mask(shape, vertices):
    """A filled polygon as a bool mask of `shape`, from (u, v) grid pixels."""
    import cv2
    m = np.zeros(shape[:2], np.uint8)
    pts = np.array([[int(round(u)), int(round(v))] for u, v in vertices], np.int32)
    if len(pts) >= 3:
        cv2.fillPoly(m, [pts.reshape(-1, 1, 2)], 1)
    return m.astype(bool)


def _edge_depth(patch_mm, above, heights, fallback):
    """The depth to convert the SILHOUETTE at: the median depth of the object
    pixels in a band just inside its edge.

    The nearest surface is the wrong distance for a silhouette. A bottle's
    outline is made of its tangent points, which sit at the bottle's centre
    distance, not at its nearest surface; converting the outline's pixels at
    the nearest depth read a 90mm bottle as 82 (90 x 244/289). The band a
    few pixels inside the edge IS the tangent region for a round thing, and
    for a flat-topped box it is simply the top, so the same rule serves both.
    """
    import cv2
    m = above.astype(np.uint8)
    # Two to six pixels inside the edge. Closer than two is the stereo
    # skirt, whose depth is a mix of object and background; further than
    # six is the top of a small object. The MEDIAN of that ring: on a flat
    # top it is the top; on a round thing it is most of the way to the
    # tangent -- a 90mm bottle at 0.6m reads about 87 rather than 82.
    inner = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    deep = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    band = inner.astype(bool) & ~deep.astype(bool) & np.isfinite(heights) & (patch_mm > 0)
    if band.sum() < 20:
        return fallback
    return float(np.median(patch_mm[band]))


def measure_box(grid, scale, intr, box, floor, min_height_mm=MIN_HEIGHT_MM,
                background=None, colour=None, colour_bg=None, quality="fast",
                dims="2d", mask_override=None, vertices_override=None):
    """Length, width, height, distance and angle of the object in a box.

    Returns a dict, or None when the box holds no measurable object. Lengths
    are millimetres; `angle_deg` is the footprint's rotation in the picture,
    0 meaning the long side runs left-to-right; `corners` are the footprint's
    four corners in picture pixels, for drawing.

    The footprint is measured at the object's TOP, using the top's own depth:
    that is the surface the camera actually sees, and for a box it is the
    same size as the base. A tapered object reads its top -- which is the
    honest answer, since the base is hidden.
    """
    import cv2
    x1, y1, x2, y2 = (int(round(v)) for v in box[:4])
    h, w = grid.shape[:2]
    x1, x2 = max(0, min(x1, x2)), min(w, max(x1, x2))
    y1, y2 = max(0, min(y1, y2)), min(h, max(y1, y2))
    if background is not None and background.shape[:2] != grid.shape[:2]:
        background = None               # a different camera mode: not this scene
    if x2 - x1 < 4 or y2 - y1 < 4 or (floor is None and background is None):
        return None
    # With a colour picture to cut from, the region is PADDED a tenth round
    # the detection: a detector's box is often a few pixels short on one
    # side, and a silhouette confined to it came back with that side sheared
    # off. The detection itself is still what seeds the cut (`inner`).
    inner = (x1, y1, x2, y2)
    if colour is not None:
        pad = int(0.1 * max(x2 - x1, y2 - y1)) + 2
        x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
        x2, y2 = min(w, x2 + pad), min(h, y2 + pad)
    # Background subtraction when the empty scene was remembered -- it needs
    # no flat floor and no plane fit; the floor plane otherwise.
    if background is not None:
        heights, mask = _height_map_bg(grid, scale, background, (x1, y1, x2, y2))
        above = np.nan_to_num(heights, nan=-1.0) > float(MIN_BG_DIFF_MM)
    else:
        heights, mask = _height_map(grid, scale, intr, floor, (x1, y1, x2, y2))
        above = np.nan_to_num(heights, nan=-1.0) > float(min_height_mm)
    if mask_override is not None:
        # The operator's own corners: the shape is theirs, only the depth
        # under it is measured. Whatever stands there counts, however thin.
        above = mask_override[y1:y2, x1:x2].astype(bool)
        if not above.any():
            return None
    elif above.sum() < MIN_OBJECT_FRACTION * above.size:
        return None
    # The object is the largest connected BLOB of pixels above the floor, not
    # every such pixel. Real depth carries a few millimetres of noise, and
    # over forty thousand pixels a handful of floor pixels will cross the
    # threshold on their own -- and a rotated rectangle drawn round the
    # object PLUS three specks in the corner of the box is the size of the
    # box. Measured on the synthetic scene: 357mm for a 250mm side.
    above = above if mask_override is not None else _largest_blob(above)
    if above is None:
        return None
    # THE OUTLINE, TIGHTENED. Stereo depth does not stop dead at an object's
    # edge: a skirt of in-between readings a few pixels wide runs round it,
    # part object, part background, and at 15mm every one of those pixels
    # counts as object -- the outline came back a jagged size too big, with
    # ten sides on a bottle. So the first pass only finds the object; a
    # second pass keeps the pixels that stand at least a third of the way
    # up to its top, which is the object's own surface and not the skirt,
    # then smooths the mask and fills its holes. The refined mask is trusted
    # when it still holds most of the object -- a thin flat thing would lose
    # too much to a relative cut and keeps its first-pass shape.
    first_heights = heights[above]
    first_top = float(np.quantile(first_heights, 0.9))
    if first_top > 0 and mask_override is None:
        tight = np.nan_to_num(heights, nan=-1.0) > max(float(MIN_BG_DIFF_MM), 0.33 * first_top)
        tight = _largest_blob(_clean_mask(tight))
        if tight is not None and tight.sum() >= 0.5 * above.sum():
            above = tight
    # THE OUTLINE FROM COLOUR, when the empty scene was remembered in colour
    # too. The colour silhouette replaces the depth blob as the object's
    # shape -- provided depth agrees that something stands there: at least a
    # tenth of the colour blob must read in front of the scene, or the
    # colour change is a shadow, a reflection, a moved cable.
    depth_says = np.nan_to_num(heights, nan=-1.0) > float(
        MIN_BG_DIFF_MM if background is not None else min_height_mm)
    if colour is not None and mask_override is None:
        # OBJECT EXTRACTION first: the object's own silhouette, cut from the
        # colour picture round where depth says it stands. Colour
        # differencing against the remembered scene is the fallback when
        # the cut gives nothing (a box too small, a picture too flat).
        best = str(quality) == "best"
        cmask = extract_object(colour, (x1, y1, x2, y2), grid.shape[:2],
                               depth_prior=above, colour_bg=colour_bg, inner=inner,
                               iterations=EXTRACT_BEST_PASSES if best else EXTRACT_PASSES,
                               max_side=EXTRACT_BEST_MAX_SIDE if best else EXTRACT_MAX_SIDE)
        if cmask is None and colour_bg is not None:
            cmask = _colour_object(colour, colour_bg, (x1, y1, x2, y2), grid.shape[:2])
        if cmask is not None and cmask.shape == above.shape:
            agree = cmask & depth_says
            if agree.sum() >= 0.1 * cmask.sum() and agree.sum() >= 0.3 * above.sum():
                above = cmask
    # Pixels of the object that DEPTH measured: the colour silhouette can
    # include pixels with no depth reading (a dark rim, a shiny cap) and
    # they must not vote on how tall or how far.
    solid = above & depth_says
    if not solid.any():
        solid = above & np.isfinite(heights)
    if not solid.any():
        return None
    # The object's top: the tallest slice of what stands above the floor. The
    # median of the upper part rather than the maximum, because the maximum
    # is one bad stereo match away from being a spike.
    obj_heights = heights[solid]
    top_h = float(np.quantile(obj_heights, 0.9))
    height_mm = float(np.median(obj_heights[obj_heights >= top_h * 0.8])) if top_h > 0 else 0.0
    # Depth of the top surface, for the pixel-to-mm conversion of the
    # footprint. Same robust slice.
    patch = grid[y1:y2, x1:x2].astype(np.float64) * float(scale)
    top_z = float(np.median(patch[solid & (np.nan_to_num(heights, nan=-1.0) >= top_h * 0.8)]))
    if not np.isfinite(top_z) or top_z <= 0:
        return None
    # The footprint: the tightest rectangle round the object pixels, at any
    # angle. cv2 wants 8-bit and (x, y) points.
    ys, xs = np.nonzero(above)
    pts = np.column_stack([xs + x1, ys + y1]).astype(np.float32)
    (cx, cy), (rw, rh), angle = cv2.minAreaRect(pts)
    # Pixels at the top's depth -> millimetres. fx and fy differ slightly, so
    # the rectangle's two sides are converted along their own axes: project
    # each side onto x and y and scale those separately.
    rad = math.radians(angle)
    # Converted at the EDGE depth (see _edge_depth), not the nearest surface.
    edge_z = _edge_depth(patch, above, heights, top_z)
    side_a = _side_mm(rw, rad, edge_z, intr)
    side_b = _side_mm(rh, rad + math.pi / 2, edge_z, intr)
    length_mm, width_mm = max(side_a, side_b), min(side_a, side_b)
    # Angle reported for the LONG side, 0 = left to right, in (-90, 90].
    long_angle = angle if side_a >= side_b else angle + 90.0
    while long_angle > 90.0:
        long_angle -= 180.0
    while long_angle <= -90.0:
        long_angle += 180.0
    corners = cv2.boxPoints(((cx, cy), (rw, rh), angle))
    out = {
        "shape": "box",
        "length_mm": round(length_mm, 1),
        "width_mm": round(width_mm, 1),
        "height_mm": round(height_mm, 1),
        "distance_mm": round(top_z, 1),
        "edge_distance_mm": round(edge_z, 1),
        "angle_deg": round(float(long_angle), 1),
        "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in corners],
        "coverage": round(float(above.mean()), 3),
        "area_mm2": round(length_mm * width_mm, 0),
        "dims": "3d" if str(dims).lower() == "3d" else "2d",
    }
    # 3D: the object's own points, not its picture. The top surface's points
    # are fitted with a plane (SVD); length and width are the extents along
    # its two in-plane principal axes, so a top that is tilted to the camera
    # -- a box on a slope, a bottle leaning -- reads its true size rather
    # than its foreshortened picture; the plane's normal gives the tilt; the
    # bounding volume follows. In 2D the picture at one distance is the
    # answer, which is what a flat thing, or a top-down camera, wants.
    if out["dims"] == "3d":
        full = np.zeros(grid.shape[:2], bool)
        full[y1:y2, x1:x2] = solid & (np.nan_to_num(heights, nan=-1.0) >= top_h * 0.8)
        top_pts = points_3d(grid, scale, intr, full)
        # The plane comes from the TOP slice (flat on a box, the crest of a
        # bottle); the extents from EVERY object point projected onto it. A
        # bottle's top slice is a narrow strip along its axis and would read
        # the width of that strip; its whole visible surface spans the full
        # diameter, tangent to tangent.
        full_all = np.zeros(grid.shape[:2], bool)
        full_all[y1:y2, x1:x2] = solid
        all_pts = points_3d(grid, scale, intr, full_all)
        if len(top_pts) >= 20 and len(all_pts) >= 20:
            centre3 = top_pts.mean(axis=0)
            _u, _s, vt = np.linalg.svd(top_pts - centre3, full_matrices=False)
            in_plane = (all_pts - centre3) @ vt[:2].T
            lo, hi = np.percentile(in_plane, 1, axis=0), np.percentile(in_plane, 99, axis=0)
            ext = np.sort(hi - lo)[::-1]
            normal = vt[2] if vt[2][2] < 0 else -vt[2]         # towards the camera
            # Tilt against the floor when there is one, the camera's axis
            # otherwise: a flat top on a level floor is 0 either way.
            ref = np.asarray(floor["normal"], dtype=np.float64) if floor else np.array([0.0, 0.0, -1.0])
            ref = ref if ref[2] < 0 else -ref
            cos_t = abs(float(np.dot(normal, ref)) / (np.linalg.norm(normal) * np.linalg.norm(ref) or 1.0))
            out.update({
                "length_mm": round(float(ext[0]), 1),
                "width_mm": round(float(ext[1]), 1),
                "area_mm2": round(float(ext[0] * ext[1]), 0),
                "tilt_deg": round(math.degrees(math.acos(min(1.0, cos_t))), 1),
                "volume_cm3": round(float(ext[0] * ext[1] * height_mm) / 1000.0, 1),
                "points_3d": int(len(top_pts)),
            })
    # Is it ROUND? A disc fills about 78.5% (pi/4) of its bounding square; a
    # box fills all of it. Judged by how much of the rotated rectangle the
    # object pixels actually cover -- a round thing leaves the corners empty,
    # a box does not. Under the threshold the rectangle's numbers are
    # replaced by the circle's, because "length 300, width 300" of a 300mm
    # disc is true and useless, while "diameter 300" is what a person wants.
    # EVERY side, not just the two of the tightest rectangle. The object's
    # outline as a polygon, each side in millimetres, so the operator can
    # tick the sides they care about rather than being handed length and
    # width of a shape that may have six. A box comes back with four; a disc
    # with many short ones, which is why the circle check below still runs.
    # Sides: in 3D each corner is converted at its own depth (a true 3D
    # length whatever the tilt); in 2D every corner sits at the top's one
    # distance, a length in the plane of the picture.
    out["outline"] = _outline(above, x1, y1, edge_z, intr,
                              grid=grid if out["dims"] == "3d" else None, scale=scale,
                              vertices=vertices_override)
    blob_area_px = float(above.sum())
    rect_area_px = float(max(rw * rh, 1.0))
    fill = blob_area_px / rect_area_px
    if fill < 0.88 and abs(side_a - side_b) < 0.15 * max(side_a, side_b):
        (ccx, ccy), radius_px = cv2.minEnclosingCircle(pts)
        # Radius in mm along both axes, averaged: fx and fy differ slightly.
        radius_mm = (radius_px * edge_z / intr["fx"]
                     + radius_px * edge_z / intr["fy"]) / 2.0
        out.update({
            "shape": "circle",
            "diameter_mm": round(2 * radius_mm, 1),
            "radius_mm": round(radius_mm, 1),
            "centre": [round(float(ccx), 1), round(float(ccy), 1)],
            "radius_px": round(float(radius_px), 1),
            "area_mm2": round(math.pi * radius_mm ** 2, 0),
        })
    return out


#: A side shorter than this fraction of the outline's perimeter is a stair
#: left by the depth edge, not a side of the object. Merged into its
#: neighbours -- a box has four sides, not four plus six stairs.
MIN_SIDE_FRACTION = 0.05


def _merge_short_sides(poly, min_len):
    """Drop vertices until no side is shorter than `min_len`, or three are
    left. Each step removes the endpoint of the shortest side whose removal
    changes the polygon's area least, so the shape keeps its corners."""
    pts = [tuple(map(float, p)) for p in poly]

    def area(ps):
        return 0.5 * abs(sum(ps[i][0] * ps[(i + 1) % len(ps)][1]
                             - ps[(i + 1) % len(ps)][0] * ps[i][1] for i in range(len(ps))))
    while len(pts) > 3:
        n = len(pts)
        sides = [math.dist(pts[i], pts[(i + 1) % n]) for i in range(n)]
        k = int(np.argmin(sides))
        if sides[k] >= min_len:
            break
        base = area(pts)
        best = None
        for idx in (k, (k + 1) % n):
            trial = pts[:idx] + pts[idx + 1:]
            delta = abs(area(trial) - base)
            if best is None or delta < best[0]:
                best = (delta, idx)
        pts.pop(best[1])
    return np.array(pts, dtype=np.float64)


def _vertex_depth(grid, scale, u, v, towards, top_z, inset_px=6):
    """The depth to convert an outline vertex at: read a little INSIDE the
    object (towards its centre), because the edge pixel itself is the skirt.
    Falls back to the top's depth when the reading is missing or absurd.

    This is what keeps a side honest when the object is not flat to the
    camera -- a box on its corner, a bottle leaning -- each corner is
    converted at its own distance rather than at one distance for all.
    """
    if grid is None:
        return top_z
    dx, dy = towards[0] - u, towards[1] - v
    n = math.hypot(dx, dy) or 1.0
    z = depth_at_point(grid, scale, u + inset_px * dx / n, v + inset_px * dy / n, radius=2)
    if z is None or not (0.6 * top_z <= z <= 1.4 * top_z):
        return top_z
    return z


def _outline(above, x1, y1, top_z, intr, tolerance=0.02, grid=None, scale=1.0,
             vertices=None):
    """The object's outline as a polygon, with every side in millimetres.

    The blob's contour, simplified until only the real corners are left
    (Douglas-Peucker at ~1.2% of the perimeter: tight enough to keep a
    chamfer, loose enough to drop pixel stairs). Vertices are converted at
    the TOP's depth -- the edge pixels themselves straddle the drop to the
    floor, and a depth read there is one match away from being the floor.

    Returns {"vertices": [[u, v], ...], "sides_mm": [...]} with side i running
    from vertex i to vertex i+1 (wrapping), so a side and its label line up.
    """
    import cv2
    if vertices is not None:
        # The operator's corners, as they set them: measured, not re-found.
        if len(vertices) < 3:
            return None
        poly = np.array([[float(u) - x1, float(v) - y1] for u, v in vertices])
        dense = poly.copy()
    else:
        contours, _ = cv2.findContours(above.astype(np.uint8), cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        perimeter = cv2.arcLength(contour, True)
        poly = cv2.approxPolyDP(contour, tolerance * perimeter, True).reshape(-1, 2)
        if len(poly) < 3:
            return None
        poly = _merge_short_sides(poly, MIN_SIDE_FRACTION * perimeter)
        dense = cv2.approxPolyDP(contour, 0.004 * perimeter, True).reshape(-1, 2)
    # THE SILHOUETTE, dense: the contour simplified only enough to drop the
    # pixel stairs (0.4% of the perimeter), so a curve stays a curve. This
    # is what gets DRAWN -- the object's shape, as an extraction shows it --
    # while the few straight sides below are what gets MEASURED and ticked.
    silhouette = [[round(float(u + x1), 1), round(float(v + y1), 1)] for u, v in dense]
    vertices = [[float(u + x1), float(v + y1)] for u, v in poly]
    centre = (float(np.mean([p[0] for p in vertices])), float(np.mean([p[1] for p in vertices])))
    pts3 = [to_3d(u, v, _vertex_depth(grid, scale, u, v, centre, top_z), intr)
            for u, v in vertices]
    sides = [round(math.dist(pts3[i], pts3[(i + 1) % len(pts3)]), 1)
             for i in range(len(pts3))]
    # The angle AT each corner, between the two sides that meet there --
    # "all sides and angles": a box says 90, 90, 90, 90; a chamfer says 135.
    angles = []
    n = len(pts3)
    for i in range(n):
        a = np.asarray(pts3[i - 1]) - np.asarray(pts3[i])
        b = np.asarray(pts3[(i + 1) % n]) - np.asarray(pts3[i])
        den = float(np.linalg.norm(a) * np.linalg.norm(b))
        angles.append(round(math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b)) / den)))), 1)
                      if den > 0 else None)
    return {"vertices": [[round(u, 1), round(v, 1)] for u, v in vertices],
            "sides_mm": sides,
            "corner_angles_deg": angles,
            "perimeter_mm": round(sum(sides), 1),
            "silhouette": silhouette}


def _clean_mask(mask):
    """Smooth a mask's edge and fill its holes: an opening and a closing with
    a 5px disc, then the largest outer contour filled in. Stereo edges are
    stairs and pinholes; an outline traced round them is stairs and
    pinholes too."""
    import cv2
    m = mask.astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return m.astype(bool)
    filled = np.zeros_like(m)
    cv2.drawContours(filled, [max(contours, key=cv2.contourArea)], -1, 1, thickness=-1)
    return filled.astype(bool)


def _largest_blob(above):
    """The biggest connected region of `above`, as a mask, after a small
    morphological opening has taken the specks off. None if nothing is left.

    Opening first, so a speck touching the object by one diagonal pixel does
    not count as part of it; then the largest component, so two separate
    things in one box measure as the bigger one rather than as their hull.
    """
    import cv2
    mask = above.astype(np.uint8)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return None
    # Label 0 is the background.
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[biggest, cv2.CC_STAT_AREA] < MIN_OBJECT_FRACTION * above.size:
        return None
    return labels == biggest


def _side_mm(pixels, rad, depth, intr):
    """A side of `pixels` length at angle `rad`, in mm at `depth`."""
    dx = abs(math.cos(rad)) * pixels * depth / intr["fx"]
    dy = abs(math.sin(rad)) * pixels * depth / intr["fy"]
    return math.hypot(dx, dy)


# -- a circle from taps on its rim -----------------------------------------------------

def circle_from_points(grid, scale, intr, points, floor=None):
    """Radius, diameter and centre of a circle through three or more taps on
    its rim. Each tap is a real 3D point (pixel + depth), so the circle is
    fitted in the plane those points lie in, at whatever angle the object
    sits -- a pipe end seen obliquely is still a circle here, not an ellipse.

    Three taps define it exactly; more are fitted by least squares, which is
    what lets an operator tap round a slightly out-of-round thing and get its
    mean. Fewer than three, or a tap over an unmeasured pixel, is refused.
    """
    pts = []
    for u, v in points:
        z = depth_at_point(grid, scale, u, v)
        if z is None:
            return {"error": "a tap landed where the camera has no reading"}
        pts.append(to_3d(u, v, z, intr))
    if len(pts) < 3:
        return {"error": "a circle needs three taps on its edge"}
    p = np.asarray(pts, dtype=np.float64)
    centroid = p.mean(axis=0)
    # The plane the rim lies in, then the fit in that plane's 2D coordinates.
    _, _, vt = np.linalg.svd(p - centroid, full_matrices=False)
    ex, ey, normal = vt[0], vt[1], vt[2]
    flat = np.column_stack([(p - centroid) @ ex, (p - centroid) @ ey])
    # Algebraic circle fit: x^2 + y^2 + D x + E y + F = 0, linear in D, E, F.
    a = np.column_stack([flat[:, 0], flat[:, 1], np.ones(len(flat))])
    b = -(flat[:, 0] ** 2 + flat[:, 1] ** 2)
    try:
        (dd, ee, ff), *_ = np.linalg.lstsq(a, b, rcond=None)
    except np.linalg.LinAlgError:
        return {"error": "the taps do not describe a circle"}
    cx2, cy2 = -dd / 2.0, -ee / 2.0
    r2 = cx2 ** 2 + cy2 ** 2 - ff
    if r2 <= 0:
        return {"error": "the taps do not describe a circle"}
    radius = math.sqrt(r2)
    centre = centroid + cx2 * ex + cy2 * ey
    out = {"shape": "circle",
           "radius_mm": round(radius, 1),
           "diameter_mm": round(2 * radius, 1),
           "area_mm2": round(math.pi * radius ** 2, 0),
           "distance_mm": round(float(centre[2]), 1),
           # The centre back in the picture, for drawing.
           "centre": [round(intr["cx"] + intr["fx"] * centre[0] / centre[2], 1),
                      round(intr["cy"] + intr["fy"] * centre[1] / centre[2], 1)],
           # How far the taps sit from the fitted rim: the operator's own
           # error bar. Three taps always fit exactly; this means something
           # from four on.
           "fit_rms_mm": round(float(np.sqrt(np.mean(
               (np.hypot(flat[:, 0] - cx2, flat[:, 1] - cy2) - radius) ** 2))), 2)}
    if floor is not None:
        out["height_mm"] = round(float(distance_above(floor, centre[None, :])[0]), 1)
    return out


def circle_from_two(grid, scale, intr, p1, p2, mode="diameter", floor=None):
    """A circle from two taps, the way a person with a tape would take it.

    `mode` says what the two taps span: "diameter" -- one side of the rim to
    the other, through the middle; or "radius" -- the centre out to the rim.
    Two taps is what an operator has patience for; three on a rim is a
    geometry exercise. The trade is that the operator, not the fit, decides
    where the middle is.
    """
    a = depth_at_point(grid, scale, *p1)
    b = depth_at_point(grid, scale, *p2)
    if a is None or b is None:
        return {"error": "a tap landed where the camera has no reading"}
    pa, pb = to_3d(p1[0], p1[1], a, intr), to_3d(p2[0], p2[1], b, intr)
    span = math.dist(pa, pb)
    if span <= 0:
        return {"error": "the two taps are the same point"}
    if mode == "radius":
        radius = span
        centre = np.asarray(pa)
        centre_px = [float(p1[0]), float(p1[1])]
    else:
        radius = span / 2.0
        centre = (np.asarray(pa) + np.asarray(pb)) / 2.0
        centre_px = [(float(p1[0]) + float(p2[0])) / 2.0,
                     (float(p1[1]) + float(p2[1])) / 2.0]
    z = float(centre[2]) if centre[2] > 0 else (a + b) / 2.0
    out = {"shape": "circle", "mode": mode,
           "radius_mm": round(radius, 1),
           "diameter_mm": round(2 * radius, 1),
           "area_mm2": round(math.pi * radius ** 2, 0),
           "distance_mm": round(z, 1),
           "centre": [round(centre_px[0], 1), round(centre_px[1], 1)],
           # In picture pixels at the centre's depth, for drawing the ring
           # over the footage.
           "radius_px": round(radius * ((intr["fx"] + intr["fy"]) / 2.0) / z, 1),
           "points": [{"u": float(p1[0]), "v": float(p1[1]), "depth_mm": round(a, 1)},
                      {"u": float(p2[0]), "v": float(p2[1]), "depth_mm": round(b, 1)}]}
    if floor is not None:
        out["height_mm"] = round(float(distance_above(floor, centre[None, :])[0]), 1)
    return out


def measure_sides(grid, scale, intr, points, floor=None):
    """Independent sides, two taps each: (p0,p1), (p2,p3), ... Each pair is
    one length, and the pairs share nothing -- so an operator can measure
    the three edges they care about on a six-sided thing and skip the rest.
    An odd tap at the end is a side not yet finished, and is ignored."""
    out = {"shape": "sides", "segments_mm": [], "segments": [], "approx": [], "notes": []}
    for i in range(0, len(points) - 1, 2):
        pair = measure_points(grid, scale, intr, [points[i], points[i + 1]], floor)
        out["segments_mm"].append(pair["segments_mm"][0] if pair["segments_mm"] else None)
        out["approx"].append(bool(pair["approx"] and pair["approx"][0]))
        out["notes"].extend("side {}: {}".format(i // 2 + 1, x) for x in pair.get("notes") or [])
        out["segments"].append([[float(points[i][0]), float(points[i][1])],
                                [float(points[i + 1][0]), float(points[i + 1][1])]])
    return out


# -- manual points -----------------------------------------------------------------

def depth_at_point(grid, scale, u, v, radius=4):
    """Depth under a tapped pixel, in mm, or None where nothing is measured.

    A patch rather than a pixel, because a fingertip is not a pixel and the
    one under it may be unmeasured. And the NEAR side of that patch's
    readings (its lower quartile) rather than its median: a side is tapped
    at the object's edges, and a patch on an edge is half object, half the
    bench behind it. The median of that is a depth nothing is at; the
    lower quartile is the object. On a flat surface the two differ by the
    depth noise, a millimetre or two.
    """
    h, w = grid.shape[:2]
    u, v = int(round(u)), int(round(v))
    # Widening twice when the patch is empty: a stereo hole at a dark edge
    # is a few pixels across, and a fingertip's worth of picture round it
    # usually has a reading. Past 12px we are no longer reading "here".
    for r in (radius, radius * 2, radius * 3):
        patch = grid[max(0, v - r):min(h, v + r + 1), max(0, u - r):min(w, u + r + 1)]
        good = patch[valid(patch)]
        if good.size:
            return float(np.quantile(good, 0.25)) * float(scale)
    return None


def measure_points(grid, scale, intr, points, floor=None):
    """Distances between tapped points, each a real 3D point.

    Two points: one length. Four points, in order round the object: two sides
    (length and width) from adjacent pairs, so the operator taps the corners
    and gets the footprint at whatever angle it lies. Any point without a depth
    under it is reported as such rather than guessed.
    """
    n = len(points)
    if n == 0:
        return {"points": [], "segments_mm": [], "approx": [], "notes": []}
    centre = (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)
    resolved, notes = [], []
    for k, (u, v) in enumerate(points):
        z = depth_at_point(grid, scale, u, v)
        note = None
        if z is None:
            # No depth under the tap -- a stereo hole at a dark edge, or an
            # object nearer than the camera can measure. The tap was put on
            # the object's edge, so the object is INWARD: along the side
            # towards the other tap (two taps), or towards the middle of
            # the shape. Walk up to a fifth of the way and read the first
            # depth found. The DISTANCE is still measured between the taps
            # as placed; only the depth is borrowed from a little inward.
            other = points[1 - k] if n == 2 else centre
            du, dv = other[0] - u, other[1] - v
            for frac in (0.03, 0.06, 0.1, 0.15, 0.2):
                z = depth_at_point(grid, scale, u + du * frac, v + dv * frac)
                if z is not None:
                    note = ("no depth under tap {}; read {:.0f} px along the side"
                            .format(k + 1, math.hypot(du, dv) * frac))
                    break
        resolved.append(None if z is None else to_3d(u, v, z, intr))
        notes.append(note)
    # Still nothing under one end of a two-tap side: a side lies on ONE
    # surface, so the other end's depth is the honest guess -- marked as one.
    if n == 2 and (resolved[0] is None) != (resolved[1] is None):
        k = 0 if resolved[0] is None else 1
        z = resolved[1 - k][2]
        resolved[k] = to_3d(points[k][0], points[k][1], z, intr)
        notes[k] = "no depth under tap {}; used the other end's depth".format(k + 1)
    out = {"points": [], "segments_mm": [], "approx": [],
           "notes": [x for x in notes if x]}
    for (u, v), p, note in zip(points, resolved, notes):
        entry = {"u": u, "v": v, "depth_mm": None if p is None else round(p[2], 1)}
        if note:
            entry["note"] = note
        if p is not None and floor is not None:
            entry["height_mm"] = round(float(distance_above(floor, np.array([p]))[0]), 1)
        out["points"].append(entry)
    pairs = [(i, (i + 1) % n) for i in range(n)] if n >= 3 else [(0, 1)] if n == 2 else []
    for i, j in pairs:
        a, b = resolved[i], resolved[j]
        out["segments_mm"].append(
            None if a is None or b is None
            else round(math.dist(a, b), 1))
        out["approx"].append(bool(notes[i] or notes[j]))
    if n == 4 and all(s is not None for s in out["segments_mm"]):
        s = out["segments_mm"]
        out["length_mm"] = round(max((s[0] + s[2]) / 2, (s[1] + s[3]) / 2), 1)
        out["width_mm"] = round(min((s[0] + s[2]) / 2, (s[1] + s[3]) / 2), 1)
    return out
