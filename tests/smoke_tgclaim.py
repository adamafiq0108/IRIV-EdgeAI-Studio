"""A person gets Telegram access by sending the bot their email address.

The admin lists addresses on the bot (Settings > Telegram); a listed person
sends theirs to the bot and is linked to it as a Telegram USER. This is the
security boundary of the inbound feature, so it is tested end to end through
telegram_net.handle with Telegram faked out -- no network:

  * a claim links the SENDER, privately or in a group, after normalising;
  * an unknown, malformed or already-held address gets ONE identical refusal,
    so the list cannot be read out by guessing; a second claimant is reported
    to the admins;
  * a group is never let in as a whole: only linked members are answered;
  * the message holding the address is deleted, and a group that refuses the
    delete is remembered for the page's hint, never an error;
  * attempts are rate limited per user and per group chat;
  * Remove and an edited list revoke at once, and a re-listed address starts
    Unclaimed;
  * the old chat ID list works as it did, and both lists empty answer nobody;
  * no token and no whole address reaches the log.

Security pass (Pipi, 2026-10-11):
  * S2-M1 the claimant is the numeric id plus a Telegram-issued @username, as
    separate fields; a first name typed as "Ana B (@ana)" cannot pass for
    the real @ana; Remove blocks the removed user from that address until
    Allow again (or the address leaves the list);
  * S2-L1 a per-bot ceiling on failed claims pauses claims, survives a
    master restart (on disk) and tells the admins once;
  * S2-L2 a linked user claiming in a NEW group is told to the admins;
  * S2-L3 in a group only a claim addressed to the bot counts (/link, a
    mention of this bot, a reply to it); a bare address there is left alone.

/start from a stranger (Tok Aba, 2026-10-14), section 14: asked for the
address (by the bot's @name), the next message from THAT user in THAT chat is
the claim, two gentle re-prompts at most, the wait expires, prompts rate
limited per chat and per person, silent while claims are paused; registered
chats get the greeting and use commands with no /start; a removed user reads
like a stranger; nothing else a stranger sends is answered.

    python tests/smoke_tgclaim.py
"""
import contextlib
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core import config                                       # noqa: E402
from core.services import detector_control as dc              # noqa: E402
from core.services import inbox                               # noqa: E402
from core.services import telegram_bots as bots               # noqa: E402
from core.services import telegram_links as links             # noqa: E402
from core.services import telegram_net as net                 # noqa: E402

CFG = tempfile.mkdtemp()
SYS = tempfile.mkdtemp()
TOKEN = "123456789:AAF-abcdefghijklmnopqrstuvwxyz012345"
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- Telegram, faked ----------------------------------------------------------
calls = []              # (method, payload)
refuse = set()          # methods the fake Telegram refuses
clock = [1_000_000.0]
log = io.StringIO()


def fake_api(token, method, payload, timeout=20):
    calls.append((method, payload))
    if method in refuse:
        raise RuntimeError("Bad Request: message can't be deleted")
    if method == "getMe":
        return {"id": int(token.split(":")[0]), "is_bot": True, "username": "Ops_bot"}
    return True


net._api = fake_api
# The per-bot ceiling is tested on its own in section 12; the sections before
# it make more failed claims than it allows.
links.BOT_FAIL_LIMIT = 10 ** 6
BOT_USER = int(TOKEN.split(":")[0])
net._now = lambda: clock[0]
dc.get_commands = lambda systems_dir, slug: []

_seq = [0]


def msg(text, user, chat=None, kind="private", **extra):
    """One update as Telegram sends it."""
    _seq[0] += 1
    sender = {"id": user, "is_bot": False, "first_name": f"U{user}"}
    sender.update(extra.pop("sender", {}))
    chat_id = user if chat is None else chat
    message = {"message_id": _seq[0], "from": sender, "text": text,
               "chat": {"id": chat_id, "type": kind}}
    if "chat_title" in extra:
        message["chat"]["title"] = extra.pop("chat_title")
    message.update(extra)
    return {"update_id": _seq[0], "message": message}


def tap(user, chat, kind="group"):
    _seq[0] += 1
    return {"update_id": _seq[0], "callback_query": {
        "id": f"cb{_seq[0]}", "from": {"id": user, "is_bot": False},
        "data": "c:x:0",
        "message": {"message_id": 1, "chat": {"id": chat, "type": kind}}}}


