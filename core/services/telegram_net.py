# -*- coding: utf-8 -*-
"""
services/telegram_net.py -- talking to Telegram, and being talked to.

The blocking half of the inbound feature: the HTTPS calls and the dispatch
that turns one update into one reply. Kept apart from telegram_commands.py so
that the part worth testing -- periods, dates, menus, tables -- can be tested
without a network, and this part stays small enough to read in one sitting.

Everything here is called through asyncio.to_thread by the master's poll loop.
Nothing here holds the event loop.

THE ALLOWLIST IS THE WHOLE SECURITY MODEL. Telegram has no login: anybody who
learns a bot's @name can message it, and a bot that answers whoever asks is a
data leak with a friendly face. So a chat is answered only if an admin wrote
its id down (Settings > Telegram). An unknown chat gets SILENCE, not a refusal
-- "you are not allowed" confirms a real system is behind the name, and a
stranger who learns nothing goes away.

TWO WAYS ONTO THE LIST. A chat id the admin wrote down (the whole chat is
answered, as before), or a PERSON who sent the bot an email address the admin
listed (telegram_links.py). A linked person is answered in their private chat
and in the groups they claimed in, and nobody else in those groups is -- a
group is never let in as a whole by an email claim. The messages answered
from a stranger are the claim itself, answered with the same sentence
whether the address is unknown, malformed or already held by someone else,
so the list cannot be read out by guessing -- and, on a bot that lists
addresses, a /start, answered with a request for the address (Tok Aba,
2026-10-14; see "/start from somebody the bot does not know yet" below). A
stranger's other messages still meet silence.
"""

import json
import re
import threading
import time
import uuid
import urllib.error
import urllib.request

#: How long one getUpdates call may hold a quiet connection open. Telegram
#: returns the moment anything arrives, so this is not latency -- it is how
#: rarely the box has to ask again when nothing is happening.
POLL_HOLD_S = 50

#: Ceilings on one exchange. The reply cap is Telegram's own (4096 characters);
#: the rest are ours, so a malformed update cannot become a large allocation.
MAX_UPDATES = 20
MAX_TEXT = 300

#: A command answered with data has to run a query, and a query that hangs
#: holds a worker thread. Shorter than the console's own budget: somebody is
#: waiting with a phone in their hand.
QUERY_TIMEOUT_S = 20

#: The line ending multipart form-data is defined in terms of. Spelled by
#: code point rather than as an escape: an escape that gets mangled in transit
#: breaks an upload with no error to read.
#: Telegram's own limit on one photo sent as a photo. A frame past it is
#: skipped with a line in the log rather than failing the whole album.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_CRLF = chr(13) + chr(10)

#: Email claims: at most CLAIM_LIMIT attempts per Telegram user, and per group
#: chat, in CLAIM_WINDOW_S. Past it every attempt is ignored without a reply
#: for CLAIM_COOLDOWN_S. Guessing an allowed address is the one attack this
#: feature invites, so it is made slow. In memory: a master restart (an
#: update, an AI switch, a crash) forgets these counts, which is why the
#: per-BOT ceiling on failed claims (telegram_links.BOT_FAIL_LIMIT) is kept
#: on disk -- many throwaway accounts cannot multiply that one.
CLAIM_LIMIT = 5
CLAIM_WINDOW_S = 600
CLAIM_COOLDOWN_S = 1800

#: The ONE answer to a claim that did not link. Identical for an address
#: that is not listed, one that is not an address, and one somebody else
#: already holds -- three different answers would let a stranger read the
#: list out one guess at a time.
CLAIM_REFUSED = "That address is not set up for this bot."
CLAIM_LINKED = ("You are linked. Send /start to see what this bot can "
                "answer.")

#: THE /start WORDING for somebody the bot does not know yet (Tok Aba,
#: 2026-10-14). All of it here, so it can be edited in one place. Plain HTML
#: text: {bot} is the bot's own @name from Telegram (public, it is what the
#: person just typed to find it), and START_HELLO_PLAIN stands in when that
#: name cannot be asked. None of these ever names the system, a listed
#: address, or why somebody is not answered.
START_HELLO = "Welcome to {bot}."
START_HELLO_PLAIN = "Welcome."
START_ASK_PRIVATE = ("To use this bot, send the email address your "
                     "administrator added for you.")
START_ASK_GROUP = ("To use this bot in this group, reply to this message with "
                   "the email address your administrator added for you.")
START_RETRY_PRIVATE = ("That does not look like an email address. Send only "
                       "the address, for example name@company.com.")
START_RETRY_GROUP = ("That does not look like an email address. Reply to my "
                     "message with only the address, for example "
                     "name@company.com.")

#: How long, after a /start, the bot waits for that person's address.
START_WAIT_S = 600
#: Not-an-address answers within one wait that get a gentle re-prompt; past
#: it the wait ends and the person is silent to the bot until /start again.
START_RETRIES = 2
#: Prompts per chat, and per person, in START_PROMPT_WINDOW_S. Past it a
#: /start is ignored without a reply: the prompt is the one thing a stranger
#: can make the bot say, so it is not made a way to spam a group.
START_PROMPT_LIMIT = 3
START_PROMPT_WINDOW_S = 3600
#: Bounds on the in-memory tables below.
MAX_WAITING = 2000
MAX_PROMPT_KEYS = 5000

