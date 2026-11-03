# -*- coding: utf-8 -*-
"""
auth.py -- Login sessions and user accounts.

One credential store serves the hub and every master on a machine:
- The admin is `admin`, whose password is [master].password in the gitignored
  auth.local.toml (docs/setup.md: one master password per deployment).
- Operator accounts live in users.local.toml, each with a per-user salted
  hash and the list of system slugs it may open ("*" = all).

Sessions are a signed cookie: "user|expiry|hmac". The signing secret is
generated once into auth.local.toml, so a restart does not log everyone out,
and the cookie is host-scoped, so one login covers the hub and every master
on the same host. No framework imports here (docs/conventions.md sec 2).

Access model: the hub admits any valid account; a master admits an account
whose systems list contains its own slug. The admin passes everything.
"""

import contextlib
import hashlib
import hmac
from email import utils as email_utils
import os
import re
import secrets
import threading
import time

from core import config
from core.services import cfg_lock

SESSION_COOKIE = "cytron_session"
#: The same session over TLS, under its own name (routes/auth.py _cookie_name).
SECURE_COOKIE = "__Secure-" + SESSION_COOKIE

#: One lock around every read-modify-write of the auth config files. Without
#: it, two concurrent writers (two admins editing accounts, two legacy-hash
#: upgrades on simultaneous logins) each read the whole file, edit their copy
#: and write it back -- last write wins and the other's change is silently
#: lost. In-process is enough in practice: only the hub serves the account
#: routes; masters read these files but never write them. (A hub+master write
#: race remains theoretically possible via login-time hash upgrades -- rare
#: enough to accept, and the atomic replace in config.save_local means the
#: file itself can never be torn.)
_WRITE_LOCK = threading.RLock()

#: Read cache for the two files every authenticated request consults. The
#: general policy is that load_local() is uncached (core/config.py) -- but
#: verify_session runs on EVERY request of EVERY user, and two blocking disk
#: reads inside the auth middleware is what made ten concurrent users feel
#: each other. Re-read only when the file's mtime changes; save_local()
#: changes the mtime, so an edit is picked up on the very next request with
#: no invalidation hooks. Callers must treat the result as READ-ONLY --
#: mutators keep loading fresh copies via config.load_local.
_LOCAL_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cached_local(name, config_dir=None):
    path = config.root(config_dir) / f"{name}.local.toml"
    key = str(path)
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    with _CACHE_LOCK:
        hit = _LOCAL_CACHE.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]
    data = config.load_local(name, config_dir)
    with _CACHE_LOCK:
        _LOCAL_CACHE[key] = (mtime, data)
    return data


def _save_local(name, data, config_dir=None):
    """config.save_local + drop this process's read cache for that file.

    The mtime alone cannot be trusted for OUR OWN writes: the kernel stamps
    file times from a coarse clock (whole milliseconds on ext4), so two
    writes inside one tick share an mtime and the second is invisible to the
    cache -- revoking a session read back as not-revoked on the Pi. Another
    PROCESS's write is still picked up by mtime, where human-scale timing
    makes a shared tick irrelevant.
    """
    config.save_local(name, data, config_dir)
    key = str(config.root(config_dir) / f"{name}.local.toml")
    with _CACHE_LOCK:
        _LOCAL_CACHE.pop(key, None)
        # The setup gate's answer is derived from the same file, and goes
        # stale on the same coarse tick (setup_pending).
        _SETUP_CACHE.pop(key, None)


def _auth_local(config_dir=None):
    return _cached_local("auth", config_dir)


def ensure_secret(config_dir=None):
    """Signing secret, created on first use. Master password is never
    generated -- an operator sets it deliberately or nobody logs in."""
    local = _auth_local(config_dir)
    if not (local.get("session") or {}).get("secret"):
        with _WRITE_LOCK:
            # Fresh read inside the lock: the cached dict must never be
            # mutated, and another thread may have won the race to mint it.
            local = config.load_local("auth", config_dir)
            if not (local.get("session") or {}).get("secret"):
                local.setdefault("session", {})["secret"] = secrets.token_urlsafe(32)
                _save_local("auth", local, config_dir)
    return local["session"]["secret"]


def master_password(config_dir=None):
    return (_auth_local(config_dir).get("master") or {}).get("password", "")


#: What the administrator account is called before anybody renames it. It is a
#: DEFAULT, not the identity: the account ships as `admin` so a fresh box can be
#: signed into at all, and first setup is where an operator gives it their own
#: name and password. Everything that used to compare against the literal
#: "admin" now compares against admin_username().
ADMIN_DEFAULT = "admin"


#: The names the administrator account leaves the factory with: ADMIN_DEFAULT
#: when nothing set one, and the installer's DEFAULT_ADMIN_USER (scripts/
#: install.sh), which is what every real box ships as. Recognised, never set
#: from here -- the account setup page nudges an owner off either (UAT-106:
#: comparing against "admin" alone meant the nudge never showed on a box).
SHIPPED_USERNAMES = (ADMIN_DEFAULT, "iriv")


def is_default_username(config_dir=None):
    """Is the administrator still called what every box ships as?"""
    return admin_username(config_dir) in SHIPPED_USERNAMES


def admin_username(config_dir=None):
    """The name the platform administrator signs in with."""
    name = str((_auth_local(config_dir).get("master") or {}).get("username")
               or "").strip().lower()
    return name or ADMIN_DEFAULT


def check_admin_username(new_name, config_dir=None):
    """The cleaned name, or ValueError -- set_admin_username's rules without
    the write, so a form that also saves other fields can refuse the whole
    submission before any of it lands (UAT-107)."""
    name = str(new_name or "").strip().lower()
    if not name.isidentifier():
        raise ValueError("a username must be a simple word (letters, digits, _)")
    if len(name) > 32:
        raise ValueError("a username is at most 32 characters")
    if _account_named(name, config_dir):
        raise UsernameTaken(f"{name!r} is already an operator account")
    # Keeping the name it has is not taking one: the systems recorded under
    # it are the administrator's own.
    if name != admin_username(config_dir):
        _refuse_held_name(name, config_dir)
    return name


class UsernameTaken(ValueError):
    """A name another identity already answers to. A ValueError, so every
    caller that refused a bad name still refuses this one; the routes answer
    it with 409 rather than 400 because the name is fine, it is just not free."""


#: What a reused name is told. Customer copy: it shows verbatim in the Add
#: User and Change Username dialogs.
NAME_TAKEN_MSG = "That username is already taken. Choose a different username."
NAME_HELD_MSG = ("That username was used by an account that has been deleted "
                 "and still owns systems. Choose a different username.")


def _account_named(name, config_dir=None):
    """Is there an operator row answering to `name`, in any case? Keys are
    lower-cased on the way in, but a hand-edited users.local.toml need not
    be, and verify_login folds case -- so two rows differing only in case
    would be one login with two credentials."""
    name = str(name or "").strip().lower()
    return any(str(k).strip().lower() == name for k in (users(config_dir) or {}))


def _refuse_held_name(name, config_dir=None):
    """Refuse a name still recorded as the OWNER of a system.

    delete_user removes the account row but not the `owner` field on the
    systems it created (config/systems.toml [masters.<slug>]), and the name is
    the only key that field has. A new account under the same name would read
    as that owner -- the Accounts page offers a system's owner its systems to
    hand on -- so it would inherit what nobody gave it. Ownership is left
    alone on delete (it is the accountability record); the name is what
    stays spent while anything still carries it.
    """
    name = str(name or "").strip().lower()
    masters = (config.systems(config_dir) or {}).get("masters") or {}
    for entry in masters.values():
        if str((entry or {}).get("owner") or "").strip().lower() == name:
            raise UsernameTaken(NAME_HELD_MSG)


def set_admin_username(new_name, config_dir=None):
    """Rename the administrator account.

    The name is the account's whole identity here -- there is no user row to
    key it by -- so this is the only thing that has to move, and every check
    reads admin_username() rather than a literal. It must not collide with an
    operator account: two identities answering to one name is one login the
    session cookie cannot tell apart.
    """
    name = check_admin_username(new_name, config_dir)
    with _WRITE_LOCK:
        local = config.load_local("auth", config_dir)
        local.setdefault("master", {})["username"] = name
        _save_local("auth", local, config_dir)
    return name


def admin_email(config_dir=None):
    return (_auth_local(config_dir).get("master") or {}).get("email", "")


def set_admin_email(email, config_dir=None):
    """Set the master's recovery address, or clear it with "".

    An address is OPTIONAL. A box on a closed network with no mail server has
    nothing to send a reset link through, and a recovery address that cannot
    be reached is worse than none: it reads as a working way back in.

    What is left when it is gone depends on the BOX. Where the front panel is
    running, the master resets at the hardware -- hold USER for five seconds
    and the OLED shows a single-use code (core/services/panel_reset.py). But
    the panel ships disabled (config/panel.toml), because on a stock board the
    factory flow drives the same I2C bus, so on a default box there is no
    second path and the way back in is a shell. Whoever offers this choice has
    to say which of the two it is: panel_reset_ready() answers that, and the
    Accounts panel asks before it offers to remove anything.
    """
    email = clean_email(email, allow_empty=True)
    with _WRITE_LOCK:
        local = config.load_local("auth", config_dir)
        if not email:
            master = local.setdefault("master", {})
            master.pop("email", None)
            # Deliberate, and recorded as such: see ADMIN_EMAIL_OPTOUT.
            master[ADMIN_EMAIL_OPTOUT] = True
            _save_local("auth", local, config_dir)
            return ""
        local.setdefault("master", {}).pop(ADMIN_EMAIL_OPTOUT, None)
        _refuse_taken_email(email, config_dir, except_user=admin_username(config_dir))
        local.setdefault("master", {})["email"] = email
        _save_local("auth", local, config_dir)
    return email


