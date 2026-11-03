# Modbus Write, and the Modbus Server connection it shares with the Read node.
#
# Asked for as Node-RED's node-red-contrib-modbus pair: a "Modbus Write" node
# (FC 5 / 6 / 15 / 16) and a named, SHARED "Modbus Server" connection -- the
# modbus-client config node -- that the existing RS485 Read uses too. For dev
# AND customer boxes: nothing about either is developer-gated.
#
# What is pinned, and why each one is the part that bites:
#
#   * the connection's defaults and clamps -- /dev/ttyAMA10 and 127.0.0.1:502
#     are what the operator's Node-RED flows already assume, and a serial port
#     string goes straight to the serial driver, so only a tty or COM port is
#     accepted;
#   * a server a node still picks cannot be deleted -- the node would turn
#     into an error about something that is simply gone;
#   * the four FC labels are Node-RED's, exactly, because the people wiring
#     this node read them off Node-RED screenshots;
#   * the runtime calls the RIGHT pymodbus method with the right address,
#     values and slave -- a wrong one writes a different register on
#     somebody's machine;
#   * the write is on change, not per frame (30 writes a second to a PLC is a
#     bus flood), and never before "Delay to activate input" has passed;
#   * the serial lock is ONE object shared with the Read poller: two masters
#     on one RS-485 pair corrupt each other's frames;
#   * failures land in the node's status, where the canvas can show them.
#
# Also pinned here, because it was asked for in the same breath: MQTT, HTTP,
# API and Modbus are one INTEGRATION drawer with a sub-group each, visible to
# a master on a customer box, and moving them changed placement only.
#
#     py -3 tests/smoke_modbuswrite.py
import ast
import io
import json
import os
import re
import sys
import tempfile
import threading
import time
import tomllib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import buses                                          # noqa: E402
from app import functions as app_fns                           # noqa: E402
from app import outputs as out                                 # noqa: E402
from core.services import caps                                 # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from core.services import functions as fn_service              # noqa: E402
from core.services import workflow as wf                       # noqa: E402

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


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# == 1. the connection: defaults, fixed types, clamps ==========================

d = dc.clean_modbus_client({})
check("a new server is TCP at 127.0.0.1:502",
      (d["type"], d["host"], d["port"]) == ("tcp", "127.0.0.1", 502), d)
check("...its serial side defaults to /dev/ttyACM0 (the built-in RS485), 9600 8N1",
      (d["serial_port"], d["baudrate"], d["databits"], d["stopbits"],
       d["parity"]) == ("/dev/ttyACM0", 9600, 8, 1, "none"), d)
check("...Unit-Id 1, Timeout 1000 ms, reconnect on, after 2000 ms",
      (d["unit_id"], d["timeout_ms"], d["reconnect_on_timeout"],
       d["reconnect_timeout_ms"]) == (1, 1000, True, 2000), d)
d = dc.clean_modbus_client({"tcp_type": "TELNET", "serial_type": "ASCII"})
check("TCP Type is always DEFAULT and Serial Type always RTU",
      (d["tcp_type"], d["serial_type"]) == ("DEFAULT", "RTU"), d)
d = dc.clean_modbus_client({"port": 70000, "unit_id": 300, "timeout_ms": 5,
                            "reconnect_timeout_ms": 999999})
check("ranges are clamped: port 65535, Unit-Id 255, timeouts 100..60000",
      (d["port"], d["unit_id"], d["timeout_ms"], d["reconnect_timeout_ms"])
      == (65535, 255, 100, 60000), d)
d = dc.clean_modbus_client({"port": 0, "unit_id": -4})
check("...from below too: port 1, Unit-Id 0", (d["port"], d["unit_id"]) == (1, 0), d)
check("a serial port that is not a tty or COM port is refused",
      raises(lambda: dc.clean_modbus_client({"serial_port": "/etc/passwd"}))
      and raises(lambda: dc.clean_modbus_client({"serial_port": "/dev/tty0; rm"})))
