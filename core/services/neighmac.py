# -*- coding: utf-8 -*-
"""
neighmac.py -- the MAC address the box's own neighbour table holds for an IP.

Shown beside a registered sign-in device as EXTRA information, never as an
identity. A browser never sends its MAC; the box can only see the address of
whatever last answered ARP for that IP on one of its own links. So:

  * a client behind a router shows the ROUTER's side of things, or nothing --
    the table only has on-link peers, and a remote IP is not in it at all;
  * a phone on a private ("randomised") Wi-Fi address shows the random one;
  * anyone on the LAN can answer ARP with any MAC they like.

That is why the UI prints "-" when there is nothing and never calls a value
verified. It is also why this is a lookup by IP only, and cheap:

  * /proc/net/arp is ONE file read, no process at all. That is the source on
    the box (the kernel's IPv4 neighbour table, the same one `ip neigh` reads).
  * Only where that file cannot be read does it fall back to `ip -4 neigh
    show <ip>`, with a FIXED argv, no shell, a short timeout, and the IP
    re-printed from ipaddress (so nothing a client sent can reach the argv).
  * Answers are cached per IP for CACHE_S, so a Sessions page polled every
    30 s by several admins costs one read a minute, not one per row per poll.

No framework imports (docs/conventions.md sec 2).
"""

import ipaddress
import re
import subprocess
import threading
import time

ARP_PATH = "/proc/net/arp"

#: How long one IP's answer is reused. ARP entries themselves live minutes.
CACHE_S = 60

#: Ceiling on the fallback process; a wedged `ip` must not hold a page.
RUN_TIMEOUT_S = 2.0

_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_ZERO = "00:00:00:00:00:00"
_CACHE = {}
_LOCK = threading.Lock()


def usable_ip(value):
    """The IPv4 address `value` names, re-printed, or None.

    IPv4 only (ARP is IPv4; an IPv4-mapped IPv6 peer is unwrapped). Loopback,
    unspecified, multicast and broadcast never have a neighbour entry worth
    showing. Link-local stays: a laptop on a direct cable to the board is a
    real case. Anything that is not an address -- a header someone typed, a
    name, "1.2.3.4; reboot" -- is None and goes no further.
    """
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        ip = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address):
        ip = ip.ipv4_mapped
        if ip is None:
            return None
    if (ip.is_loopback or ip.is_unspecified or ip.is_multicast
            or str(ip) == "255.255.255.255"):
        return None
    return str(ip)


def _clean_mac(text):
    mac = str(text or "").strip().lower()
    return mac if _MAC.match(mac) and mac != _ZERO else ""


def _read_arp():
    """The kernel's ARP table as text, or None when it cannot be read (not
    Linux, or /proc hidden) -- None, not "", so the caller can tell 'no
    entry' from 'no table' and only the second falls back to a process."""
    try:
        with open(ARP_PATH, "r", encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def parse_arp(text, ip):
    """The MAC for `ip` in /proc/net/arp text, or "". Incomplete entries
    (flags 0x0) are skipped: they are a question the kernel asked, not an
    answer it got."""
    for line in str(text or "").splitlines()[1:]:
        cols = line.split()
        if len(cols) < 4 or cols[0] != ip or cols[2] == "0x0":
            continue
        mac = _clean_mac(cols[3])
        if mac:
            return mac
    return ""


def _run(argv):
    """Run a fixed argv, never a shell. Returns stdout, or "" on any failure
    (missing binary, non-zero exit, timeout)."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True,
                             timeout=RUN_TIMEOUT_S, shell=False, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def parse_neigh(text):
    """The MAC in `ip neigh show <ip>` output ("... lladdr aa:bb:.. REACHABLE")."""
    m = re.search(r"\blladdr\s+(\S+)", str(text or ""))
    return _clean_mac(m.group(1)) if m else ""


def lookup(value, read=None, runner=None, now=None):
    """Best-effort MAC for a client IP, "" when the box cannot see one.

    `read` and `runner` are the seams a test replaces: read() returns the ARP
    table text or None; runner(argv) returns stdout. Never raises.
    """
    ip = usable_ip(value)
    if ip is None:
        return ""
    now = time.time() if now is None else now
    with _LOCK:
        hit = _CACHE.get(ip)
        if hit is not None and now - hit[0] < CACHE_S:
            return hit[1]
    try:
        text = (read or _read_arp)()
        if text is not None:
            mac = parse_arp(text, ip)
        else:
            mac = parse_neigh((runner or _run)(["ip", "-4", "neigh", "show", ip]))
    except Exception:                                       # noqa: BLE001
        mac = ""
    with _LOCK:
        if len(_CACHE) > 512:
            _CACHE.clear()
        _CACHE[ip] = (now, mac)
    return mac


def clear_cache():
    with _LOCK:
        _CACHE.clear()
