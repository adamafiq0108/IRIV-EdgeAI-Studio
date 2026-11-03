# A dashboard card can be told WHAT PERIOD to count over: live, follow the
# Analytics range, or a fixed today / week / month.
#
# A period is not a different number, it is a different metric -- the counting
# rules publish today/week/month beside total, and Analytics narrows `total`
# to its own date range. So the window setting just picks the key the card
# binds to, and the card stays an ordinary widget.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402
from core.services import analytics as an                   # noqa: E402

ZONES = [{"id": "z1", "name": "Zone 1", "kind": "area", "enabled": True,
          "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}]


def graph(window, counter="analytics.violations"):
    """Detection -> Class filter -> Zone -> counter -> Counter Card."""
    nodes = [
        {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        # mode distinct explicitly: this test is about the per-period metrics
        # (today/week/month), which only the distinct rule publishes. On a
        # real load migrate_merged_nodes stamps this; compile_graph is called
        # directly here.
        {"id": "c1", "type": counter, "position": {"x": 3, "y": 0},
         "data": {"fnId": "counter", "name": "Counter", "limit": 0,
                  "mode": "distinct"}},
        {"id": "card", "type": "dashboard.counter", "position": {"x": 4, "y": 0},
         "data": {"name": "Card", "window": window}},
    ]
    edges = [{"id": "e1", "source": "m1", "target": "f1"},
             {"id": "e3", "source": "c1", "target": "card", "targetHandle": "in"}]
    if counter != "analytics.frame_total":       # zone rules need a Zone node
        nodes.insert(3, {"id": "z1n", "type": "zone.ref",
                         "position": {"x": 2, "y": 0}, "data": {"zone": "z1"}})
        edges += [{"id": "e2a", "source": "f1", "target": "z1n"},
                  {"id": "e2b", "source": "z1n", "target": "c1"}]
    else:
        edges.append({"id": "e2", "source": "f1", "target": "c1"})
    return {"nodes": nodes, "edges": edges}


def key_for(window, counter="analytics.violations"):
    out = wf.compile_graph(graph(window, counter), ZONES, ["person"], ["person"])
    assert not out["errors"], out["errors"]
    card = next(w for w in out["widgets"] if w["id"].startswith("wf_card"))
    return card["key"], out["warnings"]


# 1. live is the default and unchanged behaviour
assert key_for("live")[0] == "fn_counter_total", key_for("live")[0]
assert key_for("")[0] == "fn_counter_total"
print("== 'live' keeps the metric the card always used")

# 2. fixed periods bind to the per-day metrics
for window in ("today", "week", "month"):
    key, _ = key_for(window)
    assert key == f"fn_counter_{window}", (window, key)
print("== today / week / month bind to the rule's per-day tallies")

# 3. 'follow' binds to the total, which is what Analytics narrows by range
key, _ = key_for("follow")
assert key == "fn_counter_total", key
# Dates derived from the clock, never written down: "today" means today, so a
# hardcoded day makes a test that passes until it silently does not.
import datetime                                              # noqa: E402
_today = datetime.date.today()
stats = {"fn_counter_by_day": {(_today - datetime.timedelta(days=5)).isoformat(): 5,
                               _today.isoformat(): 3},
         "fn_counter_total": 999}
assert an.scope_stats(stats, "today")["fn_counter_total"] == 3
assert an.scope_stats(stats, "week")["fn_counter_total"] == 8
print("== 'follow' reads the total, and Analytics cuts it to the chosen range")

# 4. the whole-frame counter answers periods too
assert key_for("month", "analytics.frame_total")[0] == "fn_counter_month"
print("== the In Frame Total counter answers periods the same way")

# 5. a rule that does NOT count per day says so, and still renders something
key, warns = key_for("today", "analytics.occupancy")
assert key == "fn_counter_count", key           # falls back to the live metric
assert any("cannot answer" in w["message"] for w in warns), warns
print("== a rule with no per-day tally warns and falls back to its live value:")
print("  ", next(w["message"] for w in warns if "cannot answer" in w["message"])[:96])

# 6. the option is offered on the cards that can use it
reg = wf.registry_public()
assert "window" in reg["dashboard.counter"]["data"], reg["dashboard.counter"]["data"]
assert "window" in reg["dashboard.gauge"]["data"]
assert wf.CARD_WINDOWS == ("live", "follow", "today", "week", "month")
print("== the choice is offered on Counter and Gauge cards:", wf.CARD_WINDOWS)
print("ALL OK")