#: The clock, as a name tests can replace.
_now = time.time

_claim_lock = threading.Lock()
_attempts = {}          # "u:<user>" / "c:<chat>" -> {"times": [...], "until": t}
_awaiting = {}          # (bot, chat, user) -> {"until": t, "retries": n}
_prompts = {}           # "c:<bot>:<chat>" / "u:<bot>:<user>" -> [times]
_noticed = {}           # (bot, email, user) -> when the admins were last told
_usernames = {}         # bot user id -> its @username from getMe ("" if unknown)


def _api(token, method, payload, timeout=20):
    """One Bot API call. Returns the parsed result, or raises."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        answer = json.loads(resp.read(1 << 20).decode("utf-8", "replace"))
    if not answer.get("ok"):
        raise RuntimeError(str(answer.get("description") or "Telegram refused it"))
    return answer.get("result")


def poll(token, offset, hold=POLL_HOLD_S):
    """(updates, next_offset). Blocks until something arrives or `hold` passes.

    The offset is Telegram's own acknowledgement: asking for `last + 1` is what
    tells it the previous batch was handled, and the reason a restart does not
    replay a week of taps. It is kept in memory rather than on disk on purpose
    -- the worst case is one repeated answer after a restart, which is a duller
    failure than a config write per message.
    """
    payload = {"timeout": int(hold), "limit": MAX_UPDATES,
               # Only the two kinds this answers. Telegram will not queue
               # edits, joins or polls for a bot that never reads them.
               "allowed_updates": ["message", "callback_query"]}
    if offset:
        payload["offset"] = int(offset)
    try:
        updates = _api(token, "getUpdates", payload, timeout=hold + 15) or []
    except urllib.error.HTTPError as exc:
        # 409 is the one worth naming: another process is polling this token,
        # which with one-bot-one-system should be impossible and means a
        # stale master is still running somewhere.
        if exc.code == 409:
            raise RuntimeError(
                "another process is already polling this bot - a bot answers "
                "for one system, so this is a master that should have "
                "stopped") from exc
        raise
    for u in updates:
        offset = max(int(offset or 0), int(u.get("update_id") or 0) + 1)
    return updates, offset


def send(token, chat_id, text, keyboard=None, reply_to=None):
    """One message, HTML, with an optional inline keyboard. Never raises.
    `reply_to` quotes one message (the /start prompt in a group answers the
    person who asked, so their reply box opens on it)."""
    payload = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
               # A report full of ids and camera names is not a place for link
               # previews to expand.
               "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = keyboard
    if reply_to:
        payload["reply_to_message_id"] = int(reply_to)
        # A /start that was deleted meanwhile must not cost the prompt.
        payload["allow_sending_without_reply"] = True
    try:
        _api(token, "sendMessage", payload)
        return True
    except Exception as exc:                                  # noqa: BLE001
        # Telegram refused the HTML ("can't parse entities"): say it once
        # more as plain text rather than say nothing. Every tag here is
        # written by this code and every value escaped, so this is the
        # belt to that brace, never the normal path.
        if _html_refused(exc):
            from core.services import telegram_commands as tg  # noqa: PLC0415
            plain = dict(payload, text=tg.plain(text)[:4096])
            plain.pop("parse_mode", None)
            try:
                _api(token, "sendMessage", plain)
                return True
            except Exception as again:                        # noqa: BLE001
                exc = again
        print(f"[TG] reply to {chat_id} failed: {exc}")
        return False


def _html_refused(exc):
    """Is this Telegram saying it could not read the message's HTML?"""
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 400:
        return True
    return "parse entities" in str(exc).lower()


def send_photo(token, chat_id, png, caption="", keyboard=None):
    """One PNG with a caption. Returns True if Telegram took it.

    sendPhoto is multipart/form-data, not JSON, which is why it does not go
    through _api: the picture is bytes, and base64 in a JSON body would be a
    third more traffic on an uplink that is often a SIM.

    The caption caps at 1024 characters -- a quarter of a message -- so the
    caller sends the headline here and, when there is a table to go with it,
    sends that separately.
    """
    fields = {"chat_id": str(chat_id), "parse_mode": "HTML"}
    if caption:
        fields["caption"] = caption[:1024]
    if keyboard:
        fields["reply_markup"] = json.dumps(keyboard)
    body, content_type = _multipart(
        fields, [("photo", "report.png", png, "image/png")])
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=body, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            answer = json.loads(resp.read(1 << 20).decode("utf-8", "replace"))
        if not answer.get("ok"):
            raise RuntimeError(str(answer.get("description") or "refused"))
        return True
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] photo to {chat_id} failed: {exc}")
        return False


