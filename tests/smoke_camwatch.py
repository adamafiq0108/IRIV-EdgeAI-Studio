# A camera that stops answering restarts AI detection by itself.
#
# Tok Aba (2026-10-06): "if the camera disconnected, then after 5s if camera
# still not detected, restart the AI detection automatically."
#
# Two halves. The watchdog's arithmetic (app/camwatch.py) on a fake clock:
# when it fires, the backoff after it, what a multi-camera system restarts,
# and that the user's switch always wins. Then the real app loop, as
# smoke_aiswitch.py runs it, with a camera that can be unplugged and a fake
# chip that counts its builds and releases -- shortened timings (1.5 s instead
# of 5, backoff 1/2/4/8 instead of 5/10/30/60) so it runs in seconds:
#   1. a camera that goes silent: detection restarts (camera AND chip
#      released and reacquired), the dot says why, the user's switch reads
#      on throughout, detection-fed numbers are absent (never zero) and the
#      free rules keep running
#   2. still missing: the retries back off, one log line each
#   3. the admins get ONE notice after the first failed restart and one when
#      the camera is back (core/services/camera_notice.py, posted by the
#      master's app watcher)
#   4. a user Stop that lands in the middle of a restart wins
#   5. with detection off nothing is ever restarted
#   6. two cameras, one lost: the first restart is the whole detection, the
#      next only the lost camera; the good camera keeps its numbers
#
# The real-loop half runs under run_all's parallel pool (and GitHub's CI), so
# nothing here is a fixed sleep or a wall-clock ceiling: every "it happened"
# is a poll with a generous deadline, every "nothing happened for N s" waits
# for the loop's own heartbeat clock to advance N s, and the backoff is read
# from the watchdog's own report (cam_watch.next_s, written at once on every
# attempt) with only the LOWER bound -- never sooner than the backoff --
# checked on the wall clock. A loaded box can stretch a restart by seconds;
# it cannot make one come early.
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


# == the watchdog on a fake clock ============================================
print("== the watchdog's arithmetic")
w = camwatch.CamWatch(5.0)
check("not armed (detection off): nothing is ever lost",
      w.lost(100.0, ["cam1"], {}) == [])
check("detection off: step never restarts",
      all(w.step(t, False, ["cam1"], {}) is None for t in (0, 10, 100)))
w = camwatch.CamWatch(5.0)
w.arm(0.0)
frames = {"cam1": 0.0}
check("4.9 s without a frame: not yet",
      w.step(4.9, True, ["cam1"], frames) is None)
act = w.step(5.0, True, ["cam1"], frames)
check("5 s without a frame: restart the whole detection",
      act == ("restart", "full", ["cam1"]), act)
check("...and the episode is not yet anyone's business",
      w.report()["failed"] is False and w.report()["state"] == "restarting",
      w.report())
times, t = [5.0], 5.0
while len(times) < 7:
    t = round(t + 0.1, 1)
    a = w.step(t, True, ["cam1"], frames)
    if a:
        times.append(t)
        w.arm(t)                    # what run.py does after a full restart
gaps = [round(b - a, 1) for a, b in zip(times, times[1:])]
check("still missing: retries after 5, 10, 30, then every 60 s",
      gaps == [5.0, 10.0, 30.0, 60.0, 60.0, 60.0], gaps)
check("the first FAILED restart marks the episode for the admins",
      w.report()["failed"] is True and w.report()["attempts"] == 7, w.report())
frames["cam1"] = t + 0.5
act = w.step(t + 0.6, True, ["cam1"], frames)
check("a frame after the restart: recovered",
      act and act[0] == "recovered" and act[1]["attempts"] == 7, act)
check("...reported as recovered, for the master's one notice",
      w.report()["state"] == "recovered" and w.report()["failed"], w.report())
check("...and a healthy camera starts nothing new",
      w.step(t + 3, True, ["cam1"], frames) is None and not w.restarting)

w = camwatch.CamWatch(5.0)
w.arm(0.0)
w.step(5.0, True, ["cam1"], {})
w.step(10.0, True, ["cam1"], {})
check("the user's Stop mid-episode: it ends silently (no recovery)",
      w.step(11.0, False, ["cam1"], {}) is None and not w.restarting
      and w.report()["state"] == "stopped", w.report())
check("...and the watchdog stays down while detection is off",
      w.step(100.0, False, ["cam1"], {}) is None and w.armed_at is None)

w = camwatch.CamWatch(5.0)
w.arm(0.0)
check("a camera still connecting is not judged",
      w.step(9.0, True, ["cam1"], {}, opening={"cam1"}) is None)
