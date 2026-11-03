# -*- coding: utf-8 -*-
"""A camera alarm must belong to the run that produced it.

Reported from a live board: the Detection page showed a red

    Camera problem
    camera disconnected: 'orbbec' gave no frames after 3 reconnect attempts

on a system that had never been started in that session -- and the card
DISAPPEARED when the operator pressed Start. Backwards from what an alarm
means, and about a camera (an Orbbec that box does not have) nobody was
using.

Two causes, one on each side:

  * the app's state file keeps the reason its last run died of, and
    runtime.status() handed that back as `error` whether or not the app was
    alive. `cameras` had always been gated on the pid; `error` never was.
  * the card rendered on `camError` alone, with no reference to whether the
    system was running.

The fix keeps the reason -- an operator who STOPPED a system because the
camera failed still wants to read why -- but demotes it: a stopped system
reports `last_error` (stamped with when it was written) and no `error`, and
the page renders that as a quiet line instead of a red card.

The half that matters more is the other direction: a gate that silences a
REAL failure is a worse bug than the one being fixed. Both directions are
asserted below, on the runtime and on the hub service.

    python tests/smoke_stalecamerror.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAIL = []

FAULT = ("camera disconnected: 'orbbec' gave no frames after 3 reconnect "
         "attempts")


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def load_runtime():
    """The app package is not importable as `app.runtime` from here (it is a
    per-system copy under systems/), so load the module by path."""
    path = os.path.join(ROOT, "systems", "demo", "app", "runtime.py")
    spec = importlib.util.spec_from_file_location("_smoke_runtime", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rt = load_runtime()
tmp = tempfile.mkdtemp(prefix="stalecam_")

print("== the runtime: a fault is dated, and dies with its run")

rt.write(tmp, running=True, pid=4242, started_at=time.time())
rt.write(tmp, camera="cam0", error=FAULT)
state = json.load(io.open(os.path.join(tmp, "state.json"), encoding="utf-8"))
check("the failure reason is stamped with the moment it was written",
      isinstance(state.get("error_at"), float),
      "without a date, a reason from a previous run cannot be told apart "
      "from a current one")

# STOPPED: nothing owns that pid. _alive/_owns are the two things that decide
# it, and both are patched rather than driven through a real process so the
# test behaves the same on the Windows dev box and on a Pi.
rt._alive = lambda pid: False
st = rt.status(tmp)
check("a stopped system raises NO camera alarm from the previous run",
      st["error"] is None,
      "state.json still held a reason and status() handed it back as live")
check("...but the reason is still readable as last_error",
      st["last_error"] == FAULT,
      "the reason was deleted, not demoted -- an operator who stopped the "
      "system BECAUSE the camera failed can no longer see why")
check("...with the moment it happened", isinstance(st["last_error_at"], float))

# RUNNING, camera failing: the case the gate must NOT silence.
rt._alive = lambda pid: True
rt._owns = lambda pid, pkg_dir=None: True
st = rt.status(tmp)
check("a RUNNING system with a failing camera still reports the fault",
      st["error"] == FAULT,
      "the gate silenced a real failure, which is worse than the stale card")
check("...and does not double it up as history",
      st["last_error"] is None and st["last_error_at"] is None)

# Starting a run wipes the previous run's reason (start() has always written
# error=None); the stamp must go with it, or a fresh error inherits an old
# date.
rt.write(tmp, running=True, error=None)
state = json.load(io.open(os.path.join(tmp, "state.json"), encoding="utf-8"))
check("clearing the error clears its stamp too", state.get("error_at") is None)
check("a cleared error is not reported by a running app",
      rt.status(tmp)["error"] is None)

print("== the hub service: the same gate, re-applied where the code is current")


class FakeRuntime(object):
    """Stands in for a system's OWN copy of app/runtime.py under the store."""

    def __init__(self, payload):
        self.payload = payload

    def status(self, data_dir, pkg_dir=None):
        return dict(self.payload)


from core.services import detector_control as dc                  # noqa: E402

CFG = {"camera": {"source": "0", "label": "front"},
       "detector": {"kind": "mock"}, "app": {}}
dc._pkg_dir = lambda systems_dir, slug: "/pkg/" + slug
dc._merged = lambda pkg_dir: dict(CFG)
dc._data_dir = lambda pkg_dir, cfg: "/data"
dc._camera_list = lambda cfg: [{"id": "cam0", "source": "0"}]
dc._refresh_usb_names = lambda cams: cams
dc._zone_list = lambda cfg: []


def hub_status(payload, slug="demo"):
    dc._app_runtime = lambda pkg_dir: FakeRuntime(payload)
    dc._running_cache.clear()
    return dc.status("/systems", slug)


# A system whose app copy is CURRENT: it already splits the two fields.
st = hub_status({"running": False, "error": None, "last_error": FAULT,
                 "last_error_at": 1.0, "cameras": None})
check("stopped: no live error reaches the page", st["error"] is None)
check("stopped: the last run's reason does, as history",
      st["last_error"] == FAULT and st["last_error_at"] == 1.0)

# A system created BEFORE the split still runs its own older app/runtime.py
# and reports the leftover reason as `error`. Every installed system has its
# own copy, so the hub cannot assume the app is current.
st = hub_status({"running": False, "error": FAULT, "cameras": None})
check("an OLDER app copy's leftover error is gated by the hub too",
      st["error"] is None,
      "a system installed before this fix would keep showing the red card")
check("...and is still offered as last_error", st["last_error"] == FAULT)

# The direction that matters more.
st = hub_status({"running": True, "error": FAULT,
                 "cameras": [{"id": "cam0", "error": FAULT}]})
check("running + failing camera: the fault reaches the page",
      st["error"] == FAULT)
check("...and the per-camera error with it",
      st["cameras"][0]["error"] == FAULT)
check("...and is not also reported as last run's history",
      st["last_error"] is None)

print("== the card: gated on running, and legible")
page = read("frontend", "src", "pages", "Detection.jsx")
block = page.split("const camError = activeCam", 1)[1].split("})()}", 1)[0]
check("the card is not rendered at all while the system is stopped",
      "if (!running) {" in block,
      "it rendered on camError alone, with no reference to whether the "
      "system was running")
# The red fault left the inline card for the camera fault popup
# (CamFaultDialog, opened from camFaultOf). The rule is the same one: a
# live camera error is only a fault WHILE RUNNING; stopped, the page reads
# the presence probe (is the camera there NOW), never the last run's error.
fault_of = page.split("function camFaultOf(", 1)[1].split("\n}\n", 1)[0]
check("a stopped system's reason is shown quietly, not as an alert",
      "status.last_error" in block and 'role="alert"' not in block
      and fault_of.index("if (running) {") < fault_of.index("cam.error")
      < fault_of.index("if (presence[cam.id] === false)")
      and "error" not in fault_of.split("if (presence[cam.id] === false)", 1)[1],
      "the popup must raise a camera's live error only inside the running "
      "branch, and the inline block must carry no alert card")
dialog = page.split("function CamFaultDialog(", 1)[1].split("\n}\n", 1)[0]
# --warn is the FILL token: 2.9:1 as text on the dark card. The *-text pair
# is what LogLevel.jsx's rows use for the same meanings as words.
check("the words use the *-text tokens, not the fill ones",
      "var(--warn-text" in block and "var(--alert-text" in dialog)
check("...and the icon beside them too",
      "color: toneText" in block and "color: tone," not in block)
check("the fill tokens still paint the border and the tint",
      "var(--warn, #b45309)" in block and "var(--alert, #dc2626)" in dialog)

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    sys.exit(1)
print("all good")
