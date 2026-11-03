#!/usr/bin/env python3
"""
mqtt_apply.py -- set up this box's MQTT broker (Mosquitto), as root, from a
request the platform hands it on stdin.

WHY THIS EXISTS

Settings > MQTT Broker lets an admin choose the broker's port, a WebSocket
port and a username/password. Every one of those lands in files under
/etc/mosquitto and ends in a restart of a root service, and the platform runs
as an unprivileged account that must not be able to write /etc at large. So
the platform ASKS, and this script -- root-owned, installed OUTSIDE every
release tree at /usr/local/lib/irivai/mqtt-apply.py, the same terms as
recover.py and db-console-role.py -- decides.

WHAT IT WILL DO, AND NOTHING ELSE

  * write /etc/mosquitto/conf.d/irivai.conf       (rendered here, from ints)
  * write or remove /etc/mosquitto/irivai.passwd  (hashed by mosquitto_passwd)
  * enable and restart mosquitto.service
  * "install": apt-get install -y mosquitto mosquitto-clients (fixed names)

It trusts NOTHING in the request. The platform validates too, for friendly
errors, but that copy is a courtesy: whoever can run this script through sudo
is exactly the account this script exists to fence in, so every field is
checked again here, and the only things copied into a file are integers this
script parsed and a username from a fixed character set. The password never
reaches a file in the clear, an argv (ps would show it) or the output.

SECURITY SHAPE

Two separate choices, each a fixed set of answers:

  * access "local"    -> every listener on 127.0.0.1: only this box connects.
    access "network"  -> every listener on all interfaces.
  * a username + password -> allow_anonymous false, password_file of hashed
    entries, 0640 root:mosquitto. None -> allow_anonymous true.

"network" with no login is allowed -- the product owner's explicit choice,
as Node-RED's Aedes allows it -- and the page warns loudly before it is
saved. A request without "access" (a platform older than the field) means
what it always did: a login opened the broker to the network, none kept it
local. The default for a fresh box, and the install action, is local.

A port another program already holds (Node-RED's Aedes on a developer box) is
refused rather than fought over, and a restart that does not come up is rolled
back to the files that were there before.

Reached through exactly one sudoers entry, with no arguments:

  sudo -n /usr/bin/python3 -I /usr/local/lib/irivai/mqtt-apply.py < request.json

and answers one JSON object on stdout: {"ok": bool, "message": str}.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

CONF_DIR = "/etc/mosquitto/conf.d"
CONF_PATH = CONF_DIR + "/irivai.conf"
PASSWD_PATH = "/etc/mosquitto/irivai.passwd"
UNIT = "mosquitto"

#: Only these binaries, only from these directories. sudo already resets the
#: environment; this makes the lookup independent of what it reset it to.
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"

#: A request bigger than this is not a settings form.
MAX_REQUEST = 16 * 1024

#: One change at a time, box-wide. The hub and every master mount the page,
#: each with its own in-process lock, so two saves (or a save during a long
#: install) could otherwise interleave their writes and rollbacks. Root-only
#: directory, so the service account cannot pre-create or hold the file.
LOCK_PATH = "/run/irivai-mqtt-apply.lock"

#: Where the plain "user:password" file lives for the length of one
#: mosquitto_passwd call: tmpfs, so the clear password never reaches the SD
#: card or NVMe, and root-owned, so nothing the service account controls
#: (TMPDIR, should a sudoers change ever keep it) chooses the directory.
PLAIN_DIR = "/run"

#: Ports the box itself needs. The hub is 5100, the device agent 5101, every
#: master and app stream port is allocated upward from there (5102, 5110...),
#: so the whole 51xx block is the platform's. Then mediamtx (RTSP, WebRTC,
#: its ICE port, its API and the defaults of the protocols it could be asked
#: to serve), PostgreSQL, and the services a box cannot live without. A
#: broker on any of these takes something else off the air at the restart.
RESERVED_PORTS = frozenset(
    list(range(5100, 5200))
    + [8554, 8889, 8189, 9997, 8888, 8000, 8001, 1935]
    + [5432, 22, 53, 67, 68, 80, 443, 5353])

#: A username mosquitto_passwd and a password file both take at face value:
#: no ':' (the file's separator), no whitespace, no newline.
USERNAME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

PASSWORD_MIN, PASSWORD_MAX = 8, 128

KEYS = {"action", "port", "ws_port", "username", "password", "access"}
ACTIONS = ("apply", "install")

#: Who may connect, independent of the login (Tok Aba, 2026-09-29: "the
#: broker should have an option to allow all or local only"). "local" binds
#: every listener to 127.0.0.1; "network" binds every interface. OPTIONAL in
#: a request, for a platform older than this field: absent, it is what that
#: platform meant -- a login opened the broker to the network, no login kept
#: it local.
ACCESS = ("local", "network")

HEADER = ("# Written by IRIV EdgeAI Studio (Settings > MQTT Broker) through\n"
          "# /usr/local/lib/irivai/mqtt-apply.py. The next save replaces this\n"
          "# file: change the broker from that page, not here.\n")


class Refused(Exception):
    """A request this script will not carry out. The message is shown to the
    admin as it is, so it never contains anything from the request except
    numbers this script already parsed."""


# ---------------------------------------------------------------------------
# Validation -- the gate
# ---------------------------------------------------------------------------

def _port(value, what):
    # bool is an int in Python; "true" is not a port.
    if isinstance(value, bool) or not isinstance(value, int):
        raise Refused(f"{what} must be a whole number")
    if not 1 <= value <= 65535:
        raise Refused(f"{what} must be between 1 and 65535")
    if value in RESERVED_PORTS:
        raise Refused(f"{what} {value} is used by the box itself - choose another")
    return value


def validate(req):
    """The request as this script will act on it, or Refused.

    Returns {action, port, ws_port (int|None), username, password, access}.
    """
    if not isinstance(req, dict):
        raise Refused("the request must be a JSON object")
    extra = set(req) - KEYS
    if extra:
        raise Refused("the request has fields this helper does not take")
    action = req.get("action")
    if action not in ACTIONS:
        raise Refused("unknown action")
    if action == "install":
        return {"action": "install", "port": 1883, "ws_port": None,
                "username": "", "password": "", "access": "local"}

    port = _port(req.get("port"), "The MQTT port")
    ws = req.get("ws_port")
    ws_port = None if ws is None else _port(ws, "The WebSocket port")
    if ws_port is not None and ws_port == port:
        raise Refused("the WebSocket port must differ from the MQTT port")

    username = req.get("username", "")
    password = req.get("password", "")
    if not isinstance(username, str) or not isinstance(password, str):
        raise Refused("username and password must be text")
    if bool(username) != bool(password):
        raise Refused("set both a username and a password, or neither")
    if username and not USERNAME_RE.fullmatch(username):
        raise Refused("the username may use letters, digits, '_', '.' and '-' "
                      "(1 to 64 of them)")
    if password:
        if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            raise Refused(f"the password must be {PASSWORD_MIN} to "
                          f"{PASSWORD_MAX} characters")
        # isprintable() is False for every control character -- newline,
        # carriage return, tab, NUL -- which is what could split one entry of
        # the password file into two.
        if not password.isprintable():
            raise Refused("the password may not contain line breaks or "
                          "control characters")
    if "access" in req:
        access = req["access"]
        # A strict enum: exactly one of two strings. Not lowercased, not
        # stripped -- a value this helper had to tidy is not one it was sent.
        if not isinstance(access, str) or access not in ACCESS:
            raise Refused("who may connect must be 'local' or 'network'")
    else:
        access = "network" if username else "local"
    return {"action": "apply", "port": port, "ws_port": ws_port,
            "username": username, "password": password, "access": access}


#: The comment at the top of irivai.conf, one per combination. Fixed text.
_BLURB = {
    ("local", False): "# This box only, no login: nothing off this box may connect.\n",
    ("local", True): "# This box only, and every client must log in.\n",
    ("network", True): "# Any device on the network, and every client must log in.\n",
    ("network", False): ("# Any device on the network, NO login: chosen on the page,\n"
                         "# with its warning. Anyone on this network may read and\n"
                         "# publish to every topic.\n"),
}


def render_conf(req):
    """irivai.conf for a validated request. Integers and fixed text only."""
    secured = bool(req["username"])
    local = req.get("access", "network" if secured else "local") == "local"
    bind = " 127.0.0.1" if local else ""
    lines = [HEADER, _BLURB[("local" if local else "network", secured)]]
    lines.append(f"listener {int(req['port'])}{bind}\n")
    if req.get("ws_port") is not None:
        lines.append(f"listener {int(req['ws_port'])}{bind}\n")
        lines.append("protocol websockets\n")
    if secured:
        lines.append("allow_anonymous false\n")
        lines.append(f"password_file {PASSWD_PATH}\n")
    else:
        lines.append("allow_anonymous true\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# The box
# ---------------------------------------------------------------------------

def _run(argv, timeout=60, env=None, stdin=None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          env=env, stdin=stdin if stdin is not None else subprocess.DEVNULL)


def held_by(port):
    """Who is listening on TCP `port`: "" for nobody, else a program name.

    As root, ss names every owner -- including a Node-RED running as another
    account, which the platform itself cannot see."""
    try:
        out = _run(["ss", "-ltnpH", f"sport = :{int(port)}"], timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    if not out.strip():
        return ""
    names = re.findall(r'\(\("([^"]+)"', out)
    return names[0] if names else "another program"


def _listening(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write(path, data, mode, group=None, owner=None):
    """Atomic: a temp file beside the target, then a rename. Never a moment
    where mosquitto could read half a file, or a password file readable by
    everyone."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".irivai-")
    try:
        os.fchmod(fd, mode)
        if group is not None or owner is not None:
            os.fchown(fd, 0 if owner is None else owner,
                      -1 if group is None else group)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _restore(path, data, mode):
    if data is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    elif path == PASSWD_PATH:
        _write(path, data, *_passwd_owner())
    else:
        _write(path, data, mode)


