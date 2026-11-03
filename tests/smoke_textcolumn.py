# A text variable wired into a Data Table column actually lands in the table.
#
# The bug this pins, found live on a customer graph (pcb_sensata on .234): a
# text constant compiles to no function -- the expression engine is numeric
# end to end -- so the wire from VIOLATION_STATUS into the `violation` column
# resolved to a stat key that never exists, and every row recorded NULL while
# the canvas showed a perfectly good string wired in.
#
# The database is the one consumer that CAN hold a string, so the wire now
# resolves at compile time into the column's fixed value -- the same `const`
# machinery a hand-configured "fixed value" column already uses, with the
# value living on the canvas where it can be seen and edited.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                        # noqa: E402

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"logbook": {"key": "logbook", "name": "Logbook", "columns": [
    {"name": "violation", "type": "text"},
    {"name": "zone_detected", "type": "number"},   # wrong on purpose, see below
    {"name": "total", "type": "number"},
]}}
bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def wire(src, tgt, handle=None):
    return {"id": f"e_{src}_{tgt}_{handle or ''}", "source": src, "target": tgt,
            "sourceHandle": None, "targetHandle": handle}


NODES = [
    {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 0, "y": 0},
     "data": {"zone": "bay"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 1, "y": 0},
     "data": {"fnId": "bay_now", "name": "Bay", "zone": "bay"}},
    # The text constant, exactly as the Variables panel's Constant box mints it.
    {"id": "vs", "type": "variable.number", "position": {"x": 1, "y": 1},
     "data": {"fnId": "violation_status", "name": "VIOLATION_STATUS",
              "value": "violate", "value_type": "text"}},
    # A text Set Variable -- its typed "Set it to" is the string.
    {"id": "sv", "type": "math.setvar", "position": {"x": 1, "y": 2},
     "data": {"fnId": "zone_set", "name": "Set Variable", "op": "set",
              "value_type": "text", "b_value": "zone 1"}},
    {"id": "hold", "type": "variable.ref", "position": {"x": 0, "y": 2},
     "data": {"key": "fn_zone_name_value", "io": "in"}},
    # The moment to record: an alert off the occupancy rule, the same shape
    # the live graph uses (rule -> branch -> Trigger -> table).
    {"id": "cmp", "type": "branch.multi", "position": {"x": 2, "y": 1},
     "data": {"fnId": "crowded", "join": "and",
              "rules": [{"op": ">", "value": 3, "input": "in1"}]}},
    {"id": "trig", "type": "output.trigger", "position": {"x": 2, "y": 2},
     "data": {"name": "overcrowded", "severity": "warn", "message": ""}},
    {"id": "ds", "type": "output.dataset", "position": {"x": 2, "y": 0},
     "data": {"dataset": "logbook", "name": "Logbook", "mode": "alert",
              "sources": {}}},
]
EDGES = [wire("src", "occ", "in"), wire("z", "occ", "zone"),
         wire("occ", "cmp", "in1"),
         {"id": "e_cmp_trig", "source": "cmp", "target": "trig",
          "sourceHandle": "out0", "targetHandle": "in"},
         wire("trig", "ds", "in"),
         wire("hold", "sv", "a"),
         wire("vs", "ds", "c_violation"),
         wire("sv", "ds", "c_zone_detected"),
         wire("occ", "ds", "c_total")]

g = wf.clean_graph({"nodes": NODES, "edges": EDGES})
c = wf.compile_graph(g, ZONES, tables=TABLES)
check("the graph compiles", not c["errors"], repr(c["errors"]))
ds = next((o for o in c["outputs"] if o.get("type") == "dataset"), None)
check("the Data Table output exists", ds is not None, repr(c["outputs"]))
cols = {col["name"]: col for col in (ds or {}).get("columns") or []}

# -- the text constant becomes the column's fixed value -----------------------
v = cols.get("violation") or {}
check("a text constant lands as the column's fixed value",
      v.get("src") == "const" and v.get("value") == "violate", repr(v))
z = cols.get("zone_detected") or {}
# A text Set Variable is a LIVE rule now (the engine carries the word), so
# the column reads the variable and records whichever word it holds.
check("a text Set Variable's column reads the variable it sets - the word it holds now",
      z.get("src") == "wire" and z.get("key") == "fn_zone_name_value", repr(z))
# Forced to text whatever the table says: one string in a number column
# breaks every query over the whole table, same reasoning as the OID column.
check("...and the column is text even where the table says number",
      z.get("type") == "text", repr(z))
# The wired NUMBER beside them is untouched.
t = cols.get("total") or {}
check("a wired number column still reads its stat key",
      t.get("src") == "wire" and t.get("key") == "fn_bay_now_count", repr(t))

# -- a text node doing its job hears no warning -------------------------------
check("no warning on a text node wired into a table",
      not any("Data Table" in w["message"] and w["node"] == "sv"
              for w in c.get("warnings") or []),
      repr(c.get("warnings")))
# The runtime path this rides is the const branch the row builder already
# has -- pinned so a rename there cannot silently orphan this.
app = open(os.path.join(ROOT, "systems", "demo", "app", "outputs.py"),
           encoding="utf-8").read()
check("the row builder writes const columns from their stored value",
      'value = col.get("value")' in app)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
