# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""The Orbbec depth camera, wearing the shape of a cv2.VideoCapture.

An Orbbec Gemini is not a UVC camera the capture path can open. It has its own
SDK, its own frameset, and -- the reason it is here at all -- a DEPTH image
that has to be aligned to the colour one before a detection box means anything
in metres. So instead of a second capture path through camera.py, this is one
object that answers grab/retrieve/read/get/set/release the way OpenCV's does,
and carries the depth frame alongside.

WHY ALIGNED. The colour lens and the two IR eyes see different fields (86x55
against 90x65) from different places on the board. A box drawn on the colour
image does not land on the same pixels in the raw depth image, and at 1m the
error is tens of centimetres -- enough to read the floor behind somebody as
their distance. The SDK's AlignFilter reprojects depth into the colour
camera's frame, so depth[y][x] is the distance to whatever colour[y][x] shows.
Measured on .63: colour 1280x720 and depth arriving on the same 1280x720 grid,
92.5% of pixels valid, about 22 fps.

THE CAMERA ON .63 IS A GEMINI 336: active stereo, not time-of-flight. Two IR
cameras 50mm apart plus a pattern projector, depth from disparity. Range 0.1m
to 20m+, honest between 0.26m and 3m. Depth is in MILLIMETRES.

TWO ZEROES. A depth pixel of 0 means nothing was measured there; 0xFFFF means
the stereo match failed outright. Both are "no reading", and code that treats
0xFFFF as a distance reports a 65-metre wall in a room.

Not imported unless a camera actually asks for it -- the SDK is an optional
dependency, installed on the boxes that have the hardware:

    /path/to/.venv/bin/pip install --no-deps pyorbbecsdk2

