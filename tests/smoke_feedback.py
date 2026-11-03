# The user's exact "hallucinating total" graph: Formula(add) reads the very
# variable its output is stored into. Must compile WITH a warning naming the
# Running Total fix; the legit Running Total in the same graph stays clean.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_msfi1fdf", "name": "Zone 1", "kind": "area",
          "enabled": True}]

g = wf.clean_graph({"nodes": [
    {"id": "m1", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
     "data": {"classes": ["person"]}},
    {"id": "z1", "type": "zone.ref", "position": {"x": 2, "y": 0},
     "data": {"zone": "zone_msfi1fdf"}},
    {"id": "viol", "type": "analytics.occupancy", "position": {"x": 3, "y": 0},
     "data": {"fnId": "z_zone_msfi1fdf_zoneviol", "name": "Zone 1 violations now"}},
    # the trap: add(a=violations_now, b=Variable reading total) -> store total
    {"id": "reader", "type": "variable.ref", "position": {"x": 3, "y": 1},
     "data": {"key": "fn_total_violation_value"}},
    {"id": "add", "type": "math.arith", "position": {"x": 4, "y": 0},
     "data": {"fnId": "formula_ye02", "name": "Add", "op": "add"}},
    {"id": "store", "type": "variable.ref", "position": {"x": 5, "y": 0},
     "data": {"name": "total_violation", "key": ""}},
    # the RIGHT way, present in the same graph
    {"id": "tot", "type": "analytics.total", "position": {"x": 4, "y": 2},
     "data": {"fnId": "running_total_crc4", "name": "Running total",
              "mode": "count", "reset": "never"}},
], "edges": [
    {"id": "e1", "source": "m1", "target": "f1"},
    {"id": "e2", "source": "f1", "target": "z1"},
    {"id": "e3", "source": "z1", "target": "viol"},
    {"id": "e4", "source": "viol", "target": "add", "targetHandle": "a"},
    {"id": "e5", "source": "reader", "target": "add", "targetHandle": "b"},
    {"id": "e6", "source": "add", "target": "store"},
    {"id": "e7", "source": "viol", "target": "tot"},
]})

c = wf.compile_graph(g, ZONES)
assert not c["errors"], c["errors"]
warns = {w["node"]: w["message"] for w in c["warnings"]}
print("warnings:", warns)
assert "store" in warns, "the frame-rate feedback loop went unwarned"
assert "Running Total" in warns["store"]
assert "30x" in warns["store"] or "30x a second" in warns["store"]
print("== the trap is named, with the fix:", warns["store"][:80], "…")

# the legit Running Total is untouched and correct
tot = next(f for f in c["functions"] if f["id"] == "running_total_crc4")
assert tot["type"] == "total"
assert tot["params"]["key"] == "fn_z_zone_msfi1fdf_zoneviol_count"
assert "tot" not in warns
print("== the Running Total in the same graph compiles clean")

# a NON-feedback store (total -> variable) warns nothing
g2 = wf.clean_graph({"nodes": [n for n in g["nodes"] if n["id"] not in
                               ("reader", "add", "store")] + [
    {"id": "store2", "type": "variable.ref", "position": {"x": 5, "y": 2},
     "data": {"name": "total_violation", "key": ""}},
], "edges": [e for e in g["edges"] if e["id"] in ("e1", "e2", "e3", "e7")] + [
    {"id": "e8", "source": "tot", "target": "store2"},
]})
c2 = wf.compile_graph(g2, ZONES)
assert not c2["errors"], c2["errors"]
assert not c2["warnings"], c2["warnings"]
stored = next(f for f in c2["functions"] if f["id"] == "total_violation")
print("== the correct wiring compiles silent:",
      stored["params"]["expr"], "-> fn_total_violation_value")
print("ALL OK")
