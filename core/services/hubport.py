# -*- coding: utf-8 -*-
"""
hubport.py -- the port the software answers on, as a SETTING.

5100 used to be spelled in nine places. Tok Aba (2026-10-04): master and a
developer may change it from a Network setting, reaching every system.

ONE FACT, ONE PLACE: config/systems.local.toml [hub].port, with [hub].url
(http://127.0.0.1:<port>) kept in step for every existing reader (the
masters' back link, provision._ports_in_use, the OTA job); the hub reads it
at start (resolve_cli). A file the service account owns: no root path.

SAVED IS NOT APPLIED (Tok Aba, 2026-10-05): Save writes [hub].pending_port
and its boot; the hub's first start in a LATER boot validates it again and
applies it (apply_pending via boxpending), or keeps the old port and says
why. A hub crash or OTA restart in the same boot moves nothing; the pending
port is reserved meanwhile (claimed_ports).

IN irivai-hub.service THE SETTING WINS over a `--port` baked into a
pre-October unit (adopted only while the file has no port): no installed
box needs a one-time step. Anywhere else --port wins: a second hub with its
own unit on a dev box keeps ITS port even when it shares the config dir.
"""

import os
import socket
import urllib.parse

from core import config, net

DEFAULT_PORT = 5100
#: Where the running process records the port it bound, for anything that
#: must reach this hub without being it (the OTA health gate, this page).
ENV_PORT = "CYTRON_HUB_PORT"
#: ...and where that port came from: "argv" (explicit --port) or "config".
ENV_SOURCE = "CYTRON_HUB_PORT_SOURCE"

PORT_MIN, PORT_MAX = 1024, 65535     # below 1024 needs root; the account is not

#: Said by the Software port and Hostname cards (and their refused saves)
#: when this hub is not irivai-hub.service: only that unit applies a saved
#: port or name (owns_setting), so a value staged from any other hub (a
#: second unit, a dev box's setsid hub) would wait for ever and the card
#: would keep promising it (Yaya's QA part 1, bug 6).
NOT_OWN_SERVICE = ("this copy of the software is not the box's own service "
                   "(irivai-hub.service), the only one that applies a saved "
                   "port or hostname at the restart; change it where this copy "
                   "was started")

#: What else the box runs, by port, with the name the refusal uses: the
#: device agent (5101), MediaMTX, the MQTT broker, PostgreSQL, the services
#: every box has. scripts/mqtt_apply.py's list minus the 51xx block itself
#: -- the hub may move within it as long as no master is there.
RESERVED = {
    5101: "the device agent", 8554: "the video gateway (RTSP)",
    8889: "the video gateway (WebRTC)", 8189: "the video gateway (media)",
    9997: "the video gateway (API)", 8888: "the video gateway (HLS)",
    8000: "the video gateway", 8001: "the video gateway",
    1935: "the video gateway (RTMP)", 1883: "the MQTT broker",
    5432: "PostgreSQL", 22: "SSH", 53: "DNS", 67: "DHCP", 68: "DHCP",
    80: "HTTP", 443: "HTTPS", 5353: "mDNS", 1880: "Node-RED",
    8443: "the dashboard's HTTPS (Settings -> Network -> HTTPS)",
    # The box redirects every PLC's 502 here (scripts/nftables/modbus-
    # redirect.nft), unconditionally: a hub or master moved onto 5020 would
    # answer a PLC with HTTP.
    5020: "the Modbus server (PLCs on 502 are redirected to it)",
}


class PortError(Exception):
    """A port this module will not take. The text is shown as it is."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def port(config_dir=None):
    """The port the hub is SET to answer on: [hub].port, else the port in
    [hub].url, else 5100. Never raises on a bad value -- a typo in the file
    must not take the box off the network."""
    hub = dict(config.systems(config_dir).get("hub") or {})
    try:
        if hub.get("port") not in (None, ""):
            value = int(hub["port"])
            if PORT_MIN <= value <= PORT_MAX:
                return value
    except (TypeError, ValueError):
        pass
    try:
        from_url = urllib.parse.urlsplit(str(hub.get("url") or "")).port
    except ValueError:
        from_url = None
    # Range-checked like [hub].port above: a hand-edited url = ...:80 used
    # to reach uvicorn as-is, fail with EACCES, and crash-loop the hub every
    # five seconds (Pipi, 2026-10-05).
    if from_url and PORT_MIN <= from_url <= PORT_MAX:
        return from_url
    return DEFAULT_PORT


def running_port():
    """The port THIS process bound, or None outside a hub process."""
    try:
        return int(os.environ.get(ENV_PORT) or 0) or None
    except ValueError:
        return None


def source():
    return os.environ.get(ENV_SOURCE) or ""


def under_systemd():
    return bool(os.environ.get("INVOCATION_ID"))   # systemd sets it per service


def owns_setting():
    """Is this THE box's hub, the unit the saved port (and the restart's
    pending changes) belong to? Asked of this process's cgroup."""
    try:
        with open("/proc/self/cgroup", encoding="ascii") as fh:
            return any(ln.rstrip().endswith("/irivai-hub.service") for ln in fh)
    except OSError:
        return False


