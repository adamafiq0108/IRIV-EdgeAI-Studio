""""How many times it changed" -- the count a live-state table is for.

A Data Table on "on change" logs whatever is in frame now, so the pid column
taking a new value IS a new object arriving. Neither neighbouring aggregation
answers that: count() scores a row that said the same thing again, and
unique() scores somebody who left and came back only once.

Pins the semantics shared by the SQL window query (datasets_repo.aggregate)
and the in-memory fallback (builder._agg_value), which must agree -- the same
card falls back to the ring when Postgres is down, and a card that changes its
answer depending on which backend served it is worse than one that fails.

    python tests/smoke_changes_agg.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core.services import builder as bs                 # noqa: E402
from core.repos import datasets as ds                   # noqa: E402

bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}")
    if not ok:
        print(f"      wanted {want!r}")
        bad += 1


def changes(values):
    return bs._agg_value([{"pid": v} for v in values], "changes", "pid")


# -- the semantics -------------------------------------------------------------
check("nothing recorded is nothing counted", changes([]), 0)
check("one object is one", changes([3, 3, 3]), 1)
check("three objects in turn", changes([3, 4, 5]), 3)
check("standing still does not count twice", changes([3, 3, 4, 4, 4, 5]), 3)

# The flicker case this exists to survive: the tracker had not confirmed a
# frame, so the pid is null between two rows of the SAME object. Counting
# 3 -> null -> 3 as two arrivals would turn detector noise into traffic.
check("a null between two of the same is not a change", changes([3, None, 3]), 1)
check("nulls only is nothing", changes([None, None]), 0)

# Someone who leaves and comes back IS counted again -- this counts visits,
# which is what a footfall number means. unique() is the other question.
check("a return visit counts again", changes([3, 4, 3]), 3)
check("...where unique() would say two",
      bs._agg_value([{"pid": v} for v in [3, 4, 3]], "unique", "pid"), 2)
check("...and count() would say three",
      bs._agg_value([{"pid": v} for v in [3, 4, 3]], "count", None), 3)

# Reading the rows newest-first must give the same answer as oldest-first: the
# ring and Postgres do not agree on order, and the number must not depend on it.
check("order does not change the answer",
      changes([3, 3, 4, 5, 5]), changes(list(reversed([3, 3, 4, 5, 5]))))

# -- it survives validation ----------------------------------------------------
check("changes is an offered aggregation", "changes" in bs.AGGREGATIONS, True)
w = bs.validate([{"id": "w1", "kind": "tile", "source": "dataset",
                  "dataset": "counting",
                  "columns": [{"name": "pid", "type": "number"}],
                  "query": {"agg": "changes", "field": "pid"}}])
check("a card may ask for it", w[0]["query"]["agg"], "changes")

# -- the SQL side agrees on shape ----------------------------------------------
# No Postgres here, so this pins the guard rather than the number: "changes"
# needs a column to look back along, and answers nothing without one.
check("changes is known to the repo", "changes" in ds._AGGREGATES, True)
check("it is a window aggregation", "changes" in ds._WINDOW_AGGS, True)
cols = [{"name": "pid", "type": "number"}]
check("no column named, no answer",
      ds.aggregate("s", "counting", cols, agg="changes", field=None),
      {"buckets": []})
check("a column that is not declared is refused",
      ds.aggregate("s", "counting", cols, agg="changes", field="nope"),
      {"buckets": []})

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
