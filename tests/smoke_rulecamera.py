# An If/Else, a Formula and a Variable run on the camera they read.
#
# Product owner, 2026-10-12, two cameras and a workflow per camera (Detection
# Stream -> Class Filter -> Counter on a zone -> If/Else -> Triggers, a
# Snapshot "photo on alert", cards PERSON IN ZONE CAM n / VIOLATION CAM n, a
# Variable). The colour half is tests/smoke_paintcamera.py. The rest of the
# same bug: an If/Else compiles to `expr` functions with no camera, and the
# app runs a camera-less function on the FIRST camera's engine, so camera
# 2's violation was raised as a camera 1 event and its Snapshot (left on
# "follow the rule") saved camera 1's frame. core/services/rule_camera.py
# now gives such an `expr` the one camera its inputs come from.
#
#   compile: every rule of a workflow carries its stream's camera, the
#            Snapshot is resolved to it, the cards read their own keys, and
#            the ambiguous cases keep the old answer
#   compile: two workflows storing into one Variable name are refused, not
#            merged
#   app:     camera 2 crowded -> the alert event says camera 2, the
#            Snapshot is taken from camera 2's capture, camera 2's numbers
#            move and camera 1's do not; then the other way round
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

from core.services import rule_camera                          # noqa: E402
from core.services import workflow as wf                        # noqa: E402
from app import capture as capture_mod                          # noqa: E402
from app import detector as detector_mod                        # noqa: E402
from app import run as apprun                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    # ascii(): a rule's name holds a middle dot, which a cp1252 console
    # cannot print -- a failure must not die reporting itself.
    print(("ok   " if ok else "FAIL ") + label
          + ("" if ok else f"\n      {ascii(detail)}"))
    if not ok:
        bad += 1


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


def workflow(cam, n, var):
    """The screenshot's canvas for one camera."""
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
         "data": {"name": f"overcrowded {n}", "alert": True}},
        {"id": p("snap"), "type": "output.snapshot", "data": {"mode": "every"}},
        {"id": p("card"), "type": "dashboard.card",
         "data": {"name": f"PERSON IN ZONE CAM {n}"}},
        {"id": p("vcard"), "type": "dashboard.card",
         "data": {"name": f"VIOLATION CAM {n}"}},
        {"id": p("var"), "type": "variable.ref", "data": {"name": var, "io": "out"}},
    ]
    for i, node in enumerate(nodes):
        node["position"] = {"x": i, "y": n}
    edges = [wire(p("src"), p("cls")), wire(p("cls"), p("cnt")),
             wire(p("z"), p("cnt"), "zone"), wire(p("cnt"), p("sw"), "in1"),
             wire(p("sw"), p("bad"), sh="out0"), wire(p("bad"), p("snap")),
             wire(p("cnt"), p("card")), wire(p("sw"), p("vcard"), sh="out0"),
             wire(p("cnt"), p("var"))]
    return {"nodes": nodes, "edges": edges}


def store(var1, var2):
    return {"workflows": [{"id": "w1", "graph": workflow("cam1", 1, var1)},
                          {"id": "w2", "graph": workflow("cam2", 2, var2)}]}


print("== the compile")
c = wf.compile_store(store("pcb_1", "pcb_2"), ZONES, cameras=["cam1", "cam2"])
check("two workflows on two cameras compile", not c["errors"], c["errors"])
fns = {f["id"]: f for f in c["functions"]}
for n, cam in ((1, "cam1"), (2, "cam2")):
    for fid in (f"cnt{n}", f"sw{n}_b0", f"sw{n}_b1", f"pcb_{n}"):
        check(f"{fid} runs on {cam}",
              (fns.get(fid) or {}).get("params", {}).get("camera") == cam,
              repr(fns.get(fid)))
    snap = next((o for o in c["outputs"] if o["id"] == f"wf_snap{n}"), {})
    check(f"Snapshot {n} follows its rule to {cam}",
          snap.get("camera") == cam and snap.get("function") == f"sw{n}_b0",
          repr(snap))
widgets = {w["title"]: w["key"] for p in c.get("pages") or ()
           for w in p.get("widgets") or ()}
check("each card reads its own camera's number",
      widgets.get("PERSON IN ZONE CAM 1") == "fn_cnt1_count"
      and widgets.get("PERSON IN ZONE CAM 2") == "fn_cnt2_count"
      and widgets.get("VIOLATION CAM 1") == "fn_sw1_b0_value"
      and widgets.get("VIOLATION CAM 2") == "fn_sw2_b0_value", repr(widgets))
dup = wf.compile_store(store("total_pcb", "total_pcb"), ZONES,
                       cameras=["cam1", "cam2"])
check("one Variable name in both workflows is refused (no shared number)",
      any("total_pcb" in e["message"] for e in dup["errors"]), dup["errors"])

check("a box updated over the air recompiles at the next master start "
      "(app_supervisor.logic_stale), so no workflow needs saving by hand",
      wf.LOGIC_VERSION >= 5, wf.LOGIC_VERSION)

# The ambiguous cases keep the old answer: the first camera.
ZB = {z["id"]: z for z in ZONES}


def stamped(functions):
    rule_camera.stamp(functions, ZB, wf.FRAME_FN_TYPES, wf.function_keys,
                      wf.key_owner)
    return {f["id"]: (f.get("params") or {}).get("camera", "") for f in functions}


def expr(fid, text):
    return {"id": fid, "type": "expr", "params": {"expr": text}}


def count(fid, cam):
    return {"id": fid, "type": "zone_count", "params": {"camera": cam} if cam else {}}


