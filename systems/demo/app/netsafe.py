# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
netsafe.py -- may this box send an HTTP request to an address a user typed?

WHY (Pipi M-R1, v1.0.38 gate; Tok Aba "do the best", 2026-10-14). A webhook
(and an API value poller) sends a request to whatever URL its node names. The
box runs services that trust any caller on the box itself -- MediaMTX's
control API on 127.0.0.1:9997 accepts a path whose "run hook" is a command,
run as the account MediaMTX runs as. So a webhook pointed at the box itself
was a way for anyone allowed to edit a workflow to run commands. The rule
here: a user-given URL may reach ANOTHER computer (the internet, or a PLC or
server on the LAN), never this one.

REFUSED, by address (after resolving a name, every address it resolves to):
  * loopback        127.0.0.0/8, ::1
  * unspecified     0.0.0.0/8 ("this network"), ::
  * link-local      169.254.0.0/16 (cloud metadata lives here), fe80::/10
  * multicast
  * metadata        100.100.100.200, fd00:ec2::254
  * this box's own interface addresses (its LAN IP too)
  and IPv4-mapped IPv6 (::ffff:127.0.0.1) is judged as the IPv4 inside it.
REFUSED, by name: localhost and *.localhost, this box's hostname (with
.local / .localdomain), metadata.google.internal and friends.
ALLOWED: everything else, private LAN ranges (10/8, 172.16/12, 192.168/16)
included, unless the address is one of this box's own.

Numbers are read the way the C library reads them, so "127.1", "2130706433",
"0x7f000001" and "0177.0.0.1" are all 127.0.0.1 here too.

