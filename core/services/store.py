# -*- coding: utf-8 -*-
"""
store.py -- The online template store.

Templates are FULL SYSTEM EXPORTS the owner publishes to GitHub: a free repo
(public, no token) and a pro repo (private). A customer box lists both, and
installing one downloads the bundle -- system.json, optional extension code,
and the .hef compiled for this box's Hailo chip -- then creates a ready-to-run
system through the same import path a hand-uploaded export uses.

Trust model
-----------
Every template folder carries a manifest signed with the owner's ed25519 key
(public half ships in the product). The manifest records sha256 for every
other file, HEFs included, so one signature covers the whole bundle. A box
refuses anything that does not verify -- which makes the GitHub repos a dumb
transport, not a trust root.

Paid access is a LICENSE KEY the owner issues at payment time:

    IRIV1.<base64url(payload)>.<base64url(signature)>

The payload holds the device serial, tier, an ABSOLUTE expiry (countdown
starts at purchase, so re-entering an old key cannot restart it), and the
customer's read-only GitHub token encrypted against the serial.

A key may also name the board's SECURE ELEMENT -- the ATECC608's serial and
the public half of the key generated inside it (scripts/dev/provision_atecc.py).
Where it does, activation asks the chip to sign a fresh random challenge and
checks the signature against that public key. The CM5 serial is a number the
operating system reports, so a copied card can be made to report the original's;
the chip's answer cannot be copied at all, because the private half never
leaves the silicon. A key with no `atecc` field behaves exactly as it always
did, which is what keeps every key already issued working.

The honest limit, again: this proves the board is HERE. It does not stop
somebody with root from deleting the code that asks. What it does is make the
copy fail every check that is worth gating -- store downloads, updates, the pro
catalogue -- rather than pass them. Activation
requires internet: expiry is checked against server time (GitHub's Date
header), and the box keeps a high-water mark of the latest server time it has
seen, so winding the clock back cannot revive a dead key. Honest limit: a
paying customer can extract their own token from their own key -- that is
inherent to serverless licensing -- but the token is read-only and scoped to
the store repo alone, so the blast radius is the paid catalogue, never the
product source.

The owner's hub additionally holds the SIGNING key (config/store_signing.pem,
never shipped, never committed) which unlocks the license-issuing routes.
Customer boxes do not have the file, so those routes refuse there.
"""

import base64
import email.utils
import hashlib
import json
import os
import pathlib
import re
import shutil
import threading
import time
import urllib.error
import urllib.request

from core import config
from core.services import ghnet
from core.services import builtin_models
from core.services import models as models_service
from core.services import provision as provision_service

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The store signing public key (ed25519, raw, base64). Baked in so a box with
#: a stripped config still verifies; config/store.toml [signing].pubkey may
#: override for a fork that runs its own store.
PUBKEY_B64 = "SCQyioIUDVVm8Gtke2lyulpQhW+4faZr39GB6WapiGw="

#: Store repos. config/store.toml [repos.free]/[repos.pro] override.
DEFAULT_REPOS = {
    "free": {"owner": "adamafiq0108", "repo": "irivai-store", "branch": "main"},
    "pro": {"owner": "adamafiq0108", "repo": "irivai-store-pro", "branch": "main"},
}

MANIFEST_APP = "irivai-store-template"
INDEX_APP = "irivai-store-index"
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
LICENSE_PREFIX = "IRIV1"
TIERS = ("free", "pro")

#: Catalog cache: memory TTL for the happy path, disk copy for offline boxes.
CATALOG_TTL_S = 300.0

#: Where staged downloads live until every byte has verified. Under the store
#: dir (NVMe when present) because HEFs are tens of MB.
def _cache_dir():
    path = os.path.join(str(config.store_dir()), "store_cache")
    os.makedirs(path, exist_ok=True)
    return path


class StoreError(ValueError):
    """A store failure with an operator-readable message."""


# ---------------------------------------------------------------------------
# Crypto -- probed, not assumed. Without `cryptography` nothing can verify,
# and an unverifiable bundle must not install, so the whole store goes
# read-only rather than trusting blindly.
# ---------------------------------------------------------------------------

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils as asym_utils
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _CRYPTO = True
except ImportError:                                         # pragma: no cover
    _CRYPTO = False


def crypto_available():
    return _CRYPTO


def _pubkey(config_dir=None):
    cfg = (config.load("store", config_dir) or {}).get("signing") or {}
    b64 = str(cfg.get("pubkey") or PUBKEY_B64)
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))


