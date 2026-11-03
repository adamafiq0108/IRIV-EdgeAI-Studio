# Hidden + placements apply to BUILDER rows (wf_* cards) on the Analytics
# overview, and hidden_catalogue offers hidden builder cards back.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import analytics as an          # noqa: E402
from core.services import builder as bs            # noqa: E402

cfg = tempfile.mkdtemp()
slug = "demo"

W = lambda i: {"id": i, "title": i, "kind": "tile", "source": "stat",
               "key": "people_now", "grid": {"x": 0, "y": 0, "w": 3, "h": 2}}

bs.save(cfg, slug, {
    "pages": [
        {"id": "main", "name": "Dashboard", "widgets": [W("mine1")]},
        {"id": "overview", "name": "Overview",
         "widgets": [W("wf_a"), W("wf_b"), W("own1")]},
    ],
    "hidden": ["wf_b"],
    "placements": {"overview": {"wf_a": {"x": 5, "y": 3, "w": 4, "h": 2}}},
})

pages = an.compute_pages([], [], {"people_now": 2}, cfg, slug)
ov = pages[0]["widgets"]
ids = [w["id"] for w in ov]
assert "wf_b" not in ids, f"hidden wf_ card still rendered: {ids}"
assert "wf_a" in ids and "own1" in ids, ids
wfa = next(w for w in ov if w["id"] == "wf_a")
assert wfa["grid"] == {"x": 5, "y": 3, "w": 4, "h": 2}, wfa["grid"]
print("== hidden filters wf_ rows; placements move them:", ids)

cat = an.hidden_catalogue([], cfg, slug)
assert {c["id"] for c in cat} == {"wf_b"}, cat
print("== hidden_catalogue offers the hidden wf_ card back:", cat)

# restore: drop the hidden entry -> the card is rendered again (doc survived)
lay = bs.load(cfg, slug)
lay["hidden"] = []
bs.save(cfg, slug, lay)
ids2 = [w["id"] for w in an.compute_pages([], [], {}, cfg, slug)[0]["widgets"]]
assert "wf_b" in ids2, ids2
print("== unhide restores the card without a recompile:", ids2)
print("ALL OK")
