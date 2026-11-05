# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
class_filter.py -- the dashboard's "Class filter" card: run or pause one
NAMED Class Filter of this system's workflow.

Thin by contract: the rules live in core/services/class_filter.py. Mounted
by master_server.py beside the workflow router: a master, never the hub.
"""

import re

from fastapi import APIRouter, HTTPException, Request

from core.routes import perm
from core.services import class_filter as class_filter_service

router = APIRouter()


@router.post("/api/workflow/class-filter", name="workflow.class_filter")
async def class_filter_run(request: Request):
    """Body {node, running}. Gated like the Inject button on the same
    dashboard, on workflow.edit: pausing a filter stops the rules and outputs
    after it, which changes what the workflow does.

    Written to the system's config.local.toml ([class_filters] paused); the
    running app re-reads it on its config poll (app/run.py class_pause), so
    there is no restart, and it survives restarts and updates like any other
    setting of the system."""
    perm.require(request, "workflow.edit")
    st = request.app.state
    system = getattr(st, "system", None)
    if not system or not getattr(st, "systems_dir", None):
        raise HTTPException(status_code=400,
                            detail="the workflow is a per-system view; open "
                                   "a system's dashboard")
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    body = body if isinstance(body, dict) else {}
    node = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("node") or ""))[:60]
    if not node:
        raise HTTPException(status_code=400, detail="which Class Filter?")
    if not isinstance(body.get("running"), bool):
        raise HTTPException(status_code=400,
                            detail="say whether the Class Filter should run")
    try:
        paused = class_filter_service.set_running(
            st.systems_dir, system.slug, node, body["running"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    # ...and the same On / Off onto the Class Filter NODE, its one source of
    # truth: only that node's `running`, nothing else of the workflow. The
    # paused list above is the app's fast path (no recompile); the next
    # applied compile rebuilds it from the nodes, so the two cannot drift.
    from core.services import workflow as workflow_service    # noqa: PLC0415
    store = workflow_service.load_store(system.slug)
    if store:
        try:
            if class_filter_service.set_node_running(store, node, body["running"]):
                workflow_service.save_store(system.slug, store)
        except (ValueError, OSError) as exc:
            # The pause itself has landed; the canvas catches up on open
            # (adopt_paused) or on its next save.
            print(f"[WF] {system.slug}: Class Filter node not updated: {exc}")
    return {"ok": True, "node": node, "running": body["running"],
            "paused": paused}
