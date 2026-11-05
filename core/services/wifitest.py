# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
wifitest.py -- "Test connection" and "Test and switch": wlan0 leaves the
access point, joins a network as a TRIAL, and either stays on it or comes
back as the access point, saying exactly why it could not join.

Tok Aba (2026-10-08): the password and the network are checked before the
box is left on them, and the reason a join failed is a specific one -- the
password, the network not there, the radio unable to get on (weak signal,
a refusing AP), no address from the network's DHCP, or out of time. Both
buttons run the same move (move() below), one trial through the root
helper (wifiscan.trial; the key on stdin):

  Test connection   keep=False. Joined or not, the helper takes the trial
                    down and deletes it; the access point comes back and the
                    page says "Wi-Fi connection is ready" with the address
                    the network gave the box (remembered: the switch that
                    follows predicts it), or the reason. Nothing is saved.
                    This is the safe way to try a password: the person never
                    left the hotspot, so putting it back is not a fallback --
                    it is the state they asked to keep.
  Test and switch   keep=True. The mode becomes wifi BEFORE the hotspot goes
                    down (the person chose Wi-Fi). Passed, the box STAYS on
                    the network (the helper turns the trial into the ordinary
                    saved profile, no bounce) and the page's overlay finds the
                    box at its new address. Failed: the reason said, NO saved
                    profile left for that SSID, and the hotspot stays OFF --
                    no automatic hotspot (Tok Aba, 2026-10-14; wifimove.py).

