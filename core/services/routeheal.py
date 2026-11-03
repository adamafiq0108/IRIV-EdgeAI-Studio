# -*- coding: utf-8 -*-
"""
routeheal.py -- the default route, and what to do when it points at nobody.

Board .22 (2026-10-05, v1.0.13): eth0 fixed at 10.0.0.10/24 with gateway
10.0.0.1 on the Modbus LAN, where nothing answers at .1; eth1 on DHCP behind
the site router. eth0's metric 100 beat eth1's 101, so everything for the
internet went to a gateway that does not exist ("[Errno 113] No route to
host" on the update check, no OTA download). The Network page now writes
never-default on a fixed port unless it is marked as the uplink; this module
is for boxes that already have the bad route, or get one by hand. heal()
demotes the WINNING default route when its gateway does not answer (one ping,
then the neighbour table: a router that drops ICMP but answers ARP is alive)
AND another port's gateway does, and tells the admins in the Inbox.

Deliberately narrow, because it writes the network without anyone asking:

  * a box with ONE gateway is never touched -- with nothing to fall back on,
    demoting it fixes nothing and a router that is merely rebooting would
    come back to find the box no longer using it;
  * nothing happens unless another gateway is PROVEN to answer;
  * a metric, not never-default and not a deleted gateway: the address and
    everything the operator typed stay, the port keeps a default route as a
    last resort, and the change is undone by setting the metric back (or
    from the Network page, which rewrites the profile);
  * only verbs netconf already runs through sudo, so the narrowed irivai-box
    sudoers grant covers it: `nmcli connection modify uuid <uuid> ...` (the
    preset path) and `nmcli connection up <name>` (the live path). `up`
    re-activates the profile: the port drops for a second or two and comes
    back on the SAME address, so a PLC poll misses a beat and a TCP session
    over it survives.

Board .47 (2026-10-07): a gateway that is the box's OWN address answers a
ping from the loopback, so the test above called it alive. gatewayfault.py
judges that (and an off-link gateway) dead without a packet, and corrects
the profile rather than demoting it; it runs first inside heal().

The hub runs it at start and every HEAL_INTERVAL_S (start_watch); the update
check and the OTA applier run it once before calling a failure "no internet".
"""

import ipaddress
import re
import subprocess
import threading
import time

#: The metric a demoted profile gets. NetworkManager's defaults are 100 for
#: ethernet (+1 per further port), 600 for Wi-Fi, 700 for a modem: anything
#: past all of them puts the route last without taking it away.
DEMOTED_METRIC = 2000

#: How often the hub looks: a healthy box pays one `ip route` and one ping.
HEAL_INTERVAL_S = 300
NOTICE_KIND = "route_demoted"

