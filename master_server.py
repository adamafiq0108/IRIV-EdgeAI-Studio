# -*- coding: utf-8 -*-
"""
master_server.py -- One system's master.

One master per system: two systems is two of these, each with its own port, its
own slave registry and its own pages. The hub (hub_server.py) runs in front and
links to them.

Deliberately thin, and it names no system and no customer. The slug comes from
--system, the package from the systems directory, the pages from core/. Adding a
system is a new folder plus a config line, and the diff to this file is zero. If
you are editing this file to add a system, something is wrong.

Run:
    python master_server.py --system conveyor --port 5110
    python master_server.py --system packing --port 5111
"""

import argparse
import asyncio
import contextlib
import logging
import os
import pathlib
import re
import time

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from core import config
from core.version import VERSION
from core.middleware import install_access_log
from core.routes.audit import router as audit_router
from core.routes.builder import router as builder_router
from core.routes.capacity import router as capacity_router
from core.routes.logs import install_error_handlers
from core.routes.datasets import router as datasets_router
from core.routes.media import router as media_router
from core.routes.events import router as events_router
from core.routes.logs import router as logs_router
from core.routes.models import router as models_router
from core.routes.storage_repair import router as storage_repair_router
from core.routes.remote import router as remote_router
from core.routes.auth import require as auth_require
from core.routes.auth import router as auth_router
from core.routes.chip_balance import router as chip_balance_router
from core.routes import pollcache as pollcache_route
from core.routes.cameras import router as cameras_router
from core.routes.control import router as control_router
from core.routes.devices import router as devices_router
from core.routes.folders import router as folders_router
from core.routes.functions import router as functions_router
from core.routes.workflow import router as workflow_router
from core.routes.class_filter import router as class_filter_router
from core.routes.history import router as history_router
from core.routes.blynk import router as blynk_router
from core.routes.monitoring import router as monitoring_router
from core.routes.overview import router as overview_router
from core.routes.email import router as email_router
from core.routes.mail import router as mail_router
from core.routes.panel import power_router
from core.routes.telegram import router as telegram_router
from core.routes.mqtt_broker import router as mqtt_broker_router
from core.routes.mqtt_test import router as mqtt_test_router
from core.routes.mqtt_clients import router as mqtt_clients_router
from core.routes.workflow_live import router as workflow_live_router
from core.routes.overlay import router as overlay_router
from core.routes.video_saving import router as video_saving_router
from core.services import telegram_net as tg_net
from core.routes.proxy import router as proxy_router
from core.routes.settings import router as settings_router
from core.routes.spa import router as spa_router
from core.routes.system import router as system_router
from core.services import audit as audit_service
from core.services import discovery as discovery_service
from core.services import email as email_service
from core.services import inventory as inventory_service
from core.services import log_collect
from core.services import logs as logs_service
from core.services import main as main_service
from core.services import slaves as slaves_service
from core.services import sync as sync_service
from core.services.registry import Registry, SystemLoadError

logging.basicConfig(level=logging.INFO)
# Everything logged anywhere in this process also lands in a bounded in-memory
# ring the Logs page reads. Installed here, before any module logs, so startup
# failures are visible in the UI too (core/services/logs.py).
logs_service.install()
# ...and nothing this process does dies quietly -- see the hub for the shape.
# A master runs the schedulers (footage mail, Telegram, alert watches) on
# long-lived threads, and a thread that dies takes its whole feature with it
# in silence.
log_collect.install_hooks("this system's master")
logger = logging.getLogger(__name__)

_BASE = pathlib.Path(__file__).resolve().parent

#: Events kept in memory for analytics widgets. The master has no database yet
#: (core/repos/ is empty); a restart re-syncs from each slave's own history
#: rather than losing anything (contract-v1.md sec 2: since=None means "from
#: the beginning"). This cap is what keeps memory bounded in the meantime.
MAX_EVENTS = 5000


async def _sync_loop(app):
    """Locate what we cannot reach, then poll every configured slave. Forever.

    Runs as an asyncio task started by the lifespan below. Both steps block
    (urllib and socket work over an internal thread pool), so they run off the
    event loop -- otherwise one slow slave would stall every page this master
    serves for the length of the whole cycle.
    """
    interval = app.state.sync_interval_s
    while True:
        try:
            await asyncio.to_thread(_relocate, app)
            updated, new_events, next_cursors = await asyncio.to_thread(
                sync_service.poll_all, app.state.slave_configs, app.state.cursors
            )
            async with app.state.ingest_lock:
                _apply_sync(app, updated, new_events, next_cursors)
        except Exception:
            # A bad cycle must not kill the loop -- the next interval tries
            # again, same as any of the individual slaves recovering.
            logger.exception("[MASTER] sync cycle failed")
        await asyncio.sleep(interval)


def _live_only(app, stats):
    """`stats` without the numbers the app is only HOLDING: with the AI off,
    a detection-fed rule's kept tally (today's count, a total) is shown on
    its card, but it is not a reading -- history keeps the gap."""
    try:
        from core.services import detector_control as dc
        kept = set((dc.app_state(app.state.systems_dir, app.state.system.slug)
                    or {}).get("ai_kept") or ())
    except Exception:                                       # noqa: BLE001
        kept = set()
    if not kept:
        return stats
    return {k: v for k, v in (stats or {}).items() if k not in kept}


def _sample_history(app):
    """One history sample of the current fleet aggregate + local stats."""
    try:
        from core.services import history as history_service
        from core.services import main as main_service
        stats = main_service.aggregate_stats(app.state.slaves,
                                             getattr(app.state, "local_stats", None))
        history_service.sample(_live_only(app, stats))
    except Exception:                                       # noqa: BLE001
        # DEBUG here was discarded before any handler saw it (root level is
        # INFO), so a history store that stopped taking samples showed as
        # flat charts and a clean Logs page. ERROR, with the traceback.
        logger.error("[MASTER] history sample failed", exc_info=True)