def verify_manifest(manifest_bytes, sig_b64, config_dir=None):
    """The signed manifest, parsed -- or StoreError. Signature covers the raw
    file bytes, so whitespace and key order can never break it."""
    if not _CRYPTO:
        raise StoreError("this box cannot verify store downloads - the "
                         "'cryptography' package is missing")
    try:
        sig = base64.b64decode(str(sig_b64 or "").strip())
        _pubkey(config_dir).verify(sig, manifest_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise StoreError(f"the template's signature does not verify: {exc}")
    try:
        manifest = json.loads(manifest_bytes)
    except ValueError:
        raise StoreError("the template manifest is not valid JSON")
    if manifest.get("app") != MANIFEST_APP:
        raise StoreError("that is not a store template manifest")
    return manifest


def verify_bytes(data, sig_b64, config_dir=None, what="the file"):
    """Check a detached ed25519 signature over raw bytes, or StoreError.

    The store key signs more than manifests now: the built-in template file
    the developer hub publishes for boxes to fetch between releases
    (services/template_updates.py) is sealed the same way, with the same
    public half every box already carries. Raw bytes on purpose, as with the
    manifest -- whitespace and key order can never break it.
    """
    if not _CRYPTO:
        raise StoreError("this box cannot verify store downloads - the "
                         "'cryptography' package is missing")
    try:
        sig = base64.b64decode(str(sig_b64 or "").strip())
        _pubkey(config_dir).verify(sig, data)
    except (InvalidSignature, ValueError) as exc:
        raise StoreError(f"{what}'s signature does not verify: {exc}")


def sign_bytes(data, config_dir=None):
    """A detached ed25519 signature over `data`, base64 -- made with the
    store signing key, which only the owner's hub holds. StoreError where
    the key is absent, so a caller can say so rather than publish something
    every box will refuse."""
    if not can_issue(config_dir):
        raise StoreError("signing needs config/store_signing.pem - this hub "
                         "does not hold the signing key")
    private = serialization.load_pem_private_key(
        pathlib.Path(_signing_key_path(config_dir)).read_bytes(),
        password=None)
    return base64.b64encode(private.sign(data)).decode("ascii")


# ---------------------------------------------------------------------------
# GitHub fetching
# ---------------------------------------------------------------------------

def _repos(config_dir=None):
    cfg = (config.load("store", config_dir) or {}).get("repos") or {}
    out = {}
    for tier, default in DEFAULT_REPOS.items():
        over = cfg.get(tier) or {}
        out[tier] = {k: str(over.get(k) or default[k])
                     for k in ("owner", "repo", "branch")}
    return out


#: Latest server time this box has ever seen, monotonic high-water mark.
#: Winding the local clock back cannot lower it, which is the whole point.
_seen_lock = threading.Lock()


def _note_server_time(headers):
    stamp = None
    try:
        stamp = email.utils.parsedate_to_datetime(headers.get("Date", ""))
    except (TypeError, ValueError):
        return
    if stamp is None:
        return
    now = int(stamp.timestamp())
    with _seen_lock:
        local = dict(config.load_local("store") or {})
        if now > int(local.get("last_seen_time") or 0):
            local["last_seen_time"] = now
            config.save_local("store", local)


def _last_seen_time():
    return int((config.load_local("store") or {}).get("last_seen_time") or 0)


def internet_now():
    """Server time from GitHub, or None when offline. The anchor license
    checks trust instead of the local clock."""
    for url in ("https://api.github.com/", "https://github.com/"):
        try:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "irivai-store"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                _note_server_time(resp.headers)
                stamp = email.utils.parsedate_to_datetime(
                    resp.headers.get("Date", ""))
                if stamp is not None:
                    return int(stamp.timestamp())
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def _open(url, token=None, accept=None):
    # The redirect handler that drops our Bearer header when GitHub bounces us
    # to signed S3 storage lives in core/services/ghnet.py -- the OTA updater
    # needs the same rule, and a security rule with two copies has one copy
    # that is wrong.
    return ghnet.open_url(url, token, accept=accept, agent="irivai-store")


def _repo_file_url(repo, path, token):
    """Where to read one repo file. Tokenless goes through raw.githubusercontent
    (no API rate-limit surprises on the free tier); with a token the contents
    API serves private repos in raw form."""
    if token:
        return (f"https://api.github.com/repos/{repo['owner']}/{repo['repo']}"
                f"/contents/{path}?ref={repo['branch']}")
    return (f"https://raw.githubusercontent.com/{repo['owner']}"
            f"/{repo['repo']}/{repo['branch']}/{path}")


def _fetch_small(repo, path, token=None, limit=4 << 20):
    """A small repo file (index, manifest, sig, system.json, code) as bytes."""
    url = _repo_file_url(repo, path, token)
    with _open(url, token, accept="application/vnd.github.raw+json") as resp:
        _note_server_time(resp.headers)
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise StoreError(f"{path} is larger than this box will read")
    return data


def _fetch_big(url, dest, token=None, expect_sha=None, expect_size=0,
               progress=None):
    """Stream a large file (the HEF) to disk, hashing as it lands. `.part`
    temp + os.replace like every other downloader in the product."""
    part = dest + ".part"
    digest = hashlib.sha256()
    done = 0
    try:
        with _open(url, token) as resp, open(part, "wb") as f:
            _note_server_time(resp.headers)
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                digest.update(chunk)
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, expect_size)
        if expect_sha and digest.hexdigest() != expect_sha:
            raise StoreError("the model file did not match its signed hash - "
                             "refusing it")
        os.replace(part, dest)
    finally:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
    return dest


# ---------------------------------------------------------------------------
# License keys
# ---------------------------------------------------------------------------

def _pat_key(serial):
    """AES key for the token inside a license, derived from the device serial
    so the blob only opens on the hardware it names."""
    return hashlib.sha256(b"iriv-store-pat:" + serial.encode("utf-8")).digest()


def _b64u(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64u(text):
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def parse_license(key_text, config_dir=None):
    """Verify a license key's signature and shape. Says nothing about THIS
    box or the date -- that is check_license's job."""
    if not _CRYPTO:
        raise StoreError("this box cannot verify license keys - the "
                         "'cryptography' package is missing")
    parts = str(key_text or "").strip().split(".")
    if len(parts) != 3 or parts[0] != LICENSE_PREFIX:
        raise StoreError("that does not look like an IRIV license key")
    try:
        payload_bytes, sig = _unb64u(parts[1]), _unb64u(parts[2])
        _pubkey(config_dir).verify(sig, payload_bytes)
        payload = json.loads(payload_bytes)
    except (InvalidSignature, ValueError) as exc:
        raise StoreError(f"the license key does not verify: {exc}")
    if not payload.get("serial") or not payload.get("expires_at"):
        raise StoreError("the license key is missing its serial or expiry")
    return payload


#: An ATECC serial is nine bytes; a P-256 public key is two 32-byte numbers.
#: Both are matched exactly, because a key naming a malformed device is a key
#: that would never match anything and should be refused at issue time.
_ATECC_RE = re.compile(r"^[0-9a-f]{18}$")
_PUBKEY_RE = re.compile(r"^[0-9a-f]{128}$")


def _device_pubkey(hex_text):
    """A 64-byte raw P-256 public key (X then Y) as a verifier object."""
    raw = bytes.fromhex(hex_text)
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"),
        ec.SECP256R1())
    return numbers.public_key()