def run(bot_id, update):
    """Hand one update to the dispatcher; return the calls it made."""
    calls.clear()
    with contextlib.redirect_stdout(log):
        net.handle(SYS, "demo", CFG, bots.get(bot_id, CFG), update, {}, "Demo")
    return list(calls)


def sent(out):
    """(chat, text) per reply. Chats as ints, the way Telegram numbers them
    (the dispatcher carries them as strings)."""
    return [(int(p["chat_id"]), p["text"]) for m, p in out if m == "sendMessage"]


def quiet(out):
    """Nothing sent, nothing deleted (a getMe lookup aside)."""
    return [m for m, _p in out if m != "getMe"] == []


def asked(out, chat):
    """Exactly one reply in `chat`: the /start request for an address (never
    the welcome, never data)."""
    got = sent(out)
    return (len(got) == 1 and got[0][0] == chat
            and (net.START_ASK_PRIVATE in got[0][1] or net.START_ASK_GROUP in got[0][1])
            and "I report on" not in got[0][1])


def deleted(out):
    return [(int(p["chat_id"]), p["message_id"])
            for m, p in out if m == "deleteMessage"]


def notices(kind):
    return [n for n in inbox.for_user("x", True, CFG) if n["kind"] == kind]


def link_of(bot_id, email):
    return next((x for x in links.for_bot(bot_id, CFG) if x["email"] == email), None)


BID = bots.save_bot({"name": "Ops", "token": TOKEN,
                     "emails": ["  Ana@Example.com ", "bo@example.com",
                                "not an address", "bo@example.com"]}, CFG)

# -- 0. the list --------------------------------------------------------------
check("listed addresses are normalised, de-duplicated, and junk is dropped",
      bots.get(BID, CFG)["emails"] == ["ana@example.com", "bo@example.com"],
      bots.get(BID, CFG)["emails"])
check("a save that does not mention the list keeps it",
      bots.save_bot({"id": BID, "name": "Ops"}, CFG)
      and bots.get(BID, CFG)["emails"] == ["ana@example.com", "bo@example.com"])

# -- 1. a claim in a private chat ---------------------------------------------
out = run(BID, msg("  ANA@Example.COM ", 111, sender={"last_name": "B",
                                                       "username": "ana"}))
check("a private claim deletes the message that held the address",
      deleted(out) == [(111, _seq[0])], out)
check("...and confirms, without repeating the address",
      sent(out) == [(111, net.CLAIM_LINKED)], out)
held = link_of(BID, "ana@example.com")
check("...linking the SENDER, case and spaces ignored",
      held and held["user_id"] == "111" and held["chat_id"] == "111"
      and held["groups"] == [], held)
check("...and the admins hear about every new claim, by id and username",
      len(notices("telegram_claim")) == 1
      and 'Telegram user ID 111, username @ana, name shown as "U111 B"'
      in notices("telegram_claim")[0]["body"]
      and "in a private chat" in notices("telegram_claim")[0]["body"],
      notices("telegram_claim"))
check("...stored as three fields, never one merged string",
      held["username"] == "ana" and held["name"] == "U111 B", held)
check("the linked person is answered in their private chat",
      sent(run(BID, msg("/start", 111))) and True)
check("links are on disk, so they survive a restart",
      any(x.get("user_id") == "111"
          for x in config.load_local("telegram_links", CFG).get("links", [])))

# -- 2. a claim in a group ----------------------------------------------------
bare = run(BID, msg("bo@example.com", 222, chat=-100500, kind="supergroup"))
check("S2-L3 a bare address in a group is NOT a claim: not deleted, not "
      "answered, not counted", quiet(bare) and link_of(BID, "bo@example.com") is None
      and "u:222" not in net._attempts, bare)
out = run(BID, msg("/link bo@example.com", 222, chat=-100500, kind="supergroup",
                   chat_title="Line A"))
check("a group claim (/link) links the sender and records the group",
      sent(out) == [(-100500, net.CLAIM_LINKED)]
      and link_of(BID, "bo@example.com")["groups"] == ["-100500"]
      and link_of(BID, "bo@example.com")["chat_id"] == "", out)
check("the linked member is answered in that group",
      sent(run(BID, msg("/start", 222, chat=-100500, kind="supergroup"))))
