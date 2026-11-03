# -*- coding: utf-8 -*-
"""
mqtt_broker.py -- this box's MQTT broker: is it up, who holds the port, and
what it is set to. Settings > MQTT Broker and the Workflow page's broker pill
both read from here.

Asked for on 2026-09-29 (Tok Aba, with Node-RED's Aedes node as the model):
"a notification in the workflow to show that the broker is running or not,
and then can set up the broker and can set up also to have security".

WHO DOES WHAT

  * Reading is done here, unprivileged and cheap: a TCP connect to loopback,
    `systemctl is-active mosquitto`, and irivai.conf (0644, world-readable)
    parsed for what the broker was last set to. Cached for a few seconds so
    a page of polling tabs costs one probe, not one each.
  * Changing it is done by scripts/mqtt_apply.py, installed root-owned at
    /usr/local/lib/irivai/mqtt-apply.py and reached through ONE sudoers
    entry. This module validates first so the page gets a readable error,
    but the helper re-validates everything: it is the gate, this is manners.

THE PASSWORD

The broker keeps only a hash (mosquitto_passwd). The platform keeps the plain
password in config/mqtt_broker.local.toml, 0600, for two reasons only: the
next save regenerates the password file from scratch and a blank field there
means "keep it", and this box's OWN MQTT nodes have to log in to a secured
broker. It is never returned by any route -- the page is told "set" or "not
set" -- and never copied into a system's compiled config (where /api/functions
and the outputs route would hand it to anyone who can read a workflow). The
detection app reads it from the file at connect time instead
(systems/demo/app/broker_login.py), found through LOGIN_ENV.
"""

import json
import logging
import os
import pathlib
import re
import shutil
import socket
import subprocess
import threading
import time

from core import config
from core.services import hubport

CONF_PATH = "/etc/mosquitto/conf.d/irivai.conf"
HELPER = "/usr/local/lib/irivai/mqtt-apply.py"
#: Exactly the command the sudoers entry names. -I: no PYTHON* environment,
#: no user site-packages -- the root interpreter runs the file and nothing
#: the service account could have put beside it.
HELPER_ARGV = ("sudo", "-n", "/usr/bin/python3", "-I", HELPER)
DEFAULT_PORT = 1883
LOCAL_NAME = "mqtt_broker"            # config/mqtt_broker.local.toml
#: How the detection app finds that file. Set by every hub and master at
#: start (export_login) and inherited by the app they launch.
LOGIN_ENV = "IRIVAI_MQTT_LOGIN"

#: Mirrors scripts/mqtt_apply.py -- duplicated on purpose, the helper imports
#: nothing from a tree the service account can write. smoke_mqttbrokerpanel
#: pins the two copies together.
RESERVED_PORTS = frozenset(
    list(range(5100, 5200))
    + [8554, 8889, 8189, 9997, 8888, 8000, 8001, 1935]
    + [5432, 22, 53, 67, 68, 80, 443, 5353])
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
PASSWORD_MIN, PASSWORD_MAX = 8, 128

#: How long one status answer is reused. Short: an admin who just pressed
#: Start in a terminal expects the pill to agree within a poll or two.
CACHE_S = 3.0

_cache = {"at": 0.0, "value": None}
_cache_lock = threading.Lock()
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Reading the box
# ---------------------------------------------------------------------------

def parse_conf(text):
    """What irivai.conf sets: {port, ws_port, secured, local_only}.

    Only the shape the helper writes is understood; anything else (a conf
    somebody edited by hand) still yields the first listener's port."""
    out = {"port": None, "ws_port": None, "secured": False, "local_only": True}
    last = None
    listeners = []
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0].lower()
        if key == "listener" and len(parts) >= 2 and parts[1].isdigit():
            last = {"port": int(parts[1]),
                    "bind": parts[2] if len(parts) >= 3 else "",
                    "ws": False}
            listeners.append(last)
        elif key == "protocol" and len(parts) >= 2 and last is not None:
            last["ws"] = parts[1].lower() == "websockets"
        elif key == "allow_anonymous" and len(parts) >= 2:
            out["secured"] = parts[1].lower() == "false"
    mqtt = [x for x in listeners if not x["ws"]]
    ws = [x for x in listeners if x["ws"]]
    if mqtt:
        out["port"] = mqtt[0]["port"]
    if ws:
        out["ws_port"] = ws[0]["port"]
    out["local_only"] = bool(listeners) and all(
        x["bind"] in ("127.0.0.1", "localhost", "::1") for x in listeners)
    return out


