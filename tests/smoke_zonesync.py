# Zone action -> canvas mirror: does the chain appear, wire to the Detection
# Stream, and compile back to the SAME function ids?
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

SLUG = "smoketest"

ZONES = [
    {"id": "zone_a", "name": "Zone 1", "kind": "area", "enabled": True},
    {"id": "line_b", "name": "Belt line", "kind": "line", "enabled": True},
]

# What apply_zone_actions would have produced for: a per-class count on an
# area, a crossing count on a line, and a violation rule with an alert.
ZONE_FNS = [
    {"id": "z_zone_a_a1_person_count", "name": "Zone 1 person",
     "type": "zone_count", "enabled": True,
     "params": {"zone": "zone_a", "classes": ["person"]}},
    {"id": "z_line_b_a2_count", "name": "Belt line crossings",
     "type": "line_count", "enabled": True,
     "params": {"line": "line_b"}},
    {"id": "z_zone_a_a3_viol", "name": "Zone 1 violations",
     "type": "zone_unique", "enabled": True,
     "params": {"zone": "zone_a", "min_present": 2, "classes": ["person"]},
     "trigger": {"metric": "new", "op": ">", "value": 0, "severity": "warn",
                 "message": "too many", "cooldown_s": 30}},
]

# A canvas that already exists, with one node the operator drew.
wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION,
              "nodes": [{"id": "mine1", "type": "note.comment",
                         "position": {"x": 10, "y": 10},
                         "data": {"text": "my note"}}],
              "edges": []}}]})

changed = wf.sync_zone_actions(SLUG, ZONE_FNS, ZONES)
print("== sync changed:", changed)

store = wf.load_store(SLUG)
graph = store["workflows"][0]["graph"]
by_id = {n["id"]: n for n in graph["nodes"]}
print("== nodes:", len(graph["nodes"]), "edges:", len(graph["edges"]))
for n in graph["nodes"]:
    print(f"   {n['id']:22s} {n['type']:22s} {n['data'].get('fnId') or n['data'].get('zone') or ''}")

assert "mine1" in by_id, "the operator's own node was destroyed"
srcs = [n for n in graph["nodes"] if n["type"] == "source.detection"]
assert len(srcs) == 1, f"expected exactly one Detection Stream, got {len(srcs)}"
src = srcs[0]["id"]

# every mirrored analytics node must trace back to the Detection Stream
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


for fn in ZONE_FNS:
    an = next(n for n in graph["nodes"]
              if n["data"].get("fnId") == fn["id"])
    assert reaches(src, an["id"]), f"{fn['id']} is not wired to the Detection Stream"
print("== every mirrored rule is wired to the Detection Stream")

# the violation node kept its alert and its limit
viol = next(n for n in graph["nodes"]
            if n["data"].get("fnId") == "z_zone_a_a3_viol")
assert viol["data"]["limit"] == 2, viol["data"]
assert viol["data"]["trigger"]["message"] == "too many"
print("== alert + limit carried onto the node")

# COMPILES BACK to the same ids -> no duplicate, no orphan
compiled = wf.compile_store(store, ZONES)
print("== compile errors:", compiled["errors"])
got = sorted(f["id"] for f in compiled["functions"])
want = sorted(f["id"] for f in ZONE_FNS)
print("== compiled ids:", got)
assert got == want, f"{got} != {want}"
assert not compiled["errors"], compiled["errors"]
print("== compiles back to the SAME function ids")

# idempotent: syncing twice changes nothing
again = wf.sync_zone_actions(SLUG, ZONE_FNS, ZONES)
print("== second sync changed:", again, "(want False)")
assert again is False

# Removing an action KEEPS its chain (nothing drawn is ever deleted) but
# switches it off and cuts it from the Detection Stream.
wf.sync_zone_actions(SLUG, ZONE_FNS[:1], ZONES)
g2 = wf.load_store(SLUG)["workflows"][0]["graph"]
by_fn = {n["data"].get("fnId"): n for n in g2["nodes"] if n["data"].get("fnId")}
assert "z_line_b_a2_count" in by_fn, "the removed action's chain was deleted"
assert by_fn["z_line_b_a2_count"]["data"].get("disabled") is True
assert by_fn["z_zone_a_a1_person_count"]["data"].get("disabled") is not True
assert any(n["id"] == "mine1" for n in g2["nodes"])

