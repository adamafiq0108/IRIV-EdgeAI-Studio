# Per-camera settings: what the CAMERA says it has, not what a panel assumes.
#
# Every camera is different -- a cheap module offers brightness and nothing
# else, an Arducam offers eleven controls with three of them locked while auto
# exposure owns them, an IP camera offers none at all because the adjusting
# happens in its own web page. So the panel is drawn from the driver's own
# account of itself, and this pins the parsing of that account plus the two
# things that are security, not UI:
#
#   * a control name reaches a COMMAND LINE, so it is a driver symbol or it is
#     dropped -- never a string somebody typed at us.
#   * "find the SDK online and install it" is arbitrary code execution from a
#     web page unless the package, the version and the source are decided in
#     advance, in a commit somebody reviewed.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import camera_controls as cc              # noqa: E402
from core.services import camera_sdk                         # noqa: E402
from core.services import detector_control as dc             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# What a real camera answered on .63 -- an Arducam B0495, verbatim.
ARDUCAM = """
User Controls

                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0
                       contrast 0x00980901 (int)    : min=0 max=20 step=1 default=10 value=10
        white_balance_automatic 0x0098090c (bool)   : default=1 value=1
                           gain 0x00980913 (int)    : min=168 max=1600 step=1 default=168 value=168
           power_line_frequency 0x00980918 (menu)   : min=0 max=2 default=0 value=0 (Disabled)
\t\t\t\t0: Disabled
\t\t\t\t1: 50 Hz
\t\t\t\t2: 60 Hz
      white_balance_temperature 0x0098091a (int)    : min=2300 max=6500 step=1 default=4500 value=4500 flags=inactive

Camera Controls

                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=0 value=0 (Auto Mode)
\t\t\t\t0: Auto Mode
\t\t\t\t1: Manual Mode
         exposure_time_absolute 0x009a0902 (int)    : min=5 max=660 step=1 default=5 value=5 flags=inactive
"""

found = {c["name"]: c for c in cc._parse_controls(ARDUCAM)}

check("every control the camera reported is offered", len(found) == 8,
      sorted(found))
check("a range comes from the DRIVER, not from a guess",
      found["brightness"]["min"] == -64 and found["brightness"]["max"] == 64
      and found["brightness"]["default"] == 0,
      "a slider drawn 0..100 over a -64..64 control is wrong at both ends")
check("a switch is a switch, not a slider",
      found["white_balance_automatic"]["type"] == "bool")
check("a menu carries the camera's own words for its options",
      [o["label"] for o in found["power_line_frequency"]["options"]]
      == ["Disabled", "50 Hz", "60 Hz"])
check("...and menu options never leak into the next control",
      found["auto_exposure"]["options"] == [{"value": 0, "label": "Auto Mode"},
                                            {"value": 1, "label": "Manual Mode"}]
      and found["exposure_time_absolute"]["options"] == [])
check("controls keep the camera's own grouping",
      found["brightness"]["group"] == "User"
      and found["exposure_time_absolute"]["group"] == "Camera")

# flags=inactive: the control exists but something else is driving it. A
# slider that moves and changes nothing is worse than a disabled one.
check("a control an auto mode is holding is marked locked",
      found["exposure_time_absolute"]["locked"]
      and found["white_balance_temperature"]["locked"])
check("...and the unlocked ones are not", not found["brightness"]["locked"])

# Being told a row is locked is half an answer; the other half is which switch.
withsaved = cc.controls.__wrapped__ if hasattr(cc.controls, "__wrapped__") else None
parsed = {c["name"]: c for c in cc._parse_controls(ARDUCAM)}
present = set(parsed)
for name, owner in (("exposure_time_absolute", "auto_exposure"),
                    ("white_balance_temperature", "white_balance_automatic")):
    check(f"...{name} names {owner} as the switch to turn off",
          cc._LOCKED_BY.get(name) == owner and owner in present)

# -- names that reach a command line -------------------------------------------
check("a control name is a driver symbol or it is dropped",
      dc._validate_camera({"source": "0", "controls": {
          "brightness": 10,
          "gain; rm -rf /": 1,
          "../../etc/passwd": 1,
          "BRIGHTNESS": 1,
          "ok_name": 2,
      }}).get("controls") == {"brightness": 10, "ok_name": 2},
      dc._validate_camera({"source": "0", "controls": {"gain; rm -rf /": 1}}))