def _read_conf():
    try:
        return pathlib.Path(CONF_PATH).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _listening(port, timeout=0.3):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _unit_active(unit):
    if not shutil.which("systemctl"):
        return False
    try:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                           text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip() == "active"


def _ss_owner(port):
    """The program name ss reports for a listener on `port`, or "".

    Unprivileged, ss names only this account's own processes -- mosquitto
    and a Node-RED under another user come back nameless -- so the caller
    falls back on which units are running."""
    if not shutil.which("ss"):
        return ""
    try:
        r = subprocess.run(["ss", "-ltnpH", f"sport = :{int(port)}"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    names = re.findall(r'\(\("([^"]+)"', r.stdout or "")
    return names[0] if names else ""


def mosquitto_installed():
    return bool(shutil.which("mosquitto")
                or os.path.exists("/usr/sbin/mosquitto"))


def helper_installed():
    return os.path.isfile(HELPER)


def _probe():
    """One uncached look at the broker. See status()."""
    conf_text = _read_conf()
    conf = parse_conf(conf_text) if conf_text is not None else None
    installed = mosquitto_installed()
    active = installed and _unit_active("mosquitto")
    port = (conf or {}).get("port") or DEFAULT_PORT
    listening = _listening(port)

    owner = _ss_owner(port) if listening else ""
    if listening and not owner:
        if active:
            owner = "mosquitto"
        elif _unit_active("nodered"):
            owner = "node-red"
        else:
            owner = "another program"
    ours = listening and "mosquitto" in owner

    if listening and not ours:
        state = "other"            # something else is the broker here
    elif ours:
        state = "running"
    elif not installed:
        state = "not_installed"
    else:
        state = "down"

    secured = bool(conf and conf["secured"])
    local_only = conf["local_only"] if conf else True
    # Who may connect and whether they log in are two settings now, so four
    # modes. A conf written before "Who can connect" existed reads the same
    # way it always behaved: a login meant every interface, none meant local.
    if state == "running":
        mode = ({(True, False): "local", (True, True): "local_secured",
                 (False, True): "secured", (False, False): "open"}
                [(bool(local_only), secured)])
    else:
        mode = ""
    return {
        "state": state,
        "installed": installed,
        "active": bool(active),
        "listening": listening,
        "owner": owner,
        "port": port,
        "ws_port": (conf or {}).get("ws_port"),
        "secured": secured,
        "local_only": local_only,
        "access": "local" if local_only else "network",
        "mode": mode,
        "managed": conf is not None,
        "helper": helper_installed(),
        "message": _message(state, port, owner, mode),
    }


#: What each mode is called on the pill and in the status line. Mirrored in
#: frontend/src/components/brokerPill.js.
MODE_LABELS = {"local": "local only", "local_secured": "local only, secured",
               "secured": "network, secured",
               "open": "network, open - no password"}

#: Who may connect: this box only, or any device on the network. Mirrors
#: scripts/mqtt_apply.py ACCESS.
ACCESS = ("local", "network")


def _message(state, port, owner, mode):
    if state == "running":
        label = MODE_LABELS.get(mode, "")
        return f"MQTT broker: running on :{port}" + (f" ({label})" if label else "")
    if state == "other":
        return f"MQTT broker: port {port} is used by {owner}"
    if state == "not_installed":
        return "MQTT broker: not installed"
    return "MQTT broker: not running"


def status(force=False):
    """The broker as it is right now, cached CACHE_S seconds."""
    now = time.time()
    with _cache_lock:
        if not force and _cache["value"] is not None \
                and now - _cache["at"] < CACHE_S:
            return dict(_cache["value"])
    value = _probe()
    with _cache_lock:
        _cache.update(at=time.time(), value=value)
    return dict(value)


# ---------------------------------------------------------------------------
# The platform's copy of the login
# ---------------------------------------------------------------------------

def login_path(config_dir=None):
    return config.root(config_dir) / f"{LOCAL_NAME}.local.toml"


def export_login(config_dir=None):
    """Tell every process this one starts where the broker login lives.

    The detection app imports nothing from core/ and is not told the hub's
    config directory, so a secured broker would otherwise lock this box's own
    MQTT nodes out. A PATH travels, never the password."""
    os.environ[LOGIN_ENV] = str(login_path(config_dir))


def _stored(config_dir=None):
    try:
        login = config.load_local(LOCAL_NAME, config_dir).get("login") or {}
    except ValueError:
        login = {}
    return {"username": str(login.get("username") or ""),
            "password": str(login.get("password") or ""),
            "port": login.get("port")}


def _save_login(username, password, port, config_dir=None):
    """Write mqtt_broker.local.toml at 0600 from the first byte.

    config.save_local chmods AFTER writing, which leaves a moment where a
    fresh file sits at the umask's mode; for the one file whose whole content
    is a password that moment is not wanted, so the file is created 0600."""
    path = login_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"login": {"username": username, "password": password,
                      "port": int(port)}} if username else {}
    text = config._toml_dump(data) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    config.load.cache_clear()


def settings(config_dir=None):
    """What the page may know about the login: the name, and whether a
    password is set. Never the password."""
    s = _stored(config_dir)
    return {"username": s["username"], "password_set": bool(s["password"])}


#: A login name this module will put in a line somebody pastes into a root
#: shell (the sudoers rule in setup_commands). Debian's own NAME_REGEX.
_ACCOUNT_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")


def _service_user():
    """The account this process runs as, from the password database.

    Not getpass.getuser(): that reads LOGNAME/USER first, and an environment
    variable is not who a process is. Anything that is not a plain login name
    comes back "" -- the name ends up inside a sudoers line."""
    try:
        import pwd                                           # noqa: PLC0415
        name = pwd.getpwuid(os.getuid()).pw_name
    except Exception:                                        # noqa: BLE001
        return ""
    return name if _ACCOUNT_RE.fullmatch(name or "") else ""


#: Where an appliance install records its live link (install.sh writes it,
#: 0644, for recover.py).
UPDATE_CONF = "/etc/irivai/update.conf"


def install_root():
    """The directory the platform is installed at, as the box names it.

    The live link from /etc/irivai/update.conf when there is one -- it
    survives the next update, where a release directory may be pruned --
    else this code's own tree, NOT symlink-resolved for the same reason.
    /opt/irivai-systor on a default install, /home/pi/... on .63-style ones."""
    try:
        with open(UPDATE_CONF, encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key == "LIVE_LINK" and value.startswith("/"):
                    return value
    except OSError:
        pass
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def lan_addresses():
    """This box's own IPv4 addresses another device could type to reach the
    broker -- the line the Broker page shows as "Reachable at". Loopback and
    link-local (169.254.x, an interface with no DHCP answer) are left out:
    neither is an address anything else on the network can use."""
    found = []
    if shutil.which("hostname"):
        try:
            out = subprocess.run(["hostname", "-I"], capture_output=True,
                                 text=True, timeout=2).stdout
            found = out.split()
        except (OSError, subprocess.SubprocessError):
            found = []
    if not found:
        try:
            found = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None,
                                                         socket.AF_INET)]
        except OSError:
            found = []
    out = []
    for ip in found:
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip) and not ip.startswith(
                ("127.", "169.254.")) and ip not in out:
            out.append(ip)
    return out


