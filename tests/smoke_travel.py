# Travel Time: every trip number, and speed only when it can be honest.
#
# The node had a "time / speed" mode that changed nothing but the ORDER of its
# metric list -- both numbers were published either way -- so it looked like a
# choice of measurement and answered nothing. And it published only the LAST
# trip, which cannot answer the question A-to-B timing exists for: which trip
# was the slowest.
#
# Speed is the real choice, because it needs a number nobody on the box can
# work out -- the real-world distance between the lines -- so it is opt-in,
# and a node saved before the switch keeps publishing what it published.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import functions as fn_service               # noqa: E402
from core.services import workflow as wf                        # noqa: E402
from app.functions import Function, FunctionEngine              # noqa: E402

LINES = [{"id": "la", "name": "A", "kind": "line", "enabled": True},
         {"id": "lb", "name": "B", "kind": "line", "enabled": True}]


def wire(src, tgt, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{target_handle or ''}", "source": src,
            "target": tgt, "sourceHandle": None, "targetHandle": target_handle}


def compile_(data):
    g = wf.clean_graph({"nodes": [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "za", "type": "zone.ref", "position": {"x": 2, "y": 0},
         "data": {"zone": "la"}},
        {"id": "zb", "type": "zone.ref", "position": {"x": 2, "y": 1},
         "data": {"zone": "lb"}},
        {"id": "tv", "type": "analytics.travel", "position": {"x": 3, "y": 0},
         "data": {"fnId": "trip", "name": "Trip", **data}},
    ], "edges": [wire("src", "f1"), wire("f1", "tv"),
                 wire("za", "tv", "line_a"), wire("zb", "tv", "line_b")]})
    out = wf.compile_graph(g, LINES)
    fn = next((f for f in out["functions"] if f["id"] == "trip"), None)
    return out, fn


# -- the ports say which line is which -----------------------------------------
spec = wf.REGISTRY["analytics.travel"]
labels = [h["label"] for h in spec["handles"]]
assert labels == ["Entrance line", "Exit line"], labels
# The handle IDS are untouched -- every saved wire holds them.
assert [h["id"] for h in spec["handles"]] == ["line_a", "line_b"]
print("== the two ends are named, and no saved wire moves")

# -- every trip number, published --------------------------------------------
for m in ("trips", "last_s", "avg_s", "max_s", "min_s"):
    assert m in fn_service.OUTPUTS["line_travel"], m
    assert m in spec["metrics"], m
# ...and `trips` still leads, because a card with no metric picked and an
# alert with none set both bind to the first one. Moving it would re-aim them.
assert spec["metrics"][0] == "trips"
assert wf.node_metrics({"type": "analytics.travel", "data": {}}, spec)[0] == "trips"
# The two old modes still load for a node that carries one, and are off the
# picker: each published a DIFFERENT metric first.
assert spec["modes"]["time"]["hidden"] and spec["modes"]["speed"]["hidden"]
assert wf.node_metrics({"type": "analytics.travel", "data": {"mode": "time"}},
                       spec)[0] == "last_s"
print("== all five trip numbers, with the first one unchanged")

# -- speed is opt-in, and a distance is only asked for when it is on ----------
_out, fn = compile_({"speed": "off", "distance_m": 25})
assert fn["params"]["distance_m"] == 0, fn["params"]
_out, fn = compile_({"speed": "on", "distance_m": 25})
assert fn["params"]["distance_m"] == 25, fn["params"]
# A node saved before the switch has no `speed` key and WAS publishing speeds
# from its distance. Absent means on, or its cards would read zero on upgrade.
_out, fn = compile_({"distance_m": 25})
assert fn["params"]["distance_m"] == 25, fn["params"]
print("== speed is opt-in; a node that already had one keeps it")

# -- the engine keeps the record trips ----------------------------------------
doc = fn_service.validate_function(
    {"id": "trip", "name": "T", "type": "line_travel",
     "params": {"line_a": "la", "line_b": "lb", "classes": ["person"]}})
eng = FunctionEngine([Function(doc)])


def cross(zone, tid, now):
    """One crossing event, in the shape ZoneEngine.update() returns."""
    eng.update({"crossing_events": [{"zone": zone, "track_id": tid,
                                     "label": "person"}]},
               [], {tid: object()}, {}, 640, 480, now=now)
    return eng.stats()


# Two trips, one slow and one quick, so every aggregate has something to say.
cross("la", 1, 1000.0)
s = cross("lb", 1, 1010.0)
assert s["fn_trip_trips"] == 1 and s["fn_trip_last_s"] == 10.0, s
cross("la", 2, 1020.0)
s = cross("lb", 2, 1024.0)
assert s["fn_trip_trips"] == 2, s
assert s["fn_trip_last_s"] == 4.0, s
assert s["fn_trip_avg_s"] == 7.0, s
assert s["fn_trip_max_s"] == 10.0, s      # the bottleneck
assert s["fn_trip_min_s"] == 4.0, s
print("== latest, average, slowest and fastest, over two real trips")
print("ALL OK")
