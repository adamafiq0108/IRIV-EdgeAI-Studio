# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
outputs.py -- fire an action when a detection event occurs.

An operator wires an action in the Workflow ("on this violation, turn on the
tower light / send an email / message Telegram") and it is stored in
config.local.toml as an [[outputs]] table. The loop calls fire() on each event;
every enabled output whose trigger matches the event type is fired.

Kinds: gpio, mqtt, modbus, modbus_write, webhook, email, telegram, snapshot,
record. Anything that talks to the network runs on a thread with a short
timeout -- a slow endpoint must never stall the detection loop.

Everything degrades: a missing library, no board, or a bad parameter logs a line
and skips -- an output wiring mistake must never crash detection. GPIO handles
are cached so repeated fires do not re-open the pin.
"""

import os
import re
import threading
import time

# An alert that failed to send is the failure an operator asks about most and
# the one this process could say least about -- it went to app.log, which no
# page reads.
try:
    from . import faults
except ImportError:                     # run as a loose script, not a package
    import faults
try:
    from . import broker_login
except ImportError:                     # run as a loose script, not a package
    import broker_login
try:
    from . import mqtt_bus
except ImportError:                     # run as a loose script, not a package
    import mqtt_bus
# A user-given URL may reach another computer, never this box (Pipi M-R1):
# the same rules as core/services/netsafe.py, in a byte-identical copy.
try:
    from . import netsafe
except ImportError:                     # run as a loose script, not a package
    import netsafe

log = faults.logger(__name__)

_gpio_cache = {}


# -- did it actually send? ---------------------------------------------------
#
# An output's RESULT used to be written down nowhere. _fire_one dispatched to
# the gpio / mqtt / webhook / email / telegram / board paths and kept nothing,
# so a Telegram that came back 401, a relay whose port was held by another
# process and a webhook to a dead host all looked identical from every page:
# the canvas drew the rule going true and stopped there. The only signal was a
# line in app.log, which is the one place an operator looking at a dashboard
# cannot read.
#
# So every dispatch leaves four values behind, published as ordinary stats
# (run.py folds telemetry() into the counts it writes to state.json, the same
# channel every other value travels on) and named by the output's own id:
#
#   out_<id>_fired   attempts since this process started
#   out_<id>_ok      1 if the LAST attempt succeeded, 0 if it failed
#   out_<id>_at      epoch seconds of the last attempt, 0 if never
#   out_<id>_error   the last failure's message, "" when the last one was fine
#
# A FAILED attempt counts in `fired`. "Tried four times, all four failed" is
# the answer to the question being asked; a counter that only moved on success
# would sit at 0 for a broken output, which is exactly what a never-wired
# output reads like and is the confusion this exists to end.
#
# The three network senders (email, telegram, webhook) do their work on their
# OWN thread and catch the failure there, so nothing they do is visible to the
# caller at all -- which is why they record from inside that thread and tell
# the caller not to count them.

#: {output id: {"fired", "ok", "at", "error"}}. Module level for the same
#: reason as _capture_state: the outputs list is re-read from config every few
#: seconds, and a count that died with the list would restart every reload.
_results = {}
_results_lock = threading.Lock()

#: Bound on the table above. Output ids are minted by the graph editor, so a
#: board running for months would otherwise keep a slot for every id it has
#: ever seen. Dropping the lot costs a counter restart, never a send.
_MAX_RESULTS = 400

#: Longest failure message published. The master caps a text stat at the same
#: 120 (core/services/main.TEXT_MAX); capping here too keeps what this process
#: publishes identical to what the dashboard shows, so nobody chases a
#: difference between the two.
_MAX_ERROR = 120


def _record(o, exc=None, now=None, note=None):
    """Write down how ONE output's dispatch went.

    Never raises: telemetry about a failure must not become a second failure
    on a path that is already handling one.

    The message is REDACTED (faults.redact) because it is about to become a
    dashboard value: a webhook URL carrying a password and a Telegram bot
    token are both quoted verbatim by the exceptions urllib and smtplib
    raise, and a credential on a wall display is a credential given away.
    """
    try:
        oid = str((o or {}).get("id") or "").strip()
        if not oid:
            return                      # nothing to name the keys after
        now = time.time() if now is None else now
        message = ""
        if exc is not None:
            message = faults.redact(str(exc) or exc.__class__.__name__)[:_MAX_ERROR]
        with _results_lock:
            if oid not in _results and len(_results) >= _MAX_RESULTS:
                _results.clear()
            slot = _results.setdefault(oid, {"fired": 0, "ok": 0, "at": 0.0,
                                             "error": ""})
            slot["fired"] += 1
            slot["ok"] = 0 if exc is not None else 1
            slot["at"] = round(now, 3)
            slot["error"] = message
            # What reached the wire, in words ("wrote 42 to 0"), for the
            # outputs that can say more than "it worked". Only a sender that
            # passes one publishes the key at all; a failure clears it, so a
            # stale success never sits beside a fresh error.
            if note is not None or "note" in slot:
                slot["note"] = str(note or "")[:_MAX_ERROR] if exc is None else ""
    except Exception:                                       # noqa: BLE001
        pass


def _attempt(o, call, note=None):
    """Run one output's dispatch and record how it went.

    A call that returns False did NOT act -- a capture paced out, an interval
    not yet elapsed, a sender that handed the work to its own thread and will
    report from there -- and is not counted: "fired 900 times" over a table
    that wrote four rows is worse than no number at all.

    The exception is re-raised untouched. Whether a failure is fatal, and what
    is logged about it, stays decided exactly where it was before; this only
    writes down what happened on the way past.
    """
    try:
        acted = call()
    except Exception as exc:                                # noqa: BLE001
        _record(o, exc)
        raise
    if acted is not False:
        # A callable note is asked AFTER the call, so it can say what happened.
        _record(o, note=note() if callable(note) else note)
    return acted


def telemetry(outputs=None):
    """The out_<id>_* stats for every output this app knows about.

    `outputs` is the CURRENT document list, so an output that has never fired
    still publishes its four keys with at=0. A card showing nothing at all
    cannot be told apart from a card whose page failed to load, and "never"
    is the answer an operator most needs on the day they wire something up.
    """
    with _results_lock:
        snapshot = {k: dict(v) for k, v in _results.items()}
    for o in outputs or []:
        oid = str((o or {}).get("id") or "").strip()
        if oid:
            snapshot.setdefault(oid, {"fired": 0, "ok": 0, "at": 0.0,
                                      "error": ""})
    out = {}
    for oid, slot in snapshot.items():
        out[f"out_{oid}_fired"] = slot["fired"]
        out[f"out_{oid}_ok"] = slot["ok"]
        out[f"out_{oid}_at"] = slot["at"]
        out[f"out_{oid}_error"] = slot["error"]
        if "note" in slot:
            out[f"out_{oid}_note"] = slot["note"]
    return out

#: Per-output capture pacing state: {output id: {"last", "ids", "seen"}}.
#: Module-level because outputs are re-read from config every few seconds --
#: keying by the output's own id keeps the pacing across those reloads.
_capture_state = {}
_MAX_TRACKED_IDS = 2000


def _capture_allowed(o, event, now=None):
    """Should this snapshot/record output act on THIS event?

    Capture outputs fire from triggers that can repeat every frame, so each
    carries a mode (core/services/detector_control.py CAPTURE_MODES):

      every    -- always (the original behaviour)
      per_id   -- once per tracked object: a person who stays in shot is
                  captured once, not once per frame
      interval -- at most once every interval_s seconds
      once     -- once per episode; re-arms only after interval_s seconds
                  with no further trigger, so one violation = one image

    Unknown modes fall through to "every": a pacing rule that cannot be read
    must not silently disable capture.
    """
    mode = str(o.get("mode") or "every")
    if mode == "every":
        return True
    now = time.time() if now is None else now
    try:
        gap = float(o.get("interval_s") or 5)
    except (TypeError, ValueError):
        gap = 5.0
    key = str(o.get("id") or o.get("type") or "capture")
    st = _capture_state.setdefault(key, {"last": 0.0, "ids": set(), "seen": 0.0})

    if mode == "interval":
        if now - st["last"] < gap:
            return False
        st["last"] = now
        return True

    if mode == "once":
        # A gap in triggering ends the episode; until then, stay silent.
        quiet = now - st["seen"] >= gap
        st["seen"] = now
        if st["last"] and not quiet:
            return False
        st["last"] = now
        return True

    if mode == "per_id":
        dets = (event.get("detail") or {}).get("detections") or []
        ids = {d.get("track_id") for d in dets if d.get("track_id") is not None}
        if not ids:
            # No tracker identity to key on (mock detector, tracking off):
            # fall back to interval pacing rather than firing every frame.
            if now - st["last"] < gap:
                return False
            st["last"] = now
            return True
        fresh = ids - st["ids"]
        if not fresh:
            return False
        st["ids"] |= fresh
        if len(st["ids"]) > _MAX_TRACKED_IDS:
            # Track ids only ever grow; forget the oldest half rather than
            # letting a long run consume memory.
            st["ids"] = set(sorted(st["ids"])[-(_MAX_TRACKED_IDS // 2):])
        st["last"] = now
        return True

    return True


def fire(outputs, event, hooks=None, stats=None):
    """Fire every enabled output whose trigger matches this event's type.

    An output may additionally carry `function`: it then fires ONLY on events
    raised by that analytics function (the workflow builder's Analytics ->
    Output wire). Function-trigger events carry detail.function (run.py);
    zone-violation events do not, so a bound output never fires on those.

    An output may instead carry `line`: it then fires only on a CROSSING of
    that line -- a zone event carries no direction, an alert carries no zone,
    a presence event carries neither, and another line fails the zone test.
    `classes` narrows it further to the counter's Class Filter. This is how a
    Data Table in crossing mode is bound, because a crossing event names no
    function (several counters may watch one line) and the row is about the
    line, not about any one of them.

    `hooks` supplies the output kinds this module cannot perform itself --
    {"snapshot": fn(o), "record": fn(o), "dataset": fn(o, cols)} -- because
    they need the FRAMES or the event store, which live in the loop (run.py
    wires them to app/capture.py and app/store.py).

    `stats` is this frame's published values, needed by a Dataset output to
    fill the columns wired to them.
    """
    etype = event.get("type")
    for o in outputs or []:
        if not o.get("enabled"):
            continue
        trigger = o.get("trigger") or "violation"
        if trigger != "any" and trigger != etype:
            continue
        fn = o.get("function")
        if fn and (event.get("detail") or {}).get("function") != fn:
            continue
        line = o.get("line")
        if line:
            detail = event.get("detail") or {}
            if str(detail.get("zone") or "") != str(line) or not detail.get("direction"):
                continue
            classes = o.get("classes")
            if classes and detail.get("detection_label") not in classes:
                continue
        # A workflow Delay node between the rule and this output: hold the
        # send. On a TIMER thread, never by sleeping -- this runs inside the
        # detection loop, and a five-second sleep here would stop detecting.
        try:
            wait = float(o.get("delay_s") or 0)
        except (TypeError, ValueError):
            wait = 0.0
        if wait > 0:
            # Snapshot the stats: a delayed Dataset row must record what was
            # true when the alert fired, not what happens to be true when the
            # timer runs out.
            _fire_later(o, hooks, event, wait, dict(stats or {}))
            continue
        try:
            _attempt(o, lambda: _fire_one(o, hooks, event, stats))
        except Exception as exc:                            # noqa: BLE001
            log.error("output %r failed: %s", o.get("id") or o.get("type"),
                      exc, exc_info=True, extra=faults.event(faults.SYS_OUTPUT_FAILED))


#: Timers for delayed outputs, so a shutdown can drop them and a runaway rule
#: cannot pile up unbounded threads.
_delayed = []
_delayed_lock = threading.Lock()

#: How many delayed sends may be in flight at once. A rule firing every frame
#: with a 30s delay would otherwise queue nine hundred timers; past this the
#: extra ones are dropped with a line in the log rather than taking the box
#: down. Pacing belongs in the output's own mode (once/interval).
MAX_DELAYED = 200


def _fire_later(o, hooks, event, wait, stats=None):
    with _delayed_lock:
        _delayed[:] = [t for t in _delayed if t.is_alive()]
        if len(_delayed) >= MAX_DELAYED:
            log.warning("output %r delayed send dropped - %d already "
                        "waiting; set a capture mode to pace it",
                        o.get("id"), MAX_DELAYED, extra=faults.event(faults.SYS_OUTPUT_FAILED))
            return

    def run():
        try:
            _attempt(o, lambda: _fire_one(o, hooks, event, stats))
        except Exception as exc:                            # noqa: BLE001
            log.error("output %r failed: %s", o.get("id") or o.get("type"),
                      exc, exc_info=True, extra=faults.event(faults.SYS_OUTPUT_FAILED))

    timer = threading.Timer(wait, run)
    timer.daemon = True
    with _delayed_lock:
        _delayed.append(timer)
    timer.start()


#: Per-publisher pacing: {output id: last publish time}. Module level for the
#: same reason as _capture_state -- outputs are re-read from config every few
#: seconds, so the schedule has to outlive the list.
_publish_state = {}


#: Output types that publish on a clock instead of firing on an event, mapped
#: to the sender that carries the value.
PUBLISH_TYPES = ("mqtt_publish", "webhook_publish")

#: How often a following PWM port is re-set. Four times a second is smooth to
#: the eye and nowhere near often enough to matter to the loop; setting it
#: every frame would be thirty writes a second for a lamp.
PWM_FOLLOW_S = 0.25


def publish(outputs, stats, now=None, hooks=None, dets=None):
    """Publish live values to MQTT or an HTTP endpoint on a schedule.

    The other MQTT/webhook outputs fire on an ALERT: something crossed a
    threshold, tell someone. This is the other half -- telemetry. A PLC or an
    MES wants the current count every few seconds whether or not anything is
    wrong, and "publish when a violation happens" cannot express that.

    Called once per frame; each publisher decides whether its interval has
    elapsed -- except an MQTT Publish, which has no interval of its own and
    follows the Trigger or Inject wired into it (_publish_follow). Failures
    print and are skipped: a broker or endpoint being down must never stall
    detection.
    """
    now = time.time() if now is None else now
    # A repeating Modbus Write whose node left the running set stops here.
    _repeat_sweep(outputs)
    for o in outputs or []:
        if not o.get("enabled"):
            continue
        # A Dataset on a timer or on change is the same idea as a publisher --
        # it says "here is the state now", not "something went wrong" -- so it
        # rides the same per-frame tick.
        if o.get("type") == "dataset":
            if o.get("mode") in ("every", "change"):
                try:
                    _attempt(o, lambda: _dataset_tick(o, stats, now,
                                                      hooks, dets))
                except Exception as exc:                    # noqa: BLE001
                    log.error("dataset %r failed: %s", o.get("id"), exc,
                              exc_info=True, extra=faults.event(faults.SYS_OUTPUT_FAILED))
            continue
        # A PWM port set to follow a value is the same idea as a publisher --
        # "here is the level now" -- except the subscriber is a lamp. It has
        # to run on the tick rather than on an alert: a brightness that only
        # updates when something goes wrong is not a brightness.
        if o.get("type") == "io_pwm" and str(o.get("when") or "alert") == "always":
            key = f"pwm:{o.get('id')}"
            if now - _publish_state.get(key, 0.0) >= PWM_FOLLOW_S:
                _publish_state[key] = now
                try:
                    _attempt(o, lambda: _io_pwm(o, stats))
                except Exception as exc:                    # noqa: BLE001
                    log.error("pwm %r failed: %s", o.get("id"), exc,
                              exc_info=True, extra=faults.event(faults.SYS_OUTPUT_FAILED))
            continue
        # A Modbus register holds the value NOW, for whenever a PLC happens to
        # scan. It cannot ride an alert: a PLC reads on its own clock and would
        # otherwise see whatever the last alert left behind, for as long as
        # nothing went wrong. Written every tick, unconditionally -- the store
        # write is a list assignment, cheaper than deciding whether to skip it.
        if o.get("type") == "modbus_reg":
            hook = (hooks or {}).get("modbus_reg")
            if hook:
                try:
                    _attempt(o, lambda: hook(
                        {"address": o.get("address"),
                         "kind": o.get("reg_kind") or "holding",
                         "scale": o.get("scale") or 1,
                         "value": (stats or {}).get(str(o.get("key") or ""))}))
                except Exception as exc:                    # noqa: BLE001
                    log.error("modbus register %r failed: %s", o.get("id"),
                              exc, exc_info=True,
                              extra=faults.event(faults.NET_MODBUS_NO_REPLY))
            continue
        # A Modbus Write with no trigger wired writes its value when it
        # CHANGES -- see _modbus_write_tick -- or, fed by Injects alone, on
        # each of their fires. Its triggered twin rides fire().
        # Every mode then gets its "keep sending" beat (_modbus_write_repeat),
        # AFTER the tick so a value written this tick restarts the clock.
        if o.get("type") == "modbus_write":
            try:
                if str(o.get("mode") or "change") in ("change", "fire"):
                    _modbus_write_tick(o, stats, now)
                _modbus_write_repeat(o, stats, now)
            except Exception as exc:                        # noqa: BLE001
                _record(o, exc)
                log.error("modbus write %r failed: %s", o.get("id"), exc,
                          exc_info=True,
                          extra=faults.event(faults.NET_MODBUS_NO_REPLY))
            continue
        # A Zone Switch FOLLOWS its wired value like a following PWM does --
        # armed while truthy -- so it rides the tick, not an alert. The hook
        # (run.py) is what actually reaches the zone engine; state is kept
        # here so the flip logs once per change, not thirty times a second.
        if o.get("type") == "zone_switch":
            skey = "zsw:" + str(o.get("id") or "")
            if now - _publish_state.get(skey, 0.0) >= 0.25:
                _publish_state[skey] = now
                try:
                    _attempt(o, lambda: _zone_switch(o, stats, hooks))
                except Exception as exc:                    # noqa: BLE001
                    log.error("zone switch %s failed: %s", o.get("id"), exc,
                              exc_info=True, extra=faults.event(faults.SYS_OUTPUT_FAILED))
            continue
        if o.get("type") not in PUBLISH_TYPES:
            continue
        key = str(o.get("id") or "publish")
        if o.get("type") == "mqtt_publish" and o.get("mode") in ("change", "fire"):
            # No clock of its own: what is wired in says when (below).
            due, value = _publish_follow(o, stats, now)
            if not due:
                continue
        else:
            # A webhook publisher, or an MQTT Publish compiled before the
            # node lost its own clock: published every interval_s.
            try:
                gap = max(0.2, float(o.get("interval_s") or 5))
            except (TypeError, ValueError):
                gap = 5.0
            if now - _publish_state.get(key, 0.0) < gap:
                continue
            _publish_state[key] = now
            if not o.get("key"):
                # Nothing wired in: the node's own payload, or the time (ms
                # since 1970, like an Inject's timestamp) when that is blank.
                value = o.get("payload") or int(now * 1000)
            else:
                value = (stats or {}).get(o.get("key"))
                if value is None and not o.get("send_missing"):
                    continue    # nothing to say yet; saying "null" is worse
        doc = {**o, "payload": _payload(o, value)}
        try:
            if o.get("type") == "webhook_publish":
                _attempt(o, lambda: _webhook(doc))
            else:
                # What went out and where, for the Debug node: "sent 42 to
                # plant/line1/count" is the line somebody checking a topic
                # wants to read beside "sent" -- or that it is QUEUED while
                # its MQTT Connection is (re)connecting (app/mqtt_bus.py).
                res = {}
                where = f"{str(doc['payload'])[:120]} to {doc.get('topic') or 'cytron/value'}"
                _attempt(o, lambda: res.setdefault("r", _mqtt(doc)),
                         note=lambda: (f"queued {where} (connecting)"
                                       if res.get("r") == "queued"
                                       else f"sent {where}"))
        except Exception as exc:                            # noqa: BLE001
            log.error("publish %r failed: %s", key, exc, exc_info=True,
                      extra=faults.event(faults.NET_UPSTREAM_TIMEOUT,
                                         output=key))


#: What each following MQTT Publish last sent: {output id: {"sig", "at"}}.
_follow_state = {}
#: The fastest a following MQTT Publish sends: a value that changes every
#: frame goes out at most five times a second, the newest one each time.
FOLLOW_MIN_S = 0.2


def _fire_beat(fid):
    """functions.fire_beat for rule `fid`: how many times it has fired (and
    an Interval Trigger re-asserted its payload). 0 when unknown."""
    if not fid:
        return 0
    try:
        try:
            from . import functions as _fns
        except ImportError:
            import functions as _fns
        return _fns.fire_beat(fid)
    except Exception:                                       # noqa: BLE001
        return 0


def _publish_follow(o, stats, now):
    """(due, value) for an MQTT Publish that follows what is wired into it
    (core/services/publish_follow.py; Tok Aba 2026-10-08: the interval comes
    from the Trigger or Inject in front of it, never from the Publish).

      fire    an Inject or a Schedule: each tick its `fired` is set, so its
              own repeat is the clock and a press is one publish
      change  anything else: when the value changes, and -- with `beat`, a
              Trigger or Condition in front -- each time that rule fires or
              an Interval Trigger re-checks and keeps its payload on, even
              with the same value. So Real-time sends the payload and then
              the idle one, Interval N sends again every N s while it holds,
              For N seconds sends once and then the idle one.

    The first value seen is published (the state at start, as a Modbus
    Write's first change is written). Paced at FOLLOW_MIN_S: a change inside
    it waits a tick and the newest value goes."""
    stats = stats or {}
    oid = str(o.get("id") or "publish")
    value = stats.get(o.get("key"))
    if o.get("mode") == "fire":
        try:
            fired = float(stats.get(o.get("fired")) or 0) > 0
        except (TypeError, ValueError):
            fired = False
        if not fired or (value is None and not o.get("send_missing")):
            return False, None
        _follow_state[oid] = {"sig": None, "at": now}
        return True, value
    if value is None and not o.get("send_missing"):
        return False, None              # nothing to say yet
    sig = (repr(value), _fire_beat(o.get("beat")))
    seen = _follow_state.get(oid)
    if seen is not None and (seen["sig"] == sig or now - seen["at"] < FOLLOW_MIN_S):
        return False, None
    _follow_state[oid] = {"sig": sig, "at": now}
    return True, value


#: Last state per zone-switch output id, so the flip logs on CHANGE only.
_zone_switch_state = {}


def _zone_switch(o, stats, hooks):
    """Arm or disarm a zone from the wired value. Runtime only: the hook
    flips the live engine's zone, never the saved config."""
    hook = (hooks or {}).get("zone_enable")
    if hook is None:
        return False
    value = (stats or {}).get(o.get("key"))
    # `action` says what a 1 on the wire does; anything else is its opposite.
    # ("invert" is the old spelling, kept so a graph saved before still runs.)
    action = str(o.get("action") or "").strip().lower()
    if action not in ("enable", "disable"):
        action = "disable" if o.get("invert") else "enable"
    on = bool(value) if action == "enable" else not bool(value)
    oid = str(o.get("id") or "zsw")
    if _zone_switch_state.get(oid) == on:
        return False
    _zone_switch_state[oid] = on
    hook(str(o.get("zone") or ""), on)
    log.info("zone switch: %s %s (value=%r)", o.get("zone"),
             "enabled" if on else "disabled", value)


def _payload(o, value):
    """The message body: the bare value, or a JSON object when asked for one.
    A bare number is what a PLC tag wants; JSON is what a broker-side consumer
    usually wants. Both are one line, so both are offered."""
    if o.get("format") == "json":
        import json                                         # noqa: PLC0415
        return json.dumps({"key": o.get("key"), "value": value,
                           "ts": round(time.time(), 3)})
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# dataset -- record a row of the operator's own columns
#
# Every other output here SENDS something and forgets it. A Dataset writes a
# row that is meant to be read back weeks later, so the value of each column
# has to be resolved the same way every time. The workflow compiler did the
# hard half already: a column dragged in from another node arrives here as the
# stat key it was dragged from, so this module never has to know the graph.
#
# The row travels as an ordinary event (detail.dataset + detail.cols) because
# this app has no database -- the master forks it into its own table on ingest
# (core/repos/datasets.py).
# ---------------------------------------------------------------------------

#: Per-dataset pacing and change detection: {output id: {"last", "cols"}}.
#: Module level for the same reason as _publish_state -- outputs are re-read
#: from config every few seconds and the schedule has to outlive the list.
_dataset_state = {}
_MAX_DATASET_STATE = 200


#: Short tag for THIS run of the app, prefixed onto every recorded track id.
#:
#: The tracker numbers from 1 again on every restart, so a bare id is only
#: unique within one run: yesterday's object 3 and today's object 3 are
#: different objects that a distinct count over a day would silently merge
#: into one. Prefixing makes "how many came through" answerable over any
#: range instead of only since the last restart.
#:
#: Derived from the clock and the process id rather than random, so two runs
#: cannot collide and the value says roughly when it was minted.
_RUN_ID = f"{int(time.time()) % 1679616:04x}{os.getpid() % 1296:03x}"


def _track_id(det):
    """A detection's identity as it is RECORDED: run-scoped, or None.

    None when the tracker has not confirmed the object yet -- a row that says
    "somebody, no idea who" is honest, where inventing an id would put a
    phantom into every count built on identity."""
    tid = (det or {}).get("track_id")
    if tid is None:
        tid = (det or {}).get("tracking_id")
    return None if tid is None else f"{_RUN_ID}-{tid}"


def _tracked(event, dets):
    """The confirmed objects a row could be about, most confident first.

    Prefers the triggering event's own detections -- an alert is about what it
    saw -- and falls back to whatever the clock-paced path handed in."""
    pool = ((event or {}).get("detail") or {}).get("detections") or dets or []
    return sorted([d for d in pool if _track_id(d) is not None],
                  key=lambda d: d.get("confidence", 0), reverse=True)


def _event_field(event, field, det=None):
    """One column's value taken from the event that triggered the row.

    Mirrors the promotion order the master uses when it expands an event
    (core/repos/events.py): the envelope first, then `detail`, then the
    strongest detection -- because a per-frame event is about its most
    confident object. Kept in step with workflow.DATASET_EVENT_FIELDS."""
    if field == "ts":
        # Also the one field a timer-paced row can fill: there is no event.
        if not event:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        return event.get("ts")
    if not event:
        return None
    detail = event.get("detail") or {}
    dets = detail.get("detections") or []
    # The most confident object -- but an IDENTIFIED one first. tracker.py
    # only stamps track_id once a track is confirmed, so a brand-new
    # high-confidence detection outranks a tracked one and the row records a
    # count with nobody attached to it, even though a tracked object is right
    # there. Identity is what the row is for; confidence only picks between
    # the objects that have it.
    # The object this row is ABOUT, when the caller names one (one row per
    # tracked object). Otherwise the most confident -- but an IDENTIFIED one
    # first, since tracker.py stamps a track only once confirmed and a
    # brand-new high-confidence detection would otherwise outrank the person
    # who has been standing there, leaving the row with no identity at all.
    top = det if det is not None else (
        max(dets, key=lambda d: (d.get("track_id") is not None,
                                 d.get("confidence", 0)),
            default={}) if dets else {})
    if field == "track_id":
        # A per-stay event (Time in Zone with PID) NAMES its object in
        # detail.track -- that object has just LEFT the zone, so the most
        # confident detection still in frame is exactly the wrong answer.
        # Same run-scoped prefix as everywhere else.
        named = detail.get("track")
        if named is not None:
            return f"{_RUN_ID}-{named}"
        # Recorded run-scoped, so a restart cannot make two objects share one
        # id. Returned before the alias walk below, which would find the bare
        # number on the detection.
        return _track_id(top)
    aliases = {"detection_label": ("detection_label", "object_class", "label"),
               "track_id": ("track_id", "tracking_id"),
               "zone": ("zone", "entered_zone"),
               "camera": ("camera", "camera_name"),
               "model": ("model", "ai_model")}
    keys = aliases.get(field, (field,))
    sources = (event, detail, top) if field != "detection_label" else (detail, top)
    for src in sources:
        for key in keys:
            value = src.get(key)
            if value not in (None, ""):
                return value
    return None


def _as_iso(value):
    """A datetime column's value, normalised to ISO-8601, or None.

    Every datetime that reaches the database goes through here, so the stored
    text is always sortable and comparable. That is what lets the query side
    treat it as plain text instead of casting it (core/repos/datasets.py
    _ref) -- and Postgres has no try_cast, so one badly-formed value would
    otherwise break every query over the whole table.

    A value that is not a time is stored as nothing rather than as text: a
    datetime column holding "shift B" sorts wrongly for ever afterwards."""
    if value is None or value == "":
        return None
    # A number is an epoch -- what a sensor or an expression produces.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(value)))
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    # Already ISO (the event's own ts, and anything this function wrote
    # before): keep it exactly, including any timezone offset.
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:32]
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y", "%H:%M:%S"):
        try:
            parsed = time.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%H:%M:%S":       # a time of day: today, at that time
            today = time.localtime()
            parsed = time.struct_time(
                (today.tm_year, today.tm_mon, today.tm_mday,
                 parsed.tm_hour, parsed.tm_min, parsed.tm_sec, 0, 0, -1))
        return time.strftime("%Y-%m-%dT%H:%M:%S", parsed)
    return None


def _dataset_row(o, event=None, stats=None, det=None):
    """The {column: value} dict for one row, per the node's definition.

    A column whose value is missing is written as None rather than skipped:
    a row with a hole in it still says WHEN it happened, and a dataset whose
    columns come and go cannot be charted."""
    stats = stats or {}
    cols = {}
    for col in o.get("columns") or []:
        name = col.get("name")
        if not name:
            continue
        src = col.get("src") or "wire"
        if src in ("wire", "stat"):
            value = stats.get(col.get("key"))
        elif src == "event":
            # A ZONE-SCOPED id column records only who is actually inside
            # that zone. Somebody merely in frame reads as nothing -- the
            # honest answer to "who is in the zone" when they are not.
            if col.get("zone") and col.get("field") == "track_id":
                value = _zone_member_id(det, event, col["zone"])
            elif col.get("field") == "in_zone_s":
                # The LIVE per-person clock: how long this row's object has
                # been inside the column's zone, right now. Stamped by the
                # zone engine on every member (det["zone_in"]).
                value = _zone_elapsed(det, event, col.get("zone") or "")
            else:
                value = _event_field(event, col.get("field"), det)
        else:
            value = col.get("value")
        # The declared type is a promise about the value, so it is kept on the
        # way in rather than checked on the way out. A number column holding
        # "n/a" breaks every average over it afterwards, silently and for ever.
        kind = col.get("type")
        if value is None:
            pass
        elif kind == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        elif kind in ("datetime", "timestamp"):
            value = _as_iso(value)
        elif kind == "date":
            iso = _as_iso(value)
            value = iso[:10] if iso else None
        elif kind == "boolean":
            if isinstance(value, bool):
                pass
            elif isinstance(value, (int, float)):
                value = bool(value)
            else:
                value = str(value).strip().lower() in ("1", "true", "yes", "on")
        elif kind == "json":
            # Kept whole. Anything that is not already structured is stored as
            # the text it is, rather than guessed at.
            if not isinstance(value, (dict, list)):
                value = str(value)[:2000]
        else:                                   # text, image, uuid
            value = str(value)[:500]
        cols[name] = value
    return cols


def _zone_member_id(det, event, zone_id):
    """The recorded id for a ZONE-scoped track-id column, or None.

    Membership is stamped by app/zones.py on every area zone's members
    (det["zones"]); violation zones also stamp the legacy det["zone"].
    When the row is ABOUT one object (fan-out), that object must be inside;
    when no object was named (a single-row write), the best-identified
    member of the zone answers -- never somebody who is merely in frame.

    A CROSSING event names its object and its line (detail.track and
    detail.zone, run.py). Lines stamp no det["zones"] membership, so without
    this a Line Counter's OID port wired onto a column -- which the compiler
    scopes to the line id -- read blank on the very row about that crossing.
    Scoped to direction-bearing events so stay rows and area rows behave
    exactly as they did.
    """
    detail = (event or {}).get("detail") or {}
    if (detail.get("direction") and detail.get("track") is not None
            and str(detail.get("zone") or "") == str(zone_id)):
        return f"{_RUN_ID}-{detail['track']}"

    def hit(d):
        # Inside the zone AND counting: a minimum stay the operator set on the
        # counter is a statement about who is in that zone as far as this
        # system is concerned, so an object still serving it is not an answer.
        return ((zone_id in (d.get("zones") or ())
                 or str(d.get("zone") or "") == zone_id)
                and zone_id not in (d.get("zone_wait") or ()))
    if det is not None:
        return _track_id(det) if hit(det) else None
    dets = [d for d in ((event or {}).get("detail") or {}).get("detections") or []
            if hit(d)]
    if not dets:
        return None
    top = max(dets, key=lambda d: (d.get("track_id") is not None,
                                   d.get("confidence", 0)))
    return _track_id(top)


def _zone_elapsed(det, event, zone_id):
    """Seconds the row's object has been inside `zone_id`, or None.

    With no named object (a single-row write), the LONGEST current stay
    among the zone's members answers -- the person who has been in there
    the longest is who a single timer cell is about.
    """
    if det is not None:
        return ((det.get("zone_in") or {}).get(zone_id))
    stays = [(d.get("zone_in") or {}).get(zone_id)
             for d in ((event or {}).get("detail") or {}).get("detections") or []]
    stays = [s for s in stays if s is not None]
    return max(stays) if stays else None


def _records_identity(o):
    """Whether this table has a column that names WHICH object a row is about."""
    return any((c.get("src") == "event" and c.get("field") == "track_id")
               for c in (o.get("columns") or []))


def _identity_zone(o):
    """The zone the table's id column is scoped to, or "" for a frame column.

    A zone-scoped id column means "who is in that zone", so the counter that
    watches that zone is the one whose timing this table must respect.
    """
    for c in (o.get("columns") or []):
        if c.get("src") == "event" and c.get("field") == "track_id":
            return str(c.get("zone") or "")
    return ""


def _counts_yet(det, zone_id):
    """Whether this object COUNTS, by the timing of the rule that watches it.

    Reads the mark app/functions.py stamps while it works out membership:
    an object inside a zone with a minimum stay is `zone_wait` until it has
    been there long enough. Nothing stamped means no timing is configured --
    every counter's default -- and then everybody counts, exactly as before.
    """
    if zone_id:
        return zone_id not in ((det or {}).get("zone_wait") or ())
    return not (det or {}).get("count_wait")


def _dataset_rows(o, event=None, stats=None, dets=None):
    """The rows one write produces: ONE PER TRACKED OBJECT.

    A row holds a single identity, so one row per write can only ever name one
    of the people in frame -- and whichever tie-break picks it (longest-lived,
    newest, most confident) hides the others. With three people in view the
    count says three and the id column says the same one every time, so a
    distinct count over it undercounts by exactly the people it never
    mentioned.

    So a table that records identity gets a row per object instead, sharing
    the write's timestamp and its counts. That is also the shape the question
    wants: "how many came through" is a question about objects, and every
    object is now in the table to be counted.

    A table with no identity column is unchanged -- one row, as before. There
    is nothing to fan out over, and multiplying rows would only inflate the
    counts made from them."""
    if not _records_identity(o):
        return [_dataset_row(o, event, stats)]
    # A per-stay event already names the one object it is about
    # (detail.track): one stay, one row. Fanning out over whoever happens to
    # be in frame would file this stay's duration under other people's ids.
    if ((event or {}).get("detail") or {}).get("track") is not None:
        return [_dataset_row(o, event, stats)]
    if _group_mode(o):
        return _group_rows(o, event, stats, dets)
    objects = _tracked(event, dets)
    # A "new object" Trigger's event NAMES who it is about (detail.new: the
    # ids that just arrived, after the Current OID guard). Rows for them only:
    # the person already standing there was filed when THEY arrived, and
    # writing them again with every newcomer is the repeat the operator sees
    # as the same ids over and over (pcb_sensata, 2026-10-13). Matched on the
    # bare tracker id, as functions.py _live_oids reports it; a run-prefixed
    # id is accepted too. Events without detail.new fan out as before.
    new = ((event or {}).get("detail") or {}).get("new")
    if isinstance(new, (list, tuple, set)) and new:
        wanted = {f"{_RUN_ID}-{t}" for t in new} | {str(t) for t in new}
        objects = [d for d in objects if _track_id(d) in wanted]
        if not objects:
            # Named, but none of them in the detections this event carries:
            # no row, rather than a row filed under somebody else.
            return []
    # A counter told to wait ("counts as 1 once it has stayed 10s") must be
    # obeyed by the TABLE too. Filing a row the moment somebody walks in, while
    # the count sits at zero for another ten seconds, is the setting working in
    # one place and not the other -- which reads as it not working at all.
    zone_id = _identity_zone(o)
    counting = [d for d in objects if _counts_yet(d, zone_id)]
    if objects and not counting:
        # Somebody is there, nobody counts yet. No row: the table's question is
        # WHICH object, and "none of them, so far" is answered by silence. A
        # blank-identity row here would say an unidentified object was counted.
        return []
    objects = counting
    if not objects:
        # Nothing confirmed: still record the state, with the identity blank.
        # A count with nobody named is honest; skipping the row would lose the
        # count as well, and "nobody was tracked" is itself worth knowing.
        return [_dataset_row(o, event, stats)]
    return [_dataset_row(o, event, stats, det) for det in objects]


# ---------------------------------------------------------------------------
# "Object IDs in a row": one row for the whole group (pcb_sensata, 2026-10-13)
#
# A panel tray holds two boards, and the question the operator asks of the
# table is "which SET was on the tray" -- not two rows sharing a timestamp
# that a reader has to glue back together. Set on the node (ids_in_row =
# "group"), the identity column holds every counting object's id, sorted and
# joined with ", ", and a row is saved only when that set differs from the
# last one saved for the table: one added, one replaced or one gone is a new
# row, the same set again (in any order) is nothing.
#
# The memory is this process's. After an app restart the first group is
# always written -- but the tracker's ids carry this run's prefix
# (_RUN_ID), so it is genuinely a different set of ids, not a repeat of the
# stored one. Reading the table back would never find a match anyway.
# ---------------------------------------------------------------------------

#: The id set last SAVED per group-mode table: {output id: frozenset}.
_dataset_groups = {}

#: How the ids in a group cell are separated. The Trigger's Current OID guard
#: splits on the comma (functions.py _oid_same), so the two must agree.
GROUP_SEP = ", "


def _group_mode(o):
    """Whether this table lists the whole group in one row. Needs an
    identity column -- the compiler drops the setting without one."""
    return (str(o.get("ids_in_row") or "") == "group"
            and str(o.get("mode") or "alert") != "crossing"
            and _records_identity(o))


def _natural_key(text):
    """Sort "k7-10" after "k7-9": digits compare as numbers, so a group
    reads in the order a person would write it and {5, 4} == {4, 5}."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p)
            for p in re.split(r"(\d+)", str(text))]


def _join_ids(ids):
    """The group cell: de-duplicated, naturally sorted, ", "-joined, and cut
    at a whole id under the column's 500-character cap rather than mid-id."""
    out = ""
    for i in sorted(set(ids), key=_natural_key):
        nxt = i if not out else out + GROUP_SEP + i
        if len(nxt) > 500:
            break
        out = nxt
    return out or None


def _group_rows(o, event, stats, dets):
    """The one row a group-mode write produces, or [] when nobody counts yet.

    Every object present AND counting -- deliberately not just the newly
    arrived ones (detail.new): the row is the whole tray, so two boards on it
    are two ids however many of them just came. Zone-scoped id columns list
    only that zone's members, as a per-object row would. Every other column
    reads the event exactly as a single-row write does."""
    objects = _tracked(event, dets)
    zone_id = _identity_zone(o)
    counting = [d for d in objects if _counts_yet(d, zone_id)]
    if objects and not counting:
        return []
    row = _dataset_row(o, event, stats)
    for col in o.get("columns") or []:
        if col.get("src") == "event" and col.get("field") == "track_id" \
                and col.get("name"):
            zone = col.get("zone")
            ids = [(_zone_member_id(d, event, zone) if zone else _track_id(d))
                   for d in counting]
            row[col["name"]] = _join_ids(i for i in ids if i)
    return [row]


def _group_ids(o, cols):
    """The set of ids a group row names, read back from its identity cell."""
    return frozenset(p.strip() for p in _identity(o, cols).split(",")
                     if p.strip())


def _group_fresh(o, cols, commit=True):
    """Is this group row's id set different from the last one SAVED for the
    table? Remembered when `commit` -- i.e. only once it is really written,
    so a row held back (rate limit, cap) is still news on the next try."""
    key = str(o.get("id") or o.get("dataset") or "dataset")
    ids = _group_ids(o, cols)
    if _dataset_groups.get(key) == ids:
        return False
    if commit:
        if key not in _dataset_groups and len(_dataset_groups) >= _MAX_DATASET_STATE:
            _dataset_groups.clear()
        _dataset_groups[key] = ids
    return True


def _dataset(o, hooks=None, event=None, stats=None):
    """Write now (the alert path): a row per object the event was about --
    or, for a group-mode table, one row, and only when the group changed."""
    hook = (hooks or {}).get("dataset")
    if not hook:
        return False
    group = _group_mode(o)
    written = 0
    for cols in _dataset_rows(o, event, stats):
        if group and not _group_fresh(o, cols):
            continue
        hook(o, cols)
        written += 1
    return bool(written)


def _live_event(dets):
    """A stand-in event for the clock-paced paths, carrying what is in frame.

    An `every`/`change` row is not caused by an event, so an event-sourced
    column (track_id above all) has nothing to read. But the objects ARE
    known -- the tracker stamped them this same frame -- and "count 1 with no
    idea who" is a row that cannot answer the question it was recorded for.
    Shaped like a real event's envelope so _event_field needs no second path.

    Only the identity-bearing fields are meaningful here; `zone` or `label`
    stay empty, because no zone was entered and nothing was alerted -- this
    is the state now, not something that happened."""
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "detail": {"detections": list(dets or [])}}


