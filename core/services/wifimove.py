# -*- coding: utf-8 -*-
"""
wifimove.py -- the two moves a live Wi-Fi switch makes: wlan0 joining a
network, and wlan0 becoming the access point (the network it left brought
back when the hotspot will not start).

NO AUTOMATIC HOTSPOT (Tok Aba, 2026-10-14, after being told the risk): a
join that fails leaves wlan0 in client mode with the hotspot OFF and says so
in plain words; the access point comes up only when a person turns it on.
The mode SETTING becomes "wifi" the moment the hotspot is taken down for the
join -- the person chose Wi-Fi -- so neither a hub restart nor a reboot
raises the hotspot behind their back either. A box with no cable that
cannot join is then reached by turning the hotspot on over Ethernet, or by
the page's Reset Settings once it is reachable.

Lifted out of wifiswitch.py, which keeps start(), the state and the budgets
and re-exports these names, so that file stays under the line ceiling. The
budgets, the reasons, the state and the address memory are read off
wifiswitch at call time: one switch state on the box, and a test that
shortens wifiswitch.JOIN_BUDGET_S shortens these too.
"""

import threading
import time

from core.services import netconf
from core.services.netaddr import NetError


def _wlan():
    """(state code, connection, IPv4) of wlan0 right now, in one nmcli call."""
    try:
        out = netconf._nmcli(["-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
                              "device", "show", netconf.AP_DEVICE], timeout=10)
    except NetError:
        return 0, "", ""
    code, conn, ip = 0, "", ""
    for line in out.splitlines():
        parts = netconf._split(line)
        key, value = parts[0], ":".join(parts[1:]).strip()
        if key == "GENERAL.STATE":
            code = int(value.split(" ", 1)[0]) if value[:1].isdigit() else 0
        elif key == "GENERAL.CONNECTION":
            conn = "" if value == "--" else value
        elif key.startswith("IP4.ADDRESS") and value and not ip:
            ip = value.split("/", 1)[0]
    return code, conn, ip


#: What the page and the log say after every failed join: the hotspot stays
#: off, and how the person gets back in.
HOTSPOT_OFF = ("The hotspot is off. Turn the hotspot on to reach this device "
               "without Wi-Fi.")


def failed_join(label, detail):
    """The one sentence a failed switch to Wi-Fi ends with."""
    return f"Could not connect to {label}: {detail}. {HOTSPOT_OFF}"


def _restore_ap(ap_ssid, ap_key, ap_name):
    """Raise the hotspot again after a TEST (wifitest, keep off: the person
    never left it). "" when it is back, else why not. Never after a switch."""
    try:
        netconf.ap_enable(ap_ssid, ap_key)
        return ""
    except NetError as exc:
        why = str(exc)
    try:                     # the profile is still saved: bring it up as it is
        netconf._nmcli(["connection", "up", ap_name or "Hotspot"], sudo=True, timeout=60)
        return ""
    except NetError:
        return why


def _to_wifi(network, new, ap_ssid, ap_key, config_dir):
    from core.services import wifiscan, wifitest              # noqa: PLC0415
    from core.services import wifiswitch as ws               # noqa: PLC0415
    if new and netconf.wifi_helper_installed() and wifiscan.helper_can("trial"):
        # A NEW network is a kept trial ("Test and switch", wifitest.py): a
        # lease proven, the reason named, no profile left when it fails.
        # An older helper keeps the join below (the page's "Switch without
        # testing").
        return wifitest.move(new[0], new[1], new[2], True, ap_ssid, ap_key, config_dir)
    ap_name = netconf.ap_active()
    label = network or (new[0] if new else "a saved network")
    # The person chose Wi-Fi: recorded before the hotspot goes, so a restart
    # mid-join, or after a failed one, does not bring the hotspot back.
    netconf.set_wifi_mode("wifi", config_dir=config_dir)
    try:                     # a step of its own: the hotspot is down BEFORE the join
        if ap_name:
            netconf._nmcli(["connection", "down", ap_name], sudo=True, timeout=30)
    except NetError:
        pass                 # the join below takes wlan0 anyway
    outcome = {}

    def attempt():
        try:
            if new:          # writes the profile, deletes it again if it fails
                netconf.join(new[0], new[1], new[2], autoconnect=True)
            elif network:
                netconf._nmcli(["connection", "up", network], sudo=True,
                               timeout=ws.JOIN_BUDGET_S + 30)
            else:
                netconf._nmcli(["device", "connect", netconf.AP_DEVICE], sudo=True,
                               timeout=ws.JOIN_BUDGET_S + 30)
        except NetError as exc:
            outcome["error"] = str(exc)
    (worker := threading.Thread(target=attempt, name="wifi-join", daemon=True)).start()
    deadline = time.monotonic() + ws.JOIN_BUDGET_S
    # The profile asked for (join() names a new one for its SSID): with the
    # hotspot down NetworkManager may autoconnect ANOTHER saved one first.
    want = network or (new[0] if new else "")
    while True:
        code, conn, ip = _wlan()
        if code == 100 and conn and conn != ap_name and conn == (want or conn) \
                and ip and not ip.startswith("169.254."):
            ws._last_ip[conn] = ip
            return ws._finish("done", f"wlan0 joined {conn} as {ip}.", network=conn,
                              address=ip, reason="")
        if "error" in outcome or time.monotonic() >= deadline:
            break
        if worker.is_alive():
            worker.join(ws.POLL_S)
        else:
            time.sleep(ws.POLL_S)
    reason = ws.reason_of(outcome.get("error", ""), 70 if ip.startswith("169.254.") else code)
    detail = ws.REASONS[reason] + (f" ({outcome['error']})" if reason == "failed" else "")
    # No hotspot: wlan0 stays a Wi-Fi client (NetworkManager may still join
    # another saved network in range by itself), and the page says so.
    ws._finish("failed", failed_join(label, detail), reason=reason, network=label,
               ap_back=False, hotspot_off=True, address="")


def _to_ap(ap_ssid, ap_key, config_dir):
    from core.services import wifiswitch as ws               # noqa: PLC0415
    _, before, left = _wlan()       # the network it leaves, to return to
    if before and left and not left.startswith("169.254."):
        ws._last_ip[before] = left     # where the way back to it will likely land
    error = ""
    try:
        netconf.ap_enable(ap_ssid, ap_key)
    except NetError as exc:
        error = str(exc)
    deadline = time.monotonic() + ws.AP_BUDGET_S
    while not error:
        code, conn, ip = _wlan()
        if code == 100 and conn and ip and netconf.ap_active() == conn:
            netconf.set_wifi_mode("ap", config_dir=config_dir)
            return ws._finish("done", f"wlan0 is the access point {ap_ssid} at {ip}.",
                              network=ap_ssid, address=ip, reason="")
        if time.monotonic() >= deadline:
            error = "it did not start broadcasting in time"
            break
        time.sleep(ws.POLL_S)
    back = False
    try:
        if before:
            netconf._nmcli(["connection", "up", before], sudo=True, timeout=60)
            back = True
    except NetError:
        pass
    message = (f"The access point did not come up: {error}. "
               + (f"wlan0 is back on {before}." if back else
                  "wlan0 is on no network now; reach the box over Ethernet."))
    ws._finish("failed", message, reason="failed", network=ap_ssid, address="")
