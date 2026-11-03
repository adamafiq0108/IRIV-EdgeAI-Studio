# The AI Models page lists the built-in models, with what is running.
#
# UAT-101 (Ochobot, first run on board "iriv", 2026-10-04): a new system ran
# yolov8s out of the box (/api/detector/builtin: current: true, downloaded),
# but Models.jsx only read /api/models and /api/detector/control/params, so
# the Default Model card said "Nothing saved yet" -- on the page the setup
# tour's step 1 ("Choose an AI model") sends the integrator to. The only path
# offered, "+ Add a model" > preset > Save, wrote yolov8s.json with no upload
# and then said "Needs its .hef" although the built-in .hef was on the box.
#
# fastapi and a browser are not on a dev box, so the page wiring is read from
# source and the listing is driven directly.
#
#     py -3 tests/smoke_builtinrows.py
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


with open(os.path.join(ROOT, "frontend", "src", "pages", "Models.jsx"),
          encoding="utf-8") as f:
    page = f.read()

load = page[page.index("const load = useCallback"):]
load = load[:load.index("}, []);")]
check("the page fetches /api/detector/builtin with the rest",
      'api.get("/api/detector/builtin")' in load, load)

check("built-in rows come from the catalogue entries, not the uploads twice",
      'b.source === "builtin"' in page)
check("...including the selected one, so the page never hides what runs",
      re.search(r"b\.downloaded \|\| b\.current \|\| activeSet\.includes\(b\.name\)",
                page) is not None)

rows = page[page.index("{builtinRows.map((b) => {"):]
rows = rows[:rows.index("{!total ? (")]
check("a built-in row carries the same buttons as a saved model",
      '"Use on this system"' in rows and '"In use · click to stop"' in rows
      and "toggleEnable(b.name)" in rows, rows[:300])
check("...marked Active when it is in the running set",
      'running && <Badge tone="ok">Active</Badge>' in rows)
check("...and says whether its .hef is on this box",
      '".hef on this box"' in rows and "b.downloaded" in rows)
check("the tour has an anchor on the button", 'data-tour="use-model"' in rows)

# The preset path: a saved manifest named after a built-in runs that .hef.
check("a manifest named after a built-in on the box is runnable",
      "const hasWeights = m.weights_present || builtinOnBox.has(m.name);" in page
      and '.endsWith(".hef") && hasWeights' in page)
check("...and its chip does not say 'no weights'",
      'hasWeights ? "built-in .hef" : "no weights"' in page)

# With built-ins listed the empty-state line must not say nothing is there.
empty = page[page.index("{!total ? ("):][:600]
check("the empty state is worded for a page that has built-in rows",
      "builtinRows.length" in empty and "built-in ones above" in empty, empty)

# And the listing the page reads really does mark the current model.
from core.services import builtin_models as builtin_service   # noqa: E402
src = open(builtin_service.__file__, encoding="utf-8").read()
check("the listing marks current and source per model",
      '"current": name == current' in src and '"source": "builtin"' in src)

print("FAILED" if bad else "PASS")
sys.exit(1 if bad else 0)