def prove_hardware(pubkey_hex, config_dir=None):
    """Ask the secure element to prove it is here. (ok, reason).

    A fresh random challenge every time: a fixed one could be answered once,
    recorded, and replayed by a copy that has the recording but not the chip.
    """
    from core.services import atecc                          # noqa: PLC0415
    if not _CRYPTO:
        return False, "this box cannot check a hardware signature"
    if not atecc.present(config_dir):
        return False, "this board has no secure element fitted"
    if not atecc.provisioned(config_dir):
        return False, "this board's secure element holds no key yet"
    challenge = os.urandom(32)
    signature = atecc.sign(challenge, config_dir=config_dir)
    if not signature or len(signature) != 64:
        return False, "the secure element did not answer the challenge"
    try:
        der = asym_utils.encode_dss_signature(
            int.from_bytes(signature[:32], "big"),
            int.from_bytes(signature[32:], "big"))
        _device_pubkey(pubkey_hex).verify(
            der, challenge,
            ec.ECDSA(asym_utils.Prehashed(hashes.SHA256())))
    except (InvalidSignature, ValueError):
        return False, "this board is not the one the key was issued for"
    return True, ""


def _hardware_check(payload, config_dir=None):
    """(ok, reason) for the hardware half of a key.

    A key that names NO secure element passes: every key issued before this
    existed is such a key, and they must go on working.
    """
    from core.services import atecc                          # noqa: PLC0415
    named = str(payload.get("atecc") or "").lower()
    if not named:
        return True, ""
    here = atecc.serial(config_dir)
    if not here:
        return False, "this key is bound to a secure element, and this board has none"
    if here != named:
        return False, "this key belongs to a different board"
    pubkey = str(payload.get("pubkey") or "").lower()
    if not pubkey:
        # The serial matched and there is nothing to challenge with. Weaker
        # than a signature and not pretended otherwise -- an identifier can be
        # reported by patched software, which is the whole reason the key
        # normally carries a public key too.
        return True, ""
    return prove_hardware(pubkey, config_dir)


def _device_serial():
    try:
        from agent.identity import host_serial               # noqa: PLC0415
        return host_serial(fallback="") or ""
    except Exception:                                        # noqa: BLE001
        return ""


def _trusted_now(require_online=False):
    """The latest time this box can honestly claim. Never earlier than the
    server-time high-water mark, so a wound-back clock changes nothing."""
    online = internet_now()
    if online is None and require_online:
        raise StoreError("connect this device to the internet to activate "
                         "the key - activation checks the key against "
                         "server time")
    return max(int(time.time()), _last_seen_time(), online or 0)


def activate_license(key_text, config_dir=None):
    """Verify + bind + store a pasted license key. Online only, on purpose."""
    from core.services import hwcheck                        # noqa: PLC0415
    if hwcheck.enforced(config_dir):
        hw = hwcheck.check(config_dir)
        if not hw["ok"]:
            raise StoreError(hwcheck.message(hw))
    payload = parse_license(key_text, config_dir)
    serial = _device_serial()
    if not serial:
        raise StoreError("this device has no readable serial number - a "
                         "license key cannot be bound here")
    if payload["serial"].lower() != serial:
        raise StoreError("this key was issued for a different device")
    # The hardware half, at the one moment the customer is watching: a key
    # that will never validate should fail HERE, with a reason, rather than
    # activate and then quietly read as invalid for ever after.
    ok, why = _hardware_check(payload, config_dir)
    if not ok:
        raise StoreError(why)
    now = _trusted_now(require_online=True)
    if int(payload["expires_at"]) <= now:
        raise StoreError("this key has already expired - expiry counts from "
                         "the purchase date")
    local = dict(config.load_local("store") or {})
    local["license"] = str(key_text).strip()
    local["activated_at"] = now
    config.save_local("store", local)
    return license_status(config_dir)


def clear_license():
    local = dict(config.load_local("store") or {})
    local.pop("license", None)
    local.pop("activated_at", None)
    config.save_local("store", local)


def _stored_license(config_dir=None):
    text = (config.load_local("store") or {}).get("license") or ""
    if not text:
        return None
    try:
        return parse_license(text, config_dir)
    except StoreError:
        return None


def license_status(config_dir=None):
    """What the UI shows and every pro-tier decision reads. Never returns key
    or token material."""
    out = {"present": False, "valid": False, "tier": "free", "reason": "",
           "expires_at": 0, "customer": "", "bound": False}
    payload = _stored_license(config_dir)
    if payload is None:
        raw = (config.load_local("store") or {}).get("license")
        if raw:
            out.update(present=True, reason="the stored key no longer verifies")
        return out
    out.update(present=True, expires_at=int(payload["expires_at"]),
               customer=str(payload.get("customer") or ""))
    serial = _device_serial()
    if not serial or payload["serial"].lower() != serial:
        out["reason"] = "the key belongs to a different device"
        return out
    if int(payload["expires_at"]) <= _trusted_now():
        out["reason"] = "the key has expired"
        return out
    # Re-proved here, not only at activation: a check that runs once is one
    # line to delete, and this is the call every pro-tier decision goes
    # through. `bound` tells the UI whether this key is hardware-bound at all.
    out["bound"] = bool(payload.get("atecc"))
    ok, why = _hardware_check(payload, config_dir)
    if not ok:
        out["reason"] = why
        return out
    out.update(valid=True, tier=str(payload.get("tier") or "pro"))
    return out


