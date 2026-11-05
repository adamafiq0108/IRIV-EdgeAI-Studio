# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
buses.py -- the wires around the camera: I2C, GPIO, serial, 1-Wire, Modbus.

An edge AI box is rarely only a camera. It reads a sensor over I2C, flips a
relay on GPIO, talks to a PLC over Modbus, and someone has to find out which
bus the thing is actually on. Node-RED gets used for exactly this, then becomes
a second runtime nobody can version -- so the primitives live here, in the app,
under the same config file and the same review as everything else.

Every probe degrades: a missing tool, a missing kernel module or a missing
Python package returns an empty/typed answer, never a traceback. That is what
lets the same code run on a laptop while you write it and on the device when it
matters.

Optional packages, imported only when used:
    smbus2         I2C read/write (scanning needs no package)
    pyserial       serial ports
    pymodbus       Modbus RTU/TCP
    gpiozero/lgpio GPIO on Raspberry Pi
"""

import glob
import json
import logging
import os
import re
import subprocess
import threading
import time

logger = logging.getLogger(__name__)


def _run(argv, timeout=8):
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# -- I2C ---------------------------------------------------------------------

def i2c_buses():
    """Bus numbers present on this host, e.g. [1, 13, 14] on a Pi 5."""
    out = []
    for path in sorted(glob.glob("/dev/i2c-*")):
        try:
            out.append(int(path.rsplit("-", 1)[1]))
        except ValueError:
            continue
    return out


def i2c_scan(bus=1):
    """Addresses answering on a bus: [{addr: 0x48, hex: "0x48"}].

    Tries `i2cdetect` first (present on every Pi image, needs no root beyond
    the i2c group), then smbus2 quick-writes. Addresses 0x00-0x07 and
    0x78-0x7f are reserved and never probed -- a quick-write there can wedge
    some devices.
    """
    found = []
    text = _run(["i2cdetect", "-y", str(bus)])
    if text:
        for line in text.splitlines()[1:]:
            row = line.split(":", 1)
            if len(row) != 2:
                continue
            base = int(row[0].strip(), 16)
            for i, cell in enumerate(row[1].split()):
                if re.fullmatch(r"[0-9a-fA-F]{2}", cell):
                    found.append(base + i)
        return [{"addr": a, "hex": hex(a)} for a in sorted(set(found))]

    try:
        from smbus2 import SMBus, i2c_msg                    # noqa: PLC0415
    except ImportError:
        return []
    try:
        with SMBus(bus) as smbus:
            for addr in range(0x08, 0x78):
                try:
                    smbus.i2c_rdwr(i2c_msg.write(addr, []))
                    found.append(addr)
                except OSError:
                    continue
    except (OSError, PermissionError) as e:
        logger.debug("[BUS] i2c scan on %s failed: %s", bus, e)
    return [{"addr": a, "hex": hex(a)} for a in sorted(set(found))]


def i2c_read(bus, addr, register, length=1):
    """Read `length` bytes. Returns bytes, or None if the bus is unusable."""
    try:
        from smbus2 import SMBus                             # noqa: PLC0415
    except ImportError:
        return None
    try:
        with SMBus(bus) as smbus:
            return bytes(smbus.read_i2c_block_data(addr, register, length))
    except OSError as e:
        logger.debug("[BUS] i2c read %s/%s failed: %s", bus, hex(addr), e)
        return None


def i2c_write(bus, addr, register, data):
    try:
        from smbus2 import SMBus                             # noqa: PLC0415
    except ImportError:
        return False
    try:
        with SMBus(bus) as smbus:
            smbus.write_i2c_block_data(addr, register, list(data))
        return True
    except OSError:
        return False


# -- 1-Wire, serial, GPIO ----------------------------------------------------

def onewire_devices():
    """1-Wire sensor ids (DS18B20 and friends), plus a temperature if present."""
    out = []
    for path in glob.glob("/sys/bus/w1/devices/*/w1_slave"):
        device_id = os.path.basename(os.path.dirname(path))
        entry = {"id": device_id}
        try:
            with open(path, "r", encoding="utf-8") as f:
                match = re.search(r"t=(-?\d+)", f.read())
            if match:
                entry["temperature_c"] = int(match.group(1)) / 1000.0
        except OSError:
            pass
        out.append(entry)
    return out


def serial_ports():
    """[{device, description, hwid}] -- empty list without pyserial."""
    try:
        from serial.tools import list_ports                  # noqa: PLC0415
    except ImportError:
        return [{"device": p} for p in sorted(glob.glob("/dev/ttyUSB*")
                                              + glob.glob("/dev/ttyACM*")
                                              + glob.glob("/dev/ttyAMA*"))]
    return [{"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in list_ports.comports()]


def gpio_set(pin, value):
    """Drive an output pin. Returns (ok, message).

    gpiozero holds the pin only while the object lives, so this is for discrete
    actions (a relay pulse, an enable line). A pin that must stay asserted
    belongs to a long-lived object owned by the app, not to this helper.
    """
    try:
        from gpiozero import DigitalOutputDevice             # noqa: PLC0415
    except ImportError:
        return False, "gpiozero not installed"
    try:
        device = DigitalOutputDevice(int(pin), initial_value=bool(value))
        device.value = bool(value)
        return True, f"gpio{pin}={int(bool(value))}"
    except Exception as e:                                   # noqa: BLE001
        return False, str(e)


#: Pins this process has read. lgpio reports every consumer as "lg" -- ours
#: and everyone else's -- and its chip handle stays open for the life of the
#: process, so a line we merely READ goes on looking held by a stranger.
#: Remembering our own is what keeps "busy" meaning "someone else has it".
_read_pins = set()


def gpio_get(pin):
    try:
        from gpiozero import DigitalInputDevice              # noqa: PLC0415
    except ImportError:
        return None
    try:
        device = DigitalInputDevice(int(pin))
        _read_pins.add(int(pin))
        try:
            return bool(device.value)
        finally:
            device.close()
    except Exception:                                        # noqa: BLE001
        return None


# -- held pins ---------------------------------------------------------------
#
# gpio_set above is deliberately momentary: its device is garbage-collected and
# the line is released, which drops the output. A relay told to CLOSE has to
# stay closed, and a PWM channel has to keep running between frames, so those
# own long-lived devices cached by pin. Both caches are keyed by pin, so the
# same pin is never claimed twice -- claiming it twice is what raises "GPIO
# busy" from lgpio.

_held = {}                  # pin -> DigitalOutputDevice
_pwm = {}                   # pin -> (PWMOutputDevice, frequency)


def gpio_hold(pin, value, active_high=True):
    """Set an output and KEEP it there. Returns (ok, message).

    The device stays alive in `_held` for the life of the process, which is
    what "latched on" means at the hardware level.
    """
    pin = int(pin)
    try:
        from gpiozero import DigitalOutputDevice             # noqa: PLC0415
    except ImportError:
        return False, "gpiozero not installed"
    try:
        device = _held.get(pin)
        if device is None:
            pwm_release(pin)                # a pin cannot be both at once
            device = DigitalOutputDevice(pin, active_high=bool(active_high),
                                         initial_value=bool(value))
            _held[pin] = device
        device.value = bool(value)
        return True, f"gpio{pin}={int(bool(value))}"
    except Exception as e:                                   # noqa: BLE001
        # "GPIO busy" is the common one and it has a specific cause worth
        # naming: another process (Node-RED, a shell script) already owns the
        # line. Two owners of one relay is not something to paper over.
        return False, f"{e} (pin {pin} may be held by another program)"


def held_pins():
    """Every GPIO pin THIS process has a claim on: driven, dimmed, or read.

    libgpiod reports every lgpio consumer as "lg" -- ours and everybody
    else's -- and the chip handle stays open for the life of the process, so
    a line this app touched is indistinguishable from one the factory
    Node-RED flow owns. That is a question only this process can answer, and
    the answer travels in the app's state file so the hub (a DIFFERENT
    process, with no view of these sets) can attribute a claim instead of
    blaming a stranger.
    """
    return set(_held) | set(_pwm) | set(_read_pins)


def gpio_hold_get(pin):
    """The value this process is currently holding a pin at, or None."""
    device = _held.get(int(pin))
    return None if device is None else bool(device.value)


def gpio_release(pin):
    device = _held.pop(int(pin), None)
    if device is not None:
        try:
            device.close()
        except Exception:                                    # noqa: BLE001
            pass


def pwm_set(pin, percent, frequency=1000):
    """Drive a pin at `percent` duty (0..100). Returns (ok, message).

    Software PWM through gpiozero, so any pin works the same way (the IRIV
    L0/L1 lines, GPIO12/13, could take hardware PWM, but nothing here needs
    it). Software PWM is fine for a lamp or an analogue-ish drive and wrong
    for a servo, which is why the frequency is settable and the default is a
    lamp-friendly 1 kHz.
    """
    pin = int(pin)
    try:
        level = max(0.0, min(100.0, float(percent))) / 100.0
    except (TypeError, ValueError):
        return False, "percent must be a number 0..100"
    try:
        from gpiozero import PWMOutputDevice                 # noqa: PLC0415
    except ImportError:
        return False, "gpiozero not installed"
    try:
        device, freq = _pwm.get(pin, (None, None))
        if device is None or freq != frequency:
            pwm_release(pin)
            gpio_release(pin)               # a pin cannot be both at once
            device = PWMOutputDevice(pin, frequency=int(frequency))
            _pwm[pin] = (device, frequency)
        device.value = level
        _note_pwm(pin, level * 100)
        return True, f"gpio{pin}={level * 100:.0f}%"
    except Exception as e:                                   # noqa: BLE001
        return False, f"{e} (pin {pin} may be held by another program)"


def pwm_get(pin):
    """The duty percent this process is holding a pin at, or None."""
    device, _ = _pwm.get(int(pin), (None, None))
    return None if device is None else round(float(device.value) * 100, 1)


def pwm_release(pin):
    device, _ = _pwm.pop(int(pin), (None, None))
    _note_pwm(pin, None)
    if device is not None:
        try:
            device.close()
        except Exception:                                    # noqa: BLE001
            pass


def gpio_lines():
    """{pin: {"name", "direction", "consumer"}} for the main GPIO bank.

    Read from `gpioinfo` (libgpiod, on every Pi OS image). The useful part is
    `consumer`: a line another program already owns cannot be driven from
    here, and saying WHO owns it turns a mystifying "GPIO busy" into an
    answerable question. Empty dict when the tool is absent -- a laptop simply
    has no lines to report.
    """
    text = _run(["gpioinfo", "gpiochip0"])
    if not text:
        text = _run(["gpioinfo"])
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


# -- Modbus ------------------------------------------------------------------

def modbus_client(config):
    """Open an RTU or TCP client from config. Caller closes it.

        [modbus]
        mode = "tcp"          # tcp | rtu
        host = "192.0.2.50"   # tcp
        port = 502            # tcp: 502, rtu: the serial device's baud
        device = "/dev/ttyUSB0"   # rtu
        baudrate = 9600
        unit = 1

    A Modbus Server connection (client_config below) adds bytesize, parity and
    stopbits: 9600 8N1 is common and wrong on plenty of drives, and a framing
    mismatch is silence on the wire rather than an error. Absent, they stay
    pymodbus's own defaults, so the older callers behave exactly as before.
    """
    mode = (config or {}).get("mode", "tcp")
    try:
        if mode == "rtu":
            from pymodbus.client import ModbusSerialClient   # noqa: PLC0415

            extra = {k: config[k] for k in ("bytesize", "parity", "stopbits")
                     if k in config}
            return ModbusSerialClient(port=config.get("device", "/dev/ttyUSB0"),
                                      baudrate=int(config.get("baudrate", 9600)),
                                      timeout=float(config.get("timeout_s", 2)),
                                      **extra)
        from pymodbus.client import ModbusTcpClient          # noqa: PLC0415

        return ModbusTcpClient(config.get("host", "127.0.0.1"),
                               port=int(config.get("port", 502)),
                               timeout=float(config.get("timeout_s", 2)))
    except ImportError:
        return None


# The named connections ("Modbus Server" in the Workflow, Node-RED's
# modbus-client). The core validates and stores them as [[modbus_clients]];
# run.py hands the list over on every config load, so a Read and a Write that
# pick the same server read ONE definition of it, live -- changing a PLC's
# address never needs a node re-saved.
_clients = {}
_clients_lock = threading.Lock()


def set_clients(rows):
    """Replace the connection table from config ([[modbus_clients]])."""
    table = {}
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip():
            table[str(row["id"]).strip()] = dict(row)
    with _clients_lock:
        _clients.clear()
        _clients.update(table)


def client(client_id):
    """One connection by id, or None when it does not exist (any more)."""
    with _clients_lock:
        row = _clients.get(str(client_id or "").strip())
        return dict(row) if row else None


def client_config(conn, unit=None):
    """A connection as the config modbus_client/modbus_read already take.

    `unit` is the node's own Unit-Id; blank falls back to the connection's,
    which is Node-RED's rule too -- several devices behind one gateway is one
    server and a Unit-Id per node.
    """
    conn = conn or {}
    try:
        unit = int(unit) if unit not in (None, "") else int(conn.get("unit_id", 1))
    except (TypeError, ValueError):
        unit = 1
    timeout_s = max(0.1, float(conn.get("timeout_ms") or 1000) / 1000.0)
    # What the box writes each time it connects (the Drive watchdog switch);
    # absent when the switch was never set, so such a connection's config is
    # exactly what it was.
    extra = {}
    setup = watchdog_writes(conn)
    if setup:
        extra = {"conn_id": str(conn.get("id") or ""), "on_connect": setup}
    if conn.get("type") == "serial":
        parity = {"none": "N", "even": "E", "odd": "O"}.get(
            str(conn.get("parity") or "none").lower(), "N")
        return {"mode": "rtu", "device": conn.get("serial_port") or "/dev/ttyACM0",
                "baudrate": int(conn.get("baudrate") or 9600),
                "bytesize": int(conn.get("databits") or 8),
                "parity": parity,
                "stopbits": int(conn.get("stopbits") or 1),
                "timeout_s": timeout_s, "unit": unit, **extra}
    return {"mode": "tcp", "host": conn.get("host") or "127.0.0.1",
            "port": int(conn.get("port") or 502),
            "timeout_s": timeout_s, "unit": unit, **extra}


# -- the Drive watchdog switch (Tok Aba 2026-10-07) ----------------------------
#
# Cytron's motor drivers (box .49, 192.168.4.4) have a Modbus comms watchdog:
# holding register 105 WDT_MS, in 100 ms units, 0 = off, taking effect
# immediately; when no valid request arrives in that time the drive resets
# MTR_PWM to 0. Register 100 CFG_SAVE (write 1) keeps the motor settings in
# flash. A tcpdump proved one FC 6 write of 1000 reached the drive and was
# accepted -- the watchdog then stopped the motor. The connection's switch
# decides what the box does:
#
#   not set  touch nothing (every connection saved before the switch);
#   off      on each connect: WDT = 0 -- one write of 1000 runs the motor
#            until something writes 0;
#   on       on each connect: WDT = timeout / 100, and every Modbus Write on
#            the connection keeps sending its last value at half the timeout
#            (outputs._repeat_every) -- if the box or the cable fails, the
#            motor stops by itself.
#
# Only WDT is written on connect. The box writes it again on EVERY connect,
# so the drive's flash is not needed for the box's own use, and a CFG_SAVE on
# every reconnect of a flaky link wears it out. "Also save to the drive's
# memory" (cfg_save, off by default) is for a drive that must keep the
# setting after its own power cycle without the box: CFG_SAVE = 1 is then
# written only when the WDT value differs from the last one SAVED for that
# connection and register, remembered in the app's data dir
# (modbus_saved.json) -- one flash write per change, not per connect.
#
# "Each connect" is each time the pooled TCP socket opens or re-opens, and for
# RTU (no connection to lose) before the first transaction on the port. The
# writes go first, on the connection's own Unit-Id, inside the transaction
# that opened the socket and so under its bus lock: no user write can get in
# front of them. A failure is logged once, kept for the nodes' status
# (connect_status), and never retried in a loop: a device that refuses the
# register is not asked again until the next connect; one that does not
# answer fails the transaction it was opened for, like any timeout.
WATCHDOG_MODES = ("off", "on")
WDT_ADDRESS = 105
CFG_SAVE_ADDRESS = 100
#: An RTU port whose setup failed for want of an answer tries again no
#: sooner than this, so a drive switched off costs one timeout per this
#: many seconds and not one on every poll.
RTU_RETRY_S = 10.0
SAVED_FILE = "modbus_saved.json"

_conn_status = {}       # connection id -> {"ok", "message", "at"}
_rtu_inited = {}        # serial device -> {setups already written}
_rtu_failed = {}        # (serial device, setup) -> when it last failed
_saved = {}             # "conn|bus|unit|register" -> WDT value last saved
_saved_dir = {"path": ""}
_init_guard = threading.Lock()


def set_state_dir(path):
    """Where the last SAVED watchdog values live (the app's data dir), so a
    restart does not write the drive's flash again. Unset (tests, a loose
    script) they are remembered for the life of the process only."""
    _saved_dir["path"] = str(path or "")
    _saved.clear()
    if not _saved_dir["path"]:
        return
    # Overwrite the previous run's activity file with what THIS process has
    # (nothing, at start): a duty left from before a restart would light an
    # L terminal no rule is driving.
    _flush_activity(("init", time.time()))
    try:
        with open(os.path.join(_saved_dir["path"], SAVED_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _saved.update({str(k): int(v) for k, v in data.items()})
    except (OSError, ValueError, TypeError):
        pass


# -- what the wires did, for the hub's terminal diagram -----------------------
#
# The hub lights the board's RS485 and L0/L1 terminals from facts only THIS
# process has: whether a Modbus RTU device answered (nothing on a GPIO says
# so), and the duty a software-PWM pin is running at (a pin sampled once a
# second reads high or low by chance). They go to a small file beside the
# watchdog memory -- the app's data dir -- which core/services/board_io.py
# reads back for every RUNNING app. Throttled to one write a second, except
# that a change of outcome (answer -> no answer, a new duty) is written at
# once so the light does not lag the event it reports.
ACTIVITY_FILE = "bus_activity.json"
ACTIVITY_WRITE_S = 1.0
_activity = {"rs485": {}, "pwm": {}}
_activity_mark = {"at": 0.0, "sig": None}


def _note_rtu(config, ok, message=""):
    """Record one Modbus RTU exchange's outcome. TCP is not the RS485 port."""
    config = config or {}
    if config.get("mode") != "rtu" or not config.get("device"):
        return
    with _init_guard:
        row = _activity["rs485"].setdefault(str(config["device"]), {})
        if ok:
            row["ok_at"] = time.time()
        else:
            row["err_at"] = time.time()
            row["error"] = str(message or "no answer")[:200]
        sig = ("rtu", str(config["device"]), bool(ok))
    _flush_activity(sig)