check("...while /dev/ttyUSB0 and COM3 are fine",
      dc.clean_modbus_client({"serial_port": "/dev/ttyUSB0"})["serial_port"] == "/dev/ttyUSB0"
      and dc.clean_modbus_client({"serial_port": "COM3"})["serial_port"] == "COM3")
check("parity accepts N/E/O and none/even/odd, refuses anything else",
      dc.clean_modbus_client({"parity": "E"})["parity"] == "even"
      and raises(lambda: dc.clean_modbus_client({"parity": "mark"})))
check("type is TCP or Serial, nothing else",
      raises(lambda: dc.clean_modbus_client({"type": "udp"})))
check("a port that is not a number is refused, not guessed",
      raises(lambda: dc.clean_modbus_client({"port": "abc"})))

# == 2. list / save / delete, and a server in use cannot go ====================

systems = tempfile.mkdtemp()
slug = "mbsys"
os.makedirs(os.path.join(systems, slug))
open(os.path.join(systems, slug, "config.toml"), "w").close()

a = dc.save_modbus_client(systems, slug, {"name": "Line PLC", "host": "10.0.0.5"})
b = dc.save_modbus_client(systems, slug, {"type": "serial",
                                          "serial_port": "/dev/ttyAMA10"})
check("saving without an id adds a server with an id of its own",
      a["id"].startswith("mb_") and b["id"].startswith("mb_") and a["id"] != b["id"], (a, b))
listed = dc.list_modbus_clients(systems, slug)
check("both are listed, with picker labels",
      [c["label"] for c in listed] == ["Line PLC", "/dev/ttyAMA10 @ 9600"], listed)
with open(os.path.join(systems, slug, "config.local.toml"), "rb") as f:
    stored = tomllib.load(f)
check("stored as [[modbus_clients]] in config.local.toml",
      [r["id"] for r in stored.get("modbus_clients", [])] == [a["id"], b["id"]], stored)
edited = dc.save_modbus_client(systems, slug, {"id": a["id"], "port": 1502})
check("saving with an id changes only what it names",
      edited["port"] == 1502 and edited["host"] == "10.0.0.5"
      and edited["name"] == "Line PLC", edited)
check("an unknown id is not silently created",
      raises(lambda: dc.save_modbus_client(systems, slug, {"id": "mb_nope"}),
             LookupError))

wf.save_store(slug, {"version": 3, "workflows": [{"id": "main", "name": "Main",
    "graph": {"nodes": [
        {"id": "w1", "type": "output.modbus_write", "position": {"x": 0, "y": 0},
         "data": {"name": "Lamp", "server": a["id"]}},
        {"id": "r1", "type": "variable.rs485", "position": {"x": 0, "y": 0},
         "data": {"name": "Flow", "server": a["id"]}}],
        "edges": []}}]})
try:
    dc.delete_modbus_client(systems, slug, a["id"])
    refused = ""
except ValueError as exc:
    refused = str(exc)
check("a server picked by nodes is NOT deleted, and says which",
      "Lamp" in refused and "Flow" in refused and "2 nodes" in refused, refused)
check("...and is still there", any(c["id"] == a["id"]
                                   for c in dc.list_modbus_clients(systems, slug)))
check("an unused server deletes", dc.delete_modbus_client(systems, slug, b["id"]) is True
      and [c["id"] for c in dc.list_modbus_clients(systems, slug)] == [a["id"]])
check("...and deleting it twice says there is no such server",
      raises(lambda: dc.delete_modbus_client(systems, slug, b["id"]), LookupError))

# == 3. routes: who may read, who may write ====================================

models = read("core", "routes", "models.py")
mt = ast.parse(models)


def mfn(name):
    for node in ast.walk(mt):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return ast.get_source_segment(models, node)
    raise AssertionError(f"{name} is gone from core/routes/models.py")


lst = mfn("detector_list_modbus_clients")
sav = mfn("detector_save_modbus_client")
dele = mfn("detector_delete_modbus_client")
check("GET /modbus/clients is an OPERATOR read",
      '"/api/detector/control/modbus/clients"' in models
      and "_require_operator(request, slug," in lst and "perm.require" not in lst)
