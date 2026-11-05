# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
iriv.py -- board support for the IRIV Edge AI industrial controller.

What the board gives a detection app beyond a Pi:

    isolated DI x2      dry-contact inputs up to 50V -- a light curtain, an
                        e-stop loop, a machine's "cycle running" signal
    isolated DO x2      up to 50V -- a tower light, a relay, a horn
    isolated RS485      Modbus RTU to a PLC or a sensor string, 500 kbps max
    RTC                 real timestamps after a boot with no network, which is
                        what makes the contract's offset-aware stamps true
    SSD1315 OLED        0.96" status display: IP, app state, last event
    buzzer, button, LEDs   local alarm and a physical acknowledge
    PCIe AI HAT+        Hailo-8 / 8L / 10H (see app/hailo.py)
    2x GbE, WiFi/BT, mPCIe (4G/LoRa), M.2 NVMe

**Pin numbers are configuration, not constants.** They come from the board
revision's pinout, and a wrong hardcoded pin drives an output nobody expected
-- on an industrial box that is a relay closing on real machinery. Every helper
below returns "not configured" until the map is filled in:

    [iriv]
    enabled = true
    din  = [17, 27]        # BCM numbers from the board pinout
    dout = [22, 23]
    buzzer = 24
    button = 25
    leds = [12, 13]
    oled_bus = 1
    oled_addr = 0x3c

    [iriv.rs485]
    device = "/dev/ttyAMA2"
    baudrate = 9600
    unit = 1

