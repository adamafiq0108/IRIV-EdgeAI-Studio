# -*- coding: utf-8 -*-
"""
tlsnames.py -- every name and address a browser may use to reach THIS
box right now: what the HTTPS certificate has to cover (https.py).

Asked of the kernel, never of the network and never with a fork: this is
read inside a TLS handshake (rate-limited there), so it has to cost
microseconds. IPv4 per interface by ioctl (hostinfo.ip_of, the front
panel's own way), IPv6 from /proc/net/if_inet6.

IPv6 TEMPORARY (privacy) addresses are left out on purpose: the kernel
replaces them every day or so, nobody types one, and following them would
re-issue the certificate daily. Link-local fe80:: is left out too (a URL
would need a zone id no browser takes). Stable global and ULA addresses
are in.
"""

import ipaddress

#: /proc/net/if_inet6 flag bits (linux/if_addr.h).
IFA_F_TEMPORARY = 0x01
IFA_F_DEPRECATED = 0x20
IFA_F_TENTATIVE = 0x40
LOOPBACK = ("127.0.0.1", "::1")


def hostname():
    from core.services import hostname as _hostname           # noqa: PLC0415
    return _hostname.current().lower()


def mdns_name():
    """The .local name avahi actually won ("iriv-2.local"), or ""."""
    from core.services import discovery_mdns                  # noqa: PLC0415
    return (discovery_mdns.own_host() or "").lower()


def ipv4s():
    from core.services import discovery_mdns                  # noqa: PLC0415
    try:
        return [ip for ip in discovery_mdns.interface_ips() if ip not in LOOPBACK]
    except Exception:                                          # noqa: BLE001
        return []


def ipv6s(path="/proc/net/if_inet6"):
    """Stable, routable IPv6 addresses of this box (see the header)."""
    out = []
    try:
        with open(path, encoding="ascii") as fh:
            rows = fh.read().splitlines()
    except OSError:
        return out
    for row in rows:
        cols = row.split()
        if len(cols) < 6 or cols[5] == "lo":
            continue
        try:
            flags = int(cols[4], 16)
            ip = ipaddress.IPv6Address(int(cols[0], 16))
        except ValueError:
            continue
        if flags & (IFA_F_TEMPORARY | IFA_F_DEPRECATED | IFA_F_TENTATIVE):
            continue
        if ip.is_link_local or ip.is_loopback or ip.is_multicast:
            continue
        out.append(str(ip))
    return out


def current():
    """(names, ips): <host>.local first (it becomes the subject), avahi's
    name if it differs, localhost; loopback and every interface address.
    NOT the bare hostname: no root may vouch for a single label (tlscert.py,
    Pipi M1), so a browser at https://<host>/ can never validate, and a
    rename must not read as a name the certificate lacks."""
    host = hostname()
    names = [f"{host}.local", mdns_name(), "localhost"]
    names = [n for n in dict.fromkeys(names) if n]
    ips = list(dict.fromkeys(list(LOOPBACK) + ipv4s() + ipv6s()))
    return names, ips

