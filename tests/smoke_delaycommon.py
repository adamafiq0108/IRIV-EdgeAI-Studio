"""The Delay node is listed in Common as well as Logic (Tok Aba, 2026-10-14).

"add delay nodes also into Common ... so when the workflow is running I can
control when to run the output". One node type, two drawers: the registry
entry keeps its home (palette.cat "logic") and names the extra drawer
(palette.also_in ["common"]). Pinned:

  1. graphs with a Delay compile byte-identical to before the listing
     changed (golden_delaycommon.json, captured before it)
  2. the registry says logic + also_in common, and nothing else moved
  3. the palette and the right-click insert menu honour also_in; a search
     lists it once (check_palettesearch draws it)

    py -3 tests/smoke_delaycommon.py            (check)
    py -3 tests/smoke_delaycommon.py --capture  (rewrite the golden; only before a change)
"""
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
os.environ["CYTRON_MASTER_PORT"] = "5110"

from core.services import workflow as wf                      # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_delaycommon.json")
ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]
BASE = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0}, "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "Bay"}},
    {"id": "when", "type": "logic.when", "position": {"x": 3, "y": 0},
     "data": {"metric": "count", "mode": "above", "value": 2}},
]
BASE_E = [{"id": "e1", "source": "s", "target": "z"},
          {"id": "e2", "source": "z", "target": "occ"},
          {"id": "e3", "source": "occ", "target": "when"}]
CASES = {
    "one": ([{"id": "dly", "type": "logic.delay", "position": {"x": 4, "y": 0},
              "data": {"delay_s": 5}},
             {"id": "snap", "type": "output.snapshot", "position": {"x": 5, "y": 0}, "data": {}}],
            [{"id": "e4", "source": "when", "target": "dly"},
             {"id": "e5", "source": "dly", "target": "snap"}]),
    "chain": ([{"id": "d1", "type": "logic.delay", "position": {"x": 4, "y": 0},
                "data": {"delay_s": 5}},
               {"id": "d2", "type": "logic.delay", "position": {"x": 4, "y": 1},
                "data": {"delay_s": 10}},
               {"id": "m", "type": "output.email", "position": {"x": 5, "y": 0},
                "data": {"to": "a@b.c", "subject": "s", "body": "{label}"}}],
              [{"id": "e4", "source": "when", "target": "d1"},
               {"id": "e5", "source": "d1", "target": "d2"},
               {"id": "e6", "source": "d2", "target": "m"}]),
    "split": ([{"id": "dly", "type": "logic.delay", "position": {"x": 4, "y": 0},
                "data": {"delay_s": 7}},
               {"id": "snap", "type": "output.snapshot", "position": {"x": 5, "y": 0}, "data": {}},
               {"id": "rec", "type": "output.record", "position": {"x": 5, "y": 1}, "data": {}}],
              [{"id": "e4", "source": "when", "target": "dly"},
               {"id": "e5", "source": "dly", "target": "snap"},
               {"id": "e6", "source": "when", "target": "rec"}]),
}


def compiled(name):
    nodes, edges = CASES[name]
    c = wf.compile_graph(wf.clean_graph({"nodes": BASE + nodes, "edges": BASE_E + edges}),
                         ZONES)
    return {k: c[k] for k in ("functions", "outputs", "errors", "warnings")}


if "--capture" in sys.argv:
    json.dump({k: compiled(k) for k in CASES},
              open(GOLDEN, "w", encoding="utf-8", newline="\n"), indent=1, sort_keys=True)
    print("captured", GOLDEN)
    sys.exit(0)

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


golden = json.load(open(GOLDEN, encoding="utf-8"))
for k in CASES:
    check(f"a graph with a Delay ({k}) compiles byte-identical to before",
          json.dumps(compiled(k), sort_keys=True) == json.dumps(golden[k], sort_keys=True))

pal = wf.registry_public()["logic.delay"]["palette"]
check("the Delay is listed in Common only (it was in Logic too)",
      pal == {"cat": "common", "group": ""}, pal)
check("...and no node has a second drawer",
      [t for t, s in wf.registry_public().items()
       if (s.get("palette") or {}).get("also_in")] == [])


def read(*p):
    return io.open(os.path.join(ROOT, *p), encoding="utf-8").read()


ui = read("frontend", "src", "pages", "Workflow.jsx")
menu = read("frontend", "src", "workflow", "ContextMenu.jsx")
check("the palette still honours also_in, but not while searching",
      "|| (!search && (sp.palette?.also_in || []).includes(cat));" in ui
      and ".filter(([, s]) => inDrawer(s, cat) && !s.deprecated && !s.hidden)" in ui)
check("...ungrouped there (its sub-heading belongs to its home drawer)",
      "const g = paletteCat(sp) === cat ? paletteGroup(sp) : \"\";" in ui)
check("the right-click insert menu lists it in both too",
      "(s.palette?.also_in || []).includes(cat))" in menu)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
