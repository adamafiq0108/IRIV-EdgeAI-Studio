# MQTT Connections: a broker set up once, picked by every MQTT Subscribe and
# MQTT Publish -- the Modbus Server pattern.
#
# Asked for on 2026-10-02 (a beta tester, via Tok Aba): "MQTT there no broker
# IP? add user and password in mqtt, add topic user can change the topic
# themselves". Pinned here: the connection store and its validation, the
# migration of nodes' own broker settings into shared connections, the
# compile (only an id travels), the routes, the password never reaching a
# browser or a compiled document, ONE client per connection in the app, and
# MQTT still running with AI detection off.
import asyncio
import io
import json
import os
import sys
import tempfile
import threading
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


def raises(call, kind=ValueError):
    try:
        call()
    except kind:
        return True
    return False


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


from core.services import detector_control as dc                   # noqa: E402
from core.services import functions as fn_service                  # noqa: E402
from core.services import mqtt_clients as mc                       # noqa: E402
from core.services import workflow as wf                           # noqa: E402

SECRET = "s3cret-Broker-pw"

# == 1. validation ==============================================================
c = mc.clean({})
check("a new connection is this box's broker", c["host"] == "127.0.0.1" and c["port"] == 1883
      and c["keepalive_s"] == 60 and c["reconnect_s"] == 1 and c["tls"] is False)
for body, why in (({"host": "10.0.0.5 x"}, "a host with a space"),
                  ({"host": "a/b"}, "a host with a slash"),
                  ({"port": "abc"}, "a port that is not a number"),
                  ({"tls": True}, "TLS (coming later)"),
                  ({"client_id": "has space"}, "a client id with a space"),
                  ({"client_id": "x" * 65}, "a 65-character client id"),
                  ({"username": "u", "password": "pw\nnext"}, "a password with a newline"),
                  ({"username": "u\x00"}, "a user name with a NUL")):
    check(f"refused: {why}", raises(lambda: mc.clean(body)))
check("ranges are clamped, not refused",
      mc.clean({"port": 99999, "keepalive_s": 1, "reconnect_s": 10000})
      == {**mc.clean({}), "port": 65535, "keepalive_s": 5, "reconnect_s": 300})
check("a password with no user name is not kept",
      mc.clean({"password": "x"})["password"] == "")

# == 2. store: list, save, edit, delete ===========================================
systems = tempfile.mkdtemp()
slug = "mqsys"
os.makedirs(os.path.join(systems, slug))
open(os.path.join(systems, slug, "config.toml"), "w").close()

a = mc.save(systems, slug, {"name": "Line broker", "host": "10.0.0.5",
                            "username": "plant", "password": SECRET})
b = mc.save(systems, slug, {})
check("a saved connection gets an mq_ id", a["id"].startswith("mq_") and len(a["id"]) == 11)
check("...and what comes back has NO password, only that one is set",
      "password" not in a and a["password_set"] is True and a["label"] == "Line broker")
listed = mc.list_clients(systems, slug)
check("the list carries no password at all",
      SECRET not in json.dumps(listed) and all("password" not in x for x in listed))
check("...says which one is this box's own broker",
      [x["this_box"] for x in listed] == [False, True])
check("the box itself can still read it", mc.resolve(systems, slug, a["id"])["password"] == SECRET)
mc.save(systems, slug, {"id": a["id"], "name": "Line broker 2", "password": ""})
check("an edit with a blank password keeps the stored one",
      mc.resolve(systems, slug, a["id"])["password"] == SECRET
      and mc.resolve(systems, slug, a["id"])["name"] == "Line broker 2")
# A stored password belongs to the broker it was typed for: moving the
# connection (port or host) without re-entering it drops it and asks again,
# so the box never logs in to a new address with the old broker's login.
mc.save(systems, slug, {"id": a["id"], "port": 1884, "password": ""})
moved = mc.resolve(systems, slug, a["id"])
check("...but a port change without a re-typed password drops it and asks for it",
      moved["password"] == "" and moved["port"] == 1884 and moved["password_needed"] is True)
