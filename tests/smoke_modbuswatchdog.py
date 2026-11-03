# Modbus Server connection: the "Drive watchdog" switch.
#
# Tok Aba, 2026-10-07: a Cytron motor driver (box .49, 192.168.4.4:502, FC 6,
# address 0 = MTR_PWM, unit 1) ran only for a moment after one write of 1000.
# A tcpdump proved the write reached the drive and was accepted; the drive's
# Modbus comms watchdog (holding register 105 WDT_MS, 100 ms units, 0 = off)
# then reset the speed. Register 100 CFG_SAVE (write 1) keeps the setting.
# The connection now has one switch, and this pins how it behaves:
#
#   * NOT SET (every connection saved before it): nothing is written on
#     connect and every Modbus Write writes once, exactly as before;
#   * OFF: on each connect (TCP: the pooled socket opening or re-opening;
#     RTU: before the port's first transaction) the box writes WDT = 0, on
#     the connection's Unit-Id, before anything else -- one write of 1000
#     then runs the motor until 0;
#   * CFG_SAVE (flash) is NEVER written by default: the box sets WDT again
#     on every connect. "Also save to the drive's memory" writes it once per
#     changed WDT value, remembered across reconnects and restarts;
#   * ON: the same with WDT = timeout / 100, and every Modbus Write on the
#     connection keeps sending its last value at half the timeout (0.05 s at
#     the 100 ms minimum, between the loop's ticks), with the held-back rules
#     (a node that drops out of the running set, or whose value is held back,
#     stops repeating and says so) and a fair bus lock (a Read on the same
#     server waits for at most one transaction);
#   * the register numbers default to 105 / 100 and are editable;
#   * a failed setup is logged once, shown on the nodes, never retried in a
#     loop; a device that does not answer fails the transaction like any
#     timeout, and the next connect tries again;
#   * the node-level "Keep sending" of 79152b9b is gone: a node saved with
#     its fields compiles exactly as one without them.
#
#     py -3 tests/smoke_modbuswatchdog.py
import copy
import logging
import os
import sys
import tempfile
import threading
import time
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


def raises(call, kind=ValueError):
    try:
        call()
    except kind:
        return True
    except Exception:                                          # noqa: BLE001
        return False
    return False


# -- a fake pymodbus that records connect / close / write / read ---------------
LOG = []
SAVES = []              # every CFG_SAVE write, never cleared
CLOCK = [1000.0]
FAIL = {"on": False, "refuse": set(), "silent": set(), "cost": 0.0}


class _Res:
    registers = [7]

    def isError(self):
        return False


class ExceptionResponse:
    """What pymodbus returns when the DEVICE answers with an error."""

    def isError(self):
        return True

    def __str__(self):
        return "Exception Response (illegal data address)"


class FakeClient:
    n = 0

    def __init__(self, *a, **k):
        FakeClient.n += 1
        self.me = FakeClient.n
        self.socket = None
        self.serial = "port" in k and str(k.get("port", "")).startswith("/dev/")

    @property
    def connected(self):
        return self.socket is not None

    def connect(self):
        if self.socket is None:
            self.socket = True
            LOG.append(("connect", self.me, CLOCK[0]))
        return True

    def close(self):
        if self.socket is not None:
            self.socket = None
            LOG.append(("close", self.me, CLOCK[0]))

    def write_register(self, address, value, slave=1):
        LOG.append(("write", address, value, slave, CLOCK[0]))
        if address in (100, 201):
            SAVES.append((address, value))
        if FAIL["cost"]:
            time.sleep(FAIL["cost"])
        if FAIL["on"] or address in FAIL["silent"]:
            raise ConnectionError("Connection unexpectedly closed")
        if address in FAIL["refuse"]:
            return ExceptionResponse()
        return _Res()

    def read_holding_registers(self, address, count=1, slave=1):
        LOG.append(("read", address, count, slave, CLOCK[0]))
        if FAIL["cost"]:
            time.sleep(FAIL["cost"])
        return _Res()


pm = types.ModuleType("pymodbus")
pmc = types.ModuleType("pymodbus.client")
pmc.ModbusTcpClient = FakeClient
pmc.ModbusSerialClient = FakeClient
pm.client = pmc
sys.modules["pymodbus"] = pm
sys.modules["pymodbus.client"] = pmc

from app import buses                                          # noqa: E402
from app import outputs as out                                 # noqa: E402
from app import run as apprun                                  # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from core.services import modbus_watchdog as mw                # noqa: E402
from core.services import workflow as wf                       # noqa: E402

DRIVE = {"id": "drive", "type": "tcp", "host": "192.168.4.4", "port": 502,
         "unit_id": 1, "timeout_ms": 1000, "reconnect_on_timeout": True,
         "reconnect_timeout_ms": 100}
INIT = {105, 100, 200, 201}


