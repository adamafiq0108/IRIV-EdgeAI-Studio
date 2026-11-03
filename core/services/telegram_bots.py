# -*- coding: utf-8 -*-
"""
telegram_bots.py -- the box's Telegram bots, defined once.

WHY BOX-WIDE. The bot token and chat id used to be fields on each Telegram
node, so a deployment whose twelve rules all message one operations group held
the same token in twelve places on the canvas. Rotating it (or moving to a new
group) meant finding every node that had it; missing one produced a rule whose
alerts silently stopped arriving, and the token itself sat in the graph file --
which is exported, imported and copied between systems as a template.

A bot is a property of the BOX's relationship with Telegram, not of any one
rule, so it is declared once here and referenced by id. Exactly the shape
mail_accounts.py already uses for mailboxes, and for the same reasons; the two
are deliberately parallel so there is one thing to learn.

ONE BOT, ONE SYSTEM, both ways. `system` names the slug a bot belongs to, and
the pairing is exclusive in both directions: a system has at most one bot, a
bot serves at most one system. That is not tidiness -- it is what lets an
INBOUND message be answered at all. A person typing /summary into a chat says
nothing about which warehouse they mean; the bot that received it does, and
only if it answers for exactly one. (Telegram also allows a single getUpdates
consumer per token, so a token has to belong to one process regardless.)

A bot with no system yet is a bot mid-setup, not an error: it saves, it can be
tested, and it simply appears on no canvas until it is assigned.

Both the hub and every master read this file: they share config/, and the
detection app needs the token embedded in its output document (it runs
standalone and imports nothing from core/).

Stored as a TOML array-of-tables in config/telegram.local.toml:

    [[bots]]
    id = "bot_a1b2c3"
    name = "Line alerts"
    token = "123456789:AA..."
    chat_id = "-1001234567890"
    system = "pcb_sensata"
    allow = ["123456789"]               # chat ids that may send commands
    emails = ["ana@example.com"]        # addresses a person may claim

Who has CLAIMED an address is not here: the master writes claims while the
hub writes this file, so they live in their own file (telegram_links.py).

No framework imports here (docs/conventions.md sec 2).
"""

import re
import secrets

from core import config

#: The one file. Not per-system on purpose -- see the module docstring.
NAME = "telegram"

#: What a stored bot carries. Anything else in a payload is dropped.
FIELDS = ("id", "name", "token", "chat_id", "system", "allow",
          "emails", "welcome")

#: How many addresses one bot may list. A bot answers a team, not a company.
MAX_EMAILS = 200

#: A BotFather token is "<numeric bot id>:<35-ish url-safe chars>". Checked
#: because the failure mode otherwise is a 404 from api.telegram.org at
#: whatever hour the rule fires, which says nothing about the typo.
_TOKEN = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

#: Who a message may be addressed to. A person or group is a NUMBER (groups
#: negative, -100…); @name reaches a public channel or supergroup and nothing
#: else -- the Bot API has no username lookup for private users, so somebody's
#: @handle is not an address however plainly their profile shows it. The length
#: is not policed: ids have grown from six digits to eleven and will grow
#: again, and refusing tomorrow's id is worse than catching today's typo.
CHAT_ID_RE = re.compile(r"^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$")


def bad_chat(chat):
    """Why this chat id cannot work, or "" if its shape is fine."""
    text = str(chat or "").strip()
    if not text:
        return "no chat id"
    if CHAT_ID_RE.match(text):
        return ""
    if text.startswith("@"):
        return (f"{text} is not a chat a bot can send to - @name reaches a "
                "public channel, never a private person. Use their numeric "
                "id: they message the bot once, then it appears in "
                "api.telegram.org/bot<token>/getUpdates.")
    return (f"{text!r} is not a chat id - it is a number (a group's starts "
            "with -100), or @name for a public channel.")


