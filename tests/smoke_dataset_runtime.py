# The runtime half of a Dataset: assembling one row, and the three ways a row
# gets written (on alert, on a timer, on change).
#
# The engine never sees the workflow graph -- the compiler already turned a
# wired column into a stat key -- so everything here is a dict lookup, which is
# exactly what makes a row reproducible weeks later.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import outputs as out                               # noqa: E402

COLUMNS = [
    {"name": "people", "type": "number", "src": "wire", "key": "fn_bay_count"},
    {"name": "dwell", "type": "number", "src": "stat", "key": "fn_bay_avg_s"},
    {"name": "zone", "type": "text", "src": "event", "field": "zone"},
    {"name": "who", "type": "text", "src": "event", "field": "track_id"},
    {"name": "shift", "type": "text", "src": "const", "value": "night"},
]
DOC = {"id": "wf_ds", "type": "dataset", "enabled": True, "dataset": "bay_log",
       "name": "Bay log", "mode": "alert", "trigger": "alert",
       "function": "bay", "columns": COLUMNS}
STATS = {"fn_bay_count": 4, "fn_bay_avg_s": 12.5}
EVENT = {"id": "e1", "ts": "2026-08-06T10:00:00", "type": "alert",
         "label": "Bay busy",
         "detail": {"function": "bay", "zone": "Bay",
                    "detections": [{"label": "person", "confidence": 0.4,
                                    "track_id": 7},
                                   {"label": "person", "confidence": 0.9,
                                    "track_id": 9}]}}

# 1. one row, every column source
row = out._dataset_row(DOC, EVENT, STATS)
# A track id is recorded RUN-SCOPED ("<run>-9"): the tracker numbers from 1
# again on every restart, so a bare id is unique only within one run and a
# distinct count over a day would merge two different objects into one.
assert row == {"people": 4.0, "dwell": 12.5, "zone": "Bay",
               "who": f"{out._RUN_ID}-9", "shift": "night"}, row
print("== a row draws from all four sources:", row)

# 2. the event column takes the STRONGEST detection, matching how the master
#    expands an event -- a per-frame event is about its most confident object
assert row["who"].endswith("-9"), "the weaker detection's track id won"
print("== an event column follows the strongest detection")

# 3. a missing value is a HOLE, not a missing column: a dataset whose columns
#    come and go cannot be charted
row = out._dataset_row(DOC, None, {})
assert set(row) == {"people", "dwell", "zone", "who", "shift"}, row
assert row["people"] is None and row["shift"] == "night", row
print("== a missing value leaves a hole, never a missing column:", row)

# 4. a number column that cannot be a number stays empty rather than storing
#    text in a column something will later try to average
row = out._dataset_row(DOC, None, {"fn_bay_count": "not a number"})
assert row["people"] is None, row
print("== text in a number column is stored as nothing, not as text")

# 5. a datetime column is normalised to ISO on the way in. This is what lets
#    the query side treat it as plain text (ISO sorts chronologically) instead
#    of casting -- Postgres has no try_cast, so one bad value in one row would
#    otherwise break every query over the whole table.
assert out._as_iso("2026-08-06T14:02:11") == "2026-08-06T14:02:11"
assert out._as_iso("06/08/2026 14:02:11") == "2026-08-06T14:02:11"
assert out._as_iso("2026/08/06") == "2026-08-06T00:00:00"
assert out._as_iso(1754481731).startswith("20"), out._as_iso(1754481731)
print("== several written forms all normalise to ISO:",
      out._as_iso("06/08/2026 14:02:11"))

# ...and one that is not a time at all is stored as nothing. A datetime column
# holding "shift B" sorts wrongly for ever afterwards.
for junk in ("shift B", "", None, "13-45-99"):
    assert out._as_iso(junk) is None, (junk, out._as_iso(junk))
print("== a value that is not a time is stored as nothing, not as text")

# and the whole row honours it
dated = {**DOC, "columns": [
    {"name": "started", "type": "datetime", "src": "event", "field": "ts"},
    {"name": "note", "type": "datetime", "src": "const", "value": "shift B"}]}
row = out._dataset_row(dated, EVENT, STATS)
assert row["started"] == "2026-08-06T10:00:00", row
assert row["note"] is None, row
print("== a datetime column records ISO and refuses non-times:", row)

# ISO is what makes text sorting chronological -- the property the repo relies
# on. Check it actually holds across a month and a year boundary.
stamps = ["2026-08-06T09:00:00", "2026-12-31T23:59:00", "2026-07-30T09:00:00",
          "2027-01-01T00:01:00"]
assert sorted(stamps) == ["2026-07-30T09:00:00", "2026-08-06T09:00:00",
                          "2026-12-31T23:59:00", "2027-01-01T00:01:00"]
print("== ISO text sorts chronologically, which is why no cast is needed")


# -- the three write modes ---------------------------------------------------
def collect():
    rows = []
    return rows, {"dataset": lambda o, cols: rows.append(cols)}


# alert: one row per TRACKED OBJECT per firing, through the ordinary output
# path. A row names one object, so an event about two people needs two rows --
# with one, whichever tie-break picked it would hide the other, and a distinct
# count over the table would undercount by exactly the people it never named.
rows, hooks = collect()
out.fire([DOC], EVENT, hooks, STATS)
out.fire([DOC], EVENT, hooks, STATS)
assert len(rows) == 4, rows            # 2 firings x 2 tracked objects
assert sorted({r["who"] for r in rows}) == sorted(
    [f"{out._RUN_ID}-7", f"{out._RUN_ID}-9"]), rows
print("== alert mode writes a row per object per firing:", len(rows))

