# SQL Playground: the period a statement's %(from)s / %(to)s run over is the
# operator's to pick -- all time, today, this week, this month, or two dates --
# and the editor helps write the statement: snippets by shelf (Basic /
# Advanced) that land at the caret, and completion of keywords, tables and
# columns as you type.
#
# The binding stays a BINDING (psycopg2 params), never text pasted into the
# statement: the console is admin-only, but a period typed into SQL is still
# the one place this feature could grow an injection.
import datetime
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import sqlconsole                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


NOW = datetime.datetime(2026, 9, 14, 15, 30)
SQL = "SELECT count(*) FROM t WHERE ts >= %(from)s AND ts < %(to)s"
D = datetime.datetime

check("a statement with no placeholders binds nothing",
      sqlconsole.preview_params("SELECT 1", "month", now=NOW) == (None, ""))
p, note = sqlconsole.preview_params(SQL, now=NOW)
check("nothing picked = today, midnight to midnight",
      p == {"from": D(2026, 9, 14), "to": D(2026, 9, 15)} and "today" in note, (p, note))
p, note = sqlconsole.preview_params(SQL, "week", now=NOW)
check("this week = the last 7 days ending today",
      p == {"from": D(2026, 9, 8), "to": D(2026, 9, 15)} and "7 days" in note, (p, note))
p, note = sqlconsole.preview_params(SQL, "month", now=NOW)
check("this month = the last 30 days", p["from"] == D(2026, 8, 16) and "30 days" in note, (p, note))
p, note = sqlconsole.preview_params(SQL, "all", now=NOW)
check("all time = the epoch to tomorrow", p["from"] == D(1970, 1, 1) and p["to"] == D(2026, 9, 15)
      and "all time" in note, (p, note))
p, note = sqlconsole.preview_params(SQL, "custom", "2026-09-01", "2026-09-10", now=NOW)
check("two dates: from the first at midnight to the midnight AFTER the last (inclusive end)",
      p == {"from": D(2026, 9, 1), "to": D(2026, 9, 11)} and "2026-09-01 to 2026-09-10" in note,
      (p, note))
p, _ = sqlconsole.preview_params(SQL, "custom", "2026-09-10", "2026-09-01", now=NOW)
check("...dates the wrong way round are put right", p == {"from": D(2026, 9, 1), "to": D(2026, 9, 11)}, p)
p, _ = sqlconsole.preview_params(SQL, "custom", "2026-09-05", "", now=NOW)
check("...one date is that one day", p == {"from": D(2026, 9, 5), "to": D(2026, 9, 6)}, p)
p, note = sqlconsole.preview_params(SQL, "custom", "", "", now=NOW)
check("...no dates falls back to today rather than refusing", p["from"] == D(2026, 9, 14) and "today" in note)
p, note = sqlconsole.preview_params(SQL, "custom", "yesterday-ish", None, now=NOW)
check("...garbage dates fall back to today", p["from"] == D(2026, 9, 14))
p, _ = sqlconsole.preview_params(SQL, "fortnight", now=NOW)
check("an unknown period is today", p["from"] == D(2026, 9, 14))
check("the values are datetimes, to be BOUND, never strings for the statement",
      all(isinstance(v, datetime.datetime) for v in p.values()))

route = io.open(os.path.join(ROOT, "core", "routes", "datasets.py"), encoding="utf-8").read()
check("the route hands period, from and to through to the binding",
      'period=body.get("period")' in route and 'start=body.get("from")' in route
      and 'end=body.get("to")' in route)

page = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Database.jsx"), encoding="utf-8").read()
check("the page has the Period picker with the five choices and two date inputs",
      all(f'value="{v}"' in page for v in ("all", "today", "week", "month", "custom"))
      and page.count('type="date"') == 2 and "{ sql, period, from: dateFrom, to: dateTo }" in page)
check("...snippets on two shelves, Basic and Advanced, dropped at the caret",
      "const SQL_SNIPPETS" in page and "Basic: [" in page and "Advanced: [" in page
      and "function insertAtCursor" in page and "setSelectionRange(caretAt" in page)
check("...and completion of keywords, tables and columns as you type",
      "const SQL_KEYWORDS" in page and "function suggest(" in page and "function accept(" in page
      and 'className="db-ac"' in page and '"table"' in page
      and 'e.key === "Tab" || e.key === "Enter"' in page)
check("...and the run reports which window was bound", "res.bound" in page)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
