# One Data Table row per line crossing, with the direction on the row.
#
# Reported from the live People Counting board (wf_5126): one person through
# the door wrote TWO rows at the same second, and the `status` column
# disagreed with in/out. Both are the mode working as written. The counter's
# live In/Out is a LEVEL -- 1 while the object is over the line, 0 after --
# and "when a value changes" records both edges. The status came from a
# variable set by a Branch, evaluated a pass after the counter it read, so it
# said what the previous frame's counter said.
#
# The runtime already raised one type "info" event per confirmed crossing
# with its direction (zones.py crossing_events -> run.py _gated_events ->
# outputs.fire once per event). What was missing was a way for a table to be
# armed by those events: alert mode demanded a rule with a trigger and bound
# `function`, which a crossing never carries. So a fourth mode, `crossing`,
# binds the document to a LINE id and rides that path -- and never the clock,
# so no change detector exists on its path to swallow a second person through
# the same door in the same frame.
#
# The compile half pins the document the canvas produces; the runtime half
# drives fire()/publish() with events built exactly as run.py builds them.
import io
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import workflow as wf                       # noqa: E402
from core.services import detector_control as dc               # noqa: E402
from app import outputs as out                                 # noqa: E402
from app import run as apprun                                  # noqa: E402
from app import functions as fx                                # noqa: E402


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# =============================================================================
# compile: what the canvas turns into an [[outputs]] document
# =============================================================================
ZONES = [{"id": "line1", "name": "Door", "kind": "line", "enabled": True,
          "points": [[0.5, 0.0], [0.5, 1.0]]},
         {"id": "line2", "name": "Gate", "kind": "line", "enabled": True,
          "points": [[0.2, 0.0], [0.2, 1.0]]}]
TABLES = {"people_counting": {"key": "people_counting", "name": "People counting",
                              "columns": [{"name": "in", "type": "number"},
                                          {"name": "out", "type": "number"},
                                          {"name": "status", "type": "text"}]}}


def graph(mode="crossing", counter=None, sources=None, wires=(("in", "in"), ("out", "out")),
          filtered=True, extra_nodes=(), extra_edges=()):
    """Detection -> (Class Filter person) -> Zone line1 -> Line Counter `lc`
    -> Data Table people_counting, with `wires` from the counter's ports onto
    the table's columns and `sources` for the unwired ones."""
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "zn", "type": "zone.ref", "position": {"x": 2, "y": 0}, "data": {"zone": "line1"}},
        {"id": "cnt", "type": "analytics.entry_exit", "position": {"x": 3, "y": 0},
         "data": {"fnId": "lc", "name": "Line counter", "mode": "both",
                  "momentary": True, **(counter or {})}},
        {"id": "db", "type": "output.dataset", "position": {"x": 5, "y": 0},
         "data": {"dataset": "people_counting", "name": "People counting",
                  "mode": mode, "sources": sources or {}}},
    ]
    edges = [{"id": "e2", "source": "zn", "target": "cnt"}]
    if filtered:
        nodes.insert(1, {"id": "cls", "type": "filter.class", "position": {"x": 1, "y": 0},
                         "data": {"classes": ["person"]}})
        edges += [{"id": "e0", "source": "src", "target": "cls"},
                  {"id": "e1", "source": "cls", "target": "zn"}]
    else:
        edges.append({"id": "e1", "source": "src", "target": "zn"})
    for column, port in wires:
        edges.append({"id": f"w{column}", "source": "cnt", "target": "db",
                      "targetHandle": f"c_{column}", "sourceHandle": port})
    nodes += list(extra_nodes)
    edges += list(extra_edges)
    return {"nodes": nodes, "edges": edges}


def compile_(g):
    return wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)


def datasets(res):
    return [o for o in res["outputs"] if o["type"] == "dataset"]


STATUS = {"status": {"src": "event", "field": "direction"}}

# -- 1. the document a crossing table compiles to ----------------------------
res = compile_(graph(sources=STATUS))
assert not res["errors"], res["errors"]
docs = datasets(res)
assert len(docs) == 1, docs
doc = docs[0]
assert doc["mode"] == "crossing" and doc["trigger"] == "info", doc
assert doc["line"] == "line1", doc
assert doc.get("classes") == ["person"], doc
assert "function" not in doc, doc
assert "interval_s" not in doc and "min_interval_s" not in doc, doc
cols = {c["name"]: c for c in doc["columns"]}
assert cols["in"]["src"] == "wire" and cols["in"]["key"] == "fn_lc_in", cols
assert cols["out"]["src"] == "wire" and cols["out"]["key"] == "fn_lc_out", cols
assert cols["status"] == {"name": "status", "type": "text", "src": "event",
                          "field": "direction"}, cols["status"]
