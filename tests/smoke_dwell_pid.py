# Time in Zone with PID: cycle time per object, all the way to a table row.
#
# The aggregate numbers (current/live_s/avg_s/max_s) are one value per system
# and cannot say WHO -- so `pid` on the node stamps every COMPLETED stay as an
# event carrying the PID, the seconds and the entry/exit times. A Data Table
# wired to that node then writes one row per stay, with no Alert needed:
# "a row per stay" is not a threshold.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
COLUMNS = [{"name": "pid", "type": "text"},
           {"name": "cycle_s", "type": "number"},
           {"name": "entered", "type": "timestamp"}]
TABLES = {"cycle_log": {"key": "cycle_log", "name": "Cycle log",
                        "columns": COLUMNS}}


def graph(pid=True, alert=False):
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "dw", "type": "analytics.dwell", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay dwell", "pid": pid}},
        {"id": "ds", "type": "output.dataset", "position": {"x": 4, "y": 0},
         "data": {"dataset": "cycle_log", "name": "cycle_log", "mode": "alert",
                  "sources": {"pid": {"src": "event", "field": "track_id"},
                              "cycle_s": {"src": "event", "field": "duration_s"},
                              "entered": {"src": "event", "field": "entered_at"}}}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "z"},
             {"id": "e2", "source": "z", "target": "dw"},
             {"id": "e3", "source": "dw", "target": "ds"}]
    if alert:
        nodes.append({"id": "al", "type": "output.alert",
                      "position": {"x": 3, "y": 0},
                      "data": {"metric": "live_s", "op": ">", "value": 30}})
        edges.append({"id": "e4", "source": "dw", "target": "al"})
    return {"nodes": nodes, "edges": edges}


def compile_(g):
    return wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)


# 1. pid on the node reaches the engine as stamp_events
c = compile_(graph())
assert not c["errors"], c["errors"]
fn = next(f for f in c["functions"] if f["id"] == "bay")
assert fn["params"].get("stamp_events") is True, fn["params"]
print("== pid on Time in Zone compiles to params.stamp_events")

# 2. ...and the per-stay event fields resolve on the table's columns
doc = next(o for o in c["outputs"] if o["type"] == "dataset")
cols = {x["name"]: x for x in doc["columns"]}
assert cols["cycle_s"]["field"] == "duration_s", cols
assert cols["entered"]["field"] == "entered_at", cols
print("== duration_s / entered_at are legal event columns")

# 3. the table is ARMED by the stays themselves: trigger info, no Alert needed
assert doc["trigger"] == "info" and doc["function"] == "bay", doc
print("== a stay-fed table fires on the per-stay events:", doc["trigger"])

# 4. an Alert on the same rule still wins -- rows on the threshold, as before
c = compile_(graph(alert=True))
assert not c["errors"], c["errors"]
doc = next(o for o in c["outputs"] if o["type"] == "dataset")
assert doc["trigger"] == "alert", doc
print("== with an Alert wired, the threshold stays the moment to record")

# 5. no pid, no alert: nothing arms it, and the message says what to do
c = compile_(graph(pid=False))
assert c["errors"], "compiled with nothing arming the table"
msg = c["errors"][0]["message"]
assert "OID" in msg, msg
print("== unarmed table names the fix:", msg)

# -- the app side: the row is about the object that LEFT ----------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import outputs as out                                 # noqa: E402

stay_event = {"ts": "2026-08-13T10:00:00",
              "detail": {"function": "bay", "track": 7,
                         "entered_at": 1000.0, "exited_at": 1012.4,
                         "duration_s": 12.4,
                         # somebody ELSE still in frame, more confident
                         "detections": [{"label": "person", "confidence": 0.99,
                                         "track_id": 9}]}}
table = {"id": "wf_ds", "type": "dataset", "enabled": True,
         "dataset": "cycle_log", "name": "cycle_log", "mode": "alert",
         "trigger": "info", "function": "bay",
         "columns": [{"name": "pid", "type": "text", "src": "event",
                      "field": "track_id"},
                     {"name": "cycle_s", "type": "number", "src": "event",
                      "field": "duration_s"},
                     {"name": "entered", "type": "timestamp", "src": "event",
                      "field": "entered_at"}]}
rows = []
out._dataset(table, {"dataset": lambda _o, c_: rows.append(c_)}, stay_event, {})
assert len(rows) == 1, rows
row = rows[0]
assert row["pid"] == f"{out._RUN_ID}-7", row       # the leaver, NOT track 9
assert row["cycle_s"] == 12.4, row
assert row["entered"] and row["entered"][:4].isdigit(), row
print("== one row per stay, naming the object that left:", row)

