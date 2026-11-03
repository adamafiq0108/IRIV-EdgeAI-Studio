# A wired jack with no cable can be given its address before the cable goes in.
#
# Beta feedback 5c: with the box in access-point mode (wlan0 broadcasting,
# nothing on the jacks), eth0 and eth1 showed "No connection profile ·
# unavailable" and the card locked with "connect a cable or join Wi-Fi before
# changing IPv4". netconf.set_ipv4 refused any interface without an ACTIVE
# profile, and an unplugged jack never has one -- so an installer on the bench
# could not set the address the panel's network expects.
#
# Now: an ethernet interface with nothing active is PRESET. A profile bound to
# that interface is modified (or added), autoconnect on, and NOT brought up --
# nothing is live, so no reboot. Pinned here:
#   1. the nmcli commands for a jack with no profile, and with a saved one
#   2. nothing is ever written to wlan0, the AP profile, or the interface the
#      request came in on; a preset is never `connection up`
#   3. validation is as strict as before, and refuses before any write
#   4. an address inside the AP's own subnet is refused (it would cut the AP
#      clients off the moment the cable went in)
#   5. never-default follows the gateway: a preset with one is standby
#      (netuplink.auto_role; no cable, nothing to ask), with none local
#      (board .22: a gateway typed on a machine-only port took the internet)
#   6. the live path is unchanged, and its "Later" reboot note is per boot,
#      cleared again when the interface goes back to DHCP
#   7. (review aea4d04) an AP only STAGED for the next boot is guarded too,
#      and host addresses that cannot work are refused in any AP state
#
#   py tests/smoke_ethpreset.py
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netconf                           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- a box in AP mode: wlan0 broadcasting, eth0 on the bench LAN, eth1 bare ----
box = {
    "status": [("eth0", "ethernet", "connected", "Wired connection 1"),
               ("eth1", "ethernet", "unavailable", ""),
               ("eth2", "ethernet", "unmanaged", ""),
               ("wlan0", "wifi", "connected", "Hotspot"),
               ("lo", "loopback", "connected (externally)", "lo")],
    "addr": {"eth0": ("192.168.1.116/24", "192.168.1.1", ["8.8.8.8"]),
             "wlan0": ("10.42.0.1/24", "", [])},
    # saved profiles: NAME, UUID, TYPE, interface-name
    "profiles": [("Wired connection 1", "u-eth0", "802-3-ethernet", "eth0"),
                 ("Hotspot", "u-ap", "802-11-wireless", "wlan0")],
    "ipv4": {},              # uuid or name -> (method, addresses, gateway, dns)
}
calls = []


def fake_nmcli(args, sudo=False, timeout=45):
    a = list(args)
    calls.append((tuple(a), sudo))
    if a[-2:] == ["device", "status"]:
        return "\n".join(":".join(row) for row in box["status"]) + "\n"
    if a[:3] == ["-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS"]:
        got = box["addr"].get(a[-1])
        if not got:
            return ""
        lines = ["IP4.ADDRESS[1]:" + got[0], "IP4.GATEWAY:" + got[1]]
        lines += ["IP4.DNS[%d]:%s" % (i + 1, d) for i, d in enumerate(got[2])]
        return "\n".join(lines) + "\n"
    if a[:3] == ["-t", "-f", "NAME,UUID,TYPE"]:
        return "\n".join(":".join(p[:3]) for p in box["profiles"]) + "\n"
    if a[:3] == ["-t", "-f", "connection.interface-name"]:
        uid = a[-1]
        p = next((p for p in box["profiles"] if p[1] == uid), None)
        return "connection.interface-name:%s\n" % (p[3] if p else "")
    if a[:2] == ["-t", "-f"] and a[2].startswith("ipv4.method,ipv4.addresses,"):
        m = box["ipv4"].get(a[-1], ("auto", "", "", ""))
        return ("ipv4.method:%s\nipv4.addresses:%s\nipv4.gateway:%s\nipv4.dns:%s\n"
                % m)
    if a[:3] == ["-t", "-f", "GENERAL.CONNECTION"]:
        return "GENERAL.CONNECTION:Hotspot\n"
    if a[:3] == ["-t", "-f", "802-11-wireless.mode"]:
        return "802-11-wireless.mode:ap\n"
    return ""


netconf._nmcli = fake_nmcli
netconf.status = lambda: {"ok": True}


def writes():
    return [a for a, sudo in calls if sudo]


def opt(args, key):
    args = list(args)
    return args[args.index(key) + 1] if key in args else None


# -- 1. the page's view of an unplugged jack -------------------------------------
devs = {d["device"]: d for d in netconf.devices()}
check("eth1 is listed with no active connection and no saved profile",
      devs["eth1"]["connection"] == "" and "profile" not in devs["eth1"], devs["eth1"])

