# Yaya's review of a34da37 (MQTT Connections), 2026-10-02 -- M1 to M5.
#
#   M1  the first publish on a Connection, and every one during a reconnect,
#       was lost: Connections now start at app start for the outputs that use
#       them, and a publish while not connected is QUEUED (bounded, aged) and
#       sent on connect; what is dropped is counted and logged.
#   M2  compiled functions from v1.0.12 still held MQTT Subscribe logins and
#       GET /api/functions returned them: every function/output payload is
#       redacted (and restored on write), and LOGIC_VERSION moved so the
#       config is recompiled from the migrated graph at master start.
#   M3  an exported system lost its Connections: they travel now (no
#       password; marked password_needed) and are re-created on import.
#   M4  an unmigratable node rewrote the store on every open: marked once.
#   M5  a fixed client ID shared by two Connections on the box: refused.
import asyncio
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


SECRET = "Pw-not-for-browsers-9"

# == M1: nothing is lost while a Connection connects =========================
from app import mqtt_bus                                             # noqa: E402

made = []


class LateClient:
    """Connects only when the test says so."""

    def __init__(self, client_id):
        self.pubs, self.subs = [], []
        made.append(self)

    def username_pw_set(self, *a):
        pass

    def reconnect_delay_set(self, **k):
        pass

    def connect_async(self, *a, **k):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def subscribe(self, f, qos=0):
        self.subs.append(f)

    def publish(self, topic, payload, qos=0, retain=False):
        self.pubs.append((topic, payload, retain))
        return types.SimpleNamespace(rc=0)

    def up(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def down(self):
        self.on_disconnect(self, None, 0)


mqtt_bus.CLIENT_FACTORY = LateClient
ROW = {"id": "mq_11112222", "name": "PLC", "host": "10.0.0.5", "port": 1883}
mqtt_bus.set_clients([ROW])
mqtt_bus.prestart(["mq_11112222", None, "mq_00000000"])
check("the Connections the outputs use start at app start", len(made) == 1)
r1 = mqtt_bus.publish("mq_11112222", "alerts/line1", "1")
r2 = mqtt_bus.publish("mq_11112222", "alerts/line1", "2", retain=True)
check("a publish before the broker answers is queued, not lost",
      r1 == r2 == "queued" and made[0].pubs == []
      and mqtt_bus.status("mq_11112222")["queued"] == 2)
made[0].up()
check("...and goes out, in order, the moment it connects",
      made[0].pubs == [("alerts/line1", "1", False), ("alerts/line1", "2", True)]
      and mqtt_bus.status("mq_11112222")["queued"] == 0)
check("a connected publish is sent at once",
      mqtt_bus.publish("mq_11112222", "a", "3") == "sent" and made[0].pubs[-1][1] == "3")
made[0].down()
mqtt_bus.publish("mq_11112222", "a", "during-blip")
made[0].up()
check("a publish during a broker blip is delivered after it",
      made[0].pubs[-1] == ("a", "during-blip", False))
made[0].down()
for i in range(mqtt_bus.QUEUE_MAX + 5):
    mqtt_bus.publish("mq_11112222", "q", str(i))
st = mqtt_bus.status("mq_11112222")
check("the queue is bounded; the oldest beyond it are dropped AND counted",
      st["queued"] == mqtt_bus.QUEUE_MAX and st["dropped"] == 5, st)
conn = mqtt_bus._conns["mq_11112222"]
conn.pending[0] = (conn.pending[0][0] - mqtt_bus.QUEUE_MAX_AGE_S - 1,) + conn.pending[0][1:]
before = len(made[0].pubs)
made[0].up()
check("...and one that waited too long is not sent late, but counted",
      len(made[0].pubs) - before == mqtt_bus.QUEUE_MAX - 1
      and mqtt_bus.status("mq_11112222")["dropped"] == 6)
mqtt_bus.set_clients([{**ROW, "port": 1884}])
mqtt_bus.publish("mq_11112222", "moved", "x")
check("waiting messages move to a new client when the Connection changes",
      len(made) == 2 and list(mqtt_bus._conns["mq_11112222"].pending)[-1][1] == "moved")
mqtt_bus.close_all()
run_src = read("systems", "demo", "app", "run.py")
check("the app starts them at start AND on every config load",
      run_src.count("mqtt_bus.prestart(") == 2)

from app import outputs as app_outputs                               # noqa: E402
made.clear()
mqtt_bus.set_clients([ROW])
o = {"id": "wf_pubq", "type": "mqtt_publish", "conn": "mq_11112222",
     "topic": "line/count", "key": "k", "interval_s": 0.2}
src_out = read("systems", "demo", "app", "outputs.py")
check("the Debug note says QUEUED while connecting, not sent",
      'f"queued {where} (connecting)"' in src_out and "note=lambda:" in src_out)
mqtt_bus.close_all()

# == M2: no credential in a functions/outputs payload ==========================
from core.services import redact                                     # noqa: E402
old_fn = {"id": "wf_m1", "type": "mqtt_sub", "params": {
    "host": "10.0.0.5", "topic": "a", "username": "plant", "password": SECRET}}
old_out = {"id": "o1", "type": "email", "smtp_pass": SECRET, "bot_token": "123:ABC",
           "headers": [{"name": "X", "value": "y"}], "token": "tok",
           "password_set": True, "password_file": "/etc/x"}
r = redact.redact([old_fn, old_out])
check("a v1.0.12 MQTT Subscribe's password is redacted", SECRET not in json.dumps(r)
      and r[0]["params"]["password"] == redact.REDACTED
      and r[0]["params"]["username"] == "plant")
check("...and an output's SMTP password, bot token and bearer token",
      r[1]["smtp_pass"] == r[1]["bot_token"] == r[1]["token"] == redact.REDACTED)
check("...but not what only looks like one (a flag, a path)",
      r[1]["password_set"] is True and r[1]["password_file"] == "/etc/x")
back = redact.restore(r, [old_fn, old_out])
check("a list sent back with the placeholders keeps the stored secrets",
      back[0]["params"]["password"] == SECRET and back[1]["smtp_pass"] == SECRET)
check("...a placeholder with nothing to restore becomes empty, never itself",
      redact.restore([{"id": "new", "password": redact.REDACTED}], [])[0]["password"] == "")
check("...and a typed new value wins",
      redact.restore([{"id": "o1", "smtp_pass": "new-pw"}], [old_out])[0]["smtp_pass"] == "new-pw")
for path, needle in ((("core", "routes", "functions.py"), '"functions": redact.redact(fns)'),
                     (("core", "routes", "functions.py"), "redact.restore(body.get(\"functions\")"),
                     (("core", "routes", "models.py"), "redact.redact(\n                detector_control_service.get_outputs"),
                     (("core", "routes", "models.py"), "redact.restore(body.get(\"outputs\")"),
                     (("core", "routes", "workflow.py"), '"functions": _redact.redact(compiled["functions"]),')):
    check(f"{'/'.join(path)} redacts ({needle[:40]}...)", needle in read(*path))
from core.services import workflow as wf                              # noqa: E402
from core.services import app_supervisor                              # noqa: E402
check("the compile version moved, so v1.0.12 configs recompile at start",
      wf.LOGIC_VERSION >= 4)
app_supervisor_stale = app_supervisor.logic_stale
import core.services.detector_control as dc                           # noqa: E402
dc_system_config = dc.system_config
dc.system_config = lambda d, s: {"logic": {"version": 3}}
check("...a config compiled by v1.0.12 ([logic] version 3) counts as stale",
      app_supervisor.logic_stale("/x", "y") is True)
dc.system_config = dc_system_config

if "fastapi" not in sys.modules:                       # not on a dev laptop
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.Response =         resp.StreamingResponse = resp.FileResponse = object
    sys.modules["fastapi.responses"] = resp

# The recompile at start, end to end: a legacy graph with a login -> the
# compiled config holds the Connection id and no password.
systems = tempfile.mkdtemp()
slug = "rv"
os.makedirs(os.path.join(systems, slug))
open(os.path.join(systems, slug, "config.toml"), "w").close()
wf.save_store(slug, {"version": 3, "workflows": [{"id": "main", "graph": {"nodes": [
    {"id": "s1", "type": "variable.mqtt", "position": {"x": 0, "y": 0},
     "data": {"fnId": "mqa", "name": "Parts", "host": "10.0.0.5", "port": 1883,
              "topic": "a/b", "username": "plant", "password": SECRET}}],
    "edges": []}}]})
from core.services import functions as fn_service                     # noqa: E402
fn_service.set_functions(systems, slug, [{**old_fn, "id": "wf_mqa"}])
check("(setup) the old compiled config holds the password",
      SECRET in open(os.path.join(systems, slug, "config.local.toml")).read())
try:
    result = app_supervisor.ensure_compiled(systems, slug, tempfile.mkdtemp())
except Exception as exc:                                              # noqa: BLE001
    result = f"error {exc}"
local = open(os.path.join(systems, slug, "config.local.toml")).read()
check("after the recompile at start the compiled config has no password",
      result == "applied" and SECRET not in local.split("[[mqtt_clients]]")[0]
      and "conn = " in local, (result, local[:400]))
from core.services import mqtt_clients as mc                         # noqa: E402
check("...it moved into the Connection, where the app reads it",
      any(c["password"] == SECRET for c in mc.list_clients(systems, slug, public=False)))

# == M3: export / import =======================================================
from core.services import templates                                   # noqa: E402
conn = mc.list_clients(systems, slug, public=False)[0]
doc = {"mqtt_clients": mc.export_rows(systems, slug)}
check("an export carries the Connections - without the password",
      doc["mqtt_clients"][0]["host"] == "10.0.0.5" and SECRET not in json.dumps(doc)
      and doc["mqtt_clients"][0]["password_needed"] is True)
scrubbed = templates._scrub_node_secrets({"workflows": [{"graph": {"nodes": [
    {"data": {"password": SECRET, "topic": "a"}}]}}]})
check("...and a node still holding a login is scrubbed",
      SECRET not in json.dumps(scrubbed)
      and scrubbed["workflows"][0]["graph"]["nodes"][0]["data"]["topic"] == "a")
src_tpl = read("core", "services", "templates.py")
check("export_system puts them in the file", 'doc["mqtt_clients"] = conns' in src_tpl)
sys2 = "rv2"
os.makedirs(os.path.join(systems, sys2))
open(os.path.join(systems, sys2, "config.toml"), "w").close()
rows = doc["mqtt_clients"] + [{**doc["mqtt_clients"][0], "id": "mq_99990000"}]
mapping, warns = mc.import_rows(systems, sys2, rows)
made_conns = mc.list_clients(systems, sys2, public=False)
check("import re-creates them, one per host/port/user",
      len(made_conns) == 1 and set(mapping) == {conn["id"], "mq_99990000"}
      and len(set(mapping.values())) == 1)
check("...asking for the password",
      made_conns[0]["password_needed"] is True and any("password" in w for w in warns))
store = {"workflows": [{"graph": {"nodes": [{"data": {"connection": conn["id"]}}]}}]}
outs = [{"id": "o", "conn": "mq_99990000"}]
fns = [{"id": "f", "params": {"conn": conn["id"]}}]
mc.remap(store, mapping)
mc.remap(outs, mapping)
mc.remap(fns, mapping)
new_id = made_conns[0]["id"]
check("...and every node, output and function points at the new one",
      store["workflows"][0]["graph"]["nodes"][0]["data"]["connection"] == new_id
      and outs[0]["conn"] == new_id and fns[0]["params"]["conn"] == new_id)
mc.save(systems, sys2, {"id": new_id, "password": "now-set-1"})
check("typing the password clears password_needed",
      mc.list_clients(systems, sys2)[0]["password_needed"] is False)
check("import_system re-creates them before anything uses them",
      src_tpl.index("mqtt_clients_service.import_rows(") < src_tpl.index(
          "for item in mqtt_clients_service.remap(list(doc.get(\"functions\")"))
ed = read("frontend", "src", "components", "MqttConnectionEditor.jsx")
check("the node's Connection row asks for a missing password",
      "picked?.password_needed" in ed and "Set the password for this Connection" in ed)

# == M4: an unmigratable node is handled once ======================================
graph = {"nodes": [{"id": "x", "type": "variable.mqtt",
                    "data": {"host": "my broker", "topic": "a"}}]}
first = mc.migrate_graph(graph, systems, slug)
second = mc.migrate_graph(graph, systems, slug)
d = graph["nodes"][0]["data"]
check("an address that cannot become a Connection is marked once",
      first is True and "mqtt_unmigrated" in d and d["host"] == "my broker")
check("...and the next open changes nothing (the store is not rewritten)", second is False)
check("...the inspector says why",
      "d.mqtt_unmigrated" in read("frontend", "src", "pages", "Workflow.jsx"))

# == M5: one client ID per client on the box =========================================
a = mc.save(systems, slug, {"name": "A", "host": "10.1.1.1", "client_id": "line1-box"})
try:
    mc.save(systems, sys2, {"name": "B", "host": "10.1.1.1", "client_id": "line1-box"})
    msg = ""
except ValueError as e:
    msg = str(e)
check("a client ID another Connection on the box uses is refused, naming it",
      "already used by MQTT Connection 'A'" in msg and "system 'rv'" in msg, msg)
check("...editing the one that has it is fine",
      mc.save(systems, slug, {"id": a["id"], "name": "A2", "client_id": "line1-box"})["name"] == "A2")
check("...and blank (automatic) ids never clash",
      mc.save(systems, sys2, {"host": "10.1.1.1"})["client_id"] == "")
check("the editor says a fixed one must be unique",
      "must be unique on the broker" in ed)

# The recompile in M2 left app_supervisor.reconcile_soon's daemon thread
# behind (locks, inbox writes, imports). Waited for, so the interpreter never
# shuts down underneath it. The v1.0.13 release runner failed this test with
# every check passed and nothing more printed -- a process lost AT EXIT -- and
# this thread is the only thing still running then. (Not reproduced on any
# Linux here; run_all now prints the exit code to settle it if it recurs.)
import threading                                                      # noqa: E402
for t in threading.enumerate():
    if t.name == "app-" + slug and t is not threading.current_thread():
        t.join(30)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
