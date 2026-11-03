# The software port and the hostname change at the next RESTART of the box,
# never on Save (Tok Aba, 2026-10-05; core/services/boxpending.py).
#
# Pinned:
#   1. pending vs applied: a saved port / name is pending, the box keeps the
#      old ones ([hub].port and [hub].url untouched, the helper not run)
#   2. the same boot applies nothing (a hub crash or an OTA restart)
#   3. both changes, ONE restart: the next boot's first hub start applies the
#      port and runs the root helper once with the name on stdin; a second
#      start applies nothing
#   4. saving the current value cancels a pending change
#   5. failure keeps the OLD value: a port taken or spoken for by the
#      restart, a helper gone or refusing; each said in a plain sentence
#   6. the root side validates on its own: scripts/set_hostname.py refuses a
#      bad name (and a failed hostnamectl) before touching anything
#   7. a box without the helper is refused at Save in plain words
#   8. the pending port is reserved for systems and the MQTT form meanwhile
#   9. POST /api/hub/restart: refused with nothing pending or no reboot rule,
#      else one reboot, and the reply carries where to go
#  10. the hub applies it in main(), before the port is resolved; only the
#      box's own unit (irivai-hub.service) does -- a second hub sharing the
#      config applies nothing
#  11. a name another device on the LAN already answers is refused at Save
#      (Pipi L1), and one of this box's own addresses is not "another device"
#
#   py -3 tests/smoke_boxrestart.py
import asyncio
import importlib.util
import io
import os
import socket
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
for key in ("CYTRON_HUB_PORT", "CYTRON_HUB_PORT_SOURCE"):
    os.environ.pop(key, None)
os.environ["INVOCATION_ID"] = "smoke"          # a systemd service

from core import config                                          # noqa: E402
from core.services import (boxpending, discovery_mdns, hostname,  # noqa: E402
                           hubport, provision)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
io.open(os.path.join(CFG, "systems.toml"), "w", encoding="utf-8").write(
    'dir = "systems"\n[hub]\nurl = "http://127.0.0.1:5100"\n'
    '[masters.demo]\nurl = "http://127.0.0.1:5110"\n')
io.open(os.path.join(CFG, "systems.local.toml"), "w", encoding="utf-8").write(
    '[hub]\nport = 5100\nurl = "http://127.0.0.1:5100"\n')


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# The box: its kernel name, its boot, its helper, its addresses.
BOX = {"name": "iriv", "boot": "boot-A", "can_set": True, "helper_rc": 0, "runs": []}
boxpending.boot_id = lambda: BOX["boot"]
hostname.current = lambda: BOX["name"]
hostname.can_set = lambda: BOX["can_set"]
hostname.local_name = lambda: BOX["name"] + ".local"
discovery_mdns.interface_ips = lambda names=None, ip_of=None: ["192.168.1.21"]
discovery_mdns._reload_panel = lambda: None
hubport.owns_setting = lambda: True             # this is irivai-hub.service


def fake_helper(argv, **kw):
    # subprocess is one module: anything but the helper (a sudo -l probe)
    # is simply "not allowed" here.
    if tuple(argv) != hostname.HELPER_ARGV:
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")
    BOX["runs"].append((list(argv), kw.get("input")))
    if BOX["helper_rc"] == 0:
        BOX["name"] = kw["input"].strip()
        return types.SimpleNamespace(returncode=0, stdout=BOX["name"], stderr="")
    return types.SimpleNamespace(returncode=2, stdout="",
                                 stderr="set-hostname: hostnamectl: failed\n")


hostname.subprocess.run = fake_helper
REBOOT = {"ok": True}
boxpending.can_reboot = lambda: REBOOT["ok"]
os.environ["CYTRON_HUB_PORT"] = "5100"          # this hub bound 5100

# -- 1. pending vs applied -------------------------------------------------------
NEW = free_port()
check("a port is saved for the restart", hubport.stage(NEW, "boot-A", CFG) == NEW)
local = config.load_local("systems", CFG)["hub"]
check("...the port and the url every reader uses do NOT move",
      hubport.port(CFG) == 5100 and local["url"] == "http://127.0.0.1:5100", local)
st = hubport.status(CFG)
check("...the card reads 'answering on 5100, after restart NEW'",
      st["running"] == 5100 and st["pending"] == NEW, st)
check("a name is saved for the restart", hostname.stage("Gate-A", "boot-A", CFG) == "gate-a")
check("...the box keeps its name and the helper has not run",
      BOX["name"] == "iriv" and BOX["runs"] == [] and hostname.pending(CFG) == "gate-a")
