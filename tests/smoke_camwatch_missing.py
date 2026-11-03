# A camera that is NOT FOUND stops AI detection; plugged back in, it starts
# again by itself.
#
# Tok Aba (2026-10-07): "if there is no camera then stop the AI detection",
# and when the camera is plugged back in, detection starts again by itself.
#
# smoke_camwatch.py pins the restart watchdog for a camera that is on the box
# but silent (its fake cameras cannot be told present or absent, so they keep
# that behaviour here too). This one pins the other fault, a camera that is
# not there at all (app/camwatch.py present / Presence / CamWatch.pause):
#   1. the presence probe: a device path that comes and goes, an RTSP host
#      that refuses and then accepts, sources it cannot tell (never paused)
#   2. the arithmetic on a fake clock: a pause only after one restart, only
#      with EVERY camera absent; a camera present-but-silent keeps restarting
#   3. the real loop, one camera on a device path: after one restart the
#      detection stops (chip and camera released, fake chip counts), the
#      user's switch reads ON, the dot and ai_paused say why, free rules run,
#      no restarts while it waits; plugged back in, it starts by itself
#   4. the Inbox: one notice when it stops, one when it starts, none between
#   5. the user's Stop while it waits wins; the user's Start while the camera
#      is still missing waits at once (no chip) and the master says so
#   6. two cameras: one missing keeps the per-camera reopen; both missing
#      stops; one back starts detection and the other is reopened alone
#   7. an RTSP camera whose host refuses, then accepts
#
# Every "it happened" is a poll with a generous deadline, every "nothing
# happened" waits for the loop's own heartbeat clock (run_all runs this in a
# parallel pool).
import copy
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import camwatch                                        # noqa: E402
from app import detector as detector_mod                       # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def wait(pred, limit=20.0):
    end = time.time() + limit
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.03)
    return False


TMP = tempfile.mkdtemp()


def device(name):
    """A stand-in for a camera's device node: a file that exists while the
    camera is plugged in."""
    path = os.path.join(TMP, name)
    plug(path)
    return path


