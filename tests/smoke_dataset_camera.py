# -*- coding: utf-8 -*-
"""
smoke_dataset_camera.py -- the Dataset page captures from the camera chosen.

Owner request (2026-10-13): "dataset camera: please allow user to choose
which camera if there is more than 1 camera, then it will record or take
photo using the chosen camera." Before, every photo and clip came from the
app's bare /raw path, which is the FIRST camera.

Driven against a fake app stream server with two cameras that send different
pictures (cam1 red, cam2 blue), so "which camera did this come from" is a
fact about the saved bytes, not about which URL was asked for:

  1. GET lists the cameras by id and name only -- no source, no password --
     and starts at the first running camera
  2. choose camera 2 -> the photo IS camera 2's frame, tagged camera 2, and
     the choice is remembered per system (config.local.toml [dataset])
  3. a clip from camera 2 is camera 2's picture, tagged too
  4. no camera named -> the bare path, camera 1, untagged, nothing written to
     config: the old behaviour exactly
  5. an unknown id, a path, a URL, a non-string: a 400, before any stream
  6. a camera that is off (config), not run by the app, faulted, or 404'd by
     the app: "This camera is not running" -- and the bare path (camera 1) is
     NEVER asked as a fallback (the rule_camera lesson, 1594944d)

    python tests/smoke_dataset_camera.py
"""
import asyncio
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# The dev box runs the suite without FastAPI; the routes only need its names
# (the same stub smoke_overlayroute uses).
if "fastapi" not in sys.modules:
    try:
        import fastapi                                         # noqa: F401
    except ImportError:
        fa = types.ModuleType("fastapi")
        fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
            get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
            put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
            patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail
        fa.HTTPException = HTTPException
        fa.Request = fa.UploadFile = object
        fa.Query = fa.File = fa.Form = lambda *a, **k: None
        fa.__path__ = []
        sys.modules["fastapi"] = fa
        resp = types.ModuleType("fastapi.responses")
        resp.StreamingResponse = resp.JSONResponse = resp.RedirectResponse = object
        resp.FileResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp
        st = types.ModuleType("starlette")
        st.__path__ = []
        bg = types.ModuleType("starlette.background")
        bg.BackgroundTask = object
        sr = types.ModuleType("starlette.requests")
        sr.ClientDisconnect = type("ClientDisconnect", (Exception,), {})
        sys.modules.update({"starlette": st, "starlette.background": bg,
                            "starlette.requests": sr})

from fastapi import HTTPException                              # noqa: E402

from core.routes import models as models_routes                # noqa: E402
from core.routes import perm                                   # noqa: E402
from core.services import dataset_capture as cap              # noqa: E402
from core.services import dataset_record as rec               # noqa: E402
from core.services import detector_control as dc              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


try:
    import cv2
    import numpy as np
except ImportError:                                           # pragma: no cover
    cv2 = np = None


def solid(bgr):
    """A small JPEG of one colour, or a stand-in when cv2 is missing."""
    if cv2 is None:
        return b"\xff\xd8" + bytes(bgr) * 8 + b"\xff\xd9"
    img = np.zeros((48, 64, 3), np.uint8)
    img[:] = bgr
    return cv2.imencode(".jpg", img)[1].tobytes()


FRAMES = {"cam1": solid((0, 0, 255)),      # red
          "cam2": solid((255, 0, 0))}      # blue
#: Every request the fake app saw, as (path, camera) -- the proof that a named
#: camera never falls back to the bare path.
SEEN = []
#: Cameras the fake app refuses (404), as an app does for a camera it has no
#: pipe for.
REFUSE = set()


class FakeApp(http.server.BaseHTTPRequestHandler):
    """The app's stream server, as far as /raw goes: ?camera= picks, a bare
    path is the first camera, an id it does not run is a 404."""

    def do_GET(self):                        # noqa: N802
        path, _, query = self.path.partition("?")
        cam = ""
        for part in query.split("&"):
            k, _, v = part.partition("=")
            if k == "camera":
                cam = v
        SEEN.append((path, cam))
        if path != "/raw":
            self.send_error(404)
            return
        cid = cam or "cam1"
        if cid not in FRAMES or cid in REFUSE:
            self.send_error(404, "no such camera")
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        jpeg = FRAMES[cid]
        try:
            for _ in range(200):
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeApp)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_address[1]

