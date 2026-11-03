# The MQTT nodes' test buttons, and the Debug node's MQTT readings.
#
# Asked for on 2026-09-29 (Tok Aba): "is there any way to check whether my
# topic publish is already correct ... for subscribe when I put the topic then
# please make it able to test also; and then please allow to show the data
# through debug nodes". Pinned against a fake broker standing in for paho:
# the round trip and every way it fails, the listen and its json_path
# preview, the limits, that no connection outlives its test, and that a
# password never comes back out.
import ast
import asyncio
import inspect
import io
import json
import os
import sys
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
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


from core.services import mqtt_test as t                         # noqa: E402

t.CONNECT_WAIT_S = 0.5
t.ECHO_WAIT_S = 0.5


def matches(flt, topic):
    fl, tl = flt.split("/"), topic.split("/")
    for i, part in enumerate(fl):
        if part == "#":
            return True
        if i >= len(tl) or (part != "+" and part != tl[i]):
            return False
    return len(fl) == len(tl)


class Broker:
    def __init__(self):
        self.reset()

    def reset(self, **kw):
        self.down = False
        self.silent = False
        self.rc = 0
        self.echo = True
        self.retained = []           # (topic, payload) handed to new subscribers
        self.clients = []
        self.logins = []
        self.__dict__.update(kw)


BROKER = Broker()


class Msg:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, payload, retain


class FakeClient:
    def __init__(self, client_id):
        self.client_id = client_id
        self.subs = []
        self.closed = False
        self.login = None
        self.on_connect = self.on_subscribe = self.on_message = None
        BROKER.clients.append(self)

    def username_pw_set(self, user, pw=None):
        self.login = (user, pw)
        BROKER.logins.append((user, pw))

    def connect(self, host, port, keepalive=60):
        if BROKER.down:
            raise ConnectionRefusedError(111, "refused")

    def loop_start(self):
        if not BROKER.silent:
            threading.Timer(0.01, lambda: self.on_connect(self, None, {}, BROKER.rc)).start()

    def subscribe(self, topic, qos=0):
        self.subs.append(topic)
        if self.on_subscribe:
            self.on_subscribe(self, None, 1, [qos])
        for tp, pl in BROKER.retained:
            if matches(topic, tp) and self.on_message:
                self.on_message(self, None, Msg(tp, pl, True))

    def publish(self, topic, payload, qos=0, retain=False):
        self.last_retain = retain
        if BROKER.echo:
            for c in BROKER.clients:
                if not c.closed and any(matches(s, topic) for s in c.subs) and c.on_message:
                    c.on_message(c, None, Msg(topic, payload))
        return types.SimpleNamespace(rc=0)

    def disconnect(self):
        self.closed = True

    def loop_stop(self):
        pass


t.CLIENT_FACTORY = FakeClient


def all_closed():
    return all(c.closed for c in BROKER.clients)


def pub(topic="line1/count", payload="42", login=None, retain=False, user="ada"):
    t._pub_busy.clear()
    return t.test_publish("127.0.0.1", 1883, topic, payload, login, retain, user)


def refusal(fn):
    try:
        fn()
    except t.TestRefused as e:
        return str(e)
    return ""


# -- test publish -----------------------------------------------------------
BROKER.reset()
r = pub()
check("a working broker: published and received back", r["ok"] and r["connected"]
      and r["published"] and r["received"] and r["rtt_ms"] is not None, r)
check("...shows what was sent and what came back",
      r["sent"] == "42" and r["received_payload"] == "42")
check("...and says nobody can tell who else listens", t.OTHER_SUBSCRIBERS in r["notes"])
check("...on its own client id, not the app's",
      BROKER.clients[-1].client_id.startswith("irivai-test-"))
check("...and hangs up", all_closed())

BROKER.reset(echo=False)
r = pub()
check("no echo: published, but it says it did not come back",
      r["published"] and not r["received"] and not r["ok"]
      and "did not come back" in r["message"], r)
