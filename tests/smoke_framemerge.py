# One "In Frame" node with a mode, not two nodes; and the palette offers one
# obvious answer for time (Time in Zone) and one for A-to-B (Travel Time).
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402


def graph(node_type, mode=None, card_metric=None):
    data = {"fnId": "frames", "name": "In frame"}
    if mode is not None:
        data["mode"] = mode
    card = {"name": "Card"}
    if card_metric:
        card["metric"] = card_metric
    return {"nodes": [
        {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "n1", "type": node_type, "position": {"x": 2, "y": 0}, "data": data},
        {"id": "card", "type": "dashboard.counter", "position": {"x": 3, "y": 0},
         "data": card},
    ], "edges": [
        {"id": "e1", "source": "m1", "target": "f1"},
        {"id": "e2", "source": "f1", "target": "n1"},
        {"id": "e3", "source": "n1", "target": "card", "targetHandle": "in"},
    ]}


def compile_it(g):
    out = wf.compile_graph(g, [], ["person"], ["person"])
    assert not out["errors"], out["errors"]
    fn = next(f for f in out["functions"] if f["id"] == "frames")
    card = next(w for w in out["widgets"] if w["id"].startswith("wf_card"))
    return fn["type"], card["key"]

# 1. one node, two modes, two different compiled rules
assert compile_it(graph("analytics.frame", "realtime")) == ("frame_count", "fn_frames_count")
assert compile_it(graph("analytics.frame", "distinct")) == ("frame_unique", "fn_frames_total")
print("== one In Frame node compiles to either rule, by its mode")

# 2. no mode set, or a nonsense one, falls back to the first mode
assert compile_it(graph("analytics.frame"))[0] == "frame_count"
assert compile_it(graph("analytics.frame", "nonsense"))[0] == "frame_count"
print("== an unset or unknown mode falls back to the first, never to an error")

# 3. the period metrics only exist in `total` mode, and a card can bind them
assert compile_it(graph("analytics.frame", "distinct", "today"))[1] == "fn_frames_today"
print("== a card can bind the period metrics the 'total' mode publishes")

# 4. the palette shows ONE entry; the old pair still compiles but is hidden
reg = wf.registry_public()
assert "analytics.frame" in reg
# TWO modes, because there are two measurements. daily/weekly/monthly used to
# sit beside them, but they were the same rule publishing a different number
# first -- a question about what to SHOW, which now lives on the card's Period
# setting. The rule tallies every period either way (case 3 above).
assert set(reg["analytics.frame"]["modes"]) == {"realtime", "distinct"}
for old in ("analytics.frame_count", "analytics.frame_total"):
    assert reg[old].get("deprecated"), f"{old} is still offered in the palette"
assert compile_it(graph("analytics.frame_count"))[0] == "frame_count"
assert compile_it(graph("analytics.frame_total"))[0] == "frame_unique"
print("== the old pair is hidden from the palette but still compiles")

# 5. a canvas drawn with the old pair is rewritten in place -- same ids/wires
g = graph("analytics.frame_total")
before = [(n["id"], n["position"]) for n in g["nodes"]]
assert wf.migrate_merged_nodes(g) is True
node = next(n for n in g["nodes"] if n["id"] == "n1")
assert node["type"] == "analytics.frame" and node["data"]["mode"] == "distinct", node
assert node["data"]["fnId"] == "frames", "the function id changed -- history would break"
assert [(n["id"], n["position"]) for n in g["nodes"]] == before
assert compile_it(g) == ("frame_unique", "fn_frames_total")
print("== an old canvas migrates in place: same ids, wires, positions, output")
assert wf.migrate_merged_nodes(g) is False, "migration is not idempotent"
print("== and migrating again is a no-op")

# 6. time questions have one obvious answer each
assert reg["analytics.cycle_time"].get("deprecated"), \
    "Cycle Time (A -> B) still competes with Travel Time in the palette"
assert not reg["analytics.travel"].get("deprecated")
assert not reg["analytics.dwell"].get("deprecated")
assert "live_s" in reg["analytics.dwell"]["metrics"], \
    "Time in Zone still cannot answer 'how long has this one been in there'"
print("== A->B is Travel Time; time spent somewhere is Time in Zone, which now")
print("   offers live_s:", reg["analytics.dwell"]["metrics"])

# 7. every duplicate pair is folded, and each old node still migrates safely
FOLDED = {"analytics.visitors": ("analytics.violations", "limit"),
          "analytics.speed": ("analytics.travel", "mode"),
          "dashboard.chart": ("dashboard.graph", "kind")}
#: Fold targets that have since been folded ONWARD. A merge does not undo the
#: one before it: Unique Visitors still becomes the Zone Counter here, and the
#: counter migration then carries it into the merged Counter (smoke_counter.py).
#: Each step owns one version, so each still runs on its own.
FOLDED_ON = {"analytics.violations": "analytics.counter"}
for old, (new, key) in FOLDED.items():
    assert reg[old].get("deprecated"), f"{old} still competes in the palette"
    if new in FOLDED_ON:
        assert reg[new].get("deprecated"), new
        assert not reg[FOLDED_ON[new]].get("deprecated"), FOLDED_ON[new]
    else:
        assert not reg[new].get("deprecated"), new
    g = {"nodes": [{"id": "x", "type": old, "position": {"x": 1, "y": 2},
                    "data": {"fnId": "keepme", "name": "Mine"}}], "edges": []}
    assert wf.migrate_merged_nodes(g) is True, old
    node = g["nodes"][0]
    assert node["type"] == new, (old, node["type"])
    assert node["data"]["fnId"] == "keepme", "the function id changed"
    assert key in node["data"], (old, node["data"])
    assert node["position"] == {"x": 1, "y": 2}
print("== Unique Visitors, Speed and Chart fold into the node they duplicated")

# an operator's own setting survives the fold rather than being overwritten
g = {"nodes": [{"id": "x", "type": "dashboard.chart", "position": {},
                "data": {"kind": "bar", "name": "Mine"}}], "edges": []}
wf.migrate_merged_nodes(g)
assert g["nodes"][0]["data"]["kind"] == "bar", g["nodes"][0]["data"]
print("== and a setting the operator chose is not overwritten by the fold")
print("ALL OK")
