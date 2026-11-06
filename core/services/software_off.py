# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
services/software_off.py -- turn the studio's own processes off before the box
powers off or reboots.

WHY. The hub runs with KillMode=process (it must not take live detection down
when it restarts), so the masters and detection apps it spawned are not part
of its stop. At power-off they are still running, and the OS then waits for
them itself -- up to systemd's 90 s default for anything slow to answer
SIGTERM (a detection app holding a camera and the AI HAT). A Shut down that
sat on a black screen for a minute and a half was that wait.

So the software is switched off FIRST, here, on a short clock: every detection
app and every master is asked to quit (SIGTERM, which lets them flush and let
go of the camera and the chip), given GRACE_S, and whatever is still alive
after that is killed. Then the caller hands off to `shutdown` / `reboot`, and
there is nothing left for the OS to wait on.

The hub itself is left alone: it is the process asking, and systemd stops it
in about a second.

No framework imports here (docs/conventions.md sec 2).
"""

import logging
import os
import signal
import time

log = logging.getLogger(__name__)

#: How long the apps and masters get to quit on their own before they are
#: killed. Long enough to flush a database write and release a camera; short
#: enough that nobody watching the screen wonders whether it hung.
GRACE_S = 4.0

#: What identifies one of OUR processes on its command line.
_MARKERS = (("master_server.py",), ("-m", "app.run"))


def _is_ours(argv):
    for marker in _MARKERS:
        if len(marker) == 1 and any(a.endswith(marker[0]) for a in argv):
            return True
        if len(marker) == 2:
            for i in range(len(argv) - 1):
                if argv[i] == marker[0] and argv[i + 1] == marker[1]:
                    return True
    return False


def studio_pids():
    """PIDs of every detection app and master on the box, never our own."""
    me = os.getpid()
    out = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for name in entries:
        if not name.isdigit() or int(name) == me:
            continue
        try:
            with open(f"/proc/{name}/cmdline", "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if argv and _is_ours(argv):
            out.append(int(name))
    return out


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0) but is already gone for our purposes.
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rsplit(b") ", 1)[1][:1] != b"Z"
    except FileNotFoundError:
        return False
    except (OSError, IndexError):
        return True


def turn_off(grace_s=GRACE_S):
    """Ask every app and master to quit, wait up to `grace_s`, kill the rest.

    Returns {"asked": n, "killed": m}. Never raises: a box that is about to
    power off must power off even if a process could not be signalled.
    """
    pids = studio_pids()
    if not pids:
        return {"asked": 0, "killed": 0}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.monotonic() + max(0.0, grace_s)
    left = list(pids)
    while left and time.monotonic() < deadline:
        time.sleep(0.1)
        left = [p for p in left if _alive(p)]
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    log.info("[POWER] software off: %d asked to quit, %d killed after %.0fs",
             len(pids), len(left), grace_s)
    return {"asked": len(pids), "killed": len(left)}