check("...and hangs up", all_closed())

BROKER.reset(rc=5)
msg = refusal(lambda: pub())
check("a refused login says so", "refused the login" in msg, msg)
BROKER.reset(rc=135)
check("...in MQTT 5 terms too", "refused the login" in refusal(lambda: pub()))
BROKER.reset(rc=2)
check("another refusal gives its code", "code 2" in refusal(lambda: pub()))
check("...and hangs up", all_closed())
BROKER.reset(down=True)
msg = refusal(lambda: pub())
check("nothing listening: is the broker running?", "nothing is listening" in msg
      and "broker running" in msg, msg)
BROKER.reset(silent=True)
msg = refusal(lambda: pub())
check("connected but no MQTT answer: timed out", "timed out" in msg, msg)
check("...and hangs up", all_closed())
for topic in ("line1/+", "line1/#", "#", ""):
    check(f"publish to {topic!r} is refused before connecting",
          refusal(lambda: pub(topic=topic)) != "")
BROKER.reset()
r = pub(retain=True)
check("a retained node: the test is sent NOT retained, and says why",
      BROKER.clients[-1].last_retain is False
      and any("NOT retained" in n for n in r["notes"]))
BROKER.reset()
BROKER.retained = []
r = pub(login=("plant", "s3cret-pass"))
check("a login is used", BROKER.logins[-1] == ("plant", "s3cret-pass"))
check("...and never comes back in the answer", "s3cret-pass" not in json.dumps(r))

t._pub_busy.clear()
t.test_publish("127.0.0.1", 1883, "a/b", "1", None, False, "sam")
check("one at a time per user: an immediate second click is refused",
      "already running" in refusal(lambda: t.test_publish(
          "127.0.0.1", 1883, "a/b", "1", None, False, "sam")))
check("...another user is not held up",
      t.test_publish("127.0.0.1", 1883, "a/b", "1", None, False, "ada")["ok"])

# -- payloads -----------------------------------------------------------------
check("the node's value, as it would send it",
      t.publish_payload({"type": "mqtt_publish", "format": "value"}, 7) == "7")
check("...JSON when the node sends JSON",
      json.loads(t.publish_payload({"type": "mqtt_publish", "format": "json",
                                    "key": "k"}, 7))["value"] == 7)
check("...and the plain word 'test' when there is no value yet - never a dict",
      t.publish_payload({"type": "mqtt_publish"}, None) == "test"
      and t.publish_payload({"type": "mqtt_publish", "format": "json"}, None) == "test")
check("...which no MQTT Subscribe reads as a number",
      "error" in t.preview(t.SAMPLE_PAYLOAD))
check("the payload typed into the row is checked",
      t.payload_problem("42") == "" and t.payload_problem("x" * 1001) != ""
      and t.payload_problem("a\x00b") != "" and t.payload_problem(5) != "")

# -- a JSON object is not a number on MQTT (Yaya, 18c4480) --------------------
for raw in (b'{"test": true, "from": "x", "ts": 1}', b'[1, 2, 3]', b'{"a": 1}'):
    check(f"preview: {raw[:18]!r} with no path is no value, not its length",
          "not a number" in t.preview(raw).get("error", ""), t.preview(raw))
check("preview: a path onto an object is no value either",
      "not a number" in t.preview(b'{"a":{"b":1}}', "a").get("error", ""))

# -- who on this box would hear a test ----------------------------------------
for flt, topic, want in (("a/b", "a/b", True), ("a/+", "a/b", True), ("a/+", "a/b/c", False),
                         ("a/#", "a/b/c", True), ("a/#", "a", True), ("#", "x/y", True),
                         ("+/b", "a/b", True), ("a/b", "a/c", False), ("a", "a/b", False)):
    check(f"topic_matches({flt!r}, {topic!r}) is {want}", t.topic_matches(flt, topic) is want)