def connect_as(**kw):
    """Point the 'drive' connection at these switch settings (live, as
    run.py's config reload does)."""
    buses.set_clients([dict(DRIVE, **kw)])


buses.close_pool()
connect_as()
out._MBW_INLINE = True
out._APP_STARTED = 0.0


def writes(since=0):
    """The USER's writes (value, time) -- the connect-time ones filtered out."""
    return [(e[2], e[4]) for e in LOG[since:] if e[0] == "write" and e[1] not in INIT]


def wire(since=0):
    """Everything written, in order: (address, value, unit)."""
    return [(e[1], e[2], e[3]) for e in LOG[since:] if e[0] == "write"]


def node(oid, **kw):
    o = {"id": oid, "type": "modbus_write", "enabled": True, "trigger": "any",
         "mode": "change", "server": "drive", "fc": 6, "unit": 1,
         "address": 0, "quantity": 1, "key": "fn_speed_value"}
    o.update(kw)
    return o


def run(outputs, stats, seconds, step=0.25):
    """The detection loop's tick: publish() every `step` s for `seconds`."""
    for _ in range(int(round(seconds / step))):
        CLOCK[0] += step
        out.publish(outputs, stats, now=CLOCK[0])


def fresh():
    """Forget every repeat, seen value and pooled socket between sections."""
    out.stop_repeats()
    out._mbw_seen.clear()
    out._mbw_down.clear()
    buses.close_pool()
    buses._rtu_inited.clear()
    buses._rtu_failed.clear()
    buses._conn_status.clear()


# == 1. the core: the connection's switch, saved ==============================

d0 = dc.clean_modbus_client({})
check("a connection with no switch set has no watchdog keys at all (as before)",
      not set(mw.WATCHDOG_KEYS) & set(d0), d0)
d = dc.clean_modbus_client({"watchdog": "off"})
check("off: the defaults are Cytron's map, 105 and 100, 1000 ms, no save",
      (d["watchdog"], d["watchdog_ms"], d["wdt_address"], d["cfg_save_address"],
       d["cfg_save"]) == ("off", 1000, 105, 100, False), d)
check("'Also save to the drive's memory' is stored when ticked",
      dc.clean_modbus_client({"watchdog": "on", "cfg_save": True})["cfg_save"] is True)
d = dc.clean_modbus_client({"watchdog": "ON", "watchdog_ms": 500})
check("on at 500 ms", (d["watchdog"], d["watchdog_ms"]) == ("on", 500), d)
check("100 and 10000 ms are the edges and both are fine",
      dc.clean_modbus_client({"watchdog": "on", "watchdog_ms": 100})["watchdog_ms"] == 100
      and dc.clean_modbus_client({"watchdog": "on",
                                  "watchdog_ms": 10000})["watchdog_ms"] == 10000)
for bad_ms in (0, 50, 150, 10001, "abc", 1000.5):
    check(f"a timeout of {bad_ms!r} ms is refused, not clamped",
          raises(lambda: dc.clean_modbus_client({"watchdog": "on",
                                                 "watchdog_ms": bad_ms})))
for k, v in (("wdt_address", -1), ("wdt_address", 65536),
             ("cfg_save_address", 1.5), ("cfg_save_address", "x")):
    check(f"a register {k}={v!r} is refused",
          raises(lambda: dc.clean_modbus_client({"watchdog": "off", k: v})))
check("the two registers must differ",
      raises(lambda: dc.clean_modbus_client({"watchdog": "off", "wdt_address": 7,
                                             "cfg_save_address": 7})))
check("...edges 0 and 65535 are fine",
      dc.clean_modbus_client({"watchdog": "off", "wdt_address": 0,
                              "cfg_save_address": 65535})["cfg_save_address"] == 65535)
check("a mode other than on/off/not managed is refused",
      raises(lambda: dc.clean_modbus_client({"watchdog": "maybe"})))
try:
    dc.clean_modbus_client({"watchdog": "on", "watchdog_ms": 50})
    msg = ""
except ValueError as exc:
    msg = str(exc)
check("...in plain words, no em dash", "between 100 and 10000" in msg
      and "\u2014" not in msg, msg)
stored = dc.clean_modbus_client({"watchdog": "on", "watchdog_ms": 2000,
                                 "wdt_address": 200, "cfg_save_address": 201})
edit = dc.clean_modbus_client({"name": "Drive"}, stored)
check("an edit that does not name the switch keeps it",
      (edit["watchdog"], edit["watchdog_ms"], edit["wdt_address"])
      == ("on", 2000, 200), edit)
back = dc.clean_modbus_client({"watchdog": ""}, stored)
check("'not managed' clears every switch key",
      not set(mw.WATCHDOG_KEYS) & set(back), back)
check("the cleaner is idempotent", dc.clean_modbus_client(stored) == stored)

