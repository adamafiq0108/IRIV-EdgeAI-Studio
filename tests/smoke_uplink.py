# -*- coding: utf-8 -*-
"""
smoke_uplink.py -- the box decides which fixed address is the internet uplink,
and a fixed address (or a DHCP one) may name its own DNS servers.

Tok Aba (2026-10-09): "remove 'this port is the internet link', just make it
auto. and then please add option to setup DNS". The checkbox (10-07 to 10-09)
is gone; netuplink.auto_role decides. Pinned against a fake NetworkManager, a
fake `ip route` and a fake internet probe, on any machine:

  1. by role: no gateway is local whatever is passed (never-default yes,
     metric -1); uplink: never-default no at its metric; standby: never-default
     no at STANDBY_METRIC, past every metric NetworkManager numbers itself
  2. metric ordering of an uplink (50, under an earlier uplink, the floor)
  3. auto_role: no gateway = local, never probed; a preset = standby; the
     first default route that reaches the internet decides (this port =
     uplink, another = standby); board .22 both ways round; nothing reaches
     = uplink; a route routeheal demoted does not count
  4. set_ipv4 live: the role in the reply, one modify then up; an older
     page's `uplink` is ignored (no gateway is accepted, not refused)
  5. uplinkguard on a standby port: reached = stays, nothing written, never
     asked; not reached = made local (never-default yes, metric -1, up),
     never a Keep / Revert question
     the hotspot's own profile (method shared) is refused, DHCP or not
  6. DNS: netaddr.dns_servers' rules; static and DHCP nmcli args
     (ignore-auto-dns yes only for a DHCP override); a bad server refused
     before any write
"""

import os
import socket
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netaddr, netconf, routeheal, uplinkguard   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def opt(args, key):
    args = list(args)
    return args[args.index(key) + 1] if key in args else None


routes = []          # "default via ... dev ... metric N" lines


def fake_run(argv, timeout=10):
    if argv[:5] == ["ip", "-4", "route", "show", "default"]:
        return 0, "".join(routes)
    return 127, ""


routeheal.run = fake_run

# Which ports the internet answers through, for auto_role's probe.
works = set()
probed = []
real_reachable = uplinkguard.reachable


def fake_reachable(device, source_ip):
    probed.append(device)
    return device in works


uplinkguard.reachable = fake_reachable

ETH0 = {"device": "eth0", "type": "ethernet", "ip": "192.168.1.116", "prefix": "24",
        "gateway": "192.168.1.1", "dns": ["192.168.1.1"], "connection": "Wired connection 1"}
ETH1 = {"device": "eth1", "type": "ethernet", "ip": "192.168.4.20", "prefix": "24",
        "gateway": "", "dns": [], "connection": "Wired connection 2"}
WLAN = {"device": "wlan0", "type": "wifi", "ip": "192.168.8.30", "prefix": "24",
        "gateway": "192.168.8.1", "dns": [], "connection": "Plant"}
DEVS = [ETH0, ETH1, WLAN]
netconf._ap_subnets = lambda: []


def settings(entry, ip, gateway, role="local", metric=None, dhcp=False, prefix="24", dns=""):
    try:
        return netconf._ipv4_settings(entry, dhcp, ip, prefix, gateway, dns, DEVS,
                                      role=role, metric=metric), ""
    except netconf.NetError as exc:
        return None, str(exc)


# -- 1. by role ------------------------------------------------------------------
out, err = settings(ETH1, "192.168.4.5", "", role="uplink", metric=49)
check("no gateway, even passed as the uplink: local (never-default yes, metric -1)",
      out is not None and opt(out, "ipv4.never-default") == "yes"
      and opt(out, "ipv4.route-metric") == "-1" and opt(out, "ipv4.gateway") == "", out or err)
out, err = settings(ETH0, "192.168.1.50", "192.168.1.1", role="uplink", metric=49)
check("uplink: never-default no at the metric it was given",
      opt(out, "ipv4.never-default") == "no" and opt(out, "ipv4.route-metric") == "49",
      out or err)
out, err = settings(ETH0, "192.168.1.50", "192.168.1.1", role="uplink")
check("...with no metric worked out, UPLINK_METRIC",
      opt(out, "ipv4.route-metric") == str(netconf.UPLINK_METRIC), out or err)
