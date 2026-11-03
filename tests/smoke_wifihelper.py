# Wi-Fi keys travel on a root helper's stdin, never on sudo's argv.
#
# WHAT WAS WRONG. The hotspot key (`nmcli device wifi hotspot ... password K`,
# at EVERY boot) and the site's Wi-Fi passphrase (`nmcli connection add ...
# wifi-sec.psk P`) reached NetworkManager as arguments of a sudo'd nmcli.
# sudo journals every argv it authorises, readable by the adm group, and the
# argv sat in /proc/<pid>/cmdline while nmcli ran (Yaya M-5, Pipi 2b). The
# sudoers grant behind it was `nmcli *` -- including `nmcli -s connection
# show`, which prints every stored key.
#
# Pinned: scripts/wifi_profile.py validates from nothing, writes a 0600
# keyfile (SSID as a byte list, key GKeyFile-escaped, a uuid fixed per
# profile), loads and brings it up, leaves an unchanged running AP alone and
# deletes a join that will not come up; netconf hands join/ap_enable to it on
# stdin (legacy argv path only while the helper is absent); the AP is
# re-asserted at boot; netconf.reset exists (the Reset Wi-Fi button called a
# function that did not); install.sh installs the helper and narrows the
# sudoers grant to the helper plus the verbs netconf uses, -s refused.
#
#     python tests/smoke_wifihelper.py
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

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


def refused(req):
    try:
        wp.validate(req)
        return False
    except wp.Refused:
        return True


# -- validation, from nothing ---------------------------------------------------
check("kind is one of two words", refused({"kind": "rm", "ssid": "a", "psk": "12345678"}))
check("an SSID over 32 bytes is refused", refused({"kind": "join", "ssid": "x" * 33}))
check("...and one with a control character",
      refused({"kind": "join", "ssid": "a\nb", "psk": "12345678"}))
check("an AP needs a key, 8-63 printable", refused({"kind": "ap", "ssid": "iriv-1"})
      and refused({"kind": "ap", "ssid": "iriv-1", "psk": "short"})
      and refused({"kind": "ap", "ssid": "iriv-1", "psk": "x" * 64})
      and refused({"kind": "ap", "ssid": "iriv-1", "psk": "tab\there1"}))
check("...or 64 hex digits", not refused({"kind": "ap", "ssid": "iriv-1", "psk": "ab" * 32}))
check("an open network joins with no key",
      wp.validate({"kind": "join", "ssid": "Cafe"})["psk"] == "")
check("hidden/autoconnect must be real booleans",
      refused({"kind": "join", "ssid": "a", "psk": "12345678", "hidden": "yes"}))
check("anything else in the request is dropped",
      set(wp.validate({"kind": "join", "ssid": "a", "psk": "12345678", "ifname": "eth0",
                       "path": "/etc/passwd"})) == {"kind", "ssid", "psk", "hidden",
                                                    "autoconnect", "keep"})

# -- the keyfile -------------------------------------------------------------------
ap = wp.validate({"kind": "ap", "ssid": "iriv-1a2b", "psk": "a b\\c;d=e#f"})
name, stem, uid, text = wp.profile(ap)
check("the AP is the Hotspot profile, file irivai-ap", name == "Hotspot" and stem == "irivai-ap")
check("...broadcasting (mode=ap) with shared IPv4, not autoconnecting by itself",
      "mode=ap" in text and "[ipv4]\nmethod=shared" in text and "autoconnect=false" in text)
check("...on wlan0 only", "interface-name=wlan0" in text)
check("...WPA2-PSK CCMP", "key-mgmt=wpa-psk" in text and "proto=rsn" in text)
check("the SSID is a byte list (no escaping to get wrong)",
      "ssid=" + "".join("%d;" % b for b in b"iriv-1a2b") in text)
check("the key is GKeyFile-escaped: backslash, then every space",
      "psk=a\\sb\\\\c;d=e#f\n" in text, text)
