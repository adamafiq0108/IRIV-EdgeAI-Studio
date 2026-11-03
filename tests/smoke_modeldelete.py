# Deleting a dropped model: only files inside a model folder, never the one
# that is running, and never anything the caller merely names.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

DROP = tempfile.mkdtemp()
os.environ["CYTRON_MODEL_DIR"] = DROP

from core.services import local_models as lm                # noqa: E402

# The service reads its root at import; point it at the sandbox either way.
lm.MODEL_DIR = DROP


def drop(name, arch="hailo8l"):
    folder = os.path.join(DROP, arch)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{name}.hef")
    with open(path, "wb") as f:
        f.write(b"HEF\0" + b"x" * 64)
    return path


keep, doomed = drop("keeper"), drop("doomed")
rows = {r["name"]: r for r in lm.listing()["models"]}
assert {"keeper", "doomed"} <= set(rows), sorted(rows)
print("== both dropped models are listed:", sorted(rows))

# 1. a plain delete removes exactly that file
lm.delete(rows["doomed"]["id"])
assert not os.path.exists(doomed), "the file is still there"
assert os.path.exists(keep), "it deleted the wrong file"
print("== deletes exactly the model asked for, and nothing beside it")

# 2. the RUNNING model is refused -- deleting live weights breaks the system
#    at its next restart with nothing on screen to explain why
try:
    lm.delete(rows["keeper"]["id"], in_use=["keeper"])
except ValueError as exc:
    assert "running" in str(exc), exc
    assert os.path.exists(keep)
    print("== the running model is refused:", exc)
else:
    raise AssertionError("the running model was deleted")

# 3. a name that is not in the listing is refused -- the path always comes
#    from our own scan, never from the request
for bad in ("../../etc/passwd", "/etc/passwd", "nope", ""):
    try:
        lm.delete(bad)
    except ValueError as exc:
        assert "no such model" in str(exc), (bad, exc)
    else:
        raise AssertionError(f"delete accepted {bad!r}")
print("== a path or a name that is not a listed model is refused")

# 4. the sidecar description an operator wrote by hand is left alone
side = drop("described")
with open(os.path.join(os.path.dirname(side), "described.json"), "w",
          encoding="utf-8") as f:
    f.write('{"labels": ["person"]}')
row = {r["name"]: r for r in lm.listing()["models"]}["described"]
lm.delete(row["id"])
assert not os.path.exists(side)
assert os.path.exists(os.path.join(os.path.dirname(side), "described.json")), \
    "deleting a model took the operator's own description with it"
print("== the sidecar manifest is left alone -- that is not what was asked for")
print("ALL OK")
