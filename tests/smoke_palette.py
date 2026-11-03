# The palette's shape: every Function node is filed under a sub-group, the
# Debug node is offered (and compiles to nothing), and a Variable can be
# told which sides it offers.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                    # noqa: E402

reg = wf.registry_public()
live = {k: v for k, v in reg.items() if not v.get("deprecated")}

# 1. every LIVE Function node is filed under one of the sub-groups, so
#    none of them can land in an unlabelled pile at the bottom of the drawer.
# "Math", "Logic" and "State" are the Math family's own sub-groups: the
# Formula's 34-operation picker became one node per operation, filed under
# three headings of their own rather than swelling "Mathematics" to thirteen
# entries (smoke_math.py).
GROUPS = {"Zones", "Frames", "Mathematics", "Math", "Logic", "State"}
unfiled = [k for k, v in live.items()
           if v.get("category") == "analytics" and v.get("group") not in GROUPS]
assert not unfiled, f"Function nodes with no sub-group: {unfiled}"
counts = {}
for v in live.values():
    if v.get("category") == "analytics":
        counts[v["group"]] = counts.get(v["group"], 0) + 1
assert set(counts) == GROUPS, counts
print("== every Function node is filed:", counts)

# 2. Debug is BACK in the palette, and still compiles to nothing.
#
# This check used to assert the opposite: the node was retired on the grounds
# that the Debug drawer already shows every value at once. It is offered again
# because the two do not answer the same question. The drawer lists what the
# graph PUBLISHES, keyed by stat id -- so reading it means knowing which key
# belongs to the node in front of you, and for a Set Variable (it publishes as
# the variable it writes) or a Telegram (it publishes nothing at all) that key
# is not the node's, or does not exist. The probe answers "what is coming down
# THIS wire", on the canvas, where the wire is.
#
# The half that must NOT change is below it: the probe still compiles to
# nothing, so bringing it back cannot alter what any graph runs.
assert not reg["common.debug"].get("deprecated"), \
    "the Debug node is not being offered - the probe is the canvas's only " \
    "answer to 'did that output actually fire?'"
assert reg["common.debug"]["data"] == {"metric": ""}, \
    "the probe's hand-typed fallback key is gone - canvases in the field " \
    "have one saved"
graph = {"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "d1", "type": "common.debug", "position": {"x": 1, "y": 0}, "data": {}},
], "edges": [{"id": "e1", "source": "m1", "target": "d1"}]}
out = wf.compile_graph(graph, [], ["person"], ["person"])
assert not out["errors"], out["errors"]
print("== Debug is back in the palette; a canvas that has one still compiles")

# 3. a Variable can be told what it is FOR, and "read" wins over a stray wire.
assert reg["variable.ref"]["io_modes"] == ["auto", "in", "out", "both"]
assert reg["variable.ref"]["data"]["io"] == "auto"


def var_graph(io):
    return {"nodes": [
        {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "c1", "type": "analytics.frame", "position": {"x": 2, "y": 0},
         "data": {"fnId": "src", "name": "Src", "mode": "realtime"}},
        {"id": "v1", "type": "variable.ref", "position": {"x": 3, "y": 0},
         "data": {"name": "held", "io": io}},
    ], "edges": [
        {"id": "e1", "source": "m1", "target": "f1"},
        {"id": "e2", "source": "f1", "target": "c1"},
        {"id": "e3", "source": "c1", "target": "v1", "targetHandle": "in"},
    ]}


# "out" stores what is wired in: a new function publishes it under that name.
out = wf.compile_graph(var_graph("out"), [], ["person"], ["person"])
assert not out["errors"], out["errors"]
assert any(f["id"] == "held" for f in out["functions"]), \
    [f["id"] for f in out["functions"]]
print("== 'store' publishes the wired value under the variable's name")

# "in" is a READER whatever is wired to it -- the operator said what it is for,
# and a stray wire must not silently turn their reader into a store.
out = wf.compile_graph(var_graph("in"), [], ["person"], ["person"])
assert not any(f["id"] == "held" for f in out["functions"]), \
    "an explicit reader was compiled as a store anyway"
print("== 'read' stays a reader even with something wired into it")

# auto keeps the original behaviour, so every canvas built before this means
# exactly what it meant.
out = wf.compile_graph(var_graph("auto"), [], ["person"], ["person"])
assert any(f["id"] == "held" for f in out["functions"]), \
    "'auto' no longer infers a store from the wiring"
print("== 'automatic' still infers a store from the wiring, as it always did")
print("ALL OK")