print("== a crossing table: one doc, trigger info, bound to the LINE, no function")

# -- 2. the operator's existing table is untouched, and told ------------------
res = compile_(graph(mode="change", sources=STATUS))
assert not res["errors"], res["errors"]
doc = datasets(res)[0]
assert doc["mode"] == "change" and doc["trigger"] == "any", doc
assert doc["interval_s"] == 60 and doc["min_interval_s"] == 0, doc
assert "line" not in doc and "classes" not in doc, doc
notes = [w["message"] for w in res["warnings"] if w["node"] == "db"]
assert any("each time something crosses a line" in m for m in notes), notes
print("== on-change compiles exactly as before, with the two-rows warning")

res = compile_(graph(mode="change", sources=STATUS, counter={"momentary": False}))
notes = [w["message"] for w in res["warnings"] if w["node"] == "db"]
assert not any("each time something crosses a line" in m for m in notes), notes
print("== ...and a running-total counter on change gets no such warning")

# -- 3. one doc per LINE, not per counter --------------------------------------
second = [{"id": "zn2", "type": "zone.ref", "position": {"x": 2, "y": 1}, "data": {"zone": "line1"}},
          {"id": "cnt2", "type": "analytics.entry_exit", "position": {"x": 3, "y": 1},
           "data": {"fnId": "lc2", "name": "Second counter", "mode": "both"}}]
same_line = compile_(graph(sources=STATUS, extra_nodes=second, extra_edges=[
    {"id": "x1", "source": "cls", "target": "zn2"},
    {"id": "x2", "source": "zn2", "target": "cnt2"},
    {"id": "x3", "source": "cnt2", "target": "db", "targetHandle": "c_status",
     "sourceHandle": "cross"}]))
assert not same_line["errors"], same_line["errors"]
assert len(datasets(same_line)) == 1, datasets(same_line)
print("== two Line Counters on ONE line write one row per crossing, not two")

second[0]["data"] = {"zone": "line2"}
two_lines = compile_(graph(sources=STATUS, extra_nodes=second, extra_edges=[
    {"id": "x1", "source": "cls", "target": "zn2"},
    {"id": "x2", "source": "zn2", "target": "cnt2"},
    {"id": "x3", "source": "cnt2", "target": "db", "targetHandle": "c_status",
     "sourceHandle": "cross"}]))
assert not two_lines["errors"], two_lines["errors"]
ds = datasets(two_lines)
assert [d["id"] for d in ds] == ["wf_db", "wf_db_1"], [d["id"] for d in ds]
assert sorted(d["line"] for d in ds) == ["line1", "line2"], ds
assert "Gate" in ds[1]["label"], ds[1]["label"]
print("== two lines into one table: one doc each, the second named for its line")

# -- 4. the class filter is mirrored only when every counter has one ---------
second[0]["data"] = {"zone": "line1"}
mixed = compile_(graph(sources=STATUS, extra_nodes=second, extra_edges=[
    {"id": "x1", "source": "src", "target": "zn2"},          # unfiltered
    {"id": "x2", "source": "zn2", "target": "cnt2"},
    {"id": "x3", "source": "cnt2", "target": "db", "targetHandle": "c_status",
     "sourceHandle": "cross"}]))
assert not mixed["errors"], mixed["errors"]
assert "classes" not in datasets(mixed)[0], datasets(mixed)[0]
print("== an unfiltered counter on the line means every class is recorded")

# -- 5. the trigger port wins completely, as it does in alert mode ------------
second[0]["data"] = {"zone": "line2"}
trig = compile_(graph(sources=STATUS, extra_nodes=second, extra_edges=[
    {"id": "x1", "source": "cls", "target": "zn2"},
    {"id": "x2", "source": "zn2", "target": "cnt2"},
    {"id": "x3", "source": "cnt2", "target": "db", "targetHandle": "trig"}]))
