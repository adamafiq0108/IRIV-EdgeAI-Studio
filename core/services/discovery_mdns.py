# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
discovery_mdns.py -- finding other IRIV boxes where a /24 sweep cannot.

Two boards cabled straight together fall back to 169.254 (65a41a81), each
at a RANDOM address across the /16: its own /24 is 254 probes at strangers,
and the whole /16 is 65,024 connects -- about seven minutes at 64 workers
and 0.4 s each. Multicast DNS works on a bare cable (avahi-daemon is on every
box, 224.0.0.251 needs no router), so every hub PUBLISHES _irivai._tcp and
every sweep also BROWSES it:

  publish   avahi-publish-service, supervised by the hub (Publisher below).
            TXT: txtvers, version, role, serial, hub port, agent port.
  browse    avahi-browse -rpt _irivai._tcp, parsed (parse_browse).

AN ADVERTISEMENT IS A HINT, NOT AN IDENTITY. Anything on the LAN can publish
_irivai._tcp with any serial in it. A browsed address is only ever added to
the list of addresses the sweep PROBES; whether it is a device of this system,
and which one, is still decided by /api/agent/info (system_slug, then
host_serial) exactly as for a swept address (discovery.py). Nothing here
enrols, pairs or trusts.

The "cheap /16 sweep" for a link-local box is the kernel's neighbour table
(/proc/net/arp, one file read): any 169.254 peer that has exchanged a packet
with this box. Only when this box is itself link-local.

Both tools come from avahi-utils; without it this is a no-op, logged once.
"""

import ipaddress
import logging
import re
import shutil
import socket
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_irivai._tcp"
#: Bumped only when a TXT key changes meaning; readers ignore keys they lack.
TXT_VERSION = "1"
AGENT_PORT = 5101
LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")
#: RFC 3927 sec 2.1: the first and last /24 are reserved, never assigned.
_LL_RESERVED = (ipaddress.ip_network("169.254.0.0/24"),
                ipaddress.ip_network("169.254.255.0/24"))
BROWSE_TIMEOUT_S = 6.0
#: Re-read our own advertised name this often: avahi renames "iriv" to
#: "iriv-2" on a collision and says so only on the wire.
OWN_NAME_EVERY_S = 600.0


def _run(argv, timeout):
    """stdout of argv, or "" -- a missing tool is not an error here."""
    if not shutil.which(argv[0]):
        return ""
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


# -- what this box says about itself -----------------------------------------

def txt_record(version, role, serial, hub_port, agent_port=AGENT_PORT):
    """The TXT strings, in a fixed order. Nothing secret: the serial is
    already on the unauthenticated /api/agent/info and in the AP's name."""
    return ["txtvers=" + TXT_VERSION, "version=%s" % version, "role=%s" % role,
            "serial=%s" % serial, "hub_port=%d" % int(hub_port),
            "agent_port=%d" % int(agent_port)]


def publish_argv(name, hub_port, txt):
    """argv for avahi-publish-service. The service's port is the hub's: a
    browser pointed at the advertisement should land on the dashboard."""
    return ["avahi-publish-service", name, SERVICE_TYPE, str(int(hub_port))] + list(txt)


# -- reading what the others say ---------------------------------------------

_ESCAPE = re.compile(r"\\(\d{3}|.)")
_TXT = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _unescape(text):
    """avahi-browse -p writes ';', spaces and non-ASCII as \\DDD (decimal)."""
    return _ESCAPE.sub(lambda m: chr(int(m.group(1))) if m.group(1).isdigit()
                       else m.group(1), text)


def parse_browse(text):
    """`avahi-browse -rpt` output -> [{name, iface, host, address, port, txt}].

    Only resolved ("=") IPv4 lines: the sweep probes IPv4, and an IPv6
    line for the same box is the same box. One entry per (name, address).
    """
    out, seen = [], set()
    for line in (text or "").splitlines():
        parts = line.split(";", 9)
        if len(parts) < 9 or parts[0] != "=" or parts[2] != "IPv4":
            continue
        try:
            address = str(ipaddress.IPv4Address(parts[7]))
            port = int(parts[8])
        except ValueError:
            continue
        name = _unescape(parts[3])
        if (name, address) in seen:
            continue
        seen.add((name, address))
        txt = {}
        for item in _TXT.findall(parts[9] if len(parts) > 9 else ""):
            key, _, value = _unescape(item).partition("=")
            txt[key] = value
        out.append({"name": name, "iface": parts[1], "host": _unescape(parts[6]),
                    "address": address, "port": port, "txt": txt})
    return out


def browse(runner=None, timeout=BROWSE_TIMEOUT_S):
    """Every _irivai._tcp service answering now. `runner(argv, timeout)` is
    the seam a test replaces; the real one forks avahi-browse once."""
    run = runner or _run
    return parse_browse(run(["avahi-browse", "-rpt", SERVICE_TYPE], timeout))