def panel_reset_ready(config_dir=None):
    """Can the master actually be reset at the box right now?

    THREE settings, all of which have to hold:

      enabled        the panel has to be running at all. False on a stock
                     board -- the factory Node-RED flow drives the same OLED
                     and button down one I2C bus.
      button         the button has to be claimed. `when_held` is wired INSIDE
                     that branch (panel.py, _open), so a kiosk install with
                     the button off -- a configuration panel.toml documents as
                     intended -- has no Button object for a hold to fire on,
                     whatever hold_shutdown says.
      hold_shutdown  the five-second press has to be armed. Kept its old name
                     so a deployment's saved `false` still turns it off.

    This reports configured INTENT, not a claimed pin: an IRIV board that is
    absent, a gpiozero that is missing, or a GPIO the factory flow still holds
    all leave these three true with no working hold (panel.status()["button"]
    is the ground truth, and only the hub process has it). So it is a floor --
    what it says is off really is off, and what it says is on is what the box
    was configured to do.

    Asked before anything offers to remove the last emailed way in. Imported
    lazily because the panel module reaches for hardware config and auth is
    imported by everything.
    """
    try:
        from core.services import panel as panel_service
        cfg = panel_service.settings(config_dir)
        return (bool(cfg.get("enabled")) and bool(cfg.get("button"))
                and bool(cfg.get("hold_shutdown")))
    except Exception:                                       # noqa: BLE001
        # A box whose panel config cannot be read is a box that cannot be
        # promised a panel reset.
        return False


#: Set on the master when its recovery address is removed ON PURPOSE, so "no
#: address" can still be told apart from "never asked". The login route used
#: to read it to decide first run; it no longer does (setup_pending is the one
#: predicate, and the address is optional), so this is a record, not a gate.
ADMIN_EMAIL_OPTOUT = "email_optout"


def admin_email_optout(config_dir=None):
    return bool((_auth_local(config_dir).get("master") or {}).get(ADMIN_EMAIL_OPTOUT))


def clean_email(email, allow_empty=False):
    """One address, lower-cased, or ValueError. The same rule the master's
    address has always had; kept small on purpose -- the mail server is the
    authority on what delivers, this only refuses what cannot be one."""
    email = " ".join(str(email or "").split()).lower()
    if not email:
        # "" is a CHOICE where the caller allows one -- "this account has no
        # recovery address" -- and a mistake everywhere else.
        if allow_empty:
            return ""
        raise ValueError("an email address is required")
    # ONE address. A comma list reached send_plain as several recipients, so
    # a "recovery address" could quietly have been two mailboxes; and the
    # characters that build a display name or a second header have no place
    # in a bare address at all.
    if any(c in email for c in ' ,;<>"()[]\\') or email.count("@") != 1:
        raise ValueError("enter one plain email address")
    if "." not in email.split("@")[-1] or len(email) > 254:
        raise ValueError("enter a valid email address")
    if email_utils.parseaddr(email)[1] != email:
        raise ValueError("enter a valid email address")
    return email


def account_for_email(email, config_dir=None):
    """The account an address belongs to -- the master's username, an
    operator's, or "" -- for the forgot-password page. One address, one
    account: create_user and set_user_email refuse a duplicate, and the master
    address is checked against the rows too, so this cannot be ambiguous."""
    try:
        email = clean_email(email)
    except ValueError:
        return ""
    if email == str(admin_email(config_dir) or "").strip().lower():
        return admin_username(config_dir)
    for name, u in users(config_dir).items():
        if str(u.get("email") or "").strip().lower() == email:
            # A frozen account cannot sign in, so it cannot ask to: a reset
            # would only put an approve button in front of the admins for
            # an account they switched off.
            return name if u.get("enabled", True) else ""
    return ""


def user_email(username, config_dir=None):
    """The address on file for one account -- the master's from the auth
    config, anyone else's from their row. "" when none."""
    if not username:
        return ""
    if username == admin_username(config_dir):
        return str(admin_email(config_dir) or "")
    return str((users(config_dir).get(username) or {}).get("email") or "")


def _refuse_taken_email(email, config_dir, except_user=""):
    """A recovery address names ONE account, or the forgot page could not say
    whose password it is resetting."""
    if email == str(admin_email(config_dir) or "").strip().lower() \
            and except_user != admin_username(config_dir):
        raise ValueError("that email belongs to the administrator account")
    for name, u in users(config_dir).items():
        if name != except_user and \
                str(u.get("email") or "").strip().lower() == email:
            raise ValueError(f"that email is already on the account {name!r}")


def set_user_email(username, email, config_dir=None):
    """Set or change an account's recovery address. Master
    only -- the CALLER enforces that (routes/auth.py): the address is where a
    reset link goes, so whoever may set it may take the account over, and
    that is not a site admin's to do."""
    username = (username or "").strip().lower()
    email = clean_email(email, allow_empty=True)
    with _WRITE_LOCK:
        local = config.load_local("users", config_dir)
        row = (local.get("users") or {}).get(username)
        if row is None:
            raise ValueError("no such account")
        # "" REMOVES it. The account then has no emailed way back in, which
        # the Accounts panel says on its face -- whoever can set the address
        # can already take the account over, so being able to remove it adds
        # no authority they did not have.
        if not email:
            row.pop("email", None)
            _save_local("users", local, config_dir)
            return ""
        _refuse_taken_email(email, config_dir, except_user=username)
        row["email"] = email
        _save_local("users", local, config_dir)
    return email


def check_password(new_password):
    """The master password rule, without the write (see check_admin_username)."""
    if not new_password or len(new_password) < 8:
        raise ValueError("password must be at least 8 characters")


def set_master_password(new_password, config_dir=None):
    """Store the admin password as a HASH.

    It used to be written in clear next to the session signing secret, so
    anyone who could read one file had both the platform's admin credential
    and the ability to mint sessions -- and every master process on the box
    reads that file at startup. The cleartext key is removed on write, so the
    upgrade takes effect the first time the password is set or reset.
    """
    check_password(new_password)
    with _WRITE_LOCK:
        local = config.load_local("auth", config_dir)
        master = local.setdefault("master", {})
        salt = secrets.token_hex(16)
        master["salt"] = salt
        master["password_hash"] = _hash(new_password, salt)
        master.pop("password", None)
        _save_local("auth", local, config_dir)


def _verify_master(password, config_dir=None):
    """Check the admin password against the hash, or the legacy cleartext.

    A deployment upgrading in place still has `password` in its auth.local
    .toml and nothing else; that keeps working, and the first correct login
    rewrites it as a hash so the cleartext leaves the disk without anyone
    having to run anything.
    """
    master = _auth_local(config_dir).get("master") or {}
    stored = str(master.get("password_hash") or "")
    if stored:
        ok, stale = _password_matches(password, master.get("salt", ""), stored)
        if ok and stale:
            try:
                set_master_password(password, config_dir)
            except Exception:                               # noqa: BLE001
                pass
        return ok
    legacy = str(master.get("password") or "")
    if not legacy:
        return False
    if not hmac.compare_digest(password or "", legacy):
        return False
    try:
        set_master_password(password, config_dir)           # retire the cleartext
    except Exception:                                       # noqa: BLE001
        pass
    return True


#: The password every board leaves the factory with (scripts/install.sh
#: DEFAULT_ADMIN_PASSWORD). It is written here only to be RECOGNISED -- nothing
#: sets a password from this constant, and the installer remains the one place
#: that puts it on a box.
SHIPPED_PASSWORD = "iriv1234"


def master_password_is_default(config_dir=None):
    """Is the master still signing in with the password printed in the manual?

    A board left on it is open to anyone who can read the README -- the same
    string is also the Wi-Fi AP key, so "anyone" means anyone within radio
    range of the unit, with no network access needed first. First-run setup
    used to be gated on the recovery address alone, so an owner who filled that
    in finished setup for ever with the shipped password still live.

    Asked through the SAME verify path a login takes, so it answers correctly
    whether the box stores a PBKDF2 hash, a legacy hash, or the cleartext an
    upgraded deployment still carries. An owner who chose their own password
    answers False and is never asked again -- including after an install.sh
    re-run, which only writes a password when there is none.
    """
    try:
        return bool(_verify_master(SHIPPED_PASSWORD, config_dir))
    except Exception:                                       # noqa: BLE001
        # A store that cannot be read is not evidence of a default password,
        # and this gate must not fire on one.
        return False


#: auth.local.toml path -> (st_mtime_ns, master_password_is_default()).
#: The setup gate (routes/auth.py require) asks on EVERY request the master
#: makes, and the honest answer is a PBKDF2 verify -- ~0.15 s on a Pi, which
#: would put that much on each call while the box is on the shipped password.
#: The answer can only change when the auth file does, so it is re-derived on
#: an mtime change and dropped by _save_local for this process's own writes.
_SETUP_CACHE = {}


def _auth_path(config_dir=None):
    return config.root(config_dir) / "auth.local.toml"


def setup_pending_cached(username, config_dir=None):
    """setup_pending() without the slow path: True/False when the answer is
    already known for the file as it is now, None when it would need the
    PBKDF2 verify. Lets the async middleware run that verify off the event
    loop and skip the thread hop on every other request."""
    if not username or username != admin_username(config_dir):
        return False
    path = _auth_path(config_dir)
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    with _CACHE_LOCK:
        hit = _SETUP_CACHE.get(str(path))
    if hit is not None and hit[0] == mtime:
        return hit[1]
    return None


def first_run_done(config_dir=None):
    """Has the owner been through the first-run wizard? Choosing to keep the
    shipped password there is allowed, so "still on the shipped password" can
    no longer mean "not set up" -- this flag is what says the choice was made."""
    return bool((_auth_local(config_dir).get("master") or {}).get("setup_done"))


def mark_first_run_done(config_dir=None):
    with _WRITE_LOCK:
        local = config.load_local("auth", config_dir)
        local.setdefault("master", {})["setup_done"] = True
        _save_local("auth", local, config_dir)


def first_run_pending(config_dir=None):
    """The wizard's gate: on the shipped password AND never set up."""
    return master_password_is_default(config_dir) and not first_run_done(config_dir)


def setup_pending(username, config_dir=None):
    """Must `username` finish account setup before using the box?

    True only for the MASTER account while it is still on SHIPPED_PASSWORD
    (UAT-103). The login response already said so, but that only steered the
    browser: every API answered the shipped password as a finished box,
    network page and AP key included. The middleware holds the line now, and
    this is the one predicate it asks. Operators are never
    gated -- they cannot change the master's password, so gating them would be
    a lock-out rather than a step to finish.

    The shipped password is the ONLY thing that opens first run. Nothing else
    re-derives "set up" from fields a later version made optional (the
    recovery email, the sending mailbox), so an update that adds an optional
    step never sends a finished box back through setup. The answer lives in
    auth.local.toml, which apply_update carries into every new release.
    """
    hit = setup_pending_cached(username, config_dir)
    if hit is not None:
        return hit
    path = _auth_path(config_dir)
    # Stamp BEFORE the verify: a write landing during it leaves the entry
    # filed under the older stamp, so the next call re-derives instead of
    # trusting an answer about a file that has since changed.
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        mtime = None
    pending = first_run_pending(config_dir)
    with _CACHE_LOCK:
        _SETUP_CACHE[str(path)] = (mtime, pending)
    return pending