# -- 2. preset a fixed address on eth1, no profile yet ---------------------------
calls.clear()
out = netconf.set_ipv4("eth1", dhcp=False, ip="192.168.50.10",
                       prefix="255.255.255.0", gateway="192.168.50.1",
                       dns="1.1.1.1", via="eth0", uplink=True)
w = writes()
check("one write, and it is an ADD of a wired profile", len(w) == 1
      and w[0][:4] == ("connection", "add", "type", "ethernet"), w)
add = w[0]
check("...bound to eth1 by interface name", opt(add, "ifname") == "eth1", add)
check("...named for the jack", opt(add, "con-name") == "Wired eth1", add)
check("...autoconnect on, at a priority over the default 0",
      opt(add, "connection.autoconnect") == "yes"
      and int(opt(add, "connection.autoconnect-priority")) > 0, add)
check("...method manual, mask translated, gateway and DNS as given",
      opt(add, "ipv4.method") == "manual"
      and opt(add, "ipv4.addresses") == "192.168.50.10/24"
      and opt(add, "ipv4.gateway") == "192.168.50.1"
      and opt(add, "ipv4.dns") == "1.1.1.1", add)
check("...a gateway on a preset may carry a default route, as standby behind every port",
      opt(add, "ipv4.never-default") == "no"
      and opt(add, "ipv4.route-metric") == str(netconf.STANDBY_METRIC), add)
check("nothing is brought up, down or connected -- the change is not live",
      not [a for a in w if a[:2] in (("connection", "up"), ("connection", "down"))
           or a[:1] == ("device",)], w)
check("the reply says the change is deferred", out.get("deferred") is True, out)
touched = " ".join(" ".join(a) for a in w)
check("no write names wlan0, the AP profile, or eth0 (the interface carrying the page)",
      "wlan0" not in touched and "Hotspot" not in touched and "eth0" not in touched
      and "Wired connection 1" not in touched, touched)

# no gateway: a camera LAN with no way out
calls.clear()
netconf.set_ipv4("eth1", dhcp=False, ip="192.168.50.10", prefix="24", via="eth0")
add = writes()[0]
check("no gateway = never-default yes, and empty values are left out of the ADD",
      opt(add, "ipv4.never-default") == "yes" and "ipv4.gateway" not in add
      and "" not in add, add)

# a gateway on a preset: board .22's Modbus port, typed with 10.0.0.1
calls.clear()
netconf.set_ipv4("eth1", dhcp=False, ip="10.0.0.10", prefix="24",
                 gateway="10.0.0.1", via="eth0", uplink=False)
add = writes()[0]
check("a preset with a gateway is saved as standby whatever an older page sends",
      opt(add, "ipv4.gateway") == "10.0.0.1"
      and opt(add, "ipv4.route-metric") == str(netconf.STANDBY_METRIC), add)
check("...a metric no DHCP port, Wi-Fi or modem is under, so it cannot take the internet",
      netconf.STANDBY_METRIC > 700)

# DHCP preset
calls.clear()
netconf.set_ipv4("eth1", dhcp=True, via="eth0")
add = writes()[0]
check("a DHCP preset adds an auto profile, never-default no, no empty values",
      add[:2] == ("connection", "add") and opt(add, "ipv4.method") == "auto"
      and opt(add, "ipv4.never-default") == "no" and "" not in add, add)

# -- 3. a saved profile already bound to eth1 is changed, not duplicated --------
box["profiles"].append(("Wired eth1", "u-eth1", "802-3-ethernet", "eth1"))
box["profiles"].append(("Old site", "u-old", "802-3-ethernet", "eth1"))
box["ipv4"]["u-eth1"] = ("manual", "192.168.50.10/24", "192.168.50.1", "1.1.1.1")
calls.clear()
netconf.set_ipv4("eth1", dhcp=False, ip="192.168.50.20", prefix="24",
                 gateway="192.168.50.1", via="eth0")
w = writes()
check("with a saved profile bound to eth1, OURS is modified by uuid",
      len(w) == 1 and w[0][:4] == ("connection", "modify", "uuid", "u-eth1"), w)
check("...keeping autoconnect and the priority, never brought up",
      opt(w[0], "connection.autoconnect") == "yes"
      and opt(w[0], "connection.autoconnect-priority") == str(netconf.PRESET_PRIORITY)
      and not [a for a in w if a[:2] == ("connection", "up")], w)
