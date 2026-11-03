# The two counters, asked the same question the same way -- and the PID they
# both know but never used to hand on.
#
# Frame Counter and Zone Counter both count each object ONCE by tracking id.
# They answered the same "over what span" question with different controls (one
# had a picker, the other did not), and neither could say WHICH object it had
# counted, so a table of counted objects had to get the id from somewhere else.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"log": {"key": "log", "name": "Log", "columns": [
    {"name": "who", "type": "text"}, {"name": "counted", "type": "number"}]}}


def graph(counter, data=None, wires=()):
    """Detection -> (Zone) -> counter -> Alert, and into a Data Table."""
    zone = counter == "analytics.violations"
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "c", "type": counter, "position": {"x": 2, "y": 0},
         "data": {"fnId": "cnt", "name": "Counter", **(data or {})}},
        {"id": "al", "type": "output.alert", "position": {"x": 3, "y": 0},
         "data": {"metric": "new", "op": ">", "value": 0}},
    ]
    # The Data Table only appears when something is wired into it: a table
    # with no column attached is a legitimate compile error, and not the one
    # any of these cases is about.
    if wires:
        nodes.append({"id": "db", "type": "output.dataset",
                      "position": {"x": 4, "y": 0},
                      "data": {"dataset": "log", "name": "Log",
                               "mode": "alert", "sources": {}}})
    edges = [{"id": "e2", "source": "s", "target": "c"},
             {"id": "e3", "source": "c", "target": "al"}]
    if zone:
        nodes.insert(1, {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
                         "data": {"zone": "zone_a"}})
        edges[0] = {"id": "e1", "source": "s", "target": "z"}
        edges.insert(1, {"id": "e2b", "source": "z", "target": "c"})
    for column, port in wires:
        edges.append({"id": f"w{column}", "source": "c", "target": "db",
                      "targetHandle": f"c_{column}", "sourceHandle": port})
    return {"nodes": nodes, "edges": edges}


def compile_(g):
    return wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)


REG = wf.registry_public()

# 1. RENAMED, and named for what it is. "Frame Functionality" said which part
#    of the product it belonged to, not what it did.
assert REG["analytics.frame"]["label"] == "Object Counter", REG["analytics.frame"]["label"]
assert REG["analytics.violations"]["label"] == "Zone Counter"
print("== the two counters are called Object Counter and Zone Counter")

# 2. THE COUNTER DECIDES WHAT TO MEASURE, NOT WHAT TO SHOW -- and what a NEW
#    node offers is the realtime count alone. Distinct-by-PID counting has
#    its supported paths now (Entry/Exit, a Data Table row per object), so
#    the distinct modes are HIDDEN from the picker but still compiled: every
#    graph saved with one keeps counting exactly what it counted.
frame = REG["analytics.frame"]["modes"]
assert set(frame) == {"realtime", "distinct"}, sorted(frame)
assert frame["realtime"]["fn_type"] == "frame_count"
assert frame["distinct"]["fn_type"] == "frame_unique"
assert frame["distinct"].get("hidden") and not frame["realtime"].get("hidden")
print("== the Object Counter still compiles both; only realtime is offered")

# The Zone Counter has the SAME shape, in the SAME order -- realtime first,
# realtime the default -- so the two counters read as one node over a
# different surface. (A pre-mode Zone Counter is a distinct counter;
# migrate_merged_nodes stamps that onto the node, so the realtime fallback
# can never re-decide what an old graph counts.)
zone = REG["analytics.violations"]["modes"]
assert list(zone) == ["realtime", "distinct"], list(zone)
assert zone["distinct"]["fn_type"] == "zone_unique"
assert zone["realtime"]["fn_type"] == "zone_count"
assert zone["distinct"].get("hidden") and not zone["realtime"].get("hidden")
assert REG["analytics.violations"]["data"]["mode"] == "realtime"
assert REG["analytics.occupancy"].get("deprecated"), \
    "Zone Functionality is still offered beside the mode that replaced it"
print("== the Zone Counter mirrors the Object Counter; Zone Functionality is folded in")

# 3. THE PERIOD IS ON THE CARD, and on every card. The rule tallies today,
#    this week, this month and the running total whichever way it is set, so
#    choosing between them is a display decision -- and putting it on the
#    counter meant a second counter to see the same count over a different
#    week.
for card in ("dashboard.card", "dashboard.graph"):
    assert "window" in REG[card]["data"], f"{card} has no Period setting"
