# The Zone Counter absorbs Zone Functionality, and the Switch grows shapes.
#
# Two merges with the same discipline as smoke_cardmerge:
#
# * analytics.occupancy (Zone Functionality) was the Zone Counter answering
#   "how many NOW" -- the same question the counter's distinct mode answers
#   over a different span, exactly the split the Frame Counter models as a
#   mode. It is a mode now; the old node migrates in place and compiles to
#   the SAME zone_count function under the SAME fn id.
#
# * logic.switch gains `ctype` shapes -- compare / switch / range / boolean /
#   multi(AND/OR) -- with ports that follow the settings. A graph saved
#   before ctype existed has none, which must mean the old behaviour with
#   the old compiled ids, or every wired output re-binds.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def compile_graph(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.migrate_merged_nodes(g)
    wf.ensure_fn_ids(g)
    return wf.compile_graph(g, ZONES)


def zone_chain(counter_type, data):
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "c", "type": counter_type, "position": {"x": 2, "y": 0}, "data": data},
    ]
    edges = [{"id": "e1", "source": "s", "target": "z"},
             {"id": "e2", "source": "z", "target": "c"}]
    return nodes, edges


# 1. AN OLD OCCUPANCY NODE still compiles to the same function. The migration
#    rewrites the type and pins mode realtime; fnId is untouched, so the
#    compiled doc id and its fn_<id>_count key are byte-identical.
c = compile_graph(*zone_chain("analytics.occupancy", {"fnId": "bay", "name": "Bay"}))
assert not c["errors"], c["errors"]
fn = next(f for f in c["functions"] if f["id"] == "bay")
assert fn["type"] == "zone_count", fn["type"]
assert "min_present" not in fn["params"], fn["params"]
print("== an old Zone Functionality node compiles to the same zone_count under the same id")

# 2. AN OLD ZONE COUNTER (saved before modes existed, so no mode key) still
#    counts distinct. The node mirrors the Frame Counter now -- realtime
#    first, realtime the fallback -- so migrate_merged_nodes STAMPS
#    mode="distinct" onto any mode-less counter; without the stamp the new
#    fallback would silently change what every old graph counts.
c = compile_graph(*zone_chain("analytics.violations",
                              {"fnId": "keep", "name": "Keepout", "limit": 2}))
assert not c["errors"], c["errors"]
fn = next(f for f in c["functions"] if f["id"] == "keep")
assert fn["type"] == "zone_unique", fn["type"]
assert fn["params"].get("min_present") == 2, fn["params"]
print("== an old Zone Counter without a mode is stamped distinct, limit intact")

# ...and the stamp is REAL data, not a compile-time guess: after one load the
# node carries the mode explicitly.
g = wf.clean_graph({"nodes": zone_chain("analytics.violations",
                                        {"fnId": "keep2"})[0],
                    "edges": zone_chain("analytics.violations",
                                        {"fnId": "keep2"})[1]})
wf.migrate_merged_nodes(g)
stamped = next(n for n in g["nodes"] if n["id"] == "c")
assert stamped["data"]["mode"] == "distinct", stamped["data"]
print("== the migration writes the mode onto the node itself")

# 3. the merged node's realtime mode is occupancy: zone_count, no min_present
c = compile_graph(*zone_chain("analytics.violations",
                              {"fnId": "occ", "name": "Occ", "mode": "realtime",
                               "limit": 5}))
assert not c["errors"], c["errors"]
fn = next(f for f in c["functions"] if f["id"] == "occ")
assert fn["type"] == "zone_count" and "min_present" not in fn["params"], fn
print("== realtime mode compiles to zone_count and drops the limit knob")

# 4. the palette offers ONE zone counter: occupancy loads but is deprecated
reg = wf.registry_public()
assert reg["analytics.occupancy"].get("deprecated"), "occupancy still offered"
# The Zone Counter itself has since been folded into the one Counter, whose
# zone port is optional (smoke_counter.py). It still loads and still compiles
# -- a node carrying a `limit` deliberately stays on it -- and it keeps the
# two modes, which is what this checks.
assert reg["analytics.violations"].get("deprecated")
assert set(reg["analytics.violations"]["modes"]) == {"distinct", "realtime"}
assert set(reg["analytics.counter"]["modes"]) == {"distinct", "realtime"}
assert not reg["analytics.counter"].get("deprecated")
print("== Zone Functionality is off the palette; the Counter has its modes")


# -- the Switch shapes --------------------------------------------------------

def switch_graph(data):
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "f", "type": "analytics.frame", "position": {"x": 1, "y": 0},
         "data": {"fnId": "inframe", "name": "In frame", "mode": "realtime"}},
        {"id": "sw", "type": "logic.switch", "position": {"x": 2, "y": 0}, "data": data},
    ]
    edges = [{"id": "e1", "source": "s", "target": "f"},
             {"id": "e2", "source": "f", "target": "sw"}]
    return nodes, edges


def exprs(c, base):
    return {f["id"]: f["params"]["expr"] for f in c["functions"]
            if f["id"].startswith(f"{base}_b")}


# 5. NO ctype (a graph saved before shapes existed) = the old behaviour with
#    the old ids, so everything wired to out0/out1/... stays bound.
c = compile_graph(*switch_graph({"fnId": "route", "name": "Route",
                                 "rules": [{"op": ">", "value": 10},
                                           {"op": ">", "value": 5}],
                                 "otherwise": True}))