--no-deps ON PURPOSE: the wheel depends on opencv-python 5.x, and the app runs
4.10. Letting pip resolve that swaps OpenCV out from under the detector.
"""
import threading
import time

import numpy as np

#: What a camera's `source` says to get here.
SOURCE = "orbbec"

#: Neither is a distance. See the module docstring.
NO_READING = 0
UNMEASURED = 0xFFFF


def is_orbbec_source(source):
    """Does this camera entry want the depth camera?

    "orbbec" for the only one, "orbbec:<serial>" to name one of several.
    """
    s = str(source or "").strip().lower()
    return s == SOURCE or s.startswith(SOURCE + ":")


def serial_of(source):
    s = str(source or "").strip()
    return s.split(":", 1)[1].strip() if ":" in s else ""


class OrbbecCapture:
    """One Orbbec camera, opened, with colour and aligned depth.

    Answers enough of the cv2.VideoCapture surface for camera.py's grab loop:
    isOpened, grab, retrieve, read, get, set, release. grab() takes a frameset
    and holds it; retrieve() decodes the colour frame to BGR. That split is
    what lets the grab loop run at the camera's rate while paying for the
    conversion only at the pipeline's.
    """

    def __init__(self, source="", want_width=0, want_height=0, want_fps=0):
        self._lock = threading.Lock()
        self._pipe = None
        self._align = None
        self._pending = None          # the frameset grab() took
        self._depth = None            # latest aligned depth, uint16
        self._depth_scale = 1.0       # millimetres per unit
        self._depth_at = 0.0          # when it landed
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self.error = ""
        self._open(serial_of(source), want_width, want_height, want_fps)

    # -- opening ---------------------------------------------------------------

    def _open(self, serial, want_width, want_height, want_fps):
        try:
            from pyorbbecsdk import (AlignFilter, Config, Context, OBSensorType,
                                     OBStreamType, Pipeline)
        except ImportError:
            self.error = ("the Orbbec SDK is not installed on this box - "
                          "pip install --no-deps pyorbbecsdk2")
            return
        try:
            # The Context must be HELD, not used as a temporary. Written as
            # Context().query_devices() the context is freed before the device
            # list is read, and the SDK answers with a NULL pointer for
            # "deviceMgr" -- an error that says nothing about the real cause.
            self._context = Context()
            devices = self._context.query_devices()
            if devices.get_count() < 1:
                self.error = "no Orbbec camera found on this box"
                return
            self._pipe = Pipeline()
            config = Config()
            colour = self._pick(
                self._pipe.get_stream_profile_list(OBSensorType.COLOR_SENSOR),
                want_width, want_height, want_fps)
            depth = self._pipe.get_stream_profile_list(
                OBSensorType.DEPTH_SENSOR).get_default_video_stream_profile()
            config.enable_stream(colour)
            config.enable_stream(depth)
            self._pipe.start(config)
            self._align = AlignFilter(align_to_stream=OBStreamType.COLOR_STREAM)
            self._width = int(colour.get_width())
            self._height = int(colour.get_height())
            self._fps = float(colour.get_fps())
            # The lens, in numbers: focal lengths and principal point in
            # pixels. What turns "100 pixels wide at 2m" into millimetres.
            # The COLOUR intrinsics serve depth too, because depth is aligned
            # onto the colour grid above. Read once; a lens does not change.
            self._intrinsics = None
            try:
                intr = self._pipe.get_camera_param().rgb_intrinsic
                self._intrinsics = {"fx": float(intr.fx), "fy": float(intr.fy),
                                    "cx": float(intr.cx), "cy": float(intr.cy),
                                    "width": int(intr.width),
                                    "height": int(intr.height)}
            except Exception:                               # noqa: BLE001
                # Without intrinsics the camera still sees and still gives
                # distances; it cannot give sizes. Said by intrinsics() being
                # None, and by whatever asks for a size.
                pass
        except Exception as exc:                            # noqa: BLE001
            # Anything the SDK raises is a camera that did not open, which is
            # the caller's "camera error" case -- not an exception climbing
            # into the detection loop.
            self.error = "orbbec: {}".format(exc)
            self.release()

    @staticmethod
    def _pick(profiles, want_width, want_height, want_fps):
        """The colour profile closest to what the config asked for.

        Asked-for rather than imposed: an exact match if the camera has it,
        otherwise its own default, because a profile the device does not offer
        is not a smaller picture -- it is no stream at all.
        """
        if want_width and want_height:
            try:
                from pyorbbecsdk import OBFormat
                return profiles.get_video_stream_profile(
                    int(want_width), int(want_height), OBFormat.MJPG,
                    int(want_fps or 30))
            except Exception:                               # noqa: BLE001
                pass
        return profiles.get_default_video_stream_profile()

    # -- the VideoCapture surface ----------------------------------------------

    def isOpened(self):                                     # noqa: N802
        return self._pipe is not None and not self.error

    def grab(self):
        """Take the newest frameset, align it, keep it for retrieve().

        The depth half is stored HERE rather than in retrieve(): the grab loop
        may grab many times per retrieve, and depth should be as fresh as the
        colour frame it will be asked about.
        """
        if not self.isOpened():
            return False
        try:
            frames = self._pipe.wait_for_frames(1000)
            if frames is None:
                return False
            aligned = self._align.process(frames)
            if aligned is None:
                return False
            frames = aligned.as_frame_set()
        except Exception:                                   # noqa: BLE001
            return False
        depth = frames.get_depth_frame()
        if depth is not None:
            try:
                buf = np.frombuffer(depth.get_data(), dtype=np.uint16)
                grid = buf.reshape(depth.get_height(), depth.get_width())
                with self._lock:
                    self._depth = grid.copy()
                    self._depth_scale = float(depth.get_depth_scale())
                    self._depth_at = time.time()
            except Exception:                               # noqa: BLE001
                pass
        self._pending = frames
        return True

    def retrieve(self, *_args):
        """Decode the held colour frame to BGR, the way OpenCV hands it over."""
        frames = self._pending
        if frames is None:
            return False, None
        colour = frames.get_color_frame()
        if colour is None:
            return False, None
        frame = self._to_bgr(colour)
        return (frame is not None), frame

    def read(self):
        return self.retrieve() if self.grab() else (False, None)

    @staticmethod
    def _to_bgr(colour):
        """Colour frames arrive MJPG (the default profile) or as raw RGB."""
        import cv2
        data = np.frombuffer(colour.get_data(), dtype=np.uint8)
        width, height = colour.get_width(), colour.get_height()
        fmt = str(colour.get_format())
        if "MJPG" in fmt or "JPEG" in fmt:
            return cv2.imdecode(data, cv2.IMREAD_COLOR)
        if "RGB" in fmt:
            return cv2.cvtColor(data.reshape(height, width, 3), cv2.COLOR_RGB2BGR)
        if "BGR" in fmt:
            return data.reshape(height, width, 3)
        if "YUYV" in fmt:
            return cv2.cvtColor(data.reshape(height, width, 2), cv2.COLOR_YUV2BGR_YUYV)
        return None

    def get(self, prop):
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._width)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._height)
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        return 0.0

    def set(self, _prop, _value):
        """Every capture property is decided when the stream profile is chosen.

        Answering False rather than pretending: the caller's size and rate asks
        were already honoured in _pick(), and silently accepting a later one
        would leave the config and the stream disagreeing.
        """
        return False

    def release(self):
        pipe, self._pipe = self._pipe, None
        self._pending = None
        if pipe is not None:
            try:
                pipe.stop()
            except Exception:                               # noqa: BLE001
                pass

    # -- the part that is not a VideoCapture -----------------------------------

    def depth_frame(self):
        """The latest aligned depth grid and its scale, or (None, 1.0).

        Aligned to the COLOUR image, so it is indexed with colour pixels.
        """
        with self._lock:
            grid = self._depth
            return (None if grid is None else grid.copy()), self._depth_scale

    def intrinsics(self):
        """The colour lens's fx, fy, cx, cy in pixels, or None if the SDK
        would not say. Valid for the depth grid too -- it is aligned to the
        colour image."""
        return dict(self._intrinsics) if getattr(self, "_intrinsics", None) else None


def depth_mm(grid, scale, box, sample=0.5):
    """How far away the thing in this box is, in millimetres. None if unknown.

    The MEDIAN of the middle of the box, not the centre pixel and not the
    whole box. The centre pixel alone is one stereo match away from being the
    floor showing through a gap between somebody's arm and their body. The
    whole box includes its corners, which are background by definition -- a
    box around a person contains the wall on both sides of them.

    So: the middle `sample` of the box by area, dropping the two kinds of
    no-reading, and the median of what is left. Half the pixels have to be
    valid for an answer at all, otherwise this is a box over a window or a
    mirror and a confident number would be worse than none.
    """
    if grid is None or not box:
        return None
    height, width = grid.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box[:4])
    x1, x2 = max(0, min(x1, x2)), min(width, max(x1, x2))
    y1, y2 = max(0, min(y1, y2)), min(height, max(y1, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    inset_x = int((x2 - x1) * (1.0 - sample) / 2)
    inset_y = int((y2 - y1) * (1.0 - sample) / 2)
    patch = grid[y1 + inset_y:y2 - inset_y or y2, x1 + inset_x:x2 - inset_x or x2]
    if patch.size == 0:
        patch = grid[y1:y2, x1:x2]
    good = patch[(patch != NO_READING) & (patch != UNMEASURED)]
    if good.size < patch.size * 0.5 or good.size == 0:
        return None
    return float(np.median(good)) * float(scale or 1.0)