def _pro_token(config_dir=None):
    """The GitHub token from a LIVE license, or None. Expired = no token, so
    a lapsed customer degrades to the free catalogue with no extra checks."""
    if not license_status(config_dir)["valid"]:
        return None
    payload = _stored_license(config_dir)
    enc = _unb64u(str(payload.get("pat_enc") or ""))
    if len(enc) < 13:
        return None
    serial = _device_serial()
    try:
        return AESGCM(_pat_key(serial)).decrypt(
            enc[:12], enc[12:], serial.encode("utf-8")).decode("utf-8")
    except Exception:                                        # noqa: BLE001
        return None


# --- issuing (the OWNER's hub only) ----------------------------------------

def _signing_key_path(config_dir=None):
    return os.path.join(config.root(config_dir), "store_signing.pem")


def can_issue(config_dir=None):
    return _CRYPTO and os.path.exists(_signing_key_path(config_dir))


def issue_license(config_dir, customer, serial, tier, days, github_token,
                  atecc_serial="", device_pubkey=""):
    """Sign a new license key -- the dev page's backend. Refuses without the
    signing key file, which only the owner's hub has."""
    if not can_issue(config_dir):
        raise StoreError("license issuing needs config/store_signing.pem - "
                         "this hub does not hold the signing key")
    serial = str(serial or "").strip().lower()
    if not re.match(r"^[0-9a-f]{8,32}$", serial):
        raise StoreError("the device serial should be the Pi CPU serial "
                         "(hex digits, as shown on the customer's hub)")
    tier = str(tier or "pro")
    if tier not in TIERS:
        raise StoreError(f"tier must be one of {', '.join(TIERS)}")
    try:
        days = int(days)
        if not 1 <= days <= 3660:
            raise ValueError
    except (TypeError, ValueError):
        raise StoreError("duration must be between 1 and 3660 days")
    github_token = str(github_token or "").strip()
    if tier == "pro" and not github_token:
        raise StoreError("a pro key needs the customer's read-only GitHub "
                         "token to bundle")
    # The secure element this key is for. Optional, because a board without
    # one is still a board we sell to -- but a key that names a chip and no
    # public key can only ever compare identifiers, so the two travel
    # together or the operator is told why that is weaker.
    atecc_serial = str(atecc_serial or "").strip().lower()
    device_pubkey = str(device_pubkey or "").strip().lower()
    if atecc_serial and not _ATECC_RE.match(atecc_serial):
        raise StoreError("the secure element serial is 18 hex digits, as the "
                         "customer's hub shows it")
    if device_pubkey and not _PUBKEY_RE.match(device_pubkey):
        raise StoreError("the device public key is 128 hex digits, as "
                         "provision_atecc.py printed it")
    if device_pubkey and not atecc_serial:
        raise StoreError("a device public key needs the secure element "
                         "serial beside it, or there is nothing to bind to")

    private = serialization.load_pem_private_key(
        pathlib.Path(_signing_key_path(config_dir)).read_bytes(), password=None)
    now = int(time.time())
    pat_enc = ""
    if github_token:
        nonce = os.urandom(12)
        ct = AESGCM(_pat_key(serial)).encrypt(
            nonce, github_token.encode("utf-8"), serial.encode("utf-8"))
        pat_enc = _b64u(nonce + ct)
    payload = {"serial": serial, "tier": tier, "entitlements": [],
               "customer": str(customer or "").strip()[:80],
               "issued_at": now, "expires_at": now + days * 86400,
               "pat_enc": pat_enc}
    # Only when there is something to say: an absent field is what every key
    # issued before this looks like, and the check treats the two the same.
    if atecc_serial:
        payload["atecc"] = atecc_serial
    if device_pubkey:
        payload["pubkey"] = device_pubkey
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    key_text = ".".join((LICENSE_PREFIX, _b64u(payload_bytes),
                         _b64u(private.sign(payload_bytes))))

    # The customer ledger -- owner's box only, gitignored, no key material
    # beyond what the owner typed.
    local = dict(config.load_local("licenses", config_dir) or {})
    issued = list(local.get("issued") or [])
    issued.append({"customer": payload["customer"], "serial": serial,
                   "atecc": atecc_serial, "bound": bool(device_pubkey),
                   "tier": tier, "issued_at": now,
                   "expires_at": payload["expires_at"], "revoked": False})
    local["issued"] = issued
    config.save_local("licenses", local, config_dir)
    return {"key": key_text, "expires_at": payload["expires_at"]}


def issued_licenses(config_dir=None):
    return list((config.load_local("licenses", config_dir) or {})
                .get("issued") or [])


def set_revoked(config_dir, serial, issued_at, revoked):
    """Bookkeeping: keys verify offline, so the real teeth are rotating that
    customer's GitHub token. The flag keeps the ledger honest."""
    local = dict(config.load_local("licenses", config_dir) or {})
    issued = list(local.get("issued") or [])
    hit = False
    for row in issued:
        if (str(row.get("serial")) == str(serial)
                and int(row.get("issued_at") or 0) == int(issued_at)):
            row["revoked"] = bool(revoked)
            hit = True
    if not hit:
        raise StoreError("no such issued key")
    local["issued"] = issued
    config.save_local("licenses", local, config_dir)
    return issued


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