def send_album(token, chat_id, photos, caption=""):
    """Post up to ten photos as ONE media group. Returns True if Telegram took it.

    An album rather than ten messages, because ten messages is ten buzzes on
    somebody's phone for one answer. The caption rides on the FIRST photo --
    that is where Telegram shows an album's text, and putting it anywhere else
    means it is not shown at all.

    The pictures are referenced by attach://<name> from the media list, which
    is how the Bot API takes files that are being uploaded in the same request
    rather than already living on its servers.
    """
    if not photos:
        return False
    media, files = [], []
    for i, photo in enumerate(photos[:10]):
        tag = f"p{i}"
        try:
            with open(photo["path"], "rb") as fh:
                blob = fh.read(MAX_UPLOAD_BYTES + 1)
        except OSError:
            continue                    # deleted since it was listed
        if len(blob) > MAX_UPLOAD_BYTES:
            # Skipped, not fatal: one oversized frame must not cost the others.
            print(f"[TG] {photo.get('id')} is too large for an album")
            continue
        item = {"type": "photo", "media": f"attach://{tag}"}
        if caption and not media:
            item["caption"] = caption[:1024]
            item["parse_mode"] = "HTML"
        media.append(item)
        files.append((tag, f"{tag}.jpg", blob, "image/jpeg"))
    if not media:
        return False
    body, content_type = _multipart(
        {"chat_id": str(chat_id), "media": json.dumps(media)}, files)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMediaGroup",
        data=body, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            answer = json.loads(resp.read(1 << 20).decode("utf-8", "replace"))
        if not answer.get("ok"):
            raise RuntimeError(str(answer.get("description") or "refused"))
        return True
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] album to {chat_id} failed: {exc}")
        return False


def _multipart(fields, files):
    """(body, content_type) for one multipart POST.

    `files` is [(field, filename, bytes, mime)] -- several, because an album
    uploads ten pictures in one request. Hand-rolled because the whole box is
    stdlib-only by convention. The boundary is random rather than fixed: a
    fixed one appearing inside a payload would truncate the upload, and a JPEG
    is arbitrary bytes.
    """
    boundary = "----irivai" + uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        head = (f"--{boundary}{_CRLF}"
                f'Content-Disposition: form-data; name="{key}"{_CRLF}{_CRLF}'
                f"{value}{_CRLF}")
        out += head.encode("utf-8")
    for field, filename, payload, mime in files:
        head = (f"--{boundary}{_CRLF}"
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"{_CRLF}'
                f"Content-Type: {mime}{_CRLF}{_CRLF}")
        out += head.encode("utf-8")
        out += payload
        out += _CRLF.encode("utf-8")
    out += f"--{boundary}--{_CRLF}".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def publish_commands(token, commands):
    """Register the command menu with Telegram, so typing "/" lists them.

    This is Telegram's own feature, and the reason it is worth using instead of
    building a menu of our own: the client shows the list the moment somebody
    types a slash, filters it as they keep typing, and does both offline. A
    menu sent as a message would be scrolled past within a day.

    Sent WHOLESALE, the way the commands themselves are written: the argument
    is the entire menu, so a command deleted from the canvas disappears from
    the keyboard rather than lingering as a suggestion that answers nothing.
    """
    menu = []
    for c in commands[:100]:                      # Telegram's own ceiling
        word = str(c.get("command") or "").strip()[:32]
        if not word:
            continue
        desc = " ".join(str(c.get("title") or "").split())[:256]
        # Telegram refuses a description under 3 characters, and a title that
        # short ("A") is a real thing an operator types.
        if len(desc) < 3:
            desc = f"{word} report"[:256]
        menu.append({"command": word, "description": desc})
    try:
        _api(token, "setMyCommands", {"commands": menu}, timeout=15)
        return True
    except Exception as exc:                                  # noqa: BLE001
        # Never fatal: the bot answers the same words whether or not the menu
        # went up. A box with no outbound HTTPS at this instant should not stop
        # polling over a cosmetic call.
        print(f"[TG] command menu not published: {exc}")
        return False


def menu_digest(commands):
    """What the published menu depends on, as a string to compare against.

    The loop republishes only when this changes. setMyCommands on every poll
    would be a needless call every 50 seconds, forever, on a box whose network
    is somebody's factory uplink.
    """
    return "|".join(f"{c.get('command')}:{c.get('title')}" for c in commands)


def _answer_callback(token, callback_id, text=""):
    """Clear the spinner on a tapped button. Telegram shows it until this is
    called, so an answer that skips it looks like a button that hung."""
    try:
        _api(token, "answerCallbackQuery",
             {"callback_query_id": callback_id, "text": text[:200]}, timeout=10)
    except Exception:                                         # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def allowed(bot, chat_id):
    """May this chat ask anything at all?

    Empty list means NOBODY, deliberately. A default of "everyone" would be a
    bot that answers whoever finds its name, and the cost of being wrong here
    is somebody's production data in a stranger's chat.
    """
    want = str(chat_id or "").strip()
    return bool(want) and want in {str(x).strip()
                                   for x in (bot.get("allow") or [])}


def _real_person(sender, message):
    """Is this a Telegram USER whose id names one person?

    Not when an anonymous group admin posts (Telegram sends them all as one
    shared bot account, and `sender_chat` is set), nor a post made as a
    channel, nor another bot. Linking any of those would let in everyone who
    can post that way.
    """
    sender = sender if isinstance(sender, dict) else {}
    return (bool(str(sender.get("id") or "").strip())
            and not sender.get("is_bot")
            and not (message or {}).get("sender_chat"))


