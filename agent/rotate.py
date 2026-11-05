# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
rotate.py -- Contract v1.3 credential rotation endpoints (agent side).

Zero-downtime rotation (#11), kept in its own router so agent.py stays under
its size ceiling. Both endpoints are bearer-gated (included with
require_token by agent.py):

  POST /api/agent/rotate          mint a new token, store it as PENDING
                                  (old token stays valid), return the new one
  POST /api/agent/rotate/commit   promote the pending token; the old dies

The master calls rotate, switches to the new token, verifies it works, then
calls commit. If it never commits, the old token still works and a later
rotate overwrites the stale pending one -- no lockout is possible.
"""

import secrets

from fastapi import APIRouter, HTTPException

router = APIRouter()

_get_pairing = None


def configure(get_pairing):
    global _get_pairing
    _get_pairing = get_pairing


def _pairing():
    if _get_pairing is None:
        raise HTTPException(status_code=503, detail="agent not configured")
    return _get_pairing()


@router.post("/api/agent/rotate")
def rotate():
    pairing = _pairing()
    if not getattr(pairing, "begin_rotation", None):
        raise HTTPException(status_code=501, detail="rotation not supported")
    token = secrets.token_urlsafe(32)
    pairing.begin_rotation(token)
    return {"ok": True, "token": token}


@router.post("/api/agent/rotate/commit")
def rotate_commit():
    pairing = _pairing()
    if not getattr(pairing, "commit_rotation", None):
        raise HTTPException(status_code=501, detail="rotation not supported")
    pairing.commit_rotation()
    return {"ok": True}