# The node-level option of 79152b9b is gone: its fields are ignored.
spec = wf.REGISTRY["output.modbus_write"]
check("the Modbus Write node has no repeat fields any more",
      not {"repeat", "repeat_s"} & set(spec["data"]), spec["data"])


def N(nid, t, **dd):
    return {"id": nid, "type": t, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[t]["data"], **dd}}


def E(s, t, i, th="in", sh=None):
    return {"id": f"e{i}", "source": s, "target": t, "targetHandle": th,
            "sourceHandle": sh}


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return wf.compile_store({"version": 3, "workflows": [
        {"id": "main", "name": "Main", "enabled": True, "graph": g}]}, [], [], [])


old_data = {"name": "speed", "server": "drive", "unit": 1, "fc": 6,
            "address": 0, "quantity": 1, "delay_start": False,
            "start_delay_s": 5}
old = {"id": "mbs", "type": "output.modbus_write", "position": {"x": 0, "y": 0},
       "data": old_data}
inj = N("i1000", "common.inject", fnId="speed_1000", ptype="number",
        payload="1000")
c_old = compile_([inj, old], [E("i1000", "mbs", 1)])
PINNED = {"id": "wf_mbs", "label": "speed → Modbus Write",
          "type": "modbus_write", "enabled": True,
          "key": "fn_speed_1000_value", "server": "drive", "unit": 1, "fc": 6,
          "address": 0, "quantity": 1, "delay_start": False,
          "start_delay_s": 0.0, "trigger": "any", "mode": "fire",
          "sources": [{"fired": "fn_speed_1000_fired",
                       "key": "fn_speed_1000_value"}]}
doc_old = (c_old["outputs"] or [{}])[0]
check("a Modbus Write compiles exactly as before (pinned)",
      not c_old["errors"] and {k: doc_old.get(k) for k in PINNED} == PINNED
      and "repeat" not in doc_old, (c_old["errors"], doc_old))
for extra in ({"repeat": True, "repeat_s": 2}, {"repeat": True, "repeat_s": 0.01},
              {"repeat": False, "repeat_s": 1}):
    c_x = compile_([inj, dict(old, data=dict(old_data, **extra))],
                   [E("i1000", "mbs", 1)])
    check(f"...and so does one saved with 79152b9b's {extra} (ignored)",
          not c_x["errors"] and c_x["outputs"] == c_old["outputs"],
          (c_x["errors"], c_x["outputs"]))

# == 2. the connect-time writes ===============================================

fresh()
connect_as()
LOG.clear()
o1 = node("wf_one")
run([o1], {"fn_speed_value": 1000}, 5)
check("NOT SET: no connect-time write, and 1000 is written ONCE",
      wire() == [(0, 1000, 1)], wire())
check("...the app's config for it has no on_connect at all",
      "on_connect" not in buses.client_config(buses.client("drive")))
fresh()

connect_as(watchdog="off", watchdog_ms=1000, wdt_address=105, cfg_save_address=100)
LOG.clear()
run([o1], {"fn_speed_value": 1000}, 5)
check("OFF: on connect WDT = 0 (and no CFG_SAVE), then the user's 1000, once",
      [e[0] for e in LOG][:1] == ["connect"]
      and wire() == [(105, 0, 1), (0, 1000, 1)], (LOG, wire()))
mark = len(LOG)
CLOCK[0] += 0.25
out.publish([o1], {"fn_speed_value": 0}, now=CLOCK[0])
check("...the next write on the same socket is just the write",
      wire(mark) == [(0, 0, 1)], wire(mark))
check("...and the connection says the setup is done",
      (buses.connect_status("drive") or {}).get("ok") is True,
      buses.connect_status("drive"))
# The socket drops (the drive rebooted): the next transaction reconnects and
# the setup runs again, before the write.
FAIL["on"] = True
mark = len(LOG)
CLOCK[0] += 0.25
out.publish([o1], {"fn_speed_value": 500}, now=CLOCK[0])
FAIL["on"] = False
mark = len(LOG)
CLOCK[0] += 0.25
out.publish([o1], {"fn_speed_value": 600}, now=CLOCK[0])
check("after a reconnect the setup runs again, in order, before the write",
      [e[0] for e in LOG[mark:]][:1] == ["connect"]
      and wire(mark) == [(105, 0, 1), (0, 600, 1)], LOG[mark:])
fresh()

connect_as(watchdog="on", watchdog_ms=1000, unit_id=3)
LOG.clear()
o5 = node("wf_u5", unit=5)
CLOCK[0] += 0.25
out.publish([o5], {"fn_speed_value": 1000}, now=CLOCK[0])
check("ON at 1000 ms: WDT = 10 on the CONNECTION's unit (3), "
      "then the node's write on its own unit (5)",
      wire() == [(105, 10, 3), (0, 1000, 5)], wire())
out.stop_repeats()
fresh()

