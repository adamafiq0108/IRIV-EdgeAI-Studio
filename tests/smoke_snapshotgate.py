"""Preview snapshot is a camera change, and its escape hatches are master's.

WHAT WAS WRONG (security review F1). GET /api/detector/snapshot checked only
that it was answering on a system. Any signed-in account could pass any
`source`, and since the camera-cause probe a failed open connects out to the
host:port the URL names and reports what answered -- a LAN and loopback port
scanner with a status oracle. open_capture_spec also takes "gst:<pipeline>"
(a pipeline can write files as the master's user) and "file:<abs path>"
(another system's media, or anything else on disk).

Pinned, by calling the route the way FastAPI would:
  * an ordinary account is refused whatever the source
  * a site admin with the camera grant previews USB, GigE, depth and network
    cameras, and is refused gst:, file:, a bare path and file://
  * master and developer preview everything, escape hatches included
  * the page offers the button only to whoever the server would answer
  * a USB camera held by this system's own app is not "another program"

    python tests/smoke_snapshotgate.py
"""
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

# The dev box runs the suite without FastAPI; the route only needs its names.
if "fastapi" not in sys.modules:
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
    resp.JSONResponse = resp.RedirectResponse = resp.FileResponse =         resp.StreamingResponse = object
    resp.Response = lambda content=None, media_type=None, **k: content
    sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402

from core import config                                       # noqa: E402
from core.routes import models as routes                      # noqa: E402
from core.services import auth                                # noqa: E402

CFG = tempfile.mkdtemp()
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", "line_a", CFG, admin=True)
auth.create_user("sam", "passw0rd!", "line_a", CFG)
local = config.load_local("users", CFG)
local["users"]["dee"] = {"salt": "x", "password_hash": "y", "systems": "*",
                         "enabled": True, "developer": True}
config.save_local("users", local, CFG)
if hasattr(auth._cached_local, "cache_clear"):
    auth._cached_local.cache_clear()


def preview(user, source):
    """200 or the refusal's status, with the capture itself stubbed out."""
    req = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, systems_dir=tempfile.gettempdir(),
            system=SimpleNamespace(slug="line_a"))),
        state=SimpleNamespace(user=user))
    with mock.patch.object(routes.detector_control_service, "snapshot",
                           return_value=b"\xff\xd8jpeg\xff\xd9"):
        try:
            routes.detector_snapshot(req, source=source, width=None, height=None,
                                     fps=None, exposure_auto="", exposure_us=None,
                                     gain_auto="", gain_db=None, binning=None)
            return 200
        except HTTPException as e:
            return e.status_code


PLAIN = ["0", "gige", "gige:Basler-1", "orbbec", "rtsp://admin:x@192.168.1.50/live",
         "http://127.0.0.1:8080/video"]
HATCH = ["gst:videotestsrc ! appsink", "file:/home/pi/other/clip.mp4",
         "/etc/passwd", "file:///etc/passwd", "concat:/a|/b"]

for src in PLAIN + HATCH:
    check(f"an ordinary account cannot preview {src!r}", preview("sam", src) == 403)
for src in PLAIN:
    check(f"a site admin with the camera grant previews {src!r}",
          preview("ada", src) == 200)
for src in HATCH:
    check(f"...but not the escape hatch {src!r}", preview("ada", src) == 403)
for who in ("boss", "dee"):
    for src in PLAIN + HATCH:
        check(f"{who} previews {src!r}", preview(who, src) == 200)

auth.update_user("ada", caps={"line_a": ["detection.camera.add"]}, config_dir=CFG)
check("a site admin who may only ADD a camera still previews in the add dialog",
      preview("ada", "0") == 200)
auth.update_user("ada", caps={"line_a": []}, config_dir=CFG)
check("...and one with neither grant is refused", preview("ada", "0") == 403)

# -- the holder is this system itself ---------------------------------------
# The probe calls whoever holds a USB camera "another program"; when that is
# this system's own app, there is nothing to close (Yaya, v1.0.13 review).
def busy_detail(holder_pid, app_pid):
    req = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, systems_dir=tempfile.gettempdir(),
            system=SimpleNamespace(slug="line_a"))),
        state=SimpleNamespace(user="boss"))
    err = RuntimeError(f"cannot open camera '0': in use by another program "
                       f"(python3, pid {holder_pid})")
    with mock.patch.object(routes.detector_control_service, "snapshot",
                           side_effect=err), \
            mock.patch.object(routes.detector_control_service, "status",
                              return_value={"pid": app_pid}):
        try:
            routes.detector_snapshot(req, source="0", width=None, height=None,
                                     fps=None, exposure_auto="", exposure_us=None,
                                     gain_auto="", gain_db=None, binning=None)
        except HTTPException as e:
            return e.detail
    return ""


own = busy_detail(812, 812)
check("a camera held by this system's own app says so",
      own == "cannot open camera '0': in use by this system's own detection", own)
other = busy_detail(901, 812)
check("...and one held by anything else is still another program",
      "in use by another program (python3, pid 901)" in other, other)
check("...as is one while this system's app is not running",
      "another program" in busy_detail(901, None))
js = open(os.path.join(ROOT, "frontend", "src", "lib", "camErrors.js"),
          encoding="utf-8").read()
check("the page has a sentence for it", "in use by this system's own detection" in js)

page = open(os.path.join(ROOT, "frontend", "src", "pages", "Detection.jsx"),
            encoding="utf-8").read()
btn = page[page.index("onClick={snapPreview}"):page.index("Preview Snapshot\n")
           if "Preview Snapshot\n" in page else page.index("Preview Snapshot\r\n")]
check("the page disables Preview Snapshot for an account the server would refuse",
      "!editable" in btn and 'can("detection.camera.change")' in btn
      and 'can("detection.camera.add")' in btn, btn)

# -- no camera chosen, and a detector that is not running -------------------
# An empty source went all the way to the open and came back as "it may be in
# use by the running detector" -- with nothing chosen and nothing running.
def refusal(source, err=None, running=False):
    req = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, systems_dir=tempfile.gettempdir(),
            system=SimpleNamespace(slug="line_a"))),
        state=SimpleNamespace(user="boss"))
    with mock.patch.object(routes.detector_control_service, "snapshot",
                           side_effect=err or RuntimeError("never opened")) as snap, \
            mock.patch.object(routes.detector_control_service, "status",
                              return_value={"running": running, "pid": None}):
        try:
            routes.detector_snapshot(req, source=source, width=None, height=None,
                                     fps=None, exposure_auto="", exposure_us=None,
                                     gain_auto="", gain_db=None, binning=None)
        except HTTPException as e:
            return e.status_code, e.detail, snap.called
    return 200, "", True


for empty in ("", "   "):
    code, detail, opened = refusal(empty)
    check(f"an empty source {empty!r} is refused before any open",
          code == 400 and not opened, (code, detail, opened))
    check("...saying choose a camera first", "choose a camera first" in detail, detail)
check("...in the words camErrors.js maps to no_source",
      "no camera chosen" in refusal("")[1])
GUESS = RuntimeError("cannot open camera 'x.mp4' - it may be in use by the running "
                     "detector, absent, or an unreachable URL.")
_, stopped, _ = refusal("0", GUESS, running=False)
check("a failed open does not blame the running detector when none runs",
      "running detector" not in stopped and "absent" in stopped, stopped)
_, live, _ = refusal("0", GUESS, running=True)
check("...and still names it while the detector runs",
      "in use by the running detector" in live, live)

print("\n%s" % ("all good" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
