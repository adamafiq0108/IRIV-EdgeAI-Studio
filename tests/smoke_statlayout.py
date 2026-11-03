# Where a stat sits in its card (Tok Aba, 2026-10-14): `align` (left /
# center / right) and `valign` (top / middle / bottom) on the tile, the
# status pill and the verdict (builder.STAT_LAYOUT_KINDS), end to end:
#
#   1. GOLDEN: an old layout of stat cards (none of the fields) validates and
#      round-trips exactly as before (tests/golden_stat_layout.json, captured
#      before the change)
#   2. builder: kept only when set to a known value, only on the stat kinds;
#      compute hands them to the page
#   3. compile: onto the wf_ card only when the node says so
#   4. Builder -> node (card-style), stamped; a stale canvas tab cannot undo
#      it; junk never reaches the node
#
#   py -3 tests/smoke_statlayout.py
#   py -3 tests/smoke_statlayout.py --write-golden   (only before a change)
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
from core.services import workflow as wf                         # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_stat_layout.json")
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


OLD = [
    {"id": "t1", "title": "Count", "kind": "tile", "source": "stat", "key": "fn_x", "sub": "today",
     "unit": "pcs", "template": "accent", "value_size": "huge"},
    {"id": "t2", "title": "Shift", "kind": "tile", "source": "stat", "key": "", "text": "Night"},
    {"id": "s1", "title": "Pill", "kind": "status", "source": "stat", "key": "fn_x",
     "status": {"op": ">=", "value": 1, "ok_label": "OK", "alert_label": "ALERT"}},
    {"id": "v1", "title": "Status", "kind": "verdict", "source": "stat", "key": "compliance"},
    {"id": "g1", "title": "Dial", "kind": "gauge", "source": "stat", "key": "fn_x"},
    {"id": "tx", "title": "Notes", "kind": "text_card", "body": "hi", "align": "center"},
]

if "--write-golden" in sys.argv:
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(builder_service.validate(copy.deepcopy(OLD)), f, indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

# -- 1. golden ---------------------------------------------------------------------------
golden = json.load(io.open(GOLDEN, encoding="utf-8"))
now = json.loads(json.dumps(builder_service.validate(copy.deepcopy(OLD)), sort_keys=True))
check("old stat cards validate exactly as before the Layout row existed", now == golden,
      [w["id"] for w, g in zip(now, golden) if w != g])
builder_service.save(None, "stats", {"pages": [{"id": "main", "widgets": copy.deepcopy(OLD)}]})
loaded = builder_service.load(None, "stats")["pages"][0]["widgets"]
check("...and round-trip through save and load unchanged",
      json.loads(json.dumps(loaded, sort_keys=True)) == golden)

# -- 2. builder --------------------------------------------------------------------------
out = builder_service.validate([
    {"id": "a", "kind": "tile", "source": "stat", "key": "k", "align": "right", "valign": "bottom"},
    {"id": "b", "kind": "status", "source": "stat", "key": "k", "align": "left", "valign": "top"},
    {"id": "c", "kind": "verdict", "source": "stat", "key": "compliance", "valign": "middle"},
    {"id": "d", "kind": "tile", "source": "stat", "key": "k", "align": "justify", "valign": "upper"},
    {"id": "e", "kind": "gauge", "source": "stat", "key": "k", "align": "right", "valign": "top"},
    {"id": "f", "kind": "line", "source": "history", "key": "k", "align": "right"},
])
check("tile, status and verdict keep a known alignment and position",
      (out[0]["align"], out[0]["valign"], out[1]["align"], out[1]["valign"], out[2]["valign"])
      == ("right", "bottom", "left", "top", "middle"))
check("...anything else is not a setting", "align" not in out[3] and "valign" not in out[3])
check("...and the other kinds have no Layout row to keep",
      "align" not in out[4] and "valign" not in out[4] and "align" not in out[5])
rows = builder_service.compute(out[:2], [], {}, slug="stats")
check("compute hands both to the page", rows[0]["align"] == "right" and rows[1]["valign"] == "top")


# -- 3. compile ---------------------------------------------------------------------------
def graph(extra):
    return {"nodes": [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "f", "type": "analytics.frame", "position": {"x": 0, "y": 0},
         "data": {"fnId": "f", "name": "F", "mode": "realtime"}},
        {"id": "c", "type": "dashboard.card", "position": {"x": 0, "y": 0},
         "data": {"name": "In view", "kind": "tile", **extra}}],
        "edges": [{"id": "e", "source": "s", "target": "f"},
                  {"id": "e2", "source": "f", "target": "c"}]}


def compiled(extra):
    c = wf.compile_graph(wf.clean_graph(graph(extra)), [])
    assert not c["errors"], c["errors"]
    return next(w for w in c["widgets"] if w["id"] == "wf_c")


w = compiled({"align": "center", "valign": "top"})
check("a node's Layout compiles onto its card", w.get("align") == "center" and w.get("valign") == "top")
w = compiled({})
check("...a node without it compiles as before", "align" not in w and "valign" not in w)
w = compiled({"align": "sideways"})
check("...and junk on the node never reaches the card", "align" not in w)

# -- 4. Builder -> node, stale tab ----------------------------------------------------------
store = {"workflows": [{"graph": graph({})}]}
n = wf.apply_card_style(store, [{"id": "wf_c", "align": "right", "valign": "bottom"}])
data = store["workflows"][0]["graph"]["nodes"][2]["data"]
check("card-style writes the Builder's Layout onto the node, stamped",
      n == 2 and data["align"] == "right" and data["valign"] == "bottom"
      and wf.BUILDER_EDIT_KEY in data, data)
check("...and refuses a value that is not one",
      wf.apply_card_style(store, [{"id": "wf_c", "align": "justify", "valign": 3}]) == 0)
stale = graph({})
wf.keep_builder_edits(stale, store["workflows"][0]["graph"])
check("a stale canvas tab cannot put the old layout back",
      stale["nodes"][2]["data"].get("align") == "right"
      and stale["nodes"][2]["data"].get("valign") == "bottom", stale["nodes"][2]["data"])

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
