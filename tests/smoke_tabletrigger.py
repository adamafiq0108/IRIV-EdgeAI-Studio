# A Data Table can be told WHAT fires the row, instead of having it guessed.
#
# WHAT IT USED TO DO. With "Write a row: when something alerts", every rule
# wired into a COLUMN that could alert armed the table. The reasoning was that
# a trigger port would only restate the dropdown.
#
# It does not. Derivation cannot express "record these three values AT THE
# MOMENT THIS OTHER THING happens" -- the moment had to BE one of the values,
# so a table recording speed, lane and temperature when a gate opened needed
# the gate wired into a column it had no business filling.
#
# WHAT IS PINNED. That a wired trigger decides the row and the columns then
# only supply values; that it wins COMPLETELY, so a column rule that could
# also alert no longer fires anything; that a table with nothing on the port
# behaves exactly as it always did, because the graphs already built that way
# are correct; and that the two ways of getting it wrong say different things,
# since "add a Condition to one of the rules wired into it" sends the operator
# to the columns, which are not the problem.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True},
         {"id": "zone_b", "name": "Gate", "kind": "area", "enabled": True}]
COLUMNS = [{"name": "people", "type": "number"},
           {"name": "dwell", "type": "number"}]
TABLES = {"bay_log": {"key": "bay_log", "name": "Bay log", "columns": COLUMNS}}

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def graph(trig_from=None, column_alert=True, mode="alert"):
    """Two rules. `bay` feeds a column and may alert; `gate` is the separate
    thing whose MOMENT the operator wants recorded, and feeds no column."""
    nodes = [
        {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
         "data": {"zone": "zone_a"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
         "data": {"fnId": "bay", "name": "Bay"}},
        {"id": "z2", "type": "zone.ref", "position": {"x": 1, "y": 2},
         "data": {"zone": "zone_b"}},
        {"id": "gate", "type": "analytics.occupancy", "position": {"x": 2, "y": 2},
         "data": {"fnId": "gate", "name": "Gate"}},
        {"id": "gal", "type": "output.alert", "position": {"x": 3, "y": 2},
         "data": {"metric": "count", "op": ">", "value": 0}},
        {"id": "ds", "type": "output.dataset", "position": {"x": 4, "y": 0},
         "data": {"dataset": "bay_log", "name": "bay_log", "mode": mode,
                  "sources": {}}},
    ]
    edges = [
        {"id": "e1", "source": "s", "target": "z"},
        {"id": "e2", "source": "z", "target": "occ"},
        {"id": "e3", "source": "s", "target": "z2"},
        {"id": "e4", "source": "z2", "target": "gate"},
        {"id": "e5", "source": "gate", "target": "gal"},
        # The column wire: a value, always.
        {"id": "w1", "source": "occ", "target": "ds", "targetHandle": "c_people"},
    ]
    if column_alert:
        # The column's own rule CAN alert -- which is what used to fire the row.
        nodes.append({"id": "bal", "type": "output.alert", "position": {"x": 3, "y": 0},
                      "data": {"metric": "count", "op": ">", "value": 2}})
        edges.append({"id": "e6", "source": "occ", "target": "bal"})
    if trig_from:
        edges.append({"id": "t1", "source": trig_from, "target": "ds",
                      "targetHandle": "trig"})
    return {"nodes": nodes, "edges": edges}


def compile_(g):
    return wf.compile_graph(wf.clean_graph(g), ZONES, tables=TABLES)


def table_docs(c):
    return [o for o in c["outputs"] if o.get("type") == "dataset"]


# -- 1. nothing wired: exactly as before ----------------------------------------
c = compile_(graph())
check("with nothing on the port, the column's own rule still fires the row",
      not c["errors"] and len(table_docs(c)) == 1
      and table_docs(c)[0]["function"] == "bay", (c["errors"], table_docs(c)))

# -- 2. wired: the wired rule fires it, and ONLY it ------------------------------
c = compile_(graph(trig_from="gal"))
docs = table_docs(c)
check("a rule wired to the trigger fires the row",
      not c["errors"] and len(docs) == 1 and docs[0]["function"] == "gate",
      (c["errors"], docs))
check("...and the column's rule no longer fires one, so the row is recorded "
      "once, at the moment asked for",
      len(docs) == 1 and docs[0]["function"] != "bay", docs)
check("...while the column still supplies its VALUE",
      any(col["name"] == "people" and col.get("src") == "wire"
          for col in docs[0]["columns"]), docs[0]["columns"])

# -- 3. the point of the whole thing: a trigger that feeds no column --------------
#
# `gate` fills nothing. Before this port there was no way to say "record when
# the gate opens" except to wire the gate into a column it had no business in.
check("the firing rule need not fill a column at all",
      docs[0]["function"] == "gate"
      and all(col.get("key", "").find("gate") < 0 for col in docs[0]["columns"]),
      docs[0]["columns"])

# -- 4. a trigger wired to something that cannot fire ----------------------------
c = compile_(graph(trig_from="occ", column_alert=False))
msg = (c["errors"][0]["message"] if c["errors"] else "")
check("a trigger wired to something that never fires is refused",
      bool(c["errors"]), c)
check("...and the message is about the TRIGGER, not about the columns",
      "trigger" in msg and "one of the rules wired into it" not in msg, msg)

# -- 5. and the old message still greets the old mistake -------------------------
c = compile_(graph(column_alert=False))
msg = (c["errors"][0]["message"] if c["errors"] else "")
check("with nothing wired and no column rule alerting, the old advice stands",
      bool(c["errors"]) and "nothing feeding this table alerts" in msg, msg)
check("...and it now also mentions the port as a way out",
      "trigger port" in msg, msg)

# -- 6. the trigger wire is not a column -----------------------------------------
c = compile_(graph(trig_from="gal"))
cols = {col["name"] for col in table_docs(c)[0]["columns"]}
check("wiring the trigger does not invent or fill a column",
      cols <= {"people", "dwell"} and "trig" not in cols, cols)

# -- 7. it only applies to the alert mode ----------------------------------------
c = compile_(graph(trig_from="gal", mode="every"))
docs = table_docs(c)
check("a timed table ignores the port and still writes on its interval",
      not c["errors"] and docs and docs[0]["trigger"] == "any", (c["errors"], docs))

# -- 8. declared, and drawn ------------------------------------------------------
import io                                                     # noqa: E402
check("the node declares the handle",
      wf.REGISTRY["output.dataset"]["handles"] == ["trig"])
ui = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
             encoding="utf-8").read()
check("the canvas draws it", 'id="trig"' in ui)
# Two modes now: an alert fires the row, or a Line Counter's crossing does.
# The clock modes still have nothing to wire there (check_node_render.mjs
# mounts a change-mode table and asserts the port is absent).
check("...only in the modes it means anything in",
      'const trigPort = alertMode || d.mode === "crossing";' in ui
      and "{table && trigPort && (" in ui)

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