_catalog_lock = threading.Lock()
_catalog_cache = {"at": 0.0, "data": None}


def _disk_cache_path():
    return os.path.join(_cache_dir(), "catalog.json")


def _fetch_index(repo, token=None):
    doc = json.loads(_fetch_small(repo, "index.json", token))
    if doc.get("app") != INDEX_APP:
        raise StoreError("that repo does not serve a store index")
    return [t for t in (doc.get("templates") or [])
            if isinstance(t, dict) and KEY_RE.match(str(t.get("key") or ""))]


def _installed_map():
    return dict((config.load_local("store") or {}).get("installs") or {})


def catalog(config_dir=None, force=False):
    """Free + (licensed) pro templates, decorated for the UI. Cached in memory
    briefly and on disk for offline boxes -- browsing should survive a flaky
    uplink even though installing cannot."""
    with _catalog_lock:
        fresh = (time.time() - _catalog_cache["at"]) < CATALOG_TTL_S
        if _catalog_cache["data"] is not None and fresh and not force:
            data = json.loads(json.dumps(_catalog_cache["data"]))
        else:
            data = _build_catalog(config_dir)
            if not data.get("offline"):
                _catalog_cache.update(at=time.time(),
                                      data=json.loads(json.dumps(data)))
    arch = builtin_models.detect_arch()
    installed = _installed_map()
    lic = license_status(config_dir)
    for t in data["templates"]:
        t["compatible"] = bool(arch) and arch in (t.get("archs") or [])
        t["installed_slug"] = str(installed.get(t["key"], {}).get("slug") or "")
        t["locked"] = t.get("tier") == "pro" and not lic["valid"]
    data.update(arch=arch or "", license=lic,
                crypto=crypto_available())
    return data


def _build_catalog(config_dir):
    repos = _repos(config_dir)
    templates, offline, pro_error = [], False, ""
    try:
        templates += [dict(t, tier="free")
                      for t in _fetch_index(repos["free"])]
    except (urllib.error.URLError, OSError, StoreError, ValueError):
        offline = True
    token = _pro_token(config_dir)
    if token and not offline:
        try:
            templates += [dict(t, tier="pro")
                          for t in _fetch_index(repos["pro"], token)]
        except urllib.error.HTTPError as exc:
            # 401/404 = the token no longer opens the repo (expired or
            # rotated). The license itself may still be fine -- say so
            # precisely instead of pretending the customer never paid.
            pro_error = ("your key is valid but the store rejected its "
                         f"access token (HTTP {exc.code}) - contact support")
        except (urllib.error.URLError, OSError, StoreError, ValueError):
            pro_error = "the pro catalogue could not be reached"

    if offline:
        try:
            with open(_disk_cache_path(), "r", encoding="utf-8") as f:
                cached = json.load(f)
            return {"templates": cached.get("templates") or [],
                    "offline": True, "pro_error": pro_error}
        except (OSError, ValueError):
            return {"templates": [], "offline": True, "pro_error": pro_error}

    data = {"templates": templates, "offline": False, "pro_error": pro_error}
    try:
        with open(_disk_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"templates": templates}, f)
    except OSError:
        pass
    return data


# ---------------------------------------------------------------------------
# Install pipeline
# ---------------------------------------------------------------------------

_INSTALLS = {}
_install_lock = threading.Lock()


def install_status(key):
    with _install_lock:
        state = _INSTALLS.get(str(key))
        return dict(state) if state else {"state": "idle"}


def _set(key, **fields):
    with _install_lock:
        _INSTALLS.setdefault(key, {}).update(fields)


def start_install(systems_dir, config_dir, key, name="", owner="",
                  unlimited=False):
    """Kick off a background install. Everything downloads and verifies into
    staging FIRST; the system is created only after the last byte checks out,
    so a dead uplink can never leave half a system behind."""
    key = str(key or "").strip().lower()
    if not KEY_RE.match(key):
        raise StoreError("bad template key")
    if not crypto_available():
        raise StoreError("this box cannot verify store downloads - the "
                         "'cryptography' package is missing")
    with _install_lock:
        if (_INSTALLS.get(key) or {}).get("state") == "running":
            return dict(_INSTALLS[key])

    # Template facts come from the catalog (which repo, which tier).
    entry = next((t for t in catalog(config_dir)["templates"]
                  if t["key"] == key), None)
    if entry is None:
        raise StoreError("that template is not in the store catalogue")
    if entry.get("tier") == "pro" and not license_status(config_dir)["valid"]:
        raise StoreError("that template needs a live license key")
    arch = builtin_models.detect_arch()
    if not arch:
        raise StoreError("no Hailo accelerator detected on this box")
    if arch not in (entry.get("archs") or []):
        raise StoreError(f"this template has no build for this box's "
                         f"{arch} accelerator")
    if not unlimited and provision_service.count_systems(
            systems_dir, config_dir,
            include_developer=False) >= provision_service.MAX_SYSTEMS:
        raise StoreError(f"this box already runs its maximum of "
                         f"{provision_service.MAX_SYSTEMS} systems")

    _set(key, state="running", step="starting", pct=0, error="",
         warnings=[], slug="", url="")
    thread = threading.Thread(
        target=_install, name=f"store-install-{key}",
        args=(systems_dir, config_dir, key, entry, arch,
              str(name or entry.get("name") or key), str(owner or ""),
              unlimited),
        daemon=True)
    thread.start()
    return install_status(key)


