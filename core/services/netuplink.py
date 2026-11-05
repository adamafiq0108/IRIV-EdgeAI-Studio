# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
netuplink.py -- which port a fixed address puts the box's internet on, the
route metric that says so, and the snapshot of the port's ipv4 settings that
uplinkguard puts back when it does not reach the internet.

THE UPLINK IS DECIDED, NOT TICKED (Tok Aba, 2026-10-09: "remove 'this port is
the internet uplink', just make it auto"). From 10-07 to 10-09 the form had a
checkbox, ticked by default; the one fact it really asked for is already in
the form -- a machine-only network (PLC, Modbus, cameras) has no gateway --
and the rest is something the box can find out better than the operator can
guess it. auto_role() says, for a fixed address:

  local    no gateway: never the uplink, never a default route.
  uplink   a gateway, and this port carries a working internet NOW, or no
           port reaches the internet: it goes ahead of every port
           (uplink_metric) and uplinkguard proves it or puts it back.
  standby  a gateway, and another port already reaches the internet: a
           default route at STANDBY_METRIC, behind every port NetworkManager
           numbers itself, so it can never take the internet from a working
           uplink (board .22: a 10.0.0.1 nobody answers at did exactly that).
           uplinkguard then asks through the port itself: reached, it stays
           as the fallback routeheal moves to if the uplink dies; not
           reached, it is made local (a gateway that routes nowhere must not
           be in the table for routeheal to pick). A preset (no cable, nothing
           to ask) is always standby.

Lifted out of netconf.py (which keeps the names, re-exported, because the
routes and the tests know them as netconf's) so that file stays under its
line ceiling. The nmcli reads still go through netconf's own _nmcli,
devices() and _split, looked up at call time: one nmcli wrapper on the box,
and a test that stubs netconf's stubs these too.
"""

from core.services import routeheal

#: The route metric of a fixed address marked as the internet uplink.
#:
#: Tok Aba (2026-10-07): the static uplink the operator just set is THE
#: uplink, whatever DHCP hands the other ports. NetworkManager's own defaults
#: are 100 for the first ethernet (+1 per further jack), 600 for Wi-Fi, 700 for
#: a modem; this sits under all of them. routeheal's demotion (2000) still
#: sits past everything, so a static uplink whose gateway is dead still gives
#: way to a live one by itself.
UPLINK_METRIC = 50
#: Each newer uplink goes one under the best OTHER default route there is
#: (uplink_metric), so two static uplinks set one after the other resolve to
#: the one set last; never below this floor, a band nothing else writes.
UPLINK_METRIC_FLOOR = 20

#: The route metric of a fixed address with a gateway that is NOT the uplink
#: (auto_role "standby"): past NetworkManager's own 100/600/700, short of
#: routeheal's 2000, so it carries the internet only when nothing else does.
STANDBY_METRIC = 1000

#: The ipv4 settings a revert puts back, as `connection modify` takes them.
#: ignore-auto-dns last: the (3, 7) indexes below stay where they were.
SNAPSHOT_KEYS = ("ipv4.method", "ipv4.addresses", "ipv4.gateway", "ipv4.dns",
                 "ipv4.never-default", "ipv4.route-metric", "ipv4.ignore-auto-dns")


def auto_role(device, gateway, live, devs=None):
    """"local", "uplink" or "standby" for a fixed address on `device` (see the
    header). The default routes are asked best first, through their own
    ports (uplinkguard.reachable), and the first that reaches the internet
    decides: this port, it stays the uplink; another, this one is standby;
    none, this one is tried as the uplink. So a port that holds the route
    but answers nobody (board .22) is not preferred over one that works. A
    working uplink answers on the first probe; a dead one costs a few
    seconds, inside the Apply that asked. A route routeheal demoted does
    not count, for this port or any other."""
    if not str(gateway or "").strip():
        return "local"
    if not live:
        return "standby"
    from core.services import uplinkguard                       # noqa: PLC0415
    ips = {d.get("device"): d.get("ip") or "" for d in (devs or [])}
    for route in routeheal.default_routes():
        if route["metric"] < routeheal.DEMOTED_METRIC \
                and uplinkguard.reachable(route["dev"], ips.get(route["dev"], "")):
            return "uplink" if route["dev"] == device else "standby"
    return "uplink"


def route_pairs(role, metric=None):
    """The ipv4.never-default / ipv4.route-metric pairs for a role. -1 is
    NetworkManager's default for the port type, so a local port keeps
    neither an uplink's metric nor routeheal's demotion from before."""
    if role == "uplink":
        return ["ipv4.never-default", "no",
                "ipv4.route-metric", str(metric or UPLINK_METRIC)]
    if role == "standby":
        return ["ipv4.never-default", "no", "ipv4.route-metric", str(STANDBY_METRIC)]
    return ["ipv4.never-default", "yes", "ipv4.route-metric", "-1"]


def uplink_metric(device):
    """The metric that puts `device`'s default route ahead of every other
    port's: UPLINK_METRIC, or one under the best other default route when
    that is already lower (an uplink set earlier), not below the floor. Read
    off the kernel's routes (routeheal.default_routes), no privilege."""
    others = [r["metric"] for r in routeheal.default_routes() if r["dev"] != device]
    best = min(others) if others else UPLINK_METRIC + 1
    return max(UPLINK_METRIC_FLOOR, min(UPLINK_METRIC, best - 1))


def ipv4_snapshot(device):
    """(connection, [key, value, ...]) of the profile LIVE on `device` now,
    exactly as `nmcli connection modify` will take it back; ("", []) when
    nothing is live on it. Read before set_ipv4 writes, so uplinkguard can
    put the port back if the new uplink does not reach the internet."""
    from core.services import netconf                          # noqa: PLC0415
    entry = next((d for d in netconf.devices() if d.get("device") == device), None)
    name = ((entry or {}).get("connection") or "").strip()
    if not name or name == "--":
        return "", []
    out = netconf._nmcli(["-t", "-f", ",".join(SNAPSHOT_KEYS), "connection", "show",
                          name], timeout=15)
    values = {}
    for line in out.splitlines():
        parts = netconf._split(line)
        if len(parts) >= 2:
            values[parts[0]] = ":".join(parts[1:]).strip()
    settings = []
    for key in SNAPSHOT_KEYS:
        value = values.get(key, "")
        if key == "ipv4.method" and not value:
            value = "auto"
        elif key == "ipv4.route-metric" and not value:
            value = "-1"
        elif key in ("ipv4.never-default", "ipv4.ignore-auto-dns"):
            value = "yes" if value == "yes" else "no"
        elif value == "--":
            value = ""
        settings += [key, value]
    # nmcli lists several addresses and DNS servers with ", "; it takes ","
    for i in (3, 7):
        settings[i] = ",".join(p.strip() for p in settings[i].split(",") if p.strip())
    return name, settings
