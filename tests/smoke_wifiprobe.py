# -*- coding: utf-8 -*-
"""
smoke_wifiprobe.py -- after a Wi-Fi switch the page finds the box again.

Tok Aba (2026-10-07): switching AP <-> Wi-Fi opens the page at the box's new
address by itself. The browser cannot learn the address after the fact, so
the box says BEFORE it moves where it may be (wifiswitch.candidates) and
answers one open, cross-origin probe (GET /api/health/box). Pinned here
against a fake NetworkManager, on any machine:

  1. to the AP: the AP's own address, marked as the box's own (the session
     may travel there), plus a cable's address and the .local name
  2. to Wi-Fi: the profile's fixed address (own), the address this hub saw
     wlan0 hold on it, NetworkManager's last lease for it (internal client and
     dhclient formats) -- those two NOT own -- for the chosen profile only;
     never the AP's, never a 169.254 address, never a docker bridge
  3. start() hands the list out with the switch, read before anything moves
  4. GET /api/health/box: this box's hashed id (not the serial) and one word
     of switch state, nothing else; CORS on that response; open by name
     (auth.OPEN_EXACT), and nothing else under /api/health is (Pipi L1)
  5. the route passes the devices it already read, and the admin payload
     carries box_id for the page to match against
"""

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import hostinfo, hostname, netconf, wifiswitch  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


UUID_PLANT = "1b2c3d4e-0000-4000-8000-00000000a001"
UUID_OFFICE = "1b2c3d4e-0000-4000-8000-00000000a002"
calls = []
box = {"conn": "Hotspot", "ip": "10.42.0.1"}


def fake_nmcli(args, sudo=False, timeout=45):
    a = tuple(args)
    calls.append((a, sudo))
    if a[:3] == ("-t", "-f", "NAME,UUID,TYPE"):
        return ("Hotspot:aaaa0000-0000-4000-8000-000000000000:802-11-wireless\n"
                f"Plant:{UUID_PLANT}:802-11-wireless\n"
                f"Office:{UUID_OFFICE}:802-11-wireless\n"
                "Wired connection 1:bbbb0000-0000-4000-8000-000000000000:802-3-ethernet\n")
    # A prefix: netconf reads more fields over time (ignore-auto-dns, 10-09).
    if a[:2] == ("-t", "-f") and a[2].startswith("ipv4.method,ipv4.addresses,"):
        if a[-1] == UUID_OFFICE:
            return "ipv4.method:manual\nipv4.addresses:192.168.5.40/24\n"
        return "ipv4.method:auto\nipv4.addresses:\n"
    if a[:3] == ("-t", "-f", "GENERAL.CONNECTION"):
        return "GENERAL.CONNECTION:%s\n" % box["conn"]
    if a[:3] == ("-t", "-f", "802-11-wireless.mode"):
        return "802-11-wireless.mode:%s\n" % ("ap" if a[-1] == "Hotspot" else "infrastructure")
    if a[:3] == ("-t", "-f", "NAME,TYPE,ACTIVE,DEVICE"):
        return "".join("%s:802-11-wireless:no:wlan0\n" % n for n in ("Plant", "Office", "Hotspot"))
    return ""


netconf._nmcli = fake_nmcli
hostname.local_name = lambda: "iriv-1234abcd.local"
LEASES = tempfile.mkdtemp()
wifiswitch.LEASE_DIR = LEASES
with open(os.path.join(LEASES, f"internal-{UUID_PLANT}-wlan0.lease"), "w") as fh:
    fh.write("# This is private data. Do not parse.\nADDRESS=192.168.1.61\nNETMASK=255.255.255.0\n")
with open(os.path.join(LEASES, f"dhclient-{UUID_OFFICE}-wlan0.lease"), "w") as fh:
    fh.write("lease {\n  fixed-address 192.168.5.10;\n}\nlease {\n  fixed-address 192.168.5.11;\n}\n")

DEVICES = [{"device": "eth0", "type": "ethernet", "ip": "192.168.1.9"},
           {"device": "docker0", "type": "bridge", "ip": "172.17.0.1"},
           {"device": "eth1", "type": "ethernet", "ip": "169.254.3.3"},
           {"device": "wlan0", "type": "wifi", "ip": "10.42.0.1"}]


def hosts(cands):
    return [c["host"] for c in cands]


# -- 1. to the AP --------------------------------------------------------------
c = wifiswitch.candidates("ap", devices=DEVICES)
check("to the AP: the AP's address first, the cable, the .local name",
      hosts(c) == ["10.42.0.1", "192.168.1.9", "iriv-1234abcd.local"], repr(c))
check("...the AP's and the cable's are the box's own; the .local name is not",
      [x["own"] for x in c] == [True, True, False], repr(c))
check("...never a docker bridge or a 169.254 address",
      "172.17.0.1" not in hosts(c) and "169.254.3.3" not in hosts(c))

# -- 2. to Wi-Fi ---------------------------------------------------------------
wifiswitch._last_ip.clear()
wifiswitch._last_ip["Plant"] = "192.168.1.50"
c = wifiswitch.candidates("wifi", network="Plant", devices=DEVICES)
check("to a saved network: the address it had there, its last lease, the cable, the name",
      hosts(c) == ["192.168.1.50", "192.168.1.61", "192.168.1.9", "iriv-1234abcd.local"], repr(c))
