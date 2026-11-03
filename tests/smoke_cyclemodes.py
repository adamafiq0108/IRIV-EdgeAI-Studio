# Cycle Time: three measurements, and a clock that needs no place.
#
# It offered five modes. Three of them -- "Zone Dwell Time", "OID Timer",
# "OID Measure Time" -- were ONE rule publishing the same three numbers,
# differing only in what the node added: a per-object threshold, or an event
# per completed stay. That is a checkbox and a delivery choice, not three
# things to choose between before knowing they are the same measurement.
#
# And the zone became optional for the two that measure a CLOCK: "how long has
# this been on camera" and "how long did each arrival get" are the same
# measurements over the whole view. The Occupied clock is the exception it
# sounds like -- "the frame is occupied" is true whenever the camera is on --
# so it asks for a zone instead.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fn_service               # noqa: E402
from core.services import workflow as wf                        # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def wire(src, tgt, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{target_handle or ''}", "source": src,
            "target": tgt, "sourceHandle": None, "targetHandle": target_handle}


def graph(mode, zone=True, extra=None):
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "f1", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person"]}},
        {"id": "cy", "type": "analytics.dwell", "position": {"x": 2, "y": 0},
         "data": {"fnId": "cyc", "name": "Cycle", "mode": mode,
                  **(extra or {})}},
    ]
    edges = [wire("src", "f1"), wire("f1", "cy")]
    if zone:
        nodes.append({"id": "z", "type": "zone.ref", "position": {"x": 2, "y": 1},
                      "data": {"zone": "bay"}})
        edges.append(wire("z", "cy", "zone"))
    return wf.clean_graph({"nodes": nodes, "edges": edges})


def compile_(mode, zone=True, extra=None):
    return wf.compile_graph(graph(mode, zone, extra), ZONES)


def fn_of(out, fid="cyc"):
    return next((f for f in out["functions"] if f["id"] == fid), None)


# -- three modes are offered; the old three still load -------------------------
spec = wf.REGISTRY["analytics.dwell"]
offered = [k for k, m in spec["modes"].items() if not m.get("hidden")]
assert offered == ["present", "countdown", "occupancy"], offered
for old in ("dwell", "timer", "stamps"):
    assert spec["modes"][old]["hidden"], old
# The first mode is what a mode-less node compiles as, and its first metric is
# what an unlabelled wire and a metric-less alert bind to. Both unchanged.
assert list(spec["modes"])[0] == "present"
assert spec["modes"]["present"]["metrics"][0] == "live_s"
print("== three modes offered, the five still load, live_s still leads")

# -- a clock runs with or without a place --------------------------------------
for mode, ftype in (("present", "zone_dwell"), ("countdown", "zone_countdown")):
    out = compile_(mode, zone=True)
    assert not out["errors"], (mode, out["errors"])
    assert fn_of(out)["params"]["zone"] == "bay", mode

    out = compile_(mode, zone=False)
    assert not out["errors"], (mode, out["errors"])
    fn = fn_of(out)
    assert fn["type"] == ftype, (mode, fn["type"])
    assert "zone" not in fn["params"], fn["params"]
print("== Time present and Countdown measure the whole view with no zone")

# -- ...but the Occupied clock says what it needs -------------------------------
# "The frame is occupied" is true whenever the camera is on. Switched OFF with
# a readable reason rather than compiled into a number that means nothing.
out = compile_("occupancy", zone=False)
assert not fn_of(out), "the occupied clock compiled with no zone"
msg = " ".join(str(w) for w in (out.get("warnings") or []))
assert "Occupied clock needs a Zone" in msg, msg
assert not out["errors"], out["errors"]
print("== the Occupied clock asks for a zone instead of measuring the frame")

# -- the rule itself agrees, on the core side ----------------------------------
doc = fn_service.validate_function(
    {"id": "c1", "name": "C", "type": "zone_dwell", "params": {}})
assert "zone" not in doc["params"], doc["params"]
for ftype in ("zone_count", "zone_unique", "zone_occupancy"):
    try:
        fn_service.validate_function({"id": "c", "name": "C", "type": ftype,
                                      "params": {}})
    except ValueError:
        pass
    else:
        raise AssertionError(f"{ftype} accepted a rule with no zone")
print("== a clock validates with no zone; a population count still insists")

# -- OID delivery: live, or a record of each completed stay --------------------
# `stamp_events` is what writes the per-object record a Data Table row is made
# from. It follows the delivery choice now, where it used to follow the mode.
out = compile_("present", extra={"OID": True, "delivery": "once"})
assert fn_of(out)["params"].get("stamp_events") is True, fn_of(out)["params"]
out = compile_("present", extra={"OID": True, "delivery": "live"})
assert "stamp_events" not in fn_of(out)["params"], fn_of(out)["params"]
# A node saved BEFORE the choice existed ticked OID alone, and that stamped
# the stays -- which is what its table is reading. Absent means "once".
out = compile_("present", extra={"OID": True})
assert fn_of(out)["params"].get("stamp_events") is True, fn_of(out)["params"]
print("== the record of each stay follows the delivery choice, and old nodes "
      "keep theirs")

# -- MIGRATION -----------------------------------------------------------------
g = {"version": 7, "nodes": [
    {"id": "a", "type": "analytics.dwell", "position": {"x": 0, "y": 0},
     "data": {"fnId": "d1", "mode": "dwell"}},
    {"id": "b", "type": "analytics.dwell", "position": {"x": 0, "y": 0},
     "data": {"fnId": "d2", "mode": "timer"}},
    {"id": "c", "type": "analytics.dwell", "position": {"x": 0, "y": 0},
     "data": {"fnId": "d3", "mode": "stamps"}},
    {"id": "e", "type": "analytics.dwell", "position": {"x": 0, "y": 0},
     "data": {"fnId": "d4", "mode": "dwell", "OID": True}},
    {"id": "f", "type": "analytics.dwell", "position": {"x": 0, "y": 0},
     "data": {"fnId": "d5", "mode": "occupancy"}}],
    "edges": []}
assert wf.migrate_cycle_modes(g) is True
data = {n["id"]: n["data"] for n in g["nodes"]}
assert data["a"]["mode"] == "present" and not data["a"].get("OID")
# The per-object timer WAS the live clock; the measure-time mode WAS the record.
assert data["b"] == {"fnId": "d2", "mode": "present", "OID": True,
                     "delivery": "live"}, data["b"]
assert data["c"] == {"fnId": "d3", "mode": "present", "OID": True,
                     "delivery": "once"}, data["c"]
# OID already on, no mode to read it from: it was stamping, so say so.
assert data["e"]["delivery"] == "once", data["e"]
# The two modes that were never in question are left exactly alone.
assert data["f"] == {"fnId": "d5", "mode": "occupancy"}, data["f"]
assert g["version"] == 8, g["version"]
print("== the three old modes fold into one, and what they DID is written down")
print("ALL OK")
