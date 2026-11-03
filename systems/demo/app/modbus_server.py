# -*- coding: utf-8 -*-
"""
modbus_server.py -- this box AS a Modbus device, for a PLC to poll.

The other Modbus direction already existed: the workflow's Modbus nodes READ
a PLC's registers (buses.modbus_read). This is the mirror. A PLC or SCADA
head asks us "how many people are in zone 1, is the guard rule tripped", and
we answer on the wire it already speaks -- no HTTP client on the PLC, no
polling script in the middle.

WHY IT LIVES IN THE APP, not the master: the values are the app's. Zone
counts, function metrics and rule states exist in the detection loop's stats
dict and nowhere else at that freshness. A server in the master would be
serving a copy fetched over HTTP, one poll stale, and stale is the one thing
a PLC scan cannot use.

WHAT IT SERVES is not decided here. A `Modbus Register` output node in the
workflow says "this value goes at address N", the same way a Board Output
node says "this rule drives DO0" -- so the register map is designed on the
canvas next to the rule that fills it, and a value's address is visible where
the value is. This module owns the wire, not the meaning.

Registers are 16-bit and the values are not, so every register carries a
SCALE: 2.5 fps at scale 10 is 25, read back as 25 and divided by the PLC.
Silently truncating to 2 would be a number that is almost right, which is
worse on a factory floor than one that is obviously wrong.

Both transports run from one datastore, so a box can answer a SCADA head over
ethernet and a local PLC over RS-485 with the same numbers.
"""

import logging
import threading

logger = logging.getLogger(__name__)

#: Registers per block. 512 is far more than a vision system publishes and
#: still trivial to hold; a PLC reading an address we never filled gets 0
#: rather than an exception, which is what a Modbus master expects.
BLOCK_SIZE = 512

#: What a register may hold. Modbus is unsigned 16-bit; a negative value
#: (a temperature, a delta) is sent two's-complement so a PLC reading it as
#: INT sees the right number.
REG_MIN = -32768
REG_MAX = 65535

#: The box's own "detection active" flag: INPUT register 511 reads 1 while
#: the AI detection runs and 0 while it is switched off (logic mode). A PLC
#: program checks it before trusting any detection-fed register. The last
#: address of the input block, so no register map designed before it existed
#: is likely to sit there; the master refuses a Modbus Register node on it
#: (core/services/detector_control.py MODBUS_STATUS_ADDRESS -- mirrored, the
#: app does not import core/).
STATUS_ADDRESS = BLOCK_SIZE - 1

#: The port the server falls back to when the config names none: what a
#: non-root process can bind, and what the box's nftables rule redirects
#: PLCs' 502 to (scripts/nftables/modbus-redirect.nft).
DEFAULT_PORT = 5020


