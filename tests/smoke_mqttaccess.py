# "Who can connect": this box only, or any device on the network -- chosen
# separately from whether clients log in.
#
# Asked for on 2026-09-29 (Tok Aba, seeing "local only" on a fresh box):
# "ensure that the broker have option to allow all or local only". Four
# combinations, all allowed; "any device, no login" is the product owner's
# explicit choice (Node-RED's Aedes does the same) and the page warns on it.
# Pinned here: what each combination writes, that the helper takes the new
# field as a strict enum and still renders from ints and fixed text only,
# that a platform or a conf from before the field keeps meaning what it did,
# and what the status line calls each one.
import importlib
import io
import os
import re
import sys

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
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


import mqtt_apply as helper                                        # noqa: E402
from core.services import mqtt_broker as broker                   # noqa: E402

BASE = {"action": "apply", "port": 1883, "ws_port": 9001}
LOGIN = {"username": "plant", "password": "longenough"}
NONE = {"username": "", "password": ""}


def conf(access, login):
    req = {**BASE, **(LOGIN if login else NONE)}
    if access is not None:
        req["access"] = access
    return helper.render_conf(helper.validate(req))


def listeners(text):
    return re.findall(r"^listener .*$", text, re.M)


# -- the four combinations ----------------------------------------------------
c = conf("local", False)
check("this box only, no login: loopback, anonymous",
      listeners(c) == ["listener 1883 127.0.0.1", "listener 9001 127.0.0.1"]
      and "allow_anonymous true" in c and "password_file" not in c, c)
c = conf("local", True)
check("this box only, with a login: loopback, and it must log in",
      listeners(c) == ["listener 1883 127.0.0.1", "listener 9001 127.0.0.1"]
      and "allow_anonymous false" in c
      and f"password_file {helper.PASSWD_PATH}" in c, c)
c = conf("network", True)
check("any device, with a login: every interface, secured",
      listeners(c) == ["listener 1883", "listener 9001"]
      and "allow_anonymous false" in c and "password_file" in c, c)
c = conf("network", False)
check("any device, no login: every interface, anonymous - allowed",
      listeners(c) == ["listener 1883", "listener 9001"]
      and "allow_anonymous true" in c and "password_file" not in c, c)
check("...and the conf itself says what was chosen",
      "NO login" in c and "every topic" in c)
for access in ("local", "network"):
    for login in (False, True):
        text = conf(access, login)
        check(f"{access}/{'login' if login else 'none'}: no name or password in the conf",
              "plant" not in text and "longenough" not in text)
check("the WebSocket listener stays a websocket listener",
      conf("network", False).count("protocol websockets") == 1)

# -- a strict enum -----------------------------------------------------------
def refused(value):
    try:
        helper.validate({**BASE, **NONE, "access": value})
    except helper.Refused as e:
        return str(e)
    return ""


check("'local' and 'network' pass", refused("local") == "" and refused("network") == "")
for value in ("Local", "NETWORK", "local ", " network", "all", "any", "lan", "",
              "local\n", "network;", "0.0.0.0", "127.0.0.1", None, 0, 1, True,
              False, ["local"], {"v": "local"}):
    check(f"access {value!r} is refused", refused(value) != "", refused(value))
RENDER = read("scripts", "mqtt_apply.py").split("def render_conf")[1].split("\n# ----")[0]
check("render_conf never copies the value in - it only picks fixed text",
      "_BLURB[(" in RENDER and not re.search(r"\{[^}]*access[^}]*\}", RENDER), RENDER)
check("install is local only", helper.validate({"action": "install"})["access"] == "local")

# -- a request from before the field -----------------------------------------
check("no field + a login means the network, as it always did",
      helper.validate({**BASE, **LOGIN})["access"] == "network")
check("no field + no login means this box only, as it always did",
      helper.validate({**BASE, **NONE})["access"] == "local")