devs = {d["device"]: d for d in netconf.devices()}
check("the page sees the saved profile and its address on the unplugged jack",
      devs["eth1"].get("profile") == "Wired eth1"
      and devs["eth1"].get("static_ip") == "192.168.50.10"
      and devs["eth1"]["dhcp"] is False, devs["eth1"])
box["profiles"] = [p for p in box["profiles"] if p[1] != "u-eth1"]
calls.clear()
netconf.set_ipv4("eth1", dhcp=True, via="eth0")
check("...and with only someone else's profile bound, that one is changed",
      writes()[0][:4] == ("connection", "modify", "uuid", "u-old"), writes())
check("going back to DHCP clears the address and puts never-default back to no",
      opt(writes()[0], "ipv4.addresses") == ""
      and opt(writes()[0], "ipv4.never-default") == "no", writes())
box["profiles"] = [p for p in box["profiles"] if p[1] != "u-old"]

# -- 4. refused before anything is written -----------------------------------------
for label, kwargs in [
        ("an address that is not one", {"ip": "192.168.50.300"}),
        ("no address", {"ip": ""}),
        ("a mask that is not one", {"ip": "192.168.50.10", "prefix": "255.0.255.0"}),
        ("a gateway that is not one", {"ip": "192.168.50.10", "gateway": "router"}),
        ("a DNS server that is not one", {"ip": "192.168.50.10", "dns": "1.1.1.1 x"}),
        ("an address inside the access point's own subnet",
         {"ip": "10.42.0.50", "prefix": "24"}),
        ("a wide mask that swallows the AP subnet",
         {"ip": "10.0.0.5", "prefix": "255.0.0.0"})]:
    calls.clear()
    try:
        netconf.set_ipv4("eth1", dhcp=False, via="eth0",
                         **{"prefix": "24", **kwargs})
        check(f"preset: {label} is refused", False, "it was written")
    except netconf.NetError as exc:
        check(f"preset: {label} is refused, nothing written", not writes(),
              (str(exc), writes()))
calls.clear()
netconf.set_ipv4("eth1", dhcp=False, ip="10.42.1.5", prefix="24", via="eth0")
check("...but the next subnet over is fine", len(writes()) == 1, writes())

for label, device, via in [("wlan0 (not ethernet) with nothing joined", "wlan0", ""),
                           ("the interface the page arrived on", "eth1", "eth1"),
                           ("an interface NetworkManager does not manage", "eth2", "")]:
    saved = box["status"]
    if device == "wlan0":
        box["status"] = [r if r[0] != "wlan0" else ("wlan0", "wifi", "disconnected", "")
                         for r in saved]
    calls.clear()
    try:
        netconf.set_ipv4(device, dhcp=False, ip="192.168.60.5", prefix="24", via=via)
        check(f"preset refused for {label}", False, writes())
    except netconf.NetError:
        check(f"preset refused for {label}, nothing written", not writes(), writes())
    box["status"] = saved

# -- 5. the live path is what it was ---------------------------------------------------
calls.clear()
out = netconf.set_ipv4("eth0", dhcp=False, ip="192.168.1.65", prefix="24",
                       gateway="192.168.1.1", via="eth0")
w = writes()
check("a connected interface: its active profile is modified by name, then brought up",
      w[0][:3] == ("connection", "modify", "Wired connection 1")
      and w[1] == ("connection", "up", "Wired connection 1"), w)
check("...and the reply says it is live", out.get("deferred") is False, out)
check("...resolvers carried over from the lease as before",
      opt(w[0], "ipv4.dns") == "8.8.8.8", w)

# -- 6. the Test button on a jack with no cable ------------------------------------------
answer = netconf.address_in_use("192.168.50.10", "eth1")
check("testing an address from an unplugged jack says it could not, not 'free'",
      answer["checked"] is False and "not connected" in answer["detail"], answer)

# -- 7. "Later": the reboot note lasts until the next boot --------------------------------
cfg = tempfile.mkdtemp()
netconf._boot_id = lambda: "boot-A"
check("nothing pending on a fresh box", netconf.ipv4_reboot_pending(cfg) == [])
netconf.mark_ipv4_reboot("eth0", cfg)
netconf.mark_ipv4_reboot("eth0", cfg)
check("a live address change is noted once", netconf.ipv4_reboot_pending(cfg) == ["eth0"],
      netconf.ipv4_reboot_pending(cfg))
netconf._boot_id = lambda: "boot-B"
check("...and is gone after the reboot, with no clean-up step",
      netconf.ipv4_reboot_pending(cfg) == [])
netconf.set_wifi_mode("wifi", config_dir=cfg)
check("the note shares network.local.toml without disturbing the wlan0 mode",
      netconf.wifi_mode(cfg) == "wifi")

