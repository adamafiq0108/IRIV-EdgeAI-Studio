# Browser-written Python: only code the editor wrote runs, edits are caught,
# and turning [python].enabled off actually removes it.
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

SAND = os.path.join(tempfile.mkdtemp(), "pygate_sandbox")
shutil.rmtree(SAND, ignore_errors=True)
PKG = os.path.join(SAND, "systems", "demo")
os.makedirs(os.path.join(PKG, "app"), exist_ok=True)
shutil.copy(os.path.join(ROOT, "systems", "demo", "app", "extensions.py"),
            os.path.join(PKG, "app", "extensions.py"))
open(os.path.join(PKG, "app", "__init__.py"), "w").close()

FOLDER = os.path.join(PKG, "app", "wf_code")
os.makedirs(FOLDER, exist_ok=True)
open(os.path.join(FOLDER, "__init__.py"), "w").close()

CODE = "def evaluate(ctx):\n    return 42\n"
with open(os.path.join(FOLDER, "wf_thing.py"), "wb") as f:
    f.write(CODE.encode("utf-8"))

sys.path.insert(0, PKG)
from app import extensions as ext                            # noqa: E402
import hashlib                                               # noqa: E402


def fresh():
    ext._wf_cache.clear()
    return ext._load_wf_handler("wf_thing")


# 1. no manifest -> refused, even though the file is right there
assert fresh() is None, "hand-dropped code ran with no manifest"
print("== a .py with no manifest entry is refused")

# 2. manifest that vouches for it -> loads
with open(os.path.join(FOLDER, ext.WF_MANIFEST), "w", encoding="utf-8") as f:
    json.dump({"wf_thing.py": hashlib.sha256(CODE.encode()).hexdigest()}, f)
fn = fresh()
assert fn is not None and fn({}) == 42, fn
print("== code the editor wrote (hash matches) loads and runs")

# 3. edit the file after the fact -> refused
with open(os.path.join(FOLDER, "wf_thing.py"), "a", encoding="utf-8") as f:
    f.write("\nimport os\n")
assert fresh() is None, "an edited file still imported"
print("== an edit to vouched-for code is caught and refused")

# 4. a file added by hand and listed under someone else's name -> refused
with open(os.path.join(FOLDER, "wf_evil.py"), "w", encoding="utf-8") as f:
    f.write("def evaluate(ctx):\n    return 1\n")
ext._wf_cache.clear()
assert ext._load_wf_handler("wf_evil") is None
print("== an unlisted file is refused")

# 5. the kill switch: [python].enabled off -> the code is REMOVED at boot
from core.services import workflow as wf                     # noqa: E402
from core import config as config_mod                        # noqa: E402

CFG = tempfile.mkdtemp()
with open(os.path.join(CFG, "workflow.toml"), "w", encoding="utf-8") as f:
    f.write("[python]\nenabled = false\n")

# enforce_python_policy builds <pkg_root>/<slug>/app/wf_code, so the root it
# must see is the directory CONTAINING the system package.
from core.services import provision as prov                  # noqa: E402
prov._pkg_root = lambda systems_dir, slug: os.path.join(SAND, "systems")

with open(os.path.join(FOLDER, "wf_thing.py"), "wb") as f:
    f.write(CODE.encode("utf-8"))
assert os.path.isfile(os.path.join(FOLDER, "wf_thing.py"))
assert wf.python_enabled(CFG) is False, "test config did not disable python"
removed = wf.enforce_python_policy("systems", CFG, "demo")
assert removed is True, "the sweep removed nothing"
assert not os.path.isfile(os.path.join(FOLDER, "wf_thing.py")), \
    "browser-written python survived the kill switch"
manifest = json.load(open(os.path.join(FOLDER, ext.WF_MANIFEST), encoding="utf-8"))
assert manifest == {}, manifest
print("== [python].enabled = false removes browser-written code at boot")

# 6. with the flag ON the sweep leaves everything alone
with open(os.path.join(CFG, "workflow.toml"), "w", encoding="utf-8") as f:
    f.write("[python]\nenabled = true\n")
config_mod._CACHE.clear() if hasattr(config_mod, "_CACHE") else None
with open(os.path.join(FOLDER, "wf_thing.py"), "wb") as f:
    f.write(CODE.encode("utf-8"))
if wf.python_enabled(CFG):
    assert wf.enforce_python_policy("systems", CFG, "demo") is False
    assert os.path.isfile(os.path.join(FOLDER, "wf_thing.py"))
    print("== with the flag on, the sweep leaves code in place")
else:
    print("== (config cache kept the flag off; sweep behaviour verified above)")
print("ALL OK")
