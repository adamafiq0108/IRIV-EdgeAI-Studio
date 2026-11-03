# A "new object" Trigger feeding a Data Table: ONE row per object, written
# once, when that object first violates (pcb_sensata, 2026-10-13).
#
# Seen on the box: the table took a pair of rows (the same two object ids)
# about every second. Two causes, both checked here:
#
#   A. app/functions.py remembered only who was present THIS frame, so a
#      tracker missing one of two people for a frame (or the two alternating)
#      made the returning one "new" and re-fired the alert. Now an announced
#      object is kept until it has been gone NEW_OBJECT_GRACE_S, and the
#      condition letting go wipes the slate only after the same grace.
#   B. app/outputs.py _dataset_rows wrote a row for EVERY tracked object in
#      the zone on each alert, so a newcomer re-filed whoever was already
#      there. An event that names its new objects (detail.new) now gets rows
#      for them only; any other event fans out as before.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import outputs                                           # noqa: E402
from app import functions as fmod                                 # noqa: E402
from app.functions import Function, FunctionEngine                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


RUN = outputs._RUN_ID
GRACE = fmod.NEW_OBJECT_GRACE_S
OIDK = "fn_tgo_trg_cur_oid_value"


def rule(limit=0, **trig):
    t = {"metric": "count", "op": ">", "value": limit, "severity": "crit",
         "message": "violation", "cooldown_s": 30,
         "oid_source": "f1", "repeat": "new_object"}
    t.update(trig)
    return Function({"id": "f1", "type": "zone_count", "name": "z",
                     "params": {"zone": "z1"}, "triggers": [t]})


def engine(fn, stored_oid=None):
    e = FunctionEngine.__new__(FunctionEngine)
    e.functions, e._trig, e._stats, e._live_oids = [fn], {}, {}, {}
    e._scope = f"grace-{id(e)}"
    if stored_oid is not None:
        e._stats[OIDK] = stored_oid
    return e


def run(e, frames):
    """[(t, who)] -> [event] fired. The count IS len(who)."""
    out = []
    for t, who in frames:
        e._stats["fn_f1_count"] = len(who)
        e._live_oids = {"f1": set(who)}
        for ev in e._check_triggers(t):
            out.append((t, ev))
    return out


print("== (1) a tracker flicker is not a newcomer")
e = engine(rule())
got = run(e, [(0, {1, 16}), (1, {1}), (2, {16}), (3, {1, 16}), (4, {16}),
              (5, {1}), (6, {1, 16})])
check("exactly one event for the pair, no re-fire",
      [t for t, _ in got] == [0], str([(t, ev.get("detail")) for t, ev in got]))
check("...and it names both as new",
      got and got[0][1]["detail"]["new"] == [1, 16], str(got))

print("== (1b) a one-frame dip of the condition is the same violation")
e = engine(rule(limit=1))
got = run(e, [(0, {1, 16}), (1, {1}), (2, {1, 16}), (3, set()), (4, {1, 16})])
check("the count dipping under the limit for a frame re-announces nobody",
      [t for t, _ in got] == [0], str(got))

print("== (2) gone longer than the grace: new again")
e = engine(rule())
got = run(e, [(0, {1, 16}), (1, {1}), (1 + GRACE + 1, {1}),
              (1 + GRACE + 2, {1, 16})])
check("16 returning after the grace fires, for 16 alone",
      [t for t, _ in got] == [0, 1 + GRACE + 2]
      and got[-1][1]["detail"]["new"] == [16], str(got))

print("== (3) B joins while A is already announced / stored")
TABLE = {"id": "wf_ds", "type": "dataset", "enabled": True, "mode": "alert",
         "dataset": "violations", "name": "Violations",
         "columns": [{"name": "object_id", "type": "text", "src": "event",
                      "field": "track_id", "zone": "z1"}]}
A = {"label": "person", "confidence": 0.95, "track_id": 1, "zones": ["z1"]}
B = {"label": "person", "confidence": 0.60, "track_id": 16, "zones": ["z1"]}