check("the uuid is fixed per profile, so a rewrite replaces",
      wp.profile(ap)[2] == uid and wp.profile(
          wp.validate({"kind": "join", "ssid": "Cytron", "psk": "hunter22"}))[2] != uid)
j = wp.validate({"kind": "join", "ssid": "Back office", "psk": "hunter22", "hidden": True,
                 "autoconnect": False})
jn, jstem, _, jtext = wp.profile(j)
check("a joined network is named for its SSID, in a file named from its hex",
      jn == "Back office" and jstem == "irivai-wifi-" + b"Back office".hex()
      and "id=Back\\soffice" in jtext)
check("...infrastructure mode, DHCP, hidden and autoconnect as asked",
      "mode=infrastructure" in jtext and "[ipv4]\nmethod=auto" in jtext
      and "hidden=true" in jtext and "autoconnect=false" in jtext)
check("an open network has no security block",
      "[wifi-security]" not in wp.profile(wp.validate({"kind": "join", "ssid": "Cafe"}))[3])

# -- apply(), against a temp directory and a fake nmcli ---------------------------
conn = tempfile.mkdtemp()
wp.CONN_DIR = conn
runs, state = [], {"active": False, "up_rc": 0}


def fake_run(argv, timeout=90):
    runs.append(tuple(argv))
    if argv[:3] == ["nmcli", "connection", "up"]:
        if state["up_rc"] == 0:
            state["active"] = True
        return state["up_rc"], "Error: Secrets were required, but not provided."
    return 0, ""


wp._run = fake_run
wp._others_named = lambda name, keep: ["old-hotspot-uuid"] if name == "Hotspot" else []
wp._active = lambda u: state["active"]
res = wp.apply(ap)
path = os.path.join(conn, "irivai-ap.nmconnection")
check("an AP request writes the keyfile, loads it and brings it up by uuid",
      res["ok"] and res["changed"] and os.path.isfile(path)
      and ("nmcli", "connection", "load", path) in runs
      and ("nmcli", "connection", "up", "uuid", uid) in runs, repr(runs))
check("...after deleting the old nmcli-made Hotspot (the old key at rest)",
      ("nmcli", "connection", "delete", "uuid", "old-hotspot-uuid") in runs)
check("no key on any argv", not any("a b" in " ".join(r) or "hunter22" in " ".join(r)
                                    for r in runs))
if os.name == "posix":
    check("...in a 0600 file", (os.stat(path).st_mode & 0o777) == 0o600)
check("no temp file is left beside it", os.listdir(conn) == ["irivai-ap.nmconnection"])
runs.clear()
res = wp.apply(ap)
check("the same AP, already up: nothing written, not bounced",
      res["ok"] and not res["changed"]
      and not [r for r in runs if r[:3] in (("nmcli", "connection", "up"),
                                           ("nmcli", "connection", "load"))], repr(runs))
runs.clear()
state["up_rc"] = 4
res = wp.apply(j)
jpath = os.path.join(conn, jstem + ".nmconnection")
check("a join that will not come up answers NetworkManager's reason",
      not res["ok"] and "Secrets were required" in res["message"], repr(res))
check("...and is deleted again (file and profile), so it is not retried for ever",
      not os.path.exists(jpath)
      and any(r[:4] == ("nmcli", "connection", "delete", "uuid") for r in runs), repr(runs))
if hasattr(os, "geteuid") and os.geteuid() != 0:
    proc = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "wifi_profile.py")],
                          input="{}", capture_output=True, text=True)
    check("the helper refuses to run as anyone but root",
          proc.returncode == 1 and "must run as root" in proc.stdout)

# -- netconf hands the key over on stdin --------------------------------------------
from core.services import netconf                             # noqa: E402

sent = []


class Done:
    def __init__(self, out, rc=0, err=""):
        self.stdout, self.returncode, self.stderr = out, rc, err


answer = {"value": {"ok": True, "message": "up", "name": "x", "changed": True}}