def linked(bot, sender, message, chat_id, chat_type, config_dir=None):
    """May this PERSON ask, here, by an email address they claimed?

    Their private chat with the bot, or a group they claimed in. Never a
    group as a whole: the answer depends on who sent the message, so an
    unlinked member of the same group is not answered.
    """
    from core.services import telegram_links                  # noqa: PLC0415
    if not _real_person(sender, message):
        return False
    user_id = str(sender.get("id")).strip()
    link = telegram_links.live(bot, user_id, config_dir)
    if not link:
        return False
    if chat_type == "private":
        return str(chat_id) == user_id
    if chat_type in ("group", "supergroup"):
        return str(chat_id) in (link.get("groups") or [])
    return False


def _limited(user_id, chat_id, is_group, now):
    """True when this claim attempt is over the rate limit (and so ignored).
    Counts the attempt otherwise."""
    keys = ["u:" + str(user_id or "?")]
    if is_group:
        keys.append("c:" + str(chat_id))
    with _claim_lock:
        blocked = False
        for key in keys:
            st = _attempts.setdefault(key, {"times": [], "until": 0.0})
            if st["until"] > now:
                blocked = True
                continue
            st["times"] = [t for t in st["times"] if now - t < CLAIM_WINDOW_S]
            if len(st["times"]) >= CLAIM_LIMIT:
                st["until"] = now + CLAIM_COOLDOWN_S
                st["times"] = []
                blocked = True
        if not blocked:
            for key in keys:
                _attempts[key]["times"].append(now)
        if len(_attempts) > 5000:
            # Bounded: a flood of distinct ids must not grow this for ever.
            for key in [k for k, v in _attempts.items()
                        if v["until"] <= now and not v["times"]]:
                _attempts.pop(key, None)
    return blocked


def delete_message(token, chat_id, message_id):
    """Delete one message. True if Telegram did. Never raises.

    Works in a private chat; in a group only for a bot that is an admin with
    the Delete messages right. Telegram's refusal is logged by its words --
    never the URL, which carries the token.
    """
    if not message_id:
        return False
    try:
        _api(token, "deleteMessage",
             {"chat_id": chat_id, "message_id": int(message_id)}, timeout=10)
        return True
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] could not delete a message in chat {chat_id}: {exc}")
        return False


def _claim(token, bot, config_dir, message, chat_id, chat_type, text):
    """Somebody sent an email address. Link them, or refuse in one sentence.

    The order is the privacy order: the message holding the address is
    deleted FIRST (an attempt over the rate limit included -- the address is
    the person's whatever we think of the attempt), then the reply, then the
    admins are told.
    """
    from core.services import telegram_links                  # noqa: PLC0415
    sender = message.get("from") or {}
    user_id = str(sender.get("id") or "").strip()
    is_group = chat_type in ("group", "supergroup")
    now = _now()

    gone = delete_message(token, chat_id, message.get("message_id"))
    if is_group:
        # A group that will not let the bot delete is the page's hint to make
        # it an admin. A private chat always lets it; a failure there is the
        # network, and says nothing the admin can act on.
        try:
            telegram_links.note_delete(bot["id"], gone, config_dir)
        except Exception as exc:                              # noqa: BLE001
            print(f"[TG] could not record the delete result: {exc}")

    # The per-BOT pause (S2-L1) before the per-user count: while it holds,
    # nobody's attempt is weighed at all.
    try:
        paused = telegram_links.claims_paused(bot["id"], config_dir, now)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] could not read the claim pause: {exc}")
        paused = 0
    if paused:
        print(f"[TG] ignored an email claim from user {user_id or '?'} in chat "
              f"{chat_id} - claims on this bot are paused")
        return
    if _limited(user_id, chat_id, is_group, now):
        print(f"[TG] ignored an email claim from user {user_id or '?'} in chat "
              f"{chat_id} - over the claim limit")
        return

    outcome, holder = "unknown", None
    if _real_person(sender, message):
        outcome, holder = telegram_links.claim(
            bot["id"], text, sender, chat_id, is_group, config_dir, now=now)
    print(f"[TG] email claim from user {user_id or '?'} in chat {chat_id}: "
          f"{telegram_links.mask(text)} - {outcome}")
    ok = outcome in ("linked", "again", "group")
    send(token, chat_id, CLAIM_LINKED if ok else CLAIM_REFUSED)

    # Told AFTER the reply, so a refused claim and an unknown address take
    # the same path as far as the person waiting can see. Every notice names
    # the claimant by numeric id and username as separate fields
    # (telegram_links.identity), never by the free-text name alone (S2-M1).
    email = telegram_links.clean_email(text)
    who = telegram_links.identity(telegram_links.sender_fields(sender))
    where = (_group_label(message, chat_id) if is_group
             else "in a private chat")
    try:
        if not ok and telegram_links.note_failed_claim(bot["id"], config_dir,
                                                       now=now):
            _tell_admins(
                bot, config_dir, "telegram_claims_paused",
                "Email claims paused on a Telegram bot",
                f'The bot "{bot.get("name")}" received '
                f"{telegram_links.BOT_FAIL_LIMIT} failed email claims within "
                f"{telegram_links.BOT_FAIL_WINDOW_S // 60} minutes, so it "
                "ignores new claims for the next "
                f"{telegram_links.BOT_PAUSE_S // 60} minutes. Somebody may be "
                "guessing the addresses on its list. People already linked "
                "are not affected.")
        if outcome == "linked":
            _tell_admins(bot, config_dir, "telegram_claim",
                         "A Telegram user claimed an email address",
                         f'{who} is now linked to {email} on the bot '
                         f'"{bot.get("name")}" ({where}). Check the user ID '
                         "and username, not the name: anyone can type any "
                         "name. If you do not know this person, remove the "
                         "claim on Settings > Telegram.")
        elif outcome == "group":
            _tell_admins(bot, config_dir, "telegram_claim_group",
                         "A linked Telegram user added a group",
                         f'{who}, linked to {email} on the bot '
                         f'"{bot.get("name")}", claimed it {where}. The bot '
                         "now answers them in that group too, where every "
                         "member sees the answers. If that group should not "
                         "see them, remove the claim on Settings > Telegram.")
        elif outcome in ("taken", "blocked"):
            key = (bot["id"], email, user_id)
            with _claim_lock:
                fresh = now - _noticed.get(key, 0) > 3600
                if fresh:
                    _noticed[key] = now
            if fresh and outcome == "taken":
                _tell_admins(
                    bot, config_dir, "telegram_claim_refused",
                    "A second Telegram user tried to claim an email address",
                    f'{who} sent {email} to the bot "{bot.get("name")}", but '
                    "it is already claimed by "
                    f"{telegram_links.identity(holder)}, so it was refused. "
                    "If the wrong person holds it, remove the claim on "
                    "Settings > Telegram and ask the right person to send it "
                    "again.")
            elif fresh:
                _tell_admins(
                    bot, config_dir, "telegram_claim_refused",
                    "A removed Telegram user tried to claim an email address "
                    "again",
                    f'{who} sent {email} to the bot "{bot.get("name")}" '
                    "after their claim on it was removed, so it was refused. "
                    "They stay blocked from that address until you choose "
                    "Allow again on Settings > Telegram.")
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] could not post the claim notice: {exc}")