check("an UNLINKED member of the same group gets no answer, only the email prompt",
      asked(run(BID, msg("/start", 333, chat=-100500, kind="supergroup")), -100500))
check("...and nothing else they send there is answered",
      run(BID, msg("/summary", 333, chat=-100500, kind="supergroup")) == [])
check("...and so is a button they tap, beyond clearing its spinner",
      [m for m, _p in run(BID, tap(333, -100500))] == ["answerCallbackQuery"])
check("a person linked privately is not answered in a group they never claimed in "
      "(asked for the address there, never the welcome)",
      asked(run(BID, msg("/start", 111, chat=-100500, kind="supergroup")), -100500),
      "a group is let in by a claim made THERE, so the answer is never posted "
      "where its owner did not ask for it")
check("the linked person is still answered privately",
      bool(sent(run(BID, msg("/start", 222)))))

# S2-L3: the other two addressed forms, and forms meant for another bot.
for label, update, ok in (
        ("a mention of this bot", msg("@Ops_bot bo@example.com", 222, chat=-100501,
                                      kind="supergroup"), True),
        ("/link@ThisBot", msg("/link@ops_bot bo@example.com", 222, chat=-100502,
                              kind="group"), True),
        ("a reply to the bot", msg("bo@example.com", 222, chat=-100503, kind="group",
                                   reply_to_message={"message_id": 1, "from": {
                                       "id": BOT_USER, "is_bot": True}}), True),
        ("a mention of ANOTHER bot", msg("@Other_bot bo@example.com", 222,
                                         chat=-100504, kind="group"), False),
        ("/link@AnotherBot", msg("/link@Other_bot bo@example.com", 222,
                                 chat=-100505, kind="group"), False),
        ("a reply to a person", msg("bo@example.com", 222, chat=-100506, kind="group",
                                    reply_to_message={"message_id": 1, "from": {
                                        "id": 333, "is_bot": False}}), False)):
    out = run(BID, update)
    chat = update["message"]["chat"]["id"]
    if ok:
        check(f"S2-L3 {label} is a claim in a group",
              sent(out) == [(chat, net.CLAIM_LINKED)]
              and str(chat) in link_of(BID, "bo@example.com")["groups"], out)
    else:
        check(f"S2-L3 {label} is left alone", quiet(out)
              and str(chat) not in link_of(BID, "bo@example.com")["groups"], out)
check("/link works in a private chat too",
      links.claim_text("/link ana@example.com", "private") == "ana@example.com"
      and links.claim_text("ana@example.com", "private") == "ana@example.com"
      and links.claim_text("ana@example.com", "group") == "")

# S2-L2: an existing link that gains a group is told to the admins.
grew = notices("telegram_claim_group")
check("S2-L2 a linked user claiming in a NEW group is told to the admins, "
      "naming the group and the user by id",
      len(grew) == 3 and "chat ID -100501" in grew[-1]["body"]
      + grew[-2]["body"] + grew[-3]["body"]
      and all("Telegram user ID 222" in n["body"] for n in grew), grew)
run(BID, msg("/link bo@example.com", 222, chat=-100500, kind="supergroup"))
check("...but not when they claim again in a group they already had",
      len(notices("telegram_claim_group")) == 3)
check("...and the new group's notice names its title in quotes beside its id",
      any('in the group "Line A" (chat ID -100500)' in n["body"]
          for n in notices("telegram_claim")), notices("telegram_claim"))

# -- 3. refusals all read the same --------------------------------------------
unknown = run(BID, msg("zed@example.com", 444))
check("an address not on the list is refused in the one neutral sentence",
      sent(unknown) == [(444, net.CLAIM_REFUSED)]
      and net.CLAIM_REFUSED == "That address is not set up for this bot.")
check("...and its message is deleted too", deleted(unknown) == [(444, _seq[0])])
malformed = run(BID, msg("ana@example", 445))
check("a near-miss address gets the same sentence",
      sent(malformed) == [(445, net.CLAIM_REFUSED)])
taken = run(BID, msg("ana@example.com", 555))
check("a second person claiming a held address gets the SAME sentence",
      sent(taken) == [(555, net.CLAIM_REFUSED)], taken)
