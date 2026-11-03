# The cheap ones from perf audit item L (2026-10-02):
#   * /api/hub/health no longer blocks 0.25 s per call on psutil.cpu_percent:
#     it reads CPU% since the previous caller (the footer's sampler), and only
#     the very first call pays the blocking read;
#   * detect_arch (hailortcli + lspci subprocesses) is cached per process --
#     a found chip for good, "none" retried after ARCH_RETRY_S;
#   * the pages' own setInterval pollers pause while the tab is hidden.
import io
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- host_health ------------------------------------------------------------------
from core.services import host_health                                # noqa: E402

blocking = []
fake_psutil = types.SimpleNamespace(
    cpu_percent=lambda interval=None: blocking.append(interval) or 50.0,
    virtual_memory=lambda: types.SimpleNamespace(percent=10.0),
    disk_usage=lambda p: types.SimpleNamespace(percent=20.0),
    cpu_count=lambda: 4, getloadavg=lambda: (0.5, 0, 0))
sys.modules["psutil"] = fake_psutil
host_health._storages = lambda: []
host_health._npu = lambda: {}
seq = iter([None, 12.5, 13.0])
host_health._cpu_percent_since_last = lambda: next(seq)
first = host_health.host_health()["cpu_percent"]
second = host_health.host_health()["cpu_percent"]
third = host_health.host_health()["cpu_percent"]
check("the first call has no previous sample and reads it the blocking way, once",
      first == 50.0 and blocking == [0.25], blocking)
check("every call after that reads since-last, without the 0.25 s wait",
      second == 12.5 and third == 13.0 and blocking == [0.25])
del sys.modules["psutil"]

# -- detect_arch ---------------------------------------------------------------------
from core.services import builtin_models as bm                       # noqa: E402

runs = []
answers = [None, "hailo10h"]
bm.detect_arch_uncached = lambda: runs.append(1) or answers[min(len(runs) - 1, 1)]
bm._arch.update(value=None, at=0.0)
check("no chip found: None", bm.detect_arch() is None and len(runs) == 1)
check("...asked again within ARCH_RETRY_S: not re-run", bm.detect_arch() is None
      and len(runs) == 1)
bm._arch["at"] -= bm.ARCH_RETRY_S + 1
check("...after it, asked again (a driver that was still loading)",
      bm.detect_arch() == "hailo10h" and len(runs) == 2)
for _ in range(5):
    bm.detect_arch()
check("a found chip is kept for the life of the process", len(runs) == 2)

# -- the pollers pause on a hidden tab --------------------------------------------------
for path, why in ((("frontend", "src", "pages", "Detection.jsx"), "camera budget and capture"),
                  (("frontend", "src", "pages", "Dataset.jsx"), "table status"),
                  (("frontend", "src", "pages", "Blynk.jsx"), "push status"),
                  (("frontend", "src", "pages", "Modbus.jsx"), "listener state"),
                  (("frontend", "src", "components", "AccountsPanel.jsx"), "sessions"),
                  (("frontend", "src", "workflow", "DebugPanel.jsx"), "event log")):
    src = read(*path)
    check(f"{path[-1]} ({why}) polls through useVisibleInterval, not setInterval",
          "useVisibleInterval(" in src and not re.search(r"setInterval\(", src))
check("the Workflow page's poll pauses too (usePoll, perf item L2)",
      'usePoll("/api/workflow/live", 4000)' in read("frontend", "src", "pages", "Workflow.jsx"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
