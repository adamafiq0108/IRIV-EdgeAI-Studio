# With several models running, tuning one must not touch the others.
#
# Each entry of [[detector.models]] carries its own classes and confidence.
# Writing the SHARED keys instead applied one model's filter to every model --
# so ticking "pcb" on a two-class board model silenced the COCO model beside
# it, which emits no such class.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc            # noqa: E402

SYSTEMS = tempfile.mkdtemp()
SLUG = "permodel_demo"
PKG = os.path.join(SYSTEMS, SLUG)
os.makedirs(os.path.join(PKG, "data"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as f:
    f.write('[detector]\nkind = "hailo"\nconfidence = 0.45\n')

# Two models running, each with its own vocabulary on disk.
coco = os.path.join(PKG, "data", "coco.txt")
pcb = os.path.join(PKG, "data", "pcb.txt")
with open(coco, "w", encoding="utf-8") as f:
    f.write("person\ncar\ndog\n")
with open(pcb, "w", encoding="utf-8") as f:
    f.write("pcb\nsolder\n")
local = {"detector": {"kind": "hailo", "model": "yolov8n",
                      "labels": coco.replace(os.sep, "/"),
                      "models": [
                          {"model": "yolov8n", "models_dir": "/a",
                           "labels": coco.replace(os.sep, "/")},
                          {"model": "pcbnet", "models_dir": "/b",
                           "labels": pcb.replace(os.sep, "/")},
                      ]}}
dc._write_local(PKG, local)

# 1. each model reports ITS OWN vocabulary, not the union
by_model = dc.model_labels_by_model(SYSTEMS, SLUG)
assert by_model["yolov8n"] == ["person", "car", "dog"], by_model
assert by_model["pcbnet"] == ["pcb", "solder"], by_model
print("== each model reports its own labels:",
      {k: len(v) for k, v in by_model.items()})

# 2. tuning ONE model writes that entry only
out = dc.set_detector_params(SYSTEMS, SLUG, classes=["pcb"], confidence=0.6,
                             model="pcbnet")
assert out["model"] == "pcbnet" and out["classes"] == ["pcb"], out
cfg = dc._read_toml(dc._local_path(PKG))
entries = {e["model"]: e for e in cfg["detector"]["models"]}
assert entries["pcbnet"]["classes"] == ["pcb"], entries["pcbnet"]
assert entries["pcbnet"]["confidence"] == 0.6, entries["pcbnet"]
print("== tuning one model writes that entry:", entries["pcbnet"])

# 3. ...and leaves the other alone. This is the bug: a shared write would have
#    given yolov8n a "pcb" filter, and it emits no such class -- silencing it.
assert "classes" not in entries["yolov8n"], entries["yolov8n"]
assert "classes" not in cfg["detector"], \
    "the shared filter was written too, which every model obeys"
print("== the other model is untouched:", entries["yolov8n"])

# 4. no model named = the shared keys, which is what a one-model system wants
dc.set_detector_params(SYSTEMS, SLUG, classes=["person"], confidence=0.5)
cfg = dc._read_toml(dc._local_path(PKG))
assert cfg["detector"]["classes"] == ["person"], cfg["detector"]
entries = {e["model"]: e for e in cfg["detector"]["models"]}
assert entries["pcbnet"]["classes"] == ["pcb"], "the per-model filter was lost"
print("== an unnamed save still writes the shared keys, per-model kept")

# 5. a name that is not running falls back to the shared keys rather than
#    inventing an entry nothing runs
dc.set_detector_params(SYSTEMS, SLUG, classes=["dog"], model="not_running")
cfg = dc._read_toml(dc._local_path(PKG))
assert {e["model"] for e in cfg["detector"]["models"]} == {"yolov8n", "pcbnet"}
assert cfg["detector"]["classes"] == ["dog"], cfg["detector"]
print("== an unknown model does not add a phantom entry")
print("ALL OK")
