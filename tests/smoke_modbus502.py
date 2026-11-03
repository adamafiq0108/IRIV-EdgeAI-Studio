# A PLC on 502 reaches the box's Modbus server; the server's port cannot take
# anybody else's; registers fed by detection do not lie when the AI is off.
#
# WHAT WAS WRONG / WHAT WAS ASKED (Pipi's review, 2026-10-05, option B):
#   * every PLC polls 502 and a non-root app cannot bind it. A fixed
#     nftables redirect 502 -> 5020 replaces "point the PLC at 5020"
#     (CAP_NET_BIND_SERVICE was rejected: hub-spawned masters would not
#     inherit it, and it opens every low port to the app);
#   * 5020 is now the redirect's fixed target, so it is reserved: no hub,
#     master or stream may land there;
#   * set_modbus_server took any 1-65535 port, the hub's own included -- on
#     the next reboot the faster of the two won, and an app winning left the
#     box with no dashboard;
#   * modbus_server.py fell back to 502 when the port key was missing;
#   * a register fed by detection kept its LAST value when the AI went off,
#     so a PLC kept reading "3 people" from a box that was not looking.
#
#     python tests/smoke_modbus502.py
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from core.services import detector_control as dc              # noqa: E402
from core.services import hubport, provision                  # noqa: E402
from app import modbus_server as ms                           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8", newline="") as fh:
        return fh.read()


# -- the redirect ---------------------------------------------------------------
nft = read("scripts", "nftables", "modbus-redirect.nft")
rules = "\n".join(ln for ln in nft.splitlines() if not ln.strip().startswith("#"))
check("its own table, NAT prerouting at dstnat", "table inet irivai_modbus" in rules
      and "type nat hook prerouting priority dstnat; policy accept;" in rules)
check("502 addressed to the box, from anywhere but loopback, goes to 5020, and nothing else",
      'iifname != "lo" fib daddr type local tcp dport 502 redirect to :5020' in rules
      and rules.count("redirect") == 1 and "\r" not in nft)
inst = read("scripts", "install.sh")
step = inst.split("# MODBUS ON 502.", 1)
check("install.sh loads it once, root-owned, with a oneshot unit", len(step) == 2
      and "/usr/local/lib/irivai/modbus-redirect.nft" in step[1]
      and "irivai-modbus-redirect.service" in step[1]
      and "ExecStartPre=-/usr/sbin/nft delete table inet irivai_modbus" in step[1])
check("...and drops the marker the panel reads only when the table is really there",
      len(step) == 2 and "nft list table inet irivai_modbus" in step[1]
      and "/etc/irivai/modbus-502-redirect" in step[1])
check("no capability route: nothing grants CAP_NET_BIND_SERVICE",
      "CAP_NET_BIND_SERVICE" not in inst.replace("# ", ""))
check("nftables is in the package list",
      re.search(r"APT_PACKAGES=\([^)]*\bnftables\b", inst, re.S) is not None)
check("the Modbus panel is told whether the redirect is there",
      '"redirect_502": detector_control_service.modbus_redirect_installed()'
      in read("core", "routes", "models.py")
      and dc.MODBUS_REDIRECT_MARKER == "/etc/irivai/modbus-502-redirect")

# -- 5020 is reserved ---------------------------------------------------------------
check("5020 is a box port the hub can never be moved onto", 5020 in hubport.RESERVED)
check("...and the master/stream allocator skips it (and every live Modbus port)",
      "set(modbus_ports_in_use(systems_dir, config_dir))" in read("core", "services", "provision.py"))
mserver = read("systems", "demo", "app", "modbus_server.py")
check("the app's fallback is 5020, not 502", "or 502" not in mserver
      and ms.DEFAULT_PORT == 5020 and mserver.count("or DEFAULT_PORT") == 2)

