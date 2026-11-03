# A chart over a Data Table has to be ASKABLE, and its axis has to be readable.
#
# Seen on a live box: a bar chart of a 0/1 column, summing that column and
# grouped by that same column, drawing exactly two bars labelled "0.0" and
# "1.0". Both halves of that are the product's fault:
#
#   * the Builder had no editor at all for a dataset widget -- the question was
#     fixed when the card was dragged in, so "one bar per day" could not be
#     asked for and a nonsense grouping could not be undone;
#   * and a number group key rendered as the float the database returned, so
#     an axis that should read 0 and 1 read 0.0 and 1.0.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as bs                           # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- the axis says what a person would say ---------------------------------
check("a whole number loses its float tail", bs._short_label(0.0) == "0")
check("...both of them", bs._short_label("1.0") == "1")
check("a real fraction keeps it", bs._short_label("1.5") == "1.5")
check("a negative one too", bs._short_label("-2.0") == "-2")
# A DAY bucket is a date. The midnight was never asked for and eats the axis.
check("a day bucket reads as a date",
      bs._short_label("2026-09-03T00:00:00") == "09-03")
check("...an hourly one keeps its time",
      bs._short_label("2026-09-03T14:00:00") == "09-03 14:00")
check("a plain label is left alone", bs._short_label("loading bay") == "loading bay")

# -- and the chart still draws what it is given ----------------------------
widget = bs.validate([{"id": "w1", "kind": "bar", "source": "dataset",
                       "dataset": "bay_log", "title": "Violations",
                       "columns": [{"name": "violation", "type": "number"}],
                       "query": {"agg": "sum", "field": "violation",
                                 "bucket": "day"}}])[0]
check("a bucketed dataset chart survives validation",
      widget["query"]["bucket"] == "day", str(widget["query"]))
check("...keeping the column declaration it reads by",
      [c["name"] for c in widget["columns"]] == ["violation"], str(widget))

# -- the Builder can ASK the question, not just inherit it ------------------
page = read("frontend", "src", "components", "widgets", "builderShared.jsx")
check("the Builder edits a dataset widget at all",
      'current.source === "dataset"' in page)
check("...offering every aggregation the workflow inspector does",
      all(f'"{a}"' in page for a in
          ("count", "sum", "avg", "min", "max", "latest", "unique", "changes")))
check("...including one bar per period of time",
      "each {b}" in page.replace("`", "") or "each " in page)
# The picker itself is the shared card field (cardFields.PerField), the one
# the workflow node draws too.
fields = read("frontend", "src", "components", "widgets", "cardFields.jsx")
# Since the chart time axis model (services/timeaxis.py) the X FIELD ("One
# point per <column>") and the time grouping are two controls, and the field
# wins: the step is greyed with its reason while a column is chosen.
check("...where a column on the X axis and time grouping never both apply",
      "onChange({ group_by: v })" in fields
      and "<PerField groupBy={q.group_by}" in page
      and "lockedReason={q.group_by ? X_FIELD_REASON" in page)
# The exact mistake that produced "0.0 / 1.0", named where it is made.
check("...and it warns when a chart groups by the column it works out",
      "bar per value of it" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
