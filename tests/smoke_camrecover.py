# A stalled live camera must be reopened, and a start() must never adopt a
# process it cannot prove is ours.
#
# Both bugs presented as "the camera froze": an app that could never start kept
# spawning short-lived twins that touched the device, and the app that owned
# the camera then sat on its last good frame for ever while the overlay went on
# counting fps.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


print("== an unverifiable process is not ours")
rt = read("systems", "demo", "app", "runtime.py")
# A DYING app.run has an unreadable /proc/<pid>/cwd. Returning True there
# adopted the corpse: start() answered "already running -- re-adopted", wrote
# the dead pid to state.json, and the system never started. Every retry then
# adopted the same ghost.
body = rt.split("def _owns", 1)[1].split("\ndef ", 1)[0]
check("a readable cmdline with an unreadable cwd is NOT claimed",
      "return False" in body.split('os.readlink(f"/proc/{pid}/cwd")', 1)[1])
check("no /proc at all is still trusted (dev boxes)",
      'return True                     # no /proc: not checkable, not a lie' in body)

print("== a stalled live camera is reopened")
cam = read("systems", "demo", "app", "camera.py")
check("recovery is keyed on TIME without a good frame",
      "_REOPEN_AFTER_STALL_S" in cam and "self._last_good" in cam)
# The failure COUNT was the first attempt and it never fired: a stalled USB
# camera still delivers the odd frame between its 10s select() timeouts, and
# each one reset the counter.
check("not on a count of consecutive failures", "_REOPEN_AFTER_FAILS" not in cam)
check("attempts are rate-limited", "self._last_reopen" in cam)
check("the old handle is released before reopening",
      cam.index("old.release()") < cam.index("cv2.VideoCapture(arg, backend) if backend\n                     else"))
check("a reopened camera gets the SAME negotiation as a fresh one",
      "_apply_capture_settings(fresh, backend)" in cam
      and "def _apply_capture_settings" in cam)

# The recovery runs inside the grab thread. An exception there kills the
# thread and makes a recoverable stall permanent -- which is exactly what an
# AttributeError in this call did during development.
check("a fault in the recovery cannot kill the grab thread",
      "try:\n                        self._reopen_live(round(stalled, 1))" in cam)
check("and it reports which camera, by an attribute that exists",
      "self.source, stalled_s" in cam and "self.id, stalled_s" not in cam)


print("== the device is asked for the best rate it can do")
check("the ceiling is discovered by probing, not assumed",
      "_FPS_PROBE" in cam and "cap.set(cv2.CAP_PROP_FPS, _FPS_PROBE)" in cam)
check("a driver that parrots the probe back is not believed",
      "1.0 <= got < _FPS_PROBE" in cam)
check("capped, so the bus is not paid for frames nothing reads",
      "_MAX_DEVICE_FPS" in cam and "min(_MAX_DEVICE_FPS, want)" in cam)
check("floored at the pipeline rate", "max(float(self.fps or 1)" in cam)
check("an explicit [camera].device_fps still wins",
      'self.device_fps = float(self.config.get("device_fps", 0) or 0)' in cam)
# The driver-picked fallback open is the path a camera that ignores size
# requests actually takes -- the very cameras this matters most for.
check("the driver-picked fallback negotiates too",
      cam.count("self._negotiate_fps(cap)") == 2)
check("the negotiated rate is logged", "device rate %.0f fps" in cam)

print("== the app's log is readable while it runs")
rtsrc = read("systems", "demo", "app", "runtime.py")
check("the child's stdout is unbuffered",
      'PYTHONUNBUFFERED="1"' in rtsrc and '"env": env' in rtsrc)

print("== the operator can ask the camera for its rates")
dcsrc = read("core", "services", "detector_control.py")
check("there is a rate probe beside the resolution one",
      "def camera_frame_rates" in dcsrc and "def _v4l2_frame_rates" in dcsrc)
# A UVC camera's ceiling belongs to the MODE: the same sensor does 60fps
# small and a fraction of that at full frame, so a rate list that ignores
# the chosen size lists rates the camera will refuse.
check("it is size-aware", "camera_frame_rates(source, kind=\"\", width=0, height=0)" in dcsrc)
check("an unprobed answer says so, like resolutions",
      '"probed": probed' in dcsrc.split("def camera_frame_rates", 1)[1])
routes = read("core", "routes", "models.py")
check("the route exists and takes the size",
      "cameras/framerates" in routes and "width: int = Query(0)" in routes)
det = read("frontend", "src", "pages", "Detection.jsx")
# One "Ask the camera" button asks for sizes and then rates: askCamera hands
# both probes to askBoth (lib/qualityAsk.js), which runs the rate probe at the
# size the first one landed on. The old separate rates button and its tooltip
# are gone.
ask = det.split("async function askCamera", 1)[1][:600] if "async function askCamera" in det else ""
check("the page has the button", "async function probeFrameRates" in det
      and "probeRates: probeFrameRates" in ask and "askBoth(" in ask)
check("and it fills in the best the camera offers",
      "if (d.max) setCamera((c) => ({ ...c, fps: d.max }));" in det)

print("== a degraded camera is caught, not just a silent one")
# The stall test asks "has ANY frame arrived recently", and a camera limping
# at 0.5fps answers yes every two seconds -- so it never fired on the failure
# an operator sees as "camera fps: 0.5" on the HUD.
check("delivery is compared against the rate the device agreed to",
      "_COLLAPSE_FRACTION" in cam and "self.device_rate or 0" in cam)
check("the two bars are ANDed, so dim light is not treated as a fault",
      "min(_COLLAPSE_FPS, rate * _COLLAPSE_FRACTION)" in cam)

print("== a frozen picture is caught even at full frame rate")
# The HUD read 27.4fps over a still image: grab() was succeeding (that is what
# the number counts) while the kept frame never advanced.
check("the kept frame is fingerprinted", "def _note_frame" in cam
      and "frame[::64, ::64].tobytes()" in cam)
check("and an unchanging picture triggers a reopen", "_frozen_since" in cam)

print("== two apps cannot deadlock consuming each other")
# Each restarted while the other held the registry entry, so each decided the
# OTHER owned the camera. Both consumed, nobody grabbed the device.
check("a stalled consumer gives up and takes the camera",
      "_CONSUMER_GIVE_UP" in cam and "taking the camera instead" in cam)
check("and registers itself, or the other app keeps pointing at nothing",
      cam.count("register_camera(s, self.config[\"_stream_port\"]") == 2)

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
