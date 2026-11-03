# The detection side of a Snapshot Detection Stream (app/snapgate.py).
#
# The compile and the editor are tests/smoke_snapshot.py. This is what the app
# does with what they write: a camera set to snapshot detects only the frames
# a trigger asked for, and the source goes back to false afterwards.
#
#   1. the gate's rules: a rising edge starts a snap, an edge mid-snap is
#      ignored, a level already true at start is the baseline, a Trigger or
#      sensor must fall before it can snap again
#   2. the frame filter: a camera with a gate keeps only the frames it is owed,
#      one without a gate keeps every frame, the frames of a batch are counted
#   3. the resets, against a real function engine: an Inject's value returns
#      to 0 and does not re-fire; a Trigger's level is held low while its rule
#      still holds and released when it lets go
#   4. the loop in miniature: gate + filter + engine, snap after snap, with a
#      detector that counts how often it was really called
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import functions as appfx                              # noqa: E402
from app import snapgate                                         # noqa: E402
from app.functions import Function, FunctionEngine               # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def cfg(*sources):
    return {"sources": list(sources)}


def inject_src(frames=3, key="fn_kick_fired", fn="kick"):
    return {"kind": "inject", "key": key, "fn": fn, "frames": frames}


def level_src(frames=2, key="fn_door_value", kind="level"):
    return {"kind": kind, "key": key, "fn": "door", "frames": frames}


# -- 1. the gate's rules --------------------------------------------------------
print("== the gate")
g = snapgate.Gate(cfg(inject_src(frames=3)))
g.update({"fn_kick_fired": 0})
check("nothing happens until the Inject fires", g.left == 0)
g.update({"fn_kick_fired": 1})
check("an inject starts a snap of the frames it asked for", g.left == 3, g.left)
g.update({"fn_kick_fired": 1})
check("a second inject mid-snap is ignored, not queued", g.left == 3, g.left)
check("frames are spent one at a time", (g.take(1), g.left) == (1, 2))
check("...up to what is owed, never beyond", (g.take(5), g.left) == (2, 0))
done = g.pop_finished()
check("the last frame reports which source's snap ended", done and done["fn"] == "kick", done)
check("...once", g.pop_finished() is None)
g.update({"fn_kick_fired": 1})
check("and it is ready for the next inject", g.left == 3, g.left)

g = snapgate.Gate(cfg(level_src(frames=2)))
g.update({"fn_door_value": 1})
check("a level already true at start is the baseline, not a snap", g.left == 0, g.left)
g.update({"fn_door_value": 1})
check("...and stays one while it stays true", g.left == 0, g.left)
g.update({"fn_door_value": 0})
g.update({"fn_door_value": 1})
check("a real rising edge snaps", g.left == 2, g.left)
g.take(2)
g.pop_finished()
g.update({"fn_door_value": 1})
check("after its frames a sensor still true cannot snap again", g.left == 0, g.left)
g.update({"fn_door_value": 0})
g.update({"fn_door_value": 1})
check("it must fall and rise first", g.left == 2, g.left)

g = snapgate.Gate(cfg(level_src(frames=1, key="fn_busy_active", kind="trigger")))
g.update({"fn_busy_active": 0})
g.update({"fn_busy_active": 1})
check("a Trigger's level rising snaps", g.left == 1, g.left)
g.take(1)
fin = g.pop_finished()
check("...and its source is reported as a trigger, to be held low",
      fin and fin["kind"] == "trigger" and fin["key"] == "fn_busy_active", fin)

g = snapgate.Gate(cfg({"kind": "inject", "key": "fn_k_fired", "frames": 10 ** 9},
                      {"kind": "inject", "key": "fn_j_fired", "frames": "abc"},
                      {"kind": "inject", "key": "", "frames": 1}))
check("frames are clamped and a keyless source is dropped",
      [s["frames"] for s in g.sources] == [snapgate.MAX_FRAMES, 1], g.sources)
check("truthy reads numbers and words",
      snapgate._truthy(1) and snapgate._truthy("on") and not snapgate._truthy(0)
      and not snapgate._truthy("false") and not snapgate._truthy(None)
      and not snapgate._truthy(0.0))


# -- 2. the frame filter ----------------------------------------------------------
print("== the frame filter")
class Pipe:
    def __init__(self, pid, snapshot=None):
        self.id = pid
        self.cfg = {"snapshot": snapshot} if snapshot else {}
        self.snap = None


