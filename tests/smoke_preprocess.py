# Software pre-processing on the frame BEFORE the detector sees it.
#
# The operator wants grayscale / exposure / sharpness / blur / sobel on a
# camera the hardware controls cannot reach (RTSP, Orbbec) or cannot do. What
# this pins is the contract the frame loop depends on, because every one of
# these runs on every frame of every camera:
#
#   * three channels out, always -- grayscale and sobel produce one, and a
#     detector handed a 2-D array either raises or misreads it;
#   * an empty chain is the SAME object, not a copy: off by default has to
#     cost nothing, and a 720p copy per camera per pass is not nothing;
#   * the order is the operator's and is never rearranged;
#   * every parameter is clamped, and a step that is still unusable is skipped
#     with a warning while the frame carries on -- a bad number in a config
#     file must never raise out of the capture loop;
#   * the `mask` op hides pixels BEFORE inference (which is not what the Zone
#     Filter does -- that drops detections after it), and its polygons are
#     NORMALISED, so the same region masks the same place after the camera
#     renegotiates its resolution;
#   * the built mask is cached, because building it is the expensive half;
#   * every step has an off switch, and off costs nothing.
import logging
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import preprocess                                    # noqa: E402

# -- everything that does not need OpenCV ------------------------------------

# 1. the clamps themselves. An even kernel is rounded UP rather than refused
# (a slider WILL land on 4), and both ends are bounded.
assert preprocess._odd(4, 5, 3, 31) == 5
assert preprocess._odd(5, 5, 3, 31) == 5
assert preprocess._odd(99, 5, 3, 31) == 31
assert preprocess._odd(0, 5, 3, 31) == 3
assert preprocess._odd("nonsense", 5, 3, 31) == 5
assert preprocess._num("2.5", 1.0, 0.1, 5.0) == 2.5
assert preprocess._num(99, 1.0, 0.1, 5.0) == 5.0
assert preprocess._num(-4, 1.0, 0.1, 5.0) == 0.1           # gamma stays > 0
assert preprocess._num(float("nan"), 1.0, 0.1, 5.0) == 1.0
assert preprocess._num(None, 1.0, 0.1, 5.0) == 1.0
print("== every parameter is clamped, NaN and nonsense included")

# 2. the caps are the ones the cost table argues for
assert preprocess.MAX_BLUR_KSIZE == 31 and preprocess.MAX_SOBEL_KSIZE == 7
assert preprocess.MAX_STEPS == 8
assert preprocess.OPS == ("grayscale", "exposure", "sharpness", "blur",
                          "sobel", "mask")
print("== the ops and the cost caps are what the app config may name")

# 3. nothing at all is a no-op on the very same object, without OpenCV in it
sentinel = object()
assert preprocess.apply(sentinel, None) is sentinel
assert preprocess.apply(sentinel, []) is sentinel
assert preprocess.apply(None, [{"op": "grayscale"}]) is None
print("== an empty chain returns the identical frame object")

# 3b. polygon cleaning needs no OpenCV either: out-of-range coordinates are
# clamped into the frame rather than refused (a drag off the edge of the
# preview is how a region gets drawn), and anything that is not an AREA is
# dropped -- a 2-point line has no inside to keep.
assert preprocess._polygons([[[0, 0], [2, 0.5], [-1, 1]]]) ==     (((0.0, 0.0), (1.0, 0.5), (0.0, 1.0)),)
assert preprocess._polygons([[[0.1, 0.1], [0.9, 0.9]]]) == ()
assert preprocess._polygons([[["x", 1], [0.1, 0.1], [0.2, 0.2], [0.3, 0.9]]]) ==     (((0.1, 0.1), (0.2, 0.2), (0.3, 0.9)),)
assert preprocess._polygons(None) == () and preprocess._polygons([[]]) == ()
print("== mask polygons are normalised, clamped, and must be areas")

if preprocess.cv2 is None:
    print("cv2 is not importable here; the pixel assertions need it")
    raise SystemExit(77)

import numpy as np                                            # noqa: E402

# Warnings are the app's fault records. Collected here so "skipped with a
# warning" is asserted rather than assumed.
WARNINGS = []


class _Catch(logging.Handler):
    def emit(self, record):
        WARNINGS.append(record.getMessage())


logging.getLogger("app.preprocess").addHandler(_Catch())
logging.getLogger("app.preprocess").setLevel(logging.WARNING)

# A picture with structure in it: a bright block and a hard edge, so blur,
# sharpen and sobel each have something to actually change.
FRAME = np.zeros((120, 160, 3), dtype=np.uint8)
FRAME[:, 80:] = 200
FRAME[30:60, 20:50] = (40, 90, 180)


def out(steps, frame=FRAME):
    return preprocess.apply(frame, steps)


