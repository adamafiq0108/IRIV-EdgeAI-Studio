# The Anomaly node: GOOD / NG for a Snapshot Detection Stream.
#
#   1. the node: one Verdict port, no per-class ports, a class filter upstream
#   2. the compile: Detection Stream -> Class Filter -> Anomaly -> Verdict Card
#      writes an object_detect rule flagged anomaly, a card reading its verdict,
#      with GOOD / NG as the card's words (unless the card's words were changed)
#   3. the engine: verdict 0 while nothing is found, 1 while something is
#   4. the gate: a snap is NG when ANY of its frames found something, the answer
#      is held between snaps, and nothing is published before the first snap
#   5. the loop in miniature: gate + engine, as run.py wires them
#
#     py -3 tests/smoke_anomaly.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import snapgate                                         # noqa: E402
from app.functions import Function, FunctionEngine               # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 1, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def edge(eid, src, dst, sh="", th="in"):
    e = {"id": eid, "source": src, "target": dst, "targetHandle": th}
    if sh:
        e["sourceHandle"] = sh
    return e


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], tables={})


# -- 1. the node ---------------------------------------------------------------
spec = wf.REGISTRY.get("analytics.anomaly") or {}
check("the registry has an Anomaly node", bool(spec))
check("...it publishes one Verdict port", spec.get("metrics") == ["verdict"], spec.get("metrics"))
check("...built on the object watch, so one engine rule serves it",
      spec.get("fn_type") == "object_detect")
check("...and the rule's outputs list the verdict",
      "verdict" in __import__("core.services.functions", fromlist=["x"]).OUTPUTS["object_detect"])

# -- 2. the compile ---------------------------------------------------------------
def graph(**card):
    return compile_(
        [node("d1", "source.detection", capture="snapshot"),
         node("f1", "filter.class", classes=["defect"]),
         node("a1", "analytics.anomaly"),
         node("c1", "dashboard.card", kind="verdict", **card)],
        [edge("e1", "d1", "f1"), edge("e2", "f1", "a1"),
         edge("e3", "a1", "c1", sh="verdict", th="in")])


g, out = graph()
check("Detection Stream -> Class Filter -> Anomaly -> Verdict Card compiles clean",
      not out["errors"], out["errors"])
fns = [f for f in out.get("functions") or [] if f.get("type") == "object_detect"]
check("...one object_detect rule, flagged as an anomaly watch",
      len(fns) == 1 and fns[0]["params"].get("anomaly") is True, fns)
check("...watching the filter's class", fns and fns[0]["params"].get("classes") == ["defect"], fns)
widgets = out.get("widgets") or []
card = next((w for w in widgets if w.get("kind") == "verdict"), {})
check("...a verdict card reading the rule's verdict", card.get("key", "").endswith("_verdict"), card)
words = {s.get("value"): s.get("label") for s in card.get("states") or []}
check("...that says GOOD for 0 and NG for 1", words == {0: "GOOD", 1: "NG"}, words)
tones = {s.get("value"): s.get("tone") for s in card.get("states") or []}
check("...GOOD calm, NG alerting", tones == {0: "ok", 1: "alert"}, tones)

g2, out2 = graph(ok_label="PASS", alert_label="REJECT")
card2 = next((w for w in out2.get("widgets") or [] if w.get("kind") == "verdict"), {})
check("words the user chose are kept",
      {s["value"]: s["label"] for s in card2.get("states") or []} == {0: "PASS", 1: "REJECT"}, card2)

g3, out3 = compile_(
    [node("d1", "source.detection"), node("f1", "filter.class", classes=["person"]),
     node("o1", "analytics.object"), node("c1", "dashboard.card", kind="verdict")],
    [edge("e1", "d1", "f1"), edge("e2", "f1", "o1"),
     edge("e3", "o1", "c1", sh="found", th="in")])
card3 = next((w for w in out3.get("widgets") or [] if w.get("kind") == "verdict"), {})
check("a verdict card behind any other node still says COMPLIANT / VIOLATION",
      {s["value"]: s["label"] for s in card3.get("states") or []}
      == {0: "COMPLIANT", 1: "VIOLATION"}, card3)

# -- the violation timer: how long something must stay before it counts ------------
def timed(ntype, secs):
    return compile_(
        [node("d1", "source.detection"), node("f1", "filter.class", classes=["defect"]),
         node("a1", ntype, enter_after_s=secs)],
        [edge("e1", "d1", "f1"), edge("e2", "f1", "a1")])[1]


