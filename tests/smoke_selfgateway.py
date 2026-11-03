# A gateway that is the box itself is refused at save, and named by the
# update check when a box already has one.
#
# Board .47 (2026-10-07, v1.0.18): eth1's profile, saved on v1.0.12, carried
# gateway 192.168.3.1 = eth1's own address. v1.0.12 checked only that the
# gateway parsed; the self and subnet checks (netaddr.refuse_bad_host) came
# with 1.0.14 and a saved profile is not rewritten. Pinned here:
#   1. the Network page's writer refuses a gateway equal to the address
#      being set, one outside the subnet (both since 1.0.14), and now one
#      equal to ANOTHER port's address (wlan0's, say), each in plain words
#      and before any nmcli write; the port's own current address is not
#      counted (it is the one being replaced)
#   2. the update check: an offline failure on a box whose winning default
#      route is a known dead end says so ("No internet: port eth1's gateway
#      ... is this box itself"), the generic sentence otherwise; the worded
#      form still counts as offline for the notice body and the auto-check
#      loop, and a TLS failure never asks
#
#   py tests/smoke_selfgateway.py
import os
import ssl
import sys
import tempfile
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netconf                           # noqa: E402
from core.services import update as upd                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. the writer -----------------------------------------------------------------
writes = []
netconf._nmcli = lambda args, sudo=False, timeout=45: writes.append(list(args)) or ""
# .47 as the page sees it: eth0 on the site LAN by DHCP, wlan0 joined to a
# second network as a client, eth1 the machine port being set.
DEVS = [{"device": "eth0", "type": "ethernet", "ip": "192.168.1.47", "prefix": "24",
         "gateway": "192.168.1.1", "dns": ["192.168.1.1"]},
        {"device": "eth1", "type": "ethernet", "ip": "192.168.3.1", "prefix": "24",
         "gateway": "", "dns": []},
        {"device": "wlan0", "type": "wifi", "ip": "10.8.0.5", "prefix": "24",
         "gateway": "10.8.0.1", "dns": []}]
ETH1 = DEVS[1]


def attempt(ip, gateway, prefix="24", role="uplink"):
    writes.clear()
    try:
        return netconf._ipv4_settings(ETH1, False, ip, prefix, gateway, "", DEVS,
                                      role=role), ""
    except netconf.NetError as exc:
        return None, str(exc)


out, err = attempt("192.168.3.1", "192.168.3.1")
check("gateway = the address being set is refused, naming the box",
      out is None and "this box's own address" in err, err)
out, err = attempt("192.168.3.1", "192.168.4.1")
check("a gateway outside the subnet is refused", out is None and "outside" in err, err)
out, err = attempt("10.8.0.9", "10.8.0.5")
check("a gateway that is ANOTHER port's address (wlan0's) is refused, naming that port",
      out is None and "this box's own address on wlan0" in err and "router" in err, err)
check("...and nothing was written", not writes, writes)
out, err = attempt("192.168.3.5", "192.168.3.1")
check("the port's own current address is not counted: it is being replaced",
      out is not None and "ipv4.gateway" in out and out[out.index("ipv4.gateway") + 1]
      == "192.168.3.1", err)
out, err = attempt("192.168.3.1", "", role="local")
check("no gateway on the machine port is accepted, never-default yes",
      out is not None and out[out.index("ipv4.never-default") + 1] == "yes", err)
out, err = attempt("192.168.3.1", "")
check("...and so it is whatever role is passed: no gateway is never a default route "
      "(Tok Aba 2026-10-09: the uplink is decided, not ticked)",
      out is not None and out[out.index("ipv4.never-default") + 1] == "yes", err)
out, err = attempt("10.8.0.9", "10.8.0.1")
check("a real router on that subnet is accepted", out is not None, err)
src = open(os.path.join(ROOT, "core", "services", "netconf.py"), encoding="utf-8").read()
check("set_ipv4 hands the devices list to the writer, so the check sees every port",
      "_ipv4_settings(entry, dhcp, ip, prefix, gateway, dns, devs)" in src
      and "_ipv4_settings(entry, dhcp, ip, prefix, gateway, dns, devs, role, metric)" in src)

# -- 2. the update check's sentence ------------------------------------------------
CFG = tempfile.mkdtemp()
RAISE = {"exc": None}
SAY = {"text": "", "asked": 0}
SELF = ("No internet: port eth1's gateway 192.168.3.1 is this box itself, so the "
        "route leads nowhere - clear the gateway on eth1 under Settings -> Network "
        "(a machine-only port needs none)")


def fake_api(path, tok, accept="", headers=None, config_dir=None, channel=None):
    raise RAISE["exc"]


def fake_explain():
    SAY["asked"] += 1
    return SAY["text"]


upd._api = fake_api
upd._heal = lambda config_dir=None: False
upd._explain = fake_explain
upd.token = lambda config_dir=None, channel=None: "ghp_fake"


def run(exc, text):
    RAISE["exc"], SAY["text"], SAY["asked"] = exc, text, 0
    upd._write_json(upd._p(upd.CHECK_FILE["stable"]), {})
    return upd.check(config_dir=CFG, channel="stable")


no_route = urllib.error.URLError(OSError(113, "No route to host"))
out = run(no_route, SELF)
check("an offline failure on a box with a self-gateway route names it",
      out["error"] == SELF and SAY["asked"] == 1, out)
check("...the raw failure still rides in details only",
      "No route to host" in out["details"] and "Errno" not in out["error"], out)
out = run(no_route, "")
check("...and reads the generic sentence when there is nothing to name",
      out["error"] == upd.CHECK_OFFLINE, out)
out = run(urllib.error.URLError(ssl.SSLError(1, "CERTIFICATE_VERIFY_FAILED")), SELF)
check("a TLS failure never asks for the route sentence",
      out["error"] == upd.CHECK_UNREACHABLE and SAY["asked"] == 0, out)
check("is_offline: both forms, and not the unreachable one",
      upd.is_offline(SELF) and upd.is_offline(upd.CHECK_OFFLINE)
      and not upd.is_offline(upd.CHECK_UNREACHABLE) and not upd.is_offline(""))
check("the counted notice keeps its 'no internet' body for the worded form",
      upd._check_notice_body(SELF) == upd.CHECK_NOTICE_BODY)
check("...and it still counts as a failed check", upd._check_failed(SELF))
auto = open(os.path.join(ROOT, "core", "services", "update_auto.py"),
            encoding="utf-8").read()
check("the auto-check loop goes back offline on the worded form too",
      "update.is_offline(out.get(\"error\"))" in auto)
check("no em dash in the sentence", chr(0x2014) not in SELF)

print("\n%d failed" % bad if bad else "\nall passed")
sys.exit(1 if bad else 0)
