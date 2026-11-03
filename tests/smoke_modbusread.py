"""Modbus Read against the pymodbus 3.7+ call shape.

The boards install pymodbus 3.8.6, where `count` is keyword-only. Until
2026-10-04 modbus_read passed it by position, so every read raised a
TypeError inside the library before any byte reached the wire; the broad
except logged it at DEBUG and the poller reported "no answer from unit N"
on both RTU and TCP, which is exactly what the client's UAT saw. This test
drives modbus_read with a fake client whose read methods have keyword-only
`count`, so the suite catches the regression without pymodbus installed.
"""
import logging
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

fails = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(label)


class _Result:
    def __init__(self, registers=None, bits=None):
        self.registers = registers or []
        self.bits = bits or []

    def isError(self):
        return False


class _Client:
    """Mirrors pymodbus 3.7+: address positional, count keyword-only."""
    calls = []

    def __init__(self, *a, **k):
        pass

    def connect(self):
        return True

    def close(self):
        pass

    def read_holding_registers(self, address, *, count=1, slave=1):
        _Client.calls.append(("fc3", address, count, slave))
        return _Result(registers=list(range(100, 100 + count)))

    def read_input_registers(self, address, *, count=1, slave=1):
        _Client.calls.append(("fc4", address, count, slave))
        return _Result(registers=[7] * count)

    def read_coils(self, address, *, count=1, slave=1):
        _Client.calls.append(("fc1", address, count, slave))
        return _Result(bits=[True] * count + [False] * 8)

    def read_discrete_inputs(self, address, *, count=1, slave=1):
        _Client.calls.append(("fc2", address, count, slave))
        return _Result(bits=[False] * count + [True] * 8)


# A stand-in pymodbus so buses.py imports the same way it does on a board.
pm = types.ModuleType("pymodbus")
pm_client = types.ModuleType("pymodbus.client")
pm_client.ModbusSerialClient = _Client
pm_client.ModbusTcpClient = _Client
pm.client = pm_client
sys.modules["pymodbus"] = pm
sys.modules["pymodbus.client"] = pm_client

from app import buses  # noqa: E402

rtu = {"mode": "rtu", "device": "/dev/ttyACM0", "baudrate": 9600, "bytesize": 8,
       "parity": "N", "stopbits": 1, "timeout_s": 1.0, "unit": 1}
tcp = {"mode": "tcp", "host": "10.0.0.7", "port": 502, "timeout_s": 1.0, "unit": 12}

check("FC3 holding registers come back on a keyword-only client (RTU)",
      buses.modbus_read(rtu, 0, 2, "holding") == [100, 101], _Client.calls)
check("FC4 input registers (TCP, unit 12)",
      buses.modbus_read(tcp, 11, 3, "input") == [7, 7, 7], _Client.calls)
check("FC1 coils are cut to count",
      buses.modbus_read(rtu, 4, 3, "coil") == [True, True, True], _Client.calls)
check("FC2 discrete inputs are cut to count",
      buses.modbus_read(rtu, 4, 2, "discrete") == [False, False], _Client.calls)
check("count and unit reach the library as keywords",
      _Client.calls[0] == ("fc3", 0, 2, 1) and _Client.calls[1] == ("fc4", 11, 3, 12),
      _Client.calls)


# A library error is reported once per bus at WARNING, not swallowed at DEBUG.
class _Broken(_Client):
    def read_holding_registers(self, address, *, count=1, slave=1):
        raise TypeError("takes 2 positional arguments but 3 were given")


records = []


class _Catch(logging.Handler):
    def emit(self, record):
        records.append(record)


buses.logger.addHandler(_Catch())
buses.logger.setLevel(logging.DEBUG)
pm_client.ModbusSerialClient = _Broken
pm_client.ModbusTcpClient = _Broken
buses._LAST_READ_ERROR.clear()
check("a library error returns None", buses.modbus_read(rtu, 0, 2, "holding") is None)
buses.modbus_read(rtu, 0, 2, "holding")
warns = [r for r in records if r.levelno == logging.WARNING and "modbus read" in r.getMessage()]
check("...and is logged ONCE at WARNING naming the bus and the real reason",
      len(warns) == 1 and "/dev/ttyACM0" in warns[0].getMessage()
      and "positional" in warns[0].getMessage(), [r.getMessage() for r in warns])

check("the RTU default device is the built-in RS485, not the console UART",
      buses.client_config({"type": "serial"})["device"] == "/dev/ttyACM0",
      buses.client_config({"type": "serial"}))

print()
if fails:
    print(f"{len(fails)} check(s) failed"); sys.exit(1)
print("smoke_modbusread OK")