def _unreachable(app):
    """Configured slaves with no address, or that failed to answer last cycle.

    A device answering at its known url is never swept for: re-addressing a
    reachable device would hand the sweep authority it does not need, and the
    sweep takes its answer from whoever replies (core/services/discovery.py).
    On the first cycle app.state.slaves is empty, so this is exactly the
    url-less ones.
    """
    offline = {s["host_serial"] for s in app.state.slaves if not s.get("online")}
    return [s for s in app.state.slave_configs
            if not s["url"] or s["host_serial"] in offline]


def _relocate(app):
    """Sweep for unreachable slaves and adopt any address they answer on."""
    if not app.state.discovery_enabled:
        return
    pending = _unreachable(app)
    if not pending:
        return

    found = discovery_service.locate(pending, app.state.system.slug,
                                     **app.state.discovery_cfg)
    changed = {}
    for slave in app.state.slave_configs:
        url = found.get(slave["host_serial"])
        if url and url != slave["url"]:
            slave["url"] = url
            app.state.slave_tokens[slave["host_serial"]]["url"] = url
            changed[slave["host_serial"]] = url

    if changed:
        for host_serial, url in changed.items():
            logger.info("[MASTER] located %s at %s", host_serial, url)
        _persist_urls(app, changed)


def _persist_urls(app, changed):
    """Write discovered addresses into slaves.local.toml.

    The gitignored local file is the only one a machine ever writes
    (core/config.py): the committed slaves.toml is structure and operator
    intent. Persisting means a restart polls straight away instead of paying
    for a sweep to rediscover what this cycle already learned.
    """
    try:
        local = config.load_local("slaves", app.state.config_dir)
        entries = local.setdefault("slaves", {})
        for host_serial, url in changed.items():
            entries.setdefault(host_serial, {})["url"] = url
        config.save_local("slaves", local, app.state.config_dir)
    except OSError as e:
        # A read-only config dir costs a sweep next cycle, nothing more. The
        # addresses are already live in app.state.
        logger.warning("[MASTER] discovered urls not persisted: %s", e)


def _apply_sync(app, updated, new_events, next_cursors):
    # Token never rides in app.state.slaves: that list is what templates and
    # /api/health read, and a credential has no business reaching either.
    stripped = [
        {k: v for k, v in slave.items() if k != "token"}
        for slave in updated.values()
    ]
    # v2.0: stamp master-owned metadata (name/description/notes/folder) onto
    # each device and refresh the cached folder snapshot the sidebar renders
    # from. Both degrade to empty on a Postgres outage -> every device shows
    # as Unassigned; the sync loop's own try/except keeps a bad DB cycle from
    # killing polling.
    app.state.slaves = inventory_service.merge_meta(
        stripped, inventory_service.device_meta_map())
    app.state.folders = inventory_service.folder_snapshot(app.state.system.slug) or app.state.folders
    app.state.cursors.update(next_cursors)

    for events in new_events.values():
        app.state.events.extend(events)
    if len(app.state.events) > MAX_EVENTS:
        app.state.events = app.state.events[-MAX_EVENTS:]

    # Persist events to the queryable store (analytics + event table). Best
    # effort: the repo returns 0 on a Postgres outage, so the in-memory ring
    # above stays the live source and a bad DB cycle never blocks polling.
    try:
        from core.repos import events as events_repo
        slug = app.state.system.slug
        label_by_host = {s.get("host_serial"): (s.get("label") or s.get("display_name"))
                         for s in app.state.slaves}
        # Never a reserved name: a synced event must not pass for the local
        # app's ("__local__"), whatever a device was stored as (Pipi N-2).
        rows = [events_repo.expand(ev, slug, host_serial=host,
                                   device_label=label_by_host.get(host))
                for host, events in new_events.items()
                if slaves_service.valid_serial(host) for ev in events]
        if rows:
            events_repo.insert_many(rows)
    except Exception:
        # "Best effort" must still be VISIBLE. At DEBUG under an INFO root
        # this was `except: pass`: live tiles kept animating off the ring
        # above while the Event Table, Analytics and every history chart
        # returned nothing, with no line anywhere saying the writes had
        # stopped. The detections are already lost; the record must not be.
        logger.error("[MASTER] event persistence skipped", exc_info=True)

    # Fleet bandwidth point for the Live Monitoring graph (deliberate sum of
    # per-device rates -- unlike stats, this sum is meaningful). Bounded ring.
    import datetime as _dt
    up = sum(int((s.get("telemetry") or {}).get("net_up_bps") or 0) for s in app.state.slaves)
    down = sum(int((s.get("telemetry") or {}).get("net_down_bps") or 0) for s in app.state.slaves)
    app.state.bandwidth_history.append(
        {"ts": _dt.datetime.now().astimezone().isoformat(), "up_bps": up, "down_bps": down})
    if len(app.state.bandwidth_history) > 240:  # ~ last 240 cycles
        app.state.bandwidth_history = app.state.bandwidth_history[-240:]

    online = sum(1 for s in app.state.slaves if s["online"])
    logger.info("[MASTER] sync: %d/%d device(s) online, %d event(s) this cycle",
                online, len(app.state.slaves), sum(len(e) for e in new_events.values()))


def _apply_local(app, limit=2000):
    """Ingest the local detection app's new events + stats (local_ingest).

    Events join the same in-memory ring and Postgres store a synced device's
    do (host '__local__'), so the Analytics page, the widgets and the Event
    Table see local detections with no pairing. Stats land on
    app.state.local_stats and merge into the fleet aggregate at read time."""
    ingest = getattr(app.state, "local_ingest", None)
    if ingest is None:
        return
    try:
        events = ingest.poll(limit)
        app.state.local_stats = ingest.stats()
    except Exception:
        # A local app whose event log cannot be drained goes silent on every
        # page at once. Below the root level that looked like "no detections".
        logger.error("[MASTER] local ingest failed", exc_info=True)
        return
    if not events:
        return
    app.state.events.extend(events)
    if len(app.state.events) > MAX_EVENTS:
        app.state.events = app.state.events[-MAX_EVENTS:]
    try:
        from core.repos import events as events_repo
        from core.services import datasets as datasets_service
        rows = [events_repo.expand(ev, app.state.system.slug,
                                   host_serial="__local__",
                                   device_label=ingest.display_name)
                for ev in events]
        # Only this path may ask for "update the existing row", and only on
        # each table's stored identity column (Pipi N-1). The synced path
        # above passes none, so a paired device's hint is always an insert.
        events_repo.insert_many(rows, upsert_keys=datasets_service.upsert_keys(
            app.state.systems_dir, app.state.system.slug))
    except Exception:
        # Same failure as the synced path above, for locally detected events:
        # unseen at DEBUG, and the only symptom is an empty Event Table.
        logger.error("[MASTER] local event persistence skipped", exc_info=True)


