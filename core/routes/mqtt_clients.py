# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mqtt_clients.py -- the MQTT Connections an MQTT Subscribe or MQTT Publish
picks (core/services/mqtt_clients.py). Mounted on every MASTER.

The same shape as the Modbus Servers (routes/models.py): list, add or change,
delete. Reading is for anyone who may open the workflow -- the Connection row
on a node needs the list -- and never includes a password, only whether one
is set. Changing a connection is workflow.edit: the people wiring the nodes
are the people who know which broker they meant, customer box or not.
"""

from fastapi import APIRouter, HTTPException, Request

from core.routes import perm
from core.routes.workflow import _system_context
from core.services import mqtt_clients as svc

router = APIRouter()

URL = "/api/mqtt/clients"


def _signed_in(request):
    if not getattr(request.state, "user", None):
        raise HTTPException(status_code=401, detail="sign in first")


@router.get(URL, name="mqtt_clients.list")
def mqtt_clients_list(request: Request):
    _signed_in(request)
    systems_dir, slug = _system_context(request)
    return {"ok": True, "clients": svc.list_clients(systems_dir, slug),
            "editable": perm.editable(request, "workflow.")}


@router.post(URL, name="mqtt_clients.save")
async def mqtt_clients_save(request: Request):
    """Add a connection (no id) or change one (its id). A blank password
    keeps the stored one; clear_password: true removes it."""
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    try:
        body = await request.json()
    except Exception:                                        # noqa: BLE001
        body = {}
    try:
        saved = svc.save(systems_dir, slug, body if isinstance(body, dict) else {})
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "client": saved}


@router.delete(URL + "/{conn_id}", name="mqtt_clients.delete")
def mqtt_clients_delete(request: Request, conn_id: str):
    perm.require(request, "workflow.edit")
    systems_dir, slug = _system_context(request)
    try:
        svc.delete(systems_dir, slug, conn_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}