connect_as(watchdog="off", wdt_address=200, cfg_save_address=201)
LOG.clear()
CLOCK[0] += 0.25
out.publish([o1], {"fn_speed_value": 7}, now=CLOCK[0])
check("Advanced: other register numbers are used as given",
      wire() == [(200, 0, 1), (0, 7, 1)], wire())
# Changing the switch while the socket is up runs the new setup at once.
connect_as(watchdog="on", watchdog_ms=3000, wdt_address=200, cfg_save_address=201)
mark = len(LOG)
CLOCK[0] += 0.25
out.publish([o1], {"fn_speed_value": 8}, now=CLOCK[0])
check("switching it on with the socket up: the new setup goes before the "
      "next write, no reconnect needed",
      "connect" not in [e[0] for e in LOG[mark:]]
      and wire(mark) == [(200, 30, 1), (0, 8, 1)], LOG[mark:])
out.stop_repeats()
fresh()

# A Modbus Read that opens the connection runs the setup too.
connect_as(watchdog="off")
LOG.clear()
cfg = buses.client_config(buses.client("drive"))
rows = buses.modbus_read(cfg, 5, 1)
check("a READ that opens the socket writes the setup first, then reads",
      rows == [7] and [(e[0], e[1]) for e in LOG if e[0] != "connect"]
      == [("write", 105), ("read", 5)], LOG)
fresh()

# RTU: no connection to keep; once before the port's first transaction.
buses.set_clients([{"id": "rtu", "type": "serial", "serial_port": "/dev/ttyACM0",
                    "baudrate": 9600, "unit_id": 2, "timeout_ms": 500,
                    "watchdog": "off"}])
LOG.clear()
o_r = node("wf_rtu", server="rtu", unit="")
CLOCK[0] += 0.25
out.publish([o_r], {"fn_speed_value": 1}, now=CLOCK[0])
CLOCK[0] += 0.5
out.publish([o_r], {"fn_speed_value": 2}, now=CLOCK[0])
check("RTU: the setup once before the first transaction, not again after",
      wire() == [(105, 0, 2), (0, 1, 2), (0, 2, 2)], wire())
check("...and the port is closed after each transaction (not held open)",
      [e[0] for e in LOG].count("close") == 2, LOG)
fresh()

check("by default CFG_SAVE (register 100 / 201) was never written above",
      SAVES == [], SAVES)

# "Also save to the drive's memory": once per changed value, remembered.
import json                                                    # noqa: E402

state_dir = tempfile.mkdtemp()
buses.set_state_dir(state_dir)
connect_as(watchdog="on", watchdog_ms=1000, cfg_save=True)
cfg_s = buses.client_config(buses.client("drive"))
LOG.clear()
buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
check("ticked: the first connect writes WDT = 10, then CFG_SAVE = 1, then the write",
      wire() == [(105, 10, 1), (100, 1, 1), (0, 1, 1)], wire())
saved_file = os.path.join(state_dir, buses.SAVED_FILE)
check("...and the saved value is remembered in the app's data dir",
      os.path.exists(saved_file)
      and list(json.load(open(saved_file, encoding="utf-8")).values()) == [10],
      open(saved_file, encoding="utf-8").read() if os.path.exists(saved_file) else "")
for _ in range(3):                      # a flaky link: three reconnects
    buses.close_pool()
    buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
check("three reconnects with the same value: WDT each time, CFG_SAVE never again",
      [a for a, _, _ in wire()].count(105) == 4
      and [a for a, _, _ in wire()].count(100) == 1, wire())
buses._saved.clear()                    # the app restarts ...
buses.set_state_dir(state_dir)          # ... and reads the record back
buses.close_pool()
mark = len(LOG)
buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
check("...nor after an app restart (the record survives it)",
      wire(mark) == [(105, 10, 1), (0, 1, 1)], wire(mark))
connect_as(watchdog="on", watchdog_ms=2000, cfg_save=True)
cfg_s = buses.client_config(buses.client("drive"))
mark = len(LOG)
buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
buses.close_pool()
buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
check("the timeout changed: ONE more save (WDT = 20, CFG_SAVE), none on the "
      "reconnect after it",
      wire(mark) == [(105, 20, 1), (100, 1, 1), (0, 1, 1), (105, 20, 1), (0, 1, 1)],
      wire(mark))
connect_as(watchdog="off", cfg_save=True)
cfg_s = buses.client_config(buses.client("drive"))
FAIL["refuse"] = {100}
mark = len(LOG)
ok, _ = buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
check("switched off, the drive refuses the save: the write still goes, the "
      "connection says why",
      ok and wire(mark) == [(105, 0, 1), (100, 1, 1), (0, 1, 1)]
      and "writing 1 to 100" in (buses.connect_status("drive") or {}).get("message", ""),
      (wire(mark), buses.connect_status("drive")))
