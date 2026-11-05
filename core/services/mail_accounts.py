# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mail_accounts.py -- the box's sending mailboxes, defined once.

WHY BOX-WIDE. The sender used to be a field on each system's own email config
(email_<slug>.local.toml), so a deployment whose four systems all mail from
one alerts@ mailbox held that account -- and its app password -- in four
places. Rotating the password meant four edits, and forgetting one produced a
system whose mail silently stopped authenticating. An account is a property of
the BOX's relationship with a mail provider, not of a detection system, so it
is declared once here and referenced by id everywhere else.

Both the hub and every master read this file: they share config/, and the
detection app needs the credentials embedded in its output document (it runs
standalone and imports nothing from core/). So this module is the one place
that knows how an account is stored, and callers ask it for one by id.

Stored as a TOML array-of-tables in config/mail.local.toml -- the local writer
handles [[sections]] (see core/config._is_table_array), so accounts are real
entries rather than an encoded blob somebody has to unpick by hand on a box.

    [[accounts]]
    id = "acc_a1b2c3"
    name = "Line alerts"
    sender = "alerts@corp.com"
    app_password = "...."
    smtp_host = "smtp.gmail.com"
    smtp_port = 587

No framework imports here (docs/conventions.md sec 2).
"""

import re
import secrets

from core import config

#: The one file. Not per-system on purpose -- see the module docstring.
NAME = "mail"

#: Gmail is what nearly every small deployment uses, and it is the provider
#: whose app-password rule surprises people, so it is the default rather than
#: an empty box somebody has to research.
DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

#: What a stored account carries. Anything else in a payload is dropped.
FIELDS = ("id", "name", "sender", "app_password", "smtp_host", "smtp_port")

_ADDR = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _new_id():
    """Short, stable, and not derived from the address: renaming a mailbox or
    moving it to another provider must not orphan every node pointing at it."""
    return "acc_" + secrets.token_hex(4)


def load(config_dir=None):
    """Every account, with real passwords. Server-side callers only."""
    raw = config.load_local(NAME, config_dir) or {}
    out = []
    for item in raw.get("accounts") or []:
        if not isinstance(item, dict):
            continue
        acc = _clean(item, allow_blank_password=True)
        if acc.get("id") and acc.get("sender"):
            out.append(acc)
    return out


def default_id(config_dir=None):
    """Which account is used when nothing names one. The stored choice when it
    still exists, else the first -- a deleted default must not silently stop
    every node that relied on it."""
    raw = config.load_local(NAME, config_dir) or {}
    accounts = load(config_dir)
    ids = {a["id"] for a in accounts}
    want = str(raw.get("default_id") or "").strip()
    if want in ids:
        return want
    return accounts[0]["id"] if accounts else ""


def get(account_id, config_dir=None):
    """One account by id, or None. An id that no longer exists returns None
    rather than the default: a node pointing at a deleted mailbox must be
    reported, not silently redirected to somebody else's outbox."""
    want = str(account_id or "").strip()
    if not want:
        return None
    for acc in load(config_dir):
        if acc["id"] == want:
            return acc
    return None


def resolve(account_id, config_dir=None):
    """The account to send through: the named one, else the default.

    Returns (account_or_None, problem). `problem` is non-empty when the caller
    asked for an id that is gone -- worth saying out loud, because the sender
    changing underneath a rule is exactly the surprise this avoids.
    """
    want = str(account_id or "").strip()
    if want:
        acc = get(want, config_dir)
        if acc:
            return acc, ""
        return None, (f"the mailbox this was set to send from ({want}) no "
                      "longer exists - pick one on the Settings > Email page")
    acc = get(default_id(config_dir), config_dir)
    if acc:
        return acc, ""
    return None, ("no sending mailbox is set up - add one on the "
                  "Settings > Email page")


def public(config_dir=None):
    """What the browser may see: every account, passwords masked.

    A password never returns unmasked -- not to render a form, not to check
    it. The page shows whether one is SET; changing it means typing a new one.
    """
    out = []
    for acc in load(config_dir):
        shown = dict(acc)
        shown["app_password"] = config.mask(acc.get("app_password"))
        shown["has_password"] = bool(acc.get("app_password"))
        out.append(shown)
    return {"accounts": out, "default_id": default_id(config_dir)}