#: The session window once auto logout is switched OFF: long enough that
#: nobody meets it, not infinite -- a token with no expiry is a token that
#: outlives being copied off a screen. Thirty days.
NO_AUTO_LOGOUT_MINUTES = 30 * 24 * 60


def auto_logout(config_dir=None):
    """Whether sessions time out at all. OFF out of the box (2026-09-14): a
    watched dashboard signing itself out mid-shift was the complaint; the
    admin who wants the timeout switches it on in Settings > Accounts."""
    return bool(config.auth(config_dir).get("session", {}).get("auto_logout", False))


def set_auto_logout(on, config_dir=None):
    """The one switch: on = the configured idle window, off = thirty days.

    Takes effect on the next sign-in and the next renewal; a session already
    issued keeps the expiry it was stamped with.
    """
    with _WRITE_LOCK:
        local = config.load_local("auth", config_dir)
        local.setdefault("session", {})["auto_logout"] = bool(on)
        _save_local("auth", local, config_dir)
    return bool(on)


def idle_minutes(config_dir=None):
    session = config.auth(config_dir).get("session", {})
    if not session.get("auto_logout", False):
        return NO_AUTO_LOGOUT_MINUTES
    return int(session.get("idle_timeout_minutes", 60))


# -- users --------------------------------------------------------------------

#: Work factor for password hashing. A stolen SD card is the realistic threat
#: for a board that sits on a factory floor, and the old scheme -- ONE round of
#: sha256(salt + password) -- is a rate a commodity GPU counts in billions per
#: second, i.e. no protection at all for the 8-character minimum this product
#: allows. PBKDF2-HMAC-SHA256 is in the standard library (no new dependency on
#: a Pi image), and 240k iterations costs a Pi ~0.15s per login while costing
#: an attacker the same 240,000x.
PBKDF2_ROUNDS = 240_000
PBKDF2_PREFIX = "pbkdf2$"


def _hash(password, salt, rounds=PBKDF2_ROUNDS):
    """The stored form of a password: `pbkdf2$<rounds>$<hex>`.

    The prefix is what makes migration possible -- see `_password_matches`.
    """
    dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                             (salt or "").encode("utf-8"), rounds)
    return f"{PBKDF2_PREFIX}{rounds}${dk.hex()}"


def _legacy_hash(password, salt):
    """The ORIGINAL scheme. Kept only to recognise stored hashes written
    before the upgrade, never to write new ones."""
    return hashlib.sha256(((salt or "") + (password or "")).encode("utf-8")).hexdigest()


def _password_matches(password, salt, stored):
    """(matched, needs_rehash) for either hash generation.

    An upgrade that invalidated every stored password would lock every
    operator out of their own board, so old hashes keep working and are
    replaced with a PBKDF2 one the next time that account logs in
    successfully.
    """
    stored = stored or ""
    if stored.startswith(PBKDF2_PREFIX):
        try:
            rounds = int(stored.split("$")[1])
        except (IndexError, ValueError):
            return False, False
        ok = hmac.compare_digest(_hash(password, salt, rounds), stored)
        return ok, ok and rounds < PBKDF2_ROUNDS
    ok = hmac.compare_digest(_legacy_hash(password, salt), stored)
    return ok, ok


#: Platform roles. Vocabulary + display only, except: platform administration
#: (user management, system create/remove) stays gated on the admin ACCOUNT.
#: `systems` remains the access-control source of truth -- a role never widens
#: what an account can open, it describes what kind of user it is.
#:
#: These three ship with the product and cannot be deleted; an admin may add
#: more (create_role) because "engineer / operator / viewer" is one industry's
#: vocabulary, not everyone's -- a site with shift leads and QA inspectors
#: should be able to say so. Because a role grants nothing, a custom one is a
#: label, and adding it cannot widen anyone's access.
BUILTIN_ROLES = ("engineer", "operator", "viewer")

#: Kept as the default vocabulary for callers that have no config_dir.
ROLES = BUILTIN_ROLES

#: The SITE ADMIN tier, between master and an ordinary account.
#:
#: A deployment is often run by two different people with two different jobs.
#: One owns the box: which systems exist, what the hardware does, who gets an
#: account at all. The other runs the operation on it day to day -- adds their
#: own staff, decides who sees which pages, sets retention -- and must NOT be
#: able to change the workflow, create systems, or touch the board.
#:
#: There was no tier for the second job. Anyone who needed to add an operator
#: had to be handed the master account, which hands them everything.
#:
#: A FLAG on the account, not a role: roles here grant nothing by design (see
#: BUILTIN_ROLES), and this one has to actually gate things. It IS settable
#: over the web -- but only by master (see may_set_admin), because an admin
#: who could promote themselves is not a tier, it is a formality.
ADMIN_FLAG = "admin"

#: How many systems an admin may be given, when master has capped them. Stored
#: per account; absent means no cap. Not a limit on what they can BUILD --
#: admins cannot create systems at all -- but on how much of the box one
#: operation is handed.
MAX_SYSTEMS_FIELD = "max_systems"

#: The tiers, STRONGEST FIRST. Everything that compares authority compares
#: positions in this tuple, so adding a tier is one edit here and one in
#: `tier()` rather than a new boolean threaded through every route.
TIER_MASTER = "master"
TIER_ADMIN = "admin"
TIER_USER = "user"
TIERS = (TIER_MASTER, TIER_ADMIN, TIER_USER)
_TIER_RANK = {name: i for i, name in enumerate(TIERS)}


def tier(username, config_dir=None):
    """Which tier this account is in. One of TIERS; unknown accounts are
    ordinary users, which is the tier that grants nothing.

    A DISABLED account falls to `user` whatever flags it carries -- switching
    an account off has to switch off everything it could do, and that has to
    include its rank.
    """
    name = (username or "").strip().lower()
    if not name:
        return TIER_USER
    if name == admin_username(config_dir):
        return TIER_MASTER
    u = users(config_dir).get(name) or {}
    # The Monitor account is an ordinary user whatever else the row says.
    if u.get("enabled", True) and u.get(ADMIN_FLAG) and not u.get("monitor"):
        return TIER_ADMIN
    return TIER_USER


def at_least(username, want, config_dir=None):
    """Is this account `want` or stronger? `at_least(me, TIER_MASTER)`."""
    return _TIER_RANK[tier(username, config_dir)] <= _TIER_RANK[want]


def is_master(username, config_dir=None):
    """Owns the BOX: systems, hardware, who has an account at all.

    The same answer is_admin() has always given -- the master account --
    under the name the product now uses for that tier. is_admin
    is kept as the spelling every existing route was written against.
    """
    return at_least(username, TIER_MASTER, config_dir)


def is_site_admin(username, config_dir=None):
    """Runs the OPERATION: their own staff, page grants, retention, mail.

    True for master as well -- it outranks the tier, and a
    superior who could not do a subordinate's job would be a strange kind of
    superior.
    """
    return at_least(username, TIER_ADMIN, config_dir)


def may_manage(actor, target, config_dir=None):
    """May `actor` create, edit, disable or delete `target`?

    STRICTLY DOWNWARD, and never sideways: an admin managing another admin is
    how one of them removes the other, and a tier whose members can demote
    each other has no ranking in it. Ordinary users manage nobody.

    """
    a = _TIER_RANK[tier(actor, config_dir)]
    t = _TIER_RANK[tier(target, config_dir)]
    return a <= _TIER_RANK[TIER_ADMIN] and a < t


def may_set_admin(actor, config_dir=None):
    """May `actor` grant or revoke the admin tier? Master only.

    Deliberately not "may_manage plus a bit": promotion is the one operation
    where the actor's own tier is the whole question, and an admin creating
    another admin -- who could then delete the first -- is the flat hierarchy
    this tier exists to avoid.
    """
    return at_least(actor, TIER_MASTER, config_dir)


def is_admin(username, config_dir=None):
    """May this account administer the BOX? (master tier.)

    This is the ONE place that decides it. The routes used to compare the
    username to the string "admin" in 43 places; a second identity could not
    be added correctly by editing 43 conditions and hoping none was missed.
    That is also why the name did not change when the site-admin tier arrived
    below it: every caller means "master or above", and rewording 40-odd call
    sites to prove it would risk exactly the mistake this function prevented.
    Read it as is_master(); it is literally that function.

    """
    return is_master(username, config_dir)



MAX_CUSTOM_ROLES = 40


def _removed_roles(config_dir=None):
    """Built-ins the admin has deleted. Recorded as a REMOVAL rather than by
    rewriting the built-in list, so the shipped vocabulary stays a constant
    and a site that deletes "engineer" today can be understood tomorrow."""
    raw = _cached_local("users", config_dir).get("roles_removed") or []
    return {str(r).strip().lower() for r in raw if str(r).strip()}


def roles(config_dir=None):
    """Every role name available on this platform: the built-ins the site has
    kept, then any the admin has added, in creation order."""
    local = _cached_local("users", config_dir)
    gone = _removed_roles(config_dir)
    out = [r for r in BUILTIN_ROLES if r not in gone]
    for name in local.get("roles") or []:
        name = str(name).strip().lower()
        if name and name not in out:
            out.append(name)
    return out


def _clean_role_name(name):
    name = str(name or "").strip().lower()
    if not name:
        raise ValueError("a role needs a name")
    if len(name) > 30:
        raise ValueError("a role name is at most 30 characters")
    if not all(c.isalnum() or c in "_- " for c in name):
        raise ValueError("a role name may use letters, numbers, spaces, - and _")
    return name