Everything degrades on a laptop: helpers answer False/None, `inventory()` still
returns a dict, and nothing raises.
"""

import logging
import os
import socket

from app import buses

logger = logging.getLogger(__name__)

OLED_DEFAULT_ADDR = 0x3C            # SSD1315, same address family as SSD1306


def _cfg(config):
    return (config or {}).get("iriv") or {}


def enabled(config):
    return bool(_cfg(config).get("enabled"))


# -- the board's named ports -------------------------------------------------
#
# DI0/DI1, DO0/DO1, L0/L1: the labels silkscreened on the IRIV Edge AI, and the
# BCM pin behind each one. These numbers are NOT guessed -- they were read off
# the board's own factory Node-RED flows (`rpi-gpio` nodes named "DI0", "DO1",
# "LED0 Output", ...), which is the vendor's own statement of the pinout.
# Except L0/L1: "LED0 Output" is the board's USER LED (GPIO20/21); the L0/L1
# LED driver terminals are GPIO12/13 per the datasheet (Rev 1.0, 5.1), fixed
# 2026-10-07 after a rule on "L0" was found blinking the status LED instead.
#
# A wrong pin here closes a relay on real machinery, so the map is only applied
# when the box is actually an IRIV (see `detect`); on anything else the ports
# list is empty and every helper answers "no board", rather than driving pin 23
# on a stranger's Raspberry Pi.
#
# The same table exists in core/services/board_io.py for the API side. The app
# imports nothing from core by design, so it is mirrored, not shared -- keep
# the two in step.
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


def _is_iriv_board():
    """Whether this box is an IRIV Edge AI, from evidence rather than a flag.

    Two signals together: the CM5 module (the compute the board is built
    around) and the platform marker the IRIV image ships. Either alone is not
    enough -- a bare CM5 on someone else's carrier must not be handed this
    pinout, and the marker directory could outlive a reflash onto other
    hardware.
    """
    try:
        with open("/proc/device-tree/model", "rb") as f:
            model = f.read().decode("utf-8", "replace").rstrip("\x00").strip()
    except OSError:
        return False, ""
    is_cm5 = "compute module 5" in model.lower()
    marked = os.path.isdir("/etc/iriv")
    return bool(is_cm5 and marked), model


def ports(config=None):
    """[{id, kind, pin, label, busy, consumer}] -- the ports this box has.

    Empty on a box that is not an IRIV, unless [iriv].ports_force is set (a
    developer running the same image on a bench carrier). `busy`/`consumer`
    come from libgpiod: a line another program owns -- the factory Node-RED
    flow is the usual one -- cannot be driven from here, and the operator
    needs to be told that BEFORE they wire a node to it, not after the relay
    fails to move.
    """
    detected, _model = _is_iriv_board()
    if not detected and not _cfg(config).get("ports_force"):
        return []
    lines = buses.gpio_lines()
    out = []
    for port in BOARD_PORTS:
        info = lines.get(port["pin"]) or {}
        consumer = info.get("consumer") or ""
        # A line THIS app holds -- driving it, or having read it -- is not
        # "busy": that is the app doing its job. lgpio calls every consumer
        # "lg", ours included, so without this every port we touch looks
        # taken by a stranger from the moment we touch it.
        ours = (port["pin"] in buses._held or port["pin"] in buses._pwm
                or port["pin"] in buses._read_pins)
        out.append({**port, "consumer": "" if ours else consumer,
                    "busy": bool(consumer) and not ours})
    return out


def held_ports():
    """The silkscreen names of the ports this app currently has a claim on.

    Published in the app's state file so the hub can tell ITS OWN system's
    claim from a stranger's. Without it every port a rule drives reads as
    "held by another program (lg) -- most often the factory Node-RED flow",
    which sent an operator hunting through Node-RED for a pin their own
    workflow was driving.
    """
    pins = buses.held_pins()
    return [p["id"] for p in BOARD_PORTS if p["pin"] in pins]


def port_state(port_id):
    """What a port is doing right now: 0/1 for digital, 0..100 for PWM, or
    None when it cannot be read."""
    port = PORT_BY_ID.get(str(port_id or "").upper())
    if not port:
        return None
    if port["kind"] == "pwm":
        # A PWM port may be held either way: as a duty cycle, or fully on/off
        # by a plain output node wired to it.
        duty = buses.pwm_get(port["pin"])
        if duty is not None:
            return duty
        held = buses.gpio_hold_get(port["pin"])
        return None if held is None else (100 if held else 0)
    if port["kind"] == "input":
        value = buses.gpio_get(port["pin"])
        return None if value is None else int(value)
    held = buses.gpio_hold_get(port["pin"])
    return None if held is None else int(held)


def set_port(port_id, value, active_high=True):
    """Drive DO0/DO1/L0/L1/BUZZER on or off. Returns (ok, message)."""
    port = PORT_BY_ID.get(str(port_id or "").upper())
    if not port:
        return False, f"unknown port {port_id!r}"
    if port["kind"] == "input":
        return False, f"{port['id']} is an input - it cannot be driven"
    return buses.gpio_hold(port["pin"], value, active_high=active_high)


def set_pwm(port_id, percent, frequency=1000):
    """Drive L0/L1 at a duty percentage. Returns (ok, message)."""
    port = PORT_BY_ID.get(str(port_id or "").upper())
    if not port:
        return False, f"unknown port {port_id!r}"
    if port["kind"] != "pwm":
        return False, f"{port['id']} is not a PWM port - use DO0/DO1 for on/off"
    return buses.pwm_set(port["pin"], percent, frequency)


def read_port(port_id):
    """A digital input as 0/1, or None when it cannot be read. None is NOT
    False: an unreadable e-stop input read as "clear" is the dangerous
    default, so callers must distinguish them."""
    port = PORT_BY_ID.get(str(port_id or "").upper())
    if not port or port["kind"] != "input":
        return None
    value = buses.gpio_get(port["pin"])
    return None if value is None else int(value)


# -- isolated digital I/O ----------------------------------------------------

def read_inputs(config):
    """[bool|None] per configured isolated input, in config order.

    None means "could not read" and must not be treated as False -- an
    unreadable e-stop input read as "clear" is the dangerous default.
    """
    return [buses.gpio_get(pin) for pin in _cfg(config).get("din") or []]


def set_output(config, index, value):
    """Drive isolated output `index` (0-based). Returns (ok, message)."""
    pins = _cfg(config).get("dout") or []
    if index >= len(pins):
        return False, f"no isolated output {index}; configure [iriv].dout"
    return buses.gpio_set(pins[index], value)


def set_led(config, index, value):
    leds = _cfg(config).get("leds") or []
    if index >= len(leds):
        return False, f"no LED {index}; configure [iriv].leds"
    return buses.gpio_set(leds[index], value)


def buzz(config, on=True):
    """The onboard buzzer. Local alarms matter: the operator standing at the
    machine is not looking at the dashboard."""
    pin = _cfg(config).get("buzzer")
    if pin is None:
        return False, "no buzzer pin; configure [iriv].buzzer"
    return buses.gpio_set(pin, on)


def button_pressed(config):
    pin = _cfg(config).get("button")
    return None if pin is None else buses.gpio_get(pin)


# -- RS485 / Modbus ----------------------------------------------------------

def rs485_config(config):
    """Modbus config for the isolated RS485 port, in buses.py's shape."""
    rs485 = dict(_cfg(config).get("rs485") or {})
    rs485.setdefault("mode", "rtu")
    rs485.setdefault("device", "/dev/ttyAMA2")
    rs485.setdefault("baudrate", 9600)
    return rs485


