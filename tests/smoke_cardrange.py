# A card's ONE Date range reaches the same window it always did (owner,
# 2026-10-13: Period, Count over and a second Period "make it into one").
#
# The panel change stores nothing new: a card asking recorded rows reads
# `range`, a card showing a live rule's value reads `window`, and the one
# control writes the field its source reads. Pinned through the real compile
# and render:
#
#   1. a column-fed tile: today / this week / this month / follow the picker /
#      all time / a quick pick / picked dates reach the query as before, the
#      Analytics date picker still drives "follow"; a window left on it does
#      nothing (as before)
#   2. a live rule's tile: today / week / month / follow read the rule's
#      counter, live reads the value; a range left on it does nothing
#   3. a compiled live card carries its window for the Builders, stored only
#      when it is not the live value
#   4. a Builder edit of the window is stamped against a stale canvas tab
import copy
import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.repos import datasets as datasets_repo                  # noqa: E402
from core.services import builder as builder_service             # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CALLS = []


def fake_aggregate(slug, dataset, columns, agg="count", field=None, group_by=None,
                   bucket=None, filters=None, since=None, until=None, limit=500,
                   config_dir=None, field_window=None):
    CALLS.append({"since": since, "until": until, "agg": agg, "field": field})
    return {"buckets": [{"key": None, "value": 4.0}]}


datasets_repo.aggregate = fake_aggregate
TABLES = {"cam1": {"key": "cam1", "name": "PCB-SENSATA-CAM1", "columns": [
    {"name": "object_id", "type": "text"}]}}
ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def node(nid, ntype, data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def compile_card(data, wire="column"):
    nodes = [node("s", "source.detection", {}),
             node("fr", "analytics.frame", {"fnId": "f", "name": "F", "mode": "realtime"}),
             node("zc", "analytics.zone_count", {"fnId": "bay", "name": "Bay", "zone": "zone_a"}),
             node("t1", "output.dataset", {"dataset": "cam1", "name": "PCB-SENSATA-CAM1",
                                           "mode": "change", "sources": {}}),
             node("c1", "dashboard.card", {"name": "Total Violation CAM 1", "kind": "tile",
                                           **data})]
    edges = [{"id": "e1", "source": "s", "target": "fr"},
             {"id": "e2", "source": "fr", "target": "t1", "targetHandle": "c_object_id"},
             {"id": "e3", "source": "s", "target": "zc"}]
    edges.append({"id": "w", "source": "t1", "sourceHandle": "o_object_id", "target": "c1"}
                 if wire == "column" else {"id": "w", "source": "zc", "target": "c1"})
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES, tables=TABLES)
    assert not c["errors"], c["errors"]
    return builder_service.validate([next(x for x in c["widgets"] if x["id"] == "wf_c1")])[0]


def compile_rule_card(data):
    """Detection -> class filter -> In Frame Total (per-day tallies) -> Card,
    the chain tests/smoke_cardwindow.py pins for "Count over"."""
    nodes = [node("m1", "source.detection", {}),
             node("f1", "filter.class", {"classes": ["person"]}),
             node("c1", "analytics.frame_total", {"fnId": "counter", "name": "Counter",
                                                  "limit": 0, "mode": "distinct"}),
             node("card", "dashboard.card", {"name": "Card", "kind": "tile", **data})]
    edges = [{"id": "e1", "source": "m1", "target": "f1"},
             {"id": "e2", "source": "f1", "target": "c1"},
             {"id": "e3", "source": "c1", "target": "card", "targetHandle": "in"}]
    c = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES,
                         ["person"], ["person"])
    assert not c["errors"], c["errors"]
    return builder_service.validate([next(x for x in c["widgets"]
                                          if x["id"] == "wf_card")])[0]


def render(w, **page):
    CALLS.clear()
    return builder_service.compute([w], [], {}, slug="smoke", **page)[0]["data"]


