# -*- coding: utf-8 -*-
"""
telegram_links.py -- who on Telegram has claimed which allowed email address.

WHY EMAIL. The allowed list used to be chat ids only, and a chat id is a
number nobody knows: the admin had to get the person to message the bot, open
getUpdates in a browser, and read the id out of JSON. Now the admin lists the
EMAIL addresses of the people who may ask, and each person sends their own
address to the bot once. The bot links that Telegram USER (not the chat) to the
address, and from then on answers that user.

WHAT IT IS NOT. Sending an address proves only that the sender KNOWS it, not
that they own the mailbox. So the first claim wins, a second claimant is
refused and reported to the admins, and the page says plainly that only the
addresses of known people belong on the list. The admin can free any claim
(Remove) and a freed or de-listed address grants nothing.

WHO CLAIMED IT, NOT WHO THEY SAY THEY ARE (Pipi S2-M1, 2026-10-11). A
Telegram display name is free text: "Ana B (@ana)" can be typed by anyone. So
a link keeps three fields apart -- the numeric user id (the identity), the
@username Telegram issued (checked against Telegram's own rule) and the free
name -- and every place an admin judges a claim shows the id and the username
as fields of their own (identity()). And Remove BLOCKS the removed user from
that address until the admin clears it (Allow again, or taking the address
off the list): otherwise the person just removed could claim it again in the
same minute, ahead of its owner.

TWO WRITERS, ONE FILE. The master's poller writes a claim; the hub writes a
revocation (Remove, an edited list, a deleted bot). Kept OUT of
telegram.local.toml for that reason -- that file holds the tokens and is the
hub's alone to rewrite -- and every read-modify-write here holds the cross-
process lock (cfg_lock.held), so a claim landing while the admin edits the
list cannot write back a link the edit just dropped.

Stored in config/telegram_links.local.toml:

    [delete_failed]
    bot_a1b2c3 = 1760000000          # a group would not let the bot delete

    [claim_failures]
    bot_a1b2c3 = [1760000000]        # failed claims in the last hour

    [claims_paused]
    bot_a1b2c3 = 1760003600          # too many failures: claims off until

    [[links]]
    bot = "bot_a1b2c3"
    email = "ana@example.com"
    user_id = "123456789"            # Telegram's from.id -- the identity
    username = "ana"                 # Telegram's from.username, "" if none
    name = "Ana B"                   # first + last name, free text
    chat_id = "123456789"            # their private chat, "" if never used
    groups = ["-1001234567890"]      # groups they claimed in
    at = 1760000000

    [[blocked]]                      # Removed: may not claim this address
    bot = "bot_a1b2c3"
    email = "ana@example.com"
    user_id = "987654321"
    username = ""
    name = "Ana B (@ana)"
    at = 1760000000

A link only grants anything while its email is STILL on the bot's list (live()
checks it on every read), so revocation does not depend on the prune that
also runs on save.

No framework imports here (docs/conventions.md sec 2).
"""

import re
import time

from core import config

#: The one file. See the module docstring for why it is not telegram.local.toml.
NAME = "telegram_links"

#: Bounds, so a malformed or hostile update cannot grow the file.
MAX_LINKS = 1000
MAX_GROUPS = 20
MAX_BLOCKED = 1000

#: Per BOT, across every account and chat (Pipi S2-L1): BOT_FAIL_LIMIT failed
#: claims inside BOT_FAIL_WINDOW_S pause claims on that bot for BOT_PAUSE_S.
#: telegram_net's per-user and per-group limits stay; this is the ceiling N
#: throwaway accounts cannot multiply. On disk, so a master restart (an
#: update, an AI switch, a crash) does not reset it.
BOT_FAIL_LIMIT = 20
BOT_FAIL_WINDOW_S = 3600
BOT_PAUSE_S = 3600

#: The characters a Telegram username is made of, so a stored handle is one
#: Telegram could have issued and never text that merely looks like one (no
#: spaces, brackets or "@"). Length is not policed: that is Telegram's to say.
_HANDLE = re.compile(r"[A-Za-z0-9_]{1,32}")

#: A claim addressed to the bot in a group: "/link you@x.com", optionally
#: "/link@BotName you@x.com" as Telegram writes it from the command menu.
_LINK_CMD = re.compile(r"/link(?:@([A-Za-z0-9_]{1,64}))?\s+(\S+)\s*", re.I)
#: "@BotName you@x.com".
_MENTION = re.compile(r"@([A-Za-z0-9_]{1,64})\s+(\S+)\s*")


