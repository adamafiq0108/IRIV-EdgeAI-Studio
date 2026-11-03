# A camera's password is shown only to who may change the camera, a raw
# source is the box owner's to save, and a GigE name cannot write pipeline.
#
# WHAT WAS WRONG (Pipi F4 and the camera-save review, item 7):
#   * GET /api/detector/control and /api/detector/control/camera returned
#     every camera's source -- rtsp://user:PASSWORD@host -- to any account
#     that could open the system;
#   * the snapshot preview carried that URL in a GET query string, and
#     uvicorn's own access line printed query strings to the journal;
#   * the app's state.json repeated every source in clear;
#   * a site admin granted camera.add/change could SAVE gst:, file: or a bare
#     path as a camera, although the preview already kept those master-only;
#   * "gige:<name>" was pasted inside double quotes in the aravissrc pipeline
#     text, so a name with a quote appended elements of its own;
#   * an MQTT connection moved to another host or port kept the old broker's
#     password (pinned in smoke_mqttclients).
#
#     python tests/smoke_camsecrets.py
import logging
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

if "fastapi" not in sys.modules:
    try:
        import fastapi                                       # noqa: F401
    except ImportError:
        fa = types.ModuleType("fastapi")

        class HTTPException(Exception):
            def __init__(self, status_code=500, detail=""):
                super().__init__(detail)
                self.status_code, self.detail = status_code, detail

        def _route(*_a, **_k):
            return lambda fn: fn

        class APIRouter:
            get = post = put = delete = patch = staticmethod(_route)

        fa.HTTPException, fa.APIRouter = HTTPException, APIRouter
        fa.Request = fa.UploadFile = object
        fa.File = fa.Form = fa.Query = lambda *a, **k: None
        sys.modules["fastapi"] = fa
        resp = types.ModuleType("fastapi.responses")
        resp.JSONResponse = resp.RedirectResponse = resp.FileResponse = \
            resp.StreamingResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402

from core import config                                       # noqa: E402
from core.services import auth                                # noqa: E402
from core.services import detector_control as dc              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


R = dc.SOURCE_REDACTED

# -- masking ---------------------------------------------------------------------
for src, want in (
        ("rtsp://admin:P@ssw0rd@192.168.1.10/s", f"rtsp://admin:{R}@192.168.1.10/s"),
        ("rtsp://u:pa:ss@1.2.3.4/", f"rtsp://u:{R}@1.2.3.4/"),
        ("http://cam:x@10.0.0.2:8080/video", f"http://cam:{R}@10.0.0.2:8080/video"),
        ("rtsp://10.0.0.3/live", "rtsp://10.0.0.3/live"),
        ("rtsp://useronly@10.0.0.3/live", "rtsp://useronly@10.0.0.3/live"),
        ("0", "0"), ("gige:Basler-1", "gige:Basler-1")):
    check(f"mask {src!r}", dc.mask_source(src) == want, dc.mask_source(src))
stored = "rtsp://admin:P@ssw0rd@192.168.1.10/s"
check("an exact round trip of a masked source restores the stored one",
      dc.unmask_source(dc.mask_source(stored), stored) == stored)
check("a source with no marker is taken as typed",
      dc.unmask_source("rtsp://a:new@h/", stored) == "rtsp://a:new@h/")
try:
    dc.unmask_source(f"rtsp://admin:{R}@6.6.6.6/s", stored)
    check("a masked password aimed at ANOTHER host is refused, not carried over", False)
except ValueError:
    check("a masked password aimed at ANOTHER host is refused, not carried over", True)

# -- the raw-source gate and the GigE name -----------------------------------------
for src in ("0", "gige", "gige:Basler acA1300-60gc (21234567)", "orbbec",
            "rtsp://a:b@h/", "https://h/x"):
    check(f"plain: {src!r}", dc.plain_source(src))
for src in ("gst:videotestsrc ! appsink", "file:clip.mp4", "/etc/passwd",
            "file:///etc/passwd", "udp://1.2.3.4:5000", 'gige:x" ! filesink location=/tmp/p'):
    check(f"escape hatch: {src!r}", not dc.plain_source(src))
try:
    dc.gst_gige_pipeline(camera_name='x" ! filesink location=/tmp/p')
    check("the pipeline builder refuses a GigE name that closes its quotes", False)
except ValueError:
    check("the pipeline builder refuses a GigE name that closes its quotes", True)
check("...and quotes an ordinary one",
      'camera-name="Basler-1"' in dc.gst_gige_pipeline(camera_name="Basler-1"))
try:
    dc._validate_camera({"source": 'gige:x"!y'})
    check("a camera entry with such a name is refused at save", False)
except ValueError:
    check("a camera entry with such a name is refused at save", True)

# -- the save path: restore, and refuse raw for non-owners ----------------------------
systems = tempfile.mkdtemp()
slug = "demo"
os.makedirs(os.path.join(systems, slug, "app"), exist_ok=True)
with open(os.path.join(systems, slug, "system.toml"), "w", encoding="utf-8") as f:
    f.write('[system]\nname = "demo"\n')
shelf = [{"id": "gate", "source": stored, "kind": "rtsp", "enabled": True},
         {"id": "pipe", "source": "gst:videotestsrc ! appsink", "kind": "file",
          "enabled": True}]
