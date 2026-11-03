# Everything the canvas publishes to the editors carries WHERE it came from.
#
# The "From workflow" rail listed cards, live measurements, variables and
# recorded columns in one undifferentiated column that grew with the pipeline
# and pushed the widget palette off the screen. It is a picker now, grouped by
# `origin` -- so every catalogue entry has to carry one, and a variable (which
# used to be left out of the palette entirely) has to be offered.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"counting": {"key": "counting", "name": "Counting", "columns": [
    {"name": "live_counting", "type": "number"}]}}

GRAPH = {"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "In Zone Now"}},
    {"id": "v", "type": "variable.number", "position": {"x": 2, "y": 2},
     "data": {"fnId": "target", "name": "Target", "value": 5,
              "value_type": "number"}},
    # A dashboard node is offered by NAME -- naming it is what publishes it.
    {"id": "card", "type": "dashboard.card", "position": {"x": 3, "y": 0},
     "data": {"name": "Zone Counting", "kind": "tile"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "card"},
]}

c = wf.compile_graph(wf.clean_graph(GRAPH), ZONES, tables=TABLES)
assert not c["errors"], c["errors"]
by_origin = {}
for entry in c["catalog"]:
    by_origin.setdefault(entry.get("origin"), []).append(
        entry.get("title") or entry.get("label"))

# 1. every entry is filed. An entry with no origin lands in the picker's
#    "Other" bucket -- it still shows, but nobody would ever look there.
assert None not in by_origin, by_origin
print("== every catalogue entry says where it came from:",
      {k: len(v) for k, v in by_origin.items()})

# 2. the four groups, each holding what it says on the tin
assert "Zone Counting" in by_origin.get("card", []), by_origin
assert any("In Zone Now" in t for t in by_origin.get("measure", [])), by_origin
assert any("live counting" in t or "live_counting" in t
           for t in by_origin.get("table", [])), by_origin
print("== cards, measurements and recorded columns are separated")

# 3. VARIABLES are offered now. They were left out on the grounds that a value
#    parked for a formula to read is plumbing -- but the real objection was
#    the length of a flat list, and the picker groups and searches.
assert any("Target" in t for t in by_origin.get("variable", [])), by_origin
var = next(e for e in c["catalog"] if e.get("origin") == "variable")
assert var["key"].startswith("fn_target_"), var
print("== a variable is offered, in its own group:", var["key"])

# 4. a table column carries the table it belongs to, so two tables with a
#    `count` column are told apart in the list
col = next(e for e in c["catalog"] if e.get("origin") == "table")
assert col["group"] == "Counting", col
print("== a recorded column names its table:", col["group"])
print("ALL OK")
