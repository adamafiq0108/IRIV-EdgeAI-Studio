# -*- coding: utf-8 -*-
"""
hub.py -- The system hub: pick a system to enter.

Served by hub_server.py, which runs in front of the per-system masters. Cards
come from the registry, so a new system package appears here on restart with no
edit to this file. Route parses and returns; probing and card shaping are the
services' job (docs/conventions.md sec 2).

What a DEPLOYMENT does with systems lives here: list them, create, import,
export, rename, delete, start, stop. The create wizard needs the format list,
so GET /api/hub/formats and GET /api/hub/templates are here and admin-readable.
"""

import asyncio
import json
import logging
import os
import pathlib
import tempfile
import time
import urllib.parse

from fastapi import (APIRouter, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import FileResponse, JSONResponse

from core import config, net
from core.routes import perm
from core.services import auth as auth_service
from core.services import hub as hub_service
from core.services import masters as masters_service
from core.services import monitor_account
from core.services import power_state
from core.services import provision as provision_service
from core.services import template_preview
from core.services import templates as templates_service
from core.services.registry import Registry
from core.version import VERSION

router = APIRouter()

#: The repo root (…/core/routes/hub.py -> two levels up): where
#: master_server.py lives, for spawning a system's master.
_REPO_DIR = str(pathlib.Path(__file__).resolve().parents[2])


@router.get("/api/bootstrap", name="hub.bootstrap")
def bootstrap(request: Request):
    """Shell context for the hub SPA. The hub serves no system, so system/nav/
    sidebar are empty and mode is 'hub' -- the React app shows the systems grid
    at '/' instead of a master's Main page."""
    cfg = request.app.state.config_dir
    username = getattr(request.state, "user", None) or ""
    is_admin = auth_service.is_admin(username, cfg)
    role_label = auth_service.role_label(username, cfg) if username else ""
    return {
        "ok": True,
        "mode": "hub",
        "brand": config.brand(cfg),
        "user": {"name": username or "guest",
                 "role_label": role_label if username else "",
                 "is_admin": is_admin,
                 # The site-admin tier                 # ...and the same for the site-admin tier: which Settings
                 # tabs to draw, decided once on the server so the page and
                 # the endpoints behind it cannot disagree.
                 "tier": auth_service.tier(username, cfg),
                 "is_site_admin": auth_service.is_site_admin(username, cfg),
                 "settings_tabs": auth_service.settings_tabs(username, cfg)},
        # The master is still on the shipped password: every other API call
        # answers 409 until setup is done (routes/auth.py SETUP_OPEN), so the
        # shell goes straight to /account-setup instead of drawing a grid
        # that cannot load (UAT-103).
        "needs_account_setup": auth_service.setup_pending(username, cfg),
        # What this box runs, from the one call the setup gate always answers:
        # the System tab would otherwise print a bare "v".
        "version": VERSION,
        "system": None,
        "nav": [],
        "capabilities": [],
        "hub_url": "",
        "sidebar": None,
    }


def _rebuild_registry(st):
    """Re-read the systems config into app state after a change.

    One helper because the name override has to travel with every rebuild: a
    site that forgot it would silently drop every rename until the next
    restart.
    """
    cfg = config.systems(st.config_dir)
    st.registry = Registry(st.systems_dir, cfg.get("enabled"),
                           extra_dirs=[config.generated_systems_dir()],
                           names=provision_service.system_names(st.config_dir))
    st.masters = cfg.get("masters", {})
    return cfg


def _require_admin(request, action):
    """MASTER -- the box owner. See auth.is_admin for why the
    name did not change when the site-admin tier arrived below it."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                  request.app.state.config_dir):
        raise HTTPException(status_code=403, detail=f"{action} is an admin action")


def _require_site_admin(request, action):
    """Master OR a site admin. For the settings that belong to
    running the OPERATION rather than to owning the hardware: retention, how
    long footage is kept, how the box sends mail."""
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      request.app.state.config_dir):
        raise HTTPException(status_code=403, detail=f"{action} is an admin action")


def _require_operator(request, slug, action):
    """Gate a per-system OPERATION on being assigned that system.

    Running a system is not administering the platform. An account granted a
    system is the person who stands in front of it: they are expected to start
    it in the morning, stop it to clear a jam, and restart it when a camera
    drops -- and every one of those was admin-only, so the answer to "the line
    is down" was to find whoever holds the admin password.

    The grant is the authority, and it is the same one that decides whether
    the system is visible at all (auth.allowed): if an operator can see the
    row, they can work it. What stays admin-only is everything that changes
    what the deployment IS rather than what it is doing -- creating, renaming,
    importing, exporting, deleting data, and the platform-wide settings.
    """
    user = getattr(request.state, "user", None)
    cfg = request.app.state.config_dir
    if auth_service.is_admin(user, cfg):
        return
    if not auth_service.allowed(user, slug, cfg):
        raise HTTPException(status_code=403,
                            detail=f"{action} needs access to this system")


@router.get("/api/hub/templates", name="hub.templates")
def templates(request: Request):
    """What the create panel may start a system from.

    Two kinds, deliberately in one list: the built-in detection formats (a
    flavour -- classes and zone guidance) and the operator's own saved systems
    (a whole build -- workflow, dashboards, class filter). The picker shows
    them grouped by category; `builtin` says which is which."""
    return _template_listing(request)


def _template_listing(request):
    formats = provision_service.builtin_formats(request.app.state.config_dir)
    builtin = [{"key": key, "label": f["label"], "hint": f["hint"],
                "category": f.get("category") or "built-in", "builtin": True,
                "shipped": f["shipped"], "edited": f["edited"],
                "draft": f.get("draft", False),
                "zone": f["zone"], "classes": f["classes"],
                "widgets": len(f["widgets"]),
                # The Hub home's template cards and their preview (Tok Aba,
                # 2026-10-22): pictures by file name, and what the template
                # brings, read from the build it carries.
                "images": template_preview.images(key),
                "media": template_preview.media(key),
                "preview": template_preview.summary(f),
                **template_preview.copy_of(key, request.app.state.config_dir)}
               for key, f in formats.items()
               if not f.get("draft")]
    return {"ok": True,
            "default": provision_service.default_template(request.app.state.config_dir),
            "format_categories": provision_service.format_categories(
                request.app.state.config_dir),
            "templates": builtin,
            "categories": [],
            "saved": []}


@router.get("/api/hub/formats", name="hub.formats")
def list_formats(request: Request):
    """The built-in formats in full. Readable by any admin (the create
    wizard needs the list); there is no write route."""
    return {"ok": True,
            "editable": False,
            "zone_modes": list(provision_service.ZONE_MODES),
            "default": provision_service.default_template(request.app.state.config_dir),
            "categories": provision_service.format_categories(
                request.app.state.config_dir),
            "formats": provision_service.builtin_formats(
                request.app.state.config_dir)}


@router.post("/api/hub/systems", name="hub.create_system")
async def create_system(request: Request):
    """Create an AI-vision system from a name and a detection-format template
    (the hub's "New AI Vision" panel): write systems/<slug>/ with that
    template's layout, enable it, assign the next master port, start that
    master, and refresh the in-memory registry so the card shows on the
    Systems grid without a restart."""
    # MASTER, never a site admin. Which systems exist is a
    # decision about the box -- its cameras, its share of the accelerator, its
    # disk -- and the admin tier runs an operation ON the box rather than
    # deciding what the box is. Master hands them systems; how many is capped
    # on the account (auth.MAX_SYSTEMS_FIELD).
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                  request.app.state.config_dir):
        raise HTTPException(
            status_code=403,
            detail="only the box owner can create a system - ask them to add "
                   "one and grant it to your account")
    try:
        body = await request.json()
    except Exception:
        body = {}

    st = request.app.state
    creator = getattr(request.state, "user", None) or ""
    warnings = []
    # No "pick a saved template" gate any more: a built-in format now IS a
    # whole build (the Built-in templates folder publishes the workbench's
    # snapshot -- workflow, dashboard, classes), so picking one is picking a
    # build. Starting from nothing is a deliberate choice with a name: the
    # Blank Canvas format, which cannot be deleted for exactly this reason.
    try:
        created = provision_service.create_vision_system(
            st.systems_dir, body.get("name"), st.config_dir,
            template=body.get("template"),
            slug=body.get("slug") or None,
            subtitle=body.get("subtitle") or None,
            icon=body.get("icon") or None,
            port=body.get("port") or None,
            role=body.get("role") or None,
            master_slug=body.get("master") or None,
            owner=creator)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # The creator must be able to open what they just made (no-op for admin).
    auth_service.grant_system(creator, created["slug"], st.config_dir)

    # A NEW system captures nothing until asked: the snapshot and record
    # switches start OFF. A template that carries its own capture settings
    # overrides this below -- the author's choice travels with the build.
    from core.services import detector_control as dc_service
    # Created systems live under the STORE root, not the repo's systems/ --
    # every direct dc call here has to resolve the root the same way the
    # template applier does.
    created_root = templates_service._root_for(st.systems_dir, created["slug"],
                                               st.config_dir)
    try:
        dc_service.set_capture(created_root, created["slug"],
                               snap=False, record=False)
    except Exception:                                       # noqa: BLE001
        pass
    # The template's model may still be downloading -- say so, rather than
    # letting the operator open the system and wonder why it detects nothing.
    if created.get("model_note"):
        warnings.append(created["model_note"])
    if body.get("template"):
        fmt = provision_service.builtin_formats(st.config_dir).get(
            str(body["template"]))
        warnings += templates_service.apply_format_workflow(
            st.systems_dir, st.config_dir, created["slug"], fmt,
            key=str(body["template"]))

    # Re-read what create_app computed at boot, now including the new system.
    _rebuild_registry(st)

    # The wizard's advanced path may defer the start (an operator installing
    # proper units via run_all.sh); the card then honestly reads "master
    # unreachable" until they do.
    started = body.get("start", True) is not False
    if started:
        provision_service.spawn_master(
            str(pathlib.Path(st.systems_dir).resolve().parent),
            created["slug"], created["port"])
        # AUTO-RUN. A system whose template arrived with a camera configured
        # starts detecting at once -- created should mean running, not "now
        # find the Start button". Best effort: no camera yet (a Blank Canvas)
        # simply skips, and a failed start is a note, not a failed create.
        try:
            cams = dc_service.cameras(created_root, created["slug"])
            has_cam = bool(dc_service._enabled_sources(cams or []))
        except Exception:                                   # noqa: BLE001
            has_cam = False
        if has_cam:
            try:
                res = dc_service.start(created_root, created["slug"])
                if not res.get("ok"):
                    warnings.append("detection did not start: "
                                    + str(res.get("message") or ""))
            except (ValueError, OSError) as exc:
                warnings.append(f"detection did not start: {exc}")
    # The address the BROWSER can open (UAT-104): created["url"] is the
    # config's loopback master url, and the wizard toasted "Starting at
    # http://127.0.0.1:5102" to a person on a laptop. Same rewrite the card
    # on the grid gets (core/net.py), onto the host this request came in by.
    return {"ok": True, "started": started, "warnings": warnings,
            **created, "url": net.rewrite(created.get("url") or "", request)}


@router.get("/api/hub/systems/{slug}/export", name="hub.export_system")
def export_system(slug: str, request: Request):
    """Download a whole system as one file: cameras, zones, workflow,
    dashboards, class filter. Ports and credentials are left out -- the first
    belongs to the box, the second must not travel in a shareable file."""
    _require_admin(request, "exporting a system")
    st = request.app.state
    if slug not in [s.slug for s in st.registry]:
        raise HTTPException(status_code=404, detail="no such system")
    try:
        doc = templates_service.export_system(st.systems_dir, st.config_dir, slug)
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(
        doc, headers={"Content-Disposition": f'attachment; filename="system-{slug}.json"'})


#: The biggest database an import will take. An export of a busy year is a
#: few hundred megabytes; past this it is not one of ours, and finding that
#: out costs the disk it was written to first.
MAX_DB_UPLOAD = 2 * 1024 * 1024 * 1024


async def _spool_upload(upload, suffix=".db"):
    """An upload on disk, as a path. Returns the path.

    Written in chunks rather than read whole: this is the one upload on the
    product measured in gigabytes, and holding it in memory to write it out
    again is how an import takes the box down instead of just taking a while.
    """
    tmp = tempfile.NamedTemporaryFile(prefix="iriv-import-", suffix=suffix,
                                      delete=False)
    size = 0
    try:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DB_UPLOAD:
                raise HTTPException(
                    status_code=400,
                    detail="that database file is too large to import")
            tmp.write(chunk)
    except BaseException:
        tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise
    tmp.close()
    return tmp.name


@router.post("/api/hub/systems/import", name="hub.import_system")
async def import_system(request: Request, file: UploadFile = File(...),
                        name: str = Form(""),
                        database: UploadFile = File(None)):
    """Create a system from an exported file -- the "we bought another box"
    path. The app CODE is scaffolded fresh; only documents come from the file,
    and each one goes through the validator that owns it.

    `database` is optional: the SQLite export from that system's Database page.
    Two files rather than one because they are two different things -- the JSON
    is the design and is small, the database is the recorded history and can be
    gigabytes -- so a design is never made unmailable by data nobody asked to
    move. When it is supplied the new system arrives with its rows already in
    place, which is the difference between "the same system" and "the same
    system, empty"."""
    _require_admin(request, "importing a system")
    st = request.app.state
    raw = await file.read(templates_service.MAX_EXPORT_BYTES + 1)
    if len(raw) > templates_service.MAX_EXPORT_BYTES:
        raise HTTPException(status_code=400, detail="that file is too large to be a system export")
    creator = getattr(request.state, "user", None) or ""
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="that file is not valid JSON")
    # Spooled to disk, never held in memory: sqlite3 reads a path, and this
    # file is the one upload on the product that can be gigabytes.
    db_path = None
    if database is not None and getattr(database, "filename", ""):
        db_path = await _spool_upload(database)
    try:
        created, warnings = templates_service.import_system(
            st.systems_dir, st.config_dir, doc, name=name or None, owner=creator,
            db_path=db_path)
    except (ValueError, TypeError, OSError) as e:
        detail = str(e)
        if "already exists" in detail:
            # The importer picks a free slug itself, so reaching here means a
            # folder is in the way that no system claims. Say that, rather than
            # repeating the packaging layer's "publish with overwrite" advice,
            # which names an action this page does not offer.
            detail = (f"{detail} - a leftover folder of that name is on the "
                      "box. Give the import a different name, or remove that "
                      "folder first.")
        raise HTTPException(status_code=400, detail=detail)

    finally:
        if db_path:
            try:
                os.unlink(db_path)
            except OSError:
                pass

    auth_service.grant_system(creator, created["slug"], st.config_dir)
    _rebuild_registry(st)
    provision_service.spawn_master(
        str(pathlib.Path(st.systems_dir).resolve().parent),
        created["slug"], created["port"])
    # Browser-facing, like the create above (UAT-104).
    return {"ok": True, "warnings": warnings,
            **created, "url": net.rewrite(created.get("url") or "", request)}


@router.patch("/api/hub/systems/{slug}", name="hub.rename_system")
async def rename_system(slug: str, request: Request):
    """Edit a system (was Rename). Body: {name, port}.

    The name only -- the slug is identity (config keys, stat keys, stored
    layouts, workflow files, the agent's system_slug, every bookmarked URL) and
    renaming it to match a label would break all of them. An empty name puts
    back whatever the package declares. `port` moves the master, FIRST, so a
    refused or undone move saves nothing (routes/sysport.py); none sent, or
    the same port, and this is the rename exactly as before."""
    _require_admin(request, "renaming a system")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    st = request.app.state
    if slug not in [s.slug for s in st.registry]:
        raise HTTPException(status_code=404, detail="no such system")
    from core.routes import sysport                         # noqa: PLC0415
    moved = await sysport.move_for_edit(request, slug, body)
    name = None
    if "name" in body or "port" not in body:     # a port-only edit keeps the name
        try:
            name = provision_service.rename_system(slug, body.get("name"), st.config_dir)
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    _rebuild_registry(st)
    system = st.registry.get(slug)
    shown = name or (system.display_name if system else slug)
    return {"ok": True, "slug": slug, "name": shown,
            **sysport.moved_fields(request, slug, moved)}


@router.delete("/api/hub/systems/{slug}", name="hub.remove_system")
def remove_system(slug: str, request: Request):
    """Remove a system from the hub: stop its local master, disable it in
    systems.local.toml, and delete its folder only if it was generated by this
    tool -- a hand-written package (the demo) keeps its files and can be
    re-enabled in config. Admin only."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                  request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="removing a system is an admin action")
    st = request.app.state
    all_slugs = [s.slug for s in st.registry]
    if slug not in all_slugs:
        raise HTTPException(status_code=404, detail="no such system")

    result = provision_service.remove_system(
        st.systems_dir, slug, all_slugs, st.config_dir)

    _rebuild_registry(st)
    return {"ok": True, **result}


@router.get("/api/hub/orphans", name="hub.orphans")
def list_orphans(request: Request):
    """Files on this box that no system claims.

    Deleting a system used to leave its dashboard layout, its workflow store
    and its database rows behind. Nothing lists a document whose system does
    not exist, so they were not leftovers an operator could find and tidy --
    they were invisible, and nothing else would ever remove them. A box in use
    for months has a drawer full: one had thirty layouts for fourteen systems.

    The delete path no longer leaks. This is for what it already leaked.
    """
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="reviewing leftover files is an admin action")
    st = request.app.state
    return {"ok": True,
            "orphans": provision_service.orphan_documents(st.systems_dir,
                                                          st.config_dir)}


@router.delete("/api/hub/orphans/{slug}", name="hub.remove_orphan")
def remove_orphan(slug: str, request: Request):
    """Delete one orphan's files. Refuses a slug a live system claims.

    That refusal is the safety of the whole feature: everything here works
    from a slug the browser sends back, and without it a mistyped one would
    remove a running system's dashboard behind its back.
    """
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403,
                            detail="removing leftover files is an admin action")
    st = request.app.state
    try:
        return {"ok": True, **provision_service.remove_orphan(
            st.systems_dir, slug, st.config_dir)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/hub/streams", name="hub.streams")
def hub_streams(request: Request):
    """Live-wall data: every system's stream-capable devices, fetched from
    each master AS the calling operator (their cookie is forwarded - one
    signed session works across every process on this box). The browser then
    pulls each MJPEG feed straight from that master's /video_feed/<host>; the
    hub never proxies video. Scoped like the cards: only systems the account
    may open."""
    from core.services import detector_control, overlay_view
    st = request.app.state
    username = getattr(request.state, "user", None) or ""
    devices = masters_service.stream_devices_all(
        st.masters, request.headers.get("cookie"))
    systems = []
    for system in st.registry:
        # THE shared scoping rule (grant + workshop/deployment split) -- the
        # bare grant check alone showed an admin the developer's workbench
        # systems here while the grid next door hid them.
        if not hub_service.visible_to(username, system.slug, st.masters,
                                      st.config_dir):
            continue
        url = (st.masters.get(system.slug) or {}).get("url")
        if not url:
            continue
        devs = devices.get(system.slug)
        # The system's LOCAL cameras, so the wall can show one tile per camera
        # off the master's /api/detector/preview?camera=<id>. Read from the
        # package on THIS box -- the hub may not have it (a master elsewhere
        # owns the system), and an empty list is the right answer then: the
        # wall falls back to the single unqualified feed.
        try:
            cams = detector_control.cameras(
                provision_service._pkg_root(st.systems_dir, system.slug), system.slug)
        except Exception:                                   # noqa: BLE001
            cams = []
        systems.append({
            "slug": system.slug,
            "name": system.display_name,
            # The browser fetches each MJPEG feed from this url directly, so it
            # must be an address the BROWSER can reach -- the request's host,
            # not whatever host the board recorded in config.
            "url": net.rewrite(url.rstrip("/"), request),
            "reachable": devs is not None,
            "cameras": [{
                "id": c.get("id"),
                "name": c.get("name") or _camera_label(c),
                "label": _camera_label(c),
            } for c in cams],
            "devices": [{
                "host_serial": d.get("host_serial"),
                "label": d.get("label") or d.get("display_name") or d.get("host_serial"),
                "online": bool(d.get("online")),
            } for d in (devs or [])],
            # {"overlay": {mode, delay_ms}} in "client" mode, nothing otherwise.
            **overlay_view.for_system(
                provision_service._pkg_root(st.systems_dir, system.slug), system.slug),
        })
    return {"ok": True, "systems": systems}


_ARCH_CACHE = {}


def _chip_arch():
    """The accelerator arch on this box, detected once per process."""
    if "arch" not in _ARCH_CACHE:
        try:
            from core.services import builtin_models
            _ARCH_CACHE["arch"] = builtin_models.detect_arch()
        except Exception:
            _ARCH_CACHE["arch"] = None
    return _ARCH_CACHE["arch"]


#: slug -> (when, last good detector_control.status). The System Control table
#: polls every 5s; a single throw inside status() used to blank the whole row
#: (state, camera, model) for exactly one poll -- which reads as flicker.
_STAT_CACHE = {}

_REACH_CACHE = {"at": 0.0, "by_slug": {}}
#: Reachability is re-probed at most this often. The System Control table polls
#: every 5s; probing every master on every poll would spend 1.5s of timeouts on
#: a state that changes on the scale of minutes.
_REACH_TTL_S = 4.0


def _reachable(masters):
    """{slug: bool} -- is each system's master answering? Cached briefly.

    This is the ONLY input to a system's online/offline state. The card grid's
    _status also folds in paired-device counts, which is a fleet-era notion: a
    perfectly healthy standalone board with nothing paired reports "down"
    there. System Control must not inherit that, so it asks this instead."""
    import time as _time
    now = _time.monotonic()
    if now - _REACH_CACHE["at"] < _REACH_TTL_S and _REACH_CACHE["by_slug"]:
        return _REACH_CACHE["by_slug"]
    try:
        probed = masters_service.probe_all(masters)
    except Exception:                                       # noqa: BLE001
        probed = {}
    by_slug = {slug: probed.get(slug) is not None for slug in masters}
    _REACH_CACHE["at"] = now
    _REACH_CACHE["by_slug"] = by_slug
    return by_slug


def _camera_label(camera):
    """A camera name a person can read. `source` is a machine spec -- a USB
    camera is the string "0", a GigE one is `gige:Basler-abc123`. The friendly
    name chosen in Detection is stored alongside it; fall back to a readable
    rendering of the spec rather than showing the raw string."""
    label = str((camera or {}).get("label") or "").strip()
    if label:
        return label
    src = str((camera or {}).get("source") or "").strip()
    if not src:
        return ""
    if src.startswith("rtsp://"):
        host = src.split("@")[-1].split("/")[0].split(":")[0]
        return f"IP camera {host}" if host else "IP camera"
    if src.startswith("gige"):
        name = src.split(":", 1)[1] if ":" in src else ""
        return f"GigE {name}" if name else "GigE camera"
    if src == "synthetic":
        return "Synthetic"
    if src.isdigit():
        return f"USB {src}"
    # Any other URL (http://user:pass@...) would carry its password here.
    from core.services import detector_control
    return detector_control.mask_source(src)


@router.get("/api/hub/pipelines", name="hub.pipelines")
def hub_pipelines(request: Request):
    """Every system's detection pipeline at a glance -- what the System Control
    table and the NPU Monitor render: camera source, model, backend, measured
    FPS (loop + per-model on-chip), running state, pid, and the autostart flag.
    Read straight from each system's package on this box (standalone layout).
    Scoped to systems the account may open."""
    from core.services import detector_control
    st = request.app.state
    username = getattr(request.state, "user", None) or ""
    masters = config.systems(st.config_dir).get("masters", {})
    reach = _reachable(masters)
    rows = []
    for system in st.registry:
        # Same shared rule as the Systems grid (grant + workshop split).
        if not hub_service.visible_to(username, system.slug, masters,
                                      st.config_dir):
            continue
        root = provision_service._pkg_root(st.systems_dir, system.slug)
        try:
            stat = detector_control.status(root, system.slug)
            _STAT_CACHE[system.slug] = (time.time(), stat)
        except Exception:
            # One failed read must not render as "system gone" for one poll
            # and back the next -- reuse the last good answer briefly instead
            # of nulling the row.
            when, cached = _STAT_CACHE.get(system.slug, (0, {}))
            stat = cached if time.time() - when < 30 else {}
        det = stat.get("detector") or {}
        entry = masters.get(system.slug) or {}
        camera = stat.get("camera") or {}
        # Multi-camera systems report the full list; `camera`/`camera_label`
        # below stay the FIRST one so the existing table keeps rendering.
        cams = [c for c in (stat.get("cameras") or []) if isinstance(c, dict)]
        running = bool(stat.get("running"))
        # ONE field decides what the table says. Deriving the word client-side
        # from two independently-polled endpoints is why it used to flicker:
        # the two answers arrived at different moments and disagreed.
        state = "running" if running else ("online" if reach.get(system.slug) else "offline")
        # Powered off on the Hub. Its master may well answer again (systemd
        # restarts it), but the system is OFF until somebody powers it on --
        # saying "Online" there hid why nothing ran.
        try:
            powered_off = detector_control.powered_off(root, system.slug)
        except Exception:                                    # noqa: BLE001
            powered_off = False
        if powered_off and not running:
            state = "powered_off"
        rows.append({
            "slug": system.slug,
            "name": system.display_name,
            "state": state,
            "running": running,
            # Detection off, the rest of the workflow running in the app.
            "logic_running": bool(stat.get("logic_running")),
            # The "AI running" dot: green only while a real detector and a
            # camera are producing; `ai_error` is the red dot's tooltip.
            "ai_ok": bool(stat.get("ai_ok")),
            "ai_error": stat.get("ai_error") or "",
            # Stopped by the box because no camera is there ("camera_missing"),
            # not by the user; it starts again by itself.
            "ai_paused": stat.get("ai_paused") or None,
            "pid": stat.get("pid"),
            "uptime_s": stat.get("uptime_s") or 0,
            "kind": det.get("kind") or "mock",
            "task": det.get("task") or "detection",
            "model": det.get("model"),
            # Masked for every viewer: this table is read-only, and a hub
            # account that may see the row is not one that may read an RTSP
            # password (detector_control.mask_source, the __redacted__ rule).
            "camera": detector_control.mask_source(camera.get("source")) or None,
            "camera_label": _camera_label(camera),
            "cameras": [{"id": c.get("id"), "label": _camera_label(c)} for c in cams],
            "camera_count": len(cams),
            "fps": stat.get("fps"),
            "infer_fps": stat.get("infer_fps") or {},
            "error": stat.get("error"),
            "autostart": bool(entry.get("autostart")),
            "capture": detector_control.get_capture(root, system.slug),
            "url": net.rewrite((entry.get("url") or "").rstrip("/"), request),
        })
    # The two caps this box enforces, sent with the rows that count against
    # them: how many systems may EXIST and how many may RUN at once. The
    # header meters read these rather than hardcoding 4 -- a UI holding its
    # own copy of a server limit is a UI that lies the day the limit moves.
    #
    from core.services import detector_control as dc
    return {"ok": True, "arch": _chip_arch(), "pipelines": rows,
            "running": sum(1 for r in rows if r["running"]),
            "max_running": dc.MAX_RUNNING_APPS,
            "max_systems": provision_service.MAX_SYSTEMS}


@router.post("/api/hub/systems/{slug}/capture", name="hub.capture")
async def set_capture(slug: str, request: Request):
    """System Control's snapshot/record switches: whether the workflow's
    Snapshot and Record nodes may act on this system. Open to whoever is
    granted the system; the app picks the change up on its next config poll
    (~2s)."""
    _require_operator(request, slug, "changing capture")
    st = request.app.state
    if slug not in {s.slug for s in st.registry}:
        raise HTTPException(status_code=404, detail="no such system")
    body = await request.json()
    from core.services import detector_control
    # Generated systems live outside the repo -- resolve the package's real home.
    root = provision_service._pkg_root(st.systems_dir, slug)
    cap = detector_control.set_capture(root, slug,
                                       snap=body.get("snap"),
                                       record=body.get("record"))
    return {"ok": True, "slug": slug, "capture": cap}


@router.post("/api/hub/systems/{slug}/detection", name="hub.detection")
async def set_detection(slug: str, request: Request):
    """System Control's run switch: start or stop a system's detection app
    directly from the table -- same engine calls the master's own power
    endpoint makes, so both stay in step. Open to whoever is granted the
    system: starting and stopping the line is the operator's own job."""
    _require_operator(request, slug, "starting and stopping detection")
    st = request.app.state
    if slug not in {s.slug for s in st.registry}:
        raise HTTPException(status_code=404, detail="no such system")
    body = await request.json()
    on = bool(body.get("on"))
    from core.services import detector_control
    root = provision_service._pkg_root(st.systems_dir, slug)
    from core.services import app_supervisor                   # noqa: PLC0415
    try:
        # Start and Stop flip the AI switch inside the running app; an old
        # app copy is stopped for real, which can wait up to 10s for the pid
        # -- off the event loop, or every hub page freezes behind it.
        result = await asyncio.to_thread(
            detector_control.start if on else detector_control.stop, root, slug)
    except power_state.StoppingError as e:
        # The previous run is still releasing the camera and the chip: the
        # app is up and nothing to reconcile, just not yet.
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        # Detection could not start: the rest of the workflow runs anyway.
        await asyncio.to_thread(app_supervisor.reconcile, root, slug,
                                st.config_dir)
        raise HTTPException(status_code=400, detail=str(e))
    if not result.get("ok"):
        # The switch is only live for an ONLINE system (the table disables
        # it otherwise), so its app belongs up whatever happened here.
        result["workflow"] = await asyncio.to_thread(
            app_supervisor.reconcile, root, slug, st.config_dir)
    return {"ok": True, "slug": slug, "on": on, "result": result}


@router.post("/api/hub/systems/{slug}/autostart", name="hub.autostart")
async def set_autostart(slug: str, request: Request):
    """Flip a system's autostart flag: at hub boot, flagged systems get their
    master spawned AND their detection app started, so the board comes up
    detecting with nobody clicking Start. Open to whoever is granted the
    system -- it decides how THIS system comes back after a power cut, which
    is the operator's own line, not the deployment's shape."""
    _require_operator(request, slug, "setting autostart")
    st = request.app.state
    if slug not in {s.slug for s in st.registry}:
        raise HTTPException(status_code=404, detail="no such system")
    body = await request.json()
    on = bool(body.get("on"))
    local = config.load_local("systems", st.config_dir)
    entry = local.setdefault("masters", {}).setdefault(slug, {})
    # Keep the existing url if the local file lacks it (merged from committed).
    if not entry.get("url"):
        url = (config.systems(st.config_dir).get("masters", {}).get(slug) or {}).get("url")
        if url:
            entry["url"] = url
    entry["autostart"] = on
    config.save_local("systems", local, st.config_dir)
    st.masters = config.systems(st.config_dir).get("masters", {})
    # The toggle acts NOW, not only on the next reboot: switching it on brings
    # the system up immediately (same helper the hub's boot loop runs).
    started = None
    if on:
        # Switching autostart ON says "this one comes up", now and at every
        # boot -- the same intent as Start, which also ends a Hub Power off
        # (detector_control.start). Left standing, a Power off made the toggle
        # do nothing and the system stayed down after every reboot with
        # autostart showing "on". The boot loop itself still leaves a powered
        # off system down; only this deliberate switch-on clears it.
        try:
            from core.services import detector_control as _dc  # noqa: PLC0415
            _dc.set_powered_off(provision_service._pkg_root(st.systems_dir, slug),
                                slug, False)
        except Exception:                                    # noqa: BLE001
            logging.getLogger(__name__).warning(
                "autostart: could not end the Power off for %s", slug, exc_info=True)
        # Probes masters and starts the app -- seconds of blocking work, so
        # off the event loop.
        started = await asyncio.to_thread(
            provision_service.autostart_system,
            st.systems_dir, slug, st.masters.get(slug) or entry, _REPO_DIR)
    return {"ok": True, "slug": slug, "autostart": on, "started": started}


@router.get("/api/hub/retention", name="hub.retention")
def get_retention(request: Request):
    """The platform data-retention policy and its hard product maximums.
    Readable by anyone who can see the hub; changing it is admin-only."""
    from core.services import retention as retention_service
    st = request.app.state
    # The CHOSEN policy, and per system what applies: a system's device-data
    # cap follows the drive ITS data is on (60 on the built-in storage, 365 on
    # the NVMe), so the systems are resolved here; `notice` says, once, when a
    # saved period is above the box's cap (the saved value is kept, 60
    # applies), and `alarm` is the NVMe-not-mounted banner.
    view = retention_service.view(st.config_dir, [
        os.path.join(provision_service._pkg_root(st.systems_dir, s.slug), s.slug)
        for s in st.registry])
    return {"ok": True, **view,
            # When the next automatic pass runs and what each kind's cutoff
            # date is, so the panel can show a schedule instead of a promise.
            "schedule": retention_service.sweep_status(
                request.app.state.config_dir)}


@router.get("/api/hub/board", name="hub.board")
def board(request: Request):
    """The board's terminals, and who owns each one.

    Box-wide on purpose: a GPIO line is ONE piece of hardware. Every system on
    the box shares it, so the decision to hand DO1 to Node-RED cannot sensibly
    live inside one system's settings.
    """
    from core.services import board_io as board_io_service
    from core.services import detector_control as dc_service
    # NOT st.systems_dir. That is the release's own package tree -- the
    # shipped template of each system, whose config carries no compiled
    # outputs -- so asking it which rules drive a terminal answered "nothing"
    # for every terminal on a box that was visibly driving four. A system's
    # real config lives in the store, which is what port_users() resolves for
    # itself when nobody tells it otherwise.
    users = board_io_service.port_users()
    try:
        live = set(dc_service.running_apps(None))
    except Exception:                                       # noqa: BLE001
        live = set()
    ports = []
    for port in board_io_service.ports():
        # "Used by" means two different things and the page needs both: a rule
        # CONFIGURED against this terminal, and whether the system holding it
        # is running this second. A stopped system's rule still reserves the
        # terminal (releasing it would break that system the moment it
        # starts), but it is not driving anything now.
        rows = [{**u, "running": u.get("slug") in live}
                for u in users.get(port["id"], [])]
        ports.append({**port, "users": rows,
                      "in_use": any(r["running"] for r in rows)
                                or bool(port.get("owner"))})
    # A box that is not an IRIV has no terminals to hand over; say so rather
    # than render eight switches that would drive nothing.
    if not ports:
        for port in board_io_service.BOARD_PORTS:
            rows = [{**u, "running": u.get("slug") in live}
                    for u in users.get(port["id"], [])]
            ports.append({**port, "released": port["id"] in board_io_service.released(),
                          "users": rows,
                          "in_use": any(r["running"] for r in rows),
                          "busy": False, "owner": "", "consumer": "",
                          "direction": ""})
    from core.services import panel as panel_service
    # The other claimant, and this one is OURS. The front panel runs in THIS
    # process and takes its pins straight out of the board map -- BUTTON is
    # PORT_BY_ID["BUTTON"], BUZZER is PORT_BY_ID["BUZZER"] -- so while it runs,
    # those two terminals are as unavailable to a workflow node as anything
    # Node-RED is holding. The page showed them owned by the platform and
    # switchable, which was true of the platform and false of the terminal.
    #
    # The button is held for as long as the panel runs. The buzzer is claimed
    # per beep and released, so a node CAN take it -- and then the panel's next
    # beep fails instead. An intermittent conflict is worse to diagnose than a
    # steady one, so both are reported as held.
    panel_running = panel_service.current() is not None
    panel_cfg = panel_service.settings()
    panel_holds = []
    if panel_running:
        if panel_cfg.get("button"):
            panel_holds.append("BUTTON")
        if panel_cfg.get("buzzer"):
            panel_holds.append("BUZZER")
    return {"ok": True, "detected": board_io_service.detected(),
            "model": board_io_service.board_model(), "ports": ports,
            "panel": {"running": panel_running, "holds": panel_holds},
            # The factory flow belongs on this page because it is the other
            # claimant on these very terminals: "held by another program"
            # names it, and this is where the operator can do something
            # about it.
            "nodered": panel_service.nodered_state()}


@router.post("/api/hub/board/nodered", name="hub.set_nodered")
async def set_nodered(request: Request):
    """Admin-only: start or stop the board's factory Node-RED flow.

    The one service outside this product that the product will stop, and only
    because the two cannot share the hardware: the factory flow drives the
    OLED, the buzzer, the user button and several terminals, and while it runs
    those are not ours to give. Stopping it also disables it, because a flow
    that returns at the next power cycle to take back terminals a workflow is
    now driving is a fault that would surface on site, not here.

    Starting it is the reverse and the caller is told so: Node-RED will claim
    its lines back the moment it comes up.
    """
    from core.services import panel as panel_service
    _require_admin(request, "starting or stopping Node-RED")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    on = bool((body or {}).get("on"))
    ok, message = panel_service.set_nodered(on)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message,
            "nodered": panel_service.nodered_state()}