def browser_url(request, config_dir=None):
    """The hub as the BROWSER should reach it: the configured port on the
    host the request came in by (core/net.py). The one place the masters'
    login bounce and "< Systems" link get this from."""
    hub = dict(config.systems(config_dir).get("hub") or {})
    return net.rewrite(str(hub.get("url") or ""), request,
                       default_port=port(config_dir))


def master_ports(config_dir=None):
    # {port: slug} for every system's master, from config.
    out = {}
    for slug, entry in (config.systems(config_dir).get("masters") or {}).items():
        try:
            p = urllib.parse.urlsplit(str((entry or {}).get("url") or "")).port
        except ValueError:
            p = None
        if p:
            out[p] = slug
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def parse(value):
    """A whole number in range, or PortError."""
    if isinstance(value, bool):
        raise PortError("the port must be a whole number")
    try:
        p = int(str(value).strip())
    except (TypeError, ValueError):
        raise PortError("the port must be a whole number") from None
    if not PORT_MIN <= p <= PORT_MAX:
        raise PortError(f"the port must be between {PORT_MIN} and {PORT_MAX} "
                        f"(below {PORT_MIN} needs root, which the software lacks)")
    return p


def reserved_by(p, config_dir=None):
    """Who the box itself has this port spoken for, or "" -- config facts
    only, nothing on the network is asked."""
    if p in RESERVED:
        return RESERVED[p]
    slug = master_ports(config_dir).get(p)
    if slug:
        return f"the system \"{slug}\" (its own port)"
    # The listeners a system owns whether or not it is running right now: its
    # video stream and its Modbus server. Neither shows up in a bind test
    # while its app is stopped, and a hub moved onto one won or lost the next
    # reboot's race -- losing it crash-looped the dashboard (Pipi, 2026-10-05).
    from core.services import provision                    # noqa: PLC0415
    try:
        if p in provision.stream_ports_in_use(config_dir=config_dir):
            return "a system's live video stream"
    except Exception:                                      # noqa: BLE001
        pass
    try:
        owner = provision.modbus_ports_in_use(config_dir=config_dir).get(p)
    except Exception:                                      # noqa: BLE001
        owner = None
    if owner:
        return f"the system \"{owner}\"'s Modbus server"
    return ""


