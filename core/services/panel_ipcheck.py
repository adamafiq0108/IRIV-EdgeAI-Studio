# -*- coding: utf-8 -*-
"""
panel_ipcheck.py -- is a static address on the IP screen taken by somebody else?

Product owner (2026-10-11): when a STATIC address set on a port clashes with
another device on the network, the OLED's IP screen says "IP conflict" on
that port's line. The Network page only asks before a save (its Test button
and Apply, ipconflict.py); a box that is already up on its address, with a
second device plugged in later, never asked again.

The question is the same one, asked the same way (ipconflict.held_address_
clash: one arping -D, the standard duplicate address probe, nothing written),
with three limits that keep it off the network and off the screen's back:

  * only while the IP screen is up: the panel pokes this when it draws that
    page, and nothing else does;
  * at most once every INTERVAL seconds, in a daemon thread -- an arping
    takes up to three seconds per port, and the display thread must never
    wait for one;
  * static ports only (NetworkManager method "manual"). A DHCP lease is the
    DHCP server's to keep unique, and the hotspot (method "shared") is ours.

Anything that goes wrong -- no arping, no capability, no nmcli, a port that
is down -- is "no conflict shown". A front panel that cries wolf is worse
than one that stays quiet, and the Network page's Test is still there.
"""

import threading
import time

#: The ports the IP screen prints.
WATCH = ("eth0", "eth1", "wlan0")

#: Seconds between two checks while the IP screen stays up.
INTERVAL = 45.0

#: A result older than this is not shown. The screen was left on another page
#: for a while; the next check on reopening answers again within seconds, and
#: until then a conflict that may have gone is not printed.
SHOW_FOR = 120.0

_lock = threading.Lock()
_state = {"started": None, "done": None, "found": frozenset(), "busy": False}


def check_now():
    """One pass over the static ports, synchronously: the set of ports a
    probe found taken. The thread body; the tests call it directly."""
    from core.services import ipconflict, netconf             # noqa: PLC0415
    try:
        devs = netconf.devices()
    except Exception:                                          # noqa: BLE001
        return frozenset()
    found = set()
    for dev in devs or []:
        name = dev.get("device")
        if name not in WATCH or dev.get("method") != "manual":
            continue
        # The live address, else the profile's: NetworkManager can hold a
        # static address back after its own DAD failed, and that is exactly
        # the jack that shows blank while it is in conflict.
        ip = dev.get("ip") or dev.get("static_ip") or ""
        if ip and ipconflict.held_address_clash(ip, name) is True:
            found.add(name)
    return frozenset(found)


def _run(on_change):
    try:
        found = check_now()
    except Exception:                                          # noqa: BLE001
        found = frozenset()
    with _lock:
        changed = found != _state["found"]
        _state.update(found=found, done=time.monotonic(), busy=False)
    if changed and on_change:
        try:
            on_change()
        except Exception:                                      # noqa: BLE001
            pass


def poke(on_change=None):
    """Start a check if none is running and the last began INTERVAL ago.
    Returns at once; `on_change` is called (from the worker) when the answer
    differs from the last one, so the panel can redraw. Never raises."""
    now = time.monotonic()
    with _lock:
        last = _state["started"]
        if _state["busy"] or (last is not None and now - last < INTERVAL):
            return False
        _state.update(started=now, busy=True)
    try:
        threading.Thread(target=_run, args=(on_change,), name="panel-ipcheck",
                         daemon=True).start()
    except Exception:                                          # noqa: BLE001
        with _lock:
            _state["busy"] = False
        return False
    return True


def conflicts():
    """The ports to print as in conflict: the last answer, while fresh."""
    with _lock:
        done = _state["done"]
        if done is None or time.monotonic() - done > SHOW_FOR:
            return frozenset()
        return _state["found"]
