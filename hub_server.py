# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
hub_server.py -- The system hub. One process, in front of the masters.

Every system runs its own master (master_server.py). This process runs in front
of them and does exactly one thing: draw a card per system and link to that
system's master.

It deliberately does not SERVE a system. A hub that did would have to understand
every master's pages, hold their sessions, and stream their video -- at which
point it is a second master with none of the data, and every system's outage
becomes the hub's outage. Cards link out; the master serves its own pages.

The one exception is a pipe, not a page: in reverse-proxy mode (Settings ->
Network -> Software port -> Use proxy, core/services/webaccess.py) the box is
reached on port 80 with no port in the address, and the hub relays
/s/<slug>/... byte for byte to that system's master on 127.0.0.1
(core/services/webproxy.py). The master still renders, authenticates and
streams everything itself; the hub reads nothing it relays.

Run:
    python hub_server.py                 # the configured port (5100 by default)
    python hub_server.py --port 5100     # an explicit port wins over the setting
    uvicorn hub_server:create_app --factory --port 5100 --reload   # development
"""

import argparse
import logging
import pathlib

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from core import config
from core.middleware import install_access_log
from core.routes.audit import router as audit_router
from core.routes.auth import require as auth_require
from core.routes.auth import router as auth_router
from core.routes.chip_balance import router as chip_balance_router
from core.routes import pollcache as pollcache_route
from core.routes.hub import router as hub_router
from core.routes.hub_overlay import router as hub_overlay_router
from core.routes.template_media import router as template_media_router
from core.routes.monitor import router as monitor_router
from core.routes.hubport import router as hubport_router
from core.routes.https import router as https_router
from core.routes.sysport import router as sysport_router
from core.routes.hostname import router as hostname_router
from core.routes.boxrestart import router as boxrestart_router
from core.routes.manual_update import router as manual_update_router
from core.routes.about import router as about_router
from core.routes.capacity import router as capacity_router
from core.routes.mail import router as mail_router
from core.routes.telegram import router as telegram_router
from core.routes.mqtt_broker import router as mqtt_broker_router
from core.routes.network import router as network_router
from core.routes.network_wifitest import router as network_wifitest_router
from core.routes.panel import router as panel_router
from core.routes.logs import install_error_handlers
from core.routes.logs import router as logs_router
from core.routes.storage import router as storage_router
from core.routes.storage_setup import router as storage_setup_router
from core.services import log_collect
from core.services import logs as logs_service
from core.services import provision as provision_service
from core.services import workflow as workflow_service
from core.services.registry import Registry

logging.basicConfig(level=logging.INFO)
# Same ring the masters keep (core/services/logs.py). The hub had none, so a
# hub-side failure -- a system that would not spawn, a store install that threw
# -- was visible only to whoever was watching journalctl. Installed before any
# module logs so boot itself is on the page.
logs_service.install()
# ...and nothing this process does dies quietly. Without these hooks an
# unhandled exception went to the default excepthook -- a traceback on a
# terminal nobody is watching under systemd -- and a thread that raised out of
# its target (the retention sweep) died the same way, leaving the feature
# stopped with every page still drawing it as running.
log_collect.install_hooks("the hub")
logger = logging.getLogger(__name__)

_BASE = pathlib.Path(__file__).resolve().parent


def create_app(config_dir=None):
    app = FastAPI(title="IRIV EdgeAI Studio hub", docs_url=None, redoc_url=None)

    # No cleartext master password and no group-readable secret file survives a
    # start (services/secrets_at_rest.py).
    from core.services import secrets_at_rest
    secrets_at_rest.run(config_dir)

    systems_cfg = config.systems(config_dir)
    systems_dir = pathlib.Path(systems_cfg.get("dir", "systems"))
    if not systems_dir.is_absolute():
        systems_dir = _BASE / systems_dir

    app.state.config_dir = config_dir
    # The create-system endpoint (POST /api/hub/systems) writes packages here
    # and rebuilds the registry in place, so a new system's card appears
    # without a hub restart.
    app.state.systems_dir = systems_dir
    # One-time consolidation: move any hub-generated system still in the repo
    # systems dir (created before the store existed) into the persistent store,
    # so it survives future updates. Built-ins (demo) stay put.
    migrated = provision_service.migrate_generated_to_store(systems_dir)
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
    if migrated:
        logger.info("[HUB] migrated generated systems to the store: %s",
                    ", ".join(migrated))
    # Created systems carry a copy of the reference app; refresh that copy from
    # the (just-pulled) reference so they run current app code, not the code
    # from their creation day.
    refreshed = provision_service.repair_store_apps(systems_dir)
    if refreshed:
        logger.info("[HUB] refreshed app code for: %s", ", ".join(refreshed))
    # [python].enabled is a kill switch, so it has to kill something. Writing
    # is already refused while it is off, but code written while it was ON
    # kept running until someone happened to save that workflow -- so turning
    # the flag off and restarting is what an operator expects to be enough.
    for slug in provision_service.system_slugs(systems_dir, config_dir):
        try:
            workflow_service.enforce_python_policy(systems_dir, config_dir, slug)
        except Exception:                                   # noqa: BLE001
            logger.exception("[HUB] python policy sweep failed for %s", slug)
    # Generated systems live in a persistent store OUTSIDE the repo (survives a
    # git pull); the registry scans both it and the built-in systems dir, and a
    # store system is enabled by its presence (no dependency on the enabled list).
    app.state.registry = Registry(systems_dir, systems_cfg.get("enabled"),
                                  extra_dirs=[config.generated_systems_dir()],
                                  names=provision_service.system_names(config_dir))
    # A board that changed address (DHCP, new network) has master URLs naming a
    # host that is no longer it -- the hub would then refuse to start its own
    # systems. Repoint those at loopback before anything reads them.
    healed = provision_service.heal_master_hosts(config_dir)
    if healed:
        logger.warning("[HUB] repointed masters after an address change: %s",
                       ", ".join(healed))
    # Give every discovered system a master URL if its config entry was lost, so
    # a recovered system shows a working card and can be started.
    reassigned = provision_service.reconcile_masters(
        [s.slug for s in app.state.registry], config_dir, systems_dir)
    if reassigned:
        logger.warning("[HUB] assigned master URLs to recovered systems: %s",
                       ", ".join(reassigned))
    # {slug: {"url": ...}}. Re-read after reconcile so any URLs just assigned to
    # recovered systems are included. A system with no entry still gets a card --
    # it says "no master configured", a different problem from "master down".
    app.state.masters = config.systems(config_dir).get("masters", {})

    # Retention: enforce the admin's data-retention policy (config/storage.toml
    # [retention], hub Administration panel) on a slow background cadence.
    # Daemon thread, never the request path: sweeping is file I/O over every
    # system's snapshots/clips/archives and must not block a page. The sweep
    # only deletes files matching its allowlist (core/services/retention.py) --
    # auth, credentials, audit, configs, templates and workflows are
    # structurally out of reach.
    def _retention_loop():
        import time as _time
        from core.services import retention as retention_service
        _time.sleep(300)                # let boot settle; policy is not urgent
        while True:
            try:
                import os as _os
                pkg_dirs = [
                    _os.path.join(
                        provision_service._pkg_root(systems_dir, s.slug), s.slug)
                    for s in app.state.registry]
                report = retention_service.sweep(pkg_dirs, config_dir)
                deleted = report.get("deleted", 0)
                if deleted or report.get("errors"):
                    logger.info("[HUB] retention sweep: %d file(s) removed%s",
                                deleted,
                                f", errors: {report['errors']}"
                                if report.get("errors") else "")
            except Exception:                               # noqa: BLE001
                # exception(), not a print: a sweep that fails silently is how
                # a disk fills up unnoticed, and the traceback is the whole
                # diagnosis. Thread survives either way.
                logger.exception("[HUB] retention sweep failed")
            _time.sleep(retention_service.SWEEP_INTERVAL_S)

    import threading
    threading.Thread(target=_retention_loop, name="retention-sweep",
                     daemon=True).start()

    # The NVMe-not-mounted alarm (core/services/storage_watch.py): once a
    # minute, not only at boot, because a drive that drops off mid-life is
    # the case that fills the eMMC unnoticed. Two stats and a few small
    # TOML reads per pass; it writes only when the alarm turns on or off.
    def _storage_watch_loop():
        import os as _os
        import time as _time
        from core.services import storage_watch
        while True:
            try:
                storage_watch.check([
                    _os.path.join(
                        provision_service._pkg_root(systems_dir, s.slug), s.slug)
                    for s in app.state.registry], config_dir)
            except Exception:                               # noqa: BLE001
                logger.exception("[HUB] storage watch failed")
            _time.sleep(storage_watch.CHECK_EVERY_S)

    threading.Thread(target=_storage_watch_loop, name="storage-watch",
                     daemon=True).start()

    # Registered sign-in devices unused for 30 days are removed and their
    # sessions revoked (core/services/device_sweep.py): once at start, then
    # hourly. The hub only -- one sweeper per box, and masters share the
    # same sessions file. A pass that finds nothing stale writes nothing.
    def _device_sweep_loop():
        import time as _time
        from core.services import auth as _auth
        from core.services import device_sweep
        while True:
            try:
                n = device_sweep.run(config_dir)
                if n:
                    logger.info("[HUB] device sweep: %d inactive device(s) removed", n)
            except Exception:                               # noqa: BLE001
                logger.exception("[HUB] device sweep failed")
            _time.sleep(_auth.DEVICE_SWEEP_S)

    threading.Thread(target=_device_sweep_loop, name="device-sweep",
                     daemon=True).start()

    # "New storage detected" (core/services/storage_setup.py): a drive fitted
    # after install is noticed within seconds, not at the next boot. One
    # unprivileged lsblk per pass; it writes only when a drive is new. The
    # same pass keeps a MOUNTED drive's folders writable (storage_job.
    # keep_writable: an os.access per folder, the root helper only when one
    # fails, at most once a minute).
    def _storage_setup_loop():
        import time as _time
        from core.services import storage_job, storage_setup
        while True:
            try:
                storage_setup.poll(config_dir)
                storage_job.keep_writable(config_dir)
            except Exception:                               # noqa: BLE001
                logger.exception("[HUB] storage detection failed")
            _time.sleep(storage_setup.POLL_S)

    threading.Thread(target=_storage_setup_loop, name="storage-setup-poll",
                     daemon=True).start()

    # Autostart: flagged systems get their master spawned and their detection
    # app started at hub boot -- the board comes up detecting on power-on.
    # Same helper the Autostart toggle uses, so boot and toggle cannot drift.
    for slug, entry in app.state.masters.items():
        if not (entry or {}).get("autostart"):
            continue
        result = provision_service.autostart_system(systems_dir, slug, entry,
                                                    str(_BASE))
        # autostart_system never raises; it reports "failed: ..." in the parts
        # it could not do. Raise the level for those so a system that did not
        # come up on power-on is an ERROR on the page, not a line to scroll past.
        failed = "failed" in result["message"]
        logger.log(logging.ERROR if failed else logging.INFO,
                   "[HUB] autostart %s: %s", slug, result["message"])
    # Systems the Monitor account may open stay online: bring their masters up
    # now, and keep looking (services/monitor_online.py).
    from core.services import monitor_online
    got = monitor_online.ensure_online(app.state.masters, str(_BASE), config_dir)
    if got:
        logger.info("[HUB] Monitor systems brought online: %s", ", ".join(got))
    monitor_online.start_keepalive(app, str(_BASE), config_dir)

    # The front panel: OLED pages, the USER button, the buzzer. Off unless
    # config/panel.toml says otherwise, because the board's factory Node-RED
    # flow drives the same three pieces of hardware and two owners of one I2C
    # bus is two programs writing opposite pixels. Never fatal: a panel that
    # cannot open its screen logs it and the hub carries on.
    try:
        from core.services import panel as panel_service
        if panel_service.start(config_dir):
            state = panel_service.current()
            logger.info("[HUB] front panel on (screen %s)",
                        "ready" if state and state.display else
                        (state.error if state else "absent"))
    except Exception:                                       # noqa: BLE001
        logger.exception("[HUB] front panel failed to start")

    # Advertise this box as _irivai._tcp over mDNS, so a master on a box
    # cabled straight to this one (169.254, no DHCP) can find it where no /24
    # sweep looks (core/services/discovery_mdns.py). A hint, never trusted:
    # the master still checks /api/agent/info. Never fatal.
    try:
        from core.services import discovery_mdns
        discovery_mdns.start(config_dir)
    except Exception:                                       # noqa: BLE001
        logger.exception("[HUB] mDNS advertisement failed to start")

    # wlan0's job -- access point or Wi-Fi client -- is a setting applied
    # HERE, at boot (config/network.toml [ap].mode, "ap" out of the box).
    # The Network page switches it live (wifiswitch.py) and stores the mode
    # once the switch worked; this re-asserts it at every start.
    try:
        from core.services import apconf as _apconf
        from core.services import netconf as _netconf
        # The stored key, or the default (iriv1234) stored here when the box
        # has none: a box updated over the air never re-runs the installer
        # that writes it (netconf.ensure_ap_password). A stored key is kept.
        # Once per box: a key the 10-05..07 generator made goes back to the
        # default, with an Inbox notice (apdefault.py, Tok Aba 2026-10-07).
        from core.services import apdefault as _apdefault
        try:
            _migrated = _apdefault.migrate(config_dir)
        except Exception:                                   # noqa: BLE001
            _migrated = False
            logger.exception("[NET] the hotspot key migration failed")
        _had_key = bool(_netconf.ap_password(config_dir))
        _ap_key = _netconf.ensure_ap_password(config_dir)
        if not _had_key:
            logger.info("[NET] no hotspot key was stored - the default is set "
                        "(front panel Wi-Fi page, Settings -> Network)")
        # The SAVED name (Settings -> Network -> Save), else iriv-<serial>.
        # The mode is applied as SET, nothing more: a box set to Wi-Fi that
        # cannot join, or that loses its network later, does NOT raise its
        # hotspot by itself (Tok Aba, 2026-10-14: the start-up and Wi-Fi-loss
        # fallbacks were removed; the person turns the hotspot on). One
        # exception, which only preserves: a hotspot already broadcasting
        # (this hub restarted in the same boot, e.g. an update) is left up
        # even when the setting says Wi-Fi (netconf.apply_wifi_mode).
        # A Wi-Fi trial cut short (a power cut, the hub's time limit) leaves
        # a keyfile with the key in it until the next helper run: one sweep
        # now, when there is one (wifiscan.sweep_at_start; the helper's lock
        # orders it with the AP step below).
        from core.services import wifiscan as _wifiscan
        threading.Thread(target=_wifiscan.sweep_at_start, kwargs={"log": logger.info},
                         name="wifi-sweep", daemon=True).start()
        _netconf.apply_wifi_mode(_apconf.ap_ssid(config_dir), _ap_key,
                                 _netconf.wifi_mode(config_dir),
                                 log=logger.info,
                                 rekey=not _had_key or _migrated)
    except Exception:                                     # noqa: BLE001
        logger.exception("[HUB] wlan0 mode could not be applied")

    # A default route whose gateway answers nobody is a box with no internet
    # and a perfectly good second port (board .22: eth0's Modbus LAN gateway
    # 10.0.0.1 outranked eth1's router). Checked shortly after start and every
    # few minutes; only ever acts when another gateway is proven to answer
    # (routeheal.py says why it is that narrow).
    try:
        from core.services import routeheal as _routeheal
        _routeheal.start_watch(config_dir, log=logger.info)
    except Exception:                                       # noqa: BLE001
        logger.exception("[HUB] the default-route check could not start")

    app.mount("/static", StaticFiles(directory=str(_BASE / "core" / "static")),
              name="static")
    app.include_router(auth_router)
    # /api/system/chip-balance - the Hailo load balancer card's live table
    # (read-only; the switch saves through /api/system/settings).
    app.include_router(chip_balance_router)
    # /api/system/poll-cache - the shared poll cache card's counters
    # (read-only; the switch saves through /api/system/settings).
    app.include_router(pollcache_route.router)
    app.include_router(hub_router)
    app.include_router(hub_overlay_router)  # /api/hub/overlay - Live Wall boxes
    app.include_router(template_media_router)  # template preview pictures/video
    app.include_router(audit_router)  # /api/audit - the Administration panel's log
    app.include_router(monitor_router)  # /api/hub/monitor - the Monitor account
    app.include_router(storage_router)  # /api/hub/retention/nvme/forget - drop the NVMe memory
    app.include_router(storage_setup_router)  # /api/hub/storage/* - New storage detected
    app.include_router(logs_router)   # /api/logs     - this process's log ring
    # /api/capacity - systems + running apps, for the header meters. Mounted
    # here AND on every master so the meters do not vanish when an operator
    # opens a system (core/routes/capacity.py).
    app.include_router(capacity_router)
    # /api/mail - the box's sending mailboxes, defined once under Settings and
    # referenced by id from every system's Email nodes and Footage page.
    app.include_router(mail_router)
    # /api/telegram - the same idea for Telegram: bots declared once here and
    # named by a Workflow Telegram node, instead of a token per node on the
    # canvas (and in every exported template).
    app.include_router(telegram_router)
    # /api/mqtt/broker - the box's MQTT broker (Mosquitto): status, settings,
    # install. Mounted on every master too, for the Workflow page's pill.
    # The login's PATH goes into the environment the apps inherit
    # (services/mqtt_broker.py export_login), and the hub alone watches for
    # the broker stopping so the admins hear it once, not once per master.
    app.include_router(mqtt_broker_router)
    from core.services import mqtt_broker as _mqtt_broker
    _mqtt_broker.export_login(config_dir)
    # ...and where the box switches live, so an app can read
    # [system] chip_balance live (core/services/sysconfig.export_location).
    from core.services import sysconfig as _sysconfig
    _sysconfig.export_location(config_dir)
    _mqtt_broker.start_watch(config_dir)
    # A box with no broker gets one here, in the background, through the
    # root helper -- the hub's start is where a new version first runs.
    _mqtt_broker.start_auto_install(config_dir)
    # ...and a box missing its detection models (installed offline, updated
    # over the air, accelerator plugged in later) fetches them here, in the
    # background, once per version with a back-off (model_bootstrap).
    from core.services import model_bootstrap as _model_bootstrap
    _model_bootstrap.start_ensure(config_dir)
    # /api/hub/network - joining Wi-Fi, raising the access point, addressing an
    # interface. Hub only: the network belongs to the BOX, not to a system.
    app.include_router(network_router)
    app.include_router(network_wifitest_router)  # Test connection (wifitest.py)
    # /api/hub/panel - the OLED, the user button and the buzzer.
    app.include_router(panel_router)
    # /api/hub/port - the port this very process answers on, as a setting
    # (master or developer only; core/services/hubport.py).
    app.include_router(hubport_router)
    # /api/hub/ports/check, /api/hub/systems/{slug}/port - a SYSTEM's port
    # for the create wizard and Edit system (core/services/sysport.py).
    app.include_router(sysport_router)
    # /api/hub/hostname - the box's name on the LAN (<name>.local), saved
    # for the next restart and set then through a root helper (master or
    # developer only; core/services/hostname.py).
    app.include_router(hostname_router)
    # /api/hub/https - HTTPS with the box's own root certificate, served
    # by a second listener in this process (core/tlsserve.py; master or
    # developer only; off until switched on).
    app.include_router(https_router)
    # /api/hub/restart - "Restart now" for those two: a reboot, after which
    # main() applies them (core/services/boxpending.py).
    app.include_router(boxrestart_router)
    app.include_router(manual_update_router)  # /api/hub/update/* - the About page's software update
    app.include_router(about_router)  # /api/hub/about - who made this, for the About page
    # Last of the routers: an unhandled error anywhere above becomes one
    # recorded line and one {"ok": false, ...} body carrying the request id,
    # instead of a bare 500 (conventions sec 8).
    install_error_handlers(app)
    # Credential management lives with the platform admin, on the hub. The hub
    # itself pairs no devices, so these start empty; the page still offers
    # re-auth-gated token operations for anything a deployment registers.
    app.state.slave_tokens = {}
    app.state.slaves = []
    # Inside the session wall (added before it): an authenticated write
    # empties the shared poll cache before its response leaves.
    pollcache_route.install(app)
    # Session wall. slug=None: any valid account may see the hub; each master
    # enforces its own system on click-through.
    app.middleware("http")(auth_require(None))
    # Outermost (registered last): one line per request with its status code,
    # duration and id -- including the 401s the session wall above issues
    # (core/middleware.py).
    install_access_log(app)
    # Outside even that: /s/<slug>/... in reverse-proxy mode goes straight to
    # the system's master, which logs, authenticates and answers it itself
    # (core/services/webproxy.py). Every other path, and every path in port
    # mode, passes through untouched.
    from core.services import weblinks, webproxy
    app.add_middleware(webproxy.HubProxy, config_dir=config_dir)
    weblinks.start(config_dir)
    # Outermost of all: "Send browsers to HTTPS" (off unless set), and the
    # HTTPS listener itself, started by this app's lifespan when HTTPS is on.
    from core import tlsserve
    tlsserve.install(app, config_dir)

    @app.get("/api/health")
    def health():
        # Open (OPEN_PREFIXES). Lets run_all.sh / systemd confirm the hub is up
        # -- the masters have their own /api/health; the hub needs one too, or
        # the SPA catch-all answers 404 and the health poll thinks it's down.
        # `version` is the constant frozen at process start (core/version.py)
        # -- the OTA health gate uses it to prove the NEW process is serving,
        # not merely that new files are on disk.
        from core.version import VERSION
        reg = app.state.registry
        return {"ok": True, "role": "hub", "version": VERSION,
                "systems": len(reg),
                "masters_configured": len([s for s in reg
                                           if s.slug in app.state.masters])}

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request, exc):
        return JSONResponse({"ok": False, "error": exc.detail},
                            status_code=exc.status_code)

    # -- React SPA serving (same built bundle as the masters) -----------------
    # The hub renders the systems grid at '/' (mode "hub" in /api/bootstrap);
    # everything else the SPA needs it fetches from /api/hub.
    app_dist = _BASE / "core" / "static" / "app"
    if (app_dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(app_dist / "assets")),
                  name="assets")

    @app.get("/{full_path:path}", name="spa.catch_all")
    def spa_catch_all(request: Request, full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        # Reverse-proxy mode: a page opened on the old port goes to the
        # address with no port (core/services/weblinks.py).
        from core.services import hubport as _hubport, weblinks
        go = weblinks.direct_redirect(request, None, _hubport.running_port(),
                                      config_dir)
        if go:
            return RedirectResponse(go, status_code=307)
        index = app_dist / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503,
                                detail="frontend not built - run the frontend build (install.sh)")
        # <base href="/">: the bundle's paths are relative, so a deep link
        # (/settings/network) must say where they start.
        return HTMLResponse(weblinks.index_html(str(index)),
                            headers={"Cache-Control": "no-store"})

    configured = [s.slug for s in app.state.registry if s.slug in app.state.masters]
    logger.info("[HUB] %d system(s) discovered, %d with a master configured: %s",
                len(app.state.registry), len(configured), ", ".join(configured) or "none")
    return app


def main():
    p = argparse.ArgumentParser(description="IRIV EdgeAI Studio hub.")
    p.add_argument("--host", default="0.0.0.0")
    # No default here on purpose. The port is a SETTING (config/systems
    # .local.toml [hub].port, the Network page's Software port card), read by
    # core/services/hubport.py; an explicit --port still wins, for a developer
    # running two checkouts or a unit written before the setting existed.
    # resolve_cli also records the bound port and its source in the
    # environment, for anything that has to reach this hub without being it:
    # the OTA updater polls /api/health to decide whether a new version came
    # up, and a box on a non-default port would otherwise be checked on 5100,
    # find nothing, and roll back every update it was ever offered.
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--config-dir", default=None)
    p.add_argument("--about", action="store_true",
                   help="print the product, version and developer, then exit")
    args = p.parse_args()
    if args.about:
        from core import about
        print(about.text())
        raise SystemExit(0)

    # Hailo: stops here with a plain message if one is absent.
    from core.services import hwcheck
    hwcheck.require(args.config_dir, "hub")

    from core.services import boxpending, hubport
    # A port or hostname saved on the Network page waits for the box to
    # restart; this is that moment (first hub start of a new boot), before
    # the port is resolved and before anything advertises the name. A
    # refused change keeps the old value and tells the admins why.
    try:
        applied = boxpending.apply_at_start(args.config_dir)
    except Exception:                                       # noqa: BLE001
        applied = None
        logger.exception("[HUB] could not apply the saved port / hostname")
    for sentence in boxpending.failures(applied):
        logger.warning("[HUB] %s", sentence)
        try:
            from core.services import inbox as _inbox
            _inbox.notify_admins("box_restart_kept", "Change not applied",
                                 sentence, args.config_dir, page="settings",
                                 tab="network")
        except Exception:                                   # noqa: BLE001
            logger.exception("[HUB] could not post the restart notice")
    port = hubport.resolve_cli(args.port, args.config_dir)
    port, moved = hubport.fallback_if_taken(port, args.config_dir)
    if moved:
        logger.warning("[HUB] %s", moved)
        try:
            from core.services import inbox as _inbox
            _inbox.notify_admins("hub_port_fallback", "Software port taken",
                                 moved, args.config_dir, page="settings",
                                 tab="network")
        except Exception:                                   # noqa: BLE001
            logger.exception("[HUB] could not post the port notice")

    # Port 80 for reverse-proxy mode, pointed at the port just resolved (a
    # fallback port included), or removed in port mode. A redirect that
    # cannot be made puts the box back on addresses with a port and says so.
    from core.services import webaccess
    try:
        access_note = webaccess.ensure_redirect(port, args.config_dir)
    except Exception:                                       # noqa: BLE001
        access_note = ""
        logger.exception("[HUB] could not set the port 80 redirect")
    if access_note:
        logger.warning("[HUB] %s", access_note)
        try:
            from core.services import inbox as _inbox
            _inbox.notify_admins("web_access_fallback", "Port 80 not available",
                                 access_note, args.config_dir, page="settings",
                                 tab="network")
        except Exception:                                   # noqa: BLE001
            logger.exception("[HUB] could not post the port 80 notice")

    # access_log=False: uvicorn's access line carries the query string into
    # the journal; core/middleware.py's own line (path only) is the log.
    app = create_app(args.config_dir)
    app.state.https.host = args.host     # HTTPS on the same interfaces as HTTP
    # timeout_graceful_shutdown: on SIGTERM uvicorn waits for every open
    # request to finish, and the hub's live streams (overlay events, the video
    # relay) never do, so a stop or a reboot with a dashboard open sat out the
    # unit's whole 90 s TimeoutStopSec. Five seconds, as the HTTPS listener
    # already uses (core/tlsserve.py).
    uvicorn.run(app, host=args.host, port=port, access_log=False,
                timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