#: Objects whose last-written row "on change" remembers, per table. Bounded:
#: a busy scene mints ids for ever and the change detector must not grow with
#: them. Clearing it costs one duplicate row per object still in view.
_MAX_DATASET_IDENTITIES = 500

#: The backstop under "on change" with no rate limit set. Every row is also an
#: entry in events.jsonl, which is re-read in full on each restart, so a table
#: whose value genuinely moves every frame must still not be able to write at
#: frame rate for ever. This is not a pacing knob -- it is the ceiling that
#: makes "no rate limit" a safe thing to offer.
_MAX_DATASET_ROWS_PER_S = 20


def _identity(o, cols):
    """Which object a row is about, as the change detector keys on it.

    Empty string for a table that records no identity -- there is exactly one
    row per write there, so they all share the one slot and the mode behaves
    as it always did."""
    for col in o.get("columns") or []:
        if col.get("src") == "event" and col.get("field") == "track_id":
            return str((cols or {}).get(col.get("name")) or "")
    return ""


def dataset_upsert_key(o, cols):
    """The column a write is KEYED on when the table is set to "update the
    existing row" when the same object is seen again, or None to insert.

    The app decides only WHICH column names the object; the master does the
    update (core/repos/datasets.py _upsert_rows), because only it has the
    database. None -- an ordinary new row -- when the setting is off (the
    default, and every table saved before it existed), when the table has no
    identity column, or when this row's identity is blank: a row about
    nobody in particular has no earlier row to be.

    Applies to every write mode alike, since all of them reach the one hook:
    an alert or a timer-paced Trigger refreshes the object's row each time it
    fires, `every` refreshes each object in view on its clock, `change`
    refreshes it when its own values move, and `crossing` keeps one row per
    object holding its LAST crossing. The OID carries this run's prefix, so
    the same tracker number after a restart is a different object and a new
    row -- which is what it is.

    A group-mode table (ids_in_row = "group") keys on the WHOLE group cell
    ("k7-4, k7-5"): only a row with exactly that group is refreshed. Since an
    unchanged group is never written again (_group_fresh), this matters only
    when the in-process memory was lost; it never merges two groups."""
    if str(o.get("on_repeat") or "insert") != "update":
        return None
    for col in o.get("columns") or []:
        if col.get("src") == "event" and col.get("field") == "track_id":
            name = col.get("name")
            if name and (cols or {}).get(name) not in (None, ""):
                return name
            return None
    return None


