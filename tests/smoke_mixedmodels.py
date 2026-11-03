# A model dropped into the model folder must run ALONGSIDE a built-in one.
#
# Every entry in [[detector.models]] carries its own models_dir, so a mixed set
# was always expressible -- the resolver simply did not know the drop folder
# existed, so "Add onto this system" failed with "no detection model named
# 'yolox_s_leaky_hailo10h'" about a model the page was listing two cards away.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

DROP = tempfile.mkdtemp()
os.environ["CYTRON_MODEL_DIR"] = DROP

from core.services import builtin_models as bm              # noqa: E402
from core.services import detector_control as dc            # noqa: E402
from core.services import local_models as lm                # noqa: E402

lm.MODEL_DIR = DROP

# A system package with one built-in model on disk...
SYSTEMS = tempfile.mkdtemp()
SLUG = "mixed_demo"
PKG = os.path.join(SYSTEMS, SLUG)
os.makedirs(os.path.join(PKG, "models", "hailo8l"), exist_ok=True)
os.makedirs(os.path.join(PKG, "data"), exist_ok=True)
with open(os.path.join(PKG, "models", "hailo8l", "yolov8n.hef"), "wb") as f:
    f.write(b"HEF\0")
with open(os.path.join(PKG, "models.toml"), "w", encoding="utf-8") as f:
    f.write('[models.yolov8n]\ntask = "detection"\nfile = "yolov8n.hef"\n'
            'label = "YOLOv8n"\n')
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write(f'[app]\ndata_dir = "{os.path.join(PKG, "data").replace(os.sep, "/")}"\n'
            '[detector]\nkind = "hailo"\nmodels_dir = "models"\n')

# ...and a HEF dropped in by hand, with its own labels beside it.
folder = os.path.join(DROP, "yolox_s")
os.makedirs(folder, exist_ok=True)
with open(os.path.join(folder, "yolox_s.hef"), "wb") as f:
    f.write(b"HEF\0")
with open(os.path.join(folder, "yolox_s.json"), "w", encoding="utf-8") as f:
    f.write('{"task": "detection", "arch": "hailo8l", "labels": ["pcb"]}')

dropped = {r["name"]: r for r in lm.listing()["models"]}
assert "yolox_s" in dropped, sorted(dropped)
print("== the dropped model is listed:", sorted(dropped))

# 1. it resolves at all -- this is the bit that used to raise
sel = bm._resolve_selection(PKG, None, "yolox_s")
assert sel["model"] == "yolox_s", sel
assert os.path.realpath(sel["models_dir"]) == os.path.realpath(folder), sel
print("== a dropped model resolves, pointing at its own folder")

# 2. its labels are materialised into THIS system's data dir -- the drop
#    folder is not ours to write into, and the app reads labels from a file
assert sel["labels"] and os.path.exists(sel["labels"]), sel["labels"]
assert os.path.realpath(sel["labels"]).startswith(
    os.path.realpath(os.path.join(PKG, "data"))), sel["labels"]
with open(sel["labels"], encoding="utf-8") as f:
    assert "pcb" in f.read()
print("== its labels are written into the system's own data dir")

# 3. BOTH run together, each entry keeping its own models_dir
out = bm.select_many(SYSTEMS, SLUG, ["yolov8n", "yolox_s"])
assert sorted(out["models"]) == ["yolov8n", "yolox_s"], out
cfg = dc._read_toml(os.path.join(PKG, "config.local.toml"))
entries = {e["model"]: e for e in (cfg.get("detector") or {}).get("models") or []}
assert set(entries) == {"yolov8n", "yolox_s"}, entries
assert os.path.realpath(entries["yolox_s"]["models_dir"]) == os.path.realpath(folder)
assert os.path.realpath(entries["yolov8n"]["models_dir"]) != os.path.realpath(folder)
print("== both run together, each pointing at the folder that holds it:")
for name, e in sorted(entries.items()):
    print(f"   {name:10s} -> {e['models_dir']}")

# 4. a name in NO store still fails, and says where it looked
try:
    bm._resolve_selection(PKG, None, "not_a_model")
except ValueError as exc:
    assert "model folder" in str(exc), exc
    print("== an unknown model names every store it looked in:", exc)
else:
    raise AssertionError("an unknown model resolved")
print("ALL OK")
