# One TCP connection per Modbus server, held open across writes and reads.
#
# Tok Aba's bug (2026-10-07, box 192.168.1.49, asri_test_4): Inject 1000 ->
# Modbus Write (FC 6, address 0) to an iSmartDrive motor driver over Modbus
# TCP. In Node-RED the motor runs until 0 is written; in ours it ran "for a
# while only" and stopped by itself. Nothing wrote a 0 -- the recording below
# shows exactly one write per press, the pressed value, nothing after -- but
# every write was its OWN connection, opened and closed within milliseconds
# (buses.modbus_write_fc connected and closed per call, modbus_read too).
# The driver takes the master's socket going away as communication lost and
# stops after its watchdog; Node-RED's modbus-client holds one socket open,
# which is why the same flow works there. The driver answering one
# connection at a time also refused the reader's next connect while the
# closed one was still draining, the "no answer from unit 1" beside it.
#
# Pinned:
#   * the Inject -> Modbus Write path writes ONCE per press, the pressed
#     value, and never a 0 of its own: 1000, 500, 0 wired into one node is
#     "last pressed wins", the second node's 1/0 likewise;
#   * one connect for the whole sequence, no close after a write, and the
#     reader on the same server uses the SAME socket;
#   * a failed transaction (raise, or a ModbusIOException result) closes the
#     socket so the next call reconnects; a protocol exception from the device
#     (illegal address) keeps it;
#   * a changed timeout rebuilds the connection; serial still connects and
#     closes per call;
#   * the Modbus Read loop keeps polling a silent unit (backing off to 30 s)
#     and no longer logs "returned -- that feature has stopped" on a hand-over;
#   * with pymodbus installed: a real TCP server sees ONE accept for several
#     writes and a read, and the register holds the last pressed value.
#
#     py -3 tests/smoke_modbuspersist.py
import json
import logging
import os
import socket
import struct
import sys
import tempfile
import threading
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 0. a real Modbus TCP server, when the real pymodbus is here -------------
# Runs FIRST, before the fake pymodbus below takes its place in sys.modules.
try:
    import pymodbus                                            # noqa: F401
    HAVE_PYMODBUS = True
except ImportError:
    HAVE_PYMODBUS = False


class TinyServer:
    """Modbus TCP, FC 3 and FC 6 only, one client at a time. Counts accepts
    and closes the way a driver's watchdog sees them."""

    def __init__(self):
        self.regs = {}
        self.accepts = 0
        self.closes = 0
        self.writes = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        self.sock.settimeout(0.2)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            self.accepts += 1
            with conn:
                while not self.stop.is_set():
                    try:
                        head = conn.recv(7)
                    except OSError:
                        break
                    if len(head) < 7:
                        break
                    tid, pid, length, uid = struct.unpack(">HHHB", head)
                    pdu = conn.recv(length - 1)
                    fc = pdu[0]
                    if fc == 6:
                        addr, val = struct.unpack(">HH", pdu[1:5])
                        self.regs[addr] = val
                        self.writes.append((addr, val))
                        body = pdu
                    elif fc == 3:
                        addr, count = struct.unpack(">HH", pdu[1:5])
                        regs = [self.regs.get(addr + i, 0) for i in range(count)]
                        body = bytes([3, count * 2]) + struct.pack(f">{count}H", *regs)
                    else:
                        body = bytes([fc | 0x80, 1])
                    conn.sendall(struct.pack(">HHHB", tid, pid, len(body) + 1, uid) + body)
            self.closes += 1


