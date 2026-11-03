# -*- coding: utf-8 -*-
"""
gatewayfault.py -- a default gateway that can never forward, and the one
case that is corrected rather than demoted.

Board .47 (2026-10-07, v1.0.18): eth1 fixed at 192.168.3.1/24 with gateway
192.168.3.1 -- the box's OWN address, typed on v1.0.12 whose form checked
only that it parsed (the self and subnet checks arrived in 1.0.14, and a
saved profile is not rewritten). eth1's static route (metric 100) beat
eth0's DHCP uplink (101), every update check said "No internet", and
routeheal did not demote it: its "does the gateway answer" test pings, and
a ping to one's own address answers from the loopback. The neighbour entry
for the box's own address stays INCOMPLETE for ever; nothing was ever
forwarding.

So two kinds of gateway are dead WITHOUT being asked (fault):

  * "self": one of the box's own addresses, on any port;
  * "offlink": not inside a subnet the port holds, so nothing on that wire
    can be asked to forward to it.

A self-gateway gets more than routeheal's demotion (heal_self): an address
the box holds is never a router that is merely rebooting, and a demoted dead
end is still a dead end, so the PROFILE is corrected -- gateway cleared,
ipv4.never-default yes, what the Network page writes for a machine-only
port since 1.0.15 -- even on a box with that one default route, and the
admins' Inbox names the port. Only when the profile itself says that gateway
under method manual: a route from a lease or from `ip route` by hand is
reported and left alone. The writes are routeheal's verbs (`nmcli
connection modify uuid ...`, `nmcli connection up uuid ...`), so the
narrowed irivai-box sudoers grant already covers them and no box needs a
manual step. explain() is the sentence the update check prints instead of
the generic "No internet connection" when the winning route is one of these.

Reads go through routeheal.run so the tests' fake answers both modules.
"""

import ipaddress
import re

from core.services import routeheal

NOTICE_KIND = "gateway_self"


def own_addresses():
    """{dev: [IPv4Interface]} for every IPv4 address the box holds, off
    `ip -4 -o addr show` ("3: eth1    inet 192.168.3.1/24 brd ... scope
    global eth1"). No privilege; {} with no iproute2."""
    rc, out = routeheal.run(["ip", "-4", "-o", "addr", "show"])
    if rc != 0:
        return {}
    held = {}
    for line in out.splitlines():
        m = re.match(r"\s*\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", line)
        if not m:
            continue
        try:
            held.setdefault(m.group(1), []).append(ipaddress.ip_interface(m.group(2)))
        except ValueError:
            continue
    return held


def fault(gateway, dev, held=None):
    """Why `gateway` can never forward for `dev`, or "" when it could.

    "self": one of the box's own addresses, on any port (board .47). A ping
    to it answers from the loopback, so it must be judged before any ping.
    "offlink": not inside a subnet `dev` holds, so nothing on that wire can
    be asked to forward to it. A port with no address yet is not judged.
    """
    held = own_addresses() if held is None else held
    try:
        gw = ipaddress.ip_address(gateway)
    except ValueError:
        return ""
    if any(gw == iface.ip for ifaces in held.values() for iface in ifaces):
        return "self"
    mine = held.get(dev) or []
    if mine and not any(gw in iface.network for iface in mine):
        return "offlink"
    return ""


def explain(held=None):
    """One plain sentence for a caller about to say "no internet", naming
    the winning default route when it is a known dead end; "" otherwise.
    Reads only, no packet: the heal has already run by then."""
    routes = routeheal.default_routes()
    if not routes:
        return ""
    dev, gw = routes[0]["dev"], routes[0]["gateway"]
    why = fault(gw, dev, held)
    if why == "self":
        return (f"No internet: port {dev}'s gateway {gw} is this box itself, "
                f"so the route leads nowhere - clear the gateway on {dev} under "
                "Settings -> Network (a machine-only port needs none)")
    if why == "offlink":
        return (f"No internet: port {dev}'s gateway {gw} is not on {dev}'s own "
                "network, so nothing there can forward to it - fix the gateway "
                f"or the subnet mask on {dev} under Settings -> Network")
    return ""


