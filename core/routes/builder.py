# -*- coding: utf-8 -*-
"""
builder.py -- the widget builder's API: read, save, preview, publish.

Thin by contract (conventions sec 2): every decision is in
core/services/builder.py (layout as data) and core/services/publish.py
(packaging). This file resolves the request, enforces who may write, and turns
a ValueError into a 400.

Writes are admin-only. A layout is rendered for every operator afterwards, so
composing one is an administrative act, the same as editing a device.
"""

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from core.services import auth as auth_service
from core.routes import perm
from core.services import analytics as analytics_service
from core.services import builder as builder_service
from core.services import main as main_service
from core.services import publish as publish_service
from core.routes.spa import _caps

router = APIRouter()


def _slug(request):
    system = getattr(request.app.state, "system", None)
    if system is None:
        raise HTTPException(status_code=404, detail="no system on this master")
    return system.slug


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _live(request, range_key="month"):
    """The same events/stats the Analytics page renders, so a preview cannot
    disagree with the page it is previewing."""
    st = request.app.state
    # live_local_stats, not the sync-cycle cache: a preview number lagging the
    # feed by ~10s reads as "the builder is broken".
    stats = analytics_service.scope_stats(
        main_service.aggregate_stats(st.slaves, main_service.live_local_stats(st)),
        range_key)
    events = analytics_service.scope_events(st.events, range_key)
    # `compliance` is derived from zone actions, not published by the app, but
    # the palette and the preview both read `stats` -- so it is folded in here,
    # where every builder-side reader picks it up.
    try:
        stats = {**stats, builder_service.COMPLIANCE_KEY: _shaped(request)[1]["compliance"]}
    except Exception:                                       # noqa: BLE001
        pass
    return events, stats


def _shaped(request):
    """This system's zones/verdict, and the live stats with `compliance` folded
    in so an ordinary widget can bind to it."""
    from core.services import detector_control as dc
    from core.services import overview as overview_service
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    # The Dashboard polls this every 2s; reading the sync-cycle cache here made
    # every tile lag the video by up to a full cycle (~10s). live_local_stats
    # reads the app's state.json fresh -- one small-file read per poll.
    stats = main_service.aggregate_stats(st.slaves, main_service.live_local_stats(st))
    if not system or not systems_dir:
        return {}, {}, stats
    try:
        status = dc.status(systems_dir, system.slug)
    except Exception:                                       # noqa: BLE001
        status = {}
    shaped = overview_service.zone_overview(status, stats)
    stats = dict(stats)
    stats[builder_service.COMPLIANCE_KEY] = shaped["compliance"]
    return status, shaped, stats


def _may_press(request, widgets):
    """Tell each Inject BUTTON whether the person looking may press it.

    The press goes through /api/workflow/inject, gated on workflow.edit -- a
    button fires real outputs -- so a viewer without that right is shown the
    button disabled rather than a button that answers 403. Checked once per
    render, not per button. The AI model classes and Class filter cards the
    same way, each on its own route's gate (dashboard_controls.GATES)."""
    def can(cap):
        try:
            perm.require(request, cap)
            return True
        except HTTPException:
            return False
    from core.services import dashboard_controls             # noqa: PLC0415
    return dashboard_controls.mark_gates(widgets, can)


@router.get("/api/dashboard", name="builder.dashboard")
@perm.shared("dashboard")
def dashboard(request: Request):
    """The Dashboard: one poll, one payload.

    The page used to hardcode a feed, an indicator and a row of zone cards.
    Now it renders a Builder page, so what appears is the operator's decision --
    including whether the video feed is there at all. Everything the page needs
    (run state, computed widgets, setup progress) comes back together, because
    two polls for one screen is how a UI starts contradicting itself.
    """
    st = request.app.state
    slug = _slug(request)
    status, shaped, stats = _shaped(request)
    layout = builder_service.ensure_main_page(st.config_dir, slug, shaped)
    page = builder_service.main_page(layout) or {"widgets": []}
    widgets = _may_press(request, builder_service.compute(
        page.get("widgets"), st.events, stats, _caps(st), st.config_dir, slug,
        systems_dir=st.systems_dir))
    return {
        "ok": True,
        "running": bool(status.get("running")),
        # Alive but mid-something-slow (loading a model, reopening a dropped
        # camera). The dashboard used to read this moment as STOPPED and offer
        # "Start detection →" -- a button that starts a second app under one
        # that is already running.
        "stalled": bool(status.get("stalled")),
        # Detection stopped by the box because no camera is there; the
        # header's Stop stays (the user's switch is still on) beside a note
        # that it starts again by itself.
        "ai_paused": status.get("ai_paused") or None,
        # Stop pressed, camera and chip not released yet: the header holds
        # Start ("Stopping...") until this clears (power_state.py).
        "stopping": bool(status.get("stopping")),
        "detector_name": status.get("detector_name"),
        "fps": status.get("fps"),
        "camera": status.get("camera") or {},
        "frame": {"objects_in_frame": stats.get("objects_in_frame"),
                  "violations_now": stats.get("violations_now")},
        "verdict": shaped.get("verdict"),
        "widgets": widgets,
        # How tall a grid row is on this page. Chosen in the Builder, saved
        # with the layout, so the finished dashboard matches what was arranged.
        "grid_size": builder_service._as_size(page.get("size")),
        # What the first-run card walks through. Derived, never stored, so a
        # step cannot get stuck ticked after the thing it checked was undone.
        "setup": _setup_state(request, status, shaped),
        "editable": perm.editable(request, "main."),
        # The header's Start/Stop is drawn disabled for an account without
        # detection.power rather than answering it with a 403.
        "caps": perm.held(request),
    }


