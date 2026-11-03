"""The CSV node: a Query's rows, attached to a message.

A Query answers with a TABLE, and until now everything downstream took one
number out of it -- a card, a variable -- because a message can only say a
number. "How many rejects" is answerable; "which ones" was not, and that is
the question a shift report is actually asked.

So: Query -> CSV -> Email/Telegram. Its own node rather than a checkbox on the
message, because "turn this query into a file" is a step, a step two outputs
share should be one thing on the canvas, and the wire says WHICH query on a
message that is wired to several.

The awkward fact it is built around is the same one the query variable has:
the detection app has NO database. It emits events and core writes them. So
the compiled output carries the statement, and the app fetches the rendered
file from this box's own master over localhost when the message fires.

    python tests/smoke_csvnode.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc             # noqa: E402
from core.services import workflow as wf                     # noqa: E402

TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "pid", "type": "text"},
    {"name": "live_counting", "type": "number"}]}}
SQL = "select pid, live_counting from counting"

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def graph(sql=SQL, wire_table=True, wire_query=True, csv_data=None,
          out="output.email"):
    nodes = [
        {"id": "q", "type": "storage.query", "position": {"x": 2, "y": 0},
         "data": {"name": "Rejects", "sql": sql}},
        {"id": "c", "type": "storage.csv", "position": {"x": 4, "y": 0},
         "data": csv_data if csv_data is not None else {"name": "", "max_rows": 0}},
        {"id": "s", "type": "variable.schedule", "position": {"x": 2, "y": 2},
         "data": {"fnId": "clock", "name": "Nightly", "every": "daily",
                  "at": "22:00"}},
        {"id": "m", "type": out, "position": {"x": 6, "y": 0},
         "data": {"to": "a@b.c", "subject": "Rejects", "body": "see {csv}",
                  "chat_id": "123", "bot_token": "1:" + "x" * 24,
                  "message": "see {csv}"}},
    ]
    edges = [{"id": "e_s", "source": "s", "target": "m"}]
    if wire_query:
        edges.append({"id": "e_qc", "source": "q", "target": "c"})
    edges.append({"id": "e_cm", "source": "c", "target": "m"})
    if wire_table:
        nodes.append({"id": "t0", "type": "output.dataset",
                      "position": {"x": 0, "y": 0},
                      "data": {"dataset": "counting", "name": "counting",
                               "mode": "change",
                               "sources": {"pid": {"src": "event",
                                                   "field": "track_id"}}}})
        edges.append({"id": "e_tq", "source": "t0", "target": "q",
                      "targetHandle": "table"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def compiled(**kw):
    return wf.compile_graph(graph(**kw), [], tables=TABLES)


# -- 1. what may be wired to what ---------------------------------------------
check("a Query feeds a CSV", wf.edge_allowed("storage.query", "storage.csv")[0])
check("...and a CSV feeds an Email",
      wf.edge_allowed("storage.csv", "output.email")[0])
check("...and a Telegram",
      wf.edge_allowed("storage.csv", "output.telegram")[0])
# Everywhere else the wire would draw cleanly and then attach nothing.
ok, why = wf.edge_allowed("storage.csv", "dashboard.card")
check("a card cannot carry a file", not ok, why)
ok, why = wf.edge_allowed("storage.csv", "output.snapshot")
check("...nor can a Snapshot", not ok, why)
ok, why = wf.edge_allowed("analytics.formula", "storage.csv")
check("...and only a Query has rows to write", not ok, why)

# The canvas refuses a wire before the server sees it, from its own copy of
# these rules. Server-only permission is a wire that still cannot be drawn --
# it shipped that way once already.
js = read("frontend", "src", "workflow", "registry.js")
check("the canvas knows the same rules",
      'srcType === "storage.query" && tgtType === "storage.csv"' in js
      and 'if (srcType === "storage.csv")' in js
      and 'if (tgtType === "storage.csv")' in js)

# -- 2. what it compiles onto the message -------------------------------------
c = compiled()
check("it compiles", not c["errors"], c["errors"])
doc = next((o for o in c["outputs"] if o["id"] == "wf_m"), None)
check("the message is built", doc is not None, [o["id"] for o in c["outputs"]])
# Copied, not referenced: what is sent has to be what was compiled, or editing
# the canvas silently changes what last night's report will contain.
check("the statement travels ON the output", (doc or {}).get("csv_sql") == SQL,
      (doc or {}).get("csv_sql"))
check("...and the tables its Query may read",
      [t["key"] for t in (doc or {}).get("csv_tables") or []] == ["counting"])
check("...named after the Query when the node says nothing",
      (doc or {}).get("csv_name") == "rejects.csv", (doc or {}).get("csv_name"))
check("...or after the CSV node when it does",
      compiled(csv_data={"name": "Shift report", "max_rows": 50})
      and next(o for o in compiled(csv_data={"name": "Shift report"})["outputs"]
               if o["id"] == "wf_m")["csv_name"] == "shift_report.csv")
check("...with a row cap when one was set",
      next(o for o in compiled(csv_data={"name": "", "max_rows": 50})["outputs"]
           if o["id"] == "wf_m")["csv_rows"] == 50)
# The app has no database, so the document carries the address that renders it.
check("it polls this box and nowhere else",
      str((doc or {}).get("csv_url", "")).startswith("http://127.0.0.1:"),
      (doc or {}).get("csv_url"))
check("...naming the OUTPUT, never a statement",
      (doc or {}).get("csv_url", "").endswith("/api/workflow/query-csv?out=wf_m"))

# The Query is a DATA wire. The message still fires on the schedule -- a wire
# that silently became the trigger would send the report whenever the query
# was recompiled.
check("the message still fires on its Schedule, not on the Query",
      (doc or {}).get("function") == "clock", (doc or {}).get("function"))

tg = next(o for o in compiled(out="output.telegram")["outputs"]
          if o["id"] == "wf_m")
check("a Telegram message carries the same attachment", tg.get("csv_sql") == SQL)

# -- 3. what it refuses --------------------------------------------------------
dry = compiled(wire_query=False)
check("a CSV node with no Query says so",
      any("wire a Query into this CSV node" in m["message"]
          for m in dry["warnings"] + dry["errors"]), dry["warnings"])
check("...and attaches nothing",
      not next(o for o in dry["outputs"] if o["id"] == "wf_m").get("csv_sql"))
notab = compiled(wire_table=False)
check("a Query reading no tables cannot make a file",
      any("Data Table" in m["message"]
          for m in notab["warnings"] + notab["errors"]), notab["warnings"])

# The document boundary re-checks the statement rather than trusting the
# compile that wrote it: written by one route, read by another.
def refused(**over):
    o = dict({"id": "wf_x", "type": "email", "to": ["a@b.c"],
              "csv_sql": SQL, "csv_tables": [TABLES["counting"]],
              "csv_name": "x.csv",
              "csv_url": "http://127.0.0.1:5110/api/workflow/query-csv?out=wf_x"},
             **over)
    try:
        dc._validate_output(o)
        return ""
    except (ValueError, TypeError) as exc:
        return str(exc)


check("a stored attachment is accepted", not refused(), refused())
check("...a statement that is not a SELECT is refused",
      "only SELECT" in refused(csv_sql="delete from counting"))
check("...two statements are refused",
      "only SELECT" in refused(csv_sql="select 1; drop table counting")
      or "one statement" in refused(csv_sql="select 1; drop table counting"))
check("...a url pointing off this box is refused",
      "this box only" in refused(csv_url="http://10.0.0.9/api/workflow/query-csv"),
      refused(csv_url="http://10.0.0.9/api/workflow/query-csv"))
check("...and one with no tables",
      "needs the tables" in refused(csv_tables=[]))

# -- 4. the route that renders it ----------------------------------------------
whole = read("core", "routes", "workflow.py")
route = read("core", "routes", "workflow.py").split(
    '@router.get("/api/workflow/query-csv"')[1].split("\n@router.")[0]
# net.local_caller: loopback AND not relayed by the hub's /s/ proxy.
check("the file is served to this box only",
      "if not net.local_caller(request):" in route)
# The rendering itself is shared with the browser's own download button, so
# the checks below read the helper both routes call.
route += read("core", "routes", "workflow.py").split("def _csv_response(")[1]
check("...and the caller may name an output, never a statement",
      'o.get("csv_sql")' in route and "no such attachment" in route)
# An attachment with no rows is indistinguishable from a quiet period, and the
# one thing a report must not do is say "nothing happened" because the query
# was broken.
check("a broken query is an error, not an empty file",
      "raise HTTPException(status_code=400," in route)
check("...and the row count rides in a header, so a message can say it",
      '"X-Row-Count"' in route)

# -- 5. the app end -------------------------------------------------------------
app = read("systems", "demo", "app", "outputs.py")
check("the app fetches the file rather than running SQL",
      "def _csv_fetch(o):" in app and "psycopg" not in app)
# An attachment that cannot be built must not stop the message: a report
# saying the table was unavailable beats no report, which looks like silence.
check("...and a failed fetch does not stop the message",
      "Never raises" in app.split("def _csv_fetch(o):")[1][:900])
check("...fetched on the sending thread, not the detection loop",
      "files = _csv_fetch_all(o)" in app.split("def _email(")[1].split("def _multipart")[0])
check("email attaches it as a file (every file, smoke_multicsv)",
      "msg.add_attachment(data, maintype=\"text\", subtype=\"csv\"" in app)
# Telegram cannot attach to a text message after the fact, so it is one
# document post rather than a message and an orphaned file under it.
check("telegram sends it as a document with the message as the caption",
      "sendDocument" in app and 'fields["caption"] = text[:1024]' in app)
check("...built with the standard library only",
      "def _multipart(" in app)
# The rows are ALREADY attached. Pasting them into the body as well sent the
# same table twice, and a shift report with two thousand lines of CSV in it is
# one no mail client will show.
check("{csv} names the attached file rather than pasting it",
      "def _csv_inline(" in app
      and 'return str(csv[0] or "query.csv")' in app)
check("...and {csv_text} is there for when the table belongs in the message",
      '"{csv_text}"' in app and "def _csv_text(" in app)
# A message written when {csv_rows} was offered must not start printing raw
# braces into somebody's inbox because the chip was withdrawn.
check("...while {csv_rows} still expands for a graph that used it",
      '"{csv_rows}"' in app)

# The app has no session, and the wall runs before the route: without this line
# the fetch 401s, the message goes out anyway (a report that says the table was
# missing beats no report) -- and what lands in the inbox is a literal {csv}
# with no attachment and nothing anywhere saying why.
auth = read("core", "routes", "auth.py")
check("the app is allowed through the session wall to fetch it",
      '"/api/workflow/query-csv"' in auth.split("OPEN_PREFIXES")[1][:2500])
check("...while the browser's own download button is NOT",
      '"/api/workflow/csv-file"' not in auth,
      "that one stays behind the session wall")

# A test that arrives with a literal {csv} and no file proves the message can
# be sent and nothing about what it will contain.
test_route = whole.split('name="workflow.test_output"')[1].split("\ndef _send_one")[0]
check("a test send builds the same attachment",
      "_csv_payload(" in test_route and 'doc.get("csv_sql")' in test_route)
check("...and expands the same tokens",
      '"{csv}"' in test_route and '"{csv_text}"' in test_route)
send_one = whole.split("def _send_one(")[1].split("\ndef _multipart")[0]
check("...attaching it to the email as a file",
      'msg.add_attachment(f["text"]' in send_one)
check("...and posting it to Telegram as a document",
      "sendDocument" in send_one and 'fields["caption"] = text[:1024]' in send_one)
# A table that stops mid-row without a word reads as a table that ended.
check("...capped, and it says it is capped",
      "truncated; the full" in app)

ui = read("frontend", "src", "pages", "Workflow.jsx")
check("the chips offer {csv} only when a CSV node is wired in",
      "hasCsv && csvFiles.length < 2" in ui and "const hasCsv = (edges || [])" in ui
      and "csvNames(node.id, nodes, edges)" in ui)
check("...offering the file and the table as two different things",
      '"{csv_text}"' in ui and '"{csv_rows}"' not in ui,
      "the row count was a third token for something the table already says")
check("the CSV node has its own settings",
      'node.type === "storage.csv"' in ui and "max_rows:" in ui
      and 'value="most"' in ui and 'value="all"' in ui)

# -- 6. the same file, for the person building the graph -----------------------
# Wiring a Query in and then waiting for 22:00 to find out whether the
# attachment is the right shape is not a way to build anything.
browser = whole.split('@router.post("/api/workflow/csv-file"')[1].split("\ndef _csv_response")[0]
check("the inspector can download the rows now",
      "def csv_file(" in browser)
check("...behind the same permission as the query console",
      'perm.require(request, "database.edit")' in browser)
check("...rendering from the SAVED graph, never from the body",
      "SAVED GRAPH" in browser and "csv_sql" not in browser,
      "a body that could carry SQL is a second query console with no wall")
check("...taking a node id and nothing else",
      're.sub(r"[^A-Za-z0-9_-]"' in browser and '"node"' in browser)
check("the browser button is wired to it",
      "/api/workflow/csv-file" in ui and "function CsvDownload(" in ui)
check("...and is dead until a Query feeds the node", "ready={!!querySrc}" in ui)

# A CSV node is worth LOOKING at before it is worth sending, and looking is how
# somebody decides what to wire it into. The spec rides on the message, so
# there is no compiled document for a node that reaches no Email or Telegram
# yet -- the button therefore reads the saved GRAPH instead of the outputs.
check("the button renders from the saved graph, not the compiled outputs",
      "csv_spec_for_node" in browser and "dc.get_outputs" not in browser,
      "requiring a message first is backwards: looking is how you decide "
      "whether to send")
spec, why = wf.csv_spec_for_node(
    {"workflows": [{"id": "main", "graph": graph()}]}, "c", TABLES)
check("...and one Query is the whole requirement",
      spec and spec["csv_sql"] == SQL, why)
lone, why = wf.csv_spec_for_node(
    {"workflows": [{"id": "main", "graph": graph(wire_query=False)}]}, "c", TABLES)
check("...while a node with nothing wired in says what is missing",
      lone is None and "wire a Query" in why, why)
check("a node that is not a CSV node is not one",
      wf.csv_spec_for_node({"workflows": [{"id": "main", "graph": graph()}]},
                           "q", TABLES)[0] is None)

# One place turns rows into a file, so the app's fetch, the download button and
# the test send cannot drift into attaching subtly different things.
check("there is one renderer", "def _csv_payload(" in whole
      and whole.count("csv.writer(") == 1)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