written = {}
real = (dc.cameras, dc._write_local, dc.sources_elsewhere)
dc.cameras = lambda *_a, **_k: [dict(c) for c in shelf]
dc._write_local = lambda pkg, local: written.update(local)
dc.sources_elsewhere = lambda *_a, **_k: []
try:
    masked = dc.mask_cameras(shelf)
    dc.set_cameras(systems, slug, masked, allow_raw=False)
    saved = {c["id"]: c["source"] for c in written["cameras"]}
    check("a whole list read masked and sent back keeps the real password",
          saved["gate"] == stored and R not in str(written))
    check("...and an UNCHANGED escape-hatch source a master set passes for a site admin",
          saved["pipe"] == "gst:videotestsrc ! appsink")
    try:
        dc.set_cameras(systems, slug, masked + [{"id": "x", "source": "file:/etc/shadow"}],
                       allow_raw=False)
        check("a NEW escape-hatch source is refused for a non-owner", False)
    except dc.RawSourceRefused:
        check("a NEW escape-hatch source is refused for a non-owner", True)
    try:
        dc.set_camera(systems, slug, "gst:fakesrc ! filesink location=/tmp/x",
                      camera_id="gate", allow_raw=False)
        check("...and so is re-pointing a camera at one", False)
    except dc.RawSourceRefused:
        check("...and so is re-pointing a camera at one", True)
    try:
        dc.add_camera(systems, slug, {"source": "/dev/video0"}, allow_raw=False)
        check("...and adding one", False)
    except dc.RawSourceRefused:
        check("...and adding one", True)
    try:
        dc.add_camera(systems, slug, {"source": f"rtsp://admin:{R}@6.6.6.6/"})
        check("a NEW camera cannot borrow a hidden password", False)
    except ValueError:
        check("a NEW camera cannot borrow a hidden password", True)
    written.clear()
    dc.set_camera(systems, slug, dc.mask_source(stored), camera_id="gate", allow_raw=False)
    check("set_camera with the masked source it was shown keeps the password",
          {c["id"]: c["source"] for c in written["cameras"]}["gate"] == stored)
    written.clear()
    dc.add_camera(systems, slug, {"id": "p2", "source": "gst:videotestsrc ! appsink"})
    check("the box owner (allow_raw, the default) still saves a pipeline",
          any(c["id"] == "p2" for c in written["cameras"]))
finally:
    dc.cameras, dc._write_local, dc.sources_elsewhere = real

# -- the routes -------------------------------------------------------------------------
from core.routes import models as routes                      # noqa: E402

CFG = tempfile.mkdtemp()
auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", slug, CFG, admin=True)     # site admin, camera grant
auth.create_user("sam", "passw0rd!", slug, CFG)                 # operator
if hasattr(auth._cached_local, "cache_clear"):
    auth._cached_local.cache_clear()


def req(user):
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, systems_dir=systems, system=SimpleNamespace(slug=slug))),
        state=SimpleNamespace(user=user))


cams = [dict(c) for c in shelf]
with mock.patch.object(routes.detector_control_service, "cameras", return_value=cams), \
        mock.patch.object(routes.detector_control_service, "camera_budget", return_value={}), \
        mock.patch.object(routes, "_system_context", return_value=(systems, slug)):
    out = routes.detector_get_cameras(req("sam"))
    check("an operator's camera list hides the password",
          "P@ssw0rd" not in str(out) and R in str(out))
    out = routes.detector_get_cameras(req("boss"))
    check("master, who may change the camera, sees it", "P@ssw0rd" in str(out))
    status = {"cameras": [dict(c) for c in cams], "camera": {"source": stored}}
    with mock.patch.object(routes.detector_control_service, "status", return_value=status):
        out = routes.detector_control_status(req("sam"))
    check("the Detection page's status hides it too (cameras and camera)",
          "P@ssw0rd" not in str(out) and out["camera"]["source"] == dc.mask_source(stored))

models_src = read("core", "routes", "models.py")
check("the snapshot preview is a POST with a body",
      '@router.post("/api/detector/snapshot"' in models_src
      and "async def detector_snapshot_post(request: Request):" in models_src)
check("...the GET stays one release, with a WARNING that never carries the source",
      '@router.get("/api/detector/snapshot"' in models_src
      and "GET /api/detector/snapshot is deprecated" in models_src
      and 'resp.headers["Deprecation"] = "true"' in models_src)
records = []
handler = logging.Handler()
handler.emit = records.append
routes.logger.addHandler(handler)
with mock.patch.object(routes.detector_control_service, "snapshot", return_value=b"jpeg"):
    try:
        routes.detector_snapshot(req("boss"), source=stored, width=None, height=None,
                                 fps=None, exposure_auto="", exposure_us=None,
                                 gain_auto="", gain_db=None, binning=None)
    except HTTPException:
        pass
routes.logger.removeHandler(handler)
check("...and the warning line is written without the URL",
      records and all("P@ssw0rd" not in r.getMessage() for r in records))
check("the save routes pass allow_raw from the caller (master/developer only)",
      models_src.count("allow_raw=_raw_ok(request)") == 3
      and models_src.count("except detector_control_service.RawSourceRefused") == 3)

for name in ("master_server.py", "hub_server.py"):
    src = read(name)
    check(f"{name}: uvicorn's own access line (query strings) is off",
          "access_log=False" in src.split("uvicorn.run(", 1)[1][:200])
run = read("systems", "demo", "app", "run.py")
check("state.json's cameras[].source is written masked",
      '"source": faults.redact(str(p.cfg.get("source", "")))' in run)
cam = read("systems", "demo", "app", "camera.py")
check("the app refuses a GigE name that would close its quotes, too",
      're.match(r"^[\\w .:()+/-]{1,128}$", name)' in cam)

sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import gst_detector                                  # noqa: E402
pipe = gst_detector._source_pipeline('rtsp://u:p!a"ss w@h/s', 0, 0, 0)
check("the GStreamer detector quotes a URL (a '!' or space in a password ends nothing)",
      pipe.startswith('rtspsrc location="rtsp://u:p!a\\"ss w@h/s" '), pipe)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
