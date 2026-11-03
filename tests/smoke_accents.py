# The accent colour of a COMPILED card has to survive the next compile.
#
# A wf_* card's row belongs to its workflow node and is rewritten on every
# apply; a package card's row is declared in Python and not editable at all.
# `template` and the card background already had override maps for exactly
# that reason -- the accent did not, so the Analytics Builder's accent picker
# wrote into a row the merge threw away and looked like it did nothing.
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as bs                        # noqa: E402

cfg = tempfile.mkdtemp()
SLUG = "acc"

WIDGET = {"id": "wf_n1", "title": "Total In Zone", "kind": "line",
          "source": "history", "key": "fn_a_count", "span": 1}

bs.save(cfg, SLUG, {"pages": [{"id": "overview", "name": "Overview",
                               "widgets": [WIDGET]}],
                    "accents": {"wf_n1": "#ff8800"},
                    "colors": {"wf_n1": "#101010"},
                    "templates": {"wf_n1": "gradient"}})

# 1. read back: the override is APPLIED to the widget, so every consumer --
#    the Analytics route, the editors, compute() -- sees the dressed card
#    without knowing overrides exist.
layout = bs.load(cfg, SLUG)
w = layout["pages"][0]["widgets"][0]
assert w["color"] == "#ff8800", w
assert w["bg"] == "#101010" and w["template"] == "gradient", w
print("== the accent is applied on the way in:", w["color"])

# 2. a compile rewrites the row -- the accent must not go with it. This is
#    what apply_artifacts does: replace the wf_* widgets, keep the maps.
from core.services import workflow as wf                       # noqa: E402
stored = json.load(io.open(bs.path_for(cfg, SLUG), encoding="utf-8"))
assert stored["accents"] == {"wf_n1": "#ff8800"}, stored.get("accents")
fresh = {**WIDGET, "title": "Total In Zone", "color": ""}      # compiler output
bs.save(cfg, SLUG, {**layout, "pages": [{"id": "overview", "name": "Overview",
                                         "widgets": [fresh]}]})
w = bs.load(cfg, SLUG)["pages"][0]["widgets"][0]
assert w["color"] == "#ff8800", "a recompile lost the operator's accent"
print("== and it survives the row being rewritten by a compile")

# 3. a partial save that does not mention the maps keeps them
merged = bs.merge_partial(bs.load(cfg, SLUG),
                          {"pages": [{"id": "overview", "name": "Overview",
                                      "widgets": []}]})
assert merged["accents"] == {"wf_n1": "#ff8800"}, merged["accents"]
print("== a partial save that is silent about accents keeps them")

# 4. ...and one that DOES mention them replaces them, so clearing works
merged = bs.merge_partial(bs.load(cfg, SLUG),
                          {"pages": [], "accents": {}})
assert merged["accents"] == {}, merged["accents"]
print("== clearing the accent is honoured")

# 5. rubbish never reaches a stylesheet
bs.save(cfg, SLUG, {"pages": [{"id": "overview", "name": "Overview",
                               "widgets": [WIDGET]}],
                    "accents": {"wf_n1": "red; background:url(x)"}})
assert bs.load(cfg, SLUG)["accents"] == {}, bs.load(cfg, SLUG)["accents"]
print("== a colour that is not a colour is dropped")
print("ALL OK")
