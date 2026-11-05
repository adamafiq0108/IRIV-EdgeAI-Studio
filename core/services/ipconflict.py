# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
ipconflict.py -- is another device already using this address?

The Test button beside the static IP field, and (Tok Aba, 2026-10-09: "when
detected ip conflict then tell it is conflict") the same question asked again
by the Apply route before a fixed address is written. Two machines on one
address shows up as a page that simply stops loading, on the interface the
operator is holding, so the answer has to be plain: conflict (with the other
device's MAC when the box learnt it), free, or this box's own address.

ARP, not just ping. Until 2026-10-09 this was one ping, and a device that
drops ICMP (a Windows PC with its firewall on, many PLCs and cameras) read as
"free" while it held the address. Every device on the wire has to answer ARP
for its address, so:

  arping -D   duplicate address detection, sent from 0.0.0.0 on the port, so
              it works for an address outside the port's current subnet too.
              Used when the box has an arping that may send (iputils-arping;
              not on every image, and it needs CAP_NET_RAW).
  ping + neighbour table
              otherwise: the ping makes the kernel ARP for the address, and a
              REACHABLE neighbour entry right after it is an answer even when
              the echo is dropped. Only REACHABLE counts: a STALE entry may
              be a device that left last week. Only works for an address on
              the port's current subnet (the kernel ARPs only on-link); for
              any other the answer is "could not test", never "free".

No privilege, no sudo, nothing written. Reads go through `run` so the tests
answer from canned outputs.
"""

import ipaddress
import os
import re
import shutil
import subprocess

from core.services.netaddr import NetError, valid_ip


def _run(argv, timeout=8):
    """(returncode, stdout + stderr); 127 when the program is missing."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except (OSError, subprocess.SubprocessError):
        return 124, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


#: Replaced by the tests.
run = _run
which = shutil.which

_MAC = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})")


def _answer(ip, in_use, checked, detail, mac="", own=False, how=""):
    return {"ok": True, "ip": ip, "in_use": in_use, "checked": checked, "own": own,
            "mac": mac, "method": how, "detail": detail}


def _conflict(ip, mac, how):
    return _answer(ip, True, True, f"IP conflict: {ip} is already used by another "
                   "device" + (f" ({mac.lower()})" if mac else ""), mac.lower(), how=how)


def _free(ip, how):
    return _answer(ip, False, True, f"No conflict: {ip} is free", how=how)


def _arping(ip, device):
    """True/False when arping answered the question, None when it could not
    (missing, not allowed to send, or not the iputils one)."""
    if not device or not which("arping"):
        return None, ""
    rc, out = run(["arping", "-D", "-c", "2", "-w", "3", "-I", device, ip], timeout=6)
    for line in out.splitlines():
        if "reply from" in line.lower():
            mac = _MAC.search(line)
            return True, mac.group(1) if mac else ""
    # iputils with -D: 0 is "no reply"; anything else with no reply line is
    # an arping that did not run (no capability, a different arping).
    if rc == 0 and re.search(r"Sent \d+ probes?", out):
        return False, ""
    return None, ""


def _neighbour(ip, device):
    """(state, mac) of the kernel's neighbour entry for `ip` on `device`."""
    rc, out = run(["ip", "neigh", "show", ip, "dev", device], timeout=5)
    if rc != 0 or not out.strip():
        return "", ""
    words = out.split()
    mac = words[words.index("lladdr") + 1] if "lladdr" in words[:-1] else ""
    return words[-1].upper(), mac


def address_in_use(ip, device="", devs=None):
    """{ok, ip, in_use, checked, own, mac, method, detail} for `ip`, asked
    from `device`. `checked` False means nobody could be asked: never read it
    as free. `devs` is netconf.devices(), passed by a caller that has it."""
    ip = str(ip or "").strip()
    device = str(device or "").strip()
    if not valid_ip(ip):
        raise NetError(f"{ip or 'that'} is not an IPv4 address")
    if devs is None:
        from core.services import netconf                         # noqa: PLC0415
        devs = netconf.devices()
    holder = next((d for d in devs if d.get("ip") == ip), None)
    if holder:
        # Our own address answers every ping and every ARP; that is not a
        # clash, it is the address this box already holds.
        return _answer(ip, False, True, f"{ip} is already this box's address"
                       + (f" (on {holder['device']})" if device
                          and holder.get("device") != device else ""), own=True, how="own")
    entry = next((d for d in devs if d.get("device") == device), None) if device else None
    if device and not (entry and entry.get("ip")):
        # An unplugged jack has nothing to ask FROM.
        return _answer(ip, False, False, f"{device} is not connected, so the address "
                       "cannot be tested from it yet")
    used, mac = _arping(ip, device)
    if used is True:
        return _conflict(ip, mac, "arping")
    if used is False:
        return _free(ip, "arping")
    on_link = True
    if entry:
        try:
            on_link = ipaddress.ip_address(ip) in ipaddress.ip_network(
                f"{entry['ip']}/{entry.get('prefix') or 24}", strict=False)
        except ValueError:
            on_link = False
    argv = ["ping", "-n", "-c", "1", "-W", "1"] + (["-I", device] if device else []) + [ip]
    rc, _ = run(argv, timeout=6)
    if rc == 127:
        return _answer(ip, False, False, "this box cannot test an address from here")
    state, mac = _neighbour(ip, device) if device else ("", "")
    if rc == 0 or (state == "REACHABLE" and mac):
        return _conflict(ip, mac, "ping" if rc == 0 else "arp")
    if not on_link:
        return _answer(ip, False, False, f"{ip} is not on {device}'s current network, so "
                       "this box cannot ask for it from there yet")
    return _free(ip, "ping+arp")


def _own_macs():
    """Every MAC this box's own interfaces carry, lower case."""
    macs = set()
    try:
        names = os.listdir("/sys/class/net")
    except OSError:
        return macs
    for name in names:
        try:
            with open(f"/sys/class/net/{name}/address", encoding="ascii") as fh:
                macs.add(fh.read().strip().lower())
        except OSError:
            continue
    return macs


#: Replaced by the tests.
own_macs = _own_macs


def held_address_clash(ip, device):
    """Does another device answer for an address this box already HOLDS (or
    is set to hold) on `device`? True, False, or None when nobody could be
    asked. Never raises.

    The front panel's question (product owner, 2026-10-11: say "IP conflict"
    on the OLED when a static address is taken). address_in_use() cannot
    answer it: it calls the box's own address "own" before asking anyone,
    and its ping fallback is answered by the box itself. arping -D can,
    because the probe goes out from 0.0.0.0 and the box does not hear its
    own broadcast; it also works while NetworkManager is holding the address
    back after its own DAD failed. So arping only: no arping, no capability,
    a port that is down -- None, which the panel shows as nothing.

    A reply from one of this box's own MACs is not a conflict: with two
    ports on one switch, Linux answers ARP for eth0's address on eth1 too.
    """
    try:
        if not valid_ip(ip):
            return None
        used, mac = _arping(ip, device)
        if used is True and mac and mac.lower() in own_macs():
            return False
        return used
    except Exception:                                          # noqa: BLE001
        return None
