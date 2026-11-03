# What a Violation action on the Zones page builds -- ONE chain, ending in the
# card that shows the verdict.
#
# It used to build two. The zone's own Trigger tick compiled a live presence
# rule with an alert, and a Violation action compiled a SECOND, distinct-count
# rule with a second alert -- two chains on the canvas and two alerts per
# incident for one statement ("anything in here is a violation"), leaving the
# operator to work out which of the pair to hang a card on.
#
# The action also compiled to the wrong measurement. "It is a violation when
# the number of objects in the zone is at least 1" is about NOW, but the rule
# alerted on `new` -- true for a single frame per offender -- so a Verdict card
# blinked VIOLATION and returned to COMPLIANT while the person stood there.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc               # noqa: E402
from core.services import functions as fns                     # noqa: E402
from core.services import workflow as wf                       # noqa: E402

SLUG = "smokeviol"
SYSTEMS = tempfile.mkdtemp()
os.makedirs(os.path.join(SYSTEMS, SLUG), exist_ok=True)

ZONE = {"id": "zone_a", "name": "Zone 1", "kind": "area", "enabled": True,
        "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]],
        "labels": ["person"], "violation": True,
        "actions": [{"id": "a1", "type": "violation", "limit": 1, "op": ">=",
                     "alert": {"on": True, "severity": "crit",
                               "message": "somebody in Zone 1"}}]}


def compile_zones(zone):
    dc.set_zones(SYSTEMS, SLUG, [zone])
    wf.save_store(SLUG, {"version": 3, "workflows": [{
        "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
        "author": "", "created": 0, "modified": 0,
        "graph": {"version": wf.SCHEMA_VERSION, "nodes": [], "edges": []}}]})
    fns.apply_zone_actions(SYSTEMS, SLUG)
    return (fns.get_functions(SYSTEMS, SLUG),
            wf.load_store(SLUG)["workflows"][0]["graph"])


# -- the tick and the action are ONE rule -------------------------------------
docs, graph = compile_zones(ZONE)
zone_rules = [d for d in docs if d["id"].startswith("z_")]
assert len(zone_rules) == 1, [d["id"] for d in zone_rules]
rule = zone_rules[0]
assert rule["id"].endswith("_viol"), rule["id"]
print("== the tick and the action compile to one rule:", rule["id"])

# ...measured LIVE, with the operator's own comparison as its trigger
assert rule["type"] == "zone_count", rule
assert rule["trigger"]["metric"] == "count", rule["trigger"]
assert rule["trigger"]["op"] == ">=" and rule["trigger"]["value"] == 1, rule["trigger"]
assert rule["trigger"]["severity"] == "crit", rule["trigger"]
assert rule["trigger"]["message"] == "somebody in Zone 1", rule["trigger"]
print("== it measures the live count and alerts on the operator's condition:",
      f"count {rule['trigger']['op']} {rule['trigger']['value']}")

# A live condition HOLDS while the person stands there, so the repeat has to be
# paced: at the old 2 seconds one intruder wrote an event every two seconds for
# as long as they stayed. Entry alerts immediately -- nothing has fired yet, so
# there is no cooldown to serve -- and everything after it, held or flickering,
# is paced by this number (app/functions.py _check_triggers; see
# tests/smoke_cooldown.py).
assert rule["trigger"]["cooldown_s"] >= 30, rule["trigger"]
print("== and it repeats every", rule["trigger"]["cooldown_s"],
      "seconds, not every two")

# -- the canvas draws the whole chain, ending in a Verdict card ----------------
types = sorted({n["type"] for n in graph["nodes"]})
assert types == ["analytics.counter", "dashboard.card", "filter.class",
                 "output.alert", "source.detection", "zone.ref"], types
print("== the canvas chain:", " -> ".join(
    ["source.detection", "filter.class", "zone.ref", "analytics.counter",
     "output.alert", "dashboard.card"]))