check("POST and DELETE ask for modbus.edit, the fieldbus capability",
      'perm.require(request, "modbus.edit")' in sav
      and 'perm.require(request, "modbus.edit")' in dele)
check("...a capability the catalogue actually has", "modbus.edit" in caps.ALL_CAPS)
perm_src = read("core", "routes", "perm.py")
check("...and MASTER holds it by tier (perm.require returns for is_admin)",
      re.search(r"if auth_service\.is_admin\(user, cfg\):\s+return", perm_src) is not None)
check("a node still using a server makes DELETE a 409, not a 500",
      "status_code=409" in dele)
check("nothing about the servers is developer-gated",
      not any("is_developer" in src for src in (lst, sav, dele)))

# == 4. the registry: the node, its FC list, its place =========================

spec = wf.REGISTRY.get("output.modbus_write") or {}
check("output.modbus_write is in the registry as Modbus Write",
      spec.get("label") == "Modbus Write" and spec.get("output_type") == "modbus_write"
      and spec.get("category") == "output", spec)
check("...not deprecated, not hidden, not a developer node",
      not spec.get("deprecated") and not spec.get("hidden") and not spec.get("python"))
check("...defaults: FC 6, Address blank (required), Quantity 1, no server",
      spec["data"]["fc"] == 6 and spec["data"]["address"] == ""
      and spec["data"]["quantity"] == 1 and spec["data"]["server"] == ""
      and spec["data"]["delay_start"] is False, spec["data"])
page = read("frontend", "src", "pages", "Workflow.jsx")
block = page.split('{node.type === "output.modbus_write" && (<>', 1)[1] \
    .split('{node.type === "output.rs485" && (<>', 1)[0]
fcs = re.findall(r"<option value=\{(\d+)\}>([^<]+)</option>", block)
check("the FC select offers EXACTLY Node-RED's four",
      fcs == [("5", "FC 5: Force Single Coil"), ("6", "FC 6: Preset Single Register"),
              ("15", "FC 15: Force Multiple Coils"),
              ("16", "FC 16: Preset Multiple Registers")], fcs)
for label in ("Name", "Unit-Id", "FC", "Address", "Quantity", "Server"):
    check(f"the write inspector has a {label!r} field", f'label="{label}"' in block)
check("...'Delay to activate input', and Quantity's 1-65535 placeholder",
      "Delay to activate input" in block and 'placeholder="1-65535"' in block)
check("...with the red outline on Address, Quantity and Server when empty",
      block.count("REQUIRED_EMPTY") >= 2 and "<ModbusServerRow" in block)
editor = read("frontend", "src", "components", "ModbusServerEditor.jsx")
for label in ("Name", "Type", "Host", "Port", "TCP Type", "Serial port",
              "Serial Type", "Baud rate", "Data bits", "Stop bits", "Parity",
              "Unit-Id", "Timeout (ms)", "Reconnect timeout (ms)"):
    check(f"the server editor labels {label!r} as Node-RED does",
          f'label="{label}"' in editor)
check("...'Reconnect on timeout' is a checkbox",
      "Reconnect on timeout</span>" in editor and "reconnect_on_timeout" in editor)
check("the Server row is select + pencil + plus with 'none' first",
      '<option value="">none</option>' in editor and "<Pencil" in editor
      and "<Plus" in editor)

keys = list(wf.REGISTRY)
check("Modbus Write sits beside the read node in the palette",
      wf.REGISTRY["variable.rs485"]["palette"] == spec["palette"]
      == {"cat": "integration", "group": "Modbus"}
      and keys.index("output.modbus_write") == keys.index("variable.rs485") + 1,
      (wf.REGISTRY["variable.rs485"]["palette"], spec.get("palette")))
reg = wf.REGISTRY["output.modbus_reg"]
check("Modbus Register is retired from the palette (deprecated), like output.rs485",
      reg.get("deprecated") is True and wf.REGISTRY["output.rs485"].get("deprecated"))

# == 5. the Integration drawer ================================================

def js_list(src, name):
    body = src.split(f"const {name} = [", 1)[1].split("];", 1)[0]
    return [m.group(1) for m in re.finditer(r'"([^"]+)"', body)]