check("...takes the same path otherwise (delete, then one reply)",
      [m for m, _p in taken] == [m for m, _p in unknown])
check("...the first claim stands", link_of(BID, "ana@example.com")["user_id"] == "111")
check("...and the admins get an Inbox notice naming both by id and username",
      len(notices("telegram_claim_refused")) == 1
      and "Telegram user ID 555, no username" in notices("telegram_claim_refused")[0]["body"]
      and "claimed by Telegram user ID 111, username @ana"
      in notices("telegram_claim_refused")[0]["body"]
      and notices("telegram_claim_refused")[0]["tab"] == "telegram",
      notices("telegram_claim_refused"))
run(BID, msg("ana@example.com", 555))
check("...once an hour per claimant, not once per attempt",
      len(notices("telegram_claim_refused")) == 1)
anon = run(BID, msg("/link bo@example.com", 1087968824, chat=-100500,
                    kind="supergroup", sender_chat={"id": -100500}))
check("an anonymous group admin cannot claim (one shared id for all of them)",
      sent(anon) == [(-100500, net.CLAIM_REFUSED)]
      and link_of(BID, "bo@example.com")["user_id"] == "222")

# -- 4. what is not a claim ---------------------------------------------------
for text in ("hello", "@Ops_bot", "/summary", "write to ana@example.com please"):
    check(f"{text!r} from a stranger is ignored silently",
          run(BID, msg(text, 666)) == [])

# -- 5. rate limit ------------------------------------------------------------
for i in range(net.CLAIM_LIMIT):
    run(BID, msg(f"guess{i}@example.com", 777))
over = run(BID, msg("guess9@example.com", 777))
check("past the limit an attempt gets no reply at all",
      sent(over) == [], over)
check("...though the address is still deleted", bool(deleted(over)))
clock[0] += net.CLAIM_WINDOW_S + 1
check("...and the silence lasts the cooldown, not just the window",
      sent(run(BID, msg("guess10@example.com", 777))) == [])
clock[0] += net.CLAIM_COOLDOWN_S
check("...after which an attempt is answered again",
      sent(run(BID, msg("guess11@example.com", 777))) == [(777, net.CLAIM_REFUSED)])
for user in range(801, 801 + net.CLAIM_LIMIT):
    run(BID, msg("/link nope@example.com", user, chat=-100900, kind="group"))
check("a GROUP is limited as a whole, so many accounts cannot share the guessing",
      sent(run(BID, msg("/link nope@example.com", 899, chat=-100900, kind="group"))) == [])

# -- 6. deleting the address --------------------------------------------------
refuse.add("deleteMessage")
out = run(BID, msg("/link nobody@example.com", 901, chat=-100777, kind="group"))
check("a group that will not let the bot delete is not an error",
      sent(out) == [(-100777, net.CLAIM_REFUSED)], out)
check("...and is remembered for the page's admin hint",
      bots.public(CFG)["bots"][0]["delete_failed"] is True)
out = run(BID, msg("nobody@example.com", 902))
check("a private chat failing to delete is tolerated too", bool(sent(out)))
refuse.clear()
run(BID, msg("/link nobody@example.com", 903, chat=-100777, kind="group"))
check("...and a later group delete that works clears the hint",
      bots.public(CFG)["bots"][0]["delete_failed"] is False)

# -- 7. the admin view and revocation -----------------------------------------
claims = bots.public(CFG)["bots"][0]["claims"]
check("the page sees each address and who holds it: id, username, name apart",
      claims == [{"email": "ana@example.com", "claimed": True, "user_id": "111",
                  "username": "ana", "name": "U111 B", "blocked": []},
                 {"email": "bo@example.com", "claimed": True, "user_id": "222",
                  "username": "", "name": "U222", "blocked": []}], claims)
check("...and never the token", TOKEN not in str(bots.public(CFG)))
links.unlink(BID, "bo@example.com", CFG)
check("Remove frees the address", link_of(BID, "bo@example.com") is None
      and bots.public(CFG)["bots"][0]["claims"][1]["claimed"] is False)
check("...and its holder is no longer answered from the next message (a /start "
      "is asked for an address, like any stranger's; nothing else is answered)",
      asked(run(BID, msg("/start", 222, chat=-100500, kind="supergroup")), -100500)
      and asked(run(BID, msg("/start", 222)), 222)
      and run(BID, msg("/summary", 222)) == [])

