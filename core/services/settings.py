# -*- coding: utf-8 -*-
"""
settings.py -- Device discovery and pairing for the Settings page.

Discovery is an active sweep of the local /24s on the agent port: every agent
answers /api/agent/info unauthenticated (contract-v1.md sec 2), which is enough
to list candidates and let the operator pick. mDNS (discovery_mdns) only adds
addresses to probe, for link-local boxes; a device on
another subnet is added by URL instead.

Pairing follows docs/setup.md sec 5 exactly, just executed by the master
instead of an operator with curl: generate the token here, POST it to the
agent, persist {host_serial: token} -- the device never gives the token back.

UI-managed entries are written to slaves.local.toml only (config.save_local).
The committed slaves.toml stays human-edited; an entry that lives there cannot
be removed from the UI, and the error says so.

No framework imports here (docs/conventions.md sec 2).
"""

import concurrent.futures
import ipaddress
import json
import pathlib
import secrets
import shutil
import socket
import time
import urllib.error
import urllib.request

from core import config

AGENT_PORT = 5101
PORT_TIMEOUT_S = 0.4
INFO_TIMEOUT_S = 2.0
PAIR_TIMEOUT_S = 5.0


class SettingsError(Exception):
    """Operator-facing failure. `status` maps to the HTTP response."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _request(url, payload=None, timeout=INFO_TIMEOUT_S):
    """GET (or POST json) url. Returns (status, parsed_or_None). Never raises."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


# -- discovery ---------------------------------------------------------------

def _local_networks():
    """The /24s this host sits on. The UDP connect never sends a packet; it
    only asks the kernel which interface would route there."""
    addresses = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            addresses.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    networks = set()
    for addr in addresses:
        try:
            ip = ipaddress.IPv4Address(addr)
        except ValueError:
            continue
        if not ip.is_loopback and not ip.is_link_local:
            networks.add(ipaddress.IPv4Network(f"{addr}/24", strict=False))
    return networks


def _port_open(ip, port):
    try:
        with socket.create_connection((str(ip), port), timeout=PORT_TIMEOUT_S):
            return True
    except OSError:
        return False


def _describe(url, system_slug, configured):
    status, info = _request(f"{url}/api/agent/info", timeout=INFO_TIMEOUT_S)
    if status != 200 or not isinstance(info, dict) or not info.get("host_serial"):
        return None
    host_serial = str(info["host_serial"])
    return {
        "host_serial": host_serial,
        "url": url,
        "display_name": info.get("display_name", ""),
        "system_slug": info.get("system_slug", ""),
        "slug_match": info.get("system_slug") == system_slug,
        "paired": bool(info.get("paired")),
        "app_running": bool(info.get("app_running")),
        "already_added": host_serial in configured,
    }


def scan(system_slug, configured_serials, port=AGENT_PORT):
    """Sweep the local /24s for agents. Returns device dicts, matches first.

    Every host in the subnet gets a TCP probe (sub-second timeout, wide pool),
    then only the ones with the port open pay the HTTP round trip.
    """
    hosts = [ip for net in _local_networks() for ip in net.hosts()]
    # Link-local boxes (one cable, no DHCP) are in no /24: add what mDNS and
    # the neighbour table suggest. Still probed and slug-checked below.
    from core.services import discovery, discovery_mdns     # noqa: PLC0415
    swept = {str(ip) for ip in hosts}
    hosts += [ip for ip in discovery_mdns.candidate_hosts(sorted(discovery._local_ips()))
              if ip not in swept]
    if not hosts:
        return []
    configured = set(configured_serials)

    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        open_flags = pool.map(lambda ip: _port_open(ip, port), hosts)
        candidates = [ip for ip, is_open in zip(hosts, open_flags) if is_open]
        devices = pool.map(
            lambda ip: _describe(f"http://{ip}:{port}", system_slug, configured),
            candidates,
        )

    # A multi-homed host answers once per interface; one card per device.
    found = {}
    for d in devices:
        if d and d["host_serial"] not in found:
            found[d["host_serial"]] = d
    # This master's own system only. Another system's device on the same LAN
    # is that master's business -- listing it here invites pairing a device
    # into the wrong dashboard.
    out = [d for d in found.values() if d["slug_match"]]
    out.sort(key=lambda d: (d["already_added"], d["host_serial"]))
    return out


