# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
preprocess.py -- OpenCV work on the frame BEFORE the detector sees it.

THIS IS NOT THE CAMERA'S EXPOSURE. core/services/camera_controls.py drives the
real thing: V4L2 controls on the device, where "exposure" means the sensor's
integration time and "sharpness" means the ISP's own filter. Those are better
whenever the camera has them -- they cost nothing per frame and they recover
detail the sensor would otherwise never capture. But they exist only on a UVC
device that reports them: an RTSP camera answers to its own web page and not
to us, an Orbbec is held exclusively by its SDK, and plenty of USB modules
report brightness and nothing else. This module is the SOFTWARE counterpart
for exactly those cases -- arithmetic on pixels that already arrived. It works
on every source, it can never recover what the sensor did not record, and the
two must not be confused in the UI: raising `gain` here is stretching a dark
picture, not opening the shutter.

WHY BEFORE THE DETECTOR. A model trained on ordinary photographs can miss an
object in a frame a human still reads -- a dim loading bay at night, a washed
out roller door at noon, a camera whose focus drifted. Gamma, an unsharp mask
or an edge map sometimes moves such a scene back into the range the model was
trained for. It is a tuning knob for a site, not a general improvement: every
op costs milliseconds on every frame of every camera, and most sites should
have an empty list here.

A MASK IS NOT THE ZONE FILTER, and an operator may well want both. The Zone
Filter drops DETECTIONS after inference, by where they landed: the detector
still looked at the whole frame, still spent the time, and can still hallucinate
something outside the zone that the filter then has to throw away. The `mask`
op here hides PIXELS before inference: the detector never sees outside the
region, so it cannot report anything there and does not pay for it. One is a
rule about results, the other is a rule about the picture.

ORDER IS THE OPERATOR'S. The steps run in the order they are given and are
never reordered or merged -- "sharpen then blur" and "blur then sharpen" are
different pictures, and an optimiser that "knew better" would silently undo a
setting somebody tuned by eye against the live preview.

THREE CHANNELS OUT, ALWAYS. grayscale and sobel each produce one channel, and
a detector fed a (H, W) array either raises or silently reads it as something
it is not -- hailo.py, detector.py and the Gstreamer path all expect BGR, as
does everything downstream that paints on the frame. So a single-channel
result is converted back to 3-channel BGR at the END of the chain (once, not
after every step): the picture stays grey, the SHAPE stays what every consumer
of a frame in this app already knows how to handle.

NOTHING HERE MAY KILL THE FRAME LOOP. Every parameter is clamped to a sane
range and a step that is still unusable is SKIPPED with a warning through the
app's faults logger, leaving the frame as it was. A bad number in a config
file must cost a warning and a plain picture, never a traceback out of the
capture loop -- that loop is the whole system.

WHAT IT COSTS. Measured on the dev box (Windows 11, cv2 4.13, 8 threads,
1280x720 BGR, best of three medians of 150 frames -- the whole apply() call,
including the convert back to BGR):

    grayscale                     0.75 ms
    exposure (gain+gamma, LUT)    0.85 ms
    sharpness (amount 1.0, k 5)   2.37 ms
    blur gaussian k 5             0.86 ms
    blur gaussian k 31            4.67 ms
    blur box k 5                  4.08 ms   (yes, slower than gaussian here)
    sobel k 3, dx and dy          4.19 ms
    sobel k 7, dx and dy          9.24 ms
    mask, fill black              0.85 ms  (cached mask; ~3.9 ms to build one)
    mask, fill blur k 31          4.70 ms

Two of those are worth reading twice. Box blur is NOT the cheap one on this
build -- cv2.blur on 3-channel uint8 loses to the separable Gaussian by 4x --
so "box because it is faster" is wrong here and the kind is a look, not a
performance choice. And sobel is the expensive op even after it was made to
use 16-bit L1 magnitude (see _op_sobel; the textbook 32-bit Euclidean version
measured 8.2 ms at k 3).

