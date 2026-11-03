# -*- coding: utf-8 -*-
"""
pollshare.py -- WHEN a poll's answer may be shared, and with whom.

core/services/pollcache.py is the mechanism (TTL, single-flight, LRU) and
knows nothing about users. This is the policy around it: the box switch,
the per-endpoint TTLs, the authorisation key, the query whitelist, and the
rule that any write empties every process's cache. The routes reach it
through core/routes/perm.shared(), AFTER the session wall, CSRF and every
permission check have already run -- a refused request never gets here.

[system] share_polls (default OFF, core/services/sysconfig.py). Off is
today's behaviour exactly: lookup() calls the handler and nothing else --
no key is built, no counter moves, and a cache left over from "on" is
emptied by the first request that sees "off". Read live from config through
the stamp-checked config.load, so a flip lands on the next request.

THE AUTHORISATION KEY (authz_key) is everything that can change what an
account is allowed to see in these answers, and nothing else:
  * its tier (developer / master / admin / user; a disabled account reads
    as user, and is refused at the wall anyway),
  * its capability grants on THIS system ("*" for master and developer),
  * on the hub, the exact list of systems it may see (hub.visible_to: the
    systems grant and the workshop/deployment split).
Two accounts with equal keys are entitled to byte-identical answers from
every endpoint in TTLS (the audit is in the pull request report); two
accounts with different keys never share an entry. The key is rebuilt from
config on EVERY request, so a grant changed or an account demoted between
two polls moves that account to a new key at once -- nothing to invalidate.
The username is NOT in the key: no endpoint here returns anything about
the person asking beyond what the key already decides.

Writes: an authenticated POST/PUT/PATCH/DELETE in any process empties this
process's cache before its response goes back (core/routes/pollcache.py)
and bumps a stamp file in the config directory, which every other process
(the hub and each master) checks -- one os.stat -- before its next lookup.
So after an operator's own action nothing computed before it is served on
any page of the box. Changes nobody made through a route (the detection
app's live counts, new events from the app) show within the TTL.
"""

import os
import threading
import time
import urllib.parse

from core import config
from core import tomlcache
from core.services import auth as auth_service
from core.services import sysconfig as sysconfig_service
from core.services.pollcache import PollCache

#: endpoint id -> seconds an answer may be reused. Every value is below the
#: interval its page polls at, so a single tab never sees one answer twice;
#: the cost is at most this much extra staleness on a number already that
#: old by the time it is drawn.
#:   dashboard         0.5  Main.jsx polls 0.6 s; the app rewrites its state
#:                          every ~54 ms but a count half a second old is
#:                          what the page already shows between polls.
#:   events            2.0  the event table polls 10 s; a new row 2 s late.
#:   capacity          2.0  header meters poll 5 s; start/stop are writes,
#:                          which empty the cache at once anyway.
#:   host_meter        1.0  the footer polls 2 s while busy; CPU/temp/fps.
#:   detector_control  1.0  the Detection page polls 4 s; its own saves
#:                          are writes and empty the cache.
#:   hub               2.0  the Systems grid polls 10 s (twice per tab).
TTLS = {"dashboard": 0.5, "events": 2.0, "capacity": 2.0, "host_meter": 1.0,
        "detector_control": 1.0, "hub": 2.0}

#: A normalised query string longer than this is not shared (computed as if
#: the switch were off). No legitimate page sends one near it.
MAX_QUERY = 512

#: Writes that change nothing any shared read returns: the idle timer's
#: touch. Every other authenticated write empties the cache.
QUIET_WRITES = frozenset(("/api/auth/keepalive",))

#: The stamp file every process watches (in the PRODUCT config directory,
#: which the hub and every master share and only the service user writes).
EPOCH_NAME = ".pollcache.epoch"

CACHE = PollCache()
_seen_lock = threading.Lock()
_seen = {}                      # epoch path -> last stamp this process saw


def enabled(config_dir=None):
    """Is the shared poll cache on for this box?"""
    return sysconfig_service.share_polls(config_dir)