def _setup_state(request, status, shaped):
    """Which of the four setup steps this system has actually completed."""
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    detector = status.get("detector") or {}
    steps = {
        "model": bool(detector.get("model")),
        # A real one: "synthetic" is a config written before it was
        # withdrawn, and an unset source is the scaffolded default. Neither
        # is a camera, and the Dashboard's Start button keys on this.
        "camera": str((status.get("camera") or {}).get("source")
                      or "").strip() not in ("", "synthetic"),
        "zones": bool(shaped.get("zones")),
        "workflow": False,
    }
    tutorial = {"done": False}
    if system and systems_dir:
        try:
            from core.services import functions as functions_service
            steps["workflow"] = bool(functions_service.get_functions(systems_dir, system.slug))
        except Exception:                                   # noqa: BLE001
            pass
        try:
            from core.services import detector_control as dc
            tutorial = dc.get_tutorial(systems_dir, system.slug)
        except Exception:                                   # noqa: BLE001
            pass
    # Skipping the tour must actually stop it. Without this the Dashboard would
    # keep taking the whole screen over with the setup card, which is exactly
    # the thing the operator just asked to be rid of.
    return {**steps, "done": all(steps.values()), "skipped": bool(tutorial.get("done"))}


def _catalog(request, surface):
    """Ready-made widgets the workflow offers to one surface.

    A raw stat key says nothing about how it should be drawn; the compiler that
    minted it knows, and says so here (core/services/workflow.py metric_role).
    Ticking "Dashboard" or "Analytics" on a node puts an entry in this list --
    it never places a widget, so a workflow edit can't rearrange a layout
    someone spent an afternoon on.
    """
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        return []
    try:
        from core.services import detector_control as dc
        from core.services import workflow as workflow_service
        store = workflow_service.load_store(system.slug)
        if not store:
            return []
        zones = dc._zone_list(dc._merged(dc._pkg_dir(systems_dir, system.slug)))
        from core.services import datasets as datasets_service
        compiled = workflow_service.compile_store(
            store, zones,
            tables=datasets_service.definitions(systems_dir, system.slug))
    except Exception:                                       # noqa: BLE001
        return []                   # a palette extra is never worth a 500
    return [c for c in compiled.get("catalog") or [] if surface in c.get("surfaces", ())]


def _camera_choices(st):
    """[{id, name}] for this system's cameras, or [] on the hub / on failure --
    a palette extra is never worth a 500."""
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        return []
    try:
        from core.services import detector_control as dc      # noqa: PLC0415
        return [{"id": c["id"],
                 "name": c.get("name") or c.get("label") or c["id"]}
                for c in dc.cameras(systems_dir, system.slug)]
    except Exception:                                          # noqa: BLE001
        return []


def _key_labels(st):
    """Friendly names for machine-y stat keys: fn_<id>_<metric> keys map to the
    workflow/function's display name ("Compliant · value"), zone/line keys to
    the zone's name. The pickers show these next to the raw key so an operator
    recognises what they built, not a minted id."""
    labels = {}
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        return labels
    try:
        from core.services import detector_control as dc
        from core.services import functions as functions_service
        for fn in functions_service.get_functions(systems_dir, system.slug):
            for metric in functions_service.OUTPUTS.get(fn["type"], ()):
                labels[f"fn_{fn['id']}_{metric}"] = f"{fn['name']} · {metric}"
            # A function with an alert also publishes <id>_active: 1 while the
            # condition holds, 0 otherwise -- what a status/verdict widget
            # binds to. Declared here so the picker offers it before the app
            # has ever evaluated it.
            if fn.get("trigger") or fn.get("triggers"):
                labels[f"fn_{fn['id']}_active"] = f"{fn['name']} · alert active (0/1)"
        cfg = dc._merged(dc._pkg_dir(systems_dir, system.slug))
        for z in dc._zone_list(cfg):
            if z["kind"] == "line":
                for suffix in ("in", "out", "occupancy"):
                    labels[f"line_{z['id']}_{suffix}"] = f"{z['name']} · {suffix}"
            else:
                labels[f"zone_{z['id']}_count"] = f"{z['name']} · count"
    except Exception:                                       # noqa: BLE001
        pass                    # labels are a courtesy, never a 500
    return labels


