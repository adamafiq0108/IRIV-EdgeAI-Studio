# A workflow Trigger paints the camera its Detection Stream reads, and only
# that one.
#
# Product owner, 2026-10-12, two cameras, a workflow per camera (Detection
# Stream -> Class Filter -> Counter on a zone -> If/Else -> Triggers that
# paint boxes and zone): "if a violation is detected in Video 2, the
# bounding box color changes on Camera 1 instead of Camera 2". The If/Else
# compiles to `expr` functions, which take no camera; the app's colour rules
# took their camera from the function, read "none" as the FIRST camera, and
# so every workflow's Trigger colours landed on camera 1. The fix stamps the
# stream's camera on the compiled trigger (`paint_camera`,
# core/services/trigger_paint.py stamp_camera) and run.py _color_rules
# keys on it.
#
# The real compile and the real demo app, two synthetic cameras and a fake
# chip (smoke_boxcolorcamera's harness). Both zones are called "Zone", both
# cameras' trackers number their person 1, and each camera's Trigger has
# its own colour. Checked in BOTH overlay modes: the server-painted picture
# (camera.annotate) and the browser's overlay messages (overlay_feed.Feed).
#   1. camera 2 crowded, camera 1 not: camera 2's boxes and zone take
#      camera 2's colours, camera 1 keeps plain boxes and its "not violate"
#      zone colour
#   2. the other way round
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
from app import detector as detector_mod                        # noqa: E402
from app import overlay_feed as overlay_mod                     # noqa: E402
from app import run as apprun                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# Each camera's own colours. OK is the "not violate" zone colour, shared.
BOX = {"cam1": "#FF0000", "cam2": "#0000FF"}
ZONE = {"cam1": "#FF00FF", "cam2": "#00FFFF"}
OK = "#00FF00"
ZID = {"cam1": "za", "cam2": "zb"}

