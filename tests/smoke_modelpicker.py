# The Detection Stream's Model picker on a system with ONE model.
#
# The picker is drawn from the workflow editor's `models` list, which came
# from the model->labels map -- and that map named a single model only when a
# labels file sat beside it. A custom .hef with no labels list therefore
# reported NO models at all, so the field vanished and the operator could not
# say which AI a branch reads. A model with an unknown vocabulary is still a
# model; the empty class list is the honest answer to the other question.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import detector_control as dc              # noqa: E402
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


SYSTEMS = tempfile.mkdtemp()


def system(slug, detector):
    pkg = os.path.join(SYSTEMS, slug)
    os.makedirs(os.path.join(pkg, "data"), exist_ok=True)
    with open(os.path.join(pkg, "config.toml"), "w", encoding="utf-8") as f:
        f.write('[detector]\nkind = "hailo"\n')
    dc._write_local(pkg, {"detector": detector})
    return pkg


# -- one model, no labels file: named anyway ---------------------------------
system("nolabels", {"kind": "hailo", "model": "pcb_v3"})
by_model = dc.model_labels_by_model(SYSTEMS, "nolabels")
check("a model with no labels beside it is still a model",
      list(by_model) == ["pcb_v3"], repr(by_model))
check("...and says its vocabulary is unknown rather than inventing one",
      by_model.get("pcb_v3") == [], repr(by_model))

# -- one model WITH labels: unchanged ----------------------------------------
pkg = system("labelled", {"kind": "hailo", "model": "yolov8n"})
labels = os.path.join(pkg, "data", "coco.txt")
with open(labels, "w", encoding="utf-8") as f:
    f.write("person\ncar\n")
dc._write_local(pkg, {"detector": {"kind": "hailo", "model": "yolov8n",
                                   "labels": labels.replace(os.sep, "/")}})
by_model = dc.model_labels_by_model(SYSTEMS, "labelled")
check("a labelled model still reports its classes",
      by_model == {"yolov8n": ["person", "car"]}, repr(by_model))

# -- no model configured at all: nothing to name ------------------------------
system("bare", {"kind": "hailo"})
check("a system with no model names none",
      dc.model_labels_by_model(SYSTEMS, "bare") == {})

# -- the editor offers exactly what the map names ----------------------------
page = open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
            encoding="utf-8").read()
check("the Model field is drawn from that list",
      '(vars.models || []).length > 0' in page
      and '(vars.models || []).map((m) => <option key={m} value={m}>{m}</option>)'
      in page)
# And the camera beside it is named by its SLOT: a device name goes stale the
# day the camera is swapped, and "Camera 2" is what the rail, the zone editor
# and the compile error all say.
check("the camera beside it is named by slot",
      "{camLabel(vars.cameras, c.id)}" in page)
reg = open(os.path.join(ROOT, "frontend", "src", "workflow", "registry.js"),
           encoding="utf-8").read()
check("...and a slot is the position in the list, not a stored name",
      "return i < 0 ? String(id || \"\") : `Camera ${i + 1}`;" in reg)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
