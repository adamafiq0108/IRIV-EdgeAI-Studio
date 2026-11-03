# A colouring rule paints the boxes of ITS camera only.
#
# Found while fixing the Trigger recolour (2026-10-07, Tok Aba said yes to
# the fix): run.py built every camera's pipe with the WHOLE list of colour
# rules. A rule whose function runs on camera B publishes fn_<id>_* into
# the system-wide merged numbers, every camera's _stamp_colors read that
# merged set, so while the rule held on camera B it painted camera A's
# boxes too.
#
# The demo app for real, two synthetic cameras (each frame carries its
# camera's number in its first pixel, so the fake chip and the annotate
# spy both know whose picture it is) and a fake chip:
#   1. a rule on cam1 holds: cam1's person is painted, cam2's person is
#      not
#   2. a rule on EVERY camera ("*", more than one person) holds on cam2
#      only: cam2's people take its colour, cam1's one person keeps the
#      cam1 rule's colour -- the rule is judged per camera, by that
#      camera's own numbers
#   3. a zone colour lands on the zone's own camera only (zones are per
#      camera already; the rule now is too)
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

from app import detector as detector_mod                        # noqa: E402
from app import run as apprun                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


RED, BLUE, GREEN = "#FF0000", "#00A0FF", "#00FF00"
CAM_NO = {"cam1": 1, "cam2": 2}
RealCamera = apprun.camera_mod.Camera


class FakeCamera(RealCamera):
    """Synthetic frames, with the camera's number written into pixel (0, 0)
    so a frame can be told apart downstream."""

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


# What each camera sees; the driver changes it between scenes. 320x240,
# the bay (cam2's zone) is the left half.
SCENE = {"cam1": [], "cam2": []}
PERSON_L = {"label": "person", "confidence": 0.9, "box": [20, 20, 100, 200]}
PERSON_R = {"label": "person", "confidence": 0.9, "box": [200, 20, 300, 200]}


class FakeChip(detector_mod.Detector):
    name = "fakechip"

    def detect(self, frame):
        return [dict(d) for d in SCENE.get(cam_of(frame), [])]

    def close(self):
        pass


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.02

SEEN = {"cam1": [], "cam2": []}        # per camera: [(box colours, zone colours)]
real_annotate = apprun.camera_mod.annotate


def spy_annotate(frame, detections, zone=None, overlays=None, *a, **k):
    cam = cam_of(frame)
    if cam in SEEN:
        SEEN[cam].append(([d.get("box_color", "") for d in detections],
                          {str(o.get("id")): o.get("color") for o in overlays or []}))
    return real_annotate(frame, detections, zone, overlays, *a, **k)


apprun.camera_mod.annotate = spy_annotate

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()


def rule(fid, camera, colour, value, **extra):
    return {"id": fid, "name": fid, "type": "frame_count", "enabled": True,
            "params": {"camera": camera, "classes": ["person"]},
            "trigger": {"metric": "count", "op": ">", "value": value,
                        "severity": "info", "message": fid, "cooldown_s": 0,
                        **extra}}


APP = {
    "ai": {"on": True},
    "app": {"stream_port": PORT, "stream_bind": "127.0.0.1"},
    "cameras": [{"id": "cam1", "source": "synthetic", "enabled": True,
                 "fps": 10, "width": 320, "height": 240},
                {"id": "cam2", "source": "synthetic", "enabled": True,
                 "fps": 10, "width": 320, "height": 240}],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
    "zones": [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
               "camera": "cam2", "color": "#DEBA00",
               "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}],
    "outputs": [],
    "functions": [
        # cam1's own rule: one person is enough
        rule("one", "cam1", RED, 0, box_color=RED),
        # every camera: more than one person, judged per camera
        rule("many", "*", BLUE, 1, box_color=BLUE),
        # cam2's rule paints its zone too
        rule("zoned", "cam2", GREEN, 0, box_color="", zone_color=GREEN,
             zone_paint="bay"),
    ],
}


class Run:
    def __init__(self):
        self.data = tempfile.mkdtemp()
        self.mt = [1.0]

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


def settled(cam, boxes, zones=None):
    """The last three annotate() calls on `cam` all drew `boxes` (and the
    zone colours `zones`, when given)."""
    last = SEEN[cam][-3:]
    return len(last) == 3 and all(
        b == boxes and (zones is None or z == zones) for b, z in last)


def drive(r):
    print("== 1. a rule on cam1 holds; cam2 shows a person too")
    SCENE["cam1"] = [PERSON_L]
    SCENE["cam2"] = [PERSON_L]
    check("cam1's rule holds, the every-camera rule does not",
          wait(lambda: r.counts().get("fn_one_count") == 1
               and r.counts().get("cam_cam2_fn_many_count") == 1), r.counts())
    check("cam1's person takes cam1's colour", wait(lambda: settled("cam1", [RED])),
          SEEN["cam1"][-3:])
    check("cam2's person stays uncoloured", wait(lambda: settled("cam2", [""])),
          SEEN["cam2"][-3:])
    print("== 2. the every-camera rule holds on cam2 only")
    SCENE["cam2"] = [PERSON_L, PERSON_R]
    check("it reads 2 on cam2 and 1 on cam1",
          wait(lambda: r.counts().get("cam_cam2_fn_many_count") == 2
               and r.counts().get("cam_cam1_fn_many_count") == 1), r.counts())
    check("cam2's people take its colour",
          wait(lambda: settled("cam2", [BLUE, BLUE])), SEEN["cam2"][-3:])
    check("cam1's person keeps cam1's own colour - the shared rule is judged "
          "by cam1's own number", wait(lambda: settled("cam1", [RED])),
          SEEN["cam1"][-3:])
    print("== 3. the zone colour")
    check("cam2's rule paints cam2's zone",
          wait(lambda: settled("cam2", [BLUE, BLUE], {"bay": GREEN})),
          SEEN["cam2"][-3:])
    check("...and cam1 has no zone to paint", settled("cam1", [RED], {}),
          SEEN["cam1"][-3:])


Run().go(drive)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