#: How often the LOCAL detection app's event log is drained into the stores.
#:
#: This used to ride the slave sync loop, whose shipped interval is 600s
#: (config/slaves.toml). With a 2000-row cap per pass that is a hard ceiling of
#: ~3.3 rows/second -- and a system writing faster than that did not slow down,
#: it LOST rows: events.jsonl kept growing until it rotated, and the reader
#: found its cursor had been rotated past (services/local_ingest.py). Draining
#: a local log has nothing to do with how often remote devices are polled, so
#: it no longer waits for them.
LOCAL_INGEST_INTERVAL_S = 5


async def _local_loop(app):
    """Drain the local detection app's event log, and sample history.

    Separate from _sync_loop because the two have unrelated cadences: polling
    a remote device over a link is expensive and rare, draining a log on the
    same disk is cheap and must keep up with what is being written.

    Shares app.state.events with _sync_loop, so both take the same lock --
    they are different asyncio tasks, and a list both of them rewrite is a
    race however unlikely the overlap looks.
    """
    while True:
        try:
            async with app.state.ingest_lock:
                await asyncio.to_thread(_apply_local, app)
            # Sample the aggregate stats into the history rings (charts with
            # real time axes). sample() self-rate-limits, so calling it every
            # tick costs nothing between windows -- but on the 600s loop it
            # could never sample finer than 600s either.
            await asyncio.to_thread(_sample_history, app)
        except Exception:
            logger.exception("[MASTER] local ingest tick failed")
        await asyncio.sleep(LOCAL_INGEST_INTERVAL_S)


async def _health_loop(app):
    """Fast liveness tick, decoupled from the heavy sync sweep.

    Every [sync] health_interval_s, light-probe every known slave (/api/health +
    /api/agent/info only -- see sync.ping_all) and merge the online/app_running/
    heartbeat delta onto the live slave entries. This is what makes a device
    going down show within seconds instead of up to interval_s (default 10 min);
    the full /api/summary telemetry pull stays on the slower _sync_loop.

    It only ever mutates fields on existing app.state.slaves entries (matched by
    host_serial), so a full sync replacing the list mid-tick is harmless -- the
    next update reads app.state.slaves fresh. On the first boot before any sync,
    app.state.slaves is empty and this no-ops.
    """
    interval = app.state.health_interval_s
    while True:
        try:
            slaves = app.state.slaves
            if slaves:
                deltas = await asyncio.to_thread(sync_service.ping_all, slaves)
                for slave in app.state.slaves:
                    delta = deltas.get(slave["host_serial"])
                    if delta:
                        slave.update(delta)
        except Exception:
            # A bad tick must not kill the loop; the next one tries again.
            logger.exception("[MASTER] health tick failed")
        await asyncio.sleep(interval)


async def _email_loop(app):
    """Fire each configured send time once per day, on the local clock.

    Checked every 30s against config re-read each pass, so a schedule saved on
    the Email page takes effect without a restart. Last-fired marks live in
    memory only: a master restarted mid-day may re-send that day's digest once,
    which beats persisting scheduler state for a daily email.
    """
    fired = {}   # "HH:MM" -> date last sent
    import datetime
    while True:
        try:
            cfg = email_service.load(app.state.system.slug, app.state.config_dir)
            if cfg.get("enabled"):
                now = datetime.datetime.now()
                for t in email_service.parse_times(cfg):
                    key = t.strftime("%H:%M")
                    due = now.time() >= t and fired.get(key) != now.date()
                    if not due:
                        continue
                    fired[key] = now.date()
                    stats = main_service.aggregate_stats(app.state.slaves)
                    digest = email_service.build_digest(stats, app.state.events)
                    ok, msg = await asyncio.to_thread(
                        email_service.send, cfg, digest, app.state.system.display_name
                    )
                    logger.info("[EMAIL] scheduled %s: %s", key, msg)
        except Exception:
            logger.exception("[EMAIL] scheduler cycle failed")
        await asyncio.sleep(30)


