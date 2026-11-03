# -*- coding: utf-8 -*-
"""
smoke_wifiswitch.py -- wlan0 switches between access point and Wi-Fi LIVE.

Tok Aba (2026-10-07): the switch must not reboot the box. Pinned here against
a fake NetworkManager (nmcli and the root Wi-Fi helper replaced by a model of
wlan0), so it runs on any machine:

  1. AP -> Wi-Fi: the hotspot goes down, the chosen saved profile comes up,
     the mode is wifi; "any saved network" is device connect; a NEW network
     goes through the join path (the helper, key on stdin)
  2. a join that fails NEVER brings the access point back (Tok Aba,
     2026-10-14: no automatic hotspot): the reason in plain words (wrong
     password, network not found, no address -- stuck getting a lease, and a
     169.254 fallback), "The hotspot is off. Turn the hotspot on ...", and the
     mode is wifi, so a restart does not raise the hotspot either; the hub
     starts no start-up or Wi-Fi-loss fallback thread, and an old
     [system] wifi_loss_hotspot value is ignored
  3. Wi-Fi -> AP: the client goes down, the hotspot comes up on the stored
     name and key; an AP that will not start puts the old network back
  4. no reboot, shutdown, systemctl or nft anywhere on the path; every root
     verb is one the irivai-box sudoers rule already grants; Ethernet is
     never named
  5. the front panel shows the switch and the new mode at once; the hub's
     mDNS record is renewed; the hotspot isolation table still applies
  6. the route answers at once, a caller on wlan0 gets the reply before the
     radio moves, and other network writes wait while it switches
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                       # noqa: E402
from core.services import (discovery_mdns, hostinfo, netconf, panel,  # noqa: E402
                           wifiswitch)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- nothing on this path may fork anything real ------------------------------
forks = []
_real_run, _real_popen = subprocess.run, subprocess.Popen


def _no_fork(argv, *a, **k):
    forks.append(list(argv) if isinstance(argv, (list, tuple)) else [str(argv)])
    raise FileNotFoundError("forks are not allowed in this test")


subprocess.run = _no_fork
subprocess.Popen = _no_fork
os.system = lambda cmd: forks.append([cmd]) or 1

# -- a model of wlan0 under NetworkManager ------------------------------------
AP = "Hotspot"
box = {}
calls = []          # (argv tuple, sudo)
helper = []         # the helper's requests (the key is checked, never logged)
plan = {}           # profile -> "ok" | "wrong_password" | "not_found" | "no_address" | "linklocal"
saved = ["Plant", "Office"]


def reset_box(ap=True):
    box.clear()
    box.update(conn=AP if ap else "Plant", code=100,
               ip="10.42.0.1" if ap else "192.168.1.50")
    calls.clear()
    helper.clear()
    plan.clear()
    ap_fail.clear()


def _bring_up(name):
    what = plan.get(name, "ok")
    if what == "ok":
        box.update(conn=name, code=100, ip="192.168.1.50")
        return
    if what == "wrong_password":
        box.update(conn="", code=30, ip="")
        raise netconf.NetError("Connection activation failed: (7) Secrets were "
                               "required, but not provided")
    if what == "not_found":
        box.update(conn="", code=30, ip="")
        raise netconf.NetError("Connection activation failed: (53) The Wi-Fi "
                               "network could not be found")
    if what == "no_address":
        # Associated, waiting on a lease nobody hands out: nmcli blocks.
        box.update(conn=name, code=70, ip="")
        time.sleep(3)
        return
    if what == "linklocal":
        box.update(conn=name, code=100, ip="169.254.7.9")
        return


def fake_nmcli(args, sudo=False, timeout=45):
    a = tuple(args)
    calls.append((a, sudo))
    if a[:3] == ("-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS"):
        return ("GENERAL.STATE:%d (state)\nGENERAL.CONNECTION:%s\nIP4.ADDRESS[1]:%s\n"
                % (box["code"], box["conn"] or "--",
                   (box["ip"] + "/24") if box["ip"] else ""))
    if a[:3] == ("-t", "-f", "GENERAL.CONNECTION"):
        return "GENERAL.CONNECTION:%s\n" % box["conn"]
    if a[:3] == ("-t", "-f", "802-11-wireless.mode"):
        return "802-11-wireless.mode:%s\n" % ("ap" if a[-1] == AP else "infrastructure")
    if a[:3] == ("-t", "-f", "NAME,TYPE,ACTIVE,DEVICE"):
        return "".join("%s:802-11-wireless:%s:wlan0\n" % (n, "yes" if box["conn"] == n else "no")
                       for n in saved + [AP])
    if a[:2] == ("connection", "down"):
        if box["conn"] == a[2]:
            box.update(conn="", code=30, ip="")
        return ""
    if a[:2] == ("connection", "up"):
        if a[2] == AP:
            box.update(conn=AP, code=100, ip="10.42.0.1")
            return ""
        _bring_up(a[2])
        return ""
    if a == ("device", "connect", "wlan0"):
        _bring_up(saved[0])
        return ""
    return ""


ap_fail = []


def fake_helper(request):
    helper.append({k: v for k, v in request.items()})
    if request["kind"] == "ap":
        if ap_fail:
            raise netconf.NetError(ap_fail[0])
        box.update(conn=AP, code=100, ip="10.42.0.1")
        return {"ok": True}
    _bring_up(request["ssid"])      # kind join: same story as a saved profile
    return {"ok": True}


netconf._nmcli = fake_nmcli
netconf._wifi_profile = fake_helper
netconf.wifi_helper_installed = lambda: True
netconf.status = lambda: {"ok": True}

CFG = tempfile.mkdtemp()
config.save_local("network", {"ap": {"mode": "ap", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)

# The front panel, the facts it draws, and the mDNS publisher, as recorders.
wakes = []
notes_seen = []


class FakePanel:
    class _W:
        def set(self):
            wakes.append(panel.wlan_note)
    _wake = _W()


panel._panel = FakePanel()
forced = []
hostinfo.snapshot = lambda force=False: forced.append(force) or {"ip_wlan0": box.get("ip", "")}


class FakeProc:
    terminated = 0

    def poll(self):
        return None

    def terminate(self):
        FakeProc.terminated += 1


pub = type("Pub", (), {})()
pub._proc = FakeProc()
pub.learn_due = False
pub.own_host = ""
discovery_mdns._publisher = pub

wifiswitch.JOIN_BUDGET_S = 1.0
wifiswitch.AP_BUDGET_S = 1.0
wifiswitch.POLL_S = 0.05
wifiswitch.START_DELAY_S = 0.3


def wait():
    for _ in range(300):
        if not wifiswitch.busy():
            break
        time.sleep(0.05)
    for t in [t for t in threading.enumerate() if t.name == "wifi-switch"]:
        t.join(10)
    return wifiswitch.state()


def writes():
    return [a for a, sudo in calls if sudo]


def mode():
    return netconf.wifi_mode(CFG)


# -- 1. AP -> Wi-Fi, a saved network ------------------------------------------
reset_box(ap=True)
first = wifiswitch.start("wifi", network="Plant", config_dir=CFG)
check("the switch answers at once, under way", first["state"] == "switching"
      and first["to"] == "wifi" and first["budget_s"] <= 45, repr(first))
check("...and the panel says so while it moves", panel.wlan_note == "Switching WiFi...")
done = wait()
check("AP -> Wi-Fi: joined, with the address in the outcome",
      done["state"] == "done" and done["address"] == "192.168.1.50"
      and done["network"] == "Plant", repr(done))
w = writes()
check("...the hotspot went down FIRST, then the chosen profile came up",
      w[:2] == [("connection", "down", AP), ("connection", "up", "Plant")], repr(w))
check("...and the mode is written once it worked", mode() == "wifi", mode())
check("the panel note is cleared and the panel woken after",
      panel.wlan_note == "" and "" in wakes and "Switching WiFi..." in wakes, repr(wakes))
check("...the panel's facts re-read at once (not at their 2 s cadence)", True in forced)
check("the hub's mDNS record is renewed (publisher child restarted, name re-learnt)",
      FakeProc.terminated >= 1 and pub.learn_due is True)

# The OLED's Wi-Fi page now tells the truth about wlan0.


class Rec:
    def __init__(self):
        self.texts = []

    def clear(self, *a):
        pass

    def line(self, *a):
        pass

    def text(self, x, y, s, size=1):
        self.texts.append(str(s))


rec = Rec()
facts = panel._facts(CFG)
panel.render("wifi", facts, panel.settings(CFG), rec)
check("the panel's Wi-Fi page shows the client and its address, not the AP",
      facts["wlan_mode"] == "wifi" and "wlan0: 192.168.1.50" in rec.texts
      and not any("Connect WiFi AP" in t or "Line AP" in t for t in rec.texts), repr(rec.texts))

# -- "any saved network" and a NEW network ------------------------------------
config.save_local("network", {"ap": {"mode": "ap", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=True)
wifiswitch.start("wifi", config_dir=CFG)
done = wait()
check("no network named: NetworkManager picks a saved one (device connect wlan0)",
      done["state"] == "done" and ("device", "connect", "wlan0") in writes(), repr(done))

config.save_local("network", {"ap": {"mode": "ap", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=True)
wifiswitch.start("wifi", ssid="Guest Net", password="guestpass1", config_dir=CFG)
done = wait()
check("a NEW network goes through the helper, key on stdin, never an argv",
      done["state"] == "done" and helper and helper[0]["kind"] == "join"
      and helper[0]["ssid"] == "Guest Net"
      and not any("guestpass1" in " ".join(a) for a, _ in calls), repr(helper))

# -- 2. a join that fails: the hotspot stays OFF, the reason said ---------------
HOTSPOT_OFF = "The hotspot is off. Turn the hotspot on to reach this device without Wi-Fi."
for what, want, words in (
        ("wrong_password", "wrong_password", "the password was not accepted"),
        ("not_found", "not_found", "the network was not found"),
        ("no_address", "no_address", "no address"),
        ("linklocal", "no_address", "no address")):
    config.save_local("network", {"ap": {"mode": "ap", "password": "boxkey2345",
                                         "ssid": "Line AP"}}, CFG)
    reset_box(ap=True)
    plan["Plant"] = what
    started = time.monotonic()
    wifiswitch.start("wifi", network="Plant", config_dir=CFG)
    done = wait()
    took = time.monotonic() - started
    check(f"{what}: the switch fails as {want}, in words",
          done["state"] == "failed" and done["reason"] == want
          and words in done["message"]
          and done["message"].startswith("Could not connect to Plant: "), repr(done))
    check(f"...the hotspot is NOT raised again ({what})",
          box["conn"] != AP and not [h for h in helper if h["kind"] == "ap"]
          and ("connection", "up", AP) not in writes()
          and done.get("ap_back") is False and done.get("hotspot_off") is True,
          repr((box, helper, writes())))
    check(f"...and the page is told so plainly ({what})",
          done["message"].endswith(HOTSPOT_OFF) and "is back" not in done["message"],
          done["message"])
    check(f"...the mode is wifi, so a restart does not raise the hotspot either ({what})",
          mode() == "wifi")
    check(f"...inside the budget, not after it ({what}, {took:.1f}s)", took < 3.5)
    time.sleep(0.3)
    check(f"...and nothing raises it a moment later ({what})",
          box["conn"] != AP and not [h for h in helper if h["kind"] == "ap"])

# A NEW network that fails through the old join path: the same.
config.save_local("network", {"ap": {"mode": "ap", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=True)
plan["Guest Net"] = "wrong_password"
wifiswitch.start("wifi", ssid="Guest Net", password="guestpass1", config_dir=CFG)
done = wait()
check("a NEW network that fails: no hotspot, the plain message, mode wifi",
      done["state"] == "failed" and box["conn"] != AP and mode() == "wifi"
      and [h["kind"] for h in helper] == ["join"]
      and done["message"] == ("Could not connect to Guest Net: the password was not "
                              "accepted. " + HOTSPOT_OFF), repr((done, helper)))

# -- 2b. no fallback anywhere: modules, hub start, the old setting ---------------
for gone in ("wififallback.py", "wifiloss.py"):
    check(f"core/services/{gone} is gone",
          not os.path.exists(os.path.join(ROOT, "core", "services", gone)))
hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
check("the hub starts no start-up or Wi-Fi-loss fallback thread",
      not any(w in hub for w in ("wififallback", "wifiloss", "_wfb", "boot_mode"))
      and "_netconf.apply_wifi_mode(" in hub and "_netconf.wifi_mode(config_dir)" in hub)
moves = "".join(open(os.path.join(ROOT, "core", "services", n), encoding="utf-8").read()
                for n in ("wifiswitch.py", "wifimove.py"))
check("the switch and its moves never raise the hotspot after a failed join",
      moves.count("_restore_ap(") == 1,
      "only its definition may remain (Test connection's put-back, wifitest.py)")
from core.services import sysconfig                           # noqa: E402
config.save_local("system", {"system": {"wifi_loss_hotspot": True, "email": False}}, CFG)
try:
    got_settings = sysconfig.settings(CFG)
    check("an old [system] wifi_loss_hotspot is ignored, not an error",
          "wifi_loss_hotspot" not in got_settings and got_settings["email"] is False
          and "wifi_loss_hotspot" not in sysconfig.DEFAULTS
          and not hasattr(sysconfig, "set_wifi_loss_hotspot"), repr(got_settings))
except Exception as exc:                                      # noqa: BLE001
    check("an old [system] wifi_loss_hotspot is ignored, not an error", False, repr(exc))
authsrc = open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
check("/api/system/settings no longer writes it",
      '"wifi_loss_hotspot" in body' not in authsrc and "set_wifi_loss_hotspot" not in authsrc)
check("the panel has no fallback line", not hasattr(panel, "wlan_fallback")
      and "wlan_fallback" not in panel._facts(CFG))

# -- 3. Wi-Fi -> AP ------------------------------------------------------------
config.save_local("network", {"ap": {"mode": "wifi", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=False)
wifiswitch.start("ap", config_dir=CFG)
done = wait()
check("Wi-Fi -> AP: the client went down first, the hotspot came up on the stored name and key",
      done["state"] == "done" and done["address"] == "10.42.0.1"
      and writes()[:1] == [("connection", "down", "Plant")]
      and helper == [{"kind": "ap", "ssid": "Line AP", "psk": "boxkey2345"}], repr((done, calls)))
check("...and the mode is ap once it broadcasts", mode() == "ap")
rec = Rec()
panel.render("wifi", panel._facts(CFG), panel.settings(CFG), rec)
check("the panel's Wi-Fi page is the access point again, name and key",
      "Line AP" in rec.texts and any("boxkey2345" in t for t in rec.texts), repr(rec.texts))

config.save_local("network", {"ap": {"mode": "wifi", "ssid": "Line AP"}}, CFG)
reset_box(ap=False)
wifiswitch.start("ap", config_dir=CFG)
wait()
check("a box with no stored key raises the AP on the default iriv1234",
      helper and helper[-1]["psk"] == netconf.DEFAULT_AP_PASSWORD, repr(helper))

config.save_local("network", {"ap": {"mode": "wifi", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=False)
ap_fail.append("the Wi-Fi helper gave no answer")
wifiswitch.start("ap", config_dir=CFG)
done = wait()
check("an AP that will not start: the old network comes back, mode stays wifi",
      done["state"] == "failed" and box["conn"] == "Plant" and "back on Plant" in done["message"]
      and mode() == "wifi", repr((done, box)))

# -- nothing to do, and what is refused before anything moves -----------------
reset_box(ap=True)
out = wifiswitch.start("ap", config_dir=CFG)
check("already the AP: done at once, nothing written",
      out["state"] == "done" and not writes() and mode() == "ap", repr(out))
reset_box(ap=True)
for kwargs, words in (({"network": "Nowhere"}, "no saved network called"),):
    try:
        wifiswitch.start("wifi", config_dir=CFG, **kwargs)
        check("an unknown saved network is refused", False)
    except netconf.NetError as exc:
        check("an unknown saved network is refused, nothing moved",
              words in str(exc) and not writes(), str(exc))
keep = list(saved)
saved.clear()
try:
    wifiswitch.start("wifi", config_dir=CFG)
    check("no saved network and none typed is refused", False)
except netconf.NetError as exc:
    check("no saved network and none typed is refused, in words",
          "type the name and password" in str(exc) and not writes(), str(exc))
saved.extend(keep)

# -- 4. no reboot, granted verbs only, Ethernet untouched ----------------------
GRANTED = (("connection", "up"), ("connection", "down"), ("device", "connect"))
# The switch and its two moves (wifimove.py, lifted out of wifiswitch.py).
src = "".join(open(os.path.join(ROOT, "core", "services", name), encoding="utf-8").read()
              for name in ("wifiswitch.py", "wifimove.py"))
check("nothing on the path forked a real process (no reboot, systemctl, nft, avahi)",
      not forks, repr(forks))
check("...and the module names none of them",
      not any(w in src for w in ('"reboot', "reboot now", "shutdown", "systemctl",
                                 "nft -f", "boxpending", "panel/power")))
all_sudo = []
for scenario in ("Plant", None):
    reset_box(ap=True)
    plan["Plant"] = "wrong_password"
    wifiswitch.start("wifi", network=scenario or "", config_dir=CFG)
    wait()
    all_sudo += writes()
check("every root step is a verb irivai-box already grants (no new sudoers rule)",
      all(a[:2] in GRANTED for a in all_sudo)
      and all(a[2] == "wlan0" for a in all_sudo if a[:2] == ("device", "connect")),
      repr(all_sudo))
check("Ethernet is never named on the way", not any("eth" in " ".join(a) for a, _ in calls))

# -- 5. the hotspot isolation still applies to an AP raised live ---------------
nft = open(os.path.join(ROOT, "scripts", "nftables", "ap-isolate.nft"), encoding="utf-8").read()
inst = open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8").read()
check("ap-isolate matches by interface NAME, so a live AP on wlan0 is covered",
      'iifname "wlan0" oifname != "wlan0" drop' in nft)
check("...from a table loaded at boot before NetworkManager, not per AP start",
      "Before=network-pre.target NetworkManager.service" in inst
      and "ExecStart=/usr/sbin/nft -f /usr/local/lib/irivai/ap-isolate.nft" in inst)
check("...and the live AP is raised through the same path as the boot's (ap_enable)",
      "netconf.ap_enable(ap_ssid, ap_key)" in src)

# -- 6. the route --------------------------------------------------------------
# The dev box has no fastapi; the same stand-in smoke_apsave uses.
try:
    import fastapi                                            # noqa: F401
except ImportError:
    import types
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f))

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request = _HTTPException, object
    sys.modules["fastapi"] = fa
from core.routes import network as routes                     # noqa: E402
from fastapi import HTTPException                             # noqa: E402


class _S:
    pass


class Req:
    def __init__(self, body, peer):
        self._b = body
        self.app = type("A", (), {"state": _S()})()
        self.app.state.config_dir = CFG
        self.state = _S()
        self.state.user = {"username": "admin"}
        self.client = type("C", (), {"host": peer})()

    async def json(self):
        return self._b


routes._require_admin = lambda request: None
netconf.devices = lambda: [{"device": "eth0", "type": "ethernet", "ip": "192.168.1.9"},
                           {"device": "wlan0", "type": "wifi", "ip": "10.42.0.1"}]
config.save_local("network", {"ap": {"mode": "ap", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=True)
got = {}
real_start = wifiswitch.start


def spy_start(*a, **k):
    got.update(k, mode=a[0])
    return real_start(*a, **k)


wifiswitch.start = spy_start
answer = asyncio.run(routes.network_ap(Req({"on": False, "network": "Plant"}, "10.42.0.23")))
check("POST network/ap {on:false} starts the live switch, answering with it under way",
      answer["switch"]["state"] == "switching" and got.get("mode") == "wifi"
      and got.get("network") == "Plant", repr((answer.get("switch"), got)))
check("...the caller on the AP is marked, so the radio waits for the reply",
      got.get("via") == "wlan0" and not writes(), repr(writes()))
check("...the page is told where the box will be (name.local:port, the AP's address)",
      answer["box_url"].endswith(":%d" % __import__("core.services.hubport",
                                                    fromlist=["port"]).port())
      and answer["switch"]["ap_address"] == "10.42.0.1", answer.get("box_url"))
try:
    asyncio.run(routes.network_forget(Req({"name": "Office"}, "192.168.1.20")))
    check("another network write while it switches waits", False)
except HTTPException as exc:
    check("another network write while it switches waits, in words",
          exc.status_code == 400 and "switching" in exc.detail, exc.detail)
done = wait()
check("...and the switch completes behind the reply", done["state"] == "done", repr(done))
wifiswitch.start = real_start

# Reset Settings still brings the hotspot back by hand, from a box left on
# no network by a failed join: the setup name, the default key, mode ap.
config.save_local("network", {"ap": {"mode": "wifi", "password": "boxkey2345",
                                     "ssid": "Line AP"}}, CFG)
reset_box(ap=False)
box.update(conn="", code=30, ip="")
# The route's netconf calls read the DEFAULT config dir (the hub's, on a box).
_default_cfg = config._CONFIG_DIR
config._CONFIG_DIR = __import__("pathlib").Path(CFG)
try:
    asyncio.run(routes.network_reset(Req({}, "192.168.1.20")))
finally:
    config._CONFIG_DIR = _default_cfg
check("Reset Settings (by hand) raises the setup hotspot on the default key, mode ap",
      box["conn"] == AP and helper and helper[-1]["kind"] == "ap"
      and helper[-1]["psk"] == netconf.DEFAULT_AP_PASSWORD == "iriv1234"
      and netconf.wifi_mode(CFG) == "ap", repr((box, helper)))
rsrc = open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8").read()
check("the route no longer stages a mode for a reboot",
      "pending_reboot" not in rsrc and "_net(wifiswitch.start, " in rsrc)

subprocess.run, subprocess.Popen = _real_run, _real_popen
print("\n%s" % ("all Wi-Fi switch checks passed" if not bad else f"{bad} check(s) FAILED"))
sys.exit(1 if bad else 0)
