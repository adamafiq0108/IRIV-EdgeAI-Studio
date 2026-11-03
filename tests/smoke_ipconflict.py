# -*- coding: utf-8 -*-
"""
smoke_ipconflict.py -- the static IP Test says "IP conflict" when another
device answers for the address, and Apply asks the same question.

Tok Aba (2026-10-09): "when detected ip conflict then tell it is conflict".
Pinned against canned arping / ping / `ip neigh` output, on any machine:

  1. this box's own address: not a conflict, a plain note (naming the other
     port when it is held there)
  2. arping -D (iputils): a reply is a conflict with the MAC; "Sent N probes"
     and no reply is free; an arping that could not send falls back
  3. no arping: a ping answer is a conflict; a dropped ping with a REACHABLE
     neighbour entry (a firewalled PC) is a conflict WITH its MAC; a STALE
     entry is not; nothing at all on the port's subnet is free
  4. an address off the port's current subnet with no arping: "cannot ask",
     never "free"; an unplugged jack: not checked
  5. not an address: refused; nothing here runs sudo
  6. the Apply route asks before writing and answers 409 with the sentence
"""

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import ipconflict, netconf                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


DEVS = [{"device": "eth0", "type": "ethernet", "ip": "192.168.1.116", "prefix": "24"},
        {"device": "eth1", "type": "ethernet", "ip": "", "prefix": ""},
        {"device": "wlan0", "type": "wifi", "ip": "10.42.0.1", "prefix": "24"}]
fake = {"arping": None, "arping_rc": 0, "ping": 1, "neigh": ""}
runs = []


def fake_run(argv, timeout=8):
    runs.append(list(argv))
    if argv[0] == "arping":
        return fake["arping_rc"], fake["arping"] or ""
    if argv[0] == "ping":
        return fake["ping"], ""
    if argv[:3] == ["ip", "neigh", "show"]:
        return 0, fake["neigh"]
    return 127, ""


ipconflict.run = fake_run


def ask(ip, device="eth0", has_arping=False, **kw):
    fake.update(arping=None, arping_rc=0, ping=1, neigh="")
    fake.update(kw)
    ipconflict.which = (lambda name: "/usr/sbin/arping") if has_arping else (lambda name: None)
    runs.clear()
    return ipconflict.address_in_use(ip, device, DEVS)


# -- 1. own ---------------------------------------------------------------------------
a = ask("192.168.1.116")
check("this box's own address: not a conflict, a plain note, nothing probed",
      a["in_use"] is False and a["own"] is True and "already this box's address" in a["detail"]
      and not runs, a)
a = ask("10.42.0.1")
check("...held on another port: says which", a["own"] and "(on wlan0)" in a["detail"], a)

# -- 2. arping ------------------------------------------------------------------------
a = ask("192.168.1.50", has_arping=True, arping_rc=1,
        arping="ARPING 192.168.1.50 from 0.0.0.0 eth0\n"
               "Unicast reply from 192.168.1.50 [AA:BB:CC:DD:EE:FF]  1.2ms\n"
               "Sent 1 probes (1 broadcast(s))\nReceived 1 response(s)\n")
check("arping reply: IP conflict, with the other device's MAC",
      a["in_use"] and a["checked"] and a["mac"] == "aa:bb:cc:dd:ee:ff"
      and a["detail"] == "IP conflict: 192.168.1.50 is already used by another device "
                         "(aa:bb:cc:dd:ee:ff)", a)
check("...asked as duplicate address detection on that port",
      runs[0][:2] == ["arping", "-D"] and runs[0][runs[0].index("-I") + 1] == "eth0", runs)
a = ask("192.168.1.51", has_arping=True, arping_rc=0,
        arping="ARPING 192.168.1.51 from 0.0.0.0 eth0\nSent 2 probes (2 broadcast(s))\n"
               "Received 0 response(s)\n")
