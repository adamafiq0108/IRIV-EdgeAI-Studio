# -*- coding: utf-8 -*-
"""
secrets.py -- encrypt device bearer tokens at rest (v2.0 #21).

Tokens live in the gitignored slaves.local.toml (NOT the DB -- polling must
keep working during a Postgres outage, so tokens can't hard-depend on PG).
v2.0 encrypts them there with Fernet; the key sits in storage.local.toml
[secrets].key (generated once, masked on read, never logged).

Encrypted values are stored with an "enc:" prefix. decrypt() passes a
plaintext (legacy / no-crypto) value through unchanged, so a device paired
before this landed keeps working and is re-saved encrypted on its next
write (pair/rotate). If `cryptography` is unavailable the module degrades to
plaintext with a one-time warning rather than blocking the master.
"""

import logging

from core import config

logger = logging.getLogger(__name__)
_PREFIX = "enc:"

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None


def available():
    return Fernet is not None


def _get_key(config_dir=None):
    """Fetch or lazily generate the Fernet key in storage.local.toml."""
    if Fernet is None:
        return None
    local = config.load_local("storage", config_dir)
    key = (local.get("secrets") or {}).get("key")
    if not key:
        key = Fernet.generate_key().decode()
        local.setdefault("secrets", {})["key"] = key
        config.save_local("storage", local, config_dir)
        logger.info("[secrets] generated token-encryption key in storage.local.toml")
    return key


def _fernet(config_dir=None):
    key = _get_key(config_dir)
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def encrypt(token, config_dir=None):
    """Return the at-rest form of a token (enc:… when possible)."""
    if not token:
        return token
    f = _fernet(config_dir)
    if f is None:
        # Warned EVERY time, not once per process. A single line at boot is
        # gone by the time anyone pairs a device, and "the tokens on this box
        # are in the clear" is not a fact that should scroll away -- someone
        # reading the log while adding a device has to be able to see it.
        logger.warning("[secrets] cryptography unavailable - this token is "
                       "being stored in PLAINTEXT; `pip install cryptography` "
                       "to encrypt device credentials at rest")
        return token
    return _PREFIX + f.encrypt(token.encode()).decode()


def decrypt(stored, config_dir=None):
    """Return the plaintext token from its at-rest form. Plaintext passes
    through unchanged (legacy / no-crypto)."""
    if not stored or not stored.startswith(_PREFIX):
        return stored
    f = _fernet(config_dir)
    if f is None:
        return stored  # cannot decrypt; return as-is (will fail auth, visibly)
    try:
        return f.decrypt(stored[len(_PREFIX):].encode()).decode()
    except Exception:
        logger.error("[secrets] failed to decrypt a stored token (wrong key?)")
        return ""