FAIL["refuse"] = set()
buses.close_pool()
mark = len(LOG)
buses.modbus_write_fc(cfg_s, 6, 0, [1], 1)
check("...a refused save is not remembered: the next connect saves again",
      wire(mark) == [(105, 0, 1), (100, 1, 1), (0, 1, 1)], wire(mark))
buses.set_state_dir("")
fresh()

# == 3. failures: logged once, shown, never a loop, never blocking forever ===

records = []


class _Grab(logging.Handler):
    def emit(self, record):
        records.append(record)


grab = _Grab()
blog = logging.getLogger("app.buses")
blog.addHandler(grab)
blog.setLevel(logging.DEBUG)

connect_as(watchdog="off")
FAIL["refuse"] = {105}
LOG.clear()
o_f = node("wf_f")
for v in (1, 2, 3):
    CLOCK[0] += 0.5
    out.publish([o_f], {"fn_speed_value": v}, now=CLOCK[0])
check("a drive that REFUSES the register: the user's writes still go",
      [v for v, _ in writes()] == [1, 2, 3], wire())
check("...the setup is tried once per connect, not before every write",
      [a for a, _, _ in wire()].count(105) == 1, wire())
check("...the failure is logged once",
      len([r for r in records if "drive watchdog setup" in r.getMessage()]) == 1,
      [r.getMessage() for r in records])
st = buses.connect_status("drive") or {}
tele = out.telemetry([o_f])
check("...and shown: on the connection's status and on the node",
      st.get("ok") is False and "105" in st.get("message", "")
      and "drive watchdog not set: writing 0 to 105" in tele.get("out_wf_f_note", ""),
      (st, tele))
FAIL["refuse"] = set()
buses.close_pool()                                     # the next connect
mark = len(LOG)
CLOCK[0] += 0.5
out.publish([o_f], {"fn_speed_value": 4}, now=CLOCK[0])
check("the next connect tries again; fixed, the status is good and the note "
      "loses the complaint",
      wire(mark) == [(105, 0, 1), (0, 4, 1)]
      and buses.connect_status("drive")["ok"] is True
      and "watchdog" not in out.telemetry([o_f]).get("out_wf_f_note", ""),
      (wire(mark), out.telemetry([o_f])))
fresh()

# A drive that does not answer the setup: the transaction fails (no second
# timeout for the user's write), the socket is dropped, the CFG_SAVE row is
# not even tried, and the next connect tries again.
connect_as(watchdog="off")
FAIL["silent"] = {105}
LOG.clear()
ok, message = buses.modbus_write_fc(buses.client_config(buses.client("drive")),
                                    6, 0, [1000], 1)
check("no answer to the setup: the transaction fails at once with the reason "
      "(no second timeout for the user's write)",
      not ok and message.startswith("drive watchdog setup: writing 0 to 105")
      and wire() == [(105, 0, 1)], (ok, message, wire()))
check("...and the socket is closed so the next try reconnects",
      [e[0] for e in LOG] == ["connect", "write", "close"], LOG)
FAIL["silent"] = set()
mark = len(LOG)
ok, _ = buses.modbus_write_fc(buses.client_config(buses.client("drive")),
                              6, 0, [1000], 1)
check("...which runs the setup again and then the write",
      ok and wire(mark) == [(105, 0, 1), (0, 1000, 1)], wire(mark))
fresh()

# RTU with no answer: not asked on every transaction -- once per RTU_RETRY_S.
buses.set_clients([{"id": "rtu", "type": "serial", "serial_port": "/dev/ttyACM0",
                    "unit_id": 2, "watchdog": "off"}])
real_time = buses.time
buses.time = types.SimpleNamespace(time=lambda: CLOCK[0])
try:
    cfg_r = buses.client_config(buses.client("rtu"))
    FAIL["silent"] = {105}
    LOG.clear()
    buses.modbus_write_fc(cfg_r, 6, 0, [1], 2)
    CLOCK[0] += 1
    buses.modbus_write_fc(cfg_r, 6, 0, [2], 2)
    check("RTU, no answer: the next transaction within 10 s skips the setup",
          wire() == [(105, 0, 2), (0, 2, 2)], wire())
    FAIL["silent"] = set()
    CLOCK[0] += buses.RTU_RETRY_S
    mark = len(LOG)
    buses.modbus_write_fc(cfg_r, 6, 0, [3], 2)
    check("...and tries again after it",
          wire(mark) == [(105, 0, 2), (0, 3, 2)], wire(mark))
finally:
    buses.time = real_time
blog.removeHandler(grab)
fresh()

# == 4. ON: every write on the connection keeps sending at half the timeout ===

connect_as(watchdog="on", watchdog_ms=2000)
LOG.clear()
CLOCK[0] = 2000.0
o_on = node("wf_on")
run([o_on], {}, 3)
check("ON, nothing on the wire yet: nothing is invented (the setup is not "
      "even sent: no transaction, no connect)", LOG == [], LOG)
