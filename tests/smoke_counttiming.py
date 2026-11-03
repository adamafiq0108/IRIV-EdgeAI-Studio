# Counting timing: when does an object count, and does the canvas say so.
#
# The rule is a pure function of (detections, clock), so it is asserted here
# rather than on a board -- which is the point of keeping it that way.
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "systems" / "demo"))

from app.functions import Function, FunctionEngine                # noqa: E402
from core.services import workflow as wf                          # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def engine():
    e = FunctionEngine.__new__(FunctionEngine)
    e._member = {}
    e._departed = []
    return e


def fn(**params):
    return Function({"id": "f1", "type": "zone_count", "name": "z",
                     "params": {"zone": "z1", **params}})


print("== the membership rule")
e = engine()
keep = lambda det: det["in"]                                      # noqa: E731
dets = lambda ids: [{"track_id": i, "in": True} for i in ids]     # noqa: E731

# No timing at all is REAL TIME, and returns None so the caller counts
# detections -- the path that needs no tracker.
check("real time asks for no membership",
      e._timed_members(fn(), dets([1]), 100.0, keep) is None)
check("timing is (0, 0) by default", e._timing(fn()) == (0.0, 0.0))

f = fn(enter_after_s=2, leave_after_s=1)
check("timing reads back", e._timing(f) == (2.0, 1.0))
check("nothing counts on arrival", e._timed_members(f, dets([1, 2]), 100.0, keep) == [])
check("still nothing at 1s", e._timed_members(f, dets([1, 2]), 101.0, keep) == [])
check("both count at 2.5s",
      sorted(e._timed_members(f, dets([1, 2]), 102.5, keep)) == [1, 2])
# One leaves. It keeps counting for leave_after_s, then stops -- that grace is
# what stops a tracker blink from making an alert fire twice.
check("a departure keeps counting inside the grace",
      sorted(e._timed_members(f, dets([1]), 103.0, keep)) == [1, 2])
check("still inside the grace at 103.5",
      sorted(e._timed_members(f, dets([1]), 103.5, keep)) == [1, 2])
check("dropped once the grace passes",
      e._timed_members(f, dets([1]), 105.0, keep) == [1])
check("the state does not grow forever", len(e._member["f1"]) == 1)

# A detection with no track id cannot be timed -- there is nothing to say it
# is the same object as last frame.
e2 = engine()
check("untracked detections are not members",
      e2._timed_members(fn(enter_after_s=1),
                        [{"track_id": None, "in": True}], 100.0, keep) == [])

print("== both counters use it")
src = (ROOT / "systems" / "demo" / "app" / "functions.py").read_text(encoding="utf-8")
for name in ("_zone_count", "_update_frame_count", "_update_unique"):
    body = src.split("def %s" % name, 1)[1].split("\n    def ", 1)[0]
    # Directly, or through the zone-scoped wrapper -- either way the rule is
    # the one thing deciding membership.
    check("%s honours the timing" % name,
          "_timed_members" in body or "_zone_members" in body)

print("== validation")
for bad in (-1, 3601, "soon"):
    try:
        fn(enter_after_s=bad)
        check("refuses enter_after_s=%r" % bad, False, "accepted")
    except ValueError:
        check("refuses enter_after_s=%r" % bad, True)
try:
    fn(enter_after_s=0, leave_after_s=3600)
    check("accepts the ends of the range", True)
except ValueError as exc:
    check("accepts the ends of the range", False, str(exc))

print("== the compiler")
check("the timing cap is one hour", wf.MAX_COUNT_TIMING_S == 3600)
params = {}
wf._count_timing({"id": "n1"}, {"enter_after_s": 5, "leave_after_s": 2},
                 params, lambda *a: None)
check("timing compiles onto the rule",
      params == {"enter_after_s": 5.0, "leave_after_s": 2.0}, str(params))