def _changed_part(o, cols):
    """What "on change" compares.

    Never the clock: a timestamp column is different every single frame, so
    including it would make every tick a change and turn "on change" into
    "every min_interval_s" -- the mode would silently stop meaning what it
    says. Time is already on every row as its own `ts`; a ts COLUMN is there
    to be read, not to decide when to write.

    And when the table records an IDENTITY, only that object's own columns.
    A row keyed to a track id is ABOUT that object, while a stat column holds
    a scene-wide number -- how many are in frame, how many are in the zone.
    Comparing the whole row meant one such number flickering re-wrote a row
    for EVERY object standing still beside it, which is how one person in
    view produced a page of rows a second (live_counting 1/0/1, same id, over
    and over). A scene number moving is news about the scene, not about
    person -10; a table that wants the scene's trend asks for a row on a
    timer instead.

    Without an identity column there is one row per write and nothing to key
    on, so the whole row is compared as it always was."""
    cols_spec = o.get("columns") or []
    times = {c.get("name") for c in cols_spec
             if c.get("type") in ("timestamp", "date")
             or (c.get("src") == "event" and c.get("field") == "ts")}
    keyed = any(c.get("src") == "event" and c.get("field") == "track_id"
                for c in cols_spec)
    if keyed:
        own = {c.get("name") for c in cols_spec if c.get("src") == "event"}
        return {k: v for k, v in (cols or {}).items()
                if k in own and k not in times}
    return {k: v for k, v in (cols or {}).items() if k not in times}