run([o_on], {"fn_speed_value": 1000}, 5)
w = writes()
check("ON at 2000 ms: 1000 is written, then again every second (half of it)",
      [v for v, _ in w] == [1000] * 5
      and [round(t - w[0][1], 2) for _, t in w] == [0, 1, 2, 3, 4], w)
check("...on the one pooled socket, the setup once at its opening",
      [e[0] for e in LOG].count("connect") == 1
      and wire()[:1] == [(105, 20, 1)], LOG)
tele = out.telemetry([o_on])
check("the node says it: 'repeats 1000 every 1s (drive watchdog on)'",
      tele.get("out_wf_on_note") == "wrote 1000 to 0 (FC 6, unit 1); "
                                    "repeats 1000 every 1s (drive watchdog on)", tele)
mark = len(LOG)
CLOCK[0] += 0.5
out.publish([o_on], {"fn_speed_value": 500}, now=CLOCK[0])
run([o_on], {"fn_speed_value": 500}, 3)
w = writes(mark)
check("a different value is written at once, and THAT one is repeated after",
      [v for v, _ in w] == [500, 500, 500, 500]
      and [round(t - w[0][1], 2) for _, t in w] == [0, 1, 2, 3], w)
mark = len(LOG)
CLOCK[0] += 0.25
out.publish([o_on], {"fn_speed_value": 0}, now=CLOCK[0])
run([o_on], {"fn_speed_value": 0}, 3)
check("0 is repeated too (the stop command kept up)",
      [v for v, _ in writes(mark)] == [0, 0, 0, 0], writes(mark))
mark = len(LOG)
run([dict(o_on, address=9)], {"fn_speed_value": 0}, 3)
check("an edited address stops the old repeat; the new register gets a real "
      "write and only then its own repeats",
      [(a, v) for a, v, _ in wire(mark)] == [(9, 0)] * 3, wire(mark))
connect_as(watchdog="off")
mark = len(LOG)
run([dict(o_on, address=9)], {"fn_speed_value": 0}, 3)
check("switching the connection OFF stops the repeats (its setup goes on the "
      "next transaction only)", writes(mark) == [], wire(mark))
fresh()

# Not set and off: Once, as before (section 2 pinned the writes).
for mode in ("", "off"):
    connect_as(watchdog=mode)
    LOG.clear()
    run([node("wf_once")], {"fn_speed_value": 1000}, 4)
    check(f"switch {mode or 'not set'}: one write of 1000 in four seconds",
          writes() == [(1000, writes()[0][1])] if writes() else False, wire())
    fresh()

# Fire mode (Injects) and trigger mode repeat what they wrote, too.
connect_as(watchdog="on", watchdog_ms=2000)
fire = node("wf_fire", mode="fire", key="fn_i1_value",
            sources=[{"fired": "fn_i1_fired", "key": "fn_i1_value"}])
mark = len(LOG)
CLOCK[0] += 0.25
out.publish([fire], {"fn_i1_fired": 1, "fn_i1_value": 1000}, now=CLOCK[0])
run([fire], {"fn_i1_fired": 0, "fn_i1_value": 1000}, 3)
check("an Inject press (fire mode) is repeated after it",
      [v for v, _ in writes(mark)] == [1000] * 4, writes(mark))
out._repeat_stop("wf_fire")
trig = node("wf_trig", mode="trigger", trigger="alert", function="go")
mark = len(LOG)
out.fire([trig], {"type": "alert", "detail": {"function": "go"}},
         stats={"fn_speed_value": 300})
out._mbw_repeat["wf_trig"]["at"] = CLOCK[0]     # fire() uses the wall clock
out._mbw_repeat["wf_trig"]["live"] = CLOCK[0]
run([trig], {"fn_speed_value": 300}, 3)
check("a triggered write is repeated after it fired",
      [v for v, _ in writes(mark)] == [300] * 4, writes(mark))
fresh()

# == 5. 100 ms: 0.05 s between the loop's ticks (the pump), and its lease ====

connect_as(watchdog="on", watchdog_ms=100)
check("ON at 100 ms: every write keeps sending every 0.05 s",
      out._repeat_every(node("x")) == 0.05)
o_f5 = node("wf_fast")
LOG.clear()
CLOCK[0] = 3000.0
out.publish([o_f5], {"fn_speed_value": 1000}, now=CLOCK[0])
start = CLOCK[0]
for _ in range(8):                      # two seconds of AI-off ticks (0.25 s)
    for _ in range(25):                 # the pump between them, every 0.01 s
        CLOCK[0] = round(CLOCK[0] + 0.01, 4)
        out._repeat_pump_once(CLOCK[0])
    out.publish([o_f5], {"fn_speed_value": 1000}, now=CLOCK[0])
