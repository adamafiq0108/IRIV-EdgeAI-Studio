# -*- coding: utf-8 -*-
"""
ratelimit.py -- throttle the endpoints an anonymous caller can hammer.

The login route had no brake of any kind: an 8-character minimum password and
unlimited attempts per second is an offline crack run online. Password reset
was worse -- unauthenticated, and every hit sends real email through the
deployment's SMTP account, so a loop is both a token oracle and a way to get
that account flagged as a spammer.

Two windows, deliberately:

  * a SOFT window -- N attempts per period, and past it the caller waits.
  * a LOCKOUT -- after M failures the key is refused outright for a cooldown,
    so a slow-and-wide attempt does not simply pace itself under the soft bar.

Keyed by (bucket, identity), where identity is the client IP for anonymous
endpoints and IP+username for login: a shared NAT must not lock out a whole
factory floor because one workstation typed a password wrong, and one attacker
must not lock out a real operator by guessing at their username from another
address.

In-memory and per-process on purpose. This is a single-box product (one hub,
one master per system, no shared cache), and a limiter that needs Redis to
work is a limiter that is off in the deployment that needs it most. A restart
clears the counters, which is the documented trade: it takes a crash loop --
not a request -- to reset them, and the audit log still records every failure.
"""

import threading
import time

#: bucket -> (max_attempts, window_s, lockout_after, lockout_s)
#:
#: Login is the one an attacker actually wants; the reset endpoints are rate
#: limited mostly to protect the SMTP account and the operator's inbox.
LIMITS = {
    "login":  (8, 60.0, 12, 300.0),
    "forgot": (3, 300.0, 6, 900.0),
    "reset":  (10, 300.0, 20, 900.0),
    "pair":   (10, 60.0, 20, 600.0),
}

#: Hard ceiling on tracked keys, so a spray across forged X-Forwarded-For
#: values cannot grow this dict without bound. Past it the oldest quiet keys
#: are dropped -- never an ACTIVE lockout, or eviction would be the bypass.
MAX_KEYS = 4096

_lock = threading.Lock()
#: key -> {"hits": [ts, ...], "fails": int, "until": float}
_state = {}


def _entry(key):
    return _state.setdefault(key, {"hits": [], "fails": 0, "until": 0.0})


def _evict(now):
    """Drop keys that are quiet and not locked out."""
    if len(_state) <= MAX_KEYS:
        return
    dead = [k for k, e in _state.items()
            if e["until"] <= now and (not e["hits"] or now - e["hits"][-1] > 3600)]
    for k in dead:
        _state.pop(k, None)
    if len(_state) > MAX_KEYS:
        # Still over: drop the least recently active unlocked keys.
        unlocked = sorted((e["hits"][-1] if e["hits"] else 0.0, k)
                          for k, e in _state.items() if e["until"] <= now)
        for _, k in unlocked[:len(_state) - MAX_KEYS]:
            _state.pop(k, None)


def check(bucket, identity, now=None):
    """Seconds the caller must wait, or 0 when the attempt may proceed.

    Call BEFORE doing the work. It records the attempt, so a caller that is
    refused still counts toward the window -- otherwise hammering a locked key
    would cost the attacker nothing.
    """
    max_hits, window, lock_after, lock_s = LIMITS.get(bucket, LIMITS["login"])
    now = time.time() if now is None else now
    with _lock:
        _evict(now)
        e = _entry((bucket, identity))
        if e["until"] > now:
            return e["until"] - now
        e["hits"] = [t for t in e["hits"] if now - t < window]
        if len(e["hits"]) >= max_hits:
            e["until"] = now + lock_s
            return lock_s
        e["hits"].append(now)
        return 0.0


def record_failure(bucket, identity, now=None):
    """A wrong password / bad token. Trips the lockout past the threshold."""
    _, _, lock_after, lock_s = LIMITS.get(bucket, LIMITS["login"])
    now = time.time() if now is None else now
    with _lock:
        e = _entry((bucket, identity))
        e["fails"] += 1
        if e["fails"] >= lock_after:
            e["until"] = now + lock_s
            e["fails"] = 0
            return lock_s
    return 0.0


def record_success(bucket, identity):
    """Clear the counters -- the caller proved they are legitimate."""
    with _lock:
        _state.pop((bucket, identity), None)


def reset():
    """Drop all state. Tests only."""
    with _lock:
        _state.clear()
