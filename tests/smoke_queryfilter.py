# An Event Card keeps the filter it was wired with.
#
# builder._clean_query validated `field` and `group_by` against the event
# repo's whitelist and then filtered `filters` through a SECOND, hand-kept
# copy of that list -- and the copy had drifted. `function` was missing from
# it, which is exactly the column a wired Event Card scopes itself by, so the
# filter was dropped on save and the card counted every matching event on the
# board. A card reading "violations from the Zone A rule" showing the whole
# site's total does not look broken; it is confidently wrong, which is worse.
#
# The fix is not "add function to the list", it is "stop keeping a list": the
# repo is the safety boundary, so the allow-list is ASKED FOR. The check below
# pins that, so a column added to the repo can never again be unreachable from
# a card -- and the multi-source series entries, which share this cleaner,
# inherit the same guarantee.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import events as events_repo                     # noqa: E402
from core.services import builder as bs                          # noqa: E402
from core.services import chart_series as cs                     # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- the filter a wired card carries survives ---------------------------------
q = bs._clean_query({"agg": "count",
                     "filters": {"function": "fn_zone_a", "zone": "Zone A"}})
check("a card scoped to one rule keeps that scope",
      q["filters"].get("function") == "fn_zone_a", q)
check("...alongside the filters that always worked",
      q["filters"].get("zone") == "Zone A", q)

# -- and the allow-list is the repo's, not a copy of it -----------------------
# Every filterable column must be reachable from a card. Asserted as a SUPERSET
# rather than by naming columns: a column added to the repo tomorrow has to be
# usable without anybody remembering this file exists.
every = {col: f"v_{col}" for col in events_repo.FILTERABLE}
kept = bs._clean_query({"agg": "count", "filters": every})["filters"]
check("every filterable column reaches the query spec",
      set(kept) == set(events_repo.FILTERABLE),
      sorted(set(events_repo.FILTERABLE) - set(kept)))
check("...and nothing outside the whitelist does",
      bs._clean_query({"filters": {"drop table": "x", "ts": "x",
                                   "made_up": "x"}})["filters"] == {},
      bs._clean_query({"filters": {"drop table": "x"}}))
check("a blank filter is not a filter",
      bs._clean_query({"filters": {"zone": "  "}})["filters"] == {})
check("a filter value is clamped, not trusted",
      bs._clean_query({"filters": {"zone": "z" * 200}})["filters"]["zone"]
      == "z" * 60)
# The stored order is the repo's business, not the browser's: a layout must
# not churn because a form serialised its fields differently.
check("the stored spec is stable whatever order it arrives in",
      list(bs._clean_query({"filters": {"zone": "a", "function": "b"}})["filters"])
      == list(bs._clean_query({"filters": {"function": "b", "zone": "a"}})["filters"]))

# -- a query SERIES inherits all of it ----------------------------------------
# The series entry runs through the same cleaner, so the hole was in both the
# single-source card and every multi-source chart drawn from one.
entry = cs.migrate([{"source": "query", "label": "Zone A rule",
                     "query": {"agg": "count",
                               "filters": {"function": "fn_zone_a"}}}],
                   "history")[0]
check("a query series keeps the rule it was scoped to",
      entry["query"]["filters"] == {"function": "fn_zone_a"}, entry)

# -- and the card that is saved keeps it too ----------------------------------
saved = bs.validate([{"id": "q1", "title": "Zone A violations", "kind": "tile",
                      "source": "query",
                      "query": {"agg": "count", "range": "today",
                                "filters": {"function": "fn_zone_a"}}}])
check("a saved Event Card still counts one rule, not the board",
      saved[0]["query"]["filters"] == {"function": "fn_zone_a"}, saved[0]["query"])

print()
print("event card filters: " + ("OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