# 4. every op: same size, uint8, THREE channels out
for step in ({"op": "grayscale"},
             {"op": "exposure", "gain": 1.3, "gamma": 1.8},
             {"op": "sharpness", "amount": 1.2, "ksize": 5},
             {"op": "blur", "kind": "box", "ksize": 5},
             {"op": "blur", "kind": "gaussian", "ksize": 7, "sigma": 2.0},
             {"op": "sobel", "dx": 1, "dy": 1, "ksize": 3},
             {"op": "sobel", "dx": 1, "dy": 0, "ksize": 3, "normalize": True},
             {"op": "mask", "polygons": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8]]]},
             {"op": "mask", "fill": "blur",
              "polygons": [[[0.2, 0.2], [0.8, 0.2], [0.8, 0.8]]]}):
    got = out([step])
    assert got.shape == FRAME.shape, (step, got.shape)
    assert got.dtype == np.uint8, (step, got.dtype)
    assert got.ndim == 3 and got.shape[2] == 3, (step, got.shape)
    assert got is not FRAME, step
print("== every op returns a 3-channel uint8 frame of the same size")

# ...and the input frame is never written through: the same grabbed array goes
# to the viewer threads and the recorder.
before = FRAME.copy()
out([{"op": "blur", "kind": "gaussian", "ksize": 9},
     {"op": "exposure", "gain": 2.0}])
assert np.array_equal(FRAME, before), "apply() wrote into the caller's frame"
print("== the caller's frame is never modified in place")

# 5. grayscale really is grey (all three planes equal), and exposure really
# moves the brightness in the direction the parameter says
grey = out([{"op": "grayscale"}])
assert np.array_equal(grey[:, :, 0], grey[:, :, 2])
assert float(out([{"op": "exposure", "gain": 1.5}]).mean()) > float(FRAME.mean())
assert float(out([{"op": "exposure", "gamma": 0.4}]).mean()) < float(FRAME.mean())
print("== grayscale is grey; gain lifts and a low gamma darkens")

# 6. ORDER IS RESPECTED. Sobel on a colour frame greys it first, so
# grayscale-then-sobel and sobel-then-grayscale must NOT be the same picture.
a = out([{"op": "grayscale"}, {"op": "sharpness", "amount": 2.0}])
b = out([{"op": "sharpness", "amount": 2.0}, {"op": "grayscale"}])
assert not np.array_equal(a, b), "the chain was reordered or merged"
c = out([{"op": "blur", "kind": "gaussian", "ksize": 9},
         {"op": "sobel", "dx": 1, "dy": 1}])
d = out([{"op": "sobel", "dx": 1, "dy": 1},
         {"op": "blur", "kind": "gaussian", "ksize": 9}])
assert not np.array_equal(c, d), "blur-then-edges equals edges-then-blur"
print("== the steps run in the operator's order, never rearranged")

# 7. clamping is observable from outside: 4 behaves as 5, 99 as the cap
assert np.array_equal(out([{"op": "blur", "kind": "box", "ksize": 4}]),
                      out([{"op": "blur", "kind": "box", "ksize": 5}]))
assert np.array_equal(
    out([{"op": "blur", "kind": "gaussian", "ksize": 99}]),
    out([{"op": "blur", "kind": "gaussian", "ksize": preprocess.MAX_BLUR_KSIZE}]))
assert np.array_equal(
    out([{"op": "sobel", "ksize": 99}]),
    out([{"op": "sobel", "ksize": preprocess.MAX_SOBEL_KSIZE}]))
print("== ksize 4 runs as 5 and 99 runs as the cap, rather than raising")

# 8. a malformed step is SKIPPED with a warning and the frame passes through
WARNINGS.clear()
for bad in ([{"op": "sharpen"}],                    # not an op we have
            [{"op": ""}],                           # no op at all
            ["grayscale"],                          # not even a step
            [{"op": "blur", "kind": "motion"}],     # a kind cv2 cannot do
            [{"op": "sobel", "dx": 0, "dy": 0}],    # a derivative of nothing
            "grayscale"):                           # steps is not a list
    got = preprocess.apply(FRAME, bad)
    assert got is FRAME, bad
assert len(WARNINGS) == 6, WARNINGS
print("== a malformed step is skipped, warned about, and the frame goes on")

# ...and a bad step inside a GOOD chain loses only itself
WARNINGS.clear()
mixed = out([{"op": "grayscale"}, {"op": "nope"}, {"op": "exposure", "gain": 1.4}])
assert np.array_equal(mixed, out([{"op": "grayscale"},
                                  {"op": "exposure", "gain": 1.4}]))
assert len(WARNINGS) == 1, WARNINGS
print("== one bad step in a chain costs that step and nothing else")

# 9. EVERY step has an off switch, and a switched-off step is skipped SILENTLY -- it is not a fault, and a tuned
# step should not have to be deleted to be tried without
WARNINGS.clear()
for op in ({"op": "grayscale"}, {"op": "exposure", "gain": 2.0},
           {"op": "sharpness"}, {"op": "blur"}, {"op": "sobel"},
           {"op": "mask", "polygons": [[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]]}):
    # The ONLY step, switched off: the identical frame object comes back, so
    # "off" really is free rather than a copy of an unchanged picture.
    assert preprocess.apply(FRAME, [dict(op, enabled=False)]) is FRAME, op
