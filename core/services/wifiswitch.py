# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
wifiswitch.py -- wlan0 from access point to Wi-Fi and back, LIVE, no reboot.

Tok Aba (2026-10-07): switching the radio's job must not restart the box:

  AP -> Wi-Fi  the hotspot goes down and the chosen client profile comes up:
               a saved one by name, a new one through netconf.join (the root
               helper, key on stdin), or "any saved network" (device connect).
               If wlan0 has not JOINED -- connected, on that profile, holding
               a real address, not 169.254 -- within JOIN_BUDGET_S, the switch
               is called failed, the reason said in plain words, and the
               hotspot STAYS OFF (Tok Aba, 2026-10-14: never raised by
               itself; "Test connection", wifitest.py, tries a password
               without leaving it).
  Wi-Fi -> AP  the client goes down and the hotspot comes up on the stored
               name and key. If it is not broadcasting with an address within
               AP_BUDGET_S, the network it left is brought back.

The mode SETTING (network [ap].mode, what the boot re-asserts) is "wifi" once
the hotspot is taken down for a join, whatever the join does (a restart must
not raise a hotspot the person turned off), and "ap" once it broadcasts.

No new root step: connection up/down * and device connect wlan0 are in the
irivai-box sudoers rule already, and a new network goes through the helper.
Ethernet is never named. Nothing is restarted -- not the box, the hub or avahi:
the hub, masters and agent listen on 0.0.0.0; avahi-daemon follows addresses
itself (the hub's record is renewed anyway, _after); ap-isolate.nft matches
iifname "wlan0" from a table loaded at boot, so it holds for an AP raised live
as for one raised at boot; the front panel is told at once (panel.wlan_note).

WHERE THE BOX WILL BE (candidates): a page on wlan0 loses the box partway
through, and a browser cannot move its own computer to another network. What
the page CAN do is be told, before anything moves, every address the box may
answer on afterwards, and try them all until one says "this box" (GET
/api/health/box, hostinfo.box_id). To the AP that is one known address. To
Wi-Fi it is a prediction: the profile's fixed address if it has one, the
address this hub saw wlan0 hold on that network, the last DHCP lease
NetworkManager kept for it (a DHCP server hands a returning card its old
address when it can), the wired addresses, and the .local name. `own` marks
the ones that are this box by construction; the page carries the session
only to those (the same rule as BoxRestartNote's handoff, Pipi L1): a
predicted lease or a .local name may be answered by somebody else.
"""

import glob
import os
import re
import threading
import time

from core.services import netconf
from core.services.netaddr import NetError

#: How long a join may take before it is called failed. Association plus a
#: lease is seconds on a working network; with the start delay this keeps the
#: page's wait (state()["budget_s"]) at 45 s.
JOIN_BUDGET_S = 35.0
#: How long the hotspot may take to broadcast with its address.
AP_BUDGET_S = 20.0
#: A caller on wlan0 must get the reply before the radio moves under it.
START_DELAY_S = 3.0
POLL_S = 1.0
#: NetworkManager's `ipv4.method shared` address: the AP's own, on wlan0.
AP_ADDRESS = "10.42.0.1"
#: Where NetworkManager keeps each profile's last lease: internal-<uuid>-<dev>
#: .lease (its own DHCP client, the Pi OS default) or dhclient-<uuid>-<dev>
#: .lease. World-readable on Pi OS; an unreadable one only means one fewer
#: guess, never an error.
LEASE_DIR = "/var/lib/NetworkManager"

#: Why a join failed, in the words the page and the log use.
REASONS = {
    "wrong_password": "the password was not accepted",
    "not_found": "the network was not found (out of range, or the name is wrong)",
    "no_address": "it joined, but the network gave the box no address (DHCP)",
    "timeout": "it did not finish joining in time",
    "failed": "NetworkManager refused it",
}

#: nmcli device states a join is left stuck in: 70 ip-config (associated, no
#: lease), 50/60 config/need-auth (the key handshake), 30/40 never found.
_STALLED = {70: "no_address", 50: "wrong_password", 60: "wrong_password",
            30: "not_found", 40: "not_found"}

_lock = threading.Lock()
_state = {"state": "idle"}
#: profile name -> the address wlan0 last held on it, seen by this process.
_last_ip = {}
#: A Search Wi-Fi scan is running (wifiscan.search); read and set under _lock.
searching = False


def reason_of(text, state_code=None):
    """One of REASONS for NetworkManager's sentence, else for the state wlan0
    was stuck in when the time ran out (nmcli's device state numbers)."""
    low = (text or "").lower()
    if any(k in low for k in ("secrets were required", "supplicant", "psk",
                              "password", "authenticat", "no-secrets")):
        return "wrong_password"
    if any(k in low for k in ("could not be found", "not found", "no suitable",
                              "ssid-not-found")):
        return "not_found"
    if any(k in low for k in ("ip configuration", "dhcp", "no address")):
        return "no_address"
    return _STALLED.get(state_code) or ("failed" if text else "timeout")


def state():
    """What the page and the panel show: the switch under way or the last one."""
    with _lock:
        return dict(_state, budget_s=int(JOIN_BUDGET_S + START_DELAY_S + 7),
                    ap_address=AP_ADDRESS)


def busy():
    with _lock:
        return _state.get("state") == "switching" or searching


def _finish(outcome, message, **fields):
    with _lock:
        _state.update(state=outcome, message=message, finished=time.time(), **fields)


def _panel_note(text):
    """Say it on the OLED's Wi-Fi page now; "" hands the page back."""
    try:
        from core.services import panel                       # noqa: PLC0415
        panel.wlan_note = text
        panel.refresh()
    except Exception:                                         # noqa: BLE001
        pass


def _after():
    """Show the new wlan0: the panel's facts re-read now, its note cleared,
    and the hub's mDNS record renewed (the publisher's loop starts a fresh
    avahi-publish-service within 5 s). No root; avahi is not restarted."""
    try:
        from core.services import discovery_mdns, hostinfo    # noqa: PLC0415
        hostinfo.snapshot(force=True)
        pub = discovery_mdns._publisher
        if pub is not None:
            proc = getattr(pub, "_proc", None)
            if proc is not None and proc.poll() is None:
                proc.terminate()
            pub.learn_due = True
    except Exception:                                         # noqa: BLE001
        pass
    _panel_note("")


# The two moves themselves (wlan0 to a network, wlan0 to the access point)
# live in wifimove.py; re-exported here, where the rest of the hub looks.
from core.services.wifimove import _to_ap, _to_wifi, _wlan  # noqa: E402,F401


def _leased(uuid_):
    """The address the last lease on this profile gave wlan0, "" if none."""
    if not uuid_ or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", uuid_):
        return ""
    for path in sorted(glob.glob(os.path.join(
            LEASE_DIR, "*-%s-%s.lease" % (uuid_, netconf.AP_DEVICE)))):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        found = (re.findall(r"^ADDRESS=([0-9.]+)\s*$", text, re.MULTILINE)
                 or re.findall(r"fixed-address\s+([0-9.]+)\s*;", text))
        if found:
            return found[-1]      # dhclient appends: the last block is the newest
    return ""


def _wifi_profiles():
    """[(name, uuid)] of every saved Wi-Fi profile, the AP's included."""
    try:
        out = netconf._nmcli(["-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
                             timeout=15)
    except NetError:
        return []
    found = []
    for line in out.splitlines():
        parts = netconf._split(line)
        if len(parts) >= 3 and parts[2] == "802-11-wireless" and parts[0]:
            found.append((parts[0], parts[1]))
    return found


def candidates(mode, network="", ssid="", devices=None):
    """Every address the box may answer on once wlan0 has moved to `mode`:
    [{host, why, own}], read BEFORE it moves (the module header says why).
    Read-only; any one source failing only drops that source."""
    out, seen = [], set()

    def add(host, why, own):
        host = str(host or "").strip()
        if host and host not in seen and not host.startswith("169.254."):
            seen.add(host)
            out.append({"host": host, "why": why, "own": bool(own)})

    if mode == "ap":
        add(AP_ADDRESS, "the access point's own address", True)
    else:
        want = network or ssid
        try:
            profiles = _wifi_profiles()
            ap = netconf.ap_active()
        except Exception:                                     # noqa: BLE001
            profiles, ap = [], ""
        for name, uuid_ in profiles:
            if name == ap or (want and name != want):
                continue
            try:
                fixed = netconf._connection_ipv4(name, uuid_)
            except Exception:                                 # noqa: BLE001
                fixed = {}
            if fixed.get("method") == "manual":
                add(fixed.get("static_ip"), f"the fixed address set on {name}", True)
            add(_last_ip.get(name), f"the address it had on {name} before", False)
            add(_leased(uuid_), f"its last DHCP lease on {name}", False)
        add(_last_ip.get(want), f"the address {want} gave it in the test", False)
    # A wired address does not move with wlan0, and a laptop that can reach
    # it reaches the box (the page waits for the switch to finish, not just
    # for an answer, so this one cannot cut the switch short). Ethernet only:
    # a docker or USB-gadget bridge is not an address a laptop reaches.
    for dev in devices or []:
        ip = dev.get("ip") or ""
        if dev.get("type") == "ethernet" and ip and not ip.startswith("10.42."):
            add(ip, f"its cable ({dev.get('device')})", True)
    try:
        from core.services import hostname                     # noqa: PLC0415
        add(hostname.local_name(), "its name on the network", False)
    except Exception:                                         # noqa: BLE001
        pass
    return out


def start(mode, via="", network="", ssid="", password="", hidden=False,
          config_dir=None, devices=None):
    """Check the request, mark the switch under way and run it on a thread of
    its own. Returns state() at once: the page polls for the outcome, and a
    caller on wlan0 gets this reply before the radio moves (START_DELAY_S)."""
    from core.services import apconf                          # noqa: PLC0415
    mode = str(mode or "").lower()
    if mode not in netconf.MODES:
        raise NetError("mode must be ap or wifi")
    if busy():               # (again under the lock below: two quick clicks)
        raise NetError("a Wi-Fi switch or search is already under way - wait for it")
    ap_ssid = apconf.ap_ssid(config_dir)
    ap_key = netconf.ensure_ap_password(config_dir)
    network, ssid = str(network or "").strip(), str(ssid or "").strip()
    new = (ssid, str(password or ""), bool(hidden)) if ssid else None
    if new:                  # the helper's key rule, BEFORE the AP goes down
        from core.services.wifitest import _check             # noqa: PLC0415
        _check(ssid, password)
    is_ap = bool(netconf.ap_active())
    if (mode == "ap" and is_ap) or (mode == "wifi" and not is_ap and not network and not new):
        netconf.set_wifi_mode(mode, config_dir=config_dir)
        with _lock:
            _state.clear()
        _finish("done", "wlan0 is already " + ("the access point." if is_ap else "on Wi-Fi."),
                to=mode, reason="")
        return state()
    if mode == "wifi" and not new:
        saved = [s["name"] for s in netconf.saved_wifi()]
        if network and network not in saved:
            raise NetError(f'there is no saved network called "{network}"')
        if not saved:
            raise NetError("this box has no saved Wi-Fi network yet - type the "
                           "name and password of the one to join")
    where = candidates(mode, network, ssid, devices)
    with _lock:
        if _state.get("state") == "switching" or searching:
            raise NetError("a Wi-Fi switch or search is already under way - wait for it")
        _state.clear()
        _state.update(state="switching", to=mode, started=time.time(), reason="",
                      candidates=where,
                      network=network or ssid or (ap_ssid if mode == "ap" else ""),
                      message="Switching wlan0 to " + ("the access point" if mode == "ap"
                                                       else "Wi-Fi") + " now.")
    _panel_note("Switching WiFi...")
    delay = START_DELAY_S if via == netconf.AP_DEVICE else 0.5

    def run():
        time.sleep(delay)
        try:
            if mode == "wifi":
                _to_wifi(network, new, ap_ssid, ap_key, config_dir)
            else:
                _to_ap(ap_ssid, ap_key, config_dir)
        except Exception as exc:                              # noqa: BLE001
            _finish("failed", f"The switch stopped: {exc}", reason="failed")
        finally:
            _after()
    threading.Thread(target=run, name="wifi-switch", daemon=True).start()
    return state()
