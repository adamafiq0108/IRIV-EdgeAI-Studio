# Exposure and gain: the two settings an industrial camera is useless without.
#
# aravissrc leaves both on "off" -- meaning whatever the camera was last set to
# by whatever software touched it last -- so the same camera gave a black
# picture on one board and a blown-out one on the next, with nothing on either
# box to explain it or to change it.
#
# Both are now always STATED in the pipeline, defaulting to continuous: a
# camera that adapts to the light is what a person expects a camera to do.
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


# -- the pipeline says what it wants ---------------------------------------
pipe = dc.gst_gige_pipeline(1280, 720, 15, "Hikrobot-MV-CS050-60GC-DB0176142")
check("a GigE pipeline states exposure and gain, always",
      "exposure-auto=on" in pipe and "gain-auto=on" in pipe, pipe)
check("...and never leaves them to whatever the camera remembers",
      "exposure-auto=off" not in pipe, pipe)

manual = dc.gst_gige_pipeline(640, 480, 10, None, exposure_auto="off",
                              exposure_us=8500, gain_auto="off", gain_db=6)
check("manual carries its numbers",
      "exposure-auto=off" in manual and "exposure=8500" in manual
      and "gain-auto=off" in manual and "gain=6.00" in manual, manual)

# A manual value set while the camera is measuring for itself is overwritten on
# the next frame -- writing it would report a setting that is not in force.
auto_with_number = dc.gst_gige_pipeline(640, 480, 10, None,
                                        exposure_auto="on", exposure_us=8500,
                                        gain_auto="on", gain_db=6)
check("an auto mode does not also carry a manual number",
      "exposure=" not in auto_with_number and " gain=" not in auto_with_number,
      auto_with_number)

# "Continuous" is what the manuals and the page say; Aravis calls it "on".
check("continuous is Aravis' \"on\"", dc._gige_auto("continuous") == "on")
check("...once survives", dc._gige_auto("once") == "once")
check("...manual is off", dc._gige_auto("manual") == "off")
check("...and nonsense falls back to auto", dc._gige_auto("purple") == "on")

# -- the settings survive being saved --------------------------------------
cam = dc._validate_camera({"id": "gig", "source": "gige:Cam-1",
                           "exposure_auto": "off", "exposure_us": 5000,
                           "gain_auto": "once", "gain_db": 3.5}, set())
check("a GigE camera keeps its exposure and gain",
      (cam.get("exposure_auto"), cam.get("exposure_us")) == ("off", 5000.0),
      str(cam))
check("...and its gain mode", (cam.get("gain_auto"), cam.get("gain_db"))
      == ("once", 3.5), str(cam))
# The manual numbers are kept whatever the mode says, so switching to auto and
# back does not lose what was dialled in.
back = dc._validate_camera({**cam, "exposure_auto": "on"}, set())
check("switching to auto keeps the manual number for later",
      back.get("exposure_us") == 5000.0, str(back))
wild = dc._validate_camera({"id": "g2", "source": "gige:Cam-2",
                            "exposure_us": 99_000_000, "gain_db": 900,
                            "gain_auto": "off"}, set())
check("an impossible exposure is clamped", wild["exposure_us"] == 10_000_000.0,
      str(wild))
check("...and an impossible gain", wild["gain_db"] == 48.0, str(wild))

# On any other kind these are aravissrc properties with nothing to apply them
# to: storing them would be settings that quietly do nothing.
usb = dc._validate_camera({"id": "u", "source": "0", "exposure_auto": "off",
                           "gain_db": 12}, set())
check("a USB camera carries no GigE tuning",
      "exposure_auto" not in usb and "gain_db" not in usb, str(usb))

# -- and they reach the pipeline that opens the camera ---------------------
arg, backend = dc.open_capture_spec(
    "gige:Cam-1", 1280, 720, 15,
    {"exposure_auto": "off", "exposure_us": 4000, "gain_auto": "on"})
check("the capture spec carries what the camera was saved with",
      backend == "CAP_GSTREAMER" and "exposure=4000" in arg
      and "gain-auto=on" in arg, str(arg))

# The app builds its own pipeline (it must not import core/), so the two have
# to agree -- this is the duplication that has to stay in step.
app = read("systems", "demo", "app", "camera.py")
check("the app states both modes too",
      "exposure-auto={exposure} gain-auto={gain}" in app)
check("...and only writes a manual number in manual mode",
      re.search(r'if exposure == "off" and number\(.exposure_us.\) > 0', app)
      is not None)
check("...defaulting to continuous like the service does",
      'return v if v in ("on", "once", "off") else "on"' in app)

# -- the page can set them, and previews with them -------------------------
page = read("frontend", "src", "pages", "Detection.jsx")
check("the camera dialog offers exposure and gain",
      "exposure_auto: e.target.value" in page and "gain_auto: e.target.value" in page)
check("...sends them when adding a camera",
      "exposure_auto: camera.exposure_auto" in page)
check("...and previews the source WITH them, not with the camera's defaults",
      'body.exposure_auto = ' in page)          # the POST body (a64efa56)
routes = read("core", "routes", "models.py")
check("the snapshot route accepts them", "exposure_auto: str = Query" in routes)
check("...and the camera route saves them",
      '"exposure_auto", "exposure_us"' in routes)

# -- binning: the one setting that buys rate on a starved link -------------
# A GigE camera asked for a smaller SIZE is cropped, not scaled -- the size is
# the sensor's region of interest. Binning is the setting that keeps the whole
# view and sends less: 2x2 is a quarter of the data. Measured on a Hikrobot
# MV-CS050-60GC that had negotiated a 100 Mbps link: 1.96fps at 2448x1944,
# which is exactly what 4.76MB a frame down 75 Mbps comes to.
CAM = "Hikrobot-MV-CS050-60GC-DB0176142"
src = read("core", "services", "detector_control.py")
binned = dc.gst_gige_pipeline(1280, 720, 15, CAM, binning=2)
check("binning reaches the pipeline",
      "h-binning=2 v-binning=2" in binned, binned)
check("...and the size ask is dropped with it -- a binned sensor sends "
      "native/N, so a full-sensor size describes nothing it can send",
      "video/x-raw,width=" not in binned, binned)
check("binning off leaves the pipeline exactly as it was",
      "binning" not in dc.gst_gige_pipeline(1280, 720, 15, CAM, binning=1))
check("...and so does a mode no sensor implements",
      "binning" not in dc.gst_gige_pipeline(1280, 720, 15, CAM, binning=3))

cam = dc._validate_camera({"id": "g3", "source": "gige:Cam", "binning": 2}, set())
check("a GigE camera keeps its binning", cam.get("binning") == 2, str(cam))
check("...and only the modes a sensor has",
      "binning" not in dc._validate_camera(
          {"id": "g4", "source": "gige:Cam", "binning": 3}, set()))

# -- the rate the picker shows is the camera's own answer ------------------
# The generic list said 30 for a camera delivering 1.9, and 1.9 reads as a
# broken camera until something says WHY.
check("the GigE rate probe reads ResultingFrameRate",
      "ResultingFrameRate" in src and "_gige_rate_facts" in src)
check("...and a starved link is named, not just the number",
      "Mbps link, and" in src)

page = read("frontend", "src", "pages", "Detection.jsx")
check("the page offers binning", "Sensor binning" in page)
check("...and saves it", "binning: camera.binning" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
