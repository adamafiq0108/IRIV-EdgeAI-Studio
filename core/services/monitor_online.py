# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
monitor_online.py -- keep the systems the Monitor may open ONLINE.

A central program that watches the box through the Monitor account must not
find a system gone. Every system the admin allowed for it is therefore kept up
in two ways (the second is this file):

  * Power off is refused for it (routes/hub.py power_system) -- the one button
    that stopped a master and kept it stopped.
  * its MASTER (the process that serves the system's pages) is brought back if
    it is not answering: once when the hub starts and then every POLL_S
    seconds. Detection is NOT started here -- whether the camera and the model
    run is the Monitor's own decision (it may start and stop detection), and a
    system's pages being up costs nothing.

Never raises, never touches a system that is not allowed for the Monitor, and
waits while a platform update is swapping the code (the applier owns that
window).
"""

import logging
import threading
import time
import urllib.parse

from core.services import monitor_account

log = logging.getLogger(__name__)

#: How often the hub looks. A dropped master is back within about this long.
POLL_S = 30


def keep_list(masters, config_dir=None):
    """The slugs whose masters must be up, in the order the box lists them."""
    return [slug for slug in (masters or {})
            if monitor_account.keeps_online(slug, config_dir)]


def ensure_online(masters, repo_dir, config_dir=None, answers=None, spawn=None,
                  local=None):
    """Spawn every kept system's master that is not answering. Returns the
    slugs it started. `answers(url)`, `spawn(repo_dir, slug, port)` and
    `local(host)` are the provisioning helpers, injectable for the tests."""
    if answers is None or spawn is None or local is None:
        from core.services import provision as provision_service  # noqa: PLC0415
        answers = answers or provision_service._master_answers
        spawn = spawn or provision_service.spawn_master
        local = local or provision_service.is_local_master
    started = []
    for slug in keep_list(masters, config_dir):
        url = (masters.get(slug) or {}).get("url") or ""
        parsed = urllib.parse.urlparse(url)
        try:
            # A master on another host is that host's to keep up.
            if not parsed.port or not local(parsed.hostname):
                continue
            if answers(url):
                continue
            spawn(repo_dir, slug, parsed.port)
            started.append(slug)
        except Exception as exc:                              # noqa: BLE001
            log.warning("[MONITOR] could not bring %s online: %s", slug, exc)
    return started


def start_keepalive(app, repo_dir, config_dir=None):
    """The hub's background loop. `app.state.masters` is read fresh each round
    (systems come and go)."""
    def loop():
        while True:
            time.sleep(POLL_S)
            try:
                got = ensure_online(getattr(app.state, "masters", {}) or {},
                                    repo_dir, config_dir)
                if got:
                    log.info("[MONITOR] brought back online: %s", ", ".join(got))
            except Exception:                                   # noqa: BLE001
                log.exception("[MONITOR] keep-online round failed")

    threading.Thread(target=loop, name="monitor-online", daemon=True).start()
