# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
envfile.py -- the .env file: install-time defaults in one place.

WHAT GOES WHERE
  .env             the defaults a box starts from (hostname, hotspot address
                   and key, store paths, the hardware check). Read ONCE, at
                   start, into the process environment -- so every process the
                   hub starts (masters, apps) inherits the same values.
  config/*.toml    structure and safe defaults (committed).
  config/*.local.toml
                   what is SAVED on the box from the pages (gitignored). A
                   value saved on a page always wins over .env: .env says what
                   a box starts as, not what it must stay.

WHICH FILE (first that exists):
  $IRIV_ENV_FILE           an explicit path
  <repo>/.env              a developer checkout, or a box run from its folder
  /etc/irivai/studio.env   an installed box: outside the release folder, so an
                           update (a new release folder) keeps it. install.sh
                           writes it once from .env or .env.example.

RULES: KEY=VALUE per line, `#` comments, optional `export `, values may be
quoted. A variable already in the environment is NOT overwritten -- a value
set by systemd or the shell is the more specific one.
"""

import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Never taken from a .env file: the device lock (hwcheck.py). A file anyone
#: can copy and edit is not where the check may be switched off; a developer
#: still can, deliberately, in the real environment or system.local.toml.
NOT_FROM_FILE = ("IRIV_HW_CHECK", "IRIV_HW_WAIT_S")
SYSTEM_ENV = pathlib.Path("/etc/irivai/studio.env")

#: The file that was loaded (None if none), for diagnostics.
loaded_from = None


def candidates():
    explicit = os.environ.get("IRIV_ENV_FILE")
    out = [pathlib.Path(explicit)] if explicit else []
    return out + [REPO_ROOT / ".env", SYSTEM_ENV]


def parse(text):
    """{key: value} from .env text."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # An unquoted value ends at an inline comment.
            hash_at = value.find(" #")
            if hash_at >= 0:
                value = value[:hash_at].rstrip()
        out[key] = value
    return out


def load():
    """Load the first .env that exists into os.environ (never overwriting).
    Safe to call more than once; returns the path loaded, or None."""
    global loaded_from
    if loaded_from is not None:
        return loaded_from
    for path in candidates():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for key, value in parse(text).items():
            # Blank means "the built-in default": leave the variable unset
            # rather than set to "", which reads as an (empty) path.
            if value != "" and key not in NOT_FROM_FILE:
                os.environ.setdefault(key, value)
        loaded_from = path
        return path
    return None


def get(key, default=""):
    """A value from the environment (after load()), stripped; `default` when
    unset or blank."""
    load()
    value = os.environ.get(key, "").strip()
    return value if value else default
