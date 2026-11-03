# The hub installs a missing MQTT broker by itself, at start.
#
# Asked for on 2026-09-29 (Tok Aba): "ensure when installing new version will
# automatically install the broker". An over-the-air update ends with the hub
# restarting, so hub start is where it happens -- through the same root
# helper and the same {"action": "install"} as the panel's button. Pinned
# here: it installs once and not again, a failure backs off, --no-mqtt and
# the panel's switch are honoured, a port somebody else holds is left alone,
# a box without the helper is told ONCE what to run (and root is never
# worked around), and none of it holds up the hub's startup.
import asyncio
import io
import os
import re
import sys
import tempfile
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


from core.services import mqtt_broker as broker                  # noqa: E402

box = {"linux": True, "installed": False, "listening": False, "helper": True,
       "install_ok": True, "calls": 0}
posts = []
broker._on_linux = lambda: box["linux"]
broker.mosquitto_installed = lambda: box["installed"]
broker._listening = lambda port, timeout=0.3: box["listening"]
broker.helper_allowed = lambda: box["helper"]
broker._service_user = lambda: "pi"


def fake_install(config_dir=None):
    box["calls"] += 1
    if box["install_ok"]:
        box["installed"] = True
        return True, "broker installed"
    return False, "E: Unable to locate package mosquitto"


broker.install = fake_install


def step(cfg, version="1.0.9", now=None):
    return broker.auto_install_step(cfg, version, now=now,
                                    notify=lambda *a, **k: posts.append((a, k)))


def fresh():
    box.update(linux=True, installed=False, listening=False, helper=True,
               install_ok=True, calls=0)
    posts.clear()
    return tempfile.mkdtemp()


# -- the ordinary case ------------------------------------------------------
cfg = fresh()
check("a box with the helper and no broker installs one", step(cfg) == "ok"
      and box["calls"] == 1)
check("...and tells the admins, local only",
      len(posts) == 1 and posts[0][0][1] == "MQTT broker installed, local only"
      and posts[0][1].get("tab") == "mqtt", posts)
check("...and does not install again at the next start",
      step(cfg) == "present" and box["calls"] == 1 and len(posts) == 1)
box["installed"] = False                     # say it vanished, same version
check("...nor on a restart of the same version that already did it",
      step(cfg) == "done" and box["calls"] == 1)
check("the outcome is recorded in a .local.toml the update carries",
      broker._auto_state(cfg).get("last_result") == "ok"
      and os.path.isfile(os.path.join(cfg, "mqtt_autoinstall.local.toml")))

# -- a failure backs off ----------------------------------------------------
cfg = fresh()
box["install_ok"] = False
t0 = 1_000_000.0
check("a failed install says so", step(cfg, now=t0) == "failed" and box["calls"] == 1)
check("...with the reason and what to do",
      "Unable to locate package" in posts[-1][0][2]
      and "apt-get install -y mosquitto" in posts[-1][0][2], posts[-1])
check("...and is not retried on the next restart",
      step(cfg, now=t0 + 60) == "backoff" and box["calls"] == 1)
check("...nor within six hours",
      step(cfg, now=t0 + broker.AUTO_RETRY_S - 1) == "backoff" and box["calls"] == 1)
check("...but is after six hours",
      step(cfg, now=t0 + broker.AUTO_RETRY_S + 1) == "failed" and box["calls"] == 2)
check("...and at once on a new version",
      step(cfg, version="1.1.0", now=t0 + broker.AUTO_RETRY_S + 2) == "failed"
      and box["calls"] == 3)

# -- opting out -------------------------------------------------------------
cfg = fresh()
with open(os.path.join(cfg, "mqtt_autoinstall.local.toml"), "w") as f:
    f.write("auto_install = false\n")         # exactly what --no-mqtt writes
check("the installer's --no-mqtt marker is honoured",
      step(cfg) == "opted_out" and box["calls"] == 0 and not posts)