def _note_pwm(pin, duty):
    with _init_guard:
        if duty is None:
            if _activity["pwm"].pop(str(int(pin)), None) is None:
                return
        else:
            _activity["pwm"][str(int(pin))] = round(float(duty), 1)
        sig = ("pwm", tuple(sorted(_activity["pwm"].items())))
    _flush_activity(sig)


def _flush_activity(sig):
    if not _saved_dir["path"]:
        return
    now = time.time()
    with _init_guard:
        # A PWM rule re-sets the same duty every frame: unchanged, nothing to
        # write. An RTU success re-stamps ok_at, so it is written once a second.
        if sig == _activity_mark["sig"] and (
                sig[0] == "pwm" or now - _activity_mark["at"] < ACTIVITY_WRITE_S):
            return
        _activity_mark.update(at=now, sig=sig)
        snapshot = json.dumps(_activity, sort_keys=True)
    path = os.path.join(_saved_dir["path"], ACTIVITY_FILE)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(snapshot)
        os.replace(tmp, path)
    except OSError:
        pass                    # a light that lags is not worth a log line


def _remember_saved(key, value):
    with _init_guard:
        _saved[key] = int(value)
        snapshot = dict(_saved)
    if not _saved_dir["path"]:
        return
    path = os.path.join(_saved_dir["path"], SAVED_FILE)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, sort_keys=True)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("[BUS] could not remember the saved watchdog value: %s", e)


