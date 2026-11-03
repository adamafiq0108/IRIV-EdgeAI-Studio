# -*- coding: utf-8 -*-
"""
models.py -- bring-your-own-model API: upload weights, describe them, deploy.

Writes are admin-only. `GET /api/models/<name>/file` is the one endpoint a
DEVICE calls -- it pulls the weights its master told it to use, over the same
session-gated origin, so a model never travels by USB stick again.

Thin by contract: validation, storage and the manifest format live in
core/services/models.py; deploying is a v1.2 control call the control service
already knows how to make.
"""

import asyncio
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Query
from fastapi.responses import FileResponse, Response, StreamingResponse

from core.services import board_io as board_io_service
from core.services import builtin_models as builtin_service
from core.services import control as control_service
from core.services import camera_controls as camera_controls_service
from core.services import camera_sdk as camera_sdk_service
from core.services import class_lock
from core.services import detector_control as detector_control_service
from core.services import redact
from core.services import measurement as measurement_service
from core.services import footage_mail as footage_mail_service
from core.services import models as models_service
from core.services import audit as audit_service
from core.services import auth as auth_service
from core.services import log_catalog as catalog
from core.routes import perm

router = APIRouter()


def _require_admin(request):
    """MASTER or developer -- see auth.is_admin."""
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


logger = logging.getLogger(__name__)


def _may(request, cap):
    """perm.require as a yes/no, for deciding what a response may show."""
    try:
        perm.require(request, cap)
        return True
    except HTTPException:
        return False


def _cams_out(request, cams):
    """Camera entries as this caller may see them: a network camera's
    password only to an account that may change the camera (Pipi F4). Every
    other account -- an operator, a viewer, a site admin with camera.add
    alone -- reads it as redact.REDACTED, and a list sent back that way is
    restored on save (detector_control.unmask_source)."""
    if _may(request, "detection.camera.change"):
        return cams
    return detector_control_service.mask_cameras(cams)


def _raw_ok(request):
    """Whether this caller may SAVE an escape-hatch camera source (gst:,
    file:, a bare path, other protocols) -- master or developer, the same
    rule the snapshot preview has (security review F1)."""
    return auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir)


def _require_operator(request, slug, action):
    """Gate a per-system ACTION on being assigned that system -- the same rule
    the hub uses. Running the hardware is the operator's job; changing what
    the deployment IS stays admin-only."""
    user = getattr(request.state, "user", None)
    cfg = request.app.state.config_dir
    if auth_service.is_admin(user, cfg):
        return
    if not auth_service.allowed(user, slug, cfg):
        raise HTTPException(status_code=403,
                            detail=f"{action} needs access to this system")


def _models_home(request):
    """The package directory whose models/ folder this system installs into.
    None on the hub (no system), where the shared folder is still correct."""
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        return None
    from core.services import provision as provision_service   # noqa: PLC0415
    root = provision_service._pkg_root(systems_dir, system.slug)
    return os.path.join(str(root), system.slug)


def _system_context(request):
    """(systems_dir, slug) for the master serving this system, or a 400. The
    built-in catalogue is a master-side, single-system notion -- the hub has no
    system, so these endpoints only answer on a master."""
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        raise HTTPException(status_code=400,
                            detail="built-in models are a per-system view; open a system's dashboard")
    return systems_dir, system.slug


@router.get("/api/models", name="models.list")
def list_models(request: Request):
    # Once, not once per field: detect_arch shells out to lspci.
    detected = builtin_service.detect_arch()
    return {"ok": True,
            "models": models_service.listing(request.app.state.config_dir,
                                             _models_home(request)),
            "template": models_service.manifest_template(),
            "vocab": {"tasks": list(models_service.TASKS),
                      # What the FORM offers, narrowed to this board's own
                      # accelerator -- a HEF for another arch installs cleanly
                      # and then fails at load. The full vocabulary still
                      # validates (models_service.ARCHS), so a model imported
                      # from another box stays editable.
                      "archs": models_service.offered_archs(detected),
                      "all_archs": list(models_service.ARCHS),
                      "postprocess": list(models_service.POSTPROCESS),
                      "extensions": list(models_service.EXTENSIONS),
                      "coco_classes": list(models_service.COCO_CLASSES),
                      "detected_arch": detected,
                      "presets": [{"name": p["name"], "label": p["label"],
                                   "description": p["description"]}
                                  for p in models_service.PRESETS]},
            "editable": perm.editable(request, "models.")}


@router.get("/api/models/presets/{name}", name="models.preset")
def model_preset(request: Request, name: str):
    """A full manifest draft for a detection preset, labels included. The form
    loads it so an operator never hand-fills a known Model Zoo detector."""
    draft = models_service.preset(name)
    if not draft:
        raise HTTPException(status_code=404, detail="no such preset")
    return {"ok": True, "preset": draft}


@router.get("/api/detector/local_models", name="models.local_list")
def local_models_list(request: Request):
    """Models copied onto the board by hand (default /home/pi/model). Listing
    only -- the dashboard never writes into that folder."""
    _system_context(request)
    from core.services import local_models as local_service
    return {"ok": True, "editable": perm.editable(request, "models."),
            **local_service.listing()}


@router.post("/api/detector/local_models/select", name="models.local_select")
async def local_models_select(request: Request):
    """Run one of the drop-folder models on this system."""
    perm.require(request, "models.select")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    from core.services import local_models as local_service
    try:
        result = local_service.select(systems_dir, slug, str(body.get("name") or ""))
    except (ValueError, TypeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}




def _no_models_guard(request):
    """After a model delete: if this system has NOTHING left to run, stop its
    detection and say so.

    Detection without a model is a camera burning CPU to detect nothing --
    running it is a lie the dashboard tells ("AI Running") with no AI in it.
    So the delete that removed the last model also stops the app, and the
    response carries the fact so the page can tell the operator in words:
    "No AI Models Detected". Best effort on every read: a guard that can
    fail the DELETE it guards would be worse than no guard.
    """
    try:
        systems_dir, slug = _system_context(request)
    except HTTPException:
        return {}                       # hub-side call: no system to stop
    remaining = 0
    try:
        from core.services import local_models as local_service
        remaining += len(local_service.listing().get("models") or [])
    except Exception:                                       # noqa: BLE001
        pass
    try:
        rows = models_service.listing(request.app.state.config_dir,
                                      _models_home(request))
        remaining += sum(1 for m in rows if m.get("weights_present"))
    except Exception:                                       # noqa: BLE001
        pass
    try:
        rows = builtin_service.listing(systems_dir, slug,
                                       request.app.state.config_dir)
        remaining += sum(1 for m in (rows.get("models") or [])
                         if m.get("downloaded"))
    except Exception:                                       # noqa: BLE001
        pass
    if remaining:
        return {}
    stopped = False
    try:
        if detector_control_service.ai_running(systems_dir, slug):
            detector_control_service.stop(systems_dir, slug)
            stopped = True
    except Exception:                                       # noqa: BLE001
        pass
    return {"no_models": True, "detection_stopped": stopped}


@router.post("/api/detector/local_models/upload", name="models.local_upload")
async def local_models_upload(request: Request, hef: UploadFile = File(...),
                              manifest: UploadFile = File(...),
                              replace: str = Form("")):
    """Put a compiled model into the drop folder from the browser: a .hef
    and a .json of the same name, as <root>/<name>/<name>.hef + .json. Admin
    (models.manage). The pair is checked in full before a byte lands
    (local_models.check_pair): same stem, a JSON that parses, a class list,
    a `target` that matches the chip in this box.

    The HEF is streamed to a temp file BESIDE its destination and renamed
    in, never read whole into memory: these are hundreds of megabytes on a
    board with a few gigabytes of RAM, and the existing per-system upload
    reading the blob in one go is a habit not to repeat here.
    """
    perm.require(request, "models.manage")
    from core.services import local_models as local_service
    text = (await manifest.read(local_service.MAX_MANIFEST_BYTES + 1)).decode("utf-8", "replace")
    try:
        _spec, stem = local_service.check_pair(
            hef.filename or "", manifest.filename or "", text,
            arch=builtin_service.detect_arch())
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        tmp_dir = local_service.upload_tmp_dir()
    except OSError as e:
        # Was a 400, which blamed the operator for a board that had run out of
        # disk. Same fault as media.py's save and panel.py's shutdown, and all
        # three answered differently until the catalog gave them one status.
        raise catalog.http_error("MDL_STORE_UNWRITABLE", cause=e,
                                 filename=hef.filename or "") from e
    fd, tmp_path = tempfile.mkstemp(prefix=f"{stem}.", suffix=".hef.part", dir=tmp_dir)
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await hef.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > local_service.MAX_HEF_BYTES:
                    raise HTTPException(status_code=413,
                                        detail="that .hef is larger than any model "
                                               "this hardware runs")
                out.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="the .hef is empty")
        row = await asyncio.to_thread(local_service.install, stem, tmp_path, text,
                                      str(replace).lower() in ("1", "true", "yes"))
    except HTTPException:
        _rm_quiet(tmp_path)
        raise
    except (ValueError, OSError) as e:
        _rm_quiet(tmp_path)
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "model": row, **local_service.listing()}


def _rm_quiet(path):
    try:
        os.remove(path)
    except OSError:
        pass


