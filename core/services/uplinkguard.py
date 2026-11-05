# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
uplinkguard.py -- a fixed address saved as the internet uplink proves it, or
goes back to what it was.

Tok Aba (2026-10-07, Pipi M2): the Network page now starts a new fixed
address as the uplink (netconf._uplink_metric puts it ahead of every other
port). A wrong gateway there takes the box's internet away -- update checks,
downloads, email alerts -- and the page that set it may be cut off by the
very change. So after a LIVE static uplink is applied:

  check     for up to CHECK_S the box tries to reach the internet THROUGH
            that port: a TCP connect from the port's own address to a few
            well-known addresses (no DNS: a resolver is a second thing that
            can be wrong), and when the kernel's route for them is not that
            port, ping bound to it (-I, as routeheal does). Reached: done,
            nothing asked.
  pending   not reached: the page asks "Keep this setting?" and the box
            waits KEEP_S. Keep ends it; Revert, or NO ANSWER, puts the
            connection's previous ipv4 settings back (netconf.ipv4_snapshot,
            read before the write) and brings it up again.

THE TIMER IS HERE, on the hub, not in the browser: a page riding on the port
being changed loses the box exactly when the question matters, and silence
must mean "put it back". Same verbs as set_ipv4 (`connection modify` and
`connection up` through sudo), so no new sudoers rule.

STANDBY (2026-10-09, the uplink decided by the box: netuplink.auto_role): a
fixed address with a gateway set while ANOTHER port reaches the internet is
checked the same way, but never asked about and never put back -- nothing
about the box's internet changed. Reached: it stays as the fallback. Not
reached: it is made machine-only (`local`: ipv4.never-default yes, metric
back to -1), the address and gateway kept, so a gateway that routes nowhere
is not left in the table for routeheal to move the internet onto.

Not covered: a preset (no cable, nothing live to test; it applies at the
next carrier), and a hub restart inside the window (the timer dies with the
process and the new setting stays; the page's Revert is gone with it, the
Network page can still set the port back by hand).
"""

import socket
import threading
import time

from core.services import netconf, routeheal
from core.services.netaddr import NetError

#: How long the box keeps trying before it asks. Bringing the profile up,
#: ARP for the gateway and a first SYN are seconds on a working network.
CHECK_S = 12.0
#: How long the question stands before the old settings come back.
KEEP_S = 60.0
POLL_S = 2.0
CONNECT_S = 3.0
#: Well-known addresses, by number: Cloudflare, Google and Quad9 DNS, which
#: all answer TCP 443 as well as 53.
PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))
#: An outcome stays in the status for the page this long, then goes.
KEEP_OUTCOME_S = 600

_lock = threading.Lock()
_jobs = {}          # device -> {state, ..., "_cancel": Event} (one per port)


def _route_dev(host):
    """The interface the kernel would send to `host` on, "" if unknown."""
    rc, out = routeheal.run(["ip", "-4", "route", "get", host])
    if rc != 0:
        return ""
    parts = out.split()
    return parts[parts.index("dev") + 1] if "dev" in parts[:-1] else ""


def reachable(device, source_ip):
    """Does the internet answer through `device`? TCP from its own address
    when the kernel routes that way; ping bound to the port otherwise (an
    earlier uplink, or routeheal, may own the default route)."""
    for host, port in PROBES:
        if _route_dev(host) == device:
            try:
                with socket.create_connection((host, port), timeout=CONNECT_S,
                                              source_address=(source_ip, 0)):
                    return True
            except OSError:
                pass
        rc, _ = routeheal.run(["ping", "-n", "-c", "1", "-W", "2", "-I", device, host],
                              timeout=5)
        if rc == 0:
            return True
    return False


def state():
    """{device: {state, message, remaining_s, ...}} for the Network page:
    a check under way, a question waiting, or an outcome of the last 10 min."""
    now = time.time()
    out = {}
    with _lock:
        for dev, job in list(_jobs.items()):
            if job.get("finished") and now - job["finished"] > KEEP_OUTCOME_S:
                del _jobs[dev]
                continue
            view = {k: v for k, v in job.items() if not k.startswith("_")}
            if job["state"] == "pending":
                view["remaining_s"] = max(0, int(round(job["deadline"] - now)))
            out[dev] = view
    return out


def _set(device, **fields):
    with _lock:
        job = _jobs.get(device)
        if job is not None:
            job.update(fields)


def _revert(device, job, why):
    """Put the connection's previous ipv4 settings back and bring it up."""
    name, previous = job["_connection"], job["_previous"]
    try:
        netconf._nmcli(["connection", "modify", name] + previous, sudo=True, timeout=30)
        try:
            netconf._nmcli(["connection", "up", name], sudo=True, timeout=90)
        except NetError:
            pass             # written; NetworkManager applies it at the next carrier
        if (dict(zip(previous[::2], previous[1::2])).get("ipv4.method") or "auto") == "auto":
            try:
                netconf.clear_ipv4_reboot(device, config_dir=job.get("_config_dir"))
            except Exception:                                     # noqa: BLE001
                pass
        _set(device, state="reverted", finished=time.time(),
             message=f"{why} {device} is back on its previous settings.")
    except NetError as exc:
        _set(device, state="revert_failed", finished=time.time(),
             message=f"{why} Putting {device} back failed too ({exc}); set it on "
                     "the Network page, or over the front panel's Reset.")


