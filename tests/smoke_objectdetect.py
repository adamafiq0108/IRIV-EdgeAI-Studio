# "Is this object here?" as a workflow node -- in a zone, or anywhere on
# camera. One compiled rule (object_detect) behind two palette nodes, so a
# graph of Detection Stream -> Class Filter -> Object Detection -> Alert works
# Filter to wire and no threshold to reason about: `found` is 1 while a
# chosen object is present.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import functions as fn_service              # noqa: E402
from core.services import workflow as wf                       # noqa: E402
from app.functions import Function, FunctionEngine             # noqa: E402
from app.zones import ZoneEngine, parse_zones                  # noqa: E402

# 1. both nodes exist and compile to the SAME rule
frame_spec = wf.REGISTRY["analytics.object_frame"]
zone_spec = wf.REGISTRY["analytics.object_zone"]
assert frame_spec["fn_type"] == zone_spec["fn_type"] == "object_detect"
assert frame_spec["zone_kind"] is None and zone_spec["zone_kind"] == "area"
assert frame_spec["metrics"] == ["found", "count", "arrived"]
assert fn_service.OUTPUTS["object_detect"] == ("found", "count", "arrived", "verdict")
print("== Object Detection / Object in Zone compile to one rule")

# 1b. ...and are ONE node now. They always were one rule -- the compiler reads
# the zone off the wiring -- so the merged watch keeps the rule and makes the
# zone port optional. It answers PRESENCE only: `count` and `arrived` made it
# a third counter competing with the two that count, and the engine still
# publishes both, so a card already bound to one keeps reading it.
merged = wf.REGISTRY["analytics.object"]
assert merged["fn_type"] == "object_detect"
assert merged["metrics"] == ["found"], merged["metrics"]
assert merged["zone_optional"] == "area" and merged["zone_kind"] is None
assert frame_spec["deprecated"] and zone_spec["deprecated"]
assert not merged.get("deprecated")
# A zone reaches it, on the client's rules as well as the server's.
ok, why = wf.edge_allowed("zone.ref", "analytics.object")
assert ok, why

g = {"version": 6, "nodes": [
    {"id": "a", "type": "analytics.object_frame", "position": {"x": 0, "y": 0},
     "data": {"fnId": "watch1"}},
    {"id": "b", "type": "analytics.object_zone", "position": {"x": 0, "y": 0},
     "data": {"fnId": "watch2"}}], "edges": []}
assert wf.migrate_object_merge(g) is True
assert {n["type"] for n in g["nodes"]} == {"analytics.object"}, g["nodes"]
assert [n["data"]["fnId"] for n in g["nodes"]] == ["watch1", "watch2"]
assert g["version"] == 7, g["version"]
print("== both watches are one node whose zone port is optional")

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
          "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}]


def compile_graph(nodes, edges, zones=ZONES):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    return wf.compile_graph(g, zones)


SRC = {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
       "data": {}}


def wire(a, b, handle=None):
    return {"id": f"e{a}{b}{handle or ''}", "source": a, "target": b,
            "targetHandle": handle}


# 2. the frame node: a Class Filter says what to watch for
def cf(nid, classes):
    return {"id": nid, "type": "filter.class", "position": {"x": 1, "y": 0},
            "data": {"classes": classes}}


c = compile_graph(
    [SRC, cf("f1", ["person"]),
     {"id": "o1", "type": "analytics.object_frame", "position": {"x": 2, "y": 0},
      "data": {"fnId": "ofr", "name": "Person here"}}],
    [wire("src", "f1"), wire("f1", "o1")])
assert not c["errors"], c["errors"]
doc = next(f for f in c["functions"] if f["id"] == "ofr")
assert doc["type"] == "object_detect", doc
assert doc["params"]["classes"] == ["person"], doc
assert "zone" not in doc["params"], doc
print("== a frame watch takes its classes from the Class Filter wired in")

# 2b. MORE THAN ONE filter may feed one watch -- their classes add together,
#     which is how "a person OR a forklift is here" is one node.
c = compile_graph(
    [SRC, cf("f1", ["person"]), cf("f2", ["forklift", "truck"]),
     {"id": "o1", "type": "analytics.object_frame", "position": {"x": 2, "y": 0},
      "data": {"fnId": "ofr", "name": "Anyone here"}}],
    [wire("src", "f1"), wire("src", "f2"),
     wire("f1", "o1"), wire("f2", "o1")])
