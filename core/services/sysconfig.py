"""Box-wide switches: the settings that are about the BOX.

Everything else in config is about a SYSTEM (a camera and its rules), an
ACCOUNT (who may open what) or a PAGE (how something is drawn). These are
about the appliance itself -- what it is allowed to do at all -- and they
were scattered across the pages that happened to use them: auto logout under
Accounts, whether email works at all nowhere, inferred from whether anybody
had filled a mailbox in.

One page, one file, and room for the next one. A switch here answers "may
this box do X" and nothing narrower; anything that needs a system slug or a
username belongs in that system's or that account's config, not here.

Read through `settings()`, written through the setters -- never by reaching
into the file -- so a default added below reaches every caller at once.
"""
import os

from core import config

#: Every box-wide switch, with the answer a box gives when nobody has said.
#:
#: `email`: whether this appliance uses email AT ALL -- the recovery links,
#: the reset requests, the addresses offered on accounts. Defaults ON so that
#: every box already in the field keeps the behaviour it has; turning it off
#: is a deliberate act by somebody who knows the box has no mail path, and it
#: closes the emailed way back in for every account at once (the Accounts page
#: says so, the way it does for a single address).
#:
#: `chip_balance`: the Hailo load balancer (testing) -- split the AI chip's
#: frame rate fairly between every camera on this box, weighted by what each
#: model costs (the detection app's app/chipshare.py). Defaults OFF: off is
#: the fixed pacing rule every box has today. Box-wide because the chip is:
#: one system cannot be fair on its own. The apps read it live from this
#: directory (see export_location), so a flip lands within ~10 s without a
#: restart.
#:
#: `share_polls`: the shared poll cache (testing) -- compute an expensive
#: read (the dashboard, the event list, the meters) once and hand it to every
#: open page entitled to the identical answer, instead of once per tab
#: (core/services/pollshare.py). Defaults OFF: off is today's behaviour,
#: byte for byte. Box-wide because the hub and every master share the same
#: tabs. Read on every request through the stamp-checked config.load, so a
#: flip lands at once in every process, no restart.
#:
#: `wifi_loss_hotspot` (v1.0.37-38) is GONE with the Wi-Fi loss fallback it
#: switched (Tok Aba, 2026-10-14: the hotspot never comes up by itself). A
#: box that still has it in system.local.toml is fine: settings() reads only
#: the keys below, so the old value is ignored, never an error.
#:
#: `auto_logout` is deliberately NOT here. It lives with the session policy in
#: auth.local.toml [session], which is what stamps an expiry onto a token, and
#: moving the store to group a page would mean migrating every box that has
#: already set it. The System page shows the two together; where each is kept
#: is the storage's business.
DEFAULTS = {
    "email": True,
    "chip_balance": False,
    "share_polls": False,
}

#: The environment variable every process the hub or a master starts
#: inherits: the PRODUCT config directory, so a detection app (which imports
#: nothing from core/ and is never told this directory) can read
#: [system] chip_balance. Mirrors app/chipshare.CONFIG_ENV (pinned equal by
#: smoke_chipshare). A path travels, never a value from inside the file.
BOX_CONFIG_ENV = "IRIVAI_BOX_CONFIG"


def settings(config_dir=None):
    """Every switch, defaults filled in."""
    saved = dict(config.load("system", config_dir).get("system") or {})
    saved.update(dict(config.load_local("system", config_dir).get("system") or {}))
    return {key: _coerce(default, saved.get(key, default))
            for key, default in DEFAULTS.items()}


def _coerce(default, value):
    """A saved value read back as the TYPE its default is. A switch written by
    hand as "false" is a non-empty string, which is true -- and a box whose
    email switch reads the opposite of what the file says is the worst kind of
    setting."""
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    return value


def email_enabled(config_dir=None):
    """Does this box use email at all?"""
    return bool(settings(config_dir)["email"])


def set_email_enabled(on, config_dir=None):
    """Turn the box's use of email on or off. Master or developer -- the
    CALLER enforces that, as with every other recovery-path setting: this
    closes the emailed way back in for every account at once."""
    return bool(_set("email", bool(on), config_dir)["email"])


def chip_balance(config_dir=None):
    """Is the Hailo load balancer on for this box?"""
    return bool(settings(config_dir)["chip_balance"])


def set_chip_balance(on, config_dir=None):
    """Turn the Hailo load balancer on or off. Master or developer -- the
    CALLER enforces that. Nothing is restarted: the apps re-read the switch
    on their 5 s balance check."""
    return bool(_set("chip_balance", bool(on), config_dir)["chip_balance"])


def share_polls(config_dir=None):
    """Is the shared poll cache on? Asked on EVERY shared read, so it takes
    the cached path: config.load re-parses only when system.toml or
    system.local.toml changes on disk (two os.stat calls otherwise), where
    settings() also re-reads the local file each time."""
    raw = (config.load("system", config_dir).get("system") or {}).get(
        "share_polls", DEFAULTS["share_polls"])
    return bool(_coerce(DEFAULTS["share_polls"], raw))


def set_share_polls(on, config_dir=None):
    """Turn the shared poll cache on or off. Master or developer -- the
    CALLER enforces that. Every process sees it on its next request; off
    also empties each one's cache then (pollshare.lookup)."""
    return bool(_set("share_polls", bool(on), config_dir)["share_polls"])


def export_location(config_dir=None):
    """Tell every process this one starts where the box switches live (the
    same move as mqtt_broker.export_login). Called by the hub and by every
    master at start; an app started before it simply reads the switch as
    off."""
    os.environ[BOX_CONFIG_ENV] = str(config.root(config_dir).resolve())


def _set(key, value, config_dir=None):
    if key not in DEFAULTS:
        raise ValueError(f"unknown system setting {key!r}")
    local = config.load_local("system", config_dir)
    local.setdefault("system", {})[key] = value
    config.save_local("system", local, config_dir)
    return settings(config_dir)
