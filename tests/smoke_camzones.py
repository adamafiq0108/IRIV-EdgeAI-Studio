# Removing a camera takes the zones drawn on it -- and nothing that merely
# looks like it belongs to it.
#
# Tok Aba (2026-10-09): deleting a camera on Camera Setup must delete every
# zone bound to it, after a confirm that names them. The rule is
# detector_control._zone_camera's, the one the engine uses:
#   * a zone naming the camera is on it;
#   * a zone naming NO camera, or a camera the system no longer has, is on
#     the DEFAULT (first) camera -- so it goes only when that camera goes.
# Canvas nodes bound to a deleted zone stay, unbound, and compile to the
# normal "waiting for a zone" warning instead of an error.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as d                # noqa: E402
from core.services import workflow as wf                       # noqa: E402

SD = tempfile.mkdtemp()
SLUG = "camzones"
pkg = os.path.join(SD, SLUG)
os.makedirs(pkg)

SQUARE = "points = [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]\n"
LINE = "points = [[0.1, 0.5], [0.9, 0.5]]\n"
with open(os.path.join(pkg, "config.local.toml"), "w", encoding="utf-8") as f:
    f.write(
        '[[cameras]]\nid = "main"\nsource = "0"\nenabled = true\n\n'
        '[[cameras]]\nid = "cam2"\nsource = "rtsp://10.0.0.9/s"\nenabled = true\n\n'
        '[[cameras]]\nid = "cam3"\nsource = "rtsp://10.0.0.8/s"\nenabled = false\n\n'
        # on main, by name
        '[[zones]]\nid = "z_main"\nname = "Main bay"\ncamera = "main"\nkind = "area"\n' + SQUARE + '\n'
        # no camera at all: belongs to the first camera (main)
        '[[zones]]\nid = "z_old"\nname = "Old zone"\nkind = "area"\n' + SQUARE + '\n'
        # names a camera that is gone: also resolves to the first camera
        '[[zones]]\nid = "z_ghost"\nname = "Ghost"\ncamera = "camX"\nkind = "area"\n' + SQUARE + '\n'
        # on cam2
        '[[zones]]\nid = "z_c2"\nname = "Dock"\ncamera = "cam2"\nkind = "area"\n' + SQUARE + '\n'
        '[[zones]]\nid = "l_c2"\nname = "Gate line"\ncamera = "cam2"\nkind = "line"\n' + LINE + '\n'
        # on cam3
        '[[zones]]\nid = "z_c3"\nname = "Yard"\ncamera = "cam3"\nkind = "area"\n' + SQUARE)


def ids(rows):
    return sorted(r["id"] for r in rows)


# 1. the preview answers by the rule, and changes nothing
assert ids(d.zones_on_camera(SD, SLUG, "cam2")) == ["l_c2", "z_c2"]
assert ids(d.zones_on_camera(SD, SLUG, "main")) == ["z_ghost", "z_main", "z_old"], \
    "the first camera owns zones that name no camera, or a camera that is gone"
assert ids(d.zones_on_camera(SD, SLUG, "cam3")) == ["z_c3"]
kinds = {z["id"]: z["kind"] for z in d.zones_on_camera(SD, SLUG, "cam2")}
assert kinds == {"z_c2": "area", "l_c2": "line"}
assert len(d._merged(pkg).get("zones")) == 6, "a preview must not write"

# 2. a canvas bound to cam2's zones (and to one that survives)
wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "zr", "type": "zone.ref", "position": {"x": 200, "y": 0},
         "data": {"zone": "z_c2", "zoneGeometry": {"points": []}}},
        {"id": "cnt", "type": "analytics.violations", "position": {"x": 400, "y": 0},
         "data": {"fnId": "dock_count", "name": "Dock count"}},
        {"id": "zs", "type": "output.zone_switch", "position": {"x": 600, "y": 0},
         "data": {"zone": "l_c2", "action": "enable", "metric": ""}},
        {"id": "keep", "type": "zone.ref", "position": {"x": 200, "y": 200},
         "data": {"zone": "z_c3"}},
        {"id": "cnt3", "type": "analytics.violations", "position": {"x": 400, "y": 200},
         "data": {"fnId": "yard_count", "name": "Yard count"}},
        {"id": "sched", "type": "note.comment", "position": {"x": 0, "y": 300},
         "data": {"text": "x", "start": "08:00"}},
        {"id": "pre", "type": "filter.preprocess", "position": {"x": 100, "y": 100},
         "data": {"steps": [{"op": "mask", "zones": ["z_c2", "z_c3"]}]}},
    ], "edges": [
        {"id": "e1", "source": "src", "target": "zr"},
        {"id": "e2", "source": "zr", "target": "cnt"},
        {"id": "e4", "source": "src", "target": "keep"},
        {"id": "e5", "source": "keep", "target": "cnt3"},
        # the switch follows a LIVE rule, so its own zone is what it waits on
        {"id": "e3", "source": "cnt3", "target": "zs"},
    ]}}]})