def _reg(value, fallback):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return n if 0 <= n <= 65535 else fallback


def watchdog_mode(conn):
    """"on", "off" or "" (not managed) for a connection."""
    mode = str((conn or {}).get("watchdog") or "").strip().lower()
    return mode if mode in WATCHDOG_MODES else ""


def watchdog_ms(conn):
    """The watchdog timeout the switch asks for, 100..10000 ms in 100 ms."""
    try:
        ms = int(round(float((conn or {}).get("watchdog_ms") or 1000) / 100.0)) * 100
    except (TypeError, ValueError):
        ms = 1000
    return max(100, min(10000, ms))


def watchdog_writes(conn):
    """The connect-time setup: ((wdt address, value, unit), save) where save
    is the CFG_SAVE address when "Also save to the drive's memory" is ticked,
    else None; () when the switch is not set."""
    mode = watchdog_mode(conn)
    if not mode:
        return ()
    conn = conn or {}
    try:
        unit = int(conn.get("unit_id", 1))
    except (TypeError, ValueError):
        unit = 1
    wdt = _reg(conn.get("wdt_address"), WDT_ADDRESS)
    save = _reg(conn.get("cfg_save_address"), CFG_SAVE_ADDRESS)
    units = watchdog_ms(conn) // 100 if mode == "on" else 0
    return ((wdt, units, unit), save if conn.get("cfg_save") else None)