def explain(exc):
    """A Telegram failure, in words that say what to DO about it.

    Everything that talks to Telegram lands here, because the raw failure is
    useless to the person holding it: urllib renders a refusal as "HTTP Error
    400: Bad Request" and throws away the body -- and the body is the whole
    message. Telegram puts the reason in `description`, and its two common
    reasons are both about the RELATIONSHIP rather than the id:

      * a bot may not open a conversation. The person has to message it (or
        press Start) once, after which its numeric id works for ever;
      * a bot may not post into a group it is not a member of. Add it, then
        take the chat id from getUpdates.

    Also: @name addresses a public CHANNEL or supergroup only. There is no
    username lookup for private users in the Bot API -- @someone is not an
    address a bot can send to, whatever their profile shows.
    """
    import json                                              # noqa: PLC0415
    import urllib.error                                      # noqa: PLC0415
    detail = ""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = str(json.loads(exc.read(20000).decode("utf-8", "replace"))
                         .get("description") or "")
        except Exception:                                    # noqa: BLE001
            detail = ""
        if not detail:
            detail = f"HTTP {exc.code}"
    else:
        detail = str(exc)
    low = detail.lower()
    if "chat not found" in low:
        return (detail + " - Telegram will not say more than that, and it "
                "means one of three things: the id is wrong; it is a person "
                "who has never messaged the bot (a bot cannot start a "
                "conversation, so they must send it something first); or it "
                "is a group the bot has not been added to. @name works for a "
                "public channel only - a private person has no @ address a "
                "bot can use, only their numeric id.")
    if "bot was blocked" in low or "bot can't initiate" in low:
        return (detail + " - the recipient has to message the bot (or press "
                "Start) before it may message them.")
    if "not enough rights" in low or "have no rights" in low:
        return (detail + " - the bot is in the chat but may not post there. "
                "Give it permission to send messages, or make it an admin of "
                "the channel.")
    if "unauthorized" in low:
        return (detail + " - the token is wrong or has been revoked "
                "(Settings → Telegram).")
    if "file too large" in low or "request entity too large" in low:
        return (detail + " - Telegram takes about 50MB per document. Narrow "
                "the date range, or send the photos without the clips.")
    return detail or "Telegram refused it"


def _new_id():
    """Short, stable, and not derived from the token: rotating a token or
    pointing a bot at another group must not orphan every node using it."""
    return "bot_" + secrets.token_hex(4)


def load(config_dir=None):
    """Every bot, with real tokens. Server-side callers only."""
    raw = config.load_local(NAME, config_dir) or {}
    out = []
    for item in raw.get("bots") or []:
        if not isinstance(item, dict):
            continue
        bot = _clean(item, allow_blank_token=True)
        if bot.get("id"):
            out.append(bot)
    return out


def default_id(config_dir=None):
    """Which bot is used when nothing names one. The stored choice while it
    still exists, else the first -- a deleted default must not silently stop
    every node that relied on it."""
    raw = config.load_local(NAME, config_dir) or {}
    bots = load(config_dir)
    ids = {b["id"] for b in bots}
    want = str(raw.get("default_id") or "").strip()
    if want in ids:
        return want
    return bots[0]["id"] if bots else ""


def get(bot_id, config_dir=None):
    """One bot by id, or None. An id that no longer exists returns None rather
    than the default: a node pointing at a deleted bot must be reported, not
    quietly redirected into somebody else's group chat."""
    want = str(bot_id or "").strip()
    if not want:
        return None
    for bot in load(config_dir):
        if bot["id"] == want:
            return bot
    return None


def resolve(bot_id, config_dir=None):
    """The bot to send through: the named one, else the default.

    Returns (bot_or_None, problem). `problem` is non-empty when the caller
    asked for an id that is gone -- worth saying out loud, because a rule
    quietly changing which chat it shouts into is exactly the surprise this
    exists to avoid.
    """
    want = str(bot_id or "").strip()
    if want:
        bot = get(want, config_dir)
        if bot:
            return bot, ""
        return None, (f"the Telegram bot this was set to send from ({want}) no "
                      "longer exists - pick one on the Settings > Telegram page")
    bot = get(default_id(config_dir), config_dir)
    if bot:
        return bot, ""
    return None, ("no Telegram bot is set up - add one on the "
                  "Settings > Telegram page")


