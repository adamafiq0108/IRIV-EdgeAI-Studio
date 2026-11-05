# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
board_io.py -- what physical I/O this box has, for the UI and the workflow.

The detection app drives the pins (systems/demo/app/iriv.py); this is the half
the browser talks to: which ports exist, what they are doing right now, and a
manual test so an installer can prove a wire before a rule depends on it.

**Pin numbers are evidence, not folklore.** The map below was read off the
board's own factory Node-RED flows (`rpi-gpio` nodes named "DI0", "DO1",
"LED0 Output" ...), which is the vendor's own statement of its pinout. It is
applied ONLY when this box is actually an IRIV Edge AI -- a wrong pin closes a
relay on real machinery, so a bare Raspberry Pi gets an empty list and a
straight answer instead of a guess.

Mirrored from app/iriv.py on purpose: the app imports nothing from core, so the
table lives in both. Keep them in step.

L0/L1 were mapped to GPIO20/21 until 2026-10-07. The datasheet (Rev 1.0, 5.1)
puts the L0/L1 LED driver terminals on GPIO12/13; 20/21 are the two USER LEDs
on the board, which is what the factory flow's "LED0 Output" node drives. A
rule on "L0" was blinking a status LED instead of the terminal it names.
"""

import glob as _globmod
import os
import re
import subprocess
import time

# Lifted out so this file stays under its line ceiling, nothing changed, and
# re-exported under the names the routes, the panel and the tests use:
# board_live.py is what each terminal is doing right now (pin levels, the
# apps' bus activity, RS485); board_release.py the terminals handed to
# another program and the rules that use each one.
from core.services.board_live import (ACTIVITY_FILE, RS485_LIVE_S,  # noqa: F401
                                      _PIN_TTL_S, _lit, _parse_debugfs, _parse_pinctrl,
                                      _pin_cache, app_activity, pin_states, rs485_status)
from core.services.board_release import (RELEASED_CONFIG, _display_names,  # noqa: F401
                                         _every_system, port_users, released,
                                         set_released)

#: id, what it does, the BCM pin behind it.
BOARD_PORTS = (
    {"id": "DI0", "kind": "input",  "pin": 22, "label": "Digital input 0"},
    {"id": "DI1", "kind": "input",  "pin": 27, "label": "Digital input 1"},
    {"id": "DO0", "kind": "output", "pin": 23, "label": "Digital output 0"},
    {"id": "DO1", "kind": "output", "pin": 24, "label": "Digital output 1"},
    {"id": "L0",  "kind": "pwm",    "pin": 12, "label": "LED / PWM 0"},
    {"id": "L1",  "kind": "pwm",    "pin": 13, "label": "LED / PWM 1"},
    {"id": "BUZZER", "kind": "output", "pin": 19, "label": "Buzzer"},
    {"id": "BUTTON", "kind": "input",  "pin": 4,  "label": "User button"},
)

PORT_BY_ID = {p["id"]: p for p in BOARD_PORTS}

#: The chip these BCM numbers live on. gpiochip0 on every Pi this ships to;
#: named once so a board revision that moves them is one edit.
_CHIP = 0

#: Ports a workflow may DRIVE, by node kind. Digital and PWM are separate on
#: purpose -- a relay and a brightness are different verbs, and the nodes that
#: say them are different nodes.
OUTPUT_PORTS = tuple(p["id"] for p in BOARD_PORTS if p["kind"] == "output")
PWM_PORTS = tuple(p["id"] for p in BOARD_PORTS if p["kind"] == "pwm")
INPUT_PORTS = tuple(p["id"] for p in BOARD_PORTS if p["kind"] == "input")


def _run(argv, timeout=6):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def board_model():
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return f.read().decode("utf-8", "replace").rstrip("\x00").strip()
    except OSError:
        return ""


#: The carrier's own I2C address, on the bus the board wires it to. The IRIV
#: carrier puts an SSD1306 here; a bare CM5 on a third-party carrier has
#: nothing at this address, which is what makes it worth asking.
_CARRIER_I2C_BUS = 1
_CARRIER_I2C_ADDR = 0x3C
_I2C_SLAVE = 0x0703

_carrier_seen = [None]                  # process-lifetime cache for the probe


def carrier_present():
    """Whether the carrier's own I2C device answers -- a HARDWARE signal.

    Asked of the bus rather than of the filesystem, and cached for the process
    because a bus probe on every call would be paid by the Ports panel once a
    second. A box where the i2c device cannot be opened at all (no i2c-dev, no
    group membership, not Linux) answers False rather than raising: this is
    corroboration, and an unavailable probe must not be read as a denial.
    """
    if _carrier_seen[0] is not None:
        return _carrier_seen[0]
    found = False
    try:
        import fcntl                                         # noqa: PLC0415

        fd = os.open("/dev/i2c-%d" % _CARRIER_I2C_BUS, os.O_RDWR)
        try:
            fcntl.ioctl(fd, _I2C_SLAVE, _CARRIER_I2C_ADDR)
            # A zero-length write, which is how i2cdetect probes this range:
            # it makes the device ACK without asking it to do anything.
            os.write(fd, b"")
            found = True
        finally:
            os.close(fd)
    except (OSError, ImportError, AttributeError):
        found = False
    _carrier_seen[0] = found
    return found


def detected():
    """Whether this box is an IRIV Edge AI, from two independent signals.

    The CM5 module it is built around, AND corroboration that the carrier
    under it is ours. Either alone is not enough: a bare CM5 on someone
    else's carrier must never be handed this pinout.

    Corroboration is EITHER the carrier's own I2C device answering, OR the
    /etc/iriv marker. The I2C probe is first because it asks the hardware:
    /etc/iriv is written by the platform SOFTWARE (it holds installed_apps
    and a migration flag), so a board flashed with a stock Raspberry Pi OS
    image, or one that never ran the older stack, has no such directory --
    and reported "no IRIV board on this box" for the USER button and the
    buzzer while the OLED two lines above it was working, on a real IRIV
    board. A leftover of one program is not evidence about hardware.
    """
    model = board_model()
    if "compute module 5" not in model.lower():
        return False
    return bool(carrier_present() or os.path.isdir("/etc/iriv"))


def gpio_lines():
    """{pin: {"name", "direction", "consumer"}} from libgpiod, or {}.

    `consumer` is the interesting field: a line another program already holds
    cannot be driven from here. The factory Node-RED flow holds several of
    these out of the box, and "GPIO busy" with no name attached is a support
    call waiting to happen.
    """
    text = _run(["gpioinfo", "gpiochip0"]) or _run(["gpioinfo"])
    out = {}
    for row in text.splitlines():
        match = re.search(r"line\s+(\d+):\s+\"([^\"]*)\"\s+(\w+)", row.strip())
        if not match:
            continue
        pin = int(match.group(1))
        if pin in out:                      # first chip wins (gpiochip0)
            continue
        consumer = re.search(r"consumer=\"([^\"]*)\"", row)
        out[pin] = {"name": match.group(2), "direction": match.group(3),
                    "consumer": consumer.group(1) if consumer else ""}
    return out


def serial_ports():
    """Serial devices that could carry RS485, most specific first.

    The isolated RS485 port appears as a UART device; USB adapters appear as
    ttyUSB (FTDI/CH340 style) or ttyACM (CDC class). Listing what is THERE beats defaulting to a device path that may
    not exist -- a Modbus write to a missing device fails silently on the wire
    and loudly nowhere.
    """
    import glob                                             # noqa: PLC0415

    found = sorted(glob.glob("/dev/ttyAMA*") + glob.glob("/dev/ttyUSB*")
                   + glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttySC*")
                   + glob.glob("/dev/ttyS[0-9]"))
    # Not the kernel console (ttyAMA10 on the IRIV, with a getty on it): a
    # Modbus client opening it talks to a login prompt, and it was the one
    # UART the list used to put first.
    try:
        with open("/sys/class/tty/console/active", encoding="utf-8") as f:
            console = {f"/dev/{n}" for n in f.read().split()}
    except OSError:
        console = set()
    return [p for p in found if os.path.exists(p) and p not in console]


#: Lines THIS process has claimed (a read, a test pulse). lgpio names every
#: consumer "lg" -- ours and everyone else's -- so without this the first time
#: the panel read DI0 it would report DI0 as held by a stranger, for ever: the
#: chip handle stays open for the life of the process even after the device is
#: closed. A port we touched ourselves is not a conflict.
_CLAIMED = set()


#: running_apps() asks every system for its status. port_owners() and
#: app_activity() both need the answer on every poll of the Ports panel, so
#: it is kept for about one poll rather than computed twice a second.
_RUNNING_TTL_S = 1.0
_running_cache = {"at": 0.0, "value": None}


def _running():
    """(systems_dir, [slug]) of the detection apps running now, or (None, [])."""
    now = time.time()
    if _running_cache["value"] is not None \
            and now - _running_cache["at"] < _RUNNING_TTL_S:
        return _running_cache["value"]
    try:
        from core import config as config_mod                # noqa: PLC0415
        from core.services import detector_control           # noqa: PLC0415
        systems_dir = config_mod.generated_systems_dir()
        value = (systems_dir, detector_control.running_apps(systems_dir))
    except Exception:                                       # noqa: BLE001
        value = (None, [])
    _running_cache.update(at=now, value=value)
    return value


def port_owners():
    """{port_id: slug} -- which RUNNING system app holds each board terminal.

    The detection apps are separate processes, and libgpiod reports every
    lgpio consumer as "lg", so from here a pin our own workflow drives is
    byte-for-byte identical to one the factory Node-RED flow owns. Each app
    therefore publishes the terminals it holds in its state file (`io_held`,
    written every cycle) and this reads them back.

    Best-effort by design: a system whose state cannot be read contributes
    nothing and its pin falls back to the honest "held by something else".
    """
    owners = {}
    systems_dir, slugs = _running()
    if systems_dir is None:
        return owners
    from core.services import detector_control               # noqa: PLC0415
    for slug in slugs:
        try:
            state = detector_control.app_state(systems_dir, slug) or {}
        except Exception:                                   # noqa: BLE001
            continue
        for port_id in (state.get("io_held") or []):
            owners.setdefault(str(port_id).upper(), slug)
    return owners


def ports(slug=None, activity=None):
    """Every port with what it is doing right now.

    [{id, kind, pin, label, busy, consumer, direction, owner, level,
    function, duty, lit}] -- empty on a box that is not an IRIV. `busy`
    means another program holds the line; the UI shows it on the port
    itself, because finding out after wiring a rule costs an operator a site
    visit.

    `lit` is the terminal diagram's light: the signal is active NOW, read
    back from the pin (see _lit), never from what a rule last asked for.
    `level`/`function` are the raw read (None/"" when unreadable), `duty` the
    PWM duty a running app published for an L terminal.

    `slug` is the system being looked at. A terminal held by THAT system's
    own app is not a conflict -- it is the rule the operator is editing,
    doing exactly what they asked. Held by a DIFFERENT system it still is a
    conflict (two owners of one relay), but `owner` names which, so the page
    can say so instead of blaming Node-RED for a pin we drive ourselves.
    """
    if not detected():
        return []
    lines = gpio_lines()
    owners = port_owners()
    handed_over = released()
    states = pin_states()
    pwm = (activity if activity is not None else app_activity()).get("pwm") or {}
    out = []
    for port in BOARD_PORTS:
        info = lines.get(port["pin"]) or {}
        consumer = info.get("consumer") or ""
        owner = owners.get(port["id"], "")
        state = states.get(port["pin"]) or {}
        duty = pwm.get(port["pin"]) if port["kind"] == "pwm" else None
        # Ours three ways: this process claimed it, the system being edited
        # holds it, or -- with no system in context -- any of our own apps
        # does. The last keeps the hub-wide board page from calling every
        # terminal the box drives a stranger's.
        held_by_us = (port["pin"] in _CLAIMED
                      or (owner and (slug is None or owner == slug)))
        out.append({**port,
                    "direction": info.get("direction") or "",
                    "owner": owner,
                    # Handed to another program deliberately. Not the same as
                    # `busy`: busy is an observation about the line right now,
                    # released is a STANDING decision that survives whether
                    # anything happens to be holding it this second.
                    "released": port["id"] in handed_over,
                    "consumer": "" if held_by_us else (owner or consumer),
                    "busy": bool(consumer) and not held_by_us,
                    "level": state.get("level"),
                    "function": state.get("function") or "",
                    "duty": duty,
                    "lit": _lit(port, state, duty)})
    return out


def inventory(slug=None):
    """One payload for the UI: the board, its ports, its serial devices, and
    whether the RS485 bus has talked lately."""
    if not detected():
        return {"detected": False, "model": board_model(), "ports": [],
                "serial_ports": serial_ports(),
                "rs485": {"device": "", "present": False, "state": "absent",
                          "ok_age_s": None, "error": "",
                          "window_s": RS485_LIVE_S}}
    activity = app_activity()
    return {"detected": True, "model": board_model(),
            "ports": ports(slug, activity), "serial_ports": serial_ports(),
            "rs485": rs485_status(activity)}


def eth_ports():
    """The board's ethernet jacks with their LIVE link state, ETH0 first.

    [{name, up, carrier, speed_mbps, mac, ip}] straight from sysfs plus one
    `ip` call -- the same facts the jack's own LEDs show, which is the point:
    the UI draws the faceplate and an installer compares it to the cabinet.
    A NIC that cannot be read reports null fields rather than "down": an
    unknown link and a dead link are different answers.
    """
    out = []
    for path in sorted(_globmod.glob("/sys/class/net/eth*")):
        name = os.path.basename(path)
        entry = {"name": name, "up": None, "carrier": None,
                 "speed_mbps": None, "mac": None, "ip": None}
        try:
            with open(os.path.join(path, "operstate"), encoding="utf-8") as f:
                entry["up"] = f.read().strip() == "up"
        except OSError:
            pass
        try:
            with open(os.path.join(path, "carrier"), encoding="utf-8") as f:
                entry["carrier"] = f.read().strip() == "1"
        except OSError:
            pass                        # reads EINVAL while the link is down
        try:
            with open(os.path.join(path, "speed"), encoding="utf-8") as f:
                speed = int(f.read().strip())
                # -1 is the kernel's "no link"; it is not a speed.
                entry["speed_mbps"] = speed if speed > 0 else None
        except (OSError, ValueError):
            pass
        try:
            with open(os.path.join(path, "address"), encoding="utf-8") as f:
                entry["mac"] = f.read().strip()
        except OSError:
            pass
        # The address the dashboard is actually reached on, when this jack
        # carries one. `ip -o` is one line per address, stable to parse.
        text = _run(["ip", "-o", "-4", "addr", "show", "dev", name])
        m = re.search(r"inet\s+([0-9.]+)/", text or "")
        if m:
            entry["ip"] = m.group(1)
        out.append(entry)
    return out


def read_port(port_id):
    """A digital input's level as 0/1, or None if it cannot be read.

    None is NOT False: an unreadable input reported as "clear" is exactly the
    wrong default for a safety contact, so the caller has to see the
    difference.
    """
    port = PORT_BY_ID.get(str(port_id or "").upper())
    if not port or not detected():
        return None

    # The pad read first (see pin_states): it claims nothing, so it also
    # answers for an input a detection app is holding, where gpioget fails.
    state = pin_states().get(port["pin"]) or {}
    if state.get("level") in (0, 1):
        return state["level"]

    # `gpioget`, NOT gpiozero. gpiozero's lgpio backend does not give the line
    # back on close(): the bias resets but `consumer="lg"` stays, so the line
    # is claimed for the life of the process. This function is called for
    # EVERY input by /api/board/io, which the workflow's Ports panel polls
    # once a second -- so a master that had ever served that page owned the
    # user button and DI1 forever, and the front panel's button did nothing
    # on a box where nothing was using it. libgpiod's own tool requests,
    # reads and releases in one shot, which is exactly the verb wanted here.
    out = _run(["gpioget", "-c", "gpiochip%d" % _CHIP, str(port["pin"])],
               timeout=4)
    if "=" in out:
        # `gpioget -c gpiochip0 4` prints  "4"=active  /  "4"=inactive
        state = out.split("=", 1)[1].strip().lower()
        if state.startswith("inactive"):
            return 0
        if state.startswith("active"):
            return 1

    # No libgpiod tools: fall back to the leaking read rather than reporting
    # an input this box can physically answer as unknown -- and mark the pin
    # as ours, because from here on it really is.
    try:
        from gpiozero import DigitalInputDevice             # noqa: PLC0415
    except ImportError:
        return None
    try:
        device = DigitalInputDevice(port["pin"])
        _CLAIMED.add(port["pin"])       # ours from here on -- see _CLAIMED
        try:
            return int(bool(device.value))
        finally:
            device.close()
    except Exception:                                       # noqa: BLE001
        return None


def pulse_port(port_id, ms=500, percent=None):
    """Drive a port briefly, so an installer can prove the wiring.

    Deliberately a PULSE and nothing else: this endpoint exists to make a lamp
    blink or a relay click while someone is standing at the cabinet, not to
    become a second way of controlling the plant behind the workflow's back.
    The pulse is capped at five seconds for the same reason.

    Returns (ok, message).
    """
    port = PORT_BY_ID.get(str(port_id or "").upper())
    if not port:
        return False, f"unknown port {port_id!r}"
    if port["kind"] == "input":
        return False, f"{port['id']} is an input - it cannot be driven"
    if not detected():
        return False, "no IRIV board detected on this box"
    ms = max(50, min(5000, int(ms or 500)))
    try:
        import time                                         # noqa: PLC0415

        if percent is not None and port["kind"] == "pwm":
            from gpiozero import PWMOutputDevice            # noqa: PLC0415

            device = PWMOutputDevice(port["pin"], frequency=1000)
            device.value = max(0.0, min(100.0, float(percent))) / 100.0
        else:
            from gpiozero import DigitalOutputDevice        # noqa: PLC0415

            device = DigitalOutputDevice(port["pin"], initial_value=True)
        _CLAIMED.add(port["pin"])       # ours from here on -- see _CLAIMED
        time.sleep(ms / 1000.0)
        device.off()
        device.close()
        return True, f"{port['id']} pulsed for {ms} ms"
    except ImportError:
        return False, "gpiozero not installed on this box"
    except Exception as e:                                  # noqa: BLE001
        return False, f"{e} (the pin may be held by another program)"