def create_role(name, config_dir=None):
    """Add a role to the platform's vocabulary. Returns the full list."""
    name = _clean_role_name(name)
    with _WRITE_LOCK:
        if name in roles(config_dir):
            raise ValueError(f"the role {name!r} already exists")
        local = config.load_local("users", config_dir)
        # Adding back a built-in that was deleted: un-remove it rather than
        # writing a custom role of the same name, so it reads as the built-in
        # it is and cannot appear twice.
        if name in BUILTIN_ROLES:
            local["roles_removed"] = sorted(_removed_roles(config_dir) - {name})
            _save_local("users", local, config_dir)
            return roles(config_dir)
        custom = list(local.get("roles") or [])
        if len(custom) >= MAX_CUSTOM_ROLES:
            raise ValueError(f"at most {MAX_CUSTOM_ROLES} custom roles")
        custom.append(name)
        local["roles"] = custom
        _save_local("users", local, config_dir)
    return roles(config_dir)


def delete_role(name, config_dir=None):
    """Remove a role -- custom or built-in.

    "engineer / operator / viewer" is one industry's vocabulary, and a site
    that uses none of them should not be stuck displaying all three. A role
    grants nothing (access is the systems allowlist plus the admin account),
    so removing one narrows a dropdown, never someone's access.

    Two guards, both about not breaking what exists:

      * a role still ASSIGNED to an account cannot go -- that would leave the
        account labelled with something the platform no longer knows about;
      * the LAST role cannot go -- every account carries one, so an empty
        vocabulary is a create form with nothing to pick.

    `admin` is not in this list at all and cannot be removed
    by it: admin is an account name
    (see ADMIN_FLAG), not a role.
    """
    name = _clean_role_name(name)
    with _WRITE_LOCK:
        current = roles(config_dir)
        if name not in current:
            raise ValueError(f"there is no role called {name!r}")
        if len(current) <= 1:
            raise ValueError(
                f"{name!r} is the only role left - add another one first")
        holders = [u["username"] for u in public_users(config_dir)
                   if u["role"] == name]
        if holders:
            raise ValueError(
                f"{name!r} is still assigned to {', '.join(holders[:5])}"
                + (" and others" if len(holders) > 5 else "")
                + " - change their role first")
        local = config.load_local("users", config_dir)
        local["roles"] = [r for r in (local.get("roles") or [])
                          if str(r).strip().lower() != name]
        # A built-in is not in that list to remove, so its deletion is recorded.
        if name in BUILTIN_ROLES:
            local["roles_removed"] = sorted(_removed_roles(config_dir) | {name})
        _save_local("users", local, config_dir)
    return roles(config_dir)


def users(config_dir=None):
    return _cached_local("users", config_dir).get("users", {}) or {}


def public_users(config_dir=None):
    return [{"username": name,
             "systems": u.get("systems", ""),
             # Where a reset link for this account goes. Empty on a row from
             # before addresses were required -- such an account cannot ask
             # for a reset until master gives it one.
             "email": str(u.get("email") or ""),
             "role": u.get("role") or "operator",
             # The tier, and the cap that only means anything on it.
             "admin": bool(u.get(ADMIN_FLAG)) and not u.get("monitor"),
             "monitor": bool(u.get("monitor")),
             "tier": tier(name, config_dir),
             "max_systems": int(u.get(MAX_SYSTEMS_FIELD) or 0),
             "enabled": bool(u.get("enabled", True)),
             # slug -> comma list of visible nav categories. A slug with no
             # entry means "all of them" -- the default, and the whole
             # pre-feature population.
             "sections": dict(u.get("sections") or {}),
             # slug -> comma list of pages master allows at all. Same "absent
             # means everything" rule, one level finer.
             "pages": dict(u.get("pages") or {}),
             # slug -> comma list of CAPABILITIES on those pages. Absent means
             # the tier's defaults (caps.DEFAULT_ADMIN_CAPS), not everything --
             # a capability is a thing that CHANGES something, so it is opted
             # into rather than assumed.
             "caps": dict(u.get("caps") or {})}
            for name, u in sorted(users(config_dir).items())]


def admin_account(config_dir=None):
    """The administrator as a row the accounts page can render.

    It has no entry in users.local.toml -- its credential lives in the auth
    config -- but it is still an ACCOUNT somebody signs in as, and leaving it
    off the list made the page claim a box with a working login had no
    accounts on it. `builtin` is what tells the UI which controls do not
    apply: it cannot be deleted or disabled (that would lock the deployment
    out of itself) and its role is deployment branding, not a role anyone
    picked.
    """
    return {"username": admin_username(config_dir),
            "systems": "*",
            "email": str(admin_email(config_dir) or ""),
            "role": role_label(admin_username(config_dir), config_dir).lower(),
            "builtin": True,
            "enabled": True}


def create_user(username, password, systems, config_dir=None, role=None,
                admin=False, email=None):
    """systems: comma string of slugs, or "*".

    `admin` makes the new account a site admin. The CALLER decides whether it
    may (see may_set_admin) -- an account creating one of its own tier is the
    flat hierarchy the tier exists to avoid, and that guard lives at the route
    where the actor's identity is known.

    `email` is where a reset link for this account goes; the accounts route
    requires it. None (the tests, and tools from before addresses existed)
    makes an account with no recovery path but master. One address, one
    account.
    """
    username = (username or "").strip().lower()
    taken = admin_username(config_dir)
    if not username.isidentifier() or username == taken:
        raise ValueError(
            f"username must be a simple word, and not {taken!r} "
            "(the administrator account)")
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if not systems:
        raise ValueError("choose at least one system")
    # An address is OPTIONAL: an account on a box with no mail server has
    # nothing a reset link could travel through, and one that reads as a way
    # back in without being one is worse than none.
    address = clean_email(email, allow_empty=True) if email is not None else ""
    with _WRITE_LOCK:
        local = config.load_local("users", config_dir)
        # Inside the lock, against the fresh read: this used to overwrite an
        # existing row outright, so "creating" a taken name silently reset
        # that account's password and grants.
        if any(str(k).strip().lower() == username
               for k in (local.get("users") or {})):
            raise UsernameTaken(NAME_TAKEN_MSG)
        # A live account is checked first: a name both live and owning a
        # system is "taken", not "deleted".
        _refuse_held_name(username, config_dir)
        if address:
            _refuse_taken_email(address, config_dir, except_user=username)
        salt = secrets.token_hex(8)
        row = {
            "salt": salt,
            "password_hash": _hash(password, salt),
            "systems": systems,
            "role": role if role in roles(config_dir) else "operator",
            "enabled": True,
        }
        if address:
            row["email"] = address
        if admin:
            row[ADMIN_FLAG] = True
        local.setdefault("users", {})[username] = row
        _save_local("users", local, config_dir)


def update_user(username, systems=None, role=None, enabled=None, config_dir=None,
                sections=None, pages=None, admin=None, max_systems=None,
                caps=None):
    """Edit an account in place -- systems list, role, enabled, per-system
    category and page visibility, tier and system cap -- without the
    delete-and-recreate dance (which would burn the password).

    `sections` is {slug: value} for the slugs being CHANGED; untouched slugs
    keep their grant. A value of "*" (or a list naming every category) removes
    the entry -- full visibility is the absence of a restriction, not a stored
    list that would go stale when a category is added to the product.

    `pages` is the same shape one level finer: {slug: [page ids]}. It belongs
    to MASTER -- an admin hands out categories, master decides which
    individual pages exist for an admin to hand out at all. The two intersect
    at read time (see visible_pages), so an admin can never widen past what
    master allowed by granting a category.

    `admin` and `max_systems` are the tier and the cap. THE CALLER decides who
    may set them (see may_set_admin): this function is the storage, and every
    route reaching it has already been through the routes' own guard.
    """
    from core.services import nav as nav_service
    with _WRITE_LOCK:
        local = config.load_local("users", config_dir)
        u = (local.get("users") or {}).get(username)
        if u is None:
            raise KeyError(username)
        if u.get("monitor"):
            raise ValueError("the Monitor account is managed in System settings")
        if systems is not None:
            if not str(systems).strip():
                raise ValueError("choose at least one system")
            # The cap master set on this account, checked where the systems
            # are actually handed over. An admin cannot create a system, so
            # this is the only door through which one reaches them.
            cap = u.get(MAX_SYSTEMS_FIELD)
            if cap:
                named = [s.strip() for s in str(systems).split(",") if s.strip()]
                if "*" in named:
                    raise ValueError(
                        f"this account is limited to {cap} system"
                        f"{'' if cap == 1 else 's'} - name them instead of "
                        "granting all of them")
                if len(named) > int(cap):
                    raise ValueError(
                        f"this account is limited to {cap} system"
                        f"{'' if cap == 1 else 's'}, and {len(named)} were "
                        "chosen - raise the limit or pick fewer")
            u["systems"] = str(systems)
            # A revoked system's category grant goes with it: keeping the row
            # would silently re-apply a stale restriction if the system is
            # ever granted back. (An "all systems" grant keeps every row --
            # each named system is still granted.)
            slugs = {s.strip() for s in str(systems).split(",") if s.strip()}
            if "*" not in slugs:
                held = {k: v for k, v in (u.get("sections") or {}).items()
                        if k in slugs}
                if held:
                    u["sections"] = held
                else:
                    u.pop("sections", None)
        if sections is not None:
            valid = set(nav_service.SECTION_KEYS)
            held = dict(u.get("sections") or {})
            for slug, value in dict(sections).items():
                slug = str(slug).strip()
                if not slug:
                    continue
                if value == "*" or value is None:
                    held.pop(slug, None)
                    continue
                keys = [str(k).strip() for k in
                        (value.split(",") if isinstance(value, str) else value)
                        if str(k).strip()]
                bad = sorted(set(keys) - valid)
                if bad:
                    raise ValueError("unknown category: " + ", ".join(bad))
                if not keys:
                    raise ValueError(
                        "leave at least one category visible, or remove the "
                        "system from the account instead")
                if set(keys) >= valid:
                    held.pop(slug, None)
                    continue
                # Stored in the product's order so the file reads like the nav.
                held[slug] = ",".join(k for k in nav_service.SECTION_KEYS
                                      if k in set(keys))
            if held:
                u["sections"] = held
            else:
                u.pop("sections", None)
        if role is not None:
            available = roles(config_dir)
            if role not in available:
                raise ValueError(f"role must be one of {', '.join(available)}")
            u["role"] = role
        if enabled is not None:
            u["enabled"] = bool(enabled)
        if pages is not None:
            valid = set(nav_service.ALL_PAGES)
            held = dict(u.get("pages") or {})
            for slug, value in dict(pages).items():
                slug = str(slug).strip()
                if not slug:
                    continue
                if value == "*" or value is None:
                    held.pop(slug, None)
                    continue
                keys = [str(k).strip() for k in
                        (value.split(",") if isinstance(value, str) else value)
                        if str(k).strip()]
                bad = sorted(set(keys) - valid)
                if bad:
                    raise ValueError("unknown page: " + ", ".join(bad))
                if not keys:
                    raise ValueError(
                        "leave at least one page visible, or remove the "
                        "system from the account instead")
                if set(keys) >= valid:
                    held.pop(slug, None)
                    continue
                held[slug] = ",".join(p for p in nav_service.ALL_PAGES
                                      if p in set(keys))
            if held:
                u["pages"] = held
            else:
                u.pop("pages", None)
        if caps is not None:
            # MASTER's grant, one level under the page: what this account may
            # CHANGE there. Unlike the two above, an EMPTY list is a real
            # answer and is stored as one ("this account changes nothing on
            # this system"); absence means the tier's defaults, so the two
            # cannot share the "empty removes the row" rule.
            from core.services import caps as caps_service
            valid = set(caps_service.ALL_CAPS)
            held = dict(u.get("caps") or {})
            for slug, value in dict(caps).items():
                slug = str(slug).strip()
                if not slug:
                    continue
                if value == "*" or value is None:
                    held.pop(slug, None)        # back to the tier's defaults
                    continue
                keys = [str(k).strip() for k in
                        (value.split(",") if isinstance(value, str) else value)
                        if str(k).strip()]
                bad = sorted(set(keys) - valid)
                if bad:
                    raise ValueError("unknown capability: " + ", ".join(bad))
                held[slug] = ",".join(c for c in caps_service.ALL_CAPS
                                      if c in set(keys))
            if held:
                u["caps"] = held
            else:
                u.pop("caps", None)
        if admin is not None:
            if admin:
                u[ADMIN_FLAG] = True
            else:
                u.pop(ADMIN_FLAG, None)
                # A cap belongs to the tier that has one. Left behind, it
                # would silently apply again if the account were promoted a
                # year later by somebody who never set it. Same for the
                # capability grant: it is the admin tier's, and a stale one
                # would decide what a re-promoted account may change.
                u.pop(MAX_SYSTEMS_FIELD, None)
                u.pop("caps", None)
        if max_systems is not None:
            try:
                cap = int(max_systems)
            except (TypeError, ValueError):
                raise ValueError("the system limit must be a number")
            if cap < 0:
                raise ValueError("the system limit cannot be negative")
            if cap:
                u[MAX_SYSTEMS_FIELD] = cap
            else:
                u.pop(MAX_SYSTEMS_FIELD, None)   # 0 means no cap
        _save_local("users", local, config_dir)
    return {"username": username, "systems": u.get("systems", ""),
            "role": u.get("role") or "operator",
            "enabled": bool(u.get("enabled", True)),
            "admin": bool(u.get(ADMIN_FLAG)),
            "max_systems": int(u.get(MAX_SYSTEMS_FIELD) or 0),
            "pages": dict(u.get("pages") or {}),
            "caps": dict(u.get("caps") or {}),
            "sections": dict(u.get("sections") or {})}