# A real system package: two cameras and a spare that is switched off. cam2's
# label is the pre-1.0.37 kind with the password in it.
SAND = tempfile.mkdtemp()
SLUG = "two_cams"
PKG = os.path.join(SAND, SLUG)
os.makedirs(os.path.join(PKG, "data"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'''[app]
name = "Two cameras"
stream_port = {PORT}

[[cameras]]
id = "cam1"
name = "Front gate"
source = "rtsp://10.0.0.4/a"

[[cameras]]
id = "cam2"
label = "IP camera admin:secret@10.0.0.5"
source = "rtsp://admin:secret@10.0.0.5/stream"

[[cameras]]
id = "cam3"
name = "Spare"
source = "rtsp://10.0.0.6/c"
enabled = false
''')

NVME = tempfile.mkdtemp()
cap.DATASET_ROOT = NVME
cap.dataset_state = lambda repair=True, force=False: {"ok": True, "reason": "", "detail": ""}

#: What the app reports about itself (state.json, through dc.status).
LIVE = {"running": True, "cameras": [
    {"id": "cam1", "measured_fps": 10, "error": None},
    {"id": "cam2", "measured_fps": 10, "error": None}]}
dc.status = lambda systems_dir, slug, reveal=False: json.loads(json.dumps(LIVE))
perm.require = lambda request, cap_name: None


def request(body=None):
    async def _json():
        return body or {}
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        system=types.SimpleNamespace(slug=SLUG), systems_dir=SAND, config_dir=SAND))
    return types.SimpleNamespace(app=app, state=types.SimpleNamespace(user=None),
                                 json=_json, headers={})


def run(coro):
    return asyncio.run(coro)


def snap(body):
    """POST /api/detector/dataset/snap -> (status, answer)."""
    try:
        return 200, run(models_routes.detector_dataset_snap(request(body)))
    except HTTPException as exc:
        return exc.status_code, exc.detail


def record(body):
    try:
        return 200, run(models_routes.detector_dataset_record(request(body)))
    except HTTPException as exc:
        return exc.status_code, exc.detail


def local_toml():
    p = os.path.join(PKG, "config.local.toml")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def img_dir():
    return cap.dataset_dir("image", SLUG)


# -- 1. what the page is told ---------------------------------------------------
got = models_routes.detector_dataset(request())
ids = [c["id"] for c in got["cameras"]]
check("GET lists the system's cameras in order", ids == ["cam1", "cam2", "cam3"], ids)
check("...by name: the operator's, a cleaned label, never an address password",
      [c["name"] for c in got["cameras"]] == ["Front gate", "IP camera 10.0.0.5", "Spare"],
      got["cameras"])
check("...ids, names and on/off only - no source reaches the page",
      all(sorted(c) == ["enabled", "id", "name"] for c in got["cameras"])
      and "secret" not in json.dumps(got), got["cameras"])
check("...the switched-off camera says so", got["cameras"][2]["enabled"] is False)
check("nothing chosen yet: starts at the first running camera",
      got["camera"] == "cam1" and got["default_camera"] == "cam1", got)

# -- 4 (first, while config is untouched). no camera named = the old path --------
SEEN.clear()
status, ans = snap({"name": "plain"})
check("no camera named: a photo is saved", status == 200 and ans.get("file"), ans)
saved = open(os.path.join(img_dir(), ans["file"]), "rb").read() if status == 200 else b""
check("...from the bare path, camera 1, as before",
      saved == FRAMES["cam1"] and SEEN == [("/raw", "")], SEEN)
check("...untagged, and the answer has no camera keys",
      "camera" not in ans and "camera_name" not in ans, ans)
check("...and nothing was written to the system's config", local_toml() == "", local_toml())

# -- 2. choose camera 2 ------------------------------------------------------------
SEEN.clear()
time.sleep(1.1)                       # a new second: a new file name
status, ans = snap({"name": "bay", "camera": "cam2"})
check("camera 2 chosen: the photo is saved", status == 200, ans)
saved = open(os.path.join(img_dir(), ans["file"]), "rb").read() if status == 200 else b""
check("...and it IS camera 2's frame, not camera 1's",
      saved == FRAMES["cam2"] and saved != FRAMES["cam1"])
check("...asked of camera 2 only", SEEN == [("/raw", "cam2")], SEEN)
check("...the answer names the camera",
      ans.get("camera") == "cam2" and ans.get("camera_name") == "IP camera 10.0.0.5", ans)
listed = cap.dataset_list(SLUG)["images"]
row = next((r for r in listed if r["name"] == ans.get("file")), {})
plain = next((r for r in listed if r["name"].startswith("plain_")), {})
check("the library row is tagged camera 2",
      row.get("camera") == "cam2" and row.get("camera_name") == "IP camera 10.0.0.5", row)
check("...and the earlier untagged photo still lists, without a camera",
      plain and "camera" not in plain, plain)
check("the tag index is not listed as a capture",
      all(not r["name"].startswith(".") for r in listed), listed)
check("...nor served, zipped or deleted as one",
      os.path.exists(os.path.join(img_dir(), cap.CAMERA_INDEX))
      and cap.dataset_path("image", cap.CAMERA_INDEX, SLUG) is None)
check("the choice is remembered per system ([dataset] camera)",
      'camera = "cam2"' in local_toml() and "[dataset]" in local_toml(), local_toml())
check("...and GET starts there next time",
      models_routes.detector_dataset(request())["camera"] == "cam2")
mtime = os.path.getmtime(os.path.join(PKG, "config.local.toml"))
time.sleep(1.1)
snap({"name": "again", "camera": "cam2"})
check("the same choice again does not rewrite the config the app watches",
      os.path.getmtime(os.path.join(PKG, "config.local.toml")) == mtime)