def watchdog_repeat_s(conn):
    """Seconds between keep-sending writes the switch imposes on every Modbus
    Write on this connection (half the timeout), or 0.0 when it does not."""
    if watchdog_mode(conn) != "on":
        return 0.0
    return watchdog_ms(conn) / 2000.0


def connect_status(conn_id):
    """{"ok", "message", "at"} of the last connect-time setup on this
    connection, or None when it has none (switch not set, not connected yet)."""
    with _init_guard:
        st = _conn_status.get(str(conn_id or ""))
        return dict(st) if st else None


def _set_status(config, ok, message):
    cid = str(config.get("conn_id") or bus_key(config))
    with _init_guard:
        before = _conn_status.get(cid)
        _conn_status[cid] = {"ok": ok, "message": message, "at": time.time()}
    # Logged once per change, not once per connect.
    if not ok and (before is None or before.get("message") != message):
        logger.warning("[BUS] drive watchdog setup on %s failed: %s",
                       bus_key(config), message)
    elif ok and before is not None and not before.get("ok"):
        logger.info("[BUS] drive watchdog setup on %s done", bus_key(config))


def _write_one(client, address, value, unit):
    """(ok, lost, why) for one setup register write."""
    try:
        result = client.write_register(int(address), int(value) & 0xFFFF,
                                       slave=int(unit))
    except Exception as e:                                   # noqa: BLE001
        return False, True, str(e) or e.__class__.__name__
    if result.isError():
        return False, _lost(client, result), str(result)
    return True, False, ""


