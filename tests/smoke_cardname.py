# A dashboard node can be NAMED, and naming it publishes it to the editors.
#
# Two problems, one field. A card was titled after the function feeding it, so
# one Occupancy function feeding a tile, a gauge and a chart produced three
# cards all called "Bay" -- and on the canvas three headers all reading "Card".
#
# And the editors' "From workflow" rail only ever listed raw measurements
# (function metrics, table columns). A card the operator had already composed
# on the canvas -- shape, range, unit and all -- could not be reached from the
# Dashboard or Analytics editor; it had to be rebuilt there by hand.
#
# Naming is the opt-in: an unnamed card keeps the old title and stays OFF the
# rail, so this cannot flood the palette with a dozen identical "Card" rows.
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


def graph(cards):
    """Detection -> Zone -> Occupancy("Bay") -> one card per entry."""
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "z"},
             {"id": "e2", "source": "z", "target": "occ"}]
    for i, (node_type, data) in enumerate(cards):
        nodes.append({"id": f"c{i}", "type": node_type,
                      "position": {"x": 3, "y": i}, "data": data})
        edges.append({"id": f"ec{i}", "source": "occ", "target": f"c{i}"})
    return {"nodes": nodes, "edges": edges}


def compile_cards(cards):
    g = wf.clean_graph(graph(cards))
    wf.ensure_fn_ids(g)
    c = wf.compile_graph(g, ZONES)
    assert not c["errors"], c["errors"]
    return c


# 1. UNNAMED, nothing changes: the card is still titled after its function.
c = compile_cards([("dashboard.card", {"kind": "tile"})])
w = next(w for w in c["widgets"] if w["id"] == "wf_c0")
assert w["title"] == "Bay", w["title"]
print("== an unnamed card is still titled after its function:", w["title"])

# ...and it is NOT on the palette rail. Only the function's own metrics are.
assert not [e for e in c["catalog"] if e.get("node") == "c0"], c["catalog"]
print("== and an unnamed card adds nothing to the palette")

# 2. NAMED, the name is the title -- three cards on one function, three names.
c = compile_cards([
    ("dashboard.card", {"kind": "tile", "name": "People in Bay"}),
    ("dashboard.card", {"kind": "gauge", "name": "Bay capacity"}),
    ("dashboard.graph", {"kind": "area", "name": "Bay over time", "unit": "ppl"}),
])
titles = {w["id"]: w["title"] for w in c["widgets"]}
assert titles == {"wf_c0": "People in Bay", "wf_c1": "Bay capacity",
                  "wf_c2": "Bay over time"}, titles
print("== three cards on one function get three names:",
      ", ".join(sorted(titles.values())))

# 3. each named card reaches the palette, on BOTH editors.
entries = {e["title"]: e for e in c["catalog"] if e.get("role") == "custom"}
assert set(entries) == set(titles.values()), sorted(entries)
for name, e in entries.items():
    assert e["surfaces"] == ["dashboard", "analytics"], (name, e)
print("== and each one reaches both editors' palette")

# 4. the palette entry carries the COMPOSED card, not a bare stat key -- drop
#    it on another page and it arrives as the card that was drawn.
gauge = entries["Bay capacity"]
assert gauge["kind"] == "gauge" and gauge["source"] == "stat", gauge
assert gauge["key"].startswith("fn_bay_"), gauge
chart = entries["Bay over time"]
assert chart["kind"] == "area" and chart["source"] == "history", chart
assert chart["unit"] == "ppl", chart
assert chart.get("range"), chart          # a history card carries its window
# The canvas copy's own business, and never inherited from the original.
assert "id" not in gauge and "span" not in gauge, gauge
print("== the entry carries the drawn card:", chart["kind"], chart["range"],
      chart["unit"])

# 5. An Event Card over a data table names itself too, and the SAME field does
#    it -- one "name" across every dashboard node, not one per node type.
for node_type in ("dashboard.card", "dashboard.graph", "dashboard.events"):
    assert "name" in wf.REGISTRY[node_type]["data"], node_type
print("== every dashboard node has the same name field")

# 6. the named widget still passes the validator that renders it, title and all
for w in c["widgets"]:
    assert builder_service.validate([w])[0]["title"] == w["title"], w
print("== and the names survive the layout validator")

# 7. THE TEMPLATE IS THE NODE'S, and reaches the widget.
#
#    It could not be the Builder's: a wf_* card is rebuilt from its node on
#    every compile, and _merge_layout keeps the STORED compiled copy on a
#    partial save (so a recompile cannot lose the card). A template picked in
#    the Builder was therefore discarded, and every workflow card rendered
#    plain whatever the picker said.
c = compile_cards([
    ("dashboard.graph", {"kind": "area", "template": "stacked"}),
    ("dashboard.graph", {"kind": "line", "template": "glow"}),
    ("dashboard.card", {"kind": "tile", "template": "accent"}),
    ("dashboard.card", {"kind": "tile"}),                      # unset
    ("dashboard.card", {"kind": "tile", "template": "not-a-template"}),
])
got = {w["id"]: w.get("template") for w in c["widgets"]}
assert got == {"wf_c0": "stacked", "wf_c1": "glow", "wf_c2": "accent",
               "wf_c3": "plain", "wf_c4": "plain"}, got
print("== the node's template reaches the widget; junk falls back to plain")

# and it survives the validator, which is the last thing before the renderer
for w in c["widgets"]:
    assert builder_service.validate([w])[0]["template"] == w["template"], w
print("== and survives the layout validator")