def _install(systems_dir, config_dir, key, entry, arch, name, owner,
             unlimited=False):
    try:
        _run_install(systems_dir, config_dir, key, entry, arch, name, owner,
                     unlimited)
    except StoreError as exc:
        _set(key, state="failed", error=str(exc))
    except urllib.error.URLError as exc:
        _set(key, state="failed",
             error=f"download failed: {getattr(exc, 'reason', exc)}")
    except Exception as exc:                                 # noqa: BLE001
        _set(key, state="failed", error=f"install failed: {exc}")


def _run_install(systems_dir, config_dir, key, entry, arch, name, owner,
                 unlimited=False):
    repos = _repos(config_dir)
    tier = str(entry.get("tier") or "free")
    repo = repos["pro"] if tier == "pro" else repos["free"]
    token = _pro_token(config_dir) if tier == "pro" else None
    base = str(entry.get("path") or f"templates/{key}").strip("/")

    # 1. The signed manifest is the ground truth for everything after it.
    _set(key, step="fetching the manifest", pct=2)
    manifest_bytes = _fetch_small(repo, f"{base}/manifest.json", token)
    sig = _fetch_small(repo, f"{base}/manifest.sig", token).decode("ascii")
    manifest = verify_manifest(manifest_bytes, sig, config_dir)
    if manifest.get("key") != key:
        raise StoreError("the manifest names a different template")
    hw = (manifest.get("hardware") or {}).get(arch)
    if not isinstance(hw, dict) or not hw.get("url"):
        raise StoreError(f"this template has no build for {arch}")

    staging = os.path.join(_cache_dir(), key,
                           str(manifest.get("template_version") or 1))
    os.makedirs(staging, exist_ok=True)

    # 2. Every repo file, hash-checked against the signed manifest.
    files = manifest.get("files") or {}
    if "system.json" not in files:
        raise StoreError("the template bundle has no system.json")
    for i, (rel, want) in enumerate(sorted(files.items())):
        _set(key, step=f"downloading {rel}",
             pct=5 + int(20 * i / max(1, len(files))))
        if rel != os.path.normpath(rel).replace("\\", "/") or rel.startswith(
                ("/", "..")) or ".." in rel.split("/"):
            raise StoreError(f"the manifest names an unsafe path: {rel!r}")
        dest = os.path.join(staging, *rel.split("/"))
        if os.path.exists(dest) and _sha256_file(dest) == want.get("sha256"):
            continue                     # staged by an earlier attempt
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        blob = _fetch_small(repo, f"{base}/{rel}", token)
        if hashlib.sha256(blob).hexdigest() != want.get("sha256"):
            raise StoreError(f"{rel} did not match its signed hash")
        with open(dest, "wb") as f:
            f.write(blob)

    # 3. The HEF -- the long download, so it owns most of the progress bar.
    hef_dest = os.path.join(staging, os.path.basename(str(hw.get("file")
                                                          or f"{key}.hef")))
    if not (os.path.exists(hef_dest)
            and _sha256_file(hef_dest) == hw.get("sha256")):
        size = int(hw.get("size") or 0)

        def progress(done, total):
            span = max(total or size, 1)
            _set(key, step="downloading the model",
                 pct=25 + min(60, int(60 * done / span)))

        _fetch_big(str(hw["url"]), hef_dest, token,
                   expect_sha=str(hw.get("sha256") or ""),
                   expect_size=size, progress=progress)

    # 4. Everything verified locally -- NOW build the system.
    _set(key, step="creating the system", pct=88)
    from core.services import templates as templates_service  # noqa: PLC0415
    with open(os.path.join(staging, "system.json"), encoding="utf-8") as f:
        doc = json.load(f)
    created, warnings = templates_service.import_system(
        systems_dir, config_dir, doc, name=name, owner=owner,
        unlimited=unlimited)
    slug = created["slug"]

    try:
        # A created system's package lives in the generated store outside the
        # repo, not under systems/ -- resolve the real root before touching it.
        root = provision_service._pkg_root(systems_dir, slug)
        pkg_dir = os.path.join(str(root), slug)

        # 5. Extension code, only from a bundle that just verified.
        if manifest.get("code"):
            _set(key, step="installing the template code", pct=92)
            ext_dir = os.path.join(pkg_dir, "app", "store_ext")
            if os.path.isdir(ext_dir):
                shutil.rmtree(ext_dir)
            os.makedirs(ext_dir, exist_ok=True)
            for rel in files:
                if rel.startswith("code/"):
                    target = os.path.join(ext_dir, *rel.split("/")[1:])
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copyfile(os.path.join(staging, *rel.split("/")),
                                    target)
            with open(os.path.join(ext_dir, ".manifest_sha256"), "w",
                      encoding="utf-8") as f:
                f.write(hashlib.sha256(manifest_bytes).hexdigest())

        # 6. The model: manifest + weights + labels in the system's own models
        # dir, then selected, exactly as an uploaded model would be.
        _set(key, step="installing the model", pct=95)
        model = manifest.get("model") or {}
        model_name = str(model.get("name") or key).lower()
        if not models_service.NAME_RE.match(model_name):
            model_name = key
        # A name the built-in catalogue also uses would make select() take the
        # catalogue path, which expects the shared per-arch layout instead of
        # this system's flat models dir. The template's copy is per-system by
        # design, so it takes the template's key as its name instead.
        try:
            if model_name in builtin_models._catalogue(pkg_dir):
                model_name = key
        except Exception:                                   # noqa: BLE001
            pass
        labels_rel = str(model.get("labels_file") or "")
        model_manifest = {
            "name": model_name, "task": "detection", "arch": arch,
            "file": f"{model_name}.hef",
            "labels": f"{model_name}.labels" if labels_rel else "",
            "input": {"width": 640, "height": 640},
            "postprocess": "nms_on_chip",
            "confidence": model.get("confidence") or 0.35,
            "classes": [],
            "description": f"Installed by the store template {key}",
        }
        models_service.save_manifest(config_dir, model_manifest,
                                     pkg_dir=pkg_dir)
        models_dir = os.path.join(pkg_dir, "models")
        shutil.copyfile(hef_dest, os.path.join(models_dir,
                                               f"{model_name}.hef"))
        if labels_rel and labels_rel in files:
            with open(os.path.join(staging, *labels_rel.split("/")),
                      encoding="utf-8") as f:
                models_service.save_labels(config_dir, model_name, f.read(),
                                           pkg_dir=pkg_dir)
        try:
            builtin_models.select(root, slug, model_name, config_dir)
        except ValueError as exc:
            warnings.append(f"the model was installed but not selected: {exc}")
    except Exception as exc:                                 # noqa: BLE001
        # The system exists and works from import; code/model extras failing
        # downgrades to a warning rather than deleting the operator's system.
        warnings.append(f"the template installed with a problem: {exc}")

    # 7. Remember what this box installed (feeds the "Installed" badge).
    local = dict(config.load_local("store") or {})
    installs = dict(local.get("installs") or {})
    installs[key] = {"slug": slug,
                     "version": int(manifest.get("template_version") or 1),
                     "at": int(time.time())}
    local["installs"] = installs
    config.save_local("store", local)

    _set(key, state="done", step="done", pct=100, slug=slug,
         url=str(created.get("url") or ""), warnings=list(warnings))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Cloud publishing -- the OWNER's hub pushes a template to the store repos