def _open(client, config, entry):
    """connect(), then the connection's connect-time setup when this is a
    fresh connection (TCP) or the port's first transaction (RTU).

    Returns (ok, message); not ok means the transaction must fail and the
    socket be dropped (no connection, or the device did not answer the
    setup). A device that answered with an error does NOT fail the
    transaction -- the user's write still goes -- it is only reported.
    """
    was_up = bool(getattr(client, "connected", False)) if entry else False
    if not client.connect():
        return False, "cannot reach the Modbus device"
    setup = tuple(config.get("on_connect") or ())
    if not setup:
        return True, ""
    (wdt, value, unit), save = setup
    with _init_guard:
        if entry is not None:
            if not was_up:
                entry["inited"] = set()
            done = entry.setdefault("inited", set())
        else:
            done = _rtu_inited.setdefault(bus_key(config), set())
            failed = _rtu_failed.get((bus_key(config), setup))
            if setup not in done and failed is not None \
                    and time.time() - failed < RTU_RETRY_S:
                return True, ""
        if setup in done:
            return True, ""
    steps = [(wdt, value)]
    saved_key = f"{config.get('conn_id')}|{bus_key(config)}|{unit}|{wdt}"
    if save is not None:
        with _init_guard:
            if _saved.get(saved_key) != value:
                steps.append((save, 1))
    for address, v in steps:
        ok, lost, why = _write_one(client, address, v, unit)
        if not ok:
            message = f"writing {v} to {address} (unit {unit}) failed: {why}"
            _set_status(config, False, message)
            with _init_guard:
                if lost:
                    if entry is None:
                        _rtu_failed[(bus_key(config), setup)] = time.time()
                else:
                    done.add(setup)     # asked again at the next connect only
            if lost:
                return False, f"drive watchdog setup: {message}"
            return True, ""
        if address == save and (address, v) != (wdt, value):
            _remember_saved(saved_key, value)
    with _init_guard:
        done.add(setup)
        if entry is None:
            _rtu_failed.pop((bus_key(config), setup), None)
    _set_status(config, True, "set on connect")
    return True, ""


