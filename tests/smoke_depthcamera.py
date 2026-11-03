# The depth camera: a camera that also says HOW FAR AWAY things are.
#
# An Orbbec Gemini 336 (active stereo, not time-of-flight) opens through its
# own SDK rather than through cv2, so it enters the capture path as a shim
# that answers grab/retrieve/read/get/set/release the way a VideoCapture does
# and carries an aligned depth frame alongside.
#
# No hardware here. What this pins is the arithmetic and the refusals -- the
# parts that were wrong on the box before they were right, and that no amount
# of the camera being plugged in would catch:
#
#   * depth is indexed in the DETECTOR's pixels, not the camera's. Capture
#     downscales to 640 before the model sees anything while depth stays
#     1280x720, so an unscaled box reads the top-left quarter of the picture:
#     2399mm for something 3453mm away.
#   * 0 and 0xFFFF are both "no reading", and 0xFFFF read as a distance puts a
#     65-metre wall in an office.
#   * a box mostly without readings gets no answer at all, rather than a
#     confident number derived from the few pixels that did match.
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

import numpy as np                                          # noqa: E402

from app import orbbec                                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- which cameras go this way -------------------------------------------------
check("a camera whose source is orbbec takes the SDK path",
      orbbec.is_orbbec_source("orbbec") and orbbec.is_orbbec_source("Orbbec"))
check("...and one naming a serial, for a box with two",
      orbbec.is_orbbec_source("orbbec:CP9876300177")
      and orbbec.serial_of("orbbec:CP9876300177") == "CP9876300177")
check("...while every other camera is untouched",
      not any(orbbec.is_orbbec_source(s) for s in
              ("0", "1", "rtsp://cam/stream", "gige:front", "", None,
               "orbbecular")),
      "a source test this loose would send ordinary USB cameras through an "
      "SDK that is not installed")

# -- the two zeroes ------------------------------------------------------------
GRID = np.full((100, 100), 2000, dtype=np.uint16)
check("a flat wall reads its distance",
      orbbec.depth_mm(GRID, 1.0, [10, 10, 90, 90]) == 2000)
check("...in millimetres, through the scale the camera reports",
      orbbec.depth_mm(GRID, 0.5, [10, 10, 90, 90]) == 1000)

half = GRID.copy()
half[:, 50:] = orbbec.UNMEASURED
check("0xFFFF is not a 65-metre wall",
      orbbec.depth_mm(half, 1.0, [10, 10, 90, 90]) == 2000,
      "the unmeasured half must be dropped, not averaged in")
zeroed = GRID.copy()
zeroed[:, 50:] = orbbec.NO_READING
check("...and neither is 0", orbbec.depth_mm(zeroed, 1.0, [10, 10, 90, 90]) == 2000)

# Mostly unmeasured: a window, a mirror, a black surface. The honest answer is
# "I do not know", not the median of the few pixels that happened to match.
mostly = GRID.copy()
mostly[:, 20:] = orbbec.UNMEASURED
check("a box the camera could not read gets no distance",
      orbbec.depth_mm(mostly, 1.0, [0, 0, 100, 100]) is None,
      orbbec.depth_mm(mostly, 1.0, [0, 0, 100, 100]))

# -- the middle of the box, not its corners ------------------------------------
# A box around a person contains the wall on either side of them. Sampling the
# whole box would average the person with the background behind them.
person = np.full((100, 100), 5000, dtype=np.uint16)     # the far wall
person[30:70, 30:70] = 1500                              # somebody in front of it
check("the middle of the box decides, so background does not drag it",
      orbbec.depth_mm(person, 1.0, [25, 25, 75, 75]) == 1500,
      orbbec.depth_mm(person, 1.0, [25, 25, 75, 75]))

# -- refusals ------------------------------------------------------------------
for label, args in (("no grid at all", (None, 1.0, [0, 0, 10, 10])),
                    ("no box", (GRID, 1.0, None)),
                    ("a box off the side of the picture", (GRID, 1.0, [200, 0, 300, 50])),
                    ("a box of no width", (GRID, 1.0, [10, 10, 10, 90]))):
    check("nothing to measure: " + label, orbbec.depth_mm(*args) is None,
          str(orbbec.depth_mm(*args)))

# -- the scaling that was wrong on the box -------------------------------------
# Camera.depth_at is the only caller that knows both sizes, so it is the only
# place the box can be scaled. Exercised through a stand-in rather than a real
# Camera, which would want a device.
from app import camera as camera_mod                        # noqa: E402

