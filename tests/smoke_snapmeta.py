# What a snapshot knows about itself: the record beside the photo, the close-ups,
# and the timer -- real data only, never an estimate.
#
#   1. app/snapmeta.py: detection id, close-up boxes (the object that caused it
#      first, padded, clamped, tiny ones skipped), the timer from an event or
#      from a counting rule, the real total filled in when the stay ends
#   2. the app engine: a per-object Cycle Time alarm carries the threshold it
#      crossed; a counting rule reports an object's time so far and tells when
#      a stay ended
#   3. app/store.py: the record and close-ups are written beside the photo and
#      go when the photo goes (delete and prune)
#   4. services/snapshot_meta.py (the master's side): the page's view of a
#      snapshot, an older one read back from its name, the timer figures
#      (threshold 20 s, 40 s on camera = +20 s), and a close-up path that
#      cannot be steered out of its folder
#   5. the Snapshot node: crop and location are node settings, kept for a
#      snapshot and refused on a clip
#
#     py -3 tests/smoke_snapmeta.py
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import snapmeta                                         # noqa: E402
from app.functions import Function, FunctionEngine               # noqa: E402
from app.store import Store                                      # noqa: E402
from app.zones import ZoneEngine, parse_zones                    # noqa: E402
from core.services import detector_control as dc                 # noqa: E402
from core.services import snapshot_meta as sm                    # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


TS = time.mktime((2026, 10, 21, 9, 18, 1, 0, 0, -1))      # 2026-10-21 09:18:01 local

# -- 1. app/snapmeta ------------------------------------------------------------
check("the detection id is the moment, then the object",
      snapmeta.detection_id(TS, 29400) == "20261021_091801_29400")
check("...and only the moment when no object is known",
      snapmeta.detection_id(TS, "") == "20261021_091801")
check("...an object id is cleaned to letters and digits",
      snapmeta.detection_id(TS, "4 2/..") == "20261021_091801_42")

dets = [
    {"label": "person", "track_id": 7, "confidence": 0.60, "box": [100, 100, 200, 300]},
    {"label": "person", "track_id": 29400, "confidence": 0.50, "box": [10, 10, 60, 90]},
    {"label": "person", "track_id": 3, "confidence": 0.99, "box": [300, 300, 305, 304]},
    {"label": "cart", "track_id": None, "confidence": 0.80, "box": [600, 400, 640, 480]},
]
cb = snapmeta.crop_boxes(dets, 640, 480, oid="29400")
check("the object that caused it comes first, then the most confident",
      [c["track"] for c in cb] == [29400, None, 7], [(c["track"], c["primary"]) for c in cb])
check("...only that one is marked primary", [c["primary"] for c in cb] == [True, False, False])
check("...a box too small to show anything is skipped", all(c["track"] != 3 for c in cb))
check("...they are numbered in the order they are cut", [c["n"] for c in cb] == [0, 1, 2])
first = cb[0]["box"]
check("a close-up has room around the box and stays inside the frame",
      first[0] == 4 and first[1] == 0 and first[2] > 60 and first[3] > 90, first)
last = [c for c in cb if c["label"] == "cart"][0]["box"]
check("...clamped at the frame's edge", last[2] == 640 and last[3] == 480, last)
check("no limit beyond MAX_CROPS", len(snapmeta.crop_boxes(
    [{"label": "x", "track_id": i, "confidence": 0.5, "box": [0, 0, 50, 50]} for i in range(30)],
    640, 480)) == snapmeta.MAX_CROPS)
check("no boxes, nothing to cut", snapmeta.crop_boxes([], 640, 480) == []
      and snapmeta.crop_boxes([{"label": "x"}], 640, 480) == [])

# -- only what violates is cut out ---------------------------------------------------
frame_dets = [
    {"label": "holding", "track_id": 1, "box": [10, 10, 80, 120], "zones": ["stairs"]},
    {"label": "not holding", "track_id": 2, "box": [100, 10, 180, 120], "zones": ["stairs"]},
    {"label": "not holding", "track_id": 3, "box": [200, 10, 280, 120], "zones": ["yard"]},
    {"label": "person", "track_id": 4, "box": [300, 10, 380, 120], "zones": ["stairs"],
     "violation": True},
]
want = lambda d: d["label"] == "not holding"                       # the rule's class filter
check("a rule with a class filter: only its classes are the violation",
      [d["track_id"] for d in snapmeta.violating(frame_dets, want, True)] == [2, 3])
