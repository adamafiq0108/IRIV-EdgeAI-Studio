# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
services/device_gate.py -- may this software run on this box?

The studio runs ONLY on IRIV Edge AI hardware with an AI HAT fitted. Signing
in and starting detection both ask here first (routes/auth.py, the Start
button in routes/models.py), and so does the installer (scripts/install.sh).

Two questions, asked in this order, each with its own answer to show:

  1. Is it an IRIV Edge AI device?  EITHER signal is enough:
       * the compute module is a Raspberry Pi Compute Module 5 (the module
         every IRIV Edge AI board is built around), from /proc/device-tree/model;
       * an ATECC secure element (Microchip ATECC508/608) answers on an I2C
         bus at its address 0x60.
     No  -> code "not_iriv": RUN ONLY IN IRIV EDGE AI DEVICES.
  2. Is an AI HAT (the Hailo accelerator) fitted?  Asked of the same detector
     the startup check uses (builtin_models.detect_arch_uncached).
     No  -> code "no_ai_hat": To run this software, you need an AI HAT.

The ATECC sleeps until it is woken, and a sleeping one does not acknowledge
its address -- so a plain i2cdetect scan shows nothing even when the chip is
fitted. The probe sends the WAKE first (a general-call write, which holds SDA
low long enough), then reads the 4-byte wake response (0x04 0x11 0x33 0x43),
then puts the chip back to sleep.

A positive answer is cached for the life of the process (hardware does not
leave). A negative one is re-checked after NEGATIVE_TTL_S, so a box whose
i2c-dev module or Hailo driver came up late is not refused until a restart.

A bench or development machine opts out with the SAME switch as the startup
hardware check (services/hwcheck.py): IRIV_HW_CHECK=off in the environment,
or [hardware] enforce = false in config/system.local.toml. One switch for
"this is not a production board", not two that drift apart.

No framework imports here (docs/conventions.md sec 2).
"""

import glob
import os
import time

#: What each refusal says: a headline, and one plain sentence under it.
ANSWERS = {
    "not_iriv": {
        "title": "RUN ONLY IN IRIV EDGE AI DEVICES",
        "message": "This software runs only on IRIV Edge AI hardware. This device has no "
                   "Compute Module 5 and no IRIV secure element.",
    },
    "no_ai_hat": {
        "title": "AI HAT REQUIRED",
        "message": "To run this software, you need an AI HAT. No AI HAT (Hailo "
                   "accelerator) was found on this device - fit one, then restart it.",
    },
}

#: Kept for callers that only want one line.
NOT_IRIV_MSG = ANSWERS["not_iriv"]["title"]

#: How long a refusal is believed before asking the hardware again.
NEGATIVE_TTL_S = 30

ATECC_ADDR = 0x60
_I2C_SLAVE = 0x0703
#: What an ATECC5x8/6x8 answers right after waking: count, status 0x11, CRC.
_WAKE_REPLY = b"\x04\x11\x33\x43"

_cache = {"result": None, "at": 0.0}


def _model():
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode("utf-8", "replace").rstrip("\x00").strip()
    except OSError:
        return ""


def is_cm5():
    return "compute module 5" in _model().lower()


def _probe_atecc(bus_path):
    """True if an ATECC answers its wake on this bus."""
    try:
        import fcntl                                         # noqa: PLC0415
    except ImportError:
        return False
    try:
        fd = os.open(bus_path, os.O_RDWR)
    except OSError:
        return False
    try:
        # WAKE: a write to the general-call address 0x00 holds SDA low for a
        # byte time, which is the >60us low pulse the chip wakes on. It is
        # usually NACKed -- that is fine, the pulse is what matters.
        try:
            fcntl.ioctl(fd, _I2C_SLAVE, 0x00)
            os.write(fd, b"\x00")
        except OSError:
            pass
        time.sleep(0.0025)                    # tWHI: wake-high delay, 1.5 ms
        try:
            fcntl.ioctl(fd, _I2C_SLAVE, ATECC_ADDR)
            reply = os.read(fd, 4)
        except OSError:
            return False
        found = reply[:2] == _WAKE_REPLY[:2]
        if found:
            # Back to sleep (word address 0x01), so the chip is left as found.
            try:
                os.write(fd, b"\x01")
            except OSError:
                pass
        return found
    finally:
        os.close(fd)


def has_atecc():
    """True if an ATECC secure element answers on any I2C bus."""
    for bus in sorted(glob.glob("/dev/i2c-*")):
        if _probe_atecc(bus):
            return True
    # A kernel driver that already bound the chip owns the address, and the
    # probe above then cannot talk to it -- but the binding is the answer.
    return bool(glob.glob("/sys/bus/i2c/drivers/atmel-ecc/*-0060"))


def has_ai_hat():
    """True if an AI HAT (Hailo 8, 8L or 10H) is fitted."""
    try:
        from core.services import builtin_models             # noqa: PLC0415
        return bool(builtin_models.detect_arch_uncached())
    except Exception:                                        # noqa: BLE001
        return False


def iriv_device():
    """(ok, why) for question 1 alone -- the installer prints each answer."""
    if is_cm5():
        return True, "Compute Module 5"
    if has_atecc():
        return True, "ATECC secure element"
    return False, "no Compute Module 5 and no ATECC secure element found"


def _opted_out():
    try:
        from core.services import hwcheck                    # noqa: PLC0415
        return not hwcheck.enforced()
    except Exception:                                        # noqa: BLE001
        return False                                         # unreadable: enforce


def result():
    """{"ok", "code", "title", "message", "why"}. code is "" when ok."""
    if _opted_out():
        return {"ok": True, "code": "", "title": "", "message": "",
                "why": "hardware check off (IRIV_HW_CHECK / [hardware] enforce)"}
    now = time.monotonic()
    cached = _cache["result"]
    if cached and (cached["ok"] or now - _cache["at"] < NEGATIVE_TTL_S):
        return cached
    ok, why = iriv_device()
    if not ok:
        out = {"ok": False, "code": "not_iriv", "why": why, **ANSWERS["not_iriv"]}
    elif not has_ai_hat():
        out = {"ok": False, "code": "no_ai_hat", "why": f"{why}, but no AI HAT",
               **ANSWERS["no_ai_hat"]}
    else:
        out = {"ok": True, "code": "", "title": "", "message": "",
               "why": f"{why} with an AI HAT"}
    _cache.update(result=out, at=now)
    return out


def check():
    """(ok, why) -- the short form."""
    r = result()
    return r["ok"], r["why"]


def allowed():
    return result()["ok"]