@router.get("/api/builder", name="builder.get")
def get_layout(request: Request):
    """The saved layout, the keys this system can publish, and the palette's
    fixed vocabulary."""
    st = request.app.state
    _, stats = _live(request)
    from core.repos import events as events_repo
    labels = _key_labels(st)
    sources = builder_service.sources(stats)
    # The picker offers every key this system DECLARES, not only the ones that
    # happen to hold a value this second. A variable is created in the Workflow
    # and publishes nothing until the detection app runs and its function
    # evaluates -- so binding a widget to it was impossible precisely when an
    # operator was setting things up, and the Workflow appeared to have more
    # variables than the Builder could see.
    declared = [k for k in labels if k not in set(sources.get("stat") or ())]
    sources["stat"] = sorted((sources.get("stat") or []) + declared)
    return {
        "slug": _slug(request),
        "layout": builder_service.load(st.config_dir, _slug(request)),
        "sources": sources,
        "key_labels": labels,
        # Keys that exist but have not published a value yet, so the picker can
        # say so instead of looking broken when the widget renders empty.
        "pending_keys": sorted(declared),
        "catalog": _catalog(request, "dashboard"),
        "kinds": list(builder_service.KINDS),
        # Vocabulary for time-series and comparison widgets (multi-series).
        "history": {
            "ranges": list(builder_service.HISTORY_RANGES),
            "intervals": list(builder_service.HISTORY_INTERVALS),
            "aggs": list(builder_service.SERIES_AGGS),
        },
        # Vocabulary for the "query" source (event-store aggregations).
        "query": {
            "aggregations": list(builder_service.AGGREGATIONS),
            "buckets": list(builder_service.BUCKETS),
            "ranges": list(builder_service.RANGES),
            "columns": list(events_repo.FILTERABLE),
            "attributes": events_repo.attributes(_slug(request)),
        },
        # The cameras a video widget may show. A system with one camera needs
        # no choice, but with several the widget has to say WHICH feed it is.
        "cameras": _camera_choices(st),
        "capabilities": sorted(_caps(st)),
        "editable": perm.editable(request, "main."),
    }


@router.put("/api/builder", name="builder.save")
async def save_layout(request: Request):
    perm.require(request, "main.edit")
    body = await request.json()
    config_dir = request.app.state.config_dir
    # `partial: true` says "these are MY widgets, keep whatever else is in
    # there" -- so an Analytics layout save cannot clobber the wf_* widgets a
    # concurrent workflow recompile wrote between the read and this write.
    if body.get("partial"):
        body = builder_service.merge_partial(
            builder_service.load(config_dir, _slug(request)), body)
    try:
        layout = builder_service.save(config_dir, _slug(request), body)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "layout": layout}


@router.post("/api/builder/preview", name="builder.preview")
async def preview(request: Request):
    """Render an unsaved layout against live data. Lets the UI show the real
    widget while dragging, instead of a mock that lies.

    strict=False: this runs on every keystroke in the inspector, so a series
    row the operator has half filled in must draw as an empty, named series
    rather than raise -- a 400 here blanks every OTHER card on the page too.
    The save and publish routes validate strictly and still refuse the row.
    """
    body = await request.json()
    try:
        widgets = builder_service.validate(body.get("widgets"), strict=False)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    events, stats = _live(request, body.get("range") or "month")
    st = request.app.state
    caps = _caps(st)
    return {"widgets": _may_press(request, builder_service.compute(
        widgets, events, stats, caps, st.config_dir, _slug(request),
        systems_dir=st.systems_dir, page_range=body.get("range") or ""))}


