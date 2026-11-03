# -*- coding: utf-8 -*-
"""
update.py -- Routes for over-the-air platform updates.

Route parses and returns; core/services/update.py owns checking, and the
applier script owns everything that touches the tree (docs/conventions.md
sec 2).

GATE: admin only, every route. Updating replaces the code the whole box runs
and restarts every system on it -- that is the deployment's most consequential
button, and it belongs to the person who administers the box. Not developer-
only, unlike the template store: a customer appliance HAS no developer
account, and keeping a customer able to take fixes is the entire point.

Two channels, and the second one has a second gate. `stable` carries the
customer build and is what every appliance takes. `dev` carries the developer
build -- core/dev, scripts/dev, the owner frontend -- and is reachable only
when BOTH are true:

  * the ACCOUNT is a developer (the flag in core/services/auth.py, which
    update_user() refuses to grant, so no amount of role-creating in the web UI
    produces one), and
  * the BOX is declared a dev box (config/deploy.toml, which no API and no UI
    writes, defaulting to customer).

Which is the answer to the obvious worry: a customer admin who creates a role
named "dev" gets a display label and nothing else. Their account is not a
developer and their box is not a dev box, so no dev release is ever offered or
installed there. Each channel also has a release repo and a token of its own
(config/update.toml), so a customer box holds nothing that opens the dev repo;
the guard that bites at install time is still the manifest `app` name, which
the applier checks before unpacking a byte.

Nothing here ever returns a release token, an ETag, or an asset URL --
`public_state()` in the service decides what leaves the box.

OFF THE LOOP. check() talks to GitHub (a 60 s timeout on a dead uplink),
apply() and rollback() run systemctl; every one of them goes through
asyncio.to_thread, the idiom the other hub routes use. Called inline from an
`async def` they stalled the whole hub -- every page, every API -- for as
long as GitHub took to not answer (v1.0.15 board report). Two "Check now"
clicks make ONE GitHub request: the service's lock answers the second with
the cached state marked `checking`.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from core.services import log_catalog as catalog

from core.routes.hub import _require_admin, _require_developer
from core.services import update as update_service

router = APIRouter()


def _gate(request, action, channel=None):
    """Admin for everything; developer as well for the dev channel.

    Returns the normalised channel name, so a caller can never act on a
    channel that differs from the one this function checked.
    """
    _require_admin(request, action)
    try:
        channel = update_service.channel_name(channel)
    except update_service.UpdateError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if channel == update_service.CHANNEL_DEV:
        _require_developer(request, f"{action} on the developer channel")
        if not update_service.dev_available(request.app.state.config_dir):
            # A developer account on a customer box. Said as 404-ish "not
            # here" rather than 403 "not you": the channel genuinely does not
            # exist on this deployment, and there is no permission that would
            # make it appear.
            raise HTTPException(
                status_code=404,
                detail="this box has no developer channel")
    return channel


def _dev_offered(request):
    """Whether the UI should show the developer channel at all. Both locks,
    computed server-side -- the frontend is told, never asked."""
    cfg = request.app.state.config_dir
    if not update_service.dev_available(cfg):
        return False
    from core.services import auth as auth_service
    return bool(auth_service.is_developer(
        getattr(request.state, "user", None) or "", cfg))


async def _body(request):
    try:
        return await request.json()
    except Exception:                                       # noqa: BLE001
        return {}


def _channel_of(request, body=None):
    """The channel a request names, from its body or its query string."""
    if body and (body or {}).get("channel"):
        return (body or {}).get("channel")
    return request.query_params.get("channel") or ""


@router.get("/api/hub/update", name="update.state")
def state(request: Request):
    channel = _gate(request, "reading the update state",
                    _channel_of(request))
    return {"ok": True,
            "update": update_service.state(channel,
                                           request.app.state.config_dir),
            "channel": channel,
            "dev_available": _dev_offered(request),
            # The box alone, for any admin: the Customer build tab warns there.
            "dev_box": update_service.dev_available(request.app.state.config_dir),
            "ota_ready": update_service.ota_ready()}


@router.post("/api/hub/update/check", name="update.check")
async def check(request: Request):
    body = await _body(request)
    channel = _gate(request, "checking for updates", _channel_of(request, body))
    return {"ok": True,
            "update": await asyncio.to_thread(
                update_service.check, force=True,
                config_dir=request.app.state.config_dir, channel=channel)}


@router.post("/api/hub/update/token", name="update.token")
async def set_token(request: Request):
    body = await _body(request)
    channel = _gate(request, "setting the update token",
                    _channel_of(request, body))
    configured = update_service.set_token(
        (body or {}).get("token") or "", request.app.state.config_dir, channel)
    # Deliberately does not echo the token back, not even masked-with-a-tail:
    # the only thing a caller needs to know is whether one is stored.
    return {"ok": True, "configured": configured, "channel": channel}


@router.post("/api/hub/update/apply", name="update.apply")
async def apply(request: Request):
    body = await _body(request)
    channel = _gate(request, "applying an update", _channel_of(request, body))
    try:
        status = await asyncio.to_thread(
            update_service.apply, (body or {}).get("version") or "",
            request.app.state.config_dir, channel)
    except update_service.UpdateBusy as e:
        raise catalog.http_error("SYS_UPDATE_BUSY", cause=e) from e
    except update_service.UpdateSuperseded as e:
        # Not a failure: the box asked GitHub again before installing and a
        # newer release had appeared. `details` names it, in the operator's
        # words, so the toast can say which version the card now offers.
        raise catalog.http_error(
            "SYS_UPDATE_SUPERSEDED", cause=e, level="WARNING",
            details=(f"Version {e.newest} is newer than the {e.asked} this "
                     "page showed. The card now offers it - press Update "
                     "again to install it."),
            version=e.asked, newest=e.newest, channel=channel) from e
    except update_service.UpdateError as e:
        # `details`: the refusal in the service's own words ("could not confirm
        # ... no internet"); the card showed only the catalog sentence before.
        raise catalog.http_error("SYS_UPDATE_APPLY_FAILED", cause=e, details=str(e),
                                 version=(body or {}).get("version") or "",
                                 channel=channel) from e
    return {"ok": True, "status": status}


@router.post("/api/hub/update/rollback", name="update.rollback")
async def rollback(request: Request):
    # No channel: a rollback installs nothing. It points the box back at a
    # release already on its disk, whichever channel put it there.
    _require_admin(request, "rolling back an update")
    try:
        status = await asyncio.to_thread(update_service.rollback,
                                         request.app.state.config_dir)
    except update_service.UpdateBusy as e:
        raise catalog.http_error("SYS_UPDATE_BUSY", cause=e) from e
    except update_service.UpdateError as e:
        raise catalog.http_error("SYS_UPDATE_APPLY_FAILED", cause=e, details=str(e),
                                 rollback=True) from e
    return {"ok": True, "status": status}


@router.get("/api/hub/update/status", name="update.status")
def status(request: Request):
    _require_admin(request, "reading update progress")
    return {"ok": True, "status": update_service.status()}