def _profile_gateway(uuid):
    """(ipv4.method, ipv4.gateway) of a saved profile, ("", "") if unread."""
    rc, out = routeheal._nmcli(["-t", "-f", "ipv4.method,ipv4.gateway",
                                "connection", "show", "uuid", uuid])
    if rc != 0:
        return "", ""
    values = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        values[key.strip()] = value.strip()
    return values.get("ipv4.method", ""), values.get("ipv4.gateway", "")


def heal_self(config_dir, notify, log, held):
    """Correct a manual profile whose gateway is this box's own address
    (board .47). Returns the result dict when a route was found, else None.

    Unlike the demotion this acts on a box with ONE default route, writes the
    profile's gateway away and sets never-default: an address the box holds
    is never a router that is merely rebooting, and a demoted dead end would
    still be a dead end. The write is only made when the PROFILE says that
    gateway under method manual, so a route that came from elsewhere (a
    lease, `ip route` by hand) is reported and left alone.
    """
    for route in routeheal.default_routes():
        if fault(route["gateway"], route["dev"], held) != "self":
            continue
        dev, gw = route["dev"], route["gateway"]
        result = {"changed": False, "state": "self", "device": dev, "gateway": gw,
                  "detail": ""}
        name, uuid = routeheal._profile(dev)
        if not uuid:
            result["detail"] = f"{dev} has no NetworkManager profile"
            return result
        result["profile"] = name
        method, saved = _profile_gateway(uuid)
        if method != "manual" or saved != gw:
            result["detail"] = (f"profile \"{name}\" does not set gateway {gw} "
                                "itself")
            return result
        rc, _ = routeheal._nmcli(["connection", "modify", "uuid", uuid,
                                  "ipv4.gateway", "", "ipv4.never-default", "yes"],
                                 sudo=True)
        if rc != 0:
            result["detail"] = "nmcli refused to clear the gateway"
            if log:
                log("[NET] %s's gateway %s is this box's own address, and the "
                    "profile could not be corrected (sudo nmcli connection "
                    "modify refused)", dev, gw)
            return result
        routeheal._nmcli(["connection", "up", "uuid", uuid], sudo=True)
        others = [r for r in routeheal.default_routes() if r["dev"] != dev]
        result.update(changed=True, via_device=others[0]["dev"] if others else "",
                      via_gateway=others[0]["gateway"] if others else "")
        result["detail"] = (f"{dev}'s gateway {gw} was this box's own address; "
                            f"gateway cleared from profile \"{name}\" "
                            "(never-default yes)")
        if log:
            log("[NET] %s", result["detail"])
        if notify:
            _notice(result, config_dir, log)
        return result
    return None


def _notice(r, config_dir, log):
    via = (f" The internet now goes via {r['via_device']} ({r['via_gateway']})."
           if r.get("via_device") else "")
    try:
        from core.services import inbox                       # noqa: PLC0415
        inbox.notify_admins(
            NOTICE_KIND, f"{r['device']}'s gateway was this box itself",
            f"Port {r['device']} had gateway {r['gateway']}, which is this "
            "box's own address, so its default route led nowhere and the box "
            "could not reach the internet (update checks, downloads, email "
            f"alerts). The gateway was removed from {r['device']}'s profile "
            f"\"{r['profile']}\" and the port no longer carries a default "
            f"route. {r['device']} keeps its address, and machines on it (PLC, "
            f"Modbus, cameras) are unaffected.{via} If {r['device']} really "
            "has a router, enter that router's address under Settings -> "
            "Network.", config_dir, page="settings", tab="network")
    except Exception as exc:                                  # noqa: BLE001
        if log:
            log("[NET] could not post the gateway notice: %s", exc)
