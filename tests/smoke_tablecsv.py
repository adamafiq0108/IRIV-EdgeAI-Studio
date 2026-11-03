# A table card's own "Download CSV" (Tok Aba, 2026-10-14):
# services/table_export.py and POST /api/builder/table-csv.
#
#   1. the range: named and custom, parsed strictly, end after start, at most
#      366 days, all time unbounded by date
#   2. the columns: only the card's own, in its order; unknown, SQL-looking,
#      empty or non-list refused
#   3. the card: found by id on this system's layout; only a table that reads
#      rows (Data Table, event log, Query); anything else refused
#   4. the rows: the range goes INTO the store's query (since / until), read
#      in pages, capped at ROW_CAP with the cap said; an empty range is a
#      header-only file
#   5. each cell: formula-looking text is defused, numbers stay numbers
#   6. the file name: letters, digits, - and _ only
#   7. the route: session wall only (like the page read), every choice
#      checked by the service (FastAPI when installed, else off the source)
#
#   py -3 tests/smoke_tablecsv.py
import csv
import datetime
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                  # noqa: E402
from core.repos import events as events_repo                      # noqa: E402
from core.services import table_export as tx                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return False
    except tx.ExportError:
        return True


TODAY = datetime.date(2026, 10, 14)

# -- 1. the range ------------------------------------------------------------------------
s, u, f, t = tx.window({"range": "7d"}, TODAY)
check("Last 7 days is the 8th to the 14th, the end day included",
      s.startswith("2026-10-08T00:00:00") and u.startswith("2026-10-15T00:00:00")
      and (f, t) == ("2026-10-08", "2026-10-14"), (s, u))
s, u, f, t = tx.window({"range": "today"}, TODAY)
check("Today is one day", s.startswith("2026-10-14") and u.startswith("2026-10-15"))
s, u, f, t = tx.window({"from": "2026-01-01", "to": "2026-01-31"}, TODAY)
check("a custom range is its two days, inclusive", s.startswith("2026-01-01") and u.startswith("2026-02-01"))
check("All time has no date bound", tx.window({"range": "all"}, TODAY)[:2] == (None, None))
for bad_spec in ({"from": "2026-1-1", "to": "2026-01-31"}, {"from": "2026-02-30", "to": "2026-03-01"},
                 {"from": "2026-01-31", "to": "2026-01-01"}, {"from": "2025-01-01", "to": "2026-01-02"},
                 {"range": "forever"}, {"from": "2026-01-01; DROP TABLE x", "to": "2026-01-02"}, {}):
    check(f"refused: {bad_spec}", refused(tx.window, bad_spec, TODAY))

# -- 2. the columns ------------------------------------------------------------------------
AV = [("Datetime", "ts"), ("status", "status"), ("total panel", "total_panel")]
check("the chosen columns come back in the card's order",
      tx.pick_columns(AV, ["total panel", "Datetime"]) == [("Datetime", "ts"), ("total panel", "total_panel")])
for wanted in (["nope"], ["status; DROP TABLE dataset_rows"], ["total_panel"], [], "When", [1], None):
    check(f"refused columns: {wanted!r}", refused(tx.pick_columns, AV, wanted))
# The time column was "When" until 2026-10-14; an old request still finds it.
check("alias: an old request's \"When\" is the Datetime column",
      tx.pick_columns(AV, ["When", "status"]) == [("Datetime", "ts"), ("status", "status")])
check("...asked both ways, it comes back once",
      tx.pick_columns(AV, ["When", "Datetime"]) == [("Datetime", "ts")])
check("...but a Query's own column named When stays its own",
      tx.pick_columns([("When", "when"), ("x", "x")], ["When"]) == [("When", "when")])
check("...and a card with no time column still refuses it",
      refused(tx.pick_columns, [("x", "x")], ["When"]))

# -- 3. the card ------------------------------------------------------------------------------
layout = {"pages": [{"id": "overview", "widgets": [
    {"id": "t1", "title": "Rejects / line 2", "kind": "table", "source": "dataset",
     "dataset": "rejects", "columns": [{"name": "status", "type": "text"},
                                       {"name": "total_panel", "type": "number"}],
     "query": {"filters": {"status": "NG"}}},
    {"id": "e1", "title": "Events", "kind": "table", "source": "events"},
    {"id": "c1", "title": "Chart", "kind": "line", "source": "history", "key": "k"},
]}]}
check("a card is found by id on any page", tx.find_card(layout, "e1")["kind"] == "table")
check("...an unknown id is nobody", tx.find_card(layout, "x9") is None and tx.find_card(layout, "") is None)
check("a chart is not a table to download", refused(tx.columns_of, tx.find_card(layout, "c1")))
check("an event-log table offers its five columns",
      [label for label, _ in tx.columns_of(tx.find_card(layout, "e1"))]
      == ["Datetime", "Type", "Severity", "Event", "Device"])
check("a Data Table offers Datetime and its declared columns, as the card shows them",
      tx.columns_of(tx.find_card(layout, "t1")) == AV)

# -- 4. the rows ---------------------------------------------------------------------------------
CALLS = []


def fake_ds_query(slug, dataset, columns, filters=None, since=None, until=None, limit=200,
                  offset=0, config_dir=None, **_):
    CALLS.append({"dataset": dataset, "filters": filters, "since": since, "until": until,
                  "limit": limit, "offset": offset})
    total = 2500
    n = max(0, min(limit, total - offset))
    return {"rows": [{"ts": f"2026-10-14T10:{i % 60:02d}:00", "status": "=HYPERLINK(\"x\")",
                      "total_panel": -5} for i in range(n)]}