# -- 3. a clip from camera 2 ---------------------------------------------------------
if cv2 is not None:
    SEEN.clear()
    status, ans = record({"name": "clip", "duration_s": 1, "camera": "cam2"})
    check("a clip from camera 2 starts", status == 200 and ans.get("camera") == "cam2", ans)
    job = cap._dataset_jobs.get(SLUG) or {}
    deadline = time.time() + 20
    while not job.get("done") and time.time() < deadline:
        time.sleep(0.1)
    check("...and finishes without an error", job.get("done") and not job.get("error"), job)
    check("...reading camera 2 only", SEEN == [("/raw", "cam2")], SEEN)
    path = os.path.join(cap.dataset_dir("clip", SLUG), ans.get("file", "x"))
    ok_frame = None
    vc = cv2.VideoCapture(path)
    ok, frame = vc.read()
    vc.release()
    if ok:
        b, g, r = [float(frame[..., i].mean()) for i in range(3)]
        ok_frame = b > 150 and r < 80
    check("...and the clip's picture is camera 2's (blue), not camera 1's (red)",
          ok_frame is True, f"read={ok}")
    clips = cap.dataset_list(SLUG)["clips"]
    crow = next((r for r in clips if r["name"] == ans.get("file")), {})
    check("...tagged camera 2 in the library", crow.get("camera") == "cam2", crow)
else:
    print("skip cv2 is not installed here - the clip half is not exercised")

# -- 5. ids that are not this system's cameras ------------------------------------------
SEEN.clear()
for bad_id, why in (("cam9", "an unknown id"), ("../../etc/passwd", "a path"),
                    ("rtsp://10.0.0.9/x", "a URL"), ("http://127.0.0.1:1/raw", "a URL")):
    status, detail = snap({"name": "x", "camera": bad_id})
    check(f"{why} is a 400", status == 400, (status, detail))
status, detail = snap({"name": "x", "camera": {"id": "cam2"}})
check("a non-string camera is a 400", status == 400, (status, detail))
status, detail = snap({"name": "x", "camera": "cam9"})
check("...with words that say what is wrong", "no camera" in str(detail), detail)
status, detail = record({"name": "x", "camera": "cam9"})
check("recording an unknown camera is a 400 too", status == 400, (status, detail))
check("none of them reached the app at all", SEEN == [], SEEN)

# -- 6. a camera that is not running: an error, never camera 1 ------------------------------
NOT = cap.NOT_RUNNING


def refused(label, body, rec_too=True):
    SEEN.clear()
    before = sorted(os.listdir(img_dir()))
    status, detail = snap(body)
    check(f"{label}: the photo is refused with '{NOT}'",
          status == 400 and detail == NOT, (status, detail))
    check(f"{label}: ...no photo was saved", sorted(os.listdir(img_dir())) == before)
    check(f"{label}: ...and camera 1 was never asked instead",
          ("/raw", "") not in SEEN and ("/raw", "cam1") not in SEEN, SEEN)
    if rec_too:
        status, detail = record(dict(body, duration_s=1))
        check(f"{label}: a clip is refused the same way, before it starts",
              status == 400 and detail == NOT, (status, detail))


refused("switched off in config", {"name": "x", "camera": "cam3"})
LIVE["cameras"] = [c for c in LIVE["cameras"] if c["id"] != "cam2"]
refused("not run by the app", {"name": "x", "camera": "cam2"})
LIVE["cameras"].append({"id": "cam2", "measured_fps": 0, "error": "camera read failed"})
refused("faulted", {"name": "x", "camera": "cam2"})
LIVE["cameras"][-1]["error"] = None
LIVE["running"] = False
refused("detection stopped", {"name": "x", "camera": "cam2"})
LIVE["running"] = True
# An app too old to report per camera: its own 404 is the answer.
LIVE["cameras"] = [{"id": "cam1"}, {"id": "cam2"}]
REFUSE.add("cam2")
refused("refused by the app (404)", {"name": "x", "camera": "cam2"}, rec_too=False)
REFUSE.clear()

# A clip whose camera the app refuses AFTER the checks: the job errors with the
# same words, and no bare-path stream is opened to fill it.
if cv2 is not None:
    REFUSE.add("cam2")
    SEEN.clear()
    status, ans = record({"name": "late", "duration_s": 1, "camera": "cam2"})
    job = cap._dataset_jobs.get(SLUG) or {}
    deadline = time.time() + 10
    while not job.get("done") and time.time() < deadline:
        time.sleep(0.1)
    check("a clip refused by the app ends with the same error",
          job.get("error") == NOT, job.get("error"))
    check("...without falling back to camera 1", SEEN == [("/raw", "cam2")], SEEN)
    REFUSE.clear()

# -- defaults ----------------------------------------------------------------------------------
check("the default is the first ENABLED camera",
      cap.default_camera([{"id": "a", "enabled": False}, {"id": "b", "enabled": True}]) == "b")
with open(os.path.join(PKG, "config.local.toml"), "w", encoding="utf-8") as f:
    f.write('[dataset]\ncamera = "gone"\n')
check("a remembered camera that was removed falls back to the default",
      models_routes.detector_dataset(request())["camera"] == "cam1")

server.shutdown()
print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
