# Snapshot/Record nodes must read as unwired when their System Control switch
# is off -- and come back when it is on. The operator's wire is never touched.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]

GRAPH = wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "Bay"}},
    {"id": "when", "type": "logic.when", "position": {"x": 3, "y": 0},
     "data": {"metric": "count", "mode": "above", "value": 2}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 4, "y": 0}, "data": {}},
    {"id": "rec", "type": "output.record", "position": {"x": 4, "y": 1}, "data": {}},
    {"id": "mq", "type": "output.mqtt", "position": {"x": 4, "y": 2},
     "data": {"host": "127.0.0.1", "port": 1883, "topic": "t", "payload": "1"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "when"},
    {"id": "e4", "source": "when", "target": "snap"},
    {"id": "e5", "source": "when", "target": "rec"},
    {"id": "e6", "source": "when", "target": "mq"},
]})


def types_of(capture):
    c = wf.compile_graph(GRAPH, ZONES, capture=capture)
    assert not c["errors"], c["errors"]
    warned = {w["node"]: w["message"] for w in c["warnings"]}
    return sorted(o["type"] for o in c["outputs"]), warned


# 1. both switches on -> everything compiles
outs, warned = types_of({"snap": True, "record": True})
print("both on   ->", outs)
assert outs == ["mqtt", "record", "snapshot"], outs

# 2. clips off -> the Record node stops compiling and says why
outs, warned = types_of({"snap": True, "record": False})
print("clips off ->", outs, "|", warned.get("rec"))
assert outs == ["mqtt", "snapshot"], outs
assert "switched off" in warned["rec"], warned
assert "System Control" in warned["rec"]

# 3. snapshots off too -> only the untouched output survives
outs, warned = types_of({"snap": False, "record": False})
print("both off  ->", outs, "|", warned.get("snap"))
assert outs == ["mqtt"], outs
assert "switched off" in warned["snap"], warned

# 4. the phrase the canvas dims on
import re                                                     # noqa: E402
SWITCHED_OFF_RE = re.compile(r"switched off", re.I)
assert SWITCHED_OFF_RE.search(warned["snap"]), \
    "the warning does not match what the canvas dims on"
print("== the warning matches the canvas's switched-off test")

# 5. turning it back on restores it -- the graph was never edited
outs, _ = types_of({"snap": True, "record": True})
assert outs == ["mqtt", "record", "snapshot"], outs
assert len(GRAPH["edges"]) == 6, "compiling edited the operator's wiring"
print("== switching back on restores them; the wiring was never touched")

# 6. no capture info at all (older callers) changes nothing
outs, _ = types_of(None)
assert outs == ["mqtt", "record", "snapshot"], outs
print("== a caller that passes no switches compiles everything, as before")

# 7. a snapshot node naming a camera the system no longer has: the id is
# dropped and the photo follows the rule's own camera, instead of being
# skipped for ever at runtime. This is how a swapped camera used to kill
# snapshots silently -- the node kept the old id and the app logged "camera
# not running" per alert.
#
# Fixed, not announced. The capture works after this, so a warning on every
# save would be a banner about a graph doing exactly what it should.
STALE = wf.clean_graph({"nodes": GRAPH["nodes"][:-3] + [
    {"id": "snap", "type": "output.snapshot", "position": {"x": 4, "y": 0},
     "data": {"camera": "ghost_cam"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "when"},
    {"id": "e4", "source": "when", "target": "snap"},
]})
c = wf.compile_graph(STALE, ZONES, cameras={"cam_a"})
assert not c["errors"], c["errors"]
warned = {w["node"]: w["message"] for w in c["warnings"]}
assert "snap" not in warned, warned
snap_out = [o for o in c["outputs"] if o["type"] == "snapshot"][0]
assert snap_out.get("camera", "") != "ghost_cam", snap_out
print("== a dead camera id is dropped, quietly:", snap_out.get("camera") or "(follows the rule)")

# 8. a camera the system DOES have passes untouched...
ok_graph = wf.clean_graph({**STALE, "nodes": STALE["nodes"][:-1] + [
    {"id": "snap", "type": "output.snapshot", "position": {"x": 4, "y": 0},
     "data": {"camera": "cam_a"}},
]})
c = wf.compile_graph(ok_graph, ZONES, cameras={"cam_a"})
assert not c["errors"], c["errors"]
assert not any(w["node"] == "snap" for w in c["warnings"]), c["warnings"]
assert [o for o in c["outputs"] if o["type"] == "snapshot"][0]["camera"] == "cam_a"
print("== a live camera id compiles as named")

# 9. ...and a caller that cannot list cameras (None) skips the check entirely
c = wf.compile_graph(STALE, ZONES, cameras=None)
assert not any("no longer has" in w["message"] for w in c["warnings"]), c["warnings"]
print("== no camera list, no stale-camera verdict")
print("ALL OK")
