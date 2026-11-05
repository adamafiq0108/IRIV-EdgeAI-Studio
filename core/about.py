# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""Who made this product. Printed by `python hub_server.py --about` and
`iriv-ctl about`, and shown on the About page (GET /api/hub/about); one place,
so the credit reads the same everywhere."""

from core.version import VERSION

PRODUCT = "IRIV EdgeAI Studio"
DEVELOPER = "adamcytron"
COMPANY = "Cytron Technologies"
COMPANY_LEGAL = "Cytron Technologies Sdn Bhd"
WEBSITE = "https://www.cytron.io"
COPYRIGHT = "Copyright (c) 2026 %s. All rights reserved." % COMPANY_LEGAL


def text():
    return "%s v%s\nDeveloped by %s, %s" % (PRODUCT, VERSION, DEVELOPER, COMPANY)


def info():
    """What the About page shows. The version is the one this process started
    with (core.version), so it is what is RUNNING, not what is on disk."""
    return {"product": PRODUCT, "version": VERSION, "company": COMPANY_LEGAL,
            "website": WEBSITE, "copyright": COPYRIGHT}
