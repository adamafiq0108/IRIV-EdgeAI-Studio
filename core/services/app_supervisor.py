# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
app_supervisor.py -- keep a system's ONE app running while the system is online.

A workflow is more than detection. An Inject, a Schedule, MQTT, Modbus, the
Math and the outputs after them need no camera, so a system's app runs the
whole workflow whenever the system is up, and AI detection is a switch INSIDE
it (app/run.py ai_config; detector_control.start / stop flip [ai] on). This
module is what keeps that app there:

    the system is online (its master is up -- this code runs IN the master or
      in the hub on its behalf, never for a system that is powered off)
    the app is not running -> start it, with the AI OFF (dc.ensure_app)
    an old logic-mode app from before the switch is running -> replace it, once

It runs at master start, after a workflow save, and after Start / Stop on the
pages, and a watcher restarts an app that DIED, with a back-off after
repeated crashes.

The same watcher passes on the camera watchdog's word (camera_notice): an
app whose camera went silent restarts its own detection (app/camwatch.py),
and the admins hear about it once when the first restart fails and once when
the camera is back.

What it must never do, and does not:
  * turn AI detection on: only Start detection (the operator, or the hub's
    boot autostart) does. A box that reboots, or an app that crashes while
    detecting, comes back with the workflow running and the AI off -- the
    same answer as before the switch, when a detection app that died stayed
    down -- and a crash while detecting says so in the inbox
  * bring back a system that was powered off (dc.powered_off) or deleted
  * start an app copy too old for the switch (it would open the camera and
    the model): the admins get a notice once per version instead