def _path(config_dir):
    return config.root(config_dir) / f"{NAME}.local.toml"


def _held(config_dir):
    from core.services import cfg_lock                          # noqa: PLC0415
    return cfg_lock.held(str(_path(config_dir)))


def clean_email(text):
    """One address, lower-cased, or "" -- the SAME rule account addresses
    use (auth.clean_email), so an address that saves on an account is one
    that can be listed here."""
    from core.services import auth                              # noqa: PLC0415
    try:
        return auth.clean_email(text)
    except ValueError:
        return ""


def looks_like_email(text):
    """Is this message an ATTEMPT to send an address? One token with an @ in
    it, not a command and not a mention. Deliberately looser than
    clean_email: a near-miss ("ana@example") is still somebody typing an
    address into a chat, so it is rate limited, deleted and answered the same
    way as a miss."""
    text = str(text or "").strip()
    if not text or len(text) > 320 or text.startswith(("/", "@")):
        return False
    return len(text.split()) == 1 and "@" in text


def claim_text(text, chat_type, bot_username="", to_bot=False):
    """The address a message claims with, or "" when it is not a claim.

    In a private chat the address alone is a claim, as is "/link <address>".
    In a GROUP only a message ADDRESSED to the bot is (Pipi S2-L3): the
    command (/link or /link@ThisBot), "@ThisBot <address>", or a reply to one
    of the bot's own messages (`to_bot`). A colleague pasting an address into
    the group is not a claim attempt: it is not deleted, not answered and not
    counted. `bot_username` is the bot's own @name (getMe); unknown, a
    mention or a /link@Name cannot be told apart from one meant for another
    bot, so only the plain command and a reply are taken then.
    """
    text = str(text or "").strip()
    if not text or len(text) > 320:
        return ""
    mine = str(bot_username or "").strip().lstrip("@").lower()
    m = _LINK_CMD.fullmatch(text)
    if m:
        named = (m.group(1) or "").lower()
        if named and named != mine:
            return ""
        return m.group(2) if "@" in m.group(2) else ""
    if chat_type == "private":
        return text if looks_like_email(text) else ""
    if chat_type not in ("group", "supergroup"):
        return ""
    m = _MENTION.fullmatch(text)
    if m:
        return m.group(2) if mine and m.group(1).lower() == mine \
            and looks_like_email(m.group(2)) else ""
    return text if to_bot and looks_like_email(text) else ""


def mask(email):
    """An address as a LOG may show it: the local part hidden. Logs are
    copied into bug reports; an address somebody typed into a chat is not
    theirs to keep."""
    text = str(email or "")
    if "@" not in text:
        return "***"
    local, _, domain = text.rpartition("@")
    return (local[:1] + "***@" + domain[:80]) if local else "***@" + domain[:80]


def sender_fields(sender):
    """{user_id, username, name} of a Telegram sender, each its own field.

    The username only when it is one Telegram could have issued; the name is
    whatever the person typed, capped. Never merged into one string: that is
    how "Ana B (@ana)" typed as a first name passed for the real @ana."""
    sender = sender if isinstance(sender, dict) else {}
    handle = str(sender.get("username") or "").strip()
    name = " ".join(str(sender.get(k) or "").strip()
                    for k in ("first_name", "last_name")).strip()
    return {"user_id": str(sender.get("id") or "").strip()[:32],
            "username": handle if _HANDLE.fullmatch(handle) else "",
            "name": " ".join(name.split())[:80]}


def identity(fields):
    """One line an admin can judge a claimant by: the numeric id first, the
    username as its own field ("no username" said out loud), the free name
    last and quoted, so it cannot pass for either of the other two."""
    fields = fields if isinstance(fields, dict) else {}
    out = f"Telegram user ID {fields.get('user_id') or '?'}, "
    out += (f"username @{fields['username']}" if fields.get("username")
            else "no username")
    if fields.get("name"):
        out += f', name shown as "{fields["name"]}"'
    return out