check("...and, when the rule is about a zone, only inside that zone",
      [d["track_id"] for d in snapmeta.violating(frame_dets, want, True, zone="stairs")] == [2])
check("a rule with no class filter: the detections flagged as violating",
      [d["track_id"] for d in snapmeta.violating(frame_dets)] == [4])
unflagged = [{k: v for k, v in d.items() if k != "violation"} for d in frame_dets]
check("nothing says which: the object the event is about, never everything",
      [d["track_id"] for d in snapmeta.violating(unflagged, oid="3")] == [3])
check("...and with no object either, nothing at all",
      snapmeta.violating(unflagged) == [] and snapmeta.violating([], want, True) == [])
check("an oid of 0 is an object, not 'no object'",
      [d["track_id"] for d in snapmeta.violating([{"label": "x", "track_id": 0,
                                                    "box": [0, 0, 50, 50]}], oid=0)] == [0])
check("a class filter that matches nothing finds nothing (the object is the fallback)",
      snapmeta.violating(frame_dets, lambda d: False, True) == []
      and [d["track_id"] for d in snapmeta.violating(frame_dets, lambda d: False, True, oid="1")] == [1])
cuts = snapmeta.crop_boxes(snapmeta.violating(frame_dets, want, True, zone="stairs"), 640, 480, oid="2")
check("so the close-ups are one per violating object", len(cuts) == 1 and cuts[0]["label"] == "not holding"
      and cuts[0]["primary"] is True, cuts)

cycle_event = {"type": "alert", "detail": {"function": "dw", "track": 685,
                                           "duration_s": 20.4, "threshold_s": 20.0}}
t = snapmeta.timer_from_event(cycle_event)
check("a Cycle Time alarm gives its clock and its threshold",
      t and t["threshold_s"] == 20.0 and t["elapsed_s"] == 20.4 and t["kind"] == "cycle"
      and t["ended"] is False and t["total_s"] is None, t)
t2 = snapmeta.timer_from_event({"type": "info", "detail": {"function": "c", "track": 4}},
                               {"threshold_s": 5.0, "elapsed_s": 6.2})
check("a counting rule gives the time on camera so far and its setting",
      t2 and t2["kind"] == "count" and t2["threshold_s"] == 5.0 and t2["elapsed_s"] == 6.2, t2)
check("no timer data: no timer",
      snapmeta.timer_from_event({"detail": {"function": "c"}}) is None
      and snapmeta.timer_from_event({"detail": {}}, {"threshold_s": 0, "elapsed_s": 3}) is None
      and snapmeta.timer_from_event({"detail": {"threshold_s": 0, "duration_s": 3}}) is None
      and snapmeta.timer_from_event(None) is None)
meta = snapmeta.build_meta("cap_x", TS, "cam1", "staircase4", "Handrail", "685",
                           cycle_event, "Level 2", cb, t)
check("the record carries the moment, camera, object and timer",
      meta["detection_id"] == "20261021_091801_685" and meta["date"] == "2026-10-21"
      and meta["time"] == "09:18:01" and meta["camera_name"] == "staircase4"
      and meta["location"] == "Level 2" and meta["function"] == "dw"
      and meta["timer"]["threshold_s"] == 20.0 and len(meta["crops"]) == 3, meta)
words = snapmeta.build_meta("cap_w", TS, "c", "", "", "", {"type": "alert"}, "", [], None,
                            violation="Not holding the handrail",
                            notice_title="HANDRAIL VIOLATION NOTICE")
check("the notice words ride in the record",
      words["violation"] == "Not holding the handrail"
      and words["notice_title"] == "HANDRAIL VIOLATION NOTICE", words)
bare = snapmeta.build_meta("cap_y", TS, "cam1", "", "", "", {"type": "alert"}, "", [], None)
check("...and an optional field is absent, not blank",
      "timer" not in bare and "crops" not in bare and "location" not in bare
      and "direction" not in bare, bare)
