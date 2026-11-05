# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
secrets_at_rest.py -- what this box keeps on disk that is secret, and that it
is kept safely. Run once at every hub and master start (hub_server.py,
master_server.py), so a box does not depend on somebody logging in, or running
a script, for the cleartext to leave the disk.

  * MASTER PASSWORD. auth.set_master_password has stored it as a salted PBKDF2
    hash for a long time, and a box upgraded from before that still has the
    old cleartext `password` key in auth.local.toml until somebody signs in
    with it (auth._verify_master retires it then). A box nobody signs in to
    would keep it for ever. Here the cleartext is hashed and removed at start:
    nothing is guessed and nobody is locked out, because the stored value IS
    the password. A cleartext shorter than the 8-character rule is hashed
    exactly as it is (the rule applies to NEW passwords, never to one already
    in use); the first-run gate still asks the owner to change a shipped one.

  * FILE MODES. Every config/*.local.toml holds something private (the session
    signing secret, tokens, hashes, mail and bot credentials). They are
    written 0600; any that a copy, a restore or a hand edit left readable by
    others is put back to 0600 here. The signing secret itself cannot be
    hashed -- it is an HMAC key -- so its protection is the file mode and
    that no route or diagnostic reads the file back (see
    tests/smoke_secrets_at_rest.py).

Never raises: a read-only config must not stop a box from starting.
"""

import logging
import os
import secrets
import stat

from core import config
from core.services import auth as auth_service

log = logging.getLogger(__name__)


def migrate_master_password(config_dir=None):
    """Hash a leftover cleartext master password. True when it changed."""
    with auth_service._WRITE_LOCK:
        local = config.load_local("auth", config_dir)
        master = local.get("master") or {}
        legacy = str(master.get("password") or "")
        if not legacy:
            return False
        if not master.get("password_hash"):
            salt = secrets.token_hex(16)
            master["salt"] = salt
            master["password_hash"] = auth_service._hash(legacy, salt)
        master.pop("password", None)
        local["master"] = master
        auth_service._save_local("auth", local, config_dir)
    return True


def tighten_modes(config_dir=None):
    """Put every config/*.local.toml back to owner-only. Returns the names
    changed."""
    root = config.root(config_dir)
    changed = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return changed
    for name in names:
        if not name.endswith(".local.toml"):
            continue
        path = os.path.join(root, name)
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
            if mode & 0o077:
                os.chmod(path, 0o600)
                changed.append(name)
        except OSError:
            continue
    return changed


def run(config_dir=None):
    """Both, quietly; logs what it did. Never raises."""
    try:
        if migrate_master_password(config_dir):
            log.warning("[SECURITY] the master password was stored in clear; "
                        "it is now a hash and the cleartext is gone")
        for name in tighten_modes(config_dir):
            log.warning("[SECURITY] %s was readable by others; set to owner-only",
                        name)
    except Exception:                                           # noqa: BLE001
        log.exception("[SECURITY] could not check the secrets on disk")