assert not trig["errors"], trig["errors"]
ds = datasets(trig)
assert len(ds) == 1 and ds[0]["line"] == "line2", ds
print("== a Line Counter on the trigger port names the line; column wires are values")

# -- 6. nothing that crosses -> an error that names what to wire --------------
frame = compile_(graph(sources=STATUS, wires=(), extra_nodes=[
    {"id": "fc", "type": "analytics.counter", "position": {"x": 3, "y": 1},
     "data": {"fnId": "frame", "name": "Frame count"}}],
    extra_edges=[{"id": "f1", "source": "src", "target": "fc"},
                 {"id": "f2", "source": "fc", "target": "db", "targetHandle": "c_in",
                  "sourceHandle": "count"}]))
msgs = [e["message"] for e in frame["errors"] if e["node"] == "db"]
assert any("no Line Counter" in m for m in msgs), (msgs, frame["warnings"])
print("== a crossing table fed by anything but a Line Counter says so")

# -- 7. a Delay between counter and table is carried ---------------------------
delayed = compile_(graph(sources=STATUS, wires=(), extra_nodes=[
    {"id": "dl", "type": "logic.delay", "position": {"x": 4, "y": 0}, "data": {"delay_s": 3}}],
    extra_edges=[{"id": "d1", "source": "cnt", "target": "dl"},
                 {"id": "d2", "source": "dl", "target": "db", "targetHandle": "trig"}]))
assert not delayed["errors"], delayed["errors"]
doc = datasets(delayed)[0]
assert doc["line"] == "line1" and doc.get("delay_s") == 3.0, doc
print("== a Delay upstream holds the row, and the line still resolves through it")

# -- 8. the field lists, both sides -------------------------------------------
assert "direction" in wf.DATASET_EVENT_FIELDS
page = read("frontend", "src", "pages", "Workflow.jsx")
assert '["direction",' in page
block = page[page.index("const DATASET_EVENT_FIELDS = ["):]
block = block[:block.index("];")]
client = set(re.findall(r'\["(\w+)",', block))
assert client == set(wf.DATASET_EVENT_FIELDS) - {"in_zone_s"}, \
    (sorted(client ^ (set(wf.DATASET_EVENT_FIELDS) - {"in_zone_s"})))
print("== the client field list mirrors the server's, in_zone_s excepted on purpose")
assert 'value="crossing"' in page and "each time something crosses a line" in page
print("== the Write a row menu offers the mode")

# -- 9. the validator keeps what a crossing doc needs and nothing else --------
base = {"id": "wf_t", "type": "dataset", "enabled": True, "trigger": "info",
        "dataset": "people_counting", "name": "People",
        "columns": [{"name": "status", "type": "text", "src": "event",
                     "field": "direction"}]}
kept = dc._validate_output({**base, "mode": "crossing", "line": "line1",
                            "classes": ["person", "car"]})
assert kept["mode"] == "crossing" and kept["line"] == "line1", kept
assert kept["classes"] == ["person", "car"], kept
assert "interval_s" not in kept and "min_interval_s" not in kept, kept
try:
    dc._validate_output({**base, "mode": "crossing"})
    raise AssertionError("a crossing doc without a line was accepted")
except ValueError as exc:
    assert "line" in str(exc), exc
alert = dc._validate_output({**base, "mode": "alert", "trigger": "alert",
                             "line": "line1", "classes": ["person"]})
assert "line" not in alert and "classes" not in alert, alert
assert "interval_s" not in alert and "min_interval_s" not in alert, alert
for mode in ("every", "change"):
    clock = dc._validate_output({**base, "mode": mode, "trigger": "any",
                                 "interval_s": 30, "min_interval_s": 2})
    assert clock["interval_s"] == 30.0 and clock["min_interval_s"] == 2.0, clock
    assert "line" not in clock, clock
assert dc._validate_output({**base, "mode": "nonsense"})["mode"] == "alert"
print("== _validate_output: line/classes for crossing only, clocks for every/change only")


# =============================================================================
# runtime: the rows the app writes
# =============================================================================
DOC = {"id": "wf_t", "type": "dataset", "enabled": True, "mode": "crossing",
       "trigger": "info", "line": "door", "classes": ["person"],
       "dataset": "people_counting", "name": "People counting",
       "columns": [
           {"name": "in", "type": "number", "src": "wire", "key": "fn_lc_in"},
           {"name": "out", "type": "number", "src": "wire", "key": "fn_lc_out"},
           {"name": "status", "type": "text", "src": "event", "field": "direction"},
           {"name": "who", "type": "text", "src": "event", "field": "track_id"},
           {"name": "when", "type": "datetime", "src": "event", "field": "ts"},
       ]}