def _make_local(device, job):
    """A standby port the internet does not answer through: no default route
    on it any more. Its address, gateway and DNS stay as typed."""
    name = job["_connection"]
    try:
        netconf._nmcli(["connection", "modify", name] + netconf._route_pairs("local"),
                       sudo=True, timeout=30)
        try:
            netconf._nmcli(["connection", "up", name], sudo=True, timeout=90)
        except NetError:
            pass             # written; NetworkManager applies it at the next carrier
        _set(device, state="local", finished=time.time(),
             message=f"The internet does not answer through {device}, so it is a "
                     "machine-only network: the box keeps its internet on its other port.")
    except NetError as exc:
        _set(device, state="local_failed", finished=time.time(),
             message=f"{device} does not reach the internet, and taking its default "
                     f"route away failed ({exc}). It stays behind the working port.")


def _run(device, job):
    cancel = job["_cancel"]
    deadline = time.monotonic() + CHECK_S
    standby = job.get("role") == "standby"
    while not cancel.is_set():
        if reachable(device, job["ip"]):
            _set(device, state="ok", finished=time.time(),
                 message=(f"The internet answers through {device} too. It stays the "
                          "fallback behind the port in use now." if standby
                          else f"The internet answers through {device}."))
            return
        if time.monotonic() >= deadline:
            break
        cancel.wait(POLL_S)
    if cancel.is_set():
        return
    if standby:
        with _lock:
            if _jobs.get(device) is not job:
                return
        _make_local(device, job)
        return
    _set(device, state="pending", deadline=time.time() + KEEP_S,
         message=f"Internet not reachable through {device} (gateway "
                 f"{job['gateway']}). Keep this setting?")
    if cancel.wait(KEEP_S):
        return                    # kept, reverted by hand, or replaced
    with _lock:
        if _jobs.get(device) is not job or job["state"] != "pending":
            return
        job["state"] = "reverting"
    _revert(device, job, "No answer within %d s." % int(KEEP_S))


def arm(device, connection, previous, ip, gateway, config_dir=None, role="uplink"):
    """Start the check for a live fixed address just applied on `device`, as
    the `role` netuplink.auto_role gave it ("uplink" or "standby").
    `previous` is netconf.ipv4_snapshot's settings list for `connection`,
    read BEFORE the write. A check already running for the port is replaced."""
    disarm(device)
    job = {"state": "checking", "device": device, "ip": ip, "gateway": gateway, "role": role,
           "started": time.time(), "message": f"Checking the internet through {device}...",
           "_connection": connection, "_previous": list(previous),
           "_config_dir": config_dir, "_cancel": threading.Event()}
    with _lock:
        _jobs[device] = job
    threading.Thread(target=_run, args=(device, job), name=f"uplink-check-{device}",
                     daemon=True).start()
    return {k: v for k, v in job.items() if not k.startswith("_")}


def disarm(device):
    """Drop any check or question for `device`: another write to the port
    supersedes it (it is not reverted)."""
    with _lock:
        job = _jobs.pop(device, None)
    if job is not None:
        job["_cancel"].set()


def keep(device):
    """The operator keeps the setting. NetError when nothing is waiting."""
    with _lock:
        job = _jobs.get(device)
        if job is None or job["state"] != "pending":
            raise NetError(f"nothing is waiting for an answer on {device}")
        job.update(state="kept", finished=time.time(),
                   message=f"Kept. {device} stays the uplink without a working internet.")
    job["_cancel"].set()


def revert(device):
    """The operator says put it back, now."""
    with _lock:
        job = _jobs.get(device)
        if job is None or job["state"] not in ("pending", "checking"):
            raise NetError(f"nothing is waiting for an answer on {device}")
        job["state"] = "reverting"
    job["_cancel"].set()
    _revert(device, job, "Reverted.")
