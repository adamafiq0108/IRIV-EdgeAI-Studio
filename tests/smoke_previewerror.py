# The live-preview relay says the AI-5003 sentence when the detection app is
# down or answers anything but 200 -- and logs it as ONE WARNING line.
#
# It used to tell the operator "detection app answered 401 on :5102" (an HTTP
# code and a port, in front of somebody watching a blank video panel) and
# write a full traceback for a 502 the route had already handled. The
# technical text now rides in the response's `details` field and the record's
# detail, never in `error`, which is the toast.
import asyncio
import http.server
import os
import socket
import sys
import tempfile
import threading
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# The dev box runs the suite without FastAPI; the route and the handler only
# need its names (the same stub smoke_snapshotgate uses, plus a JSONResponse
# that keeps what it was given).
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

    class JSONResponse:
        def __init__(self, status_code=200, content=None, headers=None):
            self.status_code, self.content, self.headers = status_code, content, headers
    resp.JSONResponse = JSONResponse
    resp.RedirectResponse = resp.FileResponse = resp.StreamingResponse = object
    resp.Response = lambda content=None, media_type=None, **k: content
    sys.modules["fastapi.responses"] = resp
if "starlette.requests" not in sys.modules:
    try:
        import starlette.requests                             # noqa: F401
    except ImportError:
        st = types.ModuleType("starlette")
        st.__path__ = []
        sr = types.ModuleType("starlette.requests")
        sr.ClientDisconnect = type("ClientDisconnect", (Exception,), {})
        sys.modules["starlette"], sys.modules["starlette.requests"] = st, sr

from fastapi import HTTPException                             # noqa: E402

from core.routes import logs as logs_routes                   # noqa: E402
from core.routes import models as models_routes               # noqa: E402
from core.services import log_catalog as catalog              # noqa: E402
from core.services import logs as logs_service                # noqa: E402

FAIL = []
SENTENCE = catalog.CATALOG["AI_SERVICE_UNAVAILABLE"].message


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name + (" -- " + str(detail) if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


class _Answer401(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                         # noqa: N802
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


upstream = http.server.HTTPServer(("127.0.0.1", 0), _Answer401)
threading.Thread(target=upstream.serve_forever, daemon=True).start()
UP_PORT = upstream.server_address[1]

s = socket.socket()
s.bind(("127.0.0.1", 0))
DEAD_PORT = s.getsockname()[1]
s.close()                                  # nothing listens here any more

logs_service.install()
HANDLERS = {}
fake_app = types.SimpleNamespace(
    exception_handler=lambda kind: (lambda f: HANDLERS.setdefault(kind, f)))
logs_routes.install_error_handlers(fake_app)
REQ_APP = types.SimpleNamespace(state=types.SimpleNamespace(
    system=types.SimpleNamespace(slug="demo"), systems_dir=tempfile.mkdtemp()))

PORT = {"value": UP_PORT}
models_routes.detector_control_service.stream_port = lambda d, s: PORT["value"]


def get_preview(rid):
    """The route, the HTTPException handler and the access log's emit, in the
    order FastAPI runs them for one request."""
    req = types.SimpleNamespace(
        app=REQ_APP, method="GET",
        url=types.SimpleNamespace(path="/api/detector/preview"),
        state=types.SimpleNamespace(request_id=rid, user="ada", session_sid=""))
    try:
        models_routes.detector_preview(req, camera="", fps=0, quality=0, view="")
    except HTTPException as exc:
        out = asyncio.run(HANDLERS[HTTPException](req, exc))
        logs_service.log_request("GET", "/api/detector/preview", out.status_code,
                                 5, request_id=rid, user="ada")
        return out
    raise AssertionError("the route did not refuse")


def records(rid):
    return [r for r in logs_service.query(limit=2000) if r.get("request_id") == rid]


for label, port, needle in (("app answers 401", UP_PORT, "HTTP 401"),
                            ("app not listening", DEAD_PORT, "cannot reach")):
    print("== " + label)
    PORT["value"] = port
    rid = "rid" + str(port)
    r = get_preview(rid)
    body = r.content
    check("answers 5xx", r.status_code >= 500, r.status_code)
    check("error is the AI-5003 sentence", body.get("error") == SENTENCE, body)
    check("...with no HTTP code or port in it",
          str(port) not in body.get("error", "") and "401" not in body.get("error", ""))
    check("technical text is in details", needle in (body.get("details") or "")
          and str(port) in body.get("details", ""), body)
    rows = records(rid)
    check("one record", len(rows) == 1, rows)
    row = rows[0] if rows else {}
    check("...at WARNING", row.get("level") == "WARNING", row.get("level"))
    check("...with no traceback", "traceback" not in row, row.get("traceback", "")[:200])
    check("...carrying the detail", needle in (row.get("detail") or ""), row.get("detail"))

upstream.shutdown()
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
    raise SystemExit(1)
print("all good")
