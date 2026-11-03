# A GigE camera asked for a size it cannot reach does not give a smaller
# picture -- it gives NOTHING.
#
# The caps never negotiate, the stream stops with "not-negotiated", and the
# app reports "camera disconnected: gave no frames after 3 reconnect attempts"
# of a camera that is plugged in, powered, answering discovery and readable
# with arv-tool. Measured on a Hikrobot MV-CS050-60GC (2448x2048) that had
# been asked for 2592x1944 -- a size it inherited from the USB webcam it
# replaced, which the picker went on offering because the generic fallback
# list outlived the camera's own answer.
#
# Three fixes, all three checked here: the picker stops offering sizes past
# the camera's ceiling, the pipeline clamps to it anyway, and an open that
# still fails retries at the camera's own size instead of losing the feed.
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


CAM = "Hikrobot-MV-CS050-60GC-DB0176142"

# -- the pipeline clamps to the sensor -------------------------------------
dc._GIGE_MAX[CAM] = (2448, 2048)                # as the real camera answers
pipe = dc.gst_gige_pipeline(2592, 1944, 30, CAM)
# Per AXIS, because that is how the camera's own limits are stated (width
# 32-2448, height 8-2048): 1944 rows are fine, 2592 columns are not, and only
# the one that does not fit moves.
check("an over-size ask is clamped to what the camera has",
      "width=2448,height=1944" in pipe, pipe)
check("...both axes when both are too big",
      "width=2448,height=2048" in dc.gst_gige_pipeline(3840, 2160, 30, CAM))
check("...and a size it CAN do is untouched",
      "width=1280,height=720" in dc.gst_gige_pipeline(1280, 720, 15, CAM))

# A camera that cannot be asked is not guessed at: the ask goes through as
# written, and the open-time retry is what saves the feed.
dc._GIGE_MAX["Unknown-Cam"] = None
check("a camera that cannot be asked keeps the operator's size",
      "width=2592,height=1944" in dc.gst_gige_pipeline(2592, 1944, 30, "Unknown-Cam"))

# The answer is cached, including the unknown one: the pipeline builder asks
# on every open, and each ask is a subprocess and a GenICam handshake.
check("the sensor size is asked once per camera",
      dc._gige_max_resolution(CAM) == (2448, 2048)
      and dc._gige_max_resolution("Unknown-Cam") is None)

# -- and no size is left with no caps at all -------------------------------
# The retry path: no width/height means "whatever the sensor gives", which is
# a real picture rather than a failed negotiation.
free = dc.gst_gige_pipeline(None, None, None, CAM)
check("with no size asked, the pipeline takes the camera's own",
      "video/x-raw,width=" not in free, free)
check("...and still states exposure and gain",
      "exposure-auto=on" in free and "gain-auto=on" in free, free)

# -- the picker stops offering the impossible ------------------------------
real_probe = dc._gige_max_resolution
dc._gige_max_resolution = lambda name="": (2448, 2048)
try:
    res = dc.camera_resolutions(f"gige:{CAM}", "gige")
finally:
    dc._gige_max_resolution = real_probe
sizes = [tuple(r) for r in res["candidates"]]
check("the camera's own maximum is offered", (2448, 2048) in sizes, str(sizes))
check("...and nothing above it is", not [r for r in sizes if r[0] > 2448 or r[1] > 2048],
      str(sizes))
check("...while the smaller generic sizes stay", (1280, 720) in sizes, str(sizes))

# An RTSP probe reports what the stream is sending NOW, not a ceiling -- a
# camera can usually be asked for more, so nothing is trimmed there.
src = read("core", "services", "detector_control.py")
check("only a real ceiling trims the list",
      'kind in ("usb", "gige")' in src)

# -- "no frames" stops being said of a camera somebody else is holding -----
check("the control channel is read from the camera itself",
      "_GVCP_CCP = 0x0A00" in src and "_gvcp_read_register" in src)
check("...and a held camera says so",
      "is controlled by another application" in src)

# -- the app retries at the camera's own size ------------------------------
app = read("systems", "demo", "app", "camera.py")
check("the app can build a size-free GigE pipeline",
      "def _capture_spec(self, sized=True)" in app)
check("...and retries that way when the asked size is refused",
      re.search(r"if not cap\.isOpened\(\) and str\(self\.source\)"
                r"\.strip\(\)\.startswith\(\"gige\"\)", app) is not None)
check("...saying so in the log rather than silently changing size",
      "opening at the camera's own size" in app)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