FAMILY = {"output.mqtt_publish": "MQTT", "variable.mqtt": "MQTT",
          "output.webhook_publish": "HTTP", "output.webhook": "HTTP",
          "variable.api": "API",
          "output.modbus_write": "Modbus", "variable.rs485": "Modbus"}
public = wf.registry_public()
for t, group in FAMILY.items():
    s_ = public.get(t) or {}
    check(f"{t} is under Integration / {group}, live",
          s_.get("palette") == {"cat": "integration", "group": group}
          and not s_.get("deprecated") and not s_.get("hidden"), s_.get("palette"))
routes_wf = read("core", "routes", "workflow.py")
check("the canvas is sent the WHOLE registry, whoever asks (no tier filter)",
      '"registry": workflow_service.registry_public()' in routes_wf
      and "def registry_public():" in read("core", "services", "workflow.py"))
check("...and the palette hides only deprecated and hidden nodes",
      "inDrawer(s, cat) && !s.deprecated && !s.hidden" in page
      and "const inDrawer = (sp, cat) => paletteCat(sp) === cat" in page)
subs = js_list(page, "SUBGROUP_ORDER")
check("sub-groups read MQTT, HTTP, API, Modbus, then the messaging ones",
      all(g in subs for g in ("MQTT", "HTTP", "API", "Modbus", "Email"))
      and subs.index("MQTT") < subs.index("HTTP") < subs.index("API")
      < subs.index("Modbus") < subs.index("Email"), subs)
order = js_list(page, "PALETTE_ORDER")
check("Integration is a drawer and the emptied External Values one is gone",
      "integration" in order and "external" not in order, order)

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def N(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def W(src, tgt, sh=None, th=None):
    return {"id": f"e_{src}_{tgt}_{th}", "source": src, "target": tgt,
            "sourceHandle": sh, "targetHandle": th}


def C(nodes, edges):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES)


num = N("num", "variable.number", {"fnId": "num", "name": "Speed", "value": 42})
c = C([num,
       N("mp", "output.mqtt_publish", {"topic": "a/b", "host": "127.0.0.1"}),
       N("hp", "output.webhook_publish", {"url": "http://x.test/in"}),
       N("mr", "output.modbus_reg", {"address": 3})],
      [W("num", "mp"), W("num", "hp"), W("num", "mr")])
types = sorted(o["type"] for o in c["outputs"])
check("graphs using the moved nodes still compile to the same outputs",
      not c["errors"] and types == ["modbus_reg", "mqtt_publish", "webhook_publish"],
      (c["errors"], types))
c = C([N("mq", "variable.mqtt", {"fnId": "mq", "name": "M", "topic": "t"}),
       N("api", "variable.api", {"fnId": "api", "name": "A",
                                 "url": "http://x.test/v"})], [])
check("...and the moved readers still compile to their functions",
      not c["errors"] and sorted(f["type"] for f in c["functions"])
      == ["api_poll", "mqtt_sub"], c["errors"])

# == 6. compile ===============================================================

mbw = dict(spec["data"], server="mb_line", address=0)
c = C([num, N("w", "output.modbus_write", mbw)], [W("num", "w", None, "in")])
doc = (c["outputs"] or [{}])[0]
check("a value wired in, no trigger: a change-mode write that names its server",
      not c["errors"] and doc.get("type") == "modbus_write"
      and doc.get("server") == "mb_line" and doc.get("mode") == "change"
      and doc.get("trigger") == "any" and doc.get("key") == "fn_num_value", (c["errors"], doc))
cmp_ = N("cmp", "branch.compare", {"fnId": "busy",
                                   "rules": [{"op": ">", "value": 4, "input": "in1"}]})
c = C([num, cmp_, N("w", "output.modbus_write", mbw)],
      [W("num", "w", None, "in"), W("num", "cmp"), W("cmp", "w", "out0", "trig")])