def _passwd_owner():
    """(mode, group, owner) for a password file mosquitto can still READ.

    Normally 0640 root:mosquitto. A rollback is the one write that must not
    fail closed: if the group has gone, the file the broker ran on a moment
    ago goes back 0600 owned by the mosquitto USER -- a 0640 root:root file
    would be unreadable to it, and the rollback would leave the broker down.
    Neither exists only on a box whose mosquitto install is broken; the
    entries are hashes, so 0644 there is the least bad way to come back up."""
    gid = _mosquitto_gid()
    if gid is not None:
        return 0o640, gid, None
    import pwd                                               # noqa: PLC0415
    try:
        return 0o600, None, pwd.getpwnam("mosquitto").pw_uid
    except KeyError:
        return 0o644, None, None


def _mosquitto_gid():
    import grp                                               # noqa: PLC0415
    try:
        return grp.getgrnam("mosquitto").gr_gid
    except KeyError:
        return None


def hashed_passwd(username, password):
    """One password-file line, hashed by mosquitto's own tool.

    mosquitto_passwd -U rewrites a plain "user:password" file in place with
    the hashed form. The plain file lives for the length of that call inside
    a root-only 0700 temp directory, which is the only way to hand the tool a
    password without putting it on a command line."""
    tool = shutil.which("mosquitto_passwd", path=SAFE_PATH)
    if not tool:
        raise Refused("mosquitto_passwd is missing - reinstall the broker")
    folder = tempfile.mkdtemp(prefix="irivai-mqtt-",
                              dir=PLAIN_DIR if os.path.isdir(PLAIN_DIR) else None)
    try:
        os.chmod(folder, 0o700)
        plain = os.path.join(folder, "passwd")
        fd = os.open(plain, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{username}:{password}\n")
        r = _run([tool, "-U", plain], timeout=30)
        if r.returncode != 0:
            raise Refused("mosquitto_passwd could not hash the password")
        with open(plain, "rb") as f:
            line = f.read()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    # Proof it was hashed: "user:$<id>$...". A tool that silently left the
    # line alone must not get a plain password written to /etc.
    if not line.startswith(f"{username}:$".encode("utf-8")) \
            or password.encode("utf-8") in line:
        raise Refused("mosquitto_passwd did not hash the password")
    return line


def _journal_tail():
    try:
        out = _run(["journalctl", "-u", UNIT, "-n", "4", "--no-pager", "-o", "cat"],
                   timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    return " / ".join(x.strip() for x in out.splitlines() if x.strip())[-400:]


def _came_up(port, budget=8.0):
    deadline = time.time() + budget
    while time.time() < deadline:
        active = _run(["systemctl", "is-active", UNIT], timeout=10).stdout.strip()
        if active == "active" and _listening(port):
            return True
        time.sleep(0.5)
    return False


#: Seconds between two restarts in one run. A save whose broker does not come
#: up restarts twice (the new files, then the old ones); systemd counts
#: restarts against StartLimitBurst, and a burst of them can leave the unit
#: refusing to start at all -- which is the opposite of a rollback.
RESTART_GAP_S = 3.0
_last_restart = [0.0]


def _restart():
    wait = RESTART_GAP_S - (time.monotonic() - _last_restart[0])
    if _last_restart[0] and wait > 0:
        time.sleep(wait)
    _run(["systemctl", "reset-failed", UNIT], timeout=30)
    _run(["systemctl", "enable", UNIT], timeout=60)
    _run(["systemctl", "restart", UNIT], timeout=60)
    _last_restart[0] = time.monotonic()


def apply(req):
    if not shutil.which("mosquitto", path=SAFE_PATH):
        raise Refused("no broker is installed on this box")
    for p in [req["port"]] + ([req["ws_port"]] if req["ws_port"] else []):
        owner = held_by(p)
        if owner and "mosquitto" not in owner:
            raise Refused(f"Port {p} is used by {owner}")

    old_conf, old_pw = _read(CONF_PATH), _read(PASSWD_PATH)
    os.makedirs(CONF_DIR, exist_ok=True)
    restarted = False
    try:
        if req["username"]:
            gid = _mosquitto_gid()
            if gid is None:
                raise Refused("the mosquitto group is missing - reinstall the broker")
            _write(PASSWD_PATH, hashed_passwd(req["username"], req["password"]),
                   0o640, gid)
        else:
            _restore(PASSWD_PATH, None, 0)
        _write(CONF_PATH, render_conf(req).encode("utf-8"), 0o644)
        restarted = True
        _restart()
        if _came_up(req["port"]):
            where = ("this box only" if req["access"] == "local"
                     else "every interface") + (
                ", login required" if req["username"] else ", no login")
            return {"ok": True,
                    "message": f"broker running on port {req['port']} ({where})"}
        tail = _journal_tail()
    except BaseException:
        # Anything at all -- a refusal, a full disk, a timeout -- leaves the
        # files as they were, and a broker we already bounced is brought
        # back up on them.
        _restore(CONF_PATH, old_conf, 0o644)
        _restore(PASSWD_PATH, old_pw, 0o640)
        if restarted:
            try:
                _restart()
            except Exception:                                # noqa: BLE001
                pass
        raise
    # Did not come up: put back what was there and bring THAT up again.
    _restore(CONF_PATH, old_conf, 0o644)
    _restore(PASSWD_PATH, old_pw, 0o640)
    _restart()
    raise Refused("the broker did not start with these settings, so the "
                  "previous ones were put back"
                  + (f" (mosquitto said: {tail})" if tail else ""))


def install(_req):
    owner = held_by(1883)
    if owner and "mosquitto" not in owner:
        raise Refused(f"Port 1883 is used by {owner}")
    if not shutil.which("mosquitto", path=SAFE_PATH):
        env = {"PATH": SAFE_PATH, "DEBIAN_FRONTEND": "noninteractive",
               "LANG": "C.UTF-8"}
        # --no-remove: -y answers apt's "continue?" for REMOVALS too, and an
        # install that would take another package off the box is not what
        # this button means. Abort instead.
        rc, last = _apt_install(env)
        if rc is None:
            raise Refused(APT_SLOW)
        if rc != 0 or not shutil.which("mosquitto", path=SAFE_PATH):
            raise Refused("mosquitto did not install" + (f": {last[:200]}"
                                                          if last else "")
                          + ". " + DPKG_HINT)
    if _read(CONF_PATH) is None:
        return apply({"action": "apply", "port": 1883, "ws_port": None,
                      "username": "", "password": "", "access": "local"})
    _restart()
    return {"ok": True, "message": "broker installed"}


#: How long an install may take before this helper stops WAITING for it.
#: It is never killed: a SIGKILL in the middle of dpkg leaves the package
#: database half-configured, and every later apt run on the box then fails
#: until somebody runs `dpkg --configure -a`. Past this it is left to finish
#: in the background, and the admin is told how to check.
APT_BUDGET_S = 1500
APT_LOG = "/var/log/irivai-mqtt-install.log"
DPKG_HINT = ("If apt says dpkg was interrupted, run on the box: "
             "sudo dpkg --configure -a")
APT_SLOW = ("the install is taking longer than 25 minutes, so it was left to "
            "finish in the background (log: " + APT_LOG + "). If the broker "
            "still shows as not installed later, run on the box: sudo dpkg "
            "--configure -a, then press Install again")


def _apt_install(env, budget=APT_BUDGET_S):
    """(returncode, last log line), or (None, "") when still running.

    Output goes to a root-owned log FILE, not a pipe: apt left running after
    this helper exits must not die of SIGPIPE writing to a reader that is
    gone -- that would be the kill this avoids, by other means."""
    fd = os.open(APT_LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                 0o600)
    try:
        proc = subprocess.Popen(
            ["apt-get", "install", "-y", "--no-remove",
             "mosquitto", "mosquitto-clients"],
            stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, env=env,
            start_new_session=True)
    finally:
        os.close(fd)
    try:
        rc = proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        return None, ""
    try:
        with open(APT_LOG, encoding="utf-8", errors="replace") as f:
            lines = [x.strip() for x in f.read().splitlines() if x.strip()]
    except OSError:
        lines = []
    return rc, (lines[-1] if lines else "")


def _lock():
    """The box-wide lock, or Refused when another change holds it."""
    import fcntl                                             # noqa: PLC0415
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Refused("another broker change is still in progress - try "
                      "again in a moment") from None
    return fd


def main():
    os.environ["PATH"] = SAFE_PATH
    for name in ("TMPDIR", "TEMP", "TMP"):
        os.environ.pop(name, None)
    os.umask(0o077)
    if os.geteuid() != 0:
        print(json.dumps({"ok": False, "message": "this helper must run as root"}))
        return 1
    raw = sys.stdin.read(MAX_REQUEST + 1)
    try:
        _lock()                # held until this process exits
        if len(raw) > MAX_REQUEST:
            raise Refused("the request is too large")
        try:
            req = json.loads(raw)
        except ValueError:
            raise Refused("the request is not JSON") from None
        req = validate(req)
        result = install(req) if req["action"] == "install" else apply(req)
    except Refused as e:
        result = {"ok": False, "message": str(e)}
    except Exception as e:                                   # noqa: BLE001
        # Type only: an exception's text could, in principle, quote a value.
        result = {"ok": False, "message": f"the helper failed ({type(e).__name__})"}
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