def _dataset_tick(o, stats, now, hooks, dets=None):
    """The timer and on-change paths, called once per frame from publish().

    `every` writes on a schedule whether or not anything happened; `change`
    writes the moment a value actually moves.

    The change test runs EVERY FRAME. It used to run only once per
    `min_interval_s`, which quietly turned the mode into "sample every N
    seconds and write if it differs" -- and a zone visit shorter than the
    sample (0.6s visits against a 5s sample) was simply never seen. The rate
    limit is now per OBJECT and only asked about after a change is found, so
    it damps a jittering float without ever hiding an arrival."""
    hook = (hooks or {}).get("dataset")
    if not hook:
        return False
    key = str(o.get("id") or "dataset")
    group = _group_mode(o)
    state = _dataset_state.get(key)
    if state is None:
        if len(_dataset_state) >= _MAX_DATASET_STATE:
            _dataset_state.clear()      # a config churning ids must not leak
        state = _dataset_state[key] = {"last": 0.0, "cols": {}, "seen": {},
                                       "sec": 0.0, "n": 0}

    mode = o.get("mode")
    if mode == "every":
        try:
            gap = max(5.0, float(o.get("interval_s") or 60))
        except (TypeError, ValueError):
            gap = 60.0
        if now - state["last"] < gap:
            return False
        rows = _dataset_rows(o, _live_event(dets), stats, dets)
        if group:
            # The clock samples the group; the same group is not a new row.
            rows = [r for r in rows if _group_fresh(o, r, commit=False)]
    elif group:                                             # change, group
        # One row about the whole group, so "changed" means the SET changed
        # -- compared with the last set saved, not a per-id memory, which
        # would call A,B -> A -> A,B "seen before". The floor is the table's.
        rows = [r for r in _dataset_rows(o, _live_event(dets), stats, dets)
                if _group_fresh(o, r, commit=False)]
        try:
            floor = max(0.0, float(o.get("min_interval_s") or 0))
        except (TypeError, ValueError):
            floor = 0.0
        if not rows or (floor and now - state["last"] < floor):
            return False
    else:                                                   # change
        try:
            floor = max(0.0, float(o.get("min_interval_s") or 0))
        except (TypeError, ValueError):
            floor = 0.0
        # Changed PER OBJECT, not per write. One person moving is not a reason
        # to re-record everyone standing still beside them, and -- the other
        # way round -- a new arrival must not be suppressed just because the
        # write as a whole looks like the last one.
        #
        # The floor is per object too, and applies only AFTER a change is
        # found: an id nobody has written before is written now, whatever the
        # rest of the table did a moment ago.
        rows = []
        for r in _dataset_rows(o, _live_event(dets), stats, dets):
            ident = _identity(o, r)
            if _changed_part(o, r) == state["cols"].get(ident):
                continue
            if floor and ident in state["seen"] \
                    and now - state["seen"][ident] < floor:
                continue
            rows.append(r)
        if not rows:
            return False
        # The ceiling that makes a zero floor safe -- see the constant.
        if int(now) != int(state["sec"]):
            state["sec"], state["n"] = now, 0
        room = _MAX_DATASET_ROWS_PER_S - state["n"]
        if room <= 0:
            return False
        if len(rows) > room:
            log.warning("dataset %s: %d rows dropped this second (cap %d/s)",
                        o.get("dataset") or key, len(rows) - room,
                        _MAX_DATASET_ROWS_PER_S)
            rows = rows[:room]
        state["n"] += len(rows)
    state["last"] = now
    if len(state["cols"]) >= _MAX_DATASET_IDENTITIES:
        state["cols"].clear()           # a scene churning ids must not leak
        state["seen"].clear()
    for cols in rows:
        ident = _identity(o, cols)
        state["cols"][ident] = _changed_part(o, cols)
        state["seen"][ident] = now
        if group:
            _group_fresh(o, cols)                # remembered: it is written
        hook(o, cols)
    return bool(rows)


def _fire_one(o, hooks=None, event=None, stats=None):
    """Dispatch ONE output document. True when something was actually done,
    False when this call decided to do nothing (paced out, outside its send
    window, no hook wired) or handed the work to a sender's own thread --
    which is what _attempt counts on to keep out_<id>_fired honest."""
    kind = o.get("type")
    if kind == "dataset":
        # Records a ROW rather than sending a message, so it needs both the
        # event (for context columns) and the frame's stats (for value
        # columns) -- handled here rather than in the chain below, which only
        # ever gets the document.
        #
        mode = o.get("mode") or "alert"
        if mode == "crossing":
            # EXACTLY ONE row per crossing, bypassing _dataset_rows on
            # purpose: its identity fan-out would file one untracked crossing
            # under every tracked bystander in frame, and its "nothing
            # confirmed" branch would hand the row the most confident
            # stranger's id. The event names its object (detail.track, run.py)
            # and carries only that box when the tracker knew it; otherwise
            # det = {} makes the id blank, which is the honest answer for a
            # crossing the tracker could not name. Nothing here compares this
            # row with the last one -- two people through the same door in
            # one frame are two events and two rows.
            detail = event.get("detail") or {}
            dets = detail.get("detections") or []
            det = dets[0] if (detail.get("track") is not None and len(dets) == 1) else {}
            hook = (hooks or {}).get("dataset")
            if hook:
                hook(o, _dataset_row(o, event, stats, det))
            return bool(hook)
        # ONLY for a table that asked to be written on an alert. The other two
        # modes compile with trigger "any" (they are paced by the clock, not
        # by a condition), so without this check every event reaching fire()
        # also wrote a row -- past the interval floor and past the change
        # test, which is precisely the two things those modes are. The clock
        # modes still return here; crossing rows have already been written.
        if mode != "alert":
            return False
        return _dataset(o, hooks, event, stats)
    if kind == "gpio":
        return _gpio(o)
    elif kind == "io_out":
        return _io_out(o)
    elif kind == "io_pwm":
        return _io_pwm(o, stats)
    elif kind == "mqtt":
        return _mqtt(o)
    elif kind == "modbus":
        return _modbus(o)
    elif kind == "modbus_write":
        # Only the TRIGGERED kind fires here; the change-driven one compiles
        # with trigger "any" and is paced by publish(), so every event that
        # reaches fire() must not also write it.
        if str(o.get("mode") or "change") != "trigger":
            return False
        return _modbus_write_fire(o, stats)
    elif kind == "webhook":
        return _webhook(o)
    elif kind == "email":
        # Outside its cooldown or its send window: no message was sent, so no
        # attempt is counted. An output muted by its own schedule reading
        # "fired 300 times" is the opposite of the truth.
        if not _message_allowed(o):
            return False
        return _email(o, event, stats)
    elif kind == "telegram":
        if not _message_allowed(o):
            return False
        return _telegram(o, event, stats)
    elif kind in ("snapshot", "record"):
        # Capture outputs pace themselves (once per object, every N seconds,
        # once per episode) -- see _capture_allowed.
        if not _capture_allowed(o, event or {}):
            return False
        hook = (hooks or {}).get(kind)
        if hook:
            # The EVENT goes with it: it carries which object caused this
            # firing, which is what "tag with the object id" needs and what
            # the output dict alone can never say.
            hook(o, event or {})
        return bool(hook)
    else:
        # A kind this build cannot dispatch did nothing, and must not report
        # that it fired: an output claiming to have sent something nobody can
        # send is the most misleading answer this could give.
        return False


