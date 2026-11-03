# -*- coding: utf-8 -*-
"""
capacity.py -- What this box has left: systems and running detection apps.

Mounted on BOTH the hub and every master, because the header meters that read
it are on both: an operator who opened a system should not have to go back to
the hub to find out whether another one can be started. The counts are facts
about the BOX (how many packages exist, how many apps are live), so they are
the same answer whichever process is asked -- which is the point of one route
rather than two that could drift.

The caps come from the services that enforce them (provision.MAX_SYSTEMS,
detector_control.MAX_RUNNING_APPS) rather than being restated here, and 0
means "no cap": the developer's box is a workshop, so their header states
what is there instead of measuring it against a limit that is not theirs.
"""

from fastapi import APIRouter, Request

from core.routes import perm
from core.services import auth as auth_service

router = APIRouter()


@router.get("/api/capacity", name="capacity.state")
@perm.shared("capacity")
def capacity(request: Request):
    from core.services import detector_control as dc
    from core.services import provision as provision_service

    st = request.app.state
    cfg = getattr(st, "config_dir", None)
    username = getattr(request.state, "user", None) or ""
    workshop = auth_service.is_developer(username, cfg)

    try:
        systems = provision_service.count_systems(
            st.systems_dir, cfg, include_developer=workshop)
    except Exception:                                       # noqa: BLE001
        systems = 0
    # How many detection apps are actually live. The same helper the start
    # path checks before refusing a fifth, so the meter and the refusal can
    # never disagree about what "full" means.
    try:
        running = len(dc.running_apps(st.systems_dir))
    except Exception:                                       # noqa: BLE001
        running = 0
    # Camera FEEDS the running systems capture, against the box's cap. Feeds,
    # not distinct devices: a camera used by two systems is two inferences,
    # two encodes and two previews, so it spends two slots. The empty slug
    # excludes nothing, which turns the "everyone else's cameras" helper the
    # add-button already trusts into the box-wide count -- same arithmetic as
    # the refusal, so meter and refusal cannot disagree.
    try:
        cameras = len(dc._sources_elsewhere_cached(st.systems_dir, "", cfg))
    except Exception:                                       # noqa: BLE001
        cameras = 0

    return {"ok": True,
            "systems": systems,
            "running": running,
            "cameras": cameras,
            "max_systems": 0 if workshop else provision_service.MAX_SYSTEMS,
            "max_running": 0 if workshop else dc.MAX_RUNNING_APPS,
            # The camera cap is the accelerator's, so it binds the workshop
            # too -- a developer's chip drops frames at five cameras exactly
            # like a customer's.
            "max_cameras": dc.MAX_CAMERAS}