rs = boxpending.status(None, CFG)
check("one restart block names both, and where to go after",
      rs["any"] and rs["port"] == NEW and rs["hostname"] == "gate-a"
      and rs["current"] == {"port": 5100, "name": "iriv.local", "access": "port"}
      and rs["after"]["name_url"] == f"http://gate-a.local:{NEW}"
      and rs["after"]["ip_urls"] == [f"http://192.168.1.21:{NEW}"], rs)


class Req:
    def __init__(self, host):
        self.url = types.SimpleNamespace(hostname=host)


after = boxpending.after(Req("10.42.0.1"), CFG)
check("the browser's own address comes first when it used one",
      after["ip_urls"][0] == f"http://10.42.0.1:{NEW}", after)

# -- 2. the same boot applies nothing ------------------------------------------------
check("a hub start in the SAME boot applies nothing",
      boxpending.apply_at_start(CFG) is None and hubport.port(CFG) == 5100
      and BOX["name"] == "iriv" and hubport.pending_port(CFG) == NEW)

# -- 3. both, one restart ------------------------------------------------------------
BOX["boot"] = "boot-B"
rec = boxpending.apply_at_start(CFG)
check("the next boot applies the port", rec and rec["port_ok"] and hubport.port(CFG) == NEW
      and config.load_local("systems", CFG)["hub"]["url"] == f"http://127.0.0.1:{NEW}", rec)
check("...and the name, through the root helper once, name on stdin",
      rec["name_ok"] and BOX["name"] == "gate-a"
      and BOX["runs"] == [(list(hostname.HELPER_ARGV), "gate-a\n")], BOX["runs"])
check("...nothing left pending", hubport.pending_port(CFG) is None
      and hostname.pending(CFG) == "" and not boxpending.status(None, CFG)["any"])
check("...the card can say what this boot's restart did",
      boxpending.last(CFG) == rec and boxpending.failures(rec) == [])
check("a second hub start in that boot applies nothing more",
      boxpending.apply_at_start(CFG) is None and len(BOX["runs"]) == 1)
BOX["boot"] = "boot-C"
check("...and the record belongs to its own boot only", boxpending.last(CFG) is None)
os.environ["CYTRON_HUB_PORT"] = str(NEW)        # the hub now binds NEW

# -- 4. cancel ------------------------------------------------------------------------
hubport.stage(free_port(), "boot-C", CFG)
hostname.stage("gate-b", "boot-C", CFG)
hubport.stage(NEW, "boot-C", CFG)
hostname.stage("gate-a", "boot-C", CFG)
check("saving the current port and name cancels both",
      hubport.pending_port(CFG) is None and hostname.pending(CFG) == "")

# -- 5. failure keeps the old values -----------------------------------------------------
TAKEN = free_port()
hubport.stage(TAKEN, "boot-C", CFG)
hostname.stage("gate-b", "boot-C", CFG)
BOX["helper_rc"] = 2
held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
held.bind(("0.0.0.0", TAKEN))
held.listen(1)
BOX["boot"] = "boot-D"
try:
    rec = boxpending.apply_at_start(CFG)
finally:
    held.close()
check("a port taken by the restart: the OLD port stays",
      rec["port_ok"] is False and hubport.port(CFG) == NEW
      and "already using" in rec["port_detail"], rec)
check("a helper that fails: the OLD name stays", rec["name_ok"] is False
      and BOX["name"] == "gate-a" and "failed" in rec["name_detail"], rec)
said = boxpending.failures(rec)
check("...each said in a plain sentence for the Inbox",
      len(said) == 2 and said[0].startswith(f"The box restarted but kept port {NEW}")
      and "kept the hostname gate-a" in said[1] and all("\u2014" not in x for x in said), said)
check("...and a refused value is not retried every boot",
      hubport.pending_port(CFG) is None and hostname.pending(CFG) == "")

BOX["helper_rc"] = 0
hostname.stage("gate-c", "boot-D", CFG)
BOX["can_set"] = False                           # the helper is gone by the restart
local = dict(config.load_local("systems", CFG))
local["hub"] = dict(local["hub"], pending_port=5101, pending_boot="boot-D")
config.save_local("systems", local, CFG)         # a hand-edited pending port
BOX["boot"] = "boot-E"
rec = boxpending.apply_at_start(CFG)
check("a pending port the box needs (5101) is refused at the restart",
      rec["port_ok"] is False and "device agent" in rec["port_detail"]
      and hubport.port(CFG) == NEW, rec)
check("a helper gone by the restart: old name, plain reason",
      rec["name_ok"] is False and BOX["name"] == "gate-a"
      and "one-time update by the installer" in rec["name_detail"], rec)