A Pi 5 runs this arithmetic roughly 3-4x slower, and the budget is small:
four cameras at 25 fps is ~10 ms per frame for EVERYTHING the loop does,
inference included. So one cheap op per camera is affordable, a chain is a
decision somebody has to make with the fps counter open, and sobel at k 7 on
four cameras is not a thing a Pi can do at all. The caps exist for that:
MAX_BLUR_KSIZE 31 and MAX_SOBEL_KSIZE 7 are where the cost stops being
arguable, and a larger number is clamped down to them rather than refused --
a preview that suddenly does nothing reads as broken, a preview that stops
getting blurrier reads as a limit.
"""

import numpy as np

try:
    from . import faults
except ImportError:                     # run as a loose script, not a package
    import faults

log = faults.logger(__name__)

#: cv2 is imported lazily-ish here (module level, but guarded) for the same
#: reason the tests skip without it: on some dev boxes OpenCV is an apt package
#: that the venv running a unit test cannot see. Absent, apply() is a no-op --
#: the app itself cannot run without cv2 anyway (camera.py imports it), so this
#: only ever matters off-device.
try:
    import cv2
except ImportError:                                         # pragma: no cover
    cv2 = None

#: Every op this module knows. A step naming anything else is skipped with a
#: warning: a typo in a config file must not quietly do nothing invisible.
OPS = ("grayscale", "exposure", "sharpness", "blur", "sobel", "mask")

#: Most steps one camera may chain. Each one is a full-frame pass, so this is
#: a cost bound, not a taste one -- see the table above.
MAX_STEPS = 8

#: Blur kernel bound. 31 at 720p is ~4.7 ms here and a good deal more on a Pi;
#: past it the picture is fog anyway and no detector reads it.
MAX_BLUR_KSIZE = 31

#: Sobel aperture bound. cv2 supports 1/3/5/7 and -1 (Scharr); 7 is already
#: the expensive end and anything larger is not a supported aperture at all.
MAX_SOBEL_KSIZE = 7

#: Unsharp mask radius bound. The radius decides what "detail" means; past 9
#: it is halo, not sharpness.
MAX_SHARP_KSIZE = 9


def _odd(value, default, low, high):
    """A kernel size: an odd int inside [low, high].

    Rounded UP to odd rather than refused (4 -> 5), because a slider that
    reports even numbers is a slider the operator will land on, and rejecting
    the step there would make the picture jump back and forth while it is
    dragged. cv2 raises on an even kernel, so this is also the guard that
    keeps that exception out of the frame loop.
    """
    try:
        k = int(value)
    except (TypeError, ValueError):
        k = int(default)
    if k % 2 == 0:
        k += 1
    return max(low, min(high, k))


def _num(value, default, low, high):
    """A float inside [low, high]; anything unreadable falls back to default.
    NaN included -- it compares false against both bounds, so it has to be
    caught here or it propagates into every pixel of the frame."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float(default)
    if f != f:                                              # NaN
        return float(default)
    return max(low, min(high, f))


def _warn(op, reason):
    """One skipped step, named. Per FRAME in the worst case, which is why it
    goes through the faults logger: _Throttle collapses a repeat to one record
    per REPEAT_S, so a permanently bad config costs two lines a minute rather
    than sixty a second."""
    log.warning("preprocess: step %r skipped (%s)", op, reason,
                extra=faults.event(faults.SYS_CONFIG_INVALID,
                                   op=str(op), reason=reason))


def _gray(img):
    """Single channel, whatever came in."""
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _op_exposure(img, step):
    """Brightness and gamma, as ONE 256-entry lookup table.

    gain multiplies, gamma bends: gamma > 1 lifts the shadows without blowing
    the highlights (which is the point -- a linear gain on a dark frame just
    makes the noise brighter too), gamma < 1 pushes them down. Both in one LUT
    because a LUT is a single pass over the frame whatever it encodes, while
    multiply-then-power is three passes and a float copy of the image.
    """
    gain = _num(step.get("gain"), 1.0, 0.05, 4.0)
    gamma = _num(step.get("gamma"), 1.0, 0.1, 5.0)          # > 0, always
    if gain == 1.0 and gamma == 1.0:
        return img
    ramp = np.arange(256, dtype=np.float32) / 255.0
    table = np.clip(((ramp ** (1.0 / gamma)) * gain) * 255.0, 0, 255)
    return cv2.LUT(img, table.astype(np.uint8))