@router.get("/api/detector/local_models/guide", name="models.local_guide")
def local_models_guide(request: Request):
    """The step-by-step for preparing a custom model folder and uploading it,
    shown in a dialog on the Models page. Ships with the product
    (core/static/guides), so the guide and the rules it describes cannot
    drift apart across releases."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "static", "guides", "custom-model-upload.html")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="the guide is not in this release")
    # An HTML fragment, rendered at development time from the .md beside it
    # (scripts/dev/build_guides.py); the page styles it (.guide-body).
    with open(path, encoding="utf-8") as f:
        return {"ok": True, "html": f.read(), "title": "Custom model: prepare and upload"}


@router.post("/api/detector/local_models/delete", name="models.local_delete")
async def local_models_delete(request: Request):
    """Delete one drop-folder model's HEF from disk. Admin only.

    The model currently running is refused by the service: removing the
    weights under a live detector breaks the system at its next restart with
    nothing on screen to explain why.
    """
    perm.require(request, "models.manage")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    from core.services import local_models as local_service
    # What is running right now, so the service can refuse those. Read here
    # rather than trusted from the browser: the caller must not be able to
    # claim nothing is running in order to delete the model that is.
    try:
        det = (detector_control_service.status(systems_dir, slug)
               .get("detector") or {})
        running = [m.get("model") for m in (det.get("models") or [])]
        if det.get("model"):
            running.append(det["model"])
    except Exception:                                       # noqa: BLE001
        running = []
    try:
        path = local_service.delete(str(body.get("name") or ""),
                                    in_use=[r for r in running if r])
    except (ValueError, TypeError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "deleted": path, **_no_models_guard(request)}


@router.get("/api/detector/builtin", name="models.builtin_list")
def builtin_list(request: Request):
    """The package's built-in detection models: what's downloaded, and which one
    the local detection app is set to run right now."""
    systems_dir, slug = _system_context(request)
    return {"ok": True, "editable": perm.editable(request, "models."),
            **builtin_service.listing(systems_dir, slug,
                                      config_dir=request.app.state.config_dir)}


@router.post("/api/detector/builtin/select", name="models.builtin_select")
async def builtin_select(request: Request):
    """Point the local app at a built-in model. Writes the choice to the
    package's config.local.toml; the running app rebuilds its detector from it
    within a few seconds (systems/<slug>/app/run.py watches the file)."""
    perm.require(request, "models.select")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        result = builtin_service.select(systems_dir, slug, str(body.get("name") or ""),
                                        config_dir=request.app.state.config_dir)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/detector/builtin/select_many", name="models.builtin_select_many")
async def builtin_select_many(request: Request):
    """Run SEVERAL models at once on this system's one camera. Body:
    {"names": [..]} -- order matters (the first is what a non-multi engine
    build would fall back to). One name behaves exactly like a single select."""
    perm.require(request, "models.select")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    names = body.get("names")
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names must be a list")
    # Running NOTHING is a legitimate state -- a system that is not detecting
    # yet, or one being reconfigured -- but not while detection is live. A
    # detector with no model loaded sees nothing, so every rule on the system
    # reads zero and every card sits at zero, with nothing on any page saying
    # why. Stop detection first and the choice is plainly a choice.
    #
    # Checked HERE, not in the browser: the caller must not be able to claim
    # detection is stopped in order to strip the last model out from under a
    # running one.
    if not names:
        try:
            live = detector_control_service.ai_running(systems_dir, slug)
        except Exception:                                   # noqa: BLE001
            live = False
        if live:
            raise HTTPException(
                status_code=409,
                detail="Stop detection first - with no model running, this "
                       "system would detect nothing and every rule on it "
                       "would read zero.")
    try:
        result = builtin_service.select_many(systems_dir, slug, names,
                                             config_dir=request.app.state.config_dir)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/detector/builtin/fetch", name="models.builtin_fetch")
async def builtin_fetch(request: Request):
    """Download a catalogue model's HEF onto this box (background). The page
    polls the status endpoint below; the listing's `downloaded` flag flips when
    the file lands."""
    perm.require(request, "models.manage")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        status = builtin_service.start_fetch(systems_dir, slug,
                                             str(body.get("name") or ""))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **status}


@router.get("/api/detector/builtin/fetch", name="models.builtin_fetch_status")
def builtin_fetch_status(request: Request):
    """Progress of every HEF download this master started for its system."""
    _, slug = _system_context(request)
    return {"ok": True, "fetches": builtin_service.fetch_status(slug)}


@router.get("/api/detector/control", name="detector.control_status")
@perm.shared("detector_control")
def detector_control_status(request: Request):
    """The Detection page's whole state: camera, model, zones, app running."""
    systems_dir, slug = _system_context(request)
    # Masked unless the account may re-point a camera: the edit form keeps
    # saved credentials by rebuilding them from the source it is given
    # (Detection.jsx pickedSource), and nothing else needs the password.
    reveal = _may(request, "detection.camera.change")
    st = detector_control_service.status(systems_dir, slug, reveal=reveal)
    if not reveal:                      # status() masks already; kept as the route's own word
        st = dict(st, cameras=detector_control_service.mask_cameras(st.get("cameras")),
                  camera=detector_control_service.mask_camera(st.get("camera")))
    return {"ok": True, "editable": perm.editable(request, "detection."),
            "caps": perm.held(request), **st}


@router.get("/api/detector/control/tracker", name="detector.get_tracker")
def detector_get_tracker(request: Request):
    """The tracking knobs, plus the range each one is allowed. The UI renders
    its inputs from this rather than hardcoding bounds that could drift from
    what the server actually enforces."""
    systems_dir, slug = _system_context(request)
    return {"ok": True,
            "editable": perm.editable(request, "models."),
            "tracker": detector_control_service.tracker_params(systems_dir, slug),
            "fields": detector_control_service.TRACKER_FIELDS}


@router.post("/api/detector/control/tracker", name="detector.set_tracker")
async def detector_set_tracker(request: Request):
    """Tune tracking. Applied by the running app within a poll, in place --
    live track ids survive, so the counts built on them do not restart."""
    perm.require(request, "models.select")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="expected an object")
    try:
        tracker = detector_control_service.set_tracker_params(
            systems_dir, slug, body)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "tracker": tracker}


@router.get("/api/detector/control/params", name="detector.get_params")
def detector_get_params(request: Request):
    """What the running model is filtering on, plus every class it CAN emit --
    the two things an operator tunes after choosing a model."""
    systems_dir, slug = _system_context(request)
    st = detector_control_service.status(systems_dir, slug)
    return {"ok": True,
            "editable": perm.editable(request, "models."),
            "model": (st.get("detector") or {}).get("model"),
            "models_dir": (st.get("detector") or {}).get("models_dir"),
            "confidence": (st.get("detector") or {}).get("confidence"),
            "classes": (st.get("detector") or {}).get("classes") or [],
            "models": (st.get("detector") or {}).get("models") or [],
            "labels": detector_control_service.model_labels(systems_dir, slug),
            "labels_by_model": detector_control_service.model_labels_by_model(
                systems_dir, slug),
            **class_lock.flags(systems_dir, slug)}      # classes_locked


@router.post("/api/detector/control/params", name="detector.set_params")
async def detector_set_params(request: Request):
    perm.require(request, "models.select")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    class_lock.require_stopped(systems_dir, slug, body)  # 409 while detecting
    try:
        params = detector_control_service.set_detector_params(
            systems_dir, slug,
            classes=body.get("classes"), confidence=body.get("confidence"),
            # Which model this tuning belongs to. Omitted = the shared keys,
            # which is what a single-model system wants.
            model=body.get("model"))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # The workflow's Class Filters follow. A filter naming a class this system
    # no longer detects can never match, so the rule behind it reads zero for
    # ever with nothing on the workflow page to explain it -- the setting is
    # changed here and its consequence lands there. Best-effort: a system with
    # no workflow yet, or an unreadable store, must not fail the save the
    # operator actually asked for.
    dropped = []
    if body.get("classes") is not None:
        try:
            from core.services import workflow as workflow_service
            dropped = workflow_service.sync_class_filters(
                slug, params.get("classes") or body.get("classes"))
        except Exception:                                   # noqa: BLE001
            dropped = []
    return {"ok": True, **params, "workflow_classes_dropped": dropped}


@router.get("/api/detector/control/camera", name="detector.cameras_list")
def detector_get_cameras(request: Request):
    """Every camera this system captures. A one-camera system answers with a
    single entry, so the page has one shape to render either way."""
    systems_dir, slug = _system_context(request)
    budget = detector_control_service.camera_budget(
        systems_dir, slug, request.app.state.config_dir)
    return {"ok": True,
            "cameras": _cams_out(request,
                                 detector_control_service.cameras(systems_dir, slug)),
            "max_cameras": detector_control_service.MAX_CAMERAS,
            # The cap belongs to the accelerator, so the page has to know what
            # the OTHER systems are already using before it offers to add one.
            "budget": budget}


@router.post("/api/detector/control/camera", name="detector.set_camera")
async def detector_set_camera(request: Request):
    """Point ONE camera at a source. `camera_id` is optional: without it this
    edits the first camera, which is the single-camera body older clients send."""
    systems_dir, slug = _system_context(request)
    body = await request.json()
    # Re-pointing an EXISTING camera is a change; naming an id that is not on
    # the system yet is how this route creates one, and creating is the other
    # capability. Told apart here rather than in the service, because the
    # service has no caller to refuse.
    existing = {c["id"] for c in detector_control_service.cameras(systems_dir, slug)}
    cid = str(body.get("camera_id") or "")
    creates = (cid not in existing) if cid else not existing
    perm.require(request, "detection.camera.add" if creates
                 else "detection.camera.change")
    try:
        cam = detector_control_service.set_camera(
            systems_dir, slug, body.get("source"),
            width=body.get("width"), height=body.get("height"), fps=body.get("fps"),
            label=body.get("label"), camera_id=body.get("camera_id"),
            kind=body.get("kind"),
            # GigE exposure/gain. Only stored on a GigE camera (see
            # _validate_camera) -- on any other kind they are aravissrc
            # properties with nothing to apply them to.
            tuning={k: body.get(k) for k in ("exposure_auto", "exposure_us",
                                             "gain_auto", "gain_db",
                                             "binning")},
            allow_raw=_raw_ok(request))
    except detector_control_service.RawSourceRefused as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "camera": _cams_out(request, [cam])[0],
            "cameras": _cams_out(request,
                                 detector_control_service.cameras(systems_dir, slug))}


@router.post("/api/detector/control/cameras", name="detector.set_cameras")
async def detector_set_cameras(request: Request):
    """Replace the whole camera list. Whole-list writes only: config merging
    replaces a list wholesale, so a partial write drops what it omits."""
    systems_dir, slug = _system_context(request)
    body = await request.json()
    # A whole-list write can be any of the four camera capabilities at once,
    # so it is judged by what it CHANGES rather than by the route it arrived
    # at -- otherwise an account allowed to re-point a camera could delete
    # three by sending the list without them.
    entries = [e for e in (body.get("cameras") or []) if isinstance(e, dict)]
    before = {c["id"]: c for c in detector_control_service.cameras(systems_dir, slug)}
    after = {str(e.get("id") or ""): e for e in entries}
    for cap, changed in (
            ("detection.camera.add", set(after) - set(before)),
            ("detection.camera.remove", set(before) - set(after)),
            ("detection.camera.activate",
             {i for i in set(before) & set(after)
              if bool(before[i].get("enabled", True))
              != bool(after[i].get("enabled", True))}),
            ("detection.camera.change", set(before) & set(after))):
        if changed:
            perm.require(request, cap)
    try:
        cams = detector_control_service.set_cameras(
            systems_dir, slug, body.get("cameras"),
            config_dir=request.app.state.config_dir, allow_raw=_raw_ok(request))
    except detector_control_service.RawSourceRefused as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "cameras": _cams_out(request, cams),
            "budget": detector_control_service.camera_budget(
                systems_dir, slug, request.app.state.config_dir)}


@router.post("/api/detector/control/cameras/add", name="detector.add_camera")
async def detector_add_camera(request: Request):
    """Append one camera. The body IS the camera ({source, name?, width?,
    height?, fps?, enabled?}); the id is minted from the name when absent."""
    perm.require(request, "detection.camera.add")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        cams = detector_control_service.add_camera(
            systems_dir, slug, body, config_dir=request.app.state.config_dir,
            allow_raw=_raw_ok(request))
    except detector_control_service.RawSourceRefused as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "cameras": _cams_out(request, cams),
            "budget": detector_control_service.camera_budget(
                systems_dir, slug, request.app.state.config_dir)}


@router.get("/api/detector/control/cameras/controls",
            name="detector.camera_controls")