for counter in ("analytics.frame", "analytics.violations"):
    assert "window" not in REG[counter]["data"], f"{counter} still carries a period"
print("== every card carries the Period; neither counter does")

# and the rule publishes all four regardless of how the node is set
for counter, mode in (("analytics.frame", "distinct"),
                      ("analytics.violations", "distinct")):
    node = {"type": counter, "data": {"fnId": "cnt", "mode": mode}}
    published = wf.functions_service.OUTPUTS[
        wf.node_fn_type(node, REG[counter])]
    for period in ("today", "week", "month", "total"):
        assert period in published, (counter, period, published)
print("== both rules tally today, this week, this month and the total")

# 4. THE PID. Both counters count once per object, so both know which object.
#    Wiring that port to a column records the id -- and it is an id, not a
#    stat: there is no per-system number for "which person", so it comes off
#    the event that triggered the row, which is where the app already has it.
for counter in ("analytics.frame", "analytics.violations"):
    c = compile_(graph(counter, {"pid": True, "mode": "distinct"},
                       wires=[("who", "pid")]))
    assert not c["errors"], (counter, c["errors"])
    col = next(x for x in c["outputs"][0]["columns"] if x["name"] == "who")
    assert col["src"] == "event" and col["field"] == "track_id", col
    print(f"== {counter:22s} PID port records", col["field"])

# ...and the ordinary output still carries the NUMBER, from the same node
c = compile_(graph("analytics.frame", {"pid": True, "mode": "distinct"},
                   wires=[("who", "pid"), ("counted", "out")]))
assert not c["errors"], c["errors"]
cols = {x["name"]: x for x in c["outputs"][0]["columns"]}
assert cols["who"]["src"] == "event", cols["who"]
assert cols["counted"]["src"] == "wire", cols["counted"]
assert cols["counted"]["key"] == "fn_cnt_total", cols["counted"]
print("== one node, two outputs: the count and who was counted:",
      cols["counted"]["key"], "+", cols["who"]["field"])

# 5. THE PALETTE OFFERS THE PERIOD COUNTS. It did not, which is how a card
#    ends up titled "(today)" and bound to `total`: with no `today` to pick,
#    the key was typed by hand and the label was the only thing saying which
#    period was meant.
c = compile_(graph("analytics.frame", {"mode": "distinct"}))
offered = {e["key"] for e in c["catalog"]}
for period in ("today", "week", "month"):
    assert f"fn_cnt_{period}" in offered, (period, sorted(offered))
print("== the palette offers the per-period counts:", sorted(offered))

# 6. THE +1 ON A NEW PID. A counter that counts each object once publishes the
#    running total AND `new` -- +1 the instant a new object is counted, 0
#    otherwise. Both are wireable by name now; before, a wire always meant the
#    first metric and `new` was reachable only through a card's metric picker,
#    so "add a row when a new person appears" had nowhere to come from.
for counter in ("analytics.frame", "analytics.violations"):
    c = compile_(graph(counter, {"pid": True, "mode": "distinct"},
                       wires=[("counted", "new")]))
    assert not c["errors"], (counter, c["errors"])
    col = next(x for x in c["outputs"][0]["columns"] if x["name"] == "counted")
    assert col["key"] == "fn_cnt_new", (counter, col)
    print(f"== {counter:22s} `new` port carries", col["key"])

# the default port still means the running total, so an existing wire is
# untouched by the new ports appearing beside it
c = compile_(graph("analytics.frame", {"mode": "distinct"},
                   wires=[("counted", "out")]))
col = next(x for x in c["outputs"][0]["columns"] if x["name"] == "counted")
assert col["key"] == "fn_cnt_total", col
print("== the default port still means the running total:", col["key"])

# and all three at once: how many, the +1, and which object
c = compile_(graph("analytics.frame", {"pid": True, "mode": "distinct"},
                   wires=[("counted", "new"), ("who", "pid")]))
cols = {x["name"]: x for x in c["outputs"][0]["columns"]}
assert cols["counted"]["key"] == "fn_cnt_new", cols["counted"]
assert cols["who"]["field"] == "track_id", cols["who"]
print("== one counter, three answers: total, +1 on new, and which object")
print("ALL OK")