def _group_label(message, chat_id):
    """'in the group "Title" (chat ID -100...)' for a notice. The title is
    whatever the group's members named it, so it rides in quotes beside the
    id, never instead of it."""
    title = " ".join(str(((message or {}).get("chat") or {}).get("title")
                         or "").split())[:80]
    return (f'in the group "{title}" (chat ID {chat_id})' if title
            else f"in a group (chat ID {chat_id})")


def _bot_username(token):
    """The bot's own @username (getMe), cached per bot; "" when Telegram
    cannot be asked. Needed only to tell a mention of THIS bot in a group
    from a mention of another one."""
    key = str(token).split(":", 1)[0]
    with _claim_lock:
        if key in _usernames:
            return _usernames[key]
    try:
        me = _api(token, "getMe", {}, timeout=10)
        name = str(me.get("username") or "") if isinstance(me, dict) else ""
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] could not ask Telegram for the bot's name: {exc}")
        return ""
    with _claim_lock:
        if name:
            _usernames[key] = name
    return name


def _to_bot(message, token):
    """Is this message a reply to one of this bot's own messages? A bot's
    user id is the number before the colon in its token."""
    origin = ((message or {}).get("reply_to_message") or {}).get("from") or {}
    return bool(origin.get("is_bot")) and \
        str(origin.get("id") or "") == str(token).split(":", 1)[0]


# ---------------------------------------------------------------------------
# /start from somebody the bot does not know yet (Tok Aba, 2026-10-14)
# ---------------------------------------------------------------------------
# Before this a stranger's /start met silence, and a new person had nothing to
# go on but the admin's instructions. Now /start, and ONLY /start, is answered
# with a request for the address; that person's next message is then read as
# a claim through _claim -- the same normalising, list check, first-claim-
# wins, one neutral refusal, admin notices, per-user/group/bot limits and
# pause. Nothing else a stranger sends is answered, so the bot still says
# nothing to a chat that has not asked to start.
#
# The wait is keyed by bot + chat + USER: in a group, the person who sent
# /start is the only one whose message is read as the answer.

#: "/start", "/start@ThisBot", "/start <deep-link payload>" (the payload is
#: never read or repeated).
_START = re.compile(r"/start(?:@([A-Za-z0-9_]{1,64}))?(?:\s.*)?", re.I | re.S)


def _is_start(text, token):
    """Is this a /start meant for THIS bot? /start@OtherBot is not, and with
    the bot's own name unknown a named /start is not taken either."""
    m = _START.fullmatch(str(text or ""))
    if not m:
        return False
    named = (m.group(1) or "").lower()
    return not named or named == _bot_username(token).lower()


def _wait_key(bot, chat_id, sender):
    return (str(bot.get("id") or ""), str(chat_id),
            str((sender or {}).get("id") or "").strip())


def _waiting_for(key, now):
    """The live wait for this bot + chat + user, or None (an expired one is
    dropped on the way)."""
    with _claim_lock:
        entry = _awaiting.get(key)
        if entry and entry["until"] <= now:
            _awaiting.pop(key, None)
            entry = None
        return entry


def _stop_waiting(key):
    with _claim_lock:
        _awaiting.pop(key, None)