if HAVE_PYMODBUS:
    from app import buses as real_buses
    srv = TinyServer()
    cfg = {"mode": "tcp", "host": "127.0.0.1", "port": srv.port,
           "timeout_s": 2.0, "unit": 1}
    r1 = real_buses.modbus_write_fc(cfg, 6, 0, [1000], 1)
    r2 = real_buses.modbus_write_fc(cfg, 6, 0, [500], 1)
    got = real_buses.modbus_read(cfg, 0, 1, "holding")
    r3 = real_buses.modbus_write_fc(cfg, 6, 0, [0], 1)
    check("real pymodbus: three writes and a read land", r1[0] and r2[0] and r3[0]
          and got == [500], (r1, r2, r3, got))
    check("...on ONE accepted connection, never closed between them",
          srv.accepts == 1 and srv.closes == 0, (srv.accepts, srv.closes))
    check("...the register holds the last pressed value", srv.regs.get(0) == 0
          and srv.writes == [(0, 1000), (0, 500), (0, 0)], srv.writes)
    real_buses.close_pool()
    threading.Event().wait(0.3)
    check("close_pool() is the one thing that hangs up", srv.closes == 1, srv.closes)
    srv.stop.set()
else:
    print("note pymodbus not installed here: the real-socket part is skipped "
          "(it runs on the Linux CI, which installs requirements.txt)")

# -- the fake pymodbus: records every connect / close / write / read ---------
LOG = []
FAIL_NEXT = {"raise": False, "io": False, "exc": False}


class _Res:
    def __init__(self, regs=None):
        self.registers = regs or []
        self.bits = []

    def isError(self):
        return False


class ModbusIOException(Exception):
    """Named as pymodbus names a timeout / lost link result."""

    def isError(self):
        return True

    def __str__(self):
        return "Modbus Error: [Input/Output] no response"


class ExceptionResponse:
    """A protocol exception the DEVICE answered: the link is fine."""

    def isError(self):
        return True

    def __str__(self):
        return "Exception Response(134, 6, IllegalAddress)"


class FakeClient:
    n = 0

    def __init__(self, *a, **k):
        FakeClient.n += 1
        self.me = FakeClient.n
        self.socket = None
        self.kw = k

    @property
    def connected(self):
        return self.socket is not None

    def connect(self):
        if self.socket is None:
            self.socket = True
            LOG.append(("connect", self.me))
        return True

    def close(self):
        if self.socket is not None:
            self.socket = None
            LOG.append(("close", self.me))

    def _fault(self):
        if FAIL_NEXT["raise"]:
            FAIL_NEXT["raise"] = False
            raise ConnectionError("Connection unexpectedly closed")
        if FAIL_NEXT["io"]:
            FAIL_NEXT["io"] = False
            self.socket = None          # pymodbus drops the socket on a timeout
            return ModbusIOException()
        if FAIL_NEXT["exc"]:
            FAIL_NEXT["exc"] = False
            return ExceptionResponse()
        return None

    def write_register(self, address, value, slave=1):
        LOG.append(("write", address, value, slave))
        return self._fault() or _Res()

    def write_coil(self, address, value, slave=1):
        LOG.append(("coil", address, value, slave))
        return self._fault() or _Res()

    def read_holding_registers(self, address, *, count=1, slave=1):
        LOG.append(("read", address, count, slave))
        return self._fault() or _Res([42] * count)


pm = types.ModuleType("pymodbus")
pmc = types.ModuleType("pymodbus.client")
pmc.ModbusTcpClient = FakeClient
pmc.ModbusSerialClient = FakeClient
pm.client = pmc
sys.modules["pymodbus"] = pm
sys.modules["pymodbus.client"] = pmc

from app import buses                                          # noqa: E402
from app import faults                                         # noqa: E402
from app import functions as fx                                # noqa: E402
from app import outputs as out                                 # noqa: E402
from core.services import workflow as wf                       # noqa: E402

buses.close_pool()
LOG.clear()


def since(mark):
    return LOG[mark:]


# -- 1. the bug as drawn: Injects into two Modbus Writes ---------------------
def N(nid, t, **d):
    return {"id": nid, "type": t, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[t]["data"], **d}}


_i = [0]


def E(s, t, th="in"):
    _i[0] += 1
    return {"id": f"e{_i[0]}", "source": s, "target": t, "targetHandle": th}


