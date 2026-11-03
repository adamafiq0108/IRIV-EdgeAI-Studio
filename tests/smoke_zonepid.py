# A zone counter's PID column records WHO IS IN THE ZONE -- not whoever
# happens to be in frame.
#
# The pid port compiled to "the event's tracked object" with no zone test,
# so `zone_pid` filled with the same id as `frame_pid` for a person who never
# entered the zone. The column now carries the rule's zone, the zone engine
# stamps membership on every area zone's members, and the row builder refuses
# an id that is not inside.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "zone_a", "name": "Zone 1", "kind": "area", "enabled": True}]
TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "live_counting", "type": "number"},
    {"name": "frame_pid", "type": "text"},
    {"name": "zone_pid", "type": "text"}]}}

GRAPH = {"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 1},
     "data": {"fnId": "in_frame", "name": "In frame", "mode": "realtime",
              "pid": True}},
    {"id": "zc", "type": "analytics.violations", "position": {"x": 2, "y": 0},
     "data": {"fnId": "zone_now", "name": "Zone now", "mode": "realtime",
              "pid": True}},
    {"id": "ds", "type": "output.dataset", "position": {"x": 3, "y": 0},
     "data": {"dataset": "counting", "name": "Counting", "mode": "change",
              "sources": {}}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "s", "target": "fr"},
    {"id": "e3", "source": "z", "target": "zc"},
    {"id": "e4", "source": "fr", "target": "ds", "targetHandle": "c_live_counting"},
    {"id": "e5", "source": "fr", "sourceHandle": "pid", "target": "ds",
     "targetHandle": "c_frame_pid"},
    {"id": "e6", "source": "zc", "sourceHandle": "pid", "target": "ds",
     "targetHandle": "c_zone_pid"},
]}

c = wf.compile_graph(wf.clean_graph(GRAPH), ZONES, tables=TABLES)
assert not c["errors"], c["errors"]
doc = next(o for o in c["outputs"] if o["type"] == "dataset")
cols = {x["name"]: x for x in doc["columns"]}
assert "zone" not in cols["frame_pid"], cols["frame_pid"]
assert cols["zone_pid"].get("zone") == "zone_a", cols["zone_pid"]
print("== the zone counter's PID column carries its zone; the frame one does not")

# -- the app side --------------------------------------------------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import outputs as out                                 # noqa: E402
from app import zones as zn                                    # noqa: E402

