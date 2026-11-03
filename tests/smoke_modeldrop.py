# The drop-folder model store: the same HEF filename in several folders (a
# copied model, a renamed folder kept around) must not turn "change the
# running model" into "no detection model named X" -- an ambiguity is not an
# absence. Name lookup prefers the folder NAMED like the model; id lookup
# stays exact.
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

DROP = tempfile.mkdtemp()
os.environ["CYTRON_MODEL_DIR"] = DROP
os.environ.setdefault("CYTRON_STORE", tempfile.mkdtemp())

for folder in ("yolox_demo", "yolox_demo_forklift", "misc"):
    d = os.path.join(DROP, folder)
    os.makedirs(d)
    with open(os.path.join(d, "yolox_demo.hef"), "wb") as f:
        f.write(b"HEF?fake")
    with open(os.path.join(d, "yolox_demo.json"), "w", encoding="utf-8") as f:
        json.dump({"labels": ["forklift"] if "forklift" in folder
                   else ["person"]}, f)

from core.services import local_models  # noqa: E402

rows = local_models.listing()["models"]
same = [r for r in rows if r["name"] == "yolox_demo"]
assert len(same) == 3, f"expected 3 copies listed, got {len(same)}"

# 1. Ambiguous NAME resolves to the folder named like the model, not None.
row = local_models.get("yolox_demo")
assert row is not None, "an ambiguous name must not read as a missing model"
assert os.path.basename(str(row["folder"])) == "yolox_demo", \
    "the folder named like the model wins the tie"

# 2. Id lookup stays exact -- any copy is still addressable individually.
for r in same:
    assert local_models.get(r["id"])["folder"] == r["folder"]

# 3. A truly unknown name still answers None.
assert local_models.get("no_such_model") is None

print("smoke_modeldrop OK")