mc.save(systems, slug, {"id": a["id"], "password": SECRET})
mc.save(systems, slug, {"id": a["id"], "host": "10.9.9.9"})
check("...and so does a host change",
      mc.resolve(systems, slug, a["id"])["password"] == "")
mc.save(systems, slug, {"id": a["id"], "host": "10.9.9.8", "password": SECRET})
check("...while a move WITH the password typed keeps the new one",
      mc.resolve(systems, slug, a["id"])["password"] == SECRET
      and mc.resolve(systems, slug, a["id"])["password_needed"] is False)
mc.save(systems, slug, {"id": a["id"], "name": "Line broker", "host": "10.0.0.5",
                        "port": 1884, "password": SECRET})
mc.save(systems, slug, {"id": a["id"], "password": "new-one-123"})
check("...a typed one replaces it", mc.resolve(systems, slug, a["id"])["password"] == "new-one-123")
mc.save(systems, slug, {"id": a["id"], "clear_password": True})
check("...and clear_password removes it", mc.resolve(systems, slug, a["id"])["password"] == "")
mc.save(systems, slug, {"id": a["id"], "password": SECRET})
check("editing a connection that is gone says so",
      raises(lambda: mc.save(systems, slug, {"id": "mq_00000000"}), LookupError))

wf.save_store(slug, {"version": 3, "workflows": [{"id": "main", "graph": {"nodes": [
    {"id": "s1", "type": "variable.mqtt", "data": {"name": "Parts", "connection": a["id"]}}],
    "edges": []}}]})
try:
    mc.delete(systems, slug, a["id"])
    msg = ""
except ValueError as e:
    msg = str(e)
check("...the refusal names the node", "Parts" in msg and "1 node" in msg, msg)
check("one nothing uses can", mc.delete(systems, slug, b["id"]) is True
      and len(mc.list_clients(systems, slug)) == 1)

# == 3. migration ==================================================================
graph = {"nodes": [
    {"id": "s1", "type": "variable.mqtt", "data": {
        "host": "10.0.0.5", "port": 1884, "username": "plant", "password": SECRET,
        "topic": "a/#"}},
    {"id": "s2", "type": "variable.mqtt", "data": {
        "host": "10.0.0.5", "port": 1884, "username": "plant", "password": SECRET,
        "topic": "b/+"}},
    {"id": "s3", "type": "variable.mqtt", "data": {"host": "127.0.0.1", "topic": "c"}},
    {"id": "p1", "type": "output.mqtt_publish", "data": {"host": "localhost", "port": 1883,
                                                         "topic": "line/count"}},
    {"id": "p2", "type": "output.mqtt_publish", "data": {"host": "10.0.0.9", "topic": "x"}},
    {"id": "o1", "type": "output.mqtt", "data": {"host": "127.0.0.1", "topic": "alert"}},
    {"id": "n1", "type": "variable.number", "data": {"value": 1}},
], "edges": []}
before = {x["id"] for x in mc.list_clients(systems, slug)}
changed = mc.migrate_graph(graph, systems, slug)
by = {n["id"]: n["data"] for n in graph["nodes"]}
conns = {x["id"]: x for x in mc.list_clients(systems, slug, public=False)}
check("old MQTT nodes are migrated", changed is True
      and all("connection" in by[i] for i in ("s1", "s2", "s3", "p1", "p2", "o1")))
check("...an existing identical connection is reused (s1, s2 -> the saved one)",
      by["s1"]["connection"] == by["s2"]["connection"] == a["id"])
check("...this box's broker, by any loopback name, is one connection (s3, p1, o1)",
      by["s3"]["connection"] == by["p1"]["connection"] == by["o1"]["connection"]
      and conns[by["s3"]["connection"]]["name"] == "This box")
check("...a different broker gets its own", by["p2"]["connection"] not in
      (by["s1"]["connection"], by["s3"]["connection"])
      and conns[by["p2"]["connection"]]["host"] == "10.0.0.9")