check("the real total arrives when the stay ends",
      snapmeta.finish_timer(meta, 40.0) and meta["timer"]["total_s"] == 40.0
      and meta["timer"]["ended"] is True)
meta2 = snapmeta.build_meta("cap_z", TS, "c", "", "", "", cycle_event, "", [], dict(t))
check("...never below what was already seen", snapmeta.finish_timer(meta2, 3.0)
      and meta2["timer"]["total_s"] == 20.4)
check("...and a snapshot with no timer ignores it", not snapmeta.finish_timer(bare, 9)
      and not snapmeta.finish_timer(meta, "x"))

# -- 2. the app engine ----------------------------------------------------------------
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
          "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}]
zeng = ZoneEngine(parse_zones(ZONES))
person = {"label": "person", "box": [40, 40, 120, 400], "track_id": 1, "score": 0.9}
dw = Function({"id": "dw", "type": "zone_dwell", "name": "Time",
               "params": {"zone": "bay", "stamp_events": True},
               "triggers": [{"metric": "live_s", "op": ">", "value": 20, "cooldown_s": 0}]})
eng = FunctionEngine([dw], state_dir=None)
alarm = None
stay = None
for sec in range(0, 46):
    here = [person] if sec < 40 else []
    res = zeng.update(here, 640, 480)
    out = eng.update(res, here, {1: object()} if here else {}, zeng.stats(res), 640, 480,
                     zones=zeng.zones, now=float(sec))
    for e in out:
        if (e.get("detail") or {}).get("exited_at") is not None:
            stay = e
        elif e.get("type") == "alert" and alarm is None:
            alarm = e
check("a per-object Cycle Time alarm carries the threshold it crossed",
      alarm and alarm["detail"].get("threshold_s") == 20.0
      and 20.0 < alarm["detail"].get("duration_s") <= 22.0, alarm)
check("...and the stamped stay at the end carries the real duration",
      stay and 39.0 <= stay["detail"].get("duration_s") <= 41.0, stay)
check("...which a snapshot taken on the alarm turns into the user's example: "
      "threshold 20 s, 40 s on camera, +20 s over",
      (lambda m: snapmeta.finish_timer(m, stay["detail"]["duration_s"])
       and sm.view_timer(m["timer"])["exceeded_s"] is not None
       and 19.0 <= sm.view_timer(m["timer"])["exceeded_s"] <= 21.0)(
          snapmeta.build_meta("c", TS, "c", "", "", "1", alarm, "", [],
                              snapmeta.timer_from_event(alarm))))

cnt = Function({"id": "cnt", "type": "frame_count", "name": "Count",
                "params": {"enter_after_s": 5, "leave_after_s": 0}})
eng2 = FunctionEngine([cnt], state_dir=None)
seen_at = None
for sec in range(0, 30):
    here = [person] if sec < 12 else []
    eng2.update({}, here, {1: object()} if here else {}, {}, 640, 480, now=float(sec))
    if sec == 8:
        seen_at = eng2.presence("cnt", 1, now=8.0)
check("a counting rule reports an object's time on camera and its setting",
      seen_at == {"threshold_s": 5.0, "elapsed_s": 8.0}, seen_at)
check("...nothing for an object it has not seen, or a rule with no timer",
      eng2.presence("cnt", 99, now=8.0) is None
      and FunctionEngine([Function({"id": "z", "type": "frame_count", "name": "z",
                                    "params": {}})], state_dir=None).presence("z", 1) is None)
gone = []
eng3 = FunctionEngine([cnt], state_dir=None)
for sec in range(0, 30):
    here = [person] if sec < 12 else []
    eng3.update({}, here, {1: object()} if here else {}, {}, 640, 480, now=float(sec))
    gone += eng3.departed
check("...and tells when a stay ended, with how long it was",
      len(gone) == 1 and gone[0]["fn"] == "cnt" and gone[0]["track"] == 1
      and 10.0 <= gone[0]["total_s"] <= 12.0, gone)

