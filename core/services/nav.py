# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
nav.py -- Builds a system's nav from reported capabilities.

This is the mechanism that lets one dashboard serve every system. Hardcoding a
nav per system is how one dashboard becomes N dashboards: the moment two
systems differ -- one has email, the next doesn't -- a hardcoded nav needs a
branch. Here that difference is just a capability the devices do or don't
report.
"""

#: page id -> (label, capability required). Empty capability = always shown.
#:
#: Order follows the way a system is actually set up: pick a model, point it at
#: a camera, draw the zones, wire the workflow -- then look at what came out.
#: Account and audit pages are NOT here: the hub owns platform administration
#: now that master/slave is gone. Logs are the exception, in ADMIN_PAGES below.
#:
#: The nav's six CATEGORIES, keyed for storage. The frontend groups its rail
#: from the same six (frontend/src/components/nav-sections.js) -- the two
#: copies are pinned equal by tests/smoke_sections.py, because a category that
#: exists on one side only is a grant nobody can see or a rail group nobody
#: can hide. The keys are what an account's per-system visibility grant is
#: stored under (users.local.toml [users.<name>.sections]), so they must stay
#: stable even if a label is reworded.
SECTION_PAGES = {
    "overview":     ("main", "analytics", "builder", "footage"),
    "ai_config":    ("models", "detection", "workflow", "zones"),
    "data":         ("database",),
    "ai_train":     ("dataset", "dataset_library"),
    "integration":  ("blynk", "modbus"),
    "diagnostics":  ("events", "logs"),
}
SECTION_KEYS = tuple(SECTION_PAGES)
#: Categories only MASTER sees, whatever the grants say. Blynk and the Modbus
#: server wire the box to somebody else's controller: a control for the box
#: owner, not for a site's admin. Hidden here rather than by a grant, because
#: a grant is something master can widen back.
MASTER_SECTIONS = ("integration",)
MASTER_PAGES = tuple(p for key in MASTER_SECTIONS for p in SECTION_PAGES[key])
SECTION_LABELS = {
    "overview": "Overview", "ai_config": "AI Configuration", "data": "Data",
    "ai_train": "AI Preparation", "integration": "Integration",
    "diagnostics": "Diagnostics",
}
_PAGE_SECTION = {p: key for key, pages in SECTION_PAGES.items() for p in pages}
#: Public name for the same map -- auth.visible_pages needs to know which
#: pages a category claims, and which are claimed by none.
PAGE_SECTION = _PAGE_SECTION

PAGES = (
    ("main",       "Dashboard",     ""),
    # Zones are drawn ON Camera Setup now (a Zones tab over the same feed):
    # one page for aiming the camera and marking what it watches. The /zones
    # route still answers -- it lands on that tab -- but the nav entry would
    # be a second door to the same room.
    ("detection",  "Camera Setup",  ""),
    ("workflow",   "Workflow",      ""),
    ("dataset",    "Dataset",       ""),
    ("dataset_library", "Dataset Library", ""),
    ("analytics",  "Analytics",     ""),
    ("footage",    "Footage",       ""),
    ("events",     "Events Logs",   ""),
    # This system's own tables -- their shape AND their rows, in one place.
    # There was briefly a second "Data Tables" page for the rows; splitting
    # "what the table is" from "what is in it" meant every answer needed two
    # pages, so it is one tool now. (The Dataset entries above are training
    # photos and clips, which is a different thing entirely.)
    ("database",   "Database",      ""),
)

#: Admin-only pages (v2.0), appended after the standard set for the admin
#: account only. Same (page, label, capability) shape; route names follow the
#: system.<page> convention so _nav.html needs no change.
#: Credentials moved to the hub -- platform administration lives in one place.
#:
#: The Dashboard Builder is NOT here. It is reached from the Dashboard's own
#: "Edit dashboard" button, the same way the Analytics Builder is reached from
#: Analytics -- an editor belongs next to the thing it edits, and a second
#: entry in the nav only invited the question of which one to use. The /builder
#: route is unchanged, so existing links and bookmarks still work.
#: Logs sit here, on each system, because a log is per-PROCESS
#: (core/services/logs.py): this master's detection errors, device timeouts
#: and 5xx are in its own memory, and its app's failures are in that system's
#: data dir (core/services/log_collect.py). Each system's page shows that
#: system only, and the hub's own Logs page shows the hub only (Tok Aba,
#: 2026-10-14); the hub's Systems cards link to each system's page.
ADMIN_PAGES = (
    ("models",      "AI Models",   ""),
    ("blynk",       "Blynk IoT",   ""),
    # A fieldbus is an admin setting: it decides what this box puts on
    # a wire other machines act on, which is not an operator's call.
    ("modbus",      "Modbus",      ""),
    ("logs",        "System Log",  ""),
)


#: Every page id the product has, in nav order. The vocabulary a per-page
#: grant is written in -- a grant naming something not in here is a grant on
#: nothing, so it is refused rather than stored.
ALL_PAGES = tuple(p for p, _l, _c in PAGES) + tuple(p for p, _l, _c in ADMIN_PAGES)


def build(system, capabilities, active="", is_admin=False, deploy_role="standalone",
          sections=None, pages=None):
    """Return [{page, label, active}] for a system's top nav. `deploy_role` is
    accepted for callers' sake; every remaining page suits both roles.

    `sections` is this viewer's category grant for THIS system: None means
    everything (master, and any account with no stored grant), a
    set of SECTION_KEYS means only pages in those categories. A page no
    category claims stays visible -- an unmapped page must appear, not vanish.

    `pages` is master's finer grant: None means everything, a set of page ids
    means only those. Both apply -- an admin grants categories, master decides
    which pages exist to be granted -- so a page has to survive BOTH to show.
    Callers normally pass the already-intersected set from
    auth.visible_pages(); the two arguments are kept separate because a page
    no category claims is visible to a category grant and NOT visible to a
    page grant that omits it, and collapsing them would lose that."""
    def visible(page):
        # Master-only pages first: no grant reaches them. See
        # MASTER_SECTIONS.
        if page in MASTER_PAGES and not is_admin:
            return False
        if pages is not None and page not in pages:
            return False
        if sections is None:
            return True
        key = _PAGE_SECTION.get(page)
        return key is None or key in sections

    items = [
        {"page": page, "label": label, "active": page == active}
        for page, label, need in PAGES
        if (not need or need in capabilities) and visible(page)
    ]

    if is_admin:
        items += [
            {"page": page, "label": label, "active": page == active}
            for page, label, need in ADMIN_PAGES
            if (not need or need in capabilities) and visible(page)
        ]

    # A system package may add pages core/ genuinely cannot serve. They append
    # after the standard set so the common nav stays in a familiar order.
    for extra in system.extra_pages:
        items.append({
            "page": extra.get("page"),
            "label": extra.get("label"),
            "active": extra.get("page") == active,
        })
    return items