def detector_camera_controls(request: Request, camera_id: str = Query("")):
    """What this camera lets you adjust, asked of the camera itself.

    Per CAMERA rather than per source string, because the answer includes what
    this system has already chosen for it -- and because a camera that cannot
    be read right now still has settings worth showing.
    """
    systems_dir, slug = _system_context(request)
    _require_operator(request, slug, "camera settings")
    cams = detector_control_service.cameras(systems_dir, slug)
    cam = next((c for c in cams if c.get("id") == camera_id), None) if camera_id else (cams[0] if cams else None)
    if cam is None:
        raise HTTPException(status_code=404, detail="no such camera")
    found = camera_controls_service.controls(
        cam.get("source"), cam.get("kind"), saved=cam.get("controls"))
    return {"ok": True, "camera": _cams_out(request, [cam])[0], **found,
            # A camera that needs a vendor SDK says so here, installed or not,
            # so the panel can offer to fetch it instead of showing an empty
            # list of controls for a camera that cannot even open.
            # The detection app runs on this same interpreter (app/runtime
            # starts it with sys.executable), so what this process can import
            # is what the camera will be able to open with.
            #
            # `may_install` because installing is ADMIN, while adjusting a
            # camera is an operator capability -- without it the panel draws an
            # Install button for someone the route will refuse, which is a
            # support call rather than a permission.
            "sdk": {**camera_sdk_service.status(cam.get("kind")),
                    "may_install": auth_service.is_admin(
                        getattr(request.state, "user", None),
                        request.app.state.config_dir)}}


@router.post("/api/detector/control/cameras/controls",
             name="detector.set_camera_controls")
async def detector_set_camera_controls(request: Request):
    """Adjust a camera. {camera_id, controls: {name: value | null}}.

    Applied to the device NOW where that is possible, and SAVED either way --
    a value that only lives in the driver is gone the next time the camera is
    replugged or the app reopens it.
    """
    perm.require(request, "detection.camera.change")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    values = body.get("controls")
    if not isinstance(values, dict) or not values:
        raise HTTPException(status_code=400, detail="no settings to change")
    # Each control is a v4l2-ctl process, run serially. Refused here, before
    # anything is spawned: a body naming thousands of them would hold a
    # threadpool worker for hours, and enough of those take every worker the
    # master has. The panel sends one row at a time.
    if len(values) > camera_controls_service.MAX_CONTROLS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail="at most {} settings at a time".format(
                camera_controls_service.MAX_CONTROLS_PER_REQUEST))
    cams = detector_control_service.cameras(systems_dir, slug)
    cid = str(body.get("camera_id") or (cams[0]["id"] if cams else ""))
    cam = next((c for c in cams if c.get("id") == cid), None)
    if cam is None:
        raise HTTPException(status_code=404, detail="no such camera")
    live = {k: v for k, v in values.items() if v is not None}
    applied, problem = camera_controls_service.apply_live(
        cam.get("source"), cam.get("kind"), live)
    try:
        saved = detector_control_service.set_camera_controls(
            systems_dir, slug, cid, values,
            config_dir=request.app.state.config_dir)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    found = camera_controls_service.controls(
        saved.get("source"), saved.get("kind"), saved=saved.get("controls"))
    return {"ok": True, "camera": saved, "applied": applied,
            "problem": problem, **found}


@router.post("/api/detector/control/cameras/sdk", name="detector.camera_sdk")
async def detector_camera_sdk(request: Request):
    """Install the vendor SDK a camera needs. Master or developer.

    The package and its VERSION come from an allowlist compiled into the box
    (camera_sdk.KNOWN), never from this request: "fetch the SDK from the
    internet" is otherwise a web page that installs arbitrary code on an
    appliance. The body names a camera; everything about what gets installed
    is already decided.
    """
    _require_admin(request)
    systems_dir, slug = _system_context(request)
    body = await request.json()
    cams = detector_control_service.cameras(systems_dir, slug)
    cid = str(body.get("camera_id") or (cams[0]["id"] if cams else ""))
    cam = next((c for c in cams if c.get("id") == cid), None)
    if cam is None:
        raise HTTPException(status_code=404, detail="no such camera")
    kind = cam.get("kind")
    want = camera_sdk_service.wanted_for(kind)
    if not want:
        raise HTTPException(status_code=400,
                            detail="this camera needs no SDK")
    ok, message = await asyncio.to_thread(camera_sdk_service.install, kind)
    audit_service.record("camera.sdk",
                         username=getattr(request.state, "user", None) or "",
                         detail="{} {} for {}: {}".format(
                             want["package"], want["version"], cid,
                             "installed" if ok else "failed"),
                         config_dir=request.app.state.config_dir)
    if not ok:
        raise HTTPException(status_code=502, detail=message)
    return {"ok": True, "message": message,
            "sdk": camera_sdk_service.status(kind)}


# -- measuring objects with a depth camera --------------------------------------
#
# Four doors. STATE says where the setup stands (floor calibrated? app
# answering depth? which profiles). CALIBRATE fits the floor from the empty
# scene and saves it. RUN measures once, now, with a tool the operator chose
# -- the try-it-and-see behind every button on the panel. PROFILES saves
# which numbers each kind of object carries at detection time.
#
# Reading and trying are the operator's; saving a calibration or a profile
# changes what the system measures from then on, so those take the
# camera-change capability like every other camera setting.

@router.get("/api/detector/control/cameras/measure", name="detector.measure_state")
def detector_measure_state(request: Request, camera_id: str = Query("")):
    systems_dir, slug = _system_context(request)
    _require_operator(request, slug, "measurement")
    try:
        return {"ok": True, **measurement_service.state(systems_dir, slug, camera_id)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/detector/control/cameras/measure/calibrate",
             name="detector.measure_calibrate")
async def detector_measure_calibrate(request: Request):
    """Fit the floor from the scene AS IT IS NOW. The scene must be empty."""
    perm.require(request, "detection.camera.change")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        # The fetch and the fit block; off the event loop.
        result = await asyncio.to_thread(
            measurement_service.calibrate_floor, systems_dir, slug,
            body.get("camera_id"), request.app.state.config_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/detector/control/cameras/measure/clear",
             name="detector.measure_clear")
async def detector_measure_clear(request: Request):
    perm.require(request, "detection.camera.change")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        measurement_service.clear_floor(systems_dir, slug, body.get("camera_id"),
                                        request.app.state.config_dir)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.post("/api/detector/control/cameras/measure/run",
             name="detector.measure_run")
async def detector_measure_run(request: Request):
    """Measure once, now: {camera_id, tool, points?: [[u, v]...], box?}.

    Points and box are NORMALISED (0..1) picture coordinates. The operator
    tapped on a preview that is not the depth grid's size, and a tap carried
    as pixels of the wrong picture lands on the wrong quarter of the scene.
    """
    systems_dir, slug = _system_context(request)
    _require_operator(request, slug, "measurement")
    body = await request.json()
    points = body.get("points")
    if points is not None:
        if not isinstance(points, list) or len(points) > 32:
            raise HTTPException(status_code=400, detail="at most 32 points")
        try:
            points = [(float(p[0]), float(p[1])) for p in points]
        except (TypeError, ValueError, IndexError):
            raise HTTPException(status_code=400, detail="points are [u, v] pairs in 0..1")
        if not all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in points):
            raise HTTPException(status_code=400, detail="points are [u, v] pairs in 0..1")
    box = body.get("box")
    if box is not None:
        try:
            box = [float(v) for v in box][:4]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="box is [x1, y1, x2, y2] in 0..1")
        if len(box) != 4 or not all(0.0 <= v <= 1.0 for v in box):
            raise HTTPException(status_code=400, detail="box is [x1, y1, x2, y2] in 0..1")
    try:
        result = await asyncio.to_thread(
            measurement_service.run, systems_dir, slug, body.get("camera_id"),
            body.get("tool"), points, box, str(body.get("mode") or ""),
            "3d" if str(body.get("dims") or "").lower() == "3d" else "2d")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/detector/control/cameras/measure/profiles",
             name="detector.measure_profiles")
async def detector_measure_profiles(request: Request):
    """Which numbers each kind of object carries: {camera_id, profiles: [...]}."""
    perm.require(request, "detection.camera.change")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        profiles = measurement_service.set_profiles(
            systems_dir, slug, body.get("camera_id"), body.get("profiles"),
            request.app.state.config_dir)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "profiles": profiles}


@router.get("/api/detector/control/cameras/resolutions",
            name="detector.camera_resolutions")
def detector_camera_resolutions(request: Request, source: str = Query(...),
                                kind: str = Query("")):
    """What sizes this camera can be asked for, probed from the device itself.

    Best-effort by design: `probed` False means the answer is the generic
    fallback list, and the page has to say so rather than let an operator pick
    4K on a 720p camera and wonder why nothing looks sharper.
    """
    _system_context(request)
    return {"ok": True,
            **detector_control_service.camera_resolutions(source, kind)}


@router.get("/api/detector/control/cameras/framerates",
            name="detector.camera_frame_rates")
def detector_camera_frame_rates(request: Request, source: str = Query(...),
                                kind: str = Query(""),
                                width: int = Query(0), height: int = Query(0)):
    """What rates this camera can be asked for, at the size it is set to.

    Size-aware because a UVC camera's fastest rate is a property of the MODE:
    the same sensor offers 60fps at 640x480 and a fraction of that at its full
    frame, so a rate list that ignores the size is a list of rates the camera
    may refuse. `probed` False means generic, exactly as for resolutions.
    """
    _system_context(request)
    return {"ok": True,
            **detector_control_service.camera_frame_rates(
                source, kind, width, height)}


@router.post("/api/detector/control/cameras/enable", name="detector.enable_camera")
async def detector_enable_camera(request: Request):
    """Switch one camera on/off. Body {"camera_id", "enabled"}. Off keeps its
    settings; on is what the running caps guard (6 per box)."""
    perm.require(request, "detection.camera.activate")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        cams = detector_control_service.enable_camera(
            systems_dir, slug, body.get("camera_id"), body.get("enabled"),
            config_dir=request.app.state.config_dir)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "cameras": _cams_out(request, cams),
            "budget": detector_control_service.camera_budget(
                systems_dir, slug, request.app.state.config_dir)}