def section_grants(username, slug, config_dir=None):
    """Which nav categories `username` may see on system `slug`.

    None means ALL of them -- the admin, and any account with no
    stored restriction. Otherwise a set of nav.SECTION_KEYS. Unknown keys in
    the stored value are dropped rather than honoured: a key retired from the
    product must not keep a category alive that nothing can render."""
    from core.services import nav as nav_service
    if not username or is_admin(username, config_dir):
        return None
    u = users(config_dir).get(username) or {}
    raw = (u.get("sections") or {}).get(slug)
    if not raw:
        return None
    keys = {k.strip() for k in str(raw).split(",")
            if k.strip() in nav_service.SECTION_KEYS}
    return keys or None


def page_grants(username, slug, config_dir=None):
    """Which individual PAGES `username` may open on system `slug`.

    None means all of them. This is MASTER's grant: an admin hands out
    categories to their own staff, and this decides which pages exist for
    them to hand out at all. Master is never restricted --
    somebody has to be able to fix a grant that locked everyone out.
    """
    from core.services import nav as nav_service
    if not username or is_master(username, config_dir):
        return None
    u = users(config_dir).get(username) or {}
    held = u.get("pages") or {}
    raw = held.get(slug) or held.get("*")      # "*": every system (Monitor)
    if not raw:
        return None
    keys = {k.strip() for k in str(raw).split(",")
            if k.strip() in nav_service.ALL_PAGES}
    return keys or None


def visible_pages(username, slug, config_dir=None):
    """The pages this account may actually open: master's page grant AND the
    account's own category grant.

    Two grants, intersected, because they answer to two different people. If
    only one applied, an admin could widen past master by granting a category
    whose pages master had removed -- which would make master's restriction a
    suggestion.
    """
    from core.services import nav as nav_service
    allowed = page_grants(username, slug, config_dir)
    sections = section_grants(username, slug, config_dir)
    if sections is not None:
        by_section = {p for key in sections
                      for p in nav_service.SECTION_PAGES.get(key, ())}
        # A page no category claims is not hidden by a category grant -- see
        # nav.build, which treats an unmapped page as always visible.
        by_section |= {p for p in nav_service.ALL_PAGES
                       if p not in nav_service.PAGE_SECTION}
        allowed = by_section if allowed is None else (allowed & by_section)
    return allowed


def cap_grants(username, slug, config_dir=None):
    """What `username` may CHANGE on system `slug`, as a set of caps ids.

    None means unrestricted -- master, which is never held by a
    grant somebody below them could have written. Everyone else gets a set:
    what master ticked, or the tier's defaults where master has ticked nothing
    yet (caps.DEFAULT_ADMIN_CAPS), or nothing at all for an ordinary account --
    every route behind a capability was master-only before this existed, so an
    ordinary account holding none of them is the behaviour it always had.
    """
    from core.services import caps as caps_service
    if not username or is_master(username, config_dir):
        return None
    u = users(config_dir).get(username) or {}
    if u.get("monitor") and u.get("enabled", True):
        return set(caps_service.MONITOR_CAPS)     # fixed, never ticked
    held = u.get("caps") or {}
    if slug not in held:
        return (set(caps_service.DEFAULT_ADMIN_CAPS)
                if tier(username, config_dir) == TIER_ADMIN else set())
    # An EMPTY stored value is a decision ("changes nothing here") and reads
    # back as one -- which is why absence, above, is checked by key.
    return {c.strip() for c in str(held[slug]).split(",")
            if c.strip() in caps_service.ALL_CAPS}


def may(username, slug, cap, config_dir=None):
    """Whether this account may perform one capability on one system."""
    granted = cap_grants(username, slug, config_dir)
    return True if granted is None else cap in granted


#: Which Settings tabs each tier may open, and therefore which endpoints
#: behind them are its business.
#:
#: An ADMIN runs the operation: their own staff, how long data is kept, and
#: how the box talks out (mail, Telegram, network). They do not get Board I/O
#: or the Front Panel -- those decide what the HARDWARE does, which is the
#: box owner's call and not something a client's operations lead should be
#: able to change.
SETTINGS_TABS = {
    # "mqtt" (the box's broker) sits with "network" at every tier: both
    # decide what this box exposes on the plant network.
    TIER_ADMIN: ("accounts", "retention", "mail", "telegram", "network", "mqtt"),
    # "system" is the BOX's own switches -- what this appliance may do at
    # all. Master only: every switch on it opens or closes a
    # recovery path for every account at once.
    TIER_MASTER: ("accounts", "system", "retention", "board", "mail",
                  "telegram", "network", "mqtt", "panel"),
}


def settings_tabs(username, config_dir=None):
    """The Settings tabs this account may open. Empty for an ordinary user --
    Settings is not their page at all."""
    return list(SETTINGS_TABS.get(tier(username, config_dir), ()))


def grant_system(username, slug, config_dir=None):
    """Append one slug to an account's allowlist (idempotent). Used when a
    non-admin creates a system: the creator must be able to open what they
    just made. Unknown user / admin: no-op."""
    with _WRITE_LOCK:
        local = config.load_local("users", config_dir)
        u = (local.get("users") or {}).get(username)
        if u is None:
            return
        slugs = [s.strip() for s in str(u.get("systems") or "").split(",")
                 if s.strip()]
        if "*" in slugs or slug in slugs:
            return
        u["systems"] = ",".join(slugs + [slug])
        _save_local("users", local, config_dir)


def set_user_password(username, password, config_dir=None):
    """Admin resets an operator's password -- operators have no registered
    email, so their recovery path is the admin, by design."""
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    with _WRITE_LOCK:
        local = config.load_local("users", config_dir)
        if username not in (local.get("users") or {}):
            raise KeyError(username)
        if local["users"][username].get("monitor"):
            raise ValueError("the Monitor password is set in System settings")
        salt = secrets.token_hex(8)
        local["users"][username]["salt"] = salt
        local["users"][username]["password_hash"] = _hash(password, salt)
        _save_local("users", local, config_dir)
    # Session revocation is the CALLER's move: a self-service change keeps its
    # own session (routes/auth.py passes keep_sid); an admin reset revokes
    # them all. Doing it here would end the very session that asked.


def delete_user(username, config_dir=None):
    with _WRITE_LOCK:
        local = config.load_local("users", config_dir)
        if username not in (local.get("users") or {}):
            raise KeyError(username)
        if local["users"][username].get("monitor"):
            raise ValueError("the Monitor account is switched off in System settings")
        del local["users"][username]
        _save_local("users", local, config_dir)
    revoke_sessions_of(username, config_dir=config_dir)
    delete_devices_of(username, config_dir=config_dir)


def role_label(username, config_dir=None):
    """The badge the shell shows next to the account name.

    Admin keeps the first [roles] label from auth.toml (deployment branding,
    e.g. MASTER); everyone else shows their own stored role.
    """
    if username == admin_username(config_dir):
        labels = list(config.auth(config_dir).get("roles", {}).values())
        return labels[0] if labels else "ADMIN"
    u = users(config_dir).get(username)
    return str((u or {}).get("role") or "operator").upper()


