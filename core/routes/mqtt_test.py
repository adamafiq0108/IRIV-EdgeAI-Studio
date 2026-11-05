# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mqtt_test.py -- the MQTT nodes' test buttons: "Test publish" on MQTT Publish,
"Listen" on MQTT Subscribe. The work is in core/services/mqtt_test.py.

Mounted on every MASTER, beside the workflow routes it borrows helpers from.

Gated like "Send a test now": workflow.edit. A test publish puts a message on
somebody's broker, and a listen reads what flows through it -- both are
things an account that may change the canvas may do, and one that only views
it may not.

The request names a NODE and nothing else. Host, port, topic and login come
from what the box has SAVED and deployed for that node, so these buttons
cannot be pointed at an arbitrary broker, and they test what will actually
run rather than what the browser happens to be showing.
"""

import asyncio
import json
import os
import re

from fastapi import APIRouter, HTTPException, Request

from core.routes import perm
from core.routes.workflow import (_get_store, _live_stats, _system_context,
                                  _the_workflow)
from core import config
from core.services import detector_control as dc
from core.services import workflow as workflow_service
from core.services import functions as functions_service
from core.services import mqtt_clients as clients_service
from core.services import mqtt_test as tester

router = APIRouter()

SAVE_FIRST = "save the workflow first - the test uses what the box has saved"


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


def _node_id(body):
    node = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("node") or ""))[:40]
    if not node:
        raise HTTPException(status_code=400, detail="which node?")
    return node


def _user(request):
    return str(getattr(request.state, "user", "") or "")


def _where(systems_dir, slug, p):
    """(host, port, own login or None) for a node's saved settings: its MQTT
    Connection when it names one, else its own host/port/login (saved before
    Connections). 400 when the Connection has gone."""
    if p.get("conn"):
        row = clients_service.resolve(systems_dir, slug, p["conn"])
        if row is None:
            raise HTTPException(status_code=400,
                                detail="this node's MQTT Connection no longer "
                                       "exists - pick another and save")
        own = (row["username"], row["password"]) if row.get("username") else None
        return row["host"], int(row["port"]), own
    own = (str(p.get("username") or ""), str(p.get("password") or "")) \
        if p.get("username") else None
    return str(p.get("host") or "127.0.0.1"), int(p.get("port") or 1883), own


def _with_brokers(root, slug, functions):
    """`functions` with each Connection-based MQTT Subscribe's broker filled
    in from its system's Connections, so own_subscribers can compare
    addresses. A Connection that cannot be read leaves the function out."""
    try:
        rows = {c["id"]: c for c in clients_service.list_clients(root, slug)}
    except Exception:                                        # noqa: BLE001
        rows = {}
    out = []
    for f in functions or []:
        params = (f or {}).get("params") or {}
        if f.get("type") == "mqtt_sub" and params.get("conn"):
            row = rows.get(params["conn"])
            if row is None:
                continue
            f = {**f, "params": {**params, "host": row["host"], "port": row["port"]}}
        out.append(f)
    return out


def _graph_readonly(slug):
    """Another system's saved graph, READ ONLY -- for its node names.

    Not _get_store: that adopts, seeds and migrates, and may write, which a
    test button must never do to a system nobody opened. A shape this does
    not know just means the node is named by its function id."""
    try:
        with open(workflow_service.path_for(slug), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    if isinstance(data, dict) and isinstance(data.get("workflows"), list):
        first = next((w for w in data["workflows"] if isinstance(w, dict)), {})
        return first.get("graph") or {}
    return data if isinstance(data, dict) and "nodes" in data else {}


def _every_system(systems_dir, slug, functions_here, graph_here):
    """[(slug, label, functions, graph)] for every system on this box, this
    one first. Scans the same two roots detector_control.running_apps does.
    A system that cannot be read is skipped, never an error: the warning is
    a courtesy, and a broken neighbour must not stop a test."""
    out = [(slug, slug, _with_brokers(systems_dir, slug, functions_here), graph_here)]
    roots = {str(systems_dir)}
    try:
        roots.add(str(config.generated_systems_dir()))
    except Exception:                                        # noqa: BLE001
        pass
    seen = {slug}
    for root in sorted(roots):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for other in names:
            if other in seen or not os.path.isdir(os.path.join(root, other)):
                continue
            try:
                fns = functions_service.get_functions(root, other)
            except Exception:                                # noqa: BLE001
                continue
            seen.add(other)
            if any(f.get("type") == "mqtt_sub" for f in fns or []):
                out.append((other, other, _with_brokers(root, other, fns),
                            _graph_readonly(other)))
    return out


def _warning(names):
    quoted = [f"'{n}'" for n in names]
    if len(quoted) == 1:
        return (f"this box's own MQTT Subscribe {quoted[0]} will receive this "
                "message and may act on it")
    listed = ", ".join(quoted[:-1]) + " and " + quoted[-1]
    return (f"this box's own MQTT Subscribe nodes {listed} will receive this "
            "message and may act on it")


@router.post("/api/workflow/mqtt-test/publish", name="workflow.mqtt_test_publish")
async def mqtt_test_publish(request: Request):
    """Two steps, both naming only the node:

      {node, dry: true}           -> what would be sent, and which of this
                                     system's own Subscribe nodes would hear it
      {node, payload?, confirm?}  -> send it

    A test goes to the REAL topic, so it can reach this box's own MQTT
    Subscribe nodes and whatever rules they feed (Yaya, review of 18c4480).
    When one would hear it -- in ANY system on this box -- the send is
    refused without confirm: the page
    shows the warning and asks for a second click, and the server is where
    that second click is enforced. The payload may be edited; the broker and
    topic may not.
    """
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    body = await _body(request)
    node = _node_id(body)
    doc = next((o for o in dc.get_outputs(systems_dir, slug)
                if o.get("id") == f"wf_{node}"[:40]), None)
    if doc is None:
        raise HTTPException(status_code=404, detail=SAVE_FIRST)
    if doc.get("type") not in ("mqtt_publish", "mqtt"):
        raise HTTPException(status_code=400, detail="that node does not publish to MQTT")
    host, port, own = _where(systems_dir, slug, doc)
    topic = str(doc.get("topic") or "")
    value = (_live_stats(request) or {}).get(doc.get("key")) if doc.get("key") else None
    default = tester.publish_payload(doc, value)
    store, _ = _get_store(systems_dir, slug)
    # Every system on the box, not just this one: a Subscribe elsewhere on
    # the same broker is set off by a test just the same (Yaya, 6061106).
    hearers = tester.box_subscribers(
        _every_system(systems_dir, slug,
                      functions_service.get_functions(systems_dir, slug),
                      _the_workflow(store).get("graph") or {}),
        host, port, topic)
    info = {"topic": topic, "broker": f"{host}:{port}",
            "sample": value is None and doc.get("type") != "mqtt",
            "subscribers": hearers,
            "warning": _warning(hearers) if hearers else ""}
    if body.get("dry"):
        problem = tester.publish_topic_problem(topic)
        return {"ok": True, "dry": True, "payload": default,
                "problem": problem, **info}
    payload = body.get("payload")
    if payload is None:
        payload = default
    problem = tester.payload_problem(payload)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if hearers and body.get("confirm") is not True:
        raise HTTPException(status_code=409,
                            detail=_warning(hearers) + " - confirm to send it")
    login = tester.login_for(host, port, own, request.app.state.config_dir)
    try:
        result = await asyncio.to_thread(
            tester.test_publish, host, port, topic, payload,
            login, bool(doc.get("retain")), _user(request))
    except tester.TestRefused as e:
        raise HTTPException(status_code=400, detail=str(e))
    result.update(login="set" if login else "none", **info)
    return result


def _subscriber(systems_dir, slug, node):
    """The deployed mqtt_sub function behind a Subscribe node, or 404."""
    store, _ = _get_store(systems_dir, slug)
    graph = _the_workflow(store).get("graph") or {}
    n = next((x for x in graph.get("nodes") or []
              if x.get("id") == node and x.get("type") == "variable.mqtt"), None)
    if n is None:
        raise HTTPException(status_code=404, detail=SAVE_FIRST)
    fn_id = str((n.get("data") or {}).get("fnId") or "")
    fn = next((f for f in functions_service.get_functions(systems_dir, slug)
               if f.get("id") == fn_id and f.get("type") == "mqtt_sub"), None)
    if fn is None:
        raise HTTPException(status_code=409,
                            detail="this node is not running on the box - fix "
                                   "any error on it and save again")
    return fn.get("params") or {}


@router.post("/api/workflow/mqtt-test/listen", name="workflow.mqtt_test_listen")
async def mqtt_test_listen(request: Request):
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    body = await _body(request)
    node = _node_id(body)
    p = _subscriber(systems_dir, slug, node)
    host, port, own = _where(systems_dir, slug, p)
    login = tester.login_for(host, port, own, request.app.state.config_dir)
    try:
        seconds = float(body.get("seconds") or tester.LISTEN_MAX_S)
    except (TypeError, ValueError):
        seconds = tester.LISTEN_MAX_S
    try:
        snap = tester.start_listen((_user(request), f"{slug}/{node}"), host, port,
                                   str(p.get("topic") or ""), login,
                                   str(p.get("json_path") or ""), seconds,
                                   config_dir=request.app.state.config_dir)
    except tester.TestRefused as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "broker": f"{host}:{port}", **snap}


@router.get("/api/workflow/mqtt-test/listen", name="workflow.mqtt_test_poll")
async def mqtt_test_poll(request: Request, id: str = ""):
    perm.require(request, "workflow.edit")
    try:
        return {"ok": True, **tester.get_listen(id, _user(request))}
    except tester.TestRefused as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/workflow/mqtt-test/listen/stop", name="workflow.mqtt_test_stop")
async def mqtt_test_stop(request: Request):
    perm.require(request, "workflow.edit")
    body = await _body(request)
    try:
        return {"ok": True, **tester.stop_listen(body.get("id"), _user(request))}
    except tester.TestRefused as e:
        raise HTTPException(status_code=404, detail=str(e))
