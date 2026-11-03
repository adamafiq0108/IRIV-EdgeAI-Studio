# "Check for updates automatically": OFF on every box, and off means the
# background never talks to GitHub (Tok Aba, 2026-10-05).
#
# Pinned:
#   1. default off: a fresh box and an existing box with a token both read
#      off, and the state a route returns says so
#   2. off: the checker's tick sends nothing -- no route probe, no TCP probe,
#      no GitHub request -- and the hub-start "what changed" notice does not
#      go online either
#   3. on: no default route -> nothing; a route but GitHub unreachable ->
#      only the cheap probe; the first probe that connects -> the check runs
#      on that tick; then nothing until the interval; a check that cannot
#      reach GitHub puts the box back to "offline" so the next return of the
#      uplink is noticed at once
#   4. switching it on checks at the next tick instead of waiting
#   5. a dev box checks both channels it has tokens for, under one switch
#   6. the setting lives in update.local.toml (carried by the applier), and
#      the token and the switch do not erase each other
#   7. /proc/net/route and ipv6_route parsing
#   8. the route is admin-gated and registered; start_checker delegates
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                        # noqa: E402
from core.services import ghnet                                # noqa: E402
from core.services import update as upd                        # noqa: E402
from core.services import update_auto as auto                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def box():
    d = tempfile.mkdtemp(prefix="smoke_otaauto_")
    with open(os.path.join(d, "update.toml"), "w", encoding="utf-8") as f:
        f.write('[release]\nowner = "o"\nrepo = "r"\ndev_repo = "rd"\n'
                '[check]\ninterval_hours = 6\n')
    return d


# Everything that could leave the box, counted.
calls = {"route": 0, "probe": 0, "github": 0}
route_up = {"v": True}
probe_ok = {"v": True}


def fake_route():
    calls["route"] += 1
    return route_up["v"]


def fake_probe():
    calls["probe"] += 1
    return probe_ok["v"]


answer = {"error": ""}


def fake_open(url, tok, **kw):
    calls["github"] += 1
    if answer["error"] == "offline":
        import urllib.error
        raise urllib.error.URLError(OSError(101, "Network is unreachable"))
    import io
    import json

    class R(io.BytesIO):
        headers = {"ETag": 'W/"1"'}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return R(json.dumps([]).encode())


auto.has_route = fake_route
auto.probe = fake_probe
ghnet.open_url = fake_open
upd._heal = lambda config_dir=None: False


def reset():
    for k in calls:
        calls[k] = 0


# -- 1. default off -------------------------------------------------------------
CFG = box()
check("a fresh box reads auto-check off", auto.enabled(CFG) is False)
upd.set_token("ghp_existing", CFG)
check("an existing box with a token still reads off", auto.enabled(CFG) is False)
check("the update state says auto_check false",
      upd.public_state({}, "stable", CFG).get("auto_check") is False)
with open(os.path.join(CFG, "update.local.toml"), "a", encoding="utf-8") as f:
    f.write('auto_check = "yes"\n')
check("anything but a real true reads off (fails closed)", auto.enabled(CFG) is False)
upd.set_token("ghp_existing", CFG)
local = config.load_local("update", CFG)
local.pop("auto_check", None)
config.save_local("update", local, CFG)

# -- 2. off: nothing leaves the box --------------------------------------------
clock = {"t": 1000.0}
t = auto.Ticker(CFG, clock=lambda: clock["t"])
reset()
for _ in range(5):
    out = t.tick()
check("off: the tick does nothing", out == "off", out)
check("off: no route read, no probe, no GitHub request", calls == {"route": 0, "probe": 0, "github": 0}, calls)

upd.ota_ready = lambda: True
upd.previous_version = lambda: "1.0.0"
reset()
try:
    upd.announce_installed(CFG, running="9.9.9")
except Exception as exc:                                       # noqa: BLE001
    check("announce_installed ran", False, repr(exc))
check("off: the hub-start 'what changed' notice does not go online", calls["github"] == 0, calls)

# -- 3. on ----------------------------------------------------------------------
auto.set_enabled(True, CFG)
check("the switch is stored as auto_check = true in update.local.toml",
      config.load_local("update", CFG).get("auto_check") is True)
check("...and the token beside it is untouched", upd.token(CFG) == "ghp_existing")
check("the update state says auto_check true",
      upd.public_state({}, "stable", CFG).get("auto_check") is True)

reset()
route_up["v"] = False
out = t.tick()
check("on, no default route: offline, nothing sent", out == "offline"
      and calls["probe"] == 0 and calls["github"] == 0, (out, calls))