def _gpio(o):
    """Drive a GPIO pin (a tower light, relay, horn). Latches on, or pulses for
    pulse_ms then releases. Uses gpiozero (Pi 5 friendly)."""
    pin = int(o.get("pin") or 0)
    if pin <= 0:
        raise RuntimeError("no GPIO pin set")
    try:
        from gpiozero import LED                            # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("gpiozero not installed (pip install gpiozero lgpio)") from e
    led = _gpio_cache.get(pin)
    if led is None:
        led = LED(pin, active_high=bool(o.get("active_high", True)))
        _gpio_cache[pin] = led
    pulse_ms = int(o.get("pulse_ms") or 0)
    led.on()
    if pulse_ms > 0:
        threading.Timer(pulse_ms / 1000.0, led.off).start()


def _io_out(o):
    """Drive one of the board's named digital outputs (DO0, DO1, L0, L1,
    BUZZER).

    Different from the raw `gpio` output above in the one way that matters on
    an industrial box: it addresses the port by its SILKSCREEN name and lets
    the board decide the pin, so a workflow says "close DO0" and stays true if
    the pin behind DO0 ever moves. `action` is what to do when the rule fires:

        on      -- latch it on and leave it (the relay stays closed)
        off     -- latch it off; the pair makes "on when X, off when Y"
        pulse   -- on for pulse_ms, then off (a horn blip, a PLC strobe)
        toggle  -- flip whatever it is now

    A latched output is held by a long-lived device (buses.gpio_hold), because
    a relay told to close has to stay closed after this function returns.
    """
    from app import iriv                                    # noqa: PLC0415

    port = str(o.get("port") or "").upper()
    action = str(o.get("action") or "on").lower()
    active_high = bool(o.get("active_high", True))
    if action == "toggle":
        now = iriv.port_state(port)
        value = not bool(now)
    elif action in ("off", "0", "false"):
        value = False
    else:
        value = True
    ok, message = iriv.set_port(port, value, active_high=active_high)
    if not ok:
        raise RuntimeError(f"{port}: {message}")
    pulse_ms = int(o.get("pulse_ms") or 0)
    if action == "pulse" and pulse_ms > 0:
        threading.Timer(pulse_ms / 1000.0,
                        lambda: iriv.set_port(port, not value,
                                              active_high=active_high)).start()


def _io_pwm(o, stats=None):
    """Drive L0/L1 at a percentage.

    Two sources for the number, because both are real: a FIXED percent ("go to
    40% when this alerts"), or the wired value itself scaled into a percent
    ("brighter the fuller the queue"). The scaling is explicit -- from `in_min`
    to `in_max` -- because a metric's natural range is a thing only the
    operator knows, and a silent 0..100 assumption would look like a broken
    lamp on a metric that only ever reaches 12.
    """
    from app import iriv                                    # noqa: PLC0415

    port = str(o.get("port") or "").upper()
    if str(o.get("source") or "fixed").lower() == "value":
        key = str(o.get("key") or "").strip()
        raw = (stats or {}).get(key)
        if raw is None:
            raise RuntimeError(f"{port}: no value for {key or 'the wired metric'}")
        lo = float(o.get("in_min") or 0)
        hi = float(o.get("in_max") or 100)
        span = (hi - lo) or 1.0
        percent = (float(raw) - lo) / span * 100.0
    else:
        percent = float(o.get("percent") or 0)
    percent = max(0.0, min(100.0, percent))
    ok, message = iriv.set_pwm(port, percent, int(o.get("frequency") or 1000))
    if not ok:
        raise RuntimeError(f"{port}: {message}")


def _mqtt(o):
    """Publish a message (annunciate to a PLC/SCADA/broker)."""
    if o.get("conn"):
        # Through the named MQTT Connection's one shared client
        # (app/mqtt_bus.py): no connect per message, and the login is the
        # Connection's -- never in this document.
        return mqtt_bus.publish(o["conn"], o.get("topic") or "cytron/alert",
                                str(o.get("payload") or "1"),
                                retain=bool(o.get("retain", False)))
    try:
        import paho.mqtt.publish as publish                 # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            "paho-mqtt not installed - re-run ./run.sh (it installs new "
            "dependencies) or: .venv/bin/pip install paho-mqtt") from e
    host, port = o.get("host") or "127.0.0.1", int(o.get("port") or 1883)
    # This box's own broker may require a login (Settings > MQTT Broker); the
    # node carries none, so it is read here rather than compiled into config.
    login = broker_login.for_address(host, port)
    publish.single(o.get("topic") or "cytron/alert",
                   payload=str(o.get("payload") or "1"),
                   hostname=host, port=port,
                   auth=({"username": login[0], "password": login[1]}
                         if login else None),
                   # Retained: a subscriber connecting later is handed the last
                   # value immediately instead of waiting for the next publish.
                   # Only meaningful for telemetry, so only telemetry sets it.
                   retain=bool(o.get("retain", False)))


#: Headers urllib computes from the request itself. A hand-edited
#: config.local.toml never passes through the API's validator, so the engine
#: refuses them again here -- overriding Content-Length or Transfer-Encoding is
#: how a stored document turns into request smuggling.
_ENGINE_HEADERS = ("host", "content-length", "transfer-encoding")


