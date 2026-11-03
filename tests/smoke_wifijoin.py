# Joining a wifi network writes the profile out in full, security named.
#
# `nmcli device wifi connect SSID password PW` infers the security from the
# access point it can currently see. When it cannot -- a hidden network, or one
# that scanned away while the password was being typed -- it saves a profile
# with a password and no key-mgmt, and NetworkManager refuses it:
#
#     802-11-wireless-security.key-mgmt: property is missing.
#
# The box in the field hit exactly that on a network it had joined a hundred
# times. So join() no longer uses the convenience form: it deletes whatever
# profile the SSID had, adds one with wifi-sec.key-mgmt spelled out, brings it
# up, and deletes it again if it will not come up (an autoconnect profile with
# a wrong password would otherwise retry the AP for ever).
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import netconf                             # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


calls = []
fail_on = {}


def fake_nmcli(args, sudo=False, timeout=45):
    calls.append((tuple(args), sudo))
    verb = " ".join(args[:2])
    if verb in fail_on:
        raise netconf.NetError(fail_on[verb])
    return ""


netconf._nmcli = fake_nmcli
netconf.status = lambda: {"ok": True}

# -- a WPA network ------------------------------------------------------------
calls.clear()
netconf.join("Cytron", "hunter22")
adds = [a for a, _ in calls if a[:2] == ("connection", "add")]
ups = [a for a, _ in calls if a[:2] == ("connection", "up")]
check("the profile is added explicitly, not through `device wifi connect`",
      len(adds) == 1 and not [a for a, _ in calls if a[:3] == ("device", "wifi", "connect")],
      repr(calls))
add = adds[0]
check("...with the security named", "wifi-sec.key-mgmt" in add
      and add[add.index("wifi-sec.key-mgmt") + 1] == "wpa-psk"
      and add[add.index("wifi-sec.psk") + 1] == "hunter22", repr(add))
check("...on the wifi interface, named for the network, autoconnecting",
      add[add.index("ifname") + 1] == netconf.AP_DEVICE
      and add[add.index("con-name") + 1] == "Cytron"
      and add[add.index("connection.autoconnect") + 1] == "yes", repr(add))
check("...then brought up by name", ups == [("connection", "up", "Cytron")], repr(ups))
check("any earlier profile of that name goes first",
      calls[0][0][:2] == ("connection", "delete") and calls[0][0][2] == "Cytron", repr(calls[0]))
check("every step runs with sudo", all(s for _, s in calls), repr(calls))

# -- an open network, a hidden one -----------------------------------------------
calls.clear()
netconf.join("Cafe", "")
add = [a for a, _ in calls if a[:2] == ("connection", "add")][0]
check("no password = no security block at all", "wifi-sec.key-mgmt" not in add, repr(add))
calls.clear()
netconf.join("Back office", "pw123456", hidden=True)
add = [a for a, _ in calls if a[:2] == ("connection", "add")][0]
check("a hidden network is marked hidden on the profile",
      "802-11-wireless.hidden" in add and add[add.index("802-11-wireless.hidden") + 1] == "yes",
      repr(add))

# -- "connect automatically" unticked ----------------------------------------------
calls.clear()
netconf.join("Cytron", "hunter22", autoconnect=False)
add = [a for a, _ in calls if a[:2] == ("connection", "add")][0]
check("untick connect-automatically = the profile is saved but will not autoconnect",
      add[add.index("connection.autoconnect") + 1] == "no", repr(add))
route = open(os.path.join(ROOT, "core", "routes", "network.py"), encoding="utf-8").read()
check("...and the join route carries the tick through, defaulting to on",
      'body.get("autoconnect", True)' in route)
panel = open(os.path.join(ROOT, "frontend", "src", "components", "NetworkPanel.jsx"),
             encoding="utf-8").read()
check("the page has the picker: a row opens to the tick and Connect, Connect asks for the password",
      "Connect automatically" in panel and "Show available networks" in panel
      and "function JoinDialog" in panel and "autoconnect }" in panel)