def _prompt_allowed(bot_id, chat_id, user_id, now):
    """Count one /start prompt; False when this chat or this person has had
    START_PROMPT_LIMIT of them in the window (then nothing is counted)."""
    keys = (f"c:{bot_id}:{chat_id}", f"u:{bot_id}:{user_id}")
    with _claim_lock:
        lists = []
        for key in keys:
            times = [t for t in _prompts.get(key, ())
                     if now - t < START_PROMPT_WINDOW_S]
            _prompts[key] = times
            lists.append(times)
        if any(len(t) >= START_PROMPT_LIMIT for t in lists):
            return False
        for times in lists:
            times.append(now)
        if len(_prompts) > MAX_PROMPT_KEYS:
            for key in [k for k, v in _prompts.items()
                        if not v or now - v[-1] >= START_PROMPT_WINDOW_S]:
                _prompts.pop(key, None)
            while len(_prompts) > MAX_PROMPT_KEYS:       # a flood: oldest out
                _prompts.pop(next(iter(_prompts)))
    return True


def _start_wait(key, now):
    with _claim_lock:
        if key not in _awaiting and len(_awaiting) >= MAX_WAITING:
            for k in [k for k, v in _awaiting.items() if v["until"] <= now]:
                _awaiting.pop(k, None)
            while len(_awaiting) >= MAX_WAITING:
                _awaiting.pop(min(_awaiting, key=lambda k: _awaiting[k]["until"]))
        _awaiting[key] = {"until": now + START_WAIT_S, "retries": 0}


def _ask_reply(token, chat_id, text, is_group, message):
    """Send one prompt. In a group it quotes the person's own message and
    opens THEIR reply box (force_reply, selective), because a bot in a group
    sees replies to itself but usually not plain chatter."""
    if is_group:
        send(token, chat_id, text,
             keyboard={"force_reply": True, "selective": True},
             reply_to=(message or {}).get("message_id"))
    else:
        send(token, chat_id, text)


def _start_prompt(token, bot, config_dir, message, chat_id, chat_type):
    """A stranger sent /start: ask for the address, unless claims are paused
    or this chat / person has been asked too often lately (then silence)."""
    from core.services import telegram_commands as tg          # noqa: PLC0415
    from core.services import telegram_links                   # noqa: PLC0415
    sender = message.get("from") or {}
    user_id = str(sender.get("id") or "").strip()
    now = _now()
    try:
        paused = telegram_links.claims_paused(bot["id"], config_dir, now)
    except Exception:                                         # noqa: BLE001
        paused = 0
    if paused or not _prompt_allowed(bot["id"], chat_id, user_id, now):
        print(f"[TG] ignored /start from user {user_id or '?'} in chat {chat_id}"
              f" - {'claims are paused' if paused else 'over the prompt limit'}")
        return
    is_group = chat_type in ("group", "supergroup")
    name = _bot_username(token)
    hello = (START_HELLO.format(bot="@" + tg._esc(name)) if name
             else START_HELLO_PLAIN)
    _ask_reply(token, chat_id,
               hello + "\n" + (START_ASK_GROUP if is_group else START_ASK_PRIVATE),
               is_group, message)
    _start_wait(_wait_key(bot, chat_id, sender), now)
    print(f"[TG] asked user {user_id or '?'} in chat {chat_id} for an email address")


def _start_retry(token, bot, message, chat_id, chat_type, text):
    """A person the bot is waiting on sent something that is not an address.
    Re-prompt at most START_RETRIES times, then stop waiting. True if a
    re-prompt went out. A command, and in a group anything not sent as a
    reply to the bot, is not an answer and leaves the wait as it was."""
    key = _wait_key(bot, chat_id, message.get("from") or {})
    entry = _waiting_for(key, _now())
    is_group = chat_type in ("group", "supergroup")
    if not entry or text.startswith("/"):
        return False
    if is_group and not _to_bot(message, bot["token"]):
        return False
    with _claim_lock:
        if entry["retries"] >= START_RETRIES:
            _awaiting.pop(key, None)
            return False
        entry["retries"] += 1
    _ask_reply(bot["token"], chat_id,
               START_RETRY_GROUP if is_group else START_RETRY_PRIVATE,
               is_group, message)
    return True


def _tell_admins(bot, config_dir, kind, title, body):
    from core.services import inbox                           # noqa: PLC0415
    inbox.notify_admins(kind, title, body, config_dir=config_dir,
                        page="settings", tab="telegram")


def handle(systems_dir, slug, config_dir, bot, update, waiting,
           system_name=""):
    """One update, answered. Never raises -- a bad update must not end the poll.

    `waiting` is the caller's dict of chats that have been asked for dates.
    """
    try:
        _handle(systems_dir, slug, config_dir, bot, update, waiting,
                system_name)
    except Exception as exc:                                  # noqa: BLE001
        print(f"[TG] update {update.get('update_id')} failed: {exc}")


