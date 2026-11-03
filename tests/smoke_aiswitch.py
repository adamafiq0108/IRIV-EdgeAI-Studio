# AI detection is a switch INSIDE the one app, not a second app.
#
# Tok Aba (2026-09-30): "for the nodes activation, dont need to wait for the
# ai detection to be running ... only not active nodes that will be inactive
# .. ensure it only for nodes that cant stand alone only". So a system's app
# runs the whole workflow whenever it is online, and [ai] on decides only
# whether it opens the camera and the detector. Flipping it must not restart
# anything: an Inject's rhythm, its Start/Stop, a running total, the OID seen
# memory all carry on.
#
# There is no board for this (.47 is away), so the app loop is run HERE, for
# real, with fakes where the hardware would be: a camera that counts opens and
# releases, and a detector registered as "hailo" that counts builds and
# releases and can be made to crash. The switch is flipped by rewriting the
# config the loop polls, exactly as the master does. Pinned:
#   1. AI off: no camera opened, no detector built, no Hailo module imported
#      -- and the free rules run anyway
#   2. AI on, in place: one detector, one camera, the detection-fed rules
#      join the SAME engine; off again releases both; on/off repeatedly
#      balances every open with a release
#   3. free-node state survives every flip: Inject rhythm and its Stop, a
#      total, the OID seen memory; a held alarm does not fire again
#   4. a detector that crashes mid-run goes red with the reason, the rest
#      keeps running, and it comes back by itself
#   5. the master side: start = switch on (+ checks), stop = switch off with
#      the app kept, stop_app, budgets count AI-on systems only, an old
#      logic-mode app is replaced once, the dot fields
#   6. the pages draw the dot: toolbar, Detection Stream, System Control
import copy
import io
import json
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

from app import detector as detector_mod                       # noqa: E402
from app import functions as functions_mod                     # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def oid_seen():
    """The OID rule's seen memory, whichever camera engine keeps it
    (app/functions.py keys it by (camera, rule))."""
    return next((v for (_scope, fid), v in functions_mod._objdet_seen.items()
                 if fid == "oid"), None)


# -- the fakes -----------------------------------------------------------------
RealCamera = apprun.camera_mod.Camera
CAM = {"opens": 0, "closes": 0, "live": set()}


class FakeCamera(RealCamera):
    """A camera that counts. Synthetic frames underneath, so the loop has a
    real picture to hand the detector and to draw on."""

    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))

    def open(self):
        CAM["opens"] += 1
        CAM["live"].add(id(self))
        return super().open()

    def close(self):
        if id(self) in CAM["live"]:
            CAM["closes"] += 1
            CAM["live"].discard(id(self))
        return super().close()


DET = {"builds": 0, "closes": 0, "live": 0, "crash": False, "calls": 0}


class FakeChip(detector_mod.Detector):
    """Stands in for the Hailo detector: registered under "hailo", so the
    config is exactly a production one. Counts builds and releases; crashes
    on demand."""

    name = "fakechip"

    def __init__(self, config):
        super().__init__(config)
        DET["builds"] += 1
        DET["live"] += 1
        self._open = True

    def detect(self, frame):
        DET["calls"] += 1
        if DET["crash"]:
            raise RuntimeError("the fake chip fell over")
        return [{"label": "person", "confidence": 0.9,
                 "box": [20, 20, 120, 200]}]

    def close(self):
        if self._open:
            self._open = False
            DET["closes"] += 1
            DET["live"] -= 1


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.02
apprun.DETECTOR_RETRY_MIN_S = 0.3

DATA = tempfile.mkdtemp()
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()