def fake_subprocess_run(argv, **kw):
    sent.append((list(argv), kw.get("input")))
    return Done(json.dumps(answer["value"]) + "\n")


nm_calls = []
netconf.subprocess.run = fake_subprocess_run
netconf.wifi_helper_installed = lambda: True
netconf._nmcli = lambda args, sudo=False, timeout=45: nm_calls.append((tuple(args), sudo)) or ""
netconf.status = lambda: {"ok": True}
netconf._wlan_connection = lambda: ""
netconf.ap_active = lambda: ""

netconf.join("Cytron", "hunter22", hidden=True, autoconnect=False)
argv, stdin = sent[-1]
check("join runs exactly the sudoers command, no arguments of its own",
      tuple(argv) == netconf.WIFI_HELPER_ARGV
      == ("sudo", "-n", "/usr/bin/python3", "-I", "/usr/local/lib/irivai/wifi-profile.py"))
check("...with the passphrase on stdin, not on argv",
      json.loads(stdin) == {"kind": "join", "ssid": "Cytron", "psk": "hunter22",
                            "hidden": True, "autoconnect": False}
      and "hunter22" not in " ".join(argv))
check("...and no nmcli of its own (the helper does delete/add/up)",
      not nm_calls, repr(nm_calls))
netconf.ap_enable("iriv-abc", "k3ypass99")
check("ap_enable asks the helper for the Hotspot, key on stdin",
      json.loads(sent[-1][1]) == {"kind": "ap", "ssid": "iriv-abc", "psk": "k3ypass99"}
      and not [c for c in nm_calls if c[0][:3] == ("device", "wifi", "hotspot")])
answer["value"] = {"ok": False, "message": "Secrets were required, but not provided."}
try:
    netconf.join("Cytron", "wrongpass")
    check("a refused join raises the helper's sentence", False)
except netconf.NetError as exc:
    check("a refused join raises the helper's sentence", "Secrets" in str(exc))
answer["value"] = {"ok": True}

# The boot re-asserts the AP even while one is up: a key changed since the
# hotspot came up reaches the radio, an unchanged one is left alone (helper).
import threading                                              # noqa: E402
netconf.available = lambda: True
netconf.radio_on = lambda: True
netconf.devices = lambda: [{"type": "wifi", "device": "wlan0"}]
netconf.ap_active = lambda: "Hotspot"
sent.clear()
netconf.apply_wifi_mode("iriv-abc", "k3ypass99", "ap", grace=0)
for t in [t for t in threading.enumerate() if t.name == "wlan-mode"]:
    t.join(10)
check("with the helper, the boot re-asserts an AP that is already up",
      len(sent) == 1 and json.loads(sent[0][1])["kind"] == "ap", repr(sent))

# Reset Wi-Fi: the route has called netconf.reset since the AP became a
# staged setting, and it did not exist (a 500 on the button).
netconf.saved_wifi = lambda: [{"name": "Cytron", "active": False},
                              {"name": "Cafe", "active": True}]
cfg = tempfile.mkdtemp()
from core import config                                       # noqa: E402
config.save_local("network", {"ap": {"password": "boxkey9999"}}, cfg)
netconf.ap_password = lambda config_dir=None: "boxkey9999"
nm_calls.clear()
sent.clear()
netconf.reset("iriv-abc")
check("reset forgets every saved Wi-Fi network",
      [c[0] for c in nm_calls if c[0][:2] == ("connection", "delete")]
      == [("connection", "delete", "Cytron"), ("connection", "delete", "Cafe")])
check("...and raises the AP with the stored key when none was typed",
      json.loads(sent[-1][1]) == {"kind": "ap", "ssid": "iriv-abc", "psk": "boxkey9999"})

