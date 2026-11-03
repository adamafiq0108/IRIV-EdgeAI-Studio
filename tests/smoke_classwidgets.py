# The two dashboard controls of 2026-10-05, and the Class Filter naming rule.
#
# Tok Aba: an "AI model classes" card -- ticks of the model's classes, the
# SAME setting the AI Models page's Detection settings choose, both ways --
# and a "Class filter" card that pauses a NAMED Class Filter, so the rules
# after it stop the way detection-fed rules stop with the AI off. Several
# Class Filters on one workflow need a name each.
#
# Pinned:
#   1. two-way class sync through the real routes: the Models page's save is
#      what the card reads, and the card's save (the same route, the body the
#      card sends) is what the Models page reads; per model on a two-model box
#   2. the naming rule: 2+ unnamed refused at the editor's save in words, one
#      alone is fine, duplicates refused; the compiler itself never refuses
#      (a graph that compiled yesterday compiles today), and graphs nobody
#      named -- loaded, templated, mirrored -- are named from their classes
#   3. the compile lists, per NAMED filter, what it holds back; an unnamed
#      single one is not listed
#   4. the app, for real: every filter runs by default; unticking one stops
#      the rules after it with NO value published (no fake 0) while the
#      other branch keeps counting; ticking resumes; a paused filter is still
#      paused after a restart; its outputs are dropped
#   5. the toggle lives in config.local.toml, which an update never touches
#   6. the cards: data, "paused" in words on the cards after a paused filter
#   7. the gate: workflow.edit for the Class filter card (the Inject's gate),
#      models.select for the classes card; a viewer gets the ticks read-only
#      and the route refuses them
import asyncio
import copy
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
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

from core import config                                       # noqa: E402
from core.routes import builder as builder_routes             # noqa: E402
from core.routes import class_filter as cf_routes             # noqa: E402
from core.routes import models as models_routes               # noqa: E402
from core.routes import workflow as wf_routes                 # noqa: E402
from core.services import auth                                # noqa: E402
from core.services import builder as builder_service          # noqa: E402
from core.services import class_filter as cf                  # noqa: E402
from core.services import detector_control as dc              # noqa: E402
from core.services import workflow as wf                      # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


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
                                   "labels": COCO}})

auth.set_admin_username("boss", CFG)
auth.create_user("ada", "passw0rd!", SLUG, CFG, admin=True)   # a site admin
auth.create_user("sam", "passw0rd!", SLUG, CFG)               # a viewer
if hasattr(auth._cached_local, "cache_clear"):
    auth._cached_local.cache_clear()


def req(user, body=None):
    async def _json():
        return body if body is not None else {}
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            config_dir=CFG, systems_dir=SYSTEMS,
            system=SimpleNamespace(slug=SLUG))),
        state=SimpleNamespace(user=user), json=_json)


def call(fn, *a):
    """A route the way FastAPI runs it: its answer, or (status, detail)."""
    try:
        out = fn(*a)
        return asyncio.run(out) if asyncio.iscoroutine(out) else out
    except HTTPException as e:
        return (e.status_code, e.detail)


def card(kind, user="boss"):
    """One control card exactly as /api/dashboard renders it: compute, then
    the route's own gate stamp."""
    w = builder_service.validate([{"id": f"{kind}_1", "kind": kind,
                                   "title": kind, "source": "stat"}])
    done = builder_service.compute(w, [], {}, (), CFG, SLUG,
                                   systems_dir=SYSTEMS)
    return builder_routes._may_press(req(user), done)[0]["data"]


def card_post(url):
    """The URL a card posts to, read off Widget.jsx."""
    return url in read("frontend", "src", "components", "widgets", "Widget.jsx")


# -- 1. two-way class sync ------------------------------------------------------
print("== the AI model classes card and the Models page are one setting")
check("the card validates with no key (it reads a setting, not a stat)",
      builder_service.validate([{"id": "c", "kind": "ai_classes"}])[0]["kind"]
      == "ai_classes")
d = card("ai_classes")
check("the card lists the model's classes, none kept = every class",
      d["models"][0]["labels"] == ["person", "chair", "car"]
      and d["models"][0]["classes"] == [], d)
