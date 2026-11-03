# -*- coding: utf-8 -*-
"""
services/reset_requests.py -- "I forgot my password", held until someone says yes.

The forgot page used to reset the MASTER's password, and only that: no
address was typed, the link went to the one address the box knew. Accounts
have their own addresses now, and a reset link is a takeover of the account
it lands on -- so for everyone below master it is not sent on the strength of
knowing an email address alone. The request is recorded here, master and the
developer see it in the bell's Inbox (and get a mail saying it is waiting),
and Approve is what sends the link. Reject drops it. A request nobody answers
expires on its own.

Master and the developer approve their own: there is nobody above them to ask,
and a box whose owner cannot get back in is worse than the risk.

Stored in config/reset_requests.local.toml -- small, one row per open request,
and the same 0600 local-config file every other secret-adjacent state uses.
No token is stored: the token is minted at approval and lives only in the
email. The row carries WHO asked, from WHERE, and WHEN, which is what the
approver has to judge.
"""

import secrets
import threading
import time

from core import config

FILE = "reset_requests"

#: How long an unanswered request stays in the Inbox. Long enough for an
#: owner who checks once a day; short enough that a stale request from a
#: stranger does not sit as a live approve button for a week.
REQUEST_TTL_S = 24 * 3600

#: Open requests per account at once. A second forgot from the same person
#: before the first is answered REPLACES it (same account, newer timestamp)
#: rather than stacking: the approver should see one row per account, and a
#: flood from a stranger should not become a wall of rows.
_LOCK = threading.Lock()


def _load(config_dir):
    return dict(config.load_local(FILE, config_dir) or {})


def _rows(local):
    rows = local.get("requests")
    return [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save(local, rows, config_dir):
    local["requests"] = rows
    config.save_local(FILE, local, config_dir)


def _live(rows, now):
    return [r for r in rows if float(r.get("at") or 0) + REQUEST_TTL_S > now]


def create(username, email, ip="", config_dir=None, now=None):
    """Record that `username` asked for a reset from `ip`. Returns the row.
    An open request for the same account is replaced, not duplicated."""
    now = time.time() if now is None else now
    row = {"id": secrets.token_hex(8), "user": str(username), "email": str(email),
           "ip": str(ip or ""), "at": int(now)}
    with _LOCK:
        local = _load(config_dir)
        rows = [r for r in _live(_rows(local), now) if r.get("user") != username]
        rows.append(row)
        _save(local, rows, config_dir)
    return row


def pending(config_dir=None, now=None):
    """Open requests, oldest first, expired ones dropped on read."""
    now = time.time() if now is None else now
    return sorted(_live(_rows(_load(config_dir)), now), key=lambda r: r.get("at", 0))


def take(request_id, config_dir=None, now=None):
    """Remove one open request and return it, or None. Approve and reject
    both come through here: the row goes the moment somebody answers, so two
    approvers clicking together send one link, not two."""
    now = time.time() if now is None else now
    with _LOCK:
        local = _load(config_dir)
        rows = _live(_rows(local), now)
        hit = next((r for r in rows if r.get("id") == request_id), None)
        if hit is None:
            return None
        _save(local, [r for r in rows if r is not hit], config_dir)
    return hit


def count(config_dir=None):
    return len(pending(config_dir))