def verify_login(username, password, config_dir=None):
    username = (username or "").strip().lower()
    if username == admin_username(config_dir):
        return _verify_master(password, config_dir)
    u = users(config_dir).get(username)
    if not u or not u.get("enabled", True):
        return False
    # The Monitor account has NO password sign-in: an access token only.
    if u.get("monitor"):
        return False
    ok, stale = _password_matches(password, u.get("salt", ""),
                                  u.get("password_hash", ""))
    if ok and stale:
        _upgrade_stored_password(username, password, config_dir)
    return ok


def _upgrade_stored_password(username, password, config_dir=None):
    """Re-store a correct password under the current scheme.

    Best effort by design: a read-only config must not turn a successful
    login into a failed one.
    """
    try:
        with _WRITE_LOCK:
            local = config.load_local("users", config_dir) or {}
            entry = (local.get("users") or {}).get(username)
            if not entry:
                return
            salt = secrets.token_hex(16)
            entry["salt"] = salt
            entry["password_hash"] = _hash(password, salt)
            _save_local("users", local, config_dir)
    except Exception:                                       # noqa: BLE001
        pass


def allowed(username, slug, config_dir=None):
    """May this account open this system's master? Hub passes slug=None.

    A disabled account may open NOTHING -- not even the hub. Its live session
    also dies here, because session verification re-checks the account."""
    if username == admin_username(config_dir):
        return True
    u = users(config_dir).get(username)
    if not u or not u.get("enabled", True):
        return False
    if slug is None:
        return True
    allowed_slugs = [s.strip() for s in (u.get("systems") or "").split(",")]
    return "*" in allowed_slugs or slug in allowed_slugs


# -- sessions -------------------------------------------------------------------

def _sign(secret, payload):
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


#: The session registry: config/sessions.local.toml, one table per session id.
#: It is what makes a session a THING that can be listed and revoked, instead
#: of only a signature that has to be waited out. The token stays self-proving
#: (signature first, registry second), so verification never needs a database
#: -- the registry is read through the same mtime cache as the users file and
#: costs no disk I/O until the file actually changes.
MAX_SESSIONS = 200

#: How often a session's last-activity is allowed to reach the disk. "Active
#: 2 minutes ago" does not warrant a write per request on an edge device.
SEEN_FLUSH_S = 60

#: Last-activity times not yet flushed: sid -> epoch. In-memory, per process;
#: the flush merges into the file without disturbing rows it does not own.
_SEEN = {}
_SEEN_FLUSHED = {"at": 0.0}


#: Last client IP per sid, not yet flushed -- feeds a registered device's
#: "current IP" (the session row keeps the IP it signed in from).
_SEEN_IP = {}


def _sessions_table(config_dir=None):
    return _cached_local("sessions", config_dir).get("sessions") or {}


@contextlib.contextmanager
def _sessions_rmw(config_dir=None):
    """Hold the sessions file for a read-modify-write, across PROCESSES.

    _WRITE_LOCK alone serialised writers inside one process, but the
    sessions file has writers in every process: the hub (sign-in, sign-out,
    device registration) and each master (touch_seen's last-activity flush,
    and sign-ins on a master port). Two of them interleaving read-modify-
    write lost one side's change -- harmless for a `seen` stamp, not for a
    registered device or a revocation. cfg_lock is the file lock the system
    config writers already share (fcntl on the box, msvcrt on a dev box).
    """
    path = str(config.root(config_dir) / "sessions.local.toml")
    with _WRITE_LOCK, cfg_lock.held(path):
        yield


def _new_sid():
    return secrets.token_hex(8)


def register_session(username, sid, device="", ip="", config_dir=None, dev=""):
    """Record a freshly minted session so it can be listed and revoked.

    `dev` is the registered sign-in device the session belongs to (see the
    devices section below); "" for a session with no device, which the list
    groups as a legacy device and the cap does not count."""
    now = int(time.time())
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        table = local.setdefault("sessions", {})
        _prune(table, now)
        table[str(sid)] = {
            "user": str(username),
            # Coarse device words only -- never the raw User-Agent string.
            "device": str(device or "")[:60],
            "ip": str(ip or "")[:45],
            "login": now,
            "expiry": now + idle_minutes(config_dir) * 60,
            "seen": now,
            "revoked": False,
            "dev": str(dev or ""),
        }
        _save_local("sessions", local, config_dir)


def _prune(table, now):
    """Drop expired rows in place; cap the table at MAX_SESSIONS."""
    for sid in [s for s, row in table.items()
                if int(row.get("expiry") or 0) < now]:
        del table[sid]
    if len(table) > MAX_SESSIONS:
        for sid in sorted(table, key=lambda s: int(table[s].get("seen") or 0)) \
                [:len(table) - MAX_SESSIONS]:
            del table[sid]


def _device_ip(ip):
    """An IP worth recording as a device's current address, or "".

    Loopback is the hub calling a master on the operator's behalf
    (masters.fetch_json forwards the cookie, not the browser's address), so
    it must never overwrite the address the browser really has."""
    ip = str(ip or "").strip()[:45]
    if not ip or ip in ("127.0.0.1", "::1", "localhost") or ip.startswith("127."):
        return ""
    return ip


def touch_seen(sid, config_dir=None, ip=""):
    """Note activity on a session; flush to disk at most once per minute.

    The flush merges into a FRESH read of the file and only advances `seen`
    on rows that still exist -- it must never resurrect a revoked row or drop
    a session another process registered in between. The session's
    registered device (if any) is advanced in the same write, with the
    address the browser was last seen at.
    """
    now = time.time()
    _SEEN[str(sid)] = int(now)
    if _device_ip(ip):
        _SEEN_IP[str(sid)] = _device_ip(ip)
    if now - _SEEN_FLUSHED["at"] < SEEN_FLUSH_S:
        return
    with _WRITE_LOCK:
        if now - _SEEN_FLUSHED["at"] < SEEN_FLUSH_S:
            return
        _SEEN_FLUSHED["at"] = now
        pending = dict(_SEEN)
        pending_ip = dict(_SEEN_IP)
        _SEEN.clear()
        _SEEN_IP.clear()
        try:
            with _sessions_rmw(config_dir):
                local = config.load_local("sessions", config_dir)
                table = local.get("sessions") or {}
                devices = local.get("devices") or {}
                changed = False
                for s, seen in pending.items():
                    row = table.get(s)
                    if row is None:
                        continue
                    if seen > int(row.get("seen") or 0):
                        row["seen"] = seen
                        changed = True
                    dev = devices.get(str(row.get("dev") or ""))
                    if (dev is not None and not row.get("revoked")
                            and str(dev.get("user") or "") == str(row.get("user") or "")):
                        if seen > int(dev.get("seen") or 0):
                            dev["seen"] = seen
                            changed = True
                        if pending_ip.get(s) and dev.get("ip") != pending_ip[s]:
                            dev["ip"] = pending_ip[s]
                            changed = True
                _prune(table, int(now))
                if changed:
                    local["sessions"] = table
                    _save_local("sessions", local, config_dir)
        except Exception:                                   # noqa: BLE001
            pass                # last-activity display is never worth a 500


def extend_session(sid, config_dir=None):
    """Push a session's expiry out by the idle window (keepalive re-issue)."""
    now = int(time.time())
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        row = (local.get("sessions") or {}).get(str(sid))
        if row is None:
            return
        row["expiry"] = now + idle_minutes(config_dir) * 60
        row["seen"] = now
        _save_local("sessions", local, config_dir)


def rename_session_user(sid, new_username, config_dir=None):
    """The account was renamed mid-session; its registry row follows."""
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        row = (local.get("sessions") or {}).get(str(sid))
        if row is None:
            return
        row["user"] = str(new_username)
        _save_local("sessions", local, config_dir)


def revoke_session(sid, config_dir=None):
    """End one session everywhere its token is held. Returns the row's user,
    or None if the sid is unknown."""
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        row = (local.get("sessions") or {}).get(str(sid))
        if row is None:
            return None
        row["revoked"] = True
        _save_local("sessions", local, config_dir)
        return str(row.get("user") or "")


def revoke_sessions_of(username, keep_sid=None, config_dir=None):
    """End every session of one account, except `keep_sid` if given.

    Called on password change and account delete (a credential change must
    shut out whoever else holds a session), and by "sign out other sessions".
    Returns how many were revoked.
    """
    n = 0
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        table = local.get("sessions") or {}
        for s, row in table.items():
            if (str(row.get("user") or "") == str(username)
                    and s != str(keep_sid or "") and not row.get("revoked")):
                row["revoked"] = True
                n += 1
        if n:
            _save_local("sessions", local, config_dir)
    return n


def sessions_of(username, config_dir=None):
    """One account's live sessions, newest activity first."""
    return [s for s in all_sessions(config_dir) if s["user"] == username]


def all_sessions(config_dir=None):
    """Every live session on the box, for the admin's Sessions view.

    Revoked and expired rows are left out -- they are bookkeeping, not
    sessions. `seen` folds in this process's unflushed activity so "Active
    now" is honest even between flushes.
    """
    now = int(time.time())
    out = []
    for sid, row in _sessions_table(config_dir).items():
        if row.get("revoked") or int(row.get("expiry") or 0) < now:
            continue
        out.append({
            "sid": sid,
            "user": str(row.get("user") or ""),
            "device": str(row.get("device") or ""),
            "ip": str(row.get("ip") or ""),
            "login": int(row.get("login") or 0),
            "expiry": int(row.get("expiry") or 0),
            "seen": max(int(row.get("seen") or 0), _SEEN.get(sid, 0)),
            "dev": str(row.get("dev") or ""),
        })
    out.sort(key=lambda s: s["seen"], reverse=True)
    return out


def issue_session(username, config_dir=None, sid=None):
    """Mint a session token: `user|sid|expiry|hmac`.

    A fresh sid is minted unless the caller passes one (keepalive and rename
    re-issue the SAME session with a new window -- a new sid there would grow
    a registry row per keepalive). The signature is domain-separated ("sess|")
    from the reset/re-auth tokens, which share the pipe-delimited shape.
    """
    secret = ensure_secret(config_dir)
    sid = sid or _new_sid()
    expiry = str(int(time.time()) + idle_minutes(config_dir) * 60)
    body = f"{username}|{sid}|{expiry}"
    return f"{body}|{_sign(secret, 'sess|' + body)}"


