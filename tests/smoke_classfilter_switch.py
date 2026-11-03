# The Class Filter node's On / Off switch (Tok Aba, 2026-10-14), one state
# with the dashboard's "Class filter" card:
#
#   1. GOLDEN: representative graphs (one filter, two named filters, a filter
#      feeding a counter and a trigger, an object watch with class wires)
#      compile exactly as before (tests/golden_classfilter_switch.json,
#      captured before the change); a node without the field is On
#   2. Off is the card's existing PAUSE: the node's `running: false` makes
#      the applied [class_filters] paused list hold it -- the app's fast path
#      reads that -- and On takes it out again; the compile itself is not
#      otherwise changed
#   3. the card's switch writes the NODE (running), stamped like a Builder
#      edit, so a canvas tab opened earlier cannot undo it while a change on
#      the canvas itself still wins; it writes nothing else of the workflow
#   4. a system paused BEFORE the switch existed: the pause is adopted onto
#      the node when the canvas opens, once, so the two agree
#   5. round trip: card Off -> node field -> recompile + apply -> app config
#      paused; a workflow save with the node Off keeps the card Off; the
#      canvas switch On -> the card reads Running
#
#   py -3 tests/smoke_classfilter_switch.py
#   py -3 tests/smoke_classfilter_switch.py --write-golden   (only before a change)
import copy
import io
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                         # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_classfilter_switch.json")
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, ntype, data, x=0):
    return {"id": nid, "type": ntype, "position": {"x": x, "y": 0}, "data": data}


def e(eid, src, tgt, sh=None, th=None):
    d = {"id": eid, "source": src, "target": tgt}
    if sh:
        d["sourceHandle"] = sh
    if th:
        d["targetHandle"] = th
    return d


ZONES = [{"id": "z1", "name": "Bay", "kind": "area", "enabled": True}]


def graphs(extra=None):
    extra = extra or {}
    one = {"nodes": [node("s", "source.detection", {}),
                     node("f", "filter.class", {"classes": ["person"], **extra}),
                     node("c", "analytics.counter", {"fnId": "people", "name": "People"})],
           "edges": [e("e1", "s", "f", "on"), e("e2", "f", "c", "on")]}
    two = {"nodes": [node("s", "source.detection", {}),
                     node("fa", "filter.class", {"classes": ["person"], "name": "people", **extra}),
                     node("fb", "filter.class", {"classes": ["chair"], "name": "chairs"}),
                     node("ca", "analytics.counter", {"fnId": "pa", "name": "People here"}),
                     node("cb", "analytics.counter", {"fnId": "cb", "name": "Chairs here"}),
                     node("b", "branch.cases", {"rules": [{"op": ">", "value": 3, "input": "in1"}]}),
                     node("t", "output.trigger", {"name": "Crowd", "alert": True}),
                     node("z", "zone.ref", {"zone": "z1"})],
           "edges": [e("e1", "s", "fa", "on"), e("e2", "s", "fb", "on"),
                     e("e3", "fa", "ca", "on"), e("e4", "fb", "cb", "on"),
                     e("e5", "z", "ca", None, "zone"), e("e6", "ca", "b"),
                     e("e7", "b", "t", "out0")]}
    watch = {"nodes": [node("s", "source.detection", {}),
                       node("f", "filter.class", {"classes": ["person", "helmet"], "name": "ppe", **extra}),
                       node("o", "analytics.object", {"fnId": "ppe", "name": "PPE"})],
             "edges": [e("e1", "s", "f", "on"), e("e2", "f", "o", "on")]}
    return {"one filter": one, "two named filters, counters, zone, trigger": two,
            "object watch with class wires": watch}


def compile_all(extra=None):
    out = {}
    for name, g in graphs(extra).items():
        c = wf.compile_graph(wf.clean_graph(copy.deepcopy(g)), ZONES)
        keep = {k: c.get(k) for k in ("functions", "outputs", "class_filters", "errors", "widgets")}
        out[name] = json.loads(json.dumps(keep, sort_keys=True, default=str))
    return out