# S2-M1: Remove blocks the removed user from that address.
check("S2-M1 the removed user is listed as blocked from it, by id",
      bots.public(CFG)["bots"][0]["claims"][1]["blocked"]
      == [{"user_id": "222", "username": "", "name": "U222"}],
      bots.public(CFG)["bots"][0]["claims"][1])
again = run(BID, msg("bo@example.com", 222))
check("...and cannot claim it again: the same neutral refusal, nothing linked",
      sent(again) == [(222, net.CLAIM_REFUSED)] and link_of(BID, "bo@example.com") is None,
      again)
check("...the admins are told a removed user tried again",
      any("after their claim on it was removed" in n["body"]
          and "Telegram user ID 222" in n["body"]
          for n in notices("telegram_claim_refused")))
check("...while anyone else may still claim it",
      sent(run(BID, msg("bo@example.com", 223))) == [(223, net.CLAIM_LINKED)])
check("Allow again clears the block", links.unblock(BID, "bo@example.com", "222", CFG)
      and bots.public(CFG)["bots"][0]["claims"][1]["blocked"] == [])
links.unlink(BID, "bo@example.com", CFG)                     # frees it; blocks 223
check("...and only that user's block",
      [b["user_id"] for b in bots.public(CFG)["bots"][0]["claims"][1]["blocked"]] == ["223"])
links.unlink(BID, "ana@example.com", CFG)                    # blocks 111 from ana

bots.save_bot({"id": BID, "emails": ["bo@example.com"]}, CFG)
check("taking an address off the list revokes its holder at once",
      asked(run(BID, msg("/start", 111)), 111))
check("...and drops the link from the file",
      link_of(BID, "ana@example.com") is None)
bots.save_bot({"id": BID, "emails": ["ana@example.com", "bo@example.com"]}, CFG)
check("...so putting it back starts it Unclaimed, not with the old holder",
      asked(run(BID, msg("/start", 111)), 111)
      and bots.public(CFG)["bots"][0]["claims"][0]["claimed"] is False)
check("...and editing an address out of the list clears its blocks (and only its)",
      bots.public(CFG)["bots"][0]["claims"][0]["blocked"] == []
      and [b["user_id"] for b in bots.public(CFG)["bots"][0]["claims"][1]["blocked"]]
      == ["223"])
check("a claim for an address the admin has just removed is refused",
      links.claim(BID, "gone@example.com", {"id": 5}, "5", False, CFG)[0]
      == "unknown")
run(BID, msg("ana@example.com", 111))
check("one person holds one address: claiming another moves them",
      links.claim(BID, "bo@example.com", {"id": 111}, "111", False, CFG)[0]
      == "linked" and link_of(BID, "ana@example.com") is None)

# -- 8. the old chat ID list, and nobody ----------------------------------------
OLD = bots.save_bot({"name": "Legacy", "token": TOKEN, "allow": ["999", "-100321"]},
                    CFG)
check("a chat on the old list is still answered",
      bool(sent(run(OLD, msg("/start", 999)))))
check("...a whole group on it, whoever is speaking",
      bool(sent(run(OLD, msg("/start", 4242, chat=-100321, kind="group")))))
check("...and on a bot that lists no addresses, an address is just text",
      run(OLD, msg("ana@example.com", 4243)) == [])
NONE = bots.save_bot({"name": "Closed", "token": TOKEN}, CFG)
check("both lists empty: nobody is answered",
      run(NONE, msg("/start", 999)) == []
      and run(NONE, msg("ana@example.com", 999)) == [])

# -- 9. deleting the bot --------------------------------------------------------
bots.delete_bot(BID, CFG)
check("deleting a bot deletes its links and its blocks", links.for_bot(BID, CFG) == []
      and not [b for b in links._load(CFG)["blocked"] if b.get("bot") == BID])

# -- 12. the per-bot ceiling on failed claims (S2-L1) ---------------------------
PB = bots.save_bot({"name": "Paused", "token": TOKEN, "emails": ["pa@example.com"]}, CFG)
links.BOT_FAIL_LIMIT = 3
for user in (1201, 1202, 1203):                 # three accounts, one guess each
    run(PB, msg(f"g{user}@example.com", user))