def public(config_dir=None, force=False):
    """The admin page's payload. `service_user` is for the one-time setup
    lines a box installed before the helper existed has to run by hand (an
    over-the-air update runs as this account and cannot add a root helper)."""
    return {"status": status(force=force), "settings": settings(config_dir),
            "defaults": {"port": DEFAULT_PORT},
            "service_user": _service_user(),
            "setup_commands": setup_commands(),
            "auto_install": auto_install_enabled(config_dir),
            "addresses": lan_addresses(),
            "persistence_note": (
                "Retained messages and sessions already survive a restart: "
                "the distribution's mosquitto.conf turns persistence on.")}


# ---------------------------------------------------------------------------
# Changing it
# ---------------------------------------------------------------------------

def _port(value, what, required=True):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{what} is required")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a whole number")
    try:
        text = str(value).strip()
        if not re.fullmatch(r"\d{1,5}", text):
            raise ValueError
        port = int(text)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be a whole number") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"{what} must be between 1 and 65535")
    if port in RESERVED_PORTS:
        raise ValueError(f"{what} {port} is used by the box itself - choose another")
    return port


def build_request(body, config_dir=None):
    """The helper request for a page's form, or ValueError.

    Form fields: port, ws_port (blank = off), access ("local" | "network"),
    secure (bool), username, password (blank = keep the one already set,
    when the name is unchanged).
    """
    body = body if isinstance(body, dict) else {}
    port = _port(body.get("port", DEFAULT_PORT), "The MQTT port")
    ws_port = _port(body.get("ws_port"), "The WebSocket port", required=False)
    if ws_port is not None and ws_port == port:
        raise ValueError("the WebSocket port must differ from the MQTT port")
    # The software's own port can leave the 51xx block (Software port card);
    # the fixed list above cannot follow it, so it is asked for by name --
    # the port it has and the one saved for its next restart.
    for own in sorted(hubport.claimed_ports(config_dir) & {port, ws_port}):
        raise ValueError(f"port {own} is the software's own port - choose another")

    username = password = ""
    if body.get("secure"):
        username = str(body.get("username") or "").strip()
        if not USERNAME_RE.fullmatch(username):
            raise ValueError("the username may use letters, digits, '_', '.' "
                             "and '-' (1 to 64 of them)")
        password = body.get("password")
        password = password if isinstance(password, str) else ""
        if not password:
            stored = _stored(config_dir)
            if stored["password"] and stored["username"] == username:
                password = stored["password"]
            else:
                raise ValueError("enter a password")
        if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            raise ValueError(f"the password must be {PASSWORD_MIN} to "
                             f"{PASSWORD_MAX} characters")
        if not password.isprintable():
            raise ValueError("the password may not contain line breaks or "
                             "control characters")
    access = body.get("access")
    legacy = "network" if username else "local"
    if access is None:
        access = legacy
    elif not isinstance(access, str) or access not in ACCESS:
        raise ValueError("choose who may connect: this box only, or any "
                         "device on the network")
    req = {"action": "apply", "port": port, "ws_port": ws_port,
           "username": username, "password": password}
    # Sent only when it says something the login does not: a helper installed
    # before this field existed refuses fields it does not know, and the two
    # combinations it always supported must keep working on such a box.
    if access != legacy:
        req["access"] = access
    return req


