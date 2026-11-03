# The pid file is bookkeeping; /proc is the truth. Detection must not "end".
#
# The failure as the operator saw it: detection running, boxes on the video,
# stats moving -- and every control saying STOPPED, offering "Start detection".
# Pressing it bred a twin that died binding the stream port (Errno 98), and
# THE TWIN'S dying act was overwriting state.json with its own pid. From then
# on the record pointed at a corpse: status() honestly said stopped, the real
# app streamed on invisibly, and no Stop could ever reach it.
#
# Three rules close it:
#   * start() scans /proc for this package's own app before spawning, and
#     ADOPTS a survivor instead of racing it;
#   * a spawned child must survive its first moment before its pid is
#     recorded -- a child that dies instantly must not overwrite the record;
#   * stop() kills the strays too, not only the recorded pid.
import io
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import runtime                                      # noqa: E402

HAS_PROC = os.path.isdir("/proc")

# -- the stray scan -------------------------------------------------------------
if HAS_PROC:
    # A real `-m app.run` child with its cwd in a fake package dir.
    pkg = tempfile.mkdtemp()
    os.makedirs(os.path.join(pkg, "app"), exist_ok=True)
    io.open(os.path.join(pkg, "app", "__init__.py"), "w").write("")
    io.open(os.path.join(pkg, "app", "run.py"), "w").write(
        "import time\ntime.sleep(30)\n")
    child = subprocess.Popen([sys.executable, "-m", "app.run"], cwd=pkg)
    try:
        time.sleep(0.5)
        strays = runtime._stray_app_pids(pkg)
        assert child.pid in strays, (child.pid, strays)
        # ...and another package's scan must NOT claim it.
        assert child.pid not in runtime._stray_app_pids(tempfile.mkdtemp())
        print("== the /proc scan finds this package's app, and only this one's")

        # start() with a lost record ADOPTS the survivor instead of spawning.
        # The record carries the mode the app writes today ("unified": start()
        # stamps it, the heartbeat keeps it, stop() never clears it). A record
        # with NO mode is one an app from before the AI switch wrote, and a
        # stray behind it is that old app -- start() stops it instead.
        data = tempfile.mkdtemp()
        runtime.write(data, pid=None, running=False, mode="unified")
        ok, msg = runtime.start(pkg, data)
        assert ok and "re-adopted" in msg, (ok, msg)
        assert runtime.read(data).get("pid") == child.pid
        print("== start() adopts the stray:", msg)

        # stop() reaches it even though the record was empty a moment ago.
        ok, msg = runtime.stop(data, timeout_s=5, pkg_dir=pkg)
        assert ok, msg
        try:                    # wait for the death itself, not a fixed 0.5s
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        assert child.poll() is not None, "the stray survived stop()"
        print("== stop() kills what /proc sees, not only the recorded pid")
    finally:
        if child.poll() is None:
            child.kill()
else:
    print("== no /proc on this box -- scan behaviour verified on the Pi")

# -- a doomed spawn must not overwrite the record --------------------------------
pkg2 = tempfile.mkdtemp()
os.makedirs(os.path.join(pkg2, "app"), exist_ok=True)
io.open(os.path.join(pkg2, "app", "__init__.py"), "w").write("")
io.open(os.path.join(pkg2, "app", "run.py"), "w").write(
    "import sys\nsys.exit(3)\n")                 # dies instantly, like Errno 98
data2 = tempfile.mkdtemp()
runtime.write(data2, pid=None, running=False)
before = runtime.read(data2).get("pid")
# Deterministic under load: the survival window is made long enough that the
# doomed child is SEEN to die (start() returns the moment it does), however
# slowly a busy box gets it through its interpreter start-up. The suite once
# flaked here at the old fixed 0.8 s.
saved_survive = runtime.SURVIVE_S
runtime.SURVIVE_S = 60.0
try:
    ok, msg = runtime.start(pkg2, data2)
finally:
    runtime.SURVIVE_S = saved_survive
assert not ok, (ok, msg)
assert "exited immediately" in msg, msg
assert runtime.read(data2).get("pid") == before, \
    "a child that died instantly still overwrote the pid record"
print("== a spawn that dies at once is reported, and the record survives:", msg)
print("ALL OK")