# -- 3. app/store ---------------------------------------------------------------------
tmp = tempfile.mkdtemp()
store = Store(tmp, keep_snapshots=3)
store.save_snapshot("cap_a1", b"jpeg-a")
ok_w = store.save_snapshot_extra("cap_a1", {"id": "cap_a1", "timer": {"threshold_s": 20.0,
                                 "elapsed_s": 20.4, "total_s": None, "ended": False}},
                                 [b"c0", b"c1"])
snaps = os.path.join(tmp, "snapshots")
check("the record and close-ups are written beside the photo",
      ok_w and os.path.isfile(os.path.join(snaps, "cap_a1.json"))
      and os.path.isfile(os.path.join(snaps, ".crops", "cap_a1", "1.jpg")))
check("...the record reads back", (store.snapshot_meta("cap_a1") or {}).get("id") == "cap_a1"
      and store.snapshot_meta("nope") is None)
check("...and the photo list is unchanged by them",
      [r["id"] for r in store.snapshot_list()] == ["cap_a1"])
check("the real total is written into the record",
      store.update_snapshot_meta("cap_a1", lambda m: snapmeta.finish_timer(m, 41.0))
      and store.snapshot_meta("cap_a1")["timer"]["total_s"] == 41.0
      and not store.update_snapshot_meta("nope", lambda m: True))
store.delete_snapshots(["cap_a1"])
check("a deleted photo takes its record and close-ups with it",
      not os.path.exists(os.path.join(snaps, "cap_a1.json"))
      and not os.path.exists(os.path.join(snaps, ".crops", "cap_a1")))
for i in range(6):
    store.save_snapshot(f"cap_p{i}", b"x")
    store.save_snapshot_extra(f"cap_p{i}", {"id": f"cap_p{i}"}, [b"c"])
    os.utime(os.path.join(snaps, f"cap_p{i}.jpg"), (1000 + i, 1000 + i))
store._prune()
left = sorted(n for n in os.listdir(snaps) if n.endswith(".jpg"))
check("pruning past the cap keeps only the newest photos", len(left) == 3, left)
orphans = [n for n in os.listdir(snaps) if n.endswith(".json")
           and n[:-5] + ".jpg" not in left]
orphan_dirs = [n for n in os.listdir(os.path.join(snaps, ".crops"))
               if n + ".jpg" not in left]
check("...and no record or close-up is left behind for a pruned one",
      not orphans and not orphan_dirs, (orphans, orphan_dirs))

# -- 4. the master's side --------------------------------------------------------------
d = tempfile.mkdtemp()
jpg = os.path.join(d, "cap_staircase4_oid685_19a3f2c1d80.jpg")
open(jpg, "wb").write(b"x")
ms = int("19a3f2c1d80", 16)
check("an older snapshot's moment and object are read back from its name",
      sm.derive("cap_staircase4_oid685_19a3f2c1d80", 0) == (ms / 1000.0, "685"), sm.derive(
          "cap_staircase4_oid685_19a3f2c1d80", 0))
check("...an event snapshot's id has neither, so the file time stands",
      sm.derive("1730000000000-3", 123.0) == (123.0, ""))
v0 = sm.view("cap_staircase4_oid685_19a3f2c1d80", jpg, "staircase4", "lot")
check("a snapshot with no record still has an id, a time and a date, and no timer",
      v0["has_record"] is False and v0["detection_id"].endswith("_685")
      and len(v0["date"]) == 10 and len(v0["time"]) == 8 and v0["timer"] is None
      and v0["device"] == "staircase4" and v0["crops"] == [], v0)
rec = snapmeta.build_meta("cap_staircase4_oid685_19a3f2c1d80", TS, "cam1", "staircase4",
                          "Handrail", "685", cycle_event, "Level 2",
                          [{"n": 0, "label": "person", "track": 685, "confidence": 0.9,
                            "box": [1, 2, 3, 4], "primary": True}],
                          snapmeta.timer_from_event(cycle_event))
