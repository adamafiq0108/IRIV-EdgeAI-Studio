# The software port is a setting, and everything that says the port reads it.
#
# 5100 used to be a constant spelled in nine places. Now it is
# config/systems.local.toml [hub].port, read by core/services/hubport.py: the
# hub binds it, the masters' way back follows it, the front panel prints it,
# the OTA gate polls it. This test pins each of those to the one reader, and
# the Test button's verdicts: out of range, a port the box needs, a system's
# port, a port something is listening on, the current port, a free one.
import io
import os
import socket
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
for key in ("CYTRON_HUB_PORT", "CYTRON_HUB_PORT_SOURCE", "INVOCATION_ID"):
    os.environ.pop(key, None)

from core import config                                        # noqa: E402
from core.services import hubport                              # noqa: E402

CFG = tempfile.mkdtemp()
# A systems.toml like the committed one: a hub URL on 5100, two masters.
io.open(os.path.join(CFG, "systems.toml"), "w", encoding="utf-8").write(
    'dir = "systems"\n[hub]\nurl = "http://127.0.0.1:5100"\n'
    '[masters.demo]\nurl = "http://127.0.0.1:5110"\n'
    '[masters.parking]\nurl = "http://127.0.0.1:5111"\n')

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refused(value):
    try:
        hubport.parse(value)
        return ""
    except hubport.PortError as exc:
        return str(exc)


# -- reading -------------------------------------------------------------------
check("nothing configured reads 5100", hubport.port(CFG) == 5100)
check("...and a hub url on another port is read when there is no port key",
      (io.open(os.path.join(CFG, "systems.local.toml"), "w", encoding="utf-8")
       .write('[hub]\nurl = "http://127.0.0.1:5200"\n') or True)
      and hubport.port(CFG) == 5200)
os.remove(os.path.join(CFG, "systems.local.toml"))
config.load.cache_clear()

# -- range ---------------------------------------------------------------------
check("a word is not a port", "whole number" in refused("eighty"))
check("a bool is not a port", "whole number" in refused(True))
check("below 1024 needs root, so it is refused by range",
      "between 1024 and 65535" in refused(80))
check("above 65535 is refused", "between" in refused(70000))
check("1024 and 65535 are both allowed", hubport.parse(1024) == 1024
      and hubport.parse("65535") == 65535)

# -- the box's own services and the systems' ports ------------------------------------
for port, who in ((1883, "MQTT"), (5432, "PostgreSQL"), (8554, "video gateway"),
                  (8889, "video gateway"), (9997, "video gateway"), (5101, "agent")):
    verdict = hubport.check(port, CFG) if port >= 1024 else None
    check(f"{port} is refused as {who}'s",
          verdict is not None and not verdict["usable"] and who.lower()
          in verdict["detail"].lower(), repr(verdict))
for port in (22, 53, 80, 443):
    check(f"{port} is out of range before it is even looked up", refused(port) != "")
verdict = hubport.check(5111, CFG)
check("a system's own port is refused by the system's name",
      not verdict["usable"] and '"parking"' in verdict["detail"], repr(verdict))

# -- a port something is listening on ---------------------------------------------------
held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
held.bind(("0.0.0.0", 0))
held.listen(1)
busy = held.getsockname()[1]
try:
    verdict = hubport.check(busy, CFG)
    check("a port something already listens on is refused",
          not verdict["usable"] and "already listens" in verdict["detail"], repr(verdict))
    try:
        hubport.save(busy, CFG)
        check("...and save refuses it too (the Test button is advice, save is the gate)",
              False)
    except hubport.PortError as exc:
        check("...and save refuses it too (the Test button is advice, save is the gate)",
              "already listens" in str(exc))
finally:
    held.close()

# -- the current port, and a free one ----------------------------------------------------
os.environ["CYTRON_HUB_PORT"] = "5100"
verdict = hubport.check(5100, CFG)
check("the port the hub answers on now is 'current', not a clash",
      verdict["usable"] and verdict["current"], repr(verdict))
probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
probe.bind(("0.0.0.0", 0))
free = probe.getsockname()[1]
probe.close()
verdict = hubport.check(free, CFG)
check("a free port is free", verdict["usable"] and not verdict["current"], repr(verdict))

# -- the round trip --------------------------------------------------------------------
saved = hubport.save(free, CFG)
check("save returns the port it wrote", saved == free)
check("...and port() reads it back", hubport.port(CFG) == free)
local = config.load_local("systems", CFG)
check("...with [hub].url kept in step, for every reader of the hub's address",
      local["hub"]["port"] == free and local["hub"]["url"] == f"http://127.0.0.1:{free}",
      repr(local))
check("status: the process still holds 5100; a direct save stages nothing",
      hubport.status(CFG)["running"] == 5100 and hubport.status(CFG)["pending"] is None)
check("...and lists the systems' ports, which do not move",
      [s["port"] for s in hubport.status(CFG)["systems"]] == [5110, 5111])


# -- every URL producer reads the configured port --------------------------------------
class Req:
    def __init__(self, host):
        self.headers = {"host": host}
        self.url = type("U", (), {"hostname": host.split(":")[0]})()
        self.client = None


check("the browser-facing hub URL is the request's host on the configured port",
      hubport.browser_url(Req("192.168.1.234:5110"), CFG)
      == f"http://192.168.1.234:{free}")


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


AUTH = read("core", "routes", "auth.py")
SPA = read("core", "routes", "spa.py")
PANEL = read("core", "services", "panel.py")
UPDATE = read("core", "services", "update.py")
HUB = read("hub_server.py")
check("the login bounce reads it (core/routes/auth.py)",
      "hubport.browser_url(request" in AUTH and "default_port=5100" not in AUTH)