g = wf.clean_graph({"nodes": [
    N("i1000", "common.inject", fnId="speed_1000", ptype="number", payload="1000"),
    N("i500", "common.inject", fnId="speed_500", ptype="number", payload="500"),
    N("i0", "common.inject", fnId="speed_0", ptype="number", payload="0"),
    N("mbs", "output.modbus_write", server="drive", address=0, fc=6, unit=1,
      name="speed"),
    N("r1", "common.inject", fnId="run_1", ptype="number", payload="1"),
    N("r0", "common.inject", fnId="run_0", ptype="number", payload="0"),
    N("mbr", "output.modbus_write", server="drive", address=1, fc=6, unit=1,
      name="run"),
], "edges": [E("i1000", "mbs"), E("i500", "mbs"), E("i0", "mbs"),
             E("r1", "mbr"), E("r0", "mbr")]})
wf.ensure_fn_ids(g)
c = wf.compile_store({"version": 3, "workflows": [
    {"id": "main", "name": "Main", "enabled": True, "graph": g}]}, [], [], [])
check("three Injects into one Modbus Write compile (mode fire, one source per wire)",
      not c["errors"] and [o["mode"] for o in c["outputs"]] == ["fire", "fire"]
      and [len(o["sources"]) for o in c["outputs"]] == [3, 2], c["errors"] or c["outputs"])

buses.set_clients([{"id": "drive", "type": "tcp", "host": "192.168.4.4",
                    "port": 502, "unit_id": 1, "timeout_ms": 1000}])
out._MBW_INLINE = True
state = tempfile.mkdtemp()
eng = fx.FunctionEngine([fx.Function(f) for f in c["functions"]], state_dir=state)
seq = [0]


