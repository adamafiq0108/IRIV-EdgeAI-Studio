# -*- coding: utf-8 -*-
"""
monitor_account.py -- the Monitor account: one fixed, read-mostly sign-in that
sees every system on the box.

WHY IT EXISTS. A site wants somebody (or a wall display, or a central
dashboard) to WATCH the box: look at every system's Dashboard, pull a CSV out
of Analytics, download footage -- and nothing else. Handing that person an
ordinary account meant ticking every system, every page and every capability
by hand, and getting one of them wrong gave a viewer a way to change things.
The Monitor is that grant, made once, in one place, and impossible to widen by
accident:

  * ONE account per box, flagged `monitor = true` in users.local.toml. The
    NAME is the admin's choice; the flag is the identity.
  * every system, present and future (systems = "*").
  * pages: Dashboard, Analytics and Footage unless master / the site admin
    ticks more (the grant lives under pages["*"], see auth.page_grants).
  * what it may DO is fixed here, never ticked: start and stop detection, and
    download footage (caps.MONITOR_CAPS). Everything else a page can change is
    master-only or a site-admin grant, exactly as for any ordinary account.
  * never an admin tier, never a developer: auth.tier() ignores those flags
    on a monitor row, and update_user / delete_user refuse the row, so no
    account-management route can promote or remove it. It is switched on and
    off here, from System settings.
  * it signs in with an ACCESS TOKEN only, one per central device, and has no
    password at all (auth.verify_login refuses the row; its stored hash is a
    random secret nobody knows): the token is shown once, only its hash is
    stored, and it is exchanged for an ordinary session (routes/monitor.py) or
    sent as a Bearer on every call -- so every wall, throttle and audit line
    that applies to a session applies to it.
"""

import hashlib
import hmac
import os
import re
import secrets
import socket
import time

from core import config
from core.services import auth as auth_service
from core.services import caps as caps_service
from core.services import nav as nav_service

FLAG = "monitor"
DEFAULT_USERNAME = "monitor"

#: What the account may open until somebody ticks more.
DEFAULT_PAGES = ("main", "analytics", "footage")

#: The pages the admin may add. Everything the product has except the
#: developer-only ones -- the account can still only READ them (it holds no
#: capability beyond MONITOR_CAPS).
GRANTABLE_PAGES = tuple(p for p in nav_service.ALL_PAGES
                        if p not in nav_service.DEVELOPER_PAGES)

TOKEN_PREFIX = "mon_"
TOKEN_FIELD = "tokens"


def _row(config_dir=None):
    """(name, row) of the monitor account, or (None, None)."""
    for name, row in (auth_service.users(config_dir) or {}).items():
        if isinstance(row, dict) and row.get(FLAG):
            return name, row
    return None, None


def username(config_dir=None):
    return _row(config_dir)[0]


def is_monitor(name, config_dir=None):
    """True for the monitor account while it is switched on."""
    if not name:
        return False
    row = (auth_service.users(config_dir) or {}).get(str(name).strip().lower())
    return bool(isinstance(row, dict) and row.get(FLAG)
                and row.get("enabled", True))


def has_flag(name, config_dir=None):
    """The flag AS STORED, switched on or not -- what account management
    checks before it touches a row."""
    row = (auth_service.users(config_dir) or {}).get(str(name or "").strip().lower())
    return bool(isinstance(row, dict) and row.get(FLAG))


def pages(config_dir=None):
    """The pages the account may open, in nav order."""
    _name, row = _row(config_dir)
    raw = ((row or {}).get("pages") or {}).get("*")
    held = {p.strip() for p in str(raw or "").split(",") if p.strip()}
    held = held & set(GRANTABLE_PAGES) if held else set(DEFAULT_PAGES)
    return [p for p in GRANTABLE_PAGES if p in held]


def systems(config_dir=None):
    """None when the account may open EVERY system (now and later), else the
    list of slugs the admin allowed."""
    _name, row = _row(config_dir)
    raw = str((row or {}).get("systems") or "*")
    slugs = [s.strip() for s in raw.split(",") if s.strip()]
    return None if (not slugs or "*" in slugs) else slugs