card = next(n for n in graph["nodes"] if n["type"] == "dashboard.card")
assert card["data"]["kind"] == "verdict", card["data"]
assert card["data"]["metric"] == "active", card["data"]
assert card["data"]["name"] == "Zone 1", card["data"]
assert card["data"]["ok_label"] == "COMPLIANT", card["data"]
assert card["data"]["alert_label"] == "VIOLATION", card["data"]
print("== and it ends in a verdict card named after the zone:",
      card["data"]["ok_label"], "/", card["data"]["alert_label"])

# the card reads the ALERT, which is what makes it a verdict rather than a
# number: alert -> card is the chain an operator would have drawn by hand
alert = next(n for n in graph["nodes"] if n["type"] == "output.alert")
assert any(e["source"] == alert["id"] and e["target"] == card["id"]
           for e in graph["edges"]), graph["edges"]
print("== wired from the Alert, not from the counter")

# the class filter comes from the zone's "objects to watch"
filt = next(n for n in graph["nodes"] if n["type"] == "filter.class")
assert filt["data"]["classes"] == ["person"], filt["data"]
print("== the zone's watched classes become the Class Filter:",
      filt["data"]["classes"])

# -- the tick alone still works, and still alerts ------------------------------
docs, graph = compile_zones({**ZONE, "actions": []})
rules = [d for d in docs if d["id"].startswith("z_")]
assert len(rules) == 1 and rules[0]["id"].endswith("_zoneviol"), rules
assert rules[0]["trigger"]["metric"] == "count", rules[0]
assert any(n["type"] == "dashboard.card" for n in graph["nodes"])
print("== the tick on its own is unchanged, and gets a card too")

# -- an action with its alert box UNTICKED, on a ticked zone, still alerts -----
# Otherwise ticking the zone and then describing it with an action would switch
# the alarm off -- the opposite of what either control says.
quiet = {**ZONE, "actions": [{"id": "a1", "type": "violation", "limit": 2,
                              "op": ">", "alert": {"on": False}}]}
docs, _ = compile_zones(quiet)
rule = next(d for d in docs if d["id"].startswith("z_"))
assert rule.get("trigger"), "the zone's tick lost its alarm to a quiet action"
assert rule["trigger"]["value"] == 2 and rule["trigger"]["op"] == ">", rule["trigger"]
print("== a ticked zone keeps its alarm, on the action's numbers")

# -- untick the zone: the action stands alone and obeys its own alert box ------
docs, _ = compile_zones({**ZONE, "violation": False,
                         "actions": [{"id": "a1", "type": "violation",
                                      "limit": 3, "op": ">",
                                      "alert": {"on": False}}]})
rule = next(d for d in docs if d["id"].startswith("z_"))
assert not rule.get("trigger"), rule
print("== with the tick off, an action that does not alert simply measures")

# -- an Alert re-wired to a different rule follows the wire --------------------
# Its data keeps the OLD metric ("current", from a Time in Zone) after being
# re-wired to a Zone Counter, which publishes no such value. That used to be a
# hard error ("trigger.metric must be one of count") that stranded the canvas;
# the wire is the truth, so it compiles on the counter's primary metric with a
# warning naming the swap.
g = {"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "c", "type": "analytics.violations", "position": {"x": 2, "y": 0},
     "data": {"fnId": "zc", "name": "Zone Counter", "mode": "realtime"}},
    {"id": "al", "type": "output.alert", "position": {"x": 3, "y": 0},
     "data": {"metric": "current", "op": ">=", "value": 1, "severity": "warn"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "c"},
    {"id": "e3", "source": "c", "target": "al"},
]}
from core.services import workflow as wfs                      # noqa: E402
out = wfs.compile_graph(wfs.clean_graph(g),
                        [{"id": "zone_a", "name": "Bay", "kind": "area",
                          "enabled": True}])
assert not out["errors"], out["errors"]
fn = next(f for f in out["functions"] if f["id"] == "zc")
trigs = fn.get("triggers") or []
assert trigs and trigs[0]["metric"] == "count", trigs
assert any("'current'" in w["message"] for w in out["warnings"]), out["warnings"]
print("== a re-wired Alert follows the wire:", trigs[0]["metric"])
print("ALL OK")