class Pipe:
    id = "cam1"
    gates = {}

    def label(self):
        return "cam1"


DETS = [{"label": "person", "confidence": 0.9, "track_id": 7, "box": [1, 2, 3, 4]},
        {"label": "person", "confidence": 0.8, "track_id": 8, "box": [1, 2, 3, 4]},
        {"label": "person", "confidence": 0.7, "track_id": 9, "box": [1, 2, 3, 4]}]


def events(result, dets=DETS, counts=None):
    """Build events exactly as run.py's loop does: _gated_events, then
    _make_event, then the detail stamping with None values dropped."""
    pipe = Pipe()
    pipe.gates = {}
    made = []
    for seq, (kind, relevant, extra) in enumerate(
            apprun._gated_events(pipe, dets, result, 1000.0, 60.0, 2.0), 1):
        ev = apprun._make_event(kind, relevant, seq)
        ev["detail"]["counts"] = counts or {}
        ev["detail"]["camera"] = "cam1"
        ev["detail"]["camera_id"] = "cam1"
        ev["detail"]["model"] = "yolov8n"
        for key, value in (extra or {}).items():
            if value is not None:
                ev["detail"][key] = value
        made.append(ev)
    return made


def collect():
    rows = []
    return rows, {"dataset": lambda o, cols: rows.append(cols)}


def crossing(zone, way, tid, label="person"):
    return {"zone": zone, "direction": way, "label": label, "track_id": tid,
            "model": "yolov8n"}


RESULT = {"violations": [],
          "crossing_events": [crossing("door", "in", 7), crossing("door", "in", 8),
                              crossing("aisle", "out", 9)],
          "crossings": {"door": {"name": "Door", "in": 2, "out": 0, "cross": 0},
                        "aisle": {"name": "Aisle", "in": 0, "out": 1, "cross": 0}}}
STATS = {"fn_lc_in": 2, "fn_lc_out": 0}

evs = events(RESULT)
crossed = [e for e in evs if e["detail"].get("direction")]
assert len(crossed) == 3, [e["label"] for e in evs]
assert [e["detail"].get("track") for e in crossed] == [7, 8, 9], \
    [e["detail"].get("track") for e in crossed]
print("== every crossing event names its object as detail.track")

# -- two people through one door in one frame: two rows, no comparison --------
rows, hooks = collect()
for e in evs:
    out.fire([DOC], e, hooks, STATS)
assert len(rows) == 2, rows
assert all(r["status"] == "in" and r["in"] == 2.0 and r["out"] == 0.0 for r in rows), rows
assert sorted(r["who"] for r in rows) == [f"{out._RUN_ID}-7", f"{out._RUN_ID}-8"], rows
# Stamped per event with microseconds, so "the same second" is the claim.
assert rows[0]["when"] and rows[0]["when"][:19] == rows[1]["when"][:19], rows
print("== two crossings in one frame are two rows -- both written, nothing compared")
print("   (the aisle crossing wrote nothing: the row is bound to the door)")

# -- what does NOT write a row -------------------------------------------------
rows, hooks = collect()
car = events({"violations": [], "crossings": {},
              "crossing_events": [crossing("door", "in", 7, label="car")]},
             [{"label": "car", "confidence": 0.9, "track_id": 7, "box": [1, 2, 3, 4]}])
for e in car:
    out.fire([DOC], e, hooks, STATS)
assert rows == [], rows
print("== a car over a people line writes nothing (the counter's Class Filter)")

rows, hooks = collect()
out.fire([DOC], {"id": "v", "ts": "2026-09-24T10:00:00", "type": "violation",
                 "severity": "crit", "label": "object in keep-out zone",
                 "detail": {"zone": "door", "detections": DETS}}, hooks, STATS)
out.fire([DOC], {"id": "p", "ts": "2026-09-24T10:00:00", "type": "info",
                 "severity": "ok", "label": "person detected",
                 "detail": {"detections": DETS}}, hooks, STATS)
assert rows == [], rows
print("== a violation and a presence event write nothing (no direction)")

