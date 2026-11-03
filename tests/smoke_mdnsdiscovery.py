# Two IRIV boxes on one cable find each other (discovery across link-local).
#
# 65a41a81 made a wired port with no DHCP server fall back to 169.254, the way
# a laptop does. Two boards straight-cabled then sit at RANDOM addresses across
# 169.254.0.0/16, and discovery swept only the /24 of its own address -- 254
# probes at strangers -- so they never found each other.
#
# Pinned here, with a fake avahi-browse and a fake neighbour table:
#   1. avahi-browse -rpt output parses: resolved IPv4 lines only, escapes
#      undone, TXT split into keys, one entry per (name, address)
#   2. the TXT this box publishes carries version, role, serial, hub port
#   3. candidate_hosts: mDNS addresses always; 169.254 neighbours only when
#      this box is itself link-local; this box's own addresses never
#   4. a link-local address gets no /24 sweep of its own
#   5. scan() probes the mDNS/link-local candidates beside the /24 and still
#      filters them by system_slug -- an advertisement is a hint, not an
#      identity -- and mdns=False is the sweep alone
#   6. a box without avahi-utils browses nothing and does not fail
#   7. the hub starts the publisher, and [discovery].mdns is read
#
#   py -3 tests/smoke_mdnsdiscovery.py
import ipaddress
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import discovery, discovery_mdns          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# What avahi-browse -rpt prints on box A (169.254.10.20) cabled to box B.
BROWSE = "\n".join([
    "+;eth0;IPv4;iriv-b2ca6353;_irivai._tcp;local",
    "+;eth0;IPv4;iriv-10000000;_irivai._tcp;local",
    "=;eth0;IPv6;iriv-b2ca6353;_irivai._tcp;local;iriv-b2ca6353.local;"
    "fe80::1;5100;\"serial=b2ca6353\"",
    "=;eth0;IPv4;iriv-b2ca6353;_irivai._tcp;local;iriv-b2ca6353.local;"
    "169.254.77.5;5100;\"txtvers=1\" \"version=1.0.14\" \"role=hub\" "
    "\"serial=b2ca6353\" \"hub_port=5100\" \"agent_port=5101\"",
    # The same box again on a second interface's view: one entry.
    "=;eth0;IPv4;iriv-b2ca6353;_irivai._tcp;local;iriv-b2ca6353.local;"
    "169.254.77.5;5100;\"serial=b2ca6353\"",
    # This box itself (avahi lists local services too), renamed on collision.
    "=;eth0;IPv4;IRIV\\032box\\035two;_irivai._tcp;local;iriv-2.local;"
    "169.254.10.20;5100;\"serial=10000000\" \"note=a\\\"b\"",
    "=;eth0;IPv4;broken;_irivai._tcp;local;x.local;not-an-ip;5100;",
])

ARP = "\n".join([
    "IP address       HW type     Flags       HW address            Mask     Device",
    "169.254.200.9    0x1         0x2         aa:bb:cc:dd:ee:01     *        eth0",
    "169.254.200.10   0x1         0x0         00:00:00:00:00:00     *        eth0",
    "169.254.0.7      0x1         0x2         aa:bb:cc:dd:ee:02     *        eth0",
    "192.168.1.1      0x1         0x2         aa:bb:cc:dd:ee:03     *        wlan0",
])

calls = []


def fake_run(argv, timeout):
    calls.append(argv)
    return BROWSE if argv[0] == "avahi-browse" else ""


# -- 1. parsing ------------------------------------------------------------------
svcs = discovery_mdns.browse(fake_run)
check("browse runs avahi-browse -rpt for _irivai._tcp",
      calls and calls[0] == ["avahi-browse", "-rpt", "_irivai._tcp"], calls)
check("resolved IPv4 lines only, one per (name, address), bad address dropped",
      [s["address"] for s in svcs] == ["169.254.77.5", "169.254.10.20"], svcs)
b = svcs[0]
check("TXT keys come through",
      b["txt"] == {"txtvers": "1", "version": "1.0.14", "role": "hub",
                   "serial": "b2ca6353", "hub_port": "5100", "agent_port": "5101"},
      b["txt"])
check("host and port come through", (b["host"], b["port"]) == ("iriv-b2ca6353.local", 5100))
check("avahi's \\DDD escapes are undone in names",
      svcs[1]["name"] == "IRIV box#two", svcs[1]["name"])

# -- 2. what we publish --------------------------------------------------------------
txt = discovery_mdns.txt_record("1.0.14", "hub", "b2ca6353", 5105)
check("TXT carries version, role, serial, hub port, agent port",
      txt == ["txtvers=1", "version=1.0.14", "role=hub", "serial=b2ca6353",
              "hub_port=5105", "agent_port=5101"], txt)
