"""The Query node: a custom statement over the tables wired into it.

A Data Table says what is RECORDED; a Query says what is ASKED of it, and the
answer is drawn by whatever card the result is wired to. This pins the parts
that are easy to get wrong:

  * the tables a statement may read come from the WIRES, not from the text --
    so a query naming a table nobody wired in has no view to read and is
    refused at compile rather than as a database error later;
  * only SELECT compiles;
  * a result is shaped POSITIONALLY per card kind, because a hand-written
    query has no schema to introspect.

The isolation itself is not tested here -- it belongs to the read-only role
and the per-system views, which smoke_sqlconsole.py covers.

    python tests/smoke_querynode.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as bs                       # noqa: E402
from core.services import builder as builder_service
from core.services import workflow as wf                      # noqa: E402

TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
              {"name": "pid", "type": "text"},
              {"name": "live_counting", "type": "number"}]},
          "shifts": {"key": "shifts", "name": "Shifts", "columns": [
              {"name": "pid", "type": "text"},
              {"name": "shift", "type": "text"}]}}

SQL = "select shift, count(*) as seen from counting join shifts using (pid) group by shift"

bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}")
    if not ok:
        print(f"      wanted {want!r}")
        bad += 1


def graph(sql=SQL, kind="bar", wire_tables=("counting", "shifts")):
    # One node per card FAMILY: dashboard.card takes the single-value shapes,
    # dashboard.chart the many-value ones.
    # dashboard.graph is the live chart node ("Chart"); dashboard.chart is the
    # deprecated one-shape-per-node predecessor and always draws a line.
    card_type = "dashboard.card" if kind in wf.CARD_KINDS else "dashboard.graph"
    nodes = [{"id": "q", "type": "storage.query", "position": {"x": 3, "y": 0},
              "data": {"name": "By shift", "sql": sql}},
             {"id": "card", "type": card_type, "position": {"x": 5, "y": 0},
              "data": {"kind": kind, "name": "By shift"}}]
    edges = [{"id": "e_out", "source": "q", "target": "card"}]
    for i, key in enumerate(wire_tables):
        nodes.append({"id": f"t{i}", "type": "output.dataset",
                      "position": {"x": 0, "y": i},
                      "data": {"dataset": key, "name": key, "mode": "change",
                               "sources": {"pid": {"src": "event",
                                                   "field": "track_id"}}}})
        edges.append({"id": f"e{i}", "source": f"t{i}", "target": "q",
                      "targetHandle": "table"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


# -- compile -------------------------------------------------------------------
c = wf.compile_graph(graph(), [], tables=TABLES)
check("a wired query compiles", c["errors"], [])
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
check("the card reads the statement", w["source"], "sql")
check("...verbatim", w["sql"], SQL)
check("...and carries the tables it may read",
      sorted(t["key"] for t in w["tables"]), ["counting", "shifts"])
check("the card still decides how it is drawn", w["kind"], "bar")

# The tables come from the WIRES. A statement naming a table nobody wired in
# has no view to read, which surfaces as an unreadable database error unless
# it is caught here.
c = wf.compile_graph(graph(wire_tables=("counting",)), [], tables=TABLES)
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
check("only the wired tables travel", [t["key"] for t in w["tables"]],
      ["counting"])

c = wf.compile_graph(graph(wire_tables=()), [], tables=TABLES)
# A Query with nothing wired in is a DRAFT: the complaint is kept word for
# word, delivered as the warning that dims the node instead of the error that
# reddens it (see smoke_switch.py). It becomes an error again the moment
# anything IS wired to it.
check("a query with nothing wired in is switched off, not refused",
      c["errors"], [])
check("...saying the same thing",
      any("wired into this Query" in w["message"] and "switched off" in w["message"]
          for w in c["warnings"]), True)

# Only SELECT. The role would refuse a write anyway; saying so at compile is
# the difference between "a Query may only SELECT" and a Postgres error.
for statement in ("delete from counting", "update counting set pid = '1'",
                  "insert into counting values (1)", "drop table counting"):
    c = wf.compile_graph(graph(sql=statement), [], tables=TABLES)
    check(f"{statement.split()[0]} does not compile", len(c["errors"]), 1)

c = wf.compile_graph(graph(sql="   "), [], tables=TABLES)
check("an empty statement is refused", len(c["errors"]), 1)

# -- a Query must be named -----------------------------------------------------
# The editors list what a graph publishes BY NAME. Three unnamed queries
# publish three cards called the same thing, identifiable only by dragging each
# one out to see what it was -- so the name is required, not decorative.
g = graph()
next(n for n in g["nodes"] if n["type"] == "storage.query")["data"]["name"] = ""
c = wf.compile_graph(g, [], tables=TABLES)
check("an unnamed Query is refused", len(c["errors"]), 1)
check("...by saying why", "give this Query a name" in c["errors"][0]["message"],
      True)

# Two of the same name is the same problem one step later.
g = graph()
q = next(n for n in g["nodes"] if n["type"] == "storage.query")
g["nodes"].append({**q, "id": "q2", "position": {"x": 3, "y": 4},
                   "data": {**q["data"], "name": "By  Shift"}})
# ...wired to the same tables as the first one. A node with nothing wired into
# it is a draft now (smoke_switch.py), and a draft is not compiled -- so an
# unwired copy would not clash with anything, which is correct and is not what
# this is testing.
for e in [e for e in g["edges"] if e["target"] == "q"]:
    g["edges"].append({**e, "id": e["id"] + "_q2", "target": "q2"})
c = wf.compile_graph(g, [], tables=TABLES)
check("two Queries with the same name are refused", len(c["errors"]), 1)
check("...compared loosely, so spacing and case do not hide a clash",
      "already called" in c["errors"][0]["message"], True)

# The name is what a card falls back to, which is the point of requiring it.
c = wf.compile_graph(graph(), [], tables=TABLES)
w = next(x for x in c["widgets"] if x["id"] == "wf_card")
check("a card inherits the Query's name", w["title"], "By shift")

# with ... select is a query, and a legitimate way to write a hard one.
c = wf.compile_graph(graph(sql="with x as (select 1 n) select n from x"),
                     [], tables=TABLES)
check("a CTE compiles", c["errors"], [])

# -- validate ------------------------------------------------------------------
w = bs.validate([{"id": "w1", "kind": "tile", "source": "sql", "sql": SQL,
                  "tables": [TABLES["counting"]]}])[0]
check("a query widget survives validation", w["source"], "sql")
check("...keeping its statement", w["sql"], SQL)

for bad_widget, why in (
        ({"sql": "", "tables": [TABLES["counting"]]}, "no statement"),
        ({"sql": "delete from counting", "tables": [TABLES["counting"]]},
         "not a query"),
        ({"sql": SQL, "tables": []}, "no tables")):
    try:
        bs.validate([{"id": "w1", "kind": "tile", "source": "sql", **bad_widget}])
        check(f"a widget with {why} is refused", "accepted", "rejected")
    except ValueError:
        check(f"a widget with {why} is refused", "rejected", "rejected")

# A statement longer than the cap is refused rather than truncated: half a
# query is a different query.
try:
    bs.validate([{"id": "w1", "kind": "tile", "source": "sql",
                  "sql": "select " + "x" * bs.MAX_SQL,
                  "tables": [TABLES["counting"]]}])
    check("an oversized statement is refused", "accepted", "rejected")
except ValueError:
    check("an oversized statement is refused", "rejected", "rejected")

# -- shaping -------------------------------------------------------------------
# Positional, because a hand-written query names its columns whatever the
# author likes and the only thing always true is the order they asked for.
RESULT = {"ok": True, "columns": ["shift", "seen"],
          "rows": [{"shift": "day", "seen": 12}, {"shift": "night", "seen": 5}]}


#: What the widget handed the console, captured so the SHAPE can be checked.
#: A widget carries a list of tables (a document, stable through JSON);
#: sqlconsole works in the registry's {key: table}. Getting that wrong does
#: not raise -- ensure_ready fails deep inside and every Query card reads
#: "the query console is not available", which looks like a broken database
#: rather than a mismatched argument.
passed = {}


def shaped(kind, result=RESULT):
    from core.services import sqlconsole

    def fake(slug, sql, tables, **kw):
        passed["tables"] = tables
        return result

    real, sqlconsole.run = sqlconsole.run, fake
    try:
        return bs._sql_data({"kind": kind, "sql": SQL,
                             "tables": [TABLES["counting"]]}, None, "s")
    finally:
        sqlconsole.run = real


out = shaped("bar")
check("the console is handed tables in ITS shape, keyed by name",
      passed["tables"], {"counting": TABLES["counting"]})
check("a chart takes the first column as labels", out["labels"], ["day", "night"])
check("...and the first number column as values", out["values"], [12.0, 5.0])

out = shaped("table")
check("a table card shows every column", out["columns"], ["shift", "seen"])
check("...as rows in order", out["rows"], [["day", 12], ["night", 5]])

out = shaped("tile")
check("a tile takes the first number", out["value"], 12.0)
# The column name is NOT a caption. It is the query author's word for a field,
# and putting it under the number wrote a small grey label onto every card
# built from a statement -- one the operator never typed and could not clear,
# because clearing the field handed the fallback straight back. A caption is
# what somebody wrote there, or nothing.
check("...and captioned only by what the operator wrote", out["sub"], "")

# A tile over a one-column count is the common case and must not need a label.
out = shaped("tile", {"ok": True, "columns": ["count"], "rows": [{"count": 47}]})
check("a bare count tiles", out["value"], 47.0)

# A failed query degrades to an empty widget carrying its error: one broken
# card must not take the dashboard down with it.
out = shaped("tile", {"ok": False, "error": "relation \"nope\" does not exist"})
check("a failed query reports rather than raises",
      "does not exist" in (out.get("error") or ""), True)
check("...with no value", out.get("value"), None)

out = shaped("bar", {"ok": True, "columns": [], "rows": []})
check("an empty result draws nothing", (out["labels"], out["values"]), ([], []))

# -- wiring --------------------------------------------------------------------
check("a Data Table may feed a Query",
      wf.edge_allowed("output.dataset", "storage.query")[0], True)
check("a Query may feed a card",
      wf.edge_allowed("storage.query", "dashboard.card")[0], True)
# A Query reaches a message only THROUGH a CSV node, which is what turns its
# rows into a file. Straight into an Email there is nothing to attach and no
# way to say which query a message wired to several meant.
check("a Query may not feed a message directly",
      wf.edge_allowed("storage.query", "output.email")[0], False)
check("...it goes through a CSV node, which can",
      wf.edge_allowed("storage.query", "storage.csv")[0]
      and wf.edge_allowed("storage.csv", "output.email")[0], True)
check("a Query takes no detection stream",
      wf.edge_allowed("source.detection", "storage.query")[0], False)

# -- dragged out of the palette ------------------------------------------------
# A named card joins the palette so it can be dropped on a dashboard, and the
# drop rebuilds the widget from the catalog entry. That rebuild is a FIXED
# shape, and it did not copy `sql`/`tables` -- so a Query card dropped from the
# palette landed with source "sql" and no statement, and the server refused it:
# "a query widget needs a SELECT statement and the tables it may read". The
# card was well formed right up until it was picked up.
shared = io.open(os.path.join(ROOT, "frontend", "src", "components", "widgets",
                              "builderShared.jsx"), encoding="utf-8").read()
drop = shared.split("export function catalogWidget(c) {")[1].split("\n}")[0]
check("a dropped Query card keeps its statement and its tables",
      "sql: c.sql, tables: c.tables || []" in drop, True)
check("...and keeps how it arrives, as it already kept its template",
      'anim: c.anim || ""' in drop, True)

# The catalog entry it is built FROM carries them: everything the compiled
# widget had, except the canvas copy's own id and span.
cat = wf.compile_graph(graph(), [], tables=TABLES)["catalog"]
entry = next((e for e in cat if e.get("source") == "sql"), None)
check("the palette entry for a Query carries the statement",
      bool(entry) and entry.get("sql") == SQL, True)
check("...and the tables it may read",
      sorted(t["key"] for t in (entry or {}).get("tables") or []),
      ["counting", "shifts"])

# ...and the widget a drop produces is one the server accepts, which is where
# the refusal came from: builder.validate, not the canvas.
dropped = {"id": "q1", "kind": "tile", "source": "sql", "sql": SQL,
           "tables": (entry or {}).get("tables") or []}
check("a dropped Query card validates",
      builder_service.validate([dropped])[0]["sql"], SQL)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