# -- an untracked crossing: one row, and NEVER a bystander's id ---------------
rows, hooks = collect()
loose = events({"violations": [], "crossings": {},
                "crossing_events": [crossing("door", "in", None)]},
               DETS[:2])
for e in loose:
    out.fire([DOC], e, hooks, STATS)
assert len(rows) == 1 and rows[0]["who"] is None and rows[0]["status"] == "in", rows
print("== a crossing the tracker could not name writes one row with a blank id")

# -- a line-scoped OID column (the counter's OID port) names the crosser ------
scoped = {**DOC, "columns": [{"name": "who", "type": "text", "src": "event",
                               "field": "track_id", "zone": "door"}]}
rows, hooks = collect()
for e in events({"violations": [], "crossings": {},
                 "crossing_events": [crossing("door", "out", 8)]}):
    out.fire([scoped], e, hooks, STATS)
assert rows == [{"who": f"{out._RUN_ID}-8"}], rows
print("== a line-scoped OID column resolves to the crosser (lines stamp no membership)")

# -- the clock never visits a crossing table -----------------------------------
rows, hooks = collect()
out.publish([DOC], {"fn_lc_in": 1}, 100.0, hooks=hooks, dets=DETS)
out.publish([DOC], {"fn_lc_in": 0}, 101.0, hooks=hooks, dets=DETS)
assert rows == [], rows
print("== no trailing 0 row: publish() ignores a crossing table")

# -- on change, byte-for-byte as today ----------------------------------------
CHANGE = {k: v for k, v in DOC.items() if k not in ("line", "classes")}
CHANGE.update({"id": "wf_c", "mode": "change", "trigger": "any",
               "interval_s": 60, "min_interval_s": 0,
               "columns": [DOC["columns"][0]]})
rows, hooks = collect()
for e in evs:
    out.fire([CHANGE], e, hooks, STATS)
assert rows == [], rows
out.publish([CHANGE], {"fn_lc_in": 1}, 200.0, hooks=hooks, dets=[])
out.publish([CHANGE], {"fn_lc_in": 0}, 201.0, hooks=hooks, dets=[])
assert [r["in"] for r in rows] == [1.0, 0.0], rows
print("== an on-change table still writes 1 then 0 -- the reported behaviour, unchanged")

# -- an alert-mode table bound to a function ignores crossings ----------------
ALERT = {**CHANGE, "id": "wf_a", "mode": "alert", "trigger": "info", "function": "other"}
rows, hooks = collect()
for e in evs:
    out.fire([ALERT], e, hooks, STATS)
assert rows == [], rows
print("== a function-bound alert table never fires on a crossing")

# -- a Delay delivers the row later, with the stats of the moment -------------
rows, hooks = collect()
# 0.3s, not 0.05s: "nothing yet" is asserted right after fire(), and on a
# loaded machine fire() returning can take longer than 50ms -- the row then
# arrives before the assert and the test fails on a delay that worked.
out.fire([{**DOC, "delay_s": 0.3}], crossed[0], hooks, STATS)
assert rows == [], rows
deadline = time.time() + 5.0
while not rows and time.time() < deadline:
    time.sleep(0.01)
assert len(rows) == 1 and rows[0]["status"] == "in" and rows[0]["in"] == 2.0, rows
print("== a delayed crossing row arrives, carrying the stats of the crossing")

# -- rows follow the LINE; the tally follows the counter's own setting --------
eng = fx.FunctionEngine([fx.Function({"id": "lc", "name": "lc", "type": "line_count",
                                      "enabled": True,
                                      "params": {"line": "door", "distinct": True}})])
rows, hooks = collect()
for frame in range(2):
    ev = crossing("door", "in", 1)
    eng.update({"crossing_events": [ev], "crossings": {}, "violations": [],
                "zones": {}, "in_frame": 1, "in_frame_by_label": {}},
               [], {}, {}, 640, 480)
    for e in events({"violations": [], "crossings": {}, "crossing_events": [ev]},
                    [{"label": "person", "confidence": 0.9, "track_id": 1,
                      "box": [1, 2, 3, 4]}]):
        out.fire([DOC], e, hooks, eng.stats())
assert eng.stats()["fn_lc_in"] == 1, eng.stats()
assert len(rows) == 2, rows
print("== each-object-once tallies 1 while the table records both crossings")

print("ALL OK")