# ---------------------------------------------------------------------------
#
# The offline path (Move to built-in) needs nothing; this is the ONLINE path:
# take a format's workbench system, build the same signed bundle
# scripts/dev/publish_template.py builds, and push it into the free or pro repo
# through the GitHub contents API. Needs two secrets only the owner's hub
# holds: the signing key (config/store_signing.pem) and a GitHub token with
# write access to the store repos (store_publish.local.toml). Missing either,
# the routes refuse -- a customer box cannot publish no matter who asks.
#
# Model weights are NOT uploaded: cloud publish supports systems running a
# built-in zoo model, whose per-arch HEFs are already hosted by Hailo -- the
# manifest points there. A custom-HEF template still goes through
# publish_template.py on the owner's machine (release assets need gh).

_PUBLISHES = {}
_publish_lock = threading.Lock()

PUBLISH_FILE = "store_publish"


def publish_token(config_dir=None):
    return str((config.load_local(PUBLISH_FILE, config_dir) or {})
               .get("token") or "")


def set_publish_token(token, config_dir=None):
    local = dict(config.load_local(PUBLISH_FILE, config_dir) or {})
    token = str(token or "").strip()
    if token:
        local["token"] = token
    else:
        local.pop("token", None)
    config.save_local(PUBLISH_FILE, local, config_dir)


def publish_ready(config_dir=None):
    return {"token": bool(publish_token(config_dir)),
            "signing": can_issue(config_dir),
            "crypto": crypto_available()}


def publish_status(key):
    with _publish_lock:
        state = _PUBLISHES.get(str(key))
        return dict(state) if state else {"state": "idle"}


def _pset(key, **fields):
    with _publish_lock:
        _PUBLISHES.setdefault(key, {}).update(fields)


