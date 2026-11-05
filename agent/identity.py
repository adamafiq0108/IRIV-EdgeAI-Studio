# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
identity.py -- Canonical device identity.

host_serial is the Pi CPU serial. It is the primary key for a device
everywhere in the product. Operator-facing labels are display only: two
devices can share a label, they cannot share a CPU serial.

Do not confuse this with a camera serial. Different namespace, different
lifetime, different device. Name variables host_serial / camera_serial --
never bare `serial`.
"""

import pathlib
import re

_DEVICETREE = pathlib.Path("/sys/firmware/devicetree/base/serial-number")
_CPUINFO = pathlib.Path("/proc/cpuinfo")

_cached = None


def _from_devicetree():
    if not _DEVICETREE.exists():
        return None
    # Devicetree exports null-terminated strings. Reading this as text and
    # skipping the strip() leaves a trailing \x00 that compares unequal to the
    # same serial read anywhere else -- a mismatch with no visible cause.
    raw = _DEVICETREE.read_bytes()
    return raw.decode("ascii", "ignore").strip("\x00").strip() or None


def _from_cpuinfo():
    if not _CPUINFO.exists():
        return None
    for line in _CPUINFO.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^Serial\s*:\s*(\S+)", line)
        if m:
            return m.group(1)
    return None


def host_serial(fallback=None):
    """Return this device's CPU serial, lowercased.

    Tries devicetree, then /proc/cpuinfo. Raises unless `fallback` is given --
    a device with no stable identity must not silently invent one, or the
    master will register it as a new slave on every restart.
    """
    global _cached
    if _cached:
        return _cached

    value = _from_devicetree() or _from_cpuinfo()
    if not value:
        if fallback is None:
            raise RuntimeError(
                "No host serial: neither /sys/firmware/devicetree/base/serial-number "
                "nor /proc/cpuinfo Serial is readable. Pass a fallback only for "
                "development off-device."
            )
        value = fallback

    _cached = value.lower()
    return _cached
