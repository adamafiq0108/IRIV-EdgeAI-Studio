# -*- coding: utf-8 -*-
"""
roothelp.py -- what a card says when a root helper it needs is missing.

The cards that lean on a root-owned helper (the drive popup, the "Storage is
not writable" repair) used to say "this box needs a one-time software update"
(board .21 on 1.0.26, 2026-10-07): true, but it named neither the missing
piece nor who fixes it, and the box had just been updated. Now the sentence
names the helper (or its sudoers rule) and the one fix that applies to THIS
box:

  - the helper ships with scripts/install.sh, so a missing one means the
    installer has to be re-run. No command here: a customer never types one
    (Tok Aba, 2026-10-07); support knows which.
"""

import os

LIB = "/usr/local/lib/irivai"


def fix():
    """The one thing that brings a missing helper to this box."""
    return "re-run the installer (scripts/install.sh) or ask IRIV support"


def missing(helper, rule, purpose):
    """'<what is missing>, so <purpose>; <fix>' for `helper` (a file in LIB)
    and its sudoers rule `rule` (a name in /etc/sudoers.d)."""
    name = os.path.basename(helper)
    if not os.path.isfile(os.path.join(LIB, name)):
        what = "the helper %s is not installed on this box" % name
    else:
        what = ("the permission to run %s (sudoers rule %s) is not on this box"
                % (name, rule))
    return "%s, so %s; %s" % (what, purpose, fix())