def bus_key(config):
    """What one transaction must hold exclusively: the serial DEVICE (one
    wire, one frame at a time, whoever is asking) or the TCP host:port (a
    small PLC answers one request at a time, and interleaving two clients'
    connects on it is how requests get dropped)."""
    config = config or {}
    if config.get("mode") == "rtu":
        return str(config.get("device") or "")
    return f"tcp://{config.get('host', '127.0.0.1')}:{config.get('port', 502)}"


# ONE lock per bus for the whole process. The Modbus Read poller
# (functions.py) and the Modbus Write worker (outputs.py) both take it from
# here, so a read and a write on /dev/ttyAMA10 can never interleave their
# frames -- two masters on one RS-485 pair corrupt each other at the
# electrical level, and two private lock tables would each be happy while the
# wire was not.
_bus_locks = {}
_bus_locks_guard = threading.Lock()
#: Last read error per bus key, so the WARNING below fires once per change
#: rather than once per poll.
_LAST_READ_ERROR = {}


class FairLock:
    """A lock handed out in arrival order (a ticket lock).

    threading.Lock is not fair: a thread that releases it and asks again
    straight away usually wins against one that has been waiting. The Modbus
    Write worker does exactly that when a node keeps sending every 0.1 s to
    a slow device -- its next repeat is already queued when the last one
    finishes -- and a Modbus Read on the same server could then wait for as
    long as the repeats go on. With tickets the waiting read is next, always:
    a read waits at most for the ONE transaction in front of it.
    """

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._next = 0          # the next ticket to hand out
        self._serving = 0       # the ticket allowed in

    def acquire(self):
        with self._cond:
            ticket = self._next
            self._next += 1
            while ticket != self._serving:
                self._cond.wait()
        return True

    def release(self):
        with self._cond:
            self._serving += 1
            self._cond.notify_all()

    def locked(self):
        with self._cond:
            return self._next != self._serving

    __enter__ = acquire

    def __exit__(self, *exc):
        self.release()
        return False


