"""The box answering Telegram, not just shouting into it.

Every Telegram feature until now was outbound: something happened, a message
went out. This is the other direction -- somebody types /summary from outside
the network, taps a period, and gets the numbers back.

The awkward facts it is built around:

  * the detection app has NO database (it emits events, core writes them), so
    a command is answered by the master, which has one, and the app is not
    involved at all;
  * Telegram allows ONE getUpdates consumer per token, which is why a bot
    belongs to exactly one system -- two masters polling one token starve each
    other with 409s;
  * Telegram has no login. The allowlist is the entire security model, empty
    means nobody, and an unknown chat is met with silence rather than a
    refusal that would confirm a real system is behind the name;
  * a period arrives from a stranger's phone, so it is BOUND into the
    statement, never formatted into it.

    python tests/smoke_tgcommand.py
"""
import datetime
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc              # noqa: E402
from core.services import telegram_commands as tg             # noqa: E402
from core.services import telegram_net as net                 # noqa: E402
from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def when(text):
    return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")


def day(dt):
    return dt.strftime("%Y-%m-%d") if dt else None


TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "pid", "type": "text"}]}}
SQL = "select pid from counting where ts >= %(from)s and ts < %(to)s"

# -- 1. which window a button covers ------------------------------------------
# Wednesday 9 September 2026, 14:30.
now = when("2026-09-09 14:30")

start, stop = tg.bounds("today", now)
check("today is midnight to midnight, half-open",
      day(start) == "2026-09-09" and day(stop) == "2026-09-10",
      "a closed range double-counts the row at the boundary")
check("yesterday is the day before, and stops where today starts",
      day(tg.bounds("yesterday", now)[0]) == "2026-09-08"
      and day(tg.bounds("yesterday", now)[1]) == "2026-09-09")
check("this week runs from MONDAY", day(tg.bounds("week", now)[0]) == "2026-09-07",
      "a shift week starts on Monday everywhere this runs")
check("last 7 days includes today and six before it",
      day(tg.bounds("last7", now)[0]) == "2026-09-03"
      and day(tg.bounds("last7", now)[1]) == "2026-09-10")
check("this month runs from the 1st", day(tg.bounds("month", now)[0]) == "2026-09-01")
check("all time has no bounds at all", tg.bounds("none", now) == (None, None),
      "a query that already says what it wants must not be given a window")

# -- 2. dates typed on a phone -------------------------------------------------
# Typed by somebody who is not at a keyboard, so this is deliberately forgiving.
s1, e1, why = tg.parse_dates("2026-09-01", now)
check("one date is that day", day(s1) == "2026-09-01" and day(e1) == "2026-09-02", why)
s2, e2, why = tg.parse_dates("2026-09-01 to 2026-09-07", now)
check("two dates are a range, and the LAST day is included",
      day(s2) == "2026-09-01" and day(e2) == "2026-09-08", why,)
check("...whatever separator was used",
      tg.parse_dates("2026-09-01 - 2026-09-07", now)[1] == e2
      and tg.parse_dates("2026-09-01..2026-09-07", now)[1] == e2
      and tg.parse_dates("2026-09-01 2026-09-07", now)[1] == e2)
check("...and in either order",
      tg.parse_dates("2026-09-07 to 2026-09-01", now)[0] == s2)
check("day/month/year is read as written",
      day(tg.parse_dates("01/09/2026", now)[0]) == "2026-09-01")

check("something that is not a date says so, naming the part it choked on",
      "tomorrow" in (tg.parse_dates("tomorrow", now)[2] or ""))
check("...a year-wide range is refused with the limit",
      str(tg.MAX_DAYS) in (tg.parse_dates("2020-01-01 to 2026-01-01", now)[2] or ""))
check("...and a date in the future says nothing is recorded",
      "future" in (tg.parse_dates("2027-01-01", now)[2] or ""))

# -- 3. what a chat is shown ---------------------------------------------------
cmd = {"id": "wf_c1", "command": "summary", "title": "Shift summary",
       "buttons": [{"label": "Today", "period": "today"},
                   {"label": "This week", "period": "week"},
                   {"label": "Pick dates", "period": "custom"}]}
kb = tg.keyboard(cmd)
check("the keyboard lays buttons out two per row",
      [len(r) for r in kb["inline_keyboard"]] == [2, 1])
check("...and a callback names a choice the box already published",
      kb["inline_keyboard"][0][0]["callback_data"] == "c:wf_c1:0",
      "a callback is a string a client can forge, so it must not describe a "
      "choice the box should make")