check("...three connections in all, two of them new",
      len(conns) == 3 and len(set(conns) - before) == 2)
check("...the login leaves the graph", SECRET not in json.dumps(graph)
      and not any(k in by[i] for i in by for k in ("host", "port", "username", "password")
                  if i != "n1"))
check("...the topic stays on the node", by["s1"]["topic"] == "a/#" and by["p1"]["topic"] == "line/count")
check("...other nodes are untouched", by["n1"] == {"value": 1})
check("running it again changes nothing", mc.migrate_graph(graph, systems, slug) is False
      and len(mc.list_clients(systems, slug)) == 3)
routes_wf = read("core", "routes", "workflow.py")
check("the workflow migrates on open, and saves the result",
      "mqtt_clients_service.migrate_graph(graph, systems_dir, slug)" in routes_wf
      and "or compares or clips or charts or mqttconns:" in routes_wf)

# == 4. compile: only an id travels ==================================================
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def N(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def W(src, tgt):
    return {"id": f"e_{src}_{tgt}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": None}


def C(nodes, edges=()):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": list(edges)}), ZONES)


cid = a["id"]
out = C([N("s1", "variable.mqtt", {"fnId": "mq1", "name": "Parts", "connection": cid,
                                   "topic": "plc/+/parts", "json_path": "data.count"}),
         N("p1", "output.mqtt_publish", {"connection": cid, "topic": "line/count"})],
        [W("s1", "p1")])
fn = next((f for f in out["functions"] if f["type"] == "mqtt_sub"), None)
pub = next((o for o in out["outputs"] if o["type"] == "mqtt_publish"), None)
check("a Subscribe compiles to its connection id and topic",
      not out["errors"] and fn and fn["params"].get("conn") == cid
      and fn["params"]["topic"] == "plc/+/parts" and "host" not in fn["params"], out["errors"])
check("a Publish compiles to its connection id and topic",
      pub and pub.get("conn") == cid and pub["topic"] == "line/count" and "host" not in pub, pub)
check("no password anywhere in what was compiled", SECRET not in json.dumps(out))
out = C([N("s1", "variable.mqtt", {"fnId": "mq1", "connection": "", "topic": "a"})])
none = next((f for f in out["functions"] if f["type"] == "mqtt_sub"), None)
check("a Subscribe with no connection picked is NOT an error: it is this box's broker",
      not out["errors"] and none and none["params"].get("host") == "127.0.0.1"
      and none["params"].get("port") == 1883 and "conn" not in none["params"], out)
# Boboiboy's regression (2026-10-02): nodes straight from the palette, with
# their defaults, must compile -- every saved workflow has nodes like these.
num = N("num", "variable.number", {"fnId": "num", "name": "Speed", "value": 4})
sub = N("s9", "variable.mqtt", {**wf.REGISTRY["variable.mqtt"]["data"], "fnId": "mq9",
                                "topic": "x/y"})
pub = N("p9", "output.mqtt_publish", dict(wf.REGISTRY["output.mqtt_publish"]["data"]))
out = C([num, sub, pub], [W("num", "p9")])
check("palette-default MQTT nodes compile with no error, to this box's broker",
      not out["errors"]
      and next(o for o in out["outputs"] if o["type"] == "mqtt_publish")["host"] == "127.0.0.1",
      out["errors"])
out = C([N("s1", "variable.mqtt", {"fnId": "mq1", "host": "10.1.1.1", "topic": "a",
                                   "username": "u", "password": "p"})])
legacy = next(f for f in out["functions"] if f["type"] == "mqtt_sub")
check("a node from before Connections still compiles as it did",
      legacy["params"]["host"] == "10.1.1.1" and legacy["params"].get("username") == "u")
check("the output validator refuses a made-up connection id",
      raises(lambda: dc._validate_output({"type": "mqtt_publish", "key": "fn_x_value",
                                          "conn": "../../etc"})))
empty = dc._validate_output({"type": "mqtt_publish", "key": "fn_x_value", "connection": ""})
check("...and an empty one publishes to this box's broker",
      empty.get("host") == "127.0.0.1" and empty.get("port") == 1883 and "conn" not in empty)
check("...a stored document keeps its id on every re-validation",
      dc._validate_output(dc._validate_output({"type": "mqtt_publish", "key": "k",
                                               "connection": cid}))["conn"] == cid)
clean_fn = fn_service._validate_function({"id": "mq1", "name": "x", "type": "mqtt_sub",
                                          "params": {"conn": cid, "topic": "a/b",
                                                     "username": "u", "password": "p"}}) \
    if hasattr(fn_service, "_validate_function") else None
if clean_fn is not None:
    check("a function on a connection keeps no login of its own",
          "username" not in clean_fn["params"] and clean_fn["params"]["conn"] == cid)

# == 5. routes ===========================================================================
if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException = HTTPException
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.Response = \
        resp.StreamingResponse = resp.FileResponse = object
    sys.modules["fastapi.responses"] = resp
from fastapi import HTTPException                                        # noqa: E402
from core.routes import mqtt_clients as routes                          # noqa: E402

editors = {"ada"}
routes.perm = types.SimpleNamespace(
    require=lambda req, cap: None if req.state.user in editors
    else (_ for _ in ()).throw(HTTPException(status_code=403, detail="no")),
    editable=lambda req, prefix: req.state.user in editors)
routes._system_context = lambda request: (systems, slug)


def call(handler, user, body=None, *args):
    async def _json():
        return body or {}
    req = types.SimpleNamespace(state=types.SimpleNamespace(user=user), json=_json)
    try:
        res = handler(req, *args)
        if asyncio.iscoroutine(res):
            res = asyncio.run(res)
        return 200, res
    except HTTPException as e:
        return e.status_code, e.detail


code, out = call(routes.mqtt_clients_list, "sam")
check("a workflow viewer can list connections - with no password in it",
      code == 200 and out["clients"] and SECRET not in json.dumps(out)
      and out["editable"] is False)
check("...but not without signing in", call(routes.mqtt_clients_list, None)[0] == 401)
check("a viewer may not add one",
      call(routes.mqtt_clients_save, "sam", {"host": "1.2.3.4"})[0] == 403)
code, out = call(routes.mqtt_clients_save, "ada", {"name": "B2", "host": "10.9.9.9",
                                                    "username": "u", "password": SECRET})
check("an editor may, and the answer has no password",
      code == 200 and out["client"]["password_set"] and SECRET not in json.dumps(out))
check("a bad body is a 400", call(routes.mqtt_clients_save, "ada", {"port": "abc"})[0] == 400)
check("a connection in use is a 409 on delete",
      call(routes.mqtt_clients_delete, "ada", None, a["id"])[0] == 409)
check("...an unused one goes",
      call(routes.mqtt_clients_delete, "ada", None, out["client"]["id"])[0] == 200)
wfjsx = read("frontend", "src", "pages", "Workflow.jsx")
check("the inspector only hints when no connection is picked (no red outline)",
      wfjsx.count("required={false}") >= 3 and "None picked: this box's own broker" in wfjsx)
check("the master mounts the routes",
      "app.include_router(mqtt_clients_router)" in read("master_server.py"))

# == 6. the app: one client per connection ===================================================
from app import mqtt_bus                                                  # noqa: E402

made = []


class FakeClient:
    def __init__(self, client_id):
        self.client_id = client_id
        self.subs, self.unsubs, self.pubs = [], [], []
        self.login = None
        self.delay = None
        self.started = self.stopped = False
        made.append(self)

    def username_pw_set(self, u, p=None):
        self.login = (u, p)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.delay = (min_delay, max_delay)

    def connect_async(self, host, port, keepalive=60):
        self.where = (host, port, keepalive)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.stopped = True

    def disconnect(self):
        pass

    def subscribe(self, flt, qos=0):
        self.subs.append(flt)

    def unsubscribe(self, flt):
        self.unsubs.append(flt)

    def publish(self, topic, payload, qos=0, retain=False):
        self.pubs.append((topic, payload, retain))
        return types.SimpleNamespace(rc=0)

    def connect_now(self, rc=0):
        self.on_connect(self, None, {}, rc)

    def deliver(self, topic, payload):
        self.on_message(self, None, types.SimpleNamespace(topic=topic, payload=payload))


mqtt_bus.CLIENT_FACTORY = FakeClient
ROW = {"id": "mq_aaaa0001", "name": "PLC broker", "host": "10.0.0.5", "port": 1884,
       "username": "plant", "password": SECRET, "client_id": "", "keepalive_s": 30,
       "reconnect_s": 2}
mqtt_bus.set_clients([ROW])
check("a connection nobody uses never connects", made == [])
got = []
h1 = mqtt_bus.subscribe("mq_aaaa0001", "plc/+/parts", lambda t, p: got.append(("a", t, p)))
h2 = mqtt_bus.subscribe("mq_aaaa0001", "plc/#", lambda t, p: got.append(("b", t, p)))
check("two Subscribes and the connection make ONE client", len(made) == 1)
cl = made[0]
check("...logged in with the connection's login, keep-alive and backoff",
      cl.login == ("plant", SECRET) and cl.where == ("10.0.0.5", 1884, 30)
      and cl.delay == (2, mqtt_bus.RECONNECT_CAP_S))
check("...with a made-up id unique to this app",
      cl.client_id == f"irivai-{os.getpid()}-mq_aaaa0001")
cl.connect_now()
check("on connect it subscribes every topic once", sorted(cl.subs) == ["plc/#", "plc/+/parts"])
cl.deliver("plc/l1/parts", b"5")
cl.deliver("plc/l1/state", b"run")
check("each message goes to the subscribers whose filter matches",
      got == [("a", "plc/l1/parts", b"5"), ("b", "plc/l1/parts", b"5"),
              ("b", "plc/l1/state", b"run")], got)
mqtt_bus.publish("mq_aaaa0001", "line/count", "42", retain=True)
check("a Publish goes out on the same client", len(made) == 1
      and cl.pubs == [("line/count", "42", True)])
mqtt_bus.unsubscribe(h1)
check("dropping a Subscribe drops its topic from the broker", cl.unsubs == ["plc/+/parts"])
mqtt_bus.set_clients([{**ROW, "port": 1885}])
check("changing the connection builds a new client and stops the old",
      len(made) == 2 and cl.stopped and made[1].where[1] == 1885)
made[1].connect_now()
check("...and the remaining Subscribe moves across", made[1].subs == ["plc/#"])
mqtt_bus.set_clients([{**ROW, "port": 1886}])
check("a publish before the broker answers is queued, not lost (Yaya M1)",
      mqtt_bus.publish("mq_aaaa0001", "x", "1") == "queued")
mqtt_bus.set_clients([])
check("a deleted connection is closed", made[-1].stopped)
check("...and naming it is a clear error",
      raises(lambda: mqtt_bus.subscribe("mq_aaaa0001", "a", print), LookupError))
mqtt_bus.close_all()

from app import broker_login                                              # noqa: E402
broker_login.for_address = lambda host, port: ("boxuser", "boxpass") \
    if str(host) in ("127.0.0.1", "localhost") else None
mqtt_bus.set_clients([{"id": "mq_bbbb0002", "host": "127.0.0.1", "port": 1883,
                       "username": "", "password": ""}])
mqtt_bus.subscribe("mq_bbbb0002", "x", print)
check("this box's broker with no user name logs in with the box's own login",
      made[-1].login == ("boxuser", "boxpass"))
mqtt_bus.close_all()

# the app's MQTT Subscribe on a connection
from app import functions as app_fns                                      # noqa: E402
made.clear()
mqtt_bus.set_clients([ROW])
fn_a = types.SimpleNamespace(id="fa", params={"conn": "mq_aaaa0001", "topic": "plc/+/parts",
                                              "json_path": "count"})
fn_b = types.SimpleNamespace(id="fb", params={"conn": "mq_aaaa0001", "topic": "line/x"})
app_fns._ensure_subscriber(fn_a, 100.0)
app_fns._ensure_subscriber(fn_b, 100.0)
app_fns._ensure_subscriber(fn_a, 101.0)
check("two MQTT Subscribe rules on one connection share its one client",
      len(made) == 1 and app_fns._mqtt_state["fa"]["handle"] is not None
      and app_fns._mqtt_state["fa"]["thread"] is None)
made[0].connect_now()
made[0].deliver("plc/l2/parts", b'{"count": 7}')
check("...and read their messages as before (path, value, ok, raw for Debug)",
      app_fns._mqtt_state["fa"]["value"] == 7.0 and app_fns._mqtt_state["fa"]["ok"]
      and app_fns._mqtt_state["fa"]["topic"] == "plc/l2/parts")
app_fns._reap_subscribers(100.0 + app_fns.POLLER_TTL_S + 10)
check("a deleted rule leaves the connection", "fa" not in app_fns._mqtt_state
      and sorted(made[0].unsubs) == ["line/x", "plc/+/parts"])
fn_c = types.SimpleNamespace(id="fc", params={"conn": "mq_99999999", "topic": "a"})
app_fns._ensure_subscriber(fn_c, 500.0)
check("a rule on a connection that is gone waits and says so, without a storm",
      app_fns._mqtt_state["fc"]["handle"] is None
      and app_fns._mqtt_state["fc"]["retry_at"] == 500.0 + app_fns.MQTT_CONN_RETRY_S)
app_fns._reap_subscribers(10 ** 9)

from app import outputs as app_outputs                                    # noqa: E402
made[0].connect_now()
app_outputs._mqtt({"conn": "mq_aaaa0001", "topic": "line/count", "payload": "9"})
check("an MQTT Publish output sends through the connection's client",
      made[0].pubs[-1] == ("line/count", "9", False) and len(made) == 1)
mqtt_bus.close_all()

# == 7. with AI detection off ===================================================================
from app import run as app_run                                             # noqa: E402
cfg = {"ai": {"on": False}, "mqtt_clients": [ROW],
       "functions": [{"id": "fx", "type": "mqtt_sub", "name": "x",
                      "params": {"conn": "mq_aaaa0001", "topic": "a"}}],
       "outputs": [{"id": "wf_p", "type": "mqtt_publish", "conn": "mq_aaaa0001",
                    "topic": "b", "key": "fn_fx_value", "trigger": "any"}]}
off = app_run.ai_config(cfg)
check("AI off: the connections are still handed to the app",
      off["mqtt_clients"] == [ROW])
check("...the MQTT Subscribe still runs", "fx" not in off.get("_ai_inactive", []))
check("...and so does the MQTT Publish", [o["id"] for o in off["outputs"]] == ["wf_p"])
run_src = read("systems", "demo", "app", "run.py")
check("the app hands the table over at start and on every config change",
      run_src.count('mqtt_bus.set_clients(') == 2)

# == 8. the Broker page: where other devices reach it =========================================
from core.services import mqtt_broker                                      # noqa: E402
_saved = (mqtt_broker.shutil.which, mqtt_broker.subprocess.run)
mqtt_broker.shutil.which = lambda name: "/usr/bin/hostname"
mqtt_broker.subprocess.run = lambda *a, **k: types.SimpleNamespace(
    stdout="192.168.1.20 127.0.1.1 169.254.3.4 10.0.0.7 fe80::1 192.168.1.20\n")
check("Reachable at: this box's LAN addresses, not loopback, link-local or IPv6",
      mqtt_broker.lan_addresses() == ["192.168.1.20", "10.0.0.7"],
      mqtt_broker.lan_addresses())
mqtt_broker.shutil.which, mqtt_broker.subprocess.run = _saved
check("...handed to the page with the rest of the broker's answer",
      '"addresses": lan_addresses(),' in read("core", "services", "mqtt_broker.py"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
