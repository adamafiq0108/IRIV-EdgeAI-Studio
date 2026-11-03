# -*- coding: utf-8 -*-
"""
smoke_wifitrial.py -- Search Wi-Fi, Test connection and Test and switch.

Tok Aba (2026-10-08): from the access point, search for networks without
taking it down, and test a network and password before the box is left on
it, with the precise reason when it fails. Pinned here with nmcli, the
journal and the root helper faked, on any machine:

  1. the helper (scripts/wifi_profile.py) takes kinds trial and scan; a
     trial is the join's profile in a strict form (autoconnect off, one key
     attempt, a lease required, a bounded DHCP wait)
  2. classify(): NetworkManager's sentence and the NM/wpa_supplicant journal
     map to wrong_password, assoc_failed, not_found, no_address (none, or
     169.254 only), timeout, failed
  3. trial(): kept when it passes with keep (ordinary join profile, loaded
     without a bounce), dropped otherwise -- down, deleted, keyfile gone --
     even when it raises; the internet check is reported, never a failure
  4. scan(): a fresh scan as root, parsed; a refused rescan says not fresh
  5. the hub: helper_can reads the installed helper's KINDS; search() shapes
     one row per SSID and falls back to the old list (not fresh)
  6. Test connection (wifitest.start): AP down, trial with keep off, AP back,
     the outcome in words for every reason; refused off the AP, with an old
     helper, and while a switch runs; the address is remembered for the
     switch that follows
  7. Test and switch (the ordinary switch with a new network): a kept trial;
     passed, the box stays (mode wifi, no AP raised); failed, the hotspot is
     NOT raised (Tok Aba, 2026-10-14), the plain "hotspot is off" message,
     mode wifi; an old helper keeps the old join. Test connection alone puts
     the hotspot back: the person never left it
  8. no key on any argv or in any state the page reads
"""

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


spec = importlib.util.spec_from_file_location(
    "wifi_profile", os.path.join(ROOT, "scripts", "wifi_profile.py"))
wp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wp)

# -- 1. the request and the strict profile ------------------------------------
t = wp.validate({"kind": "trial", "ssid": "Plant", "psk": "goodpass1", "keep": False,
                 "ifname": "eth0"})
check("a trial validates like a join, keep is a real bool, nothing else kept",
      t == {"kind": "trial", "ssid": "Plant", "psk": "goodpass1", "hidden": False,
            "autoconnect": True, "keep": False}, repr(t))
check("scan carries nothing", wp.validate({"kind": "scan", "ssid": "x"}) == {"kind": "scan"})
for req in ({"kind": "trial", "ssid": "a", "psk": "short"},
            {"kind": "trial", "ssid": "a", "keep": "yes"},
            {"kind": "probe", "ssid": "a"}):
    try:
        wp.validate(req)
        check(f"refused: {req}", False)
    except wp.Refused:
        check(f"refused: {req}", True)
tstem = wp.profile(t, strict=True)[1]
check("a test-only trial has a profile of its own (a saved network is never touched)",
      tstem == "irivai-trial" and wp.profile(t, strict=True)[2] != wp.profile(
          dict(t, keep=True), strict=True)[2])
t = dict(t, keep=True)
name, stem, uid, strict = wp.profile(t, strict=True)
_, jstem, juid, plain = wp.profile(wp.validate({"kind": "join", "ssid": "Plant",
                                                "psk": "goodpass1"}))
check("the trial IS the join's profile (same name, file, uuid: it replaces a saved one)",
      (stem, uid) == (jstem, juid) and name == "Plant")
check("...strict while tried: autoconnect off, one key attempt, a lease required",
      "autoconnect=false" in strict and "auth-retries=1" in strict
      and "may-fail=false" in strict and "dhcp-timeout=15" in strict, strict)
check("...and the kept form is the ordinary join (autoconnect on, no trial limits)",
      wp.profile(t)[3] == plain and "autoconnect=true" in plain and "auth-retries" not in plain)
check("the helper names its kinds on one line the hub reads",
      'KINDS = ("ap", "join", "trial", "scan", "sweep")' in open(
          os.path.join(ROOT, "scripts", "wifi_profile.py"), encoding="utf-8").read())

# Pipi F2: a 64-hex key with a trailing newline is not 64 hex digits.
for bad_key in ("a" * 64 + "\n", "A" * 63 + "\n"):
    try:
        wp.validate({"kind": "trial", "ssid": "a", "psk": bad_key})
        check(f"a key with a trailing newline is refused ({len(bad_key)})", False)
    except wp.Refused:
        check(f"a key with a trailing newline is refused ({len(bad_key)})", True)
check("...while 64 hex digits alone are taken",
      wp.validate({"kind": "join", "ssid": "a", "psk": "a" * 64})["psk"] == "a" * 64)

