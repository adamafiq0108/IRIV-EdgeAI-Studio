# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
netconf.py -- the box's network, through NetworkManager.

An appliance is commissioned by somebody standing next to it with a laptop,
and the first thing they need is a way onto it: join the site's Wi-Fi, or
make the box broadcast its own access point and join that. Until now the only
answer was ssh and `nmcli`, which is not an answer for the person who bought
the product.

EVERY WRITE HERE CAN CUT THE CABLE UNDER THE CALLER. Changing wlan0 while the
request arrived over wlan0 is a supported thing to want and an unrecoverable
thing to do by accident, so:

  * `status()` marks the interface the request came in on, and the routes
    pass that down so the UI can warn on the one that matters;
  * nothing is "applied" implicitly -- an interface changes only when its own
    Apply is pressed;
  * `reset()` exists precisely because the recoverable path has to be one
    button on the OLED's terms: forget every saved network, come back as the
    access point printed on the panel.

nmcli, not /etc/network or wpa_supplicant: NetworkManager owns the interfaces
on this image, and a second writer would fight it.
"""

import ipaddress
import json
import os
import re
import logging
import subprocess
import uuid

# The address rules live in netaddr.py (pure functions, no nmcli); the names
# stay reachable here because the routes and the tests know them as ours.
from core.services import netaddr, routeheal
from core.services.netaddr import (NetError, mask_to_prefix, prefix_to_mask,  # noqa: F401
                                   valid_ip as _valid_ip,
                                   refuse_bad_host as _refuse_bad_host)
# Likewise the uplink's route metric and the port snapshot uplinkguard
# reverts to (netuplink.py).
from core.services.netuplink import (UPLINK_METRIC, UPLINK_METRIC_FLOOR,  # noqa: F401
                                     STANDBY_METRIC, SNAPSHOT_KEYS as _SNAPSHOT_KEYS,
                                     auto_role as _auto_role, route_pairs as _route_pairs,
                                     uplink_metric as _uplink_metric, ipv4_snapshot)
# The Test button's "is another device on this address?" (ipconflict.py).
from core.services.ipconflict import address_in_use  # noqa: F401,E402

#: Wi-Fi with no password is a decision, not a typo -- but eight characters is
#: WPA2's own floor and a shorter one is silently rejected by the supplicant.
MIN_AP_PASSWORD = 8

#: The interface an access point is raised on.
AP_DEVICE = "wlan0"

#: The access point password a new box broadcasts, and the one Reset Wi-Fi
#: brings back. The same on every unit and printed in the manual -- Tok Aba's
#: decision (2026-10-07), made knowing that anyone in Wi-Fi range can join and
#: reach the sign-in page: the dashboard password still guards the box, and the
#: AP is isolated from the wired LAN (install.sh, scripts/nftables/ap-isolate.nft). From
#: 2026-10-05 to 10-07 every box made its own random key instead; the hub's
#: start puts such a key back to this once (apdefault.py). Changeable on Settings ->
#: Network like any other key, and allowed to be typed back.
logger = logging.getLogger(__name__)

DEFAULT_AP_PASSWORD = "iriv1234"
# .env IRIV_AP_PASSWORD replaces it when it is a valid WPA2 key (8-63
# printable characters); anything else keeps the built-in one.
try:
    from core import envfile as _envfile
    _env_key = _envfile.get("IRIV_AP_PASSWORD")
    if 8 <= len(_env_key) <= 63 and all(32 <= ord(c) <= 126 for c in _env_key):
        DEFAULT_AP_PASSWORD = _env_key
except Exception:                                             # noqa: BLE001
    pass

#: Autoconnect priority of a wired profile written AHEAD of its cable.
#:
#: An unplugged jack can have more than one profile that would take it: an
#: old one bound to it from another site, or an unbound "Wired connection 1"
#: that NetworkManager lets autoconnect on any ethernet port. NetworkManager
#: picks among them by this number first and by last use second, and a
#: profile that has never been used loses the second test to all of them.
#: Default is 0; anything above it makes the address the operator just typed
#: the one the jack comes up with when the cable goes in.
PRESET_PRIORITY = 10


#: The root helper that writes a Wi-Fi profile with its key on STDIN
#: (scripts/wifi_profile.py, installed by scripts/install.sh). The hotspot key
#: and the site's Wi-Fi passphrase used to ride on nmcli's argv through sudo,
#: which journals every argv it authorises (Yaya M-5, Pipi 2b). Exactly the
#: command the sudoers entry names; -I: no PYTHON* environment, no user site.
WIFI_HELPER = "/usr/local/lib/irivai/wifi-profile.py"
WIFI_HELPER_ARGV = ("sudo", "-n", "/usr/bin/python3", "-I", WIFI_HELPER)


def wifi_helper_installed():
    """Whether this box has the helper. A box updated over the air before
    its one-time install step has not, and keeps the old nmcli path (and the
    old `nmcli *` sudoers grant it needs) until it does."""
    return os.path.isfile(WIFI_HELPER)


def _wifi_profile(request):
    """Hand one request to the helper on stdin. Returns its answer; raises
    NetError with its sentence (never the request: it holds the key)."""
    try:
        proc = subprocess.run(list(WIFI_HELPER_ARGV), input=json.dumps(request),
                              capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise NetError("sudo is not available on this box")
    except subprocess.SubprocessError as exc:
        raise NetError(str(exc))
    answer = {}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        try:
            answer = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(answer, dict) or not answer:
        refused = proc.stderr or ""
        if "password is required" in refused or "not allowed" in refused:
            raise NetError("this box may not run its Wi-Fi helper yet - re-run "
                           "the installer (sudo ./scripts/install.sh) once")
        raise NetError("the Wi-Fi helper gave no answer")
    if not answer.get("ok"):
        raise NetError(str(answer.get("message") or "the Wi-Fi change failed"))
    return answer


def _nmcli(args, sudo=False, timeout=45):
    argv = (["sudo", "-n"] if sudo else []) + ["nmcli"] + list(args)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        raise NetError("NetworkManager (nmcli) is not installed on this box")
    except subprocess.TimeoutExpired:
        # Not str(exc): TimeoutExpired quotes the whole argv, and on a box
        # without the Wi-Fi helper that argv holds the hotspot key (the
        # legacy `device wifi hotspot ... password <key>`). The message goes
        # to the page and to the log.
        raise NetError("NetworkManager did not answer in time (nmcli %s)"
                       % (args[0] if args else ""))
    except subprocess.SubprocessError as exc:
        raise NetError(exc.__class__.__name__)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        # nmcli prefixes almost everything with "Error: ", which reads as
        # stuttering once the UI has already said "Could not join".
        message = re.sub(r"^Error:\s*", "", message.splitlines()[0]
                         if message else "the command failed")
        raise NetError(message)
    return proc.stdout or ""


def _split(line):
    """Split one -t line on its unescaped colons.

    nmcli escapes a colon inside a value as `\\:` -- an SSID may legitimately
    contain one, and a naive split renames the network.
    """
    out, buf, escaped = [], "", False
    for char in line:
        if escaped:
            buf += char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            out.append(buf)
            buf = ""
        else:
            buf += char
    out.append(buf)
    return out


def available():
    try:
        _nmcli(["--version"], timeout=10)
        return True
    except NetError:
        return False


# -- reading ------------------------------------------------------------------

def radio_on():
    try:
        return _nmcli(["radio", "wifi"], timeout=10).strip() == "enabled"
    except NetError:
        return False


def _connection_ipv4(name, uuid_=""):
    """The PROFILE's intent: auto or a static address somebody typed.

    By uuid when there is one: a saved profile is looked up off the profile
    list, where two can share a name and only the uuid says which.
    """
    if not name and not uuid_:
        return {}
    which = ["uuid", uuid_] if uuid_ else [name]
    try:
        out = _nmcli(["-t", "-f", "ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns,"
                      "ipv4.never-default,ipv4.ignore-auto-dns", "connection", "show"] + which,
                     timeout=15)
    except NetError:
        return {}
    values = {}
    for line in out.splitlines():
        parts = _split(line)
        if len(parts) >= 2:
            values[parts[0]] = ":".join(parts[1:])
    address = (values.get("ipv4.addresses") or "").split(",")[0].strip()
    ip, _, prefix = address.partition("/")
    return {
        "method": values.get("ipv4.method") or "",
        "static_ip": ip,
        "static_prefix": prefix or "24",
        "static_gateway": (values.get("ipv4.gateway") or "").strip(),
        "static_dns": (values.get("ipv4.dns") or "").strip().replace(",", ", "),
        "never_default": (values.get("ipv4.never-default") or "").strip() == "yes",
        # DHCP with DNS servers of its own: the lease's are ignored.
        "dns_override": (values.get("ipv4.ignore-auto-dns") or "").strip() == "yes",
    }


def _device_ipv4(device):
    """What the interface actually HAS right now, lease included."""
    try:
        out = _nmcli(["-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS",
                      "device", "show", device], timeout=15)
    except NetError:
        return {}
    address, gateway, dns = "", "", []
    for line in out.splitlines():
        parts = _split(line)
        if len(parts) < 2:
            continue
        key, value = parts[0], ":".join(parts[1:]).strip()
        if key.startswith("IP4.ADDRESS") and value and not address:
            address = value
        elif key.startswith("IP4.GATEWAY") and value:
            gateway = value
        elif key.startswith("IP4.DNS") and value:
            dns.append(value)
    ip, _, prefix = address.partition("/")
    return {"ip": ip, "prefix": prefix, "gateway": gateway, "dns": dns}


def _ethernet_profiles():
    """Every saved wired profile: [{name, uuid, ifname}].

    `ifname` is connection.interface-name, the interface the profile is BOUND
    to; "" is a profile that may come up on any jack. Read off the saved
    list, not `device status`: what matters for an unplugged jack is the
    profile that WILL take it, and nothing is active on it to ask.
    """
    try:
        out = _nmcli(["-t", "-f", "NAME,UUID,TYPE", "connection", "show"],
                     timeout=15)
    except NetError:
        return []
    result = []
    for line in out.splitlines():
        parts = _split(line)
        if len(parts) < 3 or parts[2] not in ("802-3-ethernet", "ethernet"):
            continue
        try:
            bound = _nmcli(["-t", "-f", "connection.interface-name",
                            "connection", "show", "uuid", parts[1]], timeout=15)
        except NetError:
            continue
        ifname = ":".join(_split(bound.strip())[1:]).strip()
        result.append({"name": parts[0], "uuid": parts[1], "ifname": ifname})
    return result


def _preset_profile(device, profiles=None):
    """The saved wired profile bound to `device`, or None.

    Ours ("Wired eth1") first if there is one, else the first bound one:
    changing an existing profile beats adding a second that then has to win
    an autoconnect contest against it.
    """
    bound = [p for p in (profiles if profiles is not None
                         else _ethernet_profiles()) if p["ifname"] == device]
    ours = [p for p in bound if p["name"] == preset_name(device)]
    return (ours or bound or [None])[0]


def preset_name(device):
    """What a profile written here for an unplugged jack is called."""
    return f"Wired {device}"


def devices():
    """Every real interface with its profile, state and addresses.

    Loopback and the p2p pseudo-device are dropped: neither is something a
    person configures, and both only make the page longer.

    A wired interface with nothing active on it (no cable, usually) still
    reports the saved profile bound to it as `profile`, with that profile's
    address: the page has to show what the jack WILL do when a cable goes
    in, which is the whole point of setting it beforehand.
    """
    try:
        out = _nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION",
                      "device", "status"])
    except NetError:
        return []
    result = []
    profiles = None                 # read once, and only if a jack needs it
    for line in out.splitlines():
        parts = _split(line)
        if len(parts) < 4:
            continue
        name, kind, state, connection = parts[0], parts[1], parts[2], parts[3]
        if kind in ("loopback", "wifi-p2p") or name == "lo":
            continue
        entry = {"device": name, "type": kind, "state": state,
                 "connection": connection}
        entry.update(_device_ipv4(name))
        if connection and connection != "--":
            entry.update(_connection_ipv4(connection))
        elif kind == "ethernet":
            if profiles is None:
                profiles = _ethernet_profiles()
            saved = _preset_profile(name, profiles)
            if saved:
                entry["profile"] = saved["name"]
                entry.update(_connection_ipv4(saved["name"], saved["uuid"]))
        entry["dhcp"] = (entry.get("method") or "auto") != "manual"
        result.append(entry)
    # eth0 first, then the rest by name: the jack labelled 0 on the case is
    # the one an installer plugs into first.
    result.sort(key=lambda d: (d["type"] != "ethernet", d["device"]))
    # Two jacks live on one network is a fault the page should show beside
    # them (netaddr: why it is a fault); a write never gets there, a pair of
    # DHCP leases does.
    shared = netaddr.shared_subnets(result)
    for entry in result:
        entry["shares_subnet_with"] = shared.get(entry["device"], [])
    return routeheal.annotate(result)      # which port holds the default route


def wifi_list(rescan=False):
    """Nearby networks, strongest first, one entry per SSID.

    An access point with three radios shows three times in nmcli's output and
    that is noise: what a person picks is a NAME.
    """
    args = ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    args += ["--rescan", "yes" if rescan else "no"]
    try:
        out = _nmcli(args, timeout=60)
    except NetError:
        return []
    best = {}
    for line in out.splitlines():
        parts = _split(line)
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue                       # hidden network: nothing to click
        try:
            strength = int(signal or 0)
        except ValueError:
            strength = 0
        entry = {"ssid": ssid, "signal": strength,
                 "secure": security not in ("", "--"),
                 "security": security if security != "--" else "",
                 "active": in_use.strip() == "*"}
        if ssid not in best or strength > best[ssid]["signal"]:
            best[ssid] = entry
    return sorted(best.values(), key=lambda n: -n["signal"])


def saved_wifi():
    """The Wi-Fi profiles this box remembers, active one first.

    What "forget this network" acts on. Read off the profile list, not the
    scan: a network the box joined at another site is not in range here, and
    that is exactly the one an operator wants to forget -- NetworkManager
    keeps trying it, and a saved wrong password keeps failing against it.
    The access-point profile is left out: it is the setup hotspot, and the
    AP button is how that one comes and goes.
    """
    try:
        out = _nmcli(["-t", "-f", "NAME,TYPE,ACTIVE,DEVICE", "connection", "show"],
                     timeout=15)
    except NetError:
        return []
    ap = ap_active()
    result = []
    for line in out.splitlines():
        parts = _split(line)
        if len(parts) < 3 or parts[1] != "802-11-wireless":
            continue
        name = parts[0]
        if not name or name == ap:
            continue
        result.append({"name": name, "active": parts[2] == "yes"})
    return sorted(result, key=lambda c: (not c["active"], c["name"].lower()))


def _wlan_connection():
    """The profile wlan0 is on right now, joined or broadcasting; "" if none."""
    try:
        out = _nmcli(["-t", "-f", "GENERAL.CONNECTION", "device", "show",
                      AP_DEVICE], timeout=15)
    except NetError:
        return ""
    return ":".join(_split(out.strip())[1:]).strip()


def ap_active():
    """Whether wlan0 is currently BROADCASTING rather than joined.

    Read off the profile's mode, not its name: "Hotspot" is only what nmcli
    happens to call the one it generates, and a renamed profile still puts
    the radio in AP mode. "" also when NetworkManager cannot be read; a
    caller that must tell the two apart uses _ap_probe().
    """
    try:
        return _ap_probe()
    except NetError:
        return ""


def _ap_probe():
    """ap_active(), except that a NetworkManager that cannot be read RAISES
    NetError instead of reading as "no hotspot" (apply_wifi_mode's
    fail-safe: an unknown radio is left alone)."""
    out = _nmcli(["-t", "-f", "GENERAL.CONNECTION", "device", "show",
                  AP_DEVICE], timeout=15)
    name = ":".join(_split(out.strip())[1:]).strip()
    if not name:
        return ""
    mode = _nmcli(["-t", "-f", "802-11-wireless.mode", "connection",
                   "show", name], timeout=15)
    return name if "ap" in mode.split(":", 1)[-1].strip().lower() else ""


def status():
    """One payload for the Network page."""
    if not available():
        return {"available": False, "devices": [], "wifi": [], "saved": [],
                "radio": False, "ap": "", "has_wifi": False}
    devs = devices()
    return {
        "available": True,
        "radio": radio_on(),
        "has_wifi": any(d["type"] == "wifi" for d in devs),
        "ap": ap_active(),
        "devices": devs,
        "wifi": wifi_list(False),
        "saved": saved_wifi(),
    }


# -- writing ------------------------------------------------------------------

def set_radio(on):
    _nmcli(["radio", "wifi", "on" if on else "off"], sudo=True)
    return status()


def join(ssid, password="", hidden=False, autoconnect=True):
    """Join a network by name. Replaces any saved profile for that SSID.

    `autoconnect` is the operator's "connect automatically" tick: off, the
    box joins now but will not come back to this network by itself after a
    reboot -- the profile stays saved, so it can be joined again from the
    list without retyping the password.

    `nmcli device wifi connect` works out the security from the access point
    it can SEE. When it cannot -- a hidden network, or one that has scanned
    away by the time the operator has typed the password -- it writes a
    profile with a password and no key-mgmt, and NetworkManager refuses it:
    "802-11-wireless-security.key-mgmt: property is missing". Seen on the
    box in the field with the wifi it had joined a hundred times.

    So the profile is written out in full instead, security named, and then
    brought up. The convenience form is not used at all: the explicit form
    does everything it does, on every network, and never leaves a half-made
    profile behind for the next attempt to trip over.
    """
    ssid = (ssid or "").strip()
    if not ssid:
        raise NetError("a network name is required")
    if wifi_helper_installed():
        # The same profile, written by the root helper with the passphrase on
        # its stdin: it deletes any other profile of that name, writes a
        # 0600 keyfile, brings it up, and deletes it again if it will not
        # come up -- this function's old steps, minus the argv.
        _wifi_profile({"kind": "join", "ssid": ssid, "psk": password or "",
                       "hidden": bool(hidden), "autoconnect": bool(autoconnect)})
        return status()
    # LEGACY (no helper yet): the passphrase is on nmcli's argv below.
    # One profile per SSID, ours, named for the network. Anything saved under
    # that name before -- including the half-made one -- goes first.
    try:
        _nmcli(["connection", "delete", ssid], sudo=True, timeout=30)
    except NetError:
        pass
    args = ["connection", "add", "type", "wifi", "ifname", AP_DEVICE,
            "con-name", ssid, "ssid", ssid,
            "connection.autoconnect", "yes" if autoconnect else "no"]
    if hidden:
        args += ["802-11-wireless.hidden", "yes"]
    if password:
        # WPA-PSK covers WPA2 and, on a NetworkManager new enough to offer
        # it, WPA3-transition networks. A too-short passphrase is refused
        # here with NetworkManager's own wording rather than joining to a
        # network that will never authenticate.
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    _nmcli(args, sudo=True, timeout=60)
    try:
        _nmcli(["connection", "up", ssid], sudo=True, timeout=90)
    except NetError:
        # A profile that could not come up is not worth keeping: left in
        # place with autoconnect on, NetworkManager would keep retrying a
        # wrong password against the AP for ever.
        try:
            _nmcli(["connection", "delete", ssid], sudo=True, timeout=30)
        except NetError:
            pass
        raise
    return status()


def _ipv4_settings(entry, dhcp, ip, prefix, gateway, dns, devs=None, role="local",
                   metric=None):
    """Validate the form and return the ipv4.* pairs to write. Writes nothing.

    Everything that can be wrong is found here, before any nmcli write: the
    operator is often reaching this page through the interface being
    changed, and an address NetworkManager half-takes is a board nobody can
    reach. `devs` is the devices() list the caller already has, so the other
    jacks' subnets can be checked against without a second round of nmcli.

    never-default and route-metric are written every time, both ways, from
    `role` (netuplink: auto_role decides it, route_pairs spells it): a port
    with no gateway is never a default route, whatever it held before. On
    the way back to DHCP: never-default no (a profile left at yes takes the
    lease's address and ignores its router) and the metric back to -1.

    DNS (Tok Aba, 2026-10-09): `dns` is the form's own servers
    (netaddr.dns_servers). On DHCP they replace the lease's
    (ignore-auto-dns yes); blank, the lease's are used again.
    """
    servers = netaddr.dns_servers(dns)
    if dhcp:
        # Clearing the static fields matters as much as the method: a profile
        # left holding an address and a gateway under method auto is one
        # nmcli edit away from being static again with stale values.
        return ["ipv4.method", "auto",
                "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ",".join(servers),
                "ipv4.never-default", "no", "ipv4.route-metric", "-1",
                "ipv4.ignore-auto-dns", "yes" if servers else "no"]
    ip = str(ip or "").strip()
    gateway = str(gateway or "").strip()
    if not _valid_ip(ip):
        raise NetError(f"{ip or 'that'} is not an IPv4 address")
    bits = mask_to_prefix(prefix)
    if gateway and not _valid_ip(gateway):
        raise NetError(f"{gateway} is not an IPv4 address")
    _refuse_bad_host(ip, bits, gateway)
    # ...and not another port's address either (board .47: a gateway that is
    # the box itself is a default route to nowhere, and before 1.0.14 only
    # the spelling was checked). The port being set is left out: its address
    # is the one being replaced.
    for other in (devs or []):
        if other.get("device") != entry.get("device") and gateway \
                and other.get("ip") == gateway:
            raise NetError(f"the gateway {gateway} is this box's own address "
                           f"on {other['device']} - it must be the router on "
                           "that network, or blank on a machine-only network")
    _refuse_ap_overlap(entry.get("device"), ip, bits)
    netaddr.refuse_sibling_overlap(entry.get("device"), ip, bits, devs)
    if not servers:
        # Blank is "automatic", and a manual profile with no DNS server
        # resolves no names at all: no update check, no template fetch, no
        # email alert, and nothing on the page to say why. So the resolvers
        # the interface is ALREADY using are carried over: they came from the
        # lease and are known to work on this network. The gateway is the
        # last resort, and only that -- it was the first guess here, and it
        # broke a board whose router does not serve DNS (the DHCP server
        # hands out 8.8.8.8).
        servers = [d for d in (entry.get("dns") or []) if _valid_ip(d)]
    if not servers and gateway:
        servers = [gateway]
    return ["ipv4.method", "manual",
            "ipv4.addresses", f"{ip}/{bits}",
            "ipv4.gateway", gateway,
            "ipv4.dns", ",".join(servers),
            "ipv4.ignore-auto-dns", "no"] + _route_pairs(role if gateway else "local", metric)


#: NetworkManager's hotspot subnet when the profile does not set one -- the
#: `ipv4.method shared` default, 10.42.0.1/24 on wlan0. Used now only when the
#: box's own hotspot address would collide with a wired network (ap_address_plan).
AP_DEFAULT_SUBNET = "10.42.0.0/24"
NM_SHARED_ADDRESS = "10.42.0.1"

#: The hotspot's own address unless network [ap].address says otherwise: the
#: box answers its hotspot clients here, and they get 192.168.1.x leases.
DEFAULT_AP_ADDRESS = "192.168.1.1"
AP_PREFIX = 24
# .env IRIV_AP_ADDRESS replaces it when it is a private IPv4 host address.
try:
    from core import envfile as _envfile
    _env_ip = ipaddress.IPv4Address(_envfile.get("IRIV_AP_ADDRESS", DEFAULT_AP_ADDRESS))
    _env_net = ipaddress.ip_network(f"{_env_ip}/{AP_PREFIX}", strict=False)
    if _env_ip.is_private and not _env_ip.is_loopback and not _env_ip.is_link_local \
            and _env_ip not in (_env_net.network_address, _env_net.broadcast_address):
        DEFAULT_AP_ADDRESS = str(_env_ip)
except Exception:                                             # noqa: BLE001
    pass


def check_ap_address(ip):
    """A valid hotspot address (a private IPv4 host address in its /24), or
    NetError saying what is wrong with it. Returns it normalised."""
    raw = str(ip or "").strip()
    try:
        addr = ipaddress.IPv4Address(raw)
    except ValueError:
        raise NetError("The hotspot address must be an IPv4 address such as 192.168.1.1.")
    net = ipaddress.ip_network(f"{addr}/{AP_PREFIX}", strict=False)
    if not addr.is_private or addr.is_loopback or addr.is_link_local:
        raise NetError("Use a private address (192.168.x.x, 10.x.x.x or 172.16-31.x.x).")
    if addr in (net.network_address, net.broadcast_address):
        raise NetError(f"{addr} is the network's own {'first' if addr == net.network_address else 'last'} "
                       "address - pick one between .1 and .254.")
    return str(addr)


def ap_address(config_dir=None):
    """The hotspot address this box is SET to use: network [ap].address, else
    DEFAULT_AP_ADDRESS. A stored value that no longer validates reads as the
    default rather than breaking the hotspot."""
    from core import config                                   # noqa: PLC0415
    ap = config.load("network", config_dir).get("ap") or {}
    try:
        return check_ap_address(ap.get("address") or DEFAULT_AP_ADDRESS)
    except NetError:
        return DEFAULT_AP_ADDRESS


def _wired_subnets():
    """[(device, ip_network)] every non-wlan interface holds now."""
    out = []
    try:
        for dev in devices():
            if dev.get("device") == AP_DEVICE or not dev.get("ip"):
                continue
            try:
                out.append((dev["device"], ipaddress.ip_network(
                    f"{dev['ip']}/{dev.get('prefix') or 24}", strict=False)))
            except ValueError:
                pass
    except Exception:                                         # noqa: BLE001
        pass
    return out


def ap_address_plan(config_dir=None, wanted=None):
    """{"address", "wanted", "conflict"}: the address the hotspot will really
    take, the one set, and the wired port that blocks it ("" if none).

    The hotspot and a cable in the SAME subnet is two connected routes to one
    network, and ethernet's wins: the box answers its hotspot clients out of
    the wrong port and they lose the page (the reason _refuse_ap_overlap
    exists, seen from the other side). So when the set address collides with
    a network a cable is on now, the hotspot keeps NetworkManager's own
    10.42.0.1 and the page says why, rather than taking the box off both.
    """
    wanted = wanted or ap_address(config_dir)
    net = ipaddress.ip_network(f"{wanted}/{AP_PREFIX}", strict=False)
    for device, wired in _wired_subnets():
        if wired.overlaps(net):
            return {"address": NM_SHARED_ADDRESS, "wanted": wanted,
                    "conflict": f"{device} ({wired})"}
    return {"address": wanted, "wanted": wanted, "conflict": ""}


def _ap_subnets():
    """Every subnet the box's access point holds now, or WILL hold at boot.

    Live: wlan0's own address while it broadcasts. Set: wlan0 is set to be
    the access point (wifi_mode) but is a Wi-Fi client now -- no live AP
    address to read, and the hotspot comes up on NetworkManager's default. Checking only the live one let an
    address in 10.42.0.0/24 through while the switch was pending, and the
    AP clients lost the box after the reboot as soon as the cable was in.
    """
    nets = []
    if ap_active():
        wlan = next((d for d in devices() if d.get("device") == AP_DEVICE), None)
        if wlan and wlan.get("ip"):
            try:
                nets.append(ipaddress.ip_network(
                    f"{wlan['ip']}/{wlan.get('prefix') or 24}", strict=False))
            except ValueError:
                pass
        if not nets:
            nets.append(ipaddress.ip_network(f"{ap_address()}/{AP_PREFIX}", strict=False))
    else:
        try:
            staged = wifi_mode() == "ap"
        except Exception:                                     # noqa: BLE001
            staged = False
        if staged:
            nets.append(ipaddress.ip_network(f"{ap_address()}/{AP_PREFIX}", strict=False))
    return nets


def _refuse_ap_overlap(device, ip, bits):
    """Refuse a wired address inside the box's own access-point subnet.

    The AP's clients reach the box over wlan0's connected route (10.42.0.0/24
    by NetworkManager's default). A cable configured into that same subnet
    gets a connected route of its own, and ethernet's route metric (100)
    beats Wi-Fi's (600): the moment the cable is in, the box answers its AP
    clients out of the wrong port and the setup laptop loses the page. That
    is the one way a wired address can take the AP side away, and it is a
    typo-sized mistake, so it is refused rather than warned about.
    """
    if device == AP_DEVICE:
        return
    try:
        mine = ipaddress.ip_network(f"{ip}/{bits}", strict=False)
    except ValueError:
        return
    for ap_net in _ap_subnets():
        if mine.overlaps(ap_net):
            raise NetError(f"{ip}/{bits} overlaps this box's own access point "
                           f"({ap_net}) - AP clients would lose the box when "
                           "the cable is connected. Pick an address outside it")


def set_ipv4(device, dhcp=True, ip="", prefix="24", gateway="", dns="", via="",
             uplink=None):
    """Put an interface on DHCP, or on a fixed address.

    Written against the PROFILE, not the live device: NetworkManager owns the
    address, and anything set with `ip addr` is gone at the next carrier
    event.

    TWO CASES, by whether anything is active on the interface:

    LIVE (a connected interface): the profile it is on is modified and
    brought up so the change takes hold now. The page then offers a reboot,
    because every service that cached the old address needs restarting, but
    the interface itself must not be left holding one address while its
    profile says another.

    PRESET (a wired jack with nothing active on it -- no cable, usually, or
    a box in access-point mode that was never on a wire): an installer wants
    the address set before the cable goes in. A wired profile BOUND to that
    interface (ifname) is modified, or added if there is none, with
    autoconnect on and PRESET_PRIORITY, and is NOT brought up: nothing is
    live, so nothing needs a reboot, and NetworkManager applies it when the
    jack gets carrier. Bound by interface name, it cannot come up on wlan0
    or on the other jack. Refused for anything that is not ethernet (wlan0
    has its own story: join or AP) and for `via`, the interface this request
    arrived on.

    Which port carries the internet is decided here, not asked (netuplink
    auto_role); `uplink`, the old checkbox an older page may still send, is
    ignored. The reply's `role` says what was decided. It cannot take anything from the access point: hotspot
    clients live on wlan0's connected route, and the one collision that WOULD
    cut them, an address inside the AP subnet, is refused (_refuse_ap_overlap).

    `prefix` takes either a prefix length or a subnet mask (mask_to_prefix).
    Returns status() with `deferred` true for a preset.
    """
    device = str(device or "").strip()
    if not device:
        raise NetError("an interface is required")
    devs = devices()
    entry = next((d for d in devs if d.get("device") == device), None)
    if entry is None:
        raise NetError(f"this box has no interface called {device}")
    name = (entry.get("connection") or "").strip()
    live = bool(name) and name != "--"
    if entry.get("method") == "shared":
        # The hotspot's own profile: "DHCP" here (a DNS-only Apply is one)
        # would turn it into a client profile and take the AP away.
        raise NetError(f"{device} is broadcasting the access point - switch "
                       "it to Wi-Fi first")
    if not live:
        if entry.get("type") != "ethernet" or device == AP_DEVICE:
            raise NetError(f"{device} is not connected - join a network on it "
                           "first")
        if via and device == via:
            raise NetError(f"{device} carries this page, so it cannot be "
                           "preset as an unplugged interface")
        if str(entry.get("state") or "").startswith("unmanaged"):
            raise NetError(f"NetworkManager does not manage {device} on this "
                           "box, so an address saved for it would never be used")
    # The form is refused before any port is probed: a typo answers at once.
    settings = _ipv4_settings(entry, dhcp, ip, prefix, gateway, dns, devs)
    role = "dhcp" if dhcp else _auto_role(device, gateway, live, devs)
    if role in ("uplink", "standby"):
        metric = _uplink_metric(device) if role == "uplink" else None
        settings = _ipv4_settings(entry, dhcp, ip, prefix, gateway, dns, devs, role, metric)

    if live:
        _nmcli(["connection", "modify", name] + settings, sudo=True, timeout=30)
        try:
            _nmcli(["connection", "up", name], sudo=True, timeout=90)
        except NetError:
            # Bringing it up is how the change takes effect, but a profile
            # that will not come up right now is not necessarily wrong, and
            # it is already written. The page reads the state back.
            pass
        return dict(status(), deferred=False, role=role)

    # PRESET. Never `connection up`: there is no carrier to bring it up on,
    # and nothing about the running box changes until there is.
    keep = ["connection.autoconnect", "yes",
            "connection.autoconnect-priority", str(PRESET_PRIORITY)]
    saved = _preset_profile(device)
    if saved:
        _nmcli(["connection", "modify", "uuid", saved["uuid"]] + keep + settings,
               sudo=True, timeout=30)
    else:
        # Empty values are left out of an ADD: there is nothing to clear on a
        # profile that does not exist yet.
        args = ["connection", "add", "type", "ethernet", "ifname", device,
                "con-name", preset_name(device)] + keep
        for i in range(0, len(settings), 2):
            if settings[i + 1] != "":
                args += [settings[i], settings[i + 1]]
        _nmcli(args, sudo=True, timeout=30)
    return dict(status(), deferred=True, role=role)


# -- reboot pending after a live address change --------------------------------
# A fixed address applied live is on the interface at once, but every service
# that cached the old one only picks it up from a restart. The page offers
# "Reboot now / Later", and Later has to stay visible on the page: the device
# is noted against THIS boot's id, so the note goes stale by itself at the
# next boot -- there is no clean-up step to forget.

_PROCESS_TOKEN = uuid.uuid4().hex


def _boot_id():
    """This boot's id; this process's own token where the kernel has none."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
            return fh.read().strip() or _PROCESS_TOKEN
    except OSError:
        return _PROCESS_TOKEN


def mark_ipv4_reboot(device, config_dir=None):
    """Note that `device` took an address live and waits on a reboot."""
    from core import config                                   # noqa: PLC0415
    local = dict(config.load_local("network", config_dir))
    note = dict(local.get("ipv4_reboot") or {})
    boot = _boot_id()
    devs = list(note.get("devices") or []) if note.get("boot") == boot else []
    if device and device not in devs:
        devs.append(str(device))
    local["ipv4_reboot"] = {"boot": boot, "devices": devs}
    config.save_local("network", local, config_dir)
    return devs


def clear_ipv4_reboot(device, config_dir=None):
    """Drop `device` from the note: it went back to DHCP before the reboot.

    The note says "this interface took a fixed address live and its services
    still hold the old one". Put back on DHCP, the operator has undone what
    the reboot was for; a note left standing would ask for a restart nobody
    needs and teach people to ignore it.
    """
    from core import config                                   # noqa: PLC0415
    local = dict(config.load_local("network", config_dir))
    note = dict(local.get("ipv4_reboot") or {})
    devs = [d for d in (note.get("devices") or []) if d != device]
    if devs == list(note.get("devices") or []):
        return devs
    local["ipv4_reboot"] = {"boot": note.get("boot") or _boot_id(), "devices": devs}
    config.save_local("network", local, config_dir)
    return devs


def ipv4_reboot_pending(config_dir=None):
    """Interfaces given an address live since this boot, waiting on a reboot."""
    from core import config                                   # noqa: PLC0415
    note = dict(config.load_local("network", config_dir).get("ipv4_reboot") or {})
    if note.get("boot") != _boot_id():
        return []
    return [str(d) for d in (note.get("devices") or [])]


def forget(name):
    """Delete a saved profile by name."""
    if not (name or "").strip():
        raise NetError("a connection name is required")
    _nmcli(["connection", "delete", name], sudo=True)
    return status()


def ap_enable(ssid, password, address=None):
    """Raise the box's own access point on wlan0, at `address` (default: the
    one set, network [ap].address -- see ap_address_plan for when a cable's
    network makes it fall back to 10.42.0.1).

    The password floor is enforced here rather than left to the supplicant:
    nmcli accepts a short one, the AP then fails to start, and the box
    disappears from the network with no message anywhere.
    """
    ssid = (ssid or "").strip()
    password = password or ""
    if not ssid:
        raise NetError("an access point name is required")
    if len(password) < MIN_AP_PASSWORD:
        raise NetError("the access point password must be at least %d characters"
                       % MIN_AP_PASSWORD)
    # Wi-Fi OFF first, said explicitly. The hotspot takes wlan0 anyway, but
    # doing it as a step means the joined network is down before the AP is
    # up rather than yanked out from under it, and reads in the log as the
    # decision it is: one radio, and while it broadcasts it is not a client.
    joined = _wlan_connection()
    if joined and not ap_active():
        try:
            _nmcli(["connection", "down", joined], sudo=True, timeout=30)
        except NetError:
            pass
    # The EXISTING hotspot profile, brought up by the helper -- not re-created
    # with the key on argv at every boot. The helper rewrites the keyfile only
    # when the name, key or address changed, and leaves an unchanged AP that
    # is already up alone, so this is safe to call at every hub start.
    plan = ap_address_plan(wanted=check_ap_address(address) if address else None)
    if plan["conflict"]:
        logger.warning("[NET] hotspot address %s collides with %s - the hotspot "
                       "uses %s until one of them changes", plan["wanted"],
                       plan["conflict"], plan["address"])
    # NetworkManager's own address needs no setting; anything else is written
    # into the profile (shared mode serves DHCP on that /24).
    cidr = "" if plan["address"] == NM_SHARED_ADDRESS else f"{plan['address']}/{AP_PREFIX}"
    if wifi_helper_installed():
        _wifi_profile({"kind": "ap", "ssid": ssid, "psk": password,
                       **({"address": cidr} if cidr else {})})
        return status()
    # LEGACY (no helper yet): the key is on nmcli's argv.
    _nmcli(["device", "wifi", "hotspot", "ifname", AP_DEVICE,
            "ssid", ssid, "password", password], sudo=True, timeout=60)
    name = ap_active()
    if name:
        try:
            _nmcli(["connection", "modify", name, "ipv4.method", "shared",
                    "ipv4.addresses", cidr], sudo=True, timeout=30)
            _nmcli(["connection", "up", name], sudo=True, timeout=60)
        except NetError as exc:
            logger.warning("[NET] hotspot address not applied: %s", exc)
    return status()


def reset(ssid, password=""):
    """Forget every saved Wi-Fi network and come back as the access point.

    The Network page's "Reset Wi-Fi" (POST /api/hub/network/reset) has called
    this since the AP became a staged setting, and it did not exist: the
    button answered a 500. Ethernet profiles are not touched. The key is the
    one passed, else the stored one (ap_password()); the route stores
    DEFAULT_AP_PASSWORD first, so a reset box is keyed as the manual says.
    """
    for saved in saved_wifi():
        try:
            _nmcli(["connection", "delete", saved["name"]], sudo=True, timeout=30)
        except NetError:
            pass
    return ap_enable(ssid, password or ap_password())


def ap_disable():
    name = ap_active()
    if not name:
        return status()
    _nmcli(["connection", "down", name], sudo=True)
    # ...and hand wlan0 back to Wi-Fi: NetworkManager rejoins whichever
    # saved network is in range, which is what "AP off" means to the
    # operator. Best effort -- no saved network in range is not an error.
    try:
        _nmcli(["device", "connect", AP_DEVICE], sudo=True, timeout=45)
    except NetError:
        pass
    return status()


#: The two things wlan0 can be -- and it is always exactly one of them. The
#: Network page switches it LIVE (wifiswitch.py, Tok Aba 2026-10-07: never a
#: reboot) and writes this SETTING as the person chose it (to Wi-Fi as soon
#: as the hotspot is taken down, to the AP once it broadcasts); the boot
#: re-asserts it (apply_wifi_mode) and never overrides it -- except that a
#: hotspot already broadcasting at a hub start is left up (it is never
#: started for a Wi-Fi setting). There is no "both off": Wi-Fi off IS
#: the access point, and the access point off IS Wi-Fi.
MODES = ("ap", "wifi")
DEFAULT_MODE = "ap"


def wifi_mode(config_dir=None):
    """What wlan0 is SET to be -- not necessarily what it is right now."""
    from core import config                                   # noqa: PLC0415
    mode = str((config.load("network", config_dir).get("ap") or {})
               .get("mode") or DEFAULT_MODE).lower()
    return mode if mode in MODES else DEFAULT_MODE


def current_mode():
    """What wlan0 IS right now."""
    return "ap" if ap_active() else "wifi"


def ap_password(config_dir=None):
    """The hotspot key this box broadcasts: network [ap].password, or "".

    The ONE reader: the Network page, the front panel and the boot path all
    ask here. No fallback of its own -- ensure_ap_password() is what stores
    the default, so the key on the screen is always one written down in
    network.local.toml rather than a literal in three places.
    """
    from core import config                                   # noqa: PLC0415
    ap = config.load("network", config_dir).get("ap") or {}
    return str(ap.get("password") or "")


def ensure_ap_password(config_dir=None):
    """The stored key, or DEFAULT_AP_PASSWORD saved to network.local.toml.

    scripts/install.sh calls this at install and the hub at every boot (a
    box whose local file went missing comes back on the default, which is
    what the manual tells the person at the box to type). A key somebody
    STORED is kept here; a random per-box key from 2026-10-05..07 is put
    back to the default once by apdefault.migrate, not by this function.
    """
    from core import config                                   # noqa: PLC0415
    key = ap_password(config_dir)
    if key:
        return key
    local = dict(config.load_local("network", config_dir))
    ap = dict(local.get("ap") or {})
    ap["password"] = DEFAULT_AP_PASSWORD
    local["ap"] = ap
    config.save_local("network", local, config_dir)
    return DEFAULT_AP_PASSWORD


def set_wifi_mode(mode, password=None, config_dir=None):
    """Record the radio's job (the boot re-asserts it). Returns the saved mode."""
    from core import config                                   # noqa: PLC0415
    mode = str(mode or "").lower()
    if mode not in MODES:
        raise NetError("mode must be ap or wifi")
    if mode == "ap" and password is not None:
        from core.services import apconf                      # noqa: PLC0415
        apconf.check_ap_password(password)   # the rules Save uses
    local = dict(config.load_local("network", config_dir))
    ap = dict(local.get("ap") or {})
    ap["mode"] = mode
    if password:
        ap["password"] = password
    local["ap"] = ap
    config.save_local("network", local, config_dir)
    return mode


def apply_wifi_mode(ssid, password, mode, grace=25.0, log=None, rekey=False):
    """At boot: make wlan0 what it is set to be.

    `ap`: broadcast the access point printed on the panel, taking any joined
    network down (ap_enable does that). `wifi`: let NetworkManager join a
    saved network -- but a hotspot that is ALREADY broadcasting is left up
    (Tok Aba, 2026-10-14, "leave a running hotspot alone"): this only ever
    PRESERVES one, it never starts one, and the saved setting stays Wi-Fi
    for the person's next action to decide. That is the hub restart of an
    over-the-air update (same boot, hotspot still up). After a REBOOT no
    hotspot is up (the AP profile is autoconnect=false, wifi_profile.py), so
    a box saved as Wi-Fi joins or stays dark, as set. When the hotspot
    state cannot be read, the radio is left alone and that is logged. The
    radio is switched on
    first if something left it off -- one of the two must run. NetworkManager
    is given `grace` seconds so its own autoconnect has settled and this
    reads the real state. Nothing is done when NetworkManager is absent or
    there is no radio. Runs in a thread of its own, returned; never raises.
    A `wifi` box that cannot join stays without its hotspot: nothing raises
    it automatically (Tok Aba, 2026-10-14).
    """
    import threading                                          # noqa: PLC0415
    import time                                               # noqa: PLC0415

    def _run():
        time.sleep(max(0.0, float(grace)))
        try:
            if not available():
                return
            if not any(d["type"] == "wifi" for d in devices()):
                return
            if not radio_on():
                _nmcli(["radio", "wifi", "on"], sudo=True)
            # With the helper, the AP is re-asserted at every start: a key
            # changed since the hotspot came up (a re-key on the page)
            # reaches the radio now, and an unchanged one is left up
            # untouched.
            # `rekey`: the hub just stored a key the box did not have (the
            # default, ensure_ap_password). Without the helper an AP already
            # up would keep broadcasting whatever it came up with while the
            # front panel printed the stored one, until the next reboot.
            if mode == "ap" and (wifi_helper_installed() or rekey
                                 or not ap_active()):
                ap_enable(ssid, password)
                if log:
                    log("[NET] wlan0 set to access point - %s is up", ssid)
            elif mode == "wifi":
                # Read and log only: nothing here moves the radio (docstring).
                try:
                    running = _ap_probe()
                except NetError as exc:
                    if log:
                        log("[NET] wlan0 set to Wi-Fi; could not read whether the "
                            "hotspot is up (%s) - the radio is left as it is", exc)
                    return
                if running and log:
                    log("[NET] wlan0 set to Wi-Fi, but the hotspot %s is already up - "
                        "left up (the setting stays Wi-Fi)", running)
        except Exception as exc:                              # noqa: BLE001
            if log:
                log("[NET] could not apply the wlan0 mode: %s", exc)

    thread = threading.Thread(target=_run, name="wlan-mode", daemon=True)
    thread.start()
    return thread
