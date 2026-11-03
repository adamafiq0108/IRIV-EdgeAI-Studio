# The hardware requirement: Compute Module 5 + ATECC608 + Hailo accelerator.
#
# Checked at install (scripts/install.sh) and again at every start from four
# places: the hub, the master, the detection app and the license activation.
# Here the probes are faked, so it runs anywhere:
#   * all three present passes; any one missing names that piece
#   * require() stops the process (exit 79, never 78: a master's unit does not restart on 78) with the message; opted out, it does not
#   * the opt-out is the environment or [hardware] enforce = false, nothing else
#   * license activation refuses on a board without the hardware
#   * every call site is in the source, and the app's copy agrees with core's
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
os.environ.pop("IRIV_HW_CHECK", None)

from core.services import hwcheck                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def fake(cm5=True, chip=True, hailo=True):
    hwcheck._has_cm5 = lambda: cm5
    hwcheck._has_atecc = lambda config_dir=None: chip
    hwcheck.builtin_models.detect_arch_uncached = lambda: "hailo8" if hailo else None


fake()
check("all three present passes", hwcheck.check()["ok"])
for name, kw, word in (("a missing CM5", {"cm5": False}, "Compute Module 5"),
                       ("a missing ATECC608", {"chip": False}, "ATECC608"),
                       ("a missing Hailo", {"hailo": False}, "Hailo")):
    fake(**kw)
    got = hwcheck.check()
    check(f"{name} fails and is named", not got["ok"] and word in hwcheck.message(got), got)

fake(chip=False)
os.environ["IRIV_HW_WAIT_S"] = "0"
try:
    hwcheck.require()
    check("require() stops the process when hardware is missing", False)
except SystemExit as exc:
    check("require() stops the process when hardware is missing", exc.code == 79, exc.code)
check("the gate's exit is not the removed-system status 78", hwcheck.GATE_EXIT != 78)
# a piece that shows up late (boot) is waited for, not a failure
hwcheck.time.sleep = lambda s: fake()
os.environ["IRIV_HW_WAIT_S"] = "30"
fake(chip=False)
check("a chip that appears within the wait passes", hwcheck.require()["ok"])
os.environ["IRIV_HW_WAIT_S"] = "0"
os.environ["IRIV_HW_CHECK"] = "off"
check("IRIV_HW_CHECK=off opts out", hwcheck.require() is None and not hwcheck.enforced())
os.environ.pop("IRIV_HW_CHECK")
fake()
check("with everything present require() returns the result", hwcheck.require()["ok"])

# license activation refuses without the hardware
from core.services import store                                      # noqa: E402
fake(cm5=False)
try:
    store.activate_license("any-key")
    check("license activation refuses on the wrong board", False)
except store.StoreError as exc:
    check("license activation refuses on the wrong board", "Compute Module 5" in str(exc), exc)
except Exception as exc:                                             # noqa: BLE001
    check("license activation refuses on the wrong board", False, repr(exc))


def src(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


check("the hub checks at start", "hwcheck.require(args.config_dir" in src("hub_server.py"))
check("the master checks at start", "hwcheck.require(args.config_dir" in src("master_server.py"))
check("the detection app checks at start", "hwcheck.require()" in src("systems", "demo", "app", "run.py"))
check("license activation checks", "hwcheck.check(config_dir)" in src("core", "services", "store.py"))
check("the installer checks", "Hardware check" in src("scripts", "install.sh"))
from app import hwcheck as apphw                                     # noqa: E402
apphw._has_cm5, apphw._has_atecc, apphw._has_hailo = (lambda: True, lambda: False, lambda: True)
check("the app's own copy names the same missing piece", apphw.check()["missing"] == ["ATECC608 secure element"])

from core import about                                               # noqa: E402
check("--about names the product, version and developer",
      "IRIV EdgeAI Studio" in about.text() and "Developed by adamcytron" in about.text())
check("the hub has --about", '"--about"' in src("hub_server.py"))

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