# Pipi F1: only Wi-Fi profiles, never the AP's, are "others named" an SSID.
AP_UUID = str(wp.uuid.uuid5(wp.NAMESPACE, wp.AP_FILE))
LISTING = ("Wired connection 1:wired-uuid:802-3-ethernet\n"
           "Hotspot:%s:802-11-wireless\n"
           "Hotspot:old-nmcli-hotspot:802-11-wireless\n"
           "Plant:plant-old:802-11-wireless\n" % AP_UUID)


class _Done:
    stdout, returncode, stderr = LISTING, 0, ""


real_sub_run = wp.subprocess.run
wp.subprocess.run = lambda argv, **kw: _Done()
check("an SSID named like the cable's profile deletes nothing",
      wp._others_named("Wired connection 1", "new-uuid") == [])
check("an SSID named Hotspot never takes the box's own AP profile",
      wp._others_named("Hotspot", "new-uuid") == ["old-nmcli-hotspot"])
check("...the AP request still clears the old nmcli-made Hotspot (Wi-Fi, not ours)",
      wp._others_named("Hotspot", AP_UUID) == ["old-nmcli-hotspot"])
check("...and an SSID's own older Wi-Fi profile is still found",
      wp._others_named("Plant", "new-uuid") == ["plant-old"])
wp.subprocess.run = real_sub_run

# Pipi handoff: only wlan0's (or the trial profile's) journal lines count.
MIXED = ("dhcp4 (eth0): state changed new lease, address=192.168.1.21\n"
         "device (eth0): DHCP transaction started\n"
         "wlan0: Authentication with 50:3d:d1:1b:7f:3d timed out.\n")
real_out = wp._out
wp._out = lambda argv, timeout=20: MIXED
lines = wp._journal(0, "trial-uuid")
check("the journal read keeps wlan0's lines and drops eth0's",
      "eth0" not in lines and "Authentication with" in lines, lines)
wp._out = lambda argv, timeout=20: ("dhcp4 (eth0): lease\n"
                                    "policy: auto-activating connection 'Plant' (trial-uuid)\n")
lines = wp._journal(0, "trial-uuid")
check("...and the trial profile's own lines", "trial-uuid" in lines and "eth0" not in lines)
check("eth0's DHCP lines no longer turn a Wi-Fi timeout into 'no address'",
      wp.classify("Timeout expired (25 seconds)",
                  "\n".join(x for x in MIXED.splitlines()[:2] if "wlan0" in x)) == "timeout"
      and wp.classify("Timeout expired (25 seconds)", MIXED.splitlines()[0]) == "no_address")
wp._out = real_out

# -- 2. classify ----------------------------------------------------------------
J_WRONG = ("wlan0: WPA: 4-Way Handshake failed - pre-shared key may be incorrect\n"
           "wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid=\"Plant\" auth_failures=1 "
           "duration=10 reason=WRONG_KEY\n")
J_NM_SECRETS = "device (wlan0): state change: need-auth -> failed (reason 'no-secrets', managed-type: 'full')\n"
J_ASSOC = ("wlan0: CTRL-EVENT-ASSOC-REJECT bssid=50:3d:d1:1b:7f:3d status_code=17\n"
           "device (wlan0): state change: config -> failed (reason 'supplicant-timeout')\n")
J_AUTH_TO = "wlan0: Authentication with 50:3d:d1:1b:7f:3d timed out.\n"
J_NOTFOUND = "wlan0: CTRL-EVENT-NETWORK-NOT-FOUND\n"
J_DHCP = "device (wlan0): state change: ip-config -> failed (reason 'ip-config-unavailable')\n"
cases = (
    ("Connection activation failed: (7) Secrets were required, but not provided", "",
     "", "wrong_password"),
    ("Connection activation failed: the base network connection was interrupted", J_WRONG,
     "", "wrong_password"),
    ("Connection activation failed: (8) supplicant disconnected", J_NM_SECRETS, "",
     "wrong_password"),
    ("Connection activation failed: (11) 802.1X supplicant took too long", J_ASSOC, "",
     "assoc_failed"),
    ("Timeout expired (25 seconds)", J_AUTH_TO, "", "assoc_failed"),
    ("Connection activation failed: (53) The Wi-Fi network could not be found", J_ASSOC,
     "", "not_found"),
    ("Timeout expired (25 seconds)", J_NOTFOUND, "", "not_found"),
    ("Connection activation failed: IP configuration could not be reserved "
     "(no available address, timeout, etc.)", "", "", "no_address"),
    ("Timeout expired (25 seconds)", J_DHCP, "", "no_address"),
    ("", "", "", "no_address"),
    ("", "", "169.254.7.9", "no_address"),
    ("Timeout expired (25 seconds)", "", "", "timeout"),
    ("Connection activation failed: unknown", "", "", "failed"),
    ("Timeout expired (25 seconds)", J_WRONG + J_ASSOC, "", "wrong_password"),
)
for nm, journal, addr, want in cases:
    got = wp.classify(nm, journal, addr)
    check(f"classify -> {want}: {nm[:48]!r} + {journal[:30]!r}", got == want, got)