out, err = settings(ETH1, "10.0.0.10", "10.0.0.1", role="standby")
check("standby: never-default no at STANDBY_METRIC",
      opt(out, "ipv4.never-default") == "no"
      and opt(out, "ipv4.route-metric") == str(netconf.STANDBY_METRIC), out or err)
check("...which is past NetworkManager's 100 / 600 / 700 and short of routeheal's 2000",
      700 < netconf.STANDBY_METRIC < routeheal.DEMOTED_METRIC)
out, err = settings(ETH0, "192.168.1.50", "192.168.1.50", role="uplink")
check("the gateway rules still hold: the address being set is refused (board .47)",
      out is None and "own address" in err, err)
out, err = settings(ETH1, "192.168.1.60", "192.168.1.116", role="standby")
check("...another port's address is refused, naming it",
      out is None and "own address on eth0" in err, err)
out, err = settings(ETH0, "192.168.1.50", "192.168.2.1")
check("...a gateway outside the subnet is refused", out is None and "outside" in err, err)
out, err = settings(ETH0, "", "", role="uplink", dhcp=True)
check("DHCP: never-default no and the metric back to the default, whatever the role",
      opt(out, "ipv4.method") == "auto" and opt(out, "ipv4.never-default") == "no"
      and opt(out, "ipv4.route-metric") == "-1", out or err)

# -- 2. an uplink's metric -----------------------------------------------------------
routes[:] = []
check("no other default route: UPLINK_METRIC (50)",
      netconf._uplink_metric("eth0") == 50 == netconf.UPLINK_METRIC)
routes[:] = ["default via 192.168.1.1 dev eth0 proto static metric 50\n",
             "default via 192.168.8.1 dev wlan0 proto dhcp metric 600\n"]
check("an uplink set earlier at 50 on eth0: the one set NOW goes under it (49)",
      netconf._uplink_metric("eth1") == 49)
check("...and the port's own current route does not count against itself",
      netconf._uplink_metric("eth0") == 50)
routes[:] = ["default via 192.168.1.1 dev eth0 proto static metric 21\n",
             "default via 10.0.0.1 dev eth1 proto static metric 1000\n"]
check("never below the floor, and a standby route changes nothing",
      netconf._uplink_metric("wlan0") == 20)

# -- 3. auto_role -----------------------------------------------------------------------
role = netconf._auto_role


def decide(device, gateway, live=True):
    probed.clear()
    return role(device, gateway, live, DEVS)


routes[:] = ["default via 192.168.4.1 dev eth1 proto dhcp metric 100\n"]
works.clear()
works.add("eth1")
check("no gateway: local, and nothing is probed",
      decide("eth0", "") == "local" and not probed, probed)
check("a preset (no cable): standby, nothing probed",
      decide("eth0", "10.0.0.1", live=False) == "standby" and not probed, probed)
check("board .22: eth1 on DHCP reaches the internet, eth0 set to 10.0.0.10 gw 10.0.0.1 "
      "-> standby", decide("eth0", "10.0.0.1") == "standby", probed)
routes[:] = ["default via 10.0.0.1 dev eth0 proto static metric 100\n",
             "default via 192.168.4.1 dev eth1 proto dhcp metric 101\n"]
check("board .22 the other way round: eth0 HOLDS the route but reaches nothing, eth1 "
      "works -> eth0 is standby, not preferred for holding it",
      decide("eth0", "10.0.0.1") == "standby" and probed == ["eth0", "eth1"], probed)
works.add("eth0")
check("the port that carries a WORKING internet now stays the uplink, the others "
      "not even asked", decide("eth0", "192.168.1.1") == "uplink" and probed == ["eth0"],
      probed)
routes[:] = []
works.clear()
check("no default route anywhere (AP-only bench box): uplink",
      decide("eth0", "192.168.1.1") == "uplink")
routes[:] = ["default via 192.168.8.1 dev wlan0 proto dhcp metric 600\n"]
check("another route that reaches nothing: this port is tried as the uplink",
      decide("eth0", "192.168.1.1") == "uplink" and probed == ["wlan0"], probed)
works.add("wlan0")
check("Wi-Fi reaching the internet: a wired port with a gateway is standby behind it",
      decide("eth0", "192.168.1.1") == "standby")
