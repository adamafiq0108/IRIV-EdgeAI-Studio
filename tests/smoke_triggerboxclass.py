# A Trigger's "Recolour the bounding box" paints the RULE'S classes only.
#
# Tok Aba, box .49 (2026-10-07): a Trigger on "person in the zone" with
# "Recolour the bounding box" and "Only boxes inside the zone" turned a
# chair's box red too. "The color change should only be applied to the
# selected object type (e.g., person) and should not affect other object
# types."
#
# The demo app for real (fake camera + fake chip), his graph:
#   detection -> Class Filter person -> Object Detection (Zone wired, OID)
#             -> Compare > 0 -> Trigger (alert, red boxes, only in zone)
# and a chip that sees a person inside the zone, a person outside it and a
# chair inside it. What is pinned, read off the one annotate() call that
# draws the live stream, the snapshots and the clips:
#   1. only in zone ON:  the person inside is red; the person outside and
#                        the chair keep their own colour
#   2. only in zone OFF: both people are red, the chair never is
#   3. a Trigger with no Class Filter anywhere behind it says so on the
#      node and paints every class, as it always did
#   4. each of those is reached by a HOT RELOAD of the running app (the
#      config changes under it, the way a canvas save lands), not by a
#      restart: run.py built the colour rules once in the pipe's __init__,
#      so a Trigger colour saved while the app ran rebuilt every rule and
#      left the painting as it was until the next detection restart -- the
#      live config on .49 named box_zone and every box stayed red
import copy
import json
import os
import socket
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import workflow as wf                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


_n = [0]


def edge(src, dst, sh=None, th=None):
    _n[0] += 1
    e = {"id": f"e{_n[0]}", "source": src, "target": dst}
    if sh:
        e["sourceHandle"] = sh
    if th:
        e["targetHandle"] = th
    return e


# The bay is the left half of the picture.
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
          "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}]
RED = "#FF0000"


def compile_graph(box_in_zone, class_filter=True):
    nodes = [node("src", "source.detection"),
             node("z", "zone.ref", zone="bay"),
             node("cmp", "branch.multi", fnId="many", join="and",
                  rules=[{"op": ">", "value": 0, "input": "in1"}]),
             node("t1", "output.trigger", name="People_Trigger", alert=True,
                  severity="warn", draw=True, box_color=RED,
                  box_in_zone=box_in_zone)]
    edges = [edge("cmp", "t1", "out0", "in")]
    if class_filter:
        nodes += [node("f1", "filter.class", classes=["person"], name="person"),
                  node("od", "analytics.object", fnId="watch", name="Watch",
                       OID=True)]
        edges += [edge("src", "f1"), edge("f1", "od"),
                  edge("z", "od", None, "zone"),
                  edge("od", "cmp", "found", "in1"),
                  edge("od", "t1", "OID", "oid")]
    else:
        nodes.append(node("od", "analytics.occupancy", fnId="watch",
                          name="Watch"))
        edges += [edge("src", "od", None, "in"), edge("z", "od", None, "zone"),
                  edge("od", "cmp", None, "in1")]
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    store = {"version": 3, "workflows": [
        {"id": "main", "name": "Main", "enabled": True, "graph": g}]}
    return wf.compile_store(store, ZONES, ["person", "chair"], ["person", "chair"])


print("== what compiles")
c = compile_graph(True)
check("the graph compiles", not c["errors"], c["errors"])
trig = next(f["trigger"] for f in c["functions"] if f["id"] == "many_b0")
check("the Trigger carries the rule's classes and its zone",
      trig.get("box_classes") == ["person"] and trig.get("box_zone") == "bay",
      trig)
check("...with nothing to say on the node",
      not [w for w in c.get("warnings") or [] if w["node"] == "t1"],
      c.get("warnings"))
c_off = compile_graph(False)
check("only in zone off: the classes stay, the zone goes",
      next(f["trigger"] for f in c_off["functions"]
           if f["id"] == "many_b0").get("box_classes") == ["person"]
      and "box_zone" not in next(f["trigger"] for f in c_off["functions"]
                                 if f["id"] == "many_b0"))
c_any = compile_graph(False, class_filter=False)
t_any = next(f["trigger"] for f in c_any["functions"] if f["id"] == "many_b0")
warns = [w["message"] for w in c_any.get("warnings") or [] if w["node"] == "t1"]
check("no Class Filter behind the Trigger: no classes, said on the node",
      "box_classes" not in t_any and t_any.get("box_color") == RED
      and any("no Class Filter behind this Trigger" in w for w in warns),
      (t_any, warns))