# -- 3. trial() against a fake nmcli -----------------------------------------------
conn = tempfile.mkdtemp()
wp.CONN_DIR = conn
runs, world = [], {}


def fake_run(argv, timeout=90):
    runs.append(tuple(argv))
    if "up" in argv:
        if world.get("raise"):
            raise wp.subprocess.TimeoutExpired(argv, timeout)
        return world.get("up_rc", 0), world.get("up_err", "")
    if argv[:3] == ["nmcli", "connection", "load"]:
        world.setdefault("loaded", []).append(open(argv[3], encoding="utf-8").read())
    return 0, ""


def fake_out(argv, timeout=20):
    runs.append(tuple(argv))
    if argv[0] == "journalctl":
        return world.get("journal", "")
    if "IP4.ADDRESS,IP4.GATEWAY" in argv:
        return world.get("ip", "192.168.1.57/24\n192.168.1.1\n")
    return ""


wp._run, wp._out = fake_run, fake_out
wp._others_named = lambda name, keep: []
wp._pings = lambda host: world.get("pings", {}).get(host, True) if host else None
path = os.path.join(conn, stem + ".nmconnection")


OLD_SAVED = "[connection]\nid=Plant\nuuid=%s\nautoconnect=true\n# the old key\n" % uid
others = []
wp._others_named = lambda name, keep: list(others)


def go(keep=True, before=None, other=(), **w):
    """One trial. before: the saved profile already in the SSID's file
    (None: nothing saved); other: uuids of other profiles named for it."""
    runs.clear()
    world.clear()
    world.update(w)
    others[:] = list(other)
    for f in os.listdir(conn):
        os.unlink(os.path.join(conn, f))
    if before is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(before)
    return wp.trial(dict(t, keep=keep))


def deleted():
    return [r[4] for r in runs if r[:4] == ("nmcli", "connection", "delete", "uuid")]


res = go(keep=True)
check("a trial that passes with keep: ok, the address, the internet answered",
      res["ok"] and res["address"] == "192.168.1.57" and res["internet"] is True
      and res["gateway"] is True, repr(res))
check("...up with a bounded wait, by uuid",
      ("nmcli", "--wait", str(wp.TRIAL_WAIT_S), "connection", "up", "uuid", uid) in runs)
check("...the strict form loaded first, then the ordinary one under the live connection",
      len(world["loaded"]) == 2 and "auth-retries=1" in world["loaded"][0]
      and world["loaded"][1] == plain, repr(world.get("loaded")))
check("...kept: never taken down or deleted, the keyfile stays (ordinary form)",
      not [r for r in runs if r[:3] in (("nmcli", "connection", "down"),
                                        ("nmcli", "connection", "delete"))]
      and open(path, encoding="utf-8").read() == plain
      and os.listdir(conn) == [os.path.basename(path)], repr((runs, os.listdir(conn))))
res = go(keep=False, before=OLD_SAVED, other=["older-plant-uuid"])
_, _, tuid, _ = wp.profile(dict(t, keep=False), strict=True)
tpath = os.path.join(conn, "irivai-trial.nmconnection")
check("Test connection (keep off): passes, then its own profile is down, deleted, gone",
      res["ok"] and res["address"] == "192.168.1.57" and not os.path.exists(tpath)
      and ("nmcli", "--wait", str(wp.TRIAL_WAIT_S), "connection", "up", "uuid", tuid) in runs
      and deleted() == [tuid], repr(runs))
check("...and the saved profiles for that network are left exactly as they were",
      open(path, encoding="utf-8").read() == OLD_SAVED, repr(runs))
res = go(keep=True, pings={"1.1.1.1": False, "8.8.8.8": False})
check("no internet through it is reported, not a failure",
      res["ok"] and res["internet"] is False and res["gateway"] is True, repr(res))
res = go(keep=True, up_rc=4, up_err="Error: Connection activation failed: (7) Secrets "
         "were required, but not provided", journal=J_WRONG)
check("a wrong key, nothing saved before: wrong_password, the new profile deleted",
      not res["ok"] and res["reason"] == "wrong_password" and "Secrets" in res["message"]
      and not os.listdir(conn) and deleted() == [uid], repr((res, os.listdir(conn))))

# Pipi (2026-10-08): a failed attempt must not cost the network saved before.
res = go(keep=True, before=OLD_SAVED, other=["older-plant-uuid"], up_rc=4,
         up_err="Error: Connection activation failed: (7) Secrets were required, but not provided")
