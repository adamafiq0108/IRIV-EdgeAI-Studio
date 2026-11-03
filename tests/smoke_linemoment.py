# The Line Counter's LIVE count: how many objects are ON the line right now,
# by the way they crossed -- 1 while one is over it going in, 2 while two
# are, 0 once they have passed -- instead of a running total. The default
# for a new counter, and the operator's own words: "count only when the
# object on the line, then reset after they pass; 2 on the line count as 2".
#
# WHY. A running total is a number a Data Table can only log as "the total
# so far"; the day's count is then a subtraction across rows, and a restart
# in the middle breaks it. A momentary count is logged row by row as it
# happens (Trigger on "> 0", a column on the trigger's value) and the total
# is the SUM of the column. The running tally stays available underneath as
# total_in / total_out / total_cross for a card.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

from app.functions import Function, FunctionEngine                # noqa: E402
from app import zones as zn                                       # noqa: E402
from core.services import functions as fx                         # noqa: E402
from core.services import workflow as wf                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    import io
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def engine(fn):
    e = FunctionEngine.__new__(FunctionEngine)
    e.functions, e._trig, e._stats, e._live_oids, e._counts = [fn], {}, {}, {}, {}
    return e


def crossings(*ways):
    return [{"zone": "door", "direction": w, "label": "person", "track_id": i + 1}
            for i, w in enumerate(ways)]


def frames(e, fn, seq, step=2.0):
    """One frame per entry, `step` seconds apart -- past the moment's hold,
    so each entry is its own moment. `seq` entries may be (events, t)."""
    out = []
    for i, entry in enumerate(seq):
        events, t = (entry if isinstance(entry, tuple) else (entry, i * step))
        e._stats = {}
        e._update_line_count(fn, "fn_lc_", events, now=t)
        out.append({k[len("fn_lc_"):]: v for k, v in e._stats.items()})
    return out


# -- the moment -------------------------------------------------------------------
moment = Function({"id": "lc", "name": "Door", "type": "line_count",
                   "params": {"line": "door", "momentary": True, "totals": True}})
seq = [crossings("in", "in"), [], crossings("out"), [], crossings("in", "out", "in")]
got = frames(engine(moment), moment, seq)
check("no line to test against: a crossing counts for its own frame, two together 2",
      got[0]["in"] == 2 and got[0]["cross"] == 2 and got[0]["net"] == 2, got[0])
check("...and 0 once the moment has passed, not 1 still", got[1]["in"] == 0 and got[1]["cross"] == 0, got[1])
# LIVE means this frame: no hold. A one-second hold was tried and asked off
# again -- the next frame with no crossing reads 0, however soon it comes.
live = frames(engine(moment), moment, [(crossings("in"), 0.0), ([], 0.05), (crossings("in"), 0.1),
                                        ([], 0.15)])
check("the very next frame reads 0 - live is this frame, not the last second",
      [f["in"] for f in live] == [1, 0, 1, 0], [f["in"] for f in live])
check("one out reads 1 out and net -1 for that frame",
      got[2]["out"] == 1 and got[2]["in"] == 0 and got[2]["net"] == -1, got[2])
check("a mixed frame reads each way for that frame",
      got[4]["in"] == 2 and got[4]["out"] == 1 and got[4]["net"] == 1 and got[4]["cross"] == 3, got[4])
check("the running tally rides underneath, for a card",
      got[4]["total_in"] == 4 and got[4]["total_out"] == 2 and got[4]["total_cross"] == 6,
      {k: v for k, v in got[4].items() if k.startswith("total")})
check("...and the summed moments equal the total - which is the whole point",
      sum(f["in"] for f in got) == got[-1]["total_in"]
      and sum(f["out"] for f in got) == got[-1]["total_out"])

# -- ON THE LINE: the object is the clock -----------------------------------------------------
# A vertical line at x = 0.5 of a 100x100 frame. A person crossing in: their
# box reaches the line on the crossing frame, still overlaps it two frames
# later, and has passed on the fourth. In reads 1, 1, 1, 0. Two on the line
# read 2.
LINE = zn.Zone({"id": "door", "name": "Door", "kind": "line", "enabled": True,
                "points": [[0.5, 0.0], [0.5, 1.0]], "match": "center"})


def det(tid, x1, x2):
    return {"track_id": tid, "label": "person", "box": [x1, 20, x2, 80]}


def on_line_frames(fn, seq):
    e = engine(fn)
    out = []
    for i, (events, dets) in enumerate(seq):
        e._stats = {}
        e._update_line_count(fn, "fn_lc_", events, now=i * 0.05, detections=dets,
                             zones=[LINE], frame_w=100, frame_h=100)
        out.append(e._stats["fn_lc_in"])
    return out


