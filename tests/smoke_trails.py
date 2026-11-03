# Motion trails: each tracked object leaves a dotted movement line on the
# feed. A workflow node (analytics.trails) controls which classes leave one
# and how many seconds of history to keep; the app's FunctionEngine stores a
# point only when the object MOVED (dot spacing IS movement) and camera.
# annotate() draws the dots under everything else.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import functions as fn_mod                            # noqa: E402

# 1. The engine: a moving person leaves a growing trail; a class the node
#    did not ask about leaves none; points age out with length_s.
fn = fn_mod.Function({"id": "trail1", "type": "trails", "name": "Trail",
                      "params": {"classes": ["person"], "length_s": 5}})
eng = fn_mod.FunctionEngine([fn])
t0 = 1000.0
for i in range(6):
    dets = [
        {"label": "person", "track_id": 7,
         "box": [100 + i * 20, 100, 140 + i * 20, 180]},
        {"label": "chair", "track_id": 9, "box": [400, 300, 460, 380]},
    ]
    eng.update({}, dets, {}, {}, 640, 480, zones=[], now=t0 + i * 0.5)
trails = eng.trails()
assert len(trails) == 1, f"one tracked person = one trail, got {len(trails)}"
pts, live = trails[0]
assert live and len(pts) >= 4, "a moving object must accumulate points"
stats = eng.stats()
assert stats.get("fn_trail1_tracked") == 1
assert stats.get("fn_trail1_active") == 1, "it moved this second"
# 5s later without the object: the trail ages out entirely.
eng.update({}, [], {}, {}, 640, 480, zones=[], now=t0 + 20)
assert eng.trails() == [], "an absent object's trail must fade, not hang"
assert eng.stats().get("fn_trail1_tracked") == 0

# 2. A parked object stores (almost) nothing: dot spacing is movement.
fn2 = fn_mod.Function({"id": "trail2", "type": "trails", "name": "T",
                       "params": {"classes": [], "length_s": 10}})
eng2 = fn_mod.FunctionEngine([fn2])
for i in range(10):
    eng2.update({}, [{"label": "car", "track_id": 1,
                      "box": [200, 200, 260, 260]}],
                {}, {}, 640, 480, zones=[], now=t0 + i * 0.3)
assert eng2.trails() == [], "a parked object is a dot, never a line"

# 3. Validation: silly length refused, and the type is registered.
assert "trails" in fn_mod.TYPES
try:
    fn_mod.Function({"id": "bad", "type": "trails",
                     "params": {"length_s": 999}})
    raise AssertionError("length_s 999 accepted")
except ValueError:
    pass

# 4. The draw path: annotate() takes trails and run.py passes them.
with open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
          encoding="utf-8") as f:
    cam_src = f.read()
assert "trails=None" in cam_src and "for pts, live in trails or []" in cam_src
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    run_src = f.read()
# The loop hands the trails to the OVERLAY snapshot, and the one renderer
# (_paint) draws it -- for the recorder, for a snapshot and for every viewer
# painting its own frame (smoke_idlejpeg.py). Two draw sites would be two
# chances for the feed and the clip to disagree about what was tracked.
assert '"trails": pipe.fn_engine.trails(),' in run_src
assert 'trails=overlay.get("trails") or []' in run_src

# 5. The workflow node exists server-side, with the class-picker wiring, and
#    the page offers it: overlay card, inspector, palette summary.
from core.services import workflow as wf                       # noqa: E402
spec = wf.REGISTRY.get("analytics.trails")
assert spec and spec["fn_type"] == "trails"
assert "tracked" in spec["metrics"] and "active" in spec["metrics"]
with open(os.path.join(ROOT, "core", "services", "workflow.py"),
          encoding="utf-8") as f:
    assert '"analytics.trails": ("classes",)' in f.read()
with open(os.path.join(ROOT, "frontend", "src", "components",
                       "OverlayPanel.jsx"), encoding="utf-8") as f:
    ov = f.read()
# The overlay panel still offers it, under the name the node now carries.
# The TYPE is what everything else keys on and it is deliberately unchanged --
# renaming a node's label must never migrate a saved graph.
assert '"analytics.trails"' in ov and "Add motion tracking" in ov
with open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
          encoding="utf-8") as f:
    assert 'node.type === "analytics.trails"' in f.read()

# 6. Idle: an object that stops moving counts in `idle` after idle_s.
fn3 = fn_mod.Function({"id": "trail3", "type": "trails", "name": "T",
                       "params": {"classes": [], "idle_s": 2, "length_s": 30}})
eng3 = fn_mod.FunctionEngine([fn3])
t1 = 2000.0
# moves for 2s...
for i in range(4):
    eng3.update({}, [{"label": "person", "track_id": 3,
                      "box": [100 + i * 30, 100, 140 + i * 30, 180]}],
                {}, {}, 640, 480, zones=[], now=t1 + i * 0.5)
assert eng3.stats().get("fn_trail3_idle") == 0
# ...then parks for 3s
for i in range(6):
    eng3.update({}, [{"label": "person", "track_id": 3,
                      "box": [190, 100, 230, 180]}],
                {}, {}, 640, 480, zones=[], now=t1 + 2.0 + i * 0.5)
assert eng3.stats().get("fn_trail3_idle") == 1, "a parked object must read idle"
assert eng3.stats().get("fn_trail3_active") == 0

# 7. The Zone Switch output: registered, validated, and wired end to end --
#    workflow node, app tick handler, run.py hook into the live zone engine.
from core.services import detector_control as dcs              # noqa: E402
out = dcs._validate_output({"type": "zone_switch", "zone": "z1",
                            "key": "fn_x_idle", "action": "disable",
                            "trigger": "any", "enabled": True})