plain, snappy = Pipe("a"), Pipe("b", cfg(inject_src(frames=2)))
out, done = snapgate.filter_frames([(plain, "A1"), (snappy, "B1"), (plain, "A2"), (snappy, "B2")])
check("a camera without a gate keeps every frame", [f for p, f in out if p is plain] == ["A1", "A2"], out)
check("a gate with nothing owed lets no frame through",
      [f for p, f in out if p is snappy] == [None, None], out)
check("...and its pipe now carries the gate", snappy.snap is not None and plain.snap is None)
snappy.snap.update({"fn_kick_fired": 1})
out, done = snapgate.filter_frames([(plain, "A1"), (snappy, "B1"), (snappy, "B2"), (snappy, "B3")])
check("a snap of 2 lets exactly 2 frames of a batch through",
      [f for p, f in out if p is snappy] == ["B1", "B2", None], out)
check("...and reports the source as finished", len(done) == 1 and done[0]["fn"] == "kick", done)
out, done = snapgate.filter_frames([(snappy, None)])
check("a missing frame is never counted", out == [(snappy, None)] and not done)
snappy.cfg = {}
snapgate.filter_frames([(snappy, "B1")])
check("a camera switched back to video loses its gate", snappy.snap is None)
snappy.cfg = {"snapshot": cfg(inject_src(frames=2))}
gate_before = snapgate.sync(snappy)
snappy.cfg = {"snapshot": cfg(inject_src(frames=5))}
check("a changed config builds a fresh gate", snapgate.sync(snappy) is not gate_before)


# -- 3. the resets, against a real engine -----------------------------------------
print("== the resets")
kick = Function({"id": "kick", "type": "inject", "name": "k",
                 "params": {"ptype": "boolean", "payload": "true",
                            "once": True, "once_delay_s": 0}})
eng = FunctionEngine([kick], state_dir=None)
eng.update({}, [], {}, {}, 640, 480, now=0.0)
eng.update({}, [], {}, {}, 640, 480, now=1.0)
check("the Inject publishes true when it fires", eng.stats().get("fn_kick_value") == 1,
      eng.stats().get("fn_kick_value"))
check("...and reset_inject puts it back to 0", appfx.reset_inject("kick") is True)
eng.update({}, [], {}, {}, 640, 480, now=2.0)
check("...where it stays: it does not fire again by itself",
      eng.stats().get("fn_kick_value") == 0 and eng.stats().get("fn_kick_fired") == 0,
      {k: v for k, v in eng.stats().items() if k.startswith("fn_kick")})
check("an unknown Inject is a no-op, not an error", appfx.reset_inject("nope") is False)
text = Function({"id": "say", "type": "inject", "name": "s",
                 "params": {"ptype": "string", "payload": "hi", "once": True,
                            "once_delay_s": 0}})
eng2 = FunctionEngine([text], state_dir=None)
eng2.update({}, [], {}, {}, 640, 480, now=0.0)
eng2.update({}, [], {}, {}, 640, 480, now=1.0)
appfx.reset_inject("say")
eng2.update({}, [], {}, {}, 640, 480, now=2.0)
check("a text Inject goes back to empty, not 0", eng2.stats().get("fn_say_value") == "",
      repr(eng2.stats().get("fn_say_value")))

src = Function({"id": "crowd", "type": "frame_count", "name": "c", "params": {}})
moment = Function({"id": "busy", "type": "expr", "name": "b",
                   "params": {"expr": "fn_crowd_count > 0"},
                   "triggers": [{"metric": "value", "op": ">", "value": 0,
                                 "cooldown_s": 0}]})
engt = FunctionEngine([src, moment], state_dir=None)
person = [{"label": "person", "box": [10, 10, 50, 50], "track_id": 1, "score": 0.9}]
engt.update({}, person, {}, {}, 640, 480, now=0)
check("a Trigger's rule holding publishes active", engt.stats().get("fn_busy_active") == 1)
appfx.hold_low("fn_busy_active")
engt.update({}, person, {}, {}, 640, 480, now=1)
check("held low while the rule still holds", engt.stats().get("fn_busy_active") == 0,
      engt.stats().get("fn_busy_active"))
