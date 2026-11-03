# "Show title" on every Dashboard / Analytics card (Tok Aba, 2026-10-14),
# end to end on the server:
#
#   1. GOLDEN: an old layout (every kind, no show_title) validates and
#      round-trips through save/load exactly as before the switch existed
#      (tests/golden_show_title_layout.json, captured before the change)
#   2. builder: the switch is kept only when it is a real yes / no; the
#      title text is never touched; compute hands it to the page
#   3. compile: a dashboard node's show_title reaches its wf_ card only when
#      set; a node without it compiles as before
#   4. the Builder -> node write (card-style) stores it and stamps it, and a
#      stale canvas tab cannot put the old value back (keep_builder_edits);
#      the canvas itself may still change it
#   5. a linked copy keeps its own switch across a recompile
#
#   py -3 tests/smoke_showtitle.py
#   py -3 tests/smoke_showtitle.py --write-golden   (only before a change)
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

GOLDEN = os.path.join(ROOT, "tests", "golden_show_title_layout.json")
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


OLD = {"title": "Old", "pages": [{"id": "main", "name": "Dashboard", "widgets": [
    {"id": "t1", "title": "Count", "kind": "tile", "source": "stat", "key": "fn_x", "sub": "today"},
    {"id": "l1", "title": "Trend", "kind": "line", "source": "history", "key": "fn_x", "range": "24h"},
    {"id": "b1", "title": "Bars", "kind": "bar", "source": "bucket", "key": "fn_day"},
    {"id": "v1", "title": "Status", "kind": "verdict", "source": "stat", "key": "compliance"},
    {"id": "s1", "title": "Pill", "kind": "status", "source": "stat", "key": "fn_x",
     "status": {"op": ">=", "value": 1, "ok_label": "OK", "alert_label": "ALERT"}},
    {"id": "e1", "title": "Events", "kind": "table", "source": "events"},
    {"id": "g1", "title": "Dial", "kind": "gauge", "source": "stat", "key": "fn_x"},
    {"id": "h1", "title": "Heat", "kind": "heatmap", "source": "stat", "key": "fn_h"},
    {"id": "vid", "title": "Feed", "kind": "video", "source": "stat", "key": ""},
    {"id": "tx", "title": "Notes", "kind": "text_card", "body": "hello"},
    {"id": "im", "title": "Plan", "kind": "image_card", "asset": "0123456789abcdef0123456789abcdef"},
]}]}

if "--write-golden" in sys.argv:
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(builder_service.validate(copy.deepcopy(OLD["pages"][0]["widgets"])), f,
                  indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

# -- 1. golden -----------------------------------------------------------------------
golden = json.load(io.open(GOLDEN, encoding="utf-8"))
now = json.loads(json.dumps(builder_service.validate(copy.deepcopy(OLD["pages"][0]["widgets"])),
                            sort_keys=True))
check("an old layout validates exactly as before the switch existed", now == golden,
      [w["id"] for w, g in zip(now, golden) if w != g])
saved = builder_service.save(None, "titles", copy.deepcopy(OLD))
loaded = builder_service.load(None, "titles")
check("...and round-trips through save and load unchanged, no show_title anywhere",
      json.loads(json.dumps(loaded["pages"][0]["widgets"], sort_keys=True)) == golden
      and not any("show_title" in w for w in loaded["pages"][0]["widgets"]))

# -- 2. builder ------------------------------------------------------------------------
out = builder_service.validate([
    {"id": "a", "title": "Kept text", "kind": "tile", "source": "stat", "key": "k", "show_title": False},
    {"id": "b", "title": "B", "kind": "text_card", "show_title": True},
    {"id": "c", "title": "C", "kind": "tile", "source": "stat", "key": "k", "show_title": "no"},
    {"id": "d", "title": "D", "kind": "line", "source": "history", "key": "k", "show_title": 0},
])
check("a real yes / no is kept, on any kind",
      out[0]["show_title"] is False and out[1]["show_title"] is True)
check("...anything else is not a setting (the default stands)",
      "show_title" not in out[2] and "show_title" not in out[3])
check("hiding the title never touches the title text", out[0]["title"] == "Kept text")
check("the default per kind: shown everywhere but the Text and Image cards",
      builder_service.title_shown_by_default("tile")
      and builder_service.title_shown_by_default("line")
      and not builder_service.title_shown_by_default("text_card")
      and not builder_service.title_shown_by_default("image_card"))
rows = builder_service.compute(out[:2], [], {}, slug="titles")
check("compute hands the switch to the page", rows[0]["show_title"] is False and rows[1]["show_title"] is True)
plain = builder_service.compute(builder_service.validate(
    [{"id": "p", "kind": "tile", "source": "stat", "key": "k"}]), [], {}, slug="titles")
check("...and a card without it gets no key at all", "show_title" not in plain[0])


# -- 3. compile ------------------------------------------------------------------------
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


check("a node with Show title off compiles a card that hides it",
      compiled({"show_title": False}).get("show_title") is False)
check("...a node without the setting compiles as before (no key)",
      "show_title" not in compiled({}))
check("...and the catalogue entry carries it for a copy dragged out",
      compiled({"show_title": False})["title"] == "In view")

# -- 4. Builder -> node, and a stale canvas tab ------------------------------------------
store = {"workflows": [{"graph": graph({})}]}
n = wf.apply_card_style(store, [{"id": "wf_c", "show_title": False}])
node = store["workflows"][0]["graph"]["nodes"][2]["data"]
check("card-style writes the Builder's switch onto the node, stamped",
      n == 1 and node["show_title"] is False and wf.BUILDER_EDIT_KEY in node, node)
check("...and ignores anything that is not a yes / no",
      wf.apply_card_style(store, [{"id": "wf_c", "show_title": "off"}]) == 0
      and node["show_title"] is False)
stale = graph({})                               # a tab opened before the Builder edit
wf.keep_builder_edits(stale, store["workflows"][0]["graph"])
check("a stale canvas tab cannot put the title back",
      stale["nodes"][2]["data"].get("show_title") is False, stale["nodes"][2]["data"])
fresh = copy.deepcopy(store["workflows"][0]["graph"])
fresh["nodes"][2]["data"]["show_title"] = True  # switched on the canvas itself
wf.keep_builder_edits(fresh, store["workflows"][0]["graph"])
check("...while the canvas itself may still switch it",
      fresh["nodes"][2]["data"]["show_title"] is True)

# -- 5. a linked copy keeps its own switch -------------------------------------------------
check("a linked copy's own switch survives a recompile (it is part of its look)",
      "show_title" in open(os.path.join(ROOT, "core", "services", "workflow.py"),
                              encoding="utf-8").read().split("_OPERATORS_LOOK = (")[1].split(")")[0])

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