FNS = [{"id": "m1", "type": "mqtt_sub", "params": {"host": "localhost", "port": 1883,
                                                  "topic": "plc/+/parts"}},
       {"id": "m2", "type": "mqtt_sub", "params": {"host": "10.0.0.9", "port": 1883,
                                                  "topic": "plc/#"}},
       {"id": "m3", "type": "mqtt_sub", "enabled": False,
        "params": {"host": "127.0.0.1", "topic": "#"}},
       {"id": "m4", "type": "mqtt_sub", "params": {"host": "127.0.0.1", "port": 1884,
                                                  "topic": "#"}},
       {"id": "e1", "type": "expr", "params": {}}]
GRAPH = {"nodes": [{"type": "variable.mqtt", "data": {"fnId": "m1", "name": "Line parts"}}]}
check("this box's own Subscribe on the same broker and a matching filter is named",
      t.own_subscribers(FNS, GRAPH, "127.0.0.1", 1883, "plc/l1/parts") == ["Line parts"])
check("...not one on another broker, another port, or switched off",
      t.own_subscribers(FNS, GRAPH, "127.0.0.1", 1883, "other/topic") == [])
check("...and a remote broker is matched by its own address",
      t.own_subscribers(FNS, GRAPH, "10.0.0.9", 1883, "plc/x") == ["m2"])

# -- one broker, many names (Yaya, review of 6061106) -------------------------
for raw, want in (("", "127.0.0.1"), ("0.0.0.0", "127.0.0.1"), ("IRIV.local.", "iriv.local"),
                  ("[::1]", "::1"), (" 192.168.1.50 ", "192.168.1.50")):
    check(f"address {raw!r} reads as {want!r}", t._norm(raw) == want)
import socket as _socket                                           # noqa: E402
names = t.box_addresses()
host = _socket.gethostname().lower()
check("this box's own names count as this box",
      {"127.0.0.1", "localhost", "::1", host, host.split(".")[0] + ".local"} <= names,
      sorted(names))
OWN = frozenset({"127.0.0.1", "localhost", "::1", "iriv", "iriv.local", "192.168.1.50"})
for h1, h2, same in (("127.0.0.1", "192.168.1.50", True), ("localhost", "iriv.local", True),
                     ("IRIV", "127.0.0.1", True), ("", "192.168.1.50", True),
                     ("192.168.1.50", "192.168.1.51", False), ("10.0.0.9", "127.0.0.1", False)):
    check(f"{h1!r} and {h2!r} are {'the same' if same else 'different'} broker(s)",
          t._same_broker(h1, 1883, h2, 1883, OWN) is same)
check("...but never on different ports",
      t._same_broker("127.0.0.1", 1883, "192.168.1.50", 1884, OWN) is False)
t._box.update(at=time.monotonic(), names=OWN)
LAN = [{"id": "m9", "type": "mqtt_sub", "name": "Dock count",
        "params": {"host": "192.168.1.50", "port": 1883, "topic": "dock/#"}}]
check("a Subscribe on the box's LAN address hears a publish to 127.0.0.1",
      t.own_subscribers(LAN, {}, "127.0.0.1", 1883, "dock/1") == ["Dock count"])
check("every system on the box is asked, each named after its node",
      t.box_subscribers([("demo", "demo", FNS, GRAPH), ("dock", "dock", LAN, {})],
                        "iriv.local", 1883, "dock/2") == ["Dock count (in dock)"])
check("...this system's own nodes stay unlabelled",
      t.box_subscribers([("demo", "demo", FNS, GRAPH), ("dock", "dock", LAN, {})],
                        "127.0.0.1", 1883, "plc/l1/parts") == ["Line parts"])
t._box.update(at=0.0, names=frozenset())

# -- filters and the json_path preview ---------------------------------------
for ok_filter in ("a/b", "a/+/c", "a/#", "#", "+"):
    check(f"filter {ok_filter!r} is fine", t.filter_problem(ok_filter) == "")
