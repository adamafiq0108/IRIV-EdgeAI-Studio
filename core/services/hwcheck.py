"""The hardware this product is built for, and the check that it is there.

IRIV EdgeAI Studio needs a Hailo accelerator (8, 8L or 10H) fitted. The
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
from core.services import builtin_models

#: The exit status of a process the gate stopped. NOT 78: a master's unit lists
#: 78 in RestartPreventExitStatus (a removed system), and a board that was only
#: slow to show its chip must be restarted by systemd, not left down for good.
GATE_EXIT = 79

#: How long a start waits for a missing piece to appear before it gives up:
#: at boot the chip's bus, the Hailo driver and every other service are all
#: coming up together. IRIV_HW_WAIT_S overrides it.
WAIT_S = 60.0

#: What a missing piece is called in the error.
_NAMES = (("hailo", "Hailo accelerator"),)


def check(config_dir=None):
    """{"hailo": bool, "ok": bool, "missing": [..]}."""
    found = {"hailo": bool(builtin_models.detect_arch_uncached())}
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
    return ("This board is missing hardware IRIV EdgeAI Studio requires: %s. "
            "It runs only with a Hailo accelerator." % ", ".join(result["missing"]))


def _wait_s():
    try:
        return max(0.0, float(os.environ.get("IRIV_HW_WAIT_S", WAIT_S)))
    except ValueError:
        return WAIT_S


def require(config_dir=None, who="IRIV EdgeAI Studio"):
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
