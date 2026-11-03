# Ticking "Raise an alert" (and save a photo / record a clip) on the Zones
# page must DRAW those on the workflow canvas.
#
# The alert already travelled as data on the measuring node, so the system
# behaved right -- but the canvas showed no Alert, no Snapshot and no Clip,
# so the page that is supposed to show how the system works showed a chain
# that appeared to do nothing.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402

SLUG = "zonealert_demo"
ZONES = [{"id": "z1", "name": "Zone 1", "kind": "area", "enabled": True,
          "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}]

# A canvas must already exist -- sync mirrors onto a stored graph.
wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": "main", "name": "Workflow", "enabled": True,
    "graph": {"version": 1, "nodes": [], "edges": []}}]})

FN = {"id": "z_z1_viol", "name": "Zone 1 violations", "type": "zone_unique",
      "enabled": True,
      "params": {"zone": "z1", "classes": ["person"], "min_present": 0},
      "trigger": {"metric": "new", "op": ">", "value": 0, "severity": "warn",
                  "message": "There is person in the zone", "cooldown_s": 30}}


def sync(captures):
    wf.sync_zone_actions(SLUG, [dict(FN)], ZONES, captures=captures)
    g = wf.load_store(SLUG)["workflows"][0]["graph"]
    return ({n["id"]: n for n in g["nodes"]},
            [(e["source"], e["target"]) for e in g["edges"]])


# 1. alert only
nodes, edges = sync({})
types = {n["type"] for n in nodes.values()}
assert "output.alert" in types, f"no Alert node drawn: {sorted(types)}"
alert = next(n for n in nodes.values() if n["type"] == "output.alert")
an = next(n for n in nodes.values() if n["type"] == "analytics.counter")
assert (an["id"], alert["id"]) in edges, "the Alert is not wired to the rule"
assert alert["data"]["message"] == "There is person in the zone", alert["data"]
assert alert["data"]["metric"] == "new" and alert["data"]["op"] == ">", alert["data"]
print("== ticking 'Raise an alert' draws a wired Alert node carrying its message")

# 2. alert + photo + clip
nodes, edges = sync({"z_z1_viol": ("snapshot", "record")})
types = [n["type"] for n in nodes.values()]
assert types.count("output.snapshot") == 1, types
assert types.count("output.record") == 1, types
an = next(n for n in nodes.values() if n["type"] == "analytics.counter")
for kind in ("snapshot", "record"):
    cap = next(n for n in nodes.values() if n["type"] == f"output.{kind}")
    # Beside the Alert, off the RULE: an output cannot feed another output,
    # and the compiled capture binds to the function, not to the alert node.
    assert (an["id"], cap["id"]) in edges, f"{kind} is not wired to the rule"
print("== 'save a photo' and 'record a clip' are drawn beside the Alert, wired to the rule")

# 3. it COMPILES -- a mirrored chain must be a valid graph, not decoration
graph = wf.load_store(SLUG)["workflows"][0]["graph"]
out = wf.compile_graph(graph, ZONES, ["person"], ["person"])
assert not out["errors"], out["errors"]
kinds = sorted(o["type"] for o in out["outputs"])
assert "snapshot" in kinds and "record" in kinds, kinds
print("== and the drawn chain compiles to real outputs:", kinds)

# 4. re-syncing does not duplicate them, and keeps a position the operator moved
moved = wf.load_store(SLUG)
for n in moved["workflows"][0]["graph"]["nodes"]:
    if n["type"] == "output.alert":
        n["position"] = {"x": 1234, "y": 567}
wf.save_store(SLUG, moved)
nodes, _ = sync({"z_z1_viol": ("snapshot", "record")})
alerts = [n for n in nodes.values() if n["type"] == "output.alert"]
assert len(alerts) == 1, f"re-sync duplicated the Alert: {len(alerts)}"
assert alerts[0]["position"] == {"x": 1234, "y": 567}, alerts[0]["position"]
print("== a re-sync neither duplicates them nor undoes where you dragged them")

# 5. unticking the capture removes those nodes' REASON but never the canvas
#    work: they stay, switched off, like every other orphan.
nodes, _ = sync({})
caps = [n for n in nodes.values()
        if n["type"] in ("output.snapshot", "output.record")]
assert caps, "unticking a capture deleted canvas nodes"
assert all(n["data"].get("disabled") for n in caps), \
    "an orphaned capture node is not marked disabled"
print("== unticking leaves them drawn but disabled -- canvas work is never deleted")
print("ALL OK")