def session_sid(token):
    """The sid inside a 4-part token, or "" for a legacy 3-part one."""
    parts = (token or "").split("|")
    return parts[1] if len(parts) == 4 else ""


# -- registered sign-in devices --------------------------------------------------
# Owner decision (2026-10-13): an account may sign in from at most
# MAX_DEVICES browsers, and a browser that has not been used for a month has
# to sign in and register again. The MAC address was discussed and rejected
# as the identity: a browser never sends it, phones randomise it per network,
# and anyone on the LAN can claim any MAC. So a device is a BROWSER, named by
# a random id the server mints at the first sign-in and keeps in a long-lived
# HttpOnly cookie. The box stores only a hash of it.
#
# What the id is NOT: a credential. It never authenticates anything -- the
# password does that, and the session token after it. The id only tells the
# box "this sign-in comes from a browser it has seen before", so the cap
# counts browsers instead of sign-ins. A stolen device cookie with no
# session is a stolen nothing: it cannot open a page, it only spares the
# thief a slot in the cap AFTER they have typed the right password.
#
# The cap is PER ACCOUNT, so one account's pile of old phones cannot lock a
# different account out. The master tier is not capped (the
# box owner must always be able to sign in), and
# master can remove any account's devices.
#
# Records live in the sessions file, under [devices.<ref>]: one atomic write
# covers "remove the device AND revoke its sessions", and the last-activity
# flush advances both in one go. `ref` is a short public handle the API uses
# (never the id itself, never the hash). One browser signed in as two
# accounts is two records with the same hash: each counts in its own account.

DEVICE_COOKIE = "cytron_device"
#: The HTTPS name. A Secure cookie set on https:// is not sent to http:// on
#: the same host, and a browser refuses to let an http:// page overwrite it --
#: with one name, every plain-HTTP sign-in after an HTTPS one would mint a new
#: id, fail to store it, and eat a slot in the cap. Two names keep the two
#: schemes apart (one browser = at most two devices, never N). Same split as
#: the session cookie's (core/services/masters.py for_master).
DEVICE_COOKIE_SECURE = "__Secure-" + DEVICE_COOKIE

#: How many devices one account may register. Master: no cap.
MAX_DEVICES = 10
#: A device unused for this long is removed (and its sessions revoked).
DEVICE_EXPIRY_S = 30 * 24 * 3600
#: "Online" = some session of the device was active this recently.
DEVICE_ONLINE_S = 5 * 60
#: The cookie's lifetime, refreshed on use (400 days is what Chrome caps a
#: cookie at anyway; the 30-day expiry above is the real limit).
DEVICE_COOKIE_MAX_AGE_S = 400 * 24 * 3600
#: How often the hub sweeps expired devices.
DEVICE_SWEEP_S = 3600
#: The uncapped tiers still get a ceiling, so the file cannot grow without
#: bound from a script that signs in without a cookie jar: past it, the
#: least recently used device of that account makes room.
MAX_EXEMPT_DEVICES = 50

DEVICE_LIMIT_MSG = ("This account already has 10 registered devices. Remove one "
                    "in Accounts > Login Sessions, or ask an administrator.")

_DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")
_DEVICE_REF = re.compile(r"^(?:d[0-9a-f]{12}|legacy-[0-9a-f]{12})$")
LEGACY_PREFIX = "legacy-"


class DeviceLimitReached(Exception):
    """A capped account already has MAX_DEVICES registered devices.
    `.expired` carries any records the same pass expired (for the audit)."""
    expired = ()


def new_device_id():
    """128 random bits, as 32 hex characters."""
    return secrets.token_hex(16)


def valid_device_id(raw):
    return isinstance(raw, str) and bool(_DEVICE_ID.match(raw))


def valid_device_ref(ref):
    return isinstance(ref, str) and bool(_DEVICE_REF.match(ref))


def device_hash(raw):
    """What the box stores instead of the id, like a token. Unsalted is
    enough: the input is 128 random bits, nothing to guess or tabulate."""
    return hashlib.sha256(("iriv-device|" + raw).encode("ascii")).hexdigest()


def device_capped(username, config_dir=None):
    """Does the MAX_DEVICES cap apply to this account?"""
    return not at_least(username, TIER_MASTER, config_dir)


def _devices_table(config_dir=None):
    return _cached_local("sessions", config_dir).get("devices") or {}


def _device_live(dev, now):
    return now - int(dev.get("seen") or 0) <= DEVICE_EXPIRY_S


def device_ref_for(raw, username, config_dir=None):
    """The ref of `username`'s live device this cookie names, or "".
    Read-only: this never registers anything."""
    if not valid_device_id(raw):
        return ""
    h, now = device_hash(raw), int(time.time())
    for ref, dev in _devices_table(config_dir).items():
        if (dev.get("hash") == h and dev.get("user") == username
                and _device_live(dev, now)):
            return ref
    return ""


def device_known(raw, config_dir=None):
    """Does this cookie still name a live device of ANY account? False for a
    removed, expired, malformed or made-up id -- the cookie is then cleared."""
    if not valid_device_id(raw):
        return False
    h, now = device_hash(raw), int(time.time())
    return any(dev.get("hash") == h and _device_live(dev, now)
               for dev in _devices_table(config_dir).values())


def _drop_device(local, ref):
    """Remove one device record and revoke every session that belongs to it.
    Mutates `local`; the caller saves. Returns the record (with its ref)."""
    dev = (local.get("devices") or {}).pop(ref, None)
    if dev is None:
        return None
    for row in (local.get("sessions") or {}).values():
        if str(row.get("dev") or "") == ref:
            row["revoked"] = True
    return {**dev, "ref": ref}


def _expire_locked(local, now):
    """Drop every device unused for DEVICE_EXPIRY_S. Caller holds the lock."""
    devices = local.get("devices") or {}
    gone = [ref for ref, dev in devices.items() if not _device_live(dev, now)]
    return [_drop_device(local, ref) for ref in gone]


def _new_ref(devices):
    while True:
        ref = "d" + secrets.token_hex(6)
        if ref not in devices:
            return ref


def _legacy_key(user, words, ip):
    digest = hashlib.sha256(f"{user}|{words}|{ip}".encode("utf-8")).hexdigest()
    return LEGACY_PREFIX + digest[:12]


def register_device(username, raw, name="", ip="", mac="", config_dir=None,
                    now=None):
    """The sign-in half of the device model. Called only after the password
    has been verified. Returns a dict:

      raw      the id the browser must hold from now on (set it as the cookie)
      ref      the device record's public handle (goes on the session row)
      created  True when this sign-in registered a NEW device
      expired  records dropped on the way for being unused 30 days (audit)
      evicted  the record an uncapped account's ceiling pushed out, or None

    Raises DeviceLimitReached for a capped account at MAX_DEVICES -- then
    nothing at all is written.

    The browser's id is honoured only while it is still registered to SOME
    account (this one: same device, nothing counted twice; another: the same
    browser, so it keeps its id). An id the box does not know -- removed,
    expired, or made up -- is replaced by a fresh one: the server never
    adopts an id a client chose, which is what makes fixation pointless.

    Check-and-insert happens under the cross-process file lock, so two
    sign-ins racing for the last slot cannot both get it.
    """
    now = int(time.time() if now is None else now)
    username = str(username)
    name = str(name or "")[:60]
    ip = _device_ip(ip)
    mac = str(mac or "")[:17]
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        local.setdefault("sessions", {})
        devices = local.setdefault("devices", {})
        expired = _expire_locked(local, now)
        h = device_hash(raw) if valid_device_id(raw) else ""
        for ref, dev in devices.items():
            if h and dev.get("hash") == h and dev.get("user") == username:
                dev["seen"] = now
                dev["name"] = name or dev.get("name", "")
                if ip:
                    dev["ip"] = ip
                if mac:
                    dev["mac"], dev["mac_ip"] = mac, ip
                _save_local("sessions", local, config_dir)
                return {"raw": raw, "ref": ref, "created": False,
                        "expired": expired, "evicted": None}
        mine = [ref for ref, dev in devices.items() if dev.get("user") == username]
        evicted = None
        if device_capped(username, config_dir):
            if len(mine) >= MAX_DEVICES:
                if expired:
                    # The expiry still happened; only the new row is refused.
                    _save_local("sessions", local, config_dir)
                err = DeviceLimitReached(DEVICE_LIMIT_MSG)
                err.expired = expired
                raise err
        elif len(mine) >= MAX_EXEMPT_DEVICES:
            oldest = min(mine, key=lambda r: int(devices[r].get("seen") or 0))
            evicted = _drop_device(local, oldest)
        if not (h and any(dev.get("hash") == h for dev in devices.values())):
            raw = new_device_id()
            h = device_hash(raw)
        ref = _new_ref(devices)
        devices[ref] = {"user": username, "hash": h, "name": name,
                        "first": now, "seen": now, "registered": now,
                        "ip": ip, "mac": mac, "mac_ip": ip if mac else ""}
        # This account's pre-update sessions from the same browser words and
        # address are folded under the new device, so the list stops showing
        # them as a separate legacy row. Grouping only: it never grants
        # anything (removing the device revokes them too, the safe way).
        for row in local["sessions"].values():
            if (str(row.get("user") or "") == username and not row.get("revoked")
                    and str(row.get("dev") or "") not in devices
                    and str(row.get("device") or "") == name
                    and _device_ip(row.get("ip")) == ip):
                row["dev"] = ref
        _save_local("sessions", local, config_dir)
    return {"raw": raw, "ref": ref, "created": True, "expired": expired,
            "evicted": evicted}


def expire_devices(config_dir=None, now=None):
    """The 30-day sweep: drop every device unused that long and revoke its
    sessions. Returns the dropped records (each with its ref) for the audit.
    Writes only when something expired."""
    now = int(time.time() if now is None else now)
    if not any(not _device_live(dev, now)
               for dev in _devices_table(config_dir).values()):
        return []
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        gone = _expire_locked(local, now)
        if gone:
            _save_local("sessions", local, config_dir)
    return gone


