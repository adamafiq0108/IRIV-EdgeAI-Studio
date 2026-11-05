# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""Measuring objects with a depth camera, from the master's side.

The maths lives in the system's own app (systems/<slug>/app/measure.py) and
runs there for every detection. This module is the OPERATOR's side of it:
calibrating the floor, trying a measurement by hand, and saving which
numbers each kind of object should carry.

WHY THE MASTER ASKS THE APP FOR DEPTH. The camera's SDK holds the USB device
exclusively. Nothing else on the box can open it while detection runs -- and
detection is running, because that is what the box is for. So a depth picture
is fetched from the running app over its stream port (/depth, one 16-bit PNG
with the scale and lens in headers), never grabbed here.

WHY THE MATHS IS LOADED FROM THE APP. One copy. The app measures at run time
and the master measures when an operator taps; if the two disagreed by a
constant a calibration done here would be wrong there. Loaded the way the
app's runtime module already is (detector_control._load_app_module), by
file path, with the app importing nothing from core/.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from core.services import detector_control as dc

#: How long to wait for the app to hand over one depth picture. The app
#: answers from memory (the grab thread's latest frame), so this is network
#: on loopback plus a PNG encode -- under a second when things work, and a
#: hang otherwise, which is what the bound is for.
DEPTH_TIMEOUT_S = 8

#: A floor fit worse than this is refused as a calibration. The Gemini's own
#: depth noise is a few millimetres at 2m, and a textured floor a few more;
#: 25mm of residual means something in the scene is not floor -- a box left
#: in view, a pallet edge, the camera looking across the room rather than
#: down. Measured on .63 with the camera pointed at a room: 60mm.
MAX_FLOOR_RMS_MM = 25.0

#: ...and at least this much of the picture has to carry a reading, or the
#: plane is fitted through too little of the scene to trust.
MIN_FLOOR_COVERAGE = 0.30

#: A live pixel this far (mm) from the remembered scene counts as changed,
#: and a picture with this much of it changed is a camera that has moved
#: (or a scene rebuilt): an object in view changes a tenth of the picture.
SCENE_DRIFT_MM = 80.0
SCENE_DRIFT_FRACTION = 0.4


#: How many depth pictures the background is the median of. One frame of
#: stereo depth has holes and specks; the per-pixel median of a handful has
#: neither, and a handful is under a second of the operator's time.
BACKGROUND_FRAMES = 6


def _background_path(systems_dir, slug, camera_id, kind="background"):
    import os
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(camera_id or "main"))
    rel = "measure/{}_{}.png".format(safe or "main", kind)
    return rel, os.path.join(dc._pkg_dir(systems_dir, slug), rel)


def fetch_frame(systems_dir, slug, camera_id):
    """The newest colour frame (BGR), or None when the app has none to give.
    Optional by design: the depth scene alone still measures."""
    import numpy as np
    try:
        import cv2
    except ImportError:
        return None
    port = dc.stream_port(systems_dir, slug)
    url = "http://127.0.0.1:{}/frame?camera={}".format(
        int(port), urllib.parse.quote(str(camera_id or "")))
    try:
        with urllib.request.urlopen(url, timeout=DEPTH_TIMEOUT_S) as res:
            body = res.read()
    except (urllib.error.URLError, OSError):
        return None
    img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    return img if img is not None and img.ndim == 3 else None


def capture_background(systems_dir, slug, camera_id, frames=BACKGROUND_FRAMES):
    """The empty scene, remembered: the per-pixel median of a few depth
    pictures, holes filled from whichever frames had a reading. Returns
    (grid_uint16, coverage, scale, intr)."""
    import numpy as np
    stack = []
    scale = intr = None
    for i in range(max(1, int(frames))):
        grid, scale, intr = fetch_depth(systems_dir, slug, camera_id)
        g = grid.astype(np.float64)
        g[(grid == 0) | (grid == 0xFFFF)] = np.nan
        stack.append(g)
        if i + 1 < frames:
            time.sleep(0.12)
    with np.errstate(all="ignore"):
        median = np.nanmedian(np.stack(stack), axis=0)
    good = np.isfinite(median)
    out = np.zeros(median.shape, dtype=np.uint16)
    out[good] = np.clip(np.round(median[good]), 1, 0xFFFE).astype(np.uint16)
    return out, float(good.mean()), scale, intr