near = np.full((720, 1280), 9000, dtype=np.uint16)
near[360:720, 640:1280] = 3400        # the bottom-right quarter is closer


class FakeCamera:
    """Camera.depth_at, with the device parts stood in for."""
    _lock = __import__("threading").Lock()
    _latest = None
    depth_at = camera_mod.Camera.depth_at
    depth_frame = lambda self: (near, 1.0)                   # noqa: E731


frame = np.zeros((360, 640, 3), dtype=np.uint8)              # what the model sees
cam = FakeCamera()
# The same box, in DETECTOR pixels, over the closer quarter.
box = [320, 180, 640, 360]
check("a detection box is scaled from the detector's frame to the depth grid",
      cam.depth_at(box, frame) == 3400,
      "got {} -- an unscaled box lands on the wrong quarter of the picture"
      .format(cam.depth_at(box, frame)))
check("...and the raw grid, unscaled, would have read the far wall",
      orbbec.depth_mm(near, 1.0, box) == 9000,
      "if this stops being true the test above proves nothing")

# Every other camera answers None, so callers need not ask what kind it is.
check("a camera with no depth sensor simply has no depth",
      camera_mod.Camera({"source": "0"}).depth_at([0, 0, 10, 10]) is None)

# -- the SDK is optional -------------------------------------------------------
src = open(os.path.join(ROOT, "systems", "demo", "app", "orbbec.py"),
           encoding="utf-8").read()
check("the SDK is imported inside the open, not at module load",
      "from pyorbbecsdk import" in src.split("def _open", 1)[1].split("def ", 1)[0],
      "a top-level import would make every box without the wheel fail to "
      "start the app")
check("...and the wheel is installed --no-deps, on purpose",
      "--no-deps" in src and "opencv-python 5" in src,
      "the wheel depends on OpenCV 5 and the app runs 4.10")
cam_src = open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
               encoding="utf-8").read()
check("the depth camera is never shared as an MJPEG feed",
      cam_src.index("if orbbec.is_orbbec_source(self.source):")
      < cam_src.index("shared = (shared_feed_source("),
      "the SDK holds the USB device outright; a second opener gets nothing")
check("one place resets the delivery counters, for every kind of camera",
      "def _start_grabbing(self):" in cam_src
      and cam_src.count("self._grab_thread.start()") == 1)

# -- picking one ----------------------------------------------------------------
# The mistake this has to prevent: an Orbbec DOES appear in the USB list (it
# presents several V4L2 nodes), and the node a slot symlink lands on carries
# raw Bayer or 16-bit depth. OpenCV cannot turn either into frames, so a
# camera picked there opens, delivers nothing, and reports "gave no frames
# after 3 reconnect attempts" -- which reads as broken hardware. It happened
# on .63 before this existed.
from core.services import detector_control as dc                # noqa: E402

check("a depth camera is found by USB vendor id, not by video node",
      "ORBBEC_VID" in dir(dc) and dc.ORBBEC_VID == "2bc5"
      and "idVendor" in open(os.path.join(ROOT, "core", "services",
                                          "detector_control.py"),
                             encoding="utf-8").read())
check("...and offered as its own source, never as a device index",
      '"source": "orbbec:" + serial if serial else "orbbec"'
      in open(os.path.join(ROOT, "core", "services", "detector_control.py"),
              encoding="utf-8").read())
check("...listed by the same endpoint as every other bus",
      '"depth": list_depth_cameras()'
      in open(os.path.join(ROOT, "core", "services", "detector_control.py"),
              encoding="utf-8").read())

page = open(os.path.join(ROOT, "frontend", "src", "pages", "Detection.jsx"),
            encoding="utf-8").read()
check("the picker has a mode for it",
      '["depth", "Depth (3D)"]' in page and 'camMode === "depth"' in page)
check("...which writes the camera's own source string",
      'if (camMode === "depth") return depthPick || "orbbec";' in page)
check("...and opening an existing one lands on that mode, not on USB",
      'src.startsWith("orbbec")' in page and 'setCamMode((mode) => (force || mode === "usb" ? "depth" : mode));' in page)
check("...and the panel warns against picking it from the USB list",
      "Do not pick this camera from the USB list" in page
      and "gave no frames" in page,
      "this is the warning the operator needed and did not have")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