hit = wf.forget_zones(SLUG, ["z_c2", "l_c2"], dry_run=True)
assert ids(hit) == ["pre", "zr", "zs"], hit
store = wf.load_store(SLUG)
assert next(n for n in store["workflows"][0]["graph"]["nodes"]
            if n["id"] == "zr")["data"]["zone"] == "z_c2", "a dry run must not write"

# 3. remove cam2: camera and its two zones in ONE write, others untouched
cams, gone = d.remove_camera(SD, SLUG, "cam2", with_zones=True)
assert [c["id"] for c in cams] == ["main", "cam3"]
assert ids(gone) == ["l_c2", "z_c2"]
left = {z["id"]: z for z in d._merged(pkg).get("zones")}
assert sorted(left) == ["z_c3", "z_ghost", "z_main", "z_old"], sorted(left)
assert "camera" not in left["z_old"], "a surviving zone is written back as it was"

# 4. the canvas: bindings blank, nodes kept, unrelated values untouched
hit = wf.forget_zones(SLUG, [z["id"] for z in gone])
assert ids(hit) == ["pre", "zr", "zs"]
nodes = {n["id"]: n for n in wf.load_store(SLUG)["workflows"][0]["graph"]["nodes"]}
assert len(nodes) == 8, "no node may be deleted"
assert nodes["zr"]["data"]["zone"] == "" and "zoneGeometry" not in nodes["zr"]["data"]
assert nodes["zs"]["data"]["zone"] == ""
assert nodes["keep"]["data"]["zone"] == "z_c3"
assert nodes["sched"]["data"]["start"] == "08:00"
assert nodes["pre"]["data"]["steps"][0]["zones"] == ["z_c3"]
assert wf.forget_zones(SLUG, ["z_c2"]) == [], "a second pass finds nothing"

# 5. ...and that canvas still COMPILES: the unbound chain waits, no error
zone_list = d._zone_list(d._merged(pkg))
graph = wf.load_store(SLUG)["workflows"][0]["graph"]
graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in ("pre", "sched")]
out = wf.compile_graph(wf.clean_graph(graph), zone_list)
print("errors:", out["errors"])
print("warnings:", [w["message"] for w in out["warnings"]])
assert not out["errors"], out["errors"]
msgs = " | ".join(w["message"] for w in out["warnings"])
assert "waiting for a zone - pick one on this Zone node" in msgs
assert "pick which zone this Zone Switch flips" in msgs

# 6. removing the FIRST camera takes the unbound zones with it
cams, gone = d.remove_camera(SD, SLUG, "main", with_zones=True)
assert ids(gone) == ["z_ghost", "z_main", "z_old"], gone
assert [z["id"] for z in d._merged(pkg).get("zones")] == ["z_c3"]
assert d.zones_on_camera(SD, SLUG, "cam3") == [{"id": "z_c3", "name": "Yard",
                                                "kind": "area"}]
try:
    d.remove_camera(SD, SLUG, "nope")
    raise AssertionError("removed a camera that does not exist")
except ValueError:
    pass

# 7. the route: dry run, both capabilities, removed zones in the answer
#    (read as text: the route module needs fastapi, which a dev box may lack)
with open(os.path.join(ROOT, "core", "routes", "models.py"), encoding="utf-8") as f:
    routes_src = f.read()
start = routes_src.index("async def detector_remove_camera(")
src = routes_src[start:routes_src.index("\n@router.", start)]
assert 'body.get("dry_run")' in src
assert '"detection.camera.remove"' in src and '"detection.zone.delete"' in src, \
    "removing a camera must not be a way around the zone-delete capability"
assert '"zones_removed"' in src and "forget_zones" in src
assert "apply_zone_actions" in src, "the zones' compiled rules must go too"
assert "_cams_out(request, cams)" in src, "camera passwords must stay masked"

# 8. the page names the zones before it removes anything
with open(os.path.join(ROOT, "frontend", "src", "pages", "Detection.jsx"),
          encoding="utf-8") as f:
    page = f.read()
assert "dry_run: true" in page
assert "No zones are drawn on it." in page
assert "Workflow nodes that used them will ask for a new zone." in page
assert "Camera and ${removed.size} zone" in page
print("OK camera removal takes its zones")
