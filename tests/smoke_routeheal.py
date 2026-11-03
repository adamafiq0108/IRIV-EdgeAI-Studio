# A default route whose gateway answers nobody is demoted, when -- and only
# when -- another port's gateway is proven to answer.
#
# Board .22 (2026-10-05, v1.0.13): eth0 fixed at 10.0.0.10/24 with gateway
# 10.0.0.1 on the Modbus LAN (no router there), metric 100; eth1 on DHCP
# behind 192.168.1.1, metric 101. Every update check died with "[Errno 113]
# No route to host". Pinned here, against canned ip / ping / nmcli output:
#   1. that box: eth0's profile gets ipv4.route-metric 2000 and is brought
#      back up, and the admins get an Inbox notice naming eth0 and the change
#   2. a box with ONE gateway is never touched, dead or not
#   3. both gateways dead: nothing changes (no alternative to move to)
#   4. a winning gateway that answers costs one ping and writes nothing
#   5. a gateway that drops ICMP but answers ARP (REACHABLE) is alive
#   6. an already-demoted profile is left alone (no repeat notice)
#   7. a refused sudo is reported, and nothing is brought up after it
#   8. only `nmcli connection modify uuid ...` and `nmcli connection up` ever
#      run through sudo -- the verbs the narrowed irivai-box grant names
#   9. annotate() marks which port holds the default route (Network page)
#  10. no iproute2 (a laptop): no routes, nothing done
#
# Board .47 (2026-10-07, v1.0.18): eth1 fixed at 192.168.3.1/24 with gateway
# 192.168.3.1, the box's OWN address (typed on v1.0.12, before the self check);
# its static route beat eth0's DHCP uplink and the demotion above never came,
# because a ping to one's own address answers. gatewayfault.py, pinned:
#  11. a gateway that is one of the box's addresses is dead with NO ping, and
#      the PROFILE is corrected: ipv4.gateway cleared, never-default yes, the
#      profile brought up by uuid; no metric write; an admins' notice names
#      eth1, the address, the profile and the port the internet now uses
#  12. ...even on a box with that ONE default route (never a rebooting router)
#  13. ...but not when the profile does not itself say that gateway (a route
#      from a lease or `ip route` by hand): reported, nothing written
#  14. a gateway outside the port's own subnet is dead with no ping, and the
#      ordinary demotion follows
#  15. explain(): the sentence names the port and the self / off-link cause,
#      and is "" for a sound winning route
#  16. a refused profile write is reported, and nothing is brought up
#  17. `connection up` goes by uuid (Pipi, 1.0.15 gate H1)
#
#   py tests/smoke_routeheal.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import gatewayfault, inbox, routeheal    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


BOARD_22 = ("default via 10.0.0.1 dev eth0 proto static metric 100 \n"
            "default via 192.168.1.1 dev eth1 proto dhcp src 192.168.1.22 metric 101 \n")

box = {}
calls = []


def reset(routes=BOARD_22, alive=("192.168.1.1",), neigh=None, metric="100",
          sudo_ok=True, have_ip=True, addrs="", pgw=None):
    box.clear()
    box.update(routes=routes, alive=set(alive), neigh=neigh or {}, metric=metric,
               sudo_ok=sudo_ok, have_ip=have_ip, addrs=addrs, pgw=pgw or {})
    calls.clear()


def fake_run(argv, timeout=10):
    argv = list(argv)
    calls.append(argv)
    if argv[:2] == ["sudo", "-n"]:
        return (0, "") if box["sudo_ok"] else (1, "")
    if argv[0] == "ip":
        if not box["have_ip"]:
            return 127, ""
        if argv[1:] == ["-4", "route", "show", "default"]:
            return 0, box["routes"]
        if argv[1:] == ["-4", "-o", "addr", "show"]:
            return 0, box["addrs"]
        if argv[1:3] == ["neigh", "show"]:
            gw, dev = argv[3], argv[5]
            state = box["neigh"].get(gw, "FAILED")
            return 0, f"{gw} dev {dev} {state}\n" if state else ""
    if argv[0] == "ping":
        return (0, "") if argv[-1] in box["alive"] else (1, "")
    if argv[:4] == ["nmcli", "-t", "-f", "GENERAL.CONNECTION,GENERAL.CON-UUID"]:
        dev = argv[-1]
        return 0, (f"GENERAL.CONNECTION:Wired {dev}\n"
                   f"GENERAL.CON-UUID:u-{dev}\n")
    if argv[:4] == ["nmcli", "-t", "-f", "ipv4.route-metric"]:
        return 0, f"ipv4.route-metric:{box['metric']}\n"
    if argv[:4] == ["nmcli", "-t", "-f", "ipv4.method,ipv4.gateway"]:
        method, gw = box["pgw"].get(argv[-1], ("manual", ""))
        return 0, f"ipv4.method:{method}\nipv4.gateway:{gw}\n"
    return 1, ""