engt.update({}, person, {}, {}, 640, 480, now=2)
check("...for as long as it holds", engt.stats().get("fn_busy_active") == 0)
engt.update({}, [], {}, {}, 640, 480, now=3)
check("released the moment the rule lets go",
      engt.stats().get("fn_busy_active") == 0 and "fn_busy_active" not in appfx._snap_hold)
engt.update({}, person, {}, {}, 640, 480, now=4)
check("and a fresh rise publishes active again", engt.stats().get("fn_busy_active") == 1)
check("a Trigger nobody held is untouched", True)


# -- 4. the loop in miniature ----------------------------------------------------
print("== the loop in miniature")
kick = Function({"id": "kick2", "type": "inject", "name": "k2",
                 "params": {"ptype": "boolean", "payload": "true",
                            "interval_s": 10}})
eng = FunctionEngine([kick], state_dir=None)
pipe = Pipe("cam", cfg({"kind": "inject", "key": "fn_kick2_fired", "fn": "kick2", "frames": 3}))
calls = []


def one_pass(now):
    frames, done = snapgate.filter_frames([(pipe, "frame")])
    for src in done:
        if src["kind"] == "inject":
            appfx.reset_inject(src["fn"])
    for _p, f in frames:
        if f is not None:
            calls.append(now)            # the detector was really called
    eng.update({}, [], {}, {}, 640, 480, now=now)
    pipe.snap.update(eng.stats())
    return eng.stats().get("fn_kick2_value")


values = [one_pass(t) for t in range(0, 40)]
check("the detector ran only for the frames asked, three per inject",
      len(calls) % 3 == 0 and 3 <= len(calls) <= 9, calls)
check("...the repeat at 10 s, 20 s and 30 s each snapped once more",
      len(calls) == 9, calls)
check("...nothing ran between snaps", all(c in range(1, 40) for c in calls)
      and not any(c in range(5, 10) for c in calls), calls)
check("...the Inject read true while its snap ran (t = 10, 11, 12)",
      values[10:13] == [1, 1, 1], values[8:16])
check("...and false again the moment the last frame was taken (t = 13)",
      values[13] == 0 and values[14] == 0, values[8:16])

# -- 5. camera snaps: what a snapshot stream detected is kept and findable --------
print("== camera snaps")
from app import capture as capmod                                 # noqa: E402


class StubStore:
    def __init__(self):
        self.saved = []

    def save_snapshot(self, sid, data):
        self.saved.append((sid, data))


JPEG = b"\xff\xd8" + b"x" * 20
store = StubStore()
cap = capmod.Capture(tempfile.mkdtemp(), store, fps=10, tag="cam_main")
sid_plain = cap.snap(JPEG, "label")
sid_snp = cap.snap(JPEG, "camera snap", kind="snp")
check("an ordinary capture is named exactly as it always was",
      sid_plain and sid_plain.startswith("cap_cam_main_") and "_snp_" not in sid_plain, sid_plain)
check("a camera snap carries its kind after the camera's tag",
      sid_snp and sid_snp.startswith("cap_cam_main_snp_"), sid_snp)
check("...and still starts with the camera tag, so the Footage page finds its camera",
      sid_snp.split("_", 1)[1].startswith("cam_main_"), sid_snp)
nocam = capmod.Capture(tempfile.mkdtemp(), StubStore(), fps=10, tag="")
sid_nt = nocam.snap(JPEG, kind="snp")
check("a camera with no tag still gets a marked name",
      sid_nt and sid_nt.startswith("cap_snp_"), sid_nt)
odd = cap.snap(JPEG, kind="a b/../c")
check("a kind is cut down to letters and digits", odd and "/" not in odd and " " not in odd
      and ".." not in odd, odd)

runsrc = open(os.path.join(ROOT, "systems", "demo", "app", "run.py"), encoding="utf-8").read()
check("the loop keeps each frame a Snapshot stream detected (and not a held one)",
      "if pipe.snap is not None and not pipe.snap.idle_now:" in runsrc
      and "_camsnap(pipe)" in runsrc)
check("the camera's status tells the page it is a snapshot camera",
      '**({"snap": True} if p.snap is not None else {})' in runsrc)
dcsrc = open(os.path.join(ROOT, "core", "services", "detector_control.py"), encoding="utf-8").read()
check("the snapshot list marks camera snaps for the page",
      '"kind": "snap" if "_snp_" in stem else ""' in dcsrc)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