check("Wi-Fi and the access point are two cards; wlan0's address sits inside the Wi-Fi one",
      panel.count('className="panel-card"') >= 2 and "via={data.via === d.device} embedded" in panel
      and 'const wifiOn = !!data.radio && !apOn' in panel)
# Since 2026-10-14 the fields are drawn (under Advanced Network Settings) but
# disabled while DHCP is on, rather than hidden.
check("static is DHCP off first, then the fields; Save locked until the address is valid, then Reboot now / Later",
      "disabled={dhcp}" in panel and "const IPV4 = " in panel
      and "(!dhcp && !staticReady)" in panel and '"Apply and reboot"' not in panel
      and panel.count('cancelLabel: "Later"') >= 1
      and '{ action: "reboot" }' in panel)

# -- the access point takes Wi-Fi down, and gives it back ----------------------------
calls.clear()
wlan_state = {"conn": "Cytron", "mode": "infrastructure", "dev": "connected"}


def fake_nmcli_ap(args, sudo=False, timeout=45):
    calls.append((tuple(args), sudo))
    a = list(args)
    if a[:2] == ["-t", "-f"] and a[2] == "GENERAL.CONNECTION":
        return "GENERAL.CONNECTION:%s\n" % wlan_state["conn"]
    if a[:2] == ["-t", "-f"] and a[2] == "802-11-wireless.mode":
        return "802-11-wireless.mode:%s\n" % wlan_state["mode"]
    if a[:2] == ["-t", "-f"] and a[2] == "DEVICE,STATE":
        return "wlan0:%s\neth0:connected\n" % wlan_state["dev"]
    if a[:3] == ["device", "wifi", "hotspot"]:
        wlan_state.update(conn="Hotspot", mode="ap", dev="connected")
    if a[:2] == ["connection", "down"]:
        wlan_state.update(conn="", mode="", dev="disconnected")
    return ""


netconf._nmcli = fake_nmcli_ap
netconf.ap_enable("iriv-abc", "k3ypass99")
downs = [a for a, _ in calls if a[:2] == ("connection", "down")]
spots = [a for a, _ in calls if a[:3] == ("device", "wifi", "hotspot")]
check("enabling the AP takes the joined network down FIRST, then broadcasts",
      downs == [("connection", "down", "Cytron")] and len(spots) == 1
      and calls.index((downs[0], True)) < calls.index((spots[0], True)), repr(calls))
calls.clear()
netconf.ap_disable()
check("turning the AP off hands wlan0 back to Wi-Fi (device connect), best effort",
      [a for a, _ in calls if a[:2] == ("device", "connect")] == [("device", "connect", "wlan0")],
      repr(calls))

# -- the radio's job is a setting the boot re-asserts ----------------------------------
# Always exactly one of "ap" / "wifi"; "ap" out of the box. Since 2026-10-07 the
# page switches wlan0 LIVE (wifiswitch.py, smoke_wifiswitch.py) and the switch
# writes the setting once it worked; hub boot re-asserts it.
import time as _time                                          # noqa: E402
import threading as _threading                                # noqa: E402
CFG = tempfile.mkdtemp()
check("a box out of the box is an access point", netconf.wifi_mode(CFG) == "ap")
check("...and the other choice is Wi-Fi",
      netconf.set_wifi_mode("wifi", config_dir=CFG) == "wifi" and netconf.wifi_mode(CFG) == "wifi")
try:
    netconf.set_wifi_mode("off", config_dir=CFG)
    check("an unknown mode is refused", False)
except netconf.NetError:
    check("an unknown mode is refused", True)
try:
    netconf.set_wifi_mode("ap", "short", config_dir=CFG)
    check("an AP password under eight characters is refused when staging", False)
except netconf.NetError:
    check("an AP password under eight characters is refused when staging", True)
netconf.set_wifi_mode("ap", "k3ypass99", config_dir=CFG)
check("staging writes the mode and the password to network.local.toml",
      netconf.wifi_mode(CFG) == "ap"
      and 'mode = "ap"' in open(os.path.join(CFG, "network.local.toml")).read()
      and 'password = "k3ypass99"' in open(os.path.join(CFG, "network.local.toml")).read())