assert not c["errors"], c["errors"]
got = exprs(c, "route")
assert set(got) == {"route_b0", "route_b1", "route_b2"}, sorted(got)
assert got["route_b0"] == "(fn_inframe_count > 10)", got["route_b0"]
assert "not" in got["route_b1"] and "> 5" in got["route_b1"], got["route_b1"]
print("== a pre-ctype Switch compiles exactly as before, same ids")

# 6. RANGE: bands compile to closed-open intervals, so adjacent bands tile
#    with no gap and no double-count; an inverted band is a named error.
c = compile_graph(*switch_graph({"fnId": "band", "name": "Bands", "ctype": "range",
                                 "rules": [{"op": "between", "value": 0, "value2": 5},
                                           {"op": "between", "value": 5, "value2": 10}],
                                 "otherwise": False}))
assert not c["errors"], c["errors"]
got = exprs(c, "band")
assert got["band_b0"] == "((fn_inframe_count >= 0) and (fn_inframe_count < 5))", got
print("== range bands compile to closed-open intervals")

c = compile_graph(*switch_graph({"fnId": "bad", "ctype": "range",
                                 "rules": [{"op": "between", "value": 9, "value2": 3}]}))
assert any("above its 'from'" in e["message"] for e in c["errors"]), c["errors"]
print("== an inverted band is a named error, not a rule that never matches")

# 7. BOOLEAN routes on != 0 with an otherwise -- two functions, on and off.
c = compile_graph(*switch_graph({"fnId": "gate", "name": "Gate", "ctype": "boolean",
                                 "rules": [{"op": "!=", "value": 0}],
                                 "otherwise": True}))
assert not c["errors"], c["errors"]
assert set(exprs(c, "gate")) == {"gate_b0", "gate_b1"}
print("== boolean compiles to on/off")

# 8. MULTI: several wired values, one combined test. Each condition names the
#    input port it tests; the pass expression joins them with AND or OR.
def multi_graph(join):
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "f1", "type": "analytics.frame", "position": {"x": 1, "y": 0},
         "data": {"fnId": "a", "name": "A", "mode": "realtime"}},
        {"id": "f2", "type": "analytics.frame", "position": {"x": 1, "y": 1},
         "data": {"fnId": "b", "name": "B", "mode": "realtime"}},
        {"id": "sw", "type": "logic.switch", "position": {"x": 2, "y": 0},
         "data": {"fnId": "both", "name": "Both", "ctype": "multi", "join": join,
                  "rules": [{"input": "in1", "op": ">", "value": 3},
                            {"input": "in2", "op": ">", "value": 7}],
                  "otherwise": True}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "f1"},
             {"id": "e2", "source": "s", "target": "f2"},
             {"id": "e3", "source": "f1", "target": "sw", "targetHandle": "in1"},
             {"id": "e4", "source": "f2", "target": "sw", "targetHandle": "in2"}]
    return nodes, edges


c = compile_graph(*multi_graph("and"))
assert not c["errors"], c["errors"]
got = exprs(c, "both")
assert got["both_b0"] == "(fn_a_count > 3) and (fn_b_count > 7)", got["both_b0"]
assert got["both_b1"] == "not ((fn_a_count > 3) and (fn_b_count > 7))", got["both_b1"]
print("== multi AND joins both wired inputs; otherwise is its negation")

c = compile_graph(*multi_graph("or"))
assert " or " in exprs(c, "both")["both_b0"]
print("== multi OR joins with or")

# ...and a condition pointing at an unwired port is a named error, because a
# test over nothing would read zero for ever and look like a working gate.
nodes, edges = multi_graph("and")
edges = [e for e in edges if e["id"] != "e4"]
c = compile_graph(nodes, edges)
assert any("nothing wired into it" in e["message"] for e in c["errors"]), c["errors"]
print("== a condition on an unwired input is a named error")

# 9. downstream binding by branch handle still works for every shape: an
#    output wired to out1 of a boolean switch binds to the OFF function.
nodes, edges = switch_graph({"fnId": "gate", "name": "Gate", "ctype": "boolean",
                             "rules": [{"op": "!=", "value": 0}], "otherwise": True})
nodes.append({"id": "m", "type": "output.mqtt", "position": {"x": 3, "y": 0},
              "data": {"host": "127.0.0.1", "port": 1883, "topic": "t", "payload": "1"}})
edges.append({"id": "e5", "source": "sw", "sourceHandle": "out1", "target": "m"})
c = compile_graph(nodes, edges)
assert not c["errors"], c["errors"]
out = next(o for o in c["outputs"] if o["type"] == "mqtt")
assert out["function"] == "gate_b1", out
print("== an output on the off port binds to the off branch")

# 10. The Compare and the Switch this file was written against are now the
# DEPRECATED pair -- branch.compare and the four other branch shapes replaced
# them, publishing real ports instead of being resolved backwards
# (smoke_branch.py). What is pinned here is that the old two still LOAD: every
# graph above is written in them, and they all still compile.
reg = wf.registry_public()
assert reg["logic.when"]["deprecated"], reg["logic.when"]
assert reg["logic.switch"]["deprecated"], reg["logic.switch"]
assert reg["branch.compare"]["label"] == "Compare", reg["branch.compare"]["label"]
print("== the old When and Switch still load; Compare is now a branch node")
print("ALL OK")