The access point is OFF while a trial runs (wifiscan.py says why the radio
cannot test beside it): its clients drop and rejoin when it is back. The
switch state machine is wifiswitch's own, held as "switching" for the whole
trial, so every other network write and a second switch wait for it rather
than fight it.
"""

import threading
import time

from core.services import netconf, wifiscan
from core.services.netaddr import NetError

#: The page's words for each reason the helper names (wifi_profile.classify).
REASONS = {
    "wrong_password": "the password was not accepted",
    "not_found": "the network was not found (out of range, hidden, or the name is wrong)",
    "assoc_failed": ("the box found the network but could not connect to it "
                     "(weak signal, or the router refused the box)"),
    "no_address": "it connected, but the network gave the box no address (DHCP)",
    "timeout": "it did not finish connecting in time",
    "failed": "NetworkManager refused it",
}


def _check(ssid, password):
    """Refuse before the access point goes down what the helper would refuse
    after (the helper re-checks everything; this is only the early no)."""
    ssid = str(ssid or "").strip()
    if not ssid:
        raise NetError("a network name is required")
    if len(ssid.encode("utf-8")) > 32:
        raise NetError("a network name is at most 32 bytes")
    psk = str(password or "")
    if psk and not (8 <= len(psk) <= 63 and all(32 <= ord(c) <= 126 for c in psk)
                    or (len(psk) == 64 and all(c in "0123456789abcdefABCDEF" for c in psk))):
        raise NetError("a Wi-Fi password is 8-63 printable characters (or 64 hex digits)")
    return ssid, psk


def _reason(answer):
    word = str(answer.get("reason") or "failed")
    if word == "failed" and "in time" in str(answer.get("message") or ""):
        word = "timeout"
    return word if word in REASONS else "failed"


def _detail(answer):
    """(reason, the words for it) of a trial that did not join."""
    reason = _reason(answer)
    return reason, REASONS[reason] + (f" ({answer.get('message')})" if reason == "failed"
                                      and answer.get("message") else "")


def _net_note(answer):
    """The internet check after the lease: said, never a failure (a plant
    network with no internet is a working network)."""
    if answer.get("internet") is False:
        return (" The internet did not answer through it"
                + (" (its gateway did)." if answer.get("gateway") else "."))
    return ""


def move(ssid, password, hidden, keep, ap_ssid, ap_key, config_dir):
    """The trial itself, on the caller's thread; ends in wifiswitch's state."""
    from core.services import wifimove                        # noqa: PLC0415
    from core.services import wifiswitch as ws                # noqa: PLC0415
    ap_name = netconf.ap_active()
    if keep:                 # a switch: the choice stands whatever the join does
        netconf.set_wifi_mode("wifi", config_dir=config_dir)
    try:                     # a step of its own, as in wifimove._to_wifi
        if ap_name:
            netconf._nmcli(["connection", "down", ap_name], sudo=True, timeout=30)
    except NetError:
        pass
    answer = wifiscan.trial(ssid, password, hidden, keep)
    address = str(answer.get("address") or "")
    note = _net_note(answer)
    if answer.get("ok") and address:
        ws._last_ip[ssid] = address
        if keep:
            return ws._finish("done", f"wlan0 joined {ssid} as {address}.{note}",
                              network=ssid, address=address, reason="", tested=True,
                              internet=answer.get("internet"))
    if keep:                 # Test and switch failed: no hotspot (wifimove.py)
        reason, detail = _detail(answer)
        return ws._finish("failed", wifimove.failed_join(ssid, detail), reason=reason,
                          network=ssid, test=False, ap_back=False, hotspot_off=True,
                          address="")
    # Test connection only: the person never left the hotspot; put it back.
    lost = wifimove._restore_ap(ap_ssid, ap_key, ap_name)
    back = ("" if not lost else
            f" The access point did not come back either ({lost}). Reach the box "
            "over Ethernet, or restart it: it starts as the access point.")
    if answer.get("ok") and address:
        return ws._finish(
            "done", f"Wi-Fi connection is ready: the box joined {ssid} and was given "
            f"{address}.{note}" + (back or f" The access point {ap_ssid} is back; "
                                   "nothing was saved."),
            network=ssid, address=address if lost else ws.AP_ADDRESS, tested_address=address,
            reason="", test=True, ap_back=not lost, internet=answer.get("internet"))
    reason, detail = _detail(answer)
    ws._finish("failed", f"Could not join {ssid}: {detail}."
               + (back or f" The access point {ap_ssid} is back; nothing was saved."),
               reason=reason, network=ssid, test=True, ap_back=not lost,
               address=ws.AP_ADDRESS if not lost else "")


def start(ssid, password="", hidden=False, via="", config_dir=None):
    """Test connection: check, hold the switch state, run move(keep=False)
    on a thread. Returns wifiswitch.state() at once (the page polls)."""
    from core.services import apconf                          # noqa: PLC0415
    from core.services import wifiswitch as ws                # noqa: PLC0415
    ssid, psk = _check(ssid, password)
    if not netconf.ap_active():
        raise NetError("the connection test is for a box broadcasting its access "
                       "point; on Wi-Fi, pick the network from the list instead")
    if not (netconf.wifi_helper_installed() and wifiscan.helper_can("trial")):
        raise NetError("this box's Wi-Fi helper is older than the connection test - "
                       "use Switch without testing, or update the box")
    ap_ssid = apconf.ap_ssid(config_dir)
    ap_key = netconf.ensure_ap_password(config_dir)
    with ws._lock:
        if ws._state.get("state") == "switching" or ws.searching:
            raise NetError("a Wi-Fi switch or search is already under way - wait for it")
        ws._state.clear()
        ws._state.update(state="switching", to="test", test=True, started=time.time(),
                         reason="", network=ssid,
                         candidates=[{"host": ws.AP_ADDRESS, "own": True,
                                      "why": "the access point's own address"}],
                         message=f"Testing {ssid} now. The access point {ap_ssid} is "
                                 "off until the test ends.")
    ws._panel_note("Testing WiFi...")
    delay = ws.START_DELAY_S if via == netconf.AP_DEVICE else 0.5

    def run():
        time.sleep(delay)
        try:
            move(ssid, psk, hidden, False, ap_ssid, ap_key, config_dir)
        except Exception as exc:                              # noqa: BLE001
            ws._finish("failed", f"The test stopped: {exc}", reason="failed", test=True)
        finally:
            ws._after()
    threading.Thread(target=run, name="wifi-switch", daemon=True).start()
    return ws.state()