"""

import logging
import threading
import time

from core import config as config_mod

_log = logging.getLogger(__name__)

#: Local state: the notice already given per slug.
STATE_NAME = "app_supervisor"
#: Crashes counted within this window...
CRASH_WINDOW_S = 600
#: ...at this many, the watcher stops restarting for BACKOFF_S and says so.
CRASH_LIMIT = 3
BACKOFF_S = 600
#: How often the watcher looks for an app that is not there.
WATCH_S = 30.0
#: How long after master start the first check waits: the hub's autostart of
#: DETECTION comes first, so an autostart system starts once, AI on.
START_DELAY_S = 25.0

_crashes = {}                   # slug -> [crash times]
_paused = {}                    # slug -> resume after
_threads = {}


def logic_stale(systems_dir, slug):
    """True when the system's config holds no [logic] list (which nodes run
    with the AI off), or one decided by older rules (workflow.LOGIC_VERSION)
    -- a box updated over the air until its workflow is compiled again."""
    from core.services import detector_control as dc           # noqa: PLC0415
    from core.services import workflow as wf                   # noqa: PLC0415
    logic = (dc.system_config(systems_dir, slug) or {}).get("logic")
    return not isinstance(logic, dict) \
        or int(logic.get("version") or 0) < wf.LOGIC_VERSION


def ensure_compiled(systems_dir, slug, config_dir=None):
    """Recompile the SAVED workflow when [logic] is missing or stale. Nothing
    else at boot compiles, so after an update every system would otherwise
    keep its pre-update config. Returns applied | clean | failed | none."""
    if not logic_stale(systems_dir, slug):
        return "clean"
    try:
        from core.routes import workflow as wf_routes          # noqa: PLC0415
        store, _ = wf_routes._get_store(systems_dir, slug)
        if not store or not store.get("workflows"):
            return "none"
        compiled = wf_routes._recompile(systems_dir, config_dir, slug, store)
        return "applied" if compiled.get("applied") else "failed"
    except Exception:                                          # noqa: BLE001
        _log.exception("[APP] %s: recompile at start failed", slug)
        return "failed"


def _notice_once(slug, config_dir, notify):
    from core.version import VERSION                           # noqa: PLC0415
    try:
        state = dict(config_mod.load_local(STATE_NAME, config_dir))
    except ValueError:
        state = {}
    notices = dict(state.get("notices") or {})
    if notices.get(slug) == VERSION:
        return
    notices[slug] = VERSION
    state["notices"] = notices
    config_mod.save_local(STATE_NAME, state, config_dir)
    notify("app_supervisor", "This system's workflow cannot run without detection",
           f"System '{slug}' has a copy of the app from before AI detection "
           "became a switch, so its workflow runs only while detection is "
           "on. Update the system to run the rest of the workflow with "
           "detection off.", config_dir, page="systems")


def reconcile(systems_dir, slug, config_dir=None, notify=None):
    """Bring the app in line with the rules above. Returns a word:
    maintenance | powered_off | unsupported | running | replaced | started |
    failed. Never raises."""
    from core.services import detector_control as dc           # noqa: PLC0415
    if notify is None:
        from core.services import inbox                        # noqa: PLC0415
        notify = inbox.notify_admins
    from core.services import app_lock                         # noqa: PLC0415
    try:
        # The look and the act under ONE hold of the app lock: a Start
        # detection from the hub cannot land between "nothing is running"
        # and the app starting.
        with app_lock.held(slug):
            return _reconcile_locked(systems_dir, slug, config_dir, notify, dc)
    except Exception:                                          # noqa: BLE001
        _log.exception("[APP] %s: reconcile failed", slug)
        return "failed"


def _reconcile_locked(systems_dir, slug, config_dir, notify, dc):
    """reconcile(), with the app lock held."""
    st = dc.status(systems_dir, slug)
    if dc.powered_off(systems_dir, slug):
        # Powered off on the Hub: the master may be back (systemd), the
        # system is not. Anything still running for it stops.
        if st.get("app_running"):
            dc.stop_app(systems_dir, slug)
        return "powered_off"
    if not dc.unified_supported(systems_dir, slug):
        if not st.get("app_running"):
            _notice_once(slug, config_dir, notify)
        return "unsupported"
    return dc.ensure_app(systems_dir, slug).get("word") or "failed"


def start_for_press(systems_dir, slug):
    """Make sure the app is up for an Inject press, with reconcile's guards:
    a system powered off on the Hub stays off, nothing starts while an update
    installs. Returns (word, sentence): started | powered_off | maintenance |
    unsupported | failed."""
    word = reconcile(systems_dir, slug)
    if word in ("running", "replaced", "started"):
        return "started", ""
    return word, {
        "powered_off": "this system is powered off on the Hub - power it on "
                       "to run its workflow",
        "unsupported": "this system's app is too old to run the workflow "
                       "without detection - update the system, or start "
                       "detection",
    }.get(word, "the workflow could not be started - see the app log")


def reconcile_soon(systems_dir, slug, config_dir=None, delay=0.0):
    """reconcile() in the background -- a save or a stop must not wait on an
    app starting."""
    def work():
        if delay:
            time.sleep(delay)
        _log.info("[APP] %s: %s", slug,
                  reconcile(systems_dir, slug, config_dir))
    threading.Thread(target=work, name=f"app-{slug}", daemon=True).start()


def crashed(systems_dir, slug):
    """An app that DIED: its state file still says running, but no process
    answers. A clean stop writes running=false, so a Power off, a delete or a
    counters reset never reads as a crash. Returns the dead run's raw state
    (truthy) or {}."""
    from core.services import detector_control as dc           # noqa: PLC0415
    raw = dc.app_state(systems_dir, slug) or {}
    if not raw.get("running"):
        return {}
    return {} if dc.status(systems_dir, slug).get("app_running") else raw


def watch_step(systems_dir, slug, config_dir=None, now=None, notify=None):
    """One look for an app that should be running and is not. Returns what
    it did: none | paused | restarted | backoff | started. Crash-looping stops
    after CRASH_LIMIT in CRASH_WINDOW_S, for BACKOFF_S, with a notice."""
    now = time.time() if now is None else now
    if notify is None:
        from core.services import inbox                        # noqa: PLC0415
        notify = inbox.notify_admins
    if now < _paused.get(slug, 0):
        return "paused"
    from core.services import detector_control as dc           # noqa: PLC0415
    if dc.powered_off(systems_dir, slug):
        return "none"
    dead = crashed(systems_dir, slug)
    if not dead:
        if dc.status(systems_dir, slug).get("app_running"):
            # Alive: the one thing to pass on is the camera watchdog's
            # episode, if it has reached the point of telling anyone.
            try:
                from core.services import camera_notice        # noqa: PLC0415
                camera_notice.post(systems_dir, slug, config_dir, notify)
            except Exception:                                  # noqa: BLE001
                _log.exception("[APP] %s: camera notice failed", slug)
            return "none"
        # Stopped cleanly while online (a counters reset that did not bring
        # it back, a stop by hand): the workflow belongs up.
        word = reconcile(systems_dir, slug, config_dir, notify)
        return "started" if word == "started" else "none"
    times = [t for t in _crashes.get(slug, []) if now - t < CRASH_WINDOW_S]
    times.append(now)
    _crashes[slug] = times
    if len(times) >= CRASH_LIMIT:
        _paused[slug] = now + BACKOFF_S
        _crashes[slug] = []
        notify("app_supervisor", "A system's app keeps stopping",
               f"System '{slug}' ran its workflow and the app stopped "
               f"{CRASH_LIMIT} times in {CRASH_WINDOW_S // 60} minutes. It is "
               f"tried again in {BACKOFF_S // 60} minutes; the app log on the "
               "Logs page says why.", config_dir, page="systems")
        return "backoff"
    if dead.get("ai_on") is not False and dead.get("mode") != "logic":
        # It died DETECTING. The workflow comes back; detection does not --
        # a camera or chip that crashes the app is the operator's to look
        # at, exactly as a detection app that died always stayed down.
        notify("app_supervisor", "Detection stopped unexpectedly",
               f"System '{slug}' stopped while detecting. Its workflow was "
               "restarted with AI detection OFF; start detection again when "
               "the cause is fixed (the Logs page says why).",
               config_dir, page="systems")
    reconcile(systems_dir, slug, config_dir, notify)
    return "restarted"


def start_supervisor(systems_dir, slug, config_dir=None):
    """The master's own: the first reconcile after start, then the watch.
    Never blocks, never raises."""
    if slug in _threads:
        return False

    def work():
        time.sleep(START_DELAY_S)
        _log.info("[APP] %s workflow at start: %s", slug,
                  ensure_compiled(systems_dir, slug, config_dir))
        _log.info("[APP] %s at start: %s", slug,
                  reconcile(systems_dir, slug, config_dir))
        while True:
            time.sleep(WATCH_S)
            try:
                watch_step(systems_dir, slug, config_dir)
            except Exception:                                  # noqa: BLE001
                _log.exception("[APP] %s: watch failed", slug)

    t = threading.Thread(target=work, name=f"app-watch-{slug}", daemon=True)
    _threads[slug] = t
    t.start()
    return True
