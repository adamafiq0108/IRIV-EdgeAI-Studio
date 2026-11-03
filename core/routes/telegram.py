# -*- coding: utf-8 -*-
"""
telegram.py -- the box's Telegram bots: list, add, update, delete, test.

Mounted on the HUB (where they are managed, under Settings) and on every
MASTER (where they are used: a Workflow Telegram node picks one by name). Same
file, same config, one definition -- a master that could not read this list
would be a master whose Telegram node had nothing to offer.

ADMIN ONLY, all of it, including the read. The read is masked, but the list of
chats a deployment can post into is itself worth gating: it names the operations
group, the shift channel, whoever the bot talks to.
"""

from fastapi import APIRouter, HTTPException, Request

from core.services import auth as auth_service
from core.services import telegram_bots as tg_service

router = APIRouter()


def _require_admin(request):
    """Adding a bot means teaching this box to post AS it into a chat. That is
    an outbound messaging primitive, not an operator setting -- same reasoning
    the mailbox routes apply to the sender address."""
    # Site admin and above -- same reasoning as the mailboxes.
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      request.app.state.config_dir):
        raise HTTPException(status_code=403, detail="admin only")


def _config_dir(request):
    return request.app.state.config_dir


async def _body(request):
    try:
        body = await request.json()
    except Exception:                                       # noqa: BLE001
        body = {}
    return body if isinstance(body, dict) else {}


def _systems(request):
    """[{slug, name}] for the assignment dropdown, or [].

    The HUB knows every system on the box (it draws the switcher from the same
    registry); a master knows only itself. The Settings page renders inside the
    hub shell, so the empty list a master would return is never seen -- and
    returning it honestly beats a master inventing a list it cannot have.
    """
    try:
        registry = getattr(request.app.state, "registry", None)
        return [{"slug": sysm.slug, "name": getattr(sysm, "name", "") or sysm.slug}
                for sysm in (registry.all() if registry else [])]
    except Exception:                                       # noqa: BLE001
        return []


@router.get("/api/telegram/bots", name="telegram.bots")
async def list_bots(request: Request):
    _require_admin(request)
    return {"ok": True, "systems": _systems(request),
            **tg_service.public(_config_dir(request))}


@router.post("/api/telegram/bots", name="telegram.save_bot")
async def save_bot(request: Request):
    _require_admin(request)
    try:
        bot_id = tg_service.save_bot(await _body(request), _config_dir(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": bot_id, "systems": _systems(request),
            **tg_service.public(_config_dir(request))}


@router.post("/api/telegram/bots/delete", name="telegram.delete_bot")
async def delete_bot(request: Request):
    _require_admin(request)
    tg_service.delete_bot((await _body(request)).get("id"), _config_dir(request))
    return {"ok": True, "systems": _systems(request),
            **tg_service.public(_config_dir(request))}


@router.post("/api/telegram/bots/default", name="telegram.set_default")
async def set_default(request: Request):
    _require_admin(request)
    try:
        tg_service.set_default((await _body(request)).get("id"),
                               _config_dir(request))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "systems": _systems(request),
            **tg_service.public(_config_dir(request))}


@router.post("/api/telegram/bots/unlink", name="telegram.unlink")
async def unlink(request: Request):
    """Free one claimed email address (the page's Remove).

    The address stays on the bot's list, Unclaimed, so the right person can
    send it again; whoever held it loses access on their next message, and is
    BLOCKED from claiming that address again until the admin allows it
    (/unblock) or takes the address off the list. Taking the address off the
    list altogether is an ordinary save.
    """
    _require_admin(request)
    from core.services import telegram_links                 # noqa: PLC0415
    body = await _body(request)
    telegram_links.unlink(str(body.get("id") or "").strip(),
                          body.get("email"), _config_dir(request))
    return {"ok": True, "systems": _systems(request),
            **tg_service.public(_config_dir(request))}


@router.post("/api/telegram/bots/unblock", name="telegram.unblock")
async def unblock(request: Request):
    """Let a removed Telegram user claim one address again (Allow again)."""
    _require_admin(request)
    from core.services import telegram_links                 # noqa: PLC0415
    body = await _body(request)
    telegram_links.unblock(str(body.get("id") or "").strip(), body.get("email"),
                           body.get("user_id"), _config_dir(request))
    return {"ok": True, "systems": _systems(request),
            **tg_service.public(_config_dir(request))}


@router.post("/api/telegram/bots/test", name="telegram.test_bot")
async def test_bot(request: Request):
    """Post one message through a bot, to prove the token and chat id work.

    Worth its own button. The two ways this is set up wrong -- a mistyped token
    and a chat the bot was never added to -- both fail silently at whatever
    hour the rule fires, in a log nobody is reading. One message moves that
    discovery to the moment somebody pasted them.
    """
    _require_admin(request)
    body = await _body(request)
    bot, problem = tg_service.resolve(body.get("id"), _config_dir(request))
    if problem or not bot:
        raise HTTPException(status_code=400, detail=problem or "no such bot")
    # The chat is the TEST's, not the bot's. A bot no longer has to carry one
    # (each node names who hears it), so this button asks where to prove it
    # works -- falling back to a stored chat when there is one, since a box
    # that always reports into the same group has no reason to retype it.
    chat = str(body.get("chat_id") or "").strip() or str(bot.get("chat_id") or "")
    wrong = tg_service.bad_chat(chat)
    if wrong:
        raise HTTPException(
            status_code=400,
            detail=("type a chat id to test with - the bot does not carry one"
                    if wrong == "no chat id" else wrong))

    import asyncio                                           # noqa: PLC0415
    ok, message = await asyncio.to_thread(_send_test, {**bot, "chat_id": chat})
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message}


