# -*- coding: utf-8 -*-
"""
functions.py -- the Functions page's API: read, save, validate expressions.

Thin by contract: presets, validation and config.local.toml storage live in
core/services/functions.py; the detection app interprets the documents
(systems/<slug>/app/functions.py). This file resolves the system, enforces
who may write, and turns a ValueError into a 400.

Writes are admin-only, same as zones and outputs: a function fires events and
physical outputs, so composing one is an administrative act.
"""

from fastapi import APIRouter, HTTPException, Request

from core.services import functions as functions_service
from core.services import main as main_service
from core.services import auth as auth_service
from core.routes import perm
from core.services import redact

router = APIRouter()


def _require_admin(request):
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _system_context(request):
    st = request.app.state
    system = getattr(st, "system", None)
    systems_dir = getattr(st, "systems_dir", None)
    if not system or not systems_dir:
        raise HTTPException(status_code=400,
                            detail="functions are a per-system view; open a system's dashboard")
    return systems_dir, system.slug


def _live_stats(request):
    """The fleet's current flat stats -- the same numbers widgets bind to, so
    the Functions page can show each function's live value."""
    st = request.app.state
    try:
        return main_service.aggregate_stats(st.slaves, main_service.live_local_stats(st))
    except Exception:                                       # noqa: BLE001
        return {}


@router.get("/api/overview/live", name="overview.live")
def overview_live(request: Request):
    """Everything the Main overview renders: the run state, every zone with
    its actions, the live value of each action's metrics, and the violation
    verdict per zone (red / green / no object). One poll, one payload -- the
    page stays a renderer.
    """
    from core.services import detector_control as dc
    from core.services import overview as overview_service
    systems_dir, slug = _system_context(request)
    stats = _live_stats(request)
    try:
        status = dc.status(systems_dir, slug)
    except Exception:                                       # noqa: BLE001
        status = {}
    # Zone cards and the verdict are shaped in one place (overview.zone_overview)
    # so this endpoint and the Dashboard renderer cannot disagree about what a
    # zone's actions mean.
    shaped = overview_service.zone_overview(status, stats)
    return {
        "ok": True,
        "running": bool(status.get("running")),
        "camera": status.get("camera") or {},
        "detector_name": status.get("detector_name"),
        "fps": status.get("fps"),
        # Numbers and WORDS. A rule whose result is text ("People", the
        # shift name) publishes a string, and filtering that out here was one
        # of the three places that turned an operator's word into a 0 on the
        # tile. Dicts stay out: this payload is the live SCALAR of each key,
        # and a per-day bucket is a series a card reads elsewhere.
        "values": {k: v for k, v in stats.items()
                   if isinstance(v, (int, float, str))},
        "frame": {"objects_in_frame": stats.get("objects_in_frame"),
                  "violations_now": stats.get("violations_now")},
        **shaped,
    }


@router.get("/api/functions", name="functions.get")
def get_functions(request: Request):
    systems_dir, slug = _system_context(request)
    stats = _live_stats(request)
    fns = functions_service.get_functions(systems_dir, slug)
    # Each function's current outputs ride along, so the page shows live
    # values with the same 4s polling every other page uses.
    values = {k: v for k, v in stats.items() if k.startswith("fn_")}
    return {
        "ok": True,
        # No credential leaves in a function (an MQTT Subscribe compiled
        # before Connections held its broker's password): redact.py.
        "functions": redact.redact(fns),
        "presets": [dict(p) for p in functions_service.PRESETS],
        "outputs": {k: list(v) for k, v in functions_service.OUTPUTS.items()},
        "variables": {
            **functions_service.variables(systems_dir, slug),
            # Every stat key currently published -- the expression builder's
            # variable picker. Sorted for a stable dropdown. TEXT keys are
            # offered too: an expression can compare words and a card can
            # show one, and a key the operator can SEE on the canvas but not
            # pick here reads as a broken picker.
            "stat_keys": sorted(k for k, v in stats.items()
                                if isinstance(v, (int, float, str))),
        },
        "values": values,
        "editable": perm.editable(request, "workflow."),
    }


@router.put("/api/functions", name="functions.save")
async def save_functions(request: Request):
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    body = await request.json()
    # A list read from GET carries REDACTED where a secret was: put the
    # stored value back rather than saving the placeholder.
    sent = redact.restore(body.get("functions"),
                          functions_service.get_functions(systems_dir, slug)) \
        if isinstance(body.get("functions"), list) else body.get("functions")
    try:
        cleaned = functions_service.set_functions(systems_dir, slug, sent)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "functions": redact.redact(cleaned)}


@router.post("/api/functions/validate-expr", name="functions.validate_expr")
async def validate_expr(request: Request):
    """Live validation for the expression editor: ok + the variable names the
    expression reads, or the readable error to show inline."""
    body = await request.json()
    try:
        names = functions_service.validate_expr(body.get("expr"))
    except ValueError as e:
        return {"ok": False, "error": str(e), "keys": []}
    return {"ok": True, "error": None, "keys": names}
