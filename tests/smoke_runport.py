# A Math node's first input: where the calculation SITS, not another number.
#
# `when` began as an opt-in gate -- tick "only when a condition holds", get a
# port, wire a condition, and the arithmetic computed while it held. Two
# things were wrong with that. The port existed only if somebody found the
# checkbox, and it took a VALUE and nothing else: wiring a Detection Stream
# into a Math node was refused outright ("works on NUMBERS"), so arithmetic
# had no way to say which flow it belonged to and sat on the canvas
# unattached, next to a pair of link rings that tied nodes together and
# passed nothing.
#
# So the port is always there, is called In, and takes anything. A value
# still gates (nothing saved changes meaning); anything else is sequence
# alone and compiles to nothing.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                        # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def wire(src, tgt, handle=None):
    return {"id": f"e_{src}_{tgt}_{handle or ''}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": handle}


BASE = [{"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 0, "y": 0},
         "data": {"zone": "bay"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 1, "y": 0},
         "data": {"fnId": "bay_now", "name": "Bay", "zone": "bay"}},
        {"id": "cond", "type": "logic.if", "position": {"x": 2, "y": 1},
         "data": {"conds": [{"op": ">", "value": 3}]}}]
BASE_E = [wire("src", "occ", "in"), wire("z", "occ", "zone"),
          wire("occ", "cond")]


def compiled(extra_edges=(), mnode=None):
    node = mnode or {"id": "m", "type": "math.arith", "position": {"x": 3, "y": 0},
                     "data": {"fnId": "sum", "op": "add", "a_value": 2,
                              "b_value": 3}}
    g = wf.clean_graph({"nodes": BASE + [node],
                        "edges": BASE_E + list(extra_edges)})
    return wf.compile_graph(g, ZONES)


def expr_of(c, fid="sum"):
    return next((f["params"]["expr"] for f in c["functions"] if f["id"] == fid),
                None)


# -- the wiring rule: the run port takes what an operand may not --------------
# Every rule the edge check applies asks "is this a NUMBER fit for
# arithmetic". That is the right question for an operand and the wrong one
# here, so the port answers before any of them are asked.
for src in ("source.detection", "filter.class", "zone.ref"):
    ok, why = wf.edge_allowed(src, "math.arith", wf.RUN_HANDLE)
    check(f"{src} may say WHERE a Math node runs", ok, why)
    ok, why = wf.edge_allowed(src, "math.arith", "a")
    check(f"...and still may not be an operand of one", not ok, why)
ok, _ = wf.edge_allowed("analytics.occupancy", "math.arith", wf.RUN_HANDLE)
check("a measured value may gate one", ok)
# The two nodes that take no wire on that side keep refusing it: a Comment is
# a note about the graph, and a Debug probe is a dead end.
for src in ("note.comment", "common.debug"):
    ok, _ = wf.edge_allowed(src, "math.arith", wf.RUN_HANDLE)
    check(f"{src} is still refused", not ok)
# The Formula the family grew out of takes the same wire -- a graph saved
# before the split must behave identically to the node that replaced it.
check("the old Formula takes it too",
      wf.edge_allowed("source.detection", "analytics.formula",
                      wf.RUN_HANDLE)[0])
check("...and the exception reaches arithmetic and nothing else",
      all(wf._takes_run_wire(t) for t in wf.MATH_NODES)
      and wf._takes_run_wire("analytics.formula")
      and not wf._takes_run_wire("analytics.total")
      and not wf._takes_run_wire("analytics.expr"))

# -- sequence compiles to nothing --------------------------------------------
plain = expr_of(compiled())
c = compiled([wire("src", "m", wf.RUN_HANDLE)])
check("a stream wired to In does not fail the graph", not c["errors"],
      repr(c["errors"]))
check("...and changes no arithmetic", expr_of(c) == plain,
      f"{expr_of(c)!r} != {plain!r}")

# -- a value still gates, exactly as the checkbox did -------------------------
c = compiled([wire("occ", "m", wf.RUN_HANDLE)])
check("a value wired to In still gates the calculation",
      not c["errors"] and (expr_of(c) or "").startswith("iff("),
      f"{expr_of(c)!r} {c['errors']!r}")
# Both at once: the stream says where, the value says when. Finding the
# stream first must not stop the compiler looking for the gate.
c = compiled([wire("src", "m", wf.RUN_HANDLE), wire("occ", "m", wf.RUN_HANDLE)])
check("a stream and a value on the same port: still gated",
      not c["errors"] and (expr_of(c) or "").startswith("iff("),
      f"{expr_of(c)!r} {c['errors']!r}")

# -- the node face -------------------------------------------------------------
nodes_src = open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
                 encoding="utf-8").read()
check("the port is always drawn, and called In",
      '? [{ id: "when", label: "In" },' in nodes_src)
check("...the link rings only where one is already wired",
      "linkPorts={isFormula && linkWired}" in nodes_src
      and 'e.targetHandle === "link_in"' in nodes_src)

# -- and the client refuses the same wires the server does --------------------
reg = open(os.path.join(ROOT, "frontend", "src", "workflow", "registry.js"),
           encoding="utf-8").read()
check("the client knows the run port by the same handle",
      'export const RUN_HANDLE = "when";' in reg)
check("...and is told which handle a wire is landing on",
      "edgeAllowed(registry, srcType, tgtType, tgtHandle)" in reg)
page = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
            encoding="utf-8").read()
check("...by the connection check itself",
      "edgeAllowed(registry, src.type, tgt.type,\n                                     conn.targetHandle)" in page)
# The checkbox that used to mint the port is gone: a port that is always
# there needs no switch, and a switch that changes nothing is a lie.
check("the gate checkbox is retired",
      'onChange={(e) => onChange({ gated: e.target.checked })}' not in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
