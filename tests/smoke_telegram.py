# A bot token belongs to the BOX, not to twelve nodes on a canvas.
#
# The token and chat id used to be typed into each Telegram node, so a
# deployment whose twelve rules message one operations group held the same
# credential in twelve places -- on the canvas, in the saved graph, and in
# every template exported from it. Rotating it meant finding every node, and
# missing one produced a rule whose alerts silently stopped arriving.
#
# Same shape the mailboxes already use: declared once under Settings, named by
# id from a node, resolved to the real credential on the way to disk.
import os
import sys
import tempfile
import urllib.error as urllib_error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import telegram_bots as tg                 # noqa: E402
from core.services import workflow as wf                      # noqa: E402
from core.services import detector_control as dc              # noqa: E402

CFG = tempfile.mkdtemp()
TOKEN = "123456789:AAF-abcdefghijklmnopqrstuvwxyz012345"
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _refuses(payload):
    try:
        tg.save_bot(payload, CFG)
        return False
    except (ValueError, TypeError):
        return True


# -- the store -------------------------------------------------------------
bid = tg.save_bot({"name": "Line alerts", "token": TOKEN,
                   "chat_id": "-1001234567890"}, CFG)
check("a bot saves and gets an id", bid.startswith("bot_"), bid)
check("...and is the default while it is the only one", tg.default_id(CFG) == bid)

pub = tg.public(CFG)
check("the browser never sees the token",
      TOKEN not in str(pub), str(pub))
check("...but is told one is set", pub["bots"][0]["has_token"] is True)
check("...and gets the chat id, which is not a secret",
      pub["bots"][0]["chat_id"] == "-1001234567890")

# A save from the rendered form carries the MASK, not the token. Storing the
# asterisks would fail at 3am with a 404 nobody can explain.
tg.save_bot({"id": bid, "name": "Renamed", "token": pub["bots"][0]["token"],
             "chat_id": "-1001234567890"}, CFG)
check("saving a masked token keeps the stored one",
      tg.get(bid, CFG)["token"] == TOKEN)
check("...and the rename went through", tg.get(bid, CFG)["name"] == "Renamed")

# A typo in a token fails HERE, not at whatever hour the rule fires.
try:
    tg.save_bot({"token": "nonsense", "chat_id": "1"}, CFG)
    check("a malformed token is refused", False)
except ValueError as e:
    check("a malformed token is refused", "BotFather" in str(e), str(e))
# A bot needs no chat. It is WHO the box posts as; who hears a message is
# named on the node that sends, because one bot legitimately talks to the
# maintenance group, the shift channel and one supervisor. Requiring one here
# made adding a bot wait on a number somebody had to go and fetch, and forced
# them to nominate a "main" chat nothing necessarily used.
chatless = tg.save_bot({"name": "Reports", "token": TOKEN}, CFG)
check("a bot saves with no chat id at all",
      tg.get(chatless, CFG)["chat_id"] == "")
check("...and is still named", tg.get(chatless, CFG)["name"] == "Reports")
check("...while a MALFORMED one is still refused at the door",
      _refuses({"name": "x", "token": TOKEN, "chat_id": "not a chat"}))
tg.delete_bot(chatless, CFG)

# A node pointing at a DELETED bot is reported, never silently redirected into
# somebody else's group chat.
gone, problem = tg.resolve("bot_nope", CFG)
check("a missing bot resolves to a complaint, not the default",
      gone is None and "no longer exists" in problem, problem)
found, problem = tg.resolve("", CFG)
check("blank resolves to the default", found and found["id"] == bid and not problem)

# -- the seam that puts the credential on disk -----------------------------
outs = wf._with_bot_settings(
    [{"id": "wf_a", "type": "telegram", "account": bid, "message": "hi"}],
    "unassigned_system", CFG)
check("compile fills the token from the named bot",
      outs[0]["bot_token"] == TOKEN and outs[0]["chat_id"] == "-1001234567890",
      str(outs[0]))

# A chat typed into the NODE addresses a person, a group or a channel that is
# not the bot's own. The bot is who the box posts AS; the chat is who hears it,
# and one bot posting into the maintenance group, the shift channel and one
# supervisor's own chat is the ordinary shape. Before this the seam below
# rewrote every node to the bot's single saved chat, silently.
addressed = wf._with_bot_settings(
    [{"id": "wf_p", "type": "telegram", "account": bid, "chat_id": "987654321",
      "message": "hi"}], "unassigned_system", CFG)
check("a chat named on the node wins over the bot's own",
      addressed[0]["chat_id"] == "987654321"
      and addressed[0]["bot_token"] == TOKEN, str(addressed[0]))
check("...while the bot still supplies the token",
      "token" not in str(addressed[0].get("account", "")))

for chat in ("987654321", "-1001234567890", "@shift_channel"):
    check(f"{chat} is a chat this box will send to",
          bool(wf._CHAT_ID_RE.match(chat)))