assert not c["errors"], c["errors"]
doc = next(f for f in c["functions"] if f["id"] == "ofr")
assert doc["params"]["classes"] == ["forklift", "person", "truck"], doc
print("== several Class Filters feed one watch and add together")

# 3. no Class Filter at all is an ERROR: "anything is here" is a Frame
#    Counter, and a watch that silently meant it would fire on everything.
c = compile_graph(
    [SRC,
     {"id": "o1", "type": "analytics.object_frame", "position": {"x": 2, "y": 0},
      "data": {"fnId": "ofr", "name": "Anything"}}],
    [wire("src", "o1")])
assert [e for e in c["errors"] if e.get("node") == "o1"], c
assert not [f for f in c["functions"] if f["id"] == "ofr"], c["functions"]
print("== a watch with no Class Filter is refused, not left watching everything")

# 4. the zone node: the zone comes from the WIRING, and unions the classes a
#    Class Filter upstream adds
c = compile_graph(
    [SRC,
     cf("f1", ["forklift"]), cf("f2", ["person"]),
     {"id": "z", "type": "zone.ref", "position": {"x": 2, "y": 0},
      "data": {"zone": "bay"}},
     {"id": "o1", "type": "analytics.object_zone", "position": {"x": 3, "y": 0},
      "data": {"fnId": "ozn", "name": "In bay"}}],
    [wire("src", "f1"), wire("src", "f2"),
     wire("f1", "z"), wire("f2", "z"), wire("z", "o1")])
assert not c["errors"], c["errors"]
doc = next(f for f in c["functions"] if f["id"] == "ozn")
assert doc["params"]["zone"] == "bay", doc
assert doc["params"]["classes"] == ["forklift", "person"], doc
print("== a zone watch reads its zone from the wire and both filters through it")

# 5. a zone node with no zone wired is an error, not a silent whole-frame rule
c = compile_graph(
    [SRC, cf("f1", ["person"]),
     {"id": "o1", "type": "analytics.object_zone", "position": {"x": 2, "y": 0},
      "data": {"fnId": "ozn", "name": "In bay"}}],
    [wire("src", "f1"), wire("f1", "o1")])
assert [e for e in c["errors"] if e.get("node") == "o1"], c
print("== Object in Zone without a zone is refused, not quietly reinterpreted")

# 6. a line zone is refused too -- "inside" means an area
c = compile_graph(
    [SRC, cf("f1", ["person"]),
     {"id": "z", "type": "zone.ref", "position": {"x": 2, "y": 0},
      "data": {"zone": "gate"}},
     {"id": "o1", "type": "analytics.object_zone", "position": {"x": 3, "y": 0},
      "data": {"fnId": "ozn", "name": "In bay"}}],
    [wire("src", "f1"), wire("f1", "z"), wire("z", "o1")],
    zones=[{"id": "gate", "name": "Gate", "kind": "line", "enabled": True,
            "points": [[0.0, 0.5], [1.0, 0.5]]}])
# A line is a THIN PLACE: the watch runs AT the line (a narrow band around
# it) -- accepted silently, because a warning for using a feature reads as
# a fault. The inspector text carries the semantics.
assert not c["errors"], c["errors"]
assert not [w for w in c["warnings"] if w.get("node") == "o1"], c["warnings"]
print("== a line is accepted silently: the watch runs AT the line")

# 7. the ENGINE: found/count/arrived over three frames
zeng = ZoneEngine(parse_zones(ZONES))
eng = FunctionEngine([
    Function({"id": "ofr", "name": "Person here", "type": "object_detect",
              "params": {"classes": ["person"]}}),
    Function({"id": "ozn", "name": "In bay", "type": "object_detect",
              "params": {"classes": ["person"], "zone": "bay"}}),
])
W, H = 640, 480
INSIDE = [40, 40, 120, 400]         # left half -> in the bay
OUTSIDE = [500, 40, 600, 400]       # right half -> out of it


def tick(dets, t):
    # Two frames per tick would confirm a track; the tracker is not wired in
    # here, so ids are supplied directly -- what the engine reads is
    # det["track_id"], whoever stamped it.
    result = zeng.update(dets, W, H)
    eng.update(result, dets, {}, zeng.stats(result), W, H,
               zones=zeng.zones, now=t)
    return eng.stats()


