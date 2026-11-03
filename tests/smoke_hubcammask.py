# The hub's System Control table never carries a camera password.
#
# WHAT WAS WRONG: a64efa56 masked rtsp://user:PASSWORD@host everywhere the
# Detection page reads it, but GET /api/hub/pipelines still returned
# `"camera": camera.get("source")` raw to every hub viewer who could see the
# row -- the same secret one page over. `camera_label` had the same hole for
# any URL that was not rtsp:// (its fallback returned the source as-is).
#
#     python tests/smoke_hubcammask.py
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

# The CI box has no fastapi; the route under test needs none of it at call
# time, so a stand-in that turns the decorators into no-ops is enough.
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
        resp.JSONResponse = resp.RedirectResponse = resp.FileResponse =             resp.StreamingResponse = object
        resp.Response = lambda content=None, media_type=None, **k: content
        sys.modules["fastapi.responses"] = resp

from core.routes import hub as hub_routes                    # noqa: E402
from core.services import detector_control                   # noqa: E402

SECRET = "s3cr@t:pw"
RTSP = f"rtsp://admin:{SECRET}@192.0.2.10:554/stream1"
HTTP = f"http://viewer:{SECRET}@192.0.2.11/mjpeg"
RED = detector_control.SOURCE_REDACTED

STATUS = {
    "cam1": {"running": True, "camera": {"source": RTSP},
             "cameras": [{"id": "a", "source": RTSP}]},
    "cam2": {"running": False, "camera": {"source": HTTP},
             "cameras": [{"id": "b", "source": HTTP}]},
    "usb": {"running": False, "camera": {"source": "0"}, "cameras": []},
    "none": {"running": False},
}

registry = [SimpleNamespace(slug=s, display_name=s.upper()) for s in STATUS]
request = SimpleNamespace(
    app=SimpleNamespace(state=SimpleNamespace(
        registry=registry, config_dir="cfg", systems_dir="sys")),
    state=SimpleNamespace(user="viewer"), headers={})

with mock.patch.object(hub_routes.config, "systems",
                       return_value={"masters": {s: {} for s in STATUS}}), \
     mock.patch.object(hub_routes, "_reachable", return_value={}), \
     mock.patch.object(hub_routes.hub_service, "visible_to", return_value=True), \
     mock.patch.object(hub_routes.provision_service, "_pkg_root",
                       side_effect=lambda _d, slug: slug), \
     mock.patch.object(detector_control, "status",
                       side_effect=lambda root, _slug: STATUS[root]), \
     mock.patch.object(detector_control, "powered_off", return_value=False), \
     mock.patch.object(detector_control, "get_capture", return_value={}), \
     mock.patch.object(hub_routes.net, "rewrite", side_effect=lambda u, _r: u):
    out = hub_routes.hub_pipelines(request)

body = out if isinstance(out, dict) else __import__("json").loads(out.body)
rows = {r["slug"]: r for r in body["pipelines"]}
assert set(rows) == set(STATUS), body

# 1. no password anywhere in the answer, whatever the protocol
dump = repr(body)
assert "s3cr" not in dump, dump
print("== no camera password anywhere in /api/hub/pipelines")

# 2. the user and host stay readable -- they are what an operator checks
assert rows["cam1"]["camera"] == f"rtsp://admin:{RED}@192.0.2.10:554/stream1", rows["cam1"]
assert rows["cam2"]["camera"] == f"http://viewer:{RED}@192.0.2.11/mjpeg", rows["cam2"]
print("== camera keeps user and host, password reads " + RED)

# 3. the label fallback (non-rtsp URL) is masked too; others unchanged
assert rows["cam1"]["camera_label"] == "IP camera 192.0.2.10", rows["cam1"]
assert RED in rows["cam2"]["camera_label"], rows["cam2"]
assert rows["usb"]["camera"] == "0" and rows["usb"]["camera_label"] == "USB 0"
print("== camera_label hides the password of a non-rtsp URL")

# 4. a system with no camera still answers null, not "" (the table's check)
assert rows["none"]["camera"] is None, rows["none"]
print("== no camera stays null")
print("ALL OK")
