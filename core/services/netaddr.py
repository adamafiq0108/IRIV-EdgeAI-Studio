# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
netaddr.py -- the address arithmetic behind the Network page.

Everything here is a pure function over strings and ints: what a subnet mask
means, which addresses a host can never use, and which two interfaces of one
box would fight over the same wire. None of it runs nmcli. It was lifted out
of netconf.py, which owns the TALKING to NetworkManager, so that the rules an
address has to pass can be read -- and tested -- without a box.

The rules are refusals, not warnings, because every one of them is accepted
by nmcli without a word and found out only after the installer has gone:
the box is then unreachable on the very interface the page was served over.
"""

import ipaddress
import re


class NetError(Exception):
    """A command NetworkManager refused, or an address that cannot work."""


#: A dotted IPv4 address, and nothing else. No leading zero in an octet
#: (Pipi, 2026-10-09): ping and arping read "1.2.3.07" through inet_aton
#: (octal, and "08" is a host NAME), ipaddress refuses it outright, so an
#: address this let by was tested as one thing and crashed the write (500).
_IPV4_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$")


def valid_ip(value):
    return bool(_IPV4_RE.match(str(value or "").strip()))


def mask_to_prefix(value):
    """255.255.255.0 -> 24. A number is passed through.

    The operator's form asks for a SUBNET MASK, because that is the word on
    every other device on the installer's bench and on the sheet the site
    hands them. NetworkManager wants a prefix length. The translation is
    here, where the address is applied, rather than only in the browser: a
    mask reaching this function from anywhere has to mean the same thing.
    """
    text = ("" if value is None else str(value)).strip()
    if text.isdigit():
        n = int(text)
        if 0 <= n <= 32:
            return n
        raise NetError(f"{text} is not a prefix length between 0 and 32")
    if not valid_ip(text):
        raise NetError(f"{text or 'that'} is not a subnet mask")
    bits = 0
    seen_zero = False
    for part in text.split("."):
        for i in range(7, -1, -1):
            if int(part) & (1 << i):
                # 255.0.255.0 is not a mask: once the ones stop they stop.
                if seen_zero:
                    raise NetError(f"{text} is not a valid subnet mask")
                bits += 1
            else:
                seen_zero = True
    return bits


def prefix_to_mask(value):
    """24 -> 255.255.255.0, for showing back what is configured."""
    try:
        n = int(("" if value is None else str(value)).strip())
    except (TypeError, ValueError):
        return ""
    if not 0 <= n <= 32:
        return ""
    bits = ((1 << n) - 1) << (32 - n) if n else 0
    return ".".join(str((bits >> shift) & 0xFF) for shift in (24, 16, 8, 0))


#: The narrowest mask a box on a site network is ever given. Anything wider
#: than a /8 claims a slice of the internet as "on this wire" -- the box then
#: ARPs for addresses it should route -- and is a typo, not a design.
MIN_PREFIX = 8


def refuse_bad_host(ip, bits, gateway):
    """An address NetworkManager would take, but that cannot work as a host.

    Each of these is accepted by nmcli without a word and leaves the box
    unreachable or routeless once it is live -- which, for a preset, is
    discovered only after the cable is in and the installer has gone:
      * a mask wider than /8 (0 included: "the whole internet is local");
      * the subnet's own network or broadcast address as the box's address;
      * a gateway outside the subnet (nothing on the wire can be asked to
        forward to it), a gateway that is the network or broadcast
        address, or a gateway that is the box itself.
    /31 and /32 have no network/broadcast pair, so that check skips them.
    """
    if bits < MIN_PREFIX:
        raise NetError(f"/{bits} ({prefix_to_mask(bits)}) is wider than any "
                       f"site network - the mask must be /{MIN_PREFIX} "
                       "(255.0.0.0) or narrower")
    net = ipaddress.ip_network(f"{ip}/{bits}", strict=False)
    host = ipaddress.ip_address(ip)
    if bits <= 30 and host in (net.network_address, net.broadcast_address):
        which = "network" if host == net.network_address else "broadcast"
        raise NetError(f"{ip} is the {which} address of {net}, not one a "
                       "device can use")
    if not gateway:
        return
    gw = ipaddress.ip_address(gateway)
    if gw == host:
        raise NetError(f"the gateway {gateway} is this box's own address - "
                       "it must be the router on that network")
    if gw not in net:
        raise NetError(f"the gateway {gateway} is outside {net}, so nothing "
                       "on that network can reach it - check the address "
                       "and the subnet mask")
    if bits <= 30 and gw in (net.network_address, net.broadcast_address):
        raise NetError(f"the gateway {gateway} is the network or broadcast "
                       f"address of {net}")


#: The resolver addresses a profile may name. glibc reads the first three
#: nameserver lines of resolv.conf and ignores the rest, so a fourth would be
#: saved and silently never asked.
MAX_DNS = 3


def dns_servers(text):
    """The DNS field ("8.8.8.8, 1.1.1.1", commas or spaces) as a list, or [].

    Tok Aba (2026-10-09): the static form gets its own DNS servers. Each one
    has to be a resolver some packet can actually reach: 0.0.0.0, broadcast,
    multicast, the reserved block and loopback (no resolver listens on the
    box itself) are all taken by nmcli without a word and resolve nothing,
    which shows up later as "no internet" on a box whose route is fine. The
    values end up on nmcli's argv through sudo, so nothing that is not a
    plain dotted address gets past here. A repeat is dropped, not refused.
    """
    out = []
    for part in re.split(r"[,\s]+", str(text or "")):
        part = part.strip()
        if not part:
            continue
        try:
            addr = ipaddress.IPv4Address(part) if valid_ip(part) else None
        except ValueError:              # "010.0.0.1": the regex lets it by
            addr = None
        if addr is None:
            raise NetError(f"{part} is not an IPv4 address")
        if addr.is_unspecified or addr.is_multicast or addr.is_reserved \
                or addr.is_loopback or part == "255.255.255.255":
            raise NetError(f"{part} cannot be a DNS server - use the address "
                           "of a resolver, such as 8.8.8.8 or 1.1.1.1")
        if part not in out:
            out.append(part)
    if len(out) > MAX_DNS:
        raise NetError(f"at most {MAX_DNS} DNS servers - the box never asks "
                       "a fourth")
    return out


# -- two jacks, one network ------------------------------------------------------
# A box with eth0 AND eth1 on 192.168.1.0/24 is not "twice as connected". The
# kernel keeps ONE connected route per subnet per interface and sends every
# packet for that subnet out the lower-metric one, whatever address it came
# in on; Linux also answers an ARP request for either address on BOTH ports.
# On one switch that is ARP flux (the switch's table flips between the two
# MACs); on two cables -- one to the site switch, one straight into a second
# iriv -- the reply to the second iriv goes out towards the switch and never
# arrives. Seen on the customer board on 2026-10-04: eth1 fixed at .20, eth0
# then preset to .21 on the same /24. The moment both cables are in, the box
# is reachable on one of the two addresses, and which one is luck.

def wired_subnet(entry):
    """The subnet a wired interface is on, or WILL be on when its cable goes
    in: the live address first, else a saved fixed one. None for a jack on
    DHCP with no lease -- there is nothing yet to collide with."""
    if entry.get("type") != "ethernet":
        return None
    ip, prefix = entry.get("ip"), entry.get("prefix")
    if not ip and entry.get("method") == "manual":
        ip, prefix = entry.get("static_ip"), entry.get("static_prefix")
    if not ip:
        return None
    try:
        return ipaddress.ip_network(f"{ip}/{prefix or 24}", strict=False)
    except ValueError:
        return None


def refuse_sibling_overlap(device, ip, bits, devs):
    """Refuse a fixed wired address inside another wired jack's subnet.

    The other jack's subnet is what it HAS (lease or fixed) or what it is
    SET to have (a preset waiting for its cable): a dormant profile is
    exactly the one that bites when the second cable goes in. Wi-Fi is not a
    sibling here -- the access point has its own refusal, and a joined
    network sits behind a metric that settles the question. Refused rather
    than warned about: nothing good ever comes of the configuration, and the
    way out is one step (put the other jack on DHCP or another network).
    """
    try:
        mine = ipaddress.ip_network(f"{ip}/{bits}", strict=False)
    except ValueError:
        return
    for other in devs or ():
        if other.get("device") == device:
            continue
        net = wired_subnet(other)
        if net is not None and mine.overlaps(net):
            raise NetError(f"{ip}/{bits} is on the same network as {other['device']} "
                           f"({net}). Two ports of one box on one network leave it "
                           "unable to tell which cable to answer on - give "
                           f"{device} a different network, or put {other['device']} "
                           "on DHCP or another network first")


def shared_subnets(devs):
    """{device: [other devices LIVE on an overlapping subnet]}, wired only.

    For the page: two jacks that were cabled into one network by hand (both
    on DHCP, say) pass no write here, so the only place to say so is beside
    the interfaces themselves once it has happened.
    """
    live = [(d["device"], wired_subnet(d)) for d in devs
            if d.get("ip") and wired_subnet(d) is not None]
    return {name: [o for o, onet in live if o != name and net.overlaps(onet)]
            for name, net in live}