#: What an older helper says about the "access" field, and what to tell the
#: admin instead.
_OLD_HELPER = "the request has fields this helper does not take"
_OLD_HELPER_ADVICE = ("this box's broker helper predates the \"Who can connect\" "
                      "setting, so only \"this box only\" without a login or "
                      "\"any device\" with one can be applied - re-run the "
                      "installer (sudo ./scripts/install.sh) to update it")


def _run_helper(req, timeout):
    """(ok, message) from the root helper. Never raises, never echoes req."""
    if not helper_installed():
        return False, ("this box does not have the broker helper yet - re-run "
                       "the installer (sudo ./scripts/install.sh) to add it")
    try:
        r = subprocess.run(list(HELPER_ARGV), input=json.dumps(req),
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "the broker helper did not answer in time"
    except OSError as e:
        return False, f"the broker helper could not run ({type(e).__name__})"
    try:
        answer = json.loads((r.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        # sudo's own refusal lands on stderr ("a password is required" when
        # the sudoers entry is missing). Say what to do, not what sudo said.
        return False, ("the broker helper is not allowed to run - re-run the "
                       "installer to add its sudoers entry")
    return bool(answer.get("ok")), str(answer.get("message") or "")[:500]


def apply(body, config_dir=None):
    """Validate, hand to the root helper, and on success remember the login
    the app needs. Returns (ok, message)."""
    req = build_request(body, config_dir)
    with _write_lock:
        ok, message = _run_helper(req, timeout=60)
        if not ok and message == _OLD_HELPER and "access" in req:
            message = _OLD_HELPER_ADVICE
        if ok:
            _save_login(req["username"], req["password"], req["port"], config_dir)
    status(force=True)
    return ok, message


def install(config_dir=None):
    with _write_lock:
        # Longer than the helper's own apt budget (APT_BUDGET_S, 25 min): the
        # helper decides when to stop waiting, and it never kills apt.
        ok, message = _run_helper({"action": "install"}, timeout=1700)
    status(force=True)
    return ok, message


# ---------------------------------------------------------------------------
# Telling the admins when it stops
# ---------------------------------------------------------------------------

#: How often the hub looks, and how many looks in a row a broker must be down
#: before anyone is told: a restart from this very page takes a second or
#: two, and a notice for that would be a notice for pressing Save.
WATCH_S = 30.0
WATCH_DOWN_CHECKS = 2

_watch = {"thread": None, "down": 0, "told": False}


def watch_step(config_dir=None, notify=None):
    """One look. Posts a notice the first time a broker this platform set up
    is seen down WATCH_DOWN_CHECKS times running, and clears once it is back.
    Returns True when it posted. A box whose broker the platform does not
    manage (no irivai.conf, or Node-RED holds the port) is never reported."""
    s = status(force=True)
    if not s["managed"] or s["state"] not in ("running", "down"):
        _watch.update(down=0, told=False)
        return False
    if s["state"] == "running":
        _watch.update(down=0, told=False)
        return False
    _watch["down"] += 1
    if _watch["down"] < WATCH_DOWN_CHECKS or _watch["told"]:
        return False
    _watch["told"] = True
    if notify is None:
        from core.services import inbox                      # noqa: PLC0415
        notify = inbox.notify_admins
    notify("mqtt_broker", "MQTT broker stopped",
           f"The broker on port {s['port']} is not running, so MQTT Subscribe "
           "and MQTT Publish nodes on this box cannot connect. Open "
           "Settings > MQTT Broker to start it again.",
           config_dir, page="settings", tab="mqtt")
    return True


def start_watch(config_dir=None):
    """The hub's background look. Idempotent; never fatal."""
    if _watch["thread"] is not None:
        return False

    def loop():
        while True:
            try:
                watch_step(config_dir)
            except Exception:                                # noqa: BLE001
                pass
            time.sleep(WATCH_S)

    t = threading.Thread(target=loop, name="mqtt-broker-watch", daemon=True)
    _watch["thread"] = t
    t.start()
    return True


# ---------------------------------------------------------------------------
# Installing a missing broker by itself
# ---------------------------------------------------------------------------
#
# Asked for on 2026-09-29 (Tok Aba): "when installing a new version, install
# the broker automatically". An over-the-air update ends with the hub
# restarting, so the hub's start is where a new version first runs -- and a
# box whose broker never got installed gets one then, through the SAME root
# helper and the same {"action": "install"} the panel's button sends. That
# install is local only (loopback, anonymous): nothing is opened to the LAN
# without somebody choosing a login.
#
# What it will NOT do: gain root on a box that does not have the helper. The
# update unit runs as the service account and sudo lets it flip a symlink and
# restart units, nothing else, and that is how it should stay. Such a box
# gets one notice per version with the lines to run.
#
# Bookkeeping lives in its own config/mqtt_autoinstall.local.toml -- not the
# login file -- so the installer's --no-mqtt can write the opt-out without
# touching a password, and so an OTA carries it like any other .local.toml.

AUTO_NAME = "mqtt_autoinstall"
#: A failed install is tried again after this long, or on the next version,
#: whichever comes first. Not on every restart: apt on a box with no route to
#: its mirror takes minutes to fail, and a hub that bounces three times an
#: hour would spend them all in apt.
AUTO_RETRY_S = 6 * 3600
#: How long after hub start the attempt waits. The hub's own startup (the
#: systems it autostarts) should not queue behind a package install.
AUTO_DELAY_S = 20.0

_log = logging.getLogger(__name__)
_auto = {"thread": None}


def _auto_state(config_dir=None):
    try:
        return dict(config.load_local(AUTO_NAME, config_dir))
    except ValueError:
        return {}


def _save_auto(state, config_dir=None):
    config.save_local(AUTO_NAME, state, config_dir)


def auto_install_enabled(config_dir=None):
    """On unless somebody said no: the installer's --no-mqtt, or the panel."""
    return _auto_state(config_dir).get("auto_install", True) is not False


def set_auto_install(enabled, config_dir=None):
    state = _auto_state(config_dir)
    state["auto_install"] = bool(enabled)
    _save_auto(state, config_dir)


def helper_allowed():
    """Is the helper there AND may this account run it?

    The sudoers file is root-only, so it cannot simply be looked at. `sudo -n
    -l <command>` answers exactly the question -- would this command be
    allowed, without a password -- and runs nothing."""
    if not helper_installed() or not shutil.which("sudo"):
        return False
    try:
        r = subprocess.run(["sudo", "-n", "-l", *HELPER_ARGV[2:]],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def setup_commands(user=None):
    """The one-time lines a box installed before the helper existed needs.
    Shown on the panel and in the notice, from this one place."""
    import shlex                                             # noqa: PLC0415
    user = user if user and _ACCOUNT_RE.fullmatch(user) else \
        (_service_user() or "<service user>")
    src = shlex.quote(install_root().rstrip("/") + "/scripts/mqtt_apply.py")
    # Same lines as the panel shows (BrokerPanel.jsx): the sudoers rule is
    # written under a ".tmp" name, which sudo ignores, and moved into place
    # only once visudo passes -- a live file with a typo stops sudo for
    # every account on the box.
    return [
        "sudo install -d -o root -g root -m 755 /usr/local/lib/irivai",
        f"sudo install -o root -g root -m 755 {src} "
        "/usr/local/lib/irivai/mqtt-apply.py",
        f"echo '{user} ALL=(root) NOPASSWD: /usr/bin/python3 -I "
        "/usr/local/lib/irivai/mqtt-apply.py' | sudo tee /etc/sudoers.d/irivai-mqtt.tmp >/dev/null",
        "sudo chmod 440 /etc/sudoers.d/irivai-mqtt.tmp && sudo visudo -cf "
        "/etc/sudoers.d/irivai-mqtt.tmp && sudo mv /etc/sudoers.d/irivai-mqtt.tmp "
        "/etc/sudoers.d/irivai-mqtt",
    ]


def _on_linux():
    return os.name == "posix" and shutil.which("systemctl") is not None


def auto_install_step(config_dir=None, version="", now=None, notify=None):
    """One decision, at hub start. Returns what it did, as a word:

      skipped   - not an appliance (no systemd)
      opted_out - --no-mqtt, or the panel's switch
      present   - mosquitto is already installed
      port_held - something else answers on 1883 (Node-RED on a dev box)
      no_helper - the box predates the helper; a notice was left, once
      done      - this version already installed it
      backoff   - it failed recently on this version; not yet
      ok/failed - it tried, now
    """
    now = time.time() if now is None else now
    if notify is None:
        from core.services import inbox                      # noqa: PLC0415
        notify = inbox.notify_admins
    if not _on_linux():
        return "skipped"
    state = _auto_state(config_dir)
    if state.get("auto_install", True) is False:
        return "opted_out"
    if mosquitto_installed():
        return "present"
    if _listening(DEFAULT_PORT):
        return "port_held"
    if not helper_allowed():
        if state.get("setup_notice_version") == version:
            return "no_helper"
        state["setup_notice_version"] = version
        _save_auto(state, config_dir)
        notify("mqtt_broker", "This box needs a one-time setup step to get "
               "its MQTT broker",
               "It was installed before the broker could be set up "
               "automatically. Run these once in a terminal on the box, then "
               "open Settings > MQTT Broker and press Install:\n"
               + "\n".join(setup_commands()),
               config_dir, page="settings", tab="mqtt", limit=2000)
        return "no_helper"
    same = state.get("last_version") == version
    if same and state.get("last_result") == "ok":
        return "done"
    if same and state.get("last_result") == "failed" \
            and now - float(state.get("last_attempt") or 0) < AUTO_RETRY_S:
        return "backoff"

    state.update(last_attempt=int(now), last_version=version, last_result="running")
    _save_auto(state, config_dir)
    ok, message = install(config_dir)
    state.update(last_result="ok" if ok else "failed", last_message=message[:300])
    _save_auto(state, config_dir)
    if ok:
        notify("mqtt_broker", "MQTT broker installed, local only",
               "This box now runs an MQTT broker on 127.0.0.1:1883, so its MQTT "
               "Subscribe and Publish nodes have something to connect to. Only "
               "this box can reach it; set a username and password under "
               "Settings > MQTT Broker to let other devices connect.",
               config_dir, page="settings", tab="mqtt")
    else:
        notify("mqtt_broker", "The MQTT broker could not be installed",
               f"{message or 'The install failed.'} It will be tried again in "
               "6 hours or with the next update. To try now, open Settings > "
               "MQTT Broker and press Install, or run on the box: sudo apt-get "
               "install -y mosquitto mosquitto-clients",
               config_dir, page="settings", tab="mqtt")
    return "ok" if ok else "failed"


def start_auto_install(config_dir=None, delay=AUTO_DELAY_S):
    """Run auto_install_step once, in the background. Never blocks, never
    raises: a hub that cannot install a broker must still come up."""
    if _auto["thread"] is not None:
        return False

    def run():
        time.sleep(delay)
        try:
            from core.version import VERSION                 # noqa: PLC0415
            result = auto_install_step(config_dir, VERSION)
            _log.info("[HUB] MQTT broker auto-install: %s", result)
        except Exception:                                    # noqa: BLE001
            _log.exception("[HUB] MQTT broker auto-install failed")

    t = threading.Thread(target=run, name="mqtt-broker-autoinstall", daemon=True)
    _auto["thread"] = t
    t.start()
    return True