def encode(value, scale=1):
    """One 16-bit register from a workflow value. Never raises.

    A value that cannot be a number at all (None, a string, a missing key)
    becomes 0 rather than an exception: a Modbus scan that dies because a rule
    has not fired yet is a scan that dies at 3am on the one night it matters.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0
    try:
        num = int(round(num * float(scale or 1)))
    except (TypeError, ValueError, OverflowError):
        return 0
    # Clamped, not wrapped. A count that overflows to 3 is a lie a PLC will
    # act on; a count pinned at 65535 is visibly at the top of its range.
    if num < REG_MIN:
        num = REG_MIN
    elif num > REG_MAX:
        num = REG_MAX
    return num & 0xFFFF


class ModbusServer:
    """A Modbus server over TCP, RTU, or both, backed by one live datastore.

    Started and stopped by the app; `publish` is called from the detection
    loop with the values the workflow's register nodes named. Every failure
    path degrades: no pymodbus, no serial port, a port already in use -- the
    server reports why and the detection loop carries on. A vision system
    must not stop watching because a fieldbus did not come up.
    """

    def __init__(self):
        self.error = ""
        self.running = False
        self._lock = threading.Lock()
        self._blocks = {}               # "hr" | "ir" -> data block
        self._threads = []
        self._servers = []
        self._sig = None                # the config this was started for
        self._live = set()              # (block, address) written last publish

    # -- lifecycle ----------------------------------------------------------

    def start(self, cfg):
        """Start (or restart) for `cfg`. Returns True when serving.

        Restart is by SIGNATURE: a config poll runs every couple of seconds
        and tearing a listener down to build an identical one would drop a
        PLC's connection every time anybody saved anything.
        """
        sig = _signature(cfg)
        if sig == self._sig and self.running:
            return True
        self.stop()
        self._sig = sig
        if not (cfg or {}).get("enabled"):
            return False

        try:
            from pymodbus.datastore import (                 # noqa: PLC0415
                ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext)
        except ImportError:
            # Named exactly, because "modbus not working" with no reason is
            # what the previous optional-import version gave everybody.
            self.error = ("pymodbus is not installed on this box - the Modbus "
                          "server cannot start")
            logger.warning("[MODBUS] %s", self.error)
            return False

        hr = ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE)
        ir = ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE)
        self._blocks = {"hr": hr, "ir": ir}
        self._live = set()
        # di/co are given real blocks too: a PLC configured for coils gets
        # zeros instead of an exception response it has to be taught to
        # ignore.
        slave = ModbusSlaveContext(
            hr=hr, ir=ir,
            di=ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE),
            co=ModbusSequentialDataBlock(0, [0] * BLOCK_SIZE),
            zero_mode=True)
        unit = int(cfg.get("unit_id") or 1)
        context = ModbusServerContext(slaves={unit: slave}, single=False)

        mode = str(cfg.get("mode") or "tcp").lower()
        started, problems = 0, []
        if mode in ("tcp", "both"):
            ok, why = self._start_tcp(context, cfg)
            started += 1 if ok else 0
            if why:
                problems.append(why)
        if mode in ("rtu", "both"):
            ok, why = self._start_rtu(context, cfg)
            started += 1 if ok else 0
            if why:
                problems.append(why)

        self.running = started > 0
        self.error = "; ".join(problems)
        if self.running and problems:
            # Half up is worth saying out loud: somebody watching the TCP side
            # work will not notice the serial side never came up.
            logger.warning("[MODBUS] serving, with problems: %s", self.error)
        return self.running

    def _start_tcp(self, context, cfg):
        try:
            from pymodbus.server import StartTcpServer         # noqa: PLC0415
        except ImportError as exc:                             # noqa: BLE001
            return False, f"Modbus TCP unavailable: {exc}"
        host = str(cfg.get("host") or "0.0.0.0")
        port = int(cfg.get("port") or DEFAULT_PORT)
        # Ports below 1024 need root and this app is not root. Said plainly,
        # because "permission denied" on 502 is the single most likely first
        # experience of this feature and the fix (use 5020) is not obvious.
        def run():
            try:
                StartTcpServer(context=context, address=(host, port))
            except PermissionError:
                self.error = (f"Modbus TCP cannot bind port {port}: ports below "
                              f"1024 need root. Use 5020 and point the PLC at it.")
                logger.warning("[MODBUS] %s", self.error)
            except OSError as exc:
                self.error = f"Modbus TCP cannot bind {host}:{port}: {exc}"
                logger.warning("[MODBUS] %s", self.error)
            except Exception as exc:                           # noqa: BLE001
                self.error = f"Modbus TCP stopped: {exc}"
                logger.warning("[MODBUS] %s", self.error)
        return self._spawn(run, f"tcp {host}:{port}")

    def _start_rtu(self, context, cfg):
        try:
            from pymodbus.server import StartSerialServer      # noqa: PLC0415
        except ImportError as exc:                             # noqa: BLE001
            return False, f"Modbus RTU unavailable: {exc}"
        device = str(cfg.get("device") or "/dev/ttyUSB0")
        baud = int(cfg.get("baudrate") or 9600)

        def run():
            try:
                StartSerialServer(context=context, port=device, baudrate=baud,
                                  parity=str(cfg.get("parity") or "N")[:1].upper(),
                                  stopbits=int(cfg.get("stopbits") or 1),
                                  bytesize=int(cfg.get("bytesize") or 8))
            except Exception as exc:                           # noqa: BLE001
                # The usual causes are a port that is not there and a port
                # something else already owns -- Node-RED, most often, which
                # is why serial ports are arbitrated on the Board I/O page.
                self.error = f"Modbus RTU cannot open {device}: {exc}"
                logger.warning("[MODBUS] %s", self.error)
        return self._spawn(run, f"rtu {device}@{baud}")

    def _spawn(self, run, label):
        try:
            t = threading.Thread(target=run, name=f"modbus-{label}", daemon=True)
            t.start()
            self._threads.append(t)
            logger.info("[MODBUS] serving %s", label)
            return True, ""
        except Exception as exc:                               # noqa: BLE001
            return False, f"Modbus {label} failed to start: {exc}"

    def stop(self):
        """Drop the listeners. Daemon threads, so a stuck server never holds
        the app open at shutdown."""
        if self._servers or self._threads:
            try:
                from pymodbus.server import ServerAsyncStop    # noqa: PLC0415,F401
            except Exception:                                  # noqa: BLE001
                pass
        self._threads = []
        self._servers = []
        self._blocks = {}
        self.running = False

    # -- the values ---------------------------------------------------------

    def publish(self, registers, detection_active=None):
        """Write the current values into the datastore.

        `registers` is what the compiled workflow produced:
        [{address, value, scale, kind}]. Called every detection tick, so it
        does the least possible: no allocation per register beyond the write,
        and one lock for the batch rather than one per address.

        Every register output writes EVERY tick, so an address written last
        tick and not this one has lost the output that fed it -- the AI was
        switched off and the detection-fed outputs pruned, or the node was
        deleted. It is zeroed: left alone, a PLC kept reading "3 people in
        zone 1" from a box that was no longer looking (Pipi, 2026-10-05).
        `detection_active` (when given) is written to STATUS_ADDRESS so the
        PLC can tell a real zero from a switched-off detector.
        """
        if not self.running or not self._blocks:
            return
        with self._lock:
            live = set()
            for reg in registers or []:
                name = "ir" if reg.get("kind") == "input" else "hr"
                block = self._blocks.get(name)
                if block is None:
                    continue
                try:
                    addr = int(reg.get("address") or 0)
                except (TypeError, ValueError):
                    continue
                if addr < 0 or addr >= BLOCK_SIZE:
                    continue
                try:
                    block.setValues(addr, [encode(reg.get("value"),
                                                  reg.get("scale", 1))])
                    live.add((name, addr))
                except Exception:                              # noqa: BLE001
                    # One bad register must not stop the rest being published.
                    continue
            for name, addr in self._live - live:
                try:
                    self._blocks[name].setValues(addr, [0])
                except Exception:                              # noqa: BLE001
                    continue
            self._live = live
            if detection_active is not None:
                try:
                    self._blocks["ir"].setValues(STATUS_ADDRESS,
                                                 [1 if detection_active else 0])
                except Exception:                              # noqa: BLE001
                    pass

    def state(self):
        """What the UI shows: is it up, and if not, why."""
        return {"running": self.running, "error": self.error}


def _signature(cfg):
    """The parts of the config that require a listener restart.

    The register MAP is deliberately not in here: adding an address is a
    datastore write, not a new socket, and rebuilding the listener for it
    would drop every connected PLC whenever somebody edited the workflow.
    """
    cfg = cfg or {}
    return (bool(cfg.get("enabled")), str(cfg.get("mode") or "tcp"),
            str(cfg.get("host") or ""), int(cfg.get("port") or DEFAULT_PORT),
            str(cfg.get("device") or ""), int(cfg.get("baudrate") or 9600),
            int(cfg.get("unit_id") or 1), str(cfg.get("parity") or "N"),
            int(cfg.get("stopbits") or 1), int(cfg.get("bytesize") or 8))
