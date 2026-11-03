# Four things that were each somebody pressing a button and being surprised.
#
# 1. A clip played as a timelapse. The header said 30fps because that is what
#    the camera GRABS; the recorder is fed once per detection tick, which on a
#    Hailo sharing its budget is 8. Four times fast.
# 2. Start on a Hub card also armed the AI -- spending the camera, a share of
#    the accelerator and every rule in the workflow on a system somebody only
#    wanted to look at.
# 3. Two systems, three cameras and two cameras, both started: five feeds on a
#    four-camera box, and the capacity meter said "5 out of 4" rather than
#    anything refusing it.
# 4. A Snapshot node left on "Follow the rule" saved a picture of camera 1
#    while its rule watched camera 2 -- the app's blank-means-first fallback,
#    with nothing on the canvas that said so.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# -- 1. the clip is written at the rate frames reach the RECORDER ----------
run = read("systems", "demo", "app", "run.py")
rec = run[run.index("    def _record(o, event=None):"):]
rec = rec[:rec.index("\n    # A Dataset node")]
check("a clip's header is the loop rate, not the grab rate",
      "set_fps(p.fps_ema" in rec, rec)
check("...with the old measures kept only as a fallback",
      "p.cam.measured_fps or p.cam.fps" in rec)
# One tick offers one frame per camera, which is what makes fps_ema the right
# number -- worth pinning, because moving the offer would silently unfix this.
check("the recorder is fed once per loop tick",
      "capture.offer(" in run and "pipe.fps_ema = fps_ema" in run)

# -- 2. Start brings up the system, not the AI ----------------------------
hub = read("core", "routes", "hub.py")
power = hub[hub.index("async def power_system("):hub.index("def _app_power(")]
check("Start does not arm detection",
      '_app_power(pkg_root, slug, "start")' not in power)
check("...but Stop still takes it down with the master",
      '_app_power(pkg_root, slug, "stop")' in power)
page = read("frontend", "src", "pages", "Hub.jsx")
check("...and the card says so rather than leaving it a surprise",
      "AI detection" in page)
check("a start/stop in flight shows a spinner, not just a dead button",
      "powerBusy.has(card.slug)" in page and "animate-spin" in page)

# -- 3. the box's camera budget is checked where it is SPENT --------------
svc = read("core", "services", "detector_control.py")
start = svc[svc.index("def start(systems_dir, slug, require_camera=True):"):]
start = start[:start.index("\ndef stop(")]
check("starting re-checks the box's camera budget",
      "sources_elsewhere(systems_dir, slug)" in start
      and "> MAX_CAMERAS" in start, start)
check("...and the refusal names what is using the budget",
      "already running on" in start)
# set_cameras checks it too, but against what was running when the cameras
# were SAVED -- three on an idle box is legal until another system with two
# starts, and nothing re-checked at that point.
check("the write-time check is still there as well",
      "elsewhere = len(sources_elsewhere(" in svc)
det = read("frontend", "src", "pages", "Detection.jsx")
check("Add camera at the limit explains itself instead of vanishing",
      "cameras on this box are in use" in det)

# -- 4. "Follow the rule" is resolved at compile, not guessed at runtime ---
from core.services import workflow as wf                       # noqa: E402

def graph_for(camera):
    return {"nodes": [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {"camera": camera}},
        {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
         "data": {"fnId": "in_frame", "name": "In frame", "mode": "realtime"}},
        {"id": "cond", "type": "logic.when", "position": {"x": 2, "y": 0},
         "data": {"metric": "count", "mode": "above", "value": 0}},
        {"id": "snap", "type": "output.snapshot", "position": {"x": 3, "y": 0},
         "data": {"camera": ""}},
    ], "edges": [
        {"id": "e1", "source": "src", "target": "fr"},
        {"id": "e2", "source": "fr", "target": "cond"},
        {"id": "e3", "source": "cond", "target": "snap"},
    ]}


out = wf.compile_graph(wf.clean_graph(graph_for("cam_b")), [])
snap = next((o for o in out["outputs"] if o["type"] == "snapshot"), None)
check("a blank capture camera compiles to the rule's own camera",
      snap is not None and snap.get("camera") == "cam_b",
      str(snap) + " errors=" + str(out["errors"]))

# "Every camera" genuinely runs on all of them, so the app picks the one that
# fired -- filling one in here would be a lie.
out2 = wf.compile_graph(wf.clean_graph(graph_for("*")), [])
snap2 = next((o for o in out2["outputs"] if o["type"] == "snapshot"), None)
check("...but 'every camera' is left for the app to answer",
      snap2 is not None and not snap2.get("camera"), str(snap2))

# The picker no longer offers a "first camera" that meant one thing in the
# inspector and another everywhere else.
wfp = read("frontend", "src", "pages", "Workflow.jsx")
check("the branch names the camera it reads",
      '<option value="">First camera</option>' not in wfp)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