check("a FAILED Test and switch puts the saved profile back exactly as it was",
      not res["ok"] and open(path, encoding="utf-8").read() == OLD_SAVED
      and world["loaded"][-1] == OLD_SAVED and uid not in deleted(), repr(runs))
check("...the other saved profile for that SSID is never deleted, and no copy is left",
      "older-plant-uuid" not in deleted() and os.listdir(conn) == [os.path.basename(path)],
      repr((deleted(), os.listdir(conn))))
res = go(keep=True, before=OLD_SAVED, other=["older-plant-uuid"])
check("a PASSING Test and switch replaces them only now: others deleted after the join",
      res["ok"] and deleted() == ["older-plant-uuid"]
      and runs.index(("nmcli", "--wait", str(wp.TRIAL_WAIT_S), "connection", "up", "uuid", uid))
      < [i for i, r in enumerate(runs) if r[:3] == ("nmcli", "connection", "delete")][0]
      and open(path, encoding="utf-8").read() == plain
      and os.listdir(conn) == [os.path.basename(path)], repr((runs, os.listdir(conn))))
res = go(keep=True, ip="169.254.7.9/16\n\n")
check("joined with only a link-local address: no_address, nothing kept",
      not res["ok"] and res["reason"] == "no_address" and not os.listdir(conn), repr(res))
res = go(keep=True, up_rc=3, up_err="Error: Timeout expired (25 seconds)", journal=J_ASSOC)
check("rejected by the router: assoc_failed", res["reason"] == "assoc_failed", repr(res))
try:
    go(keep=True, before=OLD_SAVED, **{"raise": True})
    check("an exception mid-trial propagates", False)
except wp.subprocess.TimeoutExpired:
    check("an exception mid-trial still puts the saved profile back (finally)",
          open(path, encoding="utf-8").read() == OLD_SAVED
          and os.listdir(conn) == [os.path.basename(path)], repr(runs))
try:
    go(keep=True, **{"raise": True})
except wp.subprocess.TimeoutExpired:
    check("...and with nothing saved, deletes the new one", not os.listdir(conn)
          and uid in deleted(), repr(runs))

# -- 3b. a plain join keeps the old profile until it has joined --------------------------
J = wp.validate({"kind": "join", "ssid": "Plant", "psk": "goodpass1"})


def join(before=None, other=(), up_rc=0):
    runs.clear()
    world.clear()
    world["up_rc"] = up_rc
    world["up_err"] = "Error: Secrets were required, but not provided."
    others[:] = list(other)
    for f in os.listdir(conn):
        os.unlink(os.path.join(conn, f))
    if before is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(before)
    return wp.apply(J)


res = join(before=OLD_SAVED, other=["older-plant-uuid"], up_rc=4)
check("a failed join puts the saved profile back and deletes no other",
      not res["ok"] and open(path, encoding="utf-8").read() == OLD_SAVED
      and not deleted() and os.listdir(conn) == [os.path.basename(path)], repr(runs))
res = join(other=["older-plant-uuid"], up_rc=4)
check("a failed join with nothing saved before leaves no profile",
      not res["ok"] and not os.listdir(conn) and deleted() == [uid], repr(runs))
res = join(before=OLD_SAVED, other=["older-plant-uuid"])
check("a join that works replaces the old ones only after it is up",
      res["ok"] and deleted() == ["older-plant-uuid"] and open(path).read() == plain
      and os.listdir(conn) == [os.path.basename(path)], repr(runs))

# -- 3c. sweep: what a killed helper left -------------------------------------------------
live = set()
wp._active_uuids = lambda: set(live)


def leave(files, active=()):
    runs.clear()
    world.clear()
    live.clear()
    live.update(active)
    for f in os.listdir(conn):
        os.unlink(os.path.join(conn, f))
    for name_, text_ in files.items():
        with open(os.path.join(conn, name_), "w", encoding="utf-8") as fh:
            fh.write(text_)
    return wp.sweep()


base_ = os.path.basename(path)
did = leave({"irivai-trial.nmconnection": strict})
check("sweep: a leftover test profile is deleted, keyfile and all",
      did == ["test"] and not os.listdir(conn) and deleted() == [tuid], repr((did, runs)))
did = leave({base_: strict, wp.KEEP_PREFIX + stem: OLD_SAVED})
check("sweep: a killed Test and switch, not joined: the saved profile is put back",
      did == ["put back"] and open(path).read() == OLD_SAVED
      and os.listdir(conn) == [base_], repr((did, os.listdir(conn))))
did = leave({base_: strict})
check("sweep: a killed trial with nothing saved before: deleted",
      did == ["put back"] and not os.listdir(conn) and uid in deleted(), repr(did))