check("...a restart is not fired into a reopen in flight",
      w.step(9.0, True, ["cam1"], {}) == ("restart", "full", ["cam1"])
      and w.step(14.5, True, ["cam1"], {}, opening={"cam1"}) is None
      and w.step(14.6, True, ["cam1"], {})[0] == "restart")

w = camwatch.CamWatch(5.0)
w.arm(0.0)
fr = {"cam1": 0.0, "cam2": 0.0}
first = second = None
for i in range(1, 200):
    now = i * 0.1
    fr["cam1"] = now                # cam1 keeps delivering, cam2 does not
    a = w.step(now, True, ["cam1", "cam2"], fr)
    if a and first is None:
        first = a
        w.arm(now)
    elif a and second is None:
        second = a
check("two cameras, one lost: the first restart is the whole detection",
      first == ("restart", "full", ["cam2"]), first)
check("...the retry only the lost camera, the other is healthy",
      second == ("restart", "cameras", ["cam2"]), second)
w = camwatch.CamWatch(5.0)
w.arm(0.0)
w.step(5.0, True, ["cam1", "cam2"], {})
check("two cameras, both lost: every retry is the whole detection",
      w.step(10.0, True, ["cam1", "cam2"], {}) == (
          "restart", "full", ["cam1", "cam2"]))
w = camwatch.CamWatch(0)
w.arm(0.0)
check("camera_lost_restart_s = 0 switches it off",
      w.step(100.0, True, ["cam1"], {}) is None)

# == the real loop ============================================================
RealCamera = apprun.camera_mod.Camera
CAM = {"opens": [], "closes": 0, "live": set()}
UNPLUGGED = set()


class FakeCamera(RealCamera):
    """A synthetic camera that can be unplugged: no frames, and a reopen
    fails, until it is plugged back in."""

    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))
        self.cid = str(config.get("id") or "")

    def open(self):
        CAM["opens"].append((self.cid, time.time()))
        CAM["live"].add(id(self))
        if self.cid in UNPLUGGED:
            self.error = "no device"
            return False
        return super().open()

    def read(self):
        if self.cid in UNPLUGGED:
            return None
        return super().read()

    def close(self):
        if id(self) in CAM["live"]:
            CAM["closes"] += 1
            CAM["live"].discard(id(self))
        return super().close()


DET = {"builds": [], "closes": 0, "live": 0, "stop_on_release": False}


class FakeChip(detector_mod.Detector):
    """Registered as "hailo": counts builds (with the time) and releases."""

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
            if DET["stop_on_release"]:
                # The user presses Stop while the watchdog's restart has the
                # chip released: exactly between its two halves.
                DET["stop_on_release"] = False
                set_cfg(ai={"on": False})


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.05
camwatch.BACKOFF_S = (1.0, 2.0, 4.0, 8.0)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


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


def settle(seconds, limit=20.0):
    """Let the loop run for `seconds` of ITS OWN time: wait until the state
    heartbeat (written at least once a second while the loop runs) is that
    much past where it stands now. A "nothing happened" check after this
    means the loop had its chance, however slowly the box is running."""
    start = state().get("heartbeat") or time.time()
    return wait(lambda: (state().get("heartbeat") or 0.0) >= start + seconds,
                limit)