def as_run_py(ev, dets):
    """The event run.py builds from a fired trigger: the frame's detections,
    with the trigger's own detail merged in."""
    full = {"ts": "2026-10-13T10:00:00", "type": "alert",
            "detail": {"detections": dets}}
    full["detail"].update(ev.get("detail") or {})
    return full


e = engine(rule())
got = run(e, [(0, {1}), (1, {1}), (2, {1, 16})])
check("A first, then B: two events", [t for t, _ in got] == [0, 2], str(got))
ev = got[-1][1]
check("B's event says new == [16], present == [1, 16]",
      ev["detail"] == {"new": [16], "present": [1, 16]}, str(ev))
rows = outputs._dataset_rows(TABLE, as_run_py(ev, [A, B]), {})
check("_dataset_rows writes ONE row, for B -- not A again",
      [r["object_id"] for r in rows] == [f"{RUN}-16"], str(rows))

# Same with the Current OID guard holding A (the table's newest row is A):
# detail.new is the post-guard set, so A is never re-written.
e = engine(rule(cur_oid_key=OIDK), stored_oid=f"{RUN}-1")
got = run(e, [(0, {1, 16})])
check("guard: an edge with stored A and new B names B only",
      len(got) == 1 and got[0][1]["detail"]["new"] == [16]
      and got[0][1]["source_oid"] == 16, str(got))
rows = outputs._dataset_rows(TABLE, as_run_py(got[0][1], [A, B]), {})
check("...and the table gets B's row alone",
      [r["object_id"] for r in rows] == [f"{RUN}-16"], str(rows))

# The rising edge with two new people files both, once.
e = engine(rule())
got = run(e, [(0, {1, 16})])
rows = outputs._dataset_rows(TABLE, as_run_py(got[0][1], [A, B]), {})
check("a first violation by two people: two rows, one each",
      sorted(r["object_id"] for r in rows) == sorted([f"{RUN}-1", f"{RUN}-16"]),
      str(rows))
# A run-prefixed id in detail.new matches the same object.
rows = outputs._dataset_rows(
    TABLE, as_run_py({"detail": {"new": [f"{RUN}-16"]}}, [A, B]), {})
check("a run-prefixed id in detail.new is the same object",
      [r["object_id"] for r in rows] == [f"{RUN}-16"], str(rows))

print("== (4) an event without detail.new is unchanged")
rows = outputs._dataset_rows(TABLE, as_run_py({}, [A, B]), {})
check("timer / other events still fan out over everyone in the zone",
      sorted(r["object_id"] for r in rows) == sorted([f"{RUN}-1", f"{RUN}-16"]),
      str(rows))
rows = outputs._dataset_rows(TABLE, None, {}, [A, B])
check("...the clock-paced path too (no event)",
      sorted(r["object_id"] for r in rows) == sorted([f"{RUN}-1", f"{RUN}-16"]),
      str(rows))
plain = {"id": "t", "type": "dataset", "columns": [
    {"name": "n", "type": "number", "src": "stat", "key": "k"}]}
rows = outputs._dataset_rows(plain, as_run_py({"detail": {"new": [16]}}, [A, B]),
                             {"k": 3})
check("a table with no id column is still one row", rows == [{"n": 3.0}], str(rows))
e = engine(rule(repeat=""))
got = run(e, [(t, {1, 16}) for t in range(0, 31)])
check("a cooldown trigger still repeats on its clock, with no detail.new",
      [t for t, _ in got] == [0, 30] and all("detail" not in ev for _, ev in got),
      str([(t, ev.get("detail")) for t, ev in got]))

print("== (5) the condition lets go longer than the grace: fresh slate")
e = engine(rule(limit=1))
got = run(e, [(0, {1, 16}), (1, {1}), (1 + GRACE, {1}), (2 + GRACE, {1, 16})])
check("the next violation is a fresh alarm for the same ids",
      [t for t, _ in got] == [0, 2 + GRACE]
      and got[-1][1]["detail"]["new"] == [1, 16], str(got))
e = engine(rule(limit=1))
got = run(e, [(0, {1, 16}), (1, {1}), (5, {1}), (6, {1, 16})])
check("...but let go for less than the grace, nobody is new",
      [t for t, _ in got] == [0], str(got))

print("\n%s" % ("all ok" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