@router.post("/api/hub/board/release", name="hub.set_board_release")
async def set_board_release(request: Request):
    """Admin-only: hand one terminal to another program, or take it back.

    Releasing is REFUSED while a rule is bound to the terminal (the service
    raises, naming them). That refusal is the whole safety story here: the
    alternative is a settings click dropping a relay that something is
    holding closed, and the machine wired to it does not care that the click
    happened in a settings page.
    """
    from core.services import board_io as board_io_service
    _require_admin(request, "changing board terminal ownership")
    st = request.app.state
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = dict(body or {})
    try:
        board_io_service.set_released(body.get("port"), bool(body.get("released")),
                                      st.systems_dir, st.config_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return board(request)


@router.post("/api/hub/retention", name="hub.set_retention")
async def set_retention(request: Request):
    """Admin-only: update the platform retention policy (a value above what
    this box allows is refused in words -- 60 days for device data unless it
    is on the NVMe, then 12 months; 12 months for the database) and run a
    sweep so the change takes effect now, not at the next cycle.
    Auth, credentials, the audit log, configs, templates and workflows are
    outside retention entirely and cannot be configured here."""
    from core.services import retention as retention_service
    # A site admin's business: how long THEIR operation's data is kept.
    _require_site_admin(request, "changing data retention")
    st = request.app.state
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = dict(body or {})
    # The start date is not a retention PERIOD, so it is taken out before the
    # policy write (which refuses unknown keys) and applied on its own. It is
    # here rather than on a route of its own because it is saved by the same
    # button, and two requests behind one Save is two ways for a page to end
    # up half-applied.
    starts_at = body.pop("starts_at", None)
    pkg_dirs = [os.path.join(provision_service._pkg_root(st.systems_dir, s.slug),
                             s.slug)
                for s in st.registry]
    try:
        pol = retention_service.set_policy(body, st.config_dir, pkg_dirs)
        if starts_at is not None:
            retention_service.set_starts_at(starts_at, st.config_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    report = await asyncio.to_thread(
        retention_service.sweep, pkg_dirs, st.config_dir)
    view = retention_service.view(st.config_dir, pkg_dirs)
    return {"ok": True, **view,
            "schedule": retention_service.sweep_status(st.config_dir),
            "swept": {k: v for k, v in report.items() if k != "policy"}}


@router.post("/api/hub/systems/{slug}/data/delete", name="hub.purge_system_data")
async def purge_system_data(slug: str, request: Request):
    """Admin-only, PER-SYSTEM, deliberate: delete one system's recorded data
    (events, event archives, snapshots, clips). Its configuration, zones,
    dashboards and credentials stay; the system keeps running. This is the
    only manual data deletion -- there is deliberately no all-systems reset,
    and none should ever be added.

    Body {"scope": ...} narrows it (retention_service.PURGE_SCOPES): "events"
    clears only the event log and its archives (the backlog that actually
    grows), "footage" only snapshots and clips, "counters" resets the
    dashboard's numbers, "eventlog" clears what the system's Events Logs page
    shows, "logs" what its System Logs page shows. Omitted body or missing
    key means "all", so anything already calling this endpoint is unchanged."""
    from core.services import retention as retention_service
    _require_admin(request, "deleting a system's data")
    st = request.app.state
    if slug not in {s.slug for s in st.registry}:
        raise HTTPException(status_code=404, detail="no such system")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    scope = str((body or {}).get("scope") or "all").strip().lower()
    if scope not in retention_service.PURGE_SCOPES:
        raise HTTPException(
            status_code=400,
            detail="scope must be one of: "
                   + ", ".join(retention_service.PURGE_SCOPES))
    systems_root = provision_service._pkg_root(st.systems_dir, slug)
    restarted = False
    if scope == "counters":
        # Seconds of work: stop the app, clear three stores, bounce the
        # master, start the app. Held open, that is a button that looks
        # frozen -- so it starts a job and answers NOW. The panel polls
        # reset-status, and a page that opens meanwhile can say the system is
        # resetting rather than render numbers about to change.
        #
        # The MASTER has to be bounced too: it caches its own running tallies
        # and its cursor into the event log in its own process, so whatever
        # the hub deletes, the page keeps serving the old numbers until that
        # process restarts. Same primitives the master power route uses, and
        # only when the master runs on this box.
        url = (st.masters.get(slug) or {}).get("url") or ""
        parsed = urllib.parse.urlparse(url)
        repo_dir = str(pathlib.Path(st.systems_dir).resolve().parent)

        def _bounce_master():
            if not parsed.port or not provision_service.is_local_master(
                    parsed.hostname):
                return False
            provision_service.stop_master(url)
            time.sleep(0.5)              # let the port free before rebinding
            provision_service.spawn_master(repo_dir, slug, parsed.port)
            return True

        return {"ok": True, "slug": slug, "scope": scope, "started": True,
                "status": retention_service.start_counter_reset(
                    systems_root, slug, restart=_bounce_master)}
    removed = await asyncio.to_thread(
        retention_service.purge_system, systems_root, slug, scope)
    if scope == "logs":
        # The master's in-memory ring is the other half of its System Logs
        # page, and it lives in another process: asked to clear itself, as
        # the operator (their cookie authorises there as it did here). Best
        # effort -- a master that is not running has no ring to clear.
        url = (st.masters.get(slug) or {}).get("url") or ""
        answer = await asyncio.to_thread(
            masters_service.post_json, url, "/api/logs/clear", {},
            request.headers.get("cookie"))
        removed["master_ring"] = bool(answer and answer.get("ok"))
    return {"ok": True, "slug": slug, "scope": scope, "removed": removed}


@router.get("/api/hub/systems/{slug}/data/reset-status", name="hub.reset_status")
def reset_status(slug: str, request: Request):
    """How a counters reset is going: {state, step, removed, restarted}.

    Readable by anyone logged in, not just an admin: the point is that a page
    opening mid-reset can say "this system is resetting" instead of showing
    numbers that are about to change under the reader.
    """
    from core.services import retention as retention_service
    if slug not in {s.slug for s in request.app.state.registry}:
        raise HTTPException(status_code=404, detail="no such system")
    return {"ok": True, "slug": slug, **retention_service.reset_status(slug)}


@router.get("/api/hub/health", name="hub.health")
def hub_health(request: Request):
    """Health of THIS box first -- the machine that runs the dashboards and
    detection, and its Hailo NPU (all systems on one board share it) -- then, for
    fleet deployments, each system's paired-device health. Scoped to systems the
    account may open."""
    from core.services import host_health as host_health_service
    st = request.app.state
    username = getattr(request.state, "user", None) or ""
    cookie = request.headers.get("cookie")
    host = host_health_service.host_health()
    systems = []
    for system in st.registry:
        # Same shared rule as the Systems grid (grant + workshop split).
        if not hub_service.visible_to(username, system.slug, st.masters,
                                      st.config_dir):
            continue
        url = (st.masters.get(system.slug) or {}).get("url")
        if not url:
            systems.append({"slug": system.slug, "name": system.display_name,
                            "url": None, "reachable": False, "devices": []})
            continue
        body = masters_service.fetch_json(url.rstrip("/"), "/api/monitoring/live", cookie)
        rows = body.get("rows") if body else None
        # fetch_json above used the configured url (this process, this box);
        # what goes back to the browser is rewritten onto the request's host.
        systems.append({"slug": system.slug, "name": system.display_name,
                        "url": net.rewrite(url.rstrip("/"), request),
                        "reachable": rows is not None,
                        "devices": rows or []})
    return {"ok": True, "host": host, "systems": systems}


@router.post("/api/hub/systems/{slug}/power", name="hub.power")
async def power_system(slug: str, request: Request):
    """Start / stop / restart a system's master from the hub's control table.
    Loopback masters only - a master on another host is that host's to manage
    (its systemd unit via run_all.sh). Open to whoever is granted the system:
    an operator who cannot restart their own system has to go and find
    somebody with the admin password to do it for them."""
    _require_operator(request, slug, "starting and stopping the master")
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = str(body.get("action") or "")
    if action not in ("start", "stop", "restart"):
        raise HTTPException(status_code=400, detail="action must be start, stop or restart")

    st = request.app.state
    if slug not in {s.slug for s in st.registry}:
        raise HTTPException(status_code=404, detail="no such system")
    # The Monitor account watches and runs detection; it does not power
    # systems (detection start and stop are its own, on the system's page).
    if monitor_account.is_monitor(getattr(request.state, "user", None), st.config_dir):
        raise HTTPException(status_code=403,
                            detail="The Monitor account cannot power systems.")
    # A system the Monitor account may open stays online: Power off would
    # leave a central program without it.
    if action == "stop" and monitor_account.keeps_online(slug, st.config_dir):
        raise HTTPException(
            status_code=409,
            detail="This system is open to the Monitor account, so it stays "
                   "online. Remove it from the Monitor's systems in System "
                   "settings first.")
    url = (st.masters.get(slug) or {}).get("url")
    if not url:
        raise HTTPException(status_code=400, detail="no master configured for this system")
    parsed = urllib.parse.urlparse(url)
    if not parsed.port or not provision_service.is_local_master(parsed.hostname):
        raise HTTPException(
            status_code=400,
            detail=f"{slug}'s master runs at {parsed.hostname} - manage it on that host")

    def _do():
        # Probes, kills and sleeps -- blocking by nature, so this whole block
        # runs off the event loop; the hub keeps serving other pages meanwhile.
        #
        # STOP moves both ends; START moves only the master.
        #
        # Stopping only the master left the app running headless -- holding
        # the camera and the Hailo chip, and reviving itself as soon as the
        # master came back, so "stopped" was neither. The app is stopped
        # FIRST for the same reason: killing the master first orphans it with
        # nothing left to ask it to quit.
        #
        # Starting is not symmetric, on purpose. This button brings a system's
        # PAGES up; running inference is a separate decision with a cost --
        # the camera, a share of the accelerator, and every rule in the
        # workflow firing. Pressing Start on the Hub used to spend all of that
        # on a system somebody only wanted to look at, and on a box with a
        # camera budget it took the camera away from a system that was using
        # it. Detection is turned on inside the system (System Control), or by
        # that system's own autostart setting if it has one.
        repo_dir = str(pathlib.Path(st.systems_dir).resolve().parent)
        pkg_root = provision_service._pkg_root(st.systems_dir, slug)
        stopped = started = False
        app_note = "skipped"
        # POWER OFF is remembered (dc.powered_off): under systemd the master
        # restarts itself seconds after it is stopped, and nothing that comes
        # back by itself -- the app supervisor, the boot autostart -- may
        # bring this system up again. Power on (and restart) clears it.
        try:
            from core.services import detector_control as _dc  # noqa: PLC0415
            _dc.set_powered_off(pkg_root, slug, action == "stop")
        except Exception:                                    # noqa: BLE001
            pass
        if action == "start" and masters_service.probe(url, timeout=1.0):
            # Already up (systemd brought it back after a Power off): Power on
            # is what lets its workflow run again, so ask for that now rather
            # than at the master's next start.
            from core.services import app_supervisor           # noqa: PLC0415
            return {"ok": True, "action": action, "note": "master already running",
                    "app": app_note,
                    "workflow": app_supervisor.reconcile(pkg_root, slug,
                                                         st.config_dir)}
        if action in ("stop", "restart"):
            app_note = _app_power(pkg_root, slug, "stop")
            stopped = provision_service.stop_master(url)
        if action in ("start", "restart"):
            if action == "restart":
                time.sleep(0.5)   # let the port free before the new bind
            provision_service.spawn_master(repo_dir, slug, parsed.port)
            started = True
        return {"ok": True, "action": action, "stopped": stopped,
                "started": started, "app": app_note}
    return await asyncio.to_thread(_do)


def _app_power(pkg_root, slug, action):
    """Start detection, or stop a system's whole app (Power off: the workflow
    goes down with the master, not only the AI). Never raises: a system with
    no app (or one whose app code predates this) must not fail the master's
    own start/stop, which is the part the button is really about."""
    from core.services import detector_control as dc          # noqa: PLC0415
    try:
        # Power on brings the whole app up; a camera not there yet is the
        # app's wait (camwatch), not a reason to leave the workflow down.
        result = dc.start(pkg_root, slug, require_camera=False) if action == "start" \
            else dc.stop_app(pkg_root, slug)
        return "started" if action == "start" and result.get("ok") else (
            "stopped" if result.get("ok") else (result.get("message") or "failed"))
    except Exception as e:                                    # noqa: BLE001
        return f"failed: {e}"


def _known_slug(request, slug):
    if slug not in {s.slug for s in request.app.state.registry}:
        raise HTTPException(status_code=404, detail="no such system")


@router.post("/api/hub/systems/{slug}/image", name="hub.image_upload")
async def upload_system_image(slug: str, request: Request,
                              file: UploadFile = File(...)):
    """Set a system card's cover image. Stored on THIS device (the persistent
    store), so every browser that opens the hub sees it. Admin only."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                  request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="setting a system image is an admin action")
    _known_slug(request, slug)
    blob = await file.read()
    try:
        stored = hub_service.save_image(slug, file.filename, blob)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "slug": slug, "stored": stored, "bytes": len(blob)}


@router.get("/api/hub/systems/{slug}/image", name="hub.image")
def system_image(slug: str, request: Request):
    """The stored cover image. Session-gated like every hub page; the card's
    <img> is same-origin so the cookie rides along."""
    _known_slug(request, slug)
    path = hub_service.image_path(slug)
    if not path:
        raise HTTPException(status_code=404, detail="no image for this system")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.delete("/api/hub/systems/{slug}/image", name="hub.image_delete")
def delete_system_image(slug: str, request: Request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                  request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="removing a system image is an admin action")
    _known_slug(request, slug)
    removed = hub_service.delete_image(slug)
    if not removed:
        raise HTTPException(status_code=404, detail="no image for this system")
    return {"ok": True, "slug": slug, "removed": removed}


def _on_host(request, payload):
    """Each card's link moved onto the host the browser used: config owns a
    master's PORT, the request owns the host (a board whose IP moved, or a
    laptop using another address, could not follow one otherwise; probing
    keeps the configured URL). Per request, AFTER perm.shared, on copies --
    the request's host is never part of an answer another request is given."""
    cards = [dict(card) for card in payload.get("cards") or ()]
    for card in cards:
        if card.get("url"):
            was, card["url"] = card["url"], net.rewrite(card["url"], request)
            hub_service.readdress(card, was)
    return dict(payload, cards=cards)


@router.get("/api/hub", name="hub.cards")
@perm.shared("hub", after=_on_host)
def hub_cards(request: Request):
    """The systems grid: one card per registered system the caller may open,
    with live master reachability. A system with no master configured still gets
    a card. Cards are scoped to the account so an operator never sees a system
    that click-through would 401 (links: _on_host)."""
    cfg = request.app.state.config_dir
    username = getattr(request.state, "user", None) or ""
    masters = request.app.state.masters
    cards = hub_service.build_cards(
        request.app.state.registry, masters,
        masters_service.probe_all(masters),
        username=username, config_dir=cfg)
    # How many systems this box allows, and how many EXIST (not just how many
    # this account may see -- the cap is per box). Sent so the page can stop
    # offering "New System" at the limit instead of letting the operator fill a
    # form and meet a 400 at the end of it.
    from core.services import provision as provision_service
    try:
        total = provision_service.count_systems(
            request.app.state.systems_dir, cfg)
    except Exception:                                       # noqa: BLE001
        total = len(cards)
    # A system whose counters are being reset is mid-restart, so its card must
    # say so: opening it during those seconds shows an unreachable master or
    # numbers about to change, and either reads as a fault rather than as the
    # button someone just pressed.
    from core.services import retention as retention_service
    resetting = set(retention_service.resetting_slugs())
    for card in cards:
        if card.get("slug") in resetting:
            card["resetting"] = True
    # The NVMe-not-mounted alarm, for the banner the shell draws on every
    # page: this poll already runs every 10 s on every hub page, so the
    # banner needs no poll of its own. current() answers from the last
    # minute's check (core/services/storage_watch.py).
    from core.services import storage_watch
    return {"ok": True, "cards": cards,
            "storage_alarm": storage_watch.current(cfg),
            "system_count": total,
            # The running platform version, for the sidebar footer -- same
            # frozen-at-import constant /api/health reports (core/version.py).
            "version": VERSION,
            "max_systems": provision_service.MAX_SYSTEMS}
