# Every box has its own name on the LAN (hostname collision, 2026-10-05).
#
# The installer named every box "iriv", so every box claimed iriv.local and
# avahi renamed the losers iriv-2, iriv-3 in boot order: the name on a front
# panel pointed at whichever box came up first.
#
# Pinned here:
#   1. default_hostname() is iriv-<last 8 of the serial>, lowercase; "iriv"
#      without a serial
#   2. validate(): RFC 1123 label, lowercased, ".local" accepted and dropped,
#      digits-only and localhost refused, each with a sentence
#   3. local_name() prefers the name avahi actually won over <hostname>.local
#   4. set_name() refuses plainly on a box without the root helper, and with
#      it sends the name on stdin to exactly HELPER_ARGV (never argv), then
#      marks the mDNS name stale
#   5. the front panel prints <this box's name>.local:<port>, not "iriv.local"
#   6. the route is master/developer only and says when the page itself rides
#      on the .local name a rename would take away
#
#   py -3 tests/smoke_hostname.py
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import discovery_mdns, hostinfo, hostname   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. default name ------------------------------------------------------------
hostinfo._static["serial"] = "B2CA63532B35EB4F"
check("default is iriv-<last 8 of serial>, lowercase",
      hostinfo.default_hostname() == "iriv-2b35eb4f", hostinfo.default_hostname())
hostinfo._static["serial"] = ""
real_serial = hostinfo.serial_number
hostinfo.serial_number = lambda: ""
check("no serial: plain iriv", hostinfo.default_hostname() == "iriv")
hostinfo.serial_number = real_serial
hostinfo._static["serial"] = "10000000b2ca6353"

# -- 2. validation ----------------------------------------------------------------
for raw, want in (("Line3-Dock", "line3-dock"), ("iriv-b2ca6353.local", "iriv-b2ca6353"),
                  ("  gate-a  ", "gate-a"), ("a" * 63, "a" * 63)):
    try:
        got = hostname.validate(raw)
    except hostname.HostnameError as exc:
        got = "refused: %s" % exc
    check("accepts %r as %r" % (raw[:20], want[:20]), got == want, got)
for raw in ("", "-gate", "gate-", "gate_a", "gate.a", "a" * 64, "12345", "localhost",
            "dock 3", "gäte"):
    try:
        hostname.validate(raw)
        check("refuses %r" % raw, False, "accepted")
    except hostname.HostnameError as exc:
        check("refuses %r with a sentence" % raw[:20], len(str(exc)) > 10, exc)

# -- 3. the name a browser types ------------------------------------------------------
real_current = hostname.current
hostname.current = lambda: "iriv"
check("no mDNS answer yet: <hostname>.local", hostname.local_name() == "iriv.local")
discovery_mdns._publisher = types.SimpleNamespace(own_host="iriv-2.local", learn_due=False)
check("avahi won iriv-2 for this box: the panel must say iriv-2.local",
      hostname.local_name() == "iriv-2.local")
st = hostname.status()
check("status flags a shared factory name and offers the default",
      st["shared_name"] and st["default"] == "iriv-b2ca6353" and not st["is_default"], st)

# -- 4. renaming --------------------------------------------------------------------
real_can, real_run = hostname.can_set, hostname.subprocess.run
hostname.can_set = lambda: False
try:
    hostname.set_name("gate-a")
    check("no helper: refused", False, "renamed")
except hostname.HostnameError as exc:
    check("no helper: refused in plain words, no command",
          "one-time update by the installer" in str(exc) and "sudo" not in str(exc), exc)

ran = []


def fake_run(argv, **kw):
    ran.append((list(argv), kw.get("input")))
    hostname.current = lambda: "gate-a"
    return types.SimpleNamespace(returncode=0, stdout="gate-a\n", stderr="")


hostname.can_set = lambda: True
hostname.subprocess.run = fake_run
reloaded = []
real_reload = discovery_mdns._reload_panel
discovery_mdns._reload_panel = lambda: reloaded.append(1)
try:
    out = hostname.set_name("Gate-A")
    check("the helper runs as HELPER_ARGV with the name on stdin, not argv",
          ran == [(list(hostname.HELPER_ARGV), "gate-a\n")], ran)
    check("...the argv is the sudoers line's exactly",
          hostname.HELPER_ARGV == ("sudo", "-n", "/usr/bin/python3", "-I",
                                   "/usr/local/lib/irivai/set-hostname.py"))
    check("...the old mDNS name is dropped until avahi re-announces",
          discovery_mdns._publisher.own_host == "" and discovery_mdns._publisher.learn_due)
    check("...the front panel is reloaded", reloaded == [1])
    check("...and the answer is the new name", out["hostname"] == "gate-a"
          and out["local_name"] == "gate-a.local", out)
    ran.clear()
    hostname.set_name("gate-a")
    check("the same name again runs nothing", ran == [])
    hostname.subprocess.run = lambda argv, **kw: types.SimpleNamespace(
        returncode=2, stdout="", stderr="trace\nset-hostname: invalid name\n")
    try:
        hostname.set_name("gate-b")
        check("a failing helper is reported", False)
    except hostname.HostnameError as exc:
        check("a failing helper is reported with its last line",
              "invalid name" in str(exc), exc)
finally:
    hostname.can_set, hostname.subprocess.run = real_can, real_run
    discovery_mdns._reload_panel = real_reload
    hostname.current = real_current

# -- 5. the front panel ---------------------------------------------------------------
from core.services import panel                                  # noqa: E402
hostname.local_name = lambda: "iriv-b2ca6353.local"
cfg_dir = tempfile.mkdtemp()
url = panel.settings(cfg_dir)["dashboard_url"]
check("the panel prints this box's own name and the hub port",
      url.startswith("iriv-b2ca6353.local:") and url.split(":")[1].isdigit(), url)

# -- 6. the route ------------------------------------------------------------------
src = open(os.path.join(ROOT, "core", "routes", "hostname.py"), encoding="utf-8").read()
check("the route is master/developer only, read and write",
      "auth_service.is_admin" in src
      and src.count("    _require_owner(request)" + chr(10)) == 2,
      src.count("_require_owner"))
check("...and asks the service whether the page rides on the .local name",
      "hostname.reached_by_name(net.request_host(request)" in src)
hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
check("the hub mounts it", "app.include_router(hostname_router)" in hub)
hostname.local_name = lambda: "iriv-2.local"
hostname.current = lambda: "iriv"
hostname.can_set = lambda: False
check("page opened as iriv-2.local: a rename drops it",
      hostname.reached_by_name("IRIV-2.local") is True)
check("page opened by IP: a rename does not touch it",
      hostname.reached_by_name("192.168.1.21") is False)
check("page opened as another box's name: not ours",
      hostname.reached_by_name("iriv-3.local") is False)

print("FAIL" if bad else "OK")
sys.exit(1 if bad else 0)
