"""The Line Counter knows WHICH object crossed, and can count each one once.

Two things, both asked for on a real door:

  * the OID port -- a line records its crossings against a track id, so it can
    hand that id to a Data Table and give you a row per person through the
    door instead of a number for the whole line;
  * "each object once" -- somebody pacing back and forth through a doorway all
    morning is one visitor in and one visitor out, not forty crossings. Off by
    default, because a turnstile really does want every crossing.

The two are independent: the events still name every crossing whichever way
the tally is counted, so an event log stays complete.

    python tests/smoke_lineoid.py
"""
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import functions as fn_service                # noqa: E402
from core.services import workflow as wf                         # noqa: E402
from app import functions as fx                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


ZONES = [{"id": "line1", "name": "Door", "kind": "line", "enabled": True,
          "points": [[0.5, 0.0], [0.5, 1.0]]}]


def compile_(data):
    graph = {"version": wf.SCHEMA_VERSION, "nodes": [
        {"id": "src", "type": "source.detection", "data": {}},
        {"id": "cls", "type": "filter.class", "data": {"classes": ["person"]}},
        {"id": "zn", "type": "zone.ref", "data": {"zone": "line1"}},
        {"id": "cnt", "type": "analytics.entry_exit",
         "data": {"fnId": "lc", "name": "Line counter", "mode": "both", **data}},
    ], "edges": [{"id": "e1", "source": "src", "target": "cls"},
                 {"id": "e2", "source": "cls", "target": "zn"},
                 {"id": "e3", "source": "zn", "target": "cnt"}]}
    out = wf.compile_graph(graph, zones=ZONES)
    assert not out["errors"], out["errors"]
    return next(f for f in out["functions"] if f["id"] == "lc")


# -- 1. the switch reaches the rule ------------------------------------------
check("a counter nobody asked carries no new param",
      "distinct" not in compile_({})["params"],
      "a rule that has never been asked for this must keep exactly the params "
      "it had, or every saved counter looks changed")
check("...and one that was asked for it says so",
      compile_({"distinct": True})["params"].get("distinct") is True)
check("the rule validates the param",
      fn_service.validate_function(
          {"id": "x", "name": "L", "type": "line_count",
           "params": {"line": "line1", "distinct": True}}
      )["params"].get("distinct") is True)

# -- 2. what it does to the tally --------------------------------------------
def run(params, crossings):
    doc = fx.Function({"id": "lc", "name": "L", "type": "line_count",
                       "enabled": True, "params": {"line": "line1", **params}})
    eng = fx.FunctionEngine([doc])
    for tid, way in crossings:
        eng.update({"crossing_events": [{"zone": "line1", "direction": way,
                                         "label": "person", "track_id": tid}]},
                   [], {}, {}, 640, 480)
    s = eng.stats()
    return {k.rsplit("_", 1)[-1]: v for k, v in s.items() if k.startswith("fn_lc_")}


PACING = [(7, "in"), (7, "out"), (7, "in"), (7, "out"), (7, "in")]
r = run({}, PACING)
check("every crossing counts, by default",
      (r["in"], r["out"], r["cross"]) == (3, 2, 5),
      f"got {r} -- a turnstile counts each pass")
r = run({"distinct": True}, PACING)
check("...and with the switch on, one object is one visitor each way",
      (r["in"], r["out"], r["cross"]) == (1, 1, 2),
      f"got {r}")

r = run({"distinct": True}, [(1, "in"), (2, "in"), (3, "in"), (1, "in")])
check("different objects still count separately",
      r["in"] == 3,
      "counting once must be per OBJECT, not once for the whole line")

r = run({"distinct": True}, [(None, "in"), (None, "in")])
check("a crossing with no object id is not counted once - it is not counted",
      r["in"] == 0,
      "with no id there is no way to tell this object from the last, and "
      "counting it anyway would defeat the setting on exactly the frames "
      "where tracking is struggling")

# -- 3. the OID port ----------------------------------------------------------
spec = wf.REGISTRY["analytics.entry_exit"]
check("the node offers the OID switch",
      spec["data"].get("OID") is False and spec["data"].get("distinct") is False,
      "off by default: a port that is always there is a port always in the way")

nodes = read("frontend", "src", "workflow", "nodes.jsx")
check("...and the port appears on the line counter when it is on",
      'id: "OID", label: "OID" }] : [])]' in nodes,
      "the tallies are one number for the line; the OID is one id per "
      "crossing, and only a Data Table column can hold it")
page = read("frontend", "src", "pages", "Workflow.jsx")
check("...with the checkbox that turns it on",
      # The set's LAST entry moved: Object Detection joined it after.
      '"analytics.entry_exit",' in page
      and "COUNTS_BY_PID" in page,
      "the OID tick is drawn for the node types that know their objects")
check("the inspector offers counting each object once",
      'node.type === "analytics.entry_exit" && (' in page
      and 'each object once (by OID)' in page)

# -- 4. an OID column from a line records THAT line ---------------------------
src = read("core", "services", "workflow.py")
check("a line is a zone for the OID column's scope",
      'or (doc.get("params") or {}).get("line"))' in src,
      "scoped by params.zone alone, a Line Counter's OID column recorded any "
      "id in frame rather than who crossed the line")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
