# Boot order, crashes and Power off against the one-app AI switch.
#
# WHAT COULD GO WRONG (review of 401b7b9): two things now start a system's
# app, and each writes the AI switch. The master's supervisor starts it with
# the AI OFF (ensure_app) 25 s after the master comes up; the hub's boot
# autostart starts DETECTION (dc.start: switch ON). Under systemd the master
# can come up before the hub, so either may run first. If the supervisor's
# "AI off" could land AFTER the autostart's "on", a box set to detect on
# power-on would boot with detection off -- and nothing would say so.
# smoke_aiswitch pins each call alone; this pins them in sequence:
#   1. supervisor first, then autostart: AI ends ON, one app started
#   2. autostart first, then supervisor: AI stays ON, no second start
#   3. an app that dies while detecting: restarted with the AI OFF, and the
#      admins are told (a crashing camera/chip is not brought back by itself)
#   4. Power off: the supervisor stops what runs and starts nothing; an Inject
#      press is refused; Start detection is what ends it
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import app_supervisor as sup                # noqa: E402
from core.services import detector_control as dc               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class FakeRuntime:
    """The system's app/runtime.py as dc sees it; `raw` is state.json."""
    UNIFIED = True

    def __init__(self):
        self.alive, self.mode, self.calls, self.raw = False, None, [], {}

    def status(self, data_dir, pkg_dir=None):
        return {"running": self.alive, "mode": self.mode if self.alive else None}

    def read(self, data_dir):
        return dict(self.raw)

    def start(self, pkg_dir, data_dir, mode="unified"):
        self.calls.append("start")
        self.alive, self.mode = True, "unified"
        self.raw = {"running": True, "mode": "unified"}
        return True, "started"

    def stop(self, data_dir, timeout_s=10, pkg_dir=None):
        self.calls.append("stop")
        self.alive = False
        self.raw = {"running": False, "mode": "unified"}
        return True, "stopped"


PKG = tempfile.mkdtemp()
rt = FakeRuntime()
saved = (dc._app_runtime, dc._pkg_dir, dc._data_dir, dc.cameras,
         dc.running_apps, dc.sources_elsewhere)
dc._pkg_dir = lambda sd, slug: PKG
dc._app_runtime = lambda pkg: rt
dc._data_dir = lambda pkg, cfg: tempfile.gettempdir()
dc.cameras = lambda sd, slug: [{"id": "cam1", "source": "0"}]
dc.running_apps = lambda sd: []
dc.sources_elsewhere = lambda sd, slug, config_dir=None: []
notes = []


def notify(kind, title, body="", config_dir=None, **kw):
    notes.append(title)


def ai():
    dc._running_cache.clear()
    return dc.ai_flag(dc._merged(PKG))


def reset():
    rt.__init__()
    open(os.path.join(PKG, "config.local.toml"), "w").close()
    dc._running_cache.clear()
    sup._crashes.clear()
    sup._paused.clear()
    notes.clear()


try:
    # -- 1. supervisor first, then the hub's autostart -------------------------
    reset()
    w1 = sup.reconcile("/x", "demo", None, notify)
    was = ai()
    res = dc.start("/x", "demo")
    check("supervisor first: it starts the app with the AI off",
          w1 == "started" and was is False, (w1, was))
    check("...then the boot autostart turns the AI ON, in place",
          res.get("ok") and ai() is True and rt.calls == ["start"],
          (res, ai(), rt.calls))

    # -- 2. autostart first, then the supervisor ---------------------------------
    reset()
    dc.start("/x", "demo")
    w2 = sup.reconcile("/x", "demo", None, notify)
    check("autostart first: the supervisor's look leaves the AI ON",
          w2 == "running" and ai() is True and rt.calls == ["start"],
          (w2, ai(), rt.calls))
    check("...and its crash watch finds nothing to do",
          sup.watch_step("/x", "demo", None, 1000, notify) == "none")

    # -- 3. it dies while detecting ----------------------------------------------
    rt.alive = False                        # state.json still says running
    rt.raw = {"running": True, "mode": "unified", "ai_on": True}
    got = sup.watch_step("/x", "demo", None, 2000, notify)
    check("an app that died detecting is restarted...",
          got == "restarted" and rt.alive and rt.calls == ["start", "start"],
          (got, rt.calls))
    check("...with the AI OFF (no camera or chip brought back by itself)",
          ai() is False, ai())
    check("...and the admins are told", any("unexpectedly" in n for n in notes),
          notes)

    # -- 4. Power off --------------------------------------------------------------
    reset()
    dc.start("/x", "demo")
    dc.set_powered_off("/x", "demo", True)
    w4 = sup.reconcile("/x", "demo", None, notify)
    check("powered off: the supervisor stops what runs",
          w4 == "powered_off" and not rt.alive and rt.calls == ["start", "stop"],
          (w4, rt.calls))
    check("...and its watch starts nothing",
          sup.watch_step("/x", "demo", None, 3000, notify) == "none"
          and not rt.alive, rt.calls)
    word, why = sup.start_for_press("/x", "demo")
    check("...an Inject press is refused, with the reason",
          word == "powered_off" and "powered off" in why and not rt.alive,
          (word, why))
    res = dc.start("/x", "demo")
    check("Start detection ends the Power off and brings it up detecting",
          res.get("ok") and rt.alive and ai() is True
          and not dc.powered_off("/x", "demo"), (res, rt.calls))

    # -- 5. a REFUSED start leaves a Power off standing -------------------------
    # set_powered_off(False) used to run before the checks, so "no camera"
    # ended the Power off and the route's error path then had the supervisor
    # bring the workflow up for a system the operator had switched off.
    reset()
    dc.set_powered_off("/x", "demo", True)
    dc.cameras = lambda sd, slug: []
    try:
        dc.start("/x", "demo")
        refused = ""
    except ValueError as e:
        refused = str(e)
    check("no camera: Start is refused with the reason",
          "no camera" in refused, refused)
    check("...and the system is STILL powered off, nothing started",
          dc.powered_off("/x", "demo") and rt.calls == [], rt.calls)
    # The boot autostart of such a system is a SKIP, not a failure: the hub
    # logged "autostart X: ... app: failed: no camera is set up" as an ERROR
    # on every power-on of a board whose workflow was up and well.
    from core.services import provision                        # noqa: E402
    dc.set_powered_off("/x", "demo", False)
    out = provision.autostart_system("/x", "demo", {}, "/x")
    check("autostart with no camera is reported as skipped, not failed",
          out["app"].startswith("skipped") and "failed" not in out["message"]
          and rt.calls == [], out)
    dc.cameras = lambda sd, slug: [{"id": "cam1", "source": "0"}]
finally:
    (dc._app_runtime, dc._pkg_dir, dc._data_dir, dc.cameras,
     dc.running_apps, dc.sources_elsewhere) = saved

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all boot/crash/power checks passed")