w = writes()
gaps = sorted({round(b[1] - a[1], 2) for a, b in zip(w, w[1:])})
check("the pump sends on the 0.05 s beat between 0.25 s ticks (41 in 2 s)",
      len(w) == 41 and gaps == [0.05], (len(w), gaps))
mark = len(LOG)
for _ in range(400):                    # the loop STOPS ticking; the pump runs on
    CLOCK[0] = round(CLOCK[0] + 0.01, 4)
    out._repeat_pump_once(CLOCK[0])
w = writes(mark)
check("a loop that stops vouching: the pump stops within the 2 s lease",
      w and w[-1][1] - (CLOCK[0] - 4.0) <= out.REPEAT_LEASE_S + 0.05
      and len(w) <= 41, (len(w), w[-1:] if w else None))
fresh()

# A failing 0.05 s repeat backs off to once a second and is logged once.
records = []
olog = logging.getLogger("app.outputs")
olog.addHandler(grab)
olog.setLevel(logging.DEBUG)
connect_as(watchdog="on", watchdog_ms=100)
o_dn = node("wf_dn")
CLOCK[0] = 4000.0
out.publish([o_dn], {"fn_speed_value": 1000}, now=CLOCK[0])
FAIL["on"] = True
mark = len(LOG)
for _ in range(16):                     # four seconds
    for _ in range(25):
        CLOCK[0] = round(CLOCK[0] + 0.01, 4)
        out._repeat_pump_once(CLOCK[0])
    out.publish([o_dn], {"fn_speed_value": 1000}, now=CLOCK[0])
tries = len([e for e in LOG[mark:] if e[0] == "write" and e[1] == 105])
check("the drive down for 4 s at 0.05 s: tried about once a second (a try and "
      "its one retry), not 80 times", 3 <= tries <= 10, tries)
check("...and logged once for the outage",
      len([r for r in records if "modbus write wf_dn failed" in r.getMessage()]) == 1,
      [r.getMessage() for r in records])
FAIL["on"] = False
run([o_dn], {"fn_speed_value": 1000}, 2)
check("back: the repeats land again and the node is good",
      out.telemetry([o_dn])["out_wf_dn_ok"] == 1)
olog.removeHandler(grab)
fresh()

# == 6. the app stops ========================================================

connect_as(watchdog="on", watchdog_ms=2000)
o_st = node("wf_st")
run([o_st], {"fn_speed_value": 1000}, 1)
out._mbw_jobs.setdefault("tcp://192.168.4.4:502", {})["wf_st"] = {"repeat": True}
out.stop_repeats()
check("stopping the app forgets every repeat and drops a queued one",
      not out._mbw_repeat and "wf_st" not in out._mbw_jobs["tcp://192.168.4.4:502"],
      (out._mbw_repeat, out._mbw_jobs))
mark = len(LOG)
run([o_st], {"fn_speed_value": 1000}, 4)
check("...and nothing is repeated after it", writes(mark) == [], writes(mark))
run_src = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
               encoding="utf-8").read()
check("run.py's exit path calls stop_repeats()",
      "outputs_mod.stop_repeats()" in run_src)
fresh()

# == 7. held back: AI off, Class Filter, a lost camera ========================

connect_as(watchdog="on", watchdog_ms=2000)
g_nodes = [
    N("ik", "common.inject", fnId="kick", ptype="number", payload="1000"),
    N("mk", "output.modbus_write", server="drive", address=0, fc=6, unit=1,
      name="speed"),
    N("src", "source.detection"),
    N("fr", "analytics.frame", fnId="crowd", name="Crowd"),
    N("md", "output.modbus_write", server="drive", address=1, fc=6, unit=1,
      name="lamp"),
]
c_ai = compile_(g_nodes, [E("ik", "mk", 1), E("src", "fr", 2, th=None),
                          E("fr", "md", 3)])
cfg = {"functions": c_ai["functions"], "outputs": c_ai["outputs"],
       "logic": c_ai["frames_free"], "ai": {"on": True}}
on_outs = apprun.class_pause(apprun.ai_config(copy.deepcopy(cfg)))["outputs"]
off_outs = apprun.class_pause(apprun.ai_config(dict(copy.deepcopy(cfg),
                                                    ai={"on": False})))["outputs"]
check("compiled: with AI off only the Inject-fed write runs",
      not c_ai["errors"] and sorted(o["id"] for o in on_outs) == ["wf_md", "wf_mk"]
      and [o["id"] for o in off_outs] == ["wf_mk"],
      (c_ai["errors"], [o["id"] for o in on_outs], [o["id"] for o in off_outs]))
mk = next(o for o in on_outs if o["id"] == "wf_mk")
md = next(o for o in on_outs if o["id"] == "wf_md")
stats = {mk["sources"][0]["key"]: 1000, mk["sources"][0]["fired"]: 1,
         md["key"]: 3}
