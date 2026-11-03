# AI model classes are added or removed only while AI detection is stopped.
#
# Tok Aba, 2026-10-07. core/services/class_lock.py decides; the routes that
# add or remove classes (POST /api/detector/control/params -- the AI Models
# page's Detection settings AND the dashboard's AI model classes card -- and a
# running model's manifest save, POST /api/models) answer 409 with its words
# while detection is anything but fully stopped.
#
# Pinned:
#   1. running: a class change is a 409 with the message and writes nothing;
#      confidence alone, and the same classes re-sent, still save
#   2. stopping (the switch says off, the app still holds the detector):
#      locked too; fully stopped: the change saves
#   3. the pages' flags: classes_locked on the params read, locked on the
#      dashboard card, both following the state with no other trigger
#   4. a manifest save that changes a RUNNING model's classes is refused
#      before anything is written; a model not in use is not
#   5. the Class Filter is NOT locked: its toggle saves while detection runs
import asyncio
import io
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# The dev box runs the suite without FastAPI; the routes only need its names.
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
    resp.JSONResponse = resp.RedirectResponse = resp.FileResponse = \
        resp.StreamingResponse = object
    resp.Response = lambda content=None, media_type=None, **k: content
    sys.modules["fastapi.responses"] = resp

from fastapi import HTTPException                            # noqa: E402

from core.routes import class_filter as cf_routes             # noqa: E402
from core.routes import models as models_routes               # noqa: E402
from core.services import auth                                # noqa: E402
from core.services import class_lock                          # noqa: E402
from core.services import dashboard_controls                  # noqa: E402
from core.services import detector_control as dc              # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