for bad_filter in ("a/#/b", "a#", "a/b+", "", "x" * 300, "a\x00b"):
    check(f"filter {bad_filter[:12]!r} is refused", t.filter_problem(bad_filter) != "")
check("preview: a path into JSON", t.preview(b'{"data":{"count":5}}', "data.count")
      == {"value": 5.0})
check("preview: a list index", t.preview(b'{"r":[1,{"v":2.5}]}', "r.1.v") == {"value": 2.5})
check("preview: a bare number", t.preview(b" 12 ") == {"value": 12.0})
check("preview: true is 1", t.preview(b"true") == {"value": 1.0})
check("preview: a path that misses says so",
      "is not in this message" in t.preview(b'{"a":1}', "b")["error"])
check("preview: text with no path says to set one",
      "set a JSON path" in t.preview(b"hello")["error"])

# The preview must say what the NODE would make of it: same _dig/_as_number.
app_src = read("systems", "demo", "app", "functions.py")


def body_of(src, name):
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    stmts = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                            and isinstance(fn.body[0].value, ast.Constant)) else fn.body
    return [ast.dump(s) for s in stmts]


core_src = read("core", "services", "mqtt_test.py")
for name in ("_dig", "_as_number", "_mqtt_number"):
    check(f"{name} is the app's own, statement for statement",
          body_of(core_src, name) == body_of(app_src, name))

# -- the live app's MQTT Subscribe follows the same rule ----------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
try:
    from app import functions as app_fns                          # noqa: E402
except Exception as e:                                             # noqa: BLE001
    app_fns = None
    print(f"skip app subscriber checks ({type(e).__name__}: {e})")
if app_fns is not None:
    check("an API value's list is still its length (a feature there)",
          app_fns._as_number([1, 2, 3]) == 3.0)

    def drive(payloads, json_path=""):
        """Feed messages through the app's real _mqtt_loop on a fake paho."""
        stop = threading.Event()
        fake_mod = types.ModuleType("paho.mqtt.client")

        class C:
            def __init__(self, *a, **k):
                self.on_message = self.on_connect = None

            def username_pw_set(self, *a):
                pass

            def connect(self, *a, **k):
                pass

            def loop_forever(self, **k):
                for pl in payloads:
                    self.on_message(self, None, types.SimpleNamespace(
                        payload=pl, topic="plc/l1/parts"))
                stop.set()

            def disconnect(self):
                pass
        fake_mod.Client = C
        saved = {k: sys.modules.get(k) for k in ("paho", "paho.mqtt", "paho.mqtt.client")}
        sys.modules["paho"] = types.ModuleType("paho")
        sys.modules["paho.mqtt"] = types.ModuleType("paho.mqtt")
        sys.modules["paho.mqtt"].client = fake_mod
        sys.modules["paho.mqtt.client"] = fake_mod
        params = {"host": "127.0.0.1", "port": 1883, "topic": "plc/+/parts",
                  "json_path": json_path}
        sig = ("t",)
        app_fns._mqtt_state["tfn"] = {"sig": sig, "value": 7.0, "ok": True, "msgs": 0}
        try:
            app_fns._mqtt_loop("tfn", params, sig, stop)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return app_fns._mqtt_state.pop("tfn")

    st = drive([b'{"test": true, "from": "IRIV", "ts": 1}'])
    check("the app: a JSON object on a Subscribe with no path is NOT 3",
          st["value"] == 7.0 and st["ok"] is False, st)
    check("...but what arrived is still kept for Debug",
          st.get("topic") == "plc/l1/parts" and '"test"' in st.get("payload", ""))
    st = drive([b"12"])
    check("the app: a plain number still reads", st["value"] == 12.0 and st["ok"] is True)
    st = drive([b'{"data": {"count": 4}}'], "data.count")
    check("the app: a path onto a number still reads", st["value"] == 4.0)
    st = drive([b"test"])
    check("the app: the test word is no reading", st["value"] == 7.0 and st["ok"] is False)
    check("the app's Subscribe uses the MQTT rule, the API value does not",
          "value = _mqtt_number(_dig(payload" in app_src
          and "value = _dig(_api_request(params)" in app_src
          and "value = _as_number(value)" in app_src)