# -- 7. no helper: refused at Save -------------------------------------------------------
try:
    hostname.stage("gate-d", "boot-E", CFG)
    check("no helper: Save refuses", False, "staged")
except hostname.HostnameError as exc:
    check("no helper: Save refuses in plain words, no command",
          "one-time update by the installer" in str(exc) and "sudo" not in str(exc))
BOX["can_set"] = True

# -- 11. a name the LAN already answers ----------------------------------------------------
real_which, real_run = hostname.shutil.which, hostname.subprocess.run
RESOLVE = {"out": "", "rc": 2, "argv": []}


def fake_resolve(argv, **kw):
    if argv and argv[0] == "avahi-resolve":
        RESOLVE["argv"] = list(argv)
        return types.SimpleNamespace(returncode=RESOLVE["rc"], stdout=RESOLVE["out"], stderr="")
    return real_run(argv, **kw)


hostname.shutil.which = lambda n: "/usr/bin/" + n if n == "avahi-resolve" else real_which(n)
hostname.subprocess.run = fake_resolve
try:
    RESOLVE.update(out="gate-x.local 192.168.1.50", rc=0)
    try:
        hostname.stage("gate-x", "boot-E", CFG)
        check("a name another device answers is refused", False, "staged")
    except hostname.HostnameError as exc:
        check("a name another device answers is refused, in plain words",
              "another device on this network (192.168.1.50) already answers to "
              "gate-x.local" in str(exc) and hostname.pending(CFG) == "", exc)
    check("...asked as a list argv with the validated label",
          RESOLVE["argv"] == ["avahi-resolve", "-4", "-n", "gate-x.local"], RESOLVE["argv"])
    RESOLVE.update(out="gate-x.local 192.168.1.21", rc=0)
    check("this box's own address answering is not a clash",
          hostname.stage("gate-x", "boot-E", CFG) == "gate-x")
    RESOLVE.update(out="", rc=2)
    check("no answer: the name is free", hostname.taken_by("gate-y") == "")
    hostname.shutil.which = lambda n: None
    check("no avahi-resolve on the box: not refused", hostname.taken_by("gate-y") == "")
finally:
    hostname.shutil.which, hostname.subprocess.run = real_which, real_run
hostname.stage("gate-a", "boot-E", CFG)          # back to the current name

# -- 6. the root side validates on its own ------------------------------------------------
path = os.path.join(ROOT, "scripts", "set_hostname.py")
spec = importlib.util.spec_from_file_location("set_hostname_smoke", path)
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)
had_geteuid = hasattr(sh.os, "geteuid")
real_geteuid = getattr(sh.os, "geteuid", None)
real_stdin, real_run = sys.stdin, sh.subprocess.run
ran, opened = [], []
had_uname = hasattr(sh.os, "uname")
real_uname = getattr(sh.os, "uname", None)
sh.os.geteuid = lambda: 0
sh.os.uname = lambda: types.SimpleNamespace(nodename="iriv")
sh.subprocess.run = lambda argv, **kw: ran.append(argv) or types.SimpleNamespace(
    returncode=1, stdout="", stderr="no dbus")
sh.open = lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError("no"))
try:
    for raw in ("a;rm -rf /", "UPPER", "x" * 64, "localhost", "12345", "-x", "a.b", ""):
        sys.stdin = io.StringIO(raw + "\n")
        rc = sh.main()
        check(f"root refuses {raw[:12]!r} before running anything",
              rc == 2 and ran == [] and opened == [], (rc, ran, opened))
    sys.stdin = io.StringIO("gate-a\n")
    rc = sh.main()
    check("a failed hostnamectl stops before /etc/hosts is touched",
          rc == 2 and len(ran) == 1 and ran[0][0] == "/usr/bin/hostnamectl" and opened == [])
finally:
    sys.stdin, sh.subprocess.run = real_stdin, real_run
    del sh.open
    if had_geteuid:
        sh.os.geteuid = real_geteuid
    else:
        del sh.os.geteuid
    if had_uname:
        sh.os.uname = real_uname
    else:
        del sh.os.uname

# -- 8. the pending port is reserved meanwhile ---------------------------------------------
P2 = free_port()
hubport.stage(P2, "boot-E", CFG)
cfg = config.systems(CFG)
check("a system created before the restart cannot be handed the pending port",
      P2 in provision._ports_in_use(cfg) and NEW in provision._ports_in_use(cfg))
check("...nor the MQTT form", P2 in hubport.claimed_ports(CFG))
hubport.stage(NEW, "boot-E", CFG)

# -- 9. the restart route ------------------------------------------------------------------
# The dev box has no fastapi; the same stand-in smoke_updatelock uses.
try:
    import fastapi                                              # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request = _HTTPException, object
    sys.modules["fastapi"] = fa