# -- pairing -----------------------------------------------------------------

def pair(url, label, system_slug, master_serial, username=None, password=None):
    """Pair the agent at `url` and return the slaves.toml-shaped entry.

    Two protocols (contract v1.1):
      v2 -- operator supplied the DEVICE's admin credentials: send them, the
            device validates and mints the token itself, returning it. The
            credentials are used for this one request and never persisted.
      v1 -- no credentials: generate the token here and send it (the agent
            enforces its unlock window; 409 when closed).
    """
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise SettingsError("device URL must start with http:// or https://")

    status, info = _request(f"{url}/api/agent/info")
    if status != 200 or not isinstance(info, dict) or not info.get("host_serial"):
        raise SettingsError(f"no agent answering at {url}", status=502)
    if info.get("system_slug") != system_slug:
        raise SettingsError(
            f"device runs {info.get('system_slug') or 'an unknown system'!s}, "
            f"this master is {system_slug} - pair it to its own system's master"
        )

    if username or password:
        payload = {"master_serial": master_serial,
                   "username": username or "", "password": password or ""}
    else:
        payload = {"master_serial": master_serial,
                   "token": secrets.token_urlsafe(32)}

    status, reply = _request(f"{url}/api/agent/pair", payload=payload,
                             timeout=PAIR_TIMEOUT_S)
    if status == 401:
        raise SettingsError("device rejected the credentials", status=401)
    if status == 429:
        raise SettingsError(
            "device locked pairing after repeated failures - wait 15 minutes",
            status=429,
        )
    if status == 409:
        raise SettingsError(
            "device is already paired - disconnect its current master first "
            "(device dashboard or unlock window, docs/setup.md sec 5)",
            status=409,
        )
    if status != 200 or not isinstance(reply, dict) or not reply.get("ok"):
        detail = (reply or {}).get("error") if isinstance(reply, dict) else None
        raise SettingsError(detail or f"pair request failed (status {status})",
                            status=502)

    # v2 devices return the token they minted; v1 used the one we sent.
    token = reply.get("token") or payload.get("token")
    if not token:
        raise SettingsError("device answered ok without a token (agent bug?)",
                            status=502)

    host_serial = str(reply.get("host_serial") or info["host_serial"])
    from core.services import slaves as slaves_service       # noqa: PLC0415
    if not slaves_service.valid_serial(host_serial):
        # The device chooses its own name; "__local__" (or anything "__") is
        # this master's, and a device using it would pass its events off as
        # the local camera's (Pipi N-2).
        raise SettingsError(
            "this device reports a name that cannot be used "
            f"({host_serial[:40]!r}): a device name is letters, digits, '.', "
            "'_' and '-', up to 64 characters, and may not start with '__'",
            status=400)
    return {
        "host_serial": host_serial,
        "label": (label or info.get("display_name") or "").strip(),
        "url": url,
        "token": token,
        "system_slug": system_slug,
    }


def revoke_on_device(url, token):
    """Best-effort remote unpair (v1.1 agents). Returns True when the device
    confirmed; False when unreachable or a pre-v1.1 agent (404) -- the caller
    still forgets the device locally either way."""
    if not url or not token:
        return False
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/agent/unpair", data=b"",
        headers={"Authorization": f"Bearer {token}"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=PAIR_TIMEOUT_S) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


# -- persistence -------------------------------------------------------------