# -- listen -------------------------------------------------------------------
BROKER.reset(retained=[("plc/l1/parts", b'{"data":{"count":3}}'),
                       ("plc/l2/parts", b'{"other":1}')])
t._listens.clear()
snap = t.start_listen(("ada", "s/n1"), "127.0.0.1", 1883, "plc/+/parts", None,
                      "data.count", seconds=1, run_async=False)
check("a listen collects what arrives on a wildcard filter",
      snap["state"] == "done" and len(snap["messages"]) == 2, snap)
m1, m2 = snap["messages"]
check("...with topic and payload", m1["topic"] == "plc/l1/parts" and "count" in m1["payload"])
check("...and what the node's path makes of each",
      m1.get("value") == 3.0 and "is not in this message" in m2.get("error", ""), snap)
check("...marked retained when the broker handed it over as retained", m1["retained"])
check("...on its own client, hung up after", BROKER.clients[-1].client_id.startswith(
    "irivai-listen-") and all_closed())

BROKER.reset()
snap = t.start_listen(("ada", "s/n2"), "127.0.0.1", 1883, "quiet/topic", None, "",
                      seconds=1, run_async=False)
check("nothing arriving says so, with how to send something",
      snap["messages"] == [] and "mosquitto_pub -t 'quiet/topic' -m 1" in snap["hint"], snap)
BROKER.reset(rc=5)
snap = t.start_listen(("ada", "s/n3"), "127.0.0.1", 1883, "a/b", None, "",
                      seconds=1, run_async=False)
check("a refused login ends the listen with the reason",
      snap["state"] == "error" and "refused the login" in snap["error"])
check("a listen is at most LISTEN_MAX_S", t.Listen(("x", "y"), "h", 1, "a", None, "", 999)
      .seconds == t.LISTEN_MAX_S)

BROKER.reset()
t._listens.clear()
s1 = t.start_listen(("ada", "s/n1"), "127.0.0.1", 1883, "a/b", None, "", seconds=5)
check("one listen per user and node", "already listening" in refusal(
    lambda: t.start_listen(("ada", "s/n1"), "127.0.0.1", 1883, "a/b", None, "", 5)))
for i in range(t.MAX_LISTENS - 1):
    t.start_listen((f"u{i}", "s/n"), "127.0.0.1", 1883, "a/b", None, "", 5)
check("...and a box-wide cap", "too many" in refusal(
    lambda: t.start_listen(("zed", "s/n"), "127.0.0.1", 1883, "a/b", None, "", 5)))
check("somebody else's listen cannot be read", "not yours" in refusal(
    lambda: t.get_listen(s1["id"], "sam")))
time.sleep(0.2)
check("...its owner can read it while it runs", t.get_listen(s1["id"], "ada")["running"])
for lst in list(t._listens.values()):
    lst.stop()


def _all_stopped():
    return (all(not x["running"] for x in (lst.snapshot() for lst in t._listens.values()))
            and all_closed())


for _ in range(250):        # up to 5s for the listen threads to wind down
    if _all_stopped():
        break
    time.sleep(0.02)
check("Stop ends every listen and hangs up",
      all(not x["running"] for x in (lst.snapshot() for lst in t._listens.values()))
      and all_closed())
t._sweep(now=time.time() + t.LISTEN_KEEP_S + 1)
check("finished listens are swept", not t._listens)

# -- the listen cap is box-wide ------------------------------------------------
import tempfile as _tf                                             # noqa: E402
try:
    import fcntl                                                   # noqa: E402,F401
    posix = True
