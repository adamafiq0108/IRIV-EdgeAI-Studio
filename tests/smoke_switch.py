# Switch: branches are exclusive (first match wins) and each output binds to
# the branch it leaves by.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402
from core.services import functions as fx                     # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]

NODES = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "Bay"}},
    {"id": "sw", "type": "logic.switch", "position": {"x": 3, "y": 0},
     "data": {"fnId": "route", "name": "Route", "metric": "count",
              "rules": [{"op": ">", "value": 10}, {"op": ">", "value": 5}],
              "otherwise": True}},
    {"id": "o0", "type": "output.snapshot", "position": {"x": 4, "y": 0}, "data": {}},
    {"id": "o1", "type": "output.record", "position": {"x": 4, "y": 1}, "data": {}},
    {"id": "o2", "type": "output.mqtt", "position": {"x": 4, "y": 2},
     "data": {"host": "127.0.0.1", "port": 1883, "topic": "t", "payload": "1"}},
]
EDGES = [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "sw"},
    {"id": "e4", "source": "sw", "target": "o0", "sourceHandle": "out0"},
    {"id": "e5", "source": "sw", "target": "o1", "sourceHandle": "out1"},
    {"id": "e6", "source": "sw", "target": "o2", "sourceHandle": "out2"},
]

c = wf.compile_graph(wf.clean_graph({"nodes": NODES, "edges": EDGES}), ZONES)
print("errors:", c["errors"])
assert not c["errors"], c["errors"]

branches = {f["id"]: f["params"]["expr"] for f in c["functions"]
            if f["id"].startswith("route_b")}
for fid in sorted(branches):
    print(f"   {fid:12s} {branches[fid]}")

# branch 0 is just its own test; later branches exclude the earlier ones
assert branches["route_b0"] == "(fn_bay_count > 10)", branches["route_b0"]
assert branches["route_b1"] == "(fn_bay_count > 5) and not (fn_bay_count > 10)", \
    branches["route_b1"]
assert branches["route_b2"] == \
    "not (fn_bay_count > 10) and not (fn_bay_count > 5)", branches["route_b2"]
print("== branches are exclusive: first match wins")

for expr in branches.values():
    fx.validate_expr(expr)              # the engine's own whitelist
print("== every branch is a valid expression")

# each output binds to ITS branch, not to the switch or to each other
bound = {o["type"]: o.get("function") for o in c["outputs"]}
print("== outputs bound to:", bound)
assert bound["snapshot"] == "route_b0"
assert bound["record"] == "route_b1"
assert bound["mqtt"] == "route_b2"
print("== each output fires on its own branch only")

# every branch carries a trigger, or nothing downstream would ever fire
for f in c["functions"]:
    if f["id"].startswith("route_b"):
        assert f.get("trigger") or f.get("triggers"), f
print("== every branch has a trigger")

# A switch with nothing wired in is a DRAFT: switched off and dim, not an
# error. Dragging a node out and wiring it a minute later is how the page is
# used, and painting the canvas red for that minute reported the operator's
# own pace as a fault. The errors it can raise are all still there -- they
# fire the moment something IS wired to it.
lone = wf.compile_graph(wf.clean_graph({
    "nodes": [NODES[3]], "edges": []}), ZONES)
assert not lone["errors"], lone["errors"]
msg = " ".join(w["message"] for w in lone["warnings"])
assert "switched off" in msg, lone["warnings"]
print("== an unwired Switch is switched off, not an error:", msg)
print("ALL OK")