def _send_test(bot):
    """One message, synchronously. Off the event loop via to_thread."""
    import json                                              # noqa: PLC0415
    import urllib.error                                      # noqa: PLC0415
    import urllib.request                                    # noqa: PLC0415
    payload = json.dumps({
        "chat_id": bot["chat_id"],
        "text": (f'IRIV EdgeAI Studio test - the bot "{bot["name"]}" can post '
                 "into this chat."),
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot['token']}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        # Telegram says which of the two it is, in a body worth passing on:
        # 401 is the token, 400 "chat not found" is the id (or a bot that was
        # never added to the group). One place turns that into instructions.
        return False, tg_service.explain(e)
    except Exception as e:                                   # noqa: BLE001
        return False, f"could not send: {e}"
    return True, f"sent to {bot['chat_id']}"


# ---------------------------------------------------------------------------
# Who a Telegram node can send to (Tok Aba, 2026-10-14)
# ---------------------------------------------------------------------------

def _bot_for_request(request, bot=""):
    """The bot a Telegram node on this system posts as: the one assigned to
    the system, else (an older graph) the one the node names. The same order
    workflow._with_bot_settings uses, so the people listed are the people the
    node's messages go out to."""
    cfg = _config_dir(request)
    system = getattr(request.app.state, "system", None)
    found = tg_service.for_system(getattr(system, "slug", "") or "", cfg)
    if found is None and str(bot or "").strip():
        found, _why = tg_service.resolve(str(bot).strip(), cfg)
    return found


@router.get("/api/telegram/recipients", name="telegram.recipients")
def recipients(request: Request, bot: str = ""):
    """The people (and groups) linked to this system's bot, for the Telegram
    node's Send to picker.

    PERSONAL DATA, gated like the node it serves: workflow.edit on this
    system (master, or a site admin it was granted to). The email address --
    what the Connected Bots page shows admins -- goes only to a site admin;
    anyone else holding the grant would see names and @usernames. Never
    cached by the browser (no-store).
    """
    from fastapi.responses import JSONResponse              # noqa: PLC0415
    from core.routes import perm                             # noqa: PLC0415
    from core.services import telegram_links                 # noqa: PLC0415
    perm.require(request, "workflow.edit")
    found = _bot_for_request(request, bot)
    data = (telegram_links.people(found, _config_dir(request)) if found
            else {"people": [], "groups": []})
    if not auth_service.is_site_admin(getattr(request.state, "user", None),
                                      _config_dir(request)):
        data["people"] = [{k: v for k, v in p.items() if k != "email"}
                          for p in data["people"]]
    return JSONResponse({"ok": True, "bot": (found or {}).get("name") or "",
                         **data}, headers={"Cache-Control": "no-store"})


@router.get("/api/telegram/recipients/resolve", name="telegram.resolve_recipients")
def resolve_recipients(request: Request, out: str = ""):
    """The chat ids one Telegram output sends to NOW, for the detection app.

    The app has no database and no core imports, so it asks this, its own
    master, at send time (cached a minute on its side). LOOPBACK ONLY, and
    the caller names an OUTPUT id and nothing else: which people is decided
    here, from the compiled document and the live links -- a removed or
    blocked person is never in the answer (telegram_links.recipients).
    """
    import re                                                # noqa: PLC0415
    from fastapi.responses import JSONResponse              # noqa: PLC0415
    from core import net                                     # noqa: PLC0415
    from core.services import detector_control as dc         # noqa: PLC0415
    from core.services import telegram_links                 # noqa: PLC0415
    if not net.local_caller(request):
        raise HTTPException(status_code=403,
                            detail="this list is served to this box only")
    system = getattr(request.app.state, "system", None)
    slug = getattr(system, "slug", "") or ""
    oid = re.sub(r"[^A-Za-z0-9_-]", "", str(out or ""))[:40]
    doc = next((o for o in dc.get_outputs(request.app.state.systems_dir, slug)
                if o.get("id") == oid and o.get("type") == "telegram"
                and o.get("to_mode") in ("all", "selected")), None)
    if doc is None:
        raise HTTPException(status_code=404, detail="no such message")
    found = _bot_for_request(request, doc.get("account") or "")
    chats = (telegram_links.recipients(found, doc["to_mode"], doc.get("to_people") or [],
                                       _config_dir(request)) if found else [])
    return JSONResponse({"ok": True, "chats": chats},
                        headers={"Cache-Control": "no-store"})