route_up["v"], probe_ok["v"] = True, False
reset()
out = t.tick()
check("on, a route but GitHub unreachable: only the probe", out == "offline"
      and calls["probe"] == 1 and calls["github"] == 0, (out, calls))
probe_ok["v"] = True
reset()
out = t.tick()
check("on, the box came online: the check runs on that tick", out == "checked"
      and calls["github"] == 1, (out, calls))
reset()
clock["t"] += 3600
out = t.tick()
check("then nothing until the interval (no probe, no request)", out == "waiting"
      and calls["probe"] == 0 and calls["github"] == 0, (out, calls))
clock["t"] += 6 * 3600 * 1.1
reset()
out = t.tick()
check("at the interval: one check, no probe needed", out == "checked"
      and calls == {"route": 1, "probe": 0, "github": 1}, (out, calls))
answer["error"] = "offline"
clock["t"] += 6 * 3600 * 1.1
reset()
out = t.tick()
check("a check that cannot reach GitHub reads as offline", out == "offline"
      and t.online is False, (out, calls))
answer["error"] = ""
reset()
out = t.tick()
check("...and the next tick probes and checks again at once", out == "checked"
      and calls["probe"] == 1 and calls["github"] == 1, (out, calls))

# -- 4. switching on checks now --------------------------------------------------
reset()
out = t.tick()
check("(waiting before the kick)", out == "waiting", out)
auto.set_enabled(True, CFG)
reset()
out = t.tick()
check("switching it on checks at the next tick", out == "checked"
      and calls["github"] == 1, (out, calls))
auto.set_enabled(False, CFG)
reset()
out = t.tick()
check("switching it off stops it at once", out == "off" and calls["github"] == 0, (out, calls))
check("off removes the key (no 'false' left to read)",
      "auto_check" not in config.load_local("update", CFG))

# -- 5. dev box: both channels, one switch --------------------------------------
DEV = box()
upd.set_token("ghp_stable", DEV)
upd.set_token("ghp_dev", DEV, channel="dev")
real_dev = upd.dev_available
upd.dev_available = lambda config_dir=None: True
asked = []
real_check = upd.check
upd.check = lambda config_dir=None, channel=None, wait=False, force=False: (
    asked.append(channel) or {"error": ""})
auto.set_enabled(True, DEV)
auto.Ticker(DEV).tick()
check("a dev box checks both channels under the one switch",
      sorted(asked) == ["dev", "stable"], asked)
upd.check, upd.dev_available = real_check, real_dev

# -- 6. carried by the applier --------------------------------------------------
spec = importlib.util.spec_from_file_location(
    "_smoke_applier_auto", os.path.join(ROOT, "scripts", "apply_update.py"))
au = importlib.util.module_from_spec(spec)
spec.loader.exec_module(au)
rel = tempfile.mkdtemp()
os.makedirs(os.path.join(rel, "old", "config"))
os.makedirs(os.path.join(rel, "new", "config"))
config.save_local("update", {"token": "t", "auto_check": True},
                  os.path.join(rel, "old", "config"))
au.carry_config(os.path.join(rel, "old"), os.path.join(rel, "new"))
check("an update carries the switch into the new release",
      auto.enabled(os.path.join(rel, "new", "config")) is True)
check("the applier still reads only the token line",
      au.read_token(os.path.join(rel, "new", "config")) == "t")

# -- 7. route table parsing ------------------------------------------------------
d = tempfile.mkdtemp()
p4 = os.path.join(d, "route")
with open(p4, "w") as f:
    f.write("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "eth0\t0001A8C0\t00000000\t0001\t0\t0\t100\t00FFFFFF\n")
check("a table with only a LAN route has no default route",
      auto._default_route_in(p4) is False)
with open(p4, "a") as f:
    f.write("eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n")
check("a 0.0.0.0/0 row is a default route", auto._default_route_in(p4) is True)
p6 = os.path.join(d, "ipv6_route")
with open(p6, "w") as f:
    f.write("00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
            "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n")
check("an IPv6 ::/0 row is a default route", auto._default_route_in(p6, True) is True)

# -- 8. wiring -------------------------------------------------------------------
rsrc = open(os.path.join(ROOT, "core", "routes", "update_local.py"), encoding="utf-8").read()
check("POST /api/hub/update/auto is admin-gated like the Update button",
      '"/api/hub/update/auto"' in rsrc and "_require_admin(request" in rsrc.split("def set_auto", 1)[1])
hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
check("the hub registers the route file", "app.include_router(update_local_router)" in hub)
usrc = open(os.path.join(ROOT, "core", "services", "update.py"), encoding="utf-8").read()
check("start_checker hands over to update_auto",
      "return update_auto.start(config_dir)" in usrc)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