check("...a predicted address is NOT the box's own (the session does not travel there)",
      [x["own"] for x in c[:2]] == [False, False], repr(c))
check("...nothing of the other profile, nothing of the AP",
      not any(h.startswith("192.168.5.") or h == "10.42.0.1" for h in hosts(c)), repr(c))
c = wifiswitch.candidates("wifi", network="Office", devices=[])
check("a profile with a fixed address: that address, as the box's own",
      c[0] == {"host": "192.168.5.40", "why": "the fixed address set on Office", "own": True},
      repr(c))
check("...and dhclient's lease file is read too, the newest block",
      "192.168.5.11" in hosts(c) and "192.168.5.10" not in hosts(c), repr(c))
c = wifiswitch.candidates("wifi", devices=[])
check("any saved network: every saved profile's addresses",
      {"192.168.1.50", "192.168.1.61", "192.168.5.40", "192.168.5.11"} <= set(hosts(c)), repr(c))
c = wifiswitch.candidates("wifi", ssid="Brand New", devices=[])
check("a network never joined: the name alone (no address can be predicted)",
      hosts(c) == ["iriv-1234abcd.local"], repr(c))
check("a lease lookup refuses a uuid that is not one (no path games)",
      wifiswitch._leased("../../etc/passwd") == "" and wifiswitch._leased("*") == "")
check("reading the candidates wrote nothing", not any(s for _, s in calls), repr(calls))

# -- 3. start() hands them out, read before anything moves ---------------------
started = []
wifiswitch.threading = types.SimpleNamespace(Lock=wifiswitch.threading.Lock,
                                             Thread=lambda **k: types.SimpleNamespace(
                                                 start=lambda: started.append(k["name"])))
wifiswitch._panel_note = lambda text: None
CFG = tempfile.mkdtemp()
out = wifiswitch.start("wifi", network="Plant", config_dir=CFG, devices=DEVICES)
check("start() answers with the switch under way and where the box may be",
      out["state"] == "switching" and hosts(out["candidates"])[:2] == ["192.168.1.50", "192.168.1.61"]
      and started == ["wifi-switch"], repr(out))
wifiswitch._state.clear()
wifiswitch._state["state"] = "idle"

# -- 4. the probe endpoint -----------------------------------------------------
# The dev box has no fastapi; the same stand-in smoke_wifiswitch uses, plus
# a JSONResponse that keeps what it was given.
try:
    import fastapi                                            # noqa: F401
    from fastapi.responses import JSONResponse                # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request = _HTTPException, object
    far = types.ModuleType("fastapi.responses")

    class _JSONResponse:
        def __init__(self, content, status_code=200, headers=None):
            self.content, self.status_code, self.headers = content, status_code, dict(headers or {})
    far.JSONResponse = _JSONResponse
    fa.responses = far
    sys.modules["fastapi"] = fa
    sys.modules["fastapi.responses"] = far
from core.routes import network as routes                     # noqa: E402

hostinfo._static["serial"] = "10000000deadbeef"
resp = routes.box_identity()
body = getattr(resp, "content", None)
if body is None:                                              # the real JSONResponse
    import json
    body = json.loads(resp.body)
check("GET /api/health/box: this box's id and the switch state, nothing else",
      set(body) == {"ok", "box", "switch"} and body["box"] == hostinfo.box_id()
      and body["switch"] == "idle", repr(body))
check("...the id is a hash, not the serial, and the same every time",
      "deadbeef" not in body["box"] and len(body["box"]) == 16
      and hostinfo.box_id() == body["box"], body["box"])
hostinfo._static["serial"] = "10000000feedface"
check("...and another box has another id", hostinfo.box_id() != body["box"])
check("...readable cross-origin, never cached",
      resp.headers.get("Access-Control-Allow-Origin") == "*"
      and resp.headers.get("Cache-Control") == "no-store", repr(resp.headers))

# The session wall's own test, lifted out of auth.py (which needs the real
# fastapi to import): OPEN_EXACT, OPEN_PREFIXES, _under and is_open.
import ast                                                    # noqa: E402
tree = ast.parse(open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read())
wall = {}
exec(compile(ast.Module(body=[n for n in tree.body
                              if (isinstance(n, ast.FunctionDef)
                                  and n.name in ("_under", "is_open"))
                              or (isinstance(n, ast.Assign)
                                  and getattr(n.targets[0], "id", "") in ("OPEN_PREFIXES",
                                                                         "OPEN_EXACT"))],
                        type_ignores=[]), "auth.py", "exec"), wall)
check("...open by name (OPEN_EXACT), with /api/health itself (Pipi L1)",
      wall["is_open"]("/api/health/box") and wall["is_open"]("/api/health"))
check("...and nothing else under or beside /api/health is open by default",
      not wall["is_open"]("/api/health/other") and not wall["is_open"]("/api/healthbox")
      and not wall["is_open"]("/api/health/box/x"))

# -- 5. the route --------------------------------------------------------------
rsrc = open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8").read()
check("the switch route hands the devices it read to the switch",
      "devices=devices," in rsrc and '"box_id"' in rsrc)
check("CORS is set on the probe's response alone",
      rsrc.count("Access-Control-Allow-Origin") == 1)

print("\n%s" % ("all Wi-Fi probe checks passed" if not bad else f"{bad} check(s) FAILED"))
sys.exit(1 if bad else 0)