def _measure_module(systems_dir, slug):
    module = dc._load_app_module(dc._pkg_dir(systems_dir, slug), "measure")
    if module is None:
        raise RuntimeError("this system's app has no measurement module - "
                           "update the system")
    return module


def _camera(systems_dir, slug, camera_id):
    cams = dc.cameras(systems_dir, slug)
    wanted = str(camera_id or "").strip()
    cam = next((c for c in cams if c.get("id") == wanted), None) if wanted \
        else (cams[0] if cams else None)
    if cam is None:
        raise ValueError("no such camera")
    return cam


def fetch_depth(systems_dir, slug, camera_id):
    """One aligned depth picture from the running app: (grid, scale, intr).

    Raises RuntimeError with the reason in the operator's terms: the app is
    not running, or this camera measures no depth.
    """
    import numpy as np
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python is not installed on this host") from exc
    port = dc.stream_port(systems_dir, slug)
    url = "http://127.0.0.1:{}/depth?camera={}".format(
        int(port), urllib.parse.quote(str(camera_id or "")))
    try:
        with urllib.request.urlopen(url, timeout=DEPTH_TIMEOUT_S) as res:
            scale = float(res.headers.get("X-Depth-Scale") or 1.0)
            intr = json.loads(res.headers.get("X-Depth-Intrinsics") or "{}")
            body = res.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("this camera measures no depth, or is not "
                               "open yet - is detection running?") from exc
        raise RuntimeError("the detector answered {} for a depth picture"
                           .format(exc.code)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("detection is not running on this system - start "
                           "it, then calibrate") from exc
    grid = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_UNCHANGED)
    if grid is None or grid.ndim != 2:
        raise RuntimeError("the depth picture could not be read")
    if not intr or not all(k in intr for k in ("fx", "fy", "cx", "cy")):
        raise RuntimeError("the camera did not report its lens - sizes "
                           "cannot be measured without it")
    return grid, scale, intr


def live_detections(systems_dir, slug, camera_id):
    """What the camera sees right now, boxes normalised. [] when the app is
    not answering -- a missing list must not fail a measurement."""
    port = dc.stream_port(systems_dir, slug)
    url = "http://127.0.0.1:{}/detections?camera={}".format(
        int(port), urllib.parse.quote(str(camera_id or "")))
    try:
        with urllib.request.urlopen(url, timeout=3) as res:
            body = json.loads(res.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return []
    rows = body.get("detections") if isinstance(body, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict) and r.get("box")]


def object_under(points, detections):
    """The detection box (normalised) most of these normalised taps fall in,
    or None when no detected object is under them. This is what a manual
    measurement is anchored to when it is saved as a profile."""
    best, best_n = None, 0
    for det in detections or []:
        box = det.get("box") or []
        if len(box) != 4:
            continue
        n = sum(1 for u, v in points if box[0] <= u <= box[2] and box[1] <= v <= box[3])
        if n > best_n:
            best, best_n = det, n
    return best


