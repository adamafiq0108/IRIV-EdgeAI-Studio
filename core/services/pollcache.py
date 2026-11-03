# -*- coding: utf-8 -*-
"""
pollcache.py -- compute an expensive READ once, hand the answer to every
request entitled to it that arrives within a short TTL.

Why: every open dashboard tab polls the same handful of reads, and each
poll recomputed its answer from scratch. Measured on a 4-core box with three
systems and four cameras (tools/load_test.py): 5 tabs on one system cost its
master 28% of a core, 20 tabs saturated it (p50 385 ms, errors), and the AI
lost ~10% of its fps. The answer to tab 7's /api/dashboard is the answer tab
3 got 200 ms ago; this module lets the second request reuse it.

PURE on purpose: no framework, no config, no request. It never sees who is
asking -- the CALLER builds the key, and the key is what carries the
authorisation (core/services/pollshare.py says how). A key that reached
here has already passed the session wall and every permission check; this
module only decides "compute or reuse".

The rules, each one a review point:
  * TTL per entry, measured on an injectable monotonic clock, from the
    moment the value was COMPUTED (not asked for).
  * SINGLE-FLIGHT. When an entry is missing or expired and N requests
    arrive together, the first (the leader) computes; the others wait on
    its result. The wait is BOUNDED (wait_s): a waiter whose leader is slow
    or failed computes for itself, exactly as it would with no cache.
  * Nothing that failed is kept. An exception propagates to the leader's
    own caller only; waiters are released and compute themselves. A None
    answer, or a dict saying {"ok": False}, is handed back but never stored.
  * BOUNDED: at most max_entries keys, least recently used dropped first.
  * clear() empties it AND starts a new generation: a computation that was
    already running when the clear came finishes for its own callers but is
    not stored, and a request arriving after the clear never joins it. That
    is what makes "a write empties the cache" mean "nothing computed before
    the write is served after it".
  * Thread-safe. The handlers using it are sync (FastAPI runs them in its
    threadpool), so a waiter blocks a pool thread, never the event loop.

Values are shared, not copied: every caller of one entry gets the same
object, so nothing after the cache may change it in place (pollshare's
`after` hooks copy before they edit).
"""

import collections
import threading
import time

#: What a cache this small holds. Keys are (endpoint, query, authorisation),
#: so the live set is endpoints x distinct authorisation keys x query
#: shapes -- a few dozen on a real box. Past this the least recently used
#: goes, which is what keeps a flood of distinct query strings harmless.
MAX_ENTRIES = 128

#: Longest a waiter sits on another request's computation before doing its
#: own. Above the slowest read measured (p95 740 ms) with room, below any
#: client timeout.
WAIT_S = 3.0


class _Flight:
    """One computation in progress: who waits on it, and what it produced."""

    __slots__ = ("gen", "event", "ok", "value")

    def __init__(self, gen):
        self.gen = gen
        self.event = threading.Event()
        self.ok = False
        self.value = None


def storable(value):
    """Is this answer fit to share? Not a missing one, not a refusal."""
    if value is None:
        return False
    return not (isinstance(value, dict) and value.get("ok") is False)


class PollCache:
    """TTL + single-flight + LRU, keyed by whatever hashable the caller says."""

    def __init__(self, max_entries=MAX_ENTRIES, wait_s=WAIT_S,
                 clock=time.monotonic):
        self.max_entries = max(1, int(max_entries))
        self.wait_s = float(wait_s)
        self.clock = clock
        self._lock = threading.Lock()
        self._entries = collections.OrderedDict()  # key -> (expires, value)
        self._flights = {}                         # key -> _Flight
        self._gen = 0
        self.stats = dict.fromkeys(
            ("hits", "misses", "coalesced", "fallbacks", "stored",
             "unstored", "evictions", "clears"), 0)

    def __len__(self):
        return len(self._entries)

    def get(self, key, ttl, compute):
        """The answer for `key`: a live entry, the result of a computation
        already running for it, or compute() -- whose result is kept for
        `ttl` seconds if it is fit to share."""
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None:
                if hit[0] > self.clock():
                    self._entries.move_to_end(key)
                    self.stats["hits"] += 1
                    return hit[1]
                del self._entries[key]
            flight = self._flights.get(key)
            leader = flight is None
            if leader:
                flight = self._flights[key] = _Flight(self._gen)
                self.stats["misses"] += 1
            else:
                self.stats["coalesced"] += 1
        if leader:
            return self._lead(key, ttl, compute, flight)
        if flight.event.wait(self.wait_s) and flight.ok:
            return flight.value
        with self._lock:
            self.stats["fallbacks"] += 1
        return compute()

    def _lead(self, key, ttl, compute, flight):
        try:
            value = compute()
        except BaseException:
            with self._lock:
                if self._flights.get(key) is flight:
                    del self._flights[key]
            flight.event.set()          # waiters see ok=False and compute
            raise
        keep = storable(value)
        with self._lock:
            if self._flights.get(key) is flight:
                del self._flights[key]
            if keep and flight.gen == self._gen:
                self._entries[key] = (self.clock() + float(ttl), value)
                self._entries.move_to_end(key)
                self.stats["stored"] += 1
                while len(self._entries) > self.max_entries:
                    self._entries.popitem(last=False)
                    self.stats["evictions"] += 1
            else:
                self.stats["unstored"] += 1
        flight.ok, flight.value = keep, value
        flight.event.set()
        return value

    def clear(self):
        """Drop every entry and every running computation's claim on the
        future (see the module docstring's generation rule)."""
        with self._lock:
            self._entries.clear()
            self._flights.clear()
            self._gen += 1
            self.stats["clears"] += 1

    def counters(self):
        """A copy of the counters plus the entry count. No keys, no values."""
        with self._lock:
            return dict(self.stats, entries=len(self._entries))