def rs485_read(config, address, count=1, kind="holding"):
    return buses.modbus_read(rs485_config(config), address, count, kind)


def rs485_write(config, address, value, kind="holding"):
    return buses.modbus_write(rs485_config(config), address, value, kind)


# -- OLED --------------------------------------------------------------------

def oled_show(config, lines):
    """Write up to four short lines to the 0.96" OLED.

    The display is the only UI on a box in a cabinet: IP address, whether
    detection is running, and the last event beat walking a laptop to the
    machine. Needs `luma.oled`; without it this is a no-op, never an error.
    """
    cfg = _cfg(config)
    try:
        from luma.core.interface.serial import i2c           # noqa: PLC0415
        from luma.oled.device import ssd1306                 # noqa: PLC0415
        from PIL import ImageDraw, ImageFont                 # noqa: PLC0415
    except ImportError:
        return False
    try:
        serial = i2c(port=int(cfg.get("oled_bus", 1)),
                     address=int(cfg.get("oled_addr", OLED_DEFAULT_ADDR)))
        device = ssd1306(serial)                             # SSD1315 is compatible
        font = ImageFont.load_default()
        with __import__("luma.core.render", fromlist=["canvas"]).canvas(device) as draw:
            for i, line in enumerate(list(lines)[:4]):
                draw.text((0, i * 14), str(line)[:21], font=font, fill=255)
        return True
    except Exception as e:                                   # noqa: BLE001
        logger.debug("[IRIV] OLED write failed: %s", e)
        return False


def status_lines(app_state, ip=None):
    """The four lines worth showing, given the app's runtime state."""
    return [
        socket.gethostname()[:21],
        ip or primary_ip() or "no network",
        "detecting" if app_state.get("running") else "STOPPED",
        f"events {app_state.get('events_today', 0)}",
    ]


def primary_ip():
    """The address this host would use to reach the network, without asking
    for one it may not have."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 1))                       # TEST-NET, no traffic
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


# -- identity ----------------------------------------------------------------

def cpu_serial():
    """Board serial from the devicetree, else /proc/cpuinfo.

    Clonable with the disk image -- fleet identity, not proof of hardware.
    """
    for path in ("/sys/firmware/devicetree/base/serial-number", "/proc/cpuinfo"):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        if "cpuinfo" in path:
            for line in text.splitlines():
                if line.lower().startswith("serial"):
                    return line.split(":", 1)[1].strip().lower() or None
        else:
            return text.strip().rstrip("\x00") or None       # devicetree is NUL-terminated
    return None


def inventory(config=None):
    """One dict describing this board, for telemetry and the Monitoring page."""
    cfg = _cfg(config)
    out = {
        "board": "iriv-edge-ai-cm5",
        "configured": bool(cfg.get("enabled")),
        "cpu_serial": cpu_serial(),
        "ip": primary_ip(),
        "inputs": read_inputs(config),
        "buses": buses.inventory(config),
    }
    try:
        from app.hailo import probe                          # noqa: PLC0415

        out["hailo"] = probe()
    except Exception:                                        # noqa: BLE001
        out["hailo"] = {"present": False}
    return out