DNS REBINDING. A name may resolve to a harmless address when checked and to
127.0.0.1 a moment later when connected. So request() checks the addresses
ONCE and then connects to the address it checked (the Host header and TLS
name stay the URL's). It never follows a redirect: a 3xx is a failure, so a
public URL cannot bounce the request back onto the box.

THIS FILE EXISTS TWICE, on purpose: here, and as systems/<slug>/app/netsafe.py
for the detection app, which imports nothing from core/. The two are kept
byte-identical (tests/smoke_netsafe.py compares them and runs both on the
same table of cases). Standard library only.
"""

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.parse

#: What the editor and the log say when an address is refused.
BLOCKED_MESSAGE = ("Webhook addresses on this device itself are not allowed. "
                   "Use an address of another computer.")

_BLOCKED_NAMES = ("localhost", "localhost.localdomain", "ip6-localhost",
                  "ip6-loopback", "metadata", "metadata.google.internal",
                  "metadata.azure.internal", "instance-data",
                  "instance-data.ec2.internal")
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "100.100.100.200/32",
    "224.0.0.0/4", "255.255.255.255/32",
    "::/128", "::1/128", "fe80::/10", "ff00::/8", "fd00:ec2::254/128"))

_OWN_TTL_S = 60.0
_own_cache = {"at": 0.0, "addrs": frozenset(), "name": ""}


class Blocked(ValueError):
    """An address this box will not send to. str() is a sentence for people."""


def _legacy_ipv4(host):
    """An IPv4 address written any way inet_aton accepts it (127.1,
    2130706433, 0x7f000001, 0177.0.0.1), or None when `host` is a name."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    nums = []
    for part in parts:
        if not part:
            return None
        try:
            if part[:2].lower() == "0x":
                nums.append(int(part[2:] or "0", 16))
            elif len(part) > 1 and part[0] == "0":
                nums.append(int(part, 8))
            elif part.isdigit():
                nums.append(int(part))
            else:
                return None
        except ValueError:
            return None
    limits = {1: [2 ** 32], 2: [256, 2 ** 24], 3: [256, 256, 2 ** 16],
              4: [256, 256, 256, 256]}[len(nums)]
    if any(n < 0 or n >= cap for n, cap in zip(nums, limits)):
        return None
    value = 0
    for n, cap in zip(nums, limits):
        value = value * cap + n
    return ipaddress.IPv4Address(value)


def parse_ip(host):
    """The address `host` spells, or None for a name. Brackets, an IPv6 zone
    id and IPv4-mapped IPv6 are handled; a mapped address is its IPv4."""
    text = str(host or "").strip().strip("[]")
    text = text.split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        ip = _legacy_ipv4(text.rstrip("."))
        if ip is None:
            return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def own_addresses():
    """This box's own addresses (every interface, and what its hostname
    resolves to), cached a minute. Best effort: what cannot be read is left
    out, and the named rules still hold."""
    now = time.monotonic()
    if now - _own_cache["at"] < _OWN_TTL_S and _own_cache["at"]:
        return _own_cache["addrs"]
    found = set()
    name = ""
    try:
        name = socket.gethostname()
        for info in socket.getaddrinfo(name, None):
            found.add(str(info[4][0]).split("%", 1)[0])
    except OSError:
        pass
    try:                                         # Linux: every interface's IPv4
        import fcntl                             # noqa: PLC0415
        import struct                            # noqa: PLC0415
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _idx, ifname in socket.if_nameindex():
                try:
                    raw = fcntl.ioctl(probe.fileno(), 0x8915,   # SIOCGIFADDR
                                      struct.pack("256s", ifname[:15].encode()))
                    found.add(socket.inet_ntoa(raw[20:24]))
                except OSError:
                    continue
        finally:
            probe.close()
    except (ImportError, OSError, AttributeError):
        pass
    try:                                         # Linux: every interface's IPv6
        with open("/proc/net/if_inet6", encoding="ascii") as handle:
            for line in handle:
                hexa = line.split()[0]
                found.add(str(ipaddress.IPv6Address(int(hexa, 16))))
    except (OSError, ValueError, IndexError):
        pass
    addrs = frozenset(str(parse_ip(a) or a) for a in found)
    _own_cache.update(at=now, addrs=addrs, name=name)
    return addrs


def _hostname():
    if not _own_cache["name"]:
        try:
            _own_cache["name"] = socket.gethostname()
        except OSError:
            _own_cache["name"] = ""
    return _own_cache["name"]


def ip_blocked(ip, own=None):
    """Is this address one a user URL may not reach?"""
    ip = parse_ip(ip) if not isinstance(ip, (ipaddress.IPv4Address,
                                             ipaddress.IPv6Address)) else ip
    if ip is None:
        return True
    if any(ip in net for net in _BLOCKED_NETS if net.version == ip.version):
        return True
    if ip.is_loopback or ip.is_unspecified or ip.is_link_local or ip.is_multicast:
        return True
    mine = own_addresses() if own is None else own
    return str(ip) in {str(parse_ip(a) or a) for a in mine}


def name_blocked(host, hostname=None):
    """Is this NAME one of this box's own (localhost, its hostname) or a
    cloud metadata name? Judged before any lookup."""
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return True
    if name in _BLOCKED_NAMES or name.endswith(".localhost"):
        return True
    box = str(_hostname() if hostname is None else hostname or "").strip().lower()
    if box and name in (box, f"{box}.local", f"{box}.localdomain",
                        f"{box}.lan"):
        return True
    return False


def _resolve(host, port):
    out = []
    for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        addr = str(info[4][0]).split("%", 1)[0]
        if addr not in out:
            out.append(addr)
    return out


def target(url):
    """(scheme, host, port, path) of an http(s) URL, or Blocked."""
    try:
        parts = urllib.parse.urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError as exc:
        raise Blocked("That is not a valid web address.") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Blocked("The address must start with http:// or https://")
    host = parts.hostname.split("%", 1)[0].rstrip(".")
    port = port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return parts.scheme, host, port, path


def check(url, resolve=None, own=None, hostname=None, strict=True):
    """The addresses `url` may be sent to, every one of them allowed, or
    Blocked. `strict` False (a save, where DNS may be down) lets a name that
    does not resolve through with [] -- the send checks again."""
    _scheme, host, port, _path = target(url)
    if name_blocked(host, hostname):
        raise Blocked(BLOCKED_MESSAGE)
    literal = parse_ip(host)
    if literal is not None:
        addrs = [str(literal)]
    else:
        try:
            addrs = [str(parse_ip(a) or a) for a in (resolve or _resolve)(host, port)]
        except (OSError, UnicodeError) as exc:
            if strict:
                raise Blocked(f"The address {host} could not be found.") from exc
            return []
        if not addrs:
            if strict:
                raise Blocked(f"The address {host} could not be found.")
            return []
    if any(ip_blocked(a, own) for a in addrs):
        raise Blocked(BLOCKED_MESSAGE)
    return addrs


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to an address already checked, with the URL's own name for SNI
    and certificate checks -- so no second lookup can change where it goes."""

    def __init__(self, ip, host, port, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, ip, host, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


def request(url, method="GET", data=None, headers=None, timeout=5.0,
            resolve=None, own=None, max_bytes=1 << 20):
    """Send one request to a user-given URL: (status, body bytes).

    Checked by check() first; connected to the address that was checked;
    redirects are NOT followed (a 3xx raises); a 4xx/5xx raises, as
    urllib's urlopen does, so callers record a failure either way."""
    scheme, host, port, path = target(url)
    addrs = check(url, resolve=resolve, own=own)
    if scheme == "https":
        conn = _PinnedHTTPS(addrs[0], host, port, timeout, ssl.create_default_context())
    else:
        conn = _PinnedHTTP(addrs[0], host, port, timeout)
    try:
        conn.request(method, path, body=data, headers=dict(headers or {}))
        resp = conn.getresponse()
        body = resp.read(max_bytes)
        status = resp.status
    finally:
        conn.close()
    if 300 <= status < 400:
        raise Blocked(f"HTTP {status}: the address answered with a redirect, "
                      "which is not followed")
    if status >= 400:
        raise OSError(f"HTTP Error {status}")
    return status, body


# ---------------------------------------------------------------------------
# The two loopback addresses the app may fetch (Pipi N-3)
# ---------------------------------------------------------------------------
# A compiled Email / Telegram output carries the address of this box's own
# master route that renders its CSV files (csv_url) or names its Telegram
# recipients (to_url). Checked against "any loopback URL", a raw saved or
# imported output could point it at another local service instead -- the
# MediaMTX path list carries camera passwords. So the address must be
# EXACTLY one of these routes, on the master's own port, with only its own
# query keys.

#: path -> the query keys it may carry (the first is required).
MASTER_ROUTES = {"/api/workflow/query-csv": ("out", "n"),
                 "/api/telegram/recipients/resolve": ("out",)}
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                      "0123456789_-")


