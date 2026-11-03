# Does this line have a FACE?
#
# A line always knew a direction -- the positive side of the two points as
# they were drawn -- but nothing on screen ever said which side that was, and
# nothing let an operator say "this corridor has no in and out, I only want to
# know how many walked through". So a line now carries `facing`:
#
#   forward  the +side is IN            (what every line did before, unchanged)
#   reverse  the same geometry, flipped (no redrawing backwards)
#   none     a plain line: crossings are counted, sides are not claimed
#
# The checks below pin all three, that a line written before the field existed
# still counts exactly as it did, and that a faceless line publishes `cross`
# rather than inventing an entry.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import zones as zn                                    # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from core.services import functions as fx                      # noqa: E402

# A vertical line down the middle; walking left-to-right crosses it once.
LINE = {"id": "door", "name": "Door", "kind": "line",
        "points": [[0.5, 0.0], [0.5, 1.0]]}

W = H = 100


def box(cx):
    """A detection whose feet land at x=cx (normalized), mid-frame."""
    return {"label": "person", "track_id": 7, "confidence": 0.9,
            "box": [int(cx * W) - 4, 40, int(cx * W) + 4, 60]}


def walk(facing, xs):
    """Drive one object across the line and return the last crossing report."""
    spec = dict(LINE)
    if facing:
        spec["facing"] = facing
    engine = zn.ZoneEngine([zn.Zone(spec)])
    last = {}
    for x in xs:
        result = engine.update([box(x)], W, H)
        c = result["crossings"]["door"]
        if c["in"] or c["out"] or c["cross"]:
            last = dict(c)
            last["events"] = list(result["crossing_events"])
    return engine, last


# The line is drawn top-to-bottom, so its POSITIVE side -- the one the app
# calls IN -- is the left of the frame: walking right-to-left is an entry.
LEFT_TO_RIGHT = [0.2, 0.3, 0.7, 0.8]
RIGHT_TO_LEFT = [0.8, 0.7, 0.3, 0.2]

# -- a line with no `facing` counts exactly as it always did ------------------
# The point of the default: every line already drawn keeps its in and its out,
# and every dashboard bound to line_door_in keeps meaning the same thing.
_, plain_old = walk(None, RIGHT_TO_LEFT)
_, forward = walk("forward", RIGHT_TO_LEFT)
assert plain_old["in"] == forward["in"] == 1, (plain_old, forward)
assert plain_old["out"] == forward["out"] == 0, (plain_old, forward)
print("== a line without the field still counts in/out by its drawn direction")

# -- forward: which way is which ---------------------------------------------
_, back = walk("forward", LEFT_TO_RIGHT)
assert back["out"] == 1 and back["in"] == 0, back
print("== forward: one way is in, the other is out")

# -- reverse: the same walk, the other name ----------------------------------
eng_r, rev = walk("reverse", RIGHT_TO_LEFT)
assert rev["out"] == 1 and rev["in"] == 0, rev
assert rev["events"][0]["direction"] == "out", rev["events"]
_, rev_back = walk("reverse", LEFT_TO_RIGHT)
assert rev_back["in"] == 1 and rev_back["out"] == 0, rev_back
print("== reverse flips in and out without touching the geometry")

# -- none: a crossing is a crossing ------------------------------------------
eng_n, none_fwd = walk("none", LEFT_TO_RIGHT)
assert none_fwd["cross"] == 1, none_fwd
assert none_fwd["in"] == 0 and none_fwd["out"] == 0, none_fwd
assert none_fwd["events"][0]["direction"] == "cross", none_fwd["events"]
_, none_back = walk("none", RIGHT_TO_LEFT)
assert none_back["cross"] == 1, none_back
assert none_back["in"] == 0 and none_back["out"] == 0, none_back
print("== a plain line counts the crossing and claims no side")