check("S2-L1 failed claims from MANY accounts pause claims on the bot",
      links.claims_paused(PB, CFG, now=clock[0]) > clock[0])
check("...and the admins are told once",
      len(notices("telegram_claims_paused")) == 1
      and '"Paused"' in notices("telegram_claims_paused")[0]["body"])
out = run(PB, msg("g1204@example.com", 1204))
check("...while paused, a claim gets no reply (the address still deleted)",
      sent(out) == [] and bool(deleted(out)), out)
net._attempts.clear()                           # a master restart
out = run(PB, msg("pa@example.com", 1205))
check("...the pause is on disk, so a master restart does not lift it, even "
      "for a listed address", sent(out) == [] and link_of(PB, "pa@example.com") is None
      and len(notices("telegram_claims_paused")) == 1, out)
clock[0] += links.BOT_PAUSE_S + 1
out = run(PB, msg("pa@example.com", 1205))
check("...and it ends by itself", sent(out) == [(1205, net.CLAIM_LINKED)], out)
links.BOT_FAIL_LIMIT = 10 ** 6

# -- 13. a display name cannot pass for a handle (S2-M1) -------------------------
SP = bots.save_bot({"name": "Spoof", "token": TOKEN, "emails": ["ana@example.com"]}, CFG)
run(SP, msg("ana@example.com", 4321, sender={"first_name": "Ana B (@ana)"}))
row = bots.public(CFG)["bots"][-1]["claims"][0]
check("a first name typed as \"Ana B (@ana)\" is stored as a name, not a username",
      row["user_id"] == "4321" and row["username"] == "" and row["name"] == "Ana B (@ana)",
      row)
spoof = [n for n in notices("telegram_claim") if "4321" in n["body"]]
real = links.identity({"user_id": "111", "username": "ana", "name": "Ana B"})
check("...and the notice says \"no username\" beside the real id, so it cannot read "
      "like the real @ana",
      spoof and 'Telegram user ID 4321, no username, name shown as "Ana B (@ana)"'
      in spoof[0]["body"] and "username @ana" not in spoof[0]["body"]
      and "username @ana" in real, spoof)
check("a username Telegram could not have issued is not stored as one",
      links.sender_fields({"id": 5, "username": "ana) (@root"})["username"] == ""
      and links.sender_fields({"id": 5, "username": "@ana"})["username"] == "")

# -- 14. /start from somebody the bot does not know yet (Tok Aba, 2026-10-14) ----
net._prompts.clear()
net._awaiting.clear()
net._attempts.clear()
ST = bots.save_bot({"name": "Starter", "token": TOKEN,
                    "emails": ["st@example.com", "sg@example.com", "sg3@example.com",
                               "held@example.com"]}, CFG)
CMD = {"id": "c1", "command": "summary", "title": "Summary", "intro": "",
       "buttons": [{"label": "Today", "period": "today", "sql": "select 1",
                    "tables": []}]}
dc.get_commands = lambda systems_dir, slug: [CMD]


def waiting_on(chat, user):
    return (ST, str(chat), str(user)) in net._awaiting


out = run(ST, msg("/start", 5001))
check("/start from an unregistered private chat asks for the address, by the "
      "bot's own @name",
      sent(out) == [(5001, "Welcome to @Ops_bot.\n" + net.START_ASK_PRIVATE)]
      and not deleted(out) and waiting_on(5001, 5001), out)
out = run(ST, msg("St@Example.com", 5001, sender={"username": "sto"}))
check("...the next message is the claim, through the same path: deleted, linked, "
      "the wait over",
      sent(out) == [(5001, net.CLAIM_LINKED)] and deleted(out)
      and link_of(ST, "st@example.com")["user_id"] == "5001"
      and not waiting_on(5001, 5001), out)
check("...and the admins hear of it as before",
      any("Telegram user ID 5001, username @sto" in n["body"] and '"Starter"' in n["body"]
          for n in notices("telegram_claim")))

out = run(ST, msg("/start", 5001))
check("registered /start: the greeting and the command list, no prompt, no wait",
      len(sent(out)) == 1 and "I report on" in sent(out)[0][1]
      and "/summary - Summary" in sent(out)[0][1]
      and net.START_ASK_PRIVATE not in sent(out)[0][1] and not waiting_on(5001, 5001), out)