def bus_lock(key):
    with _bus_locks_guard:
        lock = _bus_locks.get(key)
        if lock is None:
            lock = _bus_locks[key] = FairLock()
        return lock


# ONE TCP connection per Modbus server, held open across reads and writes,
# the way Node-RED's modbus-client holds its socket. Until 2026-10-07 every
# read and every write opened its own connection and closed it a few
# milliseconds later. A motor driver (iSmartDrive on 192.168.4.4) takes the
# master's socket going away as "communication lost" and stops after its
# watchdog, so an Inject's 1000 ran the motor "for a while only" while the
# same write from Node-RED kept it running until the next write -- and a
# small device that serves one connection at a time refuses the next connect
# while the closed one is still in TIME_WAIT, which is the "no answer from
# unit 1" the Read poller logged beside it.
#
# The entry is keyed by bus_key (host:port) and rebuilt when the connection's
# timeout changes. A failed transaction closes the socket so the NEXT call
# reconnects (pymodbus's connect() is a no-op while the socket is up); the
# callers already retry (the write worker once after reconnect_timeout_ms,
# the reader on its next poll). Callers hold bus_lock(key) around the
# transaction, so one socket is never used by two threads at once.
#
# Serial is NOT pooled: an RTU device has no connection to lose, and a port
# held open between polls would lock every other reader of /dev/ttyACM0 (the
# master's RS485 probe, a customer's script) out of the device node.
_pool = {}
_pool_guard = threading.Lock()


def _pool_sig(config):
    return (str(config.get("host", "127.0.0.1")), int(config.get("port", 502)),
            float(config.get("timeout_s", 2)))


def _lease(config):
    """(client, pooled entry) -- the server's open connection and its pool
    entry, or a fresh client (entry None) the caller closes itself when the
    bus is not pooled."""
    config = config or {}
    if config.get("mode") == "rtu":
        return modbus_client(config), None
    key, sig = bus_key(config), _pool_sig(config)
    with _pool_guard:
        entry = _pool.get(key)
        if entry is not None and entry["sig"] != sig:
            _pool.pop(key)
            try:
                entry["client"].close()
            except Exception:                                # noqa: BLE001
                pass
            entry = None
        if entry is None:
            client = modbus_client(config)
            if client is None:
                return None, None
            entry = _pool[key] = {"client": client, "sig": sig}
        return entry["client"], entry


def _release(client, pooled, broken=False):
    """After a transaction: a pooled socket stays up unless it just failed
    (closed, so the next lease reconnects); a serial client always closes."""
    if client is None:
        return
    if broken or pooled is None:
        try:
            client.close()
        except Exception:                                    # noqa: BLE001
            pass


def _lost(client, result):
    """True when an error result means the LINK failed (a timeout, a dropped
    socket: pymodbus answers a ModbusIOException and may already have closed
    the socket), not the device answering "illegal address" (an
    ExceptionResponse, on a connection that is fine)."""
    return (not getattr(client, "connected", True)
            or type(result).__name__ == "ModbusIOException")


def pooled_connections():
    """The servers with a socket held open right now (tests, diagnostics)."""
    with _pool_guard:
        return sorted(_pool)


def close_pool():
    with _pool_guard:
        entries = list(_pool.values())
        _pool.clear()
    for entry in entries:
        try:
            entry["client"].close()
        except Exception:                                    # noqa: BLE001
            pass


def modbus_read(config, address, count=1, kind="holding"):
    """Read registers/coils. Returns a list, or None when unavailable.

    `address` is the raw protocol address -- vendor documentation that says
    40001 usually means holding register 0. Getting that wrong reads a valid
    but meaningless register, which is worse than an error.
    """
    client, pooled = _lease(config)
    if client is None:
        return None
    unit = int((config or {}).get("unit", 1))
    broken = False
    try:
        opened, why = _open(client, config or {}, pooled)
        if not opened:
            broken = True
            _note_rtu(config, False, why)
            return None
        # `count` is keyword-only from pymodbus 3.7 (the boards run 3.8.6);
        # passing it by position raised a TypeError before any byte reached
        # the wire, which the poller then reported as "no answer from unit".
        if kind == "coil":
            result = client.read_coils(address, count=count, slave=unit)
        elif kind == "discrete":
            # FC2: read-only bits (a sensor's dry contacts), the input twin
            # of a coil.
            result = client.read_discrete_inputs(address, count=count, slave=unit)
        elif kind == "input":
            result = client.read_input_registers(address, count=count, slave=unit)
        else:
            result = client.read_holding_registers(address, count=count, slave=unit)
        if result.isError():
            broken = _lost(client, result)
            # An exception reply is still a frame from a device: the pair is
            # alive. Only a lost exchange (timeout, nothing back) is an error.
            _note_rtu(config, not broken, str(result))
            return None
        _note_rtu(config, True)
        if kind in ("coil", "discrete"):
            return list(result.bits)[:count]
        return list(result.registers)
    except Exception as e:                                   # noqa: BLE001
        broken = True
        _note_rtu(config, False, str(e))
        # Once per bus per distinct message: a library error is not a quiet
        # bus, and DEBUG hid the real reason for weeks.
        key = bus_key(config)
        if _LAST_READ_ERROR.get(key) != str(e):
            _LAST_READ_ERROR[key] = str(e)
            logger.warning("[BUS] modbus read on %s failed: %s", key, e)
        return None
    finally:
        _release(client, pooled, broken)


