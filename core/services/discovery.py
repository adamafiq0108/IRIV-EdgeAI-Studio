# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
discovery.py -- Finding this system's devices on the LAN by sweeping for agents.

An address is not an identity. A DHCP lease change moves a device to a new IP
and nothing about the device itself has changed, but a master reading a stale
url reports a healthy machine as down. The host_serial is the identity that
survives that (docs/contract-v1.md sec 5), so discovery answers exactly one
question -- "where is the device I already know?" -- and never "who is this?".

Two filters, in order. Neither substitutes for the other:

  system_slug   is this device even my system? One LAN carries every system's
                devices and they all answer this endpoint just as happily.
  host_serial   is this the device I already know?

`/api/agent/info` is unauthenticated by contract (docs/contract-v1.md sec 2),
which is the whole reason a sweep is possible without offering a bearer token to
every host on the subnet.

TRUST: a scan takes an address from whoever answers it. Serials are not secrets
-- that same unauthenticated endpoint hands them out -- so a rogue agent
claiming a known host_serial could attract that device's token. This is the LAN
trust the deployment already assumes (docs/setup.md sec 8: never expose 5101
wider than its master), not a new one, but it is strictly more surface than a
static url. locate() therefore returns urls only for serials this master is
already configured for: a sweep re-finds known devices and can never enroll an
unknown one, because enrolling needs a token and an operator (docs/setup.md
sec 5).

LINK-LOCAL. Two boards on one cable with no DHCP server sit at random
169.254 addresses, which no /24 sweep finds. The sweep therefore also probes
what discovery_mdns.candidate_hosts() suggests -- addresses IRIV boxes
advertise over mDNS, and on a link-local box its 169.254 neighbours. Those
are probed and filtered exactly like swept ones: a hint, never an identity.