# iriv1234 is the default key again (Tok Aba, 2026-10-07): typed or stored,
# ap_enable, Reset Wi-Fi and the boot all raise the hotspot on it. Until that
# day ap_enable refused it when typed (Pipi's 1.0.14 gate F4).
for label, call in (("ap_enable", lambda: netconf.ap_enable("iriv-abc", "iriv1234")),
                    ("Reset Wi-Fi", lambda: netconf.reset("iriv-abc", "iriv1234"))):
    sent.clear()
    try:
        call()
        check(f"{label} with iriv1234 typed raises the hotspot on it",
              sent and json.loads(sent[-1][1])["psk"] == "iriv1234", repr(sent))
    except netconf.NetError as exc:
        check(f"{label} with iriv1234 typed raises the hotspot on it", False, str(exc))
netconf.ap_password = lambda config_dir=None: "iriv1234"
sent.clear()
netconf.reset("iriv-abc")
check("a box whose STORED key is iriv1234 gets its hotspot back on reset",
      json.loads(sent[-1][1])["psk"] == "iriv1234")
netconf.ap_active = lambda: ""
sent.clear()
netconf.apply_wifi_mode("iriv-abc", "iriv1234", "ap", grace=0)
for t in [t for t in threading.enumerate() if t.name == "wlan-mode"]:
    t.join(10)
check("...and so does the boot",
      len(sent) == 1 and json.loads(sent[0][1])["psk"] == "iriv1234", repr(sent))

# -- install.sh: the helper and the narrowed grant -----------------------------------
with open(os.path.join(ROOT, "scripts", "install.sh"), encoding="utf-8") as fh:
    inst = fh.read()
check("the helper is installed root-owned outside the release tree",
      'install -m 755 "${SRC_DIR}/scripts/wifi_profile.py" /usr/local/lib/irivai/wifi-profile.py'
      in inst and "chown root:root /usr/local/lib/irivai/wifi-profile.py" in inst)
check("...with its line endings normalised like the other helpers",
      "/usr/local/lib/irivai/wifi-profile.py \\\n" in inst)
box = re.search(r"cat > /etc/sudoers.d/irivai-box.tmp <<SUDOERS\n(.*?)\nSUDOERS", inst, re.S)
rule = box.group(1) if box else ""
check("the box rule no longer grants `nmcli *`",
      rule and "/usr/bin/nmcli *," not in rule and not rule.rstrip().endswith("/usr/bin/nmcli *"))
check("...it names the helper with no arguments",
      "/usr/bin/python3 -I /usr/local/lib/irivai/wifi-profile.py," in rule)
check("...and refuses -s / --show-secrets wherever they could go",
      all(d in rule for d in ("!/usr/bin/nmcli -s *", "!/usr/bin/nmcli * -s *",
                              "!/usr/bin/nmcli --show-secrets *",
                              "!/usr/bin/nmcli * --show-secrets *")))
# Every sudo'd nmcli in netconf is covered by an allowed verb.
src = open(os.path.join(ROOT, "core", "services", "netconf.py"), encoding="utf-8").read()
allowed = [a.strip()[len("/usr/bin/nmcli "):].rstrip(",")
           for a in rule.split(",") if a.strip().startswith("/usr/bin/nmcli ")]
calls = re.findall(r'_nmcli\(\[([^\]]*)\][^\n]*sudo=True', src)
uncovered = []
for call in calls:
    call = call.replace("AP_DEVICE", '"wlan0"')
    words = re.findall(r'"([^"]+)"', call)
    tail = " X" if re.search(r",\s*[a-z_]+", call.split('"')[-1]) else ""
    lines = ([" ".join(words[:2] + [w]) + tail for w in words[2:]]
             if " if " in call else [" ".join(words) + tail])
    for line in lines:
        if not any(re.fullmatch(re.escape(a).replace(r"\*", ".*"), line) for a in allowed):
            uncovered.append(line)
# The legacy hotspot/add lines run only on a box without the helper -- which
# still has the old `nmcli *` grant.
uncovered = [u for u in uncovered if not u.startswith(("device wifi hotspot",
                                                       "connection add type wifi"))]
check("every sudo nmcli the Network page runs is in the grant",
      len(calls) >= 8 and not uncovered, repr((len(calls), uncovered)))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