class LogCapture(logging.Handler):
    """The app's log lines, as the master's app.log would hold them. The app
    runs in this process, so its logging root is ours: faults.install()
    points it at stdout and the WARNING spool, and this keeps every line."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


LOG = LogCapture()
logging.getLogger().addHandler(LOG)


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


BASE = {
    "ai": {"on": True},
    "app": {"stream_port": free_port(), "stream_bind": "127.0.0.1",
            "camera_lost_restart_s": 1.5},
    # "synthetic" rather than a device: the loop's fallback-reclaim (a
    # camera on a stand-in source retries its configured one every 10 s)
    # would otherwise reopen these behind the watchdog's back.
    # Named, as an operator names them: the AI dot's reason and the log
    # call a camera by what the page shows (run.py cam_names), not its id.
    "cameras": [{"id": "cam1", "name": "Front door", "source": "synthetic",
                 "enabled": True, "fps": 10,
                 "width": 320, "height": 240}],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
    "zones": [],
    "outputs": [],
    "logic": {"functions": ["kick"], "outputs": [], "version": 99},
    "functions": [
        {"id": "kick", "name": "Kick", "type": "inject", "enabled": True,
         "params": {"ptype": "number", "payload": "1", "interval_s": 0.3}},
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


def single():
    try:
        _single()
    finally:
        UNPLUGGED.clear()


def _single():
    print("== one camera, unplugged")
    check("detecting: dot green, one chip, one camera",
          wait(lambda: state().get("ai_ok") is True)
          and DET["live"] == 1 and len(CAM["live"]) == 1, (state(), DET))
    check("...the detection-fed rule publishes",
          wait(lambda: counts().get("fn_ppl_count") == 1), counts())
    builds0, opens0 = len(DET["builds"]), len(CAM["opens"])
    lost_at = time.time()
    UNPLUGGED.add("cam1")
    seen_off, next_s = [], {}

    def sample(want):
        """Poll: the switch as the pages read it, and each attempt's own
        backoff from the watchdog's report (an urgent state field, so every
        attempt is written the moment it is made)."""
        st = state()
        if st.get("ai_on") is False:
            seen_off.append(st)
        cw = st.get("cam_watch") or {}
        if cw.get("attempts") and cw.get("next_s") is not None:
            next_s.setdefault(cw["attempts"], cw["next_s"])
        return len(DET["builds"]) >= builds0 + want
    restarted = wait(lambda: sample(1))
    took = DET["builds"][builds0] - lost_at if restarted else None
    check("silent for 1.5 s: AI detection restarts by itself", restarted,
          DET)
    # The lower bound is the guarantee (the last frame is at most 0.1 s
    # before the unplug); how long the restart itself takes is the box's.
    check("...after the configured wait, not before",
          took is not None and took >= 1.4, took)
    check("...camera and chip both released and reacquired",
          wait(lambda: DET["closes"] >= 1 and len(CAM["opens"]) > opens0
               and DET["live"] == 1), (DET, CAM))
    check("...the dot says why, in plain words",
          wait(lambda: "camera lost, restarting detection (Front door)"
               in str(state().get("ai_error"))), state().get("ai_error"))
    st = state()
    check("...red, and the user's switch still reads ON",
          st.get("ai_ok") is False and st.get("ai_on") is True, st)
    runs0 = counts().get("fn_kick_runs", 0)
    check("...the free rules keep running",
          wait(lambda: counts().get("fn_kick_runs", 0) > runs0), counts())
    c = counts()
    check("...detection-fed numbers are absent, not zero",
          "fn_ppl_count" not in c and "objects_in_frame" not in c
          and "cam_cam1_objects" not in c, sorted(c))
    check("...and the pages are told which rules are held",
          state().get("ai_inactive") == ["ppl"], (state().get("ai_inactive"), state().get("ai_on"), state().get("cam_watch"), state().get("ai_kept")))

    print("== still missing: backoff")
    wait(lambda: sample(4), 30)
    b = DET["builds"][builds0:builds0 + 4]
    gaps = [round(y - x, 1) for x, y in zip(b, b[1:])]
    # Each gap is the backoff plus one restart: the restart's length is the
    # box's, so the wall clock only pins "never sooner"; the backoff itself
    # is what the watchdog said after each attempt.
    check("retries back off: ~1, ~2, ~4 s apart (5/10/30/60 in production)",
          len(gaps) == 3 and all(g >= lo for g, lo in zip(gaps,
                                                          (0.9, 1.9, 3.9)))
          and [next_s.get(n) for n in (1, 2, 3)] == [1.0, 2.0, 4.0],
          (gaps, next_s))
    check("the switch never read OFF during any restart", not seen_off,
          seen_off[:1])
    log = "\n".join(LOG.lines)
    lines = [ln for ln in LOG.lines if "is not giving frames" in ln]
    check("one log line per attempt, no more",
          len(lines) == len(DET["builds"]) - builds0,
          (len(lines), len(DET["builds"]) - builds0))
    check("no 'AI detection switched' lines from the restarts",
          "AI detection switched off" not in log)

    print("== notices")
    raw = state()
    check("the episode is marked failed for the admins",
          (raw.get("cam_watch") or {}).get("failed") is True,
          raw.get("cam_watch"))
    got = camera_notice.post("/x", "demo", CFG_DIR, notify, raw=raw)
    check("ONE Inbox notice after the first failed restart",
          got == ["lost"] and len(NOTES) == 1
          and NOTES[0][2] == "Camera cam1 on Gate is not answering; "
          "detection is restarting itself every minute until it comes back.",
          NOTES)
    check("...never again for the same episode (every minute, a master "
          "restart)", camera_notice.post("/x", "demo", CFG_DIR, notify,
                                         raw=state()) == []
          and len(NOTES) == 1, NOTES)

    print("== plugged back in")
    UNPLUGGED.discard("cam1")
    back = wait(lambda: state().get("ai_ok") is True, 30)
    check("the next restart brings it back: dot green", back,
          state().get("ai_error"))
    check("...reported as recovered",
          (state().get("cam_watch") or {}).get("state") == "recovered",
          state().get("cam_watch"))
    check("...the detection-fed rule publishes again",
          wait(lambda: counts().get("fn_ppl_count") == 1), counts())
    got = camera_notice.post("/x", "demo", CFG_DIR, notify, raw=state())
    check("ONE recovery notice",
          got == ["back"] and "Camera cam1 on Gate is answering again"
          in NOTES[-1][2], NOTES)
    check("...and only one", camera_notice.post(
        "/x", "demo", CFG_DIR, notify, raw=state()) == [])
    n = len(DET["builds"])
    ran = settle(2.5)
    check("a healthy camera is left alone", ran and len(DET["builds"]) == n,
          (ran, len(DET["builds"]) - n))

    print("== the user's Stop in the middle of a restart")
    DET["stop_on_release"] = True
    UNPLUGGED.add("cam1")
    released = wait(lambda: DET["stop_on_release"] is False)
    check("the restart released the chip and the Stop landed", released)
    n = len(DET["builds"])
    off = wait(lambda: state().get("ai_on") is False and DET["live"] == 0
               and not CAM["live"])
    check("...the Stop wins: chip and camera stay released", off,
          (DET, CAM, state().get("ai_on")))
    ran = settle(3.5)
    check("...and nothing turns detection back on",
          ran and len(DET["builds"]) == n and DET["live"] == 0
          and not CAM["live"] and state().get("ai_on") is False,
          (ran, DET, state().get("ai_on")))
    check("...the episode ended without a recovery",
          (state().get("cam_watch") or {}).get("state") == "stopped",
          state().get("cam_watch"))

    print("== detection off: never restarted")
    n, o = len(DET["builds"]), len(CAM["opens"])
    ran = settle(3.0)
    check("an unplugged camera with detection OFF restarts nothing",
          ran and len(DET["builds"]) == n and len(CAM["opens"]) == o
          and not str(state().get("ai_error") or "").startswith(
              "camera lost"),
          (ran, DET, CAM))
    UNPLUGGED.discard("cam1")


run_loop(BASE, single)
check("...releasing what it held", DET["live"] == 0 and not CAM["live"],
      (CAM, DET))

MULTI = copy.deepcopy(BASE)
MULTI["app"]["stream_port"] = free_port()
MULTI["cameras"].append({"id": "cam2", "name": "Loading bay",
                         "source": "synthetic", "enabled": True,
                         "fps": 10, "width": 320, "height": 240})
MULTI["functions"].append({"id": "ppl2", "name": "People 2",
                           "type": "frame_count", "enabled": True,
                           "params": {"camera": "cam2"}})


def multi():
    print("== two cameras, one lost")
    check("both detecting",
          wait(lambda: state().get("ai_ok") is True
               and counts().get("fn_ppl2_count") == 1), state())
    builds0 = len(DET["builds"])
    mark = time.time()
    UNPLUGGED.add("cam2")
    check("the first restart is the whole detection (chip reacquired)",
          wait(lambda: len(DET["builds"]) > builds0), DET)
    builds1 = len(DET["builds"])
    check("a retry follows while cam2 is still missing",
          wait(lambda: (state().get("cam_watch") or {}).get("attempts", 0)
               >= 2), state().get("cam_watch"))

    def opened(cid):
        return len([o for o in CAM["opens"] if o[0] == cid and o[1] > mark])
    check("...it reopens only the lost camera: no chip rebuild, cam1 "
          "opened once (by the full restart), cam2 again",
          wait(lambda: opened("cam2") >= 2)
          and len(DET["builds"]) == builds1 and opened("cam1") == 1,
          (len(DET["builds"]) - builds1, opened("cam1"), opened("cam2")))
    c = counts()
    check("...the good camera keeps its numbers, the lost one's are absent",
          "cam_cam1_objects" in c and "cam_cam2_objects" not in c
          and "fn_ppl2_count" not in c and c.get("fn_ppl_count") == 1,
          (c, state().get("cam_watch")))
    check("...and the dot names the lost camera",
          "(Loading bay)" in str(state().get("ai_error")), state().get("ai_error"))
    UNPLUGGED.discard("cam2")
    check("cam2 plugged back in: recovered, both detecting",
          wait(lambda: state().get("ai_ok") is True
               and counts().get("fn_ppl2_count") == 1, 30),
          (state().get("ai_error"), state().get("cam_watch")))


run_loop(MULTI, multi)
check("...releasing what it held", DET["live"] == 0 and not CAM["live"],
      (CAM, DET))

sup_src = open(os.path.join(ROOT, "core", "services", "app_supervisor.py"),
               encoding="utf-8").read()
check("the master's app watcher posts them for a running app",
      "camera_notice.post(systems_dir, slug, config_dir, notify)"
      in sup_src.split("def watch_step", 1)[1])

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all camera-watchdog checks passed")