import json                                                      # noqa: E402
open(sm.meta_path(jpg), "w", encoding="utf-8").write(json.dumps(rec))
os.makedirs(sm.crop_dir(jpg))
open(os.path.join(sm.crop_dir(jpg), "0.jpg"), "wb").write(b"c")
v1 = sm.view("cap_staircase4_oid685_19a3f2c1d80", jpg, "", "lot")
check("a snapshot with a record reads from it",
      v1["has_record"] and v1["detection_id"] == "20261021_091801_685"
      and v1["date"] == "2026-10-21" and v1["time"] == "09:18:01"
      and v1["location"] == "Level 2" and v1["device"] == "staircase4", v1)
check("...with its close-ups, and only the ones that exist",
      [c["n"] for c in v1["crops"]] == [0] and v1["crops"][0]["primary"] is True)
check("...and a timer that is still running shows no total and no excess",
      v1["timer"]["ended"] is False and v1["timer"]["total_s"] is None
      and v1["timer"]["exceeded_s"] is None and v1["timer"]["threshold_s"] == 20.0, v1["timer"])
ex = sm.view_timer({"kind": "cycle", "threshold_s": 20.0, "elapsed_s": 20.4,
                    "total_s": 40.0, "ended": True})
check("threshold 20 s and 40 s on camera: total 40 s, +20 s over",
      ex["threshold_s"] == 20.0 and ex["total_s"] == 40.0 and ex["exceeded_s"] == 20.0, ex)
check("a stay under the threshold shows 0 s over, not a negative",
      sm.view_timer({"threshold_s": 20.0, "total_s": 15.0, "ended": True})["exceeded_s"] == 0.0)
check("no threshold, no timer: nothing is shown",
      sm.view_timer(None) is None and sm.view_timer({}) is None
      and sm.view_timer({"threshold_s": 0, "total_s": 5, "ended": True}) is None
      and sm.view_timer({"threshold_s": "x"}) is None)
check("an 'ended' record with no usable total is shown as still running",
      sm.view_timer({"threshold_s": 20, "ended": True, "total_s": None})["ended"] is False)
check("a close-up path is a plain number inside the snapshot's own folder",
      sm.crop_path(jpg, 0) and sm.crop_path(jpg, "0")
      and sm.crop_path(jpg, "../0") is None and sm.crop_path(jpg, "0/../../x") is None
      and sm.crop_path(jpg, "") is None and sm.crop_path(jpg, 5) is None
      and sm.crop_path(jpg, "-1") is None)
sm.remove_extras(jpg)
check("remove_extras clears the record and the close-ups",
      not os.path.exists(sm.meta_path(jpg)) and not os.path.exists(sm.crop_dir(jpg)))

# -- 5. the Snapshot node --------------------------------------------------------------
spec = wf.REGISTRY["output.snapshot"]["data"]
check("the Snapshot node has a crop switch (off) and the notice words (blank)",
      spec.get("crop") is False and spec.get("location") == ""
      and spec.get("violation") == "" and spec.get("notice_title") == "", spec)
check("...and the Record Clip node does not",
      "crop" not in wf.REGISTRY["output.record"]["data"]
      and "location" not in wf.REGISTRY["output.record"]["data"])
base = {"id": "o1", "type": "snapshot", "trigger": "alert", "enabled": True}
got = dc._validate_output({**base, "crop": True, "location": "  Staircase   4,  level 2  "})
check("a snapshot keeps crop and a tidied location",
      got.get("crop") is True and got.get("location") == "Staircase 4, level 2", got)
got = dc._validate_output({**base, "violation": " Not  holding the handrail ",
                           "notice_title": "HANDRAIL VIOLATION NOTICE"})
check("...and the violation and the notice title",
      got.get("violation") == "Not holding the handrail"
      and got.get("notice_title") == "HANDRAIL VIOLATION NOTICE", got)
got = dc._validate_output({**base, "crop": False, "location": ""})
check("...and an off switch or blank location is simply absent",
      "crop" not in got and "location" not in got, got)
got = dc._validate_output({**base, "type": "record", "crop": True, "location": "x",
                           "violation": "x", "notice_title": "x"})
check("a clip refuses all of it", not any(k in got for k in
      ("crop", "location", "violation", "notice_title")), got)
got = dc._validate_output({**base, "location": "x" * 300})
check("a location is cut to 120 characters", len(got.get("location", "")) == 120)

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
