# Object Detection knows WHICH object it found, and can hand the id on.
#
# Tok Aba: "please add OID for Object Detection nodes". The counters, the
# line counter and Cycle Time already carry an OID switch: ticked, the node
# grows an OID output and a Data Table wired to it writes one row per object,
# with that object's id. Object Detection now does the same -- one row per
# ARRIVAL (a tracked object that was not there a moment ago), carrying its
# track id and its class, through exactly the path Cycle Time's stamped
# stays use: params.stamp_events, an info event per object naming it in
# detail.track, and the Data Table arming on those events by itself.
#
# What is pinned here:
#   1. the switch exists (off by default) and is drawn where the others are
#   2. OID reaches the rule as stamp_events; off, the rule is unchanged
#   3. an OID wire into a Data Table column records the object id as text,
#      and the table fires on the arrivals with no Alert needed
#   4. the OID output is refused anywhere but a table column, as elsewhere
#   5. the app: one event per arrival, none for an object standing still,
#      none for an untracked detection, none with OID off; the row's
#      track_id and class resolve off it
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fn_service               # noqa: E402
from core.services import workflow as wf                        # noqa: E402
from app import functions as fx                                 # noqa: E402
from app import outputs as app_outputs                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


COLUMNS = [{"name": "who", "type": "number"}, {"name": "what", "type": "text"}]
TABLES = {"arrivals": {"key": "arrivals", "name": "Arrivals", "columns": COLUMNS}}


def graph(oid=True, into="table"):
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "cls", "type": "filter.class", "position": {"x": 1, "y": 0},
         "data": {"classes": ["person", "forklift"]}},
        {"id": "ob", "type": "analytics.object", "position": {"x": 2, "y": 0},
         "data": {**wf.REGISTRY["analytics.object"]["data"],
                  "fnId": "door", "name": "Door watch", "OID": oid}},
    ]
    edges = [{"id": "e1", "source": "s", "target": "cls"},
             {"id": "e2", "source": "cls", "target": "ob"}]
    if into == "table":
        nodes.append({"id": "ds", "type": "output.dataset",
                      "position": {"x": 4, "y": 0},
                      "data": {"dataset": "arrivals", "name": "arrivals",
                               "mode": "alert",
                               "sources": {"what": {"src": "event",
                                                    "field": "detection_label"}}}})
        edges.append({"id": "e3", "source": "ob", "target": "ds",
                      "sourceHandle": "OID", "targetHandle": "c_who"})
    elif into == "card":
        nodes.append({"id": "cd", "type": "dashboard.card",
                      "position": {"x": 4, "y": 0},
                      "data": dict(wf.REGISTRY["dashboard.card"]["data"])})
        edges.append({"id": "e3", "source": "ob", "target": "cd",
                      "sourceHandle": "OID"})
    return {"nodes": nodes, "edges": edges}


def compile_(g):
    return wf.compile_graph(wf.clean_graph(g), [], tables=TABLES)


# -- 1. the switch ----------------------------------------------------------------
spec = wf.REGISTRY["analytics.object"]
check("Object Detection carries an OID switch, off by default",
      spec["data"].get("OID") is False, spec["data"])
page = read("frontend", "src", "pages", "Workflow.jsx")
check("...drawn with the others (the OID tick on the title row)",
      '"analytics.object"]);' in page
      and page.index('"analytics.entry_exit",') < page.index('"analytics.object"]);'))
nodes_js = read("frontend", "src", "workflow", "nodes.jsx")
check("...and the node grows an OID port beside its class ports",
      'props.type === "analytics.object" && d.OID' in nodes_js
      and '[{ id: "OID", label: "OID" }] : [])]' in nodes_js)

# -- 2. into the rule ----------------------------------------------------------------
c = compile_(graph(oid=False, into=None))
fn = next(f for f in c["functions"] if f["id"] == "door")
check("OID off: the rule is exactly what it was", "stamp_events" not in fn["params"],
      fn["params"])
c = compile_(graph(oid=True, into=None))
fn = next(f for f in c["functions"] if f["id"] == "door")
check("OID on: the rule stamps an event per arrival",
      fn["params"].get("stamp_events") is True, fn["params"])
check("...and the validator keeps it",
      fn_service.validate_function(fn)["params"].get("stamp_events") is True)

# -- 3. into a Data Table ------------------------------------------------------------
c = compile_(graph())
check("a Data Table on the OID port compiles", not c["errors"], c["errors"])
doc = next(o for o in c["outputs"] if o["type"] == "dataset")
cols = {x["name"]: x for x in doc["columns"]}
check("the OID column records the object's track id, as text",
      cols["who"]["src"] == "event" and cols["who"]["field"] == "track_id"
      and cols["who"]["type"] == "text", cols["who"])
check("the table fires on the arrivals themselves, no Alert needed",
      doc["trigger"] == "info" and doc["function"] == "door", doc)

# -- 4. nowhere else ------------------------------------------------------------------
c = compile_(graph(into="card"))
check("an OID wire into a card is refused, as for every OID port",
      any("OID output" in e["message"] for e in c["errors"]), c["errors"])

