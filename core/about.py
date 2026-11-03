"""Who made this product. Printed by `python hub_server.py --about` and
`iriv-ctl about`; one place, so the credit reads the same everywhere."""

from core.version import VERSION

PRODUCT = "IRIV Edge AI"
DEVELOPER = "adamcytron"
COMPANY = "Cytron Technologies"


def text():
    return "%s v%s\nDeveloped by %s, %s" % (PRODUCT, VERSION, DEVELOPER, COMPANY)
