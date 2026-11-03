"""The Add-a-model form offers what this board can actually run, and nothing
whose only use is to get it wrong.

Two controls went. The ARCHITECTURE list held all six architectures the
manifest format knows; on a board with a Hailo-8 in it, five of them were
wrong, and a wrong one is not caught on save -- the model installs, sits in
the list looking ready, and fails at load with a mismatch nobody connects to a
dropdown they picked from a week earlier. And the LABELS textarea: line order
in it IS the class index, so a line moved or deleted silently relabels every
detection the model will ever make (person becomes bicycle) with nothing
anywhere saying so.

Both are narrowings of the OFFER, not of the format: ARCHS still validates, so
a model imported from a box with a different accelerator stays editable
without its arch being rewritten, and the labels themselves still arrive from
the preset or the uploaded .labels file.

    python tests/smoke_modelform.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import models as models_service                # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- 1. the offer is this board's accelerator --------------------------------
check("a detected chip is the only architecture offered",
      models_service.offered_archs("hailo8") == ["hailo8"],
      "a HEF for another arch installs cleanly and then fails at load")
# ...except where a board really does run more than its own builds. A Hailo-10
# module has run the 8 and 8L HEFs here in practice, and hiding them left an
# operator holding weights they could not install on hardware that loads them.
check("a Hailo-10 board is offered the 8 and 8L builds as well",
      models_service.offered_archs("hailo10h") == ["hailo10h", "hailo8", "hailo8l"],
      "granted per chip, not by dropping the check that keeps the others honest")
check("...and a Hailo-8 board is still offered only its own",
      models_service.offered_archs("hailo8l") == ["hailo8l"],
      "a HEF for the wrong accelerator installs cleanly and fails at load")
check("no chip detected means no basis to narrow",
      models_service.offered_archs("") == list(models_service.ARCHS)
      and models_service.offered_archs(None) == list(models_service.ARCHS))
check("...and a value that is not an architecture narrows nothing either",
      models_service.offered_archs("banana") == list(models_service.ARCHS))
check("an arch already ON a model stays in the list",
      models_service.offered_archs("hailo8", "hailo8l") == ["hailo8", "hailo8l"],
      "a value missing from its own dropdown reads as blank, and saving over "
      "a blank arch loses the arch the weights were compiled for")
check("...without duplicating it when it IS the detected one",
      models_service.offered_archs("hailo8", "hailo8") == ["hailo8"])
check("the full vocabulary still VALIDATES",
      len(models_service.ARCHS) == 6 and "cpu" in models_service.ARCHS,
      "narrowing the offer must not make an imported model unsavable")

route = read("core", "routes", "models.py")
check("the listing serves the narrowed list, and says which chip it found",
      '"archs": models_service.offered_archs(detected),' in route
      and '"detected_arch": detected,' in route)
# Counted within the LISTING route: other routes (the custom-model upload
# checks a HEF's target against the chip) have their own reason to ask once.
_listing = route[route.index('@router.get("/api/models"'):route.index('@router.get("/api/models/presets')]
check("...detecting the chip once, not once per field",
      _listing.count("builtin_service.detect_arch()") == 1,
      "detect_arch shells out to lspci")

page = read("frontend", "src", "pages", "Models.jsx")
check("the form draws from that list",
      "{archOptions.map((a) => <option key={a}>{a}</option>)}" in page)
check("...and puts the model's own arch back if the list lacks it",
      "const archOptions = (vocab.archs || []).includes(form.arch) || !form.arch" in page)
check("...and says plainly what the board runs when there is one answer",
      "This board runs ${archOptions[0]}" in page)

# -- 2. the labels are not hand-edited any more ------------------------------
check("no labels textarea",
      "setLabelsText(e.target.value)" not in page,
      "line order IS the class index -- a line moved there relabels every "
      "detection the model will ever make, silently")
check("...but the labels themselves still travel with the model",
      "labels_text: labelsText" in page and "setLabelsText(labels_text)" in page,
      "a preset fills them and an uploaded .labels supplies them; only the "
      "editing went")
check("the .hef picker stays, and stands alone",
      'label="Weights file (.hef)"' in page
      and 'accept=".hef"' in page
      and "<details className=\"models-advanced-fields\">" not in page,
      "attaching weights is the one manual step a custom model still needs; "
      "a single field is not worth a disclosure")
check("...and what was attached is still stated",
      "{file.name} attached" in page and "{modelLabels.length} labels" in page)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