from fastapi import HTTPException                               # noqa: E402
from core.routes import boxrestart                              # noqa: E402
from core.services import audit as audit_service                # noqa: E402
from core.services import auth as auth_service                  # noqa: E402

auth_service.is_admin = lambda user, cfg=None: user == "master"
audit_service.record = lambda **kw: None
reboots = []
boxpending.reboot = lambda delay=1.0: reboots.append(delay)


def call(user):
    req = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)),
        state=types.SimpleNamespace(user=user), client=None,
        url=types.SimpleNamespace(hostname="192.168.1.21"))
    try:
        return asyncio.run(boxrestart.box_restart(req))
    except HTTPException as exc:
        return exc


r = call("admin")
check("a site admin may not", isinstance(r, HTTPException) and r.status_code == 403)
r = call("master")
check("nothing pending: refused, no reboot",
      isinstance(r, HTTPException) and "nothing is waiting" in r.detail and not reboots)
hostname.stage("gate-z", "boot-E", CFG)
REBOOT["ok"] = False
r = call("master")
check("no reboot rule: refused in plain words",
      isinstance(r, HTTPException) and "switch it off and on" in r.detail and not reboots)
REBOOT["ok"] = True
r = call("master")
check("else one reboot, and the reply says where to go",
      isinstance(r, dict) and r["restarting"] and reboots == [1.0]
      and r["after"]["name_url"] == f"http://gate-z.local:{NEW}"
      and r["after"]["ip_urls"][0] == f"http://192.168.1.21:{NEW}", r)

# -- 10. wiring --------------------------------------------------------------------------------
os.environ.pop("INVOCATION_ID", None)
hostname.stage("gate-q", "boot-E", CFG)
BOX["boot"] = "boot-F"
hubport.owns_setting = lambda: False
check("a second hub (not irivai-hub.service) applies nothing at start",
      boxpending.apply_at_start(CFG) is None and hostname.pending(CFG) == "gate-q"
      and BOX["name"] == "gate-a")
# ...and such a hub REFUSES to stage (Yaya's QA part 1, bug 6): a port or
# name saved from it would never apply, and the card kept promising it.
for label, fn, exc_type in (("port", lambda: hubport.stage(free_port(), "boot-F", CFG),
                             hubport.PortError),
                            ("name", lambda: hostname.stage("gate-r", "boot-F", CFG),
                             hostname.HostnameError)):
    try:
        fn()
        check(f"a second hub refuses to save a {label}", False, "staged")
    except exc_type as exc:
        check(f"a second hub refuses to save a {label}, in plain words",
              "not the box's own service" in str(exc) and "sudo" not in str(exc), exc)
check("...nothing new was staged, the earlier value is left alone",
      hubport.pending_port(CFG) is None and hostname.pending(CFG) == "gate-q")
rs = boxpending.status(None, CFG)
check("...and the status block says this hub applies nothing",
      rs["applies"] is False and rs["any"], rs)
hubport.owns_setting = lambda: True
check("the box's own service: applies True", boxpending.status(None, CFG)["applies"] is True)
hostname.stage("gate-a", "boot-F", CFG)          # back to the current name


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


HUB = read("hub_server.py")
check("the hub applies it in main(), before the port is resolved",
      0 < HUB.index("boxpending.apply_at_start(args.config_dir)")
      < HUB.index("hubport.resolve_cli(args.port"))
check("...posts a refusal to the admins' Inbox, and mounts the restart route",
      "boxpending.failures(applied)" in HUB and "app.include_router(boxrestart_router)" in HUB)
BP = read("core", "services", "boxpending.py")
check("the reboot argv is a constant the sudoers rule names (reboot now)",
      'REBOOT_ARGV = ("sudo", "-n", "reboot", "now")' in BP
      and "/usr/sbin/reboot now" in read("scripts", "install.sh"))
check("the save routes STAGE, they do not apply",
      "hostname.stage, name" in read("core", "routes", "hostname.py")
      and "hostname.set_name" not in read("core", "routes", "hostname.py")
      and "hubport.stage, body.get" in read("core", "routes", "hubport.py"))
for rel in (("core", "services", "boxpending.py"), ("core", "routes", "boxrestart.py"),
            ("frontend", "src", "components", "BoxRestartNote.jsx"),
            ("frontend", "src", "components", "SoftwarePortCard.jsx"),
            ("frontend", "src", "components", "HostnameCard.jsx")):
    check(f"no em dash in {rel[-1]}", "\u2014" not in read(*rel))

print("FAIL" if bad else "OK")
sys.exit(1 if bad else 0)