routeheal.run = fake_run
cfg = tempfile.mkdtemp()


def sudo_calls():
    return [c for c in calls if c[:2] == ["sudo", "-n"]]


def notices():
    return [r for r in inbox.for_user("admin", is_admin=True, config_dir=cfg)
            if r.get("kind") == routeheal.NOTICE_KIND]


# -- 1. board .22 -----------------------------------------------------------------
reset()
r = routeheal.heal(cfg)
check("board .22: the dead eth0 route is demoted", r.get("changed") is True, r)
s = sudo_calls()
check("...by raising eth0's profile metric, addressed by uuid",
      s and s[0] == ["sudo", "-n", "nmcli", "connection", "modify", "uuid", "u-eth0",
                     "ipv4.route-metric", str(routeheal.DEMOTED_METRIC)], s)
check("...then that one profile is brought up again by uuid (H1), nothing else",
      len(s) == 2 and s[1] == ["sudo", "-n", "nmcli", "connection", "up", "uuid", "u-eth0"], s)
check("...eth1's profile is never written", not [c for c in s if "u-eth1" in c
                                                  or "Wired eth1" in c], s)
n = notices()
check("...one admins' notice, naming eth0 and what changed",
      len(n) == 1 and "eth0" in n[0]["title"] and "10.0.0.1" in n[0]["body"]
      and str(routeheal.DEMOTED_METRIC) in n[0]["body"] and "eth1" in n[0]["body"]
      and n[0]["page"] == "settings", n)
check("...the result names both ports for the caller",
      r.get("device") == "eth0" and r.get("via_device") == "eth1"
      and r.get("via_gateway") == "192.168.1.1", r)

# -- 2. one gateway: never touched ---------------------------------------------------
reset(routes="default via 10.0.0.1 dev eth0 proto static metric 100\n", alive=())
r = routeheal.heal(cfg)
check("one gateway, dead: nothing written", not sudo_calls() and not r["changed"]
      and r["state"] == "single", (r, calls))
check("...and not even pinged", not [c for c in calls if c[0] == "ping"], calls)

# -- 3. both dead ---------------------------------------------------------------------
reset(alive=())
r = routeheal.heal(cfg)
check("both gateways dead: offline, nothing written",
      r["state"] == "offline" and not sudo_calls(), (r, calls))

# -- 4. healthy box -------------------------------------------------------------------
reset(alive=("10.0.0.1", "192.168.1.1"))
r = routeheal.heal(cfg)
check("winning gateway answers: one ping, no writes",
      r["state"] == "ok" and not sudo_calls()
      and len([c for c in calls if c[0] == "ping"]) == 1, calls)

# -- 5. ICMP filtered, ARP answers ----------------------------------------------------
reset(alive=("192.168.1.1",), neigh={"10.0.0.1": "REACHABLE"})
r = routeheal.heal(cfg)
check("a gateway that drops ping but answers ARP is alive",
      r["state"] == "ok" and not sudo_calls(), (r, calls))
reset(alive=("192.168.1.1",), neigh={"10.0.0.1": "INCOMPLETE"})
check("...INCOMPLETE is dead", routeheal.diagnose()["state"] == "heal")
reset(alive=("192.168.1.1",), neigh={"10.0.0.1": ""})
check("...no neighbour entry at all is dead", routeheal.diagnose()["state"] == "heal")

# -- 6. already demoted ---------------------------------------------------------------
before = len(notices())
reset(metric=str(routeheal.DEMOTED_METRIC))
r = routeheal.heal(cfg)
check("an already-demoted profile is left alone",
      not r["changed"] and not sudo_calls() and len(notices()) == before, (r, calls))

# -- 7. sudo refused ------------------------------------------------------------------
reset(sudo_ok=False)
r = routeheal.heal(cfg)
check("a refused metric change is reported, and nothing is brought up",
      not r["changed"] and len(sudo_calls()) == 1 and "refused" in r["detail"],
      (r, sudo_calls()))
check("heal_for_retry says no retry then", routeheal.heal_for_retry(cfg) is False)
reset()
check("heal_for_retry says retry after a real change", routeheal.heal_for_retry(cfg) is True)

# -- 8. only the granted verbs --------------------------------------------------------
allowed = (["sudo", "-n", "nmcli", "connection", "modify", "uuid"],
           ["sudo", "-n", "nmcli", "connection", "up"])