async def _footage_export_loop(app):
    """Send the saved recurring footage export when its period closes.

    Ticks slowly -- a minute is plenty for a schedule whose fastest setting is
    hourly -- and re-reads the file each pass, so a change saved on the
    Footage page takes effect without a restart.

    Unlike the digest loop above, the last-run mark is PERSISTED. A digest
    re-sent after a restart is a duplicate email; a footage export re-sent is
    a duplicate of every photo from the period, and a monthly one that
    restarts on the 1st would post the month again. It is written whether the
    send worked or not, because a failing mailbox would otherwise retry every
    minute for ever, and the report that matters is the next one.
    """
    from core.services import footage_mail as footage_mail_service
    from core.services import footage_schedule as sched_service
    while True:
        try:
            slug = app.state.system.slug
            config_dir = app.state.config_dir
            sched = sched_service.load(slug, config_dir)
            window = sched_service.due(sched)
            if window:
                start, end = window
                rows = await asyncio.to_thread(
                    footage_mail_service.collect, app.state.systems_dir, slug,
                    None, int(start), int(end),
                    ("snapshot", "clip") if sched.get("include_clips")
                    else ("snapshot",))
                if not rows:
                    # A quiet period is not a failure, and an email saying
                    # "0 files" every morning is how a report becomes noise
                    # that nobody opens on the day it is not empty.
                    result = "nothing recorded in that period"
                else:
                    name = getattr(app.state.system, "name", "") or slug
                    said = []
                    # Each channel is attempted independently and reports
                    # separately. A Telegram group being unreachable must not
                    # cancel the email, and "sent" covering a send that only
                    # half happened is the kind of report an operator trusts
                    # and should not.
                    if sched.get("send_email") and str(sched.get("to") or "").strip():
                        ok, msg = await asyncio.to_thread(
                            footage_mail_service.send, app.state.systems_dir,
                            slug, rows, sched.get("to"), sched.get("account"),
                            str(sched.get("subject") or ""),
                            str(sched.get("body") or ""),
                            bool(sched.get("as_zip", True)), config_dir, name)
                        said.append(("email: " if ok else "email failed: ") + msg)
                    if sched.get("send_telegram") \
                            and str(sched.get("chat_id") or "").strip():
                        ok, msg = await asyncio.to_thread(
                            footage_mail_service.send_telegram,
                            app.state.systems_dir, slug, rows,
                            sched.get("chat_id"), sched.get("bot"),
                            str(sched.get("body") or ""), config_dir, name)
                        said.append(("telegram: " if ok else
                                     "telegram failed: ") + msg)
                    result = " · ".join(said) or "no channel was switched on"
                sched_service.mark_ran(slug, end, result, config_dir)
                logger.info("[FOOTAGE] scheduled export (%s): %s",
                            sched_service.describe(sched), result)
        except Exception:
            logger.exception("[FOOTAGE] export cycle failed")
        await asyncio.sleep(60)


async def _blynk_loop(app):
    """Push mapped stat values to the Blynk cloud on the configured interval.

    The service self-disables (enabled flag, missing token, no mappings) and
    self-backs-off on failures, so this loop just ticks. Config is re-read
    each push -- a save on the Blynk page takes effect next tick, no restart.
    """
    from core.services import blynk as blynk_service
    from core.services import main as main_service
    while True:
        try:
            stats = main_service.aggregate_stats(
                app.state.slaves, getattr(app.state, "local_stats", None))
            await asyncio.to_thread(blynk_service.push, stats)
            interval = await asyncio.to_thread(blynk_service.interval_s)
        except Exception:
            logger.exception("[BLYNK] push cycle failed")
            interval = 30
        await asyncio.sleep(max(1, interval))


async def _telegram_command_loop(app):
    """Answer this system's Telegram commands. The one INBOUND path on the box.

    Long polling, not a webhook: a webhook needs a public address and a
    certificate, and every deployment this runs on sits behind somebody's
    factory NAT. `getUpdates` needs nothing but outbound HTTPS, which is what a
    box that can already send alerts demonstrably has.

    ONE CONSUMER PER TOKEN, which Telegram enforces by handing a second poller
    a 409 and silently starving the first. That is why a bot belongs to exactly
    one system (services/telegram_bots.py): this loop only runs where the bot
    is assigned, so two masters can never poll the same token.

    Everything it can refuse, it refuses before doing any work: no bot, no
    allowlist entry, no such command. An unknown chat is answered with silence
    rather than a "not allowed" -- telling a stranger they found a real bot is
    the one thing a closed door should not do.
    """
    from core.services import telegram_bots as bots_service
    from core.services import detector_control as dc
    # Where a chat is mid-conversation: {chat_id: {"cmd", "btn"}} while it has
    # been asked for dates. In memory on purpose -- a half-typed date is worth
    # nothing after a restart, and persisting it would mean a config write per
    # keystroke.
    waiting = {}
    offset = 0
    # What the "/" menu on the bot currently says. Republished only when the
    # canvas changes it -- see tg_net.menu_digest.
    published = None
    # Telegram's own idle timeout for a long poll. The request returns as soon
    # as anything arrives, so this is how long a QUIET connection is held, not
    # a delay on an answer.
    hold = 50
    while True:
        try:
            slug = app.state.system.slug
            config_dir = app.state.config_dir
            bot = bots_service.for_system(slug, config_dir)
            if not bot or not bot.get("token"):
                await asyncio.sleep(30)          # nothing assigned yet
                published = None                 # a new bot starts with none
                continue
            # Keep Telegram's own command menu in step with the canvas, so
            # typing "/" in the chat lists what this system actually answers.
            commands = await asyncio.to_thread(
                dc.get_commands, app.state.systems_dir, slug)
            digest = tg_net.menu_digest(commands)
            if digest != published:
                if await asyncio.to_thread(
                        tg_net.publish_commands, bot["token"], commands):
                    published = digest
            updates, offset = await asyncio.to_thread(
                tg_net.poll, bot["token"], offset, hold)
            for update in updates:
                await asyncio.to_thread(
                    tg_net.handle, app.state.systems_dir, slug, config_dir,
                    bot, update, waiting,
                    getattr(app.state.system, "name", "") or slug)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                              # noqa: BLE001
            # A poll that fails must not end the loop: the network drops, the
            # token gets rotated, Telegram has an outage. Back off so a
            # persistent failure is not a hot loop on a box doing inference.
            print(f"[TG] command poll failed: {exc}")
            await asyncio.sleep(15)


async def _watch_loop(app):
    """Threshold watches: a Query on a timer, a message when it crosses.

    Here rather than in the detection app for the reason every data feature is
    here -- the app has no database. It emits events, core writes them, so the
    process that can answer "how many today" is this one.

    Everything about pacing is deliberate. The tick is coarse (30s) and each
    watch keeps its own due time, so twenty watches on one box are twenty
    queries per their own intervals rather than twenty per tick. A watch fires
    on the CROSSING and then holds its tongue for the quiet time: a number
    stuck over the line is one situation, and reporting it every fifteen
    minutes is how somebody learns to mute the chat.

    State lives in memory. A restart re-arms every watch, so a value already
    over the line is reported once more -- a duller failure than a config
    write per check on an SD card.
    """
    from core.services import alert_watch as aw
    from core.services import detector_control as dc
    from core.services import sqlconsole
    from core.services import telegram_bots as bots_service
    from core.services import telegram_commands as tg
    state = {}                       # watch id -> {"over", "sent", "due"}
    while True:
        try:
            await asyncio.sleep(30)
            slug = app.state.system.slug
            config_dir = app.state.config_dir
            watches = await asyncio.to_thread(
                dc.get_watches, app.state.systems_dir, slug)
            live = {w["id"] for w in watches}
            for gone in [k for k in state if k not in live]:
                state.pop(gone, None)
            now = time.time()
            for w in watches:
                st = state.setdefault(w["id"], {})
                if now < float(st.get("due") or 0):
                    continue
                st["due"] = now + w["every_min"] * 60
                await asyncio.to_thread(_run_watch, app, slug, config_dir, w,
                                        st, aw, sqlconsole, bots_service, tg)
        except asyncio.CancelledError:
            raise
        except Exception:                                     # noqa: BLE001
            logger.exception("[WATCH] loop failed")
            await asyncio.sleep(30)