r = call(models_routes.detector_set_params,
         req("boss", {"classes": ["person"], "confidence": 0.5,
                      "model": "yolov8n"}))
check("the Models page saves person", isinstance(r, dict) and r["classes"] == ["person"], r)
check("...and the card shows it on its next read",
      card("ai_classes")["models"][0]["classes"] == ["person"])
check("the card saves through the Models page's own route",
      card_post('"/api/detector/control/params"'))
r = call(models_routes.detector_set_params,
         req("boss", {"classes": ["person", "chair"]}))     # the card's body
got = call(models_routes.detector_get_params, req("boss"))
check("a save from the card is what the Models page reads",
      isinstance(got, dict) and got["classes"] == ["person", "chair"], got)
local = dc._read_toml(dc._local_path(PKG))
check("...in the one stored place (detector + its hailo table), not a copy",
      local["detector"]["classes"] == ["person", "chair"]
      and local["detector"]["hailo"]["classes"] == ["person", "chair"]
      and "classes" not in json.dumps({k: v for k, v in local.items()
                                       if k != "detector"}), local)
r = call(models_routes.detector_set_params, req("boss", {"classes": []}))
check("ticking none is every class, as on the Models page",
      card("ai_classes")["models"][0]["classes"] == [])

# Two models: each entry its own classes, both ways.
PCB = os.path.join(PKG, "data", "pcb.txt").replace(os.sep, "/")
with open(PCB, "w", encoding="utf-8") as f:
    f.write("pcb\nsolder\n")
local = dc._read_toml(dc._local_path(PKG))
local["detector"]["models"] = [{"model": "yolov8n", "labels": COCO},
                               {"model": "pcbnet", "labels": PCB}]
dc._write_local(PKG, local)
d = card("ai_classes")
check("two models: the card offers each with its own classes",
      [(m["model"], m["labels"]) for m in d["models"]]
      == [("yolov8n", ["person", "chair", "car"]), ("pcbnet", ["pcb", "solder"])], d)
call(models_routes.detector_set_params,
     req("boss", {"classes": ["pcb"], "model": "pcbnet"}))
d = {m["model"]: m["classes"] for m in card("ai_classes")["models"]}
check("...a save for one model reaches that model only",
      d == {"yolov8n": [], "pcbnet": ["pcb"]}, d)
got = call(models_routes.detector_get_params, req("boss"))
check("...and the Models page reads it on that model's entry",
      {e["model"]: e.get("classes") for e in got["models"]}["pcbnet"] == ["pcb"], got)
local = dc._read_toml(dc._local_path(PKG))
local["detector"].pop("models")
dc._write_local(PKG, local)

# The app applies a class change the way it applies the Models page's: the
# detector config differs, so the running app rebuilds the detector in place.
run_src = read("systems", "demo", "app", "run.py")
check("the app hot-reloads a class change (detector rebuilt, no restart)",
      "if _det_core(new_det_cfg) != _det_core(det_cfg):" in run_src)

# -- 2. the naming rule --------------------------------------------------------
print("== several Class Filters need a name each")


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def graph(name1="", name2="", second=True, wire_out=False):
    nodes = [node("m1", "source.detection"),
             node("f1", "filter.class", classes=["person"], name=name1),
             node("c1", "analytics.frame_count", fnId="people_now",
                  name="People now")]
    edges = [{"id": "e1", "source": "m1", "target": "f1"},
             {"id": "e3", "source": "f1", "target": "c1"}]
    if second:
        nodes += [node("f2", "filter.class", classes=["chair"], name=name2),
                  node("c2", "analytics.frame_count", fnId="chairs_now",
                       name="Chairs now")]
        edges += [{"id": "e2", "source": "m1", "target": "f2"},
                  {"id": "e4", "source": "f2", "target": "c2"}]
    if wire_out:
        # people_now -> a Condition -> MQTT: the output behind filter f1.
        nodes += [node("k1", "logic.when", metric="count", mode="above", value=0),
                  node("o1", "output.mqtt", host="127.0.0.1", port=1883,
                       topic="t", payload="1")]
        edges += [{"id": "e5", "source": "c1", "target": "k1"},
                  {"id": "e6", "source": "k1", "target": "o1"}]
    return wf.clean_graph({"nodes": nodes, "edges": edges})