#: App passwords only (Tok Aba, 2026-10-14). For Gmail the format is known: 16
#: lowercase letters, which Google shows in four groups of four. A blank
#: server means Gmail (DEFAULT_HOST). Other providers issue other formats, so
#: for them any non-empty value is taken. Mirrors frontend lib/appPassword.js.
APP_PASSWORD_MESSAGE = ("That does not look like an app password. Gmail app passwords "
                        "have 16 letters (for example abcd efgh ijkl mnop). Create one "
                        "in your Google Account security settings.")
_GMAIL_HOST = re.compile(r"(^|\.)(gmail|googlemail)\.com$", re.I)
_GMAIL_APP_PASSWORD = re.compile(r"^[a-z]{4}( ?[a-z]{4}){3}$")


def is_gmail_host(host):
    h = str(host or "").strip()
    return not h or bool(_GMAIL_HOST.search(h))


def clean_app_password(password, host):
    """The password to store, or ValueError(APP_PASSWORD_MESSAGE).

    Gmail: must be an app password, stored without its spaces. Any other
    server: unchanged (that provider's own format). The message never quotes
    what was typed - it is a credential."""
    pw = "" if password is None else str(password)
    if not pw or not is_gmail_host(host):
        return pw
    if not _GMAIL_APP_PASSWORD.match(pw.strip()):
        raise ValueError(APP_PASSWORD_MESSAGE)
    return pw.replace(" ", "").strip()


