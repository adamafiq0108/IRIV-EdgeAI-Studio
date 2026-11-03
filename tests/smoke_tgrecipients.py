"""A Telegram node sends to the people linked to its bot (Tok Aba, 2026-10-14).

"for this telegram node please sync to all the chat ids / users from the
connected bots, then I can choose either selected people or all people."

The node's Send to is one of three:
  * "All linked people"   (to_mode "all")       every person linked to the bot,
                                                  in their PRIVATE chat
  * "Selected people"      (to_mode "selected")  the Telegram user ids (and
                                                  explicitly chosen group chat
                                                  ids) the node stores
  * "A chat ID (advanced)" (no to_mode)          today's typed chat id, unchanged

Pinned:
  1. every OLD node compiles byte-identical (golden_tgrecipients.json,
     captured before the change); new modes compile to_mode / to_people /
     to_url only, never a list of ids for "all"
  2. the validator: modes whitelisted, ids digits only (a group's with a -),
     at most 50, the address on this box only
  3. resolution (telegram_links.recipients): live links only, a removed /
     blocked person NEVER, a person claimed later included for All, Selected
     by user id, a group only when its chat id is chosen, deduped, capped
  4. the people list (telegram_links.people) and the route gates: site admin
     sees emails, a workflow editor names only, nobody else anything;
     no-store; the app's resolve route is loopback-only
  5. the app: resolved at SEND time through the master, cached 60 s, the
     last good list kept 10 min when the master is down, then skipped --
     never "empty means everyone"; one message per recipient, a failure
     never stops the rest, cap 50, dedupe, 429 retry-after honoured, files
     sent per recipient, counts only in the log
  6. Send a test now goes to the first 3 chosen recipients and says so

    py -3 tests/smoke_tgrecipients.py            (check)
    py -3 tests/smoke_tgrecipients.py --capture  (rewrite the golden; only before a change)
"""
import io
import json
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
os.environ["CYTRON_MASTER_PORT"] = "5110"

from core.services import workflow as wf                     # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_tgrecipients.json")
TOKEN = "123456789:AAF-abcdefghijklmnopqrstuvwxyz012345"


def graph(data):
    nodes = [
        {"id": "s", "type": "variable.schedule", "position": {"x": 2, "y": 2},
         "data": {"fnId": "clock", "name": "Nightly", "every": "daily", "at": "22:00"}},
        {"id": "m", "type": "output.telegram", "position": {"x": 6, "y": 0},
         "data": dict({"message": "hello {label}"}, **data)},
    ]
    return wf.clean_graph({"nodes": nodes, "edges": [{"id": "e", "source": "s",
                                                      "target": "m"}]})


def doc_of(data):
    c = wf.compile_graph(graph(data), [])
    return next((o for o in c["outputs"] if o["id"] == "wf_m"), None), c


OLD = {"typed chat": {"chat_id": "781784546"},
       "group chat": {"chat_id": "-1001234567890"},
       "no chat": {},
       "own token": {"bot_token": TOKEN, "chat_id": "42"},
       "named bot": {"account": "bot_x", "chat_id": "42"}}

if "--capture" in sys.argv:
    json.dump({k: doc_of(v)[0] for k, v in OLD.items()},
              open(GOLDEN, "w", encoding="utf-8", newline="\n"), indent=1, sort_keys=True)
    print("captured", GOLDEN)
    sys.exit(0)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- 1. compile ------------------------------------------------------------------
golden = json.load(open(GOLDEN, encoding="utf-8"))
for k, v in OLD.items():
    check(f"an old node ({k}) compiles byte-identical",
          json.dumps(doc_of(v)[0], sort_keys=True) == json.dumps(golden[k], sort_keys=True))
check("...and the same when it says to_mode \"chat\" explicitly",
      json.dumps(doc_of({"chat_id": "781784546", "to_mode": "chat"})[0], sort_keys=True)
      == json.dumps(golden["typed chat"], sort_keys=True))
doc, c = doc_of({"to_mode": "all", "to_people": ["111", "222"]})
check("All: the mode and the address it resolves from, never a list of ids",
      doc.get("to_mode") == "all" and "to_people" not in doc
      and doc.get("to_url") == "http://127.0.0.1:5110/api/telegram/recipients/resolve?out=wf_m",
      doc)
check("...and no 'names no chat' warning: it has its people",
      not any("names no chat" in w["message"] for w in c["warnings"]), c["warnings"])
doc, _c = doc_of({"to_mode": "selected", "to_people": ["111", "-1001234567890", "x9", "111"]})
check("Selected: numeric ids only, deduped (a group id keeps its -)",
      doc.get("to_mode") == "selected" and doc.get("to_people") == ["111", "-1001234567890"],
      doc)