for chat in ("me", "+601234", "@ab", "0x99", "-100 123", "123,456"):
    check(f"{chat!r} is refused as a chat", not wf._CHAT_ID_RE.match(chat),
          "Telegram answers a bad id with 'chat not found' into a log "
          "nobody is reading -- the canvas has to say it instead")

# ONE definition of what an address is. The canvas, the footage export and the
# Settings test all check it; one of them being stricter than another is a node
# that compiles and then cannot send.
check("the canvas and the box agree what a chat id is",
      wf._CHAT_ID_RE is tg.CHAT_ID_RE)

# The mistake people actually arrive with is a person's @handle. It cannot be
# refused by SHAPE -- a public channel's @name is the same string -- so the
# wall is the send, and what has to be right is the explanation that comes
# back. A malformed one is caught here, and says the same two things.
check("a person's @handle passes the shape check, because a channel's is one",
      tg.bad_chat("@someone") == "",
      "the Bot API has no username lookup for users, but nothing in the "
      "string says which kind it is -- only the send can")
handle = tg.bad_chat("@me")
check("a malformed @name is refused with the reason",
      "public channel" in handle and "numeric" in handle, handle)
check("...and a blank one says so plainly", tg.bad_chat("") == "no chat id")
check("a real id passes", tg.bad_chat("-1001234567890") == "")

# urllib renders a refusal as "HTTP Error 400: Bad Request" and throws the body
# away -- and the body is the whole message.
class _Fake400(urllib_error.HTTPError):
    def __init__(self, payload):
        super().__init__("u", 400, "Bad Request", {}, None)
        self._payload = payload.encode()

    def read(self, *a):
        return self._payload


said = tg.explain(_Fake400('{"description": "Bad Request: chat not found"}'))
check("a 400 is explained from its body, not its status line",
      "chat not found" in said and "Bad Request: Bad Request" not in said, said)
check("...naming all three causes, because Telegram names none",
      "never messaged" in said and "added to" in said and "@name" in said, said)
check("a blocked bot says who has to act first",
      "press Start" in tg.explain(
          _Fake400('{"description": "Forbidden: bot was blocked by the user"}')))
check("a bad token points at Settings",
      "revoked" in tg.explain(_Fake400('{"description": "Unauthorized"}')))
check("a body-less refusal still says something",
      tg.explain(_Fake400("not json")) == "HTTP 400")

# A graph written before the bot list existed still carries its own token.
# Repointing it at the box's default would send somebody's alerts into a
# different chat without saying so.
legacy = wf._with_bot_settings(
    [{"id": "wf_b", "type": "telegram", "bot_token": "9:" + "z" * 30,
      "chat_id": "42"}], "unassigned_system", CFG)
check("a node with its own token is left alone",
      legacy[0]["bot_token"].startswith("9:") and legacy[0]["chat_id"] == "42")

# ...and nothing else is touched.
others = wf._with_bot_settings([{"id": "wf_c", "type": "email"}],
                               "unassigned_system", CFG)
check("other outputs pass through", others[0] == {"id": "wf_c", "type": "email"})

# -- the node carries the NAME of a credential, never the credential -------
spec = wf.REGISTRY["output.telegram"]
check("the node data has an account field", "account" in spec["data"])
out = dc._validate_output({"id": "wf_d", "type": "telegram", "account": bid,
                           "message": "x", "function": "f", "trigger": "alert"})
check("the stored output keeps the account", out.get("account") == bid, str(out))

# -- the routes ------------------------------------------------------------
routes = read("core", "routes", "telegram.py")
guards = routes.count("    _require_admin(request)")
# The two recipient routes (Tok Aba, 2026-10-14) have gates of their own: the
# people list is workflow.edit (the node it serves, emails to site admins
# only), and the app's resolve route is loopback-only (smoke_tgrecipients).
people_gates = (routes.count('    perm.require(request, "workflow.edit")')
                + routes.count("    if not net.local_caller(request):"))
check("every telegram route is gated (admin, or its own recipient gate)",
      guards + people_gates == routes.count("@router.") and people_gates == 2,
      f"{guards}+{people_gates} guards / {routes.count('@router.')} routes")
check("the token never comes back out of a route",
      'tg_service.load(' not in routes)
for server in ("hub_server.py", "master_server.py"):
    check(f"{server} mounts the bot routes",
          "telegram_router" in read(server))

panel = read("frontend", "src", "components", "TelegramPanel.jsx")
check("the settings page can test a bot", "/api/telegram/bots/test" in panel)
check("...and never renders a stored token back into the form",
      'token: ""' in panel)
hub = read("frontend", "src", "components", "SettingsHub.jsx")
check("Settings offers a Telegram tab", 'id: "telegram"' in hub)

