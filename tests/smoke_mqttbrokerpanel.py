# Settings > MQTT Broker: the root helper, the service, the routes, the app's
# login pickup, and the watch that tells the admins the broker stopped.
#
# Asked for on 2026-09-29 (Tok Aba): a broker notice on the Workflow page, and
# a place to set the broker up, with security. The security rules are the
# point of most of these checks: no login => loopback only; a login => every
# interface, no anonymous clients, a HASHED password file; the password never
# comes back out of any route; and the root helper trusts nothing it is sent.
import asyncio
import io
import json
import os
import pathlib
import re
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


import mqtt_apply as helper                                   # noqa: E402
from core.services import mqtt_broker as broker              # noqa: E402


def refused(req):
    try:
        helper.validate(req)
    except helper.Refused as e:
        return str(e)
    return ""


GOOD = {"action": "apply", "port": 1883, "ws_port": None,
        "username": "", "password": ""}

# -- the helper's gate ------------------------------------------------------
check("a plain local-only request passes", refused(GOOD) == "")
check("a secured request passes",
      refused({**GOOD, "username": "plant.line-1_a", "password": "s3cret pass!"}) == "")
check("a WebSocket port passes", refused({**GOOD, "ws_port": 9001}) == "")
check("install needs nothing else", refused({"action": "install"}) == "")
for port, why in ((0, "zero"), (-1, "negative"), (65536, "too big"),
                  (10 ** 12, "huge"), ("1883", "a string"), (True, "a bool"),
                  (18.83, "a float"), (None, "missing")):
    check(f"MQTT port refused when {why}", refused({**GOOD, "port": port}) != "")
for port in (5100, 5101, 5110, 5119, 8554, 8889, 9997, 5432, 22):
    check(f"the box's own port {port} is refused",
          "used by the box" in refused({**GOOD, "port": port}))
check("...and as a WebSocket port too",
      "used by the box" in refused({**GOOD, "ws_port": 5110}))
check("the WebSocket port must differ", refused({**GOOD, "ws_port": 1883}) != "")
for name in ("a\nb", "a b", "a:b", 'a"b', "a'b", "a;b", "", "x" * 65, "ü",
             "a\rpassword_file /etc/shadow", "../../x", "a\x00b"):
    r = refused({**GOOD, "username": name, "password": "longenough"})
    check(f"username {name!r} refused", r != "", r)
for pw in ("short", "x" * 129, "pass\nword1", "pass\rword1", "pass\tword1",
           "password\x00", "ok-ish\x7fword"):
    check(f"password {pw[:12]!r}... refused",
          refused({**GOOD, "username": "plant", "password": pw}) != "")
check("a password with quotes, colons and spaces is fine (it is hashed)",
      refused({**GOOD, "username": "plant", "password": "a:b 'c\" $d"}) == "")
check("a username without a password is refused",
      refused({**GOOD, "username": "plant"}) != "")
check("a password without a username is refused",
      refused({**GOOD, "password": "longenough"}) != "")
check("unknown fields are refused", refused({**GOOD, "conf": "listener 1"}) != "")
check("an unknown action is refused", refused({**GOOD, "action": "exec"}) != "")
check("a non-object is refused", refused(["apply"]) != "")
check("text fields must be text", refused({**GOOD, "username": 5, "password": 5}) != "")

# -- the helper's rendering ------------------------------------------------
loop = helper.render_conf(helper.validate({**GOOD, "ws_port": 9001}))
check("no login: every listener on loopback",
      "listener 1883 127.0.0.1\n" in loop and "listener 9001 127.0.0.1\n" in loop
      and not re.search(r"^listener \d+\s*$", loop, re.M), loop)
check("...anonymous, no password file",
      "allow_anonymous true" in loop and "password_file" not in loop)
check("...and the WebSocket listener says so", "protocol websockets" in loop)
sec = helper.render_conf(helper.validate(
    {**GOOD, "port": 1884, "username": "plant", "password": "longenough"}))
check("a login: every interface", re.search(r"^listener 1884$", sec, re.M)
      and not re.search(r"^listener .*127\.0\.0\.1", sec, re.M), sec)
check("...no anonymous clients, a password file",
      "allow_anonymous false" in sec
      and f"password_file {helper.PASSWD_PATH}" in sec)
check("...and neither the name nor the password is in the conf",
      "plant" not in sec and "longenough" not in sec)
check("the service reads the helper's conf back (local)",
      broker.parse_conf(loop) == {"port": 1883, "ws_port": 9001, "secured": False,
                                   "local_only": True}, broker.parse_conf(loop))
check("...and (secured)",
      broker.parse_conf(sec) == {"port": 1884, "ws_port": None, "secured": True,
                                  "local_only": False}, broker.parse_conf(sec))
