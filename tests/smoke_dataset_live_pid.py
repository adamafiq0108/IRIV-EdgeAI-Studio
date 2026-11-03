"""A Data Table records WHICH objects it counted -- one row each.

Three things this pins, each of which made the pid column useless in a live
scene:

1. `every`/`change` rows are not caused by an event, so the row builder was
   handed event=None and every event-sourced column came out empty. A table
   logging live counting showed "1.0" with a blank pid for ever.

2. A row holds ONE identity, so one row per write can only name one of the
   people in frame -- and whichever tie-break picks it hides the others. With
   three people in view the count said three and the pid column said the same
   one every time. A table that records identity now gets a row per object.

3. The tracker numbers from 1 again on every restart, so a bare id is unique
   only within one run: yesterday's object 3 and today's object 3 are
   different objects that a distinct count would merge. Recorded run-scoped.

    python tests/smoke_dataset_live_pid.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                                "systems", "demo"))
from app import outputs as out           # noqa: E402


def pid(n):
    """A track id as it is RECORDED: run-scoped."""
    return f"{out._RUN_ID}-{n}"


def table(mode="change", columns=None):
    return {"id": "wf_t1", "type": "dataset", "enabled": True, "dataset": "counting",
            "name": "Counting", "mode": mode, "interval_s": 60, "min_interval_s": 5,
            "columns": columns or [
                {"name": "live_counting", "type": "number", "src": "wire",
                 "key": "fn_in_frame_now_count"},
                # text, not number: the compiler forces this, because the query
                # side casts a number column and one run-scoped id there would
                # break every query over the table.
                {"name": "pid", "type": "text", "src": "event",
                 "field": "track_id"}]}


def det(track_id, conf=0.9):
    return {"label": "person", "confidence": conf, "track_id": track_id,
            "box": [0, 0, 10, 10]}


GHOST = {"label": "person", "confidence": 0.99, "box": [0, 0, 5, 5]}


def run(o, stats, dets, now, rows):
    out._dataset_tick(o, stats, now, {"dataset": lambda _o, c: rows.append(c)}, dets)


bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}")
    if not ok:
        print(f"      wanted {want!r}")
        bad += 1


# -- change mode: the objects actually in frame --------------------------------
out._dataset_state.clear()
rows = []
o = table("change")
run(o, {"fn_in_frame_now_count": 1.0}, [det(7)], 1000.0, rows)
check("change mode records the tracked object", rows[-1],
      {"live_counting": 1.0, "pid": pid(7)})

# A DIFFERENT person is a change, so the table gets a row for them too.
run(o, {"fn_in_frame_now_count": 1.0}, [det(8)], 1100.0, rows)
check("a new pid at the same count is a change", rows[-1],
      {"live_counting": 1.0, "pid": pid(8)})
check("one row each", len(rows), 2)

# The same person at the same count is NOT.
run(o, {"fn_in_frame_now_count": 1.0}, [det(8)], 1200.0, rows)
check("the same object writes nothing new", len(rows), 2)

# Empty frame: a count with nobody in it, and pid says so rather than lying.
run(o, {"fn_in_frame_now_count": 0.0}, [], 1300.0, rows)
check("an empty frame records a null pid", rows[-1],
      {"live_counting": 0.0, "pid": None})

# -- one row PER OBJECT --------------------------------------------------------
# The whole point: a row names one object, so three people in frame need three
# rows. One row would name one of them and silently drop the other two, and a
# distinct count over the table would undercount by exactly those.
out._dataset_state.clear()
rows = []
run(table("change"), {"fn_in_frame_now_count": 3.0},
    [det(11, 0.4), det(12, 0.95), det(13, 0.7)], 2000.0, rows)
check("three in frame is three rows", len(rows), 3)
check("...each naming a different object",
      sorted(r["pid"] for r in rows), sorted([pid(11), pid(12), pid(13)]))
check("...all carrying the same count",
      {r["live_counting"] for r in rows}, {3.0})

# Untracked objects do not get rows of their own: there is nothing to say
# about them that the count does not already say.
out._dataset_state.clear()
rows = []
run(table("change"), {"fn_in_frame_now_count": 2.0}, [det(21), GHOST], 2100.0, rows)
check("an unconfirmed detection adds no row", len(rows), 1)
check("...and the tracked one is recorded", rows[0]["pid"], pid(21))

# Nothing confirmed at all: still record the count, with the identity blank.
# Dropping the row would lose the count too.
out._dataset_state.clear()
rows = []
run(table("change"), {"fn_in_frame_now_count": 1.0}, [GHOST], 2200.0, rows)
check("nothing tracked still records the count", rows[-1],
      {"live_counting": 1.0, "pid": None})

# -- "changed" is per object, not per write ------------------------------------
# One person moving is not a reason to re-record everyone standing still, and
# a new arrival must not be suppressed because the write looks like the last.
out._dataset_state.clear()
rows = []
o = table("change")
run(o, {"fn_in_frame_now_count": 2.0}, [det(31), det(32)], 3000.0, rows)
check("two objects, two rows", len(rows), 2)
run(o, {"fn_in_frame_now_count": 2.0}, [det(31), det(32)], 3100.0, rows)
check("neither changed, so nothing is written", len(rows), 2)
run(o, {"fn_in_frame_now_count": 3.0}, [det(31), det(32), det(33)], 3200.0, rows)
# ONLY the newcomer. The scene count moved from 2 to 3, but that is news about
# the scene, not about 31 and 32 -- who have not moved and must not be
# re-recorded. Comparing the whole row used to re-write every object every
# time the count flickered, which is how one person in view produced a page of
# near-identical rows a second.
check("a third arrival writes ONE row -- the newcomer's", len(rows), 3)
check("...and it is the newcomer", rows[-1]["pid"], pid(33))

# -- a timestamp column is not a change ---------------------------------------
out._dataset_state.clear()
rows = []
o = table("change", [
    {"name": "live_counting", "type": "number", "src": "wire",
     "key": "fn_in_frame_now_count"},
    {"name": "seen", "type": "timestamp", "src": "event", "field": "ts"}])
run(o, {"fn_in_frame_now_count": 1.0}, [det(3)], 4000.0, rows)
run(o, {"fn_in_frame_now_count": 1.0}, [det(3)], 4100.0, rows)
run(o, {"fn_in_frame_now_count": 1.0}, [det(3)], 4200.0, rows)
check("a ts column does not make every tick a change", len(rows), 1)
check("...but the ts is still recorded", bool(rows[0].get("seen")), True)
run(o, {"fn_in_frame_now_count": 2.0}, [det(3)], 4300.0, rows)
check("a real change still writes", len(rows), 2)
# A table with no identity column keeps the old shape: one row per write,
# however many objects are in frame. Multiplying rows there would only inflate
# the counts made from them.
out._dataset_state.clear()
rows = []
run(o, {"fn_in_frame_now_count": 3.0}, [det(41), det(42), det(43)], 4400.0, rows)
check("no identity column means one row per write", len(rows), 1)

# -- every mode fills identity too --------------------------------------------
out._dataset_state.clear()
rows = []
o = table("every")
o["interval_s"] = 10
run(o, {"fn_in_frame_now_count": 1.0}, [det(21)], 5000.0, rows)
check("timer mode records the object as well", rows[-1]["pid"], pid(21))
run(o, {"fn_in_frame_now_count": 1.0}, [det(21)], 5005.0, rows)
check("...and still respects its interval", len(rows), 1)

# -- run-scoped ids ------------------------------------------------------------
# The reason for the prefix: object 3 from before a restart and object 3 after
# it are different people. Without it a distinct count over a day merges them.
check("a recorded id carries the run", pid(3).endswith("-3") and len(pid(3)) > 2,
      True)
check("two ids in one run share the prefix",
      pid(3).rsplit("-", 1)[0], pid(4).rsplit("-", 1)[0])

# -- the alert path ------------------------------------------------------------
rows = []
event = {"ts": "2026-08-07T17:16:42",
         "detail": {"detections": [det(51), det(52)]}}
out._dataset(table("alert"), {"dataset": lambda _o, c: rows.append(c)},
             event, {"fn_in_frame_now_count": 2.0})
check("the alert path reads its own event, one row per object",
      sorted(r["pid"] for r in rows), sorted([pid(51), pid(52)]))

# -- a clock-paced table is NOT also written by every alert -------------------
# `every` and `change` compile with trigger "any" because they are paced by
# the clock rather than by a condition. fire() therefore reaches them on every
# event, and writing a row there would go past the interval floor and past the
# change test -- which are the two things those modes ARE.
hooks = {"dataset": lambda _o, c: rows.append(c)}
for mode in ("change", "every"):
    rows = []
    out._fire_one(table(mode), hooks, event, {"fn_in_frame_now_count": 1.0})
    check(f"an alert does not write a {mode!r} table", len(rows), 0)
rows = []
out._fire_one(table("alert"), hooks, event, {"fn_in_frame_now_count": 1.0})
check("...but an 'alert' table is still written", len(rows), 2)

# -- every attribute the loop stashes on a _Pipe is declared -------------------
# _Pipe uses __slots__, so `pipe.whatever = x` for an undeclared name raises on
# the first frame and the DETECTOR DIES ON START -- there is no degraded mode
# and no import-time warning, which is how it reached the Pi. A source check
# because constructing a real _Pipe here would need a camera and cv2.
import re                                                        # noqa: E402

src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "systems", "demo", "app", "run.py"),
           encoding="utf-8").read()
# Ended at the tuple's own close (a quote then ")"), not the first ")": the
# slot comments may hold parentheses -- "(seq, jpeg)" cut paint_key and
# paint_frame out of the list and failed this check on a correct file.
slots = set(re.findall(r'"(\w+)"',
                       src.split('class _Pipe')[1].split("__slots__ = (")[1]
                          .split('")')[0] + '"'))
assigned = set(re.findall(r"^\s*(?:pipe|primary|p)\.(\w+)\s*=(?!=)", src,
                          re.MULTILINE))
check("_Pipe declares every attribute the loop sets on it",
      sorted(assigned - slots), [])

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