@router.post("/api/detector/control/cameras/remove", name="detector.remove_camera")
async def detector_remove_camera(request: Request):
    """Drop one camera by id, and every zone drawn on it.

    Body {"camera_id", "dry_run"?}. A dry run changes nothing and answers
    what the removal WOULD take -- the zones (detector_control.zones_on_camera
    states the rule) and the canvas nodes bound to them -- so the confirm can
    name them before the operator agrees.

    Deleting a zone is a capability of its own (detection.zone.delete), and
    removing the camera must not be a way around it: when the camera has
    zones, both are required.
    """
    perm.require(request, "detection.camera.remove")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    from core.services import workflow as workflow_service
    cid = body.get("camera_id")
    if body.get("dry_run"):
        try:
            if not any(c["id"] == str(cid or "") for c in
                       detector_control_service.cameras(systems_dir, slug)):
                raise ValueError(f"no camera {cid!r}")
            zones = detector_control_service.zones_on_camera(systems_dir, slug, cid)
        except (ValueError, TypeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        nodes = workflow_service.forget_zones(
            slug, [z["id"] for z in zones], dry_run=True)
        return {"ok": True, "dry_run": True, "zones": zones, "nodes": nodes}
    pkg = detector_control_service._pkg_dir(systems_dir, slug)
    try:
        # One hold across the look and the removal, so a zone drawn in
        # between cannot be deleted under a permission check that never saw it.
        with detector_control_service.pkg_config_lock(pkg):
            if detector_control_service.zones_on_camera(systems_dir, slug, cid):
                perm.require(request, "detection.zone.delete")
            cams, zones = detector_control_service.remove_camera(
                systems_dir, slug, cid, with_zones=True)
            if zones:
                # The rules those zones' actions compiled into (z_<zone>_*)
                # are regenerated from the zones that are left, exactly as a
                # Zones page save does.
                from core.services import functions as functions_service
                functions_service.apply_zone_actions(systems_dir, slug)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    nodes = []
    if zones:
        # Canvas nodes bound to them go unbound (they ask for a new zone),
        # and the canvas is recompiled so the rules it had compiled against
        # those zones stop running too. Best effort: the camera and its zones
        # are already gone, and a canvas that does not compile for some other
        # reason keeps its last applied rules exactly as a failed save would.
        try:
            nodes = workflow_service.forget_zones(slug, [z["id"] for z in zones])
            if nodes:
                from core.routes import workflow as wf_routes
                store, _ = wf_routes._get_store(systems_dir, slug)
                if store and store.get("workflows"):
                    wf_routes._recompile(systems_dir, request.app.state.config_dir,
                                         slug, store)
        except Exception:                                      # noqa: BLE001
            logger.exception("%s: canvas not updated after removing camera %r",
                             slug, cid)
    # Masked like every other camera list this page reads (_cams_out): the
    # removal answer used to hand the full sources, passwords included, to
    # an account allowed to remove a camera but not to change one.
    return {"ok": True, "cameras": _cams_out(request, cams),
            "zones_removed": zones, "nodes": nodes,
            "budget": detector_control_service.camera_budget(
                systems_dir, slug, request.app.state.config_dir)}


@router.post("/api/detector/control/zones", name="detector.set_zones")
async def detector_set_zones(request: Request):
    systems_dir, slug = _system_context(request)
    body = await request.json()
    # Same whole-list rule as the cameras above: the page sends every zone on
    # every save, so DRAWING one and DELETING one arrive at this route
    # identically and are told apart by the ids.
    zones = [z for z in (body.get("zones") or []) if isinstance(z, dict)]
    before = {z["id"] for z in detector_control_service._zone_list(
        detector_control_service._merged(
            detector_control_service._pkg_dir(systems_dir, slug)))}
    after = {str(z.get("id") or "") for z in zones}
    # An entry with no id is a zone the SERVER is about to mint one for, which
    # is an addition however it was written -- so it counts as one rather than
    # slipping through the id arithmetic.
    for cap, changed in (("detection.zone.add", after - before),
                         ("detection.zone.delete", before - after),
                         ("detection.zone.edit", before & after)):
        if changed:
            perm.require(request, cap)
    try:
        zones = detector_control_service.set_zones(systems_dir, slug, body.get("zones") or [])
        # Zone default actions (count / violation / cycle time) compile into
        # [[functions]] on every save, so the values exist the moment the
        # operator ticks the boxes.
        from core.services import functions as functions_service
        functions_service.apply_zone_actions(systems_dir, slug)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    # A save ends the edit: whatever the editor was hiding, the app should be
    # drawing again, and it should be drawing what was just saved.
    detector_control_service.hide_zone_overlay(systems_dir, slug, 0)
    return {"ok": True, "zones": zones}


@router.post("/api/detector/control/zones/editing", name="detector.zones_editing")
async def detector_zones_editing(request: Request):
    """Hold the app's zone outlines OFF the feed while a zone is being dragged.

    The app burns zone outlines into the frame, so an operator moving a shape
    sees two of them: the browser's unsaved one and the app's saved one. This
    takes the app's copy off the picture for a few seconds at a time, refreshed
    by the editor while the work is unsaved. Nothing is written to config and
    no zone changes -- if the operator discards, the outlines simply come back.
    """
    perm.require(request, "detection.zone.edit")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    seconds = 0 if not body.get("editing") else body.get("seconds")
    cameras = body.get("cameras") or []
    try:
        until = detector_control_service.hide_zone_overlay(
            systems_dir, slug, seconds, cameras)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "until": until}


# ---- dataset capture (training data on the NVMe) --------------------------

@router.get("/api/detector/dataset", name="detector.dataset")
def detector_dataset(request: Request):
    """What the Dataset pages draw from. `cameras` (ids and names only, never
    a source) lets a system with more than one camera offer a choice;
    `camera` is the one it last captured from, else the default -- the first
    running camera, which is what a request naming none is served from."""
    systems_dir, slug = _system_context(request)
    from core.services import dataset_capture                  # noqa: PLC0415
    try:
        cams = dataset_capture.dataset_cameras(systems_dir, slug)
        chosen = dataset_capture.saved_camera(systems_dir, slug, cams)
    except (OSError, ValueError, TypeError):
        cams, chosen = [], ""
    return {"ok": True, **detector_control_service.dataset_list(slug),
            "max_clip_s": detector_control_service.MAX_DATASET_CLIP_S,
            "max_session_s": detector_control_service.MAX_DATASET_SESSION_S,
            "cameras": cams, "camera": chosen,
            "default_camera": dataset_capture.default_camera(cams)}


@router.post("/api/detector/dataset/snap", name="detector.dataset_snap")
async def detector_dataset_snap(request: Request):
    perm.require(request, "dataset.capture")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        result = await asyncio.to_thread(
            detector_control_service.dataset_snap, systems_dir, slug,
            body.get("name"), body.get("camera"))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/detector/dataset/record", name="detector.dataset_record")
async def detector_dataset_record(request: Request):
    """Start a dataset recording. `duration_s` records exactly that long and
    stops itself; omitting it (or 0) starts a START/STOP session the operator
    ends with the endpoint below -- how long a training clip needs to be is
    their call, not a number this page can guess."""
    perm.require(request, "dataset.capture")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        result = await asyncio.to_thread(
            detector_control_service.dataset_record, systems_dir, slug,
            body.get("name"), body.get("duration_s"), body.get("camera"))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@router.post("/api/detector/dataset/record/stop",
             name="detector.dataset_record_stop")
def detector_dataset_record_stop(request: Request):
    """End the running recording and KEEP what it captured."""
    perm.require(request, "dataset.capture")
    _, slug = _system_context(request)
    return {"ok": True, **detector_control_service.dataset_record_stop(slug)}


@router.post("/api/detector/dataset/delete", name="detector.dataset_delete")
async def detector_dataset_delete(request: Request):
    perm.require(request, "dataset.manage")
    _, slug = _system_context(request)
    body = await request.json()
    kind = str(body.get("kind") or "")
    if kind not in ("image", "clip"):
        raise HTTPException(status_code=400, detail="kind must be image or clip")
    removed = detector_control_service.dataset_delete(
        kind, body.get("names") or [], slug)
    return {"ok": True, "removed": removed}


@router.post("/api/detector/dataset/export", name="detector.dataset_export")
async def detector_dataset_export(request: Request):
    """A ZIP of the selected dataset files -- how a labelling session leaves
    the board."""
    # Bulk export of captured imagery off the customer's premises. Its
    # siblings (snap / record / delete) are all admin; this one was not.
    perm.require(request, "dataset.manage")
    _, slug = _system_context(request)
    body = await request.json()
    kind = str(body.get("kind") or "")
    if kind not in ("image", "clip"):
        raise HTTPException(status_code=400, detail="kind must be image or clip")
    names = body.get("names") or []
    if not names:
        raise HTTPException(status_code=400, detail="nothing selected")
    blob = await asyncio.to_thread(detector_control_service.dataset_zip,
                                   kind, names, slug)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Response(content=blob, media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="dataset_{kind}_{stamp}.zip"'})


@router.get("/api/detector/dataset/{kind}/{name}", name="detector.dataset_file")
def detector_dataset_file(kind: str, name: str, request: Request):
    from fastapi.responses import FileResponse
    _, slug = _system_context(request)
    path = detector_control_service.dataset_path(kind, name, slug)
    if not path:
        raise HTTPException(status_code=404, detail="no such dataset file")
    media = "image/jpeg" if kind == "image" else "video/mp4"
    return FileResponse(path, media_type=media, filename=name)


# ---- local footage (standalone: the app's own snapshots) ------------------

@router.get("/api/detector/snapshots", name="detector.snapshots")
def detector_snapshots(request: Request, limit: int = Query(0)):
    """Newest first, ALL of them unless a limit is asked for (0 = no limit):
    the page filters by date client-side, and a view that silently stopped at
    some count would under-report, and Select All would then delete less than
    it appeared to. How many photos there may be is the drive's say, not this
    list's."""
    systems_dir, slug = _system_context(request)
    return {"ok": True,
            # What the viewer may DO with these, so Delete and Export are
            # drawn from the same answer that enforces them.
            "caps": perm.held(request),
            "snapshots": detector_control_service.list_snapshots(
                systems_dir, slug, limit=max(0, limit))}


@router.post("/api/detector/snapshots/delete", name="detector.snapshots_delete")
async def detector_snapshots_delete(request: Request):
    """Bulk delete. Footage accumulates fast, so clearing a day (or the lot) is
    one action rather than one click per image."""
    perm.require(request, "footage.delete")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    ids = body.get("ids")
    if ids is None:
        raise HTTPException(status_code=400, detail="ids is required")
    removed = await asyncio.to_thread(
        detector_control_service.delete_snapshots, systems_dir, slug, ids)
    return {"ok": True, "removed": removed}


@router.get("/api/detector/snapshots/{sid}", name="detector.snapshot_image")
def detector_snapshot_image(sid: str, request: Request,
                            thumb: int = Query(0)):
    from fastapi.responses import FileResponse
    systems_dir, slug = _system_context(request)
    path = (detector_control_service.snapshot_thumb(systems_dir, slug, sid)
            if thumb else
            detector_control_service.snapshot_file(systems_dir, slug, sid))
    if not path:
        raise HTTPException(status_code=404, detail="no such snapshot")
    # A snapshot never changes after it is written, so the browser may keep
    # it: revisiting Footage stops re-downloading every frame it already has.
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control":
                                 "private, max-age=86400, immutable"})


@router.get("/api/detector/snapshots/{sid}/meta", name="detector.snapshot_meta")
def detector_snapshot_meta(sid: str, request: Request):
    """What the photo window and the PDF notice show about one snapshot: its
    detection id, time and date, the place and device, the timer when a real
    one was running, and the close-ups when the Snapshot node took them."""
    systems_dir, slug = _system_context(request)
    view = detector_control_service.snapshot_view(systems_dir, slug, sid)
    if view is None:
        raise HTTPException(status_code=404, detail="no such snapshot")
    return {"ok": True, "snapshot": view}


@router.get("/api/detector/snapshots/{sid}/crops/{n}", name="detector.snapshot_crop")
def detector_snapshot_crop(sid: str, n: str, request: Request):
    from fastapi.responses import FileResponse
    systems_dir, slug = _system_context(request)
    path = detector_control_service.snapshot_crop_file(systems_dir, slug, sid, n)
    if not path:
        raise HTTPException(status_code=404, detail="no such close-up")
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=86400, immutable"})


