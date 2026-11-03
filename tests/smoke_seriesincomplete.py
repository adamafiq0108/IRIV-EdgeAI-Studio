# A dataset chart drawn from a SERIES list, and a row still being typed.
#
# Two bugs, one shape. validate() demanded a widget-level dataset+columns from
# every source="dataset" widget, but a multi-series chart carries its table per
# ROW -- so editing any dataset series raised "a dataset widget needs a dataset
# and its columns" and the preview came back 400. And a 400 from the preview
# route blanks the WHOLE page: every other card on it disappears while the
# operator is halfway through picking a table for one row.
#
# So: a series list exempts the widget-level requirement, an unfinished row
# previews as an empty named series with a warning, and save/publish still
# refuse to store the unfinished row.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder, chart_series                # noqa: E402

COLS = [{"name": "weight", "type": "number"},
        {"name": "lane", "type": "text"}]


def widget(series, **extra):
    w = {"id": "w4", "title": "Weights", "kind": "line", "source": "dataset",
         "series": series}
    w.update(extra)
    return w


# 1. a dataset-series widget with NO widget-level dataset passes validation
full = {"id": "s1", "source": "dataset", "dataset": "scale_log",
        "columns": COLS, "field": "weight", "label": "lane A",
        "query": {"agg": "avg"}}
out = builder.validate([widget([full])])[0]
assert out["dataset"] == "" and out["columns"] == [], out
assert [s["dataset"] for s in out["series"]] == ["scale_log"], out["series"]
print("== a dataset chart drawn from series rows validates with no widget-level table")

# ...and it still passes on the strict (save/publish) path, which is the one
# that writes the file.
assert builder.validate([widget([full])], strict=True)[0]["series"]
print("== the same widget saves strictly")

# 2. an INCOMPLETE row (table not picked yet) previews instead of raising
half = {"id": "s2", "source": "dataset", "dataset": "", "label": "lane B"}
prev = builder.validate([widget([full, half])], strict=False)[0]
assert len(prev["series"]) == 2, prev["series"]
assert prev["series"][1].get("incomplete"), prev["series"][1]
print("== an unfinished row survives preview validation as an incomplete marker")

# ...and resolving it gives an empty series plus a {id, label, message} warning,
# with the FINISHED row still drawn beside it.
data = chart_series.resolve({"kind": "line", "range": "24h", "source": "dataset"},
                            prev["series"], [], {}, None, "demo", None) or {}
ids = [d["id"] for d in data.get("datasets") or []]
assert ids == ["s1", "s2"], ids
assert all(v is None for v in data["datasets"][1]["values"]), data["datasets"][1]
warn = {w["id"]: w for w in data.get("warnings") or []}
assert "s2" in warn, data.get("warnings")
assert warn["s2"]["label"] == "lane B" and warn["s2"]["message"], warn["s2"]
print("== it draws empty and names itself in warnings; the other row is untouched")

# 3. save/publish still REFUSE the unfinished row
for strict_call in (lambda: builder.validate([widget([full, half])]),
                    lambda: builder.validate([widget([half])], strict=True)):
    try:
        strict_call()
    except ValueError as e:
        assert "series" in str(e), e
    else:
        raise AssertionError("strict validation accepted an unfinished row")
# a row naming a table that is not a table name at all is refused the same way
bad = {"id": "s3", "source": "dataset", "dataset": "no such table!",
       "columns": COLS}
try:
    builder.validate([widget([bad])])
except ValueError:
    pass
else:
    raise AssertionError("strict validation accepted an unknown table")
print("== save/publish still refuse an unfinished row and an unknown table")

# 4. the widget-level requirement is untouched for a widget with NO series
try:
    builder.validate([widget([])])
except ValueError as e:
    assert "needs a dataset" in str(e), e
else:
    raise AssertionError("a dataset widget with no series and no table passed")
# and a plain single-source dataset widget still validates as it always did
one = builder.validate([widget([], dataset="scale_log", columns=COLS,
                               query={"agg": "count"})])[0]
assert one["dataset"] == "scale_log" and one["query"]["agg"] == "count", one
print("== single-source dataset widgets are unchanged")

# 5. the sql check gets the same exemption and cannot be tripped by a series
try:
    builder.validate([{"id": "q1", "kind": "line", "source": "sql"}])