check("no em dash in what it says", all("\u2014" not in w for w in warns))

# -- the demo app for real ----------------------------------------------------
from app import detector as detector_mod                        # noqa: E402
from app import run as apprun                                   # noqa: E402

RealCamera = apprun.camera_mod.Camera


class FakeCamera(RealCamera):
    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))


# A 320x240 picture; the bay is x < 160.
DETS = [{"label": "person", "confidence": 0.9, "box": [20, 20, 100, 200]},
        {"label": "person", "confidence": 0.9, "box": [200, 20, 300, 200]},
        {"label": "chair", "confidence": 0.9, "box": [30, 60, 120, 220]}]


class FakeChip(detector_mod.Detector):
    name = "fakechip"

    def detect(self, frame):
        return [dict(d) for d in DETS]

    def close(self):
        pass


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.02

SEEN = []                       # [[box_color per detection], ...] per annotate
real_annotate = apprun.camera_mod.annotate


def spy_annotate(frame, detections, *a, **k):
    SEEN.append([d.get("box_color", "") for d in detections])
    return real_annotate(frame, detections, *a, **k)


apprun.camera_mod.annotate = spy_annotate

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()


def app_config(compiled):
    return {
        "ai": {"on": True},
        "app": {"stream_port": PORT, "stream_bind": "127.0.0.1"},
        "cameras": [{"id": "cam1", "source": "rtsp://10.0.0.9/a", "enabled": True,
                     "fps": 10, "width": 320, "height": 240}],
        "detector": {"kind": "hailo", "model": "yolov8s"},
        "capture": {"snap": False, "record": False},
        "storage": {"keep_snapshots": 5},
        "zones": copy.deepcopy(ZONES),
        "outputs": copy.deepcopy(compiled["outputs"]),
        "functions": copy.deepcopy(compiled["functions"]),
        "logic": copy.deepcopy(compiled["frames_free"]) | {"version": wf.LOGIC_VERSION},
        "class_filters": {"paused": [], "nodes": copy.deepcopy(compiled["class_filters"])},
    }


APP = app_config(c)


class Run:
    def __init__(self):
        self.data = tempfile.mkdtemp()
        self.mt = [1.0]

    def bump(self):
        self.mt[0] += 1.0

    def counts(self):
        try:
            with open(os.path.join(self.data, "state.json"), encoding="utf-8") as f:
                return json.load(f).get("counts") or {}
        except (OSError, ValueError):
            return {}

    def go(self, drive):
        apprun._stop.clear()
        apprun.config_mod.load = lambda *a, **k: copy.deepcopy(APP)
        apprun.config_mod.data_dir = lambda *a, **k: self.data
        apprun._config_mtime = lambda: self.mt[0]

        def wrapped():
            try:
                drive(self)
            except Exception as exc:                            # noqa: BLE001
                check("the driver ran", False, repr(exc))
            finally:
                apprun._stop.set()
        t = threading.Thread(target=wrapped, daemon=True)
        t.start()
        apprun.main([])
        t.join(5)


def wait(pred, limit=15.0):
    end = time.time() + limit
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:                                       # noqa: BLE001
            pass
        time.sleep(0.05)
    return False


def settled(want):
    """The last three annotate() calls all drew `want`."""
    return len(SEEN) >= 3 and all(s == want for s in SEEN[-3:])


def drive(r):
    print("== only in zone ON")
    check("the branch holds", wait(lambda: r.counts().get("fn_many_b0_value", 0) > 0),
          r.counts())
    check("the person inside the bay is red; the person outside and the chair "
          "inside keep their own colour",
          wait(lambda: settled([RED, "", ""])), SEEN[-3:])
    print("== only in zone OFF")
    APP.update(app_config(c_off))
    r.bump()
    check("both people are red, the chair never is",
          wait(lambda: settled([RED, RED, ""])), SEEN[-3:])
    print("== no Class Filter anywhere behind the Trigger")
    APP.update(app_config(c_any))
    r.bump()
    check("every box takes the colour, as it always did",
          wait(lambda: settled([RED, RED, RED])), SEEN[-3:])


Run().go(drive)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
