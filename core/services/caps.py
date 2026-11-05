# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
caps.py -- what an account may DO on a page, page by page.

The grants that came before this one answered "which pages can you open":
a category grant (an admin narrowing their own staff) and a page grant
(master deciding which pages exist to be narrowed). Neither could say the
thing a site admin actually needs said -- that they may re-point a camera but
not add one, adjust a zone but not delete it. Opening the page was the whole
permission, so the only two settings were "can wreck it" and "cannot see it".

So a third level, under the page: one capability per thing a page can CHANGE,
each ticked or not by master for one account on one system. Master and
developer are never restricted by it (somebody has to be able to fix a grant
that locked everyone out); an ordinary account holds none of these, exactly as
before -- every route behind them was master-only until now, so nothing an
account could do yesterday is taken away today.

The list here IS the product's vocabulary: a capability not in it cannot be
stored, and every id below is enforced at a route (tests/smoke_caps.py pins
that both ways -- a capability nothing checks is a checkbox that lies, and a
route checking an id nobody can grant is a route nobody can use).
"""

#: page id (core/services/nav.py) -> the things that page can change.
#: Order is the order they appear under the page in the access tree, which is
#: roughly least to most destructive: change what is there, then add, then
#: take away.
CAPABILITIES = (
    ("detection", (
        ("detection.camera.change",
         "Change which camera a slot points at, and its settings"),
        ("detection.camera.add", "Add a camera"),
        ("detection.camera.remove", "Remove a camera"),
        ("detection.camera.activate", "Activate and deactivate a camera"),
        ("detection.zone.edit", "Adjust a zone"),
        ("detection.zone.add", "Draw a new zone"),
        ("detection.zone.delete", "Delete a zone"),
        ("detection.hud", "Change the on-picture readout"),
        # The overlay RULES are NOT here: they are workflow nodes, saved
        # through the workflow document (components/OverlayPanel.jsx), so they
        # follow workflow.edit. A capability of its own would be a checkbox
        # with no route behind it.
        ("detection.capture", "Change snapshot and clip capture"),
        ("detection.power", "Start and stop detection"),
    )),
    ("models", (
        ("models.select", "Choose the model, its classes and tracking"),
        ("models.manage", "Install, upload and delete models"),
    )),
    ("workflow", (
        ("workflow.edit", "Edit the workflow"),
    )),
    ("main", (
        ("main.edit", "Edit the dashboard"),
    )),
    ("footage", (
        ("footage.delete", "Delete footage"),
        ("footage.download", "Download footage"),
        ("footage.export", "Export evidence and email it out"),
    )),
    ("dataset", (
        ("dataset.capture", "Capture training photos and clips"),
        ("dataset.manage", "Delete and export training data"),
    )),
    ("database", (
        ("database.edit", "Create tables and change their rows"),
    )),
    ("blynk", (
        ("blynk.edit", "Change the Blynk link"),
    )),
    ("modbus", (
        ("modbus.edit", "Change fieldbus settings"),
    )),
)

#: Every capability id, in the order above -- the vocabulary a stored grant is
#: written in. A grant naming something outside it is refused rather than kept.
ALL_CAPS = tuple(cap for _page, items in CAPABILITIES for cap, _label in items)

CAP_LABELS = {cap: label for _page, items in CAPABILITIES for cap, label in items}

#: cap -> the page it belongs to, for the tree and for error messages.
CAP_PAGE = {cap: page for page, items in CAPABILITIES for cap, _label in items}

#: What a site admin holds on a system master has never ticked anything for.
#:
#: NOT the empty set, because that would take away on upgrade what the tier was
#: given last week -- an admin who could clear last month's footage would come
#: back to a box that refuses, with nothing on screen saying why. Every default
#: is an ADJUSTMENT: re-point a camera, move a zone, work the system, and deal
#: with the operation's own recordings. Nothing here adds, and nothing here
#: deletes anything but footage.
#: What the Monitor account may do, fixed (core/services/monitor_account.py):
#: start and stop detection, and download footage. Never ticked per account.
MONITOR_CAPS = ("detection.power", "footage.download")

DEFAULT_ADMIN_CAPS = (
    "detection.camera.change",
    "detection.zone.edit",
    "detection.capture",
    "detection.hud",
    "detection.power",
    "footage.delete",
    "footage.export",
    "dataset.capture",
)


def label(cap):
    return CAP_LABELS.get(cap, cap)


def denial(cap):
    """What a refused caller is told. Names the CAPABILITY, not the route --
    "admin only" on a button they can see is the message that starts a support
    call."""
    return (f"{label(cap)} is not allowed on this account - "
            "the box owner sets that in Settings > Accounts.")


def tree():
    """The catalogue as JSON for the accounts page: [{page, caps:[{id,label}]}].
    Sent from the server so the tree cannot drift from what is enforced."""
    return [{"page": page,
             "caps": [{"id": cap, "label": lab} for cap, lab in items]}
            for page, items in CAPABILITIES]