def _read_arp():
    try:
        with open("/proc/net/arp", "r", encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def neighbours(read=None):
    """Link-local IPv4 peers in the kernel's neighbour table (complete only)."""
    out = set()
    for line in (read or _read_arp)().splitlines()[1:]:
        cols = line.split()
        if len(cols) < 3 or cols[2] == "0x0":
            continue
        try:
            ip = ipaddress.IPv4Address(cols[0])
        except ValueError:
            continue
        if ip in LINK_LOCAL and not any(ip in r for r in _LL_RESERVED):
            out.add(str(ip))
    return sorted(out)


def interface_ips(names=None, ip_of=None):
    """The IPv4 address of every interface, asked of the kernel.

    The sweep's own trick (route lookup + getaddrinfo(hostname)) finds
    NOTHING on a link-local box: there is no default route to look up, and
    the hostname resolves to 127.0.1.1. Interfaces are what is left.
    """
    if ip_of is None:
        from core.services.hostinfo import ip_of                # noqa: PLC0415
    if names is None:
        try:
            names = [n for _, n in socket.if_nameindex()]
        except (OSError, AttributeError):
            names = []
    return sorted({ip for ip in (ip_of(n) for n in names if n != "lo") if ip})


def candidate_hosts(local_ips, runner=None, read_arp=None, own=()):
    """Extra addresses for a sweep to probe, beside its subnets.

    Every address an IRIV box advertises over mDNS, plus -- only when this
    box is itself link-local -- the 169.254 neighbours. This box's own
    addresses are dropped (`own`, and the link-local ones in local_ips).
    """
    mine = set(own) | set(local_ips)
    found = {s["address"] for s in browse(runner)}
    if any(ipaddress.IPv4Address(ip) in LINK_LOCAL for ip in local_ips):
        found.update(neighbours(read_arp))
    return sorted((ip for ip in found if ip not in mine),
                  key=ipaddress.IPv4Address)


# -- the hub's own advertisement ---------------------------------------------

def _die_with_parent():
    """Linux: SIGTERM the publisher if the hub dies without cleaning up, so
    a killed hub does not leave a ghost advertisement behind."""
    try:
        import ctypes                                           # noqa: PLC0415
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, 15)   # PR_SET_PDEATHSIG
    except Exception:                                          # noqa: BLE001
        pass


class Publisher:
    """Keeps one avahi-publish-service alive for the hub's lifetime.

    The child is restarted if it exits (avahi-daemon restarted, say), with a
    backoff, and is stopped with the hub. `own_host` is the name avahi
    actually gave this box, learnt by browsing for our own serial.
    """

    def __init__(self, name, hub_port, txt, serial, runner=None, on_name=None):
        self.argv = publish_argv(name, hub_port, txt)
        self.serial = serial
        self.runner = runner
        self.on_name = on_name          # called when own_host changes
        self.own_host = ""
        self.learn_due = False
        self._proc = None
        self._stop = threading.Event()

    def start(self):
        if not shutil.which(self.argv[0]):
            logger.info("[MDNS] avahi-publish-service not installed (avahi-utils) "
                        "- this box is found by the /24 sweep only")
            return False
        threading.Thread(target=self._loop, name="mdns-publish", daemon=True).start()
        return True

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def learn_own_host(self):
        before, self.own_host = self.own_host, ""
        for svc in browse(self.runner):
            if svc["txt"].get("serial") == self.serial and svc["host"]:
                self.own_host = svc["host"].rstrip(".")
                break
        if self.own_host != before and self.on_name:
            self.on_name()
        return self.own_host

    def _loop(self):
        backoff, learnt_at = 2.0, 0.0
        while not self._stop.is_set():
            if self._proc is None or self._proc.poll() is not None:
                try:
                    self._proc = subprocess.Popen(
                        self.argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        preexec_fn=_die_with_parent)
                    logger.info("[MDNS] publishing %s on port %s", SERVICE_TYPE, self.argv[3])
                    learnt_at = time.monotonic() - OWN_NAME_EVERY_S + 5.0
                except OSError as exc:
                    logger.warning("[MDNS] could not publish: %s", exc)
                    self._stop.wait(min(backoff, 300.0))
                    backoff *= 2
                    continue
            if self.learn_due or time.monotonic() - learnt_at >= OWN_NAME_EVERY_S:
                self.learn_due = False
                self.learn_own_host()
                learnt_at = time.monotonic()
            self._stop.wait(5.0)


_publisher = None


def own_host():
    """The .local name avahi gave this box, or "" before it is known."""
    return _publisher.own_host if _publisher else ""


def renamed():
    """The box was just renamed: the name avahi won is stale until re-read,
    and the front panel prints <hostname>.local meanwhile."""
    if _publisher:
        _publisher.own_host, _publisher.learn_due = "", True
    _reload_panel()


def _reload_panel():
    try:
        from core.services import panel                        # noqa: PLC0415
        if panel.current():
            panel.current().reload()
    except Exception:                                          # noqa: BLE001
        logger.exception("[MDNS] front panel did not take the new name")


def start(config_dir=None):
    """Hub start: advertise this box unless [mdns].advertise = false."""
    global _publisher
    from core import config                                    # noqa: PLC0415
    from core.services import hostinfo, hubport, webaccess    # noqa: PLC0415
    from core.version import VERSION                           # noqa: PLC0415
    cfg = dict(config.load("network", config_dir).get("mdns") or {})
    if cfg.get("advertise", True) is False or _publisher is not None:
        return False
    serial = hostinfo.serial_number()
    _publisher = Publisher(hostinfo.ap_name(), webaccess.advertised_port(config_dir),
                           txt_record(VERSION, "hub", serial, hubport.port(config_dir))
                           + webaccess.txt_extra(config_dir), serial, on_name=_reload_panel)
    return _publisher.start()
