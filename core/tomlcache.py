# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
tomlcache.py -- parse a TOML file once per change, not once per read.

Perf audit item L3 (2026-10-02). A system's config.local.toml was re-parsed
on every read -- detector_control._read_toml kept a copy only as a fallback
for a failed read -- and a dashboard poll did eight of them. The parse is the
expensive part (11 ms for a 17 KB system config on a laptop, several times
that on a Pi); the os.stat that tells whether the file changed is 0.01 ms.

So a file is parsed again only when its (mtime_ns, size) moves. Size is in
the key because a file system stamps times from a coarse clock: two writes
inside one tick share an mtime, and the second would otherwise go unseen
(auth._save_local found exactly that on the Pi). Our own writers also call
invalidate() as they write -- cfg_lock.write, detector_control._write_local,
config.save_local -- so this process never trusts a stamp for its own edits.
Another PROCESS's write (the hub's System Control writing a master's
[ai], the master saving zones) is seen by the stamp, which is what makes it
safe to cache across processes at all.

read() hands back a deep COPY: every caller of the old parse-per-read got a
fresh dict and some of them change it before writing it back, so sharing the
cached one would let a half-made edit leak into the next read. A copy costs
about an eighth of a parse.
"""

import copy
import os
import threading

try:
    import tomllib
except ModuleNotFoundError:                                  # pragma: no cover
    import tomli as tomllib

_lock = threading.Lock()
_cache = {}          # path -> (stamp, parsed document)
stats = {"parses": 0, "hits": 0}


def stamp(path):
    """(mtime_ns, size) of `path`, or None when it does not exist."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def read(path, fallback=False):
    """The parsed document at `path`, a copy the caller may change.

    Raises what open/parse raise (FileNotFoundError, TOMLDecodeError) --
    unless `fallback`, when a missing or unreadable file answers with the
    last copy successfully parsed (or {}), which is what
    detector_control._read_toml has always done."""
    key = str(path)
    st = stamp(key)
    with _lock:
        hit = _cache.get(key)
    if hit is not None and st is not None and hit[0] == st:
        stats["hits"] += 1
        return copy.deepcopy(hit[1])
    try:
        with open(key, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        if not fallback:
            raise
        return copy.deepcopy(hit[1]) if hit is not None else {}
    stats["parses"] += 1
    with _lock:
        _cache[key] = (st, data)
    return copy.deepcopy(data)


def invalidate(path=None):
    """Forget `path` (or everything): the next read parses it again."""
    with _lock:
        if path is None:
            _cache.clear()
        else:
            _cache.pop(str(path), None)


class _PopView:
    """dict.pop look-alike over invalidate(), for code written against the
    old detector_control._toml_cache dict (builtin_models pops from it)."""

    def pop(self, path, default=None):
        invalidate(path)
        return default

    def get(self, path, default=None):
        with _lock:
            hit = _cache.get(str(path))
        return copy.deepcopy(hit[1]) if hit is not None else default

    def clear(self):
        invalidate()


compat = _PopView()
