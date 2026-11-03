"""The detection app's own copy of the hardware check (core/services/hwcheck.py).

The app imports nothing from core/, so the probe is repeated here: the Hailo
on PCIe. What is checked
and how to opt out on a bench machine (IRIV_HW_CHECK=off) is described there.
"""

import os
import re
import subprocess
import sys
import time

def _has_hailo():
    for argv in (["hailortcli", "fw-control", "identify"], ["lspci", "-mm"]):
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if re.search(r"hailo", out, re.I):
            return True
    # Neither tool answered: the PCI vendor id is 0x1e60 (Hailo Technologies).
    try:
        for name in os.listdir("/sys/bus/pci/devices"):
            with open("/sys/bus/pci/devices/%s/vendor" % name) as f:
                if f.read().strip().lower() == "0x1e60":
                    return True
    except OSError:
        pass
    return False


def check():
    found = {"hailo": _has_hailo()}
    missing = [] if found["hailo"] else ["Hailo accelerator"]
    return {**found, "ok": not missing, "missing": missing}


GATE_EXIT = 79           # not 78: see core/services/hwcheck.py
WAIT_S = 60.0


def _wait_s():
    try:
        return max(0.0, float(os.environ.get("IRIV_HW_WAIT_S", WAIT_S)))
    except ValueError:
        return WAIT_S


def require():
    if os.environ.get("IRIV_HW_CHECK", "").strip().lower() in ("off", "0", "false", "no"):
        print("[HW] hardware check is switched off for the detection app", file=sys.stderr)
        return None
    deadline = time.monotonic() + _wait_s()
    while True:
        result = check()
        if result["ok"]:
            return result
        if time.monotonic() >= deadline:
            break
        time.sleep(5.0)
    print("[HW] ERROR: This board is missing hardware IRIV EdgeAI Studio requires: %s. "
          "It runs only with a Hailo accelerator." % ", ".join(result["missing"]), file=sys.stderr)
    raise SystemExit(GATE_EXIT)