doc, c = doc_of({"to_mode": "selected", "to_people": []})
check("Selected with nobody chosen warns", any("Nobody is selected" in w["message"]
                                               for w in c["warnings"]), c["warnings"])

# -- 2. validator ------------------------------------------------------------------
from core.services import detector_control as dc               # noqa: E402


def refused(**over):
    o = dict({"id": "wf_x", "type": "telegram", "message": "x", "to_mode": "selected",
              "to_people": ["111"],
              "to_url": "http://127.0.0.1:5110/api/telegram/recipients/resolve?out=wf_x"},
             **over)
    try:
        return dc._validate_output(o)
    except (ValueError, TypeError) as exc:
        return str(exc)


check("a stored selected node is accepted", isinstance(refused(), dict), refused())
check("...an unknown mode is refused", "send-to" in str(refused(to_mode="everyone")))
check("...a non-numeric id is refused", "numeric" in str(refused(to_people=["@ana"])))
check("...more than 50 is refused", "50" in str(refused(to_people=[str(i) for i in range(1, 52)])))
check("...an address off this box is refused",
      "this box" in str(refused(to_url="http://10.0.0.9/api/telegram/recipients/resolve")))
check("...a legacy node keeps no new keys",
      not {"to_mode", "to_people", "to_url"} & set(dc._validate_output(
          {"id": "wf_y", "type": "telegram", "chat_id": "42", "message": "x"})))

# -- 3. resolution ---------------------------------------------------------------------
from core.services import telegram_bots as bots                  # noqa: E402
from core.services import telegram_links as links                # noqa: E402

CFG = tempfile.mkdtemp()
BID = bots.save_bot({"name": "Ops", "token": TOKEN,
                     "emails": ["ana@x.com", "bo@x.com", "cy@x.com", "di@x.com"]}, CFG)
links.claim(BID, "ana@x.com", {"id": 111, "first_name": "Ana", "username": "ana"}, "111",
            False, CFG)
links.claim(BID, "bo@x.com", {"id": 222, "first_name": "Bo"}, "222", False, CFG)
links.claim(BID, "cy@x.com", {"id": 333, "first_name": "Cy"}, "-100777", True, CFG)
bot = bots.get(BID, CFG)
check("All: every linked person's PRIVATE chat; a person linked only in a group is "
      "not messaged there", links.recipients(bot, "all", [], CFG) == ["111", "222"],
      links.recipients(bot, "all", [], CFG))
check("Selected: by user id", links.recipients(bot, "selected", ["222"], CFG) == ["222"])
check("...a group only when its chat id is chosen, and only while a live link holds it",
      links.recipients(bot, "selected", ["-100777"], CFG) == ["-100777"]
      and links.recipients(bot, "selected", ["-100999"], CFG) == [])
check("...a person with no private chat is skipped, never their group",
      links.recipients(bot, "selected", ["333"], CFG) == [])
links.claim(BID, "di@x.com", {"id": 444, "first_name": "Di"}, "444", False, CFG)
check("a person claimed LATER is included for All",
      links.recipients(bots.get(BID, CFG), "all", [], CFG) == ["111", "222", "444"])
links.unlink(BID, "bo@x.com", CFG)
check("a REMOVED (blocked) person is never resolved, in either mode",
      "222" not in links.recipients(bots.get(BID, CFG), "all", [], CFG)
      and links.recipients(bots.get(BID, CFG), "selected", ["222"], CFG) == [])
bots.save_bot({"id": BID, "emails": ["ana@x.com", "cy@x.com", "di@x.com"]}, CFG)
check("an address taken off the list stops its person at once",
      links.recipients(bots.get(BID, CFG), "all", [], CFG) == ["111", "444"])
check("selected ids are deduped and capped",
      links.recipients(bots.get(BID, CFG), "selected", ["111", "111", "444"], CFG)
      == ["111", "444"])
check("an unknown mode resolves NOBODY (never everyone)",
      links.recipients(bots.get(BID, CFG), "", [], CFG) == []
      and links.recipients(bots.get(BID, CFG), "everyone", [], CFG) == [])

people = links.people(bots.get(BID, CFG), CFG)
check("people(): each linked person with name, @username, email and whether they "
      "can be reached privately; groups listed by chat id",
      [(p["user_id"], p["username"], p["name"], p["email"], p["private"])
       for p in people["people"]]
      == [("111", "ana", "Ana", "ana@x.com", True), ("333", "", "Cy", "cy@x.com", False),
          ("444", "", "Di", "di@x.com", True)]
      and people["groups"] == [{"chat_id": "-100777", "people": ["333"]}], people)