got = stamped([count("a", "cam1"), count("b", "cam2"),
               expr("both", "fn_a_count + fn_b_count"),
               expr("chain", "fn_only_value > 1"), expr("only", "fn_b_count"),
               count("all", "*"), expr("every", "fn_all_count > 0"),
               count("blank", ""), expr("first", "fn_blank_count > 0"),
               expr("zoned", "zone_zb_count > 0"),
               expr("cross", "cam_cam2_fn_b_count > 0"),
               expr("other", "fn_elsewhere_value > 0"),
               expr("const", "3"),
               {"id": "api", "type": "api_poll", "params": {}},
               expr("withapi", "fn_b_count + fn_api_value"),
               expr("loop1", "fn_loop2_value"), expr("loop2", "fn_loop1_value")])
check("an expr reading one camera (directly, via another expr, or a zone "
      "key) runs there", got["only"] == "cam2" and got["chain"] == "cam2"
      and got["zoned"] == "cam2" and got["withapi"] == "cam2", repr(got))
check("two cameras, every camera, a blank stream, a cam_ key, another "
      "canvas's key, a constant or a loop: no camera, as before",
      all(got[k] == "" for k in ("both", "every", "first", "cross", "other",
                                 "const", "loop1", "loop2")), repr(got))

# -- the app, for real ---------------------------------------------------------
CAM_NO = {"cam1": 1, "cam2": 2}
RealCamera = apprun.camera_mod.Camera


class FakeCamera(RealCamera):
    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))
        self._no = CAM_NO.get(str(config.get("id")), 0)

    def read(self):
        frame = super().read()
        if frame is not None and hasattr(frame, "shape"):
            frame[0, 0, :] = self._no
            # A block the snapshot's JPEG keeps legible, clear of the HUD.
            frame[-24:, -24:, :] = self._no * 100
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

SNAPS = []                      # (capture tag, camera the JPEG came from)
EVENTS = []                     # (function, camera_id) of every alert event


def spy_snap(self, jpeg_bytes, tag="", oid=""):
    src = "?"
    level = None
    try:
        import cv2                                              # noqa: PLC0415
        import numpy as np                                      # noqa: PLC0415
        img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        level = float(img[-12:, -12:, :].mean())
    except Exception:                                           # noqa: BLE001
        # No OpenCV (CI installs requirements.txt only): Pillow, which is
        # there, reads the same corner of the same JPEG.
        try:
            import io                                           # noqa: PLC0415
            from PIL import Image                               # noqa: PLC0415
            im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
            w, h = im.size
            px = list(im.crop((w - 12, h - 12, w, h)).getdata())
            level = sum(sum(p) for p in px) / (3.0 * len(px))
        except Exception:                                       # noqa: BLE001
            level = None
    if level is not None:
        src = "cam1" if 70 < level < 130 else "cam2" if 170 < level < 230 else "?"
    SNAPS.append((self.tag, src))
    return "snap"


capture_mod.Capture.snap = spy_snap
real_fire = apprun.outputs_mod.fire


def spy_fire(outputs, event, *a, **k):
    d = event.get("detail") or {}
    if d.get("function"):
        EVENTS.append((d.get("function"), d.get("camera_id")))
    return real_fire(outputs, event, *a, **k)


apprun.outputs_mod.fire = spy_fire

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()

APP = {
    "ai": {"on": True},
    "app": {"stream_port": PORT, "stream_bind": "127.0.0.1"},
    "cameras": [{"id": cam, "source": "synthetic", "enabled": True,
                 "fps": 10, "width": 320, "height": 240}
                for cam in ("cam1", "cam2")],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": True, "record": False},
    "storage": {"keep_snapshots": 5},
    "zones": ZONES,
    "outputs": c["outputs"],
    "functions": c["functions"],
}
DATA = tempfile.mkdtemp()


def counts():
    try:
        with open(os.path.join(DATA, "state.json"), encoding="utf-8") as f:
            return json.load(f).get("counts") or {}
    except (OSError, ValueError):
        return {}


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


def drive():
    for crowded, calm, n, m in (("cam2", "cam1", 2, 1), ("cam1", "cam2", 1, 2)):
        print(f"== {crowded} crowded, {calm} not")
        del SNAPS[:], EVENTS[:]
        SCENE[crowded], SCENE[calm] = [P1, P2], [P1]
        want = {f"fn_cnt{n}_count": 2, f"fn_cnt{m}_count": 1,
                f"fn_pcb_{n}_value": 2, f"fn_pcb_{m}_value": 1,
                f"fn_sw{n}_b0_value": 1, f"fn_sw{m}_b0_value": 0}
        check(f"{crowded}'s count, variable and violation card move, "
              f"{calm}'s do not",
              wait(lambda: all(counts().get(k) == v for k, v in want.items())),
              {k: counts().get(k) for k in want})
        check(f"the alert says {crowded}",
              wait(lambda: (f"sw{n}_b0", crowded) in EVENTS), EVENTS)
        check(f"...and never {calm}",
              not any(f == f"sw{n}_b0" and cam != crowded for f, cam in EVENTS)
              and not any(f == f"sw{m}_b0" for f, cam in EVENTS), EVENTS)
        check(f"the Snapshot is {crowded}'s, from {crowded}'s picture",
              wait(lambda: (crowded, crowded) in SNAPS)
              and all(s == (crowded, crowded) for s in SNAPS), SNAPS)


def wrapped():
    try:
        drive()
    except Exception as exc:                                    # noqa: BLE001
        check("the driver ran", False, repr(exc))
    finally:
        apprun._stop.set()


apprun._stop.clear()
apprun.config_mod.load = lambda *a, **k: copy.deepcopy(APP)
apprun.config_mod.data_dir = lambda *a, **k: DATA
apprun._config_mtime = lambda: 1.0
t = threading.Thread(target=wrapped, daemon=True)
t.start()
apprun.main([])
t.join(5)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
