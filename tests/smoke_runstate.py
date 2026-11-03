# "Detection is stopped" must mean the process is gone, not that it is busy.
#
# Opening Zones sometimes claimed detection was stopped while it plainly was
# not -- the feed was up and the boxes were moving. The reason: `running` was
# decided by the HEARTBEAT. The loop writes state every frame, so a heartbeat
# older than 30s looked like death; but it also happens while the app is very
# much alive and doing something slow INSIDE the loop -- loading a HEF takes
# tens of seconds, and reopening a dropped RTSP camera can block longer. Both
# resolve by themselves. Both used to put a "Detection is stopped / Start
# detection" card in front of an operator whose detection was running, which
# invites starting an app that is already started.
#
# So the PROCESS decides. A live pid is running; a stale heartbeat is reported
# beside it as `stalled`, and the page says "busy". The obvious objection to
# trusting a pid is reuse -- the recorded number outlives the process and the
# kernel hands numbers out again -- so ownership is checked against the real
# process, not assumed.
import io
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import runtime                                      # noqa: E402

DATA = tempfile.mkdtemp()
MINE = os.getpid()

# Sections 1-2 pin HEARTBEAT behaviour (stale vs fresh), not ownership -- that
# is section 3's job, and it already covers it correctly. Left unpatched, this
# process's own pid fails _owns() on any box with /proc (its cmdline is the
# test runner, not "app.run"), which used to pass here only because a Windows
# dev box has no /proc to check at all -- so this pinned a Windows quirk, not
# the behaviour the comment above claims.
_real_owns = runtime._owns
runtime._owns = lambda pid, pkg_dir=None: True

# -- alive, but the loop has not written for a while --------------------------
runtime.write(DATA, pid=MINE, started_at=time.time() - 300, running=True)
# Back-date the heartbeat past the staleness window, in the file, exactly as a
# blocked loop would leave it.
state = runtime.read(DATA)
state["heartbeat"] = time.time() - (runtime.STALE_AFTER_S + 60)
io.open(runtime._path(DATA), "w", encoding="utf-8").write(__import__("json").dumps(state))
runtime._read_cache.clear()

st = runtime.status(DATA)
assert st["running"] is True, "a live process with a stale heartbeat read as stopped"
assert st["stalled"] is True, st
print("== a live process with an old heartbeat is RUNNING, and flagged stalled")

# ...and with a fresh heartbeat it is simply running.
runtime.write(DATA, pid=MINE, running=True)
st = runtime.status(DATA)
assert st["running"] is True and st["stalled"] is False, st
assert st["pid"] == MINE, st
print("== a live process with a fresh heartbeat is running, not stalled")

# -- dead means dead ----------------------------------------------------------
# A pid that no longer exists is stopped no matter how fresh the state file is.
dead = 2 ** 22 - 1                       # far above any live pid on a Pi
while os.path.exists(f"/proc/{dead}"):   # pragma: no cover - vanishingly rare
    dead -= 1
runtime.write(DATA, pid=dead, running=True)
st = runtime.status(DATA)
assert st["running"] is False, "a dead pid reported as running"
assert st["stalled"] is False, st
assert st["pid"] is None, st
print("== a pid that does not exist is stopped, whatever the file claims")

# An empty state file is stopped, not "unknown".
runtime.write(DATA, pid=None, running=False)
assert runtime.status(DATA)["running"] is False
print("== no pid at all is stopped")

runtime._owns = _real_owns   # ownership is exactly what section 3 checks

# -- pid reuse ----------------------------------------------------------------
# THE reason "trust the pid" needs a guard: this test's own process is alive
# and is not a detection app. On a box with /proc it must be refused.
if os.path.exists(f"/proc/{MINE}/cmdline"):
    assert runtime._owns(MINE, None) is False, \
        "any live process counts as the detection app"
    print("== a live pid that is not app.run is refused (pid reuse)")
    # And the right kind of process still has to belong to THIS package.
    assert runtime._owns(MINE, ROOT) is False
else:
    # No /proc (a Windows dev box): the check cannot run, and says so rather
    # than refusing to report a running app on a platform it cannot inspect.
    assert runtime._owns(MINE, None) is True
    print("== without /proc the ownership check passes rather than lying")

# -- the seam core reads it through -------------------------------------------
SVC = io.open(os.path.join(ROOT, "core", "services", "detector_control.py"),
              encoding="utf-8").read()
block = SVC[SVC.index("def status(systems_dir, slug"):]
block = block[:block.index("\ndef ", 5)]
assert '"stalled"' in block, "the service drops the stalled flag"
assert "rt.status(_data_dir(pkg_dir, cfg), pkg_dir)" in block, \
    "the runtime is not told which package the pid should belong to"
# A created system whose app code predates the second argument must not 500.
assert "except TypeError:" in block, block
print("== the service passes pkg_dir, reports stalled, and tolerates old app code")

# -- the page says busy, not stopped ------------------------------------------
JSX = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Zones.jsx"),
              encoding="utf-8").read()
assert "status.stalled" in JSX, "the page never reads the flag"
assert "Detection is busy" in JSX, "a stalled app still reads as stopped"
# The 4-second poll has to carry it too, or the badge freezes on whatever the
# first load happened to see.
assert "stalled: d.stalled" in JSX, "the poll drops it"
print("== Zones shows busy, and its poll keeps the flag fresh")
print("ALL OK")