assert not WARNINGS
# absent means ON, so a steps list written before the switch existed is
# unchanged by it
assert preprocess.apply(FRAME, [{"op": "grayscale"}]) is not FRAME
# ...and one disabled step in a chain costs only itself
assert np.array_equal(
    out([{"op": "grayscale", "enabled": False}, {"op": "exposure", "gain": 1.4}]),
    out([{"op": "exposure", "gain": 1.4}]))
# and past MAX_STEPS the chain simply stops, rather than costing a frame
assert np.array_equal(
    out([{"op": "exposure", "gain": 1.1}] * 12),
    out([{"op": "exposure", "gain": 1.1}] * preprocess.MAX_STEPS))
print("== a disabled step is a silent no-op; the chain is bounded at MAX_STEPS")

# 10. THE MASK. A region given in normalised points masks the same PLACE at
# any frame size -- which is the whole reason the points are not pixels.
LEFT = [[[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]]
for (h, w) in ((120, 160), (240, 320)):
    lit = np.full((h, w, 3), 255, np.uint8)
    got = preprocess.apply(lit, [{"op": "mask", "polygons": LEFT}])
    left, right = got[:, :w // 2 - 2], got[:, w // 2 + 2:]
    assert left.min() == 255, (h, w, left.min())     # inside: untouched
    assert right.max() == 0, (h, w, right.max())     # outside: gone
print("== a normalised region masks the same place at any frame size")

# invert keeps the OUTSIDE instead
lit = np.full((120, 160, 3), 255, np.uint8)
flip = preprocess.apply(lit, [{"op": "mask", "polygons": LEFT, "invert": True}])
assert flip[:, :78].max() == 0 and flip[:, 82:].min() == 255
print("== invert keeps the outside")

# several polygons are a UNION, not a fight
TOP = [[[0.0, 0.0], [1.0, 0.0], [1.0, 0.4], [0.0, 0.4]]]
both = preprocess.apply(lit, [{"op": "mask", "polygons": LEFT + TOP}])
assert both[:40, 100:].min() == 255, "the second polygon was ignored"
assert both[:, :78].min() == 255, "the first polygon was ignored"
assert both[80:, 100:].max() == 0, "the union kept something outside both"
print("== several polygons are a union")

# the blur fill leaves no pure black where the black fill would -- a hard
# black edge is a detection artefact on some models
noisy = np.random.default_rng(7).integers(40, 255, (120, 160, 3),
                                          dtype=np.uint8)
blacked = preprocess.apply(noisy, [{"op": "mask", "polygons": LEFT}])
blurred = preprocess.apply(noisy, [{"op": "mask", "polygons": LEFT,
                                    "fill": "blur"}])
assert blacked[:, 100:].max() == 0
assert blurred[:, 100:].min() > 0, "the blur fill left pure black behind"
assert blurred[:, 100:].std() < noisy[:, 100:].std(), "nothing was blurred"
assert np.array_equal(blurred[:, :70], blacked[:, :70]), "the kept side moved"
print("== the blur fill hides the outside without a hard black edge")

# the mask is built ONCE per (shape, polygons) and rebuilt for a new shape:
# building it is ~2.5 ms at 720p and applying it is 0.8, so a cache miss per
# frame would be the most expensive thing in the file
preprocess._MASK_CACHE.clear()
preprocess.apply(lit, [{"op": "mask", "polygons": LEFT}])
assert len(preprocess._MASK_CACHE) == 1
cached = next(iter(preprocess._MASK_CACHE.values()))
preprocess.apply(lit, [{"op": "mask", "polygons": LEFT}])
assert len(preprocess._MASK_CACHE) == 1
assert next(iter(preprocess._MASK_CACHE.values())) is cached, "rebuilt a mask"
preprocess.apply(np.full((240, 320, 3), 255, np.uint8),
                 [{"op": "mask", "polygons": LEFT}])
assert len(preprocess._MASK_CACHE) == 2, "a new frame size reused the old mask"
print("== the mask is cached by frame shape and polygons, and rebuilt for a new one")

# a mask with nothing drawable is a skipped step, not a black frame
WARNINGS.clear()
assert preprocess.apply(FRAME, [{"op": "mask", "polygons": []}]) is FRAME
assert preprocess.apply(FRAME, [{"op": "mask"}]) is FRAME
assert len(WARNINGS) == 2, WARNINGS
print("== a mask with no usable polygon is skipped, not applied as black")

# 11. the run.py wiring: the config key the app reads, and the preview flag.
# Pinned as TEXT because the workflow compiler writes that key and the app
# reads it -- the two sides only meet on the board.
run_src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
               encoding="utf-8").read()
assert 'preprocess_mod.apply(frame, pipe.cfg.get("preprocess"))' in run_src
assert 'pipe.cfg.get("preprocess_preview")' in run_src
assert "_pre(pipe, _read_frame(pipe, now))" in run_src
print("== the app reads per-camera `preprocess` and `preprocess_preview`")

print("OK smoke_preprocess")