for ntype in ("analytics.anomaly", "analytics.object"):
    got = [f for f in timed(ntype, 7).get("functions") or [] if f.get("type") == "object_detect"]
    check(f"the {ntype} timer reaches the rule as enter_after_s",
          got and got[0]["params"].get("enter_after_s") == 7.0, got)
    got = [f for f in timed(ntype, 0).get("functions") or [] if f.get("type") == "object_detect"]
    check(f"...and 0 means immediately ({ntype})",
          got and not got[0]["params"].get("enter_after_s"), got)

# -- 3. the engine --------------------------------------------------------------------
fn = Function({"id": "ng", "type": "object_detect", "name": "Anomaly",
               "params": {"anomaly": True}})
eng = FunctionEngine([fn], state_dir=None)
defect = [{"label": "defect", "box": [10, 10, 50, 50], "track_id": 1, "score": 0.9}]
eng.update({}, [], {}, {}, 640, 480, now=0)
check("nothing found: verdict 0 (GOOD)", eng.stats().get("fn_ng_verdict") == 0, eng.stats())
eng.update({}, defect, {}, {}, 640, 480, now=1)
check("something found: verdict 1 (NG)", eng.stats().get("fn_ng_verdict") == 1, eng.stats())
plain = Function({"id": "pl", "type": "object_detect", "name": "P", "params": {}})
eng2 = FunctionEngine([plain], state_dir=None)
eng2.update({}, defect, {}, {}, 640, 480, now=0)
check("an ordinary Object Detection publishes no verdict",
      "fn_pl_verdict" not in eng2.stats(), eng2.stats())

# -- 4. the gate ---------------------------------------------------------------------
K = "fn_ng_verdict"
g = snapgate.Gate({"sources": [{"kind": "inject", "key": "fn_kick_fired", "fn": "kick", "frames": 3}]})
check("before the first snap there is no answer", g.observe(False, {K: False}) == {})
g.update({"fn_kick_fired": 1})
g.take(3)                                         # all three frames owed are spent
check("mid-snap, still no answer", g.observe(True, {K: False}) == {})
check("a frame that found something makes the snap NG", g.observe(True, {K: True}) == {})
check("...even if the last frame found nothing", g.observe(True, {K: False}) == {})
check("the answer arrives once the snap is over: NG", g.observe(False, {K: False}) == {K: 1})
check("...and is held while the held result is re-fed",
      g.observe(False, {K: False}) == {K: 1} and g.observe(False, {K: True}) == {K: 1})
g.update({"fn_kick_fired": 1})
g.take(3)
g.observe(True, {K: False})
g.observe(True, {K: False})
check("the next snap, all clear, turns it GOOD", g.observe(False, {K: False}) == {K: 0})
g.update({"fn_kick_fired": 1})
check("a snap not yet taken does not close early", g.observe(False, {K: False}) == {K: 0})

# -- 5. the loop in miniature -------------------------------------------------------
print("== the loop in miniature")
gate = snapgate.Gate({"sources": [{"kind": "inject", "key": "fn_kick_fired", "fn": "kick", "frames": 1}]})
fn = Function({"id": "ng", "type": "object_detect", "name": "A", "params": {"anomaly": True}})
eng = FunctionEngine([fn], state_dir=None)
last = {"dets": []}


def one_pass(now, fresh, dets=None):
    """What run.py does for a Snapshot camera: a fresh frame brings its own
    detections, an idle pass is fed the last ones again."""
    if fresh:
        last["dets"] = dets or []
    eng.update({}, last["dets"], {}, {}, 640, 480, now=now)
    counts = dict(eng.stats())
    vkeys = [k for k in counts if k.startswith("fn_") and k.endswith("_verdict")]
    held = gate.observe(fresh, {k: counts[k] == 1 for k in vkeys})
    for k in vkeys:
        counts.pop(k, None)
    counts.update(held)
    return counts.get(K)


check("idle before any snap: no verdict published", one_pass(0, False) is None)
gate.update({"fn_kick_fired": 1})
gate.take(1)
check("the snap's own pass: still pending", one_pass(1, True, defect) is None)
check("the next idle pass: NG", one_pass(2, False) == 1)
check("...stays NG while idle", one_pass(3, False) == 1 and one_pass(4, False) == 1)
gate.update({"fn_kick_fired": 1})
gate.take(1)
one_pass(5, True, [])
check("a clean snap: GOOD", one_pass(6, False) == 0)

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
