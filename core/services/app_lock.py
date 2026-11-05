# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
app_lock.py -- one system's app is started or stopped by one caller at a time.

Two processes start and stop a system's app: the hub (System Control's
switch, power, autostart) and the system's own master (its Detection page,
the app supervisor). Without a lock they raced: a master spawned a
logic-mode app (before AI detection became a switch inside the one app), the
hub's Start detection arrived inside the 0.8 s before that app's pid was
recorded, found a "stray" process of the package,
re-adopted it -- and answered "detection started" over an app with no model
and no camera.

So every start and stop of an app holds this lock: an flock on a file under
the store's run directory, one per system (two systems are two apps, and a
box-wide lock would queue one system's 10 s stop behind another's start).
Re-entrant within a process, because a Start detection STOPS an old logic app
under the same lock it starts under.

On a box without fcntl (a Windows laptop running the tests) it degrades to a
process-local lock, which is all a single-process dev run needs.
"""

import contextlib
import os
import threading

from core import config as config_mod

try:
    import fcntl                                               # noqa: PLC0415
except ImportError:                                            # pragma: no cover
    fcntl = None

_local = threading.local()
_thread_locks = {}
_guard = threading.Lock()


def _path(slug):
    run = os.path.join(str(config_mod.store_dir()), "run")
    os.makedirs(run, exist_ok=True)
    safe = "".join(c for c in str(slug) if c.isalnum() or c in "-_") or "system"
    return os.path.join(run, f"app-{safe}.lock")


@contextlib.contextmanager
def held(slug, timeout_s=60.0):
    """Hold the app lock for `slug`. Blocks up to `timeout_s` for another
    holder, then proceeds anyway: a lock nobody releases (a killed process)
    must not freeze a Start button for ever -- the kernel frees an flock with
    its process, so this is a bound, not the normal case."""
    depth = getattr(_local, "depth", {})
    _local.depth = depth
    if depth.get(slug):
        depth[slug] += 1
        try:
            yield
        finally:
            depth[slug] -= 1
        return
    with _guard:
        tlock = _thread_locks.setdefault(slug, threading.Lock())
    acquired = tlock.acquire(timeout=timeout_s)
    handle = None
    try:
        if fcntl is not None:
            handle = open(_path(slug), "a+")
            import time as _time                               # noqa: PLC0415
            deadline = _time.time() + timeout_s
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if _time.time() >= deadline:
                        break
                    _time.sleep(0.1)
        depth[slug] = 1
        yield
    finally:
        depth[slug] = 0
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
        if acquired:
            tlock.release()