doc = (c["outputs"] or [{}])[0]
check("a Compare on the trigger port: a triggered write bound to that rule",
      not c["errors"] and doc.get("mode") == "trigger" and doc.get("trigger") == "alert"
      and doc.get("function") == "busy_b0", (c["errors"], doc))
for field, value, want in (("server", "", "Server"), ("address", "", "Address"),
                           ("quantity", "", "Quantity")):
    c = C([num, N("w", "output.modbus_write", dict(mbw, **{field: value}))],
          [W("num", "w", None, "in")])
    check(f"an empty {field} is a compile error naming it",
          any(want in e["message"] for e in c["errors"]), c["errors"])
c = C([num, N("w", "output.modbus_write", dict(mbw, fc=16, quantity=200))],
      [W("num", "w", None, "in")])
check("FC 16 past the protocol's 123 registers is refused at save time",
      any("123" in e["message"] for e in c["errors"]), c["errors"])
c = C([num, N("w", "output.modbus_write", dict(mbw, fc=5, quantity=9))],
      [W("num", "w", None, "in")])
check("FC 5/6 write one value whatever the Quantity says",
      not c["errors"] and c["outputs"][0]["quantity"] == 1, c["errors"])
src = N("src", "source.detection", {})
py = N("py", "analytics.python", {"fnId": "pyn", "name": "Py",
                                  "code": "def evaluate(ctx):\n    return 1\n"})
c = C([src, py, N("w", "output.modbus_write", mbw)],
      [W("src", "py"), W("py", "w", None, "in")])
doc = (c["outputs"] or [{}])[0]
check("a Python node wired in is the payload source: its keys override the node",
      not c["errors"] and doc.get("payload_fn") == "pyn"
      and doc.get("key") == "fn_pyn_value", (c["errors"], doc))
c = C([N("w", "output.modbus_write", mbw)], [])
check("a write node with nothing wired is a draft (warning), not an error",
      not c["errors"] and c["warnings"], (c["errors"], c["warnings"]))

rd = dict(wf.REGISTRY["variable.rs485"]["data"], fnId="rd", server="mb_line")
c = C([N("rd", "variable.rs485", rd)], [])
p = (c["functions"] or [{}])[0].get("params") or {}
check("a NEW read node reads through its server (no baud, unit from the server)",
      not c["errors"] and p.get("server") == "mb_line" and "baudrate" not in p
      and "unit" not in p, (c["errors"], p))
c = C([N("rd", "variable.rs485", dict(rd, server=""))], [])
check("...and a new read node with no server is an error to fix",
      any("Server" in e["message"] for e in c["errors"]), c["errors"])
legacy = {"fnId": "old", "name": "Old", "fc": 3, "unit": 2, "address": 5,
          "quantity": 1, "baudrate": 19200, "interval_s": 5}
c = C([N("old", "variable.rs485", legacy)], [])
p = (c["functions"] or [{}])[0].get("params") or {}
check("a SAVED read node (no server key) keeps its inline bus settings",
      not c["errors"] and p.get("baudrate") == 19200 and p.get("unit") == 2
      and "server" not in p, (c["errors"], p))
check("the core accepts a server-read with a 0..255 Unit-Id",
      fn_service.validate_function({"id": "r", "name": "r", "type": "modbus_read",
                                    "params": {"server": "mb_x", "unit": 0}})
      ["params"]["unit"] == 0)

# == 7. runtime, against a FAKE pymodbus client ================================


class Result:
    def __init__(self, error=False):
        self._error = error

    def isError(self):
        return self._error

    def __str__(self):
        return "Modbus Error: [Input/Output] device did not answer"


calls = []
behaviour = {"connect": [], "error": False, "check_lock": None}


class FakeClient:
    def __init__(self, cfg):
        self.cfg = cfg

    def connect(self):
        seq = behaviour["connect"]
        return seq.pop(0) if seq else True

    def close(self):
        pass

    def _call(self, name, *args, **kw):
        lock = behaviour["check_lock"]
        calls.append((name, args, kw, lock.locked() if lock else None))
        return Result(behaviour["error"])

    def write_coil(self, *a, **k):
        return self._call("write_coil", *a, **k)

    def write_register(self, *a, **k):
        return self._call("write_register", *a, **k)

    def write_coils(self, *a, **k):
        return self._call("write_coils", *a, **k)

    def write_registers(self, *a, **k):
        return self._call("write_registers", *a, **k)