@router.delete("/api/detector/snapshots/{sid}", name="detector.snapshot_delete")
def detector_snapshot_delete(sid: str, request: Request):
    perm.require(request, "footage.delete")
    systems_dir, slug = _system_context(request)
    if not detector_control_service.delete_snapshot(systems_dir, slug, sid):
        raise HTTPException(status_code=404, detail="no such snapshot")
    return {"ok": True}


@router.get("/api/detector/tutorial", name="detector.get_tutorial")
def detector_get_tutorial(request: Request):
    systems_dir, slug = _system_context(request)
    return {"ok": True, **detector_control_service.get_tutorial(systems_dir, slug)}


@router.post("/api/detector/tutorial", name="detector.set_tutorial")
async def detector_set_tutorial(request: Request):
    """Advance the guided setup, or end it. Writes to the system, not the
    browser: whoever finishes the tour is often not whoever opens it next."""
    _require_admin(request)
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        state = detector_control_service.set_tutorial(
            systems_dir, slug, done=body.get("done"), step=body.get("step"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **state}


@router.get("/api/detector/export/db", name="detector.export_db")
async def detector_export_db(request: Request):
    """This system's data as a SQLite file.

    Every system on a box shares ONE Postgres database and is told apart by a
    slug column; this carves one system out into a file that opens anywhere.
    Operator-gated rather than admin-only: reading your own system's data is
    the job, not a change to the deployment.

    Built on a worker thread and streamed from disk, never assembled in
    memory: a system with a year of events is millions of rows, and holding
    the file in RAM to hand it over is how an export takes the box down
    instead of just taking a while.
    """
    systems_dir, slug = _system_context(request)
    _require_operator(request, slug, "exporting this system's data")

    from core.services import datasets as datasets_service
    from core.services import dbexport as dbexport_service
    try:
        tables = datasets_service.definitions(systems_dir, slug)
    except Exception:                                       # noqa: BLE001
        tables = []

    path, summary = await asyncio.to_thread(
        dbexport_service.export_system, slug, None,
        request.app.state.config_dir, tables)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask
    return FileResponse(
        path, media_type="application/vnd.sqlite3",
        filename=f"{slug}_{stamp}.db",
        # The temp file goes the moment the response is finished, whether the
        # client read it all or hung up halfway -- otherwise every export
        # leaves a copy of the database behind on the box it came from.
        background=BackgroundTask(lambda: _unlink_quietly(path)),
        headers={"X-Export-Rows": json.dumps(summary.get("counts") or {})})


def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


#: The biggest database file this box will take from an upload. An export of a
#: busy year is a few hundred megabytes; past this it is not one of ours, and
#: reading it to find out costs the disk it is written to first.
MAX_DB_UPLOAD = 2 * 1024 * 1024 * 1024


@router.post("/api/detector/import/db", name="detector.import_db")
async def detector_import_db(request: Request, file: UploadFile = File(...),
                             mode: str = Form("preview")):
    """Load a SQLite export back into this system. The other half of the
    export above, which until now was a one-way door.

    THREE modes, and the first one is the point:

        preview   read the file and say what is in it. Changes nothing.
        append    add its rows. Rows already here stay; re-running does
                  nothing, because the ids are stable.
        replace   DELETE this system's events and Data Table rows, then add
                  the file's. The restore-a-backup case.

    Preview exists because "import this database" is a question an operator
    can only answer once they know whose data it is and how much -- a file
    from another system entirely is a valid thing to import and a terrible
    thing to import by accident. So the page reads first and asks second.

    Admin-gated, not operator-gated like the export: reading your own data is
    the job, writing a million rows into the shared database is a change to
    the deployment.
    """
    perm.require(request, "database.edit")
    systems_dir, slug = _system_context(request)
    if mode not in ("preview", "append", "replace"):
        raise HTTPException(status_code=400, detail="mode is preview, append or replace")

    from core.services import dbimport as dbimport_service

    # Spooled to disk, never held whole: sqlite3 reads a PATH, and a database
    # file is exactly the kind of upload that does not fit in memory.
    tmp = tempfile.NamedTemporaryFile(prefix=f"{slug}-import-", suffix=".db",
                                      delete=False)
    size = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_DB_UPLOAD:
                raise HTTPException(status_code=400,
                                    detail="that file is too large to be a database export")
            tmp.write(chunk)
        tmp.close()

        try:
            found = await asyncio.to_thread(dbimport_service.inspect, tmp.name)
        except dbimport_service.NotAnExport as e:
            raise HTTPException(status_code=400, detail=str(e))
        if mode == "preview":
            return {"ok": True, "preview": True, **found}

        summary = await asyncio.to_thread(
            dbimport_service.import_file, tmp.name, slug,
            request.app.state.config_dir, systems_dir, mode)
        return {"ok": summary.get("ok", True), "preview": False,
                "found": found, **summary}
    finally:
        try:
            tmp.close()
        except Exception:                                   # noqa: BLE001
            pass
        _unlink_quietly(tmp.name)


@router.get("/api/detector/control/modbus", name="detector.get_modbus")
def detector_get_modbus(request: Request):
    """The Modbus SERVER settings for this system, plus whether it is up.

    Two different questions and both are needed: what the box was told to do,
    and what actually happened when it tried. A server that could not bind its
    port keeps the detection app running, so without the second half a
    misconfigured listener looks exactly like one no PLC has polled yet.
    """
    systems_dir, slug = _system_context(request)
    state = {}
    try:
        rt = detector_control_service.app_state(systems_dir, slug) or {}
        state = rt.get("modbus") or {}
    except Exception:                                       # noqa: BLE001
        state = {}
    return {"ok": True,
            "modbus": detector_control_service.get_modbus_server(systems_dir, slug),
            "state": state,
            # Whether this box redirects a PLC's 502 to 5020 (install.sh's
            # nftables rule). False: the panel tells the operator to point
            # the PLC at 5020 itself.
            "redirect_502": detector_control_service.modbus_redirect_installed(),
            "redirect_port": detector_control_service.MODBUS_REDIRECT_PORT,
            # The ports this box actually has, so RTU is chosen from a list
            # rather than typed from memory.
            "serial_ports": board_io_service.serial_ports()}


@router.post("/api/detector/control/modbus", name="detector.set_modbus")
async def detector_set_modbus(request: Request):
    """Change the Modbus server settings. Admin only, like every other
    setting that changes what this box puts on a wire."""
    perm.require(request, "modbus.edit")
    systems_dir, slug = _system_context(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        saved = detector_control_service.set_modbus_server(systems_dir, slug, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "modbus": saved}


# -- Modbus SERVERS this box talks to ---------------------------------------
# The connections a Modbus Read or Modbus Write node picks (Node-RED's
# "modbus-client" config node). NOT the section above: that is the box as a
# device a PLC polls, a developer page; these are used from the Workflow by
# whoever builds it, customer box or not, so nothing here is developer-gated.
# Writing asks for the same capability the fieldbus settings always have --
# master holds it by tier (perm.require), a site admin where master ticked it.

@router.get("/api/detector/control/modbus/clients",
            name="detector.list_modbus_clients")
def detector_list_modbus_clients(request: Request):
    """Every saved connection, plus the serial ports this box can see, so a
    Serial connection is picked from a list rather than typed from memory."""
    systems_dir, slug = _system_context(request)
    _require_operator(request, slug, "Modbus servers")
    return {"ok": True,
            "clients": detector_control_service.list_modbus_clients(systems_dir,
                                                                    slug),
            "serial_ports": board_io_service.serial_ports(),
            "editable": perm.editable(request, "modbus.")}


@router.post("/api/detector/control/modbus/clients",
             name="detector.save_modbus_client")
async def detector_save_modbus_client(request: Request):
    """Add a connection (no id in the body) or change one (its id)."""
    perm.require(request, "modbus.edit")
    systems_dir, slug = _system_context(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        saved = detector_control_service.save_modbus_client(systems_dir, slug,
                                                            body)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "client": saved}


@router.delete("/api/detector/control/modbus/clients/{client_id}",
               name="detector.delete_modbus_client")
def detector_delete_modbus_client(request: Request, client_id: str):
    """Remove a connection. One a node still picks is refused (409), naming
    the nodes, so the operator knows where to go instead of guessing."""
    perm.require(request, "modbus.edit")
    systems_dir, slug = _system_context(request)
    try:
        detector_control_service.delete_modbus_client(systems_dir, slug,
                                                      client_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@router.get("/api/detector/control/capture", name="detector.get_capture")
def detector_get_capture(request: Request):
    systems_dir, slug = _system_context(request)
    return {"ok": True,
            "capture": detector_control_service.get_capture(systems_dir, slug),
            # Whether second storage exists -- the panel disables the
            # switches (with the reason) instead of offering a toggle that
            # can only refuse.
            "storage_external":
                detector_control_service.storage_external(systems_dir, slug),
            "clips": detector_control_service.list_clips(systems_dir, slug)}


@router.post("/api/detector/control/capture", name="detector.set_capture")
async def detector_set_capture(request: Request):
    """The snapshot/record switches. The workflow's Snapshot & Record nodes
    only act while these are on -- capability is the operator's, timing is
    the workflow's."""
    perm.require(request, "detection.capture")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    # Switching ON needs second storage -- filling the boot media with
    # recordings is how a recorder bricks the box. The guard lives HERE, on
    # the operator's toggle: template restores carry capture settings
    # through set_capture legitimately, and the app refuses to write once
    # storage really is absent either way.
    if (body.get("snap") or body.get("record"))             and not detector_control_service.storage_external(systems_dir, slug):
        raise HTTPException(
            status_code=400,
            detail="no second storage: snapshots and recordings need an NVMe "
                   "(or an explicit [app].data_dir on another drive) - "
                   "writing them to the boot media fills the card the OS "
                   "runs from")
    cap = detector_control_service.set_capture(
        systems_dir, slug, snap=body.get("snap"), record=body.get("record"))
    return {"ok": True, "capture": cap}


@router.get("/api/detector/control/hud", name="detector.get_hud")
def detector_get_hud(request: Request):
    """Which rows the live feed's status box shows."""
    systems_dir, slug = _system_context(request)
    return {"ok": True,
            "hud": detector_control_service.get_hud(systems_dir, slug),
            "rows": [{"key": k, "label": v}
                     for k, v in detector_control_service.HUD_ROWS]}


@router.post("/api/detector/control/hud", name="detector.set_hud")
async def detector_set_hud(request: Request):
    """Show or hide a row of the status box burned into every live frame.

    It is drawn INTO the JPEG by the app, so this is config the app reads
    rather than something the page could hide on its own -- and turning a row
    off turns it off for every viewer, including the hub's Live Preview and
    any recorded clip.
    """
    perm.require(request, "detection.hud")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        hud = detector_control_service.set_hud(systems_dir, slug, body or {})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "hud": hud}


@router.get("/api/detector/clips", name="detector.clips")
def detector_clips(request: Request, limit: int = Query(0)):
    """Recorded clips, newest first, all of them unless a limit is asked for
    (0 = no limit) -- Footage's video half."""
    systems_dir, slug = _system_context(request)
    return {"ok": True,
            "caps": perm.held(request),
            "clips": detector_control_service.list_clips(
                systems_dir, slug, limit=max(0, limit))}


@router.post("/api/detector/clips/delete", name="detector.clips_delete")
async def detector_clips_delete(request: Request):
    perm.require(request, "footage.delete")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    names = body.get("names")
    if names is None:
        raise HTTPException(status_code=400, detail="names is required")
    removed = await asyncio.to_thread(
        detector_control_service.delete_clips, systems_dir, slug, names)
    return {"ok": True, "removed": removed}


@router.post("/api/detector/footage/email/preview", name="detector.footage_email_preview")
async def detector_footage_email_preview(request: Request):
    """How many files, how big, and whether it will send -- before it is sent.

    Its own call because the answer changes the operator's next action: a
    range that is 180MB needs narrowing, and finding that out from a failed
    send (after a minute of encoding) is how a feature gets a reputation.
    """
    systems_dir, slug = _system_context(request)
    body = await request.json()
    rows = await asyncio.to_thread(
        footage_mail_service.collect, systems_dir, slug,
        body.get("items"), _epoch(body.get("start")), _epoch(body.get("end")),
        body.get("kinds"))
    ok, problem, total = footage_mail_service.check(
        rows, bool(body.get("zip", True)))
    return {"ok": True, "count": len(rows), "bytes": total,
            "human": footage_mail_service.human_bytes(total),
            "sendable": ok, "problem": problem,
            "limit": footage_mail_service.MAX_ATTACH_BYTES,
            "limit_human": footage_mail_service.human_bytes(
                footage_mail_service.MAX_ATTACH_BYTES)}


@router.post("/api/detector/footage/download", name="detector.footage_download")
async def detector_footage_download(request: Request):
    """The ticked footage as ONE zip, saved to the machine at the keyboard.

    Same selection model as the email export -- the ticked rows, or the whole
    visible range when nothing is ticked -- and its own size budget: the mail
    limits exist because a message crosses a mail server, and this crosses
    nothing (see footage_mail.check_download).

    Staged to a temp file and streamed, not built in memory: a gigabyte of
    evidence held in RAM on a box running inference is how a download takes
    the detection loop with it. The file is deleted after the response is
    sent, whether or not the browser finished reading it.
    """
    perm.require_any(request, ("footage.download", "footage.export"))
    systems_dir, slug = _system_context(request)
    body = await request.json()
    rows = await asyncio.to_thread(
        footage_mail_service.collect, systems_dir, slug,
        body.get("items"), _epoch(body.get("start")), _epoch(body.get("end")),
        body.get("kinds"))
    ok, problem, _total = footage_mail_service.check_download(rows)
    if not ok:
        raise HTTPException(status_code=400, detail=problem)

    import tempfile                                          # noqa: PLC0415
    from starlette.background import BackgroundTask          # noqa: PLC0415

    fd, path = tempfile.mkstemp(prefix="footage-", suffix=".zip")
    os.close(fd)
    ok, problem = await asyncio.to_thread(
        footage_mail_service.write_zip, rows, path)
    if not ok:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=problem)

    system = getattr(request.app.state, "system", None)
    name = footage_mail_service.zip_name(
        getattr(system, "display_name", "") or slug)

    def _cleanup():
        try:
            os.unlink(path)
        except OSError:
            pass

    return FileResponse(path, media_type="application/zip", filename=name,
                        background=BackgroundTask(_cleanup))


@router.post("/api/detector/footage/email", name="detector.footage_email")
async def detector_footage_email(request: Request):
    """Send the selected footage, or a time range of it, as one email."""
    perm.require(request, "footage.export")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    rows = await asyncio.to_thread(
        footage_mail_service.collect, systems_dir, slug,
        body.get("items"), _epoch(body.get("start")), _epoch(body.get("end")),
        body.get("kinds"))
    # Building a zip and talking to an SMTP server both block for real time --
    # a 15MB send is seconds of encoding plus a round trip to Gmail.
    #
    # Two channels, attempted independently and reported separately. Either
    # alone is the normal case; both together is an operator who wants the
    # record in an inbox AND on the shift group's phone. A Telegram group
    # being unreachable must not cancel the email, and one "sent" covering a
    # send that only half happened is a message an operator trusts and should
    # not.
    name = getattr(request.app.state.system, "name", "") or slug
    config_dir = request.app.state.config_dir
    want_mail = body.get("email", True) and str(body.get("to") or "").strip()
    want_chat = body.get("telegram") and str(body.get("chat_id") or "").strip()
    if not (want_mail or want_chat):
        raise HTTPException(status_code=400,
                            detail="choose email, Telegram, or both - and say "
                                   "where it goes")
    said, any_ok = [], False
    if want_mail:
        ok, message = await asyncio.to_thread(
            footage_mail_service.send, systems_dir, slug, rows,
            body.get("to"), body.get("account"),
            str(body.get("subject") or ""), str(body.get("body") or ""),
            bool(body.get("zip", True)), config_dir, name)
        any_ok = any_ok or ok
        said.append(("email: " if ok else "email failed: ") + message)
    if want_chat:
        ok, message = await asyncio.to_thread(
            footage_mail_service.send_telegram, systems_dir, slug, rows,
            body.get("chat_id"), body.get("bot"),
            str(body.get("body") or ""), config_dir, name)
        any_ok = any_ok or ok
        said.append(("telegram: " if ok else "telegram failed: ") + message)
    joined = " · ".join(said)
    # A 400 only when NOTHING got through. One channel failing beside one that
    # worked is worth saying, not worth throwing the successful half away for.
    if not any_ok:
        raise HTTPException(status_code=400, detail=joined)
    return {"ok": True, "message": joined, "count": len(rows)}


def _epoch(value):
    """Accept epoch seconds or an ISO 'YYYY-MM-DDTHH:MM' from a date input.

    The browser sends local wall-clock, which is what the operator means: a
    range typed as 14:00 must cover the frames written at 14:00 on the box,
    not 14:00 UTC on a board whose clock is set to Kuala Lumpur.
    """
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        pass
    import datetime                                          # noqa: PLC0415
    text = str(value).strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    return None


@router.get("/api/detector/clips/{name}", name="detector.clip")
def detector_clip(name: str, request: Request, thumb: int = Query(0)):
    from fastapi.responses import FileResponse
    systems_dir, slug = _system_context(request)
    # ?thumb=1 answers with a POSTER FRAME rather than the video. Footage drew
    # every clip as the same black rectangle, so a page of thirty said nothing
    # about which one held the thing you were looking for. Same query flag the
    # snapshot route uses, so the grid asks for both the same way.
    if thumb:
        path = detector_control_service.clip_thumb(systems_dir, slug, name)
        if not path:
            # No OpenCV, or a clip still being written. The grid falls back to
            # its placeholder, which is what it always drew.
            raise HTTPException(status_code=404, detail="no poster frame")
        return FileResponse(path, media_type="image/jpeg",
                            headers={"Cache-Control":
                                     "private, max-age=86400, immutable"})
    path = detector_control_service.clip_path(systems_dir, slug, name)
    if not path:
        raise HTTPException(status_code=404, detail="no such clip")
    return FileResponse(path, media_type="video/mp4", filename=name)


@router.get("/api/detector/control/outputs", name="detector.get_outputs")
def detector_get_outputs(request: Request):
    """The system's output actions (GPIO / MQTT / Modbus fired on events)."""
    systems_dir, slug = _system_context(request)
    return {"ok": True,
            # SMTP passwords, bot tokens, webhook tokens: never to a browser
            # (redact.py); POST puts them back where REDACTED returns.
            "outputs": redact.redact(
                detector_control_service.get_outputs(systems_dir, slug)),
            "types": list(detector_control_service.OUTPUT_TYPES),
            "triggers": list(detector_control_service.OUTPUT_TRIGGERS),
            "editable": auth_service.is_admin(getattr(request.state, "user", None),
                                     request.app.state.config_dir)}


@router.post("/api/detector/control/outputs", name="detector.set_outputs")
async def detector_set_outputs(request: Request):
    _require_admin(request)
    systems_dir, slug = _system_context(request)
    body = await request.json()
    try:
        sent = redact.restore(body.get("outputs") or [],
                              detector_control_service.get_outputs(systems_dir, slug))
        outputs = detector_control_service.set_outputs(systems_dir, slug, sent)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "outputs": redact.redact(outputs)}


