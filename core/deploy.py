# -*- coding: utf-8 -*-
"""
deploy.py -- mounts the owner's tooling, or doesn't.

The developer routes (authoring built-in templates, publishing to the store,
issuing license keys) are product-owner work, not deployment work. A customer
appliance has no use for them, and every route that exists is a route that can
be called -- so on a customer box they are not present at all.

Three independent locks, in the order an attacker meets them:

  1. the code is not on the box    -- scripts/install.sh excludes core/dev/
  2. the box is not a dev box      -- config/deploy.toml mode, defaults customer
  3. the account is not a developer -- core/services/auth.py, per-route

Any one of them is enough. They are separate on purpose: 1 fails if someone
copies a full checkout onto an appliance, 2 fails if someone hands out a
developer account, 3 fails if someone edits deploy.toml. Issuing license keys
needs a fourth thing nobody else has -- config/store_signing.pem, which lives
only on the owner's hub.

This module always exists, so the servers can call it unconditionally; it is
core/dev/ that may be missing, and an ImportError here is the normal, expected
state of a customer install rather than an error worth reporting.
"""

import logging

from core import config

logger = logging.getLogger(__name__)


def mount_dev(app, config_dir=None):
    """Mount the developer routes if this box is entitled to them.

    Returns True when they were mounted. Never raises: a customer box has no
    core/dev/ to import, which is success, not failure.
    """
    if not config.is_dev_box(config_dir):
        return False
    try:
        from core.dev import mount                          # noqa: PLC0415
    except ImportError:
        # Declared a dev box but shipped without the tooling. Worth a line --
        # someone set mode = "dev" and will wonder where the panels went.
        logger.warning("[DEPLOY] mode is dev but core/dev/ is not installed - "
                       "developer routes unavailable")
        return False
    mount(app)
    logger.info("[DEPLOY] developer routes mounted (config/deploy.toml mode=dev)")
    return True