def for_system(slug, config_dir=None):
    """The one bot that answers for this system, or None.

    The whole reason `system` exists. A Telegram node reads this instead of
    offering a picker (there is nothing to pick -- the system has one bot or
    none), and the future inbound poller reads it to know whose data a
    /summary is about.
    """
    want = str(slug or "").strip().lower()
    if not want:
        return None
    for bot in load(config_dir):
        if bot.get("system") == want:
            return bot
    return None


def public(config_dir=None):
    """What the browser may see: every bot, tokens masked.

    A token never returns unmasked. Whoever holds it can post as the bot into
    every chat it is in, and a credential returned to a browser ends up in a
    cache, a screenshot and a bug report.
    """
    from core.services import telegram_links                    # noqa: PLC0415
    out = []
    for bot in load(config_dir):
        shown = dict(bot)
        shown["token"] = config.mask(bot.get("token"))
        shown["has_token"] = bool(bot.get("token"))
        # Who holds each listed address, and whether a group refused to let
        # the bot delete a claim message. A holder comes as three fields --
        # numeric id, @username, free name -- never merged into one string a
        # first name could imitate (telegram_links.sender_fields).
        shown.update(telegram_links.public(bot, config_dir))
        out.append(shown)
    return {"bots": out, "default_id": default_id(config_dir)}


def _clean(data, allow_blank_token=False):
    """One bot, validated. Raises ValueError with a sentence a person can act
    on -- these are pasted by hand and each field has its own common mistake."""
    bot = {}
    bot["id"] = str(data.get("id") or "").strip() or _new_id()
    token = str(data.get("token") or "").strip()
    if token and not _TOKEN.match(token):
        raise ValueError("that does not look like a bot token - BotFather "
                         "issues them as '123456789:AA...'")
    bot["token"] = token
    if not token and not allow_blank_token:
        raise ValueError("the bot token is required")
    # OPTIONAL, and that is the point. A bot is WHO the box posts as; the chat
    # is who hears it, and one bot legitimately talks to the maintenance group,
    # the shift channel and one supervisor -- each named on the node that
    # sends. Requiring one here made adding a bot wait on a number somebody had
    # to go and fetch first, and forced them to nominate a "main" chat nothing
    # necessarily used. What is kept here is a convenience: where the Test
    # button posts, and the fallback for a node that names no chat of its own.
    chat = str(data.get("chat_id") or "").strip()
    if chat:
        wrong = bad_chat(chat)
        if wrong:
            raise ValueError(wrong)
    bot["chat_id"] = chat[:60]
    # The system this bot answers for. A SLUG, not validated against the box's
    # systems here: this module knows nothing about the filesystem (no
    # framework imports, no systems_dir) and a slug that no longer exists is
    # harmless -- it matches nothing, so the bot shows up on no canvas. The
    # page that offers the dropdown is the one that knows the real list.
    bot["system"] = re.sub(r"[^a-z0-9_-]", "",
                           str(data.get("system") or "").strip().lower())[:60]
    # WHO MAY ASK. The allowlist for inbound commands, and empty means nobody.
    # Telegram has no login: anyone who learns a bot's @name can message it, so
    # a bot that answered whoever asked would be a data leak with a friendly
    # face. Admin writes the ids down; there is no self-service door.
    allow, seen = [], set()
    for item in (data.get("allow") if isinstance(data.get("allow"), (list, tuple))
                 else [])[:50]:
        chat = str(item or "").strip()
        if chat and not bad_chat(chat) and chat not in seen:
            seen.add(chat)
            allow.append(chat[:60])
    bot["allow"] = allow
    # WHO MAY CLAIM ACCESS, by email address. The person sends their address
    # to the bot and is linked to it (telegram_links.py) -- the admin no
    # longer has to dig a numeric id out of getUpdates. Same normalising as
    # an account's address, and like the chat ids a line that is not one is
    # dropped rather than refused.
    from core.services import telegram_links                    # noqa: PLC0415
    emails = []
    for item in (data.get("emails") if isinstance(data.get("emails"),
                                                   (list, tuple)) else [])[:MAX_EMAILS]:
        email = telegram_links.clean_email(item)
        if email and email not in emails:
            emails.append(email)
    bot["emails"] = emails
    # An optional first paragraph for /start. Blank builds one from the
    # commands that actually compiled, which is the version that cannot go
    # stale when somebody renames one.
    bot["welcome"] = str(data.get("welcome") or "").strip()[:600]
    # Named so a picker can offer "Line alerts" rather than four numeric chat
    # ids that differ by one digit.
    bot["name"] = (str(data.get("name") or "").strip() or chat
                   or "Telegram bot")[:60]
    return bot


