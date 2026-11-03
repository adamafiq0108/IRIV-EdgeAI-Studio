# -*- coding: utf-8 -*-
"""
update_auto.py -- whether the box checks for updates by itself, and when.

Tok Aba, 2026-10-05: "Check for updates automatically" is a switch, OFF on
every box, new and existing. Off, nothing in the background talks to GitHub:
the box learns about a release only when an admin presses Check now (or
Update, which re-confirms the newest release before installing). On, the box
checks as soon as it has internet and then on the normal interval
(config/update.toml [check] interval_hours), and a release it finds shows on
the Update card and in the Inbox exactly as before.

The setting is `auto_check` in config/update.local.toml: the machine-written
half, carried into every new release by the applier (carry_config), so it
survives updates and rollbacks. One switch for both channels on a dev box:
the question is "may this box go online by itself", which has one answer per
box, not one per release stream.

COMING ONLINE, the lightest way that is still reliable. Every TICK_S the loop
reads the kernel's routing table (/proc/net/route and /proc/net/ipv6_route: a
file read, no packet sent). No default route means no internet, full stop,
and nothing else happens. A default route is not proof (board .22: a dead
gateway on a Modbus port), so while the box believes it is offline it also
tries one TCP connection to api.github.com:443 (DNS plus a handshake, no TLS,
no token); the first one that connects is "came online" and the check runs
on that tick. Once a check has had an answer the box counts as online and
waits for the interval; a check that cannot reach GitHub puts it back to
offline, so the next return of the uplink is noticed within one tick. Chosen
over NetworkManager's connectivity state because that is often switched off
on Pi OS images (it then reports "full" for any default route, which is the
case the probe exists for) and needs nmcli in a loop.
"""

import random
import socket
import threading
import time

from core import config

#: The key in config/update.local.toml. Absent means off.
SETTING_KEY = "auto_check"

#: Seconds between looks at the routing table.
TICK_S = 30

#: The first minutes after boot belong to starting systems and cameras.
BOOT_GRACE_S = 120

#: What "can reach GitHub" is asked of, while the box believes it is offline.
PROBE_HOST = ("api.github.com", 443)
PROBE_TIMEOUT_S = 5

#: One writer of update.local.toml at a time in this process (the token and
#: this switch both rewrite the whole file).
local_lock = threading.Lock()

_wake = threading.Event()
_thread = None
_kick = False


def enabled(config_dir=None):
    """True only when an admin turned the switch on. Fails closed: a file
    that will not parse reads as off, so no box phones home by accident."""
    try:
        local = config.load_local("update", config_dir) or {}
    except ValueError:
        return False
    return local.get(SETTING_KEY) is True


def set_enabled(on, config_dir=None):
    """Store the switch and wake the loop, so turning it on checks now (when
    the box is online) instead of at the next tick."""
    global _kick
    with local_lock:
        local = dict(config.load_local("update", config_dir) or {})
        if on:
            local[SETTING_KEY] = True
        else:
            local.pop(SETTING_KEY, None)
        config.save_local("update", local, config_dir)
    if on:
        _kick = True
        _wake.set()
    return bool(on)


def _default_route_in(path, ipv6=False):
    with open(path, encoding="ascii", errors="replace") as f:
        rows = f.read().splitlines()
    for row in rows:
        cols = row.split()
        if ipv6:
            # dest, dest prefix length, ...; the default route is ::/0 on a
            # real interface (not lo).
            if len(cols) >= 10 and cols[0] == "0" * 32 and cols[1] == "00" \
                    and cols[9] != "lo":
                return True
        elif len(cols) >= 8 and cols[1] == "00000000" and cols[7] == "00000000":
            return True
    return False


def has_route():
    """Is there a default route at all? Read from /proc, no packet sent. A
    system without /proc (a developer's laptop) answers True: unknown, so the
    probe decides."""
    seen = False
    for path, v6 in (("/proc/net/route", False), ("/proc/net/ipv6_route", True)):
        try:
            if _default_route_in(path, v6):
                return True
            seen = True
        except OSError:
            continue
    return not seen


def probe():
    """One TCP connection to GitHub's API host: DNS works, a route works,
    something answers on 443. No TLS, no request, no token."""
    try:
        with socket.create_connection(PROBE_HOST, timeout=PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def check_all(config_dir=None):
    """The background check itself: every channel this box has a token for.
    Returns False when GitHub could not be reached (the box is offline after
    all), True when every check got an answer of some kind."""
    from core.services import update                       # noqa: PLC0415
    channels = [update.CHANNEL_STABLE]
    if update.dev_available(config_dir):
        channels.append(update.CHANNEL_DEV)
    reached = True
    for ch in channels:
        if not update.token(config_dir, ch):
            continue
        try:
            # Queued, not skipped: a "Check now" that happens to be out must
            # not cost the timer its turn for hours.
            out = update.check(config_dir=config_dir, channel=ch, wait=True)
        except Exception:                                   # noqa: BLE001
            continue
        if update.is_offline(out.get("error")) \
                or out.get("error") == update.CHECK_UNREACHABLE:
            reached = False
    return reached


def interval_s(config_dir=None):
    from core.services import update                       # noqa: PLC0415
    s = update.settings(config_dir)["interval_s"]
    return s + random.uniform(-0.08, 0.08) * s


class Ticker:
    """One decision per tick, kept apart from the thread so a test can drive
    it with a fake clock, a fake route table and a fake GitHub."""

    def __init__(self, config_dir=None, clock=time.monotonic):
        self.config_dir = config_dir
        self.clock = clock
        self.online = False
        self.next_due = 0.0

    def tick(self):
        """'off' | 'offline' | 'waiting' | 'checked'."""
        global _kick
        if not enabled(self.config_dir):
            self.online = False
            return "off"
        if _kick:
            _kick = False
            self.online = False                 # just switched on: check now
        if not has_route():
            self.online = False
            return "offline"
        now = self.clock()
        if self.online and now < self.next_due:
            return "waiting"
        if not self.online and not probe():
            return "offline"
        self.online = check_all(self.config_dir)
        if not self.online:
            return "offline"
        self.next_due = now + interval_s(self.config_dir)
        return "checked"


def _sweep():
    """An uploaded update file left waiting past its expiry goes (the loop
    is the one thing that runs whether or not anyone opens the card)."""
    try:
        from core.services import update_offline           # noqa: PLC0415
        update_offline.sweep()
    except Exception:                                       # noqa: BLE001
        pass


def start(config_dir=None):
    """Start the loop once per process. Always started (it costs a file read
    every TICK_S), because the switch can be turned on at any time; it only
    goes online when the switch is on. Returns whether the switch is on."""
    global _thread
    _sweep()
    if _thread is None:
        ticker = Ticker(config_dir)

        def loop():
            _wake.wait(BOOT_GRACE_S)
            while True:
                _wake.clear()
                _sweep()
                try:
                    ticker.tick()
                except Exception:                           # noqa: BLE001
                    pass
                _wake.wait(TICK_S)

        _thread = threading.Thread(target=loop, name="update-check", daemon=True)
        _thread.start()
    return enabled(config_dir)


def _reset_for_tests():
    global _kick
    _kick = False
    _wake.clear()