def save_slave(entry, config_dir=None):
    """Record a paired device in slaves.local.toml (never the committed file).
    The token is encrypted at rest (v2.0 #21, core/services/secrets.py)."""
    from core.services import secrets as secrets_service
    from core.services import slaves as slaves_service       # noqa: PLC0415
    if not slaves_service.valid_serial(entry.get("host_serial")):
        raise SettingsError("that device name cannot be used", status=400)
    local = config._read(config.root(config_dir) / "slaves.local.toml")
    local.setdefault("slaves", {})[entry["host_serial"]] = {
        "system_slug": entry["system_slug"],
        "label": entry["label"],
        "url": entry["url"],
        "token": secrets_service.encrypt(entry["token"], config_dir),
    }
    config.save_local("slaves", local, config_dir)


def remove_slave(host_serial, config_dir=None):
    """Forget a UI-added device. The agent stays paired on its side -- unpairing
    a deployed device requires physical access by design (docs/setup.md sec 5)."""
    root = config.root(config_dir)
    local = config._read(root / "slaves.local.toml")
    if host_serial in (local.get("slaves") or {}):
        del local["slaves"][host_serial]
        config.save_local("slaves", local, config_dir)
        return
    if host_serial in (config._read(root / "slaves.toml").get("slaves") or {}):
        raise SettingsError(
            f"{host_serial} is defined in slaves.toml - remove it there", status=409
        )
    raise SettingsError(f"no device {host_serial!r} configured", status=404)


# -- page data ---------------------------------------------------------------

def _human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def _human_uptime(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _storage_view(stats):
    """None unless the device reports both storage keys -- a bar drawn from a
    missing number reads as a full or empty disk, both alarms nobody should
    chase."""
    total = stats.get("storage_total_bytes")
    free = stats.get("storage_free_bytes")
    if not total or free is None:
        return None
    used_pct = round(100 * (1 - free / total))
    return {
        "free": _human_bytes(free),
        "total": _human_bytes(total),
        "used_pct": used_pct,
        "css": "crit" if used_pct >= 90 else "warn" if used_pct >= 75 else "",
    }


def build_devices(slave_configs, live_slaves):
    """Configured devices for the page: config first, live sync state merged in.

    Tokens never leave this function -- the template gets none, the API gets
    none (docs/conventions.md sec 7).
    """
    live = {s.get("host_serial"): s for s in live_slaves}
    out = []
    for cfg in slave_configs:
        state = live.get(cfg["host_serial"], {})
        stats = state.get("stats") or {}
        uptime = stats.get("uptime_s")
        out.append({
            "host_serial": cfg["host_serial"],
            "label": cfg.get("label") or state.get("label") or "",
            "url": cfg["url"],
            "online": bool(state.get("online")),
            "app_running": bool(state.get("app_running")),
            "synced": cfg["host_serial"] in live,
            "skew_flagged": bool(state.get("skew_flagged")),
            "uptime": _human_uptime(uptime) if uptime else "",
            "storage": _storage_view(stats),
            "capabilities": tuple(state.get("capabilities") or ()),
            # Stored under a reserved or odd name (Pipi N-2): not polled;
            # shown so an admin can remove it.
            **({"reserved": True,
                "problem": "This device's name is reserved or not allowed, so "
                           "it is not synced. Remove it and pair it again."}
               if cfg.get("reserved") else {}),
        })
    return out


#: Master process start, for the System panel's uptime.
_STARTED = time.time()


def build_master():
    """What the master can honestly say about itself: its own disk and how
    long this process has been up. No fake version pills, no reboot buttons --
    those need endpoints that do not exist yet."""
    try:
        usage = shutil.disk_usage(pathlib.Path(__file__).resolve().parents[2])
        storage = _storage_view({"storage_total_bytes": usage.total,
                                 "storage_free_bytes": usage.free})
    except OSError:
        storage = None
    return {
        "uptime": _human_uptime(time.time() - _STARTED),
        "hostname": socket.gethostname(),
        "storage": storage,
    }