adj2 = {}
for e in g2["edges"]:
    adj2.setdefault(e["source"], []).append(e["target"])
src2 = next(n["id"] for n in g2["nodes"] if n["type"] == "source.detection")


def reaches2(a, b):
    seen, stack = set(), [a]
    while stack:
        cur = stack.pop()
        if cur == b:
            return True
        for nxt in adj2.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


assert not reaches2(src2, by_fn["z_line_b_a2_count"]["id"]), \
    "the removed action is still fed by the Detection Stream"
assert reaches2(src2, by_fn["z_zone_a_a1_person_count"]["id"])
print("== a removed action keeps its chain: off, unwired, operator's node safe")

# -- deletions are remembered -------------------------------------------------
# The operator replaces a mirrored chain with their own build and DELETES the
# mirror's nodes. A zone save must not resurrect them -- that is the ledger's
# whole job. Unticking the action and ticking it again is the deliberate way
# to have the chain drawn fresh.
SLUG2 = "smokedel"
wf.save_store(SLUG2, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [], "edges": []}}]})
assert wf.sync_zone_actions(SLUG2, ZONE_FNS[:1], ZONES) is True
g = wf.load_store(SLUG2)["workflows"][0]["graph"]
mirrored = [n["id"] for n in g["nodes"] if n["id"].startswith(wf.ZONE_NODE_PREFIX)
            and n["type"] != "source.detection"]
assert mirrored, g["nodes"]

# The operator deletes every mirrored node (saving the canvas without them).
store = wf.load_store(SLUG2)
w2 = store["workflows"][0]
w2["graph"]["nodes"] = [n for n in w2["graph"]["nodes"]
                        if n["id"] not in mirrored]
w2["graph"]["edges"] = [e for e in w2["graph"]["edges"]
                        if e["source"] not in mirrored
                        and e["target"] not in mirrored]
wf.save_store(SLUG2, store)

# The next zone save (same actions) must NOT bring them back.
wf.sync_zone_actions(SLUG2, ZONE_FNS[:1], ZONES)
g = wf.load_store(SLUG2)["workflows"][0]["graph"]
back = [n["id"] for n in g["nodes"] if n["id"] in mirrored]
assert not back, f"a zone save resurrected deleted nodes: {back}"
print("== deleted mirror nodes stay deleted through a zone save")

# Untick (rule gone), then tick again: the chain is drawn fresh on purpose.
wf.sync_zone_actions(SLUG2, [], ZONES)
wf.sync_zone_actions(SLUG2, ZONE_FNS[:1], ZONES)
g = wf.load_store(SLUG2)["workflows"][0]["graph"]
back = [n["id"] for n in g["nodes"] if n["id"] in mirrored]
assert back, "unticking and re-ticking did not redraw the chain"
print("== untick + re-tick is what brings the chain back")

# -- what the operator set ON a mirrored node survives a zone save ------------
# The PID tick, the palette exposure, the alert's box colour: canvas
# decisions the Zones page has no field for. The redraw rebuilt data from
# the zone action alone, so every zone save silently unticked them.
SLUG3 = "smokekeep"
wf.save_store(SLUG3, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [], "edges": []}}]})
wf.sync_zone_actions(SLUG3, ZONE_FNS[:1], ZONES)
store = wf.load_store(SLUG3)
g = store["workflows"][0]["graph"]
counter = next(n for n in g["nodes"]
               if n["id"].startswith(wf.ZONE_NODE_PREFIX)
               and n["type"].startswith("analytics."))
counter["data"]["OID"] = True                      # the operator ticks OID
wf.save_store(SLUG3, store)
wf.sync_zone_actions(SLUG3, ZONE_FNS[:1], ZONES)   # the next zone save
g = wf.load_store(SLUG3)["workflows"][0]["graph"]
counter = next(n for n in g["nodes"] if n["id"] == counter["id"])
assert counter["data"].get("OID") is True, \
    "a zone save unticked the operator's OID"
print("== the OID tick survives a zone save")
print("ALL OK")