argv = discovery_mdns.publish_argv("iriv-b2ca6353", 5105, txt)
check("the advertised port is the hub's",
      argv[:4] == ["avahi-publish-service", "iriv-b2ca6353", "_irivai._tcp", "5105"], argv)

# -- 3. candidates ----------------------------------------------------------------
ll = discovery_mdns.candidate_hosts(["169.254.10.20"], runner=fake_run,
                                    read_arp=lambda: ARP)
check("link-local box: mDNS peer + complete 169.254 neighbours, never itself, "
      "never the reserved /24, never an incomplete entry",
      ll == ["169.254.77.5", "169.254.200.9"], ll)
lan = discovery_mdns.candidate_hosts(["192.168.1.50"], runner=fake_run,
                                     read_arp=lambda: ARP)
check("site-LAN box: mDNS addresses only, no neighbour harvest",
      lan == ["169.254.10.20", "169.254.77.5"], lan)

# -- 4. no /24 for a link-local address ----------------------------------------
real_local_ips = discovery._local_ips
discovery._local_ips = lambda: {"169.254.10.20", "192.168.1.50"}
nets = discovery.local_subnets()
check("a 169.254 address gets no /24 sweep; a LAN address keeps its /24",
      [str(n) for n in nets] == ["192.168.1.0/24"], nets)

# -- 5. scan() probes the candidates and still filters by slug -----------------------
discovery._local_ips = lambda: {"169.254.10.20"}
probed = []
AGENTS = {"169.254.77.5": {"host_serial": "B2CA6353", "system_slug": "conveyor"},
          "169.254.200.9": {"host_serial": "deadbeef", "system_slug": "other"}}


def fake_probe(ip, port=5101, timeout=0.4):
    probed.append(str(ip))
    return AGENTS.get(str(ip))


real_probe, real_run = discovery.probe, discovery_mdns._run
real_arp = discovery_mdns._read_arp
discovery.probe = fake_probe
discovery_mdns._run = fake_run
discovery_mdns._read_arp = lambda: ARP
try:
    found = discovery.scan("conveyor")
    check("link-local box: the cabled peer is found by mDNS", [
        (d["host_serial"], d["url"]) for d in found] == [
        ("b2ca6353", "http://169.254.77.5:5101")], found)
    check("...the other system's link-local neighbour was probed and dropped",
          "169.254.200.9" in probed and all(d["host_serial"] != "deadbeef" for d in found))
    check("...and no 169.254 /24 was swept", len(probed) == 2, len(probed))
    got = discovery.locate([{"host_serial": "b2ca6353"}], "conveyor")
    check("locate() re-finds a known serial at its link-local address",
          got == {"b2ca6353": "http://169.254.77.5:5101"}, got)
    probed.clear()
    check("mdns=False is the sweep alone (nothing to sweep here = nothing)",
          discovery.scan("conveyor", mdns=False) == [] and probed == [], probed)
    probed.clear()
    discovery.scan("conveyor", subnets=["10.0.0.0/30"])
    check("explicit subnets are swept AND the mDNS hints probed",
          sorted(probed) == ["10.0.0.1", "10.0.0.2", "169.254.200.9", "169.254.77.5"],
          probed)

    # -- 6. no avahi-utils ----------------------------------------------------------
    discovery_mdns._run = lambda argv, timeout: ""
    check("no avahi-browse: browse() is empty, not an error",
          discovery_mdns.browse() == [])
    check("..._run itself answers '' for a tool that is not installed",
          real_run(["no-such-tool-iriv-xyz"], 1) == "")
finally:
    discovery.probe, discovery_mdns._run = real_probe, real_run
    discovery_mdns._read_arp = real_arp
    discovery._local_ips = real_local_ips

# -- 7. wiring ----------------------------------------------------------------------
check("[discovery].mdns defaults on and can be turned off",
      discovery.settings({})["mdns"] is True
      and discovery.settings({"discovery": {"mdns": False}})["mdns"] is False)
hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
check("the hub starts the advertisement", "discovery_mdns.start(config_dir)" in hub)
pub = discovery_mdns.Publisher("iriv-x", 5100, txt, "b2ca6353", runner=fake_run)
pub.serial = "10000000"
check("the publisher learns the name avahi actually gave this box",
      pub.learn_own_host() == "iriv-2.local", pub.own_host)
settings_src = open(os.path.join(ROOT, "core", "services", "settings.py"),
                    encoding="utf-8").read()
check("the pairing scan probes the mDNS candidates too",
      "discovery_mdns.candidate_hosts" in settings_src)
check("candidate addresses are valid IPv4",
      all(ipaddress.IPv4Address(x) for x in ll))

print("FAIL" if bad else "OK")
sys.exit(1 if bad else 0)
