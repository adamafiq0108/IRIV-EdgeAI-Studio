# Uploading a custom model: a .hef + .json pair, checked whole, landed as
# <root>/<name>/<name>.hef + .json -- the layout the drop-folder scanner already
# reads and the guide tells people to build by hand.
#
# The checks are the ones the scanner and the runtime depend on: same stem on
# both files (that is how the sidecar is found), a JSON that parses to an
# object with a class list (a detector with no names shows numbers), a
# num_classes that agrees, and a target that is the chip in this box -- a HEF
# compiled for another chip does not load, and the error it gives at start is
# not one an operator can act on.
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
DROP = tempfile.mkdtemp()
os.environ["CYTRON_MODEL_DIR"] = DROP

from core.services import local_models as lm                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def refused(word, *a, **k):
    try:
        lm.check_pair(*a, **k)
        return False
    except ValueError as exc:
        return word in str(exc)


GOOD = json.dumps({"model": "yolox_s_leaky_hailo10h", "hef": "yolox_s_leaky_hailo10h.hef",
                   "num_classes": 3, "classes": ["Sliding-Door", "pointer", "pointer_2"],
                   "target": "hailo10h"})
# The same description with no file names in it, for the generic cases.
ANY = json.dumps({"num_classes": 3, "classes": ["Sliding-Door", "pointer", "pointer_2"],
                  "target": "hailo10h"})

m, stem = lm.check_pair("yolox_s_leaky_hailo10h.hef", "yolox_s_leaky_hailo10h.json", GOOD,
                        arch="hailo10h")
check("the customer's exact json passes, on its chip", stem == "yolox_s_leaky_hailo10h"
      and m["classes"][0] == "Sliding-Door")
check("...and with no chip known (a dev box without an accelerator)",
      lm.check_pair("a.hef", "a.json", ANY, arch=None)[1] == "a")
check("a different stem is refused", refused("share one name", "a.hef", "b.json", ANY))
check("a non-hef weights file is refused", refused(".hef", "a.onnx", "a.json", ANY))
check("a json that does not parse is refused", refused("parse", "a.hef", "a.json", "{nope"))
check("a json with no classes is refused",
      refused("classes", "a.hef", "a.json", json.dumps({"target": "hailo10h"})))
check("num_classes must agree",
      refused("num_classes", "a.hef", "a.json",
              json.dumps({"num_classes": 2, "classes": ["x", "y", "z"]})))
check("the wrong chip is refused, in the operator's words",
      refused("compiled for hailo8", "a.hef", "a.json", ANY.replace("hailo10h", "hailo8"),
              arch="hailo10h"))
check("an unknown target is refused", refused("target", "a.hef", "a.json",
                                              ANY.replace("hailo10h", "hailo99")))
check("a hef field naming another file is refused",
      refused('"hef"', "a.hef", "a.json", json.dumps({"hef": "other.hef", "classes": ["x"]})))
check("a path-shaped name is refused",
      refused("letters", "../../etc/x.hef", "x.json", ANY) or
      refused("letters", "a b.hef", "a b.json", ANY))
check("a labels key is accepted where classes is absent",
      lm.check_pair("a.hef", "a.json", json.dumps({"labels": ["a", "b"]}))[0]["labels"] == ["a", "b"])

# -- install ------------------------------------------------------------------------
lm.MODEL_DIR = DROP
tmp = os.path.join(lm.upload_tmp_dir(), "yolox_s_leaky_hailo10h.part")
with open(tmp, "wb") as f:
    f.write(b"HEF" * 100)
row = lm.install("yolox_s_leaky_hailo10h", tmp, GOOD)
folder = os.path.join(DROP, "yolox_s_leaky_hailo10h")
check("the pair lands as <root>/<name>/<name>.hef + .json",
      os.path.isfile(os.path.join(folder, "yolox_s_leaky_hailo10h.hef"))
      and os.path.isfile(os.path.join(folder, "yolox_s_leaky_hailo10h.json"))
      and not os.path.exists(tmp), os.listdir(DROP))
check("...and the scanner lists it with the classes as labels and the target as arch",
      row and row["name"] == "yolox_s_leaky_hailo10h" and row["labels"] == ["Sliding-Door", "pointer", "pointer_2"]
      and row["arch"] == "hailo10h" and row["has_manifest"], repr(row))
with open(tmp, "wb") as f:
    f.write(b"HEF2" * 100)
try:
    lm.install("yolox_s_leaky_hailo10h", tmp, GOOD)
    check("a second upload under the same name is refused", False)
except ValueError as exc:
    check("a second upload under the same name is refused", "already" in str(exc), str(exc))
check("...and the temp file is the caller's to clean, the model untouched",
      os.path.getsize(os.path.join(folder, "yolox_s_leaky_hailo10h.hef")) == 300)
row = lm.install("yolox_s_leaky_hailo10h", tmp, GOOD, replace=True)
check("replace=True overwrites in place",
      os.path.getsize(os.path.join(folder, "yolox_s_leaky_hailo10h.hef")) == 400 and row)
check("the listing sees exactly one model", len(lm.listing()["models"]) == 1,
      repr([r["id"] for r in lm.listing()["models"]]))

# -- the guide and the routes --------------------------------------------------------
guide = os.path.join(ROOT, "core", "static", "guides", "custom-model-upload.md")
check("the guide ships with the product, source and the rendered HTML fragment",
      os.path.isfile(guide) and os.path.isfile(guide[:-3] + ".html")
      and open(guide[:-3] + ".html", encoding="utf-8").read().startswith("<h1>")
      and not os.path.exists(guide[:-3] + ".pdf"))
text = open(guide, encoding="utf-8").read()
check("...and tells the whole story: the pair, the json, the target, no script",
      '"num_classes"' in text and '"target"' in text and "infer_example.py" in text
      and "same name" in text)
routes = open(os.path.join(ROOT, "core", "routes", "models.py"), encoding="utf-8").read()
check("the upload route streams the hef and is admin-gated",
      '"/api/detector/local_models/upload"' in routes and "hef.read(1024 * 1024)" in routes
      and routes[routes.index('"/api/detector/local_models/upload"'):].split("\n\n")[0]
      .count("models.manage") + routes[routes.index("async def local_models_upload"):
                                      routes.index("def _rm_quiet")].count("models.manage") >= 1)
check("the guide has a route that hands the HTML to the page's dialog",
      '"/api/detector/local_models/guide"' in routes and "custom-model-upload.html" in routes
      and '"html": f.read()' in routes)
page = open(os.path.join(ROOT, "frontend", "src", "pages", "Models.jsx"), encoding="utf-8").read()
check("the page has the button, the guide dialog and a folder picker",
      "Upload a model" in page and "local_models/guide" in page and "webkitdirectory" in page
      and "function GuideDialog" in page and "download=" not in page)
# Three folds: the saved-manifest card, the Custom Models card, and (since
# Tok Aba's 2026-10-05 report) the running built-in card's Detection settings.
check("every model card opens folded, with a chevron fold at its foot",
      "function CardFold" in page and page.count("<CardFold") == 3
      and "useState({});   // model id -> true = unfolded" in page)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
