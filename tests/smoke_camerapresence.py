# A Start checks the camera is really there first, and every word about a
# camera uses the name the page shows (2026-10-07, Tok Aba: the Cameras card
# said Live while a toast said "main is not found").
#
#   * camera_notice.presence / missing: the system's own camwatch probe, one
#     answer per camera, side by side
#   * detector_control.start refuses when every camera is absent, naming
#     the camera as the page does, never by id; the unwatched Starts (boot
#     autostart, reset restart, Power on) pass require_camera=False
#   * the words: "not reachable" for a network camera, "not found" for one
#     plugged in -- the refusal, the waiting word, the Inbox notices
#   * the presence route (core/routes/cameras.py) and the page's Checking /
#     Live / Not found / Unreachable rail
#
#   py -3 tests/smoke_camerapresence.py
import inspect
import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import camera_notice                        # noqa: E402
from core.services import detector_control as dc               # noqa: E402

DEMO = os.path.join(ROOT, "systems", "demo")
bad = 0


def check(label, ok, detail=""):
    global bad
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        bad += 1
        if detail:
            print(f"      {detail}")


def closed_port():
    """A port nothing listens on: bound then released."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


RTSP = f"rtsp://user:secret@127.0.0.1:{closed_port()}/stream"
NET = {"id": "main", "name": "Camera 1", "source": RTSP, "kind": "rtsp"}
USB = {"id": "cam2", "label": "Arducam", "source": "0", "kind": "usb"}
FILE = {"id": "cam3", "source": "file:clip.mp4"}

# -- names -------------------------------------------------------------------
check("display_name: the operator's name first",
      camera_notice.display_name(NET) == "Camera 1")
check("display_name: the scan label when no name",
      camera_notice.display_name(USB) == "Arducam")
check("display_name: the source with its password masked, never the id",
      camera_notice.display_name({"id": "main", "source": RTSP})
      == RTSP.replace("secret", dc.SOURCE_REDACTED),
      camera_notice.display_name({"id": "main", "source": RTSP}))
check("display_name: the id only when there is nothing else",
      camera_notice.display_name({"id": "cam9"}) == "cam9")
check("absence: a network camera is not reachable, a plugged-in one not found",
      camera_notice.absence(NET) == "not reachable"
      and camera_notice.absence(USB) == "not found")

# -- the probe ---------------------------------------------------------------
got = camera_notice.presence(DEMO, [NET, FILE])
check("presence: a network camera whose host refuses is False, a file is None",
      got == {"main": False, "cam3": None}, got)
check("missing: every camera absent names them all",
      camera_notice.missing(DEMO, [NET]) == [NET])
check("missing: a camera that cannot be told keeps the Start",
      camera_notice.missing(DEMO, [NET, FILE]) == [])
check("missing: no cameras, nothing missing", camera_notice.missing(DEMO, []) == [])

word = camera_notice.refusal([NET])
check("refusal: names the camera as the page does, says not reachable, and the fix",
      word.startswith("Camera 1 is not reachable - detection was not started.")
      and "address" in word and "main" not in word, word)
word = camera_notice.refusal([USB])
check("refusal: a USB camera is not found, check the plug",
      word.startswith("Arducam is not found") and "plugged in" in word, word)
wait = camera_notice.waiting(DEMO, [NET])
check("waiting (the unwatched Start's word): the name, not the id",
      wait == "Detection is waiting for the camera: Camera 1 is not reachable. "
              "It starts by itself when the camera is back.", wait)

# -- start() refuses ---------------------------------------------------------
real = (dc._pkg_dir, dc._app_runtime, dc.live_cameras, dc.running_apps,
        dc.sources_elsewhere)
dc._pkg_dir = lambda sd, slug: DEMO
dc._app_runtime = lambda pkg_dir: object()
dc.live_cameras = lambda sd, slug: [NET]
dc.running_apps = lambda sd: []
dc.sources_elsewhere = lambda sd, slug: []
try:
    try:
        dc.start("/x", "demo")
        check("start: refused when the only camera is unreachable", False)
    except ValueError as e:
        check("start: refused when the only camera is unreachable, by name",
              str(e).startswith("Camera 1 is not reachable") and "main" not in str(e),
              str(e))
finally:
    (dc._pkg_dir, dc._app_runtime, dc.live_cameras, dc.running_apps,
     dc.sources_elsewhere) = real

src = inspect.getsource(dc.start)
check("start: the check comes after the budget checks and before the switch",
      src.index("camera_notice.missing") > src.index("MAX_CAMERAS")
      and src.index("camera_notice.missing") < src.index("set_ai("))
for path, where in (("core/services/provision.py", "boot autostart"),
                    ("core/services/retention.py", "reset restart"),
                    ("core/routes/hub.py", "Power on")):
    text = open(os.path.join(ROOT, path), encoding="utf-8").read()
    check(f"{where} starts without the refusal (require_camera=False)",
          "start(" in text and "require_camera=False" in text)

# -- the route and the page --------------------------------------------------
rsrc = open(os.path.join(ROOT, "core", "routes", "cameras.py"), encoding="utf-8").read()
check("the presence route exists, off the event loop (sync def)",
      '"/api/detector/control/cameras/presence"' in rsrc
      and "def detector_cameras_presence(request" in rsrc
      and "async def detector_cameras_presence" not in rsrc)
dsrc = open(os.path.join(ROOT, "frontend", "src", "pages", "Detection.jsx"),
            encoding="utf-8").read()
check("the page asks while stopped, and says Checking / Live / Not found / Unreachable",
      "/api/detector/control/cameras/presence" in dsrc
      and 'label: "Checking"' in dsrc and "absenceWord(c)" in dsrc
      and '"Unreachable" : "Not found"' in dsrc
      and "if (stopped) probePresence()" in dsrc)
check("the running-but-paused card uses the camera's name, not the id",
      "`Detection stopped: ${camName(c)} is" in dsrc)
run_src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
               encoding="utf-8").read()
check("the app's own words name the camera (cam_names), not its id",
      "def cam_names(cfg)" in run_src
      and "paused_text(\n                    [cam_words.get(c, c)" in run_src
      and "no frames from camera {cam_words.get(p.id, p.id)}" in run_src)
cw_src = open(os.path.join(ROOT, "systems", "demo", "app", "camwatch.py"),
              encoding="utf-8").read()
check("the USB probe opens the node and closes it at once (never holds it)",
      "def _openable(node)" in cw_src and "os.close(fd)" in cw_src
      and "O_NONBLOCK" in cw_src)
# -- the fault popup -----------------------------------------------------------
check("the red inline banner is a popup now: once per episode, closes by itself, "
      "the rail's status reopens it",
      "function CamFaultDialog(" in dsrc and "faultSeen.current.add(" in dsrc
      and "if (faultPopup != null && !popupKey) setFaultPopup(null);" in dsrc
      and "setFaultPopup(c.id);" in dsrc
      and "if (!/^reconnecting camera/i.test(camError)) return null;" in dsrc)
check("the popup names the slot, says what to check, keeps Details, and offers "
      "Reconnect / Check again with a spinner",
      "`Can't reach ${slot}`" in dsrc and "`${slot} not found`" in dsrc
      and '"Check again"' in dsrc and '"Reconnect"' in dsrc
      and "<CamErrorDetails detail={detail} />" in dsrc
      and '"Reconnecting…" : "Checking…"' in dsrc)

EM_DASH = chr(0x2014)
for path in ("core/services/camera_notice.py", "core/services/detector_control.py",
             "frontend/src/pages/Detection.jsx", "systems/demo/app/camwatch.py"):
    text = open(os.path.join(ROOT, path), encoding="utf-8").read()
    check(f"{path}: no em dash", EM_DASH not in text)

if bad:
    print(f"\n{bad} check(s) failed")
    sys.exit(1)
print("\nall good")