def _handle(systems_dir, slug, config_dir, bot, update, waiting,
            system_name=""):
    from core.services import detector_control as dc          # noqa: PLC0415
    from core.services import telegram_bots as tg_bots        # noqa: PLC0415
    from core.services import telegram_commands as tg         # noqa: PLC0415
    from core.services import telegram_links                  # noqa: PLC0415
    # The bot AS STORED NOW, not as it was when the long poll began up to a
    # minute ago: an address or chat the admin just removed must stop being
    # answered on the next message. A bot deleted meanwhile answers nobody.
    if bot.get("id"):
        bot = tg_bots.get(bot["id"], config_dir)
        if not bot:
            return
    token = bot["token"]

    callback = update.get("callback_query") or {}
    message = update.get("message") or {}
    if callback:
        chat = (callback.get("message") or {}).get("chat") or {}
        sender = callback.get("from") or {}
        text = str(callback.get("data") or "")[:MAX_TEXT]
    else:
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        text = str(message.get("text") or "")[:MAX_TEXT].strip()
    chat_id = str(chat.get("id") or "")
    chat_type = str(chat.get("type") or "")
    if not chat_id or not text:
        return

    # -- an email address, sent to claim access --------------------------------
    # Before the allowlist, because the sender is by definition not on it yet.
    # Only on a bot that lists addresses: a bot that lists none keeps its old
    # silence for everything a stranger sends. In a group only a claim
    # ADDRESSED to the bot counts (telegram_links.claim_text): an address a
    # member shares with the group is not eaten (S2-L3).
    if (not callback and bot.get("emails")
            and chat_type in ("private", "group", "supergroup")):
        named = (chat_type != "private"
                 and (text.startswith("@") or text.lower().startswith("/link@")))
        address = telegram_links.claim_text(
            text, chat_type, _bot_username(token) if named else "",
            _to_bot(message, token))
        # The one more form: the answer to this bot's /start prompt, from
        # the SAME person in the same chat, while the wait lasts. In a group
        # that is how a plain address counts -- only for whoever sent /start.
        wait = _wait_key(bot, chat_id, sender)
        if not address and _waiting_for(wait, _now()) \
                and telegram_links.looks_like_email(text):
            address = text
        if address:
            _stop_waiting(wait)
            _claim(token, bot, config_dir, message, chat_id, chat_type, address)
            return

    if not allowed(bot, chat_id) and not linked(
            bot, sender, {} if callback else message,
            chat_id, chat_type, config_dir):
        # A stranger's /start, on a bot that takes email claims, is asked for
        # the address; their non-address answer gets a gentle re-prompt.
        # Everything else is silence, below.
        if (not callback and bot.get("emails")
                and chat_type in ("private", "group", "supergroup")
                and _real_person(sender, message)):
            if _is_start(text, token):
                _start_prompt(token, bot, config_dir, message, chat_id, chat_type)
                return
            if _start_retry(token, bot, message, chat_id, chat_type, text):
                return
        # Silence. See the module docstring: a refusal confirms the bot is
        # real, and this chat is not meant to know that.
        print(f"[TG] ignored a message from chat {chat_id} - not on the allowlist")
        if callback:
            _answer_callback(token, callback.get("id"), "")
        return

    commands = dc.get_commands(systems_dir, slug)

    # -- a tapped button ------------------------------------------------------
    if callback:
        _answer_callback(token, callback.get("id"))
        parts = text.split(":")
        if len(parts) != 3 or parts[0] != "c":
            return
        cmd = next((c for c in commands if c["id"] == parts[1]), None)
        if cmd is None:
            send(token, chat_id, "That menu is out of date - the command it "
                                 "came from has changed. Send it again.")
            return
        try:
            index = int(parts[2])
        except ValueError:
            return
        if not 0 <= index < len(cmd["buttons"]):
            return
        button = cmd["buttons"][index]
        if button["period"] == "custom":
            waiting[chat_id] = {"cmd": cmd["id"], "btn": index}
            send(token, chat_id,
                 "Which dates? Send one for a single day, or two for a range:\n"
                 "<code>2026-09-01</code>  or  <code>2026-09-01 to 2026-09-07</code>")
            return
        _report(token, chat_id, slug, config_dir, cmd, button,
                systems_dir=systems_dir, system_name=system_name)
        return

    # -- dates somebody was asked for -----------------------------------------
    if chat_id in waiting and not text.startswith("/"):
        pending = waiting.pop(chat_id)
        cmd = next((c for c in commands if c["id"] == pending["cmd"]), None)
        if cmd is None or not 0 <= pending["btn"] < len(cmd["buttons"]):
            send(token, chat_id, "That command has changed since you asked. "
                                 "Send it again.")
            return
        start, stop, why = tg.parse_dates(text)
        if why:
            waiting[chat_id] = pending          # still waiting; say what is wrong
            send(token, chat_id, tg._esc(why))
            return
        _report(token, chat_id, slug, config_dir, cmd,
                cmd["buttons"][pending["btn"]], custom=(start, stop),
                systems_dir=systems_dir, system_name=system_name)
        return

    # -- a typed command ------------------------------------------------------
    if not text.startswith("/"):
        return
    word = re.split(r"[@\s]", text[1:].strip(), 1)[0].lower()
    if word in ("start", "help"):
        # The system's own name, passed in by the master that is serving it --
        # "Warehouse A", not `warehouse_a`. Somebody reading a welcome should
        # see what the sign on the door says.
        send(token, chat_id,
             tg.welcome(commands, system_name or slug, bot.get("welcome") or ""))
        return
    cmd = next((c for c in commands if c["command"] == word), None)
    if cmd is None:
        send(token, chat_id, "I do not answer that one. Send /start to see "
                             "what I can do.")
        return
    head = f"<b>{tg._esc(cmd['title'])}</b>"
    if cmd.get("intro"):
        head += "\n" + tg._esc(cmd["intro"])
    head += "\n\nWhich period?"
    send(token, chat_id, head, tg.keyboard(cmd))