inst = read("scripts", "install.sh")
inst_conf = inst[inst.index("<<'MQTTCONF'") + 12:inst.index("\nMQTTCONF\n")]
check("...and the installer's conf", broker.parse_conf(inst_conf)["local_only"]
      and broker.parse_conf(inst_conf)["port"] == 1883)

# -- the helper against a fake box -----------------------------------------
tmp = pathlib.Path(tempfile.mkdtemp())
helper.CONF_DIR = str(tmp)
helper.CONF_PATH = str(tmp / "irivai.conf")
helper.PASSWD_PATH = str(tmp / "irivai.passwd")
# The helper runs as root, where /run is its tmpfs; a test runner is not
# root, and a Linux runner's /run exists but refuses it (CI, 2026-09-29).
helper.PLAIN_DIR = str(tmp)
helper.shutil = types.SimpleNamespace(which=lambda *a, **k: "/usr/sbin/x",
                                      rmtree=lambda *a, **k: None)
helper._restart = lambda: None
helper._journal_tail = lambda: "Error: bad listener"
helper._mosquitto_gid = lambda: None
helper.held_by = lambda port: ""
if not hasattr(os, "fchmod"):
    os.fchmod = lambda fd, mode: None                          # older Windows Python

(tmp / "irivai.conf").write_text("OLD\n")
helper._came_up = lambda port, budget=8.0: False
try:
    helper.apply(helper.validate({**GOOD, "port": 1885}))
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("a broker that does not come up is rolled back",
      (tmp / "irivai.conf").read_text() == "OLD\n" and "put back" in msg, msg)
check("...and says what mosquitto said", "bad listener" in msg)

helper._came_up = lambda port, budget=8.0: True
r = helper.apply(helper.validate({**GOOD, "port": 1885}))
check("a broker that comes up keeps the new conf",
      r["ok"] and "listener 1885 127.0.0.1" in (tmp / "irivai.conf").read_text())

helper.held_by = lambda port: "node-red"
try:
    helper.apply(helper.validate({**GOOD, "port": 1883}))
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("a port Node-RED holds is refused, by name", msg == "Port 1883 is used by node-red", msg)
try:
    helper.install({})
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("...and install will not take it either", "used by node-red" in msg, msg)
helper.held_by = lambda port: "mosquitto"
check("...while mosquitto on its own port is not in the way",
      helper.apply(helper.validate({**GOOD, "port": 1883}))["ok"])

before = (tmp / "irivai.conf").read_text()
try:
    helper.apply(helper.validate({**GOOD, "username": "plant", "password": "longenough"}))
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("no mosquitto group: refused, and the conf is left as it was",
      "group" in msg and (tmp / "irivai.conf").read_text() == before, msg)


class _R:
    def __init__(self, rc=0):
        self.returncode, self.stdout, self.stderr = rc, "", ""


helper.shutil = types.SimpleNamespace(which=lambda *a, **k: "/usr/bin/mosquitto_passwd",
                                      rmtree=__import__("shutil").rmtree)
helper._run = lambda argv, **k: _R(0)                           # "hashes" nothing
try:
    helper.hashed_passwd("plant", "longenough")
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("a password file mosquitto_passwd did not hash is never written",
      "did not hash" in msg, msg)

src = read("scripts", "mqtt_apply.py")
check("the helper never uses a shell", "shell=True" not in src and "os.system" not in src
      and "os.popen" not in src)
check("the helper hands mosquitto_passwd no password on argv",
      '[tool, "-U", plain]' in src)
check("the clear password file lives on tmpfs, not a TMPDIR the caller picks",
      'PLAIN_DIR = "/run"' in src and "dir=PLAIN_DIR" in src
      and 'os.environ.pop(name, None)' in src)
check("one change at a time, box-wide (hub and masters each mount the page)",
      "LOCK_EX | fcntl.LOCK_NB" in src and "O_NOFOLLOW" in src
      and helper.LOCK_PATH.startswith("/run/"))
check("an install never removes another package to make room",
      '"--no-remove"' in src)
check("the helper answers with a message only, never the request",
      "print(json.dumps(result))" in src and "print(raw" not in src
      and "print(req" not in src)

# -- the service mirrors the gate ------------------------------------------
check("both copies reserve the same ports", broker.RESERVED_PORTS == helper.RESERVED_PORTS)
check("...and take the same usernames",
      broker.USERNAME_RE.pattern == helper.USERNAME_RE.pattern
      and (broker.PASSWORD_MIN, broker.PASSWORD_MAX)
      == (helper.PASSWORD_MIN, helper.PASSWORD_MAX))

CFG = tempfile.mkdtemp()


def bad_body(body):
    try:
        broker.build_request(body, CFG)
    except ValueError as e:
        return str(e)
    return ""