# -- what each publishes ------------------------------------------------------
# `occupancy` is in minus out, so a faceless line reports 0 rather than a
# number half of which never happened.
stats_n = eng_n.stats(eng_n.update([box(0.8)], W, H))
assert stats_n["line_door_cross"] == 1, stats_n
assert stats_n["line_door_in"] == 0 and stats_n["line_door_out"] == 0, stats_n
assert stats_n["line_door_occupancy"] == 0, stats_n
assert stats_n["line_door_person_cross"] == 1, stats_n
# 0.2 is where that walk ended -- standing still, so the read adds no crossing.
stats_r = eng_r.stats(eng_r.update([box(0.2)], W, H))
# A faced line publishes `cross` too -- in + out. "How many crossed at all" is
# a fair question to ask of a door that knows its sides.
assert stats_r["line_door_out"] == 1 and stats_r["line_door_cross"] == 1, stats_r
print("== stats keys: cross for the faceless, in/out for the faced")

# -- the field survives a save ------------------------------------------------
# The allowlist gotcha: a key _validate_zone does not copy is dropped in
# silence, and the setting would look saved while the app never saw it.
SYSTEMS = tempfile.mkdtemp()
SLUG = "linefacetest"
os.makedirs(os.path.join(SYSTEMS, SLUG), exist_ok=True)
for facing, expected in (("reverse", "reverse"), ("none", "none"),
                         ("forward", None), ("", None), ("nonsense", None)):
    saved = dc.set_zones(SYSTEMS, SLUG, [{**LINE, "facing": facing}])[0]
    assert saved.get("facing") == expected, (facing, saved)
    # Whatever came back must survive the app's own parse to the same face.
    assert zn.Zone(saved).facing == (expected or "forward"), (facing, saved)
# The crossing threshold follows the same rule: its factory value (1.2, the
# module band) is never written, so a line saved at the default stays
# byte-identical to one saved before the field existed.
saved = dc.set_zones(SYSTEMS, SLUG, [{**LINE, "band_pct": 1.2}])[0]
assert "band_pct" not in saved, saved
assert zn.Zone(saved).band() == zn.LINE_DEADBAND, saved
print("== facing round-trips through set_zones; the default is never written")

# An AREA has no sides to face, so the field is refused there rather than
# stored where nothing will ever read it.
area = dc.set_zones(SYSTEMS, SLUG, [
    {"id": "a", "name": "A", "kind": "area", "facing": "reverse",
     "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]}])[0]
assert "facing" not in area, area
print("== an area zone never carries a face")

# -- the Entry/Exit node offers the metric -----------------------------------
# Without this the operator picks a plain line and finds a node whose three
# metrics are all permanently zero.
assert "cross" in fx.OUTPUTS["line_count"], fx.OUTPUTS["line_count"]
print("== the crossing counter publishes `cross`")

# -- Line Counter: WHICH DIRECTION counts ------------------------------------
# The rule tallies every direction whatever the setting; the mode says which
# the node leads with, and therefore which ports it offers. `in` is the first
# mode, so a counter saved before this existed still publishes `in` first --
# a mode-less node takes the first entry, and changing which metric leads
# would silently re-aim every alert that never named one.
from core.services import workflow as wf                        # noqa: E402

spec = wf.REGISTRY["analytics.entry_exit"]
# Drawn as "Counter" since 2026-10-13: the Line Counter is the Counter with a
# line wired (tests/smoke_counterunify.py).
assert spec["label"] == "Counter", spec["label"]
assert list(spec["modes"]) == ["in", "out", "both"], list(spec["modes"])
assert spec["data"]["mode"] == "in"
node = {"type": "analytics.entry_exit", "data": {}}
assert wf.node_metrics(node, spec)[0] == "in", wf.node_metrics(node, spec)
for mode, want in (("in", ["in", "cross"]),
                   ("out", ["out", "cross"]),
                   ("both", ["in", "out", "net", "cross"])):
    got = wf.node_metrics({"type": "analytics.entry_exit",
                           "data": {"mode": mode}}, spec)
    assert got == want, (mode, got)
# Every port it offers is a real published tally: the handle IS the metric
# key, which is how a named wire resolves at compile.
for m in spec["metrics"]:
    assert m in fx.OUTPUTS["line_count"], m
print("== the Line Counter leads with the direction that counts")
print("ALL OK")
