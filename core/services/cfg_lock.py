# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
cfg_lock.py -- one writer at a time for a system's config.local.toml, across
PROCESSES, and a write that can never be torn.

Two processes rewrite the same file: the hub (System Control's Start / Stop
writes [ai], Power writes [power], model select writes [detector]) and the
system's own master (zones, the workflow compile, outputs, cameras...). Every
writer reads the whole document, changes one table and writes it all back. A
threading lock serialised that inside ONE process only, so a hub Start landing
while the master saved zones could write back the document from before the
save -- the save silently gone (reproduced: 150 alternating writes from two
processes kept a fraction of the master's). And both used the same
"config.local.toml.tmp", so two writes at once could interleave into one file.

So: an OS file lock on a lock file beside the config (fcntl.flock on Linux,
msvcrt.locking on a Windows dev box), re-entrant within a thread because
writers call writers, and write() with a temp name unique to the process and
the call, fsync'd before the atomic replace.
"""

import contextlib
import os
import random
import threading
import time

try:
    import fcntl                                               # noqa: PLC0415
except ImportError:                                            # pragma: no cover
    fcntl = None
try:
    import msvcrt                                              # noqa: PLC0415
except ImportError:
    msvcrt = None

#: The longest a writer waits for another. A holder that died has its lock
#: freed by the kernel, so this bounds a wedged-but-alive holder only; past
#: it the write goes ahead rather than freezing a page for ever.
TIMEOUT_S = 30.0

_local = threading.local()
_thread_locks = {}
_guard = threading.Lock()


def lock_path(path):
    """The lock file for config file `path` (beside it, so every process that
    can write the file finds the same lock whatever its store says)."""
    head, name = os.path.split(os.path.abspath(path))
    return os.path.join(head, "." + name + ".lock")


def _os_lock(handle):
    deadline = time.time() + TIMEOUT_S
    while True:
        try:
            if fcntl is not None:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(0.01)


def _os_unlock(handle):
    try:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
        elif msvcrt is not None:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextlib.contextmanager
def held(path):
    """Hold the write lock for config file `path`. Re-entrant per thread."""
    key = os.path.normcase(os.path.abspath(path))
    depth = getattr(_local, "depth", None)
    if depth is None:
        depth = _local.depth = {}
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    with _guard:
        tlock = _thread_locks.setdefault(key, threading.Lock())
    acquired = tlock.acquire(timeout=TIMEOUT_S)
    handle = None
    locked = False
    try:
        try:
            os.makedirs(os.path.dirname(key), exist_ok=True)
            handle = open(lock_path(path), "a+")
            locked = _os_lock(handle)
        except OSError:
            handle = None               # no lock file possible: thread lock only
        depth[key] = 1
        yield
    finally:
        depth[key] = 0
        if handle is not None:
            if locked:
                _os_unlock(handle)
            handle.close()
        if acquired:
            tlock.release()


def write(path, text):
    """Write `text` to `path` atomically: a temp file of this process and
    call, flushed to disk, then replaced over the target in one step.

    A *.local.toml is CREATED owner-only (0600) and stays so through the
    replace: a system's config.local.toml holds its SMTP app password and
    Telegram bot token, and the umask's 0644 let every account on the box
    read them (Tok Aba, 2026-10-14). Created that way rather than chmod'ed
    after, so there is no moment it is readable -- the rule core/config.py
    save_local already keeps. Windows has no such modes and ignores it."""
    tmp = f"{path}.{os.getpid()}.{random.randrange(1 << 30):x}.tmp"
    mode = 0o600 if str(path).endswith(".local.toml") else 0o666
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                # This process's parsed copy is now stale, whatever the file
                # stamp says (two writes in one clock tick share an mtime).
                from core import tomlcache                      # noqa: PLC0415
                tomlcache.invalidate(path)
                return
            except PermissionError:
                # Windows: a reader holding the target open refuses the
                # replace for a moment. Linux never gets here.
                if attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
