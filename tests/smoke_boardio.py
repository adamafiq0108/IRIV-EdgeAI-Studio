# The IRIV Edge AI's own terminal block, reachable from a workflow: read DI0,
# switch DO0/DO1, dim L0/L1, write a PLC register over RS485.
#
# The pin map is the part worth guarding. It was read off the board's factory
# Node-RED flows -- the vendor's own statement of its pinout -- and a wrong pin
# here closes a relay on real machinery. So this test pins the numbers, pins
# that the map is only applied to a board actually identified as an IRIV, and
# pins that the two copies of it (app side and core side) agree.
#
# L0/L1 moved from GPIO20/21 to GPIO12/13 on 2026-10-07: the datasheet puts
# the LED driver terminals there; 20/21 are the board's user LEDs.
import ast
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from app import iriv as app_iriv                              # noqa: E402
from core.services import board_io                            # noqa: E402
from core.services import detector_control as dc              # noqa: E402
from core.services import functions as fn_service             # noqa: E402
from core.services import workflow as wf                      # noqa: E402

# 1. the pinout, exactly as the board states it
WANT = {"DI0": (22, "input"), "DI1": (27, "input"),
        "DO0": (23, "output"), "DO1": (24, "output"),
        "L0": (12, "pwm"), "L1": (13, "pwm"),
        "BUZZER": (19, "output"), "BUTTON": (4, "input")}
for pid, (pin, kind) in WANT.items():
    port = board_io.PORT_BY_ID[pid]
    assert port["pin"] == pin, f"{pid} moved to pin {port['pin']}"
    assert port["kind"] == kind, f"{pid} is a {port['kind']}"
print("== the pin map is the board's: DI0=22 DI1=27 DO0=23 DO1=24 L0=12 L1=13")

# 2. the two copies agree. The app imports nothing from core, so the table is
#    mirrored -- which is exactly the kind of duplication that drifts.
assert {p["id"]: (p["pin"], p["kind"]) for p in app_iriv.BOARD_PORTS} \
    == {p["id"]: (p["pin"], p["kind"]) for p in board_io.BOARD_PORTS}, \
    "app/iriv.py and core/services/board_io.py disagree about the pinout"
print("== the app-side and core-side pin maps are identical")

# 3. the map is NOT applied to a box that is not an IRIV: a bare Raspberry Pi
#    must never be handed pin 23 to drive.
src = open(os.path.join(ROOT, "core", "services", "board_io.py"),
           encoding="utf-8").read()
tree = ast.parse(src)
detect = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "detected")
detect_src = ast.get_source_segment(src, detect)
assert "compute module 5" in detect_src and "/etc/iriv" in detect_src, \
    "detection no longer requires BOTH the CM5 and the platform marker"
ports_src = ast.get_source_segment(
    src, next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "ports"))
assert "if not detected():" in ports_src and "return []" in ports_src, \
    "ports() hands out the pinout without checking the board"
print("== an unidentified board gets an empty port list, never a guess")

# 4. the nodes exist and compile to the right output kinds. DO, PWM and the
#    buzzer are SEPARATE nodes -- three verbs, three nodes -- and RS485's
#    supported direction is the READER (the write node is retired).
for ntype, otype in (("output.io_out", "io_out"), ("output.io_pwm", "io_pwm"),
                     ("output.buzzer", "io_out")):
    spec = wf.REGISTRY[ntype]
    assert spec["category"] == "output" and spec["output_type"] == otype
assert wf.REGISTRY["output.buzzer"]["data"]["port"] == "BUZZER"
assert wf.REGISTRY["output.buzzer"]["data"]["action"] == "pulse", \
    "a buzzer that defaults to latched-on is an alarm nobody can silence"
assert wf.REGISTRY["output.rs485"].get("deprecated"), \
    "the RS485 WRITE node should be retired from the palette"
assert wf.REGISTRY["variable.rs485"]["fn_type"] == "modbus_read"
assert wf.REGISTRY["variable.board_input"]["fn_type"] == "board_input"
for standalone in ("board_input", "modbus_read"):
    assert standalone in wf.STANDALONE_FN_TYPES, \
        f"{standalone} needs no camera -- it must compile without a stream"
print("== Board Input / DO / Buzzer / PWM / RS485 Read are palette nodes")

# 4b. PWM speaks 0-255 (the byte convention every datasheet uses); the old
#     percent field still works so a saved config keeps meaning what it did.
lv = dc._validate_output({"type": "io_pwm", "port": "L1", "level": 255,
                          "trigger": "any"})
assert lv["percent"] == 100.0, lv
old_pct = dc._validate_output({"type": "io_pwm", "port": "L1", "percent": 50,
                               "trigger": "any"})
assert old_pct["percent"] == 50.0
try:
    dc._validate_output({"type": "io_pwm", "port": "L1", "level": 300,
                         "trigger": "any"})