def listening(p):
    """Something on this box already holds TCP `p`. Binding is the only
    honest test: a config can forget a port a zombie master still has."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", int(p)))
        return False
    except OSError:
        return True
    finally:
        s.close()


def check(value, config_dir=None):
    """The Test button. {ok, port, usable, current, detail} -- or PortError
    for a value that is not a port at all."""
    p = parse(value)
    current = running_port() or port(config_dir)
    if p == current:
        return {"ok": True, "port": p, "usable": True, "current": True,
                "detail": f"{p} is the port the software answers on now"}
    owner = reserved_by(p, config_dir)
    if owner:
        return {"ok": True, "port": p, "usable": False, "current": False,
                "detail": f"{p} is used by {owner} - choose another"}
    if listening(p):
        return {"ok": True, "port": p, "usable": False, "current": False,
                "detail": f"something on this box already listens on {p} "
                          "- choose another, or stop that program first"}
    return {"ok": True, "port": p, "usable": True, "current": False,
            "detail": f"{p} is free"}


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def save(value, config_dir=None, validate=True):
    """Write the port to systems.local.toml ([hub].port and [hub].url
    together). `validate=False` is for the installer, which writes the port
    it was asked for before anything listens."""
    p = parse(value)
    if validate:
        verdict = check(p, config_dir)
        if not verdict["usable"]:
            raise PortError(verdict["detail"])
    local = dict(config.load_local("systems", config_dir))
    hub = dict(local.get("hub") or {})
    hub["port"] = p
    hub["url"] = f"http://127.0.0.1:{p}"
    local["hub"] = hub
    config.save_local("systems", local, config_dir)
    return p


def resolve_cli(explicit, config_dir=None):
    """hub_server.main: the port to bind, and where it came from. In the
    box's own unit the setting wins; a baked --port seeds an empty file (a
    pre-October unit keeps its port). Anywhere else --port wins."""
    if explicit and owns_setting():
        if "port" not in (config.load_local("systems", config_dir).get("hub") or {}):
            try:
                save(int(explicit), config_dir, validate=False)
            except (PortError, OSError):
                pass
        explicit = None
    if explicit:
        p = int(explicit)
        os.environ[ENV_SOURCE] = "argv"
    else:
        p = port(config_dir)
        os.environ[ENV_SOURCE] = "config"
    os.environ[ENV_PORT] = str(p)
    return p


def fallback_if_taken(p, config_dir=None):
    """(port to bind, reason or ""). A CONFIGURED port already held would
    crash-loop the hub (Restart=always); once, at start, fall back to
    DEFAULT_PORT and tell the admins. An explicit --port fails loudly."""
    if source() != "config" or p == DEFAULT_PORT or not listening(p):
        return p, ""
    if listening(DEFAULT_PORT):
        return p, ""                     # nowhere better to go; fail as before
    os.environ[ENV_PORT] = str(DEFAULT_PORT)
    os.environ[ENV_SOURCE] = "fallback"
    return DEFAULT_PORT, (f"port {p} was already taken when the software "
                          f"started, so it answers on {DEFAULT_PORT} instead. "
                          "Choose a free port under Settings -> Network -> "
                          "Software port.")


# ---------------------------------------------------------------------------
# Pending: saved now, applied at the next restart of the box
# ---------------------------------------------------------------------------

def pending_port(config_dir=None):          # waiting for a restart, or None
    hub = config.load_local("systems", config_dir).get("hub") or {}
    try:
        return int(hub.get("pending_port") or 0) or None
    except (TypeError, ValueError):
        return None


def claimed_ports(config_dir=None):
    """The port the hub has AND the one it will have: neither may be handed
    to a system or the MQTT broker before the restart."""
    return {p for p in (port(config_dir), pending_port(config_dir)) if p}


def _write_pending(p, boot, config_dir=None):
    local = dict(config.load_local("systems", config_dir))
    hub = dict(local.get("hub") or {})
    hub.pop("pending_port", None)
    hub.pop("pending_boot", None)
    if p:
        hub["pending_port"], hub["pending_boot"] = int(p), str(boot or "")
    local["hub"] = hub
    config.save_local("systems", local, config_dir)


def stage(value, boot, config_dir=None):
    """Save the port for the next restart (the page's Apply). The port the
    hub answers on now cancels a pending change. Validated as check() does;
    the gate that binds it validates again at the restart. Refused flat
    from a hub that is not the box's own service: it would never apply."""
    p = parse(value)
    if not owns_setting():
        raise PortError(NOT_OWN_SERVICE)
    if p == (running_port() or port(config_dir)):
        _write_pending(None, "", config_dir)
        return None
    verdict = check(p, config_dir)
    if not verdict["usable"]:
        raise PortError(verdict["detail"])
    _write_pending(p, boot, config_dir)
    return p


def apply_pending(boot, config_dir=None):
    """At the hub's start: a port saved in an EARLIER boot becomes the port.
    None if nothing waits; else {ok, from, to, detail}. Refused -> the old
    port stays; the pending value goes either way (no retry every boot)."""
    hub = config.load_local("systems", config_dir).get("hub") or {}
    raw = hub.get("pending_port")
    if raw in (None, "") or str(hub.get("pending_boot") or "") == str(boot):
        return None
    old = port(config_dir)
    _write_pending(None, "", config_dir)
    try:
        p = parse(raw)
        owner = reserved_by(p, config_dir)
        if owner:
            raise PortError(f"{p} is used by {owner}")
        if p != old and listening(p):
            raise PortError(f"something on this box was already using {p}")
    except PortError as exc:
        return {"ok": False, "from": old, "to": raw, "detail": str(exc)}
    save(p, config_dir, validate=False)
    return {"ok": True, "from": old, "to": p, "detail": ""}


def status(config_dir=None):                 # the Software port card
    configured = port(config_dir)
    return {
        "port": configured, "running": running_port() or configured,
        "pending": pending_port(config_dir), "default": DEFAULT_PORT,
        "source": source(),
        "systems": [{"slug": slug, "port": p}
                    for p, slug in sorted(master_ports(config_dir).items())],
    }