did = leave({base_: strict, wp.KEEP_PREFIX + stem: OLD_SAVED}, active=[uid])
check("sweep: killed AFTER it joined (wlan0 live on it): finished, not dropped",
      did == ["finished"] and open(path).read() == plain and os.listdir(conn) == [base_]
      and not deleted() and not [r for r in runs if r[:3] == ("nmcli", "connection", "down")],
      repr((did, runs)))
did = leave({base_: plain, wp.KEEP_PREFIX + stem: OLD_SAVED, ".irivai-x1y2": "half"})
check("sweep (Pipi F4): a join cut short, not live: the saved copy is put back, temp gone",
      sorted(did) == ["put back", "temp"] and os.listdir(conn) == [base_]
      and open(path).read() == OLD_SAVED, repr((did, os.listdir(conn))))
did = leave({base_: plain, wp.KEEP_PREFIX + stem: OLD_SAVED}, active=[uid])
check("...but when wlan0 is live on the new profile it stays, and the copy goes",
      did == ["copy"] and os.listdir(conn) == [base_] and open(path).read() == plain
      and not runs, repr((did, runs)))
did = leave({wp.KEEP_PREFIX + stem: OLD_SAVED})
check("sweep: a copy whose profile file is gone is put back",
      did == ["put back"] and open(path).read() == OLD_SAVED and os.listdir(conn) == [base_])
did = leave({base_: plain, "irivai-wifi-4f6666696365.nmconnection": "[connection]\n"})
check("sweep: ordinary saved profiles are never touched",
      did == [] and not runs, repr((did, runs)))

# -- 3d. every run is locked and sweeps first ----------------------------------------------
order = []
real_lock, real_sweep, real_scan = wp._lock, wp.sweep, wp.scan
wp._lock = lambda: order.append("lock") or os.open(os.devnull, os.O_RDONLY)
wp.sweep = lambda: order.append("sweep") or ["test"]
wp.scan = lambda: order.append("scan") or {"ok": True}
out = wp.run({"kind": "scan"})
check("a run takes the lock, sweeps, then does its kind",
      order == ["lock", "sweep", "scan"], repr(order))
order.clear()
out = wp.run({"kind": "sweep"})
check("kind sweep answers what it cleared", out == {"ok": True, "message": "swept",
                                                    "swept": ["test"]}, repr(out))