@router.post("/api/detector/control/power", name="detector.power")
async def detector_power(request: Request):
    """Start or stop the local detection app."""
    perm.require(request, "detection.power")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    action = str(body.get("action") or "")
    if action not in ("start", "stop"):
        raise HTTPException(status_code=400, detail="action must be start or stop")
    fn = detector_control_service.start if action == "start" else detector_control_service.stop
    from core.services import app_supervisor                   # noqa: PLC0415
    try:
        # Start / Stop flip the AI switch in the running app; an old app copy
        # is stopped for real, up to 10s for the pid to die -- off the event
        # loop, or every other request (all pages) queues behind it.
        result = await asyncio.to_thread(fn, systems_dir, slug)
    except (ValueError, TypeError) as e:
        # Detection could not start (no camera, the running-apps cap...):
        # the rest of the workflow runs anyway.
        await asyncio.to_thread(app_supervisor.reconcile, systems_dir, slug,
                                request.app.state.config_dir)
        raise HTTPException(status_code=400, detail=str(e))
    if not result.get("ok"):
        # Whatever happened to detection, the app belongs up.
        result["workflow"] = await asyncio.to_thread(
            app_supervisor.reconcile, systems_dir, slug,
            request.app.state.config_dir)
    elif action == "start":
        # No camera on the box: say detection waits for it, not "started".
        from core.services import camera_notice                # noqa: PLC0415
        result = await asyncio.to_thread(camera_notice.start_word,
                                         systems_dir, slug, result)
    return {"ok": True, "action": action, **result}


@router.get("/api/detector/rtsp_scan", name="detector.rtsp_scan")
def detector_rtsp_scan(request: Request):
    """Sweep the local subnet for hosts answering on the RTSP port (554) --
    the camera picker's 'Scan network' button. Sync def: runs in the
    threadpool, so the ~2s sweep never blocks other pages."""
    _system_context(request)
    return {"ok": True, "hosts": detector_control_service.scan_rtsp()}


@router.get("/api/detector/cameras", name="detector.cameras")
def detector_cameras(request: Request):
    """Discoverable cameras grouped by connection: usb (V4L2), gige (Aravis).
    RTSP cameras are entered by URL, not discovered."""
    _system_context(request)
    return {"ok": True, **detector_control_service.discover_cameras(
        request.app.state.config_dir)}