check("...and a value is a number or it is dropped",
      dc._validate_camera({"source": "0", "controls": {
          "brightness": "10", "contrast": "high", "gain": None}}
      ).get("controls") == {"brightness": 10},
      "a string reaching v4l2-ctl -c name=value is a shell argument")
check("a camera with nothing adjusted carries no controls table",
      "controls" not in dc._validate_camera({"source": "0"}))

# SAVED means read back. cameras() rebuilds every entry from a whitelist of
# keys, and a table missing from that list is not merely hidden: every writer
# reads the list before it writes, so the next save of anything else deletes
# it. That is exactly what happened to the first saved brightness.
import shutil                                                # noqa: E402
_sd = tempfile.mkdtemp()
shutil.copytree(os.path.join(ROOT, "systems", "demo"), os.path.join(_sd, "demo"))
dc.set_cameras(_sd, "demo", [{"id": "cam1", "source": "0"}])
dc.set_camera_controls(_sd, "demo", "cam1", {"brightness": 12})
dc.set_camera_controls(_sd, "demo", "cam1", {"contrast": 7})
_back = dc.cameras(_sd, "demo")[0].get("controls")
check("a saved control reads back, and survives the next save",
      _back == {"brightness": 12, "contrast": 7}, _back)
dc.set_camera_controls(_sd, "demo", "cam1", {"brightness": None})
check("...and clearing one leaves the other",
      dc.cameras(_sd, "demo")[0].get("controls") == {"contrast": 7},
      dc.cameras(_sd, "demo")[0].get("controls"))

# -- the depth camera is its own kind ------------------------------------------
check("an Orbbec source is a depth camera, not a USB one",
      dc.camera_kind("orbbec") == "depth"
      and dc.camera_kind("orbbec:CP987") == "depth")
check("...and every other source keeps the kind it had",
      [dc.camera_kind(s) for s in ("0", "rtsp://x/y", "gige:front", "clip.mp4", "")]
      == ["usb", "rtsp", "gige", "file", "unset"])
check("...and it is a kind the config will accept",
      "depth" in dc.CAMERA_KINDS and "depth" in dc.KIND_LIMITS
      and "depth" in dc.KIND_LABELS,
      "a kind missing from the limits is a kind with no cap at all")

# -- cameras that have no controls, and are not broken for it -------------------
for kind, word in (("rtsp", "own web page"), ("gige", "camera panel"),
                   ("file", "recorded file"), ("unset", "No camera")):
    answer = cc.controls("x", kind)
    check(f"a {kind} camera says why it has none, rather than showing an empty panel",
          answer["adjustable"] is False and word in answer["note"],
          answer["note"])

# -- the install allowlist -----------------------------------------------------
sdk = read("core", "services", "camera_sdk.py")
check("only cameras with a known SDK can ask for one",
      camera_sdk.wanted_for("depth") is not None
      and all(camera_sdk.wanted_for(k) is None
              for k in ("usb", "rtsp", "gige", "file", "", None)))
check("the version is PINNED, not resolved at install time",
      camera_sdk.KNOWN["depth"]["version"] == "2.1.2"
      and '"{}=={}".format(' in sdk,
      "an unpinned install means the box gets whatever was published today")
check("the package name comes from the allowlist, never from the request",
      "body.get(\"package\")" not in read("core", "routes", "models.py")
      and "body.get(\"url\")" not in read("core", "routes", "models.py")
      and "body.get(\"version\")" not in read("core", "routes", "models.py"),
      "a name or URL taken from the request is arbitrary code execution")
check("...and the wheel cannot drag the detector's OpenCV out from under it",
      camera_sdk.KNOWN["depth"].get("no_deps") is True and "--no-deps" in sdk)
check("an install is admin-only and audited",
      '_require_admin(request)' in read("core", "routes", "models.py")
      .split('name="detector.camera_sdk"', 1)[1].split("@router.", 1)[0]
      and 'audit_service.record("camera.sdk"' in read("core", "routes", "models.py"))
check("a box with no internet is told so, not left hanging",
      "INSTALL_TIMEOUT_S" in sdk and "cannot reach the internet" in sdk)
# What the security review found. Each of these is a real path somebody could
# have walked, not a style rule.
routes_src = read("core", "routes", "models.py")
check("one request cannot spawn an unbounded number of processes",
      cc.MAX_CONTROLS_PER_REQUEST <= 32
      and "MAX_CONTROLS_PER_REQUEST" in routes_src
      and cc.apply_live("0", "usb", {f"c{i}": 1 for i in range(99)})[1].startswith("too many"),
      "each control is a v4l2-ctl process with a 6s timeout; thousands of "
      "them hold a threadpool worker for hours")
