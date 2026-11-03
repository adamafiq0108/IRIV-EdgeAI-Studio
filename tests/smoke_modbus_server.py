# This box AS a Modbus device: a PLC polls US.
#
# The other direction already existed -- the workflow's Modbus nodes read a
# PLC's registers. This is the mirror, and it is what lets a PLC ask "how many
# people are in zone 1" over the wire it already speaks, with no HTTP client
# on the PLC and no script in the middle.
#
# Two facts worth stating up front, because both were discovered the hard way:
#
#   pymodbus was NEVER INSTALLED on the board. It had been an optional import
#   for a long time, so every Modbus node in the palette compiled, wired, and
#   did nothing at runtime. It is a real dependency now.
#
#   pymodbus 3.15 rewrote the datastore into the simulator model:
#   ModbusSlaveContext is gone and ModbusSequentialDataBlock lost the
#   setValues/getValues a server needs. The requirement is capped below 3.9
#   deliberately -- Modbus is a frozen protocol and there is nothing to gain
#   by tracking a library that rewrites its server API.
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)

from app import modbus_server as ms                          # noqa: E402
from core.services import detector_control as dc             # noqa: E402
from core.services import workflow as wf                     # noqa: E402

# -- what reaches the wire ---------------------------------------------------

# A register is 16-bit and the values are not, so the scale is how a decimal
# survives: 2.5 at scale 10 is sent as 25 and the far end divides. Truncating
# to 2 would be a number that is almost right, which on a factory floor is
# worse than one that is obviously wrong.
assert ms.encode(7) == 7
assert ms.encode(2.5, 10) == 25
assert ms.encode(2.5) == 2 or ms.encode(2.5) == 3        # rounded, not floored

# Values that are not numbers become 0, never an exception: a Modbus scan that
# dies because a rule has not fired yet is a scan that dies at 3am.
for junk in (None, "", "abc", {}, []):
    assert ms.encode(junk) == 0, f"{junk!r} must encode as 0"

# Negative values go two's-complement so a PLC reading INT sees -5.
assert ms.encode(-5) == 65531

# Out of range is CLAMPED, not wrapped. A count that wraps to 3 is a lie the
# PLC acts on; one pinned at the top of the range is visibly saturated.
assert ms.encode(999999) == 65535
assert ms.encode(-999999) == (-32768 & 0xFFFF)

# -- the register node -------------------------------------------------------

def reg(**kw):
    body = {"type": "modbus_reg", "key": "fn_a_count", "address": 0,
            "reg_kind": "holding", "scale": 1, "trigger": "any",
            "enabled": True}
    body.update(kw)
    return dc._validate_output(body)


assert reg()["address"] == 0
assert reg(address=511)["address"] == 511
assert reg(reg_kind="input")["reg_kind"] == "input"

# A register with nothing wired into it would serve 0 for ever while looking
# configured.
for bad, why in (({"key": ""}, "no value wired"),
                 ({"address": 512}, "past the end of the block"),
                 ({"address": -1}, "negative address"),
                 ({"reg_kind": "coil"}, "not a register table"),
                 ({"scale": 0}, "a scale of zero"),
                 ({"scale": -1}, "a negative scale"),
                 ({"scale": 99999}, "an absurd scale")):
    try:
        reg(**bad)
        raise AssertionError(f"{why} must be refused")
    except ValueError:
        pass

# `or 1` would treat a typed 0 as missing and silently make it 1 -- every
# value on that register then off by whatever the operator meant to divide by.
# Missing is 1; zero is an error (above).
assert reg(scale=None)["scale"] == 1.0
assert reg(scale="")["scale"] == 1.0

# -- the listener's settings -------------------------------------------------

SYS = os.path.join(ROOT, "systems")

# The defaults must be ones that actually bind: the app is not root, and ports
# below 1024 need root, so a documented default of 502 would fail on first use.
assert dc.MODBUS_SERVER_DEFAULTS["port"] >= 1024, \
    "the default port must be bindable without root"
assert dc.MODBUS_SERVER_DEFAULTS["enabled"] is False, \
    "a box must not start serving a fieldbus nobody asked for"

# -- restart policy ----------------------------------------------------------

# Editing the register map must NOT restart the listener: adding an address is
# a datastore write, and rebuilding the socket for it would drop every
# connected PLC whenever anybody saved the workflow.
base = {"enabled": True, "mode": "tcp", "host": "0.0.0.0", "port": 5020,
        "unit_id": 1}
assert ms._signature(base) == ms._signature(dict(base)), "same config, same signature"
assert ms._signature(base) != ms._signature({**base, "port": 5021}), \
    "a new port needs a new listener"
assert ms._signature(base) != ms._signature({**base, "unit_id": 2}), \
    "a new unit id needs a new listener"

# -- the node exists and is wired end to end ---------------------------------

spec = wf.REGISTRY.get("output.modbus_reg")
assert spec, "the Modbus Register node must be in the registry"
assert spec["output_type"] == "modbus_reg"
# No trigger: a PLC scans on its own clock, so the register must hold the
# value NOW, not whatever the last alert left behind.
assert spec.get("no_trigger") is True, \
    "a served register cannot ride an alert -- a PLC reads when it likes"

with open(os.path.join(ROOT, "systems", "demo", "app", "outputs.py"),
          encoding="utf-8") as f:
    assert '"modbus_reg"' in f.read(), "the tick must publish served registers"
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()
assert "modbus_srv.publish" in run_src and "modbus_srv.start" in run_src, \
    "the app must run the server and feed it"
assert "modbus=modbus_srv.state()" in run_src, \
    "a listener that could not bind must say so, or it looks merely unpolled"

with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
    reqs = f.read()
assert "pymodbus" in reqs, \
    "pymodbus must be a REAL dependency -- as an optional import it was never installed"
assert "<3.9" in reqs, "pinned below the 3.15 datastore rewrite"

print("smoke_modbus_server OK")