except ImportError:
    posix = False
check("the cap lives in shared slot files, not one process's memory",
      "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)" in core_src
      and '".mqtt-listen-slots"' in core_src and "_release_slot(self.slot)" in core_src)
if posix:
    folder = _tf.mkdtemp()
    held = [t._claim_slot(folder) for _ in range(t.MAX_LISTENS)]
    check("every slot can be claimed once", all(isinstance(h, int) for h in held))
    check("...then the box is full, for another process as much as this one",
          t._claim_slot(folder) is None)
    t._release_slot(held[0])
    again = t._claim_slot(folder)
    check("...and a finished listen frees its slot", isinstance(again, int))
    for h in held[1:] + [again]:
        t._release_slot(h)
else:
    check("no flock here (Windows): the in-process count is the fallback",
          t._claim_slot(_tf.mkdtemp()) == "local")

# -- whose login ----------------------------------------------------------------
import tempfile                                                    # noqa: E402
from core.services import mqtt_broker                              # noqa: E402
CFG = tempfile.mkdtemp()
mqtt_broker._save_login("plant", "box-pass-123", 1883, CFG)
check("the node's own login wins", t.login_for("127.0.0.1", 1883, ("me", "x"), CFG) == ("me", "x"))
check("this box's broker login for loopback", t.login_for("localhost", 1883, None, CFG)
      == ("plant", "box-pass-123"))
check("...never for another host", t.login_for("10.0.0.5", 1883, None, CFG) is None)
check("...never for another port", t.login_for("127.0.0.1", 1884, None, CFG) is None)

# -- the routes -------------------------------------------------------------------
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
from fastapi import HTTPException                                  # noqa: E402

try:
    from core.routes import mqtt_test as routes                    # noqa: E402
except Exception as e:                                             # noqa: BLE001
    routes = None
    print(f"skip route checks ({type(e).__name__}: {e})")