def _headers(o):
    """The header dict for a webhook request: JSON content type, then the
    document's own rows, then the bearer token -- which wins over an explicit
    Authorization row, because the token field is the one an operator edits
    when rotating a credential."""
    headers = {"Content-Type": "application/json"}
    for row in (o.get("headers") or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or name.lower() in _ENGINE_HEADERS:
            continue
        headers[name] = str(row.get("value") or "")
    token = str(o.get("token") or "").strip()
    if token:
        # Case matters: urllib title-cases what IT adds, not what we pass, so a
        # row named "authorization" would otherwise sit beside this one.
        for name in [k for k in headers if k.lower() == "authorization"]:
            headers.pop(name)
        headers["Authorization"] = f"Bearer {token}"
    return headers


#: Webhook outputs already told they point at this box: one log line each,
#: not one per fire.
_refused_once = set()


def _webhook(o):
    """POST/GET a URL (REST endpoint, alerting service). Stdlib only -- the app
    must run on a bare Pi. Runs in a thread with a short timeout so a slow or
    dead endpoint never stalls the detection loop.

    Through netsafe.request: the host is resolved and refused when any of its
    addresses is this box's own (loopback, link-local, its interfaces...),
    the connection goes to the address that was checked, and a redirect is
    a failure, never followed (Pipi M-R1)."""
    url = str(o.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("webhook needs an http(s) url")
    method = str(o.get("method") or "POST").upper()
    payload = str(o.get("payload") or "")
    headers = _headers(o)

    def _send():
        try:
            netsafe.request(url, method=method,
                            data=(payload.encode("utf-8") if method != "GET" else None),
                            headers=headers, timeout=3)
        except netsafe.Blocked as exc:
            _record(o, exc)
            key = o.get("id") or url
            if key not in _refused_once:
                _refused_once.add(key)
                log.error("webhook %r not sent: %s", o.get("id") or "webhook", exc,
                          extra=faults.event(faults.SYS_OUTPUT_FAILED,
                                             output=o.get("id") or ""))
        except Exception as exc:                            # noqa: BLE001
            _record(o, exc)
            log.error("webhook %r failed: %s", o.get("id") or url, exc,
                      exc_info=True,
                      extra=faults.event(faults.NET_UPSTREAM_TIMEOUT,
                                         output=o.get("id") or ""))
        else:
            _record(o)

    threading.Thread(target=_send, daemon=True).start()
    return False        # _send reports the real outcome, from its own thread


#: Last send per message output id, for the cooldown below.
_last_message = {}


def _hhmm(text):
    """"14:30" -> minutes since midnight, or None. Anything unparseable is
    None and therefore NOT a restriction: a typo must not silently mute an
    alert somebody is relying on."""
    parts = str(text or "").strip().split(":")
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


def _message_allowed(o, now=None):
    """May this Email/Telegram output send RIGHT NOW?

    The node's Advanced section, all optional and all skipped when blank:

      cooldown_s   at most one message every N seconds. A rule that trips on
                   every frame otherwise sends a message on every frame --
                   the single most common way this feature becomes a mail
                   provider blocking the box.
      hours_from /
      hours_to     a daily window. Crossing midnight is supported and MEANS
                   it: 22:00-06:00 is the night shift, not an empty set.
      days         which weekdays, "mon,tue,..." -- blank is every day.
      from_date /
      to_date      an absolute range, for an alert that only matters during a
                   trial or a shutdown. Inclusive at both ends.

    Every check fails OPEN: an unparseable field is ignored rather than
    treated as "never", because a muted alert is a fault nobody notices until
    the thing it was watching for has already happened.
    """
    adv = o.get("advanced") or o
    now = now or time.localtime()
    stamp = time.mktime(now)

    from_date = str(adv.get("from_date") or "").strip()
    to_date = str(adv.get("to_date") or "").strip()
    today = time.strftime("%Y-%m-%d", now)
    # ISO dates compare correctly as text, which is why they are stored that
    # way -- no parsing, no timezone, no ambiguity.
    if from_date and len(from_date) == 10 and today < from_date:
        return False
    if to_date and len(to_date) == 10 and today > to_date:
        return False

    days = str(adv.get("days") or "").strip().lower()
    if days:
        names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        want = {d.strip()[:3] for d in days.split(",") if d.strip()}
        if want and names[now.tm_wday] not in want:
            return False

    lo = _hhmm(adv.get("hours_from"))
    hi = _hhmm(adv.get("hours_to"))
    if lo is not None and hi is not None and lo != hi:
        minute = now.tm_hour * 60 + now.tm_min
        inside = (lo <= minute < hi) if lo < hi else (minute >= lo or minute < hi)
        if not inside:
            return False

    try:
        cooldown = float(adv.get("cooldown_s") or 0)
    except (TypeError, ValueError):
        cooldown = 0
    if cooldown > 0:
        key = o.get("id") or id(o)
        last = _last_message.get(key)
        if last is not None and (stamp - last) < cooldown:
            return False
        _last_message[key] = stamp
    return True


def _fill(template, o, event, stats=None, csv=None, csvs=None):
    """Substitute the things an alert message wants to say.

    Deliberately literal - a fixed set of names, each replaced by str() - and
    NEVER evaluated. A message template is untrusted data from a browser, and
    anything that evaluates it is a way to run code on the board.

    Two groups. The built-ins ({fn}, {value}, {label}, {time}...) describe the
    event that fired. The rest come from `o["vars"]`, which the compiler fills
    with {placeholder_name: stat_key} for every node wired INTO this output --
    so an operator who has connected three rules can write "Line stopped:
    {people_count} people, {cycle_time}s" and mean it. The mapping is built
    server-side from the actual wires. A WIRED name with no value published
    yet becomes empty text; a name this output was NOT wired to is not
    touched at all and stays in the message literally as {name} -- which is
    why the editor offers buttons rather than asking anyone to type one, and
    why the compiler warns about an unknown {name} (Tok Aba, 2026-10-14).

    `csvs` is every attached file as (name, bytes, rows) -- (name, None, None)
    for one that could not be built -- in wire order; `csv` is the older
    single-file form, taken as a list of one. {csv} / {csv_text} / {csv_rows}
    are the FIRST file; {csv:name}, {csv_text:name}, {csv_rows:name} are the
    file with that name.
    """
    if csvs is None:
        csvs = [csv] if csv else []
    csvs = [f for f in csvs if f]
    csv = csvs[0] if csvs and csvs[0][1] is not None else None
    detail = (event or {}).get("detail") or {}
    subs = {
        "{fn}": str(detail.get("function") or ""),
        "{value}": str(detail.get("value") if detail.get("value") is not None else ""),
        "{label}": str((event or {}).get("label") or ""),
        "{message}": str((event or {}).get("label") or ""),
        "{type}": str((event or {}).get("type") or ""),
        "{time}": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # The attachment, NAMED in the text -- not pasted into it. A CSV node feeds
    # this; without one the token says so rather than vanishing, because a
    # message with a silent gap where the table should be reads as a table with
    # nothing in it.
    if "{csv}" in str(template or ""):
        subs["{csv}"] = _csv_inline(csv)
    if "{csv_text}" in str(template or ""):
        subs["{csv_text}"] = _csv_text(csv)
    # No longer offered in the editor, still expanded: a message written when
    # it was must not start printing raw braces into somebody's inbox.
    if "{csv_rows}" in str(template or ""):
        subs["{csv_rows}"] = str(csv[2]) if csv else "0"
    # Each file by its own name, when the message has several. A file that
    # could not be built says so in its own place.
    for f in csvs:
        built = f if f[1] is not None else None
        subs.setdefault("{csv:%s}" % f[0], _csv_inline(built) if built
                        else "(no table available)")
        if "{csv_text:%s}" % f[0] in str(template or ""):
            subs["{csv_text:%s}" % f[0]] = _csv_text(built)
        subs.setdefault("{csv_rows:%s}" % f[0], str(f[2]) if built else "0")
    # The wired values, added SECOND but never overwriting a built-in: a rule
    # an operator happened to call "value" must not silently redefine {value}
    # for every message on the box.
    for name, key in (o.get("vars") or {}).items():
        token = "{%s}" % name
        if token in subs:
            continue
        raw = (stats or {}).get(key)
        # A wired value with nothing published yet is blank, not "None" --
        # which is what would otherwise arrive in somebody's inbox.
        subs[token] = "" if raw is None else _num(raw)
    out = str(template or "")
    for key, value in subs.items():
        out = out.replace(key, value)
    return out


def _num(value):
    """A published value as a person would write it: 3 rather than 3.0, and
    2.5 kept. These land in sentences, and "3.0 people" reads as a machine."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value)


#: How long the app will wait for this box to render a CSV, and how much of
#: one it will hold. The query runs on the same machine, so a slow answer
#: means a slow DATABASE rather than a slow network -- and a message that
#: waits a minute for an attachment is a message that arrives after the shift
#: it was about.
CSV_TIMEOUT_S = 20.0
CSV_MAX_BYTES = 8 * 1024 * 1024
#: Kept for a compiled document written by an older build that still reads it.
#: {csv} no longer pastes the table into the body -- see _csv_inline.
CSV_INLINE_CHARS = 4000


def _csv_fetch_all(o):
    """Every file this message attaches, as (filename, bytes, rows) -- or
    (filename, None, None) for one that could not be built -- in wire order.

    `csvs` when two or more CSV nodes feed the message (Tok Aba, 2026-10-14:
    both violation files, not just the first); otherwise the one file the
    older csv_url / csv_name fields describe, or nothing.
    """
    many = [e for e in (o.get("csvs") or []) if isinstance(e, dict)]
    if not many and str(o.get("csv_url") or "").strip():
        many = [{"url": o.get("csv_url"), "name": o.get("csv_name")}]
    out = []
    for e in many:
        name = str(e.get("name") or "query.csv")
        got = _csv_fetch({"id": o.get("id"), "csv_url": e.get("url"),
                          "csv_name": name})
        out.append(got or (name, None, None))
    return out


#: The master port when nothing says otherwise (core workflow
#: DEFAULT_MASTER_PORT).
DEFAULT_MASTER_PORT = 5110
_master_port_cache = {}
_master_refused = set()


def _master_port():
    """This system's master port, the only port the app fetches its CSV files
    and Telegram recipients from: the master that started the app says it in
    CYTRON_MASTER_PORT; an app started by the hub reads the [app] master_port
    the master writes into this package's config at its own start."""
    env = str(os.environ.get("CYTRON_MASTER_PORT") or "").strip()
    if env.isdigit():
        return int(env)
    if "port" in _master_port_cache:
        return _master_port_cache["port"]
    port = DEFAULT_MASTER_PORT
    try:
        import tomllib                                       # noqa: PLC0415
        pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("config.toml", "config.local.toml"):
            try:
                with open(os.path.join(pkg, name), "rb") as handle:
                    value = (tomllib.load(handle).get("app") or {}).get("master_port")
                if isinstance(value, int) and 0 < value < 65536:
                    port = value
            except (OSError, ValueError):
                continue
    except ImportError:
        pass
    _master_port_cache["port"] = port
    return port


def _master_url_ok(o, url, path):
    """Is `url` exactly this master's own `path` route (Pipi N-3)? An output
    whose address is anything else -- another local port, another path, a
    user, extra query keys -- is not fetched, and says so once."""
    why = netsafe.master_url_problem(url, path, _master_port())
    if not why:
        return True
    key = (o.get("id") or "", path)
    if key not in _master_refused:
        _master_refused.add(key)
        log.error("output %r: not fetched - its %s address %s",
                  o.get("id") or "message", path.rsplit("/", 1)[-1], why,
                  extra=faults.event(faults.SYS_OUTPUT_FAILED, output=o.get("id") or ""))
    return False


def _same_url(resp, url):
    """False when urllib followed a redirect: the answer is from somewhere
    the check above did not approve, so it is thrown away."""
    final = getattr(resp, "geturl", None)
    return not callable(final) or final() in (None, url)


def _csv_fetch(o):
    """(filename, bytes, rows) for a message's attached CSV, or None.

    The app has no database -- it emits events and core writes them -- so the
    rows are rendered by this box's own master, which does, and read back over
    localhost. The compiled output carries the address; it names the OUTPUT,
    never a statement, so nothing here can widen what a query may read.

    Never raises: an attachment that cannot be built must not stop the message
    going out. A report that arrives saying the table was unavailable is worth
    far more than no report at all, which is indistinguishable from a quiet
    night.
    """
    url = str(o.get("csv_url") or "").strip()
    if not url:
        return None
    if not _master_url_ok(o, url, "/api/workflow/query-csv"):
        return None
    import urllib.request                                     # noqa: PLC0415
    try:
        with urllib.request.urlopen(url, timeout=CSV_TIMEOUT_S) as resp:
            if not _same_url(resp, url):
                raise RuntimeError("the address redirected; not followed")
            data = resp.read(CSV_MAX_BYTES + 1)
            rows = resp.headers.get("X-Row-Count") or "?"
    except Exception as exc:                                  # noqa: BLE001
        log.error("csv for %r failed: %s", o.get("id") or "message", exc,
                  exc_info=True, extra=faults.event(faults.SYS_OUTPUT_FAILED))
        return None
    if len(data) > CSV_MAX_BYTES:
        log.warning("csv for %r is too big to attach",
                    o.get("id") or "message", extra=faults.event(faults.SYS_OUTPUT_FAILED))
        return None
    return (str(o.get("csv_name") or "query.csv"), data, rows)


def _csv_inline(csv):
    """What {csv} becomes: the ATTACHMENT'S NAME.

    It used to paste the whole table into the text, which was wrong twice
    over: the rows are already attached, so the message carried them twice,
    and a shift report with two thousand lines of CSV in the body is one no
    mail client will show and nobody will scroll. The file is the table; the
    sentence only has to point at it. {csv_text} is there for the times the
    table itself belongs in the message.
    """
    if not csv:
        return "(no table available)"
    return str(csv[0] or "query.csv")


def _csv_text(csv):
    """...and {csv_text}: the table pasted into the body.

    For a short result somebody wants to read on a phone without opening an
    attachment. Capped, and it SAYS it is capped -- a table that stops mid-row
    without a word reads as a table that ended.
    """
    if not csv:
        return "(no table available)"
    # A query that matched nothing: the file is a bare header, and pasting
    # that reads as a broken table rather than a quiet shift.
    if str(csv[2]) == "0":
        return "(no rows)"
    text = csv[1].decode("utf-8", "replace")
    if len(text) <= CSV_INLINE_CHARS:
        return text
    return (text[:CSV_INLINE_CHARS].rsplit("\n", 1)[0]
            + f"\n... (truncated; the full {csv[2]} rows are attached)")


def _email(o, event=None, stats=None):
    """Send a mail through the SMTP settings the compiler embedded in this
    output. Threaded with a short timeout: a mail server that hangs must not
    stall the detection loop."""
    to = o.get("to") or []
    if isinstance(to, str):
        to = [t.strip() for t in to.replace(";", ",").split(",") if t.strip()]
    if not to:
        raise RuntimeError("email output has no recipients")
    host = str(o.get("smtp_host") or "").strip()
    user = str(o.get("smtp_user") or "").strip()
    password = str(o.get("smtp_pass") or "")
    if not host or not user or not password:
        raise RuntimeError("email is not configured on this system's Email settings")
    sender = str(o.get("from_addr") or user)
    port = int(o.get("smtp_port") or 587)

    def _send():
        import smtplib                                       # noqa: PLC0415
        from email.message import EmailMessage               # noqa: PLC0415
        # Fetched on the SENDING thread, not the detection loop: rendering a
        # CSV is a database round trip, and the loop that is counting people
        # must not wait on one.
        files = _csv_fetch_all(o)
        subject = _fill(o.get("subject") or "{label}", o, event, stats,
                        csvs=files) or "Alert"
        body = _fill(o.get("body") or "{label}\n\nFunction: {fn}\nValue: {value}\nAt {time}",
                     o, event, stats, csvs=files)
        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = ", ".join(to)
            msg.set_content(body)
            # EVERY file, each its own text/csv part. One that could not be
            # built is left off (its placeholder already said so).
            for name, data, _rows in files:
                if data is not None:
                    msg.add_attachment(data, maintype="text", subtype="csv",
                                       filename=name)
            with smtplib.SMTP(host, port, timeout=10) as s:
                s.ehlo()
                s.starttls()
                s.login(user, password)
                s.send_message(msg)
        except Exception as exc:                             # noqa: BLE001
            _record(o, exc)
            log.error("email %r failed: %s", o.get("id") or "email", exc,
                      exc_info=True,
                      extra=faults.event(faults.NET_SMTP_FAILED,
                                         output=o.get("id") or "email"))
        else:
            _record(o)

    threading.Thread(target=_send, daemon=True).start()
    return False        # _send reports the real outcome, from its own thread


def _multipart(fields, file_field, filename, blob, content_type):
    """(body, content-type) for one file plus a few text fields.

    Hand-rolled because this app imports nothing beyond the standard library:
    it runs on a bare Pi, and a dependency for one POST is a dependency to
    keep working on every future image.
    """
    boundary = "----irivai" + hex(abs(hash(filename)) & 0xFFFFFFFF)[2:]
    out = []
    for key, value in fields.items():
        out.append(f"--{boundary}\r\n"
                   f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                   f"{value}\r\n".encode("utf-8"))
    out.append(f"--{boundary}\r\n"
               f'Content-Disposition: form-data; name="{file_field}"; '
               f'filename="{filename}"\r\n'
               f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    out.append(blob)
    out.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(out), f"multipart/form-data; boundary={boundary}"


# -- Telegram to the people linked to the bot (Tok Aba, 2026-10-14) -----------
#
# A node in "all" / "selected" mode carries no chat ids of its own for "all"
# (and only numeric ids for "selected"): WHO is resolved at SEND time by this
# box's master (/api/telegram/recipients/resolve, loopback, an output id in
# and chat ids out), so a person claimed since the last compile is included
# and one removed or blocked on Connected Bots never gets another message.
# The app has no core imports, which is why it asks rather than reads.
#
# Cached RECIPIENTS_TTL_S per output. With the master down the last good list
# is used for up to RECIPIENTS_STALE_S after it was fetched, then the send is
# SKIPPED with a log line -- never "no list, so everyone".

RECIPIENTS_TTL_S = 60
RECIPIENTS_STALE_S = 600
MAX_RECIPIENTS = 50
#: The pause between two posts of one send: Telegram allows ~30 messages a
#: second per bot, and a report to fifty people must not trip its flood wall.
SEND_GAP_S = 0.04
_RECIPIENT_RE = re.compile(r"-?\d{1,20}")

_recipients_cache = {}      # output id -> (fetched at, [chat ids])
_now = time.time            # names the tests replace
_sleep = time.sleep


def _recipients(o):
    """The chat ids this message goes to, or None when it must be skipped."""
    if o.get("to_mode") not in ("all", "selected"):
        chat = str(o.get("chat_id") or "").strip()
        return [chat] if chat else []
    key = str(o.get("id") or o.get("to_url") or "")
    now = _now()
    hit = _recipients_cache.get(key)
    if hit and now - hit[0] < RECIPIENTS_TTL_S:
        return hit[1]
    import json                                              # noqa: PLC0415
    import urllib.request                                    # noqa: PLC0415
    url = str(o.get("to_url") or "")
    if not _master_url_ok(o, url, "/api/telegram/recipients/resolve"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if not _same_url(resp, url):
                raise RuntimeError("the address redirected; not followed")
            body = json.loads(resp.read(1 << 20).decode("utf-8", "replace"))
        if not body.get("ok"):
            raise RuntimeError("the master refused")
        chats = []
        for c in body.get("chats") or []:
            c = str(c)
            if _RECIPIENT_RE.fullmatch(c) and c not in chats:
                chats.append(c)
        chats = chats[:MAX_RECIPIENTS]
        _recipients_cache[key] = (now, chats)
        return chats
    except Exception as exc:                                  # noqa: BLE001
        if hit and now - hit[0] < RECIPIENTS_STALE_S:
            log.warning("telegram %r: recipients not refreshed (%s); using the "
                        "list from %d s ago", o.get("id") or "telegram",
                        type(exc).__name__, int(now - hit[0]))
            return hit[1]
        log.error("telegram %r skipped: its recipients could not be read (%s)",
                  o.get("id") or "telegram", type(exc).__name__,
                  extra=faults.event(faults.NET_TELEGRAM_FAILED,
                                     output=o.get("id") or "telegram"))
        return None


def _tg_post(req):
    """One Bot API post. A 429 waits the retry-after Telegram names (capped
    at 30 s) and tries once more; anything else raises."""
    import json                                              # noqa: PLC0415
    import urllib.error                                      # noqa: PLC0415
    import urllib.request                                    # noqa: PLC0415
    for attempt in (0, 1):
        try:
            urllib.request.urlopen(req, timeout=30).close()
            return
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt:
                raise
            wait = 0.0
            try:
                wait = float(json.loads(exc.read().decode("utf-8", "replace"))
                             .get("parameters", {}).get("retry_after") or 0)
            except Exception:                                 # noqa: BLE001
                pass
            if not wait:
                try:
                    wait = float((exc.headers or {}).get("Retry-After") or 1)
                except (TypeError, ValueError):
                    wait = 1.0
            _sleep(min(30.0, max(0.0, wait)))


def _telegram_many(o, token, chats, text, built):
    """One message (and its files) to each chat. A failure in one chat never
    stops the others; the log says counts, never a chat id."""
    import json                                              # noqa: PLC0415
    import urllib.request                                    # noqa: PLC0415
    failed = 0
    for i, chat in enumerate(chats[:MAX_RECIPIENTS]):
        if i:
            _sleep(SEND_GAP_S)
        try:
            if built:
                for j, (name, data, _rows) in enumerate(built):
                    fields = {"chat_id": chat}
                    if j == 0:
                        fields["caption"] = text[:1024]
                    body, ctype = _multipart(fields, "document", name, data, "text/csv")
                    _tg_post(urllib.request.Request(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data=body, method="POST", headers={"Content-Type": ctype}))
            else:
                _tg_post(urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=json.dumps({"chat_id": chat, "text": text}).encode("utf-8"),
                    method="POST", headers={"Content-Type": "application/json"}))
        except Exception:                                     # noqa: BLE001
            failed += 1
    total = min(len(chats), MAX_RECIPIENTS)
    if failed:
        exc = RuntimeError(f"{failed} of {total} recipients could not be reached")
        _record(o, exc)
        log.error("telegram %r: %d of %d recipients failed",
                  o.get("id") or "telegram", failed, total,
                  extra=faults.event(faults.NET_TELEGRAM_FAILED,
                                     output=o.get("id") or "telegram"))
    else:
        _record(o)


def _telegram(o, event=None, stats=None):
    """Send a Telegram message. One HTTPS POST, stdlib only - the app must run
    on a bare Pi."""
    token = str(o.get("bot_token") or "").strip()
    chat = str(o.get("chat_id") or "").strip()
    many = o.get("to_mode") in ("all", "selected")
    if not token or (not chat and not many):
        raise RuntimeError("telegram output needs a bot token and a chat id")
    if many:
        def _send_many():
            chats = _recipients(o)
            if chats is None:
                _record(o, RuntimeError("recipients unavailable"))
                return
            if not chats:
                log.warning("telegram %r: nobody to send to", o.get("id") or "telegram")
                return
            files = _csv_fetch_all(o)
            text = _fill(o.get("message") or "{label} ({fn} = {value})",
                         o, event, stats, csvs=files)
            _telegram_many(o, token, chats, text,
                           [f for f in files if f[1] is not None])

        threading.Thread(target=_send_many, daemon=True).start()
        return False

    def _send():
        import json                                          # noqa: PLC0415
        import urllib.request                                # noqa: PLC0415
        files = _csv_fetch_all(o)
        text = _fill(o.get("message") or "{label} ({fn} = {value})",
                     o, event, stats, csvs=files)
        built = [f for f in files if f[1] is not None]
        try:
            if built:
                # sendDocument, with the message as the caption: Telegram has
                # no way to attach a file to a text message after the fact, so
                # a report with a table is a document post rather than a
                # message and an orphaned file underneath it. Several files
                # are one document each, the words riding on the FIRST.
                for i, (name, data, _rows) in enumerate(built):
                    fields = {"chat_id": chat}
                    if i == 0:
                        fields["caption"] = text[:1024]
                    body, ctype = _multipart(fields, "document", name, data,
                                             "text/csv")
                    req = urllib.request.Request(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data=body, method="POST",
                        headers={"Content-Type": ctype})
                    urllib.request.urlopen(req, timeout=30).close()
                return
            data = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=data, method="POST",
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as exc:                             # noqa: BLE001
            _record(o, exc)
            log.error("telegram %r failed: %s", o.get("id") or "telegram",
                      exc, exc_info=True,
                      extra=faults.event(faults.NET_TELEGRAM_FAILED,
                                         output=o.get("id") or "telegram"))
        else:
            _record(o)

    threading.Thread(target=_send, daemon=True).start()
    return False        # _send reports the real outcome, from its own thread


def _modbus(o):
    """Write to a Modbus RTU device over the isolated RS485 port (a PLC input,
    a VFD command, a lamp stack on a remote IO module).

    `baudrate` and `target` are carried rather than assumed: 9600 is the
    common default and wrong on plenty of installations, and a coil (a bit) is
    as ordinary a destination as a holding register (a word). A mismatch on
    either is a silent no-op on the wire, which is the hardest kind of fault to
    find on a factory floor.
    """
    try:
        from pymodbus.client import ModbusSerialClient      # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError("pymodbus not installed (pip install pymodbus)") from e
    client = ModbusSerialClient(port=o.get("device") or "/dev/ttyAMA2",
                                baudrate=int(o.get("baudrate") or 9600))
    if not client.connect():
        raise RuntimeError(f"cannot open modbus device {o.get('device')}")
    try:
        register = int(o.get("register") or 0)
        value = int(o.get("value") or 1)
        unit = int(o.get("unit") or 1)
        if str(o.get("target") or "holding").lower() == "coil":
            client.write_coil(register, bool(value), slave=unit)
        else:
            client.write_register(register, value, slave=unit)
    finally:
        client.close()


# -- Modbus Write ------------------------------------------------------------
#
# Node-RED's "Modbus Write", on a named Modbus Server connection. Two ways to
# be asked:
#
#   mode "trigger"  a Condition/Compare/Trigger is wired to the node's trigger
#                   port -- the write rides that rule's alert through fire(),
#                   like every other triggered output;
#   mode "change"   nothing on the trigger port -- the wired value is written
#                   whenever it CHANGES (publish(), on the tick). Every frame
#                   would be thirty writes a second of the same number to a
#                   PLC, which is a bus flood, not telemetry.
#
# NEVER on the detection loop. A serial device that does not answer costs its
# whole timeout, and a TCP host that is down costs a connect timeout; either
# inside the loop is a frozen picture. So a write is a JOB handed to one
# worker thread per bus, and the loop moves on. The queue is newest-wins PER
# NODE: a value that changed three times while the bus was busy is written
# once, as its latest value -- the two in between were already out of date.
#
# The worker takes buses.bus_lock for the bus, the same lock the Modbus Read
# poller holds, so reads and writes on one RS-485 pair never interleave.
#
# What happened is written down the way every output's result is (_record):
# out_<id>_ok / _at / _error, plus out_<id>_note -- "wrote 42 to 0" -- so the
# canvas can say what reached the wire and not only that something did.

#: When this process started. "Delay to activate input" measures from here:
#: the first values after a restart are the ones most likely to be stale (a
#: variable not yet read back, a count not yet restored), and writing one onto
#: a PLC is the start-up glitch Node-RED's checkbox exists to prevent.
_APP_STARTED = time.time()

#: The floor between two change-driven writes from one node. A value that
#: flickers between frames is written at most four times a second; the
#: latest value always goes out once the floor has passed.
MODBUS_WRITE_MIN_S = 0.25

#: Test hook: run jobs inline instead of on the worker thread.
_MBW_INLINE = False

_mbw_lock = threading.Lock()
_mbw_jobs = {}          # bus key -> {output id: job}, newest wins per id
_mbw_wake = {}          # bus key -> threading.Event
_mbw_threads = {}       # bus key -> worker thread
_mbw_seen = {}          # output id -> {"sig", "at"} for change mode
_mbw_repeat = {}        # output id -> what "keep sending" re-writes (below)
_mbw_down = set()       # output ids whose last write failed (log once)

# "Keep sending the last value" under the connection's Drive watchdog switch
# (Tok Aba 2026-10-07). A Cytron motor driver (box .49) has a Modbus comms
# watchdog, WDT_MS: when no request arrives in time it resets the speed to 0,
# so one write of 1000 ran the motor only for a moment. With the switch ON
# (buses.py: the box sets WDT on connect) every Modbus Write on that
# connection re-sends its last value at HALF the timeout, so the motor runs
# continuously and stops by itself when the box or the cable fails. There is
# no per-node setting: the connection decides (buses.watchdog_repeat_s).
#
# Rules, each one deliberate:
#   * nothing is repeated before the node has written a REAL value -- the
#     repeat never invents a command, 0 included;
#   * the repeat is the last commanded job, same register, same value, and a
#     newer real write replaces it (and restarts the clock);
#   * it goes through the same newest-wins queue, bus lock and pooled socket
#     as any write;
#   * HELD BACK: a node still in the running set keeps repeating (with AI off
#     that means its source is not detection-fed -- run.py's logic_prune
#     dropped the others). A node that drops OUT of the set (AI off, Class
#     Filter paused, disabled, deleted) or whose source key vanishes (a lost
#     camera's rules are held, run.py) STOPS repeating and says so on the
#     node: re-commanding a motor from a value nobody is measuring any more
#     is the one thing a repeat must not do. The drive's own watchdog then
#     does what it was built for. The next real write starts it again;
#   * a failing repeat is logged ONCE per outage, not once per tick, and
#     while the device is down it is tried at most once a second
#     (REPEAT_DOWN_S): at 0.05 s, each try costing a whole timeout under the
#     bus lock, the failing drive would own an RS-485 wire it shares.
#
# Half of a 100 ms watchdog is 0.05 s, faster than the detection loop ticks
# with the AI off (4 a second), so the tick only VOUCHES for a repeat
# (rep["live"]: the node is still in the running set and its value still
# live) and a small pump thread sends the due ones between ticks
# (_repeat_pump). A repeat the tick has not vouched for in REPEAT_LEASE_S
# stops being sent: a stalled or stopped loop -- app stopped, process gone --
# stops the motor. Bus fairness: buses.bus_lock is a ticket lock, so a Modbus
# Read waiting on the same server goes before the next repeat.
REPEAT_MIN_S = 0.05
REPEAT_MAX_S = 3600.0
REPEAT_LEASE_S = 2.0
REPEAT_DOWN_S = 1.0


def _buses():
    try:
        from . import buses
    except ImportError:                 # run as a loose script, not a package
        import buses
    return buses


def _list_stat(key):
    """The list a Python node returned under this stat key, if it did."""
    try:
        try:
            from . import functions as _fns
        except ImportError:
            import functions as _fns
        return _fns.list_stat(key)
    except Exception:                                       # noqa: BLE001
        return None


def _int_or(value, fallback):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _modbus_job(o, stats):
    """The write this node asks for right now, from its settings and what is
    on its wires: {"fc", "unit", "address", "quantity", "value"}.

    A Python node wired in may return Node-RED's payload shape,
    {"value", "fc", "unitid", "address", "quantity"}. Each of those reaches
    this process as its own stat (fn_<id>_fc, ...), and each one PRESENT
    overrides the node's own setting for this write, field by field -- the
    same thing msg.payload does to a Node-RED Modbus Write.
    """
    stats = stats or {}
    key = str(o.get("key") or "")
    value = _list_stat(key)
    if value is None:
        value = stats.get(key)
    job = {"fc": int(o.get("fc") or 6), "unit": o.get("unit"),
           "address": _int_or(o.get("address"), 0),
           "quantity": _int_or(o.get("quantity"), 1), "value": value}
    pf = str(o.get("payload_fn") or "")
    if pf:
        for field, suffix in (("fc", "fc"), ("unit", "unitid"),
                              ("address", "address"),
                              ("quantity", "quantity")):
            got = stats.get(f"fn_{pf}_{suffix}")
            if got is not None:
                job[field] = _int_or(got, job[field])
    return job


def _job_problem(job):
    """Why this job cannot be written, or "" when it can. Overrides arrive at
    runtime from code, so they are checked here as strictly as the node's own
    settings were at save time."""
    if job["fc"] not in (5, 6, 15, 16):
        return f"FC {job['fc']} is not a write (use 5, 6, 15 or 16)"
    if not 0 <= job["address"] <= 65535:
        return f"address {job['address']} is outside 0-65535"
    if not 1 <= job["quantity"] <= 65535:
        return f"quantity {job['quantity']} is outside 1-65535"
    if job["unit"] not in (None, "") and not 0 <= _int_or(job["unit"], -1) <= 255:
        return f"Unit-Id {job['unit']} is outside 0-255"
    if job["value"] is None:
        return "nothing has arrived on the value wire yet"
    return ""


def _activated(o, now):
    """False while "Delay to activate input" is still holding inputs back."""
    if not o.get("delay_start"):
        return True
    try:
        wait = float(o.get("start_delay_s") or 0)
    except (TypeError, ValueError):
        wait = 0.0
    return now - _APP_STARTED >= wait


def _modbus_write_tick(o, stats, now):
    """Change mode: queue a write when what this node would write changed.

    Compared on the WHOLE request, not only the value -- a Python node that
    moves the address with the same value is a different write. While the
    start delay holds, what arrives is remembered but not written, so the
    first write after it is a genuine change and not the stale start-up value
    the delay was ticked to keep off the wire.

    With `beat` (a Trigger or Condition in front, as for an MQTT Publish),
    each of its fires is a change too: the memory is this output's, and
    another rule may have written the same register since.
    """
    if str(o.get("mode") or "change") == "fire":
        return _modbus_write_on_fire(o, stats, now)
    oid = str(o.get("id") or "")
    job = _modbus_job(o, stats)
    if job["value"] is None:
        return False                    # nothing to say yet; not a failure
    value = job["value"]
    sig = (job["fc"], job["unit"], job["address"], job["quantity"],
           tuple(value) if isinstance(value, list) else value,
           _fire_beat(o.get("beat")))
    seen = _mbw_seen.get(oid)
    if not _activated(o, now):
        _mbw_seen[oid] = {"sig": sig, "at": 0.0}
        return False
    if seen is not None and seen["sig"] == sig:
        return False
    if seen is not None and now - seen["at"] < MODBUS_WRITE_MIN_S:
        return False                    # paced; the latest goes out next tick
    _mbw_seen[oid] = {"sig": sig, "at": now}
    _repeat_arm(o, job, now, o.get("key"))
    return _modbus_write_submit(o, job)


def _modbus_write_on_fire(o, stats, now):
    """Fire mode: the value port carries Injects (or Schedules) only and the
    trigger port is unwired, so EVERY press writes -- the same payload
    pressed twice is two writes, which "on change" would have dropped
    (client UAT 2026-10-04, item 5). Several wires: whichever fired this
    tick is written; two in the same tick, the last wired wins (the newest
    press, since a tick is one frame). Nothing fired, nothing written."""
    key = None
    for src in o.get("sources") or ():
        try:
            if float((stats or {}).get(src.get("fired")) or 0) > 0:
                key = src.get("key")
        except (TypeError, ValueError):
            continue
    if key is None or not _activated(o, now):
        return False
    job = _modbus_job({**o, "key": key}, stats)
    _repeat_arm(o, job, now, key)
    return _modbus_write_submit(o, job)


def _modbus_write_fire(o, stats):
    """Trigger mode: the wired rule fired, so write what is on the value wire
    now. Returns False -- the worker reports the outcome, not the caller."""
    now = time.time()
    if not _activated(o, now):
        return False
    job = _modbus_job(o, stats)
    _repeat_arm(o, job, now, o.get("key"))
    return _modbus_write_submit(o, job)


def _repeat_every(o):
    """Seconds between repeats for this node: half the timeout when its
    connection's Drive watchdog is ON, else 0.0 (it does not repeat). Read
    live, so switching the connection takes effect on the next tick."""
    try:
        buses = _buses()
        every = float(buses.watchdog_repeat_s(buses.client(o.get("server"))))
    except Exception:                                       # noqa: BLE001
        return 0.0
    if every <= 0:
        return 0.0
    return min(REPEAT_MAX_S, max(REPEAT_MIN_S, every))


def _repeat_settings(o):
    """What a repeat must still agree with: an edited address or server is a
    different register, and the old value is not repeated onto it."""
    return tuple(str(o.get(k) if o.get(k) is not None else "")
                 for k in ("server", "unit", "fc", "address", "quantity",
                           "payload_fn"))


def _secs(every):
    return f"{every:g}s"


def _stat_present(key, stats):
    return key in (stats or {}) or _list_stat(key) is not None


def _repeat_arm(o, job, now, src_key):
    """Remember the job about to be commanded as the one to keep sending.
    Called by the three real-write paths just BEFORE they submit, so the
    write's own status line already says it repeats; a job the submit would
    refuse (no value yet, a bad address) is never remembered."""
    every = _repeat_every(o)
    oid = str(o.get("id") or "")
    if not every or not oid or _job_problem(job):
        return
    try:
        _buses().modbus_values(job["fc"], job["value"], job["quantity"])
    except ValueError:
        return                  # never repeat a value the register refuses
    with _mbw_lock:
        _mbw_repeat[oid] = {"job": dict(job), "at": now, "every": every,
                            "key": str(src_key or ""), "o": dict(o),
                            "live": now, "settings": _repeat_settings(o)}
    _repeat_pump_start()


def _repeat_stop(oid, why=None):
    """Stop repeating for `oid`: forget the job, drop a repeat still queued,
    and say why on the node (`why` None: just drop the "repeats" words)."""
    with _mbw_lock:
        had = _mbw_repeat.pop(oid, None)
        for queue in _mbw_jobs.values():
            if (queue.get(oid) or {}).get("repeat"):
                queue.pop(oid, None)
    if had is None:
        return
    if why:
        # Held back: the next live value is a FRESH command (the same as a
        # restart's first write), not a "no change" against the old one.
        _mbw_seen.pop(oid, None)
    with _results_lock:
        slot = _results.get(oid)
        if slot is not None and "note" in slot:
            note = str(slot.get("note") or "").split("; repeats ", 1)[0]
            slot["note"] = (why or note)[:_MAX_ERROR]


def forget(ids):
    """Outputs an ON/OFF point lets back in (run.py point_gates) start with
    no "last sent": their branch comes back as a fresh start, so what it
    says now goes out once even when it is the value sent before the gap
    (OFF, ON, OFF writes the OFF side twice). Change memory only -- the
    pacing clocks stay."""
    for oid in ids or ():
        oid = str(oid)
        _follow_state.pop(oid, None)
        _mbw_seen.pop(oid, None)
        _zone_switch_state.pop(oid, None)


def stop_repeats():
    """The app is stopping: no write is repeated past this point."""
    for oid in list(_mbw_repeat):
        _repeat_stop(oid)


def _repeat_sweep(outputs):
    """Stop every repeat whose node is no longer in the running set."""
    if not _mbw_repeat:
        return
    live = {str(o.get("id") or "") for o in outputs or ()
            if isinstance(o, dict) and o.get("enabled")
            and o.get("type") == "modbus_write"}
    for oid in [k for k in list(_mbw_repeat) if k not in live]:
        _repeat_stop(oid, "stopped repeating: this node is held back (AI off "
                          "or its Class Filter paused); the next value it "
                          "writes starts it again")


def _modbus_write_repeat(o, stats, now):
    """Keep sending: vouch for this node's repeat and send it when due."""
    oid = str(o.get("id") or "")
    rep = _mbw_repeat.get(oid)
    if rep is None:
        return False
    every = _repeat_every(o)
    if not every or rep["settings"] != _repeat_settings(o):
        _repeat_stop(oid)                       # turned off, or re-pointed
        return False
    if rep["key"] and not _stat_present(rep["key"], stats):
        _repeat_stop(oid, "stopped repeating: its value is held back; the "
                          "next value it writes starts it again")
        return False
    with _mbw_lock:
        rep["every"] = every
        rep["o"] = dict(o)
        rep["live"] = now               # the tick vouches: still running, live
    return _repeat_due(oid, now)


def _repeat_due(oid, now):
    """Send `oid`'s repeat when it is due. Called by the tick and by the
    pump; the clock is advanced under the lock, so the two never both send
    one beat."""
    with _mbw_lock:
        rep = _mbw_repeat.get(oid)
        if rep is None or now - rep.get("live", now) > REPEAT_LEASE_S:
            return False
        every = rep["every"]
        if oid in _mbw_down:
            every = max(every, REPEAT_DOWN_S)
        due = rep["at"] + every
        if now < due - 1e-6:            # float sums of 0.05 must still land
            return False
        # On the beat when the loop keeps up; from now when it fell far
        # behind, so a stall does not answer with a burst of catch-up writes.
        rep["at"] = due if now - due < every else now
        o, job = rep["o"], dict(rep["job"])
    return _modbus_write_submit(o, job, repeat=True)


_mbw_pump = {"thread": None}


def _repeat_pump_once(now):
    """Send every repeat that is due; returns the seconds to the next one."""
    with _mbw_lock:
        ids = list(_mbw_repeat)
    for oid in ids:
        try:
            _repeat_due(oid, now)
        except Exception as exc:                            # noqa: BLE001
            rep = _mbw_repeat.get(oid)
            if rep is not None:
                _record(rep["o"], exc)
    with _mbw_lock:
        nxt = [r["at"] + r["every"] - now for r in _mbw_repeat.values()]
    return min(nxt) if nxt else None


def _repeat_pump():
    """The pump thread: alive while any node repeats, gone when none does."""
    while True:
        with _mbw_lock:
            if not _mbw_repeat:
                _mbw_pump["thread"] = None
                return
        wait = _repeat_pump_once(time.time())
        time.sleep(min(0.25, max(0.01, wait if wait is not None else 0.25)))


def _repeat_pump_start():
    if _MBW_INLINE:
        return                          # tests drive _repeat_pump_once
    with _mbw_lock:
        thread = _mbw_pump["thread"]
        if thread is None or not thread.is_alive():
            thread = faults.spawn("modbus-repeat", _repeat_pump, once=True)
            _mbw_pump["thread"] = thread
            thread.start()


def _modbus_write_submit(o, job, repeat=False):
    problem = _job_problem(job)
    if problem:
        _record(o, RuntimeError(problem))
        return False
    buses = _buses()
    conn = buses.client(o.get("server"))
    if conn is None:
        _record(o, RuntimeError(f"Modbus server {o.get('server')} no longer "
                                "exists - pick one on the node"))
        return False
    cfg = buses.client_config(conn, job["unit"])
    key = buses.bus_key(cfg)
    entry = {"o": dict(o), "job": job, "cfg": cfg, "conn": conn, "key": key,
             "repeat": repeat}
    if _MBW_INLINE:
        _modbus_write_run(entry)
        return False
    oid = str(o.get("id") or "")
    with _mbw_lock:
        _mbw_jobs.setdefault(key, {})[oid] = entry
        wake = _mbw_wake.setdefault(key, threading.Event())
        thread = _mbw_threads.get(key)
        if thread is None or not thread.is_alive():
            thread = faults.spawn(f"modbus-write-{key}", _modbus_write_worker,
                                  (key,))
            _mbw_threads[key] = thread
            thread.start()
    wake.set()
    return False


def _modbus_write_worker(key):
    """One bus, one thread: takes the oldest node's newest job, writes it."""
    while True:
        with _mbw_lock:
            queue = _mbw_jobs.get(key) or {}
            wake = _mbw_wake.setdefault(key, threading.Event())
            if not queue:
                wake.clear()
                entry = None
            else:
                oid = next(iter(queue))
                entry = queue.pop(oid)
        if entry is None:
            wake.wait(30.0)
            continue
        try:
            _modbus_write_run(entry)
        except Exception as exc:                            # noqa: BLE001
            _record(entry["o"], exc)


def _newer_queued(entry):
    with _mbw_lock:
        return str(entry["o"].get("id") or "") in (_mbw_jobs.get(entry["key"]) or {})


def _modbus_write_run(entry):
    """Do one write under the bus lock; retry ONCE after the connection's
    reconnect timeout when it asks for that and the first try failed.

    The wait happens OUTSIDE the lock -- a dead device must not hold every
    reader on the same wire hostage -- and is abandoned when a newer job for
    the same node has arrived meanwhile: retrying a value that has already
    been replaced would write it after its successor.
    """
    buses = _buses()
    o, job, cfg, conn = entry["o"], entry["job"], entry["cfg"], entry["conn"]
    try:
        values = buses.modbus_values(job["fc"], job["value"], job["quantity"])
    except ValueError as exc:
        _record(o, exc)
        return False
    ok, message = False, ""
    for attempt in (0, 1):
        with buses.bus_lock(entry["key"]):
            ok, message = buses.modbus_write_fc(cfg, job["fc"], job["address"],
                                                values, cfg["unit"])
        if ok or attempt or not conn.get("reconnect_on_timeout", True):
            break
        time.sleep(max(0.1, float(conn.get("reconnect_timeout_ms") or 2000)
                       / 1000.0))
        if _newer_queued(entry):
            return False
    shown = values[0] if len(values) == 1 else f"{len(values)} values"
    oid = str(o.get("id") or "")
    if ok:
        if oid in _mbw_down:
            _mbw_down.discard(oid)
            log.info("modbus write %s answering again", o.get("id"))
        note = (f"wrote {shown} to {job['address']} "
                f"(FC {job['fc']}, unit {cfg['unit']})")
        rep = _mbw_repeat.get(oid)
        if rep is not None:
            note += (f"; repeats {shown} every {_secs(rep['every'])} "
                     "(drive watchdog on)")
        _record(o, note=note + _watchdog_note(conn))
        return True
    # A real write that fails is logged every time, as before. A REPEAT that
    # fails while the device is already known to be down is not: at one a
    # second that would be 3600 identical lines an hour about one outage.
    if not (entry.get("repeat") and oid in _mbw_down):
        log.warning("modbus write %s failed: %s", o.get("id"), message,
                    extra=faults.event(faults.NET_MODBUS_NO_REPLY))
    _mbw_down.add(oid)
    _record(o, RuntimeError(message or "the device did not answer"))
    return False


def _watchdog_note(conn):
    """"; drive watchdog not set: ..." while the connection's connect-time
    setup failed, so the node a person is looking at says why the drive
    still stops (or does not)."""
    try:
        st = _buses().connect_status((conn or {}).get("id"))
    except Exception:                                       # noqa: BLE001
        st = None
    if not st or st.get("ok"):
        return ""
    return f"; drive watchdog not set: {st.get('message')}"
