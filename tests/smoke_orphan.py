# Deleting a rule must NOT delete its canvas nodes -- only its wire to the
# Detection Stream. And a re-sync must not shuffle nodes the operator moved.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

SLUG = "orphantest"
ZONES = [{"id": "zone_a", "name": "Zone 1", "kind": "area", "enabled": True}]

COUNT = {"id": "z_zone_a_a1_count", "name": "Zone 1 count", "type": "zone_count",
         "enabled": True, "params": {"zone": "zone_a", "classes": ["person"]}}
VIOL = {"id": "z_zone_a_zoneviol", "name": "Zone 1 violations",
        "type": "zone_unique", "enabled": True,
        "params": {"zone": "zone_a", "min_present": 0}}

wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [], "edges": []}}]})

wf.sync_zone_actions(SLUG, [COUNT, VIOL], ZONES)
g = wf.load_store(SLUG)["workflows"][0]["graph"]
before = {n["id"] for n in g["nodes"]}
viol_an = next(n["id"] for n in g["nodes"]
               if n["data"].get("fnId") == VIOL["id"])
print("== after first sync:", len(g["nodes"]), "nodes")

# the operator moves the violation node and wires an Alert onto it
for n in g["nodes"]:
    if n["id"] == viol_an:
        n["position"] = {"x": 1234, "y": 567}
g["nodes"].append({"id": "myalert", "type": "output.alert",
                   "position": {"x": 1500, "y": 567},
                   "data": {"severity": "warn", "message": "mine"}})
g["edges"].append({"id": "e_mine", "source": viol_an, "target": "myalert",
                   "sourceHandle": None, "targetHandle": None})
store = wf.load_store(SLUG)
store["workflows"][0]["graph"] = g
wf.save_store(SLUG, store)

# NOW the violation tick is cleared -> only the count rule remains
wf.sync_zone_actions(SLUG, [COUNT], ZONES)
g2 = wf.load_store(SLUG)["workflows"][0]["graph"]
after = {n["id"] for n in g2["nodes"]}
by_id = {n["id"]: n for n in g2["nodes"]}

print("== nodes after unticking violation:")
for n in g2["nodes"]:
    print(f"   {n['id']:22s} {n['type']:20s} off={bool(n['data'].get('disabled'))} "
          f"pos={n['position']['x']:.0f},{n['position']['y']:.0f}")

missing = before - after
assert not missing, f"REGRESSION: nodes were deleted: {missing}"
print("== nothing was deleted")

assert by_id[viol_an]["data"]["disabled"] is True, "orphan not switched off"
print("== the orphaned chain is switched off")

assert by_id[viol_an]["position"] == {"x": 1234, "y": 567}, \
    "the operator's position was reshuffled"
print("== the operator's position survived")

assert any(e["id"] == "e_mine" for e in g2["edges"]), \
    "the operator's own Alert wire was destroyed"
print("== the operator's own Alert wire survived")

src = next(n["id"] for n in g2["nodes"] if n["type"] == "source.detection")
adj = {}
for e in g2["edges"]:
    adj.setdefault(e["source"], []).append(e["target"])


def reaches(a, b):
    seen, stack = set(), [a]
    while stack:
        cur = stack.pop()
        if cur == b:
            return True
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


assert not reaches(src, viol_an), "the orphan is still fed by the source"
print("== the orphan is cut from the Detection Stream")
count_an = next(n["id"] for n in g2["nodes"]
                if n["data"].get("fnId") == COUNT["id"])
assert reaches(src, count_an), "the live rule lost its source wire"
print("== the live rule is still wired")

compiled = wf.compile_store(wf.load_store(SLUG), ZONES)
assert not compiled["errors"], compiled["errors"]
ids = sorted(f["id"] for f in compiled["functions"])
assert ids == [COUNT["id"]], ids
print("== compiles clean, orphan produces no function:", ids)
print("ALL OK")