def plug(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("cam")


def unplug(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Rtsp:
    """A host that accepts TCP on a port while `up`, and refuses when not."""

    def __init__(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.sock = None

    def up(self):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", self.port))
        s.listen(8)
        self.sock = s

    def down(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    @property
    def is_up(self):
        return self.sock is not None

    def url(self):
        return f"rtsp://user:pw@127.0.0.1:{self.port}/live"


# == 1. the presence probe ===================================================
print("== presence")
dev = device("video-a")
check("a device path that exists: present", camwatch.present(dev) is True)
unplug(dev)
check("...removed: not found", camwatch.present(dev) is False)
rt = Rtsp()
check("an RTSP host that refuses: not found",
      camwatch.present(rt.url()) is False)
rt.up()
check("...that accepts: present", camwatch.present(rt.url()) is True)
rt.down()
check("synthetic, file:, gst: and udp:// cannot be told (never paused)",
      [camwatch.present(s) for s in ("synthetic", "file:clip.mp4",
                                     "gst:videotestsrc ! appsink",
                                     "udp://0.0.0.0:5000", "")]
      == [None] * 5)
check("all_missing: every camera absent names them",
      camwatch.all_missing({"cam2": os.path.join(TMP, "nope"),
                            "cam1": rt.url()}) == ["cam1", "cam2"])
plug(dev)
check("...one of them there: nothing is missing",
      camwatch.all_missing({"cam1": dev, "cam2": rt.url()}) == [])
check("...one that cannot be told: nothing is missing",
      camwatch.all_missing({"cam1": "synthetic",
                            "cam2": os.path.join(TMP, "nope")}) == [])
pr = camwatch.Presence(0.1)
t0 = time.time()
first = pr.poll(t0, {"cam1": dev})
check("Presence: the first look starts a sweep off the loop, answers {}",
      first == {})
check("...and the sweep's answer arrives",
      wait(lambda: pr.poll(time.time(), {"cam1": dev}) == {"cam1": True}))
check("...an answer older than `since` is not used",
      pr.poll(time.time(), {"cam1": dev}, since=time.time() + 60) == {})
check("...nor one for other cameras",
      pr.poll(time.time(), {"cam9": dev}) == {})

# == 2. the arithmetic =======================================================
print("== the watchdog's arithmetic")
ABSENT = {"cam1": False}
w = camwatch.CamWatch(5.0)
w.arm(0.0)
check("5 s silent, camera absent: the first answer is still a restart",
      w.step(5.0, True, ["cam1"], {}, present=ABSENT)
      == ("restart", "full", ["cam1"]))
w.arm(5.5)
act = w.step(10.0, True, ["cam1"], {}, present=ABSENT)
check("the restart did not find it: pause, not another restart",
      act and act[0] == "pause" and act[1]["cameras"] == ["cam1"]
      and w.paused and not w.restarting, act)
rep = w.report()
check("...reported as a pause beside the episode",
      rep["pause"]["state"] == "paused" and rep["pause"]["by"] == "watchdog"
      and rep["state"] == "paused" and rep["failed"] is False, rep)
w2 = camwatch.CamWatch(5.0)
w2.arm(0.0)
w2.step(5.0, True, ["cam1"], {})
check("present but silent (a stall): it keeps restarting",
      w2.step(10.0, True, ["cam1"], {}, present={"cam1": True})[0]
      == "restart")
check("...and a camera that cannot be told keeps restarting",
      w2.step(20.0, True, ["cam1"], {}, present={"cam1": None})[0]
      == "restart")
check("...and no presence answer yet keeps restarting",
      w2.step(50.0, True, ["cam1"], {}, present={})[0] == "restart")
w3 = camwatch.CamWatch(5.0)
w3.arm(0.0)
fr = {"cam1": 4.9}                  # cam1 delivering, cam2 not
w3.step(5.0, True, ["cam1", "cam2"], fr)
w3.arm(5.0)
fr["cam1"] = 9.9
act = w3.step(10.0, True, ["cam1", "cam2"], fr,
              present={"cam1": True, "cam2": False})
check("two cameras, one absent: no pause, the absent one is reopened alone",
      act == ("restart", "cameras", ["cam2"]) and not w3.paused, act)
w4 = camwatch.CamWatch(5.0)
w4.arm(0.0)
w4.step(5.0, True, ["cam1", "cam2"], {})
act = w4.step(10.0, True, ["cam1", "cam2"], {},
              present={"cam1": False, "cam2": False})
check("two cameras, both absent: pause", act and act[0] == "pause"
      and act[1]["cameras"] == ["cam1", "cam2"], act)
w4.resume(20.0, ["cam1"], ["cam2"])
check("one back: resumed, the other carries on as a restarted episode",
      w4.report()["pause"]["state"] == "resumed"
      and w4.report()["pause"]["back"] == ["cam1"]
      and w4.episode["cameras"] == ["cam2"] and w4.episode["attempts"] == 1)
act = w4.step(30.1, True, ["cam1", "cam2"], {"cam1": 30.0},
              present={"cam1": True, "cam2": False})
check("...so its next try reopens it alone", act
      == ("restart", "cameras", ["cam2"]), act)
w5 = camwatch.CamWatch(5.0)
w5.pause(1.0, ["cam1"], by="start")
key = w5.hold["id"]
w5.disarm(2.0)
check("the user's Stop while it waits: stopped, no longer paused",
      w5.hold["state"] == "stopped" and not w5.paused)
w5.pause(3.0, ["cam1"], by="start")
check("...a Start with the camera still missing is the same absence (same id)",
      w5.hold["id"] == key and w5.paused)

# == 3+. the real loop =======================================================
RealCamera = apprun.camera_mod.Camera
CAM = {"opens": [], "closes": 0, "live": set()}
RTSP = {}


class FakeCamera(RealCamera):
    """A synthetic picture behind a configured source that can vanish: a
    device path that is deleted, or an RTSP host that refuses."""

    def __init__(self, config):
        self.src = str(config.get("source") or "")
        self.cid = str(config.get("id") or "")
        super().__init__(dict(config, source="synthetic"))

    def _there(self):
        if self.src.startswith("rtsp://"):
            return any(r.is_up and str(r.port) in self.src
                       for r in RTSP.values())
        return os.path.exists(self.src)

    def open(self):
        CAM["opens"].append((self.cid, time.time()))
        CAM["live"].add(id(self))
        if not self._there():
            self.error = "no device"
            return False
        return super().open()

    def read(self):
        if not self._there():
            return None
        return super().read()

    def close(self):
        if id(self) in CAM["live"]:
            CAM["closes"] += 1
            CAM["live"].discard(id(self))
        return super().close()


DET = {"builds": [], "closes": 0, "live": 0}


class FakeChip(detector_mod.Detector):
    """Registered as "hailo": counts builds and releases."""

    name = "fakechip"

    def __init__(self, config):
        super().__init__(config)
        DET["builds"].append(time.time())
        DET["live"] += 1
        self._open = True

    def detect(self, frame):
        return [{"label": "person", "confidence": 0.9,
                 "box": [20, 20, 120, 200]}]

    def close(self):
        if self._open:
            self._open = False
            DET["closes"] += 1
            DET["live"] -= 1


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.05
camwatch.BACKOFF_S = (1.0, 2.0, 4.0, 8.0)
camwatch.PRESENCE_S = 0.3

CFG = {}
MT = [1.0]
DATA = [None]


def set_cfg(**kw):
    CFG.update(kw)
    MT[0] += 1.0


apprun.config_mod.load = lambda *a, **k: copy.deepcopy(CFG)
apprun.config_mod.data_dir = lambda *a, **k: DATA[0]
apprun._config_mtime = lambda: MT[0]


def state():
    try:
        with open(os.path.join(DATA[0], "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def counts():
    return state().get("counts") or {}


def settle(seconds, limit=20.0):
    start = state().get("heartbeat") or time.time()
    return wait(lambda: (state().get("heartbeat") or 0.0) >= start + seconds,
                limit)


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


LOG = LogCapture()
logging.getLogger().addHandler(LOG)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run_loop(cfg, drive):
    CFG.clear()
    CFG.update(copy.deepcopy(cfg))
    MT[0] += 1.0
    DATA[0] = tempfile.mkdtemp()
    apprun._stop.clear()

    def wrapped():
        try:
            drive()
        except Exception as exc:                               # noqa: BLE001
            import traceback
            traceback.print_exc()
            check("the driver ran", False, repr(exc))
        finally:
            apprun._stop.set()
    t = threading.Thread(target=wrapped, daemon=True)
    t.start()
    rc = apprun.main([])
    t.join(5)
    check("the app exits cleanly", rc == 0, rc)
    check("...releasing what it held", DET["live"] == 0 and not CAM["live"],
          (CAM, DET))


NAMES = {"cam1": "Front door", "cam2": "Loading bay"}


def base(cameras):
    return {
        "ai": {"on": True},
        "app": {"stream_port": free_port(), "stream_bind": "127.0.0.1",
                "camera_lost_restart_s": 1.5},
        # Named as an operator names them: the AI dot's reason calls a
        # camera by what the page shows (run.py cam_names), not its id.
        "cameras": [{"id": cid, "name": NAMES.get(cid, ""), "source": src,
                     "enabled": True, "fps": 10,
                     "width": 320, "height": 240} for cid, src in cameras],
        "detector": {"kind": "hailo", "model": "yolov8s"},
        "capture": {"snap": False, "record": False},
        "storage": {"keep_snapshots": 5},
        "zones": [], "outputs": [],
        "logic": {"functions": ["kick"], "outputs": [], "version": 99},
        "functions": [
            {"id": "kick", "name": "Kick", "type": "inject", "enabled": True,
             "params": {"ptype": "number", "payload": "1",
                        "interval_s": 0.3}},
            {"id": "ppl", "name": "People", "type": "frame_count",
             "enabled": True, "params": {}}],
    }


from core.services import camera_notice                         # noqa: E402
from core.services import detector_control as dc               # noqa: E402

dc.system_config = lambda sd, slug: {"app": {"display_name": "Gate"}}
CFG_DIR = tempfile.mkdtemp()
NOTES = []


def notify(kind, title, body="", config_dir=None, **kw):
    NOTES.append((kind, title, body))


def post():
    return camera_notice.post("/x", "demo", CFG_DIR, notify, raw=state())


def paused():
    st = state()
    return st.get("ai_paused") == "camera_missing"


DEV1 = device("cam1")


def single():
    print("== one camera on a device path, unplugged")
    check("detecting: dot green, one chip, one camera",
          wait(lambda: state().get("ai_ok") is True)
          and DET["live"] == 1 and len(CAM["live"]) == 1, (state(), DET))
    check("...the detection-fed rule publishes",
          wait(lambda: counts().get("fn_ppl_count") == 1), counts())
    builds0 = len(DET["builds"])
    unplug(DEV1)
    check("not found after one restart: detection STOPS",
          wait(paused), state().get("cam_watch"))
    check("...exactly one restart was tried first",
          len(DET["builds"]) - builds0 == 1, len(DET["builds"]) - builds0)
    check("...chip and camera released, as Stop does",
          wait(lambda: DET["live"] == 0 and not CAM["live"]), (DET, CAM))
    # state() is a read of the file the app rewrites; a read that lands mid-
    # rewrite (Windows) is {} for that instant. Wait for the condition.

    def red_with_reason():
        st = state()
        return (st.get("ai_on") is True and st.get("ai_ok") is False
                and "camera not found (Front door)" in str(st.get("ai_error"))
                and "starts again by itself" in str(st.get("ai_error")))
    check("...the user's switch still reads ON, the dot red with the reason",
          wait(red_with_reason), state())
    runs0 = counts().get("fn_kick_runs", 0)
    check("...the free rules keep running",
          wait(lambda: counts().get("fn_kick_runs", 0) > runs0), counts())
    c = counts()
    check("...detection-fed numbers absent (no fake zeros), held as AI off",
          "fn_ppl_count" not in c and "objects_in_frame" not in c
          and "ppl" in (state().get("ai_inactive") or []), sorted(c))
    n, o = len(DET["builds"]), len(CAM["opens"])
    ran = settle(3.0)
    check("while it waits: no restarts, no chip, no camera opens",
          ran and len(DET["builds"]) == n and len(CAM["opens"]) == o
          and DET["live"] == 0 and paused(), (ran, DET, CAM))
    check("one log line said it stopped",
          len([ln for ln in LOG.lines if "not found - AI detection stopped"
               in ln]) == 1)

    print("== notices")
    got = post()
    check("ONE notice when it stops",
          got == ["missing"] and NOTES[-1][2] == "Camera cam1 on Gate was "
          "not found, so detection stopped; it starts again when the camera "
          "is back.", (got, NOTES))
    check("...none while it waits", post() == [] and post() == [])

    print("== plugged back in")
    plug(DEV1)
    check("detection starts again by itself: dot green, chip back",
          wait(lambda: state().get("ai_ok") is True and DET["live"] == 1),
          (state().get("ai_error"), DET))
    check("...no longer paused, the pause reported as resumed",
          not paused() and (state().get("cam_watch") or {}).get(
              "pause", {}).get("state") == "resumed", state().get("cam_watch"))
    check("...the detection-fed rule publishes again",
          wait(lambda: counts().get("fn_ppl_count") == 1), counts())
    got = post()
    check("ONE notice when it starts again",
          got == ["resumed"] and NOTES[-1][2] == "Camera cam1 on Gate is "
          "back; detection started again.", (got, NOTES))
    check("...and only one", post() == [])

    print("== the user's Stop while it waits")
    unplug(DEV1)
    check("unplugged again: stopped again", wait(paused))
    check("...a new absence, a new notice", post() == ["missing"])
    set_cfg(ai={"on": False})
    check("the user's Stop: switch OFF, no longer paused",
          wait(lambda: state().get("ai_on") is False and not paused()),
          state())
    n = len(DET["builds"])
    plug(DEV1)
    ran = settle(3.0)
    check("...camera back, and it STAYS stopped (the user wins)",
          ran and len(DET["builds"]) == n and DET["live"] == 0
          and state().get("ai_on") is False, (ran, DET))
    check("...no 'started again' notice for a Stop", post() == [])

    print("== the user's Start while the camera is missing")
    unplug(DEV1)
    n = len(DET["builds"])
    set_cfg(ai={"on": True})
    check("Start with no camera: it waits at once",
          wait(lambda: paused() and state().get("ai_on") is True), state())
    ran = settle(2.0)
    check("...without opening the chip or the camera",
          ran and len(DET["builds"]) == n and DET["live"] == 0
          and not CAM["live"], (DET, CAM))
    check("...the log says it is waiting for the camera",
          any("is not found - waiting for it" in ln for ln in LOG.lines))
    msg = camera_notice.waiting(os.path.join(ROOT, "systems", "demo"),
                                [{"id": "cam1", "name": "Front door",
                                  "source": DEV1}])
    check("...and the master's Start answer says so in plain words",
          msg == "Detection is waiting for the camera: Front door is not "
                 "found. "
                 "It starts by itself when the camera is back.",
          msg)
    check("...a camera that is there: no waiting word",
          camera_notice.waiting(os.path.join(ROOT, "systems", "demo"),
                                [{"id": "cam1", "source": ROOT}]) == "")
    real = (dc._pkg_dir, dc.live_cameras)
    dc._pkg_dir = lambda sd, slug: os.path.join(ROOT, "systems", "demo")
    dc.live_cameras = lambda sd, slug: [{"id": "cam1", "name": "Front door",
                                         "source": DEV1}]
    word = camera_notice.start_word("/x", "demo", {"ok": True,
                                                   "message": "started"})
    dc._pkg_dir, dc.live_cameras = real
    check("...the power route's Start answer carries waiting: true",
          word.get("waiting") is True and word["message"] == msg, word)
    rsrc = open(os.path.join(ROOT, "core", "routes", "models.py"),
                encoding="utf-8").read().split("async def detector_power", 1)[1]
    check("...and the route asks for it on a Start",
          "camera_notice.start_word" in rsrc.split("@router", 1)[0])
    check("...the same absence the user stopped: no second notice",
          post() == [])
    plug(DEV1)
    check("plugged in: detection starts by itself",
          wait(lambda: state().get("ai_ok") is True and DET["live"] == 1))
    check("...with its one 'started again' notice", post() == ["resumed"])


run_loop(base([("cam1", DEV1)]), single)

DEV_A, DEV_B = device("camA"), device("camB")
MULTI = base([("cam1", DEV_A), ("cam2", DEV_B)])
MULTI["functions"].append({"id": "ppl2", "name": "People 2",
                           "type": "frame_count", "enabled": True,
                           "params": {"camera": "cam2"}})


def multi():
    print("== two cameras")
    check("both detecting",
          wait(lambda: state().get("ai_ok") is True
               and counts().get("fn_ppl2_count") == 1), state())
    unplug(DEV_B)
    check("cam2 missing, cam1 there: the per-camera reopen, no stop",
          wait(lambda: (state().get("cam_watch") or {}).get("attempts", 0)
               >= 2) and not paused() and DET["live"] == 1,
          state().get("cam_watch"))
    check("...cam1 keeps its numbers", "cam_cam1_objects" in counts()
          and "fn_ppl2_count" not in counts(), counts())
    unplug(DEV_A)
    check("both missing: detection stops", wait(paused, 30),
          state().get("cam_watch"))
    check("...chip released", wait(lambda: DET["live"] == 0), DET)
    check("...one notice naming both", post() == ["missing"]
          and "cam1, cam2" in NOTES[-1][2], NOTES[-1:])
    plug(DEV_A)
    check("cam1 back: detection starts again, cam1 detecting",
          wait(lambda: DET["live"] == 1 and not paused()
               and "cam_cam1_objects" in counts()), (DET, state()))
    b = len(DET["builds"])
    check("...the notice names the camera that came back",
          post() == ["resumed"] and "Camera cam1 on Gate is back"
          in NOTES[-1][2], NOTES[-1:])
    check("...cam2, still missing, is reopened alone (no chip rebuild)",
          wait(lambda: any("reopening that camera" in ln
                           for ln in LOG.lines[-20:]), 30)
          and len(DET["builds"]) == b and not paused(),
          (len(DET["builds"]) - b, LOG.lines[-5:]))
    plug(DEV_B)
    check("cam2 back: both detecting",
          wait(lambda: state().get("ai_ok") is True
               and counts().get("fn_ppl2_count") == 1, 30),
          (state().get("ai_error"), state().get("cam_watch")))


run_loop(MULTI, multi)

RT = Rtsp()
RT.up()
RTSP["a"] = RT


def rtsp():
    print("== an RTSP camera")
    check("detecting", wait(lambda: state().get("ai_ok") is True))
    RT.down()
    check("the host refuses: detection stops", wait(paused),
          state().get("cam_watch"))
    check("...chip released", wait(lambda: DET["live"] == 0), DET)
    RT.up()
    check("the host accepts again: detection starts by itself",
          wait(lambda: state().get("ai_ok") is True and DET["live"] == 1),
          state().get("ai_error"))


run_loop(base([("cam1", RT.url())]), rtsp)
RT.down()

EM_DASH = chr(0x2014)
for name in ("Main.jsx", "Zones.jsx", "Detection.jsx"):
    src = open(os.path.join(ROOT, "frontend", "src", "pages", name),
               encoding="utf-8").read()
    check(f"{name}: no em dash", EM_DASH not in src)
zsrc = open(os.path.join(ROOT, "frontend", "src", "pages", "Zones.jsx"),
            encoding="utf-8").read()
check("the Detection page says it plainly instead of 'busy'",
      "Detection stopped: camera not found." in zsrc
      and "It starts again by itself when the camera is plugged back in."
      in zsrc and "stalled && !waitingCam" in zsrc)
for path in ("systems/demo/app/camwatch.py", "core/services/camera_notice.py"):
    check(f"{path}: no em dash", EM_DASH not in open(
        os.path.join(ROOT, path), encoding="utf-8").read())

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all camera-missing checks passed")