if routes is not None:
    allowed = {"ada"}

    def fake_require(request, cap):
        if request.state.user not in allowed:
            raise HTTPException(status_code=403, detail="no")

    routes.perm = types.SimpleNamespace(require=fake_require)
    # Two roots, like a box: the repo's systems dir and the store's. Each
    # holds another system with an MQTT Subscribe of its own.
    SYS = tempfile.mkdtemp()
    GEN = tempfile.mkdtemp()
    for root, slug in ((SYS, "demo"), (SYS, "packing"), (GEN, "dock"), (GEN, "plain")):
        os.makedirs(os.path.join(root, slug))
    WFS = tempfile.mkdtemp()
    with open(os.path.join(WFS, "packing.json"), "w") as f:
        json.dump({"version": 3, "workflows": [{"id": "main", "graph": {"nodes": [
            {"type": "variable.mqtt", "data": {"fnId": "p_1", "name": "Packed boxes"}}]}}]}, f)
    routes._system_context = lambda request: (SYS, "demo")
    routes.config = types.SimpleNamespace(generated_systems_dir=lambda: GEN)
    routes.workflow_service = types.SimpleNamespace(
        path_for=lambda slug: os.path.join(WFS, f"{slug}.json"))
    t._box.update(at=time.monotonic(),
                  names=frozenset({"127.0.0.1", "localhost", "::1", "192.168.1.50"}))
    routes._live_stats = lambda request: {"fn_count_value": 9}
    routes.dc = types.SimpleNamespace(get_outputs=lambda d, s: [
        {"id": "wf_pub1", "type": "mqtt_publish", "host": "127.0.0.1", "port": 1883,
         "topic": "line1/count", "key": "fn_count_value", "format": "value"},
        {"id": "wf_pub2", "type": "mqtt_publish", "host": "localhost", "port": 1883,
         "topic": "plc/l9/parts", "key": "fn_missing_value", "format": "value"},
        {"id": "wf_mail1", "type": "email"}])
    routes._get_store = lambda d, s: ({"workflows": [{"graph": {"nodes": [
        {"id": "sub1", "type": "variable.mqtt",
         "data": {"fnId": "m_1", "name": "Line parts"}}]}}]}, None)
    routes._the_workflow = lambda store: store["workflows"][0]
    OTHER_FNS = {
        "packing": [{"id": "p_1", "type": "mqtt_sub", "params": {
            "host": "192.168.1.50", "port": 1883, "topic": "plc/#"}}],
        "dock": [{"id": "d_1", "type": "mqtt_sub", "name": "Dock count", "params": {
            "host": "localhost", "port": 1884, "topic": "#"}}],
        "plain": [{"id": "x", "type": "expr", "params": {}}],
    }
    routes.functions_service = types.SimpleNamespace(get_functions=lambda d, s: [
        {"id": "m_1", "type": "mqtt_sub", "params": {
            "host": "127.0.0.1", "port": 1883, "topic": "plc/+/parts",
            "json_path": "data.count", "username": "nodeuser",
            "password": "node-pass-9"}}] if s == "demo" else OTHER_FNS.get(s, []))

    def call(handler, user, body=None, **query):
        async def _json():
            return body or {}
        req = types.SimpleNamespace(
            state=types.SimpleNamespace(user=user),
            app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)),
            json=_json)
        try:
            return 200, asyncio.run(handler(req, **query))
        except HTTPException as e:
            return e.status_code, e.detail

    BROKER.reset()
    t._pub_busy.clear()
    code, out = call(routes.mqtt_test_publish, "ada", {"node": "pub1"})
    check("the route tests the SAVED node, with its live value",
          code == 200 and out["ok"] and out["sent"] == "9"
          and out["topic"] == "line1/count" and out["sample"] is False, out)
    check("...logged in with this box's broker login, never shown",
          out["login"] == "set" and "box-pass-123" not in json.dumps(out)
          and BROKER.logins[-1] == ("plant", "box-pass-123"))
    check("one listener reads in the singular",
          routes._warning(["Line parts"]) == "this box's own MQTT Subscribe 'Line parts' "
          "will receive this message and may act on it")
    code, out = call(routes.mqtt_test_publish, "ada", {"node": "pub1", "dry": True})
    check("a dry run says what it would send and does not connect",
          code == 200 and out["dry"] and out["payload"] == "9"
          and out["warning"] == "", out)
    n_clients = len(BROKER.clients)
    code, out = call(routes.mqtt_test_publish, "ada", {"node": "pub2", "dry": True})
    check("a topic this box's own Subscribe hears is named, before sending",
          code == 200 and out["subscribers"][0] == "Line parts"
          and out["warning"] == ("this box's own MQTT Subscribe nodes 'Line parts' and "
                                 "'Packed boxes (in packing)' will receive this message "
                                 "and may act on it")
          and out["payload"] == "test" and out["sample"] is True
          and len(BROKER.clients) == n_clients, out)
    check("...and so is one in ANOTHER system on the box's LAN address, by its node name",
          out["subscribers"] == ["Line parts", "Packed boxes (in packing)"], out["subscribers"])
    check("...but not one on another port, or a system with no Subscribe",
          not any("dock" in x or "plain" in x for x in out["subscribers"]))
    t._pub_busy.clear()
    code, detail = call(routes.mqtt_test_publish, "ada", {"node": "pub2"})
    check("...and sending it needs the second click", code == 409
          and "confirm" in detail and len(BROKER.clients) == n_clients, detail)
    t._pub_busy.clear()
    code, out = call(routes.mqtt_test_publish, "ada",
                     {"node": "pub2", "confirm": True, "payload": "hello 1"})
    check("...with it, the edited payload goes out",
          code == 200 and out["sent"] == "hello 1" and out["received"], out)
    t._pub_busy.clear()
    check("a bad payload is refused",
          call(routes.mqtt_test_publish, "ada", {"node": "pub1", "payload": "a\x00"})[0] == 400)
    t._pub_busy.clear()
    check("an account without workflow.edit may not test",
          call(routes.mqtt_test_publish, "sam", {"node": "pub1"})[0] == 403)
    check("an unsaved node says save first",
          call(routes.mqtt_test_publish, "ada", {"node": "nope"})[0] == 404)
    check("a node that is not MQTT is refused",
          call(routes.mqtt_test_publish, "ada", {"node": "mail1"})[0] == 400)
    check("the request cannot name a broker - only a node",
          "host" not in inspect.getsource(routes.mqtt_test_publish).split("body")[0]
          and 'body.get("host")' not in read("core", "routes", "mqtt_test.py"))
    t._listens.clear()
    BROKER.reset(retained=[("plc/l1/parts", b'{"data":{"count":4}}')])
    code, out = call(routes.mqtt_test_listen, "ada", {"node": "sub1", "seconds": 1})
    check("the listen route starts on the node's saved topic",
          code == 200 and out["topic"] == "plc/+/parts" and out["json_path"] == "data.count", out)
    check("...with the node's own login, never shown",
          "node-pass-9" not in json.dumps(out))
    listen_id = out["id"]
    for _ in range(250):    # up to 5s for the retained message to arrive
        code, out = call(routes.mqtt_test_poll, "ada", id=listen_id)
        if code != 200 or out.get("messages"):
            break
        time.sleep(0.02)
    check("...and the poll shows what arrived", code == 200
          and out["messages"] and out["messages"][0]["value"] == 4.0, out)
    allowed.add("bob")               # may edit the workflow, did not start it
    check("...to its owner only", call(routes.mqtt_test_poll, "bob", id=listen_id)[0] == 404)
    allowed.discard("bob")
    check("a listen needs workflow.edit too",
          call(routes.mqtt_test_listen, "sam", {"node": "sub1"})[0] == 403)
    check("a Subscribe that is not saved says so",
          call(routes.mqtt_test_listen, "ada", {"node": "zzz"})[0] == 404)