CFG = {
    "ai": {"on": False},
    "app": {"stream_port": PORT, "stream_bind": "127.0.0.1"},
    "cameras": [{"id": "cam1", "source": "rtsp://10.0.0.9/stream",
                 "enabled": True, "fps": 10, "width": 320, "height": 240}],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
    "zones": [],
    "outputs": [],
    # What the compiler decided (workflow.logic_artifacts): these run with
    # the AI off. ppl and oid are fed by a Detection Stream, so they wait.
    "logic": {"functions": ["kick", "tot", "hold"], "outputs": [],
              "version": 99},
    "functions": [
        {"id": "kick", "name": "Kick", "type": "inject", "enabled": True,
         # 1 s, well above the loop's period (4 Hz with the AI off), so every
         # fire is a separate edge the Running Total can count.
         "params": {"ptype": "number", "payload": "1", "interval_s": 1.0}},
        {"id": "tot", "name": "Presses", "type": "total", "enabled": True,
         "params": {"key": "fn_kick_fired"}},
        # A held alarm: true from the first total on, and must not fire
        # again because the AI was switched.
        {"id": "hold", "name": "Hold", "type": "expr", "enabled": True,
         "params": {"expr": "fn_tot_total >= 1"},
         "triggers": [{"metric": "value", "op": ">=", "value": 1,
                       "severity": "info", "cooldown_s": 0,
                       "message": "held alarm"}]},
        {"id": "ppl", "name": "People", "type": "frame_count", "enabled": True,
         "params": {}},
        {"id": "oid", "name": "Arrivals", "type": "object_detect",
         "enabled": True, "params": {"stamp_events": True}}],
}
MT = [1.0]


def set_cfg(**kw):
    CFG.update(kw)
    MT[0] += 1.0


apprun.config_mod.load = lambda *a, **k: copy.deepcopy(CFG)
apprun.config_mod.data_dir = lambda *a, **k: DATA
apprun._config_mtime = lambda: MT[0]


