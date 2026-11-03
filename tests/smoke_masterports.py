# Master ports and stream ports never overlap, and the hub's url for a
# built-in system is the port its installed unit runs on.
#
# A fresh install used to come up with demo's card pointing at 127.0.0.1:5101
# (the device agent's port) and red "down", while irivai-master@demo ran fine
# on 5110: the allocator counted up from the highest port in use, and on a
# fresh box that was only the hub's 5100. The next created system then got
# 5102 -- demo's MJPEG stream port. Everything here runs against a temp store,
# a temp config dir and a temp systems dir; nothing binds.
import os
import pathlib
import sys
import tempfile
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = os.path.join(TMP, "store")

from core import config                                      # noqa: E402
from core.services import hubport, provision                 # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name + (" -- " + str(detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def package(root, slug, stream_port=None):
    """A system package with a detection app, as the registry and the
    stream-port scan see one."""
    d = os.path.join(root, slug)
    os.makedirs(os.path.join(d, "app"), exist_ok=True)
    open(os.path.join(d, "system.py"), "w").close()
    with open(os.path.join(d, "config.toml"), "w", encoding="utf-8") as f:
        f.write("[app]\n" + ("stream_port = %d\n" % stream_port if stream_port else ""))


def port_of(url):
    return urllib.parse.urlparse(url or "").port


CFG = os.path.join(TMP, "config")
SYS = os.path.join(TMP, "systems")
os.makedirs(CFG)
with open(os.path.join(CFG, "systems.toml"), "w", encoding="utf-8") as f:
    f.write('dir = "systems"\nenabled = ["demo", "cam2"]\n[hub]\nurl = "http://127.0.0.1:5100"\n')
package(SYS, "demo", 5102)
package(SYS, "cam2")                       # no stream_port: the 5102 default
# Never actually bind in a test: every port is "free" unless a check says not.
provision._port_is_free = lambda port: True

print("== allocator")
streams = provision.stream_ports_in_use(SYS, CFG)
check("demo's stream port is seen", 5102 in streams, streams)
url, port = provision.next_master_url(config.systems(CFG), SYS, CFG)
check("fresh box: the first master is 5110, not the agent's 5101",
      port == provision.MASTER_PORT_BASE == 5110, port)

# A store system whose stream port sits right where the next master would go.
package(str(config.generated_systems_dir()), "gen1", 5111)
config.save_local("systems", {"masters": {"demo": {"url": "http://127.0.0.1:5110"}}}, CFG)
cfg = config.systems(CFG)
url, port = provision.next_master_url(cfg, SYS, CFG)
taken = provision._ports_in_use(cfg) | provision.stream_ports_in_use(SYS, CFG)
check("next master skips another system's stream port", port == 5112, port)
check("allocator never returns a port a system uses as master or stream",
      port not in taken and port not in hubport.RESERVED, (port, taken))
sp = provision.next_stream_port(port, cfg, SYS, CFG)
check("a new stream port is master + 100", sp == port + 100, sp)
config.save_local("systems", {"masters": {
    "demo": {"url": "http://127.0.0.1:5110"},
    "gen1": {"url": "http://127.0.0.1:5212"}}}, CFG)
sp = provision.next_stream_port(5112, config.systems(CFG), SYS, CFG)
check("...stepped past a master already there", sp == 5213, sp)

print("== hub url follows the installed unit")
provision.UNIT_ENV_DIR = os.path.join(TMP, "etc")
os.makedirs(provision.UNIT_ENV_DIR)
with open(os.path.join(provision.UNIT_ENV_DIR, "demo.env"), "w") as f:
    f.write("PORT=5110\n")
# The broken state a fresh install left behind: demo at the agent's port.
config.save_local("systems", {"masters": {
    "demo": {"url": "http://127.0.0.1:5101", "autostart": True}}}, CFG)
fixed = provision.reconcile_masters(["demo"], CFG, SYS)
entry = (config.systems(CFG).get("masters") or {}).get("demo") or {}
check("a demo url on 5101 is repaired", fixed == ["demo"], fixed)
check("demo card url == the unit's port", port_of(entry.get("url")) == 5110, entry)
check("...keeping the rest of the entry", entry.get("autostart") is True, entry)
check("a good url is left alone", provision.reconcile_masters(["demo"], CFG, SYS) == [])

config.save_local("systems", {}, CFG)
provision.reconcile_masters(["demo", "cam2"], CFG, SYS)
masters = config.systems(CFG).get("masters") or {}
check("no entry at all: demo takes the unit's port",
      port_of((masters.get("demo") or {}).get("url")) == 5110, masters)
cam2 = port_of((masters.get("cam2") or {}).get("url"))
check("...and the next system lands past it, clear of every stream port",
      cam2 and cam2 > 5110 and cam2 not in provision.stream_ports_in_use(SYS, CFG), cam2)

print("== an installed unit's port is taken even while nothing listens on it")
# Box .164 (2026-10-09): demo's unit (PORT=5110) was stopped and demo was no
# longer in [masters] when "z" was created, so z was handed 5110; at the next
# boot systemd's demo and the hub's z both started on it.
config.save_local("systems", {"masters": {}}, CFG)       # demo not in config
units = provision.unit_ports()
check("unit_ports reads /etc/irivai/<slug>.env", units == {5110: "demo"}, units)
url, port = provision.next_master_url(config.systems(CFG), SYS, CFG)
check("a new system skips the unit's port, bound or not", port != 5110 and port > 5110, port)
# The broken state itself: z already on the unit's port.
config.save_local("systems", {"masters": {
    "z": {"url": "http://127.0.0.1:5110", "autostart": True}}}, CFG)
fixed = provision.reconcile_masters(["z"], CFG, SYS)
z = (config.systems(CFG).get("masters") or {}).get("z") or {}
check("a system squatting on another's unit port is moved", fixed == ["z"], fixed)
check("...off that port", port_of(z.get("url")) not in (5110, None), z)
check("...keeping the rest of its entry", z.get("autostart") is True, z)
check("demo itself keeps its unit's port",
      provision.reconcile_masters(["demo"], CFG, SYS) == ["demo"]
      and port_of((config.systems(CFG)["masters"]["demo"] or {}).get("url")) == 5110,
      config.systems(CFG).get("masters"))

if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
