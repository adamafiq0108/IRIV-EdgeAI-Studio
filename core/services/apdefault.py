# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
apdefault.py -- the one-time return of a generated hotspot key to iriv1234.

From 2026-10-05 to 10-07 (v1.0.14..v1.0.19) every box that installed, updated
or booted made its own random access point key (12 characters from
GENERATED_ALPHABET) instead of the default. Tok Aba's rule since 2026-10-07:
the AP password is iriv1234 unless a user set another ("not random, it
should be iriv1234 for AP mode"), so such a key goes back to the default --
ONCE, at the hub's start (which is also the first start after an OTA).

What counts as generated: exactly the generator's format AND no
[ap].user_set (apconf.save_ap writes that flag when a person stores a key).
The format alone is not proof -- the generator wrote no marker, and that
week's Network page re-posted the key it showed -- so a person's own
lowercase 12-character key on a box from before the flag goes back too. Tok
Aba accepted that (2026-10-07); the Inbox notice says what happened and
Settings -> Network sets another key.

Runs once: [ap].migrated_default = 1 is written whatever was found, so a key
stored later is never judged by its shape. The key goes in through
apconf.save_ap (the flag stays absent, the panel redraws) and a hotspot
that is broadcasting is restarted on it the way a page Save does
(apconf.apply_ap), off the boot path. A hotspot that is down is left down:
the boot's own apply_wifi_mode raises the stored key when the mode says so.
"""

import logging
import re
import threading

from core.services import apconf, netconf

logger = logging.getLogger(__name__)

#: The 10-05..07 generator: lower case and digits without the glyphs that look
#: alike on the OLED (0/o, 1/l/i), exactly 12 of them (netconf before bf59c43f).
GENERATED_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
GENERATED_KEY = re.compile(r"[%s]{12}" % GENERATED_ALPHABET)

#: network.local.toml [ap] key that says the migration has run on this box.
DONE_KEY = "migrated_default"

NOTICE_KIND = "ap_default_restored"
NOTICE_TITLE = "The access point password is back to iriv1234"


def looks_generated(key):
    """Whether `key` has the 10-05..07 generator's exact shape."""
    return bool(GENERATED_KEY.fullmatch(str(key or "")))


def _restart_ap(config_dir):
    """Restart a broadcasting hotspot on the stored values, off the boot
    path (ap_enable can wait on NetworkManager for a while)."""
    def run():
        try:
            apconf.apply_ap(config_dir)
            logger.info("[NET] access point restarted on the default key")
        except Exception as exc:                              # noqa: BLE001
            logger.warning("[NET] the access point did not restart on the default "
                           "key (%s); the next restart of the box applies it", exc)
    thread = threading.Thread(target=run, name="ap-default", daemon=True)
    thread.start()
    return thread


def _notice(config_dir):
    try:
        from core.services import inbox                       # noqa: PLC0415
        inbox.notify_admins(
            NOTICE_KIND, NOTICE_TITLE,
            "This box's access point used a password the software generated "
            "on its own (updates from 5 to 7 October 2026). It is back to the "
            "default, iriv1234, which the manual and the front panel show. "
            "Phones or laptops that joined with the old password need to join "
            "again. To use a password of your own, set it under Settings -> "
            "Network.", config_dir, page="settings", tab="network")
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("[NET] could not post the access point notice: %s", exc)


def migrate(config_dir=None, restart=_restart_ap):
    """Run the one-time migration. Returns True when the key was changed.

    Never raises for a missing network file; the caller logs anything else.
    """
    from core import config                                   # noqa: PLC0415
    ap = config.load("network", config_dir).get("ap") or {}
    if ap.get(DONE_KEY):
        return False
    key = str(ap.get("password") or "")
    changed = looks_generated(key) and not ap.get("user_set")
    if changed:
        apconf.save_ap(apconf.ap_ssid(config_dir), netconf.DEFAULT_AP_PASSWORD,
                       config_dir)
    local = dict(config.load_local("network", config_dir))
    block = dict(local.get("ap") or {})
    block[DONE_KEY] = 1
    local["ap"] = block
    config.save_local("network", local, config_dir)
    if not changed:
        return False
    logger.info("[NET] the generated access point key went back to the default")
    _notice(config_dir)
    if netconf.ap_active():
        restart(config_dir)
    return True
