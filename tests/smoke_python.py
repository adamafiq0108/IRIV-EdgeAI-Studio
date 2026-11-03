# Python node + Catch node: both gates hold, code reaches the box, the
# runtime runs it, and a crash is contained and catchable.
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
SAND = tempfile.mkdtemp()
os.environ["CYTRON_STORE"] = SAND

from core import config as config_mod                          # noqa: E402
from core.services import workflow as wf                       # noqa: E402

CODE = "def evaluate(ctx):\n    return len(ctx['detections']) * 2\n"

# A Python node reads ctx["detections"], so like every watching rule it stays
# switched off until a Detection Stream reaches it.
g = wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": -1, "y": 0},
     "data": {}},
    {"id": "py", "type": "analytics.python", "position": {"x": 0, "y": 0},
     "data": {"fnId": "mycode", "name": "My code", "code": CODE}},
    {"id": "cat", "type": "logic.catch", "position": {"x": 1, "y": 0},
     "data": {"severity": "crit", "message": "code broke"}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 2, "y": 0},
     "data": {}},
], "edges": [
    {"id": "e0", "source": "s", "target": "py"},
    {"id": "e1", "source": "py", "target": "cat"},
    {"id": "e2", "source": "cat", "target": "snap"},
]})

c = wf.compile_graph(g, [])
print("errors:", c["errors"])
assert not c["errors"], c["errors"]
fns = {f["id"]: f for f in c["functions"]}
print("functions:", {k: v["params"] for k, v in fns.items()})
assert fns["mycode"]["params"]["handler"] == "wf_mycode"
assert fns["catch_mycode"]["params"]["expr"] == "fn_mycode_error > 0"
assert fns["catch_mycode"]["trigger"]["severity"] == "crit"
assert c["outputs"][0]["function"] == "catch_mycode", c["outputs"]
print("== Python node compiles to a handler; Catch watches its error flag")

# --- syntax is checked at compile, not at 3am on a board -------------------
bad = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": -1, "y": 0}, "data": {}},
    {"id": "py", "type": "analytics.python", "position": {"x": 0, "y": 0},
     "data": {"fnId": "oops", "name": "Oops", "code": "def evaluate(ctx)\n  return 1"}},
], "edges": [{"id": "e0", "source": "s", "target": "py"}]}), [])
assert bad["errors"] and "line" in bad["errors"][0]["message"], bad["errors"]
print("== a syntax error is caught at save:", bad["errors"][0]["message"])

# --- the box flag decides whether the code is ever written -----------------
cfg = os.path.join(SAND, "config")
os.makedirs(cfg, exist_ok=True)
systems_dir = os.path.join(SAND, "systems")
pkg = os.path.join(str(config_mod.generated_systems_dir()), "sys1")
os.makedirs(os.path.join(pkg, "app"), exist_ok=True)
open(os.path.join(pkg, "system.py"), "w").close()

assert wf.python_enabled(cfg) is False, "the flag defaults to ON -- it must not"
written = wf.write_python_code(systems_dir, cfg, "sys1", g)
assert written == [], written
assert not os.path.isdir(os.path.join(pkg, "app", "wf_code")), \
    "code was written to a box that never opted in"
print("== flag OFF: nothing is written")

with open(os.path.join(cfg, "workflow.toml"), "w", encoding="utf-8") as f:
    f.write("[python]\nenabled = true\n")
config_mod.load.cache_clear()
assert wf.python_enabled(cfg) is True
written = wf.write_python_code(systems_dir, cfg, "sys1", g)
assert written == ["wf_mycode"], written
path = os.path.join(pkg, "app", "wf_code", "wf_mycode.py")
assert os.path.isfile(path)
print("== flag ON: code lands at app/wf_code/wf_mycode.py")

# deleting the node removes its file -- no orphan code left running
wf.write_python_code(systems_dir, cfg, "sys1", {"nodes": [], "edges": []})
assert not os.path.exists(path), "deleted node left its code behind"
print("== deleting the node removes its code")

# --- the runtime actually runs it ------------------------------------------
shutil.copytree(os.path.join(ROOT, "systems", "demo", "app"),
                os.path.join(pkg, "app"), dirs_exist_ok=True)
wf.write_python_code(systems_dir, cfg, "sys1", g)
sys.path.insert(0, pkg)
from app import extensions as ext                              # noqa: E402
from app import functions as appfx                             # noqa: E402

out = ext.run_function("wf_mycode", None,
                       {"detections": [{"label": "person"}, {"label": "car"}]})
print("evaluate() ->", out)
assert out == {"value": 4.0}, out
print("== a bare number return becomes the node's value")

# a crash is contained, benched, and shows up as the error flag Catch watches
crash = os.path.join(pkg, "app", "wf_code", "wf_boom.py")
with open(crash, "w", encoding="utf-8") as f:
    f.write("def evaluate(ctx):\n    raise RuntimeError('boom')\n")
fn = appfx.Function({"id": "boom", "name": "Boom", "type": "custom",
                     "enabled": True, "params": {"handler": "wf_boom"}})
engine = appfx.FunctionEngine([fn])
engine.update({"crossing_events": []}, [], {}, {}, 640, 480, zones=[])
stats = engine._stats
print("after a crash ->", {k: v for k, v in stats.items() if k.startswith("fn_boom")})
assert stats.get("fn_boom_error") == 1, stats
print("== a crash publishes fn_boom_error = 1, which is what Catch fires on")
print("ALL OK")
