# -*- coding: utf-8 -*-
"""
email.py -- The Email page's API: read config, save config, send now.

The page renders from core/routes/system.py like every page; this router is
the behaviour behind its buttons. SMTP and attachment building block, so both
run off the event loop via asyncio.to_thread -- same reason settings.py does.

The app password never returns to the browser: GET masks it, and save()
ignores a masked value coming back.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.services import log_catalog as catalog
from core.services import email as email_service
from core.services import main as main_service

router = APIRouter()


def _require_admin(request):
    """Who the deployment mails, and through whose SMTP account.

    These were session-only: any operator could repoint the sender at their
    own relay, read nothing back (the password is masked on read) but send
    mail AS the deployment to any address they typed. That is an outbound
    spoofing primitive, not an operator setting.
    """
    if not auth_service.is_admin(getattr(request.state, "user", None),
                                 request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


@router.get("/api/email/config", name="email.config")
async def get_config(request: Request):
    state = request.app.state
    return {"ok": True, "config": email_service.public(state.system.slug,
                                                       state.config_dir)}


@router.post("/api/email/config", name="email.save")
async def save_config(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        # Was a 200 carrying ok:false. Every generic client on the box -- the
        # SPA's fetch wrapper included -- reads the status first, so a
        # rejected save was indistinguishable from a saved one.
        raise catalog.http_error("API_BAD_REQUEST",
                                 cause=f"body was {type(body).__name__}, "
                                       "expected an object")
    state = request.app.state
    email_service.save(body, state.system.slug, state.config_dir)
    return {"ok": True, "config": email_service.public(state.system.slug,
                                                       state.config_dir)}


@router.post("/api/email/send", name="email.send")
async def send_now(request: Request):
    """Queue the digest to go out after a short, cancellable delay.

    Not an immediate send: mail is the one action here with no undo once SMTP
    has it, so the window to change your mind has to come BEFORE the send. Pass
    {"delay_s": 0} to send straight away (the scheduler still runs, it just
    does not wait).
    """
    _require_admin(request)
    state = request.app.state
    cfg = email_service.load(state.system.slug, state.config_dir)

    # A manual send may carry one-off overrides (recipient, subject, body)
    # without saving them.
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        for key in ("recipients", "subject", "body"):
            if body.get(key):
                cfg[key] = body[key]

    stats = main_service.aggregate_stats(state.slaves,
                                         main_service.live_local_stats(state))
    # Built NOW, not when the timer fires: the digest must describe the moment
    # the operator pressed Send, not whatever the counters drifted to during
    # the undo window.
    digest = await asyncio.to_thread(email_service.build_digest, stats, state.events)
    queued = email_service.schedule_send(
        cfg, digest, state.system.display_name,
        delay_s=(body or {}).get("delay_s"))
    return {"ok": True, "queued": True, **queued}


@router.post("/api/email/send/cancel", name="email.send_cancel")
async def cancel_send(request: Request):
    """Stop a queued send while its window is still open."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    stopped = email_service.cancel_send((body or {}).get("id"))
    return {"ok": True, "cancelled": stopped,
            **({} if stopped else
               {"message": "the send window had already closed"})}