@router.get("/api/board/io", name="board.io")
def board_io(request: Request):
    """The physical I/O this box has: ports, live levels, serial devices.

    Answers on a box that is NOT an IRIV too -- with detected:false and an
    empty port list, which is what lets the workflow palette say "no board
    detected" instead of offering ports that would drive nothing.
    """
    _systems_dir, slug = _system_context(request)
    # WHICH system is asking matters: a terminal held by this system's own app
    # is the rule the operator is editing, not a conflict. Without the slug
    # every port a workflow drives reported itself held by a stranger, and the
    # page named the factory Node-RED flow as the likely culprit -- sending
    # people to hunt through Node-RED for a pin we were driving ourselves.
    payload = board_io_service.inventory(slug)
    # The live level of each input, so the panel can show a contact closing
    # while the installer holds it.
    for port in payload["ports"]:
        if port["kind"] == "input":
            port["value"] = board_io_service.read_port(port["id"])
    return {"ok": True, **payload}


@router.get("/api/board/ports", name="board.ports")
def board_ports(request: Request):
    """The board's CONNECTORS as the faceplate shows them, with live state:
    the two USB sockets (what camera is in each, which systems capture it)
    and the two ethernet jacks (link, speed, address).

    This is the payload behind every "Ports" button on the camera panel and
    the hardware footer -- an installer holding a cable compares this to the
    physical board, so it reports sockets even when empty rather than only
    listing what happens to be plugged in.
    """
    systems_dir, _slug = _system_context(request)
    return {"ok": True,
            "usb": detector_control_service.usb_ports(systems_dir),
            "eth": board_io_service.eth_ports()}


@router.post("/api/board/io/test", name="board.io_test")
async def board_io_test(request: Request):
    """Pulse an output so an installer can prove a wire.

    A pulse and nothing more (capped in the service at five seconds): this is
    a wiring check, not a second control path that could drive the plant
    behind the workflow's back. Operator-gated like the other per-system
    actions -- the person who runs the line is the person standing at the
    cabinet.
    """
    _, slug = _system_context(request)
    _require_operator(request, slug, "testing board I/O")
    body = await request.json() if await request.body() else {}
    percent = body.get("percent")
    ok, message = await asyncio.to_thread(
        board_io_service.pulse_port, body.get("port"),
        int(body.get("ms") or 500),
        None if percent in (None, "") else float(percent))
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


#: The one definition lives with the camera code (the save routes use it too).
_plain_source = detector_control_service.plain_source


@router.get("/api/detector/snapshot", name="detector.snapshot")
def detector_snapshot(request: Request, source: str = Query(...),
                      width: int | None = Query(None), height: int | None = Query(None),
                      fps: int | None = Query(None),
                      exposure_auto: str = Query(""),
                      exposure_us: float | None = Query(None),
                      gain_auto: str = Query(""),
                      gain_db: float | None = Query(None),
                      binning: int | None = Query(None)):
    """DEPRECATED (2026-10-05): POST /api/detector/snapshot with a JSON body.

    A GET puts the source -- an RTSP URL with its password -- in the query
    string, which every proxy, access log and browser history keeps. Kept
    working for ONE release so an open tab and an older page do not break;
    each call says so at WARNING (never with the source in it)."""
    logger.warning("[CAM] GET /api/detector/snapshot is deprecated - the page "
                   "should POST it (the camera URL is in this query string)")
    resp = _snapshot(request, source, width=width, height=height, fps=fps,
                     tuning={"exposure_auto": exposure_auto, "exposure_us": exposure_us,
                             "gain_auto": gain_auto, "gain_db": gain_db,
                             "binning": binning})
    if hasattr(resp, "headers"):
        resp.headers["Deprecation"] = "true"
    return resp


def _num(body, key, cast):
    value = body.get(key)
    if value in (None, ""):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{key} must be a number")


@router.post("/api/detector/snapshot", name="detector.snapshot_post")
async def detector_snapshot_post(request: Request):
    """One JPEG from a candidate source, the source in the BODY:

        {"source": str, "camera_id"?: str, "width"?: int, "height"?: int,
         "fps"?: int, "exposure_auto"?: str, "exposure_us"?: number,
         "gain_auto"?: str, "gain_db"?: number, "binning"?: int}

    `camera_id` lets a page that was shown a masked password preview the
    saved camera: the stored source is put back on an exact round trip only
    (detector_control.unmask_source). Answers exactly as the GET did."""
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    source = str(body.get("source") or "")
    if detector_control_service.SOURCE_REDACTED in source:
        systems_dir, slug = _system_context(request)
        cams = detector_control_service.cameras(systems_dir, slug)
        cid = str(body.get("camera_id") or "")
        stored = next((c.get("source") for c in cams if c.get("id") == cid), "")
        try:
            source = detector_control_service.unmask_source(source, stored)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    tuning = {"exposure_auto": str(body.get("exposure_auto") or ""),
              "exposure_us": _num(body, "exposure_us", float),
              "gain_auto": str(body.get("gain_auto") or ""),
              "gain_db": _num(body, "gain_db", float),
              "binning": _num(body, "binning", int)}
    return await asyncio.to_thread(
        _snapshot, request, source, width=_num(body, "width", int),
        height=_num(body, "height", int), fps=_num(body, "fps", int), tuning=tuning)


def _snapshot(request, source, width=None, height=None, fps=None, tuning=None):
    """One JPEG from a candidate source, to preview it before applying. Opens and
    releases the device immediately. 409 if it is busy (the running detector owns
    it) -- the page shows the live feed for the current source instead.

    The GigE exposure/gain settings ride along so the preview is taken with the
    settings being CHOSEN, not with the camera's defaults: a preview that
    ignores them cannot answer the only question it is opened for, which is
    whether the picture looks right.

    Gated like the camera it previews. Opening an arbitrary `source` is not a
    read: on a failure the cause probe connects out to whatever host:port the
    URL names, so an ungated preview was a LAN scanner and service oracle for
    any signed-in account. The add dialog previews too, hence either grant.
    A raw GStreamer pipeline or a file path is the box owner's alone: a
    pipeline can write files as this process, and a path reaches outside the
    system (security review F1)."""
    _system_context(request)
    try:
        perm.require(request, "detection.camera.change")
    except HTTPException:
        perm.require(request, "detection.camera.add")
    # An empty source used to go all the way to the open and come back as
    # "it may be in use by the running detector" -- about a camera nobody
    # chose. The "no camera chosen" wording is the one camErrors.js maps.
    if not str(source or "").strip():
        raise HTTPException(status_code=400,
                            detail="no camera chosen - choose a camera first")
    if not _plain_source(source):
        _require_admin(request)
    try:
        jpeg = detector_control_service.snapshot(source, width=width, height=height,
                                                 fps=fps, tuning=tuning)
    except Exception as e:              # noqa: BLE001 -- any capture failure is a 409, never a 500
        raise HTTPException(status_code=409,
                            detail=_not_running(request, _own_holder(request, str(e)))
                            or e.__class__.__name__) from e
    return Response(content=jpeg, media_type="image/jpeg")


#: The open failure's catch-all (detector_control._open_capture) guesses the
#: running detector among its causes; true only while this system's app runs.
_RUNNING_GUESS = "it may be in use by the running detector, absent, or an unreachable URL."


def _not_running(request, detail):
    """Drop "in use by the running detector" from a failure while this
    system's detector is NOT running: the operator goes to stop a detector
    that is already stopped. Only on the failure path."""
    if _RUNNING_GUESS not in (detail or ""):
        return detail
    try:
        systems_dir, slug = _system_context(request)
        running = bool(detector_control_service.status(systems_dir, slug).get("running"))
    except Exception:                                       # noqa: BLE001
        return detail
    if running:
        return detail
    return detail.replace(_RUNNING_GUESS, "it may be absent or an unreachable URL.")


def _own_holder(request, detail):
    """The probe names whichever process holds a USB camera "another program"
    -- it cannot tell, from /proc, that the holder is THIS system's own app.
    The master can, and an operator told to close another program goes
    looking for one that does not exist. Only on the failure path."""
    import re                                               # noqa: PLC0415
    m = re.search(r"in use by another program \(.*, pid (\d+)\)", detail or "")
    if not m:
        return detail
    try:
        systems_dir, slug = _system_context(request)
        pid = detector_control_service.status(systems_dir, slug).get("pid")
    except Exception:                                       # noqa: BLE001
        return detail
    if str(pid or "") != m.group(1):
        return detail
    return detail.replace(m.group(0), "in use by this system's own detection")


@router.get("/api/detector/preview", name="detector.preview")
def detector_preview(request: Request, camera: str = Query(""),
                     fps: int = Query(0), quality: int = Query(0),
                     view: str = Query("")):
    """Relay the LOCAL detection app's MJPEG stream (127.0.0.1:<stream_port>) so
    the Detection page shows live video with no device pairing -- the app and
    this master share the box. Not the fleet /video_feed path (that needs a
    paired agent and a token); this is the standalone board watching itself.

    All of a system's cameras share the ONE stream port, addressed by `camera`.
    Omitting it is the single-camera call and the app answers with the first.

    `fps` and `quality` let a caller showing MANY tiles ask for a cheaper
    stream. Both are clamped here as well as in the app: this relay is reachable
    from the browser, and a request for 10000 fps is a request to spin a CPU."""
    systems_dir, slug = _system_context(request)
    port = detector_control_service.stream_port(systems_dir, slug)
    params = {}
    if camera:
        params["camera"] = camera
    if fps:
        params["fps"] = max(1, min(30, int(fps)))
    if quality:
        params["quality"] = max(20, min(90, int(quality)))
    # view=depth: the depth camera's picture in false colour, boxes and
    # zones still painted -- how a person checks what the depth sees.
    if str(view).strip().lower() == "depth":
        params["view"] = "depth"
    path = "/video_feed"
    if params:
        path += "?" + urllib.parse.urlencode(params)
    # http.client rather than urlopen: the CONNECT must fail fast (a dead app
    # is a 502 in five seconds), but once streaming, a read pause is not an
    # error -- the app pauses for seconds while it reopens a dropped camera or
    # hot-reloads a changed model, and a 5s read timeout here cut the stream
    # mid-watch every time ("the video ends, then comes back"). The browser
    # keeps its one connection through the pause; frames resume on their own.
    import http.client                                      # noqa: PLC0415
    import time as _time                                    # noqa: PLC0415
    conn = upstream = None
    # A 503 here is "too many viewers" -- transient by construction now that
    # the app evicts its oldest viewer to admit a new one. Rapid camera
    # switching produces exactly this for a beat; riding it out for a couple
    # of seconds turns a broken image into a barely-late first frame.
    for attempt in range(4):
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            upstream = conn.getresponse()
        except OSError as e:
            # The port number and the errno are diagnosis, not something to
            # put in front of somebody watching a blank video panel.
            why = f"cannot reach the detection app on 127.0.0.1:{port}: {e}"
            raise catalog.http_error("AI_SERVICE_UNAVAILABLE", cause=why,
                                     level="WARNING", details=why,
                                     port=port) from e
        if upstream.status != 503 or attempt == 3:
            break
        conn.close()
        _time.sleep(0.5)
    if upstream.status == 404:
        # The app knows its own cameras; pass its verdict through rather than
        # reporting a missing camera as a dead app.
        conn.close()
        raise HTTPException(status_code=404,
                            detail=f"camera {camera!r} is not configured on this system")
    if upstream.status != 200:
        # Any other answer is the AI-5003 sentence. It used to read "detection
        # app answered 401 on :5102" -- an HTTP code and a port in front of
        # somebody watching a blank video panel -- and log a full traceback
        # for a failure this line has already handled. The code and the port
        # go to `details` and the one WARNING record instead.
        conn.close()
        why = f"the detection app on 127.0.0.1:{port} answered HTTP {upstream.status}"
        raise catalog.http_error("AI_SERVICE_UNAVAILABLE", cause=why, status=502,
                                 level="WARNING", details=why, port=port,
                                 upstream_status=upstream.status)
    ctype = upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=frame")
    # The relay owns the upstream and its ENDING: Relay.close() shuts the
    # socket down from any thread, so a browser that goes away mid-stream
    # frees the app's viewer slot at once instead of after the next read
    # timeout -- see core/services/preview_relay.py. It runs as the
    # response's background task (Starlette calls that on a client
    # disconnect too) and again from the generator's own finally.
    from starlette.background import BackgroundTask          # noqa: PLC0415
    from core.services.preview_relay import Relay            # noqa: PLC0415
    link = Relay(conn, upstream, read_timeout=30)
    return StreamingResponse(link.chunks(), media_type=ctype,
                             background=BackgroundTask(link.close))