routes[:] = ["default via 192.168.8.1 dev wlan0 proto dhcp metric 2000\n"]
check("a route routeheal demoted does not count, even if it answers",
      decide("eth0", "192.168.1.1") == "uplink" and not probed, probed)

# -- 4. set_ipv4 live ------------------------------------------------------------------
calls = []


def fake_nmcli(args, sudo=False, timeout=45):
    calls.append((tuple(args), sudo))
    return ""


netconf._nmcli = fake_nmcli
netconf.devices = lambda: [dict(d) for d in DEVS]
netconf.status = lambda: {"ok": True}


def writes():
    return [a for a, sudo in calls if sudo]


routes[:] = ["default via 192.168.1.1 dev eth0 proto dhcp metric 100\n"]
works.clear()
works.add("eth0")
calls.clear()
out = netconf.set_ipv4("eth1", dhcp=False, ip="192.168.4.5", prefix="24",
                       gateway="192.168.4.1", via="eth0", uplink=True)
w = writes()
check("live eth1 with a gateway beside a working eth0: ONE modify as standby, then up, "
      "whatever an older page's uplink said",
      out.get("role") == "standby" and len(w) == 2
      and w[0][:3] == ("connection", "modify", "Wired connection 2")
      and opt(w[0], "ipv4.route-metric") == str(netconf.STANDBY_METRIC)
      and opt(w[0], "ipv4.never-default") == "no" and w[1][:2] == ("connection", "up"),
      (out, w))
calls.clear()
probed.clear()
out = netconf.set_ipv4("eth1", dhcp=False, ip="192.168.4.5", prefix="24", via="eth0",
                       uplink=True)
w = writes()
check("no gateway with uplink=True from an older page: accepted as local, never probed",
      out.get("role") == "local" and opt(w[0], "ipv4.never-default") == "yes"
      and opt(w[0], "ipv4.route-metric") == "-1" and not probed, (out, w, probed))
works.clear()
calls.clear()
out = netconf.set_ipv4("eth1", dhcp=False, ip="192.168.4.5", prefix="24",
                       gateway="192.168.4.1", via="eth0")
w = writes()
check("nothing reaches the internet: eth1 is the uplink, ahead of eth0 (metric 50)",
      out.get("role") == "uplink" and opt(w[0], "ipv4.route-metric") == "50"
      and opt(w[0], "ipv4.never-default") == "no", (out, w))

netconf.devices = lambda: [dict(d) for d in DEVS] + [
    {"device": "wlan9", "type": "wifi", "ip": "10.42.0.1", "prefix": "24",
     "connection": "Hotspot", "method": "shared", "dns": []}]
calls.clear()
try:
    netconf.set_ipv4("wlan9", dhcp=True, dns="1.1.1.1", via="eth0")
    check("the hotspot's own profile is refused (a DNS-only Apply would end the AP)", False)
except netconf.NetError as exc:
    check("the hotspot's own profile is refused (a DNS-only Apply would end the AP), "
          "nothing written", not writes() and "access point" in str(exc), str(exc))
netconf.devices = lambda: [dict(d) for d in DEVS]

# -- 5. the guard on a standby port -----------------------------------------------------
uplinkguard.reachable = lambda device, source_ip: device in works
uplinkguard.CHECK_S, uplinkguard.POLL_S = 0.3, 0.05