MSG = "Name each Class Filter: this workflow has 2, and the dashboard lists them by name"
c = wf.compile_graph(graph(), [], ["person", "chair"], ["person", "chair"])
check("the COMPILER never refuses unnamed filters (yesterday's graph runs)",
      not c["errors"], c["errors"])
check("...and lists no unnamed filter for the card", "class_filters" not in c, c.get("class_filters"))
errs = cf.naming_errors(graph())
check("two unnamed: each is refused in plain words",
      [e["node"] for e in errs if e["node"]] == ["f1", "f2"]
      and all(e["message"] == MSG for e in errs), errs)
check("...with a general line for the save's message",
      any(not e["node"] and e["message"] == MSG for e in errs), errs)
check("one named, one not: the unnamed one is refused",
      [e["node"] for e in cf.naming_errors(graph("people")) if e["node"]] == ["f2"])
check("exactly one Class Filter: its name is optional",
      cf.naming_errors(graph(second=False)) == [])
dup = cf.naming_errors(graph("People", " people "))
check("names are unique (case and spaces ignored)",
      [e["node"] for e in dup if e["node"]] == ["f2"]
      and 'already called "people"' in dup[0]["message"], dup)
check("two named, different: fine", cf.naming_errors(graph("people", "chair")) == [])
off = graph()
off["nodes"][3]["data"]["disabled"] = True
check("a switched-off filter does not count", cf.naming_errors(off) == [])
check("the panel says the same sentence",
      "Name each Class Filter: this workflow has ${live.length}, `" in read(
          "frontend", "src", "workflow", "registry.js")
      and "and the dashboard lists them by name" in read(
          "frontend", "src", "workflow", "registry.js"))

# Through the editor's own save.
real_reconcile = None
from core.services import app_supervisor                     # noqa: E402
with mock.patch.object(app_supervisor, "reconcile_soon", lambda *a, **k: None):
    g = graph(wire_out=False)
    r = call(wf_routes.save_workflow, req("boss", {"graph": g}))
    check("the editor's save of two unnamed is NOT applied",
          isinstance(r, dict) and r["ok"] is False and r["applied"] is False, r)
    check("...says why on each node and as the save's message",
          {e["node"] for e in r["errors"]} >= {"f1", "f2", ""}
          and all(e["message"] == MSG for e in r["errors"]), r["errors"])
    stored = wf.load_store(SLUG)
    check("...the canvas is still kept (a save never loses work)",
          {n["id"] for n in stored["workflows"][0]["graph"]["nodes"]} >= {"f1", "f2"})
    r = call(wf_routes.save_workflow,
             req("boss", {"graph": graph(second=False)}))
    check("one Class Filter, unnamed: the save applies", r["applied"] is True, r["errors"])
    r = call(wf_routes.save_workflow,
             req("boss", {"graph": graph("people", "chair", wire_out=True)}))
    check("two named: the save applies", r["applied"] is True, r["errors"])

# Graphs nobody named on this canvas are named from their classes.
legacy = graph()
with open(wf.path_for("legacy_sys"), "w", encoding="utf-8") as f:
    json.dump({"version": 3, "workflows": [{"id": wf.MAIN_ID, "name": "Main",
                                             "enabled": True, "graph": legacy}]}, f)
loaded = wf.load_store("legacy_sys")["workflows"][0]["graph"]
names = {n["id"]: n["data"].get("name") for n in loaded["nodes"]
         if n["type"] == "filter.class"}
check("a workflow saved before the rule is named from its classes on load",
      names == {"f1": "person", "f2": "chair"}, names)
twin = graph()
twin["nodes"][3]["data"]["classes"] = ["person"]
cf.name_filters(twin)
check("...two keeping the same class are numbered",
      [n["data"]["name"] for n in twin["nodes"] if n["type"] == "filter.class"]
      == ["person", "person 2"])
mine = graph("", "")
cf.name_filters(mine, only={"f2"})
check("the Zones-page mirror names only the filters it drew",
      [n["data"].get("name") for n in mine["nodes"] if n["type"] == "filter.class"]
      == ["", "chair"])
check("a template landing names its filters",
      "class_filter_service.name_filters(graph)" in read("core", "services", "templates.py"))

