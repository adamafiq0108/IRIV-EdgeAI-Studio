# -*- coding: utf-8 -*-
"""
core/dev -- the product owner's tooling. Absent from a customer install.

Everything here authors or distributes the PRODUCT's own content: built-in
templates, store publishes, license keys. A deployment consumes that content;
it never produces it, so none of this belongs on a customer box.

Never import this package directly. Go through core/deploy.py's mount_dev(),
which checks entitlement first and treats a missing core/dev/ as the normal
state of a customer install rather than an error.

Adding a route here is the whole wiring: put the router in a module, add it to
ROUTERS below, and it mounts on dev boxes and is excluded from customer ones by
the same rule as everything else -- no server file changes, no install-script
changes.
"""

from core.dev.formats import router as formats_router
from core.dev.store import router as store_router
from core.dev.template_copy import router as template_copy_router
from core.dev.template_media import router as template_media_router
from core.dev.templates import router as templates_router

#: Mounted in order. Each is already developer-gated per route -- the mount
#: check is the outer lock, not the only one, because a route that trusts the
#: mount would be a route that trusts a config file.
ROUTERS = (formats_router, templates_router, store_router,
           template_media_router, template_copy_router)


def mount(app):
    for router in ROUTERS:
        app.include_router(router)