check("the form's port may arrive as text", broker.build_request(
    {"port": "1883", "ws_port": ""}, CFG)["port"] == 1883)
check("blank WebSocket port means off", broker.build_request(
    {"port": 1883, "ws_port": ""}, CFG)["ws_port"] is None)
for body, why in (({"port": "18 83"}, "spaces"), ({"port": "-1"}, "negative"),
                  ({"port": "99999"}, "huge"), ({"port": "5100"}, "the hub"),
                  ({"port": 1883, "ws_port": "1883"}, "same ports"),
                  ({"port": True}, "a bool"),
                  ({"port": 1883, "secure": True, "username": "a b",
                    "password": "longenough"}, "bad name"),
                  ({"port": 1883, "secure": True, "username": "plant",
                    "password": "x\ny" * 5}, "newline"),
                  ({"port": 1883, "secure": True, "username": "plant"}, "no password")):
    check(f"the service refuses {why}", bad_body(body) != "")
check("secure off drops any login the form still carried",
      broker.build_request({"port": 1883, "secure": False, "username": "plant",
                            "password": "longenough"}, CFG)["username"] == "")

sent = []
broker._run_helper = lambda req, timeout: (sent.append(req) or (True, "broker running"))
broker.status = lambda force=False: {"state": "running", "port": 1883}
ok, _ = broker.apply({"port": 1883, "secure": True, "username": "plant",
                      "password": "longenough"}, CFG)
check("a save reaches the helper with the login", ok and sent[-1]["password"] == "longenough")
login_file = broker.login_path(CFG)
check("...and the app's copy is kept in a .local.toml",
      login_file.name == "mqtt_broker.local.toml" and login_file.exists())
if os.name != "nt":
    check("...at 0600", (login_file.stat().st_mode & 0o777) == 0o600,
          oct(login_file.stat().st_mode))
ok, _ = broker.apply({"port": 1883, "secure": True, "username": "plant",
                      "password": ""}, CFG)
check("a blank password keeps the stored one", sent[-1]["password"] == "longenough")
check("...but not for a different username",
      bad_body({"port": 1883, "secure": True, "username": "other", "password": ""})
      == "enter a password")
pub = json.dumps(broker.public(CFG))
check("the page's payload never carries the password",
      "longenough" not in pub and '"password_set": true' in pub, pub)
broker._run_helper = lambda req, timeout: (False, "nope")
broker.apply({"port": 1883, "secure": False}, CFG)
check("a refused save leaves the stored login alone",
      "longenough" in login_file.read_text())

# -- the routes ------------------------------------------------------------
if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

    class HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException = HTTPException
    fa.Request = object
    sys.modules["fastapi"] = fa
from fastapi import HTTPException                              # noqa: E402
from core.routes import mqtt_broker as routes                 # noqa: E402

routes.auth_service = types.SimpleNamespace(is_site_admin=lambda u, c: u == "ada")
routes.audit_service = types.SimpleNamespace(record=lambda **k: sent.append(("audit", k)))
broker._run_helper = lambda req, timeout: (True, "broker running")


def call(handler, user, body=None):
    async def _json():
        return body or {}
    req = types.SimpleNamespace(
        state=types.SimpleNamespace(user=user),
        app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)),
        client=types.SimpleNamespace(host="10.0.0.9"), json=_json)
    try:
        return 200, asyncio.run(handler(req))
    except HTTPException as e:
        return e.status_code, e.detail


check("status needs a session", call(routes.broker_status, None)[0] == 401)
code, out = call(routes.broker_status, "sam")
check("...any signed-in account may read it, and is told it cannot manage",
      code == 200 and out["can_manage"] is False)
check("...an admin is told it can", call(routes.broker_status, "ada")[1]["can_manage"])
for name, h in (("read the settings", routes.broker_get),
                ("save", routes.broker_save), ("install", routes.broker_install)):
    check(f"an operator may not {name}", call(h, "sam", {"port": 1883})[0] == 403)
code, out = call(routes.broker_save, "ada", {"port": 1883, "secure": True,
                                             "username": "plant", "password": "longenough"})
check("an admin may save, and the answer has no password",
      code == 200 and "longenough" not in json.dumps(out), out)
audits = [x[1] for x in sent if isinstance(x, tuple)]
check("...the save is audited without the login",
      audits and audits[-1]["action"] == "mqtt.broker_settings"
      and "longenough" not in json.dumps(audits[-1]) and "plant" not in json.dumps(audits[-1]))
check("a bad form is a 400, not a helper call",
      call(routes.broker_save, "ada", {"port": 5100})[0] == 400)

# -- status parsing --------------------------------------------------------
import importlib                                                 # noqa: E402
broker = importlib.reload(broker)


