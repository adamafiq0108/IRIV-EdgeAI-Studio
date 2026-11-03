"""A line counter's rows are one PER CROSSING, and each says which way.

Reported as "the line counter doesn't count by in/out/crossings, it counts
everything at once". It did -- in the event store, which is what every tally
read back over a date range comes from. The loop collapsed a whole frame into
ONE row: the first line that had any crossing, and the first direction that
was non-zero. So

  * two people through the same door in the same frame wrote one row,
  * an IN and an OUT in the same frame wrote "in" and lost the out,
  * a second counting line crossed in that frame wrote nothing at all,

and a query grouping by `direction` or by `zone` was reading a lump. The live
card was right the whole time (FunctionEngine reads crossing_events, one per
object per line), which is why the two never agreed.

    python tests/smoke_linerows.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import run as apprun                                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


class Pipe:
    id = "cam1"
    gates = {}

    def label(self):
        return "cam1"


DETS = [{"label": "person", "confidence": 0.9, "track_id": 7},
        {"label": "person", "confidence": 0.8, "track_id": 8},
        {"label": "forklift", "confidence": 0.7, "track_id": 9}]

RESULT = {
    "violations": [],
    # Two people through the door in one frame, one each way, plus a forklift
    # over a second line: three crossings, three rows.
    "crossing_events": [
        {"zone": "door", "direction": "in", "label": "person",
         "track_id": 7, "model": "yolov8n"},
        {"zone": "door", "direction": "out", "label": "person",
         "track_id": 8, "model": "yolov8n"},
        {"zone": "aisle", "direction": "cross", "label": "forklift",
         "track_id": 9, "model": "yolov8n"},
    ],
    "crossings": {
        "door": {"name": "Door", "in": 1, "out": 1, "cross": 0},
        "aisle": {"name": "Aisle", "in": 0, "out": 0, "cross": 1},
    },
}


def rows(result, dets=DETS):
    pipe = Pipe()
    pipe.gates = {}
    out = apprun._gated_events(pipe, dets, result, 1000.0, 60.0, 2.0)
    return [(k, r, d) for k, r, d in out if "direction" in (d or {})]


got = rows(RESULT)
check("one row per crossing, not one per frame",
      len(got) == 3,
      f"wrote {len(got)} rows for 3 crossings: {[d for _k, _r, d in got]}")
ways = sorted(d["direction"] for _k, _r, d in got)
check("...each carrying its OWN direction",
      ways == ["cross", "in", "out"],
      f"got {ways} -- an in and an out in one frame must not become one 'in'")
zones = sorted({d["zone"] for _k, _r, d in got})
check("...and its own line",
      zones == ["aisle", "door"],
      "the second line crossed in the same frame was dropped entirely")
check("the row names the class that crossed",
      sorted(d.get("detection_label") for _k, _r, d in got)
      == ["forklift", "person", "person"],
      "a per-class breakdown ('how many forklifts') has nothing to group by")
check("...and the object, so a row is not double-counted on re-delivery",
      sorted(d.get("track_id") for _k, _r, d in got) == [7, 8, 9])
one = [r for _k, r, d in got if d["track_id"] == 9][0]
check("the boxes on the row are the object that crossed",
      len(one) == 1 and one[0]["track_id"] == 9,
      "every crossing carried every detection in frame, so a snapshot of the "
      "crossing showed everybody")
check("the label reads as a crossing",
      all("Door" in k[2] or "Aisle" in k[2] for k, _r, _d in got)
      and any(k[2].endswith("crossed") for k, _r, _d in got),
      "a faceless line says 'crossed', a faced one says in/out")
# The event NAMES its object the way a Time in Zone stay does (detail.track):
# that is what a Data Table row's id column reads first, so a crossing row
# records the crosser and not the most confident bystander in frame.
check("...and names the crosser as detail.track",
      all(d.get("track") == d.get("track_id") for _k, _r, d in got),
      f"{[(d.get('track'), d.get('track_id')) for _k, _r, d in got]}")
untracked = rows({"violations": [], "crossings": {},
                  "crossing_events": [{"zone": "door", "direction": "in",
                                       "label": "person", "track_id": None}]})
check("an untracked crossing carries no track key at all",
      len(untracked) == 1 and untracked[0][2].get("track") is None,
      "None is dropped by run.py's copy loop; a key that IS there must be a "
      "real id, or the id column would record 'None' as text")

# -- a frame with no crossings is unchanged ----------------------------------
quiet = apprun._gated_events(Pipe(), DETS, {"violations": [],
                                            "crossing_events": [],
                                            "crossings": {}},
                             1000.0, 60.0, 2.0)
check("a quiet frame still writes its 'detected' row and nothing else",
      len(quiet) == 1 and not quiet[0][2],
      "the presence event is suppressed only when something crossed")

# -- an engine older than crossing_events still splits ------------------------
legacy = rows({"violations": [], "crossings": RESULT["crossings"]})
check("per-frame deltas alone still give a row per line and per direction",
      len(legacy) == 3
      and sorted(d["direction"] for _k, _r, d in legacy) == ["cross", "in", "out"],
      "the fallback must not re-collapse what the main path splits")

# -- a tracker having a bad second cannot write the day's rows in one frame ---
storm = {"violations": [],
         "crossing_events": [{"zone": "door", "direction": "in",
                              "label": "person", "track_id": i}
                             for i in range(500)],
         "crossings": {"door": {"name": "Door", "in": 500, "out": 0, "cross": 0}}}
check("one frame is capped",
      len(rows(storm, [])) == apprun.MAX_CROSSING_EVENTS == 32,
      "id churn across a line would otherwise write a thousand rows for one "
      "frame and fill the store")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
