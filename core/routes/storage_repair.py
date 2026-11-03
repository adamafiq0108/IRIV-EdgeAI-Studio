# -*- coding: utf-8 -*-
"""
storage_repair.py -- "Try again" on the Dataset pages' "Storage is not
writable" card (frontend/src/components/NoStorage.jsx).

The box repairs its own folders on a mounted drive by itself (core/services/
storage_job.py: the hub's pass, and every dataset check at most once a
minute), so the card only appears when that genuinely failed. This is the
button under it: one forced repair through the same root helper, then the
same check the page reads. Nothing the browser sends names a path; the
helper only ever touches the box's own folders under /mnt/nvme.

Whoever may capture training data may ask: it is the folder their capture
writes into, and the repair creates or hands over nothing else.
"""

from fastapi import APIRouter, Request

from core.routes import perm
from core.services import detector_control as detector_control_service

router = APIRouter()


@router.post("/api/detector/dataset/repair", name="detector.dataset_repair")
def dataset_repair(request: Request):
    # Sync on purpose: the repair is a sudo call, and FastAPI runs a plain
    # def in its thread pool, off the event loop.
    perm.require(request, "dataset.capture")
    return {"ok": True, **detector_control_service.dataset_state(force=True),
            "root": detector_control_service.DATASET_ROOT}
