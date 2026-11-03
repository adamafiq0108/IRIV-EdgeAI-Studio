# Which card is in front where cards overlap on a Builder page (Tok Aba,
# 2026-10-14): a card's layer is `grid.z`, part of WHERE it sits like x / y.
#
#   1. GOLDEN: an old layout (grids, placements, overlapping cards, no z)
#      saves and loads exactly as before (tests/golden_layer_layout.json,
#      captured before the change)
#   2. z is kept only as a whole number, clamped to +-50, and 0 is stored as
#      nothing; a package card's placement keeps its z the same way
#   3. a workflow recompile keeps a placed wf_ card's layer (it rides the
#      grid the recompile carries over), and z never reaches the node
#
#   py -3 tests/smoke_layerorder.py
#   py -3 tests/smoke_layerorder.py --write-golden   (only before a change)
import copy
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import builder as builder_service             # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_layer_layout.json")
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


OLD = {"title": "Old", "pages": [{"id": "main", "name": "Dashboard", "widgets": [
    {"id": "a", "title": "Count", "kind": "tile", "source": "stat", "key": "k",
     "grid": {"x": 0, "y": 0, "w": 4, "h": 3}},
    {"id": "b", "title": "Notes", "kind": "text_card", "body": "on top of a",
     "grid": {"x": 2, "y": 1, "w": 4, "h": 2}},
    {"id": "c", "title": "Flow", "kind": "tile", "source": "stat", "key": "k", "span": 2},
]}], "placements": {"overview": {"pkg_card": {"x": 0, "y": 4, "w": 6, "h": 3}}}}

if "--write-golden" in sys.argv:
    builder_service.save(None, "golden", copy.deepcopy(OLD))
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(builder_service.load(None, "golden"), f, indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

# -- 1. golden -----------------------------------------------------------------------
builder_service.save(None, "golden", copy.deepcopy(OLD))
now = json.loads(json.dumps(builder_service.load(None, "golden"), sort_keys=True))
golden = json.load(io.open(GOLDEN, encoding="utf-8"))
check("an old layout (overlapping cards, placements, no z) saves and loads exactly as before",
      now == golden)

# -- 2. z ------------------------------------------------------------------------------
g = builder_service._clean_grid
check("a whole number is kept", g({"x": 1, "y": 1, "w": 2, "h": 2, "z": 3})["z"] == 3)
check("...clamped to +-50", g({"x": 1, "y": 1, "w": 2, "h": 2, "z": 999})["z"] == 50
      and g({"x": 1, "y": 1, "w": 2, "h": 2, "z": -999})["z"] == -50)
check("...0, true, text or a fraction is not a layer (nothing stored)",
      all("z" not in g({"x": 1, "y": 1, "w": 2, "h": 2, "z": v}) for v in (0, True, "3", 2.5, None)))
lay = copy.deepcopy(OLD)
lay["pages"][0]["widgets"][1]["grid"]["z"] = 2
lay["placements"]["overview"]["pkg_card"]["z"] = -1
builder_service.save(None, "z", lay)
back = builder_service.load(None, "z")
check("a card's layer is saved with its grid",
      back["pages"][0]["widgets"][1]["grid"]["z"] == 2
      and "z" not in back["pages"][0]["widgets"][0]["grid"])
check("...and a package card's placement keeps its layer the same way",
      back["placements"]["overview"]["pkg_card"]["z"] == -1)
rows = builder_service.compute(back["pages"][0]["widgets"], [], {}, slug="z")
check("the page receives it with the grid", rows[1]["grid"].get("z") == 2)

# -- 3. a workflow card keeps its layer ----------------------------------------------------
src = open(os.path.join(ROOT, "core", "services", "workflow.py"), encoding="utf-8").read()
check("a recompile carries a placed wf_ card's grid over (so its layer with it)",
      'keep = {k: w[k] for k in ("grid", "span") if w.get(k)}' in src)
check("...and the layer is never a node field",
      '"z"' not in src.split("CARD_IDENTITY_FIELDS = (")[1].split(")")[0])

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