def _run_watch(app, slug, config_dir, w, st, aw, sqlconsole, bots_service, tg):
    """One watch, checked and possibly sent. Blocking; runs in a thread."""
    start, stop = tg.bounds(w["period"])
    params = {"from": start, "to": stop} if start and stop else None
    res = sqlconsole.run(slug, w["sql"],
                         {t["key"]: t for t in w["tables"] if t.get("key")},
                         config_dir=config_dir, params=params)
    if not res.get("ok"):
        # Logged, not sent. A broken statement is for whoever wrote it to see
        # on the canvas; mailing the database's error message to a shift
        # supervisor every fifteen minutes helps nobody.
        logger.warning("[WATCH] %s: %s", w["name"], res.get("error"))
        return
    value = aw.pick_value(res.get("columns") or [], res.get("rows") or [])
    over = aw.test(w["op"], value, w["value"])
    send_it, new_state = aw.should_send(st, over, time.time(), w["quiet_min"])
    if not send_it:
        # Nothing to deliver, so nothing can fail: this is bookkeeping (back
        # under the line and re-armed, or still over and already reported).
        aw.commit(st, new_state)
        return
    period_text = tg.describe(start, stop)
    # The rows as sentences, like a command's answer (tg.report keeps the
    # table for a wide or long result). The watch's own words around it are
    # the operator's and are not touched.
    table_text = tg.report(res.get("columns") or [], res.get("rows") or [],
                           period=period_text)
    fields = {"value": aw.number(value), "threshold": aw.number(w["value"]),
              "name": w["name"], "period": period_text, "table": table_text}
    default = (f"{w['name']}: {aw.number(value)} "
               f"({w['op']} {aw.number(w['value'])}) - {period_text}")
    # A watch with no send nodes has nothing that CAN fail, so its edge is
    # spent either way; anything else has to get a message out first.
    delivered = not w["sends"]
    for send in w["sends"]:
        try:
            if send["kind"] == "telegram":
                got_out = _watch_telegram(slug, config_dir, w, send, fields,
                                          default, bots_service, tg)
            else:
                got_out = _watch_email(slug, config_dir, send, fields, default)
            delivered = delivered or got_out
        except Exception as exc:                              # noqa: BLE001
            logger.warning("[WATCH] %s could not send: %s", w["name"], exc)
    aw.commit(st, new_state, delivered)


def _watch_telegram(slug, config_dir, w, send, fields, default, bots_service, tg):
    """Send one watch through this system's bot. True only if it went out.

    The answer is what tells the caller whether the rising edge was spent, so
    every way out of here that sends nothing has to say False -- an unassigned
    bot reported as a send would silence the watch exactly like a failed one.
    """
    from core.services import alert_watch as aw
    bot = bots_service.for_system(slug, config_dir)
    if not bot or not bot.get("token"):
        logger.warning("[WATCH] %s: no Telegram bot is assigned to this system",
                       w["name"])
        return False
    chat = send.get("chat_id") or bot.get("chat_id") or ""
    if not chat:
        logger.warning("[WATCH] %s: the Telegram node names no chat", w["name"])
        return False
    template = str(send.get("message") or "")
    if template.strip():
        # The operator's prose is escaped BEFORE the tokens go in: a camera
        # called "line<2" in a message would otherwise break Telegram's HTML
        # parse and the whole send is refused, silently. {table} is the one
        # exception -- it arrives as the markup tg.table already produced.
        safe = {k: tg._esc(v) for k, v in fields.items() if k != "table"}
        safe["table"] = fields["table"]
        body = aw.render(tg._esc(template), safe).strip()
    else:
        body = tg._esc(default)
    tg_net.send(bot["token"], chat, body)
    return True


def _watch_email(slug, config_dir, send, fields, default):
    """Mail one watch. True only if the mailbox accepted it -- see above."""
    from core.services import alert_watch as aw
    from core.services import email as email_service
    from core.services import mail_accounts as mail_service
    acc, problem = mail_service.resolve(send.get("account"), config_dir)
    if acc is None:
        try:
            acc = email_service.load(slug, config_dir)
        except Exception:                                     # noqa: BLE001
            acc = None
        if not acc:
            logger.warning("[WATCH] no mailbox to send from: %s", problem)
            return False
    to_list = [x.strip() for x in re.split(r"[,;\s]+", send.get("to") or "")
               if x.strip()]
    if not to_list:
        logger.warning("[WATCH] the Email node names nobody to send to")
        return False
    subject = aw.render(send.get("subject") or "", fields).strip() or default
    body = aw.render(send.get("body") or "", fields).strip() or default
    ok, why = email_service.send_plain(acc, to_list, subject[:200], body)
    if not ok:
        # A refused relay is the same field failure as a raised exception: the
        # edge must survive it, so this is reported, not swallowed.
        logger.warning("[WATCH] mail not sent: %s", why)
    return bool(ok)