DCFG = tempfile.mkdtemp()
try:
    netconf.set_wifi_mode("ap", netconf.DEFAULT_AP_PASSWORD, config_dir=DCFG)
    check("the default key iriv1234 is accepted as a new AP password",
          netconf.ap_password(DCFG) == "iriv1234")
except netconf.NetError as exc:
    check("the default key iriv1234 is accepted as a new AP password", False, str(exc))

netconf.available = lambda: True
netconf.radio_on = lambda: True
netconf.devices = lambda: [{"type": "wifi", "device": "wlan0"}]
cases = (
    ("ap", True, 0, 0, "set to AP and already broadcasting: nothing to do"),
    ("ap", False, 1, 0, "set to AP and on Wi-Fi: boot brings the AP up"),
    # Tok Aba 2026-10-14, "leave a running hotspot alone": only PRESERVES.
    ("wifi", True, 0, 0, "set to Wi-Fi and ALREADY broadcasting (a hub restart, e.g. an "
                         "update): the hotspot is left up, not torn down"),
    ("wifi", False, 0, 0, "set to Wi-Fi and on Wi-Fi: nothing to do"),
)
for mode, is_ap, want_spots, want_downs, label in cases:
    calls.clear()
    wlan_state.update(conn="Hotspot" if is_ap else "Cytron", mode="ap" if is_ap else "infrastructure",
                      dev="connected")
    netconf.apply_wifi_mode("iriv-abc", "k3ypass99", mode, grace=0)
    # Wait for the worker itself rather than a fixed 0.5s: on a loaded machine
    # the thread could still be running when the calls are counted.
    for _t in [t for t in _threading.enumerate() if t.name == "wlan-mode"]:
        _t.join(10)
    spots = [a for a, _ in calls if a[:3] == ("device", "wifi", "hotspot")]
    downs = [a for a, _ in calls if a[:2] == ("connection", "down") and a[2] == "Hotspot"]
    check(label, len(spots) == want_spots and len(downs) == want_downs
          and (not spots or "k3ypass99" in spots[0]), repr(calls))


# -- hub start with the saved mode Wi-Fi: preserve, never start (2026-10-14) -----------
def _start(mode, before, nmcli=None):
    """One hub-start mode step: (root calls made, log lines, mode writes)."""
    calls.clear()
    wlan_state.update(before)
    logs, writes = [], []
    real_set, real_nm = netconf.set_wifi_mode, netconf._nmcli
    netconf.set_wifi_mode = lambda *a, **k: writes.append(a)
    if nmcli:
        netconf._nmcli = nmcli
    try:
        t = netconf.apply_wifi_mode("iriv-abc", "k3ypass99", mode, grace=0,
                                    log=lambda msg, *a: logs.append(msg % a if a else msg))
        t.join(10)
    finally:
        netconf.set_wifi_mode, netconf._nmcli = real_set, real_nm
    return [a for a, sudo in calls if sudo], logs, writes


HOT = {"conn": "Hotspot", "mode": "ap", "dev": "connected"}
OFF = {"conn": "", "mode": "", "dev": "disconnected"}
root, logs, writes = _start("wifi", HOT)
check("AP up + saved Wi-Fi: no root step at all (no down, no hotspot, no connect)",
      root == [], repr(root))
check("...the setting is not touched, and the log says the hotspot was left up",
      writes == [] and any("already up - left up" in m for m in logs), repr(logs))
root, logs, writes = _start("wifi", OFF)
check("AP down + saved Wi-Fi: applied as before, NO hotspot started",
      not [a for a in root if a[:3] == ("device", "wifi", "hotspot")]
      and not [a for a in root if a[:2] == ("connection", "up")] and writes == [], repr(root))
root, logs, writes = _start("ap", HOT)
check("AP up + saved AP: still an access point (re-asserted or left up), never taken down",
      not [a for a in root if a[:2] == ("connection", "down")] and wlan_state["mode"] == "ap",
      repr(root))
root, logs, writes = _start("ap", OFF)
check("AP down + saved AP: the hotspot comes up, as before (the person chose it)",
      len([a for a in root if a[:3] == ("device", "wifi", "hotspot")]) == 1, repr(root))