except ValueError:
    pass
else:
    raise AssertionError("a level over 255 was accepted")
print("== PWM level is 0-255, and an old percent config still validates")

# 4c. the reader validates the way a Modbus datasheet reads: FC 1-4, a unit,
#     an address and a QUANTITY that loops up from it.
ok_fn = fn_service.validate_function(
    {"id": "rs", "name": "Flow sensor", "type": "modbus_read",
     "params": {"fc": 4, "unit": 7, "address": 100, "quantity": 8}})
assert ok_fn["params"]["fc"] == 4 and ok_fn["params"]["quantity"] == 8
for bad_params in ({"fc": 5}, {"fc": 6, "unit": 1}, {"quantity": 0},
                   {"quantity": 99}, {"unit": 300}):
    try:
        fn_service.validate_function({"id": "x", "name": "x",
                                      "type": "modbus_read",
                                      "params": bad_params})
    except ValueError:
        continue
    raise AssertionError(f"accepted {bad_params}")
assert fn_service.OUTPUTS["modbus_read"] == ("value", "ok")
print("== RS485 Read takes FC 1-4 / unit / address / quantity, refuses writes")

# 5. output validation: the port is named, not numbered, and refused if wrong
ok = dc._validate_output({"type": "io_out", "port": "DO1", "action": "pulse",
                          "pulse_ms": 400, "trigger": "alert"})
assert ok["port"] == "DO1" and ok["action"] == "pulse" and ok["pulse_ms"] == 400
for bad in ({"type": "io_out", "port": "DI0"},          # an input
            {"type": "io_out", "port": "GPIO23"},       # a pin, not a port
            {"type": "io_pwm", "port": "DO0"},          # not a PWM port
            {"type": "io_out", "port": "DO0", "action": "explode"}):
    try:
        dc._validate_output({**bad, "trigger": "alert"})
    except ValueError:
        continue
    raise AssertionError(f"accepted {bad}")
print("== a port that cannot do the job is refused, not silently accepted")

# 6. a PWM node driven by a value must say what its range means
pwm = dc._validate_output({"type": "io_pwm", "port": "L0", "source": "value",
                           "key": "fn_x_count", "in_min": 0, "in_max": 8,
                           "when": "always", "trigger": "any"})
assert pwm["when"] == "always" and pwm["in_max"] == 8 and pwm["key"] == "fn_x_count"
try:
    dc._validate_output({"type": "io_pwm", "port": "L0", "source": "value",
                         "key": "fn_x_count", "in_min": 5, "in_max": 5,
                         "trigger": "any"})
except ValueError:
    pass
else:
    raise AssertionError("a zero-width input range was accepted")
print("== a following PWM carries its value key and a real range")

# 7. the DI function type publishes what a rule can actually use, and `ok` is
#    distinct from `state`: an unreadable safety input is not an open one.
assert fn_service.OUTPUTS["board_input"] == ("state", "changes", "held_s", "ok")
clean = fn_service.validate_function(
    {"id": "di", "name": "Guard door", "type": "board_input",
     "params": {"port": "di0", "invert": True}})
assert clean["params"]["port"] == "DI0" and clean["params"]["invert"] is True
try:
    fn_service.validate_function({"id": "x", "name": "x", "type": "board_input",
                                  "params": {"port": "DO0"}})
except ValueError:
    pass
else:
    raise AssertionError("an OUTPUT port was accepted as an input to read")
print("== board_input publishes state/changes/held_s/ok and validates its port")

# 8. RS485 carries its own transport: baud and coil-vs-register are settable,
#    because a mismatch on either is silence on the wire rather than an error.
rs = dc._validate_output({"type": "modbus", "device": "/dev/ttyAMA2",
                          "baudrate": 19200, "unit": 3, "target": "coil",
                          "register": 12, "value": 1, "trigger": "alert"})
assert rs["baudrate"] == 19200 and rs["target"] == "coil"
app_out = open(os.path.join(ROOT, "systems", "demo", "app", "outputs.py"),
               encoding="utf-8").read()
assert "write_coil" in app_out and 'o.get("baudrate")' in app_out, \
    "the app-side writer ignores the coil/baud settings the UI collects"
print("== RS485 writes coils as well as registers, at the configured baud")

# 9. the manual test is a PULSE and is capped -- a wiring check, not a second
#    control path that could hold a relay closed from a browser.
pulse = ast.get_source_segment(
    src, next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "pulse_port"))
assert "min(5000" in pulse, "the wiring-check pulse is no longer bounded"
routes = open(os.path.join(ROOT, "core", "routes", "models.py"),
              encoding="utf-8").read()
assert '_require_operator(request, slug, "testing board I/O")' in routes, \
    "the board test endpoint is not gated on access to the system"
print("== the wiring test is capped at 5s and gated on the system grant")

print("smoke_boardio OK")