@contextlib.asynccontextmanager
async def _lifespan(app):
    # Guards app.state.events / app.state.local, which _sync_loop and
    # _local_loop both rewrite. Created here rather than at import so it binds
    # to the running loop.
    app.state.ingest_lock = asyncio.Lock()
    tasks = [asyncio.create_task(_sync_loop(app)),
             asyncio.create_task(_local_loop(app)),
             asyncio.create_task(_health_loop(app)),
             asyncio.create_task(_email_loop(app)),
             asyncio.create_task(_footage_export_loop(app)),
             asyncio.create_task(_blynk_loop(app)),
             asyncio.create_task(_telegram_command_loop(app)),
             asyncio.create_task(_watch_loop(app))]
    # The system's one app (the whole workflow, AI detection a switch in
    # it): started here with the AI off if it is not up, and restarted if it
    # crashes (app_supervisor). A thread of its own: starting an app waits on
    # a process, not on the loop.
    from core.services import app_supervisor
    app_supervisor.start_supervisor(app.state.systems_dir, app.state.system.slug,
                                    app.state.config_dir)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_app(slug, config_dir=None):
    if not slug:
        raise SystemLoadError("no system given: master_server needs --system <slug>")

    app = FastAPI(title="IRIV Edge AI Studio master", docs_url=None, redoc_url=None,
                 lifespan=_lifespan)

    from core.services import secrets_at_rest
    secrets_at_rest.run(config_dir)

    systems_cfg = config.systems(config_dir)
    systems_dir = pathlib.Path(systems_cfg.get("dir", "systems"))
    if not systems_dir.is_absolute():
        systems_dir = _BASE / systems_dir

    # enabled=[slug]: this master loads its own system and nothing else. A
    # second system's package being broken is not this master's problem. The
    # persistent generated-systems store (outside the repo) is scanned too, so a
    # hub-created system survives a git pull.
    generated_dir = config.generated_systems_dir()
    # Refresh store systems' app code from the reference (same repair the hub
    # does) -- a master started directly still runs current app code.
    names = {}
    try:
        from core.services import provision as provision_service
        provision_service.repair_store_apps(systems_dir)
        # A rename made on the hub must show in this system's own header and
        # nav too -- one stored name, every reader.
        names = provision_service.system_names(config_dir)
    except Exception:
        pass
    registry = Registry(systems_dir, enabled=[slug], extra_dirs=[generated_dir],
                        names=names)
    system = registry.get(slug)
    if system is None:
        raise SystemLoadError(
            f"no system package {slug!r} under {systems_dir} or {generated_dir}. "
            f"Discovered: {[s.slug for s in registry] or 'nothing'}"
        )
    from core.services import provision as provision_service
    refused = provision_service.removed_reason(slug, systems_cfg, generated_dir)
    if refused:
        raise SystemLoadError(refused)

    # This system's package may live in the built-in dir OR the generated store;
    # point systems_dir at whichever holds it, so the detection app, models and
    # templates resolve to the right place.
    if (generated_dir / slug / "system.py").exists():
        systems_dir = generated_dir
    # The app may fetch its CSV files and Telegram recipients from this
    # master's port and no other (Pipi N-3); an app the hub starts reads it
    # from the package config, so it is written there (only when it changed).
    try:
        port_now = int(os.environ.get("CYTRON_MASTER_PORT") or 0)
        if port_now and (pathlib.Path(systems_dir) / slug).is_dir():
            from core.services import detector_control as _dc  # noqa: PLC0415
            _dc.note_master_port(str(systems_dir), slug, port_now)
    except Exception as exc:                                  # noqa: BLE001
        logger.debug("[MASTER] could not note the master port: %s", exc)
    # Secret-holding local files owner-only (0600): a system's
    # config.local.toml carries its SMTP app password and Telegram bot token,
    # and files written before writes were created 0600 may still be 0644.
    # Never fails the start; logged once, as a count (core/config.py).
    try:
        tightened = config.tighten_secrets(
            config_dir, (systems_dir, config.generated_systems_dir()))
        if tightened:
            logger.info("[CONFIG] made %d secret file(s) owner-only (0600)",
                        tightened)
    except Exception as exc:                                  # noqa: BLE001
        logger.debug("[CONFIG] could not tighten secret files: %s", exc)

    slaves_cfg = config.slaves(config_dir)
    slave_configs = slaves_service.load(slaves_cfg, slug, config_dir)

    app.state.config_dir = config_dir
    app.state.registry = registry
    # Where a published builder layout is installed as a system package.
    app.state.systems_dir = str(systems_dir)
    app.state.system = system
    # Scope the audit log to this system, so its Audit page shows only its own
    # entries (the log table is shared across a box's masters).
    audit_service.set_system(system.slug)
    # ...and name this process on its own log records. install() runs at
    # import, before the registry exists, so the slug is only knowable here.
    # Without it a master stamps its rows "hub" and the Log Hub's source
    # filter sends support to the wrong process.
    logs_service.set_origin(logs_service.source_of_master(system.slug))
    # Tail the LOCAL detection app's event log into analytics (standalone runs
    # its app beside the master, with no agent to sync through).
    from core.services.local_ingest import LocalIngest
    app.state.local_ingest = LocalIngest(systems_dir, system.slug,
                                         system.display_name)
    app.state.local_stats = {}
    app.state.sync_interval_s = slaves_service.interval_s(slaves_cfg)
    app.state.health_interval_s = slaves_service.health_interval_s(slaves_cfg)
    # url + token per device, for the sync loop and the stream/footage proxy.
    # Never rendered, never returned by an API. The url is mutable: _relocate()
    # rewrites it in place when a device turns up at a new address.
    app.state.slave_configs = slave_configs
    app.state.slave_tokens = {s["host_serial"]: {"url": s["url"], "token": s["token"]}
                              for s in slave_configs}

    # v2.0 master persistence (folders / device metadata / audit). Best-effort:
    # a cytron_dashboard outage must not stop the master booting -- device
    # metadata simply degrades to Unassigned and audit writes skip. Polling,
    # telemetry, the control channel and auth are all DB-independent.
    try:
        from core.db.migrations import run_migrations
        run_migrations(log=lambda m: print(f"[master] {m}"))
    except Exception as e:  # noqa: BLE001
        print(f"[master] WARNING: cytron_dashboard unavailable ({e}); "
              "folders/metadata/audit disabled until it is reachable")
    # Folder snapshot cached on state for the sidebar (no per-request DB);
    # refreshed by the sync loop. Empty until first refresh / on PG-down.
    app.state.folders = []

    discovery_cfg = discovery_service.settings(slaves_cfg)
    app.state.discovery_enabled = discovery_cfg.pop("enabled")
    app.state.discovery_cfg = discovery_cfg
    # Live state, refreshed by the sync loop. Empty until the first cycle
    # completes, which is a normal boot state, not an error.
    app.state.slaves = []
    app.state.events = []
    app.state.cursors = {}
    # v2.0 fleet bandwidth history for the Live Monitoring graph. In-memory
    # ring buffer (like events), appended once per sync cycle; ephemeral.
    app.state.bandwidth_history = []

    app.mount("/static", StaticFiles(directory=str(_BASE / "core" / "static")),
              name="static")
    # A package's own static files (images, a guide): present = mounted,
    # absent = nothing. No slug ever appears in a URL.
    system_static = systems_dir / slug / "static"
    if system_static.is_dir():
        app.mount("/system-static", StaticFiles(directory=str(system_static)),
                  name="system_static")
    app.include_router(auth_router)
    app.include_router(chip_balance_router)  # /api/system/chip-balance (read-only)
    app.include_router(pollcache_route.router)  # /api/system/poll-cache (read-only)
    app.include_router(system_router)
    app.include_router(settings_router)
    app.include_router(control_router)
    app.include_router(audit_router)
    app.include_router(builder_router)   # /api/builder - drag-and-drop widgets
    app.include_router(functions_router) # /api/functions - no-code analytics rules
    app.include_router(workflow_router)  # /api/workflow - visual pipeline editor
    app.include_router(class_filter_router)  # /api/workflow/class-filter - the dashboard card
    app.include_router(history_router)   # /api/history - sampled stat time series
    app.include_router(blynk_router)     # /api/blynk - push stats to Blynk IoT
    app.include_router(remote_router)    # /api/remote  - public domain + tunnel
    app.include_router(models_router)    # /api/models  - bring your own model
    app.include_router(storage_repair_router)  # /api/detector/dataset/repair - Try again on a drive's folders
    app.include_router(events_router)    # /api/events  - generic queryable event store
    app.include_router(datasets_router)  # /api/datasets - tables an operator defined
    app.include_router(media_router)     # /api/media - footage to run detection on
    app.include_router(logs_router)      # /api/logs    - the in-memory log ring
    # /api/capacity - the same box-wide counts the hub header shows, so the
    # meters stay put when an operator opens a system.
    app.include_router(capacity_router)
    # Last: an unhandled error anywhere above becomes one recorded line and one
    # {"ok": false, ...} body, instead of a 500 nobody sees (conventions sec 8).
    install_error_handlers(app)
    app.include_router(folders_router)
    app.include_router(devices_router)
    app.include_router(monitoring_router)
    app.include_router(overview_router)
    app.include_router(proxy_router)
    app.include_router(email_router)
    # /api/mail - the box's sending mailboxes. Managed on the hub, READ here:
    # an Email node picks one and the Footage page sends through one, so a
    # master that could not see the list would offer an empty picker.
    app.include_router(mail_router)
    # Reboot and shut down, from the sign-out menu on a system page too.
    app.include_router(power_router)
    # /api/telegram - same story: managed on the hub, READ here so a Telegram
    # node can offer the box's bots by name.
    app.include_router(telegram_router)
    # /api/mqtt/broker - the box's broker, for the Workflow page's pill and
    # its settings dialog. export_login: the app this master starts finds
    # the broker login's file through the environment it inherits.
    app.include_router(mqtt_broker_router)
    # /api/workflow/mqtt-test - the MQTT nodes' Test publish and Listen.
    app.include_router(mqtt_test_router)
    # /api/mqtt/clients - the MQTT Connections the MQTT nodes pick.
    app.include_router(mqtt_clients_router)
    # /api/workflow/live - the Workflow page's and Overlay panel's poll:
    # live values only, the compile cached by mtime (perf item L2).
    app.include_router(workflow_live_router)
    # /api/detector/overlay - the live overlay as events, for a system
    # whose browser draws it ([video] overlay = "client").
    app.include_router(overlay_router)
    # /api/detector/control/video - the Detection page's Video saving
    # switches ([video] on_demand, view_on_detect, view_hold_s).
    app.include_router(video_saving_router)
    from core.services import mqtt_broker as _mqtt_broker
    _mqtt_broker.export_login(app.state.config_dir)
    # The box switches' directory, for the app's live [system] chip_balance.
    from core.services import sysconfig as _sysconfig
    _sysconfig.export_location(app.state.config_dir)
    app.include_router(spa_router)   # /api/bootstrap + read-only page data (React SPA)
    app.include_router(cameras_router)   # /api/cameras/usage (fleet camera dedup)
    # Inside the session wall (added before it): an authenticated write
    # empties the shared poll cache before its response leaves.
    pollcache_route.install(app)
    # Session wall: every page and API except the open prefixes. An account
    # must also be allowed this master's system (core/routes/auth.py).
    app.middleware("http")(auth_require(slug))
    # Outermost (registered last): one line per request with its status code,
    # duration and id -- including the 401s the session wall above issues,
    # which is exactly the class of failure an operator reports and nobody can
    # otherwise see (core/middleware.py).
    install_access_log(app)

    @app.get("/api/health")
    def health():
        # The hub polls this and nothing else. Device counts ride along because
        # the master is the only process that knows its own slaves -- the hub
        # never reads a master's database. ai_running rides along too: most
        # systems pair no devices (the AI runs in this master's own detection
        # app), and without it the hub card says "idle" while detection runs.
        from core.services import detector_control as dc      # noqa: PLC0415
        slaves = app.state.slaves
        return {
            "ok": True,
            "system_slug": system.slug,
            "devices_online": sum(1 for s in slaves if s.get("online")),
            "devices_total": len(slaves),
            "ai_running": dc.ai_running(app.state.systems_dir, system.slug),
            # Running is not the same as working. A detector that failed to
            # load leaves the app up and the stream flowing, seeing nothing --
            # this is the sentence that says so, and it is empty when all is
            # well, so a healthy system's payload is unchanged.
            "detector_error": dc.detector_error(app.state.systems_dir,
                                                system.slug),
            # The release THIS process is executing, from the constant frozen
            # at import (core/version.py) rather than from any file on disk.
            # An update replaces the tree under a master that KillMode=process
            # deliberately keeps alive, so "a master answers on the port" and
            # "the master on the port is the new one" are different claims --
            # and the updater's verify_masters was only able to make the first
            # until this field existed. It caught a real one: an update once
            # finished reporting success with a master still running the
            # previous release out of the retained rollback directory.
            "version": VERSION,
        }

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, exc):
        return JSONResponse({"ok": False, "error": exc.detail},
                            status_code=exc.status_code)

    # -- React SPA serving -----------------------------------------------------
    # The built bundle (frontend/ -> core/static/app) is served here: hashed
    # assets under /assets, and a catch-all that returns index.html for every
    # client route (/, /platform, /manage/<id>, /login, ...). Registered LAST so
    # every real route + mount above wins; unknown /api paths still 404 as JSON.
    app_dist = _BASE / "core" / "static" / "app"
    if (app_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(app_dist / "assets")),
                  name="assets")
    from core.services import weblinks as _weblinks
    _weblinks.start(config_dir)       # reverse-proxy mode: watch port 80 from now

    @app.get("/{full_path:path}", name="spa.catch_all")
    def spa_catch_all(request: Request, full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        # Reverse-proxy mode (core/services/weblinks.py): a page opened on
        # this master's own port goes to http://<box>/s/<slug>/<page>; the
        # API keeps answering here for anything that calls the port.
        from core.services import weblinks
        own_port = int(os.environ.get("CYTRON_MASTER_PORT") or 0)
        go = weblinks.direct_redirect(request, slug, own_port, config_dir)
        if go:
            return RedirectResponse(go, status_code=307)
        index = app_dist / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503,
                                detail="frontend not built - run the frontend build (install.sh)")
        # <base href>: "/" on this port, "/s/<slug>/" through the hub's relay
        # -- the bundle's paths are relative to it (frontend/src/lib/base.js).
        return HTMLResponse(weblinks.index_html(str(index),
                                                weblinks.prefix_of(request, slug)),
                            headers={"Cache-Control": "no-store"})

    logger.info("[MASTER] %s (%s) from %s - %d slave(s) configured, syncing every %ds, "
                "discovery %s",
                system.slug, system.display_name, systems_dir,
                len(slave_configs), app.state.sync_interval_s,
                "on" if app.state.discovery_enabled else "off")
    return app


