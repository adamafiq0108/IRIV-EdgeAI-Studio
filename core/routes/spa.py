# -*- coding: utf-8 -*-
"""
spa.py -- read-only JSON the React SPA needs to render pages.

The frontend is a single-page app (frontend/, built to core/static/app). It
fetches shell context once from /api/bootstrap, then per-page data from the
GETs here. Every endpoint is thin: read app.state -> call the same services the
old server-rendered pages used -> return JSON. Mutations still live in their own
routers (control/credentials/folders/settings/email/devices); this module adds
no write path and never branches on slug.

Session-gated by the master middleware (not in OPEN_PREFIXES), so an anonymous
call gets 401 -- which is exactly how the SPA learns to show its login view.
"""

import dataclasses

from fastapi import APIRouter, Request

from core import config
from core.services import analytics as analytics_service
from core.services import auth as auth_service
from core.services import detector_control as detector_control_service
from core.services import health as health_service
from core.services import hubport
from core.services import inventory as inventory_service
from core.services import main as main_service
from core.services import nav as nav_service
from core.services import overview as overview_service
from core.services import settings as settings_service
from core.version import VERSION

router = APIRouter()


def _caps(state):
    caps = set()
    for s in state.slaves:
        caps.update(s.get("capabilities") or ())
    # The LOCAL detection app counts too: a standalone board has no paired
    # agent to declare capabilities, and without these every capability-gated
    # widget (zone tiles) and page stays hidden while detection runs fine.
    if getattr(state, "local_ingest", None) is not None:
        try:
            caps.update(detector_control_service.capabilities(
                state.systems_dir, state.system.slug))
        except Exception:               # noqa: BLE001
            pass
    return caps


def _thr(request):
    return health_service.thresholds(config.monitoring(request.app.state.config_dir))


def _is_admin(request):
    return auth_service.is_admin(getattr(request.state, "user", None),
                                     request.app.state.config_dir)


@router.get("/api/bootstrap", name="spa.bootstrap")
def bootstrap(request: Request):
    """Everything the shell needs: brand, user, system, capability-driven nav,
    the sidebar tree, and the hub back-link. One call per app load."""
    st = request.app.state
    cfg = st.config_dir
    system = getattr(st, "system", None)
    caps = _caps(st)
    username = getattr(request.state, "user", None) or ""
    is_admin = auth_service.is_admin(username, cfg)

    role_label = auth_service.role_label(username, cfg) if username else ""

    sidebar = None
    if username and hasattr(st, "slaves") and hasattr(st, "folders"):
        try:
            sidebar = inventory_service.build_sidebar(st.folders or [], st.slaves or [])
        except Exception:
            sidebar = None

    # Deployment role of THIS system (systems.local.toml masters.<slug>.role).
    # Absent = standalone: a single local board, not part of a master/slave
    # group. The frontend picks its shell from this -- standalone gets the
    # local-board dashboard, master/slave get the fleet shell.
    deploy_role = "standalone"
    if system:
        entry = config.systems(cfg).get("masters", {}).get(system.slug, {})
        deploy_role = entry.get("role") or "standalone"
        # A rename is made on the hub but stored in the config this master
        # already re-reads, so adopt it here rather than making the operator
        # restart the system to see its own new name. Written back to app
        # state so every later reader (nav, the email digest) agrees.
        renamed = str(entry.get("name") or "").strip()[:60]
        if renamed and renamed != system.display_name:
            system = dataclasses.replace(system, display_name=renamed)
            st.system = system

    # The "back to Systems" target the shell's logo links to. The port comes
    # from the Software port setting ([hub].port, 5100 out of the box --
    # core/services/hubport.py); the HOST always comes from the request, so
    # the logo follows whatever address the operator reached this master by
    # -- browse a master at http://<pi-ip>:5110 and the logo points at
    # http://<pi-ip>:5100, never a stale IP or a localhost the laptop cannot
    # reach.
    hub_url = hubport.browser_url(request, cfg)

    # This viewer's category grant for THIS system -- None is "everything"
    # (admin, unrestricted account). The nav is filtered HERE, on
    # the server, so a hidden category is missing from the payload rather
    # than merely undrawn; the frontend also gets the grant so its router can
    # bounce a typed URL back onto a page the account may see.
    grants = (auth_service.section_grants(username, system.slug, cfg)
              if (system and username) else None)
    # ...and master's PAGE grant, which is a separate decision by a separate
    # person: an admin hands out categories, master decides which pages exist
    # to be handed out. Both apply, so neither can widen past the other.
    allowed_pages = (auth_service.page_grants(username, system.slug, cfg)
                     if (system and username) else None)

    return {
        "ok": True,
        "mode": "master",
        "deploy_role": deploy_role,
        "brand": config.brand(cfg),
        "user": {"name": username or "guest",
                 "role_label": role_label if username else "",
                 "is_admin": is_admin,
                 "tier": auth_service.tier(username, cfg),
                 "is_site_admin": auth_service.is_site_admin(username, cfg),
                 "settings_tabs": auth_service.settings_tabs(username, cfg)},
        "system": ({"slug": system.slug, "display_name": system.display_name,
                    "subtitle": getattr(system, "subtitle", ""),
                    "icon": getattr(system, "icon", "")} if system else None),
        "nav": nav_service.build(system, caps, is_admin=is_admin,
                                 deploy_role=deploy_role,
                                 sections=grants,
                                 pages=allowed_pages) if system else [],
        # "*" = every category. Otherwise the allowed SECTION_KEYS.
        "sections": "*" if grants is None else sorted(grants),
        # "*" = every page. The router uses it to bounce a typed URL the same
        # way it does a hidden category.
        "pages": "*" if allowed_pages is None else sorted(allowed_pages),
        "capabilities": sorted(caps),
        "hub_url": hub_url,
        "sidebar": sidebar,
        # Same flag the hub's bootstrap carries: the master is still on the
        # shipped password, so the shell routes to /account-setup rather
        # than drawing pages whose calls all answer 409 (UAT-103).
        "needs_account_setup": auth_service.setup_pending(username, cfg),
        # The running version, same as the hub's bootstrap.
        "version": VERSION,
    }


