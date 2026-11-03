# Hailo-named tracker knobs + confirmation: a one-frame ghost never mints a
# PID, a real object keeps one id, and the settings round-trip the API names.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app.tracker import Tracker                                # noqa: E402

tr = Tracker()          # exact Hailo defaults: 0.8/0.9/0.7/2/15/2


def frame(dets, t):
    tr.update(dets, 640, 480, now=t)
    return {d.get("track_id") for d in dets if "track_id" in d}


BOX = [200, 150, 300, 350]
NEAR = [205, 152, 305, 352]
GHOST = [500, 50, 560, 150]

# 1. frame one: brand-new object -> NO public id yet (keep_new_frames=2)
t = 1000.0
ids = frame([{"label": "person", "box": BOX}], t)
assert ids == set(), f"a first-frame object got an id: {ids}"
print("== frame 1: new object has no public id yet")

# 2. frame two: still there -> confirmed, id appears and stays stable
ids2 = frame([{"label": "person", "box": NEAR}], t + 0.05)
assert len(ids2) == 1, ids2
pid = ids2.pop()
ids3 = frame([{"label": "person", "box": BOX}], t + 0.10)
assert ids3 == {pid}, (ids3, pid)
print(f"== frame 2+: confirmed as pid {pid}, stable across frames")

# 3. a one-frame GHOST elsewhere never gets an id, and is gone once its grace
#    runs out. An unconfirmed track survives KEEP_NEW_GRACE misses -- a real
#    detector drops a real person for a frame constantly, and killing the
#    track each time rebuilds it under a new id for ever. What must hold is
#    that the ghost never mints a PID, not that it dies instantly.
ids4 = frame([{"label": "person", "box": BOX},
              {"label": "person", "box": GHOST}], t + 0.15)
assert ids4 == {pid}, f"a ghost minted a PID: {ids4}"
for i in range(tr.keep_new_grace + 1):                # ghost missed
    ids = frame([{"label": "person", "box": BOX}], t + 0.20 + i * 0.05)
    assert ids == {pid}, f"a ghost minted a PID while expiring: {ids}"
ghost_tracks = [i for i in set(tr.tracks) if i != pid]
assert not ghost_tracks, f"ghost track outlived its grace: {ghost_tracks}"
print(f"== a one-frame ghost never mints a PID and is gone after "
      f"{tr.keep_new_grace + 1} miss(es)")

# 3b. the case the grace exists for: the DETECTOR blinks on a real object
#     before it has confirmed. It must end up with one stable id, not a new
#     one per blink.
tr2 = Tracker()
seen = []
for i in range(6):
    dets = [] if i == 1 else [{"label": "person", "box": BOX}]
    tr2.update(dets, 640, 480, now=t + 2.0 + i * 0.05)
    seen += [d.get("track_id") for d in dets if d.get("track_id") is not None]
assert len(set(seen)) == 1, f"a blink split one object across ids: {set(seen)}"
assert seen, "a blinking object never confirmed at all"
print(f"== a detector blink does not split the object: pid {seen[0]} throughout")

# 4. the confirmed object survives keep_tracked+keep_lost=17 unseen frames
for i in range(17):
    frame([], t + 0.25 + i * 0.05)
ids5 = frame([{"label": "person", "box": NEAR}], t + 1.2)
assert ids5 == {pid}, f"id lost during the keep window: {ids5}"
print("== survives 17 unseen frames and keeps its id")

# 5. past the budget it is gone; a re-entry is a NEW object
for i in range(19):
    frame([], t + 2.0 + i * 0.05)
frame([{"label": "person", "box": BOX}], t + 3.1)     # new, unconfirmed
ids6 = frame([{"label": "person", "box": NEAR}], t + 3.15)
assert ids6 and ids6 != {pid}, ids6
print("== past keep_lost it is gone; re-entry is a new id:", ids6)

# 6. API layer: the exact names round-trip, unknown/old names refused
from core.services import detector_control as dc              # noqa: E402
assert set(dc.TRACKER_FIELDS) == {"kalman_dist_thr", "iou_thr", "init_iou_thr",
                                  "keep_new_frames", "keep_tracked_frames",
                                  "keep_lost_frames"}
print("== API exposes exactly:", sorted(dc.TRACKER_FIELDS))
defaults = {k: v["default"] for k, v in dc.TRACKER_FIELDS.items()}
assert defaults == {"kalman_dist_thr": 0.8, "iou_thr": 0.9,
                    "init_iou_thr": 0.7, "keep_new_frames": 2,
                    "keep_tracked_frames": 15, "keep_lost_frames": 2}, defaults
print("== defaults match the requested values exactly")
print("ALL OK")

# 7. retune: new thresholds land IN PLACE, live ids survive. The hot-reload
# path used to poke attributes from an API two generations old (iou_min/
# radius/ttl_s existed, ttl_s no longer did), so every tracker edit crashed
# there and the sliders changed nothing until a detection restart.
from app import tracker as tracker_mod                        # noqa: E402
tr3 = Tracker()
d3 = {"label": "person", "box": BOX}
tr3.update([d3], 640, 480, now=t)
tr3.update([d3], 640, 480, now=t + 0.05)
live_before = set(tr3.tracks)
assert live_before, tr3.tracks
tr3.retune(kalman_dist_thr=0.5, iou_thr=0.6, init_iou_thr=0.4,
           keep_new_frames=3, keep_tracked_frames=20, keep_lost_frames=5)
assert set(tr3.tracks) == live_before, "retune dropped live tracks"
assert abs(tr3.iou_min - 0.4) < 1e-9, tr3.iou_min
assert abs(tr3.init_iou_min - 0.6) < 1e-9, tr3.init_iou_min
assert abs(tr3.radius - 0.5 * tracker_mod.KALMAN_BASE) < 1e-9, tr3.radius
assert tr3.keep_new == 3 and tr3.keep_frames == 25, (tr3.keep_new, tr3.keep_frames)
# ...and the settings dict run.py builds feeds retune with no leftovers.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "app_run_probe", os.path.join(ROOT, "systems", "demo", "app", "run.py"))
_src = open(_spec.origin, encoding="utf-8").read()
assert "pipe.tracker.retune(**applied)" in _src
assert "tracker.ttl_s" not in _src
print("== retune lands live; the reload path uses it")
print("ALL OK")