def _op_sharpness(img, step):
    """Unsharp mask: the frame, plus what a blur of it threw away.

    amount 0 is a no-op and returns the SAME object rather than a copy of an
    unchanged picture -- a slider parked at zero must cost nothing.
    """
    amount = _num(step.get("amount"), 1.0, 0.0, 3.0)
    if amount <= 0:
        return img
    k = _odd(step.get("ksize"), 5, 3, MAX_SHARP_KSIZE)
    soft = cv2.GaussianBlur(img, (k, k), 0)
    return cv2.addWeighted(img, 1.0 + amount, soft, -amount, 0.0)


def _op_blur(img, step):
    """Box or Gaussian. Box is the cheap one and reads as "lose the texture";
    Gaussian is the one that keeps edges honest while it removes grain, which
    is what helps a noisy night camera."""
    # Default gaussian, and not only because it looks better: on this OpenCV
    # build the box blur is the SLOWER of the two at the same kernel (4.1 ms
    # against 0.9 ms at k 5, 720p), so the cheap-looking choice is the dear
    # one. Pick box for the flat, posterised look, not to save time.
    kind = str(step.get("kind") or "gaussian").lower()
    if kind not in ("box", "gaussian"):
        raise ValueError(f"blur kind must be box or gaussian, not {kind!r}")
    k = _odd(step.get("ksize"), 5, 3, MAX_BLUR_KSIZE)
    if kind == "box":
        return cv2.blur(img, (k, k))
    # sigma 0 lets cv2 derive it from the kernel, which is the sane default
    # and what an operator who only moved the size slider means.
    sigma = _num(step.get("sigma"), 0.0, 0.0, 25.0)
    return cv2.GaussianBlur(img, (k, k), sigma)


def _op_sobel(img, step):
    """Edges. Runs on GREY and returns one channel.

    On grey, not per channel: three derivative passes cost three times one and
    the answer is three near-identical edge maps a detector cannot use
    separately anyway. The chain converts back to BGR at the end, so this
    staying single-channel is invisible to everything downstream.

    dx and dy BOTH set means the edge STRENGTH in both directions, not cv2's
    cross derivative d2/dxdy -- the cross derivative is a checkerboard that
    looks like a bug, and "edges either way" is what anybody ticking both
    boxes means.

    That strength is (|gx| + |gy|) / 2 on 16-bit intermediates, not the
    Euclidean sqrt(gx^2 + gy^2) on 32-bit floats. The float magnitude is the
    textbook answer and it measured 8.2 ms a frame at 720p here against 2.7 ms
    for this -- three times the whole per-frame budget of a 4-camera Pi system,
    for a grey map that differs by at most 30% (at exactly 45 degrees, less
    everywhere else) and is then scaled by an operator's slider anyway. The
    cheap one is what ships; the expensive one would simply mean nobody could
    afford the op.
    """
    dx = int(_num(step.get("dx"), 1, 0, 2))
    dy = int(_num(step.get("dy"), 1, 0, 2))
    if dx == 0 and dy == 0:
        raise ValueError("sobel needs dx or dy")
    k = _odd(step.get("ksize"), 3, 1, MAX_SOBEL_KSIZE)
    scale = _num(step.get("scale"), 1.0, 0.05, 8.0)
    grey = _gray(img)
    if dx and dy:
        # Halved before the sum so a strong edge does not saturate on the way
        # in; `scale` is how the operator opens it back up.
        half = scale / 2.0
        mag = cv2.add(
            cv2.convertScaleAbs(cv2.Sobel(grey, cv2.CV_16S, dx, 0, ksize=k),
                                alpha=half),
            cv2.convertScaleAbs(cv2.Sobel(grey, cv2.CV_16S, 0, dy, ksize=k),
                                alpha=half))
    else:
        mag = cv2.convertScaleAbs(
            cv2.Sobel(grey, cv2.CV_16S, dx, dy, ksize=k), alpha=scale)
    # `scale` is a fixed multiplier and is the default BECAUSE it is fixed: the
    # same edge produces the same grey in every frame. `normalize` stretches
    # each frame to its own darkest/brightest, which makes faint edges visible
    # and makes the picture pulse frame to frame whenever the strongest edge
    # changes -- good for tuning by eye, bad for a detector, so it is opt-in.
    if step.get("normalize"):
        return cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
    return mag