reset()
routeheal.heal(cfg)
check("every sudo call is connection modify uuid / connection up, no -s",
      all(any(c[:len(a)] == a for a in allowed) and "-s" not in c
          and "--show-secrets" not in c for c in sudo_calls()), sudo_calls())

# -- 9. annotate ------------------------------------------------------------------------
reset()
devs = routeheal.annotate([{"device": "eth0"}, {"device": "eth1"}, {"device": "wlan0"}])
check("annotate: eth0 holds the default route now, eth1 has one too, wlan0 none",
      devs[0]["default_route"] and not devs[1]["default_route"]
      and devs[1]["has_default"] and not devs[2]["has_default"]
      and devs[1]["default_metric"] == 101, devs)

# -- 10. no iproute2 -------------------------------------------------------------------
reset(have_ip=False)
check("no `ip` binary: no routes, nothing done",
      routeheal.default_routes() == [] and routeheal.heal(cfg)["state"] == "single"
      and not sudo_calls())

# -- 11. board .47: the gateway is the box itself ------------------------------------------
BOARD_47 = ("default via 192.168.3.1 dev eth1 proto static metric 100 \n"
            "default via 192.168.1.1 dev eth0 proto dhcp src 192.168.1.47 metric 101 \n")
ADDRS_47 = ("2: eth0    inet 192.168.1.47/24 brd 192.168.1.255 scope global dynamic eth0\\"
            "       valid_lft 80000sec preferred_lft 80000sec\n"
            "3: eth1    inet 192.168.3.1/24 brd 192.168.3.255 scope global noprefixroute eth1\\"
            "       valid_lft forever preferred_lft forever\n"
            "4: wlan0    inet 192.168.1.49/24 brd 192.168.1.255 scope global dynamic wlan0\\"
            "       valid_lft 80000sec preferred_lft 80000sec\n")
PGW_47 = {"u-eth1": ("manual", "192.168.3.1")}


def pings():
    return [c[-1] for c in calls if c[0] == "ping"]


def self_notices():
    return [r for r in inbox.for_user("admin", is_admin=True, config_dir=cfg)
            if r.get("kind") == gatewayfault.NOTICE_KIND]


reset(routes=BOARD_47, alive=("192.168.1.1", "192.168.3.1"), addrs=ADDRS_47, pgw=PGW_47)
held = gatewayfault.own_addresses()
check("own_addresses reads every port's address off `ip -4 -o addr show`",
      sorted(held) == ["eth0", "eth1", "wlan0"] and str(held["eth1"][0]) == "192.168.3.1/24",
      held)
check("a gateway that is the box's own address is a 'self' fault, on any port",
      gatewayfault.fault("192.168.3.1", "eth1", held) == "self"
      and gatewayfault.fault("192.168.1.49", "eth0", held) == "self")
check("...and counts as dead even though a ping to it would answer",
      routeheal.gateway_answers("192.168.3.1", "eth1", held) is False
      and "192.168.3.1" not in pings(), calls)
check("the real router is still alive", gatewayfault.fault("192.168.1.1", "eth0", held) == ""
      and routeheal.gateway_answers("192.168.1.1", "eth0", held) is True)
reset(routes=BOARD_47, alive=("192.168.1.1", "192.168.3.1"), addrs=ADDRS_47, pgw=PGW_47)
r = routeheal.heal(cfg)
check("board .47: the profile is corrected", r.get("changed") is True
      and r.get("state") == "self" and r.get("device") == "eth1"
      and r.get("gateway") == "192.168.3.1", r)
s = sudo_calls()
check("...gateway cleared and never-default yes on eth1's profile, by uuid",
      s and s[0] == ["sudo", "-n", "nmcli", "connection", "modify", "uuid", "u-eth1",
                     "ipv4.gateway", "", "ipv4.never-default", "yes"], s)
check("...then brought up by uuid, nothing else, and no metric write",
      len(s) == 2 and s[1] == ["sudo", "-n", "nmcli", "connection", "up", "uuid", "u-eth1"]
      and not [c for c in s if "ipv4.route-metric" in c], s)
check("...eth0's profile is never written", not [c for c in s if "u-eth0" in c], s)
check("...192.168.3.1 was never pinged", "192.168.3.1" not in pings(), pings())
check("...the result says where the internet goes now",
      r.get("via_device") == "eth0" and r.get("via_gateway") == "192.168.1.1", r)
n = self_notices()
check("...one admins' notice naming eth1, the address, the profile and eth0",
      len(n) == 1 and "eth1" in n[0]["title"] and "192.168.3.1" in n[0]["body"]
      and "Wired eth1" in n[0]["body"] and "via eth0" in n[0]["body"]
      and "this box's own address" in n[0]["body"] and n[0]["page"] == "settings", n)