def authz_key(username, slug, config_dir=None, registry=None, masters=None):
    """(tier, caps on this system, visible systems on the hub) -- see the
    module docstring. `registry`/`masters` are passed on the hub only."""
    caps = auth_service.cap_grants(username, slug, config_dir)
    visible = None
    if registry is not None:
        from core.services import hub as hub_service       # noqa: PLC0415
        visible = tuple(s.slug for s in registry if hub_service.visible_to(
            username, s.slug, masters or {}, config_dir))
    return (auth_service.tier(username, config_dir),
            "*" if caps is None else tuple(sorted(caps)), visible)


def query_key(items, allowed):
    """The part of a query string an endpoint actually reads, in one
    spelling: whitelisted names only, sorted, each name's values in the
    order sent (that order reaches the handler too), percent-encoded so no
    value can pose as a separator. None when too long to share."""
    picked = {}
    for name, value in items:
        if name in allowed:
            picked.setdefault(name, []).append(value)
    key = "&".join(f"{urllib.parse.quote(n, safe='')}={urllib.parse.quote(v, safe='')}"
                   for n in sorted(picked) for v in picked[n])
    return key if len(key) <= MAX_QUERY else None


def lookup(endpoint, keyfn, compute, config_dir=None):
    """compute() -- or the answer an identical request (same endpoint, same
    query, same authorisation key) got within TTLS[endpoint]. `keyfn()`
    answers (query_key, authz_key); it is a callable so that with the switch
    off nothing about the request is even read."""
    if not enabled(config_dir):
        if len(CACHE):
            CACHE.clear()
        return compute()
    query, authz = keyfn()
    if query is None:
        return compute()
    _check_epoch(config_dir)
    return CACHE.get((endpoint, query, authz), TTLS[endpoint], compute)


def note_write(path, config_dir=None):
    """An authenticated write just finished: forget everything here, and
    tell the other processes. Off: only a leftover entry is dropped."""
    if path in QUIET_WRITES:
        return
    if not enabled(config_dir):
        if len(CACHE):
            CACHE.clear()
        return
    CACHE.clear()
    _bump_epoch(config_dir)


def _epoch_path(config_dir=None):
    return str(config.root(config_dir) / EPOCH_NAME)


def _check_epoch(config_dir=None):
    """Clear when another process has written since this one last looked."""
    path = _epoch_path(config_dir)
    now = tomlcache.stamp(path)
    with _seen_lock:
        missing = path not in _seen
        last = _seen.get(path)
        _seen[path] = now
    if not missing and last != now:
        CACHE.clear()


def _bump_epoch(config_dir=None):
    """Move the stamp file's mtime strictly forward (a coarse filesystem
    clock could otherwise give two writes one stamp). Never follows a
    symlink; never raises -- a box that cannot write it falls back to the
    TTL for other processes, and this process has already cleared."""
    path = _epoch_path(config_dir)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return
    try:
        n = max(time.time_ns(), os.fstat(fd).st_mtime_ns + 1)
        os.utime(fd if os.utime in os.supports_fd else path, ns=(n, n))
    except OSError:
        pass
    finally:
        os.close(fd)
    with _seen_lock:
        _seen[path] = tomlcache.stamp(path)


#: The counters the admin card shows, summed across processes.
COUNTERS = ("hits", "misses", "coalesced", "fallbacks", "stored", "unstored",
            "evictions", "clears", "entries")


def counters():
    """This process's counters. Never a key, never a value."""
    return CACHE.counters()


def box_counters(masters, cookie):
    """The hub's counters plus every master's own (asked AS the caller, so
    each master applies its own admin check). `processes` says how many
    answered; a master that did not is simply not in the sums."""
    from core.services import masters as masters_service    # noqa: PLC0415
    total = {k: int(counters().get(k, 0)) for k in COUNTERS}
    answered = 1
    for entry in (masters or {}).values():
        body = masters_service.fetch_json((entry or {}).get("url"),
                                          "/api/system/poll-cache", cookie)
        if not body:
            continue
        answered += 1
        for k in COUNTERS:
            try:
                total[k] += max(0, int(body.get(k) or 0))
            except (TypeError, ValueError):
                pass
    return dict(total, processes=answered)