route = read("core", "routes", "network.py")
netconf._boot_id = lambda: "boot-C"
netconf.mark_ipv4_reboot("eth0", cfg)
netconf.mark_ipv4_reboot("eth1", cfg)
netconf.clear_ipv4_reboot("eth0", cfg)
check("back on DHCP before the reboot: that interface's note is cleared, the other kept",
      netconf.ipv4_reboot_pending(cfg) == ["eth1"], netconf.ipv4_reboot_pending(cfg))
check("...and clearing one that was never noted changes nothing",
      netconf.clear_ipv4_reboot("eth9", cfg) == ["eth1"]
      and netconf.wifi_mode(cfg) == "wifi")

# -- 8. the AP staged for the next boot is guarded as well as a live one -------------
# Review N1: with wlan0 still a Wi-Fi client and AP mode staged, there is no
# live AP address, and 10.42.0.20/24 used to pass -- after the reboot the
# hotspot comes up on 10.42.0.0/24 and the cable's route wins.
live_ap = netconf.ap_active
netconf.ap_active = lambda: ""
for staged, want_refused in (("ap", True), ("wifi", False)):
    netconf.wifi_mode = lambda config_dir=None, m=staged: m
    calls.clear()
    try:
        netconf.set_ipv4("eth1", dhcp=False, ip="10.42.0.20", prefix="24", via="eth0")
        refused = False
    except netconf.NetError:
        refused = True
    check(f"AP not broadcasting, mode {staged!r}: 10.42.0.20/24 "
          + ("refused (the hotspot comes back on it)" if want_refused else "allowed"),
          refused == want_refused and (bool(writes()) != refused), writes())
netconf.ap_active = live_ap

# -- 9. addresses that cannot work as a host, in any AP state -----------------------
netconf.wifi_mode = lambda config_dir=None: "wifi"
netconf.ap_active = lambda: ""
for label, kwargs in [
        ("the network address as the box's own", {"ip": "192.168.50.0"}),
        ("the broadcast address as the box's own", {"ip": "192.168.50.255"}),
        ("a gateway outside the subnet", {"ip": "192.168.50.10", "gateway": "192.168.51.1"}),
        ("a gateway that is the box itself", {"ip": "192.168.50.10", "gateway": "192.168.50.10"}),
        ("a gateway that is the broadcast address",
         {"ip": "192.168.50.10", "gateway": "192.168.50.255"}),
        ("prefix 0", {"ip": "192.168.50.10", "prefix": "0"}),
        ("mask 0.0.0.0", {"ip": "192.168.50.10", "prefix": "0.0.0.0"}),
        ("anything wider than /8", {"ip": "10.1.2.3", "prefix": "254.0.0.0"})]:
    for dev in ("eth1", "eth0"):           # preset and live alike
        calls.clear()
        try:
            netconf.set_ipv4(dev, dhcp=False, via="eth0" if dev == "eth1" else "",
                             **{"prefix": "24", **kwargs})
            check(f"{dev}: {label} is refused", False, writes())
        except netconf.NetError as exc:
            check(f"{dev}: {label} is refused, nothing written", not writes(),
                  (str(exc), writes()))
for label, kwargs in [("a /8 is still a network", {"ip": "10.1.2.3", "prefix": "8",
                                                   "gateway": "10.0.0.1"}),
                      ("a /30 host with its gateway", {"ip": "192.168.50.6",
                                                      "prefix": "255.255.255.252",
                                                      "gateway": "192.168.50.5"})]:
    calls.clear()
    netconf.set_ipv4("eth1", dhcp=False, via="eth0", **kwargs)
    check(f"...but {label}", len(writes()) == 1, writes())

check("the route works out `via` before writing and hands it to set_ipv4",
      # netconf.devices() runs on a worker thread since perf item L5
      ('via = _via(request, {"devices": netconf.devices()})["via"]' in route
       or 'via = _via(request, {"devices": await asyncio.to_thread(netconf.devices)})["via"]'
       in route
       # 2026-10-09: the list is kept for the conflict check as well
       or ('devs = await asyncio.to_thread(netconf.devices)' in route
           and 'via = _via(request, {"devices": devs})["via"]' in route))
      and ("via=via)" in route or "via=via," in route))
check("...notes a reboot for a LIVE fixed address, clears it for live DHCP, reports it",
      'if not payload.get("deferred"):' in route
      and ("netconf.mark_ipv4_reboot(" in route or "netconf.mark_ipv4_reboot, " in route)
      and ("netconf.clear_ipv4_reboot(" in route or "netconf.clear_ipv4_reboot, " in route)
      and 'payload["ipv4_reboot"] = netconf.ipv4_reboot_pending()' in route)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