cfg = fresh()
broker.set_auto_install(False, cfg)
check("...and so is the panel's switch", step(cfg) == "opted_out" and box["calls"] == 0)
broker.set_auto_install(True, cfg)
check("...which turns back on", step(cfg) == "ok" and box["calls"] == 1)
check("the page is told which way the switch is",
      broker.auto_install_enabled(cfg) is True)

# -- somebody else's broker, somebody else's box -----------------------------
cfg = fresh()
box["listening"] = True
check("a port already answered (Node-RED on a dev box) is left alone",
      step(cfg) == "port_held" and box["calls"] == 0 and not posts)
cfg = fresh()
box["linux"] = False
check("a developer's Windows checkout does nothing", step(cfg) == "skipped"
      and box["calls"] == 0)

# -- a box that predates the helper -----------------------------------------
cfg = fresh()
box["helper"] = False
check("no helper: nothing is installed", step(cfg) == "no_helper" and box["calls"] == 0)
check("...one notice, with the commands to run",
      len(posts) == 1
      and posts[0][0][1] == "This box needs a one-time setup step to get its MQTT broker"
      and all(line in posts[0][0][2] for line in broker.setup_commands("pi")), posts)
check("...long enough to hold them", posts[0][1].get("limit", 0) >= len(posts[0][0][2]))
check("...the commands install the helper and a visudo-checked rule",
      "/usr/local/lib/irivai/mqtt-apply.py" in posts[0][0][2]
      and "visudo -cf" in posts[0][0][2]
      and "pi ALL=(root) NOPASSWD: /usr/bin/python3 -I /usr/local/lib/irivai/mqtt-apply.py"
      in posts[0][0][2])
step(cfg)
step(cfg)
check("...and not again on every restart", len(posts) == 1)
step(cfg, version="1.1.0")
check("...once per update, not once per restart", len(posts) == 2)
src = read("core", "services", "mqtt_broker.py")
check("no route around root: the only privileged call is the helper",
      sorted(re.findall(r'[\[(]"sudo", "[^"]+", "[^"]+"', src))
      == ['("sudo", "-n", "/usr/bin/python3"', '["sudo", "-n", "-l"'],
      re.findall(r'[\[(]"sudo", .*', src))

# -- never in the way of startup --------------------------------------------
broker._auto["thread"] = None
calls = []
broker.auto_install_step = lambda *a, **k: calls.append(a) or "ok"
t = time.time()
started = broker.start_auto_install(fresh(), delay=0.5)
took = time.time() - t
# "At once" = before its own delay has run: it hands the work to a thread.
# A fixed 0.1 s flaked on a loaded box (Yaya repro); the delay is 0.5 s so a
# parallel test run cannot eat the margin either.
check("start_auto_install returns at once", started and took < 0.5, took)
check("...runs in a daemon thread", broker._auto["thread"].daemon)
check("...only once per process", broker.start_auto_install(fresh()) is False)
broker._auto["thread"].join(10)
check("...and does run", len(calls) == 1)
hub = read("hub_server.py")
check("the hub starts it after the watch, and waits for nothing",
      hub.index("start_auto_install(config_dir)") > hub.index("start_watch(config_dir)")
      and ".join(" not in hub[hub.index("start_auto_install"):][:200])

# -- the installer and the panel ---------------------------------------------
inst = read("scripts", "install.sh")
block = inst[inst.index("# --no-mqtt is REMEMBERED."):inst.index("# 8. systemd units")]
check("--no-mqtt writes the opt-out marker, 0600, owned by the service",
      "INSTALL_MQTT -eq 0" in block
      and "printf 'auto_install = false\\n' > \"${TARGET}/config/mqtt_autoinstall.local.toml\""
      in block and "chmod 600" in block and "chown" in block, block)
