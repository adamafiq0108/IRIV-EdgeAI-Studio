# -*- coding: utf-8 -*-
"""
services/inbox.py -- messages for a signed-in account, kept on the box.

The bell's Inbox tab said "No messages" and nothing could ever put one there.
The first thing that needed to: a password-reset request waiting for master
or the developer to approve, and, once the password is changed, a note to
the account itself ("your password was changed -- if that was not you, tell
your administrator") and to master and the developer ("X changed their
password"). Both are the kind of thing that must be seen by the person it
concerns, not only logged.

A notice has an audience: one username, or the ADMINS audience (master and the
developer, whoever holds those tiers when the notice is READ -- not a list of
names frozen when it was written, so a renamed master still gets it). Read
state is per account. The store is config/inbox.local.toml, bounded: notices
older than TTL go on write, and the file is trimmed to the newest MAX rows, so
a chatty deployment cannot grow it for ever.

The pending reset REQUESTS are not stored here -- services/reset_requests.py
holds them, because a request is a thing that is answered and then gone, not
a message. The inbox route merges them in for the admins, with the two
buttons; this module is for what stays until read.
"""

import secrets
import threading
import time

from core import config

FILE = "inbox"
ADMINS = "@admins"
TTL_S = 30 * 24 * 3600
MAX_ROWS = 400

_LOCK = threading.Lock()


def _load(config_dir):
    return dict(config.load_local(FILE, config_dir) or {})


def _rows(local):
    rows = local.get("notices")
    return [dict(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save(local, rows, config_dir, now):
    keep = [r for r in rows if float(r.get("at") or 0) + TTL_S > now]
    keep.sort(key=lambda r: r.get("at", 0))
    local["notices"] = keep[-MAX_ROWS:]
    config.save_local(FILE, local, config_dir)


#: Where a notice may send a reader. A notice says something is wrong and
#: then leaves them to find the page that fixes it, which on a box with nine
#: settings tabs is most of the work -- so it can name one. Both halves are
#: CHECKED against these rather than passed through: a notice is rendered as
#: something a person clicks, and the producers are spread across routes and
#: services, so "nobody would post a bad one" is not a check. An unknown name
#: is dropped and the notice still arrives, without a link.
PAGES = ("systems", "control", "health", "wall", "vision", "settings")
TABS = ("accounts", "system", "retention", "board", "mail", "telegram",
        "network", "mqtt", "panel")


def _where(page, tab):
    """The page a notice points at, or nothing. Never a half answer: a tab
    without its page would be a link the shell could not follow."""
    page = str(page or "").strip()
    tab = str(tab or "").strip()
    if page not in PAGES:
        return "", ""
    if tab and (page != "settings" or tab not in TABS):
        return page, ""
    return page, tab


#: How long a notice's body may run. A notice is a line or two.
BODY_MAX = 600
BODY_MAX_LONG = 4000


def post(to, kind, title, body="", config_dir=None, now=None,
         page="", tab="", limit=BODY_MAX, **extra):
    """Leave a notice for `to` -- a username, or ADMINS. Returns the row.

    `page` and `tab` name where the reader should go to act on it, checked
    against PAGES and TABS above. `limit` caps the body, never above
    BODY_MAX_LONG.
    """
    now = time.time() if now is None else now
    page, tab = _where(page, tab)
    try:
        cap = max(1, min(int(limit), BODY_MAX_LONG))
    except (TypeError, ValueError):
        cap = BODY_MAX
    row = {"id": secrets.token_hex(8), "to": str(to), "kind": str(kind),
           "title": str(title)[:120], "body": str(body)[:cap],
           "page": page, "tab": tab,
           "at": int(now), "read": []}
    row.update({k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool))})
    with _LOCK:
        local = _load(config_dir)
        rows = _rows(local)
        rows.append(row)
        _save(local, rows, config_dir, now)
    return row


def notify_admins(kind, title, body="", config_dir=None, page="", tab="",
                  **extra):
    return post(ADMINS, kind, title, body, config_dir, page=page, tab=tab,
                **extra)


def for_user(username, is_admin=False, config_dir=None, now=None):
    """This account's notices, newest first, each with `read` as a bool for
    THIS reader. Admins see the ADMINS audience too."""
    now = time.time() if now is None else now
    out = []
    for r in _rows(_load(config_dir)):
        if float(r.get("at") or 0) + TTL_S <= now:
            continue
        if r.get("to") == username or (is_admin and r.get("to") == ADMINS):
            row = dict(r)
            row["read"] = username in (r.get("read") or [])
            # Notices posted before this existed carry no page. Filled in
            # here rather than migrating the file: they expire on their own,
            # and a reader that copes is cheaper than a rewrite that cannot
            # be undone.
            row["page"], row["tab"] = _where(r.get("page"), r.get("tab"))
            out.append(row)
    out.sort(key=lambda r: -r.get("at", 0))
    return out


def unread_count(username, is_admin=False, config_dir=None):
    return sum(1 for r in for_user(username, is_admin, config_dir) if not r["read"])


def mark_read(username, ids, config_dir=None):
    """Mark notices read for one reader. `ids` None = all of theirs. Read
    state is per reader, so master reading an admins notice does not read
    it for the developer."""
    with _LOCK:
        local = _load(config_dir)
        rows = _rows(local)
        for r in rows:
            if ids is not None and r.get("id") not in ids:
                continue
            seen = list(r.get("read") or [])
            if username not in seen:
                seen.append(username)
                r["read"] = seen
        _save(local, rows, config_dir, time.time())


def delete(username, notice_id, is_admin=False, config_dir=None):
    """Remove one notice the reader may see. An ADMINS notice deleted by one
    admin is gone for both -- a shared inbox is one inbox."""
    with _LOCK:
        local = _load(config_dir)
        rows = _rows(local)
        keep = [r for r in rows
                if not (r.get("id") == notice_id
                        and (r.get("to") == username
                             or (is_admin and r.get("to") == ADMINS)))]
        went = len(keep) != len(rows)
        if went:
            _save(local, keep, config_dir, time.time())
    return went