#: Built masks, by (frame shape, polygons, invert). THE MASK IS THE COST of
#: this op. Filling the polygons over a 720p frame is ~2.5 ms and the polygons
#: do not change from frame to frame, while applying a built one is 0.8 ms --
#: rebuilt every frame it would be the most expensive thing in this file, and
#: cached it is one of the cheapest. Keyed on the frame SHAPE too, so a camera
#: that renegotiates its resolution mid-run gets a mask of the new size rather
#: than one cv2 refuses to apply.
_MASK_CACHE = {}

#: How many masks to keep. One per camera per polygon set per resolution; a
#: handful covers every real system, and the whole thing is dropped rather
#: than aged when it fills -- an LRU here would be ceremony over a dict that
#: rebuilds itself in a few milliseconds.
MASK_CACHE_MAX = 16


def _polygons(raw):
    """Normalised polygons -> a hashable, validated tuple, or () for nothing
    usable.

    NORMALISED (0..1), never pixels, and for the reason zones.py gives at
    length: a camera that renegotiates its size (an RTSP stream changing
    profile, a resolution edited in the camera panel) would leave a pixel
    polygon masking the wrong part of the picture, with nothing in the UI to
    say so. The same convention the Zone Manager already stores its geometry
    in, so a zone the operator has already drawn can be handed here as points.
    """
    out = []
    for poly in raw or ():
        pts = []
        for point in poly or ():
            try:
                x, y = float(point[0]), float(point[1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if x != x or y != y:                            # NaN
                continue
            pts.append((min(1.0, max(0.0, x)), min(1.0, max(0.0, y))))
        if len(pts) >= 3:               # an area; two points are a line, and
            out.append(tuple(pts))      # a line has no inside to keep
    return tuple(out)


def _mask_for(shape, polys, invert):
    """(keep, drop): two masks SHAPED LIKE THE FRAME, built once and cached.

    Shaped like the frame, rather than the one-channel mask cv2's `mask=`
    argument takes, because the masked form of bitwise_and is five times
    slower than the plain one on this build (3.8 ms against 0.7 ms at 720p) --
    which on a 4-camera Pi is the difference between an affordable op and an
    unaffordable one. `drop` is cached beside it so the blur fill does not
    invert a full frame every pass.

    Several polygons are a UNION -- "the loading bay and the doorway" is one
    region with a gap in it, not two competing answers. ONE fillPoly CALL EACH,
    which matters: cv2.fillPoly given several contours at once treats them as
    one composite shape under the even-odd rule, so two polygons that OVERLAP
    cancel each other in the overlap and punch a hole exactly where the
    operator drew twice.
    """
    key = (shape, polys, bool(invert))
    hit = _MASK_CACHE.get(key)
    if hit is not None:
        return hit
    h, w = shape[0], shape[1]
    flat = np.zeros((h, w), dtype=np.uint8)
    for poly in polys:
        cv2.fillPoly(flat, [np.array([[int(round(x * (w - 1))),
                                       int(round(y * (h - 1)))]
                                      for x, y in poly], dtype=np.int32)], 255)
    if invert:
        flat = cv2.bitwise_not(flat)
    keep = flat if len(shape) == 2 else cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)
    built = (keep, cv2.bitwise_not(keep))
    if len(_MASK_CACHE) >= MASK_CACHE_MAX:
        _MASK_CACHE.clear()
    _MASK_CACHE[key] = built
    return built


def _op_mask(img, step):
    """Hide everything outside the operator's region, before the detector.

    fill="black" is the default and the cheap one: one bitwise_and, and the
    hidden area is gone. Its cost is that the boundary becomes a hard black
    edge, which is itself a feature some models react to -- a person standing
    half over the line becomes a black-backed silhouette, a shape the model was
    never trained on, and the detection goes strange rather than away.
    fill="blur" pays one extra full-frame blur to fill the hidden area with a
    heavy blur of ITSELF instead: no hard edge, no readable detail, and a
    boundary that looks like an out-of-focus background. Worth it on a model
    that misbehaves at the edge; not worth it by default, at roughly 5x the
    cost of the black fill.
    """
    polys = _polygons(step.get("polygons"))
    if not polys:
        raise ValueError("mask needs at least one polygon of 3+ points")
    keep, drop = _mask_for(img.shape, polys, step.get("invert"))
    kept = cv2.bitwise_and(img, keep)
    if str(step.get("fill") or "black").lower() != "blur":
        return kept
    k = _odd(step.get("ksize"), MAX_BLUR_KSIZE, 9, MAX_BLUR_KSIZE)
    soft = cv2.GaussianBlur(img, (k, k), 0)
    return cv2.add(kept, cv2.bitwise_and(soft, drop))


def apply(frame_bgr, steps):
    """Run `steps` over one frame and return the result as 3-channel BGR.

    PURE: the input frame is never written to (every op returns a new array),
    because the same grabbed frame is handed to the viewer threads and to the
    recorder, and mutating it here would change the picture underneath them.

    An empty or absent step list returns THE SAME OBJECT -- not a copy. Off is
    the default everywhere, and the default must cost nothing: a per-camera
    copy of a 1280x720 frame is 2.6 MB of memcpy per camera per pass for a
    feature nobody switched on.
    """
    if not steps or frame_bgr is None or cv2 is None:
        return frame_bgr
    if not isinstance(steps, (list, tuple)):
        _warn("preprocess", "steps must be a list")
        return frame_bgr
    if getattr(frame_bgr, "ndim", 0) not in (2, 3):
        _warn("preprocess", "not an image")
        return frame_bgr

    out = frame_bgr
    for step in steps[:MAX_STEPS]:
        if not isinstance(step, dict):
            _warn(step, "not a step")
            continue
        op = str(step.get("op") or "").strip().lower()
        # EVERY step has an off switch, and absent means on -- so a steps
        # list written before this existed is unchanged. Skipped at zero cost
        # and silently: nothing is wrong with a step somebody switched off,
        # and toggling one on and off against the live preview is how an
        # operator finds out what it was doing.
        if not step.get("enabled", True):
            continue
        if op not in OPS:
            _warn(op or "(unnamed)", "unknown op")
            continue
        try:
            if op == "grayscale":
                out = _gray(out)
            elif op == "exposure":
                out = _op_exposure(out, step)
            elif op == "sharpness":
                out = _op_sharpness(out, step)
            elif op == "blur":
                out = _op_blur(out, step)
            elif op == "mask":
                out = _op_mask(out, step)
            else:                                           # sobel
                out = _op_sobel(out, step)
        except Exception as exc:                            # noqa: BLE001
            # Everything cv2 can raise -- a kernel it dislikes, a depth it
            # cannot do, an array that is not an image. The frame carries on
            # as whatever the steps before this one made of it: a degraded
            # picture is a working camera, an exception here is a dead system.
            _warn(op, str(exc) or exc.__class__.__name__)

    if out is frame_bgr:
        return frame_bgr
    # ONCE, at the end: grayscale followed by sobel would otherwise pay two
    # colour conversions for a picture that is grey either way, and every op
    # above is happy with one channel. See the module docstring for why the
    # chain cannot simply hand a 2-D array on.
    if out.ndim == 2:
        out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
    return out
