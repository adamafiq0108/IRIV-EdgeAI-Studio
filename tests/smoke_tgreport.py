"""A command's answer in Telegram reads like a person wrote it (Tok Aba, 2026-10-14).

It used to be a monospace ASCII table for every result ("date  total_violations
/ 2026-10-14  44"). Now (telegram_commands.report):

  * a bold headline with one plain emoji, the period in italics
    ("Today, Wed 14 Oct 2026"), a footer in italics ("pcb_sensata · Today")
  * one row: a "Label: <b>value</b>" line per column
  * up to 12 rows of up to 4 columns: one line per row, numbers in bold and
    named, a total when exactly one column is a number
  * wider or longer: the monospace table, cut to fit and saying so
  * nothing: "No records for this period." in italics
  * every value escaped; numbers with thousands separators, dates friendly,
    long text cut
  * Telegram refusing the HTML: sent once more as plain text
  * the period buttons and the chart caption are unchanged in shape

    py -3 tests/smoke_tgreport.py
"""
import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import telegram_commands as tg             # noqa: E402
from core.services import telegram_net as net                 # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail!r}"))
    if not ok:
        bad += 1


E = tg.HEADLINE_EMOJI
DAY = datetime.datetime(2026, 10, 14)
NEXT = DAY + datetime.timedelta(days=1)

# -- the image-1 case ----------------------------------------------------------------
got = tg.report(["date", "total_violations"], [{"date": "2026-10-14", "total_violations": 44}],
                title="This is summary for violation occur",
                period=tg.period_line("Today", DAY, NEXT), footer="pcb_sensata · Today")
check("one row: headline, period, one line per column, footer",
      got == (f"{E} <b>This is summary for violation occur</b>\n"
              "<i>Today, Wed 14 Oct 2026</i>\n"
              "\n"
              "Date: <b>Wed 14 Oct 2026</b>\n"
              "Total violations: <b>44</b>\n"
              "\n"
              "<i>pcb_sensata · Today</i>"), got)

# -- a few rows ---------------------------------------------------------------------
got = tg.report(["day", "violations"],
                [{"day": "2026-10-13", "violations": 40},
                 {"day": "2026-10-14", "violations": 1234}],
                title="Weekly", period="This week, 12 Oct – 14 Oct 2026", footer="pcb · This week")
check("a few rows: one line each, the number bold and named, and a total",
      got == (f"{E} <b>Weekly</b>\n<i>This week, 12 Oct – 14 Oct 2026</i>\n\n"
              "Tue 13 Oct 2026 · <b>40</b> violations\n"
              "Wed 14 Oct 2026 · <b>1,234</b> violations\n\n"
              "Total: <b>1,274</b>\n\n<i>pcb · This week</i>"), got)
got = tg.report(["zone", "ok", "ng", "note"],
                [{"zone": "A", "ok": 3, "ng": 1, "note": "fine"},
                 {"zone": "B", "ok": 2, "ng": 0, "note": "-"}])
check("...two numbers are both named, text stays plain, no total",
      got == "A · <b>3</b> ok · <b>1</b> ng · fine\nB · <b>2</b> ok · <b>0</b> ng · -", got)
got = tg.report(["zone", "a", "b", "c"], [{"zone": "A", "a": 1, "b": 2, "c": 3},
                                         {"zone": "B", "a": 4, "b": 5, "c": 6}])
check("...three numbers are bold, not named", got == "A · <b>1</b> · <b>2</b> · <b>3</b>\n"
      "B · <b>4</b> · <b>5</b> · <b>6</b>", got)