ZONES = [{"id": ZID[c], "name": "Zone", "kind": "area", "enabled": True,
          "camera": c, "color": "#DEBA00",
          "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}
         for c in ("cam1", "cam2")]


def wire(src, dst, th="in", sh=None):
    e = {"id": f"{src}-{dst}-{th}", "source": src, "target": dst,
         "targetHandle": th}
    if sh:
        e["sourceHandle"] = sh
    return e


def workflow(cam, n):
    """The screenshot's shape for one camera: stream, person filter, a count
    in the zone, If count > 1 / else, "overcrowded" painting boxes and
    zone, "not violate" painting the zone."""
    p = lambda k: f"{k}{n}"                                    # noqa: E731
    nodes = [
        {"id": p("src"), "type": "source.detection", "data": {"camera": cam}},
        {"id": p("cls"), "type": "filter.class", "data": {"classes": ["person"]}},
        {"id": p("z"), "type": "zone.ref", "data": {"zone": ZID[cam]}},
        {"id": p("cnt"), "type": "analytics.counter",
         "data": {"fnId": p("cnt"), "name": f"Count {n}", "mode": "realtime"}},
        {"id": p("sw"), "type": "branch.multi",
         "data": {"fnId": p("sw"), "name": f"Crowd {n}", "join": "and",
                  "rules": [{"op": ">", "value": 1, "input": "in1"}]}},
        {"id": p("bad"), "type": "output.trigger",
         "data": {"name": f"overcrowded {n}", "draw": True,
                  "box_color": BOX[cam], "zone_color": ZONE[cam]}},
        {"id": p("good"), "type": "output.trigger",
         "data": {"name": f"not violate {n}", "zone_color": OK}},
    ]
    for i, node in enumerate(nodes):
        node["position"] = {"x": i, "y": n}
    edges = [wire(p("src"), p("cls")), wire(p("cls"), p("cnt")),
             wire(p("z"), p("cnt"), "zone"), wire(p("cnt"), p("sw"), "in1"),
             wire(p("sw"), p("bad"), sh="out0"),
             wire(p("sw"), p("good"), sh="out1"),
             # The zone each Trigger paints: a branch rule watches none.
             wire(p("z"), p("bad"), "zone"), wire(p("z"), p("good"), "zone")]
    return nodes, edges


n1, e1 = workflow("cam1", 1)
n2, e2 = workflow("cam2", 2)
out = wf.compile_graph(wf.clean_graph({"nodes": n1 + n2, "edges": e1 + e2}),
                       ZONES, cameras=["cam1", "cam2"])
print("== the compile")
check("two cameras' workflows compile", not out["errors"], out["errors"])
trigs = {f["trigger"]["message"]: f for f in out["functions"]
         if isinstance(f.get("trigger"), dict)}
for n, cam in ((1, "cam1"), (2, "cam2")):
    for name in (f"overcrowded {n}", f"not violate {n}"):
        fn = trigs.get(name) or {}
        check(f"'{name}' paints {cam}",
              fn.get("trigger", {}).get("paint_camera") == cam, repr(fn))
    # Its branch rule runs on that camera too since rule_camera.py (the
    # Snapshot/event half of the same report, tests/smoke_rulecamera.py).
    check(f"...and its branch rule runs on {cam}",
          (trigs.get(f"overcrowded {n}") or {}).get("params", {})
          .get("camera") == cam, repr(trigs.get(f"overcrowded {n}")))
rules1 = apprun._color_rules({"functions": out["functions"]},
                             lambda o: (o or "cam1") == "cam1")
rules2 = apprun._color_rules({"functions": out["functions"]},
                             lambda o: (o or "cam1") == "cam2")
check("camera 1's pipe gets camera 1's colour rules only",
      {r["colour"] for r in rules1} == {BOX["cam1"], ""}
      and {r["paint_zone"] for r in rules1} == {"za"}, repr(rules1))
check("camera 2's pipe gets camera 2's colour rules only",
      {r["colour"] for r in rules2} == {BOX["cam2"], ""}
      and {r["paint_zone"] for r in rules2} == {"zb"}, repr(rules2))
blank = wf.compile_graph(wf.clean_graph({
    "nodes": [dict(x, data={**x["data"], "camera": ""})
              if x["type"] == "source.detection" else x for x in n1],
    "edges": e1}), [dict(ZONES[0], camera="")])
check("a blank stream on a camera-less zone stamps nothing: the rule's own "
      "camera (the first) stands, as before",
      not blank["errors"] and not any(
          (f.get("trigger") or {}).get("paint_camera")
          for f in blank["functions"]), repr(blank["functions"]))
zoned = wf.compile_graph(wf.clean_graph({
    "nodes": [dict(x, data={**x["data"], "camera": ""})
              if x["type"] == "source.detection" else x for x in n2],
    "edges": e2}), ZONES)
check("a blank stream on camera 2's zone paints camera 2 (a zone implies "
      "its camera, as it does for the rule)",
      {(f.get("trigger") or {}).get("paint_camera") for f in zoned["functions"]
       if (f.get("trigger") or {}).get("zone_color")} == {"cam2"},
      repr(zoned["functions"]))

# -- the app, for real ---------------------------------------------------------
CAM_NO = {"cam1": 1, "cam2": 2}
RealCamera = apprun.camera_mod.Camera


class FakeCamera(RealCamera):
    """Synthetic frames with the camera's number in pixel (0, 0)."""

    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))
        self._no = CAM_NO.get(str(config.get("id")), 0)

    def read(self):
        frame = super().read()
        if frame is not None and hasattr(frame, "shape"):
            frame[0, 0, :] = self._no
        return frame


def cam_of(frame):
    try:
        return {v: k for k, v in CAM_NO.items()}.get(int(frame[0, 0, 0]), "?")
    except Exception:                                           # noqa: BLE001
        return "?"


SCENE = {"cam1": [], "cam2": []}
P1 = {"label": "person", "confidence": 0.9, "box": [20, 20, 100, 200]}
P2 = {"label": "person", "confidence": 0.9, "box": [200, 20, 300, 200]}


class FakeChip(detector_mod.Detector):
    name = "fakechip"

    def detect(self, frame):
        return [dict(d) for d in SCENE.get(cam_of(frame), [])]

    def close(self):
        pass


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.02

# Server mode: what camera.annotate draws, per camera.
SEEN = {"cam1": [], "cam2": []}
real_annotate = apprun.camera_mod.annotate


