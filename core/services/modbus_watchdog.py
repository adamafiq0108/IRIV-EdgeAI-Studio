# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""modbus_watchdog.py -- the Modbus Server connection's Drive watchdog switch.

Kept out of detector_control.py, which sits at its line ceiling; its
clean_modbus_client calls clean() below as its last step. The runtime half
is app/buses.py (watchdog_writes, the writes on each connect) and
app/outputs.py (_repeat_every, keep sending at half the timeout).
"""

# -- the connection's Drive watchdog switch (Tok Aba 2026-10-07) -------------
#
# The save-time half of buses.watchdog_writes: a Modbus Server connection may
# say what the box does to a motor drive's comms watchdog each time it
# connects. Three states: NOT SET (the keys absent -- every connection saved
# before the switch, and one put back to "not managed"; the box touches
# nothing), "off" (WDT = 0) and "on" (WDT = timeout / 100, and every write on
# the connection keeps sending at half the timeout). Only WDT is written on
# connect; `cfg_save` ("Also save to the drive's memory", off by default)
# adds CFG_SAVE = 1 when the WDT value differs from the one last saved, so
# the drive's flash is written once per change, not per connect (app/buses.py
# keeps that record). The register numbers default to Cytron's motor driver map and
# are editable for other drives. Bad numbers are refused, not clamped: a
# clamped register address writes somebody else's register.

WATCHDOG_KEYS = ("watchdog", "watchdog_ms", "wdt_address", "cfg_save_address",
                 "cfg_save")
WATCHDOG_DEFAULTS = {"watchdog_ms": 1000, "wdt_address": 105,
                     "cfg_save_address": 100}
WATCHDOG_MS_MIN = 100
WATCHDOG_MS_MAX = 10000


def _whole(value, lo, hi, message):
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(message)
    if f != int(f) or not lo <= f <= hi:
        raise ValueError(message)
    return int(f)


def clean(body, current, out):
    """Copy the Drive watchdog settings of connection `body` (over the
    stored `current`) into the cleaned `out`. Raises ValueError."""
    merged = {k: v for k, v in (current or {}).items() if k in WATCHDOG_KEYS}
    merged.update({k: v for k, v in (body or {}).items()
                   if k in WATCHDOG_KEYS and v is not None})
    for k in WATCHDOG_KEYS:
        out.pop(k, None)
    mode = str(merged.get("watchdog") or "").strip().lower()
    if mode in ("", "unset", "none", "not managed"):
        return out
    if mode not in ("on", "off"):
        raise ValueError("the drive watchdog must be on, off or not managed")
    vals = {**WATCHDOG_DEFAULTS,
            **{k: v for k, v in merged.items() if v not in (None, "")}}
    ms = _whole(vals["watchdog_ms"], WATCHDOG_MS_MIN, WATCHDOG_MS_MAX,
                "the watchdog timeout must be a whole number of ms between "
                "100 and 10000")
    if ms % 100:
        raise ValueError("the watchdog timeout must be in steps of 100 ms "
                         "(the drive counts in 100 ms)")
    wdt = _whole(vals["wdt_address"], 0, 65535,
                 "the watchdog register must be a whole number 0 to 65535")
    save = _whole(vals["cfg_save_address"], 0, 65535,
                  "the save register must be a whole number 0 to 65535")
    if wdt == save:
        raise ValueError("the watchdog register and the save register must "
                         "be different")
    out.update(watchdog=mode, watchdog_ms=ms, wdt_address=wdt,
               cfg_save_address=save,
               cfg_save=str(merged.get("cfg_save")).strip().lower()
               in ("true", "1", "yes", "on"))
    return out