reset(routes=BOARD_47, alive=("192.168.1.1",), addrs=ADDRS_47, pgw=PGW_47)
check("heal_for_retry says retry after the correction", routeheal.heal_for_retry(cfg) is True)

# -- 12. one default route, and it is the box itself ------------------------------------------
reset(routes="default via 192.168.3.1 dev eth1 proto static metric 100\n",
      alive=("192.168.3.1",), addrs=ADDRS_47, pgw=PGW_47)
r = routeheal.heal(cfg)
check("a self-gateway is corrected even as the box's only default route",
      r.get("changed") is True and len(sudo_calls()) == 2 and r.get("via_device") == "", r)

# -- 13. the profile does not say that gateway ---------------------------------------------
before = len(self_notices())
reset(routes=BOARD_47, alive=("192.168.1.1",), addrs=ADDRS_47, pgw={"u-eth1": ("auto", "")})
r = routeheal.heal(cfg)
check("a self-gateway the profile does not set itself: reported, nothing written",
      not r["changed"] and not sudo_calls() and "does not set gateway" in r["detail"]
      and len(self_notices()) == before, (r, sudo_calls()))

# -- 14. an off-link gateway ---------------------------------------------------------------
OFFLINK = ("default via 10.0.1.1 dev eth0 proto static metric 100\n"
           "default via 192.168.1.1 dev eth1 proto dhcp metric 101\n")
ADDRS_OFF = ("2: eth0    inet 10.0.0.10/24 brd 10.0.0.255 scope global eth0\n"
             "3: eth1    inet 192.168.1.22/24 brd 192.168.1.255 scope global dynamic eth1\n")
reset(routes=OFFLINK, alive=("192.168.1.1", "10.0.1.1"), addrs=ADDRS_OFF)
check("a gateway outside the port's subnet is an 'offlink' fault",
      gatewayfault.fault("10.0.1.1", "eth0") == "offlink")
r = routeheal.heal(cfg)
check("...counted dead with no ping, and the ordinary demotion follows",
      r.get("changed") is True and r.get("state") == "heal" and "10.0.1.1" not in pings()
      and sudo_calls()[0][-2:] == ["ipv4.route-metric", str(routeheal.DEMOTED_METRIC)],
      (r, calls))
reset(routes=OFFLINK, alive=("192.168.1.1", "10.0.1.1"), addrs="")
check("a port whose address is unknown is not judged off-link",
      gatewayfault.fault("10.0.1.1", "eth0") == "")

# -- 15. explain ---------------------------------------------------------------------------
reset(routes=BOARD_47, addrs=ADDRS_47)
text = gatewayfault.explain()
check("explain names the port and the self-gateway in plain words",
      text.startswith("No internet: port eth1's gateway 192.168.3.1 is this box itself")
      and "Settings -> Network" in text and "\u2014" not in text, text)
reset(routes=OFFLINK, addrs=ADDRS_OFF)
text = gatewayfault.explain()
check("...and an off-link gateway",
      "eth0's gateway 10.0.1.1 is not on eth0's own network" in text, text)
reset(addrs="2: eth0    inet 10.0.0.10/24 brd 10.0.0.255 scope global eth0\n")
check("...and says nothing for a sound winning route (board .22's is on-link)",
      gatewayfault.explain() == "")
reset(have_ip=False)
check("...nor with no `ip`", gatewayfault.explain() == "")

# -- 16. sudo refused on the correction ----------------------------------------------------
reset(routes=BOARD_47, addrs=ADDRS_47, pgw=PGW_47, sudo_ok=False)
r = routeheal.heal(cfg)
check("a refused profile write is reported, and nothing is brought up",
      not r["changed"] and len(sudo_calls()) == 1 and "refused" in r["detail"],
      (r, sudo_calls()))

# -- 17. still only the granted verbs, both paths -------------------------------------------
reset(routes=BOARD_47, addrs=ADDRS_47, pgw=PGW_47)
routeheal.heal(cfg)
check("the correction uses only connection modify uuid / connection up uuid, no -s",
      all(any(c[:len(a)] == a for a in allowed) and "-s" not in c for c in sudo_calls())
      and all(c[:5] != ["sudo", "-n", "nmcli", "connection", "up"] or c[5] == "uuid"
              for c in sudo_calls()), sudo_calls())

# -- the hub starts it --------------------------------------------------------------------
with open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8") as fh:
    hub = fh.read()
check("hub_server starts the watch", "routeheal.start_watch(config_dir" in hub)

print("\n%d failed" % bad if bad else "\nall passed")
sys.exit(1 if bad else 0)