#: Neighbour states read as alive. DELAY/PROBE/STALE count too: a write is
#: only ever made on FAILED, INCOMPLETE or no entry, and a gateway still
#: being probed is left for the next round rather than demoted on a guess.
_NEIGH_ALIVE = ("REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "NOARP")


def _run(argv, timeout=10):
    """(returncode, stdout). A missing binary is 127, a hang is 124: a box
    without iproute2 (a developer's laptop) simply has no routes to heal."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return 127, ""
    except (OSError, subprocess.SubprocessError):
        return 124, ""
    return proc.returncode, proc.stdout or ""


#: Replaced by the tests with a fake that answers from canned outputs.
run = _run


def default_routes():
    """Every IPv4 default route, best first: [{gateway, dev, metric}], off
    "default via 10.0.0.1 dev eth0 proto static metric 100". No privilege.
    A default with no `via` (point-to-point) has no gateway to ask."""
    rc, out = run(["ip", "-4", "route", "show", "default"])
    if rc != 0:
        return []
    routes = []
    for line in out.splitlines():
        via = re.search(r"\bvia\s+(\S+)", line)
        dev = re.search(r"\bdev\s+(\S+)", line)
        metric = re.search(r"\bmetric\s+(\d+)", line)
        if not (via and dev):
            continue
        try:
            ipaddress.IPv4Address(via.group(1))
        except ValueError:
            continue
        routes.append({"gateway": via.group(1), "dev": dev.group(1),
                       "metric": int(metric.group(1)) if metric else 0})
    routes.sort(key=lambda r: r["metric"])
    return routes


def gateway_answers(gateway, dev, held=None):
    """Does `gateway` answer on `dev`? A gateway that cannot forward at all
    (gateway_fault) is dead without a packet. Otherwise the ping makes the
    kernel ARP for it, so the neighbour entry read after it is fresh:
    REACHABLE with no echo reply is a router filtering ICMP; FAILED,
    INCOMPLETE or none is nobody."""
    from core.services import gatewayfault                    # noqa: PLC0415
    if gatewayfault.fault(gateway, dev, held):
        return False
    rc, _ = run(["ping", "-n", "-c", "1", "-W", "1", "-I", dev, gateway], timeout=5)
    if rc == 0:
        return True
    rc, out = run(["ip", "neigh", "show", gateway, "dev", dev])
    if rc != 0:
        return False
    state = (out.strip().split() or [""])[-1].upper()
    return state in _NEIGH_ALIVE


def annotate(devices):
    """Mark each netconf.devices() entry with the default route it holds.

    `default_route` (the box's internet goes this way now), `has_default`
    (it holds one at all) and `default_metric`. The Network page reads them
    to warn before a second port is given a gateway.
    """
    routes = default_routes()
    best = routes[0]["dev"] if routes else ""
    for entry in devices:
        mine = next((r for r in routes if r["dev"] == entry.get("device")), None)
        entry["has_default"] = mine is not None
        entry["default_route"] = bool(mine) and entry.get("device") == best
        entry["default_metric"] = mine["metric"] if mine else None
    return devices


def _nmcli(args, sudo=False):
    argv = (["sudo", "-n"] if sudo else []) + ["nmcli"] + list(args)
    return run(argv, timeout=90 if "up" in args else 30)


def _profile(dev):
    """(name, uuid) of the profile active on `dev`, or ("", "")."""
    rc, out = _nmcli(["-t", "-f", "GENERAL.CONNECTION,GENERAL.CON-UUID",
                      "device", "show", dev])
    if rc != 0:
        return "", ""
    values = {}
    for line in out.splitlines():
        key, _, value = line.partition(":")
        values[key.strip()] = value.replace("\\:", ":").strip()
    name = values.get("GENERAL.CONNECTION", "")
    return ("", "") if name in ("", "--") else (name, values.get("GENERAL.CON-UUID", ""))


def _profile_metric(uuid):
    rc, out = _nmcli(["-t", "-f", "ipv4.route-metric", "connection", "show",
                      "uuid", uuid])
    try:
        return int(out.strip().partition(":")[2]) if rc == 0 else -1
    except ValueError:
        return -1                       # "-1" or blank: NetworkManager's default


def diagnose(held=None):
    """What heal() would do, writing nothing.

    {state, routes, dead, alive}: state is "ok" (the winning gateway answers),
    "single" (one gateway or none -- never touched), "offline" (the winner is
    dead and so is every other), or "heal" (dead winner, live alternative).
    """
    routes = default_routes()
    out = {"state": "ok", "routes": routes, "dead": None, "alive": None}
    if len({r["dev"] for r in routes}) < 2:
        out["state"] = "single"
        return out
    from core.services import gatewayfault                    # noqa: PLC0415
    held = gatewayfault.own_addresses() if held is None else held
    first = routes[0]
    if gateway_answers(first["gateway"], first["dev"], held):
        return out
    out["dead"] = first
    for other in routes[1:]:
        if other["dev"] != first["dev"] and gateway_answers(other["gateway"],
                                                            other["dev"], held):
            out["alive"] = other
            out["state"] = "heal"
            return out
    out["state"] = "offline"
    return out


_lock = threading.Lock()


def heal(config_dir=None, notify=True, log=None):
    """Demote a dead default route when a live one is there to take over.

    Returns {changed, state, device, gateway, profile, via_device,
    via_gateway, detail}. Never raises: it runs on a timer and in front of an
    update check, and neither may fail because of it.
    """
    with _lock:
        try:
            return _heal(config_dir, notify, log)
        except Exception as exc:                              # noqa: BLE001
            if log:
                log("[NET] route check failed: %s", exc)
            return {"changed": False, "state": "error", "detail": str(exc)}


def _heal(config_dir, notify, log):
    from core.services import gatewayfault                    # noqa: PLC0415
    held = gatewayfault.own_addresses()
    fixed = gatewayfault.heal_self(config_dir, notify, log, held)
    if fixed is not None:
        return fixed
    found = diagnose(held)
    result = {"changed": False, "state": found["state"], "detail": ""}
    if found["state"] != "heal":
        return result
    dead, alive = found["dead"], found["alive"]
    result.update(device=dead["dev"], gateway=dead["gateway"],
                  via_device=alive["dev"], via_gateway=alive["gateway"])
    name, uuid = _profile(dead["dev"])
    if not uuid:
        # Not NetworkManager's route (set by hand with `ip route`): there is
        # no profile to change, and a second writer is what this avoids.
        result["detail"] = f"{dead['dev']} has no NetworkManager profile"
        return result
    result["profile"] = name
    if _profile_metric(uuid) >= DEMOTED_METRIC:
        result["detail"] = "already demoted"
        return result
    rc, _ = _nmcli(["connection", "modify", "uuid", uuid,
                    "ipv4.route-metric", str(DEMOTED_METRIC)], sudo=True)
    if rc != 0:
        result["detail"] = "nmcli refused the metric change"
        if log:
            log("[NET] %s's gateway %s does not answer, and the route could not "
                "be demoted (sudo nmcli connection modify refused)",
                dead["dev"], dead["gateway"])
        return result
    # By uuid, not name (Pipi, 1.0.15 gate H1): a profile named like an
    # nmcli keyword ("id", "path") or starting with "-" would be misparsed.
    _nmcli(["connection", "up", "uuid", uuid], sudo=True)
    result["changed"] = True
    result["detail"] = (f"{dead['dev']}'s gateway {dead['gateway']} did not "
                        f"answer; route metric of profile \"{name}\" raised to "
                        f"{DEMOTED_METRIC}, internet now via {alive['dev']} "
                        f"({alive['gateway']})")
    if log:
        log("[NET] %s", result["detail"])
    if notify:
        _notice(result, config_dir, log)
    return result


def _notice(r, config_dir, log):
    try:
        from core.services import inbox                       # noqa: PLC0415
        inbox.notify_admins(
            NOTICE_KIND, f"Internet route moved off {r['device']}",
            f"{r['device']}'s gateway {r['gateway']} did not answer, so this "
            "box could not reach the internet (update checks, downloads, "
            f"email alerts). {r['via_device']}'s gateway {r['via_gateway']} "
            f"answers, so the box now uses {r['via_device']} for the internet: "
            f"{r['device']}'s profile \"{r['profile']}\" was given route metric "
            f"{DEMOTED_METRIC}. {r['device']} keeps its address, and machines "
            f"on it (PLC, Modbus, cameras) are unaffected. If {r['device']} is "
            "a machine-only network, clear its gateway under Settings -> "
            "Network.", config_dir, page="settings", tab="network")
    except Exception as exc:                                  # noqa: BLE001
        if log:
            log("[NET] could not post the route notice: %s", exc)


def heal_for_retry(config_dir=None, log=None):
    """For a caller about to say "no internet": heal once, True if it changed
    the route and a retry is worth making."""
    return bool(heal(config_dir, log=log).get("changed"))


_watch = None


def start_watch(config_dir=None, log=None, first_delay=45.0):
    """Check at hub start (after NetworkManager has settled) and every
    HEAL_INTERVAL_S after. One thread per process, and only where there is a
    NetworkManager to change: a developer's laptop or a CI runner never
    starts it."""
    global _watch
    import shutil                                             # noqa: PLC0415
    import sys                                                # noqa: PLC0415
    if _watch is not None or not sys.platform.startswith("linux") \
            or not shutil.which("nmcli"):
        return False

    def loop():
        time.sleep(max(0.0, first_delay))
        while True:
            heal(config_dir, log=log)
            time.sleep(HEAL_INTERVAL_S)

    _watch = threading.Thread(target=loop, name="route-heal", daemon=True)
    _watch.start()
    return True


if __name__ == "__main__":
    # The OTA applier runs this as the service account (scripts/
    # apply_update.py): the same sudo grant as the hub, and the Inbox file
    # stays owned by the account that writes it.
    import json
    import sys
    sys.stdout.write(json.dumps(heal(sys.argv[1] if len(sys.argv) > 1 else None)) + "\n")