# -- wide and long fall back to the table -----------------------------------------------
wide = tg.report(["a", "b", "c", "d", "e"], [{"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}],
                 title="Wide", footer="f")
check("more than 4 columns: the monospace table", "<pre>" in wide
      and wide.startswith(f"{E} <b>Wide</b>") and wide.endswith("<i>f</i>"), wide)
long_ = tg.report(["n"], [{"n": i} for i in range(13)], title="Long")
check("more than 12 rows: the table", "<pre>" in long_ and "Total" not in long_, long_)
huge = tg.report(["zone", "n"], [{"zone": f"zone {i}", "n": i} for i in range(500)], title="Huge")
check("...cut to fit a message, and says how much is shown",
      len(huge) <= 4096 and "Showing the first 30 of 500 rows." in huge, huge[-120:])

# -- empty -----------------------------------------------------------------------------
check("nothing: an italic sentence",
      tg.report(["a"], [], title="T", footer="f")
      == f"{E} <b>T</b>\n\n<i>No records for this period.</i>\n\n<i>f</i>")

# -- escaping ----------------------------------------------------------------------------
got = tg.report(["name", "n"], [{"name": "<b>x</b>&", "n": 1}], title="a<b>&", footer="<i>")
check("every value, title and footer is escaped; no tag comes from data",
      "&lt;b&gt;x&lt;/b&gt;&amp;" in got and "<b>a&lt;b&gt;&amp;</b>" in got
      and "<i>&lt;i&gt;</i>" in got and got.count("<b>") == got.count("</b>")
      and got.count("<i>") == got.count("</i>"), got)
got = tg.report(["the_col"], [{"the_col": "x" * 300}])
check("a long text cell is cut with ...", "x" * 77 + "..." in got and "x" * 78 not in got)

# -- numbers, dates, labels --------------------------------------------------------------------
check("numbers: thousands, trimmed decimals, booleans, nothing",
      [tg.nice_value(v) for v in (1234567, 2.5, 2.50001, 3.0, True, None, -1200)]
      == ["1,234,567", "2.5", "2.5", "3", "yes", "-", "-1,200"],
      [tg.nice_value(v) for v in (1234567, 2.5, 2.50001, 3.0, True, None, -1200)])
check("dates: a day, a moment, a date object, not a date",
      [tg.nice_value(v) for v in ("2026-10-05", "2026-10-14T06:26:43.123",
                                  datetime.date(2026, 10, 14), "2026-13-99")]
      == ["Mon 5 Oct 2026", "Wed 14 Oct 2026, 06:26:43", "Wed 14 Oct 2026", "2026-13-99"])
check("labels: total_violations -> Total violations",
      tg.nice_label("total_violations") == "Total violations" and tg.nice_label("") == "Value")
check("the period: a day by its button word and date; a range by its dates",
      tg.period_line("Today", DAY, NEXT) == "Today, Wed 14 Oct 2026"
      and tg.period_line("Weekly", DAY - datetime.timedelta(days=2), NEXT).startswith("Weekly, 12 Oct")
      and tg.period_line("All time", None, None) == "All time")

# -- Telegram refusing the HTML: once more as plain text -----------------------------------
import urllib.error                                              # noqa: E402
import io                                                        # noqa: E402
calls = []


def fake_api(token, method, payload, timeout=20):
    calls.append(payload)
    if payload.get("parse_mode") == "HTML":
        raise urllib.error.HTTPError("u", 400, "Bad Request: can't parse entities", {},
                                     io.BytesIO(b"{}"))
    return True


net._api = fake_api
ok = net.send("1:x", 42, "<b>Total</b>: &lt;5&gt; &amp; more")
check("a refused HTML message is sent again as plain text, once",
      ok and len(calls) == 2 and "parse_mode" not in calls[1]
      and calls[1]["text"] == "Total: <5> & more", calls)

# -- wiring --------------------------------------------------------------------------------
src = open(os.path.join(ROOT, "core", "services", "telegram_net.py"), encoding="utf-8").read()
rep = src.split("def _report(")[1].split("\ndef _album(")[0]
check("a button's answer uses report(), with the period line and the system footer",
      "tg.report(columns, rows, title=headline, period=when, footer=footer)" in rep
      and "tg.period_line(button[\"label\"], start, stop)" in rep
      and 'footer = f"{system_name or slug} · {button[\'label\']}"' in rep)
check("...the period buttons ride on it exactly as before",
      "tg.keyboard(cmd))" in rep)
check("...and a chart's caption carries the same headline, period and footer",
      "caption=(f\"{tg.HEADLINE_EMOJI} <b>{tg._esc(headline)}</b>" in rep)
master = open(os.path.join(ROOT, "master_server.py"), encoding="utf-8").read()
check("a watch's {table} is the same natural text",
      "table_text = tg.report(" in master)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
