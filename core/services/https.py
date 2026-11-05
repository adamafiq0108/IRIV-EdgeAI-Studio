# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
https.py -- HTTPS on the LAN with the box's own certificate authority: the
setting, where the keys live, and when a certificate is issued.

Owner decision (2026-10-10): a PER-BOX LOCAL CA. The box makes its root
once, issues itself a server certificate for its own names and addresses
(tlscert.py, tlsnames.py), and the hub serves HTTPS with it beside plain
HTTP (core/tlsserve.py, httpsrun.py). The operator trusts the ROOT once per
client, from the HTTPS card's download. HTTP is unchanged and stays open.

ON UNLESS AN ADMIN TURNS IT OFF (2026-10-21, Tok Aba: "make HTTPS the normal
operating mode"). A fresh install, and every box that updates into this, has
no setting and reads as ON: the hub makes the root and the certificate at its
next start, serves HTTPS, and sends browsers there. Plain HTTP is an explicit
choice -- `enabled = false` in the file, which is what the HTTPS card's switch
writes -- and an explicit choice is never overridden by this default. Before
this release (2026-10-10) it was the other way round: off until an admin
turned it on.

WHERE THE KEYS LIVE: config/https.local.toml, written only by write():
created 0600 (mkstemp, so no umask ever widens it), fsynced, swapped in
atomically. Never in git (config/*.local.toml is ignored), and carried into
the next release by every update like every other *.local.toml
(scripts/apply_update.py carry_config) -- a root lost at an update would
make every client trust the box again. The only thing ever served from it
is the PUBLIC root certificate, and nothing here logs a key.

WHEN A CERTIFICATE IS ISSUED: ensure(), called at enable, at the hub's
start and from the TLS handshake at most every httpsrun.CHECK_EVERY_S. It
re-issues within RENEW_DAYS of expiry, for a certificate another root
signed, a damaged one, or one that lacks a name/address the box now has (a
DHCP lease, a new hostname, the hotspot coming up). Every issue is a NEW
key. The ROOT is made once, or again only on the admin's confirmed request.
"""

import os
import tempfile
import threading
import time

from core import config
from core.services import tlscert, tlsnames

NAME = "https"
DEFAULT_PORT = 8443
#: Between two "make a new one" presses: a script or a stuck button must not
#: be able to spin the CPU on key generation or rewrite the file in a loop.
COOLDOWN_S = {"leaf": 15.0, "root": 60.0}
NEW_ROOT_HINT = ("create a new root certificate from the HTTPS card (every "
                 "client must then trust it again)")


class HttpsError(ValueError):
    """Operator-facing refusal or fault; the sentence is the message."""


class TooSoon(HttpsError):
    """A regenerate inside its cooldown."""


_lock = threading.RLock()
#: gen: bumped at every issue, so the listener knows to load the new one.
_state = {"gen": 0, "last": {"leaf": None, "root": None}}


def path(config_dir=None):
    return config.root(config_dir) / f"{NAME}.local.toml"


def generation():
    return _state["gen"]


def read(config_dir=None):
    """The whole file ({} when absent), or HttpsError when it is damaged.
    The parser's own words are not repeated: they can quote the file."""
    p = path(config_dir)
    try:
        return config.load_local(NAME, config_dir)
    except (ValueError, OSError):
        raise HttpsError(f"the HTTPS file ({p.name}) cannot be read; "
                         + NEW_ROOT_HINT) from None


def write(doc, config_dir=None):
    base = config.root(config_dir)
    base.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".https.", suffix=".tmp", dir=str(base))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(config.toml_text(doc))
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path(config_dir))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    config.load.cache_clear()


def settings(config_dir=None):
    """{enabled, port, redirect_http, plain_http_chosen, error}. Never raises:
    a damaged file reads as OFF, with the reason, so the hub still starts on
    HTTP."""
    try:
        doc, err = read(config_dir), ""
    except HttpsError as exc:
        doc, err = {}, str(exc)
    s = doc.get("https") or {}
    try:
        port = int(s.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    if not 1024 <= port <= 65535:
        port = DEFAULT_PORT
    # Absent means ON; only an explicit false is the plain-HTTP choice. There
    # is ONE switch: while HTTPS is on, browsers are sent to it and a sign-in
    # or token over plain HTTP is refused (tlsserve.py) -- `redirect_http` is
    # reported for the card and the listener, and is never stored separately.
    enabled = s.get("enabled") is not False and not err
    return {"enabled": enabled, "port": port, "redirect_http": enabled,
            "plain_http_chosen": s.get("enabled") is False, "error": err}


def _set(config_dir, **values):
    with _lock:
        doc = dict(read(config_dir))
        section = dict(doc.get("https") or {})
        section.update(values)
        doc["https"] = section
        write(doc, config_dir)


def set_enabled(on, config_dir=None):
    """On makes the root and the certificate first, so the setting never
    says on while there is nothing to serve."""
    with _lock:
        if on:
            ensure(config_dir)
        _set(config_dir, enabled=bool(on))


def check_port(value, running=None, config_dir=None):
    """The HTTPS port as it will be used, or HttpsError. The hubport rules
    (range, the box's services, a system's ports, a bind test), except
    that DEFAULT_PORT is reserved FOR this."""
    from core.services import hubport                          # noqa: PLC0415
    try:
        p = hubport.parse(value)
    except hubport.PortError as exc:
        raise HttpsError(str(exc)) from None
    if p in hubport.claimed_ports(config_dir) or p == hubport.running_port():
        raise HttpsError(f"{p} is the dashboard's HTTP port - choose another")
    owner = "" if p == DEFAULT_PORT else hubport.reserved_by(p, config_dir)
    if owner:
        raise HttpsError(f"{p} is used by {owner} - choose another")
    if p != running and hubport.listening(p):
        raise HttpsError(f"something on this box already listens on {p} "
                         "- choose another")
    return p


def set_port(value, running=None, config_dir=None):
    p = check_port(value, running, config_dir)
    _set(config_dir, port=p)
    return p


def _label():
    from core.services import hostinfo                         # noqa: PLC0415
    try:
        serial = (hostinfo.serial_number() or "")[-8:]
    except Exception:                                          # noqa: BLE001
        serial = ""
    return serial or tlsnames.hostname()


def _load_pair(entry):
    """(cert, key) from a [ca]/[leaf] table, or ValueError."""
    entry = entry or {}
    cert, key = tlscert.load_cert(entry.get("cert")), tlscert.load_key(entry.get("key"))
    if not tlscert.key_matches(cert, key):
        raise ValueError("key does not match")
    return cert, key


def _clock_set(now):
    """Only when something is about to be ISSUED: a box whose clock reads
    1970 still serves the certificate it already has."""
    if now.year < tlscert.MIN_YEAR:
        raise HttpsError(f"the box's clock reads {now.year}, so a certificate "
                         "made now would be refused; let the box reach the "
                         "internet (or set its time) and try again")


def ensure(config_dir=None, names=None, ips=None, now=None,
           new_leaf=False, new_root=False):
    """Issue what is due. {issued, reason}; HttpsError when it cannot (a
    damaged or expired root -- fail closed, never a silent new root -- or
    a clock that is not set). names/ips/now are for tests."""
    now = now or tlscert.now_utc()
    if names is None:
        names, ips = tlsnames.current()
    with _lock:
        try:
            doc = dict(read(config_dir))
        except HttpsError:
            if not new_root:
                raise
            doc = {}                         # a new root replaces a damaged file
        ca = None
        if doc.get("ca") and not new_root:
            try:
                ca, ca_key = _load_pair(doc["ca"])
            except ValueError:
                raise HttpsError("the root certificate on this box is damaged; "
                                 + NEW_ROOT_HINT) from None
            if tlscert.not_after(ca) <= now:
                raise HttpsError("the root certificate has expired; " + NEW_ROOT_HINT)
        reason = ""
        if ca is None:
            _clock_set(now)
            ca, ca_key = tlscert.make_ca(_label(), now=now)
            doc["ca"] = {"cert": tlscert.cert_pem(ca), "key": tlscert.key_pem(ca_key),
                         "created": now.isoformat(timespec="seconds")}
            reason = "new root"
        elif new_leaf:
            reason = "made again on request"
        else:
            try:
                leaf, _key = _load_pair(doc.get("leaf"))
                reason = tlscert.reissue_reason(leaf, ca, names, ips, now)
            except ValueError:
                reason = "no certificate yet" if not doc.get("leaf") else "damaged"
        if not reason:
            return {"issued": False, "reason": ""}
        _clock_set(now)
        cert, key = tlscert.make_leaf(ca, ca_key, names, ips, now)
        doc["leaf"] = {"cert": tlscert.cert_pem(cert), "key": tlscert.key_pem(key),
                       "issued": now.isoformat(timespec="seconds"), "reason": reason}
        write(doc, config_dir)
        _state["gen"] += 1
        return {"issued": True, "reason": reason}


def regenerate(kind, config_dir=None, clock=time.monotonic):
    """The card's "new certificate" ("leaf") or confirmed "new root"."""
    if kind not in COOLDOWN_S:
        raise HttpsError("say which: leaf or root")
    with _lock:
        last = _state["last"][kind]
        if last is not None and clock() - last < COOLDOWN_S[kind]:
            wait = int(COOLDOWN_S[kind] - (clock() - last)) + 1
            raise TooSoon(f"one was just made - try again in {wait} s")
        out = ensure(config_dir, new_leaf=kind == "leaf", new_root=kind == "root")
        _state["last"][kind] = clock()
        return out


def material(config_dir=None):
    """(cert PEM, key PEM) the listener serves, or HttpsError."""
    entry = read(config_dir).get("leaf") or {}
    try:
        _load_pair(entry)
    except ValueError:
        raise HttpsError("this box has no usable HTTPS certificate yet") from None
    return entry["cert"], entry["key"]


def root_cert(config_dir=None):
    """The root as a certificate object (PUBLIC), or None."""
    entry = read(config_dir).get("ca") or {}
    try:
        return tlscert.load_cert(entry.get("cert")) if entry.get("cert") else None
    except ValueError:
        raise HttpsError("the root certificate on this box is damaged; "
                         + NEW_ROOT_HINT) from None
