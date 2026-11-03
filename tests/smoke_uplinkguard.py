# -*- coding: utf-8 -*-
"""
smoke_uplinkguard.py -- a static uplink proves the internet answers through
it, or the box puts the port back by itself.

Tok Aba (2026-10-07, Pipi M2). Pinned against a fake NetworkManager, a fake
`ip`/`ping` and a fake socket, on any machine:

  1. ipv4_snapshot reads the LIVE profile's ipv4 settings in the form
     `connection modify` takes back (lists joined with ",", defaults filled)
  2. reached through the port: state ok, nothing asked, nothing written
  3. not reached: state pending with the question and a countdown; Keep ends
     it with nothing written
  4. not reached and NO answer: the box itself writes the previous settings
     back and brings the connection up -- the page is not needed (it may
     have been cut off by the change)
  5. Revert now does the same at once; a new write to the port supersedes a
     pending question without reverting
  6. the probe goes over the port: TCP from its own address only when the
     kernel routes that way, else ping bound to it (-I)
  7. the route reads the snapshot BEFORE set_ipv4 writes, arms only for a
     live static uplink, and Keep / Revert are admin routes
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

from core.services import netconf, routeheal, uplinkguard     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


calls = []


def fake_nmcli(args, sudo=False, timeout=45):
    a = tuple(args)
    calls.append((a, sudo))
    if a[:2] == ("-t", "-f") and a[2].startswith("ipv4.method,") and a[-1] == "Wired eth1":
        return ("ipv4.method:manual\nipv4.addresses:192.168.4.5/24, 192.168.4.6/24\n"
                "ipv4.gateway:--\nipv4.dns:192.168.4.1, 8.8.8.8\n"
                "ipv4.never-default:yes\nipv4.route-metric:-1\n")
    return ""


netconf._nmcli = fake_nmcli
netconf.devices = lambda: [{"device": "eth1", "type": "ethernet", "connection": "Wired eth1"},
                           {"device": "eth2", "type": "ethernet", "connection": ""}]
cleared = []
netconf.clear_ipv4_reboot = lambda dev, config_dir=None: cleared.append(dev)

# -- 1. the snapshot -----------------------------------------------------------
name, prev = netconf.ipv4_snapshot("eth1")
check("ipv4_snapshot names the live profile and reads its ipv4 settings (DNS mode too)",
      name == "Wired eth1" and prev[::2] == list(netconf._SNAPSHOT_KEYS), repr(prev))
pv = dict(zip(prev[::2], prev[1::2]))
check("...lists joined the way modify takes them, '--' as empty",
      pv["ipv4.addresses"] == "192.168.4.5/24,192.168.4.6/24"
      and pv["ipv4.dns"] == "192.168.4.1,8.8.8.8" and pv["ipv4.gateway"] == ""
      and pv["ipv4.never-default"] == "yes" and pv["ipv4.route-metric"] == "-1", repr(pv))
check("...and nothing for a port with nothing live", netconf.ipv4_snapshot("eth2") == ("", []))
check("...read-only", not [c for c in calls if c[1]])

# -- the probe, faked: `net` says whether the internet answers ------------------
net = {"up": False, "route": "eth1"}
runs = []


def fake_run(argv, timeout=10):
    runs.append(list(argv))
    if argv[:4] == ["ip", "-4", "route", "get"]:
        return 0, f"{argv[4]} via 192.168.4.1 dev {net['route']} src 192.168.4.5 uid 0\n"
    if argv[0] == "ping":
        return (0 if net["up"] else 1), ""
    return 127, ""


routeheal.run = fake_run
connects = []


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_connect(addr, timeout=None, source_address=None):
    connects.append((addr, source_address))
    if net["up"]:
        return _Conn()
    raise OSError("unreachable")


socket.create_connection = fake_connect
uplinkguard.CHECK_S, uplinkguard.KEEP_S, uplinkguard.POLL_S = 0.3, 0.6, 0.05


def wait_state(dev, states, limit=5.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        st = uplinkguard.state().get(dev, {})
        if st.get("state") in states:
            return st
        time.sleep(0.02)
    return uplinkguard.state().get(dev, {})


def writes():
    return [a for a, sudo in calls if sudo]


# -- 2. reached ------------------------------------------------------------------
calls.clear()
net["up"] = True
uplinkguard.arm("eth1", "Wired eth1", prev, "192.168.4.5", "192.168.4.1")
st = wait_state("eth1", ("ok",))
check("reached through the port: ok, nothing asked, nothing written",
      st.get("state") == "ok" and not writes(), repr((st, writes())))

# -- 3. not reached, Keep ----------------------------------------------------------
calls.clear()
net["up"] = False
uplinkguard.arm("eth1", "Wired eth1", prev, "192.168.4.5", "192.168.4.1")
check("the page sees the check under way at once", uplinkguard.state()["eth1"]["state"]
      == "checking")
st = wait_state("eth1", ("pending",))
check("not reached: the question, with the gateway and a countdown",
      st.get("state") == "pending" and "Internet not reachable through eth1" in st["message"]
      and "Keep this setting?" in st["message"] and 0 <= st.get("remaining_s", -1) <= 1,
      repr(st))
check("...no private field reaches the page", not any(k.startswith("_") for k in st))
uplinkguard.keep("eth1")
time.sleep(0.8)
check("Keep: kept, and nothing is written even after the countdown",
      uplinkguard.state()["eth1"]["state"] == "kept" and not writes(), repr(writes()))
try:
    uplinkguard.keep("eth1")
    check("a second Keep is refused", False)
except netconf.NetError:
    check("a second Keep is refused in words", True)

# -- 4. not reached, no answer: the BOX reverts ------------------------------------
calls.clear()
cleared.clear()
uplinkguard.arm("eth1", "Wired eth1", prev, "192.168.4.5", "192.168.4.1")
st = wait_state("eth1", ("reverted", "revert_failed"))
w = writes()
check("no answer: the box puts the previous settings back by itself",
      st.get("state") == "reverted" and w[:1] == [("connection", "modify", "Wired eth1") + tuple(prev)]
      and w[1:2] == [("connection", "up", "Wired eth1")], repr((st, w)))
check("...and says so in words", "previous settings" in st.get("message", ""), repr(st))
check("...a previous FIXED address keeps the reboot note", cleared == [])
dprev = ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "",
         "ipv4.never-default", "no", "ipv4.route-metric", "-1"]
uplinkguard.arm("eth1", "Wired eth1", dprev, "192.168.4.5", "192.168.4.1")
wait_state("eth1", ("reverted",))
check("...a previous DHCP drops the reboot note with the revert", cleared == ["eth1"], cleared)

# -- 5. Revert now, and a new write superseding ------------------------------------
calls.clear()
uplinkguard.arm("eth1", "Wired eth1", prev, "192.168.4.5", "192.168.4.1")
wait_state("eth1", ("pending",))
uplinkguard.revert("eth1")
check("Revert now: put back at once", uplinkguard.state()["eth1"]["state"] == "reverted"
      and writes()[0][:3] == ("connection", "modify", "Wired eth1"), repr(writes()))
time.sleep(0.8)
check("...and only once (the countdown does not revert again)",
      len([w for w in writes() if w[:2] == ("connection", "modify")]) == 1, repr(writes()))
calls.clear()
uplinkguard.arm("eth1", "Wired eth1", prev, "192.168.4.5", "192.168.4.1")
wait_state("eth1", ("pending",))
uplinkguard.disarm("eth1")
time.sleep(0.8)
check("a new write to the port supersedes the question: nothing reverted",
      not writes() and "eth1" not in uplinkguard.state(), repr(writes()))

# -- 6. the probe goes over the port ----------------------------------------------
connects.clear()
runs.clear()
net.update(up=True, route="eth1")
uplinkguard.reachable("eth1", "192.168.4.5")
check("routed that way: TCP from the port's own address, no ping needed",
      connects and connects[0][1] == ("192.168.4.5", 0)
      and not any(r[0] == "ping" for r in runs), repr((connects, runs)))
connects.clear()
runs.clear()
net.update(route="eth0")
uplinkguard.reachable("eth1", "192.168.4.5")
check("routed elsewhere: no TCP (it would leave by the other port), ping -I eth1",
      not connects and any(r[:1] == ["ping"] and "-I" in r and r[r.index("-I") + 1] == "eth1"
                           for r in runs), repr((connects, runs)))

# -- 7. the route --------------------------------------------------------------------
rsrc = open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8").read()
body = rsrc[rsrc.index("async def network_ipv4(request"):]
body = body[:body.index("@router.")]
check("the route reads the snapshot BEFORE set_ipv4 writes",
      body.index("netconf.ipv4_snapshot") < body.index("_net(netconf.set_ipv4"))
check("...arms only for a live fixed address with a gateway, as the role decided",
      "if not dhcp and gateway.strip():" in body
      and 'role in ("uplink", "standby")' in body and "role=role" in body
      and 'body.get("uplink")' not in body)
check("...and any write to the port first drops a check on it",
      body.index("uplinkguard.disarm(device)") < body.index("_net(netconf.set_ipv4"))
check("Keep and Revert are admin routes",
      all('_require_admin(request)' in rsrc[rsrc.index(f'name="network.ipv4_{n}"'):][:900]
          for n in ("keep", "revert")))

print("\n%s" % ("all uplink guard checks passed" if not bad else f"{bad} check(s) FAILED"))
sys.exit(1 if bad else 0)