def wait_state(dev, states, limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        st = uplinkguard.state().get(dev, {})
        if st.get("state") in states:
            return st
        time.sleep(0.02)
    return uplinkguard.state().get(dev, {})


prev = ["ipv4.method", "auto"]
works.clear()
works.add("eth1")
calls.clear()
uplinkguard.arm("eth1", "Wired connection 2", prev, "192.168.4.5", "192.168.4.1",
                role="standby")
st = wait_state("eth1", ("ok", "local", "pending"))
check("standby reached through the port: ok, the fallback, nothing written",
      st.get("state") == "ok" and "fallback" in st.get("message", "") and not writes(),
      (st, writes()))
works.clear()
calls.clear()
uplinkguard.arm("eth1", "Wired connection 2", prev, "10.0.0.10", "10.0.0.1", role="standby")
st = wait_state("eth1", ("ok", "local", "pending", "reverted"), limit=3.0)
w = writes()
check("standby NOT reached: made local, never asked Keep / Revert, never reverted",
      st.get("state") == "local" and "machine-only" in st.get("message", ""), st)
check("...by never-default yes and the metric back to -1 on its profile, then up",
      len(w) == 2 and w[0][:3] == ("connection", "modify", "Wired connection 2")
      and opt(w[0], "ipv4.never-default") == "yes" and opt(w[0], "ipv4.route-metric") == "-1"
      and "ipv4.method" not in w[0] and w[1][:2] == ("connection", "up"), w)
uplinkguard.reachable = real_reachable

# -- 6. DNS ------------------------------------------------------------------------------
ok = netaddr.dns_servers
check("blank is []", ok("") == [] and ok("  ") == [] and ok(None) == [])
check("commas or spaces, repeats dropped",
      ok("8.8.8.8, 1.1.1.1") == ["8.8.8.8", "1.1.1.1"]
      and ok("8.8.8.8 1.1.1.1,8.8.8.8") == ["8.8.8.8", "1.1.1.1"])
for wrong, why in (("8.8.8", "not an IPv4"), ("dns.google", "not an IPv4"),
                   ("8.8.8.08", "not an IPv4"), ("0.0.0.0", "cannot be a DNS"),
                   ("127.0.0.53", "cannot be a DNS"), ("224.0.0.1", "cannot be a DNS"),
                   ("255.255.255.255", "cannot be a DNS"), ("240.0.0.1", "cannot be a DNS"),
                   ("8.8.8.8;reboot", "not an IPv4"), ("-s", "not an IPv4"),
                   ("1.1.1.1 8.8.8.8 9.9.9.9 8.8.4.4", "at most 3")):
    try:
        ok(wrong)
        check(f"{wrong!r} refused", False)
    except netconf.NetError as exc:
        check(f"{wrong!r} refused ({why})", why in str(exc), str(exc))

out, err = settings(ETH0, "192.168.1.50", "192.168.1.1", role="uplink",
                    dns="8.8.8.8 1.1.1.1")
check("static with servers: ipv4.dns as given, joined with ',' (nmcli's list form)",
      opt(out, "ipv4.dns") == "8.8.8.8,1.1.1.1" and opt(out, "ipv4.ignore-auto-dns") == "no",
      out or err)
out, err = settings(ETH0, "192.168.1.50", "192.168.1.1", role="uplink")
check("static, blank: the servers in use now are carried over (automatic)",
      opt(out, "ipv4.dns") == "192.168.1.1", out or err)
out, err = settings(ETH1, "192.168.4.5", "192.168.4.1", role="standby")
check("...none in use: the gateway, as the last resort", opt(out, "ipv4.dns") == "192.168.4.1",
      out or err)
out, err = settings(ETH0, "", "", dhcp=True, dns="1.1.1.1, 8.8.8.8")
check("DHCP with servers: they replace the lease's (ignore-auto-dns yes)",
      opt(out, "ipv4.method") == "auto" and opt(out, "ipv4.dns") == "1.1.1.1,8.8.8.8"
      and opt(out, "ipv4.ignore-auto-dns") == "yes", out or err)
out, err = settings(ETH0, "", "", dhcp=True)
check("DHCP blank: the lease's again (dns cleared, ignore-auto-dns no)",
      opt(out, "ipv4.dns") == "" and opt(out, "ipv4.ignore-auto-dns") == "no", out or err)
out, err = settings(ETH0, "", "", dhcp=True, dns="1.1.1.1 x")
check("a bad server on DHCP is refused too", out is None and "not an IPv4" in err, err)
calls.clear()
probed.clear()
try:
    netconf.set_ipv4("eth0", dhcp=False, ip="192.168.1.50", prefix="24",
                     gateway="192.168.1.1", dns="8.8.8.8, 300.1.1.1", via="wlan0")
    check("a bad DNS server on a live port is refused", False)
except netconf.NetError as exc:
    check("a bad DNS server on a live port is refused before any write or probe",
          not writes() and not probed and "300.1.1.1" in str(exc), (writes(), probed))
check("the revert snapshot carries the DNS mode",
      "ipv4.ignore-auto-dns" in netconf._SNAPSHOT_KEYS
      and netconf._SNAPSHOT_KEYS.index("ipv4.dns") == 3)

print("\n%s" % ("all uplink checks passed" if not bad else f"{bad} check(s) FAILED"))
sys.exit(1 if bad else 0)