# -- 4. route gates ----------------------------------------------------------------------
try:
    import fastapi                                                # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f))
    fa.__getattr__ = lambda name: object

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail="", headers=None):
            super().__init__(detail)
            self.status_code, self.detail = status_code, detail
    fa.HTTPException, fa.Request = _HTTPException, object
    fr = types.ModuleType("fastapi.responses")
    fr.JSONResponse = lambda content, headers=None, status_code=200: types.SimpleNamespace(
        body=content, headers=headers or {}, status_code=status_code)
    fr.__getattr__ = lambda name: object
    sys.modules.update({"fastapi": fa, "fastapi.responses": fr})
from fastapi import HTTPException                               # noqa: E402
from core.routes import telegram as tg_route                   # noqa: E402
from core.services import auth as auth_service                 # noqa: E402
from core.routes import perm                                   # noqa: E402

auth_service.is_site_admin = lambda user, cfg=None: user == "admin"


def _require(request, cap):
    if not (request.state.user in ("admin", "editor") and cap == "workflow.edit"):
        raise HTTPException(status_code=403, detail="no")


perm.require = _require
tg_route._bot_for_request = lambda request, bot="": bots.get(BID, CFG)


def req(user):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user=user),
        app=types.SimpleNamespace(state=types.SimpleNamespace(config_dir=CFG)))


def call(user):
    try:
        r = tg_route.recipients(req(user))
    except HTTPException as exc:
        return exc
    # The real fastapi JSONResponse holds its body as rendered bytes; the stub
    # above keeps the dict. Read both the same way.
    if isinstance(getattr(r, "body", None), (bytes, bytearray)):
        import json as _json                                     # noqa: PLC0415
        r = types.SimpleNamespace(body=_json.loads(r.body), headers=r.headers)
    return r


r = call("admin")
check("a site admin sees each person with the email the Connected Bots page shows",
      r.body["people"][0]["email"] == "ana@x.com" and r.headers.get("Cache-Control")
      == "no-store", r)
r = call("editor")
check("a workflow editor sees names and @usernames, never an email",
      r.body["people"] and all("email" not in p for p in r.body["people"])
      and r.body["people"][0]["name"] == "Ana", r.body)
r = call("viewer")
check("anyone else is refused", isinstance(r, HTTPException) and r.status_code == 403, r)
src = io.open(os.path.join(ROOT, "core", "routes", "telegram.py"), encoding="utf-8").read()
check("the app's resolve route is loopback-only and names an OUTPUT, never people",
      "def resolve_recipients(request: Request, out: str = \"\")" in src
      and "net.local_caller(request)" in src.split("def resolve_recipients")[1][:1500])
auth_src = io.open(os.path.join(ROOT, "core", "routes", "auth.py"), encoding="utf-8").read()
check("...let through the session wall like query-value",
      '"/api/telegram/recipients/resolve"' in auth_src.split("OPEN_PREFIXES")[1][:3000])

# -- 5. the app --------------------------------------------------------------------------
from app import outputs                                         # noqa: E402
clock = [1000.0]
outputs._now = lambda: clock[0]
outputs._sleep = lambda s: slept.append(s)
slept = []
answers = {"chats": ["111", "222", "111"]}
asked = []
posts = []
fail_for = set()
throttle = {}


class _Resp:
    def __init__(self, body):
        self.body = body

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass


import urllib.error                                             # noqa: E402
import urllib.request                                           # noqa: E402


def fake_urlopen(req_, timeout=None):
    url = req_ if isinstance(req_, str) else req_.full_url
    if "recipients/resolve" in url:
        asked.append(url)
        if answers is None:
            raise OSError("master down")
        return _Resp(json.dumps({"ok": True, **answers}).encode())
    data = req_.data or b""
    if data[:1] == b"{":
        body = json.loads(data.decode())
    else:                                       # multipart: read its chat_id field
        import re as _re
        m = _re.search(rb'name="chat_id"\r\n\r\n([^\r]*)', data)
        body = {"chat_id": m.group(1).decode() if m else ""}
    chat = str(body.get("chat_id") or "")
    if throttle.get(chat):
        throttle[chat] -= 1
        raise urllib.error.HTTPError(url, 429, "Too Many Requests",
                                     {"Retry-After": "2"}, io.BytesIO(
                                         b'{"ok":false,"parameters":{"retry_after":2}}'))
    if chat in fail_for:
        raise OSError("chat not found")
    posts.append((url.rsplit("/", 1)[-1], chat))
    return _Resp(b'{"ok":true}')


urllib.request.urlopen = fake_urlopen
outputs.threading.Thread = lambda target, daemon=True: type(
    "T", (), {"start": staticmethod(target)})()
o = {"id": "wf_m", "type": "telegram", "bot_token": TOKEN, "chat_id": "",
     "message": "hi", "to_mode": "all",
     "to_url": "http://127.0.0.1:5110/api/telegram/recipients/resolve?out=wf_m"}