def _load(config_dir):
    raw = config.load_local(NAME, config_dir) or {}

    def table(key):
        value = raw.get(key)
        return dict(value) if isinstance(value, dict) else {}

    def rows(key):
        return [dict(x) for x in (raw.get(key) or []) if isinstance(x, dict)]

    fails = {str(k): [int(t) for t in v if isinstance(t, (int, float))]
             for k, v in table("claim_failures").items() if isinstance(v, list)}
    paused = {str(k): int(v) for k, v in table("claims_paused").items()
              if isinstance(v, (int, float))}
    return {"links": rows("links"), "failed": table("delete_failed"),
            "blocked": rows("blocked"), "fails": fails, "paused": paused}


def _save(st, config_dir):
    raw = {}
    if st["failed"]:
        raw["delete_failed"] = {str(k): int(v) for k, v in st["failed"].items()}
    if st["fails"]:
        raw["claim_failures"] = {k: v for k, v in st["fails"].items() if v}
    if st["paused"]:
        raw["claims_paused"] = dict(st["paused"])
    # An EMPTY list is written as `links = []`, which reads back as a list of
    # scalars; _load ignores those, but dropping the key is the honest empty.
    if st["links"]:
        raw["links"] = st["links"][-MAX_LINKS:]
    if st["blocked"]:
        raw["blocked"] = st["blocked"][-MAX_BLOCKED:]
    config.save_local(NAME, raw, config_dir)


def for_bot(bot_id, config_dir=None):
    """Every stored link for one bot, live or not."""
    return [x for x in _load(config_dir)["links"] if x.get("bot") == bot_id]


def live(bot, user_id, config_dir=None):
    """The link that lets this Telegram user in, or None.

    Judged against the bot AS PASSED, which the caller has just read: an
    address taken off the list stops granting access at once, whatever the
    links file still says.
    """
    want = str(user_id or "").strip()
    emails = set(bot.get("emails") or [])
    if not want or not emails:
        return None
    for link in for_bot(bot.get("id"), config_dir):
        if link.get("user_id") == want and link.get("email") in emails:
            return link
    return None


def _is_blocked(st, bot_id, email, user_id):
    return any(b.get("bot") == bot_id and b.get("email") == email
               and b.get("user_id") == user_id for b in st["blocked"])


def claim(bot_id, email, sender, chat_id, is_group, config_dir=None,
          now=None):
    """Link `sender` to `email` on this bot. Returns (outcome, holder).

    outcome is one of:
      linked   a new link was written (the user's previous one, if any, freed)
      again    this user already held it
      group    this user already held it and claimed it in a NEW group
      taken    another Telegram user holds it -- `holder` is their link
      blocked  the admin removed this user from this address (Remove)
      unknown  not an address on this bot's list (or no such bot)

    The bot is re-read INSIDE the lock, so an address the admin removed a
    moment ago can never be claimed by a poller holding the old list.
    """
    from core.services import telegram_bots                     # noqa: PLC0415
    now = int(time.time() if now is None else now)
    who = sender_fields(sender)
    user_id = who["user_id"]
    email = clean_email(email)
    if not user_id or not email:
        return "unknown", None
    with _held(config_dir):
        bot = telegram_bots.get(bot_id, config_dir)
        if not bot or email not in (bot.get("emails") or []):
            return "unknown", None
        st = _load(config_dir)
        if _is_blocked(st, bot_id, email, user_id):
            return "blocked", None
        links = st["links"]
        holder = next((x for x in links if x.get("bot") == bot_id
                       and x.get("email") == email), None)
        if holder and holder.get("user_id") != user_id:
            return "taken", dict(holder)
        chat = str(chat_id or "").strip()
        if holder:
            outcome = "again"
            link = holder
        else:
            outcome = "linked"
            # ONE address per user per bot. Claiming a second moves them; it
            # never leaves two addresses held by one person.
            links = [x for x in links if not (x.get("bot") == bot_id
                                              and x.get("user_id") == user_id)]
            link = {"bot": bot_id, "email": email, "user_id": user_id,
                    "username": "", "name": "", "chat_id": "", "groups": [],
                    "at": now}
            links.append(link)
            st["links"] = links
        link["username"] = who["username"]
        link["name"] = who["name"]
        if is_group:
            had = link.get("groups") or []
            if outcome == "again" and chat not in had:
                outcome = "group"
            groups = [g for g in had if g != chat]
            link["groups"] = (groups + [chat])[-MAX_GROUPS:]
        else:
            link["chat_id"] = chat
        link.setdefault("groups", [])
        _save(st, config_dir)
    return outcome, None


