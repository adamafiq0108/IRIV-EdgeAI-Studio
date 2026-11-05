# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
board_live.py -- what each board terminal is doing RIGHT NOW: the pin levels
read without claiming a line, the bus activity the running apps publish, and
the RS485 terminal's state built from it.

Lifted out of board_io.py, which re-exports every name here (the Ports
panel, the routes and the tests know them there), so that file stays under
its line ceiling. Nothing changed in the move. The board table, the one
subprocess wrapper (_run) and the running-apps cache are read off board_io
at call time: a test that stubs board_io._run still reaches pin_states.
"""

import glob as _globmod
import json
import os
import re
import time


# -- live pin levels, read WITHOUT claiming ----------------------------------
#
# The terminal diagram lights a terminal from what the pin is doing, not from
# what a rule last asked for. Reading that honestly has one hard constraint:
# the read must not touch the line. `gpioget` REQUESTS the line as an input --
# on an output that is free it flips the direction (a relay drops), and on one
# a detection app holds it simply fails. So the first choice is `pinctrl`
# (raspi-utils, on every Pi 5 / CM5 image): it reads the RP1 pad and function
# registers through /dev/gpiomem and requests nothing. debugfs is the fallback
# for an image without it (root only, and lists only requested lines).

#: One read serves every port of one poll (and read_port's calls right after
#: it), so the panel costs one subprocess a second, not eight.
_PIN_TTL_S = 0.3
_pin_cache = {"at": 0.0, "states": {}}


def _parse_pinctrl(text):
    """{pin: {"function", "level"}} from `pinctrl get` output.

    One line per pin:  `23: op dh pd | hi // GPIO23 = output`. `function` is
    pinctrl's short name ("ip", "op", "a0".., "no"); level is 1 for hi.
    """
    out = {}
    for row in (text or "").splitlines():
        m = re.match(r"\s*(\d+):\s+(\w+)\b[^|]*\|\s*(hi|lo)\b", row)
        if m:
            out[int(m.group(1))] = {"function": m.group(2),
                                    "level": 1 if m.group(3) == "hi" else 0}
    return out


def _parse_debugfs(text):
    """Same shape from /sys/kernel/debug/gpio:
    ` gpio-592 (GPIO23 |lg ) out hi`. Only requested lines appear there."""
    out = {}
    for row in (text or "").splitlines():
        m = re.search(r"\(GPIO(\d+)\s*\|[^)]*\)\s+(in|out)\s+(hi|lo)", row)
        if m and int(m.group(1)) not in out:
            out[int(m.group(1))] = {"function": "op" if m.group(2) == "out" else "ip",
                                    "level": 1 if m.group(3) == "hi" else 0}
    return out


def pin_states():
    """{pin: {"function", "level"}} for every board pin, or {} when this box
    cannot be read that way (a laptop, an image with neither source). Never
    claims a line, never changes a direction."""
    from core.services import board_io                       # noqa: PLC0415
    now = time.time()
    if now - _pin_cache["at"] < _PIN_TTL_S:
        return _pin_cache["states"]
    pins = ",".join(str(p["pin"]) for p in board_io.BOARD_PORTS)
    states = _parse_pinctrl(board_io._run(["pinctrl", "get", pins], timeout=3))
    if not states:
        try:
            with open("/sys/kernel/debug/gpio", encoding="utf-8") as f:
                states = _parse_debugfs(f.read())
        except OSError:
            states = {}
    _pin_cache.update(at=now, states=states)
    return states


def _lit(port, state, duty):
    """Whether a terminal's signal is active right now, from the pin itself.

    input  -- the pad reads high (the contact is closed / the sensor is on);
    output -- the pin is an OUTPUT and reads back high (the relay energised);
    pwm    -- an output whose app-published duty is above 0, or, driven by
              something that publishes nothing, the pad high at this instant.
    No reading means not lit: dark is "nothing we can see", never a guess.
    """
    if not state:
        return False
    if port["kind"] == "input":
        return state.get("level") == 1
    if state.get("function") != "op":
        return False
    if port["kind"] == "pwm" and duty is not None:
        return duty > 0
    return state.get("level") == 1


#: Written by each app's buses.py into its data dir (see app/buses.py
#: _note_activity): the facts about a wire only the process using it sees.
ACTIVITY_FILE = "bus_activity.json"


def app_activity():
    """{"rs485": {device: {ok_at, err_at, error}}, "pwm": {pin: duty}} merged
    across every RUNNING app.

    Two facts no pin read can give. A Modbus RTU exchange leaves nothing on
    a GPIO, and a software-PWM pin sampled once a second reads high or low by
    chance. A stopped app's file is ignored: its PWM stopped with it, and its
    last answer is history, not activity.
    """
    from core.services import board_io                       # noqa: PLC0415
    merged = {"rs485": {}, "pwm": {}}
    systems_dir, slugs = board_io._running()
    if systems_dir is None:
        return merged
    from core.services import detector_control               # noqa: PLC0415
    for slug in slugs:
        try:
            path = os.path.join(detector_control.data_dir(systems_dir, slug),
                                ACTIVITY_FILE)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:                                   # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        for device, row in (data.get("rs485") or {}).items():
            if not isinstance(row, dict):
                continue
            have = merged["rs485"].setdefault(str(device), {})
            for key in ("ok_at", "err_at"):
                try:
                    stamp = float(row.get(key) or 0)
                except (TypeError, ValueError):
                    continue
                if stamp > float(have.get(key) or 0):
                    have[key] = stamp
                    if key == "err_at":
                        have["error"] = str(row.get("error") or "")
        for pin, duty in (data.get("pwm") or {}).items():
            try:
                pin, duty = int(pin), float(duty)
            except (TypeError, ValueError):
                continue
            merged["pwm"][pin] = max(duty, merged["pwm"].get(pin, 0.0))
    return merged


#: How long one good Modbus RTU answer keeps the RS485 group lit. A Modbus
#: Read polls every 1-2 s by default, so a live bus never goes dark between
#: polls, and a bus that went quiet does within a few seconds.
RS485_LIVE_S = 5.0


def rs485_status(activity=None, now=None):
    """The RS485 terminal's state: what the bus has DONE lately.

    A screw terminal cannot say whether a cable is in it, and the CH9102
    adapter behind it (/dev/ttyACM0, datasheet 5.1) is there whether or not
    anything is wired. What can be known is whether a device ANSWERED:
      active -- a valid Modbus RTU response in the last RS485_LIVE_S seconds
                (an exception reply counts: something on the pair answered);
      error  -- the latest exchange in that window got no answer / failed;
      idle   -- the adapter is there and nothing has talked lately;
      absent -- no adapter device at all.
    """
    now = time.time() if now is None else now
    found = sorted(_globmod.glob("/dev/ttyACM*"))
    device = found[0] if found else "/dev/ttyACM0"
    out = {"device": device, "present": bool(found), "state": "absent",
           "ok_age_s": None, "error": "", "window_s": RS485_LIVE_S}
    if not found:
        return out
    ok_at = err_at = 0.0
    error = ""
    for name, row in ((activity or {}).get("rs485") or {}).items():
        # The app names the device the way its config does (possibly a
        # /dev/serial/by-id link); the terminal is the ttyACM node behind it.
        real = os.path.realpath(name) if os.path.exists(name) else name
        if not os.path.basename(real).startswith("ttyACM"):
            continue
        ok_at = max(ok_at, float(row.get("ok_at") or 0))
        if float(row.get("err_at") or 0) > err_at:
            err_at, error = float(row.get("err_at") or 0), row.get("error") or ""
    if ok_at:
        out["ok_age_s"] = round(max(0.0, now - ok_at), 1)
    latest = max(ok_at, err_at)
    if latest and now - latest <= RS485_LIVE_S:
        out["state"] = "active" if ok_at >= err_at else "error"
        out["error"] = "" if ok_at >= err_at else error
    else:
        out["state"] = "idle"
    return out