def _report(token, chat_id, slug, config_dir, cmd, button, custom=None,
            systems_dir="", system_name=""):
    """Run one button and send the answer back: rows, a chart, or an album."""
    from core.services import sqlconsole                      # noqa: PLC0415
    from core.services import telegram_commands as tg         # noqa: PLC0415
    start, stop = tg.bounds(button["period"], custom=custom)
    if button.get("kind") == "photos":
        _album(token, chat_id, systems_dir, slug, cmd, button, start, stop)
        return
    params = None
    if start is not None and stop is not None:
        # BOUND, never formatted in. A period arriving from a chat is the one
        # value in this feature that a stranger controls.
        params = {"from": start, "to": stop}
    res = sqlconsole.run(slug, button["sql"],
                         {t["key"]: t for t in button["tables"] if t.get("key")},
                         config_dir=config_dir, params=params)
    if not res.get("ok"):
        # The database's own words, which name the mistake better than we
        # could -- and the person reading this is the one who wrote the query.
        send(token, chat_id,
             f"<b>{tg._esc(cmd['title'])}</b>\nThat report could not be built: "
             f"{tg._esc(res.get('error') or 'the query failed')}")
        return
    columns = res.get("columns") or []
    rows = res.get("rows") or []
    title = f"{cmd['title']} - {button['label']}"
    period = tg.describe(start, stop)
    # The answer as a person would write it (Tok Aba, 2026-10-14): the
    # command's title as the headline, "Today, Wed 14 Oct 2026" under it,
    # the figures as sentences, and a footer naming the system and the
    # period. tg.report keeps the monospace table for a result too wide or
    # too long to read as sentences.
    headline = cmd["title"]
    when = tg.period_line(button["label"], start, stop)
    footer = f"{system_name or slug} · {button['label']}"
    # A picture when the button asked for one AND the rows can carry it.
    # render() returns None for anything it cannot draw -- one column, no
    # numbers, nothing recorded -- and then the table answers, because an
    # operator who picked "bar" wants their figures either way.
    png = None
    if button.get("chart", "table") != "table":
        from core.services import telegram_chart               # noqa: PLC0415
        png = telegram_chart.render(columns, rows, button["chart"],
                                    title=title, subtitle=period)
    if png:
        # The picture's caption carries the same headline, period and footer.
        send_photo(token, chat_id, png,
                   caption=(f"{tg.HEADLINE_EMOJI} <b>{tg._esc(headline)}</b>\n"
                            f"<i>{tg._esc(when)}</i>\n\n<i>{tg._esc(footer)}</i>"),
                   keyboard=tg.keyboard(cmd))
        return
    send(token, chat_id,
         tg.report(columns, rows, title=headline, period=when, footer=footer),
         tg.keyboard(cmd))


def _album(token, chat_id, systems_dir, slug, cmd, button, start, stop):
    """Send one button's snapshots as an album."""
    from core.services import telegram_commands as tg         # noqa: PLC0415
    from core.services import telegram_photos as photos       # noqa: PLC0415
    title = f"{cmd['title']} - {button['label']}"
    period_text = tg.describe(start, stop)
    # The 24-hour ceiling, enforced again HERE because "Pick dates" is the one
    # window the canvas could not check: the dates arrive from a chat months
    # after anybody looked at the node.
    if tg.span_hours(start, stop) > tg.MAX_ALBUM_HOURS:
        send(token, chat_id,
             f"<b>{tg._esc(title)}</b>\nThat is more than "
             f"{tg.MAX_ALBUM_HOURS} hours of pictures. Ask for a day or less "
             "- for a longer stretch use Footage → Export, which sends the "
             "originals as a zip.", tg.keyboard(cmd))
        return
    rows, total = photos.pick(systems_dir, slug, start, stop,
                              camera=button.get("camera") or "",
                              most=button.get("most"))
    caption = (f"<b>{tg._esc(title)}</b>\n"
               + tg._esc(photos.summary(rows, total, period_text,
                                        _camera_name(rows, button))))
    if not rows:
        send(token, chat_id, caption, tg.keyboard(cmd))
        return
    # More than one album's worth goes as several albums, oldest first, and
    # only the first carries the caption -- the same text repeated six times
    # reads as six separate answers.
    sent = 0
    for i in range(0, len(rows), photos.MAX_ALBUM):
        chunk = rows[i:i + photos.MAX_ALBUM]
        if send_album(token, chat_id, chunk, caption if i == 0 else ""):
            sent += len(chunk)
    if not sent:
        send(token, chat_id, caption + "\n(the pictures could not be sent)",
             tg.keyboard(cmd))
        return
    # The keyboard travels separately: a media group takes no reply markup, so
    # without this the buttons vanish the moment somebody uses one.
    send(token, chat_id, "Ask again:", tg.keyboard(cmd))


def _camera_name(rows, button):
    """What to call the camera in a caption, when the button names one."""
    if not button.get("camera"):
        return ""
    for row in rows:
        if row.get("camera_name"):
            return str(row["camera_name"])
    return str(button.get("camera") or "")