def modbus_write(config, address, value, kind="holding"):
    """Write one coil or register. Returns (ok, message)."""
    try:
        values = modbus_values(5 if kind == "coil" else 6, value)
    except ValueError as e:
        return False, str(e)
    return modbus_write_fc(config, 5 if kind == "coil" else 6, address, values)


def modbus_values(fc, value, quantity=1):
    """What one write puts on the wire, from what arrived on the node.

    FC 5/6 write ONE value. FC 15/16 write `quantity` of them: a list (a Python
    node returning one) is written as given, capped at the quantity; a single
    number is repeated -- "switch these eight coils off" is one 0, not eight.
    Coils (5/15) take any truthy value as 1, the way Node-RED does. Registers
    are 16-bit: a negative number goes two's-complement (a PLC reading INT
    sees -5), anything else outside -32768..65535 is REFUSED -- clamping it
    would write a number that is almost right onto somebody's machine.
    Raises ValueError with the reason.
    """
    count = max(1, int(quantity or 1))
    if isinstance(value, (list, tuple)):
        items = list(value)[:1 if fc in (5, 6) else count]
    else:
        items = [value] * (1 if fc in (5, 6) else count)
    if not items:
        raise ValueError("nothing to write - the list is empty")
    out = []
    for v in items:
        if v is None:
            raise ValueError("no value to write yet")
        if fc in (5, 15):
            out.append(1 if bool(v) else 0)
            continue
        try:
            n = int(round(float(v)))
        except (TypeError, ValueError):
            raise ValueError(f"{v!r} is not a number a register can hold")
        if not -32768 <= n <= 65535:
            raise ValueError(f"{n} does not fit a 16-bit register")
        out.append(n & 0xFFFF)
    return out


def modbus_write_fc(config, fc, address, values, unit=None):
    """One Modbus write, FC 5 / 6 / 15 / 16. Returns (ok, message).

    `values` is modbus_values()'s answer. A TCP server's connection is the
    pooled one the reader uses too (see _lease) and stays up after the write;
    a serial client connects and closes per call, as the reader does, so a
    port is never held open between writes.
    """
    client, pooled = _lease(config)
    if client is None:
        return False, "pymodbus not installed"
    if unit is None:
        unit = int((config or {}).get("unit", 1))
    broken = False
    try:
        opened, message = _open(client, config or {}, pooled)
        if not opened:
            broken = True
            _note_rtu(config, False, message)
            return False, message
        if fc == 5:
            result = client.write_coil(address, bool(values[0]), slave=unit)
        elif fc == 6:
            result = client.write_register(address, int(values[0]), slave=unit)
        elif fc == 15:
            result = client.write_coils(address, [bool(v) for v in values],
                                        slave=unit)
        elif fc == 16:
            result = client.write_registers(address, [int(v) for v in values],
                                            slave=unit)
        else:
            return False, f"FC {fc} is not a write"
        if result.isError():
            broken = _lost(client, result)
            _note_rtu(config, not broken, str(result))
            return False, str(result)
        _note_rtu(config, True)
        return True, "ok"
    except Exception as e:                                   # noqa: BLE001
        broken = True
        _note_rtu(config, False, str(e))
        return False, str(e) or e.__class__.__name__
    finally:
        _release(client, pooled, broken)


def inventory(config=None):
    """Everything at once, for a control envelope or a diagnostics page."""
    buses = i2c_buses()
    return {
        "i2c": {"buses": buses,
                "devices": {str(b): i2c_scan(b) for b in buses[:4]}},
        "onewire": onewire_devices(),
        "serial": serial_ports(),
        "modbus": {"configured": bool((config or {}).get("modbus"))},
    }
