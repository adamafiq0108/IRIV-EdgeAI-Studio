# ONE Counter, and the zone port decides where it counts.
#
# The Object Counter counted the frame and the Zone Counter counted an area.
# Same modes, same timing settings, same OID output, same published metrics --
# two palette entries, two inspectors, and an operator choosing between them
# before knowing that the only difference is a wire.
#
# WHERE it counts is read off the wiring now: a zone on the optional port
# narrows it, nothing there counts the whole frame. What this pins is that the
# swap happens in the COMPILER (so the emitted rule is the one the engine
# already runs), and that a saved counter keeps counting exactly what it did.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def wire(src, tgt, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{target_handle or ''}", "source": src,
            "target": tgt, "sourceHandle": None,
            "targetHandle": target_handle}


def graph(mode="realtime", zone=True, extra=None):
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "c", "type": "analytics.counter", "position": {"x": 2, "y": 0},
         "data": {"fnId": "cnt", "name": "Counter", "mode": mode,
                  **(extra or {})}},
    ]
    edges = [wire("src", "f1"), wire("f1", "c")]
    if zone:
        nodes.append({"id": "z", "type": "zone.ref", "position": {"x": 2, "y": 1},
                      "data": {"zone": "bay"}})
        edges.append(wire("z", "c", "zone"))
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def fn_of(out, fid="cnt"):
    return next((f for f in out["functions"] if f["id"] == fid), None)


# -- the zone port picks the rule ---------------------------------------------
for mode, framed, zoned in (("realtime", "frame_count", "zone_count"),
                            ("distinct", "frame_unique", "zone_unique")):
    out = wf.compile_graph(graph(mode, zone=False), ZONES)
    assert not out["errors"], out["errors"]
    fn = fn_of(out)
    assert fn["type"] == framed, (mode, fn["type"])
    assert "zone" not in fn["params"], fn["params"]

    out = wf.compile_graph(graph(mode, zone=True), ZONES)
    assert not out["errors"], out["errors"]
    fn = fn_of(out)
    assert fn["type"] == zoned, (mode, fn["type"])
    assert fn["params"]["zone"] == "bay", fn["params"]
print("== no zone counts the frame, a zone counts the zone -- same node")

# -- and it is the SAME function the two old nodes compiled --------------------
# The whole point of a merge is that nothing downstream notices. A card, a
# history series and a dataset column all read fn_<id>_<metric>, so the rule
# and the id must be identical to what the node it replaces emitted.
old = wf.clean_graph({"nodes": [
    {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
     "data": {"classes": ["person"]}},
    {"id": "c", "type": "analytics.violations", "position": {"x": 2, "y": 0},
     "data": {"fnId": "cnt", "name": "Counter", "mode": "realtime"}},
    {"id": "z", "type": "zone.ref", "position": {"x": 2, "y": 1},
     "data": {"zone": "bay"}}],
    "edges": [wire("src", "f1"), wire("f1", "c"), wire("z", "c", "zone")]})
before = fn_of(wf.compile_graph(old, ZONES))
after = fn_of(wf.compile_graph(graph("realtime", zone=True), ZONES))
assert before["type"] == after["type"], (before, after)
assert before["params"] == after["params"], (before["params"], after["params"])
print("== byte-identical to the Zone Counter it replaces")

# -- timing settings still reach the rule --------------------------------------
out = wf.compile_graph(
    graph("distinct", zone=True, extra={"enter_after_s": 3, "leave_after_s": 2}),
    ZONES)
assert not out["errors"], out["errors"]
p = fn_of(out)["params"]
assert p["enter_after_s"] == 3.0 and p["leave_after_s"] == 2.0, p
print("== the Advanced timing reaches the rule on either surface")

# -- the metrics follow the mode, not the surface ------------------------------
spec = wf.REGISTRY["analytics.counter"]
assert wf.node_metrics({"type": "analytics.counter", "data": {"mode": "realtime"}},
                       spec) == ["count"]
assert wf.node_metrics({"type": "analytics.counter", "data": {"mode": "distinct"}},
                       spec)[:2] == ["total", "new"]
# A distinct count tallies per day on disk on BOTH surfaces (functions.OUTPUTS
# lists them for zone_unique and frame_unique alike), so a card's Period
# picker answers whether or not a zone is wired.
assert "today" in wf.node_metrics(
    {"type": "analytics.counter", "data": {"mode": "distinct"}}, spec)
print("== metrics follow the mode; the per-day tally is there either way")

# -- no limit field, and the node that used one is left alone ------------------
assert "limit" not in spec["data"], spec["data"]
assert wf.REGISTRY["analytics.frame"]["deprecated"]
assert wf.REGISTRY["analytics.violations"]["deprecated"]

g = {"version": 5, "nodes": [
    {"id": "a", "type": "analytics.frame", "position": {"x": 0, "y": 0},
     "data": {"fnId": "f1", "mode": "distinct", "OID": True}},
    {"id": "b", "type": "analytics.violations", "position": {"x": 0, "y": 0},
     "data": {"fnId": "f2", "mode": "realtime", "limit": 0}},
    # A limit is a CONDITION baked into a counter. The merged node has no such
    # field, so rewriting this one would change what it counts -- it stays.
    {"id": "c", "type": "analytics.violations", "position": {"x": 0, "y": 0},
     "data": {"fnId": "f3", "mode": "distinct", "limit": 4}}],
    "edges": []}
assert wf.migrate_counter_merge(g) is True
types = {n["id"]: n["type"] for n in g["nodes"]}
assert types == {"a": "analytics.counter", "b": "analytics.counter",
                 "c": "analytics.violations"}, types
data = {n["id"]: n["data"] for n in g["nodes"]}
assert data["a"]["mode"] == "distinct" and data["a"]["OID"] is True, data["a"]
assert "limit" not in data["b"], data["b"]
assert data["c"]["limit"] == 4, data["c"]
assert g["version"] == 6, g["version"]
print("== both counters migrate; one carrying a limit keeps its own node")

# -- the whole-frame counters still refuse a zone ------------------------------
# They have no optional port: a zone wired to one would be silently ignored,
# which reads as a broken node rather than a wrong wire.
for t in ("analytics.frame", "analytics.frame_count"):
    ok, _why = wf.edge_allowed("zone.ref", t)
    assert not ok, t
ok, why = wf.edge_allowed("zone.ref", "analytics.counter")
assert ok, why
print("== the deprecated frame counters still refuse a zone; the Counter takes one")
print("ALL OK")
