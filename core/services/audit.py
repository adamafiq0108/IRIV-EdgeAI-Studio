# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
audit.py -- centralized audit-log service (formatting + a thin record wrapper).

No framework imports (conventions sec 2). Routes call record() with the
already-extracted actor/ip; the SQL lives in core/repos/audit.py. record() is
best-effort: a Postgres outage returns False and never blocks the action being
audited (same posture as the device-side auth_events logging).
"""

from core.repos import audit as audit_repo

#: The system this master process serves, set once at boot (set_system). Every
#: audit write is stamped with it and every read filters to it, so one system's
#: Audit page never shows another's -- without threading the slug through the
#: dozen routes that call record(). A master serves exactly one system, so a
#: process-level context is the right scope; the hub leaves it None (unfiltered).
_SYSTEM = None


def set_system(slug):
    """Bind this process's audit scope. Called at master boot."""
    global _SYSTEM
    _SYSTEM = slug or None

#: Canonical action names (spec #18). Kept as constants so callers don't drift
#: on spelling and the viewer can offer a stable filter list.
ACTIONS = (
    "login", "logout", "device.register", "device.rename", "device.assign",
    "folder.create", "folder.rename", "folder.delete", "device.move",
    "config.sync", "device.restart", "device.shutdown", "app.restart",
    "credentials.rotate", "token.view", "credentials.revoke",
    "device.pair", "device.forget",
    # remote access (v2.1): a tunnel publishes this master to the internet, so
    # starting one must be attributable to a person, not just visible in a log.
    "remote.settings", "remote.tunnel_start", "remote.tunnel_stop",
    # dashboard builder: a published layout renders for every operator after.
    "builder.save", "builder.publish",
    # the box's MQTT broker: a save can open a listener to the plant LAN.
    "mqtt.broker_settings", "mqtt.broker_install",
    # Logs page Clear Log: the log rows go, so who cleared them, which
    # systems and how many must survive somewhere the clear cannot reach.
    "logs.clear",
    # Events Log's Clear events: the same reasoning, for detection events.
    "events.clear",
    # registered sign-in devices (core/services/auth.py): which browsers may
    # sign in to an account is an access decision, so each change is a row.
    "device.registered", "device.removed", "device.remove_others",
    "device.expired", "device.refused",
)


def record(action, username=None, host_serial=None, result="ok",
           source_ip=None, detail=None, config_dir=None):
    """Append one audit entry, stamped with this process's system. True if written."""
    return audit_repo.record(action, username=username, host_serial=host_serial,
                             result=result, source_ip=source_ip, detail=detail,
                             system=_SYSTEM, config_dir=config_dir)


def recent(limit=200, action=None, username=None, config_dir=None, offset=0):
    """Recent entries shaped for display, scoped to this master's system."""
    return page(limit=limit, action=action, username=username,
                config_dir=config_dir, offset=offset)[0]


def page(limit=200, action=None, username=None, config_dir=None, offset=0,
         exclude_usernames=None):
    """One page of entries, and whether another follows.

    The repo fetches one row more than asked for; that extra row is how
    "there is a next page" is known without a COUNT(*) over an append-only log
    on every page turn. It is never returned."""
    rows = audit_repo.query(limit=limit, action=action or None,
                            username=username or None, system=_SYSTEM,
                            config_dir=config_dir, offset=offset,
                            exclude_usernames=exclude_usernames)
    has_more = len(rows) > limit
    rows = rows[:limit]
    out = []
    for r in rows:
        ts = r.get("ts")
        out.append({
            "id": r.get("id"),
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts or ""),
            "username": r.get("username") or "",
            "host_serial": r.get("host_serial") or "",
            "action": r.get("action") or "",
            "result": r.get("result") or "",
            "source_ip": r.get("source_ip") or "",
            "detail": r.get("detail") or {},
        })
    return out, has_more