def spy_annotate(frame, detections, zone=None, overlays=None, *a, **k):
    cam = cam_of(frame)
    if cam in SEEN:
        SEEN[cam].append((
            [d.get("box_color", "") for d in detections],
            {str(o.get("id")): o.get("color") for o in overlays or []},
            [d.get("track_id") for d in detections]))
    return real_annotate(frame, detections, zone, overlays, *a, **k)


apprun.camera_mod.annotate = spy_annotate

# Client mode: the Feed the app builds, so a listener can be attached.
FEEDS = []
RealFeed = overlay_mod.Feed


class SpyFeed(RealFeed):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        FEEDS.append(self)


overlay_mod.Feed = SpyFeed

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()


def app_cfg(mode):
    return {
        "ai": {"on": True},
        "app": {"stream_port": PORT, "stream_bind": "127.0.0.1"},
        "video": {"overlay": mode},
        "cameras": [{"id": c, "source": "synthetic", "enabled": True,
                     "fps": 10, "width": 320, "height": 240}
                    for c in ("cam1", "cam2")],
        "detector": {"kind": "hailo", "model": "yolov8s"},
        "capture": {"snap": False, "record": False},
        "storage": {"keep_snapshots": 5},
        "zones": ZONES,
        "outputs": [],
        "functions": out["functions"],
    }


def run_app(cfg, drive):
    data = tempfile.mkdtemp()
    apprun._stop.clear()
    apprun.config_mod.load = lambda *a, **k: copy.deepcopy(cfg)
    apprun.config_mod.data_dir = lambda *a, **k: data
    apprun._config_mtime = lambda: 1.0

    def wrapped():
        try:
            drive()
        except Exception as exc:                                # noqa: BLE001
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


def settled(cam, boxes, zones):
    last = SEEN[cam][-3:]
    return len(last) == 3 and all(b == boxes and z == zones for b, z, _t in last)


def expect(crowded):
    """(boxes, zones) per camera when `crowded` has two people, the other one."""
    calm = "cam1" if crowded == "cam2" else "cam2"
    return {crowded: ([BOX[crowded]] * 2, {ZID[crowded]: ZONE[crowded]}),
            calm: ([""], {ZID[calm]: OK})}


def server_drive():
    for crowded, calm in (("cam2", "cam1"), ("cam1", "cam2")):
        print(f"== server paint: {crowded} crowded, {calm} not")
        SCENE[crowded], SCENE[calm] = [P1, P2], [P1]
        want = expect(crowded)
        for cam in (crowded, calm):
            check(f"{cam} draws {want[cam]}",
                  wait(lambda c=cam: settled(c, *want[c])), SEEN[cam][-3:])
    ids = {cam: set(SEEN[cam][-1][2]) for cam in SEEN}
    check("both cameras' trackers numbered a person the same and it did "
          "not cross over", ids["cam1"] & ids["cam2"], repr(ids))


def client_drive():
    if not wait(lambda: FEEDS and FEEDS[-1].enabled):
        check("the app built a client overlay feed", False)
        return
    li = FEEDS[-1].subscribe({"cam1", "cam2"})
    last = {}

    def drain():
        while True:
            raw = li.get(0.05)
            if raw is None:
                return
            msg = json.loads(raw)
            last[msg["cam"]] = msg

    def got(cam, boxes, zone):
        drain()
        m = last.get(cam) or {}
        return ([d.get("color") for d in m.get("dets") or []]
                == boxes and [z.get("color") for z in m.get("zones") or []]
                == [zone])

    for crowded, calm in (("cam2", "cam1"), ("cam1", "cam2")):
        print(f"== client overlay: {crowded} crowded, {calm} not")
        SCENE[crowded], SCENE[calm] = [P1, P2], [P1]
        check(f"{crowded}'s messages carry its own colours",
              wait(lambda: got(crowded, [BOX[crowded].lower()] * 2,
                               ZONE[crowded].lower())), last.get(crowded))
        check(f"{calm}'s messages keep a plain box and its OK zone",
              wait(lambda: got(calm, [overlay_mod._box_color(dict(P1))],
                               OK.lower())), last.get(calm))
    FEEDS[-1].unsubscribe(li)


run_app(app_cfg("server"), server_drive)
run_app(app_cfg("client"), client_drive)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