def keeps_online(slug, config_dir=None):
    """Is this system open to the Monitor while the Monitor is on? Such a
    system is kept online (routes/hub.py refuses Power off, and
    services/monitor_online.py brings its master back if it drops), so a
    central program that connects through the Monitor never finds it gone."""
    name, row = _row(config_dir)
    if not row or not row.get("enabled", True):
        return False
    held = systems(config_dir)
    return held is None or str(slug) in held


def set_systems(all_systems, slugs, known, config_dir=None):
    """Which systems the Monitor may open: all of them, or the named ones
    (each must exist on this box). Returns the new state."""
    current, row = _row(config_dir)
    if row is None:
        raise ValueError("turn the Monitor account on first")
    if all_systems:
        value = "*"
    else:
        chosen = [str(s).strip() for s in (slugs or []) if str(s).strip()]
        unknown = sorted(set(chosen) - set(known or []))
        if unknown:
            raise ValueError("no such system: " + ", ".join(unknown))
        if not chosen:
            raise ValueError("choose at least one system, or allow all of them")
        value = ",".join(dict.fromkeys(chosen))
    with auth_service._WRITE_LOCK:
        local = config.load_local("users", config_dir)
        local["users"][current]["systems"] = value
        auth_service._save_local("users", local, config_dir)
    return state(config_dir)


def state(config_dir=None):
    """What the settings page draws."""
    name, row = _row(config_dir)
    return {"configured": row is not None,
            "enabled": bool(row and row.get("enabled", True)),
            "username": name or DEFAULT_USERNAME,
            "pages": pages(config_dir),
            "systems": {"all": systems(config_dir) is None,
                        "slugs": systems(config_dir) or []},
            "grantable": [{"page": p, "label": _label(p)} for p in GRANTABLE_PAGES],
            "default_pages": list(DEFAULT_PAGES),
            "caps": [{"id": c, "label": caps_service.label(c)}
                     for c in caps_service.MONITOR_CAPS],
            "tokens": tokens(config_dir),
            "has_token": bool(row and row.get(TOKEN_FIELD)),
            "box": box_identity()}


_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def box_identity():
    """Who this box is, for a program that connects with the access token and
    wants to find the box again when its IP address changes: the hostname and
    the MAC address of every PHYSICAL network port (wired first). A MAC stays
    with the hardware across DHCP changes; the address does not. Virtual
    ports (docker, veth, bridges, loopback) are left out -- they are not the
    box."""
    macs = []
    base = "/sys/class/net"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    for name in names:
        if not os.path.exists(f"{base}/{name}/device"):
            continue
        try:
            with open(f"{base}/{name}/address", encoding="ascii") as fh:
                mac = fh.read().strip().lower()
        except OSError:
            continue
        if _MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
            macs.append((0 if name.startswith(("eth", "en")) else 1, name, mac))
    macs.sort()
    return {"hostname": socket.gethostname(),
            "mac": macs[0][2] if macs else "",
            "macs": [{"port": n, "mac": m} for _r, n, m in macs]}


def _label(page):
    for p, label, _need in tuple(nav_service.PAGES) + tuple(nav_service.ADMIN_PAGES):
        if p == page:
            return label
    return page


def _check_name(name, config_dir, keep=None):
    name = (name or "").strip().lower()
    if not name.isidentifier() or len(name) > 32:
        raise ValueError("the username must be one word: letters, numbers "
                         "and underscores, starting with a letter")
    if name == auth_service.admin_username(config_dir):
        raise ValueError("that name is the administrator account's")
    if name != keep:
        local = config.load_local("users", config_dir)
        if any(str(k).strip().lower() == name for k in (local.get("users") or {})):
            raise auth_service.UsernameTaken(auth_service.NAME_TAKEN_MSG)
        auth_service._refuse_held_name(name, config_dir)
    return name


def _check_password(password):
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    return password


