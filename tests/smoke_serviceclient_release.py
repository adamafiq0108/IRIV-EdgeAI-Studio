# A Stop (or any detector change) on a hailort_service client re-executes
# the app instead of releasing the client in place.
#
# asri_test_4 on .49 (Hailo-8L, HailoRT 4.23, kernel 6.18), 2026-10-09: every
# AI Stop crashed hailort_service (systemd NRestarts=11 in 40 minutes, one per
# app death) and took the app with it inside det.close() -- gRPC 14, "Failed
# to shutdown", "Executing pipeline terminate failed", and never the line
# "AI detection switched off". The supervisor then restarted the app with the
# AI off, or backed off and left the workflow down until somebody pressed
# Start. This pins:
#
#   1. hailo._open_vdevice says whether the VDevice is a service client
#      (`opened`), False on the exclusive fallback; HailoDetector carries it
#      as via_service
#   2. run.release_by_exec: True for a service client on a change the config
#      asked for; False for the watchdog's own halves, a paused wait, --once,
#      and any detector that is not a service client
#   3. the reload block asks it BEFORE det.close(), logs the switch-off, and
#      restarts with fault=False (no CRITICAL, no error on the state file)
#
#   py -3 tests/smoke_serviceclient_release.py
import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. hailo: which kind of VDevice was opened ------------------------------
class _Params:
    multi_process_service = False


class _VDevice:
    refuse_service = False

    def __init__(self, params):
        if params.multi_process_service and _VDevice.refuse_service:
            raise RuntimeError("HAILO_RPC_FAILED(77)")
        self.service = params.multi_process_service

    @staticmethod
    def create_params():
        return _Params()


sys.modules["hailo_platform"] = types.SimpleNamespace(
    VDevice=_VDevice,
    HailoSchedulingAlgorithm=types.SimpleNamespace(ROUND_ROBIN=1))
from app import hailo                                          # noqa: E402

opened = {}
vd = hailo._open_vdevice(None, True, opened)
check("a service VDevice says so", vd.service and opened == {"service": True},
      opened)
_VDevice.refuse_service = True
opened = {}
# Past the service grace with no other client (tests/smoke_hailoservice.py
# pins the waiting itself): straight to the exclusive fallback.
hailo._sleep = lambda s: None
hailo._service_active = lambda: True
hailo._other_service_clients = lambda: []
hailo._mark_service_client = lambda on: None
hailo.SERVICE_GRACE_S = 0
vd = hailo._open_vdevice(None, True, opened)
check("...the exclusive fallback says it is not one",
      not vd.service and opened == {"service": False}, opened)
_VDevice.refuse_service = False
opened = {}
hailo._open_vdevice(None, False, opened)
check("...nor is an exclusive open asked for as such",
      opened == {"service": False}, opened)
check("...and the old two-argument call still works",
      not hailo._open_vdevice(None, False).service)
src = read("systems", "demo", "app", "hailo.py")
check("HailoDetector starts with via_service False and sets it from the open",
      "self.via_service = False" in src
      and 'self.via_service = bool(opened.get("service"))' in src)

# -- 2. run.release_by_exec --------------------------------------------------
from app import run as apprun                                  # noqa: E402

svc = types.SimpleNamespace(via_service=True)
own = types.SimpleNamespace(via_service=False)
null = types.SimpleNamespace(name="none")
rbe = apprun.release_by_exec
check("a service client is let go by exec on a config change",
      rbe(svc) is True)
check("...not on the watchdog's down or up half",
      rbe(svc, "down") is False and rbe(svc, "up") is False)
check("...not while waiting for a missing camera",
      rbe(svc, None, "paused") is False)
check("...not with --once", rbe(svc, None, None, True) is False)
check("an exclusive client or the null detector is closed in place",
      rbe(own) is False and rbe(null) is False)

# -- 3. the reload block -----------------------------------------------------
run_src = read("systems", "demo", "app", "run.py")
block = run_src[run_src.index("# -- detector (model / confidence / classes)"):]
block = block[:block.index("# -- cameras (added, removed, re-pointed)")]
check("the reload block asks release_by_exec before det.close()",
      "release_by_exec(det, restart_half" in block
      and block.index("release_by_exec(") < block.index("det.close()"),
      block[:400])
check("...says AI detection switched off before the exec",
      block.index('"AI detection switched off') < block.index("_restart_self("))
check("...and the exec is the planned kind (fault=False)",
      "fault=False)" in block)
rs = run_src[run_src.index("def _restart_self(why, fault=True):"):]
rs = rs[:rs.index("os.execv(")]
check("_restart_self(fault=False) writes no CRITICAL and no state error",
      "if fault:" in rs and rs.index("if fault:") < rs.index("log.critical(")
      and rs.index("if fault:") < rs.index("runtime.write(data_dir, error="))

print("\n" + ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
