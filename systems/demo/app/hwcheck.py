"""The detection app's own copy of the hardware check (core/services/hwcheck.py).

The app imports nothing from core/, so the three probes are repeated here:
the board model, the ATECC608 on I2C, and the Hailo on PCIe. What is checked
and how to opt out on a bench machine (IRIV_HW_CHECK=off) is described there.
"""

import os
import re
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:                  # not a POSIX box: no I2C either
    fcntl = None

_AWAKE = bytes((0x04, 0x11, 0x33, 0x43))

# One process at a time on the chip (core/services/atecc.py explains why): the
# flock is on the bus descriptor, the same inode the hub and masters lock.
_BUS_LOCK_WAIT_S = 10.0


def _lock_bus(fd):
    if fcntl is None:
        return False
    deadline = time.time() + _BUS_LOCK_WAIT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() > deadline:
                return False
            time.sleep(0.02)


def _has_cm5():
    try:
        with open("/proc/device-tree/model", encoding="utf-8", errors="ignore") as f:
            return "Compute Module 5" in f.read()
    except OSError:
        return False


def _has_atecc(bus_n=1, addr=0x60):
    if fcntl is None:
        return False
    return _probe_atecc(bus_addr=(bus_n, addr))


def _probe_atecc(bus_addr):
    bus, addr = bus_addr
    for _ in range(3):
        fd = None
        try:
            fd = os.open("/dev/i2c-%d" % bus, os.O_RDWR)
            if not _lock_bus(fd):
                continue                 # held too long: ask again, do not guess
            fcntl.ioctl(fd, 0x0703, 0x00)
            try:
                os.write(fd, b"\x00")
            except OSError:
                pass                 # the wake pulse is NACKed on purpose
            time.sleep(0.003)
            fcntl.ioctl(fd, 0x0703, addr)
            if os.read(fd, 4) == _AWAKE:
                try:
                    os.write(fd, b"\x01")        # back to sleep
                except OSError:
                    pass
                return True
        except OSError:
            pass
        finally:
            if fd is not None:
                os.close(fd)
        time.sleep(0.1)
    return False


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
    found = {"cm5": _has_cm5(), "atecc": _has_atecc(), "hailo": _has_hailo()}
    names = (("cm5", "Raspberry Pi Compute Module 5"),
             ("atecc", "ATECC608 secure element"), ("hailo", "Hailo accelerator"))
    missing = [label for key, label in names if not found[key]]
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
    print("[HW] ERROR: This board is missing hardware IRIV Edge AI requires: %s. "
          "It runs only on a Compute Module 5 with the ATECC608 secure element "
          "and a Hailo accelerator." % ", ".join(result["missing"]), file=sys.stderr)
    raise SystemExit(GATE_EXIT)