check("...and what is SAVED is bounded too, since config outlives the request",
      len(dc._validate_camera({"source": "0", "controls": {
          f"c{i}": 1 for i in range(500)}})["controls"]) == dc.MAX_SAVED_CONTROLS)
check("a malformed value is refused, not a 500",
      all(cc.apply_live("0", "usb", {"brightness": v})[0] == {}
          for v in ("abc", {}, float("inf"))),
      "int(inf) is an OverflowError, which the route did not catch")
check("the live path filters names as tightly as the stored one",
      cc.NAME_RE.pattern == r"[a-z0-9_]{1,64}"
      and not cc.NAME_RE.fullmatch("brightnessＡ"),
      r"\w is Unicode-aware, and the live path is the one reaching argv")
check("two installs cannot run into one environment at once",
      "_INSTALL_LOCK" in sdk and "blocking=False" in sdk)
check("...and a wheel is the only thing that can be fetched",
      "--only-binary=:all:" in sdk and "--isolated" in sdk
      and "--index-url" in sdk,
      "an sdist added to the pinned release would run setup.py as the "
      "detector's user; pip env vars silently redirect an index")
check("the hash gap is written down rather than quietly left",
      "--require-hashes" in sdk and "Before beta" in sdk)
check("the SDK is checked in the interpreter that will OPEN the camera",
      "sys.executable" in sdk and "import {}" in sdk,
      "the package name and the import name differ for pyorbbecsdk2, so a "
      "pip-list check would answer about the wrong thing")

# -- reading and writing go through the right doors ----------------------------
routes = read("core", "routes", "models.py")
controls_post = routes.split('name="detector.set_camera_controls"', 1)[1].split("@router.", 1)[0]
check("changing a camera needs the camera-change capability",
      'perm.require(request, "detection.camera.change")' in controls_post)
check("...and the value is SAVED even when the device refuses it",
      "detector_control_service.set_camera_controls(" in controls_post
      and (controls_post.index("apply_live(")
           < controls_post.index("detector_control_service.set_camera_controls(")),
      "a value that only lives in the driver is gone at the next replug")

panel = read("frontend", "src", "components", "CameraSettingsDialog.jsx")
check("the panel draws what the server reported, not a fixed list of sliders",
      "data?.controls" in panel or "data?.controls || []" in panel
      or "const controls = data?.controls" in panel)
check("...tells the operator which switch is holding a locked row",
      "locked_by_label" in panel)
check("the Install button is drawn for whoever may actually use it",
      '"may_install"' in routes_src and "sdk.may_install" in panel,
      "a button gated on the wrong capability 403s the operator who clicks it")
check("...and saves on release rather than on every pixel of a drag",
      "onMouseUp" in panel and "onTouchEnd" in panel)
# It opens from INSIDE the camera dialog, which is the app's own
# .ui-dialog-backdrop at z-index 70. A dialog here defaults to z-50, so this
# one rendered behind it and the backdrop ate every click -- the panel opened
# at the back and nothing on it could be touched.
import re as _re                                             # noqa: E402
css = read("frontend", "src", "theme.css")
backdrop = int(_re.search(r"\.ui-dialog-backdrop[^}]*z-index:\s*(\d+)", css).group(1))
lifted = int(_re.search(r'className="cy-app z-\[(\d+)\]"', panel).group(1))
check("the panel sits above the camera dialog it opens from",
      lifted > backdrop,
      f"z-{lifted} against a backdrop at z-{backdrop}")
check("...and so does its own backdrop, or it shades the wrong page",
      'overlayClassName="z-[' in panel)
page = read("frontend", "src", "pages", "Detection.jsx")
check("every camera row has its own settings button",
      "setSettingsFor(c)" in page and "CameraSettingsDialog" in page)
check("...and the camera's own settings dialog has a way in too",
      "setSettingsFor(activeCam)" in page)

# React counts hooks per render. A useState declared after the page's early
# returns (error, still loading) runs on some renders and not others, and the
# operator gets a blank page with minified error #310 -- which is what shipped
# the first time this dialog was wired in.
check("the dialog's state is declared above the page's early returns",
      page.index("const [settingsFor, setSettingsFor] = useState(null);")
      < min(page.index("if (error) return <ErrorNote"),
            page.index("return <SkelEditor title=\"Camera Setup\"")),
      "a hook below an early return is error #310: rendered more hooks than "
      "during the previous render")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