# -- one bot, one system, both ways ----------------------------------------
# The pairing is not tidiness. An inbound /summary says nothing about which
# warehouse it means; the bot that received it does, and only if it answers
# for exactly one. (Telegram also allows one getUpdates consumer per token, so
# a token belongs to one process regardless.)
tg.save_bot({"id": bid, "name": "Line alerts", "system": "line_a"}, CFG)
check("a bot can be assigned to a system", tg.get(bid, CFG)["system"] == "line_a")
check("...and a save that does not mention a field keeps it",
      tg.save_bot({"id": bid, "name": "Line alerts"}, CFG)
      and tg.get(bid, CFG)["system"] == "line_a",
      "setting one field must not quietly unassign the bot")
check("...and the system finds it",
      (tg.for_system("line_a", CFG) or {}).get("id") == bid)
check("...while another system finds nothing",
      tg.for_system("line_b", CFG) is None)

second = tg.save_bot({"name": "Second", "token": TOKEN, "system": "line_a"}, CFG)
check("assigning a second bot to that system takes the first off it",
      tg.get(bid, CFG)["system"] == "" and tg.get(second, CFG)["system"] == "line_a",
      "last write wins -- refusing would mean a half-done move, and a system "
      "that silently reports nowhere")
check("...so a system still has exactly one",
      (tg.for_system("line_a", CFG) or {}).get("id") == second)
check("an unassigned bot is a real state, not an error",
      tg.get(bid, CFG)["system"] == "")

# The compiled document takes the SYSTEM's bot, whatever a node was saved
# naming -- which bot is no longer the node's decision.
tg.save_bot({"id": second, "chat_id": "-100999"}, CFG)
bysys = wf._with_bot_settings(
    [{"id": "wf_s", "type": "telegram", "account": bid, "message": "hi"}],
    "line_a", CFG)
check("compile fills from the bot assigned to the system",
      bysys[0]["bot_token"] == TOKEN and bysys[0]["chat_id"] == "-100999",
      str(bysys[0]))
tg.delete_bot(second, CFG)
tg.save_bot({"id": bid, "system": "", "chat_id": "-1001234567890"}, CFG)

node_list = read("core", "routes", "workflow.py")
check("a node is offered its OWN system's bot, not every bot on the box",
      "def _telegram_bots(slug, config_dir=None):" in node_list
      and "tg_service.for_system(slug, config_dir)" in node_list,
      "a picker of every bot let a node on one system be wired to another "
      "system's bot, with nothing to stop it and nothing to report it")

# -- who answers "where does it go" ----------------------------------------
# The bot answers "who does the box post AS". The node answers "who hears it".
# The Settings page's chat is neither -- it is only where Test posts.
routes_tg = read("core", "routes", "telegram.py")
test_route = routes_tg.split('name="telegram.test_bot"')[1].split(chr(10) + "def _send_test")[0]
check("the Test button takes its own chat id",
      'body.get("chat_id")' in test_route)
check("...falling back to one the bot happens to keep",
      'bot.get("chat_id")' in test_route)
check("...and says what to do when there is neither",
      "type a chat id to test with" in test_route)

panel = read("frontend", "src", "components", "TelegramPanel.jsx")
check("the settings form calls it a TEST chat, and optional",
      "Test Chat ID (Optional)" in panel)
check("...and Test asks when the bot keeps none",
      "await ask({" in panel and "chat_id: chat" in panel)
# A second id could not be typed at all: the box parsed on every keystroke, so
# Enter left an empty last line, .filter(Boolean) dropped it, and the value
# re-rendered with the newline gone. The cursor could never leave line one.
check("the allowlist keeps the raw text while it is being typed",
      "allow_text: e.target.value" in panel
      and "form.allow_text ??" in panel,
      "a field that fights the Enter key is a field nobody can put two "
      "things in")
check("...and splits it into ids once, on save",
      "const { allow_text, emails_text, claims, delete_failed, claims_paused_until,\n"
      "              ...rest } = form;"
      in panel and "body.allow = lines(allow_text)" in panel
      and 'api.post("/api/telegram/bots", body)' in panel)

# Not an error: the compiler cannot see the bot list (that is filled in
# afterwards, where the config is), so refusing would take a graph that has
# been sending for months off the air the next time anybody pressed Save.
compiler = read("core", "services", "workflow.py")
tg_block = compiler.split('if spec["output_type"] == "telegram" and tg_mode not in ("all", "selected"):')[1][:900]
check("a Telegram node with no chat is WARNED, not refused",
      "names no chat" in tg_block and "warn(" in tg_block
      and "err(n[\"id\"], _tg_bots.bad_chat(chat))" in tg_block)

page = read("frontend", "src", "pages", "Workflow.jsx")
check("the Telegram node shows its system's bot instead of a picker",
      'label="Posts as"' in page and "tgBots.map" not in page,
      "there is nothing to pick: the system has one bot or none")
check("...and says so plainly when none is assigned",
      "No bot is assigned to this system" in page)
check("...and no longer asks for a token", 'set("bot_token")' not in page)
check("...but does ask who to send to",
      'label="Send to"' in page and 'set("chat_id")' in page)
check("...showing the bot's own chat as what blank means",
      "chatOfBot(tgBots" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