buses.modbus_client = lambda cfg: FakeClient(cfg)
buses.set_clients([
    {"id": "mb_tcp", "type": "tcp", "host": "10.0.0.5", "port": 502,
     "unit_id": 7, "timeout_ms": 1000, "reconnect_on_timeout": True,
     "reconnect_timeout_ms": 100},
    {"id": "mb_ser", "type": "serial", "serial_port": "/dev/ttyAMA10",
     "baudrate": 9600, "databits": 8, "stopbits": 1, "parity": "none",
     "unit_id": 1, "timeout_ms": 1000, "reconnect_on_timeout": False,
     "reconnect_timeout_ms": 2000}])
out._MBW_INLINE = True
out._APP_STARTED = 0.0
clock = [1000.0]


def tick(o, stats):
    clock[0] += 1.0
    out.publish([o], stats, now=clock[0])


def node(oid, **kw):
    o = {"id": oid, "type": "modbus_write", "enabled": True, "trigger": "any",
         "mode": "change", "server": "mb_tcp", "fc": 6, "address": 0,
         "quantity": 1, "key": "fn_x_value"}
    o.update(kw)
    return o


calls.clear()
tick(node("wf_a", fc=5, address=3), {"fn_x_value": 2})
check("FC 5 forces a single coil, truthy -> on, at the server's unit",
      calls[:1] and calls[0][:3] == ("write_coil", (3, True), {"slave": 7}), calls)
calls.clear()
tick(node("wf_b", fc=6, unit=9), {"fn_x_value": 42.4})
check("FC 6 presets a single register, the node's own Unit-Id winning",
      calls[:1] and calls[0][:3] == ("write_register", (0, 42), {"slave": 9}), calls)
calls.clear()
tick(node("wf_c", fc=15, quantity=4), {"fn_x_value": 1})
check("FC 15 with one number repeats it Quantity times",
      calls[:1] and calls[0][:3] == ("write_coils", (0, [True] * 4), {"slave": 7}), calls)
calls.clear()
app_fns._list_stats["fn_py_value"] = [1, 0, 3]
tick(node("wf_d", fc=15, quantity=8, key="fn_py_value"), {"fn_py_value": 1})
check("FC 15 with a LIST (a Python node) writes the list, truthy -> on",
      calls[:1] and calls[0][:3] == ("write_coils", (0, [True, False, True]),
                                     {"slave": 7}), calls)
calls.clear()
app_fns._list_stats["fn_py_value"] = [10, 20, -5]
tick(node("wf_e", fc=16, quantity=3, address=100, key="fn_py_value"),
     {"fn_py_value": 10})
check("FC 16 with a list writes the registers (negative -> two's complement)",
      calls[:1] and calls[0][:3] == ("write_registers", (100, [10, 20, 65531]),
                                     {"slave": 7}), calls)
calls.clear()
tick(node("wf_f", fc=16, quantity=3, address=100), {"fn_x_value": 7})
check("FC 16 with one number repeats it",
      calls[:1] and calls[0][:3] == ("write_registers", (100, [7, 7, 7]),
                                     {"slave": 7}), calls)
app_fns._list_stats.clear()

# The operator's own payload: msg.payload = {value, fc: 6, unitid: 101,
# address: 0, quantity: 1}, from a Python node returning that dict.
calls.clear()
tick(node("wf_g", fc=16, address=500, quantity=4, key="fn_pyn_value",
          payload_fn="pyn"),
     {"fn_pyn_value": 55, "fn_pyn_fc": 6, "fn_pyn_unitid": 101,
      "fn_pyn_address": 0, "fn_pyn_quantity": 1})
check("the Node-RED payload dict overrides fc / unitid / address / quantity",
      calls[:1] and calls[0][:3] == ("write_register", (0, 55), {"slave": 101}), calls)