assert out["zone"] == "z1" and out["key"] == "fn_x_idle"
assert out["action"] == "disable"
# The node says what a 1 DOES; the default is "enable", and a graph saved
# with the old invert flag still loads as its equivalent.
assert dcs._validate_output({"type": "zone_switch", "zone": "z", "key": "k",
                             "trigger": "any"})["action"] == "enable"
assert dcs._validate_output({"type": "zone_switch", "zone": "z", "key": "k",
                             "invert": True,
                             "trigger": "any"})["action"] == "disable"
for bad in ({"type": "zone_switch", "key": "k", "trigger": "any"},
            {"type": "zone_switch", "zone": "z", "trigger": "any"}):
    try:
        dcs._validate_output(bad)
        raise AssertionError(f"accepted {bad}")
    except ValueError:
        pass
zspec = wf.REGISTRY.get("output.zone_switch")
assert zspec and zspec["output_type"] == "zone_switch" and zspec.get("no_trigger")
with open(os.path.join(ROOT, "systems", "demo", "app", "outputs.py"),
          encoding="utf-8") as f:
    osrc = f.read()
assert "_zone_switch(o, stats, hooks)" in osrc and "zone_enable" in osrc
with open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
          encoding="utf-8") as f:
    rsrc = f.read()
assert '"zone_enable": _zone_enable' in rsrc, "run.py must expose the hook"

# 8. A disabled zone is carried but SILENT -- no counts, no drawing -- so
#    the switch can flip it live. (It used to be filtered out at build,
#    which made it unreachable at runtime.)
assert "if mine(z.camera)]" in rsrc and "z.enabled and mine" not in rsrc,     "disabled zones must reach the engine"
from app import zones as zones_mod                             # noqa: E402
zs = zones_mod.parse_zones([
    {"id": "gate", "kind": "area", "name": "Gate", "enabled": False,
     "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]}])
zeng = zones_mod.ZoneEngine(zs)
res = zeng.update([{"label": "person", "track_id": 1,
                    "box": [300, 300, 340, 380]}], 640, 480)
assert (res.get("zones") or {}).get("gate") is None,     "a disabled zone must produce no stats"
zs[0].enabled = True
res = zeng.update([{"label": "person", "track_id": 1,
                    "box": [300, 300, 340, 380]}], 640, 480)
assert (res.get("zones") or {}).get("gate"),     "flipping enabled live must bring the zone back"
with open(os.path.join(ROOT, "systems", "demo", "app", "camera.py"),
          encoding="utf-8") as f:
    assert 'ov.get("enabled") is False' in f.read(),         "a disabled zone must not be drawn"

# 9. The pages: zone rows carry the enable checkbox, and the inspector
#    offers the Zone Switch fields.
with open(os.path.join(ROOT, "frontend", "src", "pages", "Zones.jsx"),
          encoding="utf-8") as f:
    assert "patchZone(z.id, { enabled: e.target.checked })" in f.read()
with open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
          encoding="utf-8") as f:
    wsrc = f.read()
assert 'node.type === "output.zone_switch"' in wsrc
assert "enable the zone (0 disables it)" in wsrc,     "the switch must say what a 1 does, not carry a polarity flag"
assert "idle_s" in wsrc, "the trails inspector must offer the idle threshold"

# 10. The two TYPES lists -- the app's and core's validator copy -- must
#     agree, or a node the palette offers fails the save with "function type
#     must be one of ..." (found live: trails existed app-side only).
from core.services import functions as core_fns                # noqa: E402
assert set(core_fns.TYPES) == set(fn_mod.TYPES), (
    "core/services/functions.py TYPES drifted from app TYPES: "
    + str(set(core_fns.TYPES) ^ set(fn_mod.TYPES)))

# 11. The node card shows the three published values live -- what there is
#     to wire, with its current number (active is the 0/1 bit).
with open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
          encoding="utf-8") as f:
    njx = f.read()
for m in ("tracked", "active", "idle"):
    assert '"' + m + '"' in njx.split('analytics.trails" && d.fnId', 1)[1][:900],         "the trail card must read out " + m

# 12. The COMPILER carries the node's settings into the function params.
#     Without a branch of its own the whole data dict was dropped and the
#     engine ran on defaults -- so a chosen class list was ignored and every
#     object got a trail ("why is it tracking all the objects?").
graph = {"nodes": [
    {"id": "src1", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "tr1", "type": "analytics.trails", "position": {"x": 200, "y": 0},
     "data": {"fnId": "trail_x", "name": "Trail", "classes": ["pointer"],
              "length_s": 22, "idle_s": 7, "draw": False}},
], "edges": [{"id": "e1", "source": "src1", "target": "tr1"}]}
out = wf.compile_graph(graph, [], classes=["pointer", "person"])
fns = [f for f in out["functions"] if f["type"] == "trails"]
assert fns, "the trails node must compile to a function: " + str(out["errors"])
p = fns[0]["params"]
assert p.get("classes") == ["pointer"],     "the chosen class list must reach the engine, got " + repr(p.get("classes"))
assert p.get("length_s") == 22 and p.get("idle_s") == 7
assert p.get("draw") is False
# ...and an empty picker still means every class (a legitimate ask).
graph["nodes"][1]["data"]["classes"] = []
out2 = wf.compile_graph(graph, [], classes=["pointer", "person"])
p2 = [f for f in out2["functions"] if f["type"] == "trails"][0]["params"]
assert not p2.get("classes"), "no picked classes = trail everything"

print("smoke_trails OK")