# The zone engine stamps membership on every area zone's members.
eng = zn.ZoneEngine(zn.parse_zones([
    {"id": "zone_a", "name": "Z", "kind": "area", "enabled": True,
     "violation": False,
     "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}]))
inside = {"label": "person", "confidence": 0.9, "track_id": 7,
          "box": [10, 10, 30, 40]}       # x1,y1,x2,y2 -- left half: inside
outside = {"label": "person", "confidence": 0.95, "track_id": 9,
           "box": [70, 10, 95, 40]}      # right half: outside
eng.update([inside, outside], 100, 100)
assert "zone_a" in (inside.get("zones") or ()), inside
assert "zone_a" not in (outside.get("zones") or ()), outside
print("== membership stamped without needing the violation tick")

# The row builder: zone_pid only for the member; frame_pid for both.
table = {"id": "wf_ds", "type": "dataset", "enabled": True,
         "dataset": "counting", "name": "Counting", "mode": "change",
         "columns": [{"name": "frame_pid", "type": "text", "src": "event",
                      "field": "track_id"},
                     {"name": "zone_pid", "type": "text", "src": "event",
                      "field": "track_id", "zone": "zone_a"}]}
event = {"ts": "2026-08-14T12:00:00", "detail": {"detections": [inside, outside]}}
rows = out._dataset_rows(table, event, {}, [inside, outside])
by_frame = {r["frame_pid"]: r for r in rows}
assert by_frame[f"{out._RUN_ID}-7"]["zone_pid"] == f"{out._RUN_ID}-7"
assert by_frame[f"{out._RUN_ID}-9"]["zone_pid"] is None, by_frame
print("== in the zone: id recorded; merely in frame: zone_pid stays empty")

# -- the live per-person clock -------------------------------------------------
# The zone engine stamps every member with seconds-since-entry; a second
# update later must read a LONGER stay for the one who stayed, and nothing
# for the one outside.
import time as _t                                              # noqa: E402
_t.sleep(0.2)
eng.update([inside, outside], 100, 100)
stay = (inside.get("zone_in") or {}).get("zone_a")
assert stay is not None and stay >= 0.1, inside.get("zone_in")
assert "zone_a" not in (outside.get("zone_in") or {}), outside.get("zone_in")
print(f"== the member's clock runs ({stay}s); the outsider has none")

# The compile: a wire from the rule's `timer` port becomes a zone-scoped
# number column, whatever the table said the type was.
g2 = {**GRAPH}
g2["nodes"] = GRAPH["nodes"] + []
TABLES["counting"]["columns"].append({"name": "zone_secs", "type": "text"})
g2["edges"] = GRAPH["edges"] + [
    {"id": "e7", "source": "zc", "sourceHandle": "timer", "target": "ds",
     "targetHandle": "c_zone_secs"}]
c = wf.compile_graph(wf.clean_graph(g2), ZONES, tables=TABLES)
assert not c["errors"], c["errors"]
doc = next(o for o in c["outputs"] if o["type"] == "dataset")
col = next(x for x in doc["columns"] if x["name"] == "zone_secs")
assert col == {"name": "zone_secs", "type": "number", "src": "event",
               "field": "in_zone_s", "handle": "c_zone_secs",
               "zone": "zone_a"}, col
print("== the timer port compiles to a zone-scoped number column")

# ...and the row builder reads the clock per object.
table["columns"].append({"name": "zone_secs", "type": "number",
                         "src": "event", "field": "in_zone_s",
                         "zone": "zone_a"})
rows = out._dataset_rows(table, event, {}, [inside, outside])
by_frame = {r["frame_pid"]: r for r in rows}
assert by_frame[f"{out._RUN_ID}-7"]["zone_secs"] == stay, by_frame
assert by_frame[f"{out._RUN_ID}-9"]["zone_secs"] is None, by_frame
print("== each row carries ITS object's own seconds inside")

# -- and they go NOWHERE else ------------------------------------------------
# Both ports are per-OBJECT and have no stat key: a stat is one value for the
# whole system. Everywhere but a table column, a wire is resolved by its
# source NODE and never by the port it left, so an OID dragged onto a card
# compiled to that node's number -- a Zone Counter's id port read as its
# count, a Cycle Time's as a duration. Plausible figures answering a different
# question, with nothing to see.
for handle in ("OID", "timer"):
    g3 = {**GRAPH}
    g3["nodes"] = GRAPH["nodes"] + [
        {"id": "card", "type": "dashboard.card", "position": {"x": 9, "y": 9},
         "data": {"name": "Who", "kind": "tile", "metric": ""}}]
    g3["edges"] = GRAPH["edges"] + [
        {"id": "e8", "source": "zc", "sourceHandle": handle, "target": "card",
         "targetHandle": "in"}]
    c = wf.compile_graph(wf.clean_graph(g3), ZONES, tables=TABLES)
    msg = " ".join(e["message"] for e in c["errors"])
    assert c["errors"], f"{handle} onto a card compiled silently"
    assert handle in msg and "Data Table column" in msg, msg
    assert not any(w.get("key") == "fn_zc_count" for w in c["widgets"]), c["widgets"]
print("== neither port can be wired to anything but a table column")

# ...and the refusal says what to do INSTEAD. "You cannot wire this" with no
# alternative is where an operator gives up: what they were reaching for --
# "do something when one has been in there too long" -- is buildable, just not
# off a per-object port.
assert "Cycle Time" in msg and "count port" in msg, msg
print("== and it names the value to branch on instead")

# The value port of the same rule branches perfectly well, which is the point:
# the refusal is about the PORT, not about the rule.
g4 = {**GRAPH}
g4["nodes"] = GRAPH["nodes"] + [
    {"id": "br", "type": "branch.cases", "position": {"x": 9, "y": 2},
     "data": {"fnId": "b1", "name": "Busy?",
              "rules": [{"op": ">", "value": 3, "input": "in1"}]}}]
g4["edges"] = GRAPH["edges"] + [
    {"id": "e9", "source": "zc", "target": "br", "targetHandle": "in1"}]
c = wf.compile_graph(wf.clean_graph(g4), ZONES, tables=TABLES)
assert not c["errors"], c["errors"]
print("== the rule's own value still branches")

# Refused at DRAW time as well, so the wire never lands and turns a node red
# after the fact -- the compile rule above is the backstop for a graph built
# anywhere else (a template, an import, the API).
page = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
            encoding="utf-8").read()
assert '["OID", "timer"].includes(conn.sourceHandle)' in page, \
    "the editor still lets a per-object port be wired anywhere"
assert "Data Table column" in page and "Cycle Time" in page
print("== the editor refuses the wire while it is being drawn")
print("ALL OK")