params = {}
wf._count_timing({"id": "n1"}, {"enter_after_s": 0, "leave_after_s": 0},
                 params, lambda *a: None)
check("real time compiles to nothing at all", params == {}, str(params))
params = {}
wf._count_timing({"id": "n1"}, {"enter_after_s": 99999}, params, lambda *a: None)
check("an absurd value is clamped, not passed through",
      params.get("enter_after_s") == 3600.0, str(params))
warned = []
params = {}
wf._count_timing({"id": "n1"}, {"enter_after_s": "later"}, params,
                 lambda nid, _msg: warned.append(nid))
check("a non-number is refused and reported", params == {} and warned == ["n1"])

# Both counters must actually call it, or the setting is a field that does
# nothing on one of them.
csrc = (ROOT / "core" / "services" / "workflow.py").read_text(encoding="utf-8")
# Three call sites now: the zone rules, the whole-frame rules, and the object
# watch -- "must stay before it counts as present" is the same question a
# counter asks, and a watch that answered it differently would be a second
# meaning for one setting.
check("the compiler wires it into every rule that counts a population",
      csrc.count("_count_timing(n, d, params, warn)") == 3)
for node in ("analytics.violations", "analytics.frame"):
    spec = wf.NODE_TYPES[node] if hasattr(wf, "NODE_TYPES") else None
    if spec is None:
        break
    check("%s defaults to real time" % node,
          spec["data"].get("enter_after_s") == 0
          and spec["data"].get("leave_after_s") == 0)

print("== the setting survives the trip to the box")
# It did not, for as long as the feature existed: the compiler wrote both keys
# and validate_function -- which rebuilds params from an allowlist -- dropped
# them again, so every "minimum stay" on every box counted in real time. No
# error anywhere; the only symptom was a counter that did not wait. The whole
# chain is asserted here, because each half was already correct on its own.
from core.services import functions as fsvc                       # noqa: E402

doc = fsvc.validate_function({"id": "zc", "name": "z", "type": "zone_count",
                              "params": {"zone": "z1", "enter_after_s": 10,
                                         "leave_after_s": 2}})
check("the validator keeps the minimum stay",
      doc["params"].get("enter_after_s") == 10.0, str(doc["params"]))
check("and the grace period",
      doc["params"].get("leave_after_s") == 2.0, str(doc["params"]))
doc = fsvc.validate_function({"id": "zc", "name": "z", "type": "zone_count",
                              "params": {"zone": "z1", "enter_after_s": 99999}})
check("an absurd value is clamped there too",
      doc["params"].get("enter_after_s") == 3600.0, str(doc["params"]))
doc = fsvc.validate_function({"id": "zc", "name": "z", "type": "zone_count",
                              "params": {"zone": "z1", "enter_after_s": 0}})
check("a zero is left off -- real time carries no params",
      "enter_after_s" not in doc["params"], str(doc["params"]))
try:
    fsvc.validate_function({"id": "zc", "name": "z", "type": "zone_count",
                            "params": {"zone": "z1", "enter_after_s": "soon"}})
    check("a non-number is refused", False, "accepted")
except ValueError:
    check("a non-number is refused", True)

# compiler -> validator -> the app's own Function, in one line each.
params = {"zone": "z1"}
wf._count_timing({"id": "n1"}, {"enter_after_s": 10, "leave_after_s": 0},
                 params, lambda *a: None)
saved = fsvc.validate_function({"id": "zc", "name": "z", "type": "zone_count",
                                "params": params})
live = Function({**saved, "enabled": True})
check("canvas to running rule, end to end",
      engine()._timing(live) == (10.0, 0.0))

print("== an object that does not count yet says so")
# The number waiting while a Data Table files a row anyway is the setting
# working in one place and not the other, which reads as it not working.
e = engine()
f = fn(enter_after_s=2)
frame = [{"track_id": 1, "in": True, "zones": ["z1"]}]
check("nobody counts on arrival",
      e._timed_members(f, frame, 100.0, keep, zone_id="z1") == [])