check("the '< Systems' link reads it (core/routes/spa.py)",
      "hubport.browser_url(request" in SPA and "default_port=5100" not in SPA)
# Through webaccess.panel_address: the port in port mode, none in proxy mode.
check("the front panel prints it (core/services/panel.py)",
      "webaccess.panel_address(hostname.local_name(), config_dir)" in PANEL
      and '"%s:%d" % (local_name, hubport.port(config_dir))'
      in read("core", "services", "webaccess.py"))
check("...and the committed panel.toml no longer pins 5100 over it",
      'dashboard_url = "iriv.local:5100"' not in
      "".join(l for l in read("config", "panel.toml").splitlines(True)
              if not l.lstrip().startswith("#")))
check("the OTA health gate polls it (core/services/update.py)",
      "hubport.running_port() or hubport.port(config_dir)" in UPDATE
      and 'environ.get("CYTRON_HUB_PORT")' not in UPDATE)
check("the hub binds it (hub_server.py)",
      "hubport.resolve_cli(args.port" in HUB and 'default=None' in HUB)

# -- how the hub resolves its port at start ------------------------------------------
os.environ.pop("CYTRON_HUB_PORT", None)
check("no --port: the configured port, recorded as such",
      hubport.resolve_cli(None, CFG) == free
      and os.environ["CYTRON_HUB_PORT"] == str(free)
      and os.environ["CYTRON_HUB_PORT_SOURCE"] == "config")
check("an explicit --port wins outside systemd, recorded as argv",
      hubport.resolve_cli(5999, CFG) == 5999
      and os.environ["CYTRON_HUB_PORT_SOURCE"] == "argv"
      and hubport.port(CFG) == free)
check("...and is not written to the config outside systemd",
      config.load_local("systems", CFG)["hub"]["port"] == free)
# A pre-October unit under systemd bakes --port: the SETTING wins over it, and
# the baked port only seeds a file with no port yet -- so the page can move
# the port on every installed box with no one-time step (2026-10-05).
os.environ["INVOCATION_ID"] = "abc"
# Which unit this process is, from its cgroup (v2 and v1 lines).
real_owns = hubport.owns_setting
for lines, want in ((["0::/system.slice/irivai-hub.service\n"], True),
                    (["1:name=systemd:/system.slice/irivai-hub.service\n"], True),
                    (["0::/system.slice/irivai-hub-b.service\n"], False),
                    (["0::/user.slice/user-1000.slice/session-3.scope\n"], False)):
    hubport.open = lambda *a, _l=lines, **k: io.StringIO("".join(_l))
    check(f"owns_setting reads {lines[0].split('/')[-1].strip()!r} as {want}",
          real_owns() is want)
del hubport.open
# A SECOND hub under systemd sharing this config dir (a dev box): its own
# --port stands, and nothing is adopted from it (Pipi hand-off, 2026-10-05).
hubport.owns_setting = lambda: False
check("a second hub's unit keeps its own --port over the shared setting",
      hubport.resolve_cli(5997, CFG) == 5997 and hubport.port(CFG) == free
      and os.environ["CYTRON_HUB_PORT_SOURCE"] == "argv")
hubport.owns_setting = lambda: True
check("in irivai-hub.service the saved port wins over a baked --port",
      hubport.resolve_cli(5998, CFG) == free and hubport.port(CFG) == free
      and os.environ["CYTRON_HUB_PORT_SOURCE"] == "config")
local = config.load_local("systems", CFG)
del local["hub"]["port"]
config.save_local("systems", local, CFG)
hubport.resolve_cli(5998, CFG)
check("...but is adopted when the file has no port yet",
      hubport.port(CFG) == 5998 and config.load_local("systems", CFG)["hub"]["url"]
      == "http://127.0.0.1:5998")
check("...and the setting is never 'locked' (no unit_fix commands for the page)",
      "locked" not in hubport.status(CFG) and "unit_fix" not in hubport.status(CFG))
os.environ.pop("INVOCATION_ID", None)
hubport.owns_setting = real_owns

# -- the route is the owner's, and the hub mounts it ---------------------------------
ROUTE = read("core", "routes", "hubport.py")
check("every endpoint asks is_admin (master or developer)",
      ROUTE.count("    _require_owner(request)\n") == 3 and "auth_service.is_admin(" in ROUTE)
check("the save STAGES the port; no hub-restart route (a reboot applies it)",
      "hubport.stage, body.get(\"port\")" in ROUTE and "/api/hub/port/restart" not in ROUTE
      and "RESTART_ARGV" not in read("core", "services", "hubport.py"))
check("the hub mounts the router", "hubport_router" in HUB)
INSTALL = read("scripts", "install.sh")
check("the appliance unit no longer bakes --port",
      "ExecStart=${PY} hub_server.py\n" in INSTALL
      and "ExecStart=${PY} hub_server.py --port" not in INSTALL)
check("...and --hub-port is written as the setting instead",
      "hubport.save(int(sys.argv[1]), validate=False)" in INSTALL)
check("the sudoers rule the restart uses is the box's reboot, unchanged",
      "/usr/sbin/reboot now, /sbin/shutdown now, /sbin/reboot now" in INSTALL)
check("the MQTT broker form refuses the hub's port and the one saved for its restart",
      "hubport.claimed_ports(config_dir) & {port, ws_port}"
      in read("core", "services", "mqtt_broker.py"))
check("the Network page draws the card",
      "<SoftwarePortCard />" in read("frontend", "src", "components", "NetworkPanel.jsx"))

print()
if bad:
    print(f"{bad} check(s) FAILED")
    sys.exit(1)
print("ALL OK")