log_buf = io.StringIO()
import logging                                                  # noqa: E402
h = logging.StreamHandler(log_buf)
h.setLevel(logging.INFO)
logging.getLogger().addHandler(h)
logging.getLogger().setLevel(logging.INFO)

outputs._telegram(dict(o), {}, {})
check("the app asks the master at SEND time and sends one message per chat, deduped",
      len(asked) == 1 and posts == [("sendMessage", "111"), ("sendMessage", "222")], posts)
posts.clear()
answers["chats"] = ["111", "222", "444"]
outputs._telegram(dict(o), {}, {})
check("...cached for 60 s (no second ask inside the minute)",
      len(asked) == 1 and [c for _m, c in posts] == ["111", "222"], (asked, posts))
clock[0] += 61
posts.clear()
outputs._telegram(dict(o), {}, {})
check("...and asked again after it: a person claimed since is included",
      len(asked) == 2 and [c for _m, c in posts] == ["111", "222", "444"], posts)
answers_backup = answers
answers = None
clock[0] += 61
posts.clear()
outputs._telegram(dict(o), {}, {})
check("master down: the last good list is used (within 10 minutes)",
      [c for _m, c in posts] == ["111", "222", "444"], posts)
clock[0] += 601
posts.clear()
outputs._telegram(dict(o), {}, {})
check("...past 10 minutes the send is SKIPPED, never sent to everyone",
      posts == [], posts)
answers = answers_backup
clock[0] += 61
answers["chats"] = [str(i) for i in range(1, 80)]
fail_for = {"3"}
posts.clear()
outputs._telegram(dict(o), {}, {})
check("one failed recipient never stops the rest, and at most 50 are sent",
      len(posts) == 49 and ("sendMessage", "3") not in posts
      and ("sendMessage", "50") in posts and ("sendMessage", "51") not in posts, len(posts))
check("...paced: a small pause between sends (Telegram's ~30/s)",
      len([s for s in slept if 0 < s < 1]) >= 40, slept[:5])
fail_for = set()
answers["chats"] = ["111"]
throttle["111"] = 1
clock[0] += 61
posts.clear()
slept.clear()
outputs._telegram(dict(o), {}, {})
check("a 429 waits the retry-after Telegram gives, then sends",
      2 in slept and posts == [("sendMessage", "111")], (slept, posts))
logged = log_buf.getvalue()
check("the log says counts, never a chat id or a name",
      "111" not in logged and "444" not in logged, logged[-400:])
legacy = dict(o)
legacy.pop("to_mode")
legacy["chat_id"] = "999"
asked.clear()
posts.clear()
outputs._telegram(legacy, {}, {})
check("a legacy node still posts to its one chat, asking nobody",
      asked == [] and posts == [("sendMessage", "999")], posts)

# Files go to every recipient.
answers["chats"] = ["111", "222"]
clock[0] += 61
posts.clear()
outputs._csv_fetch_all = lambda o_: [("a.csv", b"x,y\n1,2\n", "1")]
outputs._telegram(dict(o), {}, {})
check("the CSV files are sent to each recipient, as a document each",
      posts == [("sendDocument", "111"), ("sendDocument", "222")], posts)

# -- 6. Send a test now ------------------------------------------------------------------
wsrc = io.open(os.path.join(ROOT, "core", "routes", "workflow.py"), encoding="utf-8").read()
test_route = wsrc.split('name="workflow.test_output"')[1].split("\ndef csv_tokens")[0]
check("a test send resolves the chosen people and goes to the first 3, saying so",
      "TEST_RECIPIENTS = 3" in wsrc and "telegram_links.recipients(" in test_route
      and "a test goes to the first" in wsrc)

from core.routes import workflow as wr                           # noqa: E402
g = {"nodes": [{"id": "m", "type": "output.telegram", "data": {"to_mode": "all"}},
               {"id": "k", "type": "output.telegram", "data": {"chat_id": "1"}}]}
EMPTY = bots.save_bot({"name": "Lonely", "token": TOKEN, "emails": ["z@x.com"]}, CFG)
tg_route._bot_for_request = lambda request, bot="": bots.get(EMPTY, CFG)
w = wr._telegram_people_warnings(req("admin"), g)
check("a node sending to linked people on a bot nobody is linked to warns, "
      "and a typed-chat node does not",
      w == [{"node": "m", "message": "Nobody is linked to this bot yet: they send "
                                     "/start to the bot first"}], w)
tg_route._bot_for_request = lambda request, bot="": bots.get(BID, CFG)
check("...and says nothing once somebody is linked",
      wr._telegram_people_warnings(req("admin"), g) == [])

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