# -- 5. the app ---------------------------------------------------------------------
def engine(stamp=True):
    f = fx.Function({"id": "door", "name": "Door watch", "type": "object_detect",
                     "enabled": True,
                     "params": {"classes": ["person", "forklift"],
                                **({"stamp_events": True} if stamp else {})}})
    return fx.FunctionEngine([f])


def frame(eng, dets, now):
    return eng.update({"crossing_events": []}, dets, {}, {}, 640, 480, now=now)


def det(tid, label="person"):
    d = {"label": label, "confidence": 0.9, "box": [10, 10, 50, 90]}
    if tid is not None:
        d["track_id"] = tid
    return d


eng = engine()
ev1 = frame(eng, [det(7)], 100.0)
ev2 = frame(eng, [det(7)], 100.1)
ev3 = frame(eng, [det(7), det(9, "forklift"), det(None)], 100.2)
arrivals = [e for e in ev1 + ev2 + ev3 if e.get("type") == "info"]
check("one event per arrival: object 7, then object 9",
      [e["detail"]["track"] for e in arrivals] == [7, 9], arrivals)
check("...none for 7 standing still, none for an untracked detection",
      len(arrivals) == 2)
check("...each naming its object and its class",
      arrivals[1]["source_oid"] == 9 and arrivals[1]["detail"]["label"] == "forklift",
      arrivals[1])
check("...and found/arrived publish as before",
      eng.stats()["fn_door_found"] == 1 and eng.stats()["fn_door_arrived"] == 1,
      eng.stats())
row_id = app_outputs._event_field(arrivals[1], "track_id")
check("the Data Table row reads the arrival's object id",
      str(row_id).endswith("-9"), row_id)
check("...and its class",
      app_outputs._event_field(arrivals[1], "detection_label") == "forklift")
eng = engine(stamp=False)
quiet = frame(eng, [det(7)], 100.0) + frame(eng, [det(8)], 100.1)
check("OID off: no events at all", [e for e in quiet if e.get("type") == "info"] == [],
      quiet)

# -- review fixes (Yaya, probe_oid) ---------------------------------------------------
def engine2():
    f = fx.Function({"id": "door2", "name": "Door watch", "type": "object_detect",
                     "enabled": True,
                     "params": {"classes": ["person", "cell phone"],
                                "stamp_events": True}})
    return fx.FunctionEngine([f])


def arrivals_of(eng, dets, now):
    return [e for e in frame(eng, dets, now) if e.get("type") == "info"]


fx._objdet_seen.clear()
e1 = engine2()
first = arrivals_of(e1, [det(7), det(8, "cell phone")], 200.0)
first += arrivals_of(e1, [det(7), det(8, "cell phone")], 200.1)
check("R1 two objects, two frames: two rows",
      [e["detail"]["track"] for e in first] == [7, 8], first)
check("R1 ...the class recorded as the model names it ('cell phone')",
      app_outputs._event_field(first[1], "detection_label") == "cell phone",
      first[1])
e2 = engine2()                       # a workflow save rebuilds the engine
again = arrivals_of(e2, [det(7), det(8, "cell phone")], 200.2)
check("R1 a hot reload does not re-announce the objects already in view",
      again == [], again)
fx._objdet_seen.clear()
e3 = engine2()
blink = (arrivals_of(e3, [det(7)], 300.0) + arrivals_of(e3, [], 300.1)
         + arrivals_of(e3, [det(7)], 300.2))
check("R1 a one-frame tracker blink is one row, not two",
      len(blink) == 1, blink)
back = arrivals_of(e3, [], 301.0) + arrivals_of(e3, [det(7)], 302.5)
check("R1 ...while an object gone for longer than the gap arrives again",
      len(back) == 1, back)
check("R1 the 'seen' memory is module-level and bounded",
      fx.ARRIVE_GAP_S >= 1.0 and fx.SEEN_KEEP_S > fx.ARRIVE_GAP_S
      and "_objdet_seen = {}" in read("systems", "demo", "app", "functions.py"))

# -- per camera (Boboiboy, 2026-10-05) ---------------------------------------------
# A rule on EVERY camera runs in each camera's engine, and every camera's
# tracker numbers its objects from 1. The seen memory was keyed by rule
# alone, so object 7 arriving on camera 2 read as camera 1's object 7
# standing still: no event, no row. run.py hands each engine its camera id
# as the scope (the way the Trigger's once-per-object memory is kept apart).
fx._objdet_seen.clear()


def cam_engine(scope):
    f = fx.Function({"id": "door2", "name": "Door watch", "type": "object_detect",
                     "enabled": True,
                     "params": {"classes": ["person"], "stamp_events": True}})
    return fx.FunctionEngine([f], scope=scope)


cam1, cam2 = cam_engine("cam1"), cam_engine("cam2")
rows = arrivals_of(cam1, [det(7)], 400.0) + arrivals_of(cam2, [det(7)], 400.05)
check("R2 the same object id on two cameras is two arrivals, one each",
      [e["detail"]["track"] for e in rows] == [7, 7], rows)
check("R2 ...remembered per camera", set(fx._objdet_seen)
      == {("cam1", "door2"), ("cam2", "door2")}, repr(set(fx._objdet_seen)))
again = arrivals_of(cam_engine("cam2"), [det(7)], 400.1)
check("R2 ...and a reload on one camera still re-announces nothing there",
      again == [], again)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all object OID checks passed")