def probe(conf, listening, active, owner="", installed=True, nodered=False):
    broker._read_conf = lambda: conf
    broker._listening = lambda port, timeout=0.3: listening
    broker._unit_active = lambda u: active if u == "mosquitto" else nodered
    broker._ss_owner = lambda port: owner
    broker.mosquitto_installed = lambda: installed
    broker.helper_installed = lambda: True
    return broker._probe()


s = probe(loop, True, True)
check("running + loopback conf = local only",
      s["state"] == "running" and s["mode"] == "local"
      and s["message"] == "MQTT broker: running on :1883 (local only)", s)
s = probe(sec, True, True)
check("running + login = secured", s["mode"] == "secured" and s["port"] == 1884, s)
s = probe(None, True, False, nodered=True, installed=False)
check("Node-RED holding the port is named",
      s["state"] == "other" and s["message"] == "MQTT broker: port 1883 is used by node-red", s)
check("not installed", probe(None, False, False, installed=False)["state"] == "not_installed")
check("installed but stopped", probe(loop, False, False)["state"] == "down")

# -- the watch -------------------------------------------------------------
posts = []
broker.status = lambda force=False: probe(loop, False, False)
first = broker.watch_step(CFG, notify=lambda *a, **k: posts.append((a, k)))
second = broker.watch_step(CFG, notify=lambda *a, **k: posts.append((a, k)))
third = broker.watch_step(CFG, notify=lambda *a, **k: posts.append((a, k)))
check("one down look is a restart, not news", first is False)
check("two in a row tell the admins, once", second is True and third is False
      and len(posts) == 1 and posts[0][1].get("tab") == "mqtt", posts)
broker.status = lambda force=False: probe(loop, True, True)
broker.watch_step(CFG, notify=lambda *a, **k: posts.append((a, k)))
broker.status = lambda force=False: probe(None, True, False, nodered=True)
for _ in range(3):
    broker.watch_step(CFG, notify=lambda *a, **k: posts.append((a, k)))
check("a broker this platform does not manage is never reported", len(posts) == 1)
from core.services import inbox                                   # noqa: E402
check("the notice's link is a tab the inbox accepts", "mqtt" in inbox.TABS)

# -- the app picks up the login -------------------------------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo", "app"))
import broker_login                                              # noqa: E402
os.environ[broker_login.ENV] = str(login_file)
check("the app finds this box's login for 127.0.0.1",
      broker_login.for_address("127.0.0.1", 1883) == ("plant", "longenough"))
check("...and for localhost", broker_login.for_address("localhost", "1883") is not None)
check("...never for another host", broker_login.for_address("10.0.0.5", 1883) is None)
check("...never for another port", broker_login.for_address("127.0.0.1", 1884) is None)
broker.export_login(CFG)
check("the hub and masters export the path, not the password",
      os.environ[broker.LOGIN_ENV] == str(login_file)
      and "longenough" not in os.environ[broker.LOGIN_ENV])
check("Publish uses it", "broker_login.for_address(host, port)"
      in read("systems", "demo", "app", "outputs.py"))
check("Subscribe uses it", "broker_login.for_address(params.get(\"host\")"
      in read("systems", "demo", "app", "functions.py"))

# -- install.sh ------------------------------------------------------------
check("the installer puts the helper outside the release tree, root-owned",
      'install -m 755 "${SRC_DIR}/scripts/mqtt_apply.py" /usr/local/lib/irivai/mqtt-apply.py' in inst
      and "chown root:root /usr/local/lib/irivai/mqtt-apply.py" in inst)
rules = re.findall(r"NOPASSWD: (.*mqtt-apply\.py.*)", inst)
check("exactly one sudoers entry names it, with no room for arguments",
      rules == ["/usr/bin/python3 -I /usr/local/lib/irivai/mqtt-apply.py"], rules)
check("...and it is the command the service runs",
      " ".join(broker.HELPER_ARGV[2:]) == rules[0] if rules else False)
check("...validated by visudo before it is used",
      "visudo -cf /etc/sudoers.d/irivai-mqtt.tmp" in inst)
check("the helper's line endings are normalised with the others",
      "/usr/local/lib/irivai/mqtt-apply.py \\" in inst)
check("the helper has LF line endings in the tree",
      b"\r\n" not in open(os.path.join(ROOT, "scripts", "mqtt_apply.py"), "rb").read())
for name in ("hub_server.py", "master_server.py"):
    s = read(name)
    check(f"{name} mounts the routes and exports the login path",
          "app.include_router(mqtt_broker_router)" in s and "export_login(" in s)
check("only the hub watches", "start_watch(" in read("hub_server.py")
      and "start_watch(" not in read("master_server.py"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