No framework imports here -- plain values in, plain dicts out
(docs/conventions.md sec 2).
"""

import concurrent.futures
import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request

from core.services import discovery_mdns

logger = logging.getLogger(__name__)

DEFAULT_PORT = 5101
#: Budget for one TCP connect during the sweep, not a health opinion. Almost
#: every address on a subnet has nothing listening and must fail fast; the few
#: that answer get the longer read timeout below.
DEFAULT_TIMEOUT_S = 0.4
_READ_TIMEOUT_MULTIPLIER = 5

MAX_WORKERS = 64

#: A /24 is 254 addresses and sweeps in about a second. A /16 is 65k and would
#: look like a hang. Refuse loudly instead.
MAX_HOSTS = 4096


# ---------------------------------------------------------------------------
# Where to look
# ---------------------------------------------------------------------------

def _local_ips():
    """Every non-loopback IPv4 address this machine answers on."""
    ips = set()

    # UDP connect() sends no packet -- it only forces a route lookup, which
    # names the interface a device on this LAN would be reached through.
    # TEST-NET-1 (RFC 5737) is unroutable by definition, so this cannot leak
    # traffic even where a default route exists.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    # The route trick returns the DEFAULT route's interface only, which on a
    # multi-homed master is the wrong one -- a Pi with wifi for the internet and
    # ethernet for the camera LAN would sweep the wifi subnet. getaddrinfo sees
    # every interface, so the two together cover both single- and multi-homed.
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except (socket.gaierror, OSError):
        pass

    # Neither trick sees a link-local box: no default route, and the
    # hostname resolves to 127.0.1.1. The interfaces themselves always do.
    ips.update(discovery_mdns.interface_ips())

    out = set()
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not addr.is_loopback:
            out.add(str(addr))
    return out


def local_subnets():
    """The /24s this machine has an address on.

    /24 is an assumption, not a discovery: the netmask needs a platform call or
    a dependency this repo does not carry, and a flat /24 is what the deployments
    are. Set [discovery].subnets explicitly when that is not true -- config wins
    and this function is not consulted.

    A 169.254 address gets no /24: link-local addresses are random across the
    /16, so its /24 is 254 probes at strangers. discovery_mdns covers it.
    """
    return sorted({ipaddress.ip_network(f"{ip}/24", strict=False)
                   for ip in _local_ips()
                   if ipaddress.ip_address(ip) not in discovery_mdns.LINK_LOCAL})


def _parse_subnets(values):
    out = []
    for value in values:
        try:
            out.append(ipaddress.ip_network(value, strict=False))
        except ValueError as e:
            raise ValueError(f"bad subnet {value!r} in [discovery].subnets: {e}") from e
    return out


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def _port_open(ip, port, timeout):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((str(ip), port)) == 0


def probe(ip, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT_S):
    """GET http://<ip>:<port>/api/agent/info. Returns the body, or None.

    Never raises: on a sweep, almost every address is a failure and none of them
    are exceptional. The cheap TCP connect gates the HTTP call so that a dead
    address costs one refused connect rather than a full urllib timeout.
    """
    if not _port_open(ip, port, timeout):
        return None

    url = f"http://{ip}:{port}/api/agent/info"
    try:
        with urllib.request.urlopen(url, timeout=timeout * _READ_TIMEOUT_MULTIPLIER) as r:
            body = json.loads(r.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None

    if not isinstance(body, dict) or not body.get("host_serial"):
        # Something is listening on this port, but it is not an agent.
        return None
    return body


def scan(system_slug, subnets=None, port=DEFAULT_PORT, timeout_s=DEFAULT_TIMEOUT_S,
         mdns=True):
    """Return [{host_serial, url, display_name, paired, app_running}].

    Every agent found on `subnets` (plus, with `mdns`, the addresses
    discovery_mdns suggests) that serves `system_slug`; devices belonging to
    another system are dropped here rather than by the caller.
    """
    nets = _parse_subnets(subnets) if subnets else local_subnets()
    hosts = [ip for net in nets for ip in net.hosts()]
    if len(hosts) > MAX_HOSTS:
        raise ValueError(
            f"{len(hosts)} addresses across {[str(n) for n in nets]} is too many to "
            f"sweep (limit {MAX_HOSTS}); narrow [discovery].subnets"
        )
    extra = discovery_mdns.candidate_hosts(sorted(_local_ips())) if mdns else []
    swept = set(hosts)
    hosts += [ip for ip in map(ipaddress.ip_address, extra) if ip not in swept]
    if not hosts:
        logger.warning("[SCAN] no local subnet and no mDNS peer found - "
                       "set [discovery].subnets")
        return []

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(probe, ip, port, timeout_s): ip for ip in hosts}
        for future in concurrent.futures.as_completed(futures):
            info = future.result()
            if not info or info.get("system_slug") != system_slug:
                continue
            found.append({
                # Lowercased to match agent/identity.py, which lowercases before
                # reporting. A serial that differs only in case is the same
                # device and must not read as a stranger.
                "host_serial": str(info["host_serial"]).lower(),
                "url": f"http://{futures[future]}:{port}",
                "display_name": info.get("display_name", ""),
                "paired": bool(info.get("paired")),
                "app_running": bool(info.get("app_running")),
            })

    logger.info("[SCAN] %d agent(s) for %r across %s (+%d mDNS/link-local)",
                len(found), system_slug, ", ".join(str(n) for n in nets), len(extra))
    return sorted(found, key=lambda d: d["host_serial"])


def locate(slaves, system_slug, subnets=None, port=DEFAULT_PORT,
           timeout_s=DEFAULT_TIMEOUT_S, mdns=True):
    """Return {host_serial: url} for those `slaves` that answered the sweep.

    `slaves` is entries from services.slaves.load(). Serials this master has no
    entry for are discarded even when they answer and match the slug -- see the
    module docstring: discovery re-finds, it never enrolls.
    """
    wanted = {s["host_serial"].lower() for s in slaves}
    if not wanted:
        return {}
    return {d["host_serial"]: d["url"]
            for d in scan(system_slug, subnets=subnets, port=port, timeout_s=timeout_s,
                          mdns=mdns)
            if d["host_serial"] in wanted}


def settings(slaves_cfg):
    """Read the [discovery] block into kwargs for locate(), plus `enabled`."""
    cfg = slaves_cfg.get("discovery") or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "subnets": list(cfg.get("subnets") or ()),
        "port": int(cfg.get("port") or DEFAULT_PORT),
        "timeout_s": float(cfg.get("timeout_s") or DEFAULT_TIMEOUT_S),
        # Browse _irivai._tcp (and, link-local, read the neighbour table)
        # on top of the subnets. false = the sweep alone, as before.
        "mdns": bool(cfg.get("mdns", True)),
    }