walk = on_line_frames(moment, [
    (crossings("in"), [det(1, 40, 56)]),   # crossing detected, box over the line
    ([], [det(1, 44, 60)]),                # still over it
    ([], [det(1, 48, 64)]),                # trailing edge on the line
    ([], [det(1, 60, 76)]),                # passed: box clear of the line and its band
    ([], [det(1, 70, 86)]),
])
check("In is 1 from the crossing for as long as the box is on the line, then 0 once passed",
      walk == [1, 1, 1, 0, 0], walk)
def crossing_of(tid, way="in"):
    return [{"zone": "door", "direction": way, "label": "person", "track_id": tid}]


two = on_line_frames(moment, [
    (crossing_of(1), [det(1, 40, 56)]),
    (crossing_of(2), [det(1, 44, 60), det(2, 42, 58)]),   # second one arrives: 2 on the line
    ([], [det(1, 60, 76), det(2, 46, 62)]),                # first has passed, second still on
    ([], [det(1, 70, 86), det(2, 62, 78)]),
])
check("two on the line read 2, then 1 as the first passes, then 0",
      two == [1, 2, 1, 0], two)
gone = on_line_frames(moment, [
    (crossings("in"), [det(1, 40, 56)]),
    ([], []),                              # track lost while over the line
])
check("an object whose track vanishes is dropped, not counted for ever", gone == [1, 0], gone)

# -- the running total, untouched ------------------------------------------------------
total = Function({"id": "lc", "name": "Door", "type": "line_count",
                  "params": {"line": "door"}})
got_t = frames(engine(total), total, seq)
check("without the switch the tally still runs",
      [f["in"] for f in got_t] == [2, 2, 2, 2, 4] and got_t[-1]["out"] == 2, [f["in"] for f in got_t])
check("...and publishes no total_* twins", "total_in" not in got_t[-1])
quiet = Function({"id": "lc", "name": "Door", "type": "line_count",
                  "params": {"line": "door", "momentary": True}})
got_q = frames(engine(quiet), quiet, seq)
check("a live counter publishes its running totals as ports, no switch needed - inside too",
      got_q[-1]["total_in"] == 4 and got_q[-1]["total_out"] == 2
      and got_q[-1]["total_net"] == 2 and got_q[-1]["total_cross"] == 6, got_q[-1])
node_live = {"type": "analytics.entry_exit", "data": {"mode": "both", "momentary": True}}
node_total = {"type": "analytics.entry_exit", "data": {"mode": "both"}}
spec_ee = wf.REGISTRY["analytics.entry_exit"]
check("...and the node offers them as ports, live only",
      wf.node_metrics(node_live, spec_ee)[-4:] == ["total_in", "total_out", "total_net", "total_cross"]
      and "total_in" not in wf.node_metrics(node_total, spec_ee),
      (wf.node_metrics(node_live, spec_ee), wf.node_metrics(node_total, spec_ee)))
check("...named on the canvas and mirrored by the client",
      'total_net: "Inside (total)"' in read("frontend", "src", "workflow", "registry.js")
      and "spec?.live_metrics && node?.data?.momentary" in read("frontend", "src", "workflow", "registry.js"))

# -- the master keeps the switch, and only when on ----------------------------------------
doc = {"id": "lc", "name": "Door", "type": "line_count", "enabled": True,
       "params": {"line": "door", "momentary": True}}
clean = fx.validate_function(doc)
params = clean.get("params") if isinstance(clean, dict) else getattr(clean, "params", {})
check("validate_function keeps momentary", params.get("momentary") is True, params)
with_t = fx.validate_function({**doc, "params": {"line": "door", "momentary": True, "totals": True}})
wt = with_t.get("params") if isinstance(with_t, dict) else getattr(with_t, "params", {})
check("...and an older 'totals' switch still round-trips, harmless", wt.get("totals") is True)
doc2 = {"id": "lc", "name": "Door", "type": "line_count", "enabled": True, "params": {"line": "door"}}
clean2 = fx.validate_function(doc2)
params2 = clean2.get("params") if isinstance(clean2, dict) else getattr(clean2, "params", {})
check("...and writes nothing when it is off, so old counters round-trip untouched",
      "momentary" not in params2, params2)


check("a NEW Line Counter is live by default; a saved one keeps its own choice",
      (wf.REGISTRY["analytics.entry_exit"]["data"] or {}).get("momentary") is True)
check("the compiler carries the node's choice into the rule",
      'if d.get("momentary"):\n                params["momentary"] = True' in read("core", "services", "workflow.py"))
check("the inspector offers it as the third way to count, and says how to log it",
      'value="moment"' in read("frontend", "src", "pages", "Workflow.jsx")
      and "sum of the column" in read("frontend", "src", "pages", "Workflow.jsx"))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
