# The zone's own tick boxes: `violation` must produce a rule (and therefore a
# node), and `enabled` off must cut the wire to the Detection Stream.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fx                     # noqa: E402
from core.services import workflow as wf                      # noqa: E402

SLUG = "flagtest"

ZONES = [
    # violation ticked, enabled
    {"id": "zone_v", "name": "Keepout", "kind": "area", "enabled": True,
     "violation": True, "labels": ["person"], "actions": []},
    # a normal counting zone, but SWITCHED OFF
    {"id": "zone_off", "name": "Paused", "kind": "area", "enabled": False,
     "violation": False, "labels": ["person"],
     "actions": [{"id": "a1", "type": "count", "count_mode": "all"}]},
]

written = {}


class FakeDC:
    PRESENCE_OPS = fx.dc.PRESENCE_OPS

    def _merged(self, pkg):
        return {}

    def _pkg_dir(self, systems_dir, slug):
        return "pkg"

    def _zone_list(self, cfg):
        return ZONES

    def get_outputs(self, systems_dir, slug):
        return []

    def set_outputs(self, systems_dir, slug, outs):
        pass


real_dc = fx.dc
fx.dc = FakeDC()
fx.get_functions = lambda systems_dir, slug: []
fx.set_functions = lambda systems_dir, slug, functions: written.update(
    {f["id"]: f for f in functions})

# an existing canvas so the mirror runs (no store = migration path instead)
wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [], "edges": []}}]})

fx.apply_zone_actions("systems", SLUG)
fx.dc = real_dc

print("== functions written:")
for fid, f in sorted(written.items()):
    print(f"   {fid:28s} {f['type']:12s} enabled={f['enabled']} {f['params']}")

# 1. the violation tick produced a rule -- a LIVE count, not a running total
viol = written.get("z_zone_v_zoneviol")
assert viol, "the zone's violation tick produced no rule"
assert viol["type"] == "zone_count", f"want a live count, got {viol['type']}"
assert "min_present" not in viol["params"], viol["params"]
print("== violation tick compiles to a LIVE count rule")

# 2. the switched-off zone's rule exists but is switched off
off = [f for f in written.values() if f["id"].startswith("z_zone_off")]
assert off, "the paused zone lost its rules entirely"
assert all(not f["enabled"] for f in off), "a paused zone's rule still runs"
print("== a paused zone keeps its rules, switched off")

graph = wf.load_store(SLUG)["workflows"][0]["graph"]
by_id = {n["id"]: n for n in graph["nodes"]}
src = next(n["id"] for n in graph["nodes"] if n["type"] == "source.detection")
adj = {}
for e in graph["edges"]:
    adj.setdefault(e["source"], []).append(e["target"])


def reaches(start, target):
    seen, stack = set(), [start]
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


print("== nodes:")
for n in graph["nodes"]:
    print(f"   {n['id']:22s} {n['type']:22s} off={bool(n['data'].get('disabled'))}")

# 3. the violation rule has a node, wired to the source
vnode = next(n for n in graph["nodes"]
             if n["data"].get("fnId") == "z_zone_v_zoneviol")
assert vnode["type"] == "analytics.counter", vnode["type"]
assert reaches(src, vnode["id"]), "the violation node is not wired to the source"
print("== violation node exists and IS wired to the Detection Stream")

# 4. the paused zone's node exists, is marked off, and is NOT wired to source
onode = next(n for n in graph["nodes"]
             if str(n["data"].get("fnId") or "").startswith("z_zone_off"))
assert onode["data"].get("disabled") is True, "the paused node is not marked off"
assert not reaches(src, onode["id"]), \
    "the paused rule is still wired to the Detection Stream"
print("== paused node is drawn, marked off, and NOT wired to the source")

# 5. and the canvas still compiles cleanly (a disabled node must not error)
compiled = wf.compile_store(wf.load_store(SLUG), ZONES)
print("== compile errors:", compiled["errors"])
assert not compiled["errors"], compiled["errors"]
ids = sorted(f["id"] for f in compiled["functions"])
print("== compiled:", ids)
assert "z_zone_v_zoneviol" in ids
assert not any(i.startswith("z_zone_off") for i in ids), \
    "a paused rule compiled into a running function"
print("ALL OK")