# -- set_modbus_server refuses ports that belong to something else ------------------
# Created systems live in the store's generated dir (config/systems.toml
# [systems].enabled filters only the built-in dir), which is where a second
# Modbus system on a real box would be.
from core import config                                       # noqa: E402
systems = str(config.generated_systems_dir())
for slug in ("line_a", "line_b"):
    os.makedirs(os.path.join(systems, slug), exist_ok=True)
    open(os.path.join(systems, slug, "system.py"), "w").close()
    open(os.path.join(systems, slug, "config.toml"), "w").close()
real = (hubport.port, hubport.master_ports, provision.stream_ports_in_use)
hubport.port = lambda config_dir=None: 5100
hubport.master_ports = lambda config_dir=None: {5110: "line_a", 5111: "line_b"}
provision.stream_ports_in_use = lambda systems_dir=None, config_dir=None: {5210, 5211}


def refused(port, slug="line_a"):
    try:
        dc.set_modbus_server(systems, slug, {"enabled": True, "mode": "tcp", "port": port})
        return ""
    except ValueError as e:
        return str(e) or "refused"


try:
    check("below 1024 is refused, with the redirect named", "5020" in refused(502)
          and refused(80))
    check("a box service port is refused by name", "PostgreSQL" in refused(5432))
    check("the software's own port is refused", "software port" in refused(5100))
    check("a master's port is refused, naming the system", "line_b" in refused(5111))
    check("a system's stream port is refused", "video" in refused(5211))
    check("5020 itself is the Modbus port, taken when free", refused(5020) == "")
    check("...and a second system is refused it, naming the first",
          "line_a" in refused(5020, "line_b"))
    check("...unless the first one switches its server off",
          dc.set_modbus_server(systems, "line_a", {"enabled": False}) is not None
          and refused(5020, "line_b") == "")
    check("a switched-off server is not checked (nothing binds)",
          dc.set_modbus_server(systems, "line_a",
                               {"enabled": False, "port": 5100})["port"] == 5100)
finally:
    hubport.port, hubport.master_ports, provision.stream_ports_in_use = real
found = provision.modbus_ports_in_use(systems)
check("modbus_ports_in_use reads every package's switched-on TCP server",
      found == {5020: "line_b"}, repr(found))

# -- no stale registers when the AI goes off ----------------------------------------
class Block:
    def __init__(self):
        self.v = {}

    def setValues(self, addr, vals):
        self.v[addr] = vals[0]


srv = ms.ModbusServer()
srv.running = True
srv._blocks = {"hr": Block(), "ir": Block()}
srv.publish([{"address": 3, "value": 7}, {"address": 4, "value": 2, "kind": "input"}],
            detection_active=True)
check("registers are written, and IR 511 says detection is running",
      srv._blocks["hr"].v[3] == 7 and srv._blocks["ir"].v[4] == 2
      and srv._blocks["ir"].v[ms.STATUS_ADDRESS] == 1 and ms.STATUS_ADDRESS == 511)
srv.publish([{"address": 4, "value": 9, "kind": "input"}], detection_active=False)
check("a register whose output was pruned (AI off) reads 0, not its last value",
      srv._blocks["hr"].v[3] == 0 and srv._blocks["ir"].v[4] == 9)
check("...and IR 511 reads 0 with the AI off", srv._blocks["ir"].v[511] == 0)
srv.publish([], detection_active=False)
check("an empty batch zeroes what is left", srv._blocks["ir"].v[4] == 0)
run = read("systems", "demo", "app", "run.py")
check("the loop publishes every tick, empty or not, with the AI state",
      "modbus_srv.publish(_modbus_pending, detection_active=ai_active)" in run
      and "if _modbus_pending:\n                    modbus_srv.publish" not in run)
try:
    dc._validate_output({"type": "modbus_reg", "key": "zone_1_count",
                         "address": 511, "reg_kind": "input"})
    check("a Modbus Register node cannot take the status register", False)
except ValueError as e:
    check("a Modbus Register node cannot take the status register", "511" in str(e))
check("...the master's constant mirrors the app's",
      dc.MODBUS_STATUS_ADDRESS == ms.STATUS_ADDRESS)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
