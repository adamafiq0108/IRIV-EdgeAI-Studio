# Two jacks of one box are never put on the same network.
#
# Customer UAT 2026-10-04, topology "iriv -> iriv": the board's eth1 (USB) was
# fixed at 192.168.1.20/24 and eth0 was then PRESET to 192.168.1.21/24 from the
# Network page. nmcli took it without a word. With both cables in, the kernel
# sends everything for 192.168.1.0/24 out ONE of the two ports whatever
# address it arrived on, and answers ARP for either address on both -- so the
# box is reachable on one of its two addresses, and which one is luck; on two
# different cables (switch on eth0, a second iriv straight into eth1) the
# second iriv never gets a reply at all.
#
# Pinned here:
#   1. a fixed address inside another wired jack's LIVE subnet is refused,
#      before anything is written, and the message names the other jack
#   2. a DORMANT fixed profile on the other jack (no cable yet) counts too:
#      that is exactly the one that bites when its cable goes in
#   3. a jack on DHCP with no lease has nothing to collide with, and the next
#      network over is fine
#   4. Wi-Fi is not a sibling: the AP subnet has its own refusal and a joined
#      network sits behind a metric
#   5. the page is told which interfaces are live on one network
#      (shares_subnet_with), since two DHCP leases never pass through a write
#   6. the names the routes and older tests know (NetError, mask_to_prefix,
#      prefix_to_mask) still live on netconf after the move to netaddr
#
#   py tests/smoke_ethsibling.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netaddr, netconf                 # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- the customer board: eth1 live and fixed at .20, eth0 bare, AP up ----------
box = {
    "status": [("eth0", "ethernet", "unavailable", ""),
               ("eth1", "ethernet", "connected", "Wired connection 2"),
               ("wlan0", "wifi", "connected", "Hotspot"),
               ("lo", "loopback", "connected (externally)", "lo")],
    "addr": {"eth1": ("192.168.1.20/24", "192.168.1.1", ["192.168.1.1"]),
             "wlan0": ("10.42.0.1/24", "", [])},
    "profiles": [("Wired connection 2", "u-eth1", "802-3-ethernet", "eth1"),
                 ("Hotspot", "u-ap", "802-11-wireless", "wlan0")],
    "ipv4": {"Wired connection 2": ("manual", "192.168.1.20/24", "192.168.1.1",
                                    "192.168.1.1")},
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
        p = next((p for p in box["profiles"] if p[1] == a[-1]), None)
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


def refused(device, **kwargs):
    calls.clear()
    try:
        netconf.set_ipv4(device, dhcp=False, via="eth1", **{"prefix": "24", **kwargs})
        return None
    except netconf.NetError as exc:
        return str(exc)


# -- 1. what happened on the customer board, refused -----------------------------
why = refused("eth0", ip="192.168.1.21")
check("eth0 preset to .21 while eth1 is live on 192.168.1.0/24 is refused",
      why is not None, "it was written")
check("...before anything was written", not writes(), writes())
check("...and the message names the other jack and its network",
      why is not None and "eth1" in why and "192.168.1.0/24" in why, why)
check("...a wider mask that swallows the other jack's network too",
      refused("eth0", ip="10.0.0.5", prefix="255.0.0.0") is not None)

# -- 3. the next network over is fine; a DHCP jack with no lease collides with nothing
calls.clear()
netconf.set_ipv4("eth0", dhcp=False, ip="192.168.2.21", prefix="24", via="eth1")
check("eth0 preset to 192.168.2.21/24 is written", len(writes()) == 1, writes())

box["status"][1] = ("eth1", "ethernet", "unavailable", "")
box["addr"].pop("eth1")
box["ipv4"]["Wired connection 2"] = ("auto", "", "", "")
calls.clear()
netconf.set_ipv4("eth0", dhcp=False, ip="192.168.1.21", prefix="24", via="")
check("with eth1 on DHCP and no cable, .21 on eth0 is fine", len(writes()) == 1,
      writes())

# -- 2. a dormant fixed profile on the other jack counts ------------------------
# (a SAVED profile is read by uuid, the way netconf looks it up off the list)
box["ipv4"]["u-eth1"] = ("manual", "192.168.1.20/24", "192.168.1.1", "")
why = refused("eth0", ip="192.168.1.21")
check("eth1 unplugged but SAVED as 192.168.1.20/24: eth0 .21 is still refused",
      why is not None and "eth1" in why, why or "it was written")
check("...the way out is named: DHCP or another network on the other jack",
      why is not None and "DHCP" in why, why)

# -- the live path is guarded the same way --------------------------------------
box["status"][0] = ("eth0", "ethernet", "connected", "Wired connection 1")
box["addr"]["eth0"] = ("192.168.5.9/24", "192.168.5.1", ["8.8.8.8"])
box["profiles"].insert(0, ("Wired connection 1", "u-eth0", "802-3-ethernet", "eth0"))
why = refused("eth0", ip="192.168.1.30", gateway="192.168.1.1")
check("a LIVE eth0 moved onto the dormant eth1 network is refused",
      why is not None and not writes(), (why, writes()))

# -- 4. Wi-Fi is not a sibling -----------------------------------------------------
check("wlan0's subnet is not a wired sibling's",
      netaddr.wired_subnet({"device": "wlan0", "type": "wifi", "ip": "10.42.0.1",
                            "prefix": "24"}) is None)
try:
    netaddr.refuse_sibling_overlap("eth0", "10.42.0.7", 24, [
        {"device": "wlan0", "type": "wifi", "ip": "10.42.0.1", "prefix": "24"}])
    check("...so the sibling check lets the AP's own refusal speak for it", True)
except netaddr.NetError as exc:
    check("...so the sibling check lets the AP's own refusal speak for it", False, exc)

# -- 5. the page is told about two jacks live on one network --------------------
box["status"][1] = ("eth1", "ethernet", "connected", "Wired connection 2")
box["addr"]["eth1"] = ("192.168.5.20/24", "192.168.5.1", [])
devs = {d["device"]: d for d in netconf.devices()}
check("eth0 and eth1 both live on 192.168.5.0/24 name each other",
      devs["eth0"]["shares_subnet_with"] == ["eth1"]
      and devs["eth1"]["shares_subnet_with"] == ["eth0"],
      {k: v.get("shares_subnet_with") for k, v in devs.items()})
check("...wlan0 shares with nobody, and the key is always there",
      devs["wlan0"]["shares_subnet_with"] == [], devs["wlan0"])
box["addr"]["eth1"] = ("192.168.6.20/24", "192.168.6.1", [])
devs = {d["device"]: d for d in netconf.devices()}
check("...and on two networks the lists are empty",
      devs["eth0"]["shares_subnet_with"] == [] == devs["eth1"]["shares_subnet_with"])

# -- 6. the names older callers know are still on netconf -----------------------
check("netconf.NetError is netaddr's", netconf.NetError is netaddr.NetError)
check("mask_to_prefix and prefix_to_mask still answer on netconf",
      netconf.mask_to_prefix("255.255.255.0") == 24
      and netconf.prefix_to_mask(16) == "255.255.0.0")
check("netconf stays under its ceiling after the move",
      sum(1 for _ in open(os.path.join(ROOT, "core", "services", "netconf.py"),
                          encoding="utf-8")) <= 1062)

print("\n%s" % ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