def unlink(bot_id, email, config_dir=None, now=None):
    """Free one address (the page's Remove) and block its holder from
    claiming it again. True if a link was dropped."""
    want = str(email or "").strip().lower()
    with _held(config_dir):
        st = _load(config_dir)
        gone = [x for x in st["links"]
                if x.get("bot") == bot_id and x.get("email") == want]
        if not gone:
            return False
        st["links"] = [x for x in st["links"] if x not in gone]
        for link in gone:
            uid = str(link.get("user_id") or "")
            if uid and not _is_blocked(st, bot_id, want, uid):
                st["blocked"].append({
                    "bot": bot_id, "email": want, "user_id": uid,
                    "username": str(link.get("username") or ""),
                    "name": str(link.get("name") or ""),
                    "at": int(time.time() if now is None else now)})
        _save(st, config_dir)
    return True


def unblock(bot_id, email, user_id, config_dir=None):
    """Let a removed user claim that address again (the page's Allow again).
    True if a block was cleared."""
    want = str(email or "").strip().lower()
    uid = str(user_id or "").strip()
    with _held(config_dir):
        st = _load(config_dir)
        keep = [b for b in st["blocked"] if not (
            b.get("bot") == bot_id and b.get("email") == want
            and b.get("user_id") == uid)]
        if len(keep) == len(st["blocked"]):
            return False
        st["blocked"] = keep
        _save(st, config_dir)
    return True


def prune(config_dir=None):
    """Drop every link and block whose bot is gone or whose address left its
    list, and the claim state of a bot that is gone. Run after the bot list is
    written, so a re-listed address starts Unclaimed (and unblocked) rather
    than quietly handing the old claimant their access back."""
    from core.services import telegram_bots                     # noqa: PLC0415
    with _held(config_dir):
        bots = {b["id"]: set(b.get("emails") or [])
                for b in telegram_bots.load(config_dir)}
        st = _load(config_dir)
        new = {
            "links": [x for x in st["links"] if x.get("bot") in bots
                      and x.get("email") in bots[x.get("bot")]],
            "blocked": [x for x in st["blocked"] if x.get("bot") in bots
                        and x.get("email") in bots[x.get("bot")]],
            "failed": {k: v for k, v in st["failed"].items() if k in bots},
            "fails": {k: v for k, v in st["fails"].items() if k in bots},
            "paused": {k: v for k, v in st["paused"].items() if k in bots},
        }
        if new != st:
            _save(new, config_dir)


def note_delete(bot_id, ok, config_dir=None, now=None):
    """Remember whether a GROUP let the bot delete a claim message.

    A failure is what the page turns into "make the bot a group admin"; a
    later success clears it. Written only when the answer CHANGES, so a busy
    group is not a config write per message.
    """
    with _held(config_dir):
        st = _load(config_dir)
        failed = st["failed"]
        if ok and bot_id in failed:
            failed.pop(bot_id)
        elif not ok and bot_id not in failed:
            failed[bot_id] = int(time.time() if now is None else now)
        else:
            return
        _save(st, config_dir)


def claims_paused(bot_id, config_dir=None, now=None):
    """Until when claims on this bot are paused (epoch seconds), else 0."""
    now = int(time.time() if now is None else now)
    until = _load(config_dir)["paused"].get(bot_id, 0)
    return until if until > now else 0


def note_failed_claim(bot_id, config_dir=None, now=None):
    """Count one failed claim on this bot. True when it is the one that
    PAUSES claims (the caller tells the admins, once per pause)."""
    now = int(time.time() if now is None else now)
    with _held(config_dir):
        st = _load(config_dir)
        if st["paused"].get(bot_id, 0) > now:
            return False
        st["paused"].pop(bot_id, None)
        times = [t for t in st["fails"].get(bot_id, [])
                 if now - t < BOT_FAIL_WINDOW_S] + [now]
        tripped = len(times) >= BOT_FAIL_LIMIT
        if tripped:
            st["paused"][bot_id] = now + BOT_PAUSE_S
            st["fails"].pop(bot_id, None)
        else:
            st["fails"][bot_id] = times[-BOT_FAIL_LIMIT:]
        _save(st, config_dir)
    return tripped


#: Most chats one message goes to. A Telegram node is a report to a team,
#: not a broadcast; past this it is a mailing list and wants another tool.
MAX_RECIPIENTS = 50