def remove_device(ref, config_dir=None):
    """Unregister one device and revoke its sessions. A legacy ref revokes
    the pre-update sessions grouped under it. Returns {"user", "name",
    "ref", "sessions": n} or None when the ref names nothing."""
    if not valid_device_ref(ref):
        return None
    view = next((d for d in devices_view(config_dir) if d["id"] == ref), None)
    if view is None:
        return None
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        table = local.get("sessions") or {}
        n = 0
        if view["legacy"]:
            for s in view["sids"]:
                row = table.get(s)
                if row is not None and not row.get("revoked"):
                    row["revoked"] = True
                    n += 1
        else:
            n = sum(1 for row in table.values()
                    if str(row.get("dev") or "") == ref and not row.get("revoked"))
            _drop_device(local, ref)
        _save_local("sessions", local, config_dir)
    return {"user": view["user"], "name": view["name"], "ref": ref, "sessions": n}


def remove_devices_of(username, keep_ref="", keep_sid="", config_dir=None):
    """"Remove Other Devices": every device of `username` except `keep_ref`
    is unregistered, and every session of the account except those of
    `keep_ref` (and `keep_sid`, for a caller on a legacy session) ends.
    Returns how many devices went."""
    n = 0
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        devices = local.get("devices") or {}
        for ref in [r for r, d in devices.items()
                    if d.get("user") == username and r != keep_ref]:
            _drop_device(local, ref)
            n += 1
        legacy = set()
        for s, row in (local.get("sessions") or {}).items():
            if (str(row.get("user") or "") == username and not row.get("revoked")
                    and s != str(keep_sid or "")
                    and (not keep_ref or str(row.get("dev") or "") != keep_ref)):
                if str(row.get("dev") or "") not in devices:
                    legacy.add(_legacy_key(username, row.get("device") or "",
                                           row.get("ip") or ""))
                row["revoked"] = True
        _save_local("sessions", local, config_dir)
    return n + len(legacy)


def delete_devices_of(username, config_dir=None):
    """An account was deleted: its device records go with it."""
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        devices = local.get("devices") or {}
        gone = [r for r, d in devices.items() if d.get("user") == username]
        for ref in gone:
            _drop_device(local, ref)
        if gone:
            _save_local("sessions", local, config_dir)


def rename_devices_user(old, new, config_dir=None):
    """An account was renamed: its registered devices follow it, or every
    browser it used would have to register again under the new name."""
    with _sessions_rmw(config_dir):
        local = config.load_local("sessions", config_dir)
        changed = False
        for dev in (local.get("devices") or {}).values():
            if dev.get("user") == old:
                dev["user"] = str(new)
                changed = True
        if changed:
            _save_local("sessions", local, config_dir)


def devices_view(config_dir=None, now=None):
    """Every live device on the box, with its live sessions folded in.

    Registered devices first come from their records; then the sessions with
    no (live) device -- signed in before this feature, or re-issued from a
    pre-registry token -- are grouped by (account, browser words, sign-in IP)
    into LEGACY devices. A legacy device is display only: it is not counted
    against the cap, and it disappears once its sessions end or the browser
    signs in again (which registers it and folds the sessions in).

    Each item: id, user, name, ip, mac, mac_ip, first, seen, registered (epoch
    or 0), legacy, online, sids, sessions (the all_sessions rows).
    """
    now = int(time.time() if now is None else now)
    devices = _devices_table(config_dir)
    live = all_sessions(config_dir)
    out, by_ref, legacy = [], {}, {}
    for ref, dev in devices.items():
        if not _device_live(dev, now):
            continue
        item = {"id": ref, "user": str(dev.get("user") or ""),
                "name": str(dev.get("name") or ""), "ip": str(dev.get("ip") or ""),
                "mac": str(dev.get("mac") or ""), "mac_ip": str(dev.get("mac_ip") or ""),
                "first": int(dev.get("first") or 0), "seen": int(dev.get("seen") or 0),
                "registered": int(dev.get("registered") or 0), "legacy": False,
                "sessions": []}
        by_ref[ref] = item
        out.append(item)
    for s in live:
        item = by_ref.get(s["dev"])
        if item is not None and item["user"] == s["user"]:
            item["sessions"].append(s)
            continue
        key = _legacy_key(s["user"], s["device"], s["ip"])
        item = legacy.get(key)
        if item is None:
            item = legacy[key] = {
                "id": key, "user": s["user"], "name": s["device"], "ip": s["ip"],
                "mac": "", "mac_ip": "", "first": s["login"], "seen": 0,
                "registered": 0, "legacy": True, "sessions": []}
            out.append(item)
        item["first"] = min(item["first"], s["login"])
        item["sessions"].append(s)
    for item in out:
        item["seen"] = max([item["seen"]] + [s["seen"] for s in item["sessions"]])
        item["online"] = bool(item["sessions"]) and now - item["seen"] <= DEVICE_ONLINE_S
        item["sids"] = [s["sid"] for s in item["sessions"]]
    out.sort(key=lambda d: d["seen"], reverse=True)
    return out


#: How long an emailed reset link stays valid.
RESET_WINDOW_S = 30 * 60


def _password_nonce(config_dir=None, username=""):
    """Ties a reset token to the CURRENT password of ONE account: once that
    password changes, every previously emailed link for it dies instead of
    living out its window.

    Derived through the SIGNING SECRET, not from the password directly. The
    old version hashed the cleartext password and put 12 hex characters of
    that hash in a token which is then emailed -- putting 48 bits of an
    unsalted, unstretched hash of the live admin credential into a mailbox and
    across whatever hops SMTP takes. This changes with the password exactly
    as before, but reveals nothing about it to whoever reads the mail.

    `username` empty or the master's name means the master account, whose
    credential lives in the auth config; anyone else's is their row. The name
    is part of the material, so no two accounts can share a nonce.
    """
    if not username or username == admin_username(config_dir):
        master = _auth_local(config_dir).get("master") or {}
        material = str(master.get("password_hash") or master.get("password") or "")
        who = admin_username(config_dir)
    else:
        row = users(config_dir).get(username) or {}
        material = str(row.get("password_hash") or "")
        who = username
    return _sign(ensure_secret(config_dir), f"pwnonce|{who}|{material}")[:12]


def issue_reset_token(config_dir=None, username=""):
    """A reset link's token, for ONE account. `username` empty = the master,
    which is what every caller meant before accounts had addresses of their
    own. The name rides in the token so the reset route knows whose password
    it is setting; the signature covers it, so it cannot be swapped."""
    secret = ensure_secret(config_dir)
    who = username or admin_username(config_dir)
    expiry = str(int(time.time()) + RESET_WINDOW_S)
    payload = f"pwreset|{who}|{expiry}|{_password_nonce(config_dir, who)}"
    return f"{payload}|{_sign(secret, payload)}"


def reset_token_user(token, config_dir=None):
    """The account a reset token is good for, or "" when it is not good for
    any: wrong shape, bad signature, expired, unknown account, or minted
    before that account's password last changed."""
    if not token or token.count("|") != 4:
        return ""
    kind, who, expiry, nonce, sig = token.split("|")
    payload = f"{kind}|{who}|{expiry}|{nonce}"
    if kind != "pwreset" or not who:
        return ""
    if not hmac.compare_digest(_sign(ensure_secret(config_dir), payload), sig):
        return ""
    try:
        if int(expiry) < time.time():
            return ""
    except ValueError:
        return ""
    if who != admin_username(config_dir):
        row = users(config_dir).get(who)
        if not isinstance(row, dict) or not row.get("enabled", True):
            return ""
    if not hmac.compare_digest(nonce, _password_nonce(config_dir, who)):
        return ""
    return who


def verify_reset_token(token, config_dir=None):
    return bool(reset_token_user(token, config_dir))


# -- step-up re-authentication (v2.0 #11/#12/#13) -------------------------------
# Sensitive credential operations (view token, rotate, revoke) require the
# admin to re-enter their password. verify_login() is the check; issue_reauth
# mints a short-lived token so the confirming password isn't re-sent per click.

REAUTH_WINDOW_S = 5 * 60


def issue_reauth(username, config_dir=None):
    secret = ensure_secret(config_dir)
    expiry = str(int(time.time()) + REAUTH_WINDOW_S)
    payload = f"reauth|{username}|{expiry}"
    return f"{payload}|{_sign(secret, payload)}"


def verify_reauth(token, username, config_dir=None):
    """True if `token` is a live re-auth token for `username`."""
    if not token or token.count("|") != 3:
        return False
    kind, who, expiry, sig = token.split("|")
    if kind != "reauth" or who != username:
        return False
    payload = f"{kind}|{who}|{expiry}"
    if not hmac.compare_digest(_sign(ensure_secret(config_dir), payload), sig):
        return False
    try:
        return int(expiry) >= time.time()
    except ValueError:
        return False


def verify_session(token, config_dir=None, ip=""):
    """Username if the token is valid, unexpired and unrevoked, else None.

    `ip` is only bookkeeping: the address a registered device was last seen
    at. It plays no part in the answer.

    Two generations of token:

      * `user|sid|expiry|hmac` -- what every login mints now. The signature
        proves it; the REGISTRY row is what makes it revocable: no row, or a
        revoked row, and the token is dead however good the signature is.
      * `user|expiry|hmac` -- legacy, from before the registry. Accepted
        until its own expiry so a deploy logs nobody out; it cannot be
        listed or individually revoked (freeze the account to kill it).
    """
    if not token:
        return None
    secret = ensure_secret(config_dir)
    parts = token.split("|")
    if len(parts) == 4:
        username, sid, expiry, sig = parts
        body = f"{username}|{sid}|{expiry}"
        if not hmac.compare_digest(_sign(secret, "sess|" + body), sig):
            return None
        row = _sessions_table(config_dir).get(sid)
        if row is None or row.get("revoked"):
            return None
        if str(row.get("user") or "") != username:
            return None
    elif len(parts) == 3:
        username, expiry, sig = parts
        sid = ""
        if not hmac.compare_digest(_sign(secret, f"{username}|{expiry}"), sig):
            return None
    else:
        return None
    try:
        if int(expiry) < time.time():
            return None
    except ValueError:
        return None
    if username != admin_username(config_dir):
        u = users(config_dir).get(username)
        # Deleted OR disabled: the token is no longer good. Disabling an
        # account must end its live session, not just block the next login.
        if not u or not u.get("enabled", True):
            return None
    if sid:
        touch_seen(sid, config_dir, ip=ip)
    return username