mark = len(LOG)
CLOCK[0] += 0.25
out.publish(on_outs, stats, now=CLOCK[0])
stats[mk["sources"][0]["fired"]] = 0
run(on_outs, stats, 2)
got = [(a, v) for a, v, _ in wire(mark) if a not in INIT]
check("AI on: both repeat", sorted(set(got)) == [(0, 1000), (1, 3)]
      and len(got) == 6, got)
mark = len(LOG)
run(off_outs, {k: v for k, v in stats.items() if k != md["key"]}, 3)
got = [(a, v) for a, v, _ in wire(mark)]
check("AI off: the Inject-fed write KEEPS repeating its last value",
      got == [(0, 1000)] * 3, got)
check("...the detection-fed one stops, and its node says so",
      "wf_md" not in out._mbw_repeat
      and "stopped repeating: this node is held back"
      in out.telemetry(on_outs).get("out_wf_md_note", ""),
      out.telemetry(on_outs))
cfg_p = dict(copy.deepcopy(cfg), class_filters={
    "paused": ["cf"], "nodes": [{"node": "cf", "functions": [],
                                 "outputs": ["wf_md"]}]})
run(on_outs, stats, 1)
paused = apprun.class_pause(apprun.ai_config(cfg_p))["outputs"]
run(paused, stats, 1)
check("a paused Class Filter stops the repeat behind it the same way",
      "wf_md" not in out._mbw_repeat and "wf_mk" in out._mbw_repeat,
      sorted(out._mbw_repeat))
o_cam = node("wf_cam", key="fn_crowd_count")
run([o_cam], {"fn_crowd_count": 2}, 1)
mark = len(LOG)
run([o_cam], {}, 3)
check("a lost camera (value held back): the repeat stops, and says so",
      writes(mark) == [] and "wf_cam" not in out._mbw_repeat
      and "its value is held back" in out.telemetry([o_cam])["out_wf_cam_note"],
      (writes(mark), out.telemetry([o_cam])))
fresh()

# == 8. real threads: the worker, the pump, and a Read on the same server ====
#
# Each transaction costs 20 ms. A node keeps sending at 0.05 s (100 ms
# watchdog) and a reader asks the same server back to back for 2 s. The
# ticket lock means the read waits for at most the one transaction in front
# of it; the repeats keep their beat.

out._MBW_INLINE = False
connect_as(watchdog="on", watchdog_ms=100)
FAIL["cost"] = 0.02
o_q = node("wf_q")
stop = threading.Event()
waits = []


def reader():
    cfg_q = buses.client_config(buses.client("drive"))
    key = buses.bus_key(cfg_q)
    while not stop.is_set():
        t0 = time.time()
        with buses.bus_lock(key):
            waits.append(time.time() - t0)
            buses.modbus_read(cfg_q, 1, 1)
        time.sleep(0.005)


mark = len(LOG)
th = threading.Thread(target=reader, daemon=True)
th.start()
t_end = time.time() + 2.0
while time.time() < t_end:
    CLOCK[0] = time.time()
    out.publish([o_q], {"fn_speed_value": 42}, now=CLOCK[0])
    time.sleep(0.25)                    # the AI-off tick
stop.set()
th.join(2)
out.stop_repeats()
time.sleep(0.2)
FAIL["cost"] = 0.0
n = len(writes(mark))
check("real threads at 0.05 s with 20 ms transactions: the writes keep a "
      "beat far faster than the 0.25 s tick (>= 16 in 2 s)",
      n >= 16 and all(v == 42 for v, _ in writes(mark)), n)
check("...the read on the same server kept getting its turn (>= 20 reads)",
      len(waits) >= 20, len(waits))
check("...and never waited longer than about one transaction (< 80 ms)",
      waits and max(waits) < 0.08, max(waits) if waits else None)
print(f"     measured: {n} writes, {len(waits)} reads in 2 s; read wait max "
      f"{max(waits) * 1000:.0f} ms")
out._MBW_INLINE = True

# The lock itself: three threads that each want it 50 times take turns.
lk = buses.FairLock()
order = []


gate = threading.Barrier(3)


def taker(name):
    gate.wait()
    for _ in range(50):
        with lk:
            order.append(name)
            time.sleep(0.0005)


ts = [threading.Thread(target=taker, args=(c,)) for c in "abc"]
for t in ts:
    t.start()
for t in ts:
    t.join()
longest = max(len(list(g)) for _, g in
              __import__("itertools").groupby(order[10:-10]))
check("FairLock: no thread holds it twice in a row while others wait "
      "(longest run <= 2)", len(order) == 150 and longest <= 2, longest)

check("CFG_SAVE was written only in the 'Also save' section, 4 times in all",
      len(SAVES) == 4, SAVES)

print()
print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