calls.clear()
tick(node("wf_g2", fc=6, address=12, key="fn_pyn_value", payload_fn="pyn"),
     {"fn_pyn_value": 9})
check("...field by field: keys the payload leaves out keep the node's settings",
      calls[:1] and calls[0][:3] == ("write_register", (12, 9), {"slave": 7}), calls)
ext_src = read("systems", "demo", "app", "extensions.py")
check("a Python node's dict reaches the app as separate numeric stats, lists kept",
      "clean[str(k)[:40]] = round(float(v), 4)" in ext_src
      and 'out = {"value": out}' in ext_src)

calls.clear()
o = node("wf_h")
for _ in range(5):
    tick(o, {"fn_x_value": 3})
check("the same value frame after frame is written ONCE", len(calls) == 1, calls)
tick(o, {"fn_x_value": 4})
check("...and again when it changes", len(calls) == 2 and calls[1][1] == (0, 4), calls)
calls.clear()
out.publish([o], {"fn_x_value": 5}, now=clock[0] + 0.05)
check("a change inside the pacing floor waits", not calls, calls)
out.publish([o], {"fn_x_value": 5}, now=clock[0] + 1.0)
check("...and goes out once the floor has passed", len(calls) == 1, calls)
clock[0] += 2.0

# After a Trigger (`beat`, as an MQTT Publish has): each fire writes again,
# even the same value. asri_test_4 on .49, 2026-10-09: the OFF side's Trigger
# sends 0 to a motor the ON side set to 100; "0 was my last write" must not
# swallow the stop.
calls.clear()
ob = node("wf_beat", beat="stop_b0")
tick(ob, {"fn_x_value": 0})
tick(ob, {"fn_x_value": 0})
check("with a beat: the first value is written, the same value unfired is not",
      len(calls) == 1 and calls[0][1] == (0, 0), calls)
app_fns._beat("stop_b0")
tick(ob, {"fn_x_value": 0})
check("...and the same value IS written again when the Trigger fires",
      len(calls) == 2 and calls[1][1] == (0, 0), calls)
tick(ob, {"fn_x_value": 0})
check("...once per fire, not every tick after it", len(calls) == 2, calls)
check("the validator keeps the beat in change mode, drops it otherwise",
      dc._validate_output(dict(node("wf_v", beat="stop_b0"), label="x"))
      .get("beat") == "stop_b0"
      and "beat" not in dc._validate_output(
          dict(node("wf_v2", beat="stop_b0", mode="trigger",
                    trigger="alert", function="stop_b0"), label="x")))
clock[0] += 2.0

# Trigger mode: rides fire(), and the change-mode node never does.
calls.clear()
trig = node("wf_t", mode="trigger", trigger="alert", function="busy_b0")
event = {"type": "alert", "detail": {"function": "busy_b0"}}
out.fire([trig, node("wf_ch")], event, stats={"fn_x_value": 11})
check("a triggered write fires on its rule's alert, a change-mode one does not",
      len(calls) == 1 and calls[0][1] == (0, 11), calls)
calls.clear()
out.publish([trig], {"fn_x_value": 12}, now=clock[0] + 5)
check("...and a triggered write never writes on the tick", not calls, calls)

# The serial bus lock is ONE object, shared with the Read poller.
lock = buses.bus_lock("/dev/ttyAMA10")
check("the read poller's lock for /dev/ttyAMA10 IS the write worker's",
      app_fns._bus_lock("/dev/ttyAMA10") is lock)
cfg = buses.client_config(buses.client("mb_ser"))
check("a serial server's bus key is its device, a TCP server's its host:port",
      buses.bus_key(cfg) == "/dev/ttyAMA10"
      and buses.bus_key(buses.client_config(buses.client("mb_tcp")))
      == "tcp://10.0.0.5:502")
calls.clear()
behaviour["check_lock"] = lock
tick(node("wf_s", server="mb_ser"), {"fn_x_value": 1})
check("a write on the serial server holds that lock while it talks",
      calls[:1] and calls[0][3] is True, calls)
behaviour["check_lock"] = None
seen_locked = []
real_read = buses.modbus_read


