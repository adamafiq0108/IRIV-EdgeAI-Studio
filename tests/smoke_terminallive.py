# The terminal diagram lights a terminal from the HARDWARE, live, and from
# nothing else.
#
# Until 2026-10-07 DO0 and DO1 were printed green and orange permanently, and
# a terminal a running app had claimed lit whether it was on or not -- so the
# hardware footer said "connected" about terminals with no wire in them. A
# screw terminal cannot sense a wire, so each group now shows the one signal
# that IS measurable: an input's level, an output pin read back, the LED
# driver's duty, and whether a Modbus RTU device answered lately.
#
# Mocked throughout (no pinctrl, no board, no serial port on a dev box): the
# point is the decisions, plus the guarantee that the read never claims a pin.
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import board_io as b                       # noqa: E402

# 1. A dev box with no board: everything off, nothing raises, RS485 absent.
inv = b.inventory()
assert inv["detected"] is False and inv["ports"] == []
assert inv["rs485"]["state"] == "absent"
print("== no board: all off, RS485 absent, no error")

# 2. pinctrl's output parses, and the read is pinctrl `get` -- a register
#    read that requests nothing. gpioget would flip a free output to input.
PINCTRL = """\
 4: ip    pu | hi // GPIO4 = input
12: op dh pd | lo // GPIO12 = output
13: op dl pd | lo // GPIO13 = output
19: op dl pd | lo // GPIO19 = output
22: ip    pd | hi // GPIO22 = input
23: op dh pd | hi // GPIO23 = output
24: ip    pd | hi // GPIO24 = input
27: ip    pd | lo // GPIO27 = input
"""
calls = []


def fake_run(argv, timeout=6):
    calls.append(list(argv))
    return PINCTRL if argv[:2] == ["pinctrl", "get"] else ""


b._run = fake_run
b.detected = lambda: True
b.gpio_lines = lambda: {}
b.port_owners = lambda: {}
b.released = lambda config_dir=None: set()
b._pin_cache.update(at=0.0, states={})
states = b.pin_states()
assert states[23] == {"function": "op", "level": 1}, states[23]
assert states[22]["level"] == 1 and states[27]["level"] == 0
assert any(c[:2] == ["pinctrl", "get"] for c in calls)
assert not any(c and c[0] in ("gpioset", "gpioget") for c in calls), \
    "the live read must not request a line"
print("== pinctrl parsed; no line requested")

# debugfs fallback has the same shape
dbg = b._parse_debugfs(" gpio-592 (GPIO23              |lg                  ) out hi \n"
                       " gpio-591 (GPIO22              |lg                  ) in  lo \n")
assert dbg == {23: {"function": "op", "level": 1}, 22: {"function": "ip", "level": 0}}

# 3. Each group from its own signal.
#    DO0: output, reads high -> lit. DO1: pin is an INPUT that happens to read
#    high (released / floating) -> NOT lit: a pad that is not an output is not
#    an energised relay. DI0 high -> lit, DI1 low -> off, independently.
#    L0: output whose app publishes 40% (the instant sample read lo) -> lit.
#    L1: output, app publishes 0% -> off.
activity = {"pwm": {12: 40.0, 13: 0.0}, "rs485": {}}
b._pin_cache.update(at=time.time(), states=b._parse_pinctrl(PINCTRL))
rows = {p["id"]: p for p in b.ports(None, activity)}
assert rows["DO0"]["lit"] is True
assert rows["DO1"]["lit"] is False, "an input pad reading high is not an energised output"
assert rows["DI0"]["lit"] is True and rows["DI1"]["lit"] is False
assert rows["L0"]["lit"] is True and rows["L0"]["duty"] == 40.0
assert rows["L1"]["lit"] is False
assert rows["DO0"]["level"] == 1 and rows["DO0"]["function"] == "op"
# No published duty (driven by something that is not ours): the pad decides.
rows = {p["id"]: p for p in b.ports(None, {"pwm": {}, "rs485": {}})}
assert rows["L0"]["lit"] is False, "no duty and the pad reads lo -> off"
# Nothing readable at all -> dark, never a guess.
b._pin_cache.update(at=time.time(), states={})
rows = {p["id"]: p for p in b.ports(None, activity)}
assert not any(r["lit"] for r in rows.values()), "unreadable must be off"
assert rows["DO0"]["level"] is None
print("== DI / DO / L each lit from its own read-back, dark when unreadable")

# 4. Inputs: read_port answers from the pad read first (works while an app
#    holds the line, where gpioget fails busy).
b._pin_cache.update(at=time.time(), states=b._parse_pinctrl(PINCTRL))
calls.clear()
assert b.read_port("DI0") == 1 and b.read_port("DI1") == 0
assert not calls, "read_port must use the cached pad read, not gpioget"
print("== DI level comes from the non-claiming read")

