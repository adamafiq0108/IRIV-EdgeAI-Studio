"""A hand-written Query can follow the date picker.

`%(from)s` / `%(to)s` existed for Telegram commands and for trying a statement
by hand on the Database console. The DASHBOARD bound nothing: _sql_data called
sqlconsole.run with no params, so a card whose statement named a period failed
outright, and the only statements that worked were the ones with the window
baked in -- CURRENT_DATE - INTERVAL '1 day' -- which is a card that can never
follow the range picked above it.

So a Query node carries a Period, it travels onto every card compiled from
that query, compute() resolves "follow the Analytics range" against the date
pick exactly as it already does for a Data Table card, and _sql_data binds the
result. Bound, never pasted: a period formatted into SQL text is the one place
this feature could grow an injection.

    python tests/smoke_sqlrange.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service            # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


DATED = ("select count(*) from t "
         "where datetime >= %(from)s and datetime < %(to)s")
PLAIN = "select count(*) from t"


def widget(sql, **q):
    return {"sql": sql, "query": q}


# -- 1. only a statement that asks gets a window -----------------------------
check("a statement that names no period binds nothing",
      builder_service._sql_window(widget(PLAIN)) is None,
      "psycopg2 reads a literal % as a format directive the moment ANY "
      "params are passed, so TO_CHAR(x, 'HH12:MI AM') would break")
check("...and one that names a period gets one",
      set(builder_service._sql_window(widget(DATED)) or {}) == {"from", "to"})

# -- 2. the window is the card's Period --------------------------------------
allt = builder_service._sql_window(widget(DATED))
check("all time still binds something, because a bound value must exist",
      allt["from"].startswith("1970-01-01"),
      "a card set to all time whose statement asks for a window must run")
today = builder_service._sql_window(widget(DATED, range="today"))
check("...today runs from a floor, not from the epoch",
      today["from"] > allt["from"] and today["from"].endswith(("00", "00:00")))
page = builder_service._sql_window(
    widget(DATED, range="page", _since="2026-09-01T00:00:00+08:00",
           _until="2026-09-30T23:59:59+08:00"))
check("...and \"follow the Analytics range\" uses the date pick",
      page["from"].startswith("2026-09-01")
      and page["to"].startswith("2026-09-30"),
      "compute() resolves page -> _since/_until before the card is drawn")

# -- 3. the period survives the shape the card is stored in ------------------
w = builder_service.validate([{
    "id": "w1", "title": "Peak hour", "kind": "tile", "source": "sql",
    "sql": DATED, "tables": [{"key": "t", "name": "t",
                "columns": [{"name": "datetime", "type": "text"}]}],
    "query": {"range": "page"},
}])[0]
check("a statement-backed card keeps its period through validate",
      (w.get("query") or {}).get("range") == "page",
      "it used to be dropped: source == 'sql' stored query as {}")
w2 = builder_service.validate([{**w, "query": {"range": "nonsense"}}])[0]
check("...and only a period it recognises",
      (w2.get("query") or {}).get("range") == "")

# -- 4. it is BOUND, not pasted ----------------------------------------------
src = read("core", "services", "builder.py")
check("the window is handed to the driver, never formatted into the SQL",
      "params=_sql_window(w)" in src
      and "% (" not in src.split("def _sql_window")[1].split("def _sql_data")[0])
console = read("core", "services", "sqlconsole.py")
check("...and the runner binds its params", "params=params" in console
      or "vars=params" in console or "cur.execute(text, params)" in console)

# -- 5. the Query node is where the period is chosen -------------------------
page_src = read("frontend", "src", "pages", "Workflow.jsx")
check("the Query node offers a Period",
      '<Field label="Period"' in page_src
      and '<option value="page">follow the Analytics range</option>' in page_src)
check("...with a button that writes the filter for you",
      "datetime >= %(from)s AND datetime < %(to)s" in page_src)
check("...and it says so when the statement ignores the setting",
      "sqlWantsDates" in page_src
      and "reads every row" in page_src,
      "a Period on a statement that never names it is a setting with no "
      "effect, and silently so")

wf = read("core", "services", "workflow.py")
check("the period travels from the query onto every card built from it",
      '"query": {"range": str(qd.get("range") or "")[:12]}' in wf,
      "the same statement drawn twice must ask about the same window")

# -- 6. one Reset on the page ------------------------------------------------
picker = read("frontend", "src", "components", "ui", "DateRangePicker.jsx")
check("the date popover has no Reset of its own",
      ">Reset</button>" not in picker,
      "the page toolbar's Reset sits a few pixels away and means something "
      "else entirely - it clears the page back to its default range")
check("...Cancel still backs out of a half-made pick", "Cancel</button>" in picker)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