out = run(ST, msg("/summary", 5001))
check("registered private chat: a command works with no /start first",
      len(sent(out)) == 1 and "Which period?" in sent(out)[0][1], out)
run(ST, msg("/link sg@example.com", 5002, chat=-100600, kind="supergroup"))
out = run(ST, msg("/summary@Ops_bot", 5002, chat=-100600, kind="supergroup"))
check("registered group member: a command works with no /start first",
      len(sent(out)) == 1 and "Which period?" in sent(out)[0][1], out)
out = run(ST, msg("/start", 5002, chat=-100600, kind="supergroup"))
check("...and their /start in that group is the greeting only",
      len(sent(out)) == 1 and "I report on" in sent(out)[0][1]
      and not waiting_on(-100600, 5002), out)

for text in ("hello", "/summary", "what is this"):
    check(f"a stranger who never sent /start: {text!r} stays silent",
          run(ST, msg(text, 5020)) == [])

run(ST, msg("/start", 5003))
r1 = run(ST, msg("hello", 5003))
r2 = run(ST, msg("is this it", 5003))
r3 = run(ST, msg("hey?", 5003))
check("a non-address answer gets a gentle re-prompt, at most twice",
      sent(r1) == [(5003, net.START_RETRY_PRIVATE)]
      and sent(r2) == [(5003, net.START_RETRY_PRIVATE)] and r3 == [], (r1, r2, r3))
check("...then the wait ends and the person is silent to the bot",
      not waiting_on(5003, 5003) and run(ST, msg("hello again", 5003)) == [])
check("...a command while waiting is not an answer and is not answered",
      sent(run(ST, msg("/start", 5003))) and run(ST, msg("/summary", 5003)) == []
      and waiting_on(5003, 5003))
check("...and /start again asks again", asked(run(ST, msg("/start", 5003)), 5003))

out = run(ST, msg("/start", 5004, chat=-100700, kind="group", chat_title="Floor"))
ask = next(p for m, p in out if m == "sendMessage")
check("group /start asks in the group, quoting the person and opening THEIR "
      "reply box",
      ask["text"] == "Welcome to @Ops_bot.\n" + net.START_ASK_GROUP
      and ask["reply_to_message_id"] == _seq[0]
      and ask["reply_markup"] == {"force_reply": True, "selective": True}, ask)
other = run(ST, msg("sg3@example.com", 5005, chat=-100700, kind="group"))
check("...another member's plain address is not read as the claim (the wait is "
      "keyed by user + chat)", quiet(other) and link_of(ST, "sg3@example.com") is None
      and waiting_on(-100700, 5004), other)
check("...nor is the same person's plain address in ANOTHER group",
      quiet(run(ST, msg("sg3@example.com", 5004, chat=-100701, kind="group"))))
chatter = run(ST, msg("brb lunch", 5004, chat=-100700, kind="group"))
check("...their ordinary group chatter is not re-prompted (only a reply to the bot is)",
      quiet(chatter), chatter)
out = run(ST, msg("sg3@example.com", 5004, chat=-100700, kind="group"))
check("...their own address is the claim: deleted, linked to the group",
      sent(out) == [(-100700, net.CLAIM_LINKED)] and deleted(out)
      and link_of(ST, "sg3@example.com")["groups"] == ["-100700"], out)
check("...and no reply in the group ever repeats an address",
      not any("@example.com" in t for _c, t in sent(out) + sent(other) + sent(chatter)))
check("group rules unchanged: /start@AnotherBot is not this bot's",
      quiet(run(ST, msg("/start@Other_bot", 5006, chat=-100702, kind="group"))))
check("...and a bare address from someone who did not /start is still left alone",
      quiet(run(ST, msg("sg@example.com", 5006, chat=-100702, kind="group"))))
r = run(ST, msg("nope", 5006, chat=-100703, kind="group",
                reply_to_message={"message_id": 1, "from": {"id": BOT_USER, "is_bot": True}}))
check("...a reply to the bot from someone the bot is not waiting on is not answered",
      quiet(r), r)
anon = run(ST, msg("/start", 1087968824, chat=-100704, kind="supergroup",
                   sender_chat={"id": -100704}))
check("an anonymous group admin's /start is not answered (no one person to link)",
      quiet(anon), anon)