# The two older store shapes are named on load too (Yaya's QA, 2026-10-05:
# only the v3 branch was). Unnamed, the editor's first save was refused for
# a pair the operator never drew, and after an OTA ensure_compiled's own
# recompile answered "failed", so that system kept its pre-update [logic].


def logic_version():
    return int((dc._read_toml(dc._local_path(PKG)).get("logic") or {})
               .get("version") or 0)


with mock.patch.object(app_supervisor, "reconcile_soon", lambda *a, **k: None):
    for shape, old in (("v1", {"nodes": graph()["nodes"], "edges": graph()["edges"]}),
                       ("v2", {"workflows": [{"id": "w1", "name": "Line",
                                              "graph": graph()}]})):
        with open(wf.path_for(SLUG), "w", encoding="utf-8") as f:
            json.dump(old, f)
        loaded = wf.load_store(SLUG)["workflows"][0]["graph"]
        names = {n["id"]: n["data"].get("name") for n in loaded["nodes"]
                 if n["type"] == "filter.class"}
        check(f"a {shape} store is named from its classes on load",
              names == {"f1": "person", "f2": "chair"}, names)
        local = dc._read_toml(dc._local_path(PKG))
        local.pop("logic", None)                     # as a box fresh from an OTA
        dc._write_local(PKG, local)
        got = app_supervisor.ensure_compiled(SYSTEMS, SLUG, CFG)
        check(f"...the recompile at start applies a {shape} store",
              got == "applied" and logic_version() == wf.LOGIC_VERSION,
              (got, logic_version()))
        with open(wf.path_for(SLUG), "w", encoding="utf-8") as f:
            json.dump(old, f)
        r = call(wf_routes.save_workflow,
                 req("boss", {"graph": wf.load_store(SLUG)["workflows"][0]["graph"]}))
        check(f"...and the editor's first save of a {shape} store applies",
              isinstance(r, dict) and r["applied"] is True, r)
    r = call(wf_routes.save_workflow,
             req("boss", {"graph": graph("people", "chair", wire_out=True)}))
    check("(the named workflow is back for the sections below)", r["applied"] is True)

# -- 3. what a paused filter holds back -----------------------------------------
store = {"version": 3, "workflows": [{"id": "main", "name": "Main", "enabled": True,
                                      "graph": graph("people", "chair", wire_out=True)}]}
c = wf.compile_store(store, [], ["person", "chair"], ["person", "chair"])
check("the named workflow compiles", not c["errors"], c["errors"])
held = {e["name"]: e for e in c["class_filters"]}
check("people holds back the rule after it, and what reads it",
      set(held["people"]["functions"]) >= {"people_now"}
      and "chairs_now" not in held["people"]["functions"], held)
check("...and the output after it",
      held["people"]["outputs"] == [o["id"] for o in c["outputs"]], (held, c["outputs"]))
check("chair holds back only its own branch",
      held["chair"]["functions"] == ["chairs_now"] and held["chair"]["outputs"] == [], held)
paused_fns = held["people"]["functions"]
fn_by_id = {f["id"]: f for f in c["functions"]}
reader = {"id": "share", "type": "expr", "name": "share",
          "params": {"expr": "fn_people_now_count * 2"}}
d2 = cf.decide([{"node": "f1", "name": "people", "base": ["people_now"],
                 "out_base": []}], c["functions"] + [reader], [])
check("a Formula reading a held-back count is held back too (unwired)",
      "share" in d2[0]["functions"], d2)

# -- 4. the app, for real ------------------------------------------------------
print("== the app honours the toggle without a restart")
from app import detector as detector_mod                     # noqa: E402
from app import run as apprun                                # noqa: E402

RealCamera = apprun.camera_mod.Camera


class FakeCamera(RealCamera):
    def __init__(self, config):
        super().__init__(dict(config, source="synthetic"))


class FakeChip(detector_mod.Detector):
    name = "fakechip"

    def detect(self, frame):
        return [{"label": "person", "confidence": 0.9, "box": [20, 20, 120, 200]},
                {"label": "chair", "confidence": 0.9, "box": [150, 40, 220, 200]}]

    def close(self):
        pass