def fake_read(cfg_, address, count=1, kind="holding"):
    seen_locked.append((buses.bus_key(cfg_), lock.locked()))
    stop.set()
    return [1]


buses.modbus_read = fake_read
stop = threading.Event()
params = {"server": "mb_ser", "fc": 3, "address": 0, "quantity": 1,
          "interval_s": 2}
app_fns._modbus_loop("rd", params, app_fns._modbus_sig(
    type("F", (), {"params": params})()), stop)
buses.modbus_read = real_read
check("...and the Read poller on the same server holds the SAME lock",
      seen_locked == [("/dev/ttyAMA10", True)], seen_locked)

# Delay to activate input.
calls.clear()
out._APP_STARTED = clock[0]
od = node("wf_dl", delay_start=True, start_delay_s=5)
out.publish([od], {"fn_x_value": 1}, now=clock[0] + 1)
out.publish([od], {"fn_x_value": 2}, now=clock[0] + 2)
check("nothing is written while the start delay holds", not calls, calls)
out.publish([od], {"fn_x_value": 2}, now=clock[0] + 6)
check("...and the value it held back is NOT written as a stale start-up value",
      not calls, calls)
out.publish([od], {"fn_x_value": 3}, now=clock[0] + 7)
check("...the first real change after it is", len(calls) == 1 and calls[0][1] == (0, 3), calls)
out._APP_STARTED = 0.0
calls.clear()
trig_d = node("wf_td", mode="trigger", trigger="alert", function="busy_b0",
              delay_start=True, start_delay_s=600)
out._APP_STARTED = time.time()
out.fire([trig_d], event, stats={"fn_x_value": 1})
check("a triggered write is held back by the delay too", not calls, calls)
out._APP_STARTED = 0.0

# What the canvas shows.
tele = out.telemetry([node("wf_b")])
check("a good write leaves ok=1 and a note saying what reached the wire",
      tele.get("out_wf_b_ok") == 1
      and tele.get("out_wf_b_note") == "wrote 42 to 0 (FC 6, unit 9)", tele)
behaviour["error"] = True
tick(node("wf_err"), {"fn_x_value": 1})
behaviour["error"] = False
tele = out.telemetry([node("wf_err")])
check("a device error lands in the node's status",
      tele.get("out_wf_err_ok") == 0 and "did not answer" in tele.get("out_wf_err_error", "")
      and not tele.get("out_wf_err_note"), tele)
tick(node("wf_gone", server="mb_deleted"), {"fn_x_value": 1})
tele = out.telemetry([node("wf_gone")])
check("a server that no longer exists is said, not skipped",
      "no longer exists" in tele.get("out_wf_gone_error", ""), tele)
tick(node("wf_big"), {"fn_x_value": 70000})
check("a value that does not fit a register is refused, not clamped",
      "16-bit" in out.telemetry([node("wf_big")]).get("out_wf_big_error", ""))

# Reconnect on timeout: one retry after the reconnect timeout -- or none.
calls.clear()
behaviour["connect"] = [False, True]
tick(node("wf_rc"), {"fn_x_value": 5})
check("reconnect on: a failed connect is retried once after the timeout",
      len(calls) == 1 and out.telemetry([node("wf_rc")])["out_wf_rc_ok"] == 1, calls)
calls.clear()
behaviour["connect"] = [False, True]
tick(node("wf_nrc", server="mb_ser"), {"fn_x_value": 5})
check("reconnect off: it is not",
      not calls and "cannot reach" in out.telemetry([node("wf_nrc")])["out_wf_nrc_error"],
      calls)
behaviour["connect"] = []

# The real worker thread: the tick hands the job over and returns at once.
out._MBW_INLINE = False
calls.clear()
t0 = time.time()
tick(node("wf_q"), {"fn_x_value": 77})
handed = time.time() - t0
deadline = time.time() + 3
while not calls and time.time() < deadline:
    time.sleep(0.02)
check("on the worker thread: the tick returns immediately, the write follows",
      handed < 0.5 and calls and calls[0][1] == (0, 77), (handed, calls))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