def nmcli_unreadable(args, sudo=False, timeout=45):
    calls.append((tuple(args), sudo))
    if list(args[:3]) == ["-t", "-f", "GENERAL.CONNECTION"]:
        raise netconf.NetError("NetworkManager is not running")
    return fake_nmcli_ap(args, sudo, timeout)


root, logs, writes = _start("wifi", HOT, nmcli_unreadable)
check("hotspot state unreadable + saved Wi-Fi: fail safe, the radio is left alone",
      root == [] and writes == [], repr(root))
check("...and that is logged", any("could not read whether the hotspot is up" in m
                                   for m in logs), repr(logs))
src_nc = open(os.path.join(ROOT, "core", "services", "netconf.py"), encoding="utf-8").read()
body = src_nc.split("def apply_wifi_mode", 1)[1].split("\ndef ", 1)[0]
wifi_branch = body.split('elif mode == "wifi":', 1)[1].split("except Exception", 1)[0]
check("the Wi-Fi start branch can only PRESERVE: it never enables or disables the AP",
      "ap_enable" not in wifi_branch and "ap_disable" not in wifi_branch
      and "_nmcli" not in wifi_branch, wifi_branch)
wp_src = open(os.path.join(ROOT, "scripts", "wifi_profile.py"), encoding="utf-8").read()
check("the hotspot profile does not autoconnect, so a REBOOT never revives one to preserve",
      'if client else False' in wp_src, "wifi_profile.validate autoconnect for kind ap")

hub = open(os.path.join(ROOT, "hub_server.py"), encoding="utf-8").read()
cfgtxt = open(os.path.join(ROOT, "config", "network.toml"), encoding="utf-8").read()
check("the hub applies the mode at boot, from config/network.toml [ap] (ap), with the stored key (default iriv1234)",
      "apply_wifi_mode(" in hub and 'mode = "ap"' in cfgtxt
      and not __import__("re").search(r"(?m)^\s*password\s*=", cfgtxt)
      and "ensure_ap_password(config_dir)" in hub)
check("the AP route switches live through wifiswitch, never writing the mode itself",
      "_net(wifiswitch.start, " in route and "netconf.ap_enable" not in route
      and 'netconf.set_wifi_mode, "wifi"' not in route
      and 'payload["mode_mismatch"]' in route and 'payload["switch"]' in route)
check("the page has one switch for the two, switching now (no reboot staged)",
      "switchToAp(" in panel and "setToWifi(true)" in panel
      and "stageMode(" not in panel and "pending_reboot" not in panel
      and "/api/hub/network/radio" not in panel)
# Tok Aba 2026-10-14: the stored key is never put back into the field; it is
# blank with a placeholder, and a blank one is left out of the save (kept).
check("...and never prefills the AP password field (a new one only)",
      'payload["ap_password"]' in route and "setApPassword(d.ap_password)" not in panel
      and 'placeholder="Enter a new password"' in panel
      and "apPassword ? { ssid: name, password: apPassword } : { ssid: name }" in panel)

netconf._nmcli = fake_nmcli                 # back to the join-path fake

# -- a profile that will not come up is not kept ---------------------------------
calls.clear()
fail_on["connection up"] = "Secrets were required, but not provided."
try:
    netconf.join("Cytron", "wrongpass")
    check("a failed join raises", False)
except netconf.NetError as exc:
    check("a failed join raises NetworkManager's reason", "Secrets" in str(exc), str(exc))
deletes = [a for a, _ in calls if a[:2] == ("connection", "delete")]
check("...and deletes the profile it just made, so it does not retry for ever",
      len(deletes) == 2 and deletes[-1][2] == "Cytron", repr(calls))
fail_on.clear()

# -- a first-ever join: nothing to delete is not an error -----------------------
calls.clear()
fail_on["connection delete"] = "unknown connection 'Cytron'."
try:
    netconf.join("Cytron", "hunter22")
    check("a missing old profile is not an error", True)
except netconf.NetError as exc:
    check("a missing old profile is not an error", False, str(exc))
fail_on.clear()

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