def press(fid, now):
    seq[0] += 1
    path = os.path.join(state, fx.INJECT_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({fid: {"seq": seq[0], "ts": now, "action": "press"}}, f)
    os.utime(path, (now, now + seq[0]))          # a new mtime per press


def run(now, ticks):
    for _ in range(ticks):
        now += 0.5
        eng.update({"crossing_events": []}, [], {}, {}, 640, 480, now=now)
        out.publish(c["outputs"], eng.stats(), now=now)
    return now


t = run(1000.0, 6)
check("idle: nothing written, nothing connected", LOG == [], LOG)
press("speed_1000", t)
t = run(t, 20)
check("press 1000: ONE write of 1000, on a connection that stays up (10 s on)",
      LOG == [("connect", FakeClient.n), ("write", 0, 1000, 1)], LOG)
mark = len(LOG)
press("speed_500", t)
t = run(t, 10)
check("press 500: one write of 500, same socket, no close in between",
      since(mark) == [("write", 0, 500, 1)], since(mark))
mark = len(LOG)
press("run_1", t)
t = run(t, 10)
check("the second node's Inject 1: one write to ITS register, same socket",
      since(mark) == [("write", 1, 1, 1)], since(mark))
mark = len(LOG)
press("speed_0", t)
t = run(t, 10)
check("press 0: one write of 0 -- the only 0 is the one pressed",
      since(mark) == [("write", 0, 0, 1)], since(mark))
check("the whole sequence is one connect, four writes, no close",
      [e[0] for e in LOG] == ["connect", "write", "write", "write", "write"], LOG)
check("the server's socket is held in the pool",
      buses.pooled_connections() == ["tcp://192.168.4.4:502"], buses.pooled_connections())

# -- 2. the reader shares it; failures reconnect; serial is untouched --------
cfg = buses.client_config(buses.client("drive"), 1)
mark = len(LOG)
got = buses.modbus_read(cfg, 0, 2, "holding")
check("a Modbus Read on the same server rides the same socket (no connect, no close)",
      got == [42, 42] and since(mark) == [("read", 0, 2, 1)], since(mark))

FAIL_NEXT["raise"] = True
mark = len(LOG)
ok, msg = buses.modbus_write_fc(cfg, 6, 0, [7], 1)
check("a write that raises is reported and the socket is closed",
      not ok and "closed" in msg and since(mark)[-1][0] == "close", (ok, msg, since(mark)))
mark = len(LOG)
ok, _ = buses.modbus_write_fc(cfg, 6, 0, [7], 1)
check("...and the next write reconnects by itself, on the same client",
      ok and since(mark) == [("connect", FakeClient.n), ("write", 0, 7, 1)], since(mark))

FAIL_NEXT["io"] = True
mark = len(LOG)
got = buses.modbus_read(cfg, 0, 1, "holding")
ok, _ = buses.modbus_write_fc(cfg, 6, 0, [8], 1)
check("a timeout (ModbusIOException, socket dropped by the library) reads None, "
      "and the write after it reconnects",
      got is None and ok and [e[0] for e in since(mark)] == ["read", "connect", "write"],
      since(mark))

FAIL_NEXT["exc"] = True
mark = len(LOG)
ok, msg = buses.modbus_write_fc(cfg, 6, 9999, [1], 1)
ok2, _ = buses.modbus_write_fc(cfg, 6, 0, [9], 1)
check("the device answering 'illegal address' is an error that KEEPS the socket",
      not ok and "IllegalAddress" in msg and ok2
      and [e[0] for e in since(mark)] == ["write", "write"], (msg, since(mark)))

mark = len(LOG)
n0 = FakeClient.n
buses.modbus_write_fc(dict(cfg, timeout_s=5.0), 6, 0, [1], 1)
check("a changed timeout closes the old client and opens a new one",
      FakeClient.n == n0 + 1 and [e[0] for e in since(mark)] == ["close", "connect", "write"],
      since(mark))
check("...still one pooled entry for that server",
      buses.pooled_connections() == ["tcp://192.168.4.4:502"], buses.pooled_connections())

rtu = {"mode": "rtu", "device": "/dev/ttyACM0", "baudrate": 9600, "unit": 3}
mark = len(LOG)
buses.modbus_write_fc(rtu, 6, 1, [5], 3)
buses.modbus_read(rtu, 1, 1, "holding")
check("serial connects and closes per call, as before (nothing pooled)",
      [e[0] for e in since(mark)] == ["connect", "write", "close", "connect", "read", "close"]
      and buses.pooled_connections() == ["tcp://192.168.4.4:502"], since(mark))

buses.close_pool()
check("close_pool() hangs up and empties the pool",
      LOG[-1][0] == "close" and buses.pooled_connections() == [], LOG[-2:])

# -- 3. the Modbus Read loop: keeps polling, backs off, no false 'returned' --
check("backoff: the interval up to the stale mark, then doubling, capped at 30 s",
      [fx.modbus_backoff(1.0, n) for n in (0, 1, 2, 3, 4, 5, 6, 7, 8)]
      == [1.0, 1.0, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
      and fx.modbus_backoff(10.0, 3) == 20.0 and fx.modbus_backoff(0.5, 20) == 30.0,
      [fx.modbus_backoff(1.0, n) for n in range(9)])

records = []


class _Grab(logging.Handler):
    def emit(self, record):
        records.append(record)


grab = _Grab()
logging.getLogger("app.faults").addHandler(grab)
logging.getLogger("app.faults").setLevel(logging.DEBUG)
stop = threading.Event()
th = faults.spawn("modbus-probe", fx._modbus_loop,
                  ("probe", {"server": "drive", "address": 0, "every": 0.05},
                   ("sig",), stop), once=True)
th.start()
threading.Event().wait(0.3)
stop.set()
th.join(2.0)
check("a stopped Read loop ends quietly: no 'returned -- that feature has stopped'",
      not th.is_alive() and not [r for r in records if "returned" in r.getMessage()],
      [r.getMessage() for r in records])


def boom(*a):
    raise RuntimeError("bus on fire")


th = faults.spawn("modbus-boom", boom, (), once=True)
th.start()
th.join(2.0)
check("...while a loop that RAISES is still logged as stopped",
      [r for r in records if "stopped: bus on fire" in r.getMessage()],
      [r.getMessage() for r in records])
src = open(os.path.join(ROOT, "systems", "demo", "app", "functions.py"),
           encoding="utf-8").read()
check("_ensure_modbus spawns the loop with once=True",
      "_modbus_loop,\n                              (fn.id, dict(fn.params), sig, stop, fresh[\"kick\"]),\n"
      "                              once=True)" in src)
logging.getLogger("app.faults").removeHandler(grab)

print()
print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