panel = read("frontend", "src", "components", "BrokerPanel.jsx")
check("the panel has the switch", 'api.post("/api/mqtt/broker/auto"' in panel
      and "data.auto_install" in panel)

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
from fastapi import HTTPException                                # noqa: E402
from core.routes import mqtt_broker as routes                    # noqa: E402

routes.auth_service = types.SimpleNamespace(is_site_admin=lambda u, c: u == "ada")
routes.audit_service = types.SimpleNamespace(record=lambda **k: None)
routes.broker.public = lambda cfg, force=False: {"auto_install": broker.auto_install_enabled(cfg)}
CFG = fresh()


def call(user, body):
    async def _json():
        return body
    req = types.SimpleNamespace(
        state=types.SimpleNamespace(user=user),
        app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)),
        client=types.SimpleNamespace(host="10.0.0.9"), json=_json)
    try:
        return 200, asyncio.run(routes.broker_auto(req))
    except HTTPException as e:
        return e.status_code, e.detail


check("an operator may not flip it", call("sam", {"enabled": False})[0] == 403
      and broker.auto_install_enabled(CFG))
code, out = call("ada", {"enabled": False})
check("an admin may", code == 200 and out["auto_install"] is False
      and not broker.auto_install_enabled(CFG), out)

# -- Pipi's review of the helper and the setup lines --------------------------
import importlib                                                     # noqa: E402
import subprocess                                                    # noqa: E402

broker = importlib.reload(broker)

# (a) who the service runs as comes from the password database, not $USER
fake_pwd = types.ModuleType("pwd")
fake_pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_name=who["name"])
fake_pwd.getpwnam = lambda name: types.SimpleNamespace(pw_uid=who["mosq_uid"]) \
    if who.get("mosq_uid") is not None else (_ for _ in ()).throw(KeyError(name))
who = {"name": "pi", "mosq_uid": None}
real_pwd = sys.modules.get("pwd")
sys.modules["pwd"] = fake_pwd
had_getuid = hasattr(os, "getuid")
if not had_getuid:
    os.getuid = lambda: 1000
os.environ["USER"] = os.environ["LOGNAME"] = "root"
check("the service user is read from the password database, not $USER",
      broker._service_user() == "pi")
for name in ("evil;rm -rf /", "a b", "Root", "x" * 40, "", "-dash"):
    who["name"] = name
    check(f"...and {name[:14]!r} is not put in a sudoers line",
          broker._service_user() == "")
who["name"] = "pi"
check("a bad name handed in is replaced, not pasted",
      "a b ALL" not in broker.setup_commands("a b")[2]
      and "pi ALL=(root)" in broker.setup_commands("a b")[2])

# (d) the setup lines name where THIS box is installed
tmpd = tempfile.mkdtemp()
conf = os.path.join(tmpd, "update.conf")
with open(conf, "w") as f:
    f.write("UPDATE_DIR=/home/pi/.irivai-update\nLIVE_LINK=/home/pi/irivai-systor\n")
broker.UPDATE_CONF = conf
check("the live link from update.conf is the install path",
      broker.install_root() == "/home/pi/irivai-systor")
check("...and the copy line uses it",
      "/home/pi/irivai-systor/scripts/mqtt_apply.py /usr/local/lib/irivai/mqtt-apply.py"
      in broker.setup_commands("pi")[1], broker.setup_commands("pi")[1])
with open(conf, "w") as f:
    f.write("LIVE_LINK=/srv/my box/irivai\n")
check("...quoted when it has a space",
      "'/srv/my box/irivai/scripts/mqtt_apply.py'" in broker.setup_commands("pi")[1])
broker.UPDATE_CONF = os.path.join(tmpd, "missing.conf")
check("...and this code's own tree without one",
      os.path.normcase(broker.install_root()) == os.path.normcase(ROOT))
