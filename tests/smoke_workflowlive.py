# GET /api/workflow/live: the Workflow page's poll without the editor payload
# (perf audit item L2, 2026-10-02).
#
# Both the Workflow page (every 4 s) and the Overlay panel (every 5 s) polled
# GET /api/workflow, which carries the ~55 KB node registry and the graph and
# runs a full compile_store, dc.status three times, get_capture and the mail
# and Telegram reads -- for a handful of live numbers. Pinned here: what the
# live route returns (and does not), one dc.status per poll, the compile
# cached until the workflow or the config changes on disk, and both pages
# polling it through usePoll (which pauses on a hidden tab).
import io
import json
import os
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
bad = 0


def check(msg, passed, detail=""):
    global bad
    print(("ok   " if passed else "FAIL ") + msg
          + ("" if passed or not detail else "\n      " + str(detail)))
    if not passed:
        bad += 1


def read(*parts):
    with io.open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        put=lambda *a, **k: (lambda f: f), delete=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), include_router=lambda *a, **k: None)
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    fa.Query = lambda *a, **k: None
    fa.__path__ = []
    sys.modules["fastapi"] = fa
    resp = types.ModuleType("fastapi.responses")
    resp.JSONResponse = resp.RedirectResponse = resp.Response = \
        resp.StreamingResponse = resp.FileResponse = object
    sys.modules["fastapi.responses"] = resp

from core.routes import workflow_live as live                      # noqa: E402
from core.services import workflow as workflow_service            # noqa: E402

# -- the state, from one dc.status --------------------------------------------
check("detection running", live.live_state({"running": True, "ai_ok": True})
      == (True, "detection", {"on": True, "ok": True, "reason": ""}))
check("logic mode (AI off, the workflow running)",
      live.live_state({"logic_running": True, "ai_error": "AI is off"})
      == (True, "logic", {"on": False, "ok": False, "reason": "AI is off"}))
check("nothing running", live.live_state({}) == (False, "off",
                                                 {"on": False, "ok": False, "reason": ""}))

# -- the route ------------------------------------------------------------------
systems = tempfile.mkdtemp()
slug = "lv"
pkg = os.path.join(systems, slug)
os.makedirs(pkg)
open(os.path.join(pkg, "config.toml"), "w").close()
wf_path = workflow_service.path_for(slug)
os.makedirs(os.path.dirname(wf_path), exist_ok=True)
with open(wf_path, "w") as f:
    json.dump({"version": 3, "workflows": [{"id": "main", "graph": {"nodes": [], "edges": []}}]}, f)

calls = {"status": 0, "compile": 0}


def fake_status(d, s):
    calls["status"] += 1
    return {"running": True, "ai_ok": True}


def fake_compile(*a, **k):
    calls["compile"] += 1
    return {"waits_for_detection": ["n1"], "errors": [], "warnings": []}


live.dc.status = fake_status
live.workflow_service.compile_store = fake_compile
live._system_context = lambda request: (systems, slug)
live._live_stats = lambda request: {"fn_a_value": 3, "zone_b_count": 1, "other": 9}
live._variables = lambda request, d, s, stats: {"stat_keys": sorted(stats)}
live._get_store = lambda d, s: ({"workflows": [{"graph": {}}]}, False)
live._classes = lambda d, s: ([], [])
live._zones = lambda d, s: []
live._camera_ids = lambda d, s: []
live.dc.get_capture = lambda d, s: {}
req = types.SimpleNamespace(state=types.SimpleNamespace(user="ada"),
                            app=types.SimpleNamespace(state=types.SimpleNamespace()))

out = live.workflow_live(req)
check("it answers the live half",
      set(out) == {"ok", "values", "app_running", "runtime", "ai",
                   "waits_for_detection", "variables", "workflow_stamp",
                   # the ON/OFF points (smoke_statepoints): which Class
                   # Filters are paused, which nodes run after a point
                   "filters_paused", "runs_when"}, sorted(out))
check("...and none of the editor payload",
      not {"registry", "graph", "workflow", "mail_accounts", "telegram_bots",
           "functions"} & set(out))
check("the values are the canvas's live stats only",
      out["values"] == {"fn_a_value": 3, "zone_b_count": 1})
check("one dc.status per poll (the full route made three)", calls["status"] == 1)
for _ in range(5):
    live.workflow_live(req)
check("the compile runs once while nothing changes on disk",
      calls["compile"] == 1, calls)
stamp = out["workflow_stamp"]
time.sleep(0.02)
with open(wf_path, "w") as f:
    json.dump({"version": 3, "workflows": [{"id": "main", "graph": {"nodes": [1]}}]}, f)
out2 = live.workflow_live(req)
check("a saved workflow recompiles, and moves the stamp",
      calls["compile"] == 2 and out2["workflow_stamp"] != stamp)
time.sleep(0.02)
with open(os.path.join(pkg, "config.local.toml"), "w") as f:
    f.write("[app]\ntitle = 'x'\n")
live.workflow_live(req)
check("a config change (zones, cameras) recompiles too", calls["compile"] == 3)

# -- the pages --------------------------------------------------------------------
wf = read("frontend", "src", "pages", "Workflow.jsx")
check("the Workflow page polls the live route through usePoll",
      'usePoll("/api/workflow/live", 4000)' in wf
      and 'api.get("/api/workflow").then((d) => {\n        setValues' not in wf)
ov = read("frontend", "src", "components", "OverlayPanel.jsx")
check("the Overlay panel loads the rules once and polls the live route",
      'useApi("/api/workflow")' in ov and 'usePoll("/api/workflow/live", 5000)' in ov
      and 'usePoll("/api/workflow", 5000)' not in ov)
check("...and reloads the rules when the workflow is saved elsewhere",
      "live?.workflow_stamp" in ov and "load().catch" in ov)
check("the master mounts it", "app.include_router(workflow_live_router)" in read("master_server.py"))
reg = len(json.dumps(workflow_service.registry_public()))
print(f"     (the registry alone, no longer polled: {reg // 1024} KB per poll)")

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