# every template the Builder offers must be one the server accepts, or the
# picker offers a choice that is silently stored as "plain"
for name in ("stacked", "glow", "stripe", "forecast"):
    assert name in builder_service.TEMPLATES, name
print("== all four chart treatments are accepted server-side")

# 8. ...and compute() -- the ONE function every page's widgets pass through --
#    must carry it to the browser. It rebuilds each widget as a fresh dict, so
#    a field left off there is a field that exists in the layout, the compiler
#    and the inspector and still never reaches Widget.jsx: exactly how every
#    template rendered plain on the live Dashboard and Analytics pages while
#    everything upstream looked correct.
rows = builder_service.compute(
    [{"id": "t1", "title": "T", "kind": "tile", "source": "stat",
      "key": "nope", "template": "accent"},
     {"id": "t2", "title": "T", "kind": "tile", "source": "stat",
      "key": "nope"},
     {"id": "t3", "title": "T", "kind": "tile", "source": "stat",
      "key": "nope", "template": "junk"}],
    events=[], stats={})
got = {r["id"]: r["template"] for r in rows}
assert got == {"t1": "accent", "t2": "plain", "t3": "plain"}, got
print("== compute() carries the template to the page; junk still lands plain")

# 9. THE BUILDER CAN RE-DRESS A COMPILED CARD, via the layout's override map.
#
#    A wf_* widget row is server-owned (the partial merge keeps the stored
#    copy), so a template edited onto the row itself was discarded -- the
#    Analytics inspector's picker looked broken. The dressing lives beside the
#    row instead, like placements, and load() applies it on the way in so
#    every consumer sees the dressed widget.
import tempfile as _tf
_cfg = _tf.mkdtemp()
builder_service.save(_cfg, "t", {
    "pages": [{"id": "overview", "name": "Overview", "widgets": [
        {"id": "wf_x", "title": "T", "kind": "line", "source": "history",
         "key": "fn_a_count", "template": "plain"}]}],
    "templates": {"wf_x": "glow", "junk": "not-a-template"},
})
loaded = builder_service.load(_cfg, "t")
w = loaded["pages"][0]["widgets"][0]
assert w["template"] == "glow", w
assert loaded["templates"] == {"wf_x": "glow"}, loaded["templates"]
print("== a template override dresses the stored wf_* row on load")

# The workflow's own layout write and a partial save must both KEEP the map --
# either one dropping it would strip the choice on the next recompile/save.
merged = builder_service.merge_partial(loaded, {"pages": []})
assert merged["templates"] == {"wf_x": "glow"}, merged["templates"]
merged = builder_service.merge_partial(loaded, {"templates": {"wf_x": "stripe"}})
assert merged["templates"] == {"wf_x": "stripe"}, merged["templates"]
print("== partial saves keep or replace the override map, never drop it")

# 10. `bg` -- the CARD's own colour -- is a SEPARATE knob from `template` and
#     survives the exact same override-map path, because it hits the exact
#     same problem: a wf_* widget row is server-owned, so a colour picked on
#     the card itself would be discarded on the next compile.
c = compile_cards([
    ("dashboard.card", {"kind": "tile", "bg": "#ff0000"}),
    ("dashboard.card", {"kind": "tile"}),                # unset
    ("dashboard.card", {"kind": "tile", "bg": "not-a-colour"}),
])
got = {w["id"]: w.get("bg") for w in c["widgets"]}
assert got == {"wf_c0": "#ff0000", "wf_c1": "", "wf_c2": ""}, got
print("== the node's bg reaches the widget; junk falls back to empty")

for w in c["widgets"]:
    assert builder_service.validate([w])[0]["bg"] == w["bg"], w
print("== and survives the layout validator")

_cfg2 = tempfile.mkdtemp()
builder_service.save(_cfg2, "t", {
    "pages": [{"id": "overview", "name": "Overview", "widgets": [
        {"id": "wf_x", "title": "T", "kind": "tile", "source": "stat",
         "key": "fn_a_count", "bg": ""}]}],
    "colors": {"wf_x": "#00ff00", "junk": "not-a-colour"},
})
loaded2 = builder_service.load(_cfg2, "t")
w = loaded2["pages"][0]["widgets"][0]
assert w["bg"] == "#00ff00", w
assert loaded2["colors"] == {"wf_x": "#00ff00"}, loaded2["colors"]
print("== a colour override dresses the stored wf_* row on load")

merged = builder_service.merge_partial(loaded2, {"colors": {"wf_x": "#0000ff"}})
assert merged["colors"] == {"wf_x": "#0000ff"}, merged["colors"]
merged = builder_service.merge_partial(loaded2, {"pages": []})
assert merged["colors"] == {"wf_x": "#00ff00"}, merged["colors"]
print("== partial saves keep or replace the colour override map, never drop it")

# compute() -- the layer that dropped `template` before -- must carry `bg` too.
rows = builder_service.compute(
    [{"id": "t1", "title": "T", "kind": "tile", "source": "stat",
      "key": "nope", "bg": "#123456"},
     {"id": "t2", "title": "T", "kind": "tile", "source": "stat", "key": "nope"}],
    events=[], stats={})
got = {r["id"]: r["bg"] for r in rows}
assert got == {"t1": "#123456", "t2": ""}, got
print("== compute() carries bg to the page")

# 11. a name cannot be used to smuggle an unbounded string into a title
long = compile_cards([("dashboard.card", {"kind": "tile", "name": "x" * 400})])
w = next(w for w in long["widgets"] if w["id"] == "wf_c0")
assert len(w["title"]) <= 60, len(w["title"])
print("== an over-long name is clamped to", len(w["title"]), "characters")
print("ALL OK")