def master_url_problem(url, path, port=None):
    """"" when `url` is this master's own `path` route, else why not.

    http only; host exactly 127.0.0.1 or localhost; an explicit port, equal
    to `port` when given; no user, fragment or encoded characters; the path
    exactly `path` (one of MASTER_ROUTES); only that route's query keys,
    each once, each an id of letters, digits, _ and -."""
    text = str(url or "")
    try:
        parts = urllib.parse.urlsplit(text)
        given = parts.port
    except ValueError:
        return "not a valid address"
    if parts.scheme != "http":
        return "must be http"
    if "@" in parts.netloc or parts.username is not None:
        return "may not carry a user name"
    if parts.hostname not in ("127.0.0.1", "localhost"):
        return "must be this box's own master"
    if given is None or parts.netloc not in (f"127.0.0.1:{given}", f"localhost:{given}"):
        return "must name the master's port"
    if port is not None and int(given) != int(port):
        return "must be this box's own master port"
    if parts.fragment or "#" in text or "%" in text:
        return "may not carry encoded characters or a fragment"
    allowed = MASTER_ROUTES.get(path)
    if allowed is None or parts.path != path:
        return "must be the master's own route"
    try:
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True,
                                       strict_parsing=True)
    except ValueError:
        return "has a malformed query"
    keys = [k for k, _v in pairs]
    if len(keys) != len(set(keys)) or any(k not in allowed for k in keys) \
            or allowed[0] not in keys:
        return "has query keys the route does not take"
    if any(not v or len(v) > 40 or set(v) - _ID_CHARS for _k, v in pairs):
        return "has a query value that is not an id"
    return ""
