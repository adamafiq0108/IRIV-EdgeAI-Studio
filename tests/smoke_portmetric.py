"""Four ports, four cards, four different numbers.

Reported with a screenshot: a Line Counter's In / Out / Inside / Crossings
ports each dragged into their own Card, and all four cards reading 4. They
were all bound to `fn_<id>_in`. Dragging from a named output port IS the
choice of metric -- it is the only one an operator makes without opening an
inspector -- but the one-wire sinks never looked at the wire: they read the
card's own picker, found it unset (it is "" until somebody opens the
inspector), and fell back to the function's FIRST metric. Charts and verdicts
built from SEVERAL wires had read the handle all along, which is why the bug
only showed on the simple case.

The picker still wins where it has been set: it is the later and more
specific statement, and it is what the Analytics editor writes when a card is
re-aimed from that side.

    python tests/smoke_portmetric.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


ZONES = [{"id": "line1", "name": "Door", "kind": "line", "enabled": True,
          "points": [[0.5, 0.0], [0.5, 1.0]]}]


def graph(cards, counter_data=None):
    """A Line Counter in both-directions mode feeding one card per port."""
    nodes = [
        {"id": "src", "type": "source.detection", "data": {}},
        {"id": "cls", "type": "filter.class", "data": {"classes": ["person"]}},
        {"id": "zn", "type": "zone.ref", "data": {"zone": "line1"}},
        {"id": "cnt", "type": "analytics.entry_exit",
         "data": {"fnId": "line1cnt", "name": "Line counter",
                  "mode": "both", **(counter_data or {})}},
    ]
    edges = [{"id": "e1", "source": "src", "target": "cls"},
             {"id": "e2", "source": "cls", "target": "zn"},
             {"id": "e3", "source": "zn", "target": "cnt"}]
    for i, (handle, data) in enumerate(cards):
        nodes.append({"id": f"card{i}", "type": "dashboard.card", "data": data})
        edges.append({"id": f"c{i}", "source": "cnt", "target": f"card{i}",
                      "sourceHandle": handle})
    return {"version": wf.SCHEMA_VERSION, "nodes": nodes, "edges": edges}


PORTS = [("in", {"name": "In"}), ("out", {"name": "Out"}),
         ("net", {"name": "Inside"}), ("cross", {"name": "Crossing"})]

out = wf.compile_graph(graph(PORTS), zones=ZONES)
check("the graph compiles", not out["errors"], out["errors"])
keys = {w["title"]: w.get("key") for w in out["widgets"]}
check("each card reads the port it was dragged from",
      keys == {"In": "fn_line1cnt_in", "Out": "fn_line1cnt_out",
               "Inside": "fn_line1cnt_net", "Crossing": "fn_line1cnt_cross"},
      f"got {keys} -- four ports, four labels, one number is the bug")

# The engine must publish every one of them, or the keys above are names for
# nothing. (in/out/net/cross: `net` is what the node calls Inside.)
from core.services import functions as fx                      # noqa: E402
check("...and the counter publishes all four",
      set(fx.OUTPUTS["line_count"]) >= {"in", "out", "net", "cross"},
      fx.OUTPUTS["line_count"])

# -- the picker still wins where it was set ----------------------------------
out2 = wf.compile_graph(graph([("in", {"name": "Set", "metric": "out"})]),
                        zones=ZONES)
check("a card whose picker names a metric keeps it",
      [w.get("key") for w in out2["widgets"]] == ["fn_line1cnt_out"],
      "the inspector is the later and more specific statement")

# -- a plain wire is unchanged -----------------------------------------------
out3 = wf.compile_graph(graph([(None, {"name": "Plain"})]), zones=ZONES)
check("a wire from the plain port still reads the first metric",
      [w.get("key") for w in out3["widgets"]] == ["fn_line1cnt_in"],
      "the default must not move")

# -- and a handle the function does not publish falls through ----------------
out4 = wf.compile_graph(graph([("banana", {"name": "Stale"})]), zones=ZONES)
check("a port name the rule does not publish falls back, it does not bind",
      [w.get("key") for w in out4["widgets"]] == ["fn_line1cnt_in"],
      "a stale handle must never become fn_<id>_banana")

# -- the same rule, the same fix, on the other one-wire sinks ----------------
src = io.open(os.path.join(ROOT, "core", "services", "workflow.py"),
              encoding="utf-8").read()
check("one helper answers it, not four copies",
      src.count("def wire_metric(node, fn, metrics):") == 1
      and src.count("wire_metric(n, ") >= 4,
      "the Alert node, the telemetry publishers and the Alert card picked a "
      "metric the same way and had the same bug")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