@router.post("/api/builder/publish", name="builder.publish")
async def publish(request: Request):
    """Export the layout as a deployable system package.

    `target: "download"` returns the zip; `target: "install"` writes it into
    this host's systems directory. Installing never overwrites unless asked --
    a hand-written package outranks a generated one.
    """
    perm.require(request, "main.edit")
    body = await request.json()
    st = request.app.state
    slug = str(body.get("slug") or _slug(request)).strip()
    raw = body.get("layout") or builder_service.load(st.config_dir, _slug(request))
    # A published package is a single flat widget set (packages have no pages
    # yet), so flatten every page's widgets into one layout for packaging.
    pages = builder_service._as_pages(raw)
    layout = {"widgets": builder_service.all_widgets({"pages": pages})}
    meta = body.get("meta") or {}

    try:
        if (body.get("target") or "download") == "install":
            target = publish_service.write_package(
                st.systems_dir, slug, layout, meta, bool(body.get("overwrite")))
            return {"ok": True, "installed": target,
                    "next": f"add {slug!r} to [systems].enabled, give it a "
                            f"[masters.{slug}] url, then start its master"}
        name, blob = publish_service.package(slug, layout, meta)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(content=blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "X-Package-Slug": slug})


# -- Image card pictures (services/builder_assets.py) ---------------------------
#
# UPLOAD: the raw file as the request body (no multipart, no file name -- the
# name a browser sends never reaches the disk). Same gate as saving the layout
# (main.edit); the session wall and the cross-site refusal in
# routes/auth.require have already run. The body is counted WHILE it streams
# and refused the moment it passes the limit, so an oversized upload is never
# held in memory whole.
#
# SERVE: behind the session wall like every /api read (a viewer of this
# system's Dashboard may see its pictures), only by a 32-hex asset id, under
# the type its bytes say, with nothing allowed to run.

@router.post("/api/builder/assets", name="builder.asset_upload")
async def upload_asset(request: Request):
    perm.require(request, "main.edit")
    from core.services import builder_assets                   # noqa: PLC0415
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > builder_assets.MAX_BYTES:
        raise HTTPException(status_code=413, detail="the picture must be at most 2 MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > builder_assets.MAX_BYTES:
            raise HTTPException(status_code=413, detail="the picture must be at most 2 MB")
    slug = _slug(request)
    config_dir = request.app.state.config_dir
    try:
        # The saved layout, so an upload past the soft line can first sweep
        # pictures no card names (builder_assets.save, Pipi N-4).
        saved = builder_assets.save(
            slug, bytes(data), layout=lambda: builder_service.load(config_dir, slug))
    except builder_assets.QuotaError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **saved}


@router.get("/api/builder/assets/{asset_id}", name="builder.asset")
def get_asset(request: Request, asset_id: str):
    from core.services import builder_assets                   # noqa: PLC0415
    found = (builder_assets.read(_slug(request), asset_id)
             if builder_assets.is_asset_id(asset_id) else None)
    if not found:
        raise HTTPException(status_code=404, detail="no such picture")
    data, ctype = found
    return Response(content=data, media_type=ctype, headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
        "Content-Disposition": "inline",
        # The session wall answers no-store on every signed-in response,
        # which is stricter still; private is said here for the record.
        "Cache-Control": "private, max-age=0",
    })


# -- a table card's rows as CSV (services/table_export.py) --------------------------
#
# The download button at the top right of a table card. Behind the session
# wall like the page that shows the card (anyone who may read the Dashboard or
# Analytics may download what its tables hold); POST only because the body
# carries the range and the columns, and the cross-site refusal covers it.
# Everything in the body is checked by the service: the card must be a table
# on THIS system's saved layout, the columns are matched against that card's
# own, the dates are parsed strictly and the range is bounded.

@router.post("/api/builder/table-csv", name="builder.table_csv")
async def table_csv(request: Request):
    from core.services import table_export                    # noqa: PLC0415
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="bad request")
    st = request.app.state
    slug = _slug(request)
    card = table_export.find_card(builder_service.load(st.config_dir, slug), body.get("id"))
    if card is None:
        raise HTTPException(status_code=404, detail="no such table on this system")
    spec = {k: body.get(k) for k in ("range", "from", "to") if body.get(k) is not None}
    try:
        # In a thread: a whole table is minutes of reading, and on the event
        # loop it would hold every other request of this master for as long.
        made = await asyncio.to_thread(
            table_export.export, card, spec, body.get("columns"), slug,
            config_dir=st.config_dir, systems_dir=st.systems_dir)
    except table_export.ExportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return Response(content=made["text"], media_type="text/csv; charset=utf-8",
                    headers=table_export.response_headers(made))


@router.get("/api/builder/export", name="builder.export")
def export_json(request: Request):
    """The raw layout file, for committing to a deployment repo by hand."""
    layout = builder_service.load(request.app.state.config_dir, _slug(request))
    body = json.dumps(layout, indent=2)
    return Response(content=body, media_type="application/json",
                    headers={"Content-Disposition":
                             f'attachment; filename="{_slug(request)}-layout.json"'})