@router.get("/api/detector/webrtc", name="detector.webrtc_state")
def detector_webrtc_state(request: Request, camera: str = Query("")):
    """Whether this system's video can be watched over WebRTC right now.

    The page asks BEFORE building an RTCPeerConnection: a system on the
    mjpeg transport, a box with no gateway, or a camera whose stream is not
    published all answer the same way -- "use the img" -- and the page shows
    a picture rather than an explanation.
    """
    from core.services import overlay_view, streaming as streaming_service   # noqa: PLC0415
    systems_dir, slug = _system_context(request)
    cfg = detector_control_service._merged(
        detector_control_service._pkg_dir(systems_dir, slug))
    cam = str(camera or "").strip()
    return {
        "mode": streaming_service.mode(cfg, slug, cam),
        "configured": str(((cfg.get("app") or {}).get("stream")
                           or streaming_service.DEFAULT_MODE)).strip().lower(),
        "gateway": streaming_service.gateway_running(),
        "ready": streaming_service.stream_ready(slug, cam) if cam else False,
        "path": streaming_service.path_name(slug, cam), **overlay_view.page_info(cfg),
    }


@router.post("/api/detector/webrtc/whep", name="detector.webrtc_whep")
async def detector_webrtc_whep(request: Request, camera: str = Query("")):
    """The WebRTC handshake, proxied so that watching needs a session.

    The gateway's signalling port is bound to loopback precisely so that
    this is the only door to it: the middleware has already authenticated
    the request by the time it arrives here, which makes watching a camera
    over WebRTC exactly as gated as watching it over the MJPEG relay.

    Only the OFFER and the ANSWER pass through this process -- a few
    kilobytes of SDP, once per viewer. The video itself flows directly from
    the gateway's media port to the browser, which is what lets a Python
    master serve many WebRTC viewers without ever touching a frame.
    """
    from core.services import streaming as streaming_service   # noqa: PLC0415
    systems_dir, slug = _system_context(request)
    offer = await request.body()
    if not offer:
        raise HTTPException(status_code=400, detail="an SDP offer is required")
    cam = str(camera or "").strip()
    status, answer, session = await asyncio.to_thread(
        streaming_service.whep_offer, slug, cam, offer)
    if status not in (200, 201):
        raise HTTPException(status_code=status if status in (404, 502) else 502,
                            detail=answer)
    # 201 + the SDP answer is what WHEP specifies. The Location header is
    # the session's address, and it is handed back as THIS proxy's address
    # for it (never the gateway's own): the player DELETEs it when it tears
    # the connection down. Without that the gateway kept every abandoned
    # session until its own timeout -- pc.close() only ends the browser's
    # half -- and an operator switching cameras left a trail of them. Not
    # used for trickle ICE; the answer carries every candidate the board has.
    headers = {}
    if session:
        headers["Location"] = (f"/api/detector/webrtc/session/{session}"
                               + (f"?camera={urllib.parse.quote(cam, safe='')}"
                                  if cam else ""))
    return Response(content=answer, status_code=201,
                    media_type="application/sdp", headers=headers)


@router.delete("/api/detector/webrtc/session/{session}",
               name="detector.webrtc_session_delete")
async def detector_webrtc_session_delete(request: Request, session: str,
                                         camera: str = Query("")):
    """Release one WebRTC session on the gateway -- the WHEP DELETE, proxied
    for the same reason the offer is. Answers 204 whether or not the gateway
    still had it: a session already gone is the outcome the caller wanted."""
    from core.services import streaming as streaming_service   # noqa: PLC0415
    systems_dir, slug = _system_context(request)
    if not streaming_service.valid_session(session):
        raise HTTPException(status_code=400, detail="not a session id")
    await asyncio.to_thread(streaming_service.whep_delete, slug,
                            str(camera or "").strip(), session)
    return Response(status_code=204)


@router.post("/api/models", name="models.save")
async def save_model(request: Request):
    """Create or update a manifest. The form sends JSON; no file involved."""
    perm.require(request, "models.manage")
    body = await request.json()
    home = _models_home(request)
    class_lock.require_stopped_manifest(request.app.state, body)  # see there
    try:
        manifest = models_service.save_manifest(request.app.state.config_dir,
                                                body.get("manifest") or body, home)
        if body.get("labels_text") is not None:
            stored, count = models_service.save_labels(
                request.app.state.config_dir, manifest["name"],
                body["labels_text"], home)
            # The manifest must POINT at the labels file it owns -- writing the
            # file alone left manifest.labels empty, so the listing said
            # "0 labels", Edit found nothing to load, and selecting the model
            # fell back to coco.txt instead of its own classes.
            manifest = models_service.save_manifest(
                request.app.state.config_dir,
                {**manifest, "labels": stored if count else ""}, home)
            manifest["labels_count"] = count
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Editing the RUNNING model's manifest must take effect. Otherwise the
    # classes and confidence just typed sit in a file while the detector keeps
    # the old ones -- and "Use on this system" is disabled precisely because
    # this model is already in use, so there is no button left to press.
    reapplied = False
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if system and systems_dir:
        try:
            active = (detector_control_service.status(systems_dir, system.slug)
                      .get("detector") or {}).get("model")
            if active and manifest["name"] in str(active).split(" + "):
                builtin_service.select(systems_dir, system.slug, manifest["name"],
                                       config_dir=st.config_dir)
                reapplied = True
        except (ValueError, TypeError, OSError):
            pass                        # saving the manifest still succeeded
    return {"ok": True, "model": manifest, "reapplied": reapplied}


@router.post("/api/models/upload", name="models.upload")
async def upload_weights(request: Request, name: str = Form(...),
                         manifest: str = Form("{}"), file: UploadFile = File(...)):
    """Upload the weights themselves, with the manifest alongside.

    One request so a half-finished model -- weights with no manifest, or the
    reverse -- never sits in the store looking deployable.
    """
    perm.require(request, "models.manage")
    try:
        spec = json.loads(manifest or "{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="manifest is not valid JSON")
    blob = await file.read()
    config_dir = request.app.state.config_dir
    home = _models_home(request)
    try:
        stored = models_service.save_weights(config_dir, name, file.filename, blob, home)
        spec.update({"name": name, "file": stored})
        saved = models_service.save_manifest(config_dir, spec, home)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "model": saved, "bytes": len(blob)}


@router.get("/api/models/{name}/file", name="models.file")
def model_file(request: Request, name: str, what: str = "weights"):
    """What a device pulls. Kept simple on purpose: a plain file over the
    session-gated origin, no signed URLs, no second auth scheme."""
    path = models_service.file_path(request.app.state.config_dir, name, what,
                                    _models_home(request))
    if not path:
        raise HTTPException(status_code=404, detail="no such model file")
    return FileResponse(path, filename=path.rsplit("/", 1)[-1],
                        media_type="application/octet-stream")


@router.delete("/api/models/{name}", name="models.delete")
def delete_model(request: Request, name: str):
    perm.require(request, "models.manage")
    removed = models_service.delete(request.app.state.config_dir, name,
                                    _models_home(request))
    if not removed:
        raise HTTPException(status_code=404, detail="no such model")
    return {"ok": True, "removed": removed, **_no_models_guard(request)}


@router.post("/api/models/{name}/deploy", name="models.deploy")
async def deploy(request: Request, name: str):
    """Tell one device to use this model.

    The master does not push weights: it sends the manifest plus the URL to
    fetch them from, and the device pulls. A device on a flaky link retries on
    its own schedule instead of holding an HTTP request open across a 40MB
    upload it may drop.
    """
    perm.require(request, "models.manage")
    body = await request.json()
    host_serial = body.get("host_serial")
    manifest = models_service.get(request.app.state.config_dir, name,
                                  _models_home(request))
    if not manifest:
        raise HTTPException(status_code=404, detail="no such model")
    token = request.app.state.slave_tokens.get(host_serial)
    if token is None:
        raise HTTPException(status_code=404, detail="unknown device")

    payload = dict(manifest)
    payload["weights_url"] = str(request.base_url).rstrip("/") + f"/api/models/{name}/file"
    if manifest.get("labels"):
        payload["labels_url"] = payload["weights_url"] + "?what=labels"

    slave = {"url": token["url"], "token": token["token"]}
    status, response = control_service.apply_settings(slave, "detection", payload)
    if status >= 400:
        raise HTTPException(status_code=status,
                            detail=(response or {}).get("error") or "device refused the model")
    return {"ok": True, "device": host_serial, "model": name, "response": response}


@router.get("/api/detector/footage/schedule", name="detector.footage_schedule_get")
def detector_footage_schedule(request: Request):
    """The saved recurring export, and what it will do next."""
    perm.require(request, "footage.export")
    _systems_dir, slug = _system_context(request)
    from core.services import footage_schedule as sched_service  # noqa: PLC0415
    sched = sched_service.load(slug, request.app.state.config_dir)
    return {"ok": True, "schedule": sched,
            "describe": sched_service.describe(sched),
            # The period it would cover if it fired now: an operator setting
            # "weekly" should be able to see which seven days that means
            # before trusting it to run for a year unattended.
            "next_window": sched_service.period_start(
                sched, sched_service.period_end(sched, time.time())),
            "next_end": sched_service.period_end(sched, time.time())}


@router.put("/api/detector/footage/schedule", name="detector.footage_schedule_put")
async def detector_footage_schedule_save(request: Request):
    """Save the recurring export. Sending is a separate button on purpose.

    Saving must not send: an operator adjusting next month's schedule would
    otherwise post last month's evidence every time they changed the time.
    """
    perm.require(request, "footage.export")
    _systems_dir, slug = _system_context(request)
    body = await request.json()
    from core.services import footage_schedule as sched_service  # noqa: PLC0415
    sched = sched_service.save(body or {}, slug, request.app.state.config_dir)
    return {"ok": True, "schedule": sched,
            "describe": sched_service.describe(sched)}
