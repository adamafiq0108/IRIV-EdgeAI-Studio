"""A Query stored in a Variable.

A Query could only ever draw a card. So a number that only the database knows
-- "rejects this shift", "parts since the last changeover" -- could be LOOKED
at and nothing else: no formula could use it, no When could watch it, no alert
could fire on it. Wiring one into a Variable is what makes it an ordinary
value like any other.

The awkward fact this feature is built around: the detection app has NO
database. It emits events and core writes them; it has never held a Postgres
connection and must not start on a box where it is the process doing
inference. So the variable does not run SQL -- it compiles into a poller
pointed at this system's own master, which does have the database, and the
master answers with one number.

That shape is what this file pins, because every part of it is easy to get
subtly wrong:

  * the statement and tables are COPIED onto the compiled function, so what
    runs is what was compiled rather than whatever the canvas says later;
  * the poll may only ever address this box;
  * a table becomes one number by a rule the operator CHOSE, never a guess;
  * an empty result is not zero.

    python tests/smoke_queryvar.py
"""
import io
import re
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fns                    # noqa: E402
from core.services import sqlconsole                          # noqa: E402
from core.services import workflow as wf                      # noqa: E402

TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "pid", "type": "text"},
    {"name": "live_counting", "type": "number"}]}}

SQL = "select shift, count(*) as seen from counting group by shift"

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def graph(sql=SQL, var="shift_total", wire_table=True, **vd):
    nodes = [{"id": "q", "type": "storage.query", "position": {"x": 3, "y": 0},
              "data": {"name": "By shift", "sql": sql}},
             {"id": "v", "type": "variable.ref", "position": {"x": 5, "y": 0},
              "data": dict({"name": var, "io": "auto"}, **vd)}]
    edges = [{"id": "e_out", "source": "q", "target": "v"}]
    if wire_table:
        nodes.append({"id": "t0", "type": "output.dataset",
                      "position": {"x": 0, "y": 0},
                      "data": {"dataset": "counting", "name": "counting",
                               "mode": "change",
                               "sources": {"pid": {"src": "event",
                                                   "field": "track_id"}}}})
        edges.append({"id": "e0", "source": "t0", "target": "q",
                      "targetHandle": "table"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def compiled(**kw):
    return wf.compile_graph(graph(**kw), [], tables=TABLES)


# -- 1. the wire is legal, and only into the holder ----------------------------
ok, why = wf.edge_allowed("storage.query", "variable.ref")
check("a Query may be stored in a Variable", ok, why)
ok, why = wf.edge_allowed("storage.query", "variable.number")
check("...but not into a constant, which makes its own value", not ok, why)

# The canvas refuses a wire BEFORE the server ever sees it, from its own copy
# of this matrix -- so a rule relaxed on the server alone is a rule that still
# reads as "Invalid connection" while you drag. That is exactly how this shipped
# broken once: the server allowed the wire and the browser would not draw it.
# Compared whole rather than one row, because the next divergence will be a
# different row.
js = read("frontend", "src", "workflow", "registry.js")
block = js.split("const ALLOWED_TARGETS = {")[1].split("\n};")[0]
mirror = {}
for cat, body in re.findall(r"^\s{2}(\w+):\s*\[([^\]]*)\]", block, re.M | re.S):
    mirror[cat] = {t.strip().strip('"\'') for t in body.split(",") if t.strip()}
check("the canvas and the server agree on every wiring rule",
      mirror == {k: set(v) for k, v in wf._ALLOWED_TARGETS.items()},
      "\n      ".join(
          f"{k}: canvas={sorted(mirror.get(k, set()))} "
          f"server={sorted(wf._ALLOWED_TARGETS.get(k, set()))}"
          for k in set(mirror) | set(wf._ALLOWED_TARGETS)
          if mirror.get(k, set()) != set(wf._ALLOWED_TARGETS.get(k, set()))))

# -- 2. what it compiles into ---------------------------------------------------
c = compiled()
check("it compiles", not c["errors"], c["errors"])
fn = next((f for f in c["functions"] if f["id"] == "shift_total"), None)
check("the variable becomes a function of its own", fn is not None,
      [f["id"] for f in c["functions"]])
check("...of the query kind, not an alias of a stat that does not exist",
      fn and fn["type"] == "query_value", fn and fn["type"])
p = (fn or {}).get("params") or {}
# Copied, not referenced: a compiled function has to keep working after the
# canvas is edited, and a variable whose meaning changed when somebody renamed
# a node is a variable nobody can trust.
check("the statement travels ON the function", p.get("sql") == SQL, p.get("sql"))
check("...and so do the tables it may read",
      [t["key"] for t in p.get("tables") or []] == ["counting"], p.get("tables"))
check("...with the columns, so the view can be built without the canvas",
      bool((p.get("tables") or [{}])[0].get("columns")))

# The one address it may ever call. This URL is the app asking the master to
# run SQL for it; pointed anywhere else it would be a compiled document
# telling the box to fetch from a host nobody named.
check("it polls this box and nowhere else",
      str(p.get("url", "")).startswith("http://127.0.0.1:"), p.get("url"))
check("...naming the function, and nothing else",
      p.get("url", "").endswith("/api/workflow/query-value?fn=shift_total"),
      p.get("url"))

# -- 3. one number out of a table, by a rule that was chosen -------------------
check("the default keeps the first row", p.get("pick") == "first")
check("...and the first numeric column, said as a blank", p.get("column") == "")
c2 = compiled(q_column="seen", q_pick="sum")
p2 = next(f for f in c2["functions"] if f["id"] == "shift_total")["params"]
check("a chosen column and reduction are carried",
      (p2.get("column"), p2.get("pick")) == ("seen", "sum"), p2)
# Every tick is a database round trip on a box that is also running inference.
check("the poll has a floor well above an API one",
      fns.MIN_QUERY_POLL_S >= 10.0 and
      next(f for f in compiled(q_interval_s=1)["functions"]
           if f["id"] == "shift_total")["params"]["interval_s"]
      == fns.MIN_QUERY_POLL_S)

# -- 4. what it refuses ---------------------------------------------------------
check("a Query with no statement is caught on the Query, where it is fixed",
      any("no statement" in e["message"] for e in compiled(sql="")["errors"]),
      compiled(sql="")["errors"])
# A Query with nothing wired in is an unfinished node rather than a broken
# one, so the compiler switches it off and says so instead of failing the
# whole canvas -- the same treatment every half-built node gets. What matters
# here is that no function is built from it: a variable polling a statement
# with no tables would fail on every tick, for ever.
dry = compiled(wire_table=False)
check("...a Query with no tables wired in is switched off, and says why",
      any("nothing is wired into this Query" in m["message"]
          for m in dry["warnings"] + dry["errors"]), dry["warnings"])
check("...and no poller is built from it",
      not any(f["type"] == "query_value" for f in dry["functions"]))
check("an unnamed variable is refused -- it has no key to store under",
      any("choose which variable" in e["message"]
          for e in compiled(var="")["errors"]))

# The document boundary re-checks the statement rather than trusting the
# compile that wrote it: this doc is written by one route and read by another.
def doc(**over):
    return dict({"id": "v1", "name": "v", "type": "query_value", "enabled": True,
                 "params": dict({"sql": SQL, "tables": [TABLES["counting"]],
                                 "pick": "first", "interval_s": 60,
                                 "url": "http://127.0.0.1:5110/api/workflow/query-value?fn=v1"},
                                **over)}, )


def refused(**over):
    try:
        fns.validate_function(doc(**over))
        return ""
    except (ValueError, TypeError) as exc:
        return str(exc)


check("a written document is accepted", not refused())
check("...a statement that is not a SELECT is refused",
      "only SELECT" in refused(sql="delete from counting"),
      refused(sql="delete from counting"))
check("...two statements are refused",
      "one statement" in refused(sql="select 1; select 2"))
check("...a url pointing off this box is refused",
      "this box only" in refused(url="http://192.168.1.9/api/workflow/query-value"),
      refused(url="http://192.168.1.9/api/workflow/query-value"))
check("...and so is one with no tables",
      "needs the tables" in refused(tables=[]))
check("the answer's shape is not the operator's to choose",
      fns.validate_function(doc())["params"]["json_path"] == "value")

# -- 5. rows to a number --------------------------------------------------------
# Reduction is pure and worth testing without a database.
rows = [{"shift": "A", "seen": 3}, {"shift": "B", "seen": 5},
        {"shift": "C", "seen": None}]


class _Fake:
    def __init__(self, res):
        self.res = res

    def __call__(self, *a, **k):
        return self.res


real_run = sqlconsole.run
sqlconsole.run = _Fake({"ok": True, "columns": ["shift", "seen"], "rows": rows})
try:
    got = {p: sqlconsole.scalar("s", SQL, {}, column="seen", pick=p)["value"]
           for p in ("first", "last", "sum", "avg", "min", "max", "count")}
    check("first/last/sum/min/max read the column",
          (got["first"], got["last"], got["sum"], got["min"], got["max"])
          == (3.0, 5.0, 8.0, 3.0, 5.0), got)
    check("...average is over the rows that HAVE a number",
          got["avg"] == 4.0, got["avg"])
    check("...and count is the rows, which is why it needs no column",
          got["count"] == 3.0, got["count"])
    # Positional, because a hand-written statement has no schema: the author
    # names the columns whatever they like.
    check("a blank column takes the first one holding a number",
          sqlconsole.scalar("s", SQL, {})["value"] == 3.0)
    named = sqlconsole.scalar("s", SQL, {}, column="nope")
    check("a column that is not in the result says so, and names what is",
          not named["ok"] and "no column named" in named["error"]
          and "shift" in named["error"], named)

    # "No rows matched" and "the answer is zero" are different facts. A
    # variable reporting 0 during an outage is how a dashboard says all-clear
    # when nothing is being measured at all.
    sqlconsole.run = _Fake({"ok": True, "columns": ["seen"], "rows": []})
    empty = sqlconsole.scalar("s", SQL, {})
    check("an empty result is not zero -- it is no reading",
          not empty["ok"] and empty["value"] is None, empty)
    check("...except for count, where no rows really is zero",
          sqlconsole.scalar("s", SQL, {}, pick="count")["value"] == 0.0)
    sqlconsole.run = _Fake({"ok": False, "error": "relation does not exist"})
    failed = sqlconsole.scalar("s", SQL, {})
    check("a failed query reports Postgres' own words",
          not failed["ok"] and "relation does not exist" in failed["error"])
finally:
    sqlconsole.run = real_run

# -- 6. the route the app calls -------------------------------------------------
# Just this route: the file grew a neighbour, and reading to end-of-file made
# the neighbour's code answer for this one.
route = read("core", "routes", "workflow.py").split(
    '@router.get("/api/workflow/query-value"')[1].split("\n@router.")[0]
# net.local_caller: loopback AND not relayed by the hub's /s/ proxy.
check("the reading is served to this box only",
      "if not net.local_caller(request):" in route)
# The address is not the whole boundary. What makes it safe is that the
# caller supplies a function id and nothing else -- no SQL, no table list.
check("...and the caller may name a function, never a statement",
      'f.get("type") == "query_value"' in route and "body" not in route)
check("...only one a compile actually wrote",
      'raise HTTPException(status_code=404, detail="no such query variable")'
      in route)
# The poller's contract: a failed read keeps the previous value and drops ok
# to 0. An HTTP error would be indistinguishable from a master restart.
check("a failed query answers 200 with ok=false, not an error",
      'return {"ok": False, "value": None,' in route)
# The app is a local process with no session, so the route has to sit outside
# the session wall -- which means the wall's list and the route's own address
# check are ONE mechanism and must be read together.
auth = read("core", "routes", "auth.py")
check("the wall lets the local poller through...",
      '"/api/workflow/query-value"' in auth.split("PROTECTED_PREFIXES")[0])
check("...and only that path, not everything under /api/workflow",
      '"/api/workflow"' not in auth.split("PROTECTED_PREFIXES")[0],
      "an open prefix of /api/workflow would unauthenticate the whole editor")

# -- 7. the app end -------------------------------------------------------------
app = read("systems", "demo", "app", "functions.py")
check("the app knows the type", '"api_poll", "query_value",' in app)
check("...and reads it through the SAME poller as an API value",
      'elif fn.type in ("api_poll", "query_value"):' in app,
      "a second poller is a second staleness contract to keep in step")
check("...refusing any address but this box",
      "a query variable \nreads from this box only" in app.replace('"\n', " \n")
      or "reads from this box only" in app)
check("...at its own slower floor",
      "MIN_QUERY_POLL_S = 10.0" in app)

# ...and the app's validator is RUN, not just read. Source checks cannot see a
# NameError, and this module is loaded by the detection process at startup: a
# missing import here does not fail a poll, it stops the app booting at all --
# which is exactly what happened the first time this shipped.
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import functions as app_fns                          # noqa: E402

live = app_fns.Function({"id": "v1", "name": "v", "type": "query_value",
                         "enabled": True,
                         "params": {"sql": SQL, "tables": [TABLES["counting"]],
                                    "pick": "first", "interval_s": 60,
                                    "json_path": "value",
                                    "url": "http://127.0.0.1:5110/api/workflow/"
                                           "query-value?fn=v1"}})
check("the APP accepts a compiled query variable", live is not None)
check("...clamping the interval to its own floor",
      live.params["interval_s"] >= app_fns.MIN_QUERY_POLL_S,
      live.params.get("interval_s"))
try:
    app_fns.Function({"id": "v2", "name": "v", "type": "query_value",
                      "enabled": True,
                      "params": {"url": "http://192.168.1.9/api/workflow/query-value",
                                 "interval_s": 60}})
    off_box = ""
except ValueError as exc:
    off_box = str(exc)
check("...and refusing one aimed off this box", "this box only" in off_box,
      off_box or "accepted a url pointing at another machine")

master = read("master_server.py")
check("the master publishes its port, which is the only place it is known",
      'os.environ["CYTRON_MASTER_PORT"] = str(args.port)' in master)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
