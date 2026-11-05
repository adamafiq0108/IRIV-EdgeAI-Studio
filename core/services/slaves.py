# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
slaves.py -- Which devices this master polls.

Read from config/slaves.toml (docs/conventions.md sec 6): a device becomes known
to its master by an operator recording it here after pairing (docs/setup.md
sec 5). Membership is an operator decision and stays one -- services/discovery.py
finds a known device's address on the LAN, it does not decide who belongs here.

No framework imports here -- this takes a config dict and returns dicts
(docs/conventions.md sec 2).
"""

import re

#: What a paired device may be called (its host_serial): letters, digits,
#: '.', '_' and '-', starting with a letter or digit, up to 64 characters.
#: Names starting "__" are this master's own: "__local__" is how the LOCAL
#: app's events are stamped, so a device calling itself that had its events
#: shown as the local camera's (Pipi N-2). A reserved or odd name is refused
#: at pairing, and an entry already stored with one is listed (flagged, so it
#: can be removed) but never polled.
SERIAL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def valid_serial(host_serial):
    """May a paired device be stored and polled under this name?"""
    text = str(host_serial or "")
    return bool(SERIAL_RE.fullmatch(text)) and not text.startswith("__")

#: A master with no [sync] block in config polls every 15s. Small edge fleets
#: (a handful of devices) want near-live status -- app running/stopped, camera,
#: telemetry -- on the dashboard; the /api/summary call is a few cheap queries.
#: Raise [sync] interval_s in slaves.toml for large fleets where that would flood.
DEFAULT_INTERVAL_S = 15

#: A separate, lighter cadence for the liveness tick (sync.ping_health): just
#: /api/health + /api/agent/info, no /api/summary. Decoupled from interval_s so a
#: deployment can keep the heavy telemetry sync slow (minutes) while devices
#: still flip online/offline within seconds. Clamped to >=3s to bound the load.
DEFAULT_HEALTH_INTERVAL_S = 15


def load(slaves_cfg, system_slug, config_dir=None):
    """Return [{host_serial, label, url, token, system_slug}] for one system.

    One slaves.toml serves every master in the deployment; each master keeps
    only the entries tagged with its own system_slug. A device with no token
    (paired but never recorded, or a config typo) is dropped rather than
    polled unauthenticated -- it would only ever collect 401s.

    Tokens are decrypted from their at-rest form (v2.0 #21); a legacy
    plaintext token passes through unchanged.

    `url` is optional and may be "": an address is a cache, not an identity, and
    services/discovery.py fills it in by sweeping for the host_serial. Until
    that happens the device polls as offline, which is what it is -- the master
    knows it exists and cannot reach it. A token has no such fallback, so its
    absence still drops the entry.
    """
    from core.services import secrets as secrets_service
    out = []
    for host_serial, entry in (slaves_cfg.get("slaves") or {}).items():
        entry = entry or {}
        if entry.get("system_slug") != system_slug:
            continue
        if not entry.get("token"):
            continue
        row = {
            "host_serial": host_serial.lower(),
            "label": entry.get("label", ""),
            "url": (entry.get("url") or "").rstrip("/"),
            "token": secrets_service.decrypt(entry["token"], config_dir),
            "system_slug": system_slug,
        }
        if not valid_serial(host_serial):
            # Kept so the devices list can show it for removal; sync skips it.
            row["reserved"] = True
        out.append(row)
    return out


def interval_s(slaves_cfg):
    return int((slaves_cfg.get("sync") or {}).get("interval_s") or DEFAULT_INTERVAL_S)


def health_interval_s(slaves_cfg):
    raw = (slaves_cfg.get("sync") or {}).get("health_interval_s") or DEFAULT_HEALTH_INTERVAL_S
    return max(3, int(raw))