hello = tg.welcome([cmd], "Warehouse A")
check("the welcome names the system", "Warehouse A" in hello)
check("...and lists the commands that ACTUALLY compiled",
      "/summary" in hello and "Shift summary" in hello,
      "a hand-written menu promises whatever it was written to say, and goes "
      "stale the first time somebody renames a command")
check("...saying so plainly when there are none",
      "Nothing is set up" in tg.welcome([], "Warehouse A"))

# HTML, not Markdown: a camera called bay_1 or a class called * turns Markdown
# into a parse error Telegram refuses the WHOLE message for.
answer = tg.table(["zone", "n"], [{"zone": "bay_1", "n": 3},
                                  {"zone": "a<b>", "n": 1}],
                  title="Shift summary - Today", period="09 Sep 2026")
check("an answer escapes what HTML mode reserves",
      "&lt;b&gt;" in answer and "<b>Shift summary" in answer, answer)
check("...and an empty result is a sentence, not an empty table",
      "Nothing recorded" in tg.table(["zone"], [], title="x"))
big = tg.table(["zone"], [{"zone": f"z{i}"} for i in range(500)], title="x")
check("a long answer is cut to fit a message, and says it was cut",
      len(big) <= 4096 and "of 500 rows." in big and "Showing the first" in big,
      "a table that stops mid-row without a word reads as a table that ended")

# -- 4. who may ask ------------------------------------------------------------
check("an empty allowlist means NOBODY",
      not net.allowed({"allow": []}, "123"),
      "a bot's @name is discoverable and Telegram has no login - a default of "
      "'everyone' is somebody's production data in a stranger's chat")
check("a listed chat may ask", net.allowed({"allow": ["123", "-100999"]}, "123"))
check("...and one that is not, may not",
      not net.allowed({"allow": ["123"]}, "124"))

netsrc = read("core", "services", "telegram_net.py")
check("an unknown chat is met with SILENCE, not a refusal",
      "Silence." in netsrc and "not on the allowlist" in netsrc,
      "telling a stranger they are 'not allowed' confirms a real system is "
      "behind the name")
stranger = netsrc.split("def _handle(")[1].split("commands = dc.get_commands")[0]
check("a stranger's /start is the one message answered without a claim, on a bot "
      "that lists addresses, and only after the allowlist said no (Tok Aba, 2026-10-14)",
      stranger.index("if not allowed(bot, chat_id)") < stranger.index("_is_start(text, token)")
      and 'bot.get("emails")' in stranger.split("if not allowed(bot, chat_id)")[1]
      and "_real_person(sender, message)" in stranger.split("if not allowed(bot, chat_id)")[1])
check("...its wording lives in one place, as constants, with no em dash",
      all(isinstance(getattr(net, k), str) and "\u2014" not in getattr(net, k)
          for k in ("START_HELLO", "START_HELLO_PLAIN", "START_ASK_PRIVATE",
                    "START_ASK_GROUP", "START_RETRY_PRIVATE", "START_RETRY_GROUP"))
      and "{bot}" in net.START_HELLO)
check("...and a registered chat never goes through it: /start and /help there are "
      "the welcome, every command answers directly",
      'if word in ("start", "help"):' in netsrc and "tg.welcome(commands" in netsrc)
check("a claim is judged by claim_text before the allowlist: in a group only "
      "one addressed to the bot (/link, a mention, a reply) is a claim",
      "telegram_links.claim_text(" in netsrc and "_to_bot(message, token)" in netsrc
      and "telegram_links.looks_like_email(text)):" not in netsrc)
from core.services import telegram_links as links              # noqa: E402
check("...so a group's ordinary text and commands never reach the claim path",
      links.claim_text("/start", "group") == ""
      and links.claim_text("/summary today", "group") == ""
      and links.claim_text("mail ana@x.com later", "group") == ""
      and links.claim_text("/link ana@x.com", "supergroup") == "ana@x.com")
check("the period is BOUND into the statement, never pasted",
      'params = {"from": start, "to": stop}' in netsrc
      and "params=params" in netsrc,
      "the one value in this feature a stranger controls")
console = read("core", "services", "sqlconsole.py")
check("...and the runner binds it as a parameter",
      "cur.execute(text, params)" in console)

# -- 5. one consumer per token -------------------------------------------------
check("a 409 is named as the stale master it is",
      "another process is already polling" in netsrc,
      "Telegram starves the first poller silently, so this has to be said")