#: The exit status of a master whose system was removed on the Hub. The unit
#: (scripts/install.sh irivai-master@.service) lists it in
#: RestartPreventExitStatus, so systemd lets it go instead of restarting it.
REMOVED_EXIT = 78               # EX_CONFIG


def stand_down(reason, exit_code=None, sleep=time.sleep):
    """A removed system's master: run NOTHING, and stop systemd's loop.

    Exiting with an error under Restart=always meant a restart every 5 s for
    ever -- a journal line and a full import each time. A unit that knows
    the removed status (IRIV_REMOVED_EXIT in its environment, set beside
    RestartPreventExitStatus) is told so and stays down. An older unit
    cannot be told without root, so the master IDLES instead: no port, no
    supervisor, no workflow, one log line -- until somebody stops the unit
    or re-enables the system and restarts it."""
    logger.error("[MASTER] %s - standing down", reason)
    code = exit_code if exit_code is not None else \
        os.environ.get("IRIV_REMOVED_EXIT")
    if code:
        raise SystemExit(int(code))
    while True:
        sleep(3600)


def main():
    p = argparse.ArgumentParser(description="IRIV Edge AI Studio master (one system).")
    p.add_argument("--system", required=True, help="system slug, e.g. conveyor")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=5110)
    p.add_argument("--config-dir", default=None)
    args = p.parse_args()
    # CM5 + ATECC608 + Hailo: stops here with a plain message if one is absent.
    from core.services import hwcheck
    hwcheck.require(args.config_dir, "master " + args.system)
    # Name this process before anything else logs. create_app() sets it too,
    # but the provisioning check below and create_app's own start-up lines
    # ran first and went out stamped "hub", so a system's start-up records
    # read as the hub's (Tok Aba, 2026-10-14: each log shows its own process).
    logs_service.set_origin(logs_service.source_of_master(args.system))

    # The detection app has no database. When a Query is stored in a Variable
    # the app polls THIS process for the number, so a compile has to be able
    # to write a working address -- and the port is only ever known here, from
    # argv. Stamped before the app is built so the first compile after startup
    # already has it.
    os.environ["CYTRON_MASTER_PORT"] = str(args.port)
    # Removed on the Hub (a built-in package the enabled list no longer has):
    # before anything binds the port or starts the app supervisor.
    from core.services import provision as provision_service
    refused = provision_service.removed_reason(
        args.system, config.systems(args.config_dir),
        config.generated_systems_dir())
    if refused:
        stand_down(refused)
    # access_log=False: uvicorn's own access line prints the full request
    # target, query string included, to stdout -- the journal -- and the
    # query string is where a camera URL with its password used to ride (the
    # snapshot GET). The platform's own access log (core/middleware.py) keeps
    # one line per request, with the path only.
    # net.asgi(): the socket peer is kept for weblinks.prefix_of() -- uvicorn's
    # own proxy-header rewrite hid the hub's relay behind the LAN client.
    from core import net as _net
    # timeout_graceful_shutdown: see hub_server.py -- a master serves the live
    # picture and the overlay stream, which never end on their own.
    uvicorn.run(_net.asgi(create_app(args.system, args.config_dir)), host=args.host,
                port=args.port, access_log=False, timeout_graceful_shutdown=5,
                **_net.UVICORN)


if __name__ == "__main__":
    main()