check("arping, no reply: 'No conflict: <ip> is free', no ping needed",
      not a["in_use"] and a["checked"] and a["detail"] == "No conflict: 192.168.1.51 is free"
      and not any(r[0] == "ping" for r in runs), a)
a = ask("192.168.1.52", has_arping=True, arping_rc=2,
        arping="arping: socket: Operation not permitted\n", ping=0)
check("an arping that may not send falls back to ping (which answered: conflict)",
      a["in_use"] and any(r[0] == "ping" for r in runs), a)

# -- 3. ping + neighbour table -------------------------------------------------------
a = ask("192.168.1.60", ping=0,
        neigh="192.168.1.60 lladdr 11:22:33:44:55:66 REACHABLE\n")
check("ping answered: conflict, MAC read from the neighbour table",
      a["in_use"] and a["mac"] == "11:22:33:44:55:66" and "(11:22:33:44:55:66)" in a["detail"], a)
a = ask("192.168.1.61", ping=1,
        neigh="192.168.1.61 lladdr 11:22:33:44:55:77 REACHABLE\n")
check("ping dropped but ARP answered (a firewalled PC, a PLC): still a conflict",
      a["in_use"] and a["method"] == "arp" and a["mac"] == "11:22:33:44:55:77", a)
a = ask("192.168.1.62", ping=1, neigh="192.168.1.62 lladdr 11:22:33:44:55:88 STALE\n")
check("a STALE entry (may have left last week) is not a conflict: free",
      not a["in_use"] and a["checked"] and "No conflict" in a["detail"], a)
a = ask("192.168.1.63", ping=1, neigh="192.168.1.63  FAILED\n")
check("nothing answers on the port's subnet: free", not a["in_use"] and a["checked"], a)

# -- 4. cannot ask -----------------------------------------------------------------------
a = ask("10.0.0.10", ping=1)
check("off the port's subnet, no arping: not checked, never 'free'",
      not a["checked"] and "No conflict" not in a["detail"], a)
a = ask("10.0.0.10", has_arping=True, arping_rc=0, arping="Sent 2 probes (2 broadcast(s))\n")
check("...arping can ask off-subnet (DAD is layer 2): free", a["checked"] and not a["in_use"], a)
a = ask("192.168.50.10", device="eth1")
check("an unplugged jack: not checked, and says why",
      not a["checked"] and "not connected" in a["detail"] and not runs, a)
ipconflict.which = lambda name: None
fake["ping"] = 127
runs.clear()
a = ipconflict.address_in_use("192.168.1.70", "eth0", DEVS)
check("no ping on the box: not checked", not a["checked"] and not a["in_use"], a)

# -- 5. input --------------------------------------------------------------------------
for wrong in ("192.168.1.999", "", "-I eth0", "1.2.3.4; reboot"):
    try:
        ask(wrong)
        check(f"{wrong!r} refused", False)
    except netconf.NetError:
        check(f"{wrong!r} refused before anything runs", not runs, runs)
check("netconf.address_in_use is this one (the route and older tests call it there)",
      netconf.address_in_use is ipconflict.address_in_use)
src = open(os.path.join(ROOT, "core", "services", "ipconflict.py"), encoding="utf-8").read()
check("nothing here runs sudo", "sudo" not in src.split('"""', 2)[2])

# -- 6. the Apply route -------------------------------------------------------------------
rsrc = open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8").read()
body = rsrc[rsrc.index("async def network_ipv4(request"):]
body = body[:body.index("@router.")]
check("Apply asks the conflict question before it writes anything",
      body.index("netconf.address_in_use") < body.index("uplinkguard.disarm(device)")
      < body.index("_net(netconf.set_ipv4"))
check("...only for a fixed address, and a conflict is a 409 with the sentence",
      "if not dhcp and netconf._valid_ip(" in body and "status_code=409" in body
      and 'clash["detail"]' in body)

print("\n%s" % ("all ip conflict checks passed" if not bad else f"{bad} check(s) FAILED"))
sys.exit(1 if bad else 0)
