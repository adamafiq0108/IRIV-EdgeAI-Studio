"""chip_holder() names who else is on an exclusive accelerator - as a
diagnostic, not a gate on start().

Found on a live Hailo-10H box running two detection systems: a Hailo-10H
opens the chip EXCLUSIVELY (multi_process off there by design) and a second
exclusive open does not fail, it TAKES the chip. The app that had it sees

    [HailoRT] Lost communication with the server. This may happen if VDevice
              is released while the CIM is in use
    [HailoRT] CHECK_SUCCESS failed with status=HAILO_COMMUNICATION_CLOSED(62)

falls back to a silent detector, retries, and takes the chip back. Measured
live: the two saw back and forth for as long as both ran, and whichever had
just lost it reported the AI as not running.

start() refused a second system on the strength of that measurement for one
day (af34895). The operator confirmed, twice, that this is not the product's
rule: the limit is MAX_RUNNING_APPS systems sharing MAX_CAMERAS cameras,
whatever the chip - multi_process stays true only for hailo8/hailo8l, as
before, and a Hailo-10H board is expected to run several systems on the one
exclusive chip regardless. So start() no longer refuses on chip_holder()'s
say-so; the function stays, for a page that wants to surface the contention
as a heads-up rather than block on it.

    python tests/smoke_chipexclusive.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFGS = {}
RUNNING = []


def fake():
    dc._merged = lambda pkg: CFGS.get(os.path.basename(str(pkg)), {})
    dc._pkg_dir = lambda systems_dir, slug: slug
    dc.running_apps = lambda systems_dir: list(RUNNING)


real = (dc._merged, dc._pkg_dir, dc.running_apps)
fake()
try:
    CFGS["a"] = {"detector": {"kind": "hailo"}}
    CFGS["b"] = {"detector": {"kind": "hailo"}}
    CFGS["cpu"] = {"detector": {"kind": "mock"}}
    CFGS["shared"] = {"detector": {"kind": "hailo",
                                   "hailo": {"multi_process": True}}}
    CFGS["exclusive"] = {"detector": {"kind": "hailo",
                                      "hailo": {"multi_process": False}}}

    check("a hailo system uses the chip; a mock one does not",
          dc.uses_chip("", "a") is True and dc.uses_chip("", "cpu") is False)

    # -- what the arch decides, when nobody has said -------------------------
    check("a Hailo-8 shares by default",
          dc.shares_chip("", "a", "hailo8") is True
          and dc.shares_chip("", "a", "hailo8l") is True,
          "the service is how one Hailo-8 serves several systems")
    check("...and a Hailo-10H does not share by DEFAULT",
          dc.shares_chip("", "a", "hailo10h") is False,
          "multi_process stays true only for hailo8/hailo8l - confirmed by "
          "the operator; running several systems on a Hailo-10H exclusive "
          "chip is expected anyway, just not through this setting")
    check("an explicit multi_process wins, both ways",
          dc.shares_chip("", "shared", "hailo10h") is True
          and dc.shares_chip("", "exclusive", "hailo8") is False,
          "a false written on purpose must stay false, even on a Hailo-8")

    # -- chip_holder() still names the contention, as a diagnostic -----------
    import core.services.builtin_models as bm                  # noqa: E402
    real_detect = bm.detect_arch
    bm.detect_arch = lambda: "hailo10h"
    try:
        RUNNING[:] = []
        check("nothing else running: nothing to report",
              dc.chip_holder("", "a") == "")
        RUNNING[:] = ["b"]
        check("another system on the exclusive chip is named",
              dc.chip_holder("", "a") == "b",
              "a page can still show this as a heads-up")
        RUNNING[:] = ["cpu"]
        check("...a system that does not use the chip is not in the way",
              dc.chip_holder("", "a") == "",
              "a mock or a CPU detector shares nothing")
        RUNNING[:] = ["a"]
        check("...and a system is never reported against itself",
              dc.chip_holder("", "a") == "",
              "restarting a running system must not warn about itself")
        RUNNING[:] = ["b"]
        check("a system asked to SHARE reports nothing",
              dc.chip_holder("", "shared") == "",
              "the service serves several; that is what it is for")
    finally:
        bm.detect_arch = real_detect
finally:
    dc._merged, dc._pkg_dir, dc.running_apps = real

# -- start() does NOT gate on chip_holder() -----------------------------------
src = io.open(os.path.join(ROOT, "core", "services", "detector_control.py"),
              encoding="utf-8").read()
check("start() no longer refuses a second system on an exclusive chip",
      "holder = chip_holder(systems_dir, slug)" not in src
      and "if holder:" not in src,
      "the operator confirmed twice: several systems on one exclusive chip "
      "is expected, not an error to refuse")
check("chip_holder() itself still exists, for a page to use as a diagnostic",
      "def chip_holder(systems_dir, slug):" in src)
check("the sharing list still mirrors the app's own default",
      'SHARING_ARCHS = ("hailo8", "hailo8l")' in src,
      "multi_process is still true only for hailo8/hailo8l - that part did "
      "not change, only whether start() blocks on the rest")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