def state(systems_dir, slug, camera_id):
    """Where this camera's measurement setup stands, for the panel."""
    cam = _camera(systems_dir, slug, camera_id)
    measure = dict(cam.get("measure") or {})
    out = {"camera": cam, "floor": measure.get("floor"),
           "background": measure.get("background"),
           "profiles": measure.get("profiles") or [],
           "tools": list(dc.MEASURE_TOOLS),
           "is_depth": cam.get("kind") == "depth"}
    # Is depth reachable right now? Asked once, cheaply, so the panel can say
    # "start detection first" before the operator presses Calibrate and
    # finds out the hard way.
    try:
        grid, depth_scale, intr = fetch_depth(systems_dir, slug, cam["id"])
        out["live"] = {"width": int(grid.shape[1]), "height": int(grid.shape[0]),
                       "intrinsics": intr}
        # What is detected right now, boxes normalised: the panel ties a
        # fixed-spot profile to the object under its taps with this.
        out["detections"] = live_detections(systems_dir, slug, cam["id"])
        # Does the picture still match the remembered scene? Auto is
        # background subtraction, and a camera that has been nudged makes
        # the WHOLE picture "changed" -- Auto then measures the wall. Better
        # to say so than to hand back a confident number. An object in view
        # changes a tenth of the picture; a moved camera changes most of it.
        try:
            measure_mod = _measure_module(systems_dir, slug)
            bg = measure_mod.load_background(dc._pkg_dir(systems_dir, slug),
                                             measure.get("background"))
        except RuntimeError:
            bg = None
        if bg is not None and bg.shape == grid.shape:
            import numpy as np
            both = measure_mod.valid(grid) & measure_mod.valid(bg)
            if both.sum() > 1000:
                diff = np.abs(grid[both].astype(np.float64) - bg[both].astype(np.float64)) * float(depth_scale or 1.0)
                drift = float((diff > SCENE_DRIFT_MM).mean())
                out["scene_drift"] = round(drift, 3)
                out["moved"] = drift > SCENE_DRIFT_FRACTION
    except RuntimeError as exc:
        out["live"] = None
        out["live_problem"] = str(exc)
    return out


def calibrate_floor(systems_dir, slug, camera_id, config_dir=None):
    """Fit the floor plane from the scene AS IT IS NOW, and save it.

    The scene must be empty: whatever is in view becomes "floor". A fit that
    does not look like a floor -- too much residual, too little coverage -- is
    refused with the reason, because a bad calibration is worse than none:
    every later height would be measured against the wrong plane and no
    single reading would look wrong.
    """
    import os
    cam = _camera(systems_dir, slug, camera_id)
    if cam.get("kind") != "depth":
        raise ValueError("only a depth camera can be calibrated for measurement")
    measure = _measure_module(systems_dir, slug)
    # THE BACKGROUND FIRST. Auto measurement is background subtraction: what
    # the camera sees now, with nothing in the scene, is remembered pixel by
    # pixel, and an object is whatever later reads closer than that. It
    # needs no flat floor, so it is what makes the tool work with the camera
    # looking across a bench or at a wall, not only down at a floor.
    grid, coverage, scale, intr = capture_background(systems_dir, slug, cam["id"])
    if coverage < MIN_FLOOR_COVERAGE:
        raise ValueError("only {:.0f}% of the picture has a depth reading - "
                         "the scene may be too dark, too shiny, or out of "
                         "range (0.26m to 3m is best)".format(100 * coverage))
    rel, path = _background_path(systems_dir, slug, cam["id"])
    try:
        import cv2
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ok, png = cv2.imencode(".png", grid)
        if not ok:
            raise RuntimeError("encode failed")
        with open(path, "wb") as fh:
            fh.write(png.tobytes())
    except Exception as exc:                                # noqa: BLE001
        raise RuntimeError("could not save the background picture: {}".format(exc)) from exc
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    background = {"file": rel, "at": stamp, "coverage": round(coverage, 3),
                  "width": int(grid.shape[1]), "height": int(grid.shape[0])}
    # THE SCENE IN COLOUR TOO. The outline of an object is sharper in colour
    # than in depth (a depth edge is a skirt of in-between readings), so Auto
    # takes the silhouette from colour subtraction and asks depth only how
    # far away it is. Best effort: a system whose app cannot hand over a
    # frame still calibrates, on depth alone.
    frame = fetch_frame(systems_dir, slug, cam["id"])
    if frame is not None:
        rgb_rel, rgb_path = _background_path(systems_dir, slug, cam["id"], "background_rgb")
        try:
            import cv2
            ok, png = cv2.imencode(".png", frame)
            if ok:
                with open(rgb_path, "wb") as fh:
                    fh.write(png.tobytes())
                background["rgb_file"] = rgb_rel
        except Exception:                                   # noqa: BLE001
            pass
    # THE FLOOR PLANE AS A BONUS. Fitted from the same scene; kept when the
    # scene reads as a flat floor (it makes "height" mean height above the
    # floor and lets the manual tools report it), dropped -- with the reason
    # -- when it does not. Auto works either way.
    floor = measure.fit_floor(grid, scale, intr)
    floor_problem = None
    if floor is None or floor["coverage"] < MIN_FLOOR_COVERAGE:
        floor, floor_problem = None, "too little of the picture to fit a plane"
    elif floor["rms_mm"] > MAX_FLOOR_RMS_MM:
        floor_problem = ("the scene is not a flat floor ({:.0f}mm of scatter) - "
                         "heights are measured against the remembered scene "
                         "instead".format(floor["rms_mm"]))
        floor = None
    else:
        floor["at"] = stamp
    saved = dc.set_camera_measure(systems_dir, slug, cam["id"],
                                  {"background": background, "floor": floor},
                                  config_dir=config_dir)
    saved_measure = saved.get("measure") or {}
    return {"background": saved_measure.get("background"),
            "floor": saved_measure.get("floor"),
            "floor_problem": floor_problem,
            "tilt_deg": _tilt_deg(floor) if floor else None}