check("nothing hard-codes /opt/irivai-systor any more",
      "/opt/irivai-systor/scripts" not in read("core", "services", "mqtt_broker.py")
      and "-m 755 /opt/irivai-systor" not in read("frontend", "src", "components",
                                                  "BrokerPanel.jsx"))
check("the panel shows the server's lines",
      "data.setup_commands" in read("frontend", "src", "components", "BrokerPanel.jsx"))

# (b) apt that runs long is left to finish, never killed
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import mqtt_apply as helper                                           # noqa: E402

if not hasattr(os, "O_NOFOLLOW"):
    os.O_NOFOLLOW = 0                                                  # Windows
helper.APT_LOG = os.path.join(tmpd, "apt.log")
killed = []


class SlowApt:
    def __init__(self, *a, **k):
        self.kw = k

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("apt-get", timeout)

    def kill(self):
        killed.append("kill")

    terminate = kill


helper.subprocess = types.SimpleNamespace(
    Popen=SlowApt, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired,
    run=subprocess.run, SubprocessError=subprocess.SubprocessError)
helper.held_by = lambda port: ""
helper.shutil = types.SimpleNamespace(which=lambda *a, **k: None,
                                      rmtree=lambda *a, **k: None)
try:
    helper.install({})
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("an install past its budget is reported, with the dpkg fix",
      "left to finish in the background" in msg and "dpkg --configure -a" in msg, msg)
check("...and apt is never killed", killed == [])


class FailedApt(SlowApt):
    def wait(self, timeout=None):
        with open(helper.APT_LOG, "w") as f:
            f.write("E: dpkg was interrupted, you must manually run 'dpkg --configure -a'\n")
        return 100


helper.subprocess.Popen = FailedApt
try:
    helper.install({})
    msg = ""
except helper.Refused as e:
    msg = str(e)
check("a failed install passes apt's last line on, and the dpkg fix",
      "dpkg was interrupted" in msg and helper.DPKG_HINT in msg, msg)
src = read("scripts", "mqtt_apply.py")
check("apt writes to a log file, not a pipe that dies with the helper",
      "stdout=fd, stderr=fd" in src and "start_new_session=True" in src)
check("the service waits longer than the helper does",
      int(re.search(r'"install"\}, timeout=(\d+)', read("core", "services",
                                                        "mqtt_broker.py")).group(1))
      > helper.APT_BUDGET_S + 60)

# (e) a rollback leaves a password file mosquitto can read
helper = importlib.reload(helper)
helper._mosquitto_gid = lambda: 111
check("normally 0640 root:mosquitto", helper._passwd_owner() == (0o640, 111, None))
helper._mosquitto_gid = lambda: None
who["mosq_uid"] = 107
check("no mosquitto group: 0600 owned by the mosquitto user",
      helper._passwd_owner() == (0o600, None, 107))
who["mosq_uid"] = None
check("neither: readable, rather than a broker left down",
      helper._passwd_owner() == (0o644, None, None))
wrote = []
helper._write = lambda path, data, *a: wrote.append((path, a))
who["mosq_uid"] = 107
helper._restore(helper.PASSWD_PATH, b"plant:$7$...\n", 0o640)
check("...and the rollback path uses it",
      wrote == [(helper.PASSWD_PATH, (0o600, None, 107))], wrote)

# (f) two restarts in one run are spaced out
naps, ran = [], []
helper._run = lambda argv, **k: ran.append(argv)
helper.time = types.SimpleNamespace(monotonic=time.monotonic,
                                    sleep=lambda s: naps.append(s))
helper._restart()
helper._restart()
check("a second restart waits for the first to settle",
      len(naps) == 1 and 0 < naps[0] <= helper.RESTART_GAP_S, naps)
check("...and clears systemd's start limit first",
      ["systemctl", "reset-failed", "mosquitto"] in ran)

if real_pwd is not None:
    sys.modules["pwd"] = real_pwd
else:
    sys.modules.pop("pwd", None)
if not had_getuid:
    del os.getuid

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
