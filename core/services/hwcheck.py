"""The hardware this product is built for, and the check that it is there.

IRIV Edge AI runs on one board: a Raspberry Pi Compute Module 5 with the
ATECC608 secure element and a Hailo accelerator (8, 8L or 10H) fitted. The
installer checks it once (scripts/install.sh, "Hardware check"); this module
checks it again every time the software starts, from several places, so a card
moved to a different board, or an install that skipped the installer's check,
says so in plain words instead of failing somewhere deep in the detection loop.

Called from hub_server.py, master_server.py, the detection app
(systems/demo/app/hwcheck.py, its own copy: the app imports nothing from core)
and the license activation in core/services/store.py.

Nothing here is secret and nothing is sent anywhere: it reads the board in
front of it. When a check fails the message names what is missing.

A bench or development machine opts out, visibly, with either
    IRIV_HW_CHECK=off                      in the environment, or
    [hardware] enforce = false             in config/system.local.toml
and the process then logs that the check is off. The license for the template
store is a separate gate (store.py) and is not affected by the opt-out.
"""

import os
import sys
import time

from core import config
from core.services import atecc, builtin_models

_MODEL = "/proc/device-tree/model"

#: The exit status of a process the gate stopped. NOT 78: a master's unit lists
#: 78 in RestartPreventExitStatus (a removed system), and a board that was only
#: slow to show its chip must be restarted by systemd, not left down for good.
GATE_EXIT = 79

#: How long a start waits for a missing piece to appear before it gives up:
#: at boot the chip's bus, the Hailo driver and every other service are all
#: coming up together. IRIV_HW_WAIT_S overrides it.
WAIT_S = 60.0

#: What a missing piece is called in the error.
_NAMES = (("cm5", "Raspberry Pi Compute Module 5"),
          ("atecc", "ATECC608 secure element"),
          ("hailo", "Hailo accelerator"))


def _has_cm5():
    try:
        with open(_MODEL, encoding="utf-8", errors="ignore") as f:
            return "Compute Module 5" in f.read()
    except OSError:
        return False


def _has_atecc(config_dir=None):
    # The chip can be busy for a moment (another process mid-command), so a
    # miss is asked again before it is believed.
    for _ in range(3):
        if atecc.present(config_dir):
            return True
        atecc.info(config_dir, refresh=True)
    return False


def check(config_dir=None):
    """{"cm5": bool, "atecc": bool, "hailo": bool, "ok": bool, "missing": [..]}."""
    found = {"cm5": _has_cm5(),
             "atecc": _has_atecc(config_dir),
             "hailo": bool(builtin_models.detect_arch_uncached())}
    missing = [label for key, label in _NAMES if not found[key]]
    return {**found, "ok": not missing, "missing": missing}


def enforced(config_dir=None):
    if os.environ.get("IRIV_HW_CHECK", "").strip().lower() in ("off", "0", "false", "no"):
        return False
    try:
        section = (config.load("system", config_dir) or {}).get("hardware") or {}
    except Exception:                                        # noqa: BLE001
        section = {}
    return section.get("enforce", True) is not False


def message(result):
    return ("This board is missing hardware IRIV Edge AI requires: %s. "
            "It runs only on a Compute Module 5 with the ATECC608 secure "
            "element and a Hailo accelerator." % ", ".join(result["missing"]))


def _wait_s():
    try:
        return max(0.0, float(os.environ.get("IRIV_HW_WAIT_S", WAIT_S)))
    except ValueError:
        return WAIT_S


def require(config_dir=None, who="IRIV Edge AI"):
    """Stop the calling process with a plain message when the hardware is not
    all there. Returns the result when it is (or when the check is opted out,
    after saying so)."""
    if not enforced(config_dir):
        # The detection apps read the environment, not system.local.toml.
        os.environ["IRIV_HW_CHECK"] = "off"
        print("[HW] hardware check is switched off for %s" % who, file=sys.stderr)
        return None
    deadline = time.monotonic() + _wait_s()
    while True:
        result = check(config_dir)
        if result["ok"]:
            return result
        if time.monotonic() >= deadline:
            break
        time.sleep(5.0)
    print("[HW] ERROR: %s (%s)" % (message(result), who), file=sys.stderr)
    raise SystemExit(GATE_EXIT)