# 5. RS485 from what the bus DID, on the ttyACM adapter (datasheet 5.1).
import glob as _g                                             # noqa: E402
real_glob = _g.glob
b._globmod.glob = lambda pat: ["/dev/ttyACM0"] if pat == "/dev/ttyACM*" else []
now = 1000.0
st = b.rs485_status({"rs485": {"/dev/ttyACM0": {"ok_at": 998.0}}}, now)
assert st["state"] == "active" and st["ok_age_s"] == 2.0, st
st = b.rs485_status({"rs485": {"/dev/ttyACM0": {"ok_at": 990.0}}}, now)
assert st["state"] == "idle", "an answer 10 s ago is history, not activity"
st = b.rs485_status({"rs485": {"/dev/ttyACM0": {"ok_at": 997.0, "err_at": 999.0,
                                                "error": "no answer"}}}, now)
assert st["state"] == "error" and st["error"] == "no answer"
st = b.rs485_status({"rs485": {"/dev/ttyUSB0": {"ok_at": 999.0}}}, now)
assert st["state"] == "idle", "a USB adapter is not the board's RS485 terminal"
st = b.rs485_status({}, now)
assert st["state"] == "idle"
b._globmod.glob = lambda pat: []
assert b.rs485_status({"rs485": {"/dev/ttyACM0": {"ok_at": 999.0}}}, now)["state"] \
    == "absent", "no adapter -> absent, whatever a stale file says"
b._globmod.glob = real_glob
print("== RS485 active / error / idle / absent from real exchanges")

# 6. The app side writes those facts where the hub reads them, and a Modbus
#    exception reply counts as an answer (something on the pair spoke).
from app import buses                                         # noqa: E402

d = tempfile.mkdtemp()
buses.set_state_dir(d)
path = os.path.join(d, buses.ACTIVITY_FILE)
assert json.load(open(path, encoding="utf-8")) == {"pwm": {}, "rs485": {}}, \
    "a restart must clear the previous run's activity"
buses._note_rtu({"mode": "rtu", "device": "/dev/ttyACM0"}, True)
data = json.load(open(path, encoding="utf-8"))
assert data["rs485"]["/dev/ttyACM0"]["ok_at"] > 0
buses._note_rtu({"mode": "tcp", "host": "1.2.3.4"}, False, "x")
assert list(json.load(open(path, encoding="utf-8"))["rs485"]) == ["/dev/ttyACM0"], \
    "TCP is not the RS485 terminal"
buses._note_rtu({"mode": "rtu", "device": "/dev/ttyACM0"}, False, "timeout")
data = json.load(open(path, encoding="utf-8"))
assert data["rs485"]["/dev/ttyACM0"]["error"] == "timeout", \
    "a change of outcome is written at once, not after the throttle"
buses._note_pwm(12, 55.0)
assert json.load(open(path, encoding="utf-8"))["pwm"] == {"12": 55.0}
buses._note_pwm(12, None)
assert json.load(open(path, encoding="utf-8"))["pwm"] == {}


class ExceptionResponse:
    """pymodbus's "the device answered with an error" -- not a lost link."""

    def __init__(self, err):
        self._err = err
        self.registers = [7]

    def isError(self):
        return self._err


class _Client:
    connected = True

    def __init__(self, result):
        self.result = result

    def connect(self):
        return True

    def read_holding_registers(self, *a, **k):
        return self.result

    def close(self):
        pass


cfg = {"mode": "rtu", "device": "/dev/ttyACM9", "unit": 1}
buses.modbus_client = lambda c: _Client(ExceptionResponse(True))           # exception reply
assert buses.modbus_read(cfg, 0) is None
row = json.load(open(path, encoding="utf-8"))["rs485"]["/dev/ttyACM9"]
assert row.get("ok_at") and not row.get("err_at"), \
    "an exception reply is a frame from a device -- the bus is alive"
print("== the app publishes RTU outcomes and PWM duty for the hub")

# 7. The diagram reads only that: no permanent group colours, no "claimed =
#    lit", and a legend that says what a light means.
with open(os.path.join(ROOT, "frontend", "src", "workflow", "TerminalBlock.jsx"),
          encoding="utf-8") as f:
    block = f.read()
with open(os.path.join(ROOT, "frontend", "src", "theme.css"), encoding="utf-8") as f:
    css = f.read()
assert "p.lit === true" in block and "p.owner" not in block, \
    "an output lights from the read-back, not from who claimed it"
assert "iriv-term-grp-do0" not in block + css and "iriv-term-do0-text" not in block + css, \
    "DO0/DO1 must not be coloured while off"
assert "Lit = signal active now" in block
assert "board?.rs485" in block
for tone in ("led", "bus"):
    assert f".iriv-term-socket.is-{tone}.is-active" in css, f"{tone} has no lit style"
assert ".iriv-term-socket.is-warn" in css
print("smoke_terminallive OK")