def state():
    try:
        with open(os.path.join(DATA, "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def counts():
    return state().get("counts") or {}


def wait(pred, limit=15.0):
    end = time.time() + limit
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.05)
    return False


def held_fires():
    """How many times the held alarm reached the event log."""
    n = 0
    try:
        with open(os.path.join(DATA, "events.jsonl"), encoding="utf-8") as f:
            for line in f:
                if "held alarm" in line:
                    n += 1
    except OSError:
        pass
    return n


def hailo_imported():
    return any(m in sys.modules for m in ("app.hailo", "hailo_platform"))


results = {}


def drive():
    try:
        # -- 1. AI off ------------------------------------------------------
        ok = wait(lambda: counts().get("fn_tot_total", 0) >= 2)
        c = counts()
        st = state()
        check("AI off: the free rules run (Inject rhythm, total over it)", ok, c)
        check("...no camera opened, no detector built",
              CAM["opens"] == 0 and DET["builds"] == 0, (CAM, DET))
        check("...no Hailo module ever imported", not hailo_imported(),
              [m for m in sys.modules if "hailo" in m])
        check("...the detection-fed rules sit out",
              not any(k.startswith(("fn_ppl_", "fn_oid_")) for k in c),
              sorted(c))
        check("...and the state says: unified app, AI off, dot red",
              st.get("mode") == "unified" and st.get("ai_on") is False
              and st.get("ai_ok") is False, st)
        wait(lambda: held_fires() >= 1, 15)
        results["held_before"] = held_fires()

        # -- 2. on, in place -------------------------------------------------
        pid = state().get("pid")
        cycles = 3
        tot_seen = []
        for i in range(cycles):
            before = counts().get("fn_tot_total", 0)
            set_cfg(ai={"on": True})
            on = wait(lambda: state().get("ai_ok") is True)
            c = counts()
            check(f"on #{i + 1}: the dot turns green (detector + frames)", on,
                  state())
            check(f"on #{i + 1}: one detector, one camera open",
                  DET["live"] == 1 and len(CAM["live"]) == 1, (CAM, DET))
            got_ppl = wait(lambda: counts().get("fn_ppl_count") == 1, 15)
            check(f"on #{i + 1}: the detection-fed rules joined the engine",
                  got_ppl, sorted(counts()))
            check(f"on #{i + 1}: the free total did not restart",
                  counts().get("fn_tot_total", 0) >= before, (before, c))
            tot_seen.append(counts().get("fn_tot_total", 0))
            if i == 0:
                # An arrival needs a CONFIRMED track (a few frames).
                wait(lambda: oid_seen(), 15)
                seen = dict(oid_seen() or {})
                check("OID remembers the object that arrived", bool(seen),
                      functions_mod._objdet_seen)
                results["seen"] = seen
            set_cfg(ai={"on": False})
            off = wait(lambda: state().get("ai_on") is False
                       and DET["live"] == 0 and not CAM["live"])
            check(f"off #{i + 1}: camera and detector released in place", off,
                  (CAM, DET, state().get("ai_on")))
            # Liveness on the Inject's own run counter: monotonic whatever
            # the loop's pace, where an edge-counted total can miss an edge
            # on a slow pass (Yaya repro).
            runs0 = counts().get("fn_kick_runs", 0)
            check(f"off #{i + 1}: the free rules keep running",
                  wait(lambda: counts().get("fn_kick_runs", 0) > runs0, 15),
                  counts())
        check("the app was never restarted by the switch",
              state().get("pid") == pid, (pid, state().get("pid")))
        check(f"{cycles} on/off cycles: every open balanced by a release",
              DET["builds"] == DET["closes"] == cycles
              and CAM["opens"] == CAM["closes"] >= cycles, (CAM, DET))
        check("...and still no Hailo module imported", not hailo_imported())
        check("the total only ever grew across the flips",
              tot_seen == sorted(tot_seen), tot_seen)
        check("the OID seen memory survived the flips",
              set(results["seen"]) <= set(oid_seen() or {}),
              functions_mod._objdet_seen)
        check("the held alarm did not fire again on any flip",
              held_fires() == results["held_before"],
              (held_fires(), results["held_before"]))

        # -- 3. Inject Stop survives a flip ------------------------------------
        with open(os.path.join(DATA, functions_mod.INJECT_FILE), "w",
                  encoding="utf-8") as f:
            json.dump({"kick": {"seq": 1, "ts": time.time(),
                                "action": "stop"}}, f)
        stopped = wait(lambda: counts().get("fn_kick_running") == 0, 15)
        check("an Inject stopped from its button", stopped, counts())
        set_cfg(ai={"on": True})
        wait(lambda: state().get("ai_ok") is True)
        set_cfg(ai={"on": False})
        wait(lambda: state().get("ai_on") is False and DET["live"] == 0)
        runs = counts().get("fn_kick_runs")
        time.sleep(0.8)
        check("...stays stopped across an on/off flip",
              counts().get("fn_kick_running") == 0
              and counts().get("fn_kick_runs") == runs, counts())
        with open(os.path.join(DATA, functions_mod.INJECT_FILE), "w",
                  encoding="utf-8") as f:
            json.dump({"kick": {"seq": 2, "ts": time.time(),
                                "action": "start"}}, f)
        wait(lambda: counts().get("fn_kick_running") == 1, 15)

        # -- 4. the detector crashes mid-run -----------------------------------
        set_cfg(ai={"on": True})
        wait(lambda: state().get("ai_ok") is True)
        builds = DET["builds"]
        DET["crash"] = True
        red = wait(lambda: state().get("ai_ok") is False
                   and state().get("ai_error"))
        st = state()
        check("a crashing detector turns the dot red, with the reason", red
              and ("inference" in str(st.get("ai_error"))
                   or "fell back" in str(st.get("ai_error"))), st)
        runs0 = counts().get("fn_kick_runs", 0)
        check("...while every free rule keeps running",
              wait(lambda: counts().get("fn_kick_runs", 0) > runs0, 15),
              counts())
        check("...and the app itself stays up", state().get("pid") == pid)
        released = wait(lambda: DET["live"] == 0, 15)
        check("...the broken detector is released (the failure ladder)",
              released, DET)
        DET["crash"] = False
        back = wait(lambda: state().get("ai_ok") is True
                    and DET["builds"] > builds, 10)
        check("...and it comes back green by itself once the chip answers",
              back, (DET, state().get("ai_error")))
        set_cfg(ai={"on": False})
        wait(lambda: DET["live"] == 0 and not CAM["live"])
        check("switching off after a crash still releases everything",
              DET["live"] == 0 and not CAM["live"], (CAM, DET))
    except Exception as exc:                                   # noqa: BLE001
        check("the driver ran", False, repr(exc))
    finally:
        apprun._stop.set()


t = threading.Thread(target=drive, daemon=True)
t.start()
rc = apprun.main([])
t.join(5)
check("the app exits cleanly", rc == 0, rc)
check("...releasing what it held", DET["live"] == 0 and not CAM["live"],
      (CAM, DET))

# -- 5. the master side -------------------------------------------------------
from core.services import detector_control as dc               # noqa: E402


class FakeRuntime:
    """The system's app/runtime.py, as dc sees it."""
    UNIFIED = True

    def __init__(self, st=None):
        self.st, self.calls = dict(st or {"running": False}), []

    def status(self, data_dir, pkg_dir=None):
        return dict(self.st)

    def start(self, pkg_dir, data_dir, mode="unified"):
        self.calls.append(("start", mode))
        self.st = {"running": True, "mode": mode}
        return True, "started"

    def stop(self, data_dir, timeout_s=10, pkg_dir=None):
        self.calls.append(("stop", self.st.get("mode")))
        self.st = {"running": False}
        return True, "stopped"


pkgs = {s: tempfile.mkdtemp() for s in ("a", "b", "c", "d", "e")}
fakes = {s: FakeRuntime() for s in pkgs}
saved = (dc._app_runtime, dc._pkg_dir, dc._data_dir, dc.cameras,
         dc.running_apps, dc.sources_elsewhere, dc._running_cache.copy())
by_pkg = {p: s for s, p in pkgs.items()}
dc._pkg_dir = lambda sd, slug: pkgs[slug]
dc._app_runtime = lambda pkg: fakes.get(by_pkg.get(pkg))
dc._data_dir = lambda pkg, cfg: tempfile.gettempdir()
dc.cameras = lambda sd, slug: [{"id": "cam1", "source": "0"}]
live_list = lambda sd: [s for s in pkgs if dc.status(sd, s).get("running")]  # noqa: E731
dc.running_apps = live_list
dc.sources_elsewhere = lambda sd, slug, config_dir=None: [
    "0" for s in pkgs if s != slug and dc._running_now(
        pkgs[s], dc._merged(pkgs[s]))]
try:
    a = fakes["a"]
    dc._running_cache.clear()
    res = dc.start("/x", "a")
    st = dc.status("/x", "a")
    check("Start detection with nothing running: the unified app starts, "
          "switch on", res["ok"] and a.calls == [("start", "unified")]
          and dc.ai_flag(dc._merged(pkgs["a"])) is True
          and st["running"] and st["app_running"], (res, a.calls, st))
    a.calls.clear()
    res = dc.stop("/x", "a")
    st = dc.status("/x", "a")
    check("Stop detection switches the AI off and KEEPS the app",
          res["ok"] and a.calls == []
          and dc.ai_flag(dc._merged(pkgs["a"])) is False
          and st["running"] is False and st["logic_running"] is True
          and st["app_running"] is True, (res, a.calls, st))
    check("...the dot says why it is red",
          st["ai_ok"] is False and st["ai_error"] == "AI detection is off",
          st)
    res = dc.start("/x", "a")
    check("Start again flips it back on, no restart",
          res["ok"] and a.calls == [] and dc.status("/x", "a")["running"],
          (res, a.calls))
    a.st.update(ai_on=True, ai_ok=True, last_heartbeat=time.time())
    st = dc.status("/x", "a")
    check("the app's own 'producing' makes the dot green",
          st["ai_ok"] is True and st["ai_error"] == "", st)
    a.st.update(ai_ok=False, ai_error="waiting for the camera's first frame")
    st = dc.status("/x", "a")
    check("...and its reason is the red dot's tooltip",
          st["ai_ok"] is False
          and st["ai_error"] == "waiting for the camera's first frame", st)
    res = dc.stop_app("/x", "a")
    check("stop_app stops the whole process (Power off, delete, reset)",
          res["ok"] and a.calls[-1] == ("stop", "unified")
          and dc.status("/x", "a")["app_running"] is False, a.calls)

    # Budgets count only AI-on systems.
    for s in ("a", "b", "c"):
        fakes[s].st = {"running": True, "mode": "unified"}
        dc.set_ai("/x", s, s == "a")
    dc._running_cache.clear()
    check("running_apps counts only systems with the AI ON",
          live_list("/x") == ["a"], live_list("/x"))
    check("...and the camera budget likewise",
          dc.sources_elsewhere("/x", "d") == ["0"])
    for s in ("b", "c", "d"):
        dc.set_ai("/x", s, True)
        fakes[s].st = {"running": True, "mode": "unified"}
    try:
        dc.start("/x", "e")
        refused = ""
    except ValueError as exc:
        refused = str(exc)
    check("the running-apps cap refuses a fifth AI-on system",
          "maximum is" in refused, refused)
    dc.set_ai("/x", "d", False)
    res = dc.start("/x", "e")
    check("...and an AI-off app does not count against it",
          res["ok"], res)
    check("an AI-off app is not in the hardware footer's 'running'",
          not dc._ai_split({"running": True, "mode": "unified"},
                           {"ai": {"on": False}})[1])

    # Migration from v1.0.12.
    old = fakes["b"]
    old.st, old.calls = {"running": True, "mode": "logic"}, []
    res = dc.start("/x", "b")
    check("Start over an OLD logic-mode app replaces it once with the "
          "unified app", res["ok"] and old.calls == [("stop", "logic"),
                                                     ("start", "unified")],
          old.calls)
    old.st, old.calls = {"running": True, "mode": "logic"}, []
    res = dc.ensure_app("/x", "b")
    check("...the supervisor does the same, AI off",
          res["word"] == "replaced" and old.calls == [("stop", "logic"),
                                                      ("start", "unified")]
          and dc.ai_flag(dc._merged(pkgs["b"])) is False, (res, old.calls))
    old.calls.clear()
    check("...and leaves the unified app alone after that",
          dc.ensure_app("/x", "b")["word"] == "running" and old.calls == [])
    old.st, old.calls = {"running": True, "mode": "detection"}, []
    check("an OLD detection app still reads as detecting",
          dc.status("/x", "b")["running"] is True)
    dc.stop("/x", "b")
    check("...and Stop stops it for real, then brings the unified app up "
          "with the AI off", old.calls == [("stop", "detection"),
                                           ("start", "unified")]
          and dc.ai_flag(dc._merged(pkgs["b"])) is False, old.calls)
    old.st, old.calls = {"running": False}, []
    dc.set_powered_off("/x", "b", True)
    dc.set_ai("/x", "b", True)
    try:
        dc.start("/x", "b")
    finally:
        pass
    check("Start detection ends a Hub Power off",
          dc.powered_off("/x", "b") is False)
    fakes["c"].st, fakes["c"].calls = {"running": True, "mode": "detection"}, []
    dc.set_powered_off("/x", "c", True)
    dc.stop("/x", "c")
    check("Stop of an old app on a powered-off system brings nothing back",
          fakes["c"].calls == [("stop", "detection")], fakes["c"].calls)

    # An app copy from before the switch.
    ancient = type("Ancient", (), {"status": FakeRuntime.status,
                                   "start": FakeRuntime.start,
                                   "stop": FakeRuntime.stop})()
    ancient.st, ancient.calls = {"running": False}, []
    fakes["e"] = ancient
    check("an app copy without the switch is reported as such",
          dc.unified_supported("/x", "e") is False)
    try:
        dc.ensure_app("/x", "e")
        check("...and is never started with the AI 'off'", False,
              ancient.calls)
    except ValueError as exc:
        check("...and is never started with the AI 'off'",
              ancient.calls == [] and "too old" in str(exc), str(exc))
    dc.start("/x", "e")
    check("...its Start detection starts it the old way",
          ancient.calls == [("start", "unified")] or ancient.calls
          == [("start", "detection")], ancient.calls)
finally:
    (dc._app_runtime, dc._pkg_dir, dc._data_dir, dc.cameras, dc.running_apps,
     dc.sources_elsewhere, cache) = saved
    dc._running_cache.clear()
    dc._running_cache.update(cache)

# -- 6. the pages draw the dot --------------------------------------------------
page = read("frontend", "src", "pages", "Workflow.jsx")
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
panel = read("frontend", "src", "components", "ControlPanel.jsx")
check("the Workflow toolbar shows the AI dot", "<AiDot" in page
      and "aiStatus" in page)
check("...and every Detection Stream node", "<AiDot" in nodes_src
      and 'type === "source.detection"' in nodes_src)
# System Control states plain status words (Tok Aba, 2026-10-27: no dots, no
# "AI on/off" there); the dot lives on the Workflow page and the nodes.
check("...and System Control draws no AI dot, only plain status words",
      "<AiDot" not in panel
      and '"ai_ok": bool(stat.get("ai_ok"))' in read("core", "routes", "hub.py")
      and "src.ai_ok" in read("frontend", "src", "components", "AiDot.jsx"))
check("the dot is a status, not a button",
      "<button" not in read("frontend", "src", "components", "AiDot.jsx"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all AI-switch checks passed")