wp._lock, wp.sweep, wp.scan = real_lock, real_sweep, real_scan
if os.name == "posix":
    import fcntl                                              # noqa: E402
    wp.LOCK = os.path.join(conn, "lock")
    wp.LOCK_WAIT_S = 0.6
    held = os.open(wp.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        wp._lock()
        check("a second run waits, then refuses, while one holds the lock", False)
    except wp.Refused as exc:
        check("a second run waits, then refuses, while one holds the lock",
              "still running" in str(exc))
    fcntl.flock(held, fcntl.LOCK_UN)
    os.close(wp._lock())
    os.close(held)
    os.unlink(wp.LOCK)
check("no key on any argv in any of it", not any("goodpass1" in " ".join(r) for r in runs))

# -- 4. scan() ---------------------------------------------------------------------
LIST = ("*:Plant:80:WPA2:6\n:Plant:55:WPA2:44\n:Guest\\:Net:40::11\n::30:WPA2:1\n"
        ":Corp:70:WPA2 802.1X:36\n")
scan_rc = {"rc": 0}


def scan_run(argv, timeout=90):
    runs.append(tuple(argv))
    return scan_rc["rc"], ""


def scan_out(argv, timeout=20):
    runs.append(tuple(argv))
    return LIST


wp._run, wp._out = scan_run, scan_out
runs.clear()
ans = wp.scan()
check("scan asks NetworkManager for a rescan of wlan0, then reads the list",
      runs[0] == ("nmcli", "device", "wifi", "rescan", "ifname", "wlan0")
      and runs[1][-2:] == ("--rescan", "yes") and ans["fresh"] is True, repr(runs))
check("...parsed: an escaped colon kept, a hidden row dropped",
      [n["ssid"] for n in ans["networks"]] == ["Plant", "Plant", "Guest:Net", "Corp"]
      and ans["networks"][0]["in_use"] and ans["networks"][0]["chan"] == "6", repr(ans))
scan_rc["rc"] = 1
runs.clear()
ans = wp.scan()
check("a refused rescan says not fresh (the old list, read without waiting)",
      ans["fresh"] is False and runs[1][-2:] == ("--rescan", "no"))

# -- 5. the hub: helper_can, search -------------------------------------------------
from core import config                                       # noqa: E402
from core.services import netconf, wifiscan, wifiswitch, wifitest  # noqa: E402

fake_helper_file = os.path.join(tempfile.mkdtemp(), "wifi-profile.py")
netconf.WIFI_HELPER = fake_helper_file
with open(fake_helper_file, "w", encoding="utf-8") as fh:
    fh.write('def validate(req):\n    if kind not in ("ap", "join"):\n        pass\n')
check("an older helper (no KINDS line) can neither trial nor scan",
      not wifiscan.helper_can("trial") and not wifiscan.helper_can("scan"))
with open(fake_helper_file, "w", encoding="utf-8") as fh:
    fh.write('KINDS = ("ap", "join", "trial", "scan", "sweep")\n')
check("the new helper can", wifiscan.helper_can("trial") and wifiscan.helper_can("scan"))
netconf.wifi_helper_installed = lambda: True
sent = []


def hub_helper(request, timeout=60):
    sent.append(dict(request))
    if request["kind"] == "sweep":
        return {"ok": True, "swept": ["test"]}
    if request["kind"] == "scan":
        return {"ok": True, "fresh": True, "networks": ans["networks"]}
    return plan_trial(request)


wifiscan._helper = hub_helper
nets, fresh = wifiscan.search()
check("search: one row per SSID (the strongest), enterprise marked, fresh",
      fresh and [n["ssid"] for n in nets] == ["Plant", "Corp", "Guest:Net"]
      and nets[0]["signal"] == 80 and nets[1]["enterprise"] and not nets[2]["secure"],
      repr(nets))
netconf.wifi_list = lambda rescan=False: [{"ssid": "Old", "signal": 1}]
with open(fake_helper_file, "w", encoding="utf-8") as fh:
    fh.write("old helper\n")
check("an older helper: the list NetworkManager had, said to be not fresh",
      wifiscan.search() == ([{"ssid": "Old", "signal": 1}], False))
with open(fake_helper_file, "w", encoding="utf-8") as fh:
    fh.write('KINDS = ("ap", "join", "trial", "scan", "sweep")\n')

# -- 5b. the hub sweeps a trial leftover at start ------------------------------------
check("the hub knows the helper's test-profile uuid",
      wifiscan.TEST_UUID == str(wp.uuid.uuid5(wp.NAMESPACE, wp.TEST_FILE)))
profiles = {}
real_nmcli = netconf._nmcli


def list_nmcli(args, sudo=False, timeout=45):
    a = tuple(args)
    if a == ("-t", "-f", "UUID,TYPE", "connection", "show"):
        return "".join(f"{u}:802-11-wireless\n" for u in profiles)
    if a[:3] == ("-g", "connection.auth-retries", "connection"):
        return profiles[a[-1]] + "\n"
    return ""


netconf._nmcli = list_nmcli
for label, have, want in (("nothing left", {"a-uuid": "-1", "b-uuid": "0"}, False),
                          ("the test profile", {wifiscan.TEST_UUID: "1"}, True),
                          ("a strict trial profile", {"a-uuid": "-1", "c-uuid": "1"}, True)):
    profiles.clear()
    profiles.update(have)
    sent.clear()
    swept = wifiscan.sweep_at_start()
    check(f"start: {label} -> {'one sweep' if want else 'no helper run at all'}",
          wifiscan.leftover() is want
          and ([r["kind"] for r in sent] == (["sweep"] if want else [])), repr(sent))
netconf._nmcli = real_nmcli

# -- 6/7. the moves, against a fake wlan0 -------------------------------------------
AP = "Hotspot"
box = {"conn": AP}
calls = []
outcome = {}


def plan_trial(request):
    box["conn"] = request["ssid"] if outcome.get("ok") else ""
    return dict(outcome)


def fake_nmcli(args, sudo=False, timeout=45):
    a = tuple(args)
    calls.append((a, sudo))
    if a[:3] == ("-t", "-f", "GENERAL.CONNECTION"):
        return "GENERAL.CONNECTION:%s\n" % box["conn"]
    if a[:3] == ("-t", "-f", "802-11-wireless.mode"):
        return "802-11-wireless.mode:%s\n" % ("ap" if a[-1] == AP else "infrastructure")
    if a[:2] == ("connection", "down") and box["conn"] == a[2]:
        box["conn"] = ""
    if a[:3] == ("-t", "-f", "NAME,TYPE,ACTIVE,DEVICE"):
        return ""
    return ""


ap_raised = []


def fake_ap_enable(ssid, password):
    ap_raised.append((ssid, password))
    box["conn"] = AP
    return {}


netconf._nmcli = fake_nmcli
netconf.ap_enable = fake_ap_enable
joins = []
netconf.join = lambda *a, **k: joins.append(a) or {}
wifiswitch.START_DELAY_S = 0.05
wifiswitch._after = lambda: None
wifiswitch._panel_note = lambda text: None
CFG = tempfile.mkdtemp()


def fresh_box(mode="ap"):
    config.save_local("network", {"ap": {"mode": mode, "password": "boxkey2345",
                                         "ssid": "Line AP"}}, CFG)
    box["conn"] = AP
    calls.clear()
    sent.clear()
    ap_raised.clear()
    joins.clear()
    with wifiswitch._lock:
        wifiswitch._state.clear()
        wifiswitch._state.update(state="idle")


def wait():
    for _ in range(200):
        if not wifiswitch.busy():
            break
        time.sleep(0.02)
    for th in [x for x in threading.enumerate() if x.name == "wifi-switch"]:
        th.join(5)
    return wifiswitch.state()


fresh_box()
outcome.clear()
outcome.update(ok=True, address="192.168.1.57", gateway=True, internet=True)
first = wifitest.start("Plant", "goodpass1", config_dir=CFG)
check("Test connection answers at once, holding the switch state (others wait)",
      first["state"] == "switching" and first["to"] == "test" and first["test"])
done = wait()
check("...AP down first, the trial with keep OFF, key on stdin only",
      [a for a, sudo in calls if sudo][0] == ("connection", "down", AP) and sent[-1] == {
          "kind": "trial", "ssid": "Plant", "psk": "goodpass1", "hidden": False,
          "keep": False}, repr((calls[:2], sent)))
check("...passed: 'Wi-Fi connection is ready' with the address, the AP is back",
      done["state"] == "done" and "Wi-Fi connection is ready" in done["message"]
      and "192.168.1.57" in done["message"] and done["tested_address"] == "192.168.1.57"
      and ap_raised == [("Line AP", "boxkey2345")] and box["conn"] == AP, repr(done))
check("...nothing saved and the mode untouched", netconf.wifi_mode(CFG) == "ap")
cands = wifiswitch.candidates("wifi", ssid="Plant")
check("...and the switch that follows predicts the address the test was given",
      any(c["host"] == "192.168.1.57" and not c["own"] for c in cands), repr(cands))

for word, words in (("wrong_password", "the password was not accepted"),
                    ("not_found", "the network was not found"),
                    ("assoc_failed", "could not connect to it (weak signal"),
                    ("no_address", "no address (DHCP)"),
                    ("timeout", "did not finish connecting in time")):
    fresh_box()
    outcome.clear()
    outcome.update(ok=False, reason=word, message="nm said so")
    wifitest.start("Plant", "goodpass1", config_dir=CFG)
    done = wait()
    check(f"test fails as {word}: said in words, AP back, nothing saved",
          done["state"] == "failed" and done["reason"] == word and words in done["message"]
          and "Line AP is back" in done["message"] and box["conn"] == AP
          and netconf.wifi_mode(CFG) == "ap", repr(done))
fresh_box()
outcome.clear()
outcome.update(ok=False, reason="failed", message="the Wi-Fi helper did not answer in time")
wifitest.start("Plant", "goodpass1", config_dir=CFG)
check("a helper that ran out of time reads as a timeout", wait()["reason"] == "timeout")
fresh_box()
outcome.clear()
outcome.update(ok=True, address="192.168.1.57", gateway=True, internet=False)
wifitest.start("Plant", "goodpass1", config_dir=CFG)
check("no internet through it: still ready, and said",
      "internet did not answer" in wait()["message"])

for label, prep, words in (
        ("off the access point", lambda: box.update(conn="Office"), "broadcasting its access"),
        ("with an old helper", lambda: open(fake_helper_file, "w").write("old\n"),
         "Switch without testing"),
        ("while a switch runs", lambda: wifiswitch._state.update(state="switching"),
         "already under way"),
        ("with a short password", lambda: None, "8-63")):
    fresh_box()
    prep()
    try:
        wifitest.start("Plant", "short" if "short" in label else "goodpass1", config_dir=CFG)
        check(f"refused {label}", False)
    except netconf.NetError as exc:
        check(f"refused {label}, nothing moved", words in str(exc) and not sent
              and not [c for c in calls if c[1]], str(exc))
    with open(fake_helper_file, "w", encoding="utf-8") as fh:
        fh.write('KINDS = ("ap", "join", "trial", "scan", "sweep")\n')

# Test and switch: the ordinary switch with a typed network.
fresh_box()
outcome.clear()
outcome.update(ok=True, address="192.168.1.57", gateway=True, internet=True)
wifiswitch.start("wifi", ssid="Plant", password="goodpass1", config_dir=CFG)
done = wait()
check("Test and switch passed: a KEPT trial, the box stays, mode wifi, no AP raised",
      sent[-1]["keep"] is True and done["state"] == "done" and done["address"] == "192.168.1.57"
      and netconf.wifi_mode(CFG) == "wifi" and not ap_raised and box["conn"] == "Plant",
      repr((done, sent)))
fresh_box()
outcome.clear()
outcome.update(ok=False, reason="wrong_password", message="Secrets were required")
_real_set_mode = netconf.set_wifi_mode


def _spy_set_mode(mode, *a, **k):
    calls.append((("MODE", mode), True))
    return _real_set_mode(mode, *a, **k)


netconf.set_wifi_mode = _spy_set_mode
wifiswitch.start("wifi", ssid="Plant", password="badpass12", config_dir=CFG)
done = wait()
netconf.set_wifi_mode = _real_set_mode
check("Test and switch failed: the hotspot is NOT raised, the reason, mode wifi",
      done["state"] == "failed" and done["reason"] == "wrong_password"
      and not ap_raised and box["conn"] != AP and netconf.wifi_mode(CFG) == "wifi"
      and done.get("hotspot_off") is True and done.get("test") is False, repr(done))
check("...and the page is told plainly how to get back in",
      done["message"] == "Could not connect to Plant: the password was not accepted. "
      "The hotspot is off. Turn the hotspot on to reach this device without Wi-Fi.",
      done["message"])
check("...the mode was set to wifi BEFORE the hotspot went down",
      [a for a, sudo in calls if sudo][:2] == [("MODE", "wifi"), ("connection", "down", AP)],
      repr(calls))
fresh_box()
with open(fake_helper_file, "w", encoding="utf-8") as fh:
    fh.write("old\n")
wifiswitch.JOIN_BUDGET_S = 0.3
wifiswitch.start("wifi", ssid="Plant", password="goodpass1", config_dir=CFG)
wait()
check("an older helper: Switch without testing is the old join (no trial)",
      joins and joins[0][0] == "Plant" and not [s for s in sent if s["kind"] == "trial"],
      repr((joins, sent)))

# -- 7b. one Wi-Fi job at a time; the key checked before the AP goes down (Pipi F3) -----
check("the hub's helper limit exceeds the helper's lock wait plus a trial at its limits",
      wifiscan.HELPER_TIMEOUT_S >= wp.LOCK_WAIT_S + 180
      and netconf.WIFI_HELPER_ARGV and "timeout=300" in open(
          os.path.join(ROOT, "core", "services", "netconf.py"), encoding="utf-8").read())
fresh_box()
with open(fake_helper_file, "w", encoding="utf-8") as fh:
    fh.write('KINDS = ("ap", "join", "trial", "scan", "sweep")\n')
wifiswitch.searching = True
for label, call in (("Test connection", lambda: wifitest.start("Plant", "goodpass1",
                                                               config_dir=CFG)),
                    ("Test and switch", lambda: wifiswitch.start(
                        "wifi", ssid="Plant", password="goodpass1", config_dir=CFG))):
    try:
        call()
        check(f"{label} refused while a search runs", False)
    except netconf.NetError as exc:
        check(f"{label} refused while a search runs, nothing moved",
              "search is already under way" in str(exc) and not sent
              and not [c for c in calls if c[1]], str(exc))
check("...and other network writes wait too (busy)", wifiswitch.busy())
try:
    wifiscan.search()
    check("a second search is refused while one runs", False)
except netconf.NetError as exc:
    check("a second search is refused while one runs", "busy" in str(exc))
wifiswitch.searching = False
wifiswitch._state.update(state="switching")
try:
    wifiscan.search()
    check("a search is refused while a switch or test runs", False)
except netconf.NetError as exc:
    check("a search is refused while a switch or test runs", "busy" in str(exc))
fresh_box()
seen_during = []
wifiscan._helper = lambda req, timeout=60: (seen_during.append(wifiswitch.busy())
                                            or {"ok": True, "fresh": True, "networks": []})
wifiscan.search()
check("a search holds the Wi-Fi while it runs and lets go after",
      seen_during == [True] and not wifiswitch.busy() and wifiswitch.searching is False)
wifiscan._helper = hub_helper
fresh_box()
try:
    wifiswitch.start("wifi", ssid="Plant", password="short", config_dir=CFG)
    check("Test and switch checks the key before the AP goes down", False)
except netconf.NetError as exc:
    check("Test and switch checks the key before the AP goes down",
          "8-63" in str(exc) and not sent and not [c for c in calls if c[1]]
          and wifiswitch.state().get("state") == "idle", str(exc))

# -- 8. the key stays off argv and out of the state ------------------------------------
check("no key on any nmcli argv the hub ran",
      not any("goodpass1" in " ".join(a) or "badpass12" in " ".join(a) for a, _ in calls))
check("no key in the switch state the page polls",
      "goodpass1" not in json.dumps(wifiswitch.state()))
src = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
route = open(os.path.join(ROOT, "core", "routes", "network_wifitest.py"), encoding="utf-8").read()
check("the Test route is registered and admin-only",
      "include_router(network_wifitest_router)" in src and "_require_admin(request)" in route
      and "_net(wifitest.start" in route)
for name in ("wifiscan.py", "wifitest.py"):
    n = len(open(os.path.join(ROOT, "core", "services", name), encoding="utf-8").readlines())
    check(f"{name} is under the 300-line ceiling ({n})", n < 300)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