apprun.camera_mod.Camera = FakeCamera
detector_mod.REGISTRY["hailo"] = FakeChip
apprun._CONFIG_POLL_S = 0.02

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()
APP = {
    "ai": {"on": True},
    "app": {"stream_port": PORT, "stream_bind": "127.0.0.1"},
    "cameras": [{"id": "cam1", "source": "rtsp://10.0.0.9/a", "enabled": True,
                 "fps": 10, "width": 320, "height": 240}],
    "detector": {"kind": "hailo", "model": "yolov8s"},
    "capture": {"snap": False, "record": False},
    "storage": {"keep_snapshots": 5},
    # No outputs in the loop (an MQTT one would dial a broker); dropping a
    # paused filter's outputs is pinned on class_pause below.
    "zones": [], "outputs": [],
    "functions": copy.deepcopy(c["functions"]),
    "class_filters": {"paused": [], "nodes": copy.deepcopy(c["class_filters"])},
}


class Run:
    def __init__(self):
        self.data = tempfile.mkdtemp()
        self.mt = [1.0]

    def set_paused(self, ids):
        APP["class_filters"]["paused"] = list(ids)
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
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(0.05)
    return False


PEOPLE, CHAIRS = "fn_people_now_count", "fn_chairs_now_count"


def drive1(r):
    check("default: every Class Filter runs (both branches count)",
          wait(lambda: r.counts().get(PEOPLE) == 1 and r.counts().get(CHAIRS) == 1),
          r.counts())
    r.set_paused(["f1"])
    check("unticked people: its rule publishes nothing -- no fake 0",
          wait(lambda: PEOPLE not in r.counts()), r.counts())
    time.sleep(0.4)
    c_ = r.counts()
    check("...still nothing a moment later, while chair keeps counting",
          PEOPLE not in c_ and c_.get(CHAIRS) == 1, c_)
    r.set_paused([])
    check("ticked again: people counts again, no restart",
          wait(lambda: r.counts().get(PEOPLE) == 1), r.counts())


Run().go(drive1)

APP["class_filters"]["paused"] = ["f1"]


def drive2(r):
    check("after a restart the pause still holds (people never published)",
          wait(lambda: r.counts().get(CHAIRS) == 1) and PEOPLE not in r.counts(),
          r.counts())


Run().go(drive2)

paused_cfg = apprun.class_pause({**APP, "outputs": copy.deepcopy(c["outputs"])})
check("a paused filter's outputs are dropped, the rest kept",
      paused_cfg["outputs"] == [] and paused_cfg["_paused"] == sorted(paused_fns),
      paused_cfg)
check("nothing paused: the config is untouched",
      apprun.class_pause({**APP, "class_filters": {"paused": [], "nodes": []}})
      .get("_paused") is None)
check("the AI-off rules and the paused ones sit out together",
      apprun.inactive_ids({"_ai_inactive": ["a"], "_paused": ["b", "a"]}) == ["a", "b"])

# -- 5. the stored toggle --------------------------------------------------------
print("== the toggle is a setting of the system")
cf.apply(SYSTEMS, SLUG, c)
t = cf.table(SYSTEMS, SLUG)
check("the compile wrote the named filters to config.local.toml",
      [n["name"] for n in t["nodes"]] == ["people", "chair"] and t["paused"] == [], t)
check("default: nothing paused", cf.table(SYSTEMS, SLUG)["paused"] == [])
cf.set_running(SYSTEMS, SLUG, "f2", False)
dc._toml_cache.pop(dc._local_path(PKG), None)
check("a pause is written to config.local.toml",
      dc._read_toml(dc._local_path(PKG))["class_filters"]["paused"] == ["f2"])
cf.apply(SYSTEMS, SLUG, c)
check("...and a recompile keeps it", cf.table(SYSTEMS, SLUG)["paused"] == ["f2"])
cf.apply(SYSTEMS, SLUG, {"class_filters": [e for e in c["class_filters"]
                                           if e["node"] == "f1"]})
check("a filter gone from the workflow drops out of the paused list",
      cf.table(SYSTEMS, SLUG)["paused"] == [])
cf.apply(SYSTEMS, SLUG, c)
try:
    cf.set_running(SYSTEMS, SLUG, "nope", False)
    check("an unknown filter is refused", False)