def enable(name=None, password=None, config_dir=None):
    """Switch the Monitor on. It has no password sign-in: `password` is kept
    only so older callers still work and is ignored. The account is called
    `monitor` unless a name is given."""
    current, row = _row(config_dir)
    with auth_service._WRITE_LOCK:
        local = config.load_local("users", config_dir)
        users = local.setdefault("users", {})
        if row is None:
            name = _check_name(name or DEFAULT_USERNAME, config_dir)
            # A password nobody knows: the row has to carry a hash, and
            # verify_login refuses a monitor row regardless.
            salt = secrets.token_hex(8)
            users[name] = {
                "salt": salt,
                "password_hash": auth_service._hash(secrets.token_urlsafe(32), salt),
                "systems": "*", "role": "viewer", "enabled": True, FLAG: True,
                "pages": {"*": ",".join(DEFAULT_PAGES)}}
            auth_service._save_local("users", local, config_dir)
            return state(config_dir)
        stored = users[current]
        new = _check_name(name or current, config_dir, keep=current)
        if new != current:
            users[new] = users.pop(current)
            stored = users[new]
        stored["enabled"] = True
        # The grants are re-asserted on every switch-on: nothing a hand-edited
        # file left behind survives as extra reach. The systems the admin
        # chose are kept; an empty value means all of them.
        stored["systems"] = str(stored.get("systems") or "*")
        stored.pop(auth_service.ADMIN_FLAG, None)
        stored.pop(auth_service.DEVELOPER_FLAG, None)
        auth_service._save_local("users", local, config_dir)
    if new != current:
        auth_service.revoke_sessions_of(current, config_dir=config_dir)
        auth_service.delete_devices_of(current, config_dir=config_dir)
    return state(config_dir)


def disable(config_dir=None):
    """Switch the Monitor off: no sign-in, no token, live sessions end. The
    account and its page choices are kept for next time."""
    current, row = _row(config_dir)
    if row is None:
        return state(config_dir)
    with auth_service._WRITE_LOCK:
        local = config.load_local("users", config_dir)
        local["users"][current]["enabled"] = False
        # Tokens end with it: turning it on again must not revive a device
        # nobody remembers. Devices get new tokens.
        local["users"][current].pop(TOKEN_FIELD, None)
        auth_service._save_local("users", local, config_dir)
    auth_service.revoke_sessions_of(current, config_dir=config_dir)
    return state(config_dir)


def set_pages(chosen, config_dir=None):
    """Which pages the account may open. Never empty, never a page the
    product does not have, never a developer page."""
    current, row = _row(config_dir)
    if row is None:
        raise ValueError("turn the Monitor account on first")
    keys = [str(k).strip() for k in (chosen or []) if str(k).strip()]
    bad = sorted(set(keys) - set(GRANTABLE_PAGES))
    if bad:
        raise ValueError("unknown page: " + ", ".join(bad))
    if not keys:
        raise ValueError("leave at least one page for the Monitor account")
    with auth_service._WRITE_LOCK:
        local = config.load_local("users", config_dir)
        local["users"][current]["pages"] = {
            "*": ",".join(p for p in GRANTABLE_PAGES if p in set(keys))}
        auth_service._save_local("users", local, config_dir)
    return state(config_dir)