@router.get("/api/main", name="spa.main")
def main(request: Request):
    st = request.app.state
    # local stats read live (not the sync-cycle cache) so tiles match the feed.
    stats = main_service.aggregate_stats(st.slaves, main_service.live_local_stats(st))
    return {"ok": True,
            "tiles": main_service.build_tiles(st.slaves),
            "devices": main_service.build_devices(st.slaves, _thr(request)),
            "stats": stats,
            "overview": main_service.build_overview(stats),
            "alerts": main_service.build_alerts(st.events),
            "capabilities": sorted(_caps(st))}


@router.get("/api/platform", name="spa.platform")
def platform(request: Request):
    st = request.app.state
    return {"ok": True,
            "stats": overview_service.platform_stats(st.slaves, st.events, _thr(request))}


@router.get("/api/cameras", name="spa.cameras")
def cameras(request: Request):
    return {"ok": True,
            "cameras": overview_service.cameras(request.app.state.slaves)}


@router.get("/api/stream", name="spa.stream")
def stream(request: Request):
    devices = [s for s in request.app.state.slaves
               if "stream" in (s.get("capabilities") or ())]
    return {"ok": True, "devices": devices}


@router.get("/api/footage", name="spa.footage")
def footage(request: Request):
    devices = [s for s in request.app.state.slaves
               if "footage" in (s.get("capabilities") or ())]
    return {"ok": True, "devices": devices}


@router.get("/api/settings", name="spa.settings")
def settings(request: Request):
    st = request.app.state
    all_systems = sorted(config.systems(st.config_dir).get("masters", {}).keys())
    return {"ok": True,
            "devices": settings_service.build_devices(st.slave_configs, st.slaves),
            "master": settings_service.build_master(),
            "all_systems": all_systems,
            "is_admin": _is_admin(request)}


@router.get("/api/analytics", name="spa.analytics")
def analytics(request: Request, range: str = "month", start: str = "", end: str = "",
              span_start: str = "", span_end: str = ""):
    st = request.app.state
    if range not in analytics_service.RANGES and range != analytics_service.CUSTOM:
        range = "month"
    widgets = st.registry.widgets(st.system.slug, _caps(st))
    stats = analytics_service.scope_stats(
        main_service.aggregate_stats(st.slaves, main_service.live_local_stats(st)),
        range, start, end)
    events = analytics_service.scope_events(st.events, range, start, end)
    pages = analytics_service.compute_pages(
        widgets, events, stats, st.config_dir, st.system.slug, _caps(st),
        # So a dataset table drops a column the operator has since deleted:
        # the widget's stamped column list is a snapshot, the package's table
        # definition is the truth (builder._dataset_data).
        systems_dir=st.systems_dir,
        # The picked period, for charts whose range is "follow the page". The
        # SPA always sends custom + explicit dates (the Today/Week/Month
        # buttons are presets in its date picker), so the dates ride along.
        page_range=range, page_start=start, page_end=end)
    from core.routes import builder as builder_routes
    return {"ok": True,
            "pages": pages,
            # Back-compat: the first page's widgets, for any caller not yet
            # page-aware.
            "widgets": pages[0]["widgets"] if pages else [],
            # Ready-made widgets the workflow exposes to Analytics - the
            # palette when the page is put into edit mode.
            "catalog": builder_routes._catalog(request, "analytics"),
            # Package widgets the operator has removed - listed so they can be
            # put back; a removal nobody can undo is one nobody risks making.
            "hidden": analytics_service.hidden_catalogue(
                widgets, st.config_dir, st.system.slug),
            "editable": auth_service.is_admin(getattr(request.state, "user", None),
                                     request.app.state.config_dir),
            "range": range,
            "start": start, "end": end,
            # Which days of the SPAN hold events -- the day strip's dots. The
            # span is wider than the scoped day, so it is passed separately:
            # scoping to one day must not empty the strip that chose it.
            "day_counts": analytics_service.day_counts(
                st.events, span_start or start, span_end or end),
            "ranges": list(analytics_service.RANGES) + [analytics_service.CUSTOM]}