def _clean(data, allow_blank_password=False):
    """One account, validated. Raises ValueError with a sentence a person can
    act on -- these are typed by hand and every field has a common mistake."""
    acc = {}
    acc["id"] = str(data.get("id") or "").strip() or _new_id()
    sender = str(data.get("sender") or "").strip()
    if not sender:
        raise ValueError("the sending address is required")
    if not _ADDR.match(sender):
        raise ValueError(f"{sender!r} is not an email address")
    acc["sender"] = sender[:200]
    # Named so a picker can offer "Line alerts" rather than eight addresses
    # that differ by one character. Falls back to the address itself.
    acc["name"] = (str(data.get("name") or "").strip() or sender)[:60]
    # NOT stripped and NOT truncated here: an app password is whatever the
    # provider issued. Only a Gmail one, whose format is known, loses the
    # spaces Google shows it with (clean_app_password, at save_account).
    password = data.get("app_password")
    acc["app_password"] = "" if password is None else str(password)
    if not acc["app_password"] and not allow_blank_password:
        raise ValueError("the app password is required")
    acc["smtp_host"] = (str(data.get("smtp_host") or "").strip()
                        or DEFAULT_HOST)[:120]
    try:
        port = int(data.get("smtp_port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        raise ValueError("the SMTP port must be a number")
    if not 1 <= port <= 65535:
        raise ValueError("the SMTP port must be between 1 and 65535")
    acc["smtp_port"] = port
    return acc


def save_account(data, config_dir=None):
    """Add or update one account. Returns its id.

    An update with a MASKED or empty password keeps the stored one, which is
    what lets the page render the form, let somebody fix a typo in the name,
    and save it back without ever holding the credential.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid account")
    accounts = load(config_dir)
    want = str(data.get("id") or "").strip()
    existing = next((a for a in accounts if a["id"] == want), None)
    incoming = dict(data)
    password = incoming.get("app_password")
    masked = (password is not None and str(password)
              and set(str(password)) == {"*"})
    # Only a password being SET is judged: one kept from the store (blank or
    # masked on an edit) is never re-checked, so an account saved before the
    # app-password rule keeps working until somebody changes its password.
    keeping = bool(existing and (masked or not password))
    if keeping:
        incoming["app_password"] = existing["app_password"]
    elif masked:
        # A mask with nothing behind it is somebody saving a form they never
        # filled in -- refuse rather than store literal asterisks as a
        # password and fail at 3am with "authentication failed".
        incoming["app_password"] = ""
    acc = _clean(incoming, allow_blank_password=False)
    if not keeping:
        acc["app_password"] = clean_app_password(acc["app_password"], acc["smtp_host"])
    if existing:
        accounts = [acc if a["id"] == existing["id"] else a for a in accounts]
    else:
        accounts.append(acc)
    _write(accounts, config_dir, keep_default=True)
    return acc["id"]


def delete_account(account_id, config_dir=None, systems_dir=None):
    """Remove one, and let go of everything that pointed at it.

    Deleting used to leave the pointers. That was deliberate and it was the
    wrong half of the right idea: a node must never quietly send from
    SOMEBODY ELSE'S mailbox, which is why resolve() refuses to fall back --
    but leaving a dead id behind meant a repeating export kept firing at a
    mailbox that no longer existed, and every page went on showing its name
    as though it were a choice.

    So the pointer is cleared rather than repointed. Nothing is silently
    re-addressed; what used to send now plainly has no mailbox, which is the
    truth and is visible in the one place an operator looks.

    Returns what was let go, so the page can say so rather than leave
    somebody to discover it when an export does not arrive.
    """
    want = str(account_id or "").strip()
    accounts = [a for a in load(config_dir) if a["id"] != want]
    _write(accounts, config_dir, keep_default=True)
    return detach(want, config_dir, systems_dir) if want else {
        "schedules": [], "nodes": []}


def detach(account_id, config_dir=None, systems_dir=None):
    """Clear a mailbox id wherever it is stored. Never raises.

    A cascade that can abort half way is worse than none: the mailbox is
    already gone by the time this runs, so a failure here must leave the
    remaining pointers to be reported by resolve() rather than take the
    delete down with it. Each system is handled on its own for the same
    reason -- one unreadable workflow file must not strand the others.
    """
    want = str(account_id or "").strip()
    out = {"schedules": [], "nodes": []}
    if not want:
        return out
    for slug in _slugs(config_dir, systems_dir):
        try:
            if _detach_schedule(want, slug, config_dir):
                out["schedules"].append(slug)
            out["nodes"].extend(_detach_nodes(want, slug))
        except Exception:                                  # noqa: BLE001
            continue
    return out


def _slugs(config_dir, systems_dir):
    from core import config as _config                      # noqa: PLC0415
    from core.services import provision                     # noqa: PLC0415
    try:
        root = systems_dir or (_config.systems(config_dir) or {}).get("dir") or ""
        return sorted(provision.system_slugs(root, config_dir))
    except Exception:                                       # noqa: BLE001
        return []


def _detach_schedule(want, slug, config_dir):
    """A repeating footage export naming this mailbox stops emailing.

    It is not deleted: the schedule may also post to Telegram, and a mailbox
    going away is no reason to throw away a period, a recipient list and a
    message somebody wrote. Only the part that cannot work is switched off,
    and the whole export only stops if that was all it did.
    """
    from core.services import footage_schedule as sched     # noqa: PLC0415
    cur = sched.load(slug, config_dir)
    if str(cur.get("account") or "") != want:
        return False
    patch = {**cur, "account": "", "send_email": False}
    if not cur.get("send_telegram"):
        patch["enabled"] = False
    sched.save(patch, slug, config_dir)
    return True


def _detach_nodes(want, slug):
    """Email nodes naming this mailbox are left with no mailbox.

    Only output.email. The Telegram nodes carry an `account` too, meaning one
    of the box's BOTS, and clearing those because a mailbox was deleted would
    silence a chat for a reason nobody could ever find.
    """
    from core.services import workflow                      # noqa: PLC0415
    store = workflow.load_store(slug)
    if not store or not store.get("workflows"):
        return []
    hit = []
    for wf in store["workflows"]:
        for node in ((wf.get("graph") or {}).get("nodes") or []):
            data = node.get("data") or {}
            if node.get("type") == "output.email" and str(
                    data.get("account") or "") == want:
                node["data"] = {**data, "account": ""}
                hit.append(f"{slug}:{node.get('id') or '?'}")
    if hit:
        workflow.save_store(slug, store)
    return hit


def set_default(account_id, config_dir=None):
    want = str(account_id or "").strip()
    if want and not get(want, config_dir):
        raise ValueError("no such mailbox")
    raw = config.load_local(NAME, config_dir) or {}
    raw["default_id"] = want
    raw["accounts"] = load(config_dir)
    _save_raw(raw, config_dir)


def _write(accounts, config_dir, keep_default=True):
    raw = config.load_local(NAME, config_dir) or {}
    raw["accounts"] = accounts
    if keep_default:
        ids = {a["id"] for a in accounts}
        if str(raw.get("default_id") or "") not in ids:
            raw["default_id"] = accounts[0]["id"] if accounts else ""
    _save_raw(raw, config_dir)


def _save_raw(raw, config_dir):
    # An EMPTY accounts list is not an array-of-tables, and the writer would
    # emit `accounts = []`, which reads back as a list of scalars. Dropping
    # the key entirely is the honest empty state and load() answers [] for it.
    if not raw.get("accounts"):
        raw.pop("accounts", None)
    config.save_local(NAME, raw, config_dir)