# ...and fire() routes the info event to it, by function id
rows = []
out.fire([table], {"type": "info", "ts": "2026-08-13T10:00:01",
                   "detail": stay_event["detail"]},
         {"dataset": lambda _o, c_: rows.append(c_)}, {})
assert len(rows) == 1, rows
print("== fire() delivers the per-stay event to the table")

# an event from a DIFFERENT function does not write this table
rows = []
out.fire([table], {"type": "info", "detail": {"function": "other"}},
         {"dataset": lambda _o, c_: rows.append(c_)}, {})
assert not rows, rows
print("== another rule's event leaves the table alone")

# -- the threshold is per OBJECT too -----------------------------------------
# "In the zone longer than T" is a question about each object once OID is on.
# The aggregate live_s cannot answer it: it is the LONGEST stay running, so it
# sits over the line while a newcomer is at one second, and the moment that
# newcomer earns is never raised at all. One person already past the threshold
# must not answer for somebody who has just walked in.
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app.functions import Function, FunctionEngine              # noqa: E402
import app.zones as zones_mod                                   # noqa: E402

ZONE = zones_mod.Zone({"id": "z1", "name": "Z", "kind": "area", "enabled": True,
                       "points": [[0, 0], [1, 0], [1, 1], [0, 1]]})


def engine(oid=True):
    params = {"zone": "z1"}
    if oid:
        params["stamp_events"] = True
    fn = Function({"id": "cyc", "type": "zone_dwell", "name": "Cycle",
                   "enabled": True, "params": params,
                   "triggers": [{"metric": "live_s", "op": ">", "value": 10,
                                 "severity": "crit", "message": "too long",
                                 "cooldown_s": 30}]})
    return FunctionEngine([fn], state_dir=None)


def tick(eng, t, ids):
    dets = [{"track_id": i, "label": "person", "box": [10, 10, 50, 50]}
            for i in ids]
    evs = eng.update({"crossing_events": []}, dets, {i: None for i in ids}, {},
                     100, 100, zones=[ZONE], now=t)
    return [e for e in evs if e["type"] == "alert"], eng.stats()


e = engine(oid=True)
seen = []
for t, ids in [(0, [1]), (5, [1]), (10.5, [1]), (11, [1]), (20, [1]),
               (30, [1, 2]), (35, [1, 2]), (39, [1, 2]),
               (40.6, [1, 2]), (41, [1, 2]), (60, [1, 2])]:
    alerts, stats = tick(e, t, ids)
    seen += [(t, a["detail"]["track"], round(a["value"], 1)) for a in alerts]

assert seen == [(10.5, 1, 10.5), (40.6, 2, 10.6)], seen
print("== each object earns the alert on ITS own clock, not the zone's")
# ...and the value carried is that object's seconds, not the longest stay.
assert seen[1][2] == 10.6 and stats["fn_cyc_live_s"] == 60.0, (seen, stats)
print("== and the event says which object, and for how long")
# The rule is still 'active' while anybody is over the line -- a Verdict Card
# bound to it must not go dark just because the events are per object now.
assert stats["fn_cyc_active"] == 1, stats
print("== `active` still reports the zone as a whole")

# Nobody is told twice: the aggregate trigger no longer fires alongside.
alerts, _ = tick(e, 61, [1, 2])
assert not alerts, alerts
print("== no second event from the aggregate path")

# A stay that ENDS re-arms that object: coming back is a new visit.
tick(e, 62, [1])                       # 2 leaves
alerts, _ = tick(e, 75, [1, 2])        # ...and returns, clock from zero
assert not alerts, alerts
alerts, _ = tick(e, 86, [1, 2])
assert [(a["detail"]["track"], round(a["value"])) for a in alerts] == [(2, 11)], alerts
print("== a re-entry starts that object's clock again")

# With OID OFF nothing changes: one aggregate alert, paced by its cooldown.
e2 = engine(oid=False)
plain = []
for t, ids in [(0, [1]), (10.5, [1]), (20, [1]), (31, [1]), (40.6, [1, 2])]:
    alerts, _ = tick(e2, t, ids)
    # The aggregate path carries no detail at all -- it is about the zone,
    # and has no object to name. That absence is the difference.
    plain += [(t, (a.get("detail") or {}).get("track")) for a in alerts]
assert plain == [(10.5, None), (40.6, None)], plain
print("== OID off keeps the old zone-wide alert, cooldown and all")
print("ALL OK")
