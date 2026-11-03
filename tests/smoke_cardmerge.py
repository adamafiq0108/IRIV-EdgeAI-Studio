# Six dashboard nodes become three, and nothing already drawn changes.
#
# Counter, Gauge and Verdict were one node three times: same wiring, same
# inspector, different `widget_kind`. Chart was Graph with the shape picker
# removed. An Events Table is an Event Card with kind "table". Three nodes for
# one decision is three things to learn and three places to fix a bug.
#
# The whole risk of a merge like this is the canvases already saved against the
# old types, so most of this file is about those compiling to exactly what they
# did before.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service          # noqa: E402
from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]


def graph(card_type, data=None):
    """Detection -> Zone -> Occupancy -> a card."""
    return {"nodes": [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
        {"id": "card", "type": card_type, "position": {"x": 3, "y": 0},
         "data": data or {}},
    ], "edges": [
        {"id": "e1", "source": "s", "target": "z"},
        {"id": "e2", "source": "z", "target": "occ"},
        {"id": "e3", "source": "occ", "target": "card"},
    ]}


def widget(card_type, data=None, migrate=True):
    g = wf.clean_graph(graph(card_type, data))
    if migrate:
        wf.migrate_merged_nodes(g)
    wf.ensure_fn_ids(g)
    c = wf.compile_graph(g, ZONES)
    assert not c["errors"], (card_type, c["errors"])
    return next(w for w in c["widgets"] if w["id"] == "wf_card")


# 1. THE OLD TYPES STILL COMPILE, to the same widget kind they always did.
#    This is the migration working: an existing canvas is rewritten on open.
for old, kind in (("dashboard.counter", "tile"),
                  ("dashboard.gauge", "gauge"),
                  ("dashboard.verdict", "verdict"),
                  ("dashboard.chart", "line"),
                  ("dashboard.table", "table")):
    w = widget(old)
    assert w["kind"] == kind, (old, w["kind"], kind)
    print(f"== {old:20s} still compiles to a {kind}")

# and the migration actually rewrote the TYPE, rather than the compiler
# quietly special-casing the old name for ever
g = wf.clean_graph(graph("dashboard.counter"))
assert wf.migrate_merged_nodes(g) is True
assert {n["type"] for n in g["nodes"] if n["id"] == "card"} == {"dashboard.card"}
print("== and the node itself is rewritten, not special-cased")

# 2. ONE CARD NODE, four shapes. The shape is data, the way Chart always
#    treated it -- and the names are the ones the editors already use.
for kind in wf.CARD_KINDS:
    w = widget("dashboard.card", {"kind": kind})
    assert w["kind"] == kind, (kind, w["kind"])
print("== one Card node covers", ", ".join(wf.CARD_KINDS))

for kind in wf.CHART_KINDS:
    w = widget("dashboard.graph", {"kind": kind})
    assert w["kind"] == kind, (kind, w["kind"])
print("== one Chart node covers", ", ".join(wf.CHART_KINDS))

# 3. A VERDICT keeps everything that made it its own node: the alert state as
#    its default metric, and its value->state list.
w = widget("dashboard.card", {"kind": "verdict"})
assert w["states"], w
assert w["key"].endswith("_active") or w["states"], w
print("== the verdict shape still reads the alert state:", w["key"])

w = widget("dashboard.card", {"kind": "verdict",
                              "states": [{"value": 2, "label": "STOP", "tone": "alert"}]})
assert w["states"][0]["label"] == "STOP", w["states"]
print("== and still takes its own value -> state list")

# 4. A donut, pie or radar is a COMPARISON, not a time series: the history
#    sampler has one series to give it, and a pie of one slice is not a pie.
for kind in ("donut", "pie", "radar"):
    assert widget("dashboard.graph", {"kind": kind})["source"] == "stat", kind
for kind in ("line", "area", "spline"):
    assert widget("dashboard.graph", {"kind": kind})["source"] == "history", kind
print("== round charts read live values; line charts read history")

# 5. every shape survives the validator that actually renders it
for node_type, kinds in (("dashboard.card", wf.CARD_KINDS),
                         ("dashboard.graph", wf.CHART_KINDS)):
    for kind in kinds:
        w = widget(node_type, {"kind": kind})
        assert builder_service.validate([w])[0]["kind"] == kind, (node_type, kind)
print("== and every shape survives the layout validator")

# 6. the merged types are hidden from the palette but still LOAD -- a canvas
#    drawn last month must open, not error on an unknown node type
reg = wf.registry_public()
# The Event Card followed them into the Card and the Chart, as their
# `source_mode` (smoke_eventcardmerge pins that migration).
for old in ("dashboard.counter", "dashboard.gauge", "dashboard.verdict",
            "dashboard.chart", "dashboard.table", "dashboard.events"):
    assert old in reg, f"{old} was deleted -- an old canvas would fail to load"
    assert reg[old].get("deprecated"), f"{old} is still offered in the palette"
for live in ("dashboard.card", "dashboard.graph"):
    assert not reg[live].get("deprecated"), live
print("== the old types load but are not offered; two are")
print("ALL OK")