datasets_repo.query = fake_ds_query
made = tx.export(tx.find_card(layout, "t1"), {"range": "7d"}, ["Datetime", "status", "total panel"],
                 "smoke", today=TODAY)
check("the range and the card's filter go INTO the store's query",
      CALLS[0]["since"].startswith("2026-10-08") and CALLS[0]["until"].startswith("2026-10-15")
      and CALLS[0]["filters"] == {"status": "NG"} and CALLS[0]["dataset"] == "rejects")
check("...read in pages of 1000 until the rows run out",
      [c["offset"] for c in CALLS] == [0, 1000, 2000] and made["rows"] == 2500 and not made["truncated"])
rows = list(csv.reader(io.StringIO(made["text"])))
check("the header is the chosen columns", rows[0] == ["Datetime", "status", "total panel"])
# The card's header and the dialog's list are the same words as the file's:
# builder draws the card, table_export names the columns, both say Datetime.
from core.services import builder as builder_service              # noqa: E402
CALLS_BEFORE = len(CALLS)
drawn = builder_service._dataset_data(tx.find_card(layout, "t1"), None, "smoke")
check("the Data Table card's header is the columns the CSV offers",
      drawn["columns"] == [label for label, _ in tx.columns_of(tx.find_card(layout, "t1"))]
      and drawn["columns"][0] == "Datetime", drawn["columns"])
del CALLS[CALLS_BEFORE:]
drawn = builder_service._data(tx.find_card(layout, "e1"), [], {})
check("the event-log card's header is the columns the CSV offers",
      drawn["columns"] == [label for label, _ in tx.columns_of(tx.find_card(layout, "e1"))],
      drawn.get("columns"))
check("a formula-looking cell is defused, a negative number stays a number",
      rows[1][1].startswith("'=HYPERLINK") and rows[1][2] == "-5", rows[1])
CALLS.clear()
tx_cap = tx.ROW_CAP
tx.ROW_CAP = 1500
made = tx.export(tx.find_card(layout, "t1"), {"range": "all"}, ["status"], "smoke", today=TODAY)
check("past the cap the file stops and says so",
      made["rows"] == 1500 and made["truncated"] and CALLS[0]["since"] is None, (made["rows"], made["truncated"]))
tx.ROW_CAP = tx_cap
events_repo.query = lambda slug, **k: {"rows": []}
made = tx.export(tx.find_card(layout, "e1"), {"range": "today"}, ["Datetime", "Event"], "smoke", today=TODAY)
check("an empty range is a header-only file", made["text"] == "Datetime,Event\n" and made["rows"] == 0)
made = tx.export(tx.find_card(layout, "e1"), {"range": "today"}, ["When", "Event"], "smoke", today=TODAY)
check("an old request sending When gets a file headed Datetime", made["text"] == "Datetime,Event\n",
      made["text"])

# -- 5. cells ------------------------------------------------------------------------------------
check("cells: = + - @ tab CR text defused; numbers and numeric text kept",
      [tx.safe_cell(v) for v in ("=1+1", "+cmd", "-x", "@SUM", "\tx", "-3.5", 7, None, "ok")]
      == ["'=1+1", "'+cmd", "'-x", "'@SUM", "'\tx", "-3.5", 7, "", "ok"])

# -- 6. the file name ---------------------------------------------------------------------------
check("the file name keeps letters, digits, - and _ only",
      tx.filename({"title": "../Rejects / line 2 <x>"}, "2026-10-08", "2026-10-14")
      == "Rejects_line_2_x_2026-10-08_2026-10-14.csv")
check("...and never comes out empty", tx.filename({"title": "///"}, "all", "time") == "table_all_time.csv")

# -- 7. the route -------------------------------------------------------------------------------
src = open(os.path.join(ROOT, "core", "routes", "builder.py"), encoding="utf-8").read()
tx_src = open(os.path.join(ROOT, "core", "services", "table_export.py"), encoding="utf-8").read()
route = src.split("def table_csv(")[1].split("@router")[0]
check("route: the card comes from this system's saved layout, never from the body",
      "find_card(builder_service.load(st.config_dir, slug)" in route and "status_code=404" in route)
check("route: the service decides every choice; a refusal is a 400 in words",
      "table_export.export," in route and "to_thread" in route
      and "except table_export.ExportError" in route)
check("route: an attachment, no-store, nosniff, the row count and the cap said",
      "response_headers(made)" in route
      and 'attachment; filename=' in tx_src and '"Cache-Control": "no-store"' in tx_src
      and '"X-Truncated"' in tx_src and "nosniff" in tx_src)
ds_src = open(os.path.join(ROOT, "core", "routes", "datasets.py"), encoding="utf-8").read()
dsr = ds_src.split("def dataset_csv(")[1].split("@router")[0]
check("dataset route: the card is built from the table's own definition, never from the body",
      "_definition(request, key)" in dsr and '"columns": found["columns"]' in dsr
      and 'body.get("dataset")' not in dsr)
check("dataset route: the same service, in a thread, with the same headers",
      "table_export.export," in dsr and "to_thread" in dsr and "response_headers(made)" in dsr
      and "except table_export.ExportError" in dsr)
check("the ceiling is a safety limit now, not the old 10,000", tx.ROW_CAP >= 100000, tx.ROW_CAP)
try:
    import fastapi  # noqa: F401
    print("note: fastapi is installed -- extend this with a TestClient pass")
except ImportError:
    print("skip: fastapi is not installed here -- the session wall and the system grant "
          "(routes/auth.require) were not exercised; the route was checked off the source")

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
