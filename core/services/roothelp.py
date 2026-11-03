# -*- coding: utf-8 -*-
"""
roothelp.py -- what a card says when a root helper it needs is missing.

The cards that lean on a root-owned helper (the drive popup, the "Storage is
not writable" repair) used to say "this box needs a one-time software update"
(board .21 on 1.0.26, 2026-10-07): true, but it named neither the missing
piece nor who fixes it, and the box had just been updated. Now the sentence
names the helper (or its sudoers rule) and the one fix that applies to THIS
box:

  - the box has the root step (/usr/local/lib/irivai/root-steps.py, from
    scripts/root_steps.py): every update installs the helpers by itself, so
    the next software update brings it;
  - it does not (installed before the root step existed, then updated over
    the air): the one-time maintenance step, which a person with the box's
    administrator login runs once ever. No command here: a customer never
    types one (Tok Aba, 2026-10-07); the script is named so the installer or
    support knows which.
"""

import os

LIB = "/usr/local/lib/irivai"
ROOT_STEPS = os.path.join(LIB, "root-steps.py")


def auto_installs():
    """True when every update keeps this box's root helpers current."""
    return os.path.isfile(ROOT_STEPS)


def fix():
    """The one thing that brings a missing helper to this box."""
    if auto_installs():
        return ("the next software update (Settings, Update) installs it by "
                "itself")
    return ("it arrives with the box's one-time maintenance step "
            "(scripts/upgrade_steps.sh), which your installer or IRIV support "
            "runs once; after that, software updates install it by themselves")


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