run(ST, msg("/start", 5007))
clock[0] += net.START_WAIT_S + 1
check("the wait expires: after it, a non-address is not re-prompted",
      run(ST, msg("hello", 5007)) == [] and not waiting_on(5007, 5007))
run(ST, msg("/start", 5008, chat=-100705, kind="group"))
clock[0] += net.START_WAIT_S + 1
check("...and in a group a plain address after the wait is not a claim",
      quiet(run(ST, msg("sg3@example.com", 5008, chat=-100705, kind="group"))))

for _ in range(net.START_PROMPT_LIMIT):
    run(ST, msg("/start", 5009))
check("rate limit: past START_PROMPT_LIMIT prompts an hour, /start is ignored",
      run(ST, msg("/start", 5009)) == [])
for user in range(5100, 5100 + net.START_PROMPT_LIMIT):
    run(ST, msg("/start", user, chat=-100706, kind="group"))
check("...per GROUP too, whoever sends it",
      quiet(run(ST, msg("/start", 5199, chat=-100706, kind="group"))))
clock[0] += net.START_PROMPT_WINDOW_S
check("...and the window passes", asked(run(ST, msg("/start", 5009)), 5009))
check("the deep-link form /start <payload> asks too, and never repeats the payload",
      asked(run(ST, msg("/start sECRET_x1", 5010)), 5010)
      and "sECRET" not in str(calls))

# Blocked (Removed) users: chosen to be treated exactly as any stranger -- a
# /start is asked for an address, the claim gets today's neutral refusal --
# because answering them differently would itself say they were blocked.
links.unlink(ST, "st@example.com", CFG)
check("a removed user's /start reads like a stranger's (no hint of the block)",
      asked(run(ST, msg("/start", 5001)), 5001))
blocked = run(ST, msg("st@example.com", 5001))
unknown = run(ST, msg("zz@example.com", 5011))
check("...their claim gets today's neutral refusal, the same as an unknown address",
      sent(blocked) == [(5001, net.CLAIM_REFUSED)] and sent(unknown) == [(5011, net.CLAIM_REFUSED)]
      and [m for m, _p in blocked] == [m for m, _p in unknown])
run(ST, msg("/start", 5012))
taken = run(ST, msg("sg@example.com", 5012))
check("a prompted claim for a HELD address and for an unknown one read the same",
      sent(taken) == [(5012, net.CLAIM_REFUSED)])

links.BOT_FAIL_LIMIT = 1
run(ST, msg("zz2@example.com", 5013))
check("while claims are paused, /start is not answered either",
      links.claims_paused(ST, CFG, now=clock[0]) and run(ST, msg("/start", 5014)) == [])
links.BOT_FAIL_LIMIT = 10 ** 6
clock[0] += links.BOT_PAUSE_S + 1

LEG = bots.save_bot({"name": "Legacy2", "token": TOKEN, "allow": ["999"]}, CFG)
check("a bot that lists no addresses keeps its silence on a stranger's /start",
      run(LEG, msg("/start", 5015)) == [])
check("the awaiting table is bounded", net.MAX_WAITING >= len(net._awaiting))
dc.get_commands = lambda systems_dir, slug: []

# -- 10. the log ------------------------------------------------------------------
text = log.getvalue()
check("no token in the log", TOKEN not in text and TOKEN.split(":")[1] not in text)
check("no whole address in the log",
      not any(a in text for a in ("zed@example.com", "ana@example.com",
                                  "bo@example.com", "guess1@example.com",
                                  "nobody@example.com", "st@example.com",
                                  "sg3@example.com", "zz@example.com")), text[-600:])
check("...nor a /start payload", "sECRET" not in text)
check("...only a masked one", "z***@example.com" in text)

# -- 11. the wiring ---------------------------------------------------------------
routes = open(os.path.join(ROOT, "core", "routes", "telegram.py"),
              encoding="utf-8").read()
check("Remove is an admin-only route",
      '"/api/telegram/bots/unlink"' in routes
      and routes.split('name="telegram.unlink"')[1].split("@router")[0]
      .count("_require_admin(request)") == 1)
check("...and so is Allow again",
      '"/api/telegram/bots/unblock"' in routes
      and routes.split('name="telegram.unblock"')[1].split("@router")[0]
      .count("_require_admin(request)") == 1)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