def _digest(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


#: How many devices may hold a token at once. One per central device.
MAX_TOKENS = 20

#: last_used is written at most this often (a token sign-in is not a reason to
#: write the users file every time).
TOUCH_S = 300


def tokens(config_dir=None):
    """The access tokens, newest first, WITHOUT their secrets: id, the name
    the admin gave the device, when it was made, last used and from where."""
    _name, row = _row(config_dir)
    out = []
    for tid, t in ((row or {}).get(TOKEN_FIELD) or {}).items():
        if not isinstance(t, dict):
            continue
        out.append({"id": tid, "name": str(t.get("name") or tid),
                    "created": int(t.get("created") or 0),
                    "last_used": int(t.get("last_used") or 0),
                    "last_ip": str(t.get("last_ip") or "")})
    return sorted(out, key=lambda t: -t["created"])


def make_token(name, config_dir=None):
    """A new access token for one device. Returns {"id", "token"}: the token
    is shown ONCE and only its hash is kept, so a lost token is revoked and
    replaced, never recovered."""
    current, row = _row(config_dir)
    if row is None or not row.get("enabled", True):
        raise ValueError("turn the Monitor account on first")
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("name the device this token is for")
    with auth_service._WRITE_LOCK:
        local = config.load_local("users", config_dir)
        held = local["users"][current].setdefault(TOKEN_FIELD, {})
        if len(held) >= MAX_TOKENS:
            raise ValueError(f"at most {MAX_TOKENS} devices; remove one first")
        if any(str(t.get("name") or "").lower() == name.lower() for t in held.values()):
            raise ValueError("a device with that name already has a token")
        token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        tid = secrets.token_hex(4)
        held[tid] = {"name": name, "hash": _digest(token),
                     "created": int(time.time())}
        auth_service._save_local("users", local, config_dir)
    return {"id": tid, "token": token}


def revoke_token(token_id, config_dir=None):
    """Remove one device's token: it cannot sign in again, and the sessions it
    opened end now. The others are untouched."""
    current, row = _row(config_dir)
    tid = str(token_id or "")
    if row is None or tid not in ((row.get(TOKEN_FIELD)) or {}):
        raise KeyError(tid)
    with auth_service._WRITE_LOCK:
        local = config.load_local("users", config_dir)
        local["users"][current][TOKEN_FIELD].pop(tid, None)
        if not local["users"][current][TOKEN_FIELD]:
            local["users"][current].pop(TOKEN_FIELD, None)
        auth_service._save_local("users", local, config_dir)
    revoke_token_sessions(tid, config_dir)
    return state(config_dir)


def session_device(token_id):
    return f"token:{token_id}"


#: Sessions one token may hold at once. A token that signs in in a loop ends
#: its OWN oldest sessions first, never another account's.
MAX_TOKEN_SESSIONS = 5


def trim_token_sessions(token_id, config_dir=None):
    """Revoke a token's oldest sessions beyond MAX_TOKEN_SESSIONS."""
    tag = session_device(token_id)
    with auth_service._sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        mine = sorted((r for r in (local.get("sessions") or {}).values()
                       if str(r.get("device") or "") == tag and not r.get("revoked")),
                      key=lambda r: int(r.get("login") or 0))
        extra = mine[:max(0, len(mine) - MAX_TOKEN_SESSIONS)]
        for row in extra:
            row["revoked"] = True
        if extra:
            auth_service._save_local("sessions", local, config_dir)
    return len(extra)


#: API paths a Monitor session may NOT read, whatever pages it was granted:
#: configuration and data of areas it has no page for. Page grants decide the
#: menu; this closes the biggest data routes behind them (Pipi, 2026-10-20).
#: The wall is a list of what is known to be sensitive, not a list of what is
#: allowed, so a page that works today keeps working.
BLOCKED_EXACT = ("/api/workflow", "/api/builder/export", "/api/email/config")
BLOCKED_PREFIXES = ("/api/hub/board", "/api/devices", "/api/detector/rtsp_scan",
                    "/api/detector/dataset", "/api/models", "/api/media",
                    "/api/detector/control/modbus", "/api/board")


def blocked(path):
    path = str(path or "").rstrip("/") or "/"
    return path in BLOCKED_EXACT or any(
        path == p or path.startswith(p + "/") for p in BLOCKED_PREFIXES)


def revoke_token_sessions(token_id, config_dir=None):
    """End the sessions a token opened (they carry its id as their device)."""
    tag = session_device(token_id)
    n = 0
    with auth_service._sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        for row in (local.get("sessions") or {}).values():
            if str(row.get("device") or "") == tag and not row.get("revoked"):
                row["revoked"] = True
                n += 1
        if n:
            auth_service._save_local("sessions", local, config_dir)
    return n


def verify_token(token, config_dir=None, ip=""):
    """(monitor username, token id) when `token` is one of the live tokens,
    else None. Constant-time over every stored hash; a switched-off account
    never verifies."""
    token = str(token or "")
    if not token.startswith(TOKEN_PREFIX):
        return None
    current, row = _row(config_dir)
    if row is None or not row.get("enabled", True):
        return None
    digest = _digest(token)
    found = None
    for tid, t in ((row.get(TOKEN_FIELD)) or {}).items():
        if isinstance(t, dict) and hmac.compare_digest(str(t.get("hash") or ""), digest):
            found = tid
    if found is None:
        return None
    try:
        t = row[TOKEN_FIELD][found]
        if time.time() - int(t.get("last_used") or 0) > TOUCH_S:
            with auth_service._WRITE_LOCK:
                local = config.load_local("users", config_dir)
                held = local["users"][current][TOKEN_FIELD][found]
                held["last_used"] = int(time.time())
                held["last_ip"] = str(ip or "")[:45]
                auth_service._save_local("users", local, config_dir)
    except (KeyError, TypeError):
        pass
    return current, found