check("and the detection carries the wait", frame[0].get("zone_wait") == ["z1"])
later = [{"track_id": 1, "in": True, "zones": ["z1"]}]
check("once the stay is served it counts",
      e._timed_members(f, later, 103.0, keep, zone_id="z1") == [1])
check("and carries no wait", "zone_wait" not in later[0])
unknown = [{"track_id": None, "in": True, "zones": ["z1"]}]
e._timed_members(f, unknown, 104.0, keep, zone_id="z1")
check("an object the tracker has not confirmed waits too",
      unknown[0].get("zone_wait") == ["z1"])
whole = [{"track_id": 9, "in": True}]
e2 = engine()
e2._timed_members(Function({"id": "f2", "type": "frame_count", "name": "f",
                            "params": {"enter_after_s": 2}}),
                  whole, 100.0, keep)
check("a frame counter marks the frame, not a zone",
      whole[0].get("count_wait") is True and "zone_wait" not in whole[0])

print("== the table obeys the counter")
from app import outputs as out                                    # noqa: E402

TABLE = {"id": "t1", "dataset": "d", "mode": "change",
         "columns": [{"name": "who", "src": "event", "field": "track_id",
                      "zone": "z1"},
                     {"name": "n", "src": "stat", "key": "fn_f1_count"}]}
check("the table's id column names its zone", out._identity_zone(TABLE) == "z1")
waiting = [{"track_id": 1, "confidence": 0.9, "zones": ["z1"],
            "zone_wait": ["z1"]}]
check("no row while the only object is still waiting",
      out._dataset_rows(TABLE, out._live_event(waiting), {"fn_f1_count": 0},
                        waiting) == [])
counted = [{"track_id": 1, "confidence": 0.9, "zones": ["z1"]}]
rows = out._dataset_rows(TABLE, out._live_event(counted), {"fn_f1_count": 1},
                         counted)
check("a row the moment it does count",
      len(rows) == 1 and str(rows[0]["who"]).endswith("-1"), str(rows))
mixed = [{"track_id": 1, "confidence": 0.9, "zones": ["z1"],
          "zone_wait": ["z1"]},
         {"track_id": 2, "confidence": 0.8, "zones": ["z1"]}]
rows = out._dataset_rows(TABLE, out._live_event(mixed), {"fn_f1_count": 1},
                         mixed)
check("one waiting, one counting: one row, the right one",
      len(rows) == 1 and str(rows[0]["who"]).endswith("-2"), str(rows))
# A table on a counter with no timing must be untouched -- that is every
# table built before this existed.
plain = [{"track_id": 1, "confidence": 0.9, "zones": ["z1"]},
         {"track_id": 2, "confidence": 0.8, "zones": ["z1"]}]
check("no timing configured, everybody counts as before",
      len(out._dataset_rows(TABLE, out._live_event(plain), {}, plain)) == 2)

print("== the canvas says what is wired to an Alert")
page = (ROOT / "frontend" / "src" / "pages" / "Workflow.jsx").read_text(encoding="utf-8")
# The chip follows whatever NAMES the moment: the old Condition, and the
# Trigger that replaced it (smoke_branch.py).
check("the alert map is built from the edges",
      "alertsBySource" in page
      and '["output.alert", "output.trigger"].includes(target.type)' in page)
check("and handed to the nodes", re.search(r"objClasses,\s*alertsBySource", page))
nodes = (ROOT / "frontend" / "src" / "workflow" / "nodes.jsx").read_text(encoding="utf-8")
check("the chip names the alert rather than counting them",
      "function AlertChips" in page + nodes and "names.slice(0, 3)" in nodes)
check("measuring nodes draw it", nodes.count("<AlertChips id={props.id} />") >= 2)
check("the node face shows the minimum stay",
      "stays " in nodes and "holds" in nodes)

print()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