CFG = tempfile.mkdtemp()
SYSTEMS = tempfile.mkdtemp()
SLUG = "line_a"
PKG = os.path.join(SYSTEMS, SLUG)
os.makedirs(os.path.join(PKG, "data"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write('[detector]\nkind = "hailo"\nconfidence = 0.45\n')
COCO = os.path.join(PKG, "data", "coco.txt").replace(os.sep, "/")
with open(COCO, "w", encoding="utf-8") as f:
    f.write("person\nchair\ncar\n")
dc._write_local(PKG, {"detector": {"kind": "hailo", "model": "yolov8n",
                                   "labels": COCO, "classes": ["person"],
                                   "hailo": {"classes": ["person"]}},
                      "class_filters": {"nodes": [{"node": "f1",
                                                   "name": "people"}],
                                        "paused": []}})
auth.set_admin_username("boss", CFG)
if hasattr(auth._cached_local, "cache_clear"):
    auth._cached_local.cache_clear()

STATE = SimpleNamespace(config_dir=CFG, systems_dir=SYSTEMS,
                        system=SimpleNamespace(slug=SLUG))


def req(body=None):
    async def _json():
        return body if body is not None else {}
    return SimpleNamespace(app=SimpleNamespace(state=STATE),
                           state=SimpleNamespace(user="boss"), json=_json)


def call(fn, *a):
    try:
        out = fn(*a)
        return asyncio.run(out) if asyncio.iscoroutine(out) else out
    except HTTPException as e:
        return (e.status_code, e.detail)


REAL_STATUS = dc.status


def as_if(running=False, app=False, ai_on=None):
    """Detection's state as status() and the app's state file would say it."""
    def status(systems_dir, slug):
        return dict(REAL_STATUS(systems_dir, slug), running=running,
                    app_running=app)
    return (mock.patch.object(dc, "status", status),
            mock.patch.object(dc, "app_state",
                              lambda s, g: {} if ai_on is None else {"ai_on": ai_on}))


def kept():
    return class_lock.current_classes(SYSTEMS, SLUG)


def card():
    return dashboard_controls.widget_data({"kind": "ai_classes"}, SYSTEMS, SLUG)


# -- 1. running -----------------------------------------------------------------
print("== running: classes wait, confidence does not")
a, b = as_if(running=True, app=True, ai_on=True)
with a, b:
    r = call(models_routes.detector_set_params,
             req({"classes": ["person", "car"], "model": "yolov8n"}))
    check("adding a class while running is a 409 with the message",
          r == (409, class_lock.MESSAGE), r)
    check("...and nothing was written", kept() == ["person"], kept())
    r = call(models_routes.detector_set_params, req({"classes": []}))
    check("removing (to every class) is refused the same way",
          r == (409, class_lock.MESSAGE) and kept() == ["person"], r)
    r = call(models_routes.detector_set_params, req({"confidence": 0.6}))
    check("confidence alone still saves",
          isinstance(r, dict) and r["confidence"] == 0.6, r)
    r = call(models_routes.detector_set_params,
             req({"classes": ["person"], "confidence": 0.55,
                  "model": "yolov8n"}))
    check("the same classes re-sent with a new confidence still save "
          "(the Models page always sends both)",
          isinstance(r, dict) and r["confidence"] == 0.55, r)
    got = call(models_routes.detector_get_params, req())
    check("the params read says classes_locked, with the words",
          got.get("classes_locked") is True
          and got.get("classes_locked_reason") == class_lock.MESSAGE, got)
    check("the dashboard card says locked", card().get("locked") is True, card())

    # -- 5. the Class Filter is not locked --------------------------------------
    print("== the Class Filter keeps working while detection runs")
    r = call(cf_routes.class_filter_run, req({"node": "f1", "running": False}))
    check("pausing a Class Filter saves while running",
          isinstance(r, dict) and r["paused"] == ["f1"], r)
    r = call(cf_routes.class_filter_run, req({"node": "f1", "running": True}))
    check("...and running it again", isinstance(r, dict) and r["paused"] == [], r)

    # -- 4. the manifest save is the same change by another door ----------------
    print("== a running model's manifest save")
    r = call(class_lock.require_stopped_manifest, STATE,
             {"manifest": {"name": "yolov8n", "classes": ["chair"]}})
    check("changing the RUNNING model's classes is a 409",
          r == (409, class_lock.MESSAGE), r)
    r = call(class_lock.require_stopped_manifest, STATE,
             {"manifest": {"name": "yolov8n", "classes": ["person"]}})
    check("...its other fields with the classes unchanged pass", r is None, r)
    r = call(class_lock.require_stopped_manifest, STATE,
             {"manifest": {"name": "other", "classes": ["chair"]}})
    check("a model not in use is free", r is None, r)

# -- 2. stopping, then stopped ---------------------------------------------------
print("== stopping is locked; fully stopped is free")
a, b = as_if(running=False, app=True, ai_on=True)
with a, b:
    r = call(models_routes.detector_set_params, req({"classes": ["car"]}))
    check("Stop pressed, app still holding the detector: 409",
          r == (409, class_lock.MESSAGE) and kept() == ["person"], r)
    check("...and the card still says locked", card().get("locked") is True)
a, b = as_if(running=False, app=True, ai_on=None)
with a, b, mock.patch.object(dc, "status",
                             lambda s, g: dict(REAL_STATUS(s, g),
                                               stopping=True)):
    check("a `stopping` flag on status() locks as well",
          class_lock.detection_busy(SYSTEMS, SLUG) is True)
a, b = as_if(running=False, app=True, ai_on=False)
with a, b:
    check("the app with the AI released (workflow only) is unlocked",
          card().get("locked") is False, card())
    r = call(models_routes.detector_set_params, req({"classes": ["car"]}))
    check("...and the class change saves",
          isinstance(r, dict) and r["classes"] == ["car"], r)
a, b = as_if(running=False, app=False)
with a, b:
    got = call(models_routes.detector_get_params, req())
    check("no app at all: classes_locked is false",
          got.get("classes_locked") is False, got)
    r = call(models_routes.detector_set_params, req({"classes": ["person"]}))
    check("...and the change saves",
          isinstance(r, dict) and r["classes"] == ["person"], r)

# -- 3. the pages ----------------------------------------------------------------


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


print("== the pages")
models_jsx = read("frontend", "src", "pages", "Models.jsx")
widget_jsx = read("frontend", "src", "components", "widgets", "Widget.jsx")
check("the AI Models page polls the params read that carries the lock",
      "useVisibleInterval(" in models_jsx
      and models_jsx.count('api.get("/api/detector/control/params")') >= 2)
check("its class ticks grey out on classes_locked, confidence does not",
      "data.classes_locked" in models_jsx
      and "disabled={!classEdit}" in models_jsx
      and 'type="range" min="0.05" max="0.95" step="0.05" value={conf}\n'
          '               disabled={!data.editable}' in models_jsx)
check("the dashboard card reads `locked` and says why",
      "data.locked" in widget_jsx and "locked_reason" in widget_jsx)
check("the Class filter card does not read the lock",
      "locked" not in widget_jsx[widget_jsx.index("function ClassFilterCard"):
                                 widget_jsx.index("function ClassFilterCard") + 4000])

print("\nFAILED: %d" % bad if bad else "\nall class-lock checks passed")
sys.exit(1 if bad else 0)