# -- 1. recorded rows ---------------------------------------------------------------
now = datetime.datetime.now().astimezone()
midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
for rng, since_days in (("today", 0), ("week", 6), ("month", 29)):
    w = compile_card({"agg": "unique", "range": rng, "window": "today"})
    render(w)
    since = datetime.datetime.fromisoformat(CALLS[0]["since"])
    check(f"column tile, '{rng}': the query starts at local midnight {since_days} days back "
          "(a window left on the card does nothing)",
          w["query"].get("range") == rng and abs((midnight - datetime.timedelta(days=since_days)
                                                  - since).total_seconds()) < 2
          and CALLS[0]["agg"] == "unique" and CALLS[0]["field"] == "object_id",
          (w["query"], CALLS))
w = compile_card({"agg": "unique", "range": "page", "window": "week"})
render(w, page_range="custom", page_start="2026-10-01", page_end="2026-10-03")
check("'follow the Analytics date picker': the page's pick is the window",
      CALLS[0]["since"].startswith("2026-10-01") and CALLS[0]["until"].startswith("2026-10-04"),
      CALLS)
w = compile_card({"agg": "unique", "range": "", "window": "month"})
render(w)
check("all time: no window at all", CALLS[0]["since"] is None and CALLS[0]["until"] is None, CALLS)
w = compile_card({"agg": "unique", "range": "24h"})
render(w)
since = datetime.datetime.fromisoformat(CALLS[0]["since"])
check("a quick pick (Last 24 hours) filters the query",
      abs((now - since).total_seconds() - 86400) < 60, CALLS)
w = compile_card({"agg": "unique", "range": "custom", "range_from": "2026-09-01",
                  "range_to": "2026-09-02"})
render(w)
check("picked dates reach the query, the end day included",
      CALLS[0]["since"].startswith("2026-09-01") and CALLS[0]["until"].startswith("2026-09-03"),
      (w["query"], CALLS))

# -- 2. a live rule's value --------------------------------------------------------------
live_key = compile_rule_card({"window": "live"})["key"]
for window, metric in (("today", "today"), ("week", "week"), ("month", "month"),
                       ("follow", "total")):
    w = compile_rule_card({"window": window, "range": "week"})
    check(f"live tile, Date range '{window}': reads the rule's {metric} (a range left "
          "on it does nothing)", w["key"] == f"fn_counter_{metric}", w["key"])
check("...and 'Live value (now)' the value itself, whatever range is left on it",
      compile_rule_card({"window": "live", "range": "month"})["key"] == live_key
      and not live_key.endswith(("_today", "_week", "_month")), live_key)
# -- 3. the Builders see it ---------------------------------------------------------------------
w = compile_rule_card({"window": "today"})
live = compile_rule_card({"window": "live"})
check("a compiled live card carries its window for the Builders (not when live)",
      w.get("window") == "today" and "window" not in live, (w.get("window"), live.get("window")))

# -- 4. card-style and a stale tab ---------------------------------------------------------------
store = {"workflows": [{"graph": {"nodes": [{"id": "c1", "type": "dashboard.card",
                                              "data": {"kind": "tile", "window": "live"}}]}}]}
wf.apply_card_style(store, [{"id": "wf_c1", "window": "week"}])
nd = store["workflows"][0]["graph"]["nodes"][0]["data"]
stale = {"nodes": [{"id": "c1", "type": "dashboard.card", "data": {"kind": "tile",
                                                                  "window": "live"}}]}
wf.keep_builder_edits(stale, {"nodes": [{"id": "c1", "type": "dashboard.card",
                                         "data": copy.deepcopy(nd)}]})
check("a Builder's window reaches the node, and a canvas tab opened before cannot undo it",
      nd["window"] == "week" and stale["nodes"][0]["data"]["window"] == "week", (nd, stale))
wf.apply_card_style(store, [{"id": "wf_c1", "window": "yesterday"}])
check("...junk is not stored", nd["window"] == "week")

print("all good" if not bad else f"{bad} check(s) failed")
sys.exit(1 if bad else 0)
