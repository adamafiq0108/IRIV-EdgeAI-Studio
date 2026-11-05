# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
workflow_live.py -- GET /api/workflow/live: what the Workflow page and the
Overlay panel POLL, without what they only need once.

Perf audit item L2 (2026-10-02). Both pages polled GET /api/workflow every 4-5
seconds for their live values, and that route is the EDITOR's payload: the
node registry (~55 KB), the whole graph, the mailboxes and bots -- and to
build it the server ran compile_store over the graph, dc.status three times,
get_capture, and the mail and Telegram reads. Every four seconds, per open
tab, for a handful of numbers.

This answers the polled part only: the live values, whether the app runs (and
in which mode), the AI dot, the nodes that wait for detection, and the
variables list the editor refreshes. One dc.status call serves all three of
its answers, and the compile behind "waits for detection" is cached until the
workflow or the system's config changes on disk (their mtimes key it).

Same gate as GET /api/workflow: any account that may open the system's
workflow (the session wall in front of every master route); nothing here is
more than that route already returns.
"""

import os
import threading

from fastapi import APIRouter, Request

from core.routes.workflow import (_camera_ids, _classes, _get_store,
                                  _live_stats, _live_values, _system_context,
                                  _variables, _zones)
from core.services import class_filter as class_filter_service
from core.services import detector_control as dc
from core.services import workflow as workflow_service

router = APIRouter()

_lock = threading.Lock()
#: slug -> (key, waits_for_detection, runs_when). One entry per system; a
#: master serves one system, so this never grows past one in practice.
_waits_cache = {}
#: slug -> (key, paused Class Filter ids): the config read behind the Class
#: Filters' ON/OFF lights, again only when the config changed on disk.
_paused_cache = {}


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def _waits_key(systems_dir, slug):
    """What the compile behind waits_for_detection depends on: the workflow
    file, and the system's config (zones, cameras, capture and the table
    registry all live there). Any write to either changes the key."""
    pkg = dc._pkg_dir(systems_dir, slug)
    return (_mtime(workflow_service.path_for(slug)),
            _mtime(os.path.join(pkg, "config.toml")),
            _mtime(dc._local_path(pkg)))


def waits_for_detection(systems_dir, slug):
    """compile_store(...)["waits_for_detection"], recompiled only when the
    workflow or the config changed since the last answer."""
    return _compiled_marks(systems_dir, slug)[0]


def runs_when(systems_dir, slug):
    """compile_store(...)["runs_when"]: the nodes after an ON/OFF point and
    the state they run in, from the same cached compile."""
    return _compiled_marks(systems_dir, slug)[1]


def _compiled_marks(systems_dir, slug):
    """(waits_for_detection, runs_when) of one compile, cached on the
    workflow's and the config's change stamps."""
    key = _waits_key(systems_dir, slug)
    with _lock:
        hit = _waits_cache.get(slug)
        if hit and hit[0] == key:
            return hit[1], hit[2]
    from core.services import datasets as datasets_service   # noqa: PLC0415
    store, _ = _get_store(systems_dir, slug)
    cls, active = _classes(systems_dir, slug)
    compiled = workflow_service.compile_store(
        store, _zones(systems_dir, slug), cls, active,
        dc.get_capture(systems_dir, slug),
        datasets_service.definitions(systems_dir, slug),
        _camera_ids(systems_dir, slug))
    waits = compiled.get("waits_for_detection") or []
    marks = compiled.get("runs_when") or {}
    with _lock:
        # Keyed on what was read BEFORE compiling: a write that landed during
        # the compile leaves a stale key, so the next poll compiles again.
        _waits_cache[slug] = (key, waits, marks)
    return waits, marks


def filters_paused(systems_dir, slug):
    """The Class Filter node ids the dashboard paused: which of a Class
    Filter's two points (ON / OFF) the canvas lights. Read from the config
    the running app reads them from, cached on its change stamps."""
    key = _waits_key(systems_dir, slug)[1:]
    with _lock:
        hit = _paused_cache.get(slug)
        if hit and hit[0] == key:
            return hit[1]
    try:
        paused = class_filter_service.table(systems_dir, slug)["paused"]
    except Exception:                                        # noqa: BLE001
        paused = []
    with _lock:
        _paused_cache[slug] = (key, paused)
    return paused


def live_state(st):
    """(app_running, runtime, ai) from ONE dc.status answer -- the three
    things GET /api/workflow asked dc.status for separately."""
    st = st or {}
    running = bool(st.get("running"))
    logic = bool(st.get("logic_running"))
    return (bool(st.get("app_running") or running or logic),
            "detection" if running else "logic" if logic else "off",
            {"on": running, "ok": bool(st.get("ai_ok")),
             "reason": str(st.get("ai_error") or "")})


@router.get("/api/workflow/live", name="workflow.live")
def workflow_live(request: Request):
    systems_dir, slug = _system_context(request)
    try:
        st = dc.status(systems_dir, slug)
    except Exception:                                        # noqa: BLE001
        st = None
    app_running, runtime, ai = live_state(st)
    if st is None:
        ai = {"on": False, "ok": False, "reason": "status unavailable"}
    stats = _live_stats(request)
    return {
        "ok": True,
        "values": _live_values(stats),
        "app_running": app_running,
        "runtime": runtime,
        "ai": ai,
        "waits_for_detection": waits_for_detection(systems_dir, slug),
        "runs_when": runs_when(systems_dir, slug),
        # The Class Filters switched off on the dashboard: their OFF point is
        # lit on the canvas, the ON point of every other one (the Detection
        # Stream's two follow `ai.on`).
        "filters_paused": filters_paused(systems_dir, slug),
        # The workflow file's change stamp: a panel holding the graph from
        # GET /api/workflow reloads it when this moves (a save elsewhere).
        "workflow_stamp": _mtime(workflow_service.path_for(slug)),
        "variables": _variables(request, systems_dir, slug, stats),
    }