check("another system's graph is only READ (not _get_store, which may write)",
      "_get_store(root" not in read("core", "routes", "mqtt_test.py")
      and "def _graph_readonly" in read("core", "routes", "mqtt_test.py"))
check("the master mounts the test routes",
      "app.include_router(mqtt_test_router)" in read("master_server.py"))

# -- the Debug node's MQTT readings ---------------------------------------------
fsrc = read("systems", "demo", "app", "functions.py")
check("MQTT Subscribe publishes its last raw payload, topic and time",
      'self._stats[prefix + "payload"]' in fsrc and 'self._stats[prefix + "topic"]' in fsrc
      and 'self._stats[prefix + "at"]' in fsrc)
check("...kept even when the value path cannot read the message",
      "st.update(ok=False, **seen)" in fsrc)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo", "app"))
try:
    import outputs as app_outputs                                  # noqa: E402
except Exception as e:                                             # noqa: BLE001
    app_outputs = None
    print(f"skip outputs import ({type(e).__name__}: {e})")
if app_outputs is not None:
    o = {"id": "wf_pubx", "type": "mqtt_publish"}
    app_outputs._attempt(o, lambda: None, note="sent 42 to line1/count")
    tel = app_outputs.telemetry([o])
    check("a publish says what went out and where, for Debug",
          tel.get("out_wf_pubx_note") == "sent 42 to line1/count", tel)
check("MQTT Publish hands its note over",
      # "sent ..." or "queued ..." (Yaya M1: a publish while the MQTT
      # Connection connects is queued, and the note says so).
      'else f"sent {where}"' in read("systems", "demo", "app", "outputs.py")
      and "f\"{str(doc['payload'])[:120]} to " in read("systems", "demo", "app",
                                                       "outputs.py"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