# nothing on camera
s = tick([], 1000.0)
assert s["fn_ofr_found"] == 0 and s["fn_ofr_count"] == 0, s
assert s["fn_ozn_found"] == 0, s
print("== an empty frame reads found=0")

# a person walks in, outside the zone
s = tick([{"label": "person", "box": OUTSIDE, "track_id": 7}], 1001.0)
assert s["fn_ofr_found"] == 1 and s["fn_ofr_count"] == 1, s
assert s["fn_ofr_arrived"] == 1, s
assert s["fn_ozn_found"] == 0 and s["fn_ozn_arrived"] == 0, s
print("== on camera but outside the zone: the frame watch fires, the zone's does not")

# he stays: still found, but no longer an arrival
s = tick([{"label": "person", "box": OUTSIDE, "track_id": 7}], 1002.0)
assert s["fn_ofr_found"] == 1 and s["fn_ofr_arrived"] == 0, s
print("== standing still is not an arrival every frame")

# he steps into the bay, and a forklift joins him on camera
s = tick([{"label": "person", "box": INSIDE, "track_id": 7},
          {"label": "forklift", "box": OUTSIDE, "track_id": 9}], 1003.0)
assert s["fn_ozn_found"] == 1 and s["fn_ozn_count"] == 1, s
assert s["fn_ozn_arrived"] == 1, s
# the forklift is not a person: the class filter still decides
assert s["fn_ofr_count"] == 1, s
print("== entering the zone is an arrival; a class nobody asked for is ignored")

# he leaves the frame entirely
s = tick([], 1004.0)
assert s["fn_ofr_found"] == 0 and s["fn_ozn_found"] == 0, s
print("== leaving drops found back to 0")

# 8. an unconfirmed detection (no track id) still counts as present -- it is
#    visibly there -- but cannot fire an arrival, so a one-frame ghost raises
#    no event.
s = tick([{"label": "person", "box": OUTSIDE}], 1005.0)
assert s["fn_ofr_found"] == 1 and s["fn_ofr_count"] == 1, s
assert s["fn_ofr_arrived"] == 0, s
print("== an unconfirmed object is present but never an arrival")

# 9. the rule validates on the core side too, zone optional
doc = fn_service.validate_function(
    {"id": "ofr", "name": "P", "type": "object_detect",
     "params": {"classes": ["person"]}})
assert doc["params"]["classes"] == ["person"] and "zone" not in doc["params"]
doc = fn_service.validate_function(
    {"id": "ozn", "name": "P", "type": "object_detect",
     "params": {"classes": ["person"], "zone": "bay"}})
assert doc["params"]["zone"] == "bay"
print("== the rule validates with or without a zone")

# 10. the watch obeys the counters' timing. "Must stay before it counts as
#     present" is the same question a counter asks, and a watch that answered
#     it differently would be a second meaning for one setting. Both 0 -- the
#     default -- is real time, needs no tracker, and is what every watch did
#     before this existed.
doc = fn_service.validate_function(
    {"id": "slow", "name": "P", "type": "object_detect",
     "params": {"classes": ["person"], "enter_after_s": 2, "leave_after_s": 1}})
assert doc["params"]["enter_after_s"] == 2.0, doc["params"]
assert doc["params"]["leave_after_s"] == 1.0, doc["params"]

eng2 = FunctionEngine([Function(doc)])


def tick2(dets, now):
    result = zeng.update(dets, W, H)
    eng2.update(result, dets, {}, zeng.stats(result), W, H,
                zones=zeng.zones, now=now)
    return eng2.stats()


# ...seen once: in scope, not present yet.
s = tick2([{"label": "person", "box": OUTSIDE, "track_id": 3}], 2000.0)
assert s["fn_slow_found"] == 0 and s["fn_slow_count"] == 0, s
# ...still there two seconds later: present.
s = tick2([{"label": "person", "box": OUTSIDE, "track_id": 3}], 2002.5)
assert s["fn_slow_found"] == 1 and s["fn_slow_count"] == 1, s
# ...the tracker drops him for a frame: presence does NOT flicker off, because
# leave_after_s says keep him for a second.
s = tick2([], 2003.0)
assert s["fn_slow_found"] == 1, s
# ...and once he is properly gone, it clears.
s = tick2([], 2005.0)
assert s["fn_slow_found"] == 0, s
print("== a watch can be told to wait before it says 'present', and not to "
      "flicker when it goes")
print("ALL OK")
