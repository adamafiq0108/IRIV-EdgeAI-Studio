# -*- coding: utf-8 -*-
# Copyright (c) 2026 Cytron Technologies Sdn Bhd. All rights reserved.
"""
mail.py -- the box's sending mailboxes: list, add, update, delete.

Mounted on the HUB (where they are managed, under Settings) and on every
MASTER (where they are used: a Workflow Email node picks one, and the Footage
page sends through one). Same file, same config, one definition -- a master
that could not read this list would be a master whose Email node had nothing
to offer.

ADMIN ONLY, all of it, including the read. The read is masked, but the list of
addresses a deployment can send AS is itself worth gating: naming them is half
of a convincing spoof, and adding one is the whole of it.
"""

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.services import mail_accounts as mail_service

router = APIRouter()


def _require_admin(request):
    """Adding a mailbox means teaching this box to send mail AS an address.
    That is an outbound spoofing primitive, not an operator setting -- the
    same reasoning the Email routes already apply to the sender field."""
    # Site admin and above: sending mail is how an operation reports itself,
    # and an admin who cannot set up a mailbox has alerts that go nowhere.
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _config_dir(request):
    return request.app.state.config_dir


@router.get("/api/mail/accounts", name="mail.accounts")
async def list_accounts(request: Request):
    _require_admin(request)
    return {"ok": True, **mail_service.public(_config_dir(request))}


@router.post("/api/mail/accounts", name="mail.save_account")
async def save_account(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid payload")
    try:
        account_id = mail_service.save_account(body, _config_dir(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": account_id,
            **mail_service.public(_config_dir(request))}


@router.post("/api/mail/accounts/delete", name="mail.delete_account")
async def delete_account(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    detached = mail_service.delete_account(
        (body or {}).get("id"), _config_dir(request),
        getattr(request.app.state, "systems_dir", None))
    return {"ok": True, "detached": detached,
            **mail_service.public(_config_dir(request))}


@router.post("/api/mail/accounts/default", name="mail.set_default")
async def set_default(request: Request):
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    try:
        mail_service.set_default((body or {}).get("id"), _config_dir(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **mail_service.public(_config_dir(request))}


@router.post("/api/mail/accounts/test", name="mail.test_account")
async def test_account(request: Request):
    """Send one mail through an account, to prove the credentials work.

    Worth its own button: an app password that is wrong fails at whatever hour
    the rule happens to fire, in a log nobody is reading. Testing it costs one
    mail and moves that discovery to the moment somebody typed it.
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    account_id = (body or {}).get("id")
    to = str((body or {}).get("to") or "").strip()
    acc, problem = mail_service.resolve(account_id, _config_dir(request))
    if problem or not acc:
        raise HTTPException(status_code=400, detail=problem or "no such mailbox")
    # Defaults to the account's own address: proving a mailbox can send is
    # most easily done by having it write to itself, and it needs no second
    # address typed correctly for the test to mean anything.
    to = to or acc["sender"]

    import asyncio                                           # noqa: PLC0415
    ok, message = await asyncio.to_thread(_send_test, acc, to)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


def _send_test(acc, to):
    """One plain mail, synchronously. Off the event loop via to_thread."""
    import smtplib                                           # noqa: PLC0415
    from email.message import EmailMessage                   # noqa: PLC0415
    msg = EmailMessage()
    msg["Subject"] = "IRIV EdgeAI Studio - mailbox test"
    msg["From"] = acc["sender"]
    msg["To"] = to
    msg.set_content(
        f"This is a test from IRIV EdgeAI Studio.\n\n"
        f"If you are reading it, the mailbox \"{acc['name']}\" "
        f"({acc['sender']}) can send.\n")
    try:
        with smtplib.SMTP(acc["smtp_host"], acc["smtp_port"], timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(acc["sender"], acc["app_password"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        # By far the most common failure, and the generic message for it is
        # useless: Gmail rejects the ACCOUNT password and needs an app
        # password, which is not obvious from "authentication failed".
        return False, ("the mail server refused these credentials - Gmail "
                       "needs an App Password (Google Account → Security → "
                       "2-Step Verification → App passwords), not the account "
                       "password")
    except Exception as e:                                   # noqa: BLE001
        return False, f"could not send: {e}"
    return True, f"sent to {to}"