check("...and renders exactly as before", conf(None, True) == conf("network", True)
      and conf(None, False) == conf("local", False))

# -- the service ---------------------------------------------------------------
CFG = os.path.join(os.environ.get("TEMP", "/tmp"), "mqttaccess-cfg")
os.makedirs(CFG, exist_ok=True)


def req(**body):
    return broker.build_request({"port": 1883, **body}, CFG)


check("the page's two old combinations send no field (an old helper takes them)",
      "access" not in req(access="local")
      and "access" not in req(access="network", secure=True, **LOGIN))
check("...the two new ones do",
      req(access="network")["access"] == "network"
      and req(access="local", secure=True, **LOGIN)["access"] == "local")
check("no field from the page keeps the old meaning", "access" not in req(secure=True, **LOGIN)
      and "access" not in req())
for value in ("all", "LOCAL", "", 1, "any", ["network"]):
    try:
        req(access=value)
        ok = False
    except ValueError:
        ok = True
    check(f"the service refuses access {value!r} too", ok)
check("both copies of the enum agree", broker.ACCESS == helper.ACCESS)

broker._run_helper = lambda r, timeout: (False, "the request has fields this helper does not take")
broker.status = lambda force=False: {}
ok, msg = broker.apply({"port": 1883, "access": "network"}, CFG)
check("an old helper refusing the field is explained, with what to do",
      not ok and "predates" in msg and "install.sh" in msg, msg)

# -- what the status says ------------------------------------------------------
broker = importlib.reload(broker)


def status_of(text):
    broker._read_conf = lambda: text
    broker._listening = lambda port, timeout=0.3: True
    broker._unit_active = lambda u: u == "mosquitto"
    broker._ss_owner = lambda port: ""
    broker.mosquitto_installed = lambda: True
    broker.helper_installed = lambda: True
    return broker._probe()


want = {("local", False): ("local", "(local only)"),
        ("local", True): ("local_secured", "(local only, secured)"),
        ("network", True): ("secured", "(network, secured)"),
        ("network", False): ("open", "(network, open - no password)")}
for (access, login), (mode, label) in want.items():
    s = status_of(conf(access, login))
    check(f"{access}/{'login' if login else 'none'} reads as {label}",
          s["mode"] == mode and s["access"] == access
          and s["message"] == f"MQTT broker: running on :1883 {label}", s["message"])
pill = read("frontend", "src", "components", "brokerPill.js")
check("the pill's words are the service's words",
      all(f'{m}: "{broker.MODE_LABELS[m]}"' in pill or f'{m}: "{broker.MODE_LABELS[m]}",' in pill
          for m in broker.MODE_LABELS), pill)

# -- configs saved before the field --------------------------------------------
OLD_SECURED = ("# Written by IRIV EdgeAI Studio\nlistener 1883\n"
               "allow_anonymous false\npassword_file /etc/mosquitto/irivai.passwd\n")
OLD_LOCAL = "# Written by IRIV EdgeAI Studio\nlistener 1883 127.0.0.1\nallow_anonymous true\n"
inst = read("scripts", "install.sh")
INSTALLER = inst[inst.index("<<'MQTTCONF'") + 12:inst.index("\nMQTTCONF\n")]
check("an old secured conf reads as the network, secured",
      status_of(OLD_SECURED)["access"] == "network"
      and status_of(OLD_SECURED)["mode"] == "secured")
check("an old local conf reads as this box only",
      status_of(OLD_LOCAL)["access"] == "local" and status_of(OLD_LOCAL)["mode"] == "local")
check("...and so does the installer's", status_of(INSTALLER)["access"] == "local")
check("re-running the installer keeps whatever conf is there",
      "if [[ -f /etc/mosquitto/conf.d/irivai.conf ]]" in inst)
check("the default is this box only",
      'access: st.managed && st.access === "network" ? "network" : "local"'
      in read("frontend", "src", "components", "BrokerPanel.jsx"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