check("the poll acknowledges what it handled, so a restart does not replay",
      "offset" in netsrc and 'payload["offset"] = int(offset)' in netsrc)
master = read("master_server.py")
check("the master runs the poll loop",
      "_telegram_command_loop(app)" in master
      and "asyncio.create_task(_telegram_command_loop(app))" in master)
loop = master.split("async def _telegram_command_loop(app):")[1].split("\n\n\n")[0]
check("...only where a bot is assigned",
      "bots_service.for_system(slug, config_dir)" in loop,
      "two masters polling one token is the 409 above")
check("...off the event loop, because a poll blocks for a minute",
      loop.count("asyncio.to_thread") >= 2)
check("...and a failed poll backs off rather than spinning",
      "await asyncio.sleep(15)" in loop)

# -- 6. the canvas -------------------------------------------------------------
check("a Query may answer a button",
      wf.edge_allowed("storage.query", "integration.command")[0])
ok, why = wf.edge_allowed("analytics.formula", "integration.command")
check("...and nothing else may", not ok, why)
ok, why = wf.edge_allowed("integration.command", "output.telegram")
check("a Command feeds nothing on the canvas", not ok, why)
mirror = read("frontend", "src", "workflow", "registry.js")
check("the browser knows the same rules",
      '"storage.query" && tgtType === "integration.command"' in mirror)

spec = wf.REGISTRY["integration.command"]
check("the Command node lives in the Integration drawer",
      spec["palette"] == {"cat": "integration", "group": "Telegram"})
check("...and compiles to a command, not an output",
      spec["output_type"] is None)

page_src = read("frontend", "src", "pages", "Workflow.jsx")
check("a Command is not told to wire an Analytics node into it",
      "SELF_FIRED_OUT = [" in page_src
      and "!SELF_FIRED_OUT.includes(node.type)" in page_src,
      "it wires as an output, but a person fires it and a Query feeds it - the "
      "output panel told an operator to draw a wire the compiler refuses")

nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
check("one port per button, labelled with the button's own text",
      "const CommandNode" in nodes_src
      and "id: `b${i}`, label: b?.label" in nodes_src,
      "the wire IS the answer: which Query is on which port")