# ...and an event raised by a DIFFERENT function does not write a row
rows, hooks = collect()
other = {**EVENT, "detail": {**EVENT["detail"], "function": "door"}}
out.fire([DOC], other, hooks, STATS)
assert rows == [], rows
print("== another function's alert writes nothing")

# every: on a schedule, alert or no alert
timed = {**DOC, "mode": "every", "trigger": "any", "interval_s": 10}
rows, hooks = collect()
out._dataset_state.clear()
out.publish([timed], STATS, now=1000.0, hooks=hooks)
out.publish([timed], STATS, now=1005.0, hooks=hooks)     # too soon
out.publish([timed], STATS, now=1011.0, hooks=hooks)
assert len(rows) == 2, rows
print("== timer mode honours its interval:", len(rows), "rows over 11s at 10s")

# change: only when something actually moved, and never faster than the floor.
#
# NO identity column here on purpose: a table that records a track id compares
# only that object's own columns (see the identity block below), so a table
# asking about the SCENE is the one that answers "did this stat move".
watch = {**DOC, "mode": "change", "trigger": "any", "min_interval_s": 2,
         "columns": [c for c in COLUMNS if c["name"] != "who"]}
rows, hooks = collect()
out._dataset_state.clear()
out.publish([watch], STATS, now=1000.0, hooks=hooks)                  # first
out.publish([watch], STATS, now=1010.0, hooks=hooks)                  # same
out.publish([watch], {**STATS, "fn_bay_count": 5}, now=1011.0, hooks=hooks)
assert len(rows) == 2, rows
assert rows[0]["people"] == 4.0 and rows[1]["people"] == 5.0, rows
print("== change mode writes only when a value moved:", [r["people"] for r in rows])

# ...and a value that jitters every frame is not a change every frame
rows, hooks = collect()
out._dataset_state.clear()
out.publish([watch], {"fn_bay_count": 1}, now=1000.0, hooks=hooks)
for i in range(20):
    out.publish([watch], {"fn_bay_count": i}, now=1000.0 + i * 0.1, hooks=hooks)
assert len(rows) == 1, f"{len(rows)} rows in two seconds -- the floor did not hold"
print("== a jittering value is rate-limited to", len(rows), "row in 2s")

# ...but the change test itself runs EVERY frame, not once per floor. This is
# the zone-visit case: someone is inside for 0.6s, which is shorter than any
# sane rate limit, and the row must still be written. The old code sampled at
# the floor and missed the visit entirely.
live = {**watch, "min_interval_s": 0}
rows, hooks = collect()
out._dataset_state.clear()
out.publish([live], {"fn_bay_count": 0}, now=1000.0, hooks=hooks)
out.publish([live], {"fn_bay_count": 1}, now=1000.3, hooks=hooks)     # arrives
out.publish([live], {"fn_bay_count": 0}, now=1000.9, hooks=hooks)     # gone
assert [r["people"] for r in rows] == [0.0, 1.0, 0.0], rows
print("== real time catches a visit shorter than any floor:",
      [r["people"] for r in rows])

# The ceiling that makes a zero floor safe: every row is also an events.jsonl
# entry, so a value moving every frame for ever must still be bounded.
rows, hooks = collect()
out._dataset_state.clear()
for i in range(200):
    out.publish([live], {"fn_bay_count": i}, now=2000.0 + i * 0.001, hooks=hooks)
assert len(rows) == out._MAX_DATASET_ROWS_PER_S, len(rows)
out.publish([live], {"fn_bay_count": 999}, now=2001.5, hooks=hooks)   # next second
assert len(rows) == out._MAX_DATASET_ROWS_PER_S + 1, len(rows)
print("== a zero floor is capped at", out._MAX_DATASET_ROWS_PER_S,
      "rows a second, and the cap resets")

# A table that records an IDENTITY compares only that object's own columns. A
# scene-wide stat flickering is not news about a standing object, and treating
# it as one is what produced a page of near-identical rows a second: same id,
# live_counting 1/0/1, over and over.
keyed = {**DOC, "mode": "change", "trigger": "any", "min_interval_s": 0}
one = [{"label": "person", "confidence": 0.9, "track_id": 41}]
two = one + [{"label": "person", "confidence": 0.8, "track_id": 42}]
rows, hooks = collect()
out._dataset_state.clear()
out.publish([keyed], {"fn_bay_count": 1}, now=3000.0, hooks=hooks, dets=one)
assert len(rows) == 1, rows
for i, n in enumerate((0, 1, 0, 1, 0)):                # the scene number flickers
    out.publish([keyed], {"fn_bay_count": n}, now=3000.1 + i * 0.1,
                hooks=hooks, dets=one)
assert len(rows) == 1, f"{len(rows)} rows -- a scene number re-recorded a " \
                       f"standing object"
print("== a scene number moving does not re-record the same object:", len(rows))

out.publish([keyed], {"fn_bay_count": 2}, now=3001.0, hooks=hooks, dets=two)
assert len(rows) == 2 and rows[-1]["who"].endswith("-42"), rows
print("== but a new object writes its own row:", rows[-1]["who"])

# ...and the object's OWN value moving still writes: 41 walks into the zone.
out.publish([keyed], {"fn_bay_count": 2}, now=3002.0, hooks=hooks,
            dets=[{**one[0], "zone": "Bay"}])
assert len(rows) == 3 and rows[-1]["zone"] == "Bay", rows
print("== an object's own value moving still writes:", rows[-1]["zone"])

# 5. no hook wired (a build with no event store) must not raise
out._dataset_state.clear()
out.publish([timed], STATS, now=2000.0, hooks=None)
out.fire([DOC], EVENT, None, STATS)
print("== with no store hook it does nothing, quietly")
print("ALL OK")