def save_bot(data, config_dir=None):
    """Add or update one bot. Returns its id.

    An update with a MASKED or empty token keeps the stored one, which is what
    lets the page render the form, let somebody fix a chat id or a name, and
    save it back without ever holding the credential.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid bot")
    bots = load(config_dir)
    want = str(data.get("id") or "").strip()
    existing = next((b for b in bots if b["id"] == want), None)
    incoming = dict(data)
    token = incoming.get("token")
    masked = token is not None and str(token) and set(str(token)) == {"*"}
    if existing and (masked or not token):
        incoming["token"] = existing["token"]
    elif masked:
        # A mask with nothing behind it is somebody saving a form they never
        # filled in -- refuse rather than store literal asterisks and fail at
        # 3am with a 404 from Telegram.
        incoming["token"] = ""
    # A field the caller did not MENTION keeps what is stored; a field sent
    # blank is cleared. The page always sends the whole form, so this changes
    # nothing there -- but it is what lets any other caller change one thing
    # without knowing every other, which is how "set the chat id" quietly
    # unassigned a bot from its system.
    if existing:
        for field in ("name", "chat_id", "system", "allow", "emails",
                      "welcome"):
            if field not in incoming:
                incoming[field] = existing.get(field, "")
    bot = _clean(incoming, allow_blank_token=False)
    # ONE bot per system. Assigning this one to a system takes it off whatever
    # held it -- last write wins, the way the default-bot choice already works.
    # Refusing instead would mean an operator moving a bot has to remember to
    # unassign the old one first, and a half-done move is a system that
    # silently reports nowhere.
    if bot["system"]:
        bots = [b if b["id"] == bot["id"] or b.get("system") != bot["system"]
                else {**b, "system": ""} for b in bots]
    if existing:
        bots = [bot if b["id"] == existing["id"] else b for b in bots]
    else:
        bots.append(bot)
    _write(bots, config_dir)
    # An address taken off the list loses its claim NOW, and stays lost: put
    # back later, it starts Unclaimed instead of handing the old claimant
    # their access back. AFTER the write, so a claim racing this save either
    # lands first and is pruned here, or reads the new list and is refused.
    from core.services import telegram_links                    # noqa: PLC0415
    telegram_links.prune(config_dir)
    return bot["id"]


def delete_bot(bot_id, config_dir=None):
    """Remove one. Nodes pointing at it start reporting a missing bot rather
    than quietly sending through somebody else -- see resolve(). Its claims
    go with it."""
    want = str(bot_id or "").strip()
    _write([b for b in load(config_dir) if b["id"] != want], config_dir)
    from core.services import telegram_links                    # noqa: PLC0415
    telegram_links.prune(config_dir)


def set_default(bot_id, config_dir=None):
    want = str(bot_id or "").strip()
    if want and not get(want, config_dir):
        raise ValueError("no such bot")
    raw = config.load_local(NAME, config_dir) or {}
    raw["default_id"] = want
    raw["bots"] = load(config_dir)
    _save_raw(raw, config_dir)


def _write(bots, config_dir):
    raw = config.load_local(NAME, config_dir) or {}
    raw["bots"] = bots
    ids = {b["id"] for b in bots}
    if str(raw.get("default_id") or "") not in ids:
        raw["default_id"] = bots[0]["id"] if bots else ""
    _save_raw(raw, config_dir)


def _save_raw(raw, config_dir):
    # An EMPTY list is not an array-of-tables, and the writer would emit
    # `bots = []`, which reads back as a list of scalars. Dropping the key is
    # the honest empty state and load() answers [] for it.
    if not raw.get("bots"):
        raw.pop("bots", None)
    config.save_local(NAME, raw, config_dir)