if "--write-golden" in sys.argv:
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(compile_all(), f, indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

# -- 1. golden ------------------------------------------------------------------------
golden = json.load(io.open(GOLDEN, encoding="utf-8"))
now = compile_all()
for name in sorted(golden):
    check(f"{name}: compiles exactly as before the switch", now.get(name) == golden[name],
          json.dumps({"want": golden[name].get("class_filters"), "got": (now.get(name) or {}).get("class_filters")})[:400])
def without_running(doc):
    doc = copy.deepcopy(doc)
    for entry in doc.get("class_filters") or []:
        entry.pop("running", None)
    return doc


for extra, words in (({"running": True}, "On"), ({"running": False}, "Off")):
    got = compile_all(extra)
    for name in sorted(golden):
        check(f"{name}: an explicit {words} changes nothing but the plan's own running flag",
              without_running(got[name]) == golden[name])

# -- 2..5. one state, end to end ----------------------------------------------------------
from core.services import class_filter as cf                      # noqa: E402
from core.services import detector_control as dc                  # noqa: E402

SYSTEMS = tempfile.mkdtemp()
SLUG = "line_a"
PKG = os.path.join(SYSTEMS, SLUG)
os.makedirs(os.path.join(PKG, "data"), exist_ok=True)
with open(os.path.join(PKG, "config.toml"), "w", encoding="utf-8") as fh:
    fh.write('[detector]\nkind = "hailo"\n')
g2 = graphs()["two named filters, counters, zone, trigger"]
store = {"version": 3, "workflows": [{"id": "main", "name": "Main", "enabled": True, "graph": g2}]}


def apply(st):
    c = wf.compile_store(copy.deepcopy(st), ZONES)
    cf.apply(SYSTEMS, SLUG, c)
    dc._toml_cache.pop(dc._local_path(PKG), None)
    return cf.table(SYSTEMS, SLUG)["paused"]


def node_of(st, nid):
    return next(n for n in st["workflows"][0]["graph"]["nodes"] if n["id"] == nid)


check("a workflow nobody switched: nothing paused", apply(store) == [])
# The dashboard card switches "people" Off: the paused list (the app's fast
# path) and the NODE.
cf.set_running(SYSTEMS, SLUG, "fa", False)
dc._toml_cache.pop(dc._local_path(PKG), None)
changed = cf.set_node_running(store, "fa", False)
data = node_of(store, "fa")["data"]
check("the card's Off pauses at once (the app's paused list) AND lands on the node",
      cf.table(SYSTEMS, SLUG)["paused"] == ["fa"] and changed and data["running"] is False)
check("...stamped like a Builder edit", wf.BUILDER_EDIT_KEY in data
      and "running" in data[wf.BUILDER_EDIT_KEY]["was"])
check("...and nothing else of the workflow changed",
      node_of(store, "fb")["data"] == {"classes": ["chair"], "name": "chairs"}
      and set(data) == {"classes", "name", "running", wf.BUILDER_EDIT_KEY})
check("the recompile rebuilds the paused list from the node: still paused", apply(store) == ["fa"])
# A canvas tab opened before the dashboard switch saves its stale graph.
stale = copy.deepcopy(g2)
wf.keep_builder_edits(stale, store["workflows"][0]["graph"])
check("a stale canvas tab cannot switch it back On",
      next(n for n in stale["nodes"] if n["id"] == "fa")["data"].get("running") is False)
# The canvas itself switches it On (an edit on top of the stored stamp).
fresh = copy.deepcopy(store["workflows"][0]["graph"])
next(n for n in fresh["nodes"] if n["id"] == "fa")["data"]["running"] = True
wf.keep_builder_edits(fresh, store["workflows"][0]["graph"])
store2 = {**store, "workflows": [{**store["workflows"][0], "graph": fresh}]}
check("...while the canvas switch On wins, and the applied list follows it",
      node_of(store2, "fa")["data"]["running"] is True and apply(store2) == [])
check("...so the dashboard card reads Running on its next poll",
      not any(p == "fa" for p in cf.table(SYSTEMS, SLUG)["paused"]))
# A workflow saved with the node Off keeps the card Off.
off = copy.deepcopy(store2)
node_of(off, "fb")["data"]["running"] = False
check("a workflow saved with a node Off keeps the card paused", apply(off) == ["fb"])
# A pause set BEFORE the switch existed (no field on the node).
legacy = copy.deepcopy(store)
node_of(legacy, "fa")["data"].pop("running")
node_of(legacy, "fa")["data"].pop(wf.BUILDER_EDIT_KEY)
apply(legacy)
cf.set_running(SYSTEMS, SLUG, "fb", True)      # clear the earlier save's pause
cf.set_running(SYSTEMS, SLUG, "fa", False)
dc._toml_cache.pop(dc._local_path(PKG), None)
check("a legacy pause (node silent) is kept by a recompile", apply(legacy) == ["fa"])
graph = legacy["workflows"][0]["graph"]
check("...and adopted onto the node when the canvas opens, once",
      cf.adopt_paused(graph, SYSTEMS, SLUG) and node_of(legacy, "fa")["data"]["running"] is False
      and not cf.adopt_paused(graph, SYSTEMS, SLUG))
try:
    cf.set_node_running(store, "ca", False)
    check("...a counter cannot be switched through the card", False)
except ValueError:
    check("...a counter cannot be switched through the card", True)
src = open(os.path.join(ROOT, "core", "routes", "class_filter.py"), encoding="utf-8").read()
check("the card's route keeps its gate (workflow.edit) and writes only the node's running",
      'perm.require(request, "workflow.edit")' in src and "set_node_running(store, node" in src)

print(f"{bad} check(s) failed" if bad else "all good")
sys.exit(1 if bad else 0)