_CHAT_ID = re.compile(r"-?\d{1,20}")


def _live_links(bot, config_dir=None):
    """The links that grant anything right now: the address still on the
    bot's list and the user not blocked from it (Remove). Read fresh on every
    call, so a removal stops the next send."""
    emails = set(bot.get("emails") or [])
    if not emails:
        return []
    st = _load(config_dir)
    return [x for x in st["links"]
            if x.get("bot") == bot.get("id") and x.get("email") in emails
            and str(x.get("user_id") or "")
            and not _is_blocked(st, bot.get("id"), x.get("email"),
                                str(x.get("user_id")))]


def recipients(bot, mode, people=(), config_dir=None):
    """The chat ids a Telegram node in "all" / "selected" mode sends to NOW.

    THE UNIT IS A CHAT. A person is reached in their PRIVATE chat with the bot
    (link.chat_id, set when they claimed privately -- Telegram lets a bot
    message only someone who has messaged it). A person linked only inside a
    group has no private chat on record and is skipped: the bot never posts
    into a whole group on a person's behalf. A GROUP is a target only when
    its chat id is itself one of the selected ids, and only while a live
    link still holds that group.

      all       every live person's private chat
      selected  each id in `people`: a user id -> that person's private chat;
                a group chat id (negative) -> that group
      anything else -> nobody. Never "empty means everyone".

    Deduped, in a stable order, at most MAX_RECIPIENTS.
    """
    live = _live_links(bot, config_dir)
    out = []
    if mode == "all":
        out = [str(x.get("chat_id")) for x in live if str(x.get("chat_id") or "")]
    elif mode == "selected":
        private = {str(x["user_id"]): str(x.get("chat_id") or "") for x in live}
        groups = {str(g) for x in live for g in (x.get("groups") or [])}
        for pid in people or ():
            pid = str(pid or "").strip()
            if not _CHAT_ID.fullmatch(pid):
                continue
            if pid.startswith("-"):
                if pid in groups:
                    out.append(pid)
            elif private.get(pid):
                out.append(private[pid])
    seen, kept = set(), []
    for chat in out:
        if chat not in seen:
            seen.add(chat)
            kept.append(chat)
    return kept[:MAX_RECIPIENTS]


def people(bot, config_dir=None):
    """Who a Telegram node may send to on this bot, for its inspector:
    {"people": [{user_id, username, name, email, private}], "groups":
    [{chat_id, people: [user_id]}]}. `private` says whether the person can be
    reached in their own chat (see recipients). The caller strips the email
    for anyone who is not a site admin."""
    live = _live_links(bot, config_dir)
    rows, groups = [], {}
    for x in live:
        uid = str(x["user_id"])
        rows.append({"user_id": uid, "username": str(x.get("username") or ""),
                     "name": str(x.get("name") or ""),
                     "email": str(x.get("email") or ""),
                     "private": bool(str(x.get("chat_id") or ""))})
        for g in x.get("groups") or []:
            groups.setdefault(str(g), []).append(uid)
    rows.sort(key=lambda r: int(r["user_id"]) if r["user_id"].isdigit() else 0)
    return {"people": rows,
            "groups": [{"chat_id": g, "people": who} for g, who in groups.items()]}


def public(bot, config_dir=None):
    """What the admin page shows for one bot: each allowed address, who if
    anyone holds it (id, username and name as separate fields), who is
    blocked from it -- plus whether a group refused a delete and whether
    claims are paused."""
    st = _load(config_dir)
    bot_id = bot.get("id")
    held = {x.get("email"): x for x in st["links"] if x.get("bot") == bot_id}
    rows = []
    for email in bot.get("emails") or []:
        link = held.get(email) or {}
        rows.append({
            "email": email, "claimed": bool(link),
            "user_id": str(link.get("user_id") or ""),
            "username": str(link.get("username") or ""),
            "name": str(link.get("name") or ""),
            "blocked": [{"user_id": str(b.get("user_id") or ""),
                         "username": str(b.get("username") or ""),
                         "name": str(b.get("name") or "")}
                        for b in st["blocked"]
                        if b.get("bot") == bot_id and b.get("email") == email]})
    until = st["paused"].get(bot_id, 0)
    return {"claims": rows, "delete_failed": bot_id in st["failed"],
            "claims_paused_until": until if until > time.time() else 0}