# -- 7. compiling one ----------------------------------------------------------
def graph(buttons=None, command="summary", wire=(0,)):
    nodes = [
        {"id": "t0", "type": "output.dataset", "position": {"x": 0, "y": 0},
         "data": {"dataset": "counting", "name": "counting", "mode": "change",
                  "sources": {"pid": {"src": "event", "field": "track_id"}}}},
        {"id": "q", "type": "storage.query", "position": {"x": 2, "y": 0},
         "data": {"name": "Rejects", "sql": SQL}},
        {"id": "c", "type": "integration.command", "position": {"x": 4, "y": 0},
         "data": {"command": command, "title": "Shift summary",
                  "buttons": buttons if buttons is not None
                  else [{"label": "Today", "period": "today"},
                        {"label": "Pick dates", "period": "custom"}]}},
    ]
    edges = [{"id": "e_tq", "source": "t0", "target": "q",
              "targetHandle": "table"}]
    for i in wire:
        edges.append({"id": f"e_qc{i}", "source": "q", "target": "c",
                      "targetHandle": f"b{i}"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


out = wf.compile_graph(graph(wire=(0, 1)), [], tables=TABLES)
check("a Command node compiles to a command document",
      len(out["commands"]) == 1, out["errors"])
made = out["commands"][0]
check("...carrying the command word and its buttons",
      made["command"] == "summary" and len(made["buttons"]) == 2, made)
check("...and the STATEMENT of the Query wired to each button",
      made["buttons"][0]["sql"] == SQL,
      "copied the way a CSV node copies one -- what runs has to be what was "
      "compiled, not whatever the canvas says next week")
check("...with the tables that Query may read",
      [t["key"] for t in made["buttons"][0]["tables"]] == ["counting"])

# ...and through compile_STORE, which is what the route actually calls. The
# graph-level test above passed while the store-level merge dropped commands on
# the floor, so a canvas saved on a real box wrote no [[commands]] at all and
# the bot answered "I do not answer that one" to a command it plainly had.
store = wf.compile_store({"workflows": [{"id": "main", "enabled": True,
                                         "graph": graph(wire=(0, 1))}]},
                         [], tables=TABLES)
check("compile_store carries the commands out with the outputs",
      len(store.get("commands") or []) == 1,
      "the route compiles the STORE, never one graph")

lone = wf.compile_graph(graph(wire=()), [], tables=TABLES)
check("a button with no Query is warned about by name",
      any("Today" in m["message"] for m in lone["warnings"]), lone["warnings"])
check("...and a command left with no answerable button compiles to nothing",
      not lone["commands"])

for word, why_ in (("", "unnamed"), ("Summary!", "not a command word")):
    refused = wf.compile_graph(graph(command=word), [], tables=TABLES)
    check(f"a command that is {why_} is refused",
          bool(refused["errors"]) and not refused["commands"])

# Two nodes answering one word means whichever compiled last wins, silently.
g = graph(wire=(0,))
g["nodes"].append({"id": "c2", "type": "integration.command",
                   "position": {"x": 6, "y": 0},
                   "data": {"command": "summary",
                            "buttons": [{"label": "Today", "period": "today"}]}})
g["edges"].append({"id": "e_qc2", "source": "q", "target": "c2",
                   "targetHandle": "b0"})
dup = wf.compile_graph(wf.clean_graph(g), [], tables=TABLES)
check("two commands answering one word is refused, not silently resolved",
      any("already answered" in m["message"] for m in dup["errors"]),
      dup["errors"])

# -- 8. the stored document ----------------------------------------------------
# Written by the compiler, read by the poller: one route writes and another
# reads, so the boundary checks rather than trusts.
def refused(**over):
    doc = dict(made, **over)
    try:
        dc._validate_command(doc)
        return ""
    except (ValueError, TypeError) as exc:
        return str(exc)


check("a compiled command survives the document boundary", not refused(),
      refused())
check("...a button that is not a SELECT is refused",
      "only SELECT" in refused(buttons=[{**made["buttons"][0],
                                         "sql": "delete from counting"}]))
check("...two statements in one button are refused",
      "only SELECT" in refused(buttons=[{**made["buttons"][0],
                                         "sql": "select 1; drop table counting"}]))
check("...and a button naming no tables is refused",
      "tables" in refused(buttons=[{**made["buttons"][0], "tables": []}]))

check("the artifact write stores them",
      "dc.set_commands(systems_dir, slug, compiled.get(\"commands\")"
      in read("core", "services", "workflow.py"))

# -- 8a. running a button's statement by hand ---------------------------------
# A statement written for a button carries %(from)s and %(to)s, which only mean
# something once somebody taps. Run as typed they reach Postgres as a bare
# percent: `syntax error at or near "%"` -- an error about the symptom, on a
# statement that is correct.
from core.services import sqlconsole as console_service       # noqa: E402

check("the console notices a statement that takes a period",
      console_service.wants_dates(SQL)
      and not console_service.wants_dates("select 1 from counting"))
bound, note = console_service.preview_params(SQL, now=when("2026-09-09 14:30"))
check("...and binds a window rather than refusing",
      day(bound["from"]) == "2026-09-09" and day(bound["to"]) == "2026-09-10")
check("...saying which, because a preview over a window nobody chose reads "
      "as a wrong answer",
      "today" in note and "2026-09-09" in note)
check("a statement that binds nothing is still run with no params at all",
      console_service.preview_params("select 1")[0] is None,
      "psycopg2 makes every literal percent an escape the moment params are "
      "passed")
routes_src = read("core", "routes", "datasets.py")
check("the console route uses it",
      "sqlconsole.preview_params(body.get(\"sql\")," in routes_src
      and 'out["bound"] = note' in routes_src)
check("...and both consoles say so",
      "state.bound" in read("frontend", "src", "pages", "Workflow.jsx")
      and "res.bound" in read("frontend", "src", "pages", "Database.jsx"))


# -- 8b. the "/" menu ----------------------------------------------------------
# Telegram's own feature: the client lists the commands the moment somebody
# types a slash, and filters as they keep typing. A menu we sent as a message
# would be scrolled past within a day.
menu = []


def fake_api(token, method, payload, timeout=20):
    menu.append((method, payload))
    return True


real_api, net._api = net._api, fake_api
net.publish_commands("t", [{"command": "summary", "title": "Shift summary"},
                           {"command": "hi", "title": "A"},
                           {"command": "", "title": "nameless"}])
net._api = real_api
check("the command menu is published to Telegram",
      bool(menu) and menu[0][0] == "setMyCommands", menu)
sent = menu[0][1]["commands"] if menu else []
check("...built from the commands that actually compiled",
      [c["command"] for c in sent] == ["summary", "hi"],
      "a command with no word is not a menu entry")
check("...and a title too short for Telegram is padded, not refused",
      all(len(c["description"]) >= 3 for c in sent),
      "Telegram refuses a description under 3 characters, and 'A' is a real "
      "thing an operator types")
check("the menu is republished only when the canvas changes it",
      net.menu_digest([{"command": "a", "title": "x"}])
      != net.menu_digest([{"command": "a", "title": "y"}])
      and "digest != published" in master,
      "setMyCommands every 50 seconds forever, on a factory uplink")


# -- 8c. a button answered as a picture ----------------------------------------
from core.services import telegram_chart as chart             # noqa: E402

check("a button carries how it is drawn as well as its period",
      tg.clean_buttons([{"label": "W", "period": "week", "chart": "bar"}])
      == [{"label": "W", "period": "week", "chart": "bar"}])
check("...and anything written before charts existed is a table",
      tg.clean_buttons([{"label": "W", "period": "week"}])[0]["chart"] == "table"
      and tg.clean_buttons([{"chart": "sculpture"}])[0]["chart"] == "table")

rows = [{"date": "2026-09-0%d" % i, "n": i * 3} for i in range(1, 8)]
png = chart.render(["date", "n"], rows, "bar", title="Rejects")
check("a result with labels and numbers draws",
      bool(png) and png[1:4] == b"PNG", str(png)[:40] if png else "nothing")
check("...as a line and as a pie too",
      bool(chart.render(["date", "n"], rows, "line"))
      and bool(chart.render(["date", "n"], rows, "pie")))
check("a result with NO number to plot falls back to the table",
      chart.render(["a", "b"], [{"a": "x", "b": "y"}], "bar") is None,
      "an operator who picked 'bar' on a text query wants their rows, not "
      "silence")
check("...as does an empty one, and a single column",
      chart.render(["date", "n"], [], "bar") is None
      and chart.render(["n"], [{"n": 1}], "bar") is None)
check("...and 'table' is never drawn as a picture",
      chart.render(["date", "n"], rows, "table") is None)
labels, values = chart._top_slices(["z%d" % i for i in range(20)],
                                   list(range(1, 21)))
check("a pie keeps the big slices and sums the tail into one",
      len(values) == chart.MAX_SLICES and labels[-1] == "other"
      and sum(values) == sum(range(1, 21)),
      "a pie with twenty slivers says less than one with eight")
check("the report sends a photo when the button asked for one",
      'button.get("chart", "table") != "table"' in netsrc
      and "send_photo(token, chat_id, png" in netsrc)
check("...and multipart is spelled by code point, not by escape",
      "_CRLF = chr(13) + chr(10)" in netsrc,
      "an escape mangled in transit breaks an upload with no error to read")


# -- 8c2. a button answered with pictures --------------------------------------
# A Query button says how many; this one shows which. Capped at ten pictures
# over at most 24 hours -- not an arbitrary limit but the line between two
# features: a glance belongs in a chat, a shift of evidence belongs in
# Footage > Export, which sends the ORIGINALS (Telegram recompresses an album).
from core.services import telegram_photos as ph               # noqa: E402

check("a rolling hour is the hour before now, not since the clock struck",
      tg.bounds("hour", now)[1] == now
      and (now - tg.bounds("hour", now)[0]).seconds == 3600)
check("...and last 24 hours is a day back from now",
      round(tg.span_hours(*tg.bounds("last24", now))) == 24)
check("a day or less may be sent as an album",
      tg.album_period_ok("hour") and tg.album_period_ok("last24")
      and tg.album_period_ok("today") and tg.album_period_ok("yesterday"))
check("...and anything wider may not",
      not tg.album_period_ok("week") and not tg.album_period_ok("last7")
      and not tg.album_period_ok("month") and not tg.album_period_ok("last30")
      and not tg.album_period_ok("none"),
      "a button that could ask for a month is a button that always overflows")
# ...on every day of the year, not just the day the suite runs. A calendar
# period is one day wide on the day it starts: asked on a Monday "this week"
# spans 24 hours, and CI running on a Monday accepted a button that overflowed
# for the other six days.
check("...judged at the period's widest, whatever today is",
      tg.widest_hours("week") == 168 and tg.widest_hours("month") == 744
      and tg.widest_hours("today") == 24 and tg.widest_hours("hour") == 1,
      "a button is drawn once and pressed for months; the day it was drawn "
      "must not decide what it may ask for")
for _day in range(31):
    _when = datetime.datetime(2026, 1, 1, 12, 0) + datetime.timedelta(days=_day)
    if tg.span_hours(*tg.bounds("week", _when)) <= tg.MAX_ALBUM_HOURS:
        check("...including the day a calendar period is at its narrowest",
              not tg.album_period_ok("week"),
              f"on {_when:%A %d %b} 'this week' spans one day and was accepted")
        break
check("...while 'pick dates' can only be judged when the dates arrive",
      tg.album_period_ok("custom")
      and "MAX_ALBUM_HOURS" in netsrc.split("def _album")[1],
      "the dates come from a chat months after anybody looked at the node")

check("an album is ten pictures, which is Telegram's own limit",
      ph.MAX_ALBUM == 10)
check("...and the caption says when it is showing part of something",
      "of 40 snapshots" in ph.summary([{}] * 10, 40, "today")
      and "Footage" in ph.summary([{}] * 10, 40, "today"),
      "an album silently holding the newest ten of forty reads as 'there "
      "were ten'")
check("...and says so plainly when there is nothing",
      "No snapshots" in ph.summary([], 0, "today"))
check("the pictures go as ONE media group, not ten messages",
      "sendMediaGroup" in netsrc and "attach://" in netsrc,
      "ten messages is ten buzzes on somebody's phone for one answer")
check("...with the caption on the FIRST photo, where Telegram shows it",
      "if caption and not media:" in netsrc)

check("a Snapshots node may answer a button",
      wf.edge_allowed("integration.photos", "integration.command")[0])
ok, why = wf.edge_allowed("storage.query", "integration.photos")
check("...and nothing feeds it - the pictures are already on disk", not ok, why)
ok, why = wf.edge_allowed("integration.photos", "output.telegram")
check("...and it answers a button, not a message node", not ok, why)
check("the browser knows the same rules",
      '"integration.photos" && tgtType === "integration.command"' in mirror)


def photo_graph(period="today", most=10):
    nodes = [
        {"id": "p", "type": "integration.photos", "position": {"x": 2, "y": 0},
         "data": {"camera": "cam1", "most": most}},
        {"id": "c", "type": "integration.command", "position": {"x": 4, "y": 0},
         "data": {"command": "photos", "title": "What it saw",
                  "buttons": [{"label": "Recent", "period": period}]}},
    ]
    edges = [{"id": "e_pc", "source": "p", "target": "c", "targetHandle": "b0"}]
    return wf.clean_graph({"nodes": nodes, "edges": edges})


pout = wf.compile_graph(photo_graph(), [], tables=TABLES)
check("a Snapshots node compiles into the button it answers",
      len(pout["commands"]) == 1
      and pout["commands"][0]["buttons"][0]["kind"] == "photos",
      pout["errors"])
pbtn = pout["commands"][0]["buttons"][0]
check("...carrying the camera and the cap",
      pbtn["camera"] == "cam1" and pbtn["most"] == 10, pbtn)
check("...and a cap above what the box will ever send is brought down",
      wf.compile_graph(photo_graph(most=999), [],
                       tables=TABLES)["commands"][0]["buttons"][0]["most"]
      == ph.MAX_ALL)
check("'all of them' is 0, and means every one up to the box's ceiling",
      wf.compile_graph(photo_graph(most=0), [],
                       tables=TABLES)["commands"][0]["buttons"][0]["most"]
      == ph.ALL and ph.cap(ph.ALL) == ph.MAX_ALL and ph.cap(4) == 4,
      "0 is the one number 'how many' cannot otherwise be, so it needs no "
      "second setting to contradict it")
check("...and more than one album's worth goes as several albums",
      "range(0, len(rows), photos.MAX_ALBUM)" in netsrc
      and "caption if i == 0" in netsrc,
      "the same caption six times reads as six separate answers")
wide = wf.compile_graph(photo_graph(period="last7"), [], tables=TABLES)
check("a picture button asking for a week is refused ON THE CANVAS",
      any("Footage" in m["message"] for m in wide["errors"]), wide["errors"])
check("...where the person who can fix it is looking",
      not wide["commands"])

pdoc = dict(made, buttons=[pbtn])
check("a picture button survives the document boundary",
      dc._validate_command(pdoc)["buttons"][0]["kind"] == "photos")
try:
    dc._validate_command(dict(made, buttons=[{**pbtn, "period": "month"}]))
    wide_refused = ""
except ValueError as exc:
    wide_refused = str(exc)
check("...and the 24-hour ceiling is checked again where it is READ",
      "24 hours" in wide_refused,
      "the poller reads this months after the canvas that wrote it was open")
check("a query button still needs its statement and tables",
      dc._validate_command(made)["buttons"][0]["kind"] == "query")

page_src2 = read("frontend", "src", "pages", "Workflow.jsx")
check("the inspector offers the camera and the cap",
      'label="How many to send"' in page_src2 and "Any camera" in page_src2
      and "All of them" in page_src2)
check("...and names where a longer stretch belongs",
      "Footage → Export" in page_src2)
check("the node draws no input port",
      "const PhotosNode" in read("frontend", "src", "workflow", "nodes.jsx"))


# -- 8d. a number crossing a line ----------------------------------------------
from core.services import alert_watch as aw                   # noqa: E402

check("a Query may be watched",
      wf.edge_allowed("storage.query", "integration.watch")[0])
ok, why = wf.edge_allowed("integration.watch", "output.telegram")
check("...and the watch sends through a message node", ok, why)
ok, why = wf.edge_allowed("integration.watch", "output.buzzer")
check("...but not through anything else", not ok, why)
ok, why = wf.edge_allowed("analytics.formula", "integration.watch")
check("...and nothing but a Query may be watched", not ok, why)
check("the browser knows the same rules",
      '"storage.query" && tgtType === "integration.watch"' in mirror)

check("the first number in the first row is what is tested",
      aw.pick_value(["name", "n"], [{"name": "a", "n": "51"},
                                    {"name": "b", "n": 99}]) == 51.0,
      "one rule, said out loud in the inspector, rather than a guess that "
      "changes with the shape of the result")
check("...and a result with no number tests nothing",
      aw.pick_value(["name"], [{"name": "a"}]) is None
      and not aw.test(">", None, 10))
check("the tests are the six a threshold needs",
      aw.test(">", 11, 10) and not aw.test(">", 10, 10)
      and aw.test(">=", 10, 10) and aw.test("<", 9, 10)
      and aw.test("==", 10, 10) and aw.test("!=", 9, 10))
check("...and one that is not a test is never a firing",
      not aw.test("burn", 10, 1))

# The whole point of a watch: it fires on the CROSSING, not on every check.
st, fired = {}, []
for value in (5, 51, 52, 53):
    send, st = aw.should_send(st, aw.test(">", value, 50), 1000.0, 60)
    fired.append(send)
check("a watch sends when the number crosses, then stops",
      fired == [False, True, False, False],
      "a query stuck at 51 mailing somebody every 15 minutes is how an alert "
      "becomes a mail filter rule")
send, _st2 = aw.should_send({"over": False, "sent": 1000.0}, True, 1500.0, 60)
check("...and a crossing inside the quiet time is held", not send)
send, _st3 = aw.should_send({"over": False, "sent": 1000.0}, True, 9000.0, 60)
check("...but one after it is sent", send)
send, _st4 = aw.should_send({}, True, 1000.0, 60)
check("a watch that is already over the line when it is wired says so",
      send, "which is what somebody who just wired it expects to see")

check("a message may quote the value that tripped it",
      aw.render("{name}: {value} over {threshold}",
                {"name": "Rejects", "value": "51", "threshold": "50"})
      == "Rejects: 51 over 50")
check("...and a number is written as a person writes it",
      aw.number(51.0) == "51" and aw.number(None) == "-")


def watch_graph(op=">", wire_out=True, wire_in=True):
    nodes = [
        {"id": "t0", "type": "output.dataset", "position": {"x": 0, "y": 0},
         "data": {"dataset": "counting", "name": "counting", "mode": "change",
                  "sources": {"pid": {"src": "event", "field": "track_id"}}}},
        {"id": "q", "type": "storage.query", "position": {"x": 2, "y": 0},
         "data": {"name": "Rejects", "sql": SQL}},
        {"id": "w", "type": "integration.watch", "position": {"x": 4, "y": 0},
         "data": {"name": "Too many", "every_min": 15, "period": "today",
                  "op": op, "value": 50, "quiet_min": 60}},
        {"id": "m", "type": "output.telegram", "position": {"x": 6, "y": 0},
         "data": {"chat_id": "781784546", "message": "{name}: {value}"}},
    ]
    edges = [{"id": "e_tq", "source": "t0", "target": "q",
              "targetHandle": "table"}]
    if wire_in:
        edges.append({"id": "e_qw", "source": "q", "target": "w"})
    if wire_out:
        edges.append({"id": "e_wm", "source": "w", "target": "m"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


wout = wf.compile_graph(watch_graph(), [], tables=TABLES)
check("a Watch node compiles to a watch document",
      len(wout.get("watches") or []) == 1, wout["errors"])
watch = (wout.get("watches") or [{}])[0]
check("...carrying the statement, its tables and the test",
      watch.get("sql") == SQL and watch.get("op") == ">"
      and watch.get("value") == 50
      and [t["key"] for t in watch.get("tables") or []] == ["counting"], watch)
check("...and the message written on the node it feeds",
      watch["sends"] == [{"kind": "telegram", "chat_id": "781784546",
                          "message": "{name}: {value}"}],
      "a watch never grew a second place to write a message")
check("the message node fed by a watch is NOT also compiled as an output",
      not [o for o in wout["outputs"] if o["type"] == "telegram"],
      "nothing on the canvas fires it -- the master does, on a timer, so "
      "demanding a rule would put an error on a graph that is right")
wstore = wf.compile_store({"workflows": [{"id": "main", "enabled": True,
                                          "graph": watch_graph()}]},
                          [], tables=TABLES)
check("compile_store carries the watches out too",
      len(wstore.get("watches") or []) == 1,
      "the route compiles the STORE, never one graph -- this is exactly how "
      "commands were lost")
check("a Watch with nothing wired out is refused",
      bool(wf.compile_graph(watch_graph(wire_out=False), [],
                            tables=TABLES)["errors"]))
nowhere = wf.compile_graph(watch_graph(wire_in=False), [], tables=TABLES)
check("...and one with no Query to run compiles to nothing, and says why",
      not (nowhere.get("watches") or [])
      and any("wire the Query" in m["message"] for m in nowhere["warnings"]),
      "a node nobody has wired anything into yet is a draft, not a mistake")


def watch_refused(**over):
    doc = dict(watch, **over)
    try:
        dc._validate_watch(doc)
        return ""
    except (ValueError, TypeError) as exc:
        return str(exc)


check("a compiled watch survives the document boundary", not watch_refused(),
      watch_refused())
check("...a watch that is not a SELECT is refused",
      "only SELECT" in watch_refused(sql="delete from counting"))
check("...one naming no tables is refused",
      "tables" in watch_refused(tables=[]))
check("...one with nowhere to send is refused",
      "nowhere to send" in watch_refused(sends=[]))
check("...and a period a TIMER cannot answer becomes today",
      dc._validate_watch(dict(watch, period="custom"))["period"] == "today",
      "'ask for dates' has nobody to ask")
check("a watch runs no faster than its floor",
      aw.clean_watch({"every_min": 0})["every_min"] == aw.MIN_EVERY_MIN
      and aw.clean_watch({"every_min": 99999})["every_min"] == aw.MAX_EVERY_MIN,
      "a real query on a box doing inference")

check("the artifact write stores the watches",
      'dc.set_watches(systems_dir, slug, compiled.get("watches")'
      in read("core", "services", "workflow.py"))
wloop = master.split("async def _watch_loop(app):")[1].split(chr(10) * 3)[0]
check("the master runs the watch timer",
      "asyncio.create_task(_watch_loop(app))" in master
      and "dc.get_watches" in wloop)
check("...off the event loop, because a query blocks",
      "asyncio.to_thread" in wloop)
check("...binding the period rather than pasting it",
      'params = {"from": start, "to": stop}' in master.split("def _run_watch")[1])
check("a broken statement is logged, not mailed to a shift supervisor",
      "[WATCH]" in master)


# -- 9. the allowlist is edited where the bot is ------------------------------
panel = read("frontend", "src", "components", "TelegramPanel.jsx")
flat = " ".join(panel.split())
check("the settings page edits the allowlists",
      "Allowed Chat IDs" in flat and "Allowed Email Addresses" in flat
      and "Addresses empty to deny command" in flat)
check("...and says what silence means: an unknown chat may only /start",
      "a chat that is not set up yet can send /start" in flat
      and "Anything else from an unknown chat is ignored" in flat)
bots = read("core", "services", "telegram_bots.py")
check("a malformed chat id never reaches the allowlist",
      "not bad_chat(chat)" in bots)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
