# A template is the WHOLE build -- everything except recorded data -- and a
# compiled card is placed by a person, never by the compiler.
#
# 1. snapshot_system carries the zones (minus camera binding), the capture
#    switches and the status-box rows; because the zones keep their ids, the
#    workflow's Zone nodes keep their bindings and the clone runs at once.
# 2. promote/format round-trip: the same sections survive formats.toml --
#    but a system created from a BUILT-IN format gets blank zone bindings
#    and no zones (UAT-102: the format lands on other people's boxes).
# 3. apply_artifacts never AUTO-PLACES a compiled card: a new node's card
#    appears in the "From workflow" picker only; cards already on a page
#    (placed by an operator or a template's authored layout) refresh in place.
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as bs                        # noqa: E402
from core.services import provision, templates                 # noqa: E402
from core.services import workflow as wf                       # noqa: E402

CFG = tempfile.mkdtemp()

# -- 1. the snapshot carries the site-shaped build ----------------------------
SYS = tempfile.mkdtemp()
SLUG = "cloneme"
os.makedirs(os.path.join(SYS, SLUG), exist_ok=True)
io.open(os.path.join(SYS, SLUG, "config.local.toml"), "w", encoding="utf-8").write("""
[[zones]]
id = "zone_a"
name = "Bay"
kind = "area"
camera = "cam_1"
violation = false
enabled = true
points = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]

[capture]
snap = false
record = true

[hud]
model = false
""")
wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "c", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
    ], "edges": [
        {"id": "e1", "source": "s", "target": "z"},
        {"id": "e2", "source": "z", "target": "c"},
    ]}}]})

doc = templates.snapshot_system(SYS, CFG, SLUG, "Clone Me")
assert doc.get("zones") and doc["zones"][0]["id"] == "zone_a", doc.get("zones")
assert "camera" not in doc["zones"][0], "the camera binding travelled"
assert doc["config"]["capture"] == {"snap": False, "record": True}
assert doc["config"]["hud"].get("model") is False, doc["config"]["hud"]
zone_node = next(n for w_ in doc["workflows"]["workflows"]
                 for n in w_["graph"]["nodes"] if n["type"] == "zone.ref")
assert zone_node["data"]["zone"] == "zone_a", \
    "zones travel, so the Zone node's binding must survive"
print("== the snapshot carries zones (no camera), capture, hud, live bindings")

# -- 2. the format round-trip keeps them --------------------------------------
provision.promote_template("clone_me", doc, CFG)
fmt = provision.builtin_formats(CFG)["clone_me"]
assert fmt.get("zones") and fmt["zones"][0]["id"] == "zone_a", fmt.get("zones")
assert fmt.get("settings", {}).get("capture") == {"snap": False, "record": True}
print("== promote -> formats.toml -> read back keeps zones and settings")

# ...but a system created FROM the format does not land the author's zones
# (UAT-102): a built-in format reaches every customer's box, and a polygon
# drawn on the author's picture is wrong on all of them. The Zone node's
# binding goes blank and the compiler says what to pick; the settings land.
land = tempfile.mkdtemp()
os.makedirs(os.path.join(land, "landing"), exist_ok=True)
warnings = templates.apply_format_workflow(land, CFG, "landing", fmt,
                                           key="clone_me")
from core.services import detector_control as dc               # noqa: E402
landed = dc._zone_list(dc._merged(dc._pkg_dir(land, "landing")))
assert not landed, f"the author's zones landed on a new box: {landed}"
assert any("zone" in w for w in warnings), warnings
store = wf.load_store("landing")
zn = next(n for w_ in store["workflows"] for n in w_["graph"]["nodes"]
          if n["type"] == "zone.ref")
assert zn["data"]["zone"] == "", zn
cap = dc.get_capture(land, "landing")
assert cap == {"snap": False, "record": True}, cap
print("== a system created from the format lands settings, not the author's zones")

# -- 3. compiled cards are never auto-placed ----------------------------------
cfg2 = tempfile.mkdtemp()
sys2 = tempfile.mkdtemp()
slug2 = "noplace"
os.makedirs(os.path.join(sys2, slug2), exist_ok=True)
compiled = {"functions": [], "outputs": [], "tables": {},
            "pages": [{"workflow_id": "main", "name": "Main", "widgets": [
                {"id": "wf_n1", "title": "Fresh Card", "kind": "tile",
                 "source": "stat", "key": "fn_a_count", "span": 1}]}],
            "catalog": [], "errors": [], "warnings": []}
wf.apply_artifacts(sys2, cfg2, slug2, compiled)
layout = bs.load(cfg2, slug2)
placed = [w["id"] for p in layout["pages"] for w in p["widgets"]]
assert "wf_n1" not in placed, f"a compiled card auto-placed itself: {placed}"
print("== a brand-new compiled card is NOT auto-placed")

# ...but one an operator already placed refreshes in place
bs.save(cfg2, slug2, {"pages": [
    {"id": "overview", "name": "Overview", "widgets": [
        {"id": "wf_n1", "title": "Old Title", "kind": "tile",
         "source": "stat", "key": "fn_a_count", "span": 1,
         "grid": {"x": 3, "y": 2, "w": 3, "h": 2}}]}]})
wf.apply_artifacts(sys2, cfg2, slug2, compiled)
layout = bs.load(cfg2, slug2)
row = next(w for p in layout["pages"] for w in p["widgets"]
           if w["id"] == "wf_n1")
assert row["title"] == "Fresh Card", row
assert row["grid"] == {"x": 3, "y": 2, "w": 3, "h": 2}, row["grid"]
print("== a placed card refreshes its body and keeps its cell")

# -- every write in the landing path resolves the system's ROOT ---------------
# Created systems live under the STORE root; the repo's systems/ dir is the
# shipped ones. install_tables was called with the raw systems_dir and failed
# with "No such file or directory: .../systems/<slug>/config.local.toml.tmp"
# the moment the two roots differed (the landing toast the operator saw).
SRC = io.open(os.path.join(ROOT, "core", "services", "templates.py"),
              encoding="utf-8").read()
block = SRC[SRC.index("def apply_template("):SRC.index("def apply_format_workflow(")]
assert "install_tables(\n                _root_for(" in block.replace("  ", "  "), \
    "install_tables bypasses _root_for again"
HUB = io.open(os.path.join(ROOT, "core", "routes", "hub.py"),
              encoding="utf-8").read()
assert "created_root = templates_service._root_for(" in HUB, \
    "the create route's dc calls bypass the root resolver"
assert "dc_service.start(created_root" in HUB, HUB.count("dc_service.start")
print("== landing writes and the create route resolve the store root")
print("ALL OK")