def _gh_api(repo, path, token, method="GET", body=None):
    url = (f"https://api.github.com/repos/{repo['owner']}/{repo['repo']}"
           f"/{path}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"User-Agent": "irivai-store",
                 "Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    with ghnet.opener().open(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def _gh_put_file(repo, path, blob, message, token):
    """Create or update one repo file through the contents API."""
    sha = None
    try:
        meta = _gh_api(repo, f"contents/{path}?ref={repo['branch']}", token)
        sha = meta.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    body = {"message": message, "branch": repo["branch"],
            "content": base64.b64encode(blob).decode("ascii")}
    if sha:
        body["sha"] = sha
    _gh_api(repo, f"contents/{path}", token, method="PUT", body=body)


def _hash_remote(url, key, label):
    """sha256 + size of an already-hosted HEF, streamed, never stored. The
    signed manifest must vouch for bytes we actually read."""
    h = hashlib.sha256()
    size = 0
    with _open(url) as resp:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
            _pset(key, step=f"hashing the {label} model ({size >> 20} MB)")
    return h.hexdigest(), size


def start_cloud_publish(systems_dir, config_dir, key, tier="free"):
    """Publish format `key` (from its workbench) to the online store."""
    key = str(key or "").strip().lower()
    tier = "pro" if str(tier).lower() == "pro" else "free"
    ready = publish_ready(config_dir)
    if not ready["crypto"]:
        raise StoreError("the 'cryptography' package is missing")
    if not ready["signing"]:
        raise StoreError("cloud publishing needs config/store_signing.pem - "
                         "this hub does not hold the signing key")
    if not ready["token"]:
        raise StoreError("set a GitHub publish token first (a token with "
                         "write access to the store repos)")
    with _publish_lock:
        if (_PUBLISHES.get(key) or {}).get("state") == "running":
            return dict(_PUBLISHES[key])
    _pset(key, state="running", step="starting", error="", url="")
    threading.Thread(target=_cloud_publish, daemon=True,
                     name=f"store-publish-{key}",
                     args=(systems_dir, config_dir, key, tier)).start()
    return publish_status(key)


def _cloud_publish(systems_dir, config_dir, key, tier):
    try:
        _run_cloud_publish(systems_dir, config_dir, key, tier)
    except StoreError as exc:
        _pset(key, state="failed", error=str(exc))
    except urllib.error.HTTPError as exc:
        _pset(key, state="failed",
              error=f"GitHub refused (HTTP {exc.code}) - check the publish "
                    "token's access to the store repo")
    except Exception as exc:                                 # noqa: BLE001
        _pset(key, state="failed", error=f"publish failed: {exc}")


def _run_cloud_publish(systems_dir, config_dir, key, tier):
    from core.services import templates as templates_service  # noqa: PLC0415
    fmt = provision_service.builtin_formats(config_dir).get(key)
    if fmt is None:
        raise StoreError("no such template")
    slug = provision_service.format_workbench(config_dir).get(key)
    if not slug:
        raise StoreError("this template has no workbench system - open it "
                         "in the editor first")

    # 1. The system export IS the template's content.
    _pset(key, step="exporting the workbench system")
    doc = templates_service.export_system(systems_dir, config_dir, slug)

    # 2. The model must be a zoo one -- its HEFs are already hosted per arch.
    root = provision_service._pkg_root(systems_dir, slug)
    pkg_dir = os.path.join(str(root), slug)
    merged = builtin_models._merged_config(pkg_dir)
    model_name = str((merged.get("detector") or {}).get("model") or "")
    catalogue = builtin_models._catalogue(pkg_dir)
    spec = catalogue.get(model_name)
    if not spec:
        raise StoreError(
            f"the workbench runs {model_name or 'no model'}, which is not a "
            "built-in zoo model - templates with custom weights are "
            "published with scripts/dev/publish_template.py")
    zoo = _read_zoo(pkg_dir)
    stem = os.path.splitext(str(spec.get("file") or f"{model_name}.hef"))[0]

    hardware = {}
    for arch, ver in zoo["vers"].items():
        url = zoo["base_url"].format(ver=ver, arch=arch, name=stem)
        digest, size = _hash_remote(url, key, arch)
        hardware[arch] = {"file": f"{stem}.hef", "sha256": digest,
                          "size": size, "url": url}

    # 3. Manifest, versioned against what the repo already serves.
    repo = _repos(config_dir)[tier]
    token = publish_token(config_dir)
    _pset(key, step="building the manifest")
    version = 1
    try:
        for t in _fetch_index(repo, token):
            if t.get("key") == key:
                version = int(t.get("version") or 0) + 1
    except (urllib.error.URLError, OSError, StoreError, ValueError):
        pass

    system_bytes = json.dumps(doc, indent=2).encode("utf-8")
    manifest = {
        "app": MANIFEST_APP, "version": 1,
        "key": key, "name": fmt["label"],
        "template_version": version, "tier": tier,
        "entitlements": [],
        "min_export_version": int(doc.get("version") or 1),
        "description": fmt.get("hint") or "",
        "author": "IRIV",
        "files": {"system.json": {
            "sha256": hashlib.sha256(system_bytes).hexdigest(),
            "size": len(system_bytes)}},
        "model": {"name": model_name, "task": "detection",
                  "labels_file": "",
                  "confidence": (merged.get("detector") or {})
                  .get("confidence") or 0.35},
        "hardware": hardware,
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

    private = serialization.load_pem_private_key(
        pathlib.Path(_signing_key_path(config_dir)).read_bytes(),
        password=None)
    sig = base64.b64encode(private.sign(manifest_bytes))

    # 4. Push. Manifest LAST: a partially-pushed folder must fail signature
    # checks (old manifest + new files) rather than half-verify.
    base = f"templates/{key}"
    msg = f"Publish {key} v{version} from the hub"
    _pset(key, step="uploading system.json")
    _gh_put_file(repo, f"{base}/system.json", system_bytes, msg, token)
    _pset(key, step="uploading the manifest")
    _gh_put_file(repo, f"{base}/manifest.json", manifest_bytes, msg, token)
    _gh_put_file(repo, f"{base}/manifest.sig", sig, msg, token)

    # 5. index.json upsert.
    _pset(key, step="updating the catalogue index")
    try:
        entries = _fetch_index(repo, token)
    except (urllib.error.URLError, OSError, StoreError, ValueError):
        entries = []
    entries = [t for t in entries if t.get("key") != key]
    entries.append({"key": key, "name": fmt["label"], "version": version,
                    "tier": tier, "summary": (fmt.get("hint") or "")[:200],
                    "archs": sorted(hardware), "has_code": False,
                    "entitlements": [], "path": base})
    entries.sort(key=lambda t: t["key"])
    index_bytes = json.dumps({"app": INDEX_APP, "version": 1,
                              "templates": entries}, indent=2).encode("utf-8")
    _gh_put_file(repo, "index.json", index_bytes, msg, token)

    repo_url = (f"https://github.com/{repo['owner']}/{repo['repo']}"
                f"/tree/{repo['branch']}/{base}")
    _pset(key, state="done", step="done", url=repo_url, version=version,
          tier=tier)


def _read_zoo(pkg_dir):
    """The zoo download scheme from the package's models.toml."""
    try:
        import tomllib                                       # noqa: PLC0415
        with open(os.path.join(pkg_dir, "models.toml"), "rb") as f:
            cfg = tomllib.load(f)
        zoo = cfg.get("zoo") or {}
        base_url, vers = zoo.get("base_url"), dict(zoo.get("vers") or {})
        if base_url and vers:
            return {"base_url": base_url, "vers": vers}
    except (OSError, ValueError):
        pass
    raise StoreError("the workbench package has no [zoo] table in "
                     "models.toml - cannot locate hosted model builds")
