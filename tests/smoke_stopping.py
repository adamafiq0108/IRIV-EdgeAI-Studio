# Stop, then Start only once the app has really let go (2026-10-07, Tok Aba):
# the switch answers "stopped" at once, the app releases the camera and the
# chip seconds later, and a Start in between raced the teardown.
#
#   * power_state.ai_stopping: switch off + app still ai_on on a fresh
#     heartbeat = stopping; a stale heartbeat is stuck, not stopping
#   * detector_control.status() carries `stopping` (class_lock reads it too)
#   * start() refuses while stopping with StoppingError, 409 app-wide
#     (core/routes/log_errors.py) and on the hub's switch (core/routes/hub.py)
#   * the dashboard payload carries it; both pages hold Start on it with a
#     "Stopping..." label and let go after STOP_CONFIRM_MS with a note
#
#   py -3 tests/smoke_stopping.py
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.services import detector_control as dc               # noqa: E402
from core.services import power_state                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(f"{'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        bad += 1
        if detail:
            print(f"      {detail}")


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


# -- the helper ----------------------------------------------------------------
on = {"mode": "unified", "ai_on": True}
check("stopping: switch off, app still on, fresh heartbeat",
      power_state.ai_stopping(on, True, False, 1.0, 10) is True)
check("not stopping: the switch is on", power_state.ai_stopping(on, True, True, 1.0, 10) is False)
check("not stopping: the app has let go",
      power_state.ai_stopping({"mode": "unified", "ai_on": False}, True, False, 1.0, 10) is False)
check("not stopping: a stale heartbeat is stuck, Start must come back",
      power_state.ai_stopping(on, True, False, 30.0, 10) is False)
check("not stopping: an app without the switch inside (logic / old)",
      power_state.ai_stopping({"mode": "logic", "ai_on": True}, True, False, 1.0, 10) is False)
check("not stopping: no app at all", power_state.ai_stopping({}, False, False, 1.0, 10) is False)
check("StoppingError is not a ValueError (the routes' 400 path must not swallow it)",
      issubclass(power_state.StoppingError, RuntimeError)
      and not issubclass(power_state.StoppingError, ValueError))

# -- status() and start() -----------------------------------------------------
ssrc = inspect.getsource(dc.status)
check("status() carries `stopping` from the helper (the key class_lock reads)",
      '"stopping": power_state.ai_stopping(' in ssrc)
check("class_lock reads the same key",
      "stopping" in read("core/services/class_lock.py"))
real = (dc._pkg_dir, dc._app_runtime, dc.status)
dc._pkg_dir = lambda sd, slug: os.path.join(ROOT, "systems", "demo")
dc._app_runtime = lambda pkg_dir: object()
dc.status = lambda sd, slug: {"stopping": True}
try:
    try:
        dc.start("/x", "demo")
        check("start: refused while stopping", False)
    except power_state.StoppingError as e:
        check("start: refused while stopping, with the words for the toast",
              str(e) == power_state.MESSAGE, str(e))
finally:
    dc._pkg_dir, dc._app_runtime, dc.status = real
src = inspect.getsource(dc.start)
check("start: the stopping check comes before the camera checks and the switch",
      src.index("StoppingError") < src.index("live_cameras(")
      and src.index("StoppingError") < src.index("set_ai("))

# -- the routes ----------------------------------------------------------------
lsrc = read("core/routes/log_errors.py")
check("the app-wide handler answers 409 for StoppingError",
      "@app.exception_handler(power_state.StoppingError)" in lsrc
      and "status_code=409" in lsrc.split("StoppingError", 1)[1][:1200])
hsrc = read("core/routes/hub.py")
check("the hub's detection switch answers 409 too",
      "except power_state.StoppingError" in hsrc
      and "status_code=409" in hsrc.split("except power_state.StoppingError", 1)[1][:400])
bsrc = read("core/routes/builder.py")
check("the dashboard payload carries stopping",
      '"stopping": bool(status.get("stopping"))' in bsrc)

# -- the pages ---------------------------------------------------------------
for page in ("Main.jsx", "Detection.jsx"):
    psrc = read(f"frontend/src/pages/{page}")
    check(f"{page}: Start is held while stopping, labelled Stopping",
          "STOP_CONFIRM_MS = 20000" in psrc
          and 'stopping ? "Stopping…" : "Start"' in psrc
          and "|| stopping" in psrc)
    check(f"{page}: a stop that never confirms lets Start go with a note",
          "Detection has not confirmed it stopped" in psrc
          and "stoppingTimedOut" in psrc)
    check(f"{page}: no em dash", chr(0x2014) not in psrc)
zsrc = read("frontend/src/pages/Zones.jsx")
check("the feed overlay says it is still stopping",
      "hostStopping" in zsrc and '"Stopping detection…"' in zsrc)
dsrc = read("frontend/src/pages/Detection.jsx")
check("the page hands the panel its stopping state",
      "hostStopping={stopping}" in dsrc)

if bad:
    print(f"\n{bad} check(s) failed")
    sys.exit(1)
print("\nall good")
