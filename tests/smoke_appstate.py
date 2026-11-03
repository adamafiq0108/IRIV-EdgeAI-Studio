"""Two ways a running system reported itself dead, both found on one box.

The Parking counter looked completely broken: the master page said the AI was
not running, and the line counter read 0. The app was up, the chip was loaded
and cars were being detected in the log the whole time.

  1. state.json said `running: true` with `pid: null`. runtime.status() calls
     that dead -- correctly, it is what a state file left by a crashed app
     looks like. It came from a RESTART OVERLAP: the new app writes its pid
     and starts detecting while the old one is still releasing the
     accelerator, and the old one's shutdown write then cleared the new one's
     pid. Every page reported the AI stopped, for ever, until the next
     restart happened to not overlap.

  2. The line rule carried model = "yolov8s" from when the system ran that
     model. The system runs yolov8n now, so `accepts()` rejected every
     detection: the rule read zero with video flowing and boxes on screen,
     and nothing anywhere said why.

    python tests/smoke_appstate.py
"""
import atexit
import io
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import functions as fx                                # noqa: E402
from app import runtime                                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. a departing app does not clear its successor's pid --------------------
src = io.open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
              encoding="utf-8").read()
check("the stop write checks the pid is ITS OWN first",
      'if int((runtime.read(data_dir) or {}).get("pid") or 0) in (0, os.getpid()):'
      in src,
      "an old instance shutting down after the new one started cleared the "
      "new one's pid, and every page then read the app as stopped")
check("...and the heartbeat carries the pid, so a lost one heals",
      # Paced since perf item L1 (runtime.heartbeat): still every write
      # carries the pid, so a clobbered one heals within FLUSH_EVERY_S.
      "runtime.heartbeat(data_dir, force=bool(args.once),\n"
      "                          running=True, pid=os.getpid()," in src,
      "the pid was written once at startup; anything that clobbered it stayed "
      "clobbered until the next restart")

DATA = tempfile.mkdtemp()
PKG = os.path.join(ROOT, "systems", "demo")

# A stand-in for the detection app. It has to be a REAL process, and it has to
# look like the app to runtime._owns(), which reads /proc/<pid>/cmdline for
# "app.run" and compares /proc/<pid>/cwd against the package.
#
# Handing status() this test's own pid instead -- which is what stood here --
# only ever passed on Windows, where there is no /proc and _owns answers True
# because it cannot look. On Linux the same line asked whether a process
# running the test suite was the detection app, got the correct "no", and
# failed the suite. It went unnoticed from 2026-09-10 until a release tried to
# build: this test runs on Linux in CI and nowhere else.
#
# The file is NAMED app.run.py so the cmdline carries "app.run", and it is
# started with cwd=PKG so the package check matches. It sleeps; nothing about
# the real app is imported or run.
STANDIN = os.path.join(DATA, "app.run.py")
io.open(STANDIN, "w", encoding="utf-8").write(
    "import time\ntime.sleep(120)\n")
app_proc = subprocess.Popen([sys.executable, STANDIN], cwd=PKG,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
atexit.register(app_proc.kill)

runtime.write(DATA, running=True, pid=app_proc.pid, started_at=0.0)
st = runtime.status(DATA, PKG)
check("a state naming a live pid reads as running",
      st["running"] is True, st)

# The other half of the same rule, and the reason _owns exists: a pid that is
# alive but is NOT the app must not be adopted. Only checkable where /proc is.
if os.path.isdir("/proc"):
    runtime.write(DATA, running=True, pid=os.getpid(), started_at=0.0)
    check("...but a live pid that is NOT the app is not adopted, pid reuse "
          "being the whole reason to look",
          runtime.status(DATA, PKG)["running"] is False)
runtime.write(DATA, running=True, pid=None)
st = runtime.status(DATA, PKG)
check("...and one with no pid reads as stopped, which is why clearing it hurt",
      st["running"] is False,
      "this is correct behaviour -- the bug was writing that state under a "
      "live app")
saved = json.load(io.open(os.path.join(DATA, "state.json"), encoding="utf-8"))
check("...state.json is merged, not replaced, which is HOW it happened",
      saved.get("started_at") == 0.0,
      "runtime.write merges, so a stale writer only has to touch one field to "
      "poison the record")

# -- 2. a rule bound to a model the system no longer runs ---------------------
def rule(model):
    return fx.Function({"id": "lc", "name": "Line counter", "type": "line_count",
                        "enabled": True,
                        "params": {"line": "l1", "classes": ["car"],
                                   "model": model}})


fx.Function.running_models = frozenset()
r = rule("yolov8s")
check("told nothing about what runs, a rule filters exactly as before",
      r.accepts("car", "yolov8s") is True and r.accepts("car", "yolov8n") is False,
      "a two-model graph must keep its per-model rules")

fx.Function.running_models = frozenset({"yolov8n"})
r = rule("yolov8s")
check("a rule naming a model that is GONE counts what does run",
      r.accepts("car", "yolov8n") is True,
      "it rejected every detection and read zero for ever, with video "
      "flowing and boxes on screen")
check("...and its class filter still applies",
      r.accepts("person", "yolov8n") is False,
      "dropping the model filter must not drop the rest of the rule")
check("...and it says so, once",
      r.stale_model() is True and r._said_stale is True)

fx.Function.running_models = frozenset({"yolov8n", "yolov8s"})
r = rule("yolov8s")
check("a rule naming a model that IS running still filters on it",
      r.accepts("car", "yolov8s") is True and r.accepts("car", "yolov8n") is False,
      "two models on one system is the reason this field exists")

fx.Function.running_models = frozenset({"yolov8n"})
r2 = rule("")
check("a rule with no model is unaffected",
      r2.accepts("car", "yolov8n") is True and r2.stale_model() is False)

check("the loop publishes what it is running",
      "Function.running_models = frozenset(" in src
      and 'str(model_now or "").split("+")' in src,
      "_model_label joins several models with ' + '; the set has to be split "
      "back out or a two-model system looks like one model nobody runs")

fx.Function.running_models = frozenset()

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