except ValueError as e:
    assert "SELECT" in str(e), e
else:
    raise AssertionError("a sql widget with no statement passed")
# a sql widget carrying series is drawn by chart_series (its rows read as stat
# keys), so the missing statement is not what renders it
sq = builder.validate([{"id": "q2", "kind": "line", "source": "sql",
                        "series": [{"id": "a", "key": "fn_x_count"}]}])[0]
assert sq["series"][0]["source"] == "stat" and sq["sql"] == "", sq
print("== a statement widget still needs a statement unless it draws series")

# 6. a row that reads THE CARD's table saves -- the table is on the card.
#
# The editor's first row on a dataset card names no source of its own ("Reads
# this card's data table (set below)") and the card keeps its table editor,
# so dataset, columns and the question live on the widget. The resolver read
# the row alone and called it unfinished, so a table column dragged out of the
# palette as a chart previewed but every save of that page answered 400
# "series 1: pick the data table this column comes from" -- with the table set,
# one control further down. Reported as "the workflow did not sync to the
# builder", which is what a card that cannot be saved looks like.
LANES = [{"name": "count", "type": "number"}, {"name": "lane", "type": "text"}]
blind = {"id": "s1", "label": "", "color": "", "source": "", "agg": "last"}
card = widget([blind], dataset="people_crossings", columns=LANES,
              query={"agg": "count", "field": "count", "group_by": "lane"})
row = builder.validate([card], strict=True)[0]["series"][0]
assert row["source"] == "dataset" and row["dataset"] == "people_crossings", row
assert [c["name"] for c in row["columns"]] == ["count", "lane"], row
assert row["field"] == "count" and row["query"]["group_by"] == "lane", row
# the fold follows the card's question, not the blind default the editor
# stamps on a fresh row: count rows per bucket are summed, never "last"
assert row["query"]["agg"] == "count" and row["agg"] == "sum", row
# ...and the card keeps its own table for the editor that still shows it
saved = builder.validate([card], strict=True)[0]
assert saved["dataset"] == "people_crossings" and len(saved["columns"]) == 2, saved
print("== a row reading the card's table saves with the card's table on it")

# the preview path reads the same entry, so the chart draws the table rather
# than an empty, named series
prev_row = builder.validate([card], strict=False)[0]["series"][0]
assert prev_row == row and not prev_row.get("incomplete"), prev_row
# a latest-value question folds as last
latest = builder.validate([widget([blind], dataset="people_crossings",
                                  columns=LANES,
                                  query={"agg": "latest", "field": "count"})])[0]
assert latest["series"][0]["agg"] == "last", latest["series"]
print("== preview and save read the inherited row the same way")

# a row that names its OWN table keeps it -- the card's does not overwrite it
own = {"id": "s1", "source": "", "dataset": "scale_log", "columns": COLS,
       "field": "weight", "agg": "avg", "query": {"agg": "avg"}}
kept = builder.validate([widget([own], dataset="people_crossings",
                                columns=LANES, query={"agg": "count"})])[0]
assert kept["series"][0]["dataset"] == "scale_log", kept["series"]
assert kept["series"][0]["field"] == "weight" and kept["series"][0]["agg"] == "avg"
# a row that says "dataset" itself and names no table is the operator's own
# unfinished row, and strict still refuses it -- the card is not consulted
try:
    builder.validate([widget([half], dataset="people_crossings", columns=LANES,
                             query={"agg": "count"})], strict=True)
except ValueError as e:
    assert "series 1" in str(e), e
else:
    raise AssertionError("an explicitly unfinished dataset row was saved")
# no card at all (the pure tests, an old caller): the row is taken as it is
assert chart_series.migrate([blind], "dataset")[0].get("incomplete")
assert chart_series.migrate([blind], "dataset", widget=None) == \
    chart_series.migrate([blind], "dataset")
# a stat card's key reaches a row that carries none, the way the editor copies
# it for a row it makes itself
stat_row = builder.validate([{"id": "t1", "kind": "bar", "source": "stat",
                              "key": "fn_a_count",
                              "series": [{"id": "s1", "source": ""}]}])[0]
assert stat_row["series"][0]["key"] == "fn_a_count", stat_row["series"]
print("== a row's own table wins, an unfinished row is still refused, no card is no change")

print("OK smoke_seriesincomplete")