except ValueError as exc:
    check("an unknown filter is refused, in words", "reload the dashboard" in str(exc))
upd = read("scripts", "apply_update.py")
items = re.search(r"APP_CODE_ITEMS\s*=\s*\(([^)]*)\)", upd)
check("an update refreshes app code only, never config.local.toml",
      items and "config" not in items.group(1), items and items.group(1))

# -- 6. the cards ----------------------------------------------------------------
print("== the dashboard cards")
d = card("class_filters")
check("the Class filter card lists the named filters, all running",
      d["filters"] == [{"node": "f1", "name": "people", "running": True},
                       {"node": "f2", "name": "chair", "running": True}], d)
check("...and posts to the class-filter route",
      card_post('"/api/workflow/class-filter"'))
cf.set_running(SYSTEMS, SLUG, "f1", False)
check("unticked shows as not running",
      card("class_filters")["filters"][0]["running"] is False)
dc._write_local(PKG, {**dc._read_toml(dc._local_path(PKG)),
                      "functions": c["functions"]})
tile = builder_service.validate([{"id": "t1", "kind": "tile", "key": PEOPLE,
                                  "title": "People"}])
out = builder_service.compute(tile, [], {CHAIRS: 1}, (), CFG, SLUG,
                              systems_dir=SYSTEMS)[0]["data"]
check("a card after the paused filter says paused, not 0",
      out.get("paused") is True and out.get("paused_filter") == "people"
      and out.get("value") in (None, 0) and not out.get("paused_badge"), out)
other = builder_service.validate([{"id": "t2", "kind": "tile", "key": CHAIRS,
                                   "title": "Chairs"}])
out = builder_service.compute(other, [], {CHAIRS: 1}, (), CFG, SLUG,
                              systems_dir=SYSTEMS)[0]["data"]
check("...a card on the other branch is untouched",
      not out.get("paused") and not out.get("paused_badge"), out)
kept = builder_service.compute(tile, [], {PEOPLE: 4}, (), CFG, SLUG,
                               systems_dir=SYSTEMS)[0]["data"]
check("a kept tally still published is shown, with a paused badge",
      kept.get("paused_badge") is True and not kept.get("paused"), kept)
cf.set_running(SYSTEMS, SLUG, "f1", True)
out = builder_service.compute(tile, [], {PEOPLE: 1}, (), CFG, SLUG,
                              systems_dir=SYSTEMS)[0]["data"]
check("running again: the card reads its number", not out.get("paused"), out)

# -- 7. who may tick --------------------------------------------------------------
print("== the gate")
check("master may tick both", card("class_filters")["can_edit"] is True
      and card("ai_classes")["can_edit"] is True)
check("a viewer sees both read-only", card("class_filters", "sam")["can_edit"] is False
      and card("ai_classes", "sam")["can_edit"] is False)
r = call(cf_routes.class_filter_run, req("sam", {"node": "f1", "running": False}))
check("...and the route refuses a viewer", isinstance(r, tuple) and r[0] == 403, r)
r = call(models_routes.detector_set_params, req("sam", {"classes": ["car"]}))
check("...as does the classes route", isinstance(r, tuple) and r[0] == 403, r)
auth.update_user("ada", caps={SLUG: ["workflow.edit"]}, config_dir=CFG)
check("a site admin with workflow.edit may tick Class Filters",
      card("class_filters", "ada")["can_edit"] is True)
check("...but not the model's classes without models.select",
      card("ai_classes", "ada")["can_edit"] is False)
r = call(cf_routes.class_filter_run, req("ada", {"node": "f1", "running": False}))
check("...and the route lets them", isinstance(r, dict) and r["paused"] == ["f1"], r)
r = call(cf_routes.class_filter_run, req("ada", {"node": "f1", "running": "no"}))
check("a body that does not say run or pause is refused in words",
      isinstance(r, tuple) and "should run" in r[1], r)
check("the Inject's gate and the Class filter's are one table",
      '"button": "workflow.edit", "class_filters": "workflow.edit"' in read(
          "core", "services", "dashboard_controls.py"))

if bad:
    print(f"\n{bad} check(s) failed")
    sys.exit(1)
print("\nall class widget checks passed")