def _tilt_deg(floor):
    import math
    n = floor.get("normal") or [0, 0, 1]
    return round(math.degrees(math.acos(max(-1.0, min(1.0, abs(float(n[2])))))), 1)


def run(systems_dir, slug, camera_id, tool, points=None, box=None, mode="", dims="2d"):
    """Measure now, with one tool, on a fresh depth picture.

    `points` are NORMALISED picture coordinates (0..1), because the operator
    tapped on a preview that is not the depth grid's size -- and a tap
    carried as pixels of the wrong picture is the top-left-quarter bug all
    over again. `box`, for auto, is normalised the same way; None means the
    whole picture.
    """
    tool = str(tool or "auto").strip().lower()
    if tool not in dc.MEASURE_TOOLS:
        raise ValueError("unknown tool {!r}".format(tool))
    cam = _camera(systems_dir, slug, camera_id)
    floor = (cam.get("measure") or {}).get("floor")
    measure = _measure_module(systems_dir, slug)
    background = measure.load_background(dc._pkg_dir(systems_dir, slug),
                                         (cam.get("measure") or {}).get("background"))
    colour_bg = measure.load_colour_background(dc._pkg_dir(systems_dir, slug),
                                               (cam.get("measure") or {}).get("background"))
    grid, scale, intr = fetch_depth(systems_dir, slug, cam["id"])
    colour = fetch_frame(systems_dir, slug, cam["id"])
    h, w = grid.shape[:2]

    def px(pt):
        return (float(pt[0]) * w, float(pt[1]) * h)

    if tool == "auto":
        if floor is None and background is None:
            raise ValueError("calibrate first - auto measurement finds the "
                             "object as what stands in front of the remembered "
                             "empty scene")
        region = ([box[0] * w, box[1] * h, box[2] * w, box[3] * h]
                  if box else [0, 0, w, h])
        result = measure.measure_box(grid, scale, intr, region, floor,
                                     background=background,
                                     colour=colour, colour_bg=colour_bg,
                                     quality="best",
                                     dims="3d" if str(dims).lower() == "3d" else "2d")
        if result is None:
            raise ValueError("nothing stands out from the remembered scene here "
                             "by more than {:.0f}mm"
                             .format(measure.MIN_BG_DIFF_MM if background is not None
                                     else measure.MIN_HEIGHT_MM))
    elif tool == "circle":
        pts = [px(p) for p in (points or [])]
        # Two taps: the operator says what they span (diameter or radius).
        # Three or more: fitted through them. Both are honest circles; the
        # two-tap one is what somebody with a tape would take.
        if len(pts) == 2:
            result = measure.circle_from_two(
                grid, scale, intr, pts[0], pts[1],
                "radius" if str(mode).lower() == "radius" else "diameter", floor)
        else:
            result = measure.circle_from_points(grid, scale, intr, pts, floor)
        if "error" in result:
            raise ValueError(result["error"])
    elif tool == "sides":
        pts = [px(p) for p in (points or [])]
        if len(pts) < 2:
            raise ValueError("tap two points for a side")
        result = measure.measure_sides(grid, scale, intr, pts, floor)
    elif tool == "outline":
        # The operator moved the corners Auto found (or set their own): the
        # SHAPE is theirs, and only the depth under it is measured -- every
        # side, every corner angle, length and width, in 2D or 3D as asked.
        pts = [px(p) for p in (points or [])]
        if len(pts) < 3:
            raise ValueError("an outline needs at least three corners")
        us, vs = [p[0] for p in pts], [p[1] for p in pts]
        pad = 6
        region = [max(0, min(us) - pad), max(0, min(vs) - pad),
                  min(w, max(us) + pad), min(h, max(vs) + pad)]
        result = measure.measure_box(grid, scale, intr, region, floor,
                                     background=background,
                                     dims="3d" if str(dims).lower() == "3d" else "2d",
                                     mask_override=measure.polygon_mask(grid.shape, pts),
                                     vertices_override=pts)
        if result is None:
            raise ValueError("no depth under these corners to measure")
    else:                                   # line, polygon
        pts = [px(p) for p in (points or [])]
        if len(pts) < 2:
            raise ValueError("tap at least two points")
        result = measure.measure_points(grid, scale, intr, pts, floor)
        result["shape"] = "line" if len(pts) == 2 else "polygon"
    # Picture-space geometry back to the panel as NORMALISED coordinates too,
    # for the same reason the taps came in that way.
    result["frame"] = {"width": w, "height": h}
    if tool != "auto" and points:
        # Which detected object the taps are on, so the panel can save the
        # measurement as a profile that FOLLOWS that class of object rather
        # than a fixed spot in the picture.
        under = object_under(points, live_detections(systems_dir, slug, cam["id"]))
        result["object"] = ({"label": under.get("label"), "box": under.get("box")}
                            if under else None)
    return result


def set_profiles(systems_dir, slug, camera_id, profiles, config_dir=None):
    """Save which numbers each kind of object carries. The list replaces the
    old one whole -- it is edited as a list on the panel."""
    if not isinstance(profiles, list):
        raise ValueError("profiles must be a list")
    if len(profiles) > dc.MAX_MEASURE_PROFILES:
        raise ValueError("at most {} measurement profiles per camera"
                         .format(dc.MAX_MEASURE_PROFILES))
    cam = _camera(systems_dir, slug, camera_id)
    saved = dc.set_camera_measure(systems_dir, slug, cam["id"],
                                  {"profiles": profiles}, config_dir=config_dir)
    return (saved.get("measure") or {}).get("profiles") or []


def clear_floor(systems_dir, slug, camera_id, config_dir=None):
    """Forget the calibration: the remembered scene and the floor plane."""
    import os
    cam = _camera(systems_dir, slug, camera_id)
    dc.set_camera_measure(systems_dir, slug, cam["id"],
                          {"floor": None, "background": None}, config_dir=config_dir)
    for kind in ("background", "background_rgb"):
        _rel, path = _background_path(systems_dir, slug, cam["id"], kind)
        try:
            os.remove(path)
        except OSError:
            pass
    return True
