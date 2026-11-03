# Data Table "Object IDs in a row": one row for the whole group (pcb_sensata,
# 2026-10-13).
#
# A tray holding two panels (total_panel = 2) wrote TWO rows, one id each,
# with the same timestamp. Set to "group", the identity column lists every
# counting object's id in ONE row ("k7-4, k7-5", naturally sorted, ", "), and
# a row is saved only when that set differs from the last one saved for the
# table -- one added, replaced or gone is a new row; the same set in any
# order is nothing.
#
# Checked here: the app write paths (app/outputs.py: alert, every, change),
# the default left alone, a table with no identity column left alone, the
# combination with "update the existing row" (keyed on the whole group cell),
# the Trigger's Current OID guard reading a group cell, the core validator,
# and the compile (an old graph compiles to the same document).
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import outputs as out                                 # noqa: E402
from app.functions import _oid_same                             # noqa: E402
from core.services import detector_control as dc                # noqa: E402
from core.services import workflow as wf                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


WRITTEN = []


def hook(o, cols):
    WRITTEN.append((o["dataset"], dict(cols)))


HOOKS = {"dataset": hook}
RUN = out._RUN_ID
COLS = [{"name": "object_id", "type": "text", "src": "event", "field": "track_id"},
        {"name": "total_panel", "type": "number", "src": "stat", "key": "cnt"}]


def table(name, group=True, columns=COLS, mode="alert", **extra):
    o = {"id": f"wf_{name}", "type": "dataset", "enabled": True,
         "dataset": name, "name": name, "mode": mode, "columns": columns,
         **extra}
    if group:
        o["ids_in_row"] = "group"
    return o


def dets(*tids):
    return [{"track_id": t, "confidence": 0.5 + i / 100}
            for i, t in enumerate(tids)]


def alert(o, *tids, new=None):
    detail = {"detections": dets(*tids)}
    if new is not None:
        detail["new"] = new
    return out._fire_one(o, HOOKS, {"ts": "2026-10-13T10:00:00",
                                    "detail": detail}, {"cnt": len(tids)})


def rows(name):
    return [c for d, c in WRITTEN if d == name]


def ids(*tids):
    return ", ".join(f"{RUN}-{t}" for t in tids)


# 1. two objects -> ONE row, both ids, naturally sorted, ", "-joined
t = table("g")
alert(t, 5, 4)
r = rows("g")
check("two objects = one row listing both ids, sorted",
      len(r) == 1 and r[0]["object_id"] == ids(4, 5)
      and r[0]["total_panel"] == 2.0, r)

# 2. the same set again, and reordered: nothing written
alert(t, 4, 5)
alert(t, 5, 4)
check("the same set (in any order) is not saved again", len(rows("g")) == 1,
      rows("g"))

# 3. one id replaced -> new row with the full new set
alert(t, 4, 6)
check("one id replaced = a new row with the new set",
      len(rows("g")) == 2 and rows("g")[-1]["object_id"] == ids(4, 6), rows("g"))

# 4. one added
alert(t, 4, 6, 10)
check("one added = a new row (natural order: 10 after 6)",
      len(rows("g")) == 3 and rows("g")[-1]["object_id"] == ids(4, 6, 10),
      rows("g"))

# 5. one removed
alert(t, 4, 10)
check("one removed = a new row",
      len(rows("g")) == 4 and rows("g")[-1]["object_id"] == ids(4, 10), rows("g"))

# 6. back to a set seen two rows ago: compared with the LAST saved, so new
alert(t, 4, 6, 10)
check("a set seen before but not last = a new row", len(rows("g")) == 5)

# 7. a new-object event (detail.new) still lists the WHOLE group
t2 = table("g_new")
alert(t2, 14, 15, new=[15])
check("detail.new names one newcomer, the row still lists both",
      [c["object_id"] for c in rows("g_new")] == [ids(14, 15)], rows("g_new"))

# 8. default (no setting): one row per object, unchanged
t3 = table("g_default", group=False)
alert(t3, 14, 15)
alert(t3, 14, 15)
check("default: two objects = two rows each time (4 rows over 2 alerts)",
      len(rows("g_default")) == 4
      and {c["object_id"] for c in rows("g_default")} == {f"{RUN}-14", f"{RUN}-15"},
      rows("g_default"))
t3b = table("g_object", group=False, ids_in_row="object")
alert(t3b, 14, 15)
check("explicit 'object' behaves as the default", len(rows("g_object")) == 2)

# 9. no identity column: one row as before, no dedupe
t4 = table("g_noid", columns=COLS[1:])
alert(t4, 4, 5)
alert(t4, 4, 5)
check("no identity column: setting has no effect (a row per alert)",
      len(rows("g_noid")) == 2 and "object_id" not in rows("g_noid")[0],
      rows("g_noid"))

# 10. nobody counts yet (minimum stay): no row, and nothing remembered
t5 = table("g_wait")
alert(t5, 1)
out._fire_one(t5, HOOKS, {"ts": "x", "detail": {"detections": [
    {"track_id": 2, "confidence": .9, "count_wait": True}]}}, {"cnt": 0})
check("objects present but none counting yet: no row",
      [c["object_id"] for c in rows("g_wait")] == [ids(1)], rows("g_wait"))

# 11. zone-scoped id column lists only that zone's members
ZC = [{"name": "object_id", "type": "text", "src": "event", "field": "track_id",
       "zone": "z1"}]
t6 = table("g_zone", columns=ZC)
out._fire_one(t6, HOOKS, {"ts": "x", "detail": {"detections": [
    {"track_id": 1, "confidence": .9, "zones": ["z1"]},
    {"track_id": 2, "confidence": .8, "zones": ["z2"]},
    {"track_id": 3, "confidence": .7, "zones": ["z1"]}]}}, {})
check("zone-scoped column: only the zone's members are listed",
      [c["object_id"] for c in rows("g_zone")] == [ids(1, 3)], rows("g_zone"))

# 12. every-N-seconds: the clock samples the group, the same group is skipped
te = table("g_every", mode="every", interval_s=5)
for i, d in enumerate((dets(1, 2), dets(2, 1), dets(1, 2, 3))):
    out._dataset_tick(te, {"cnt": len(d)}, 1000.0 + 10 * i, HOOKS, d)
check("every: same group on the next tick is not saved, a change is",
      [c["object_id"] for c in rows("g_every")] == [ids(1, 2), ids(1, 2, 3)],
      rows("g_every"))

# 13. on change: compared with the LAST saved set (A,B -> A -> A,B = 3 rows)
tc = table("g_change", mode="change", min_interval_s=0)
for i, d in enumerate((dets(1, 2), dets(1, 2), dets(1), dets(2, 1))):
    out._dataset_tick(tc, {"cnt": len(d)}, 2000.0 + i, HOOKS, d)
check("change: A,B / A,B / A / B,A = 3 rows",
      [c["object_id"] for c in rows("g_change")] == [ids(1, 2), ids(1), ids(1, 2)],
      rows("g_change"))
# floor: a change inside it waits, and is written once the floor has passed
tf = table("g_floor", mode="change", min_interval_s=10)
out._dataset_tick(tf, {}, 3000.0, HOOKS, dets(1))
out._dataset_tick(tf, {}, 3002.0, HOOKS, dets(1, 2))
held = len(rows("g_floor"))
out._dataset_tick(tf, {}, 3011.0, HOOKS, dets(1, 2))
check("change floor: held inside the floor, written after it",
      held == 1 and [c["object_id"] for c in rows("g_floor")] == [ids(1), ids(1, 2)],
      rows("g_floor"))

# 14. crossing tables stay one row per crossing
tx = table("g_cross", mode="crossing")
check("crossing mode ignores the group setting", not out._group_mode(tx))

# 15. combined with "update the existing row": keyed on the WHOLE group cell
tu = table("g_upd", on_repeat="update")
alert(tu, 7, 8)
r = rows("g_upd")
check("group + update: the upsert key is the id column, value the group",
      len(r) == 1 and out.dataset_upsert_key(tu, r[0]) == "object_id"
      and r[0]["object_id"] == ids(7, 8), r)
alert(tu, 8, 7)
check("group + update: the same group still writes nothing",
      len(rows("g_upd")) == 1)

# 16. the Trigger's Current OID guard reads a group cell as each of its ids
cell = ids(4, 5)
check("guard: a stored group holds each of its ids (bare and run-scoped)",
      _oid_same(cell, 4) and _oid_same(cell, 5) and _oid_same(cell, f"{RUN}-5"))
check("guard: an id outside the group does not match",
      not _oid_same(cell, 6) and not _oid_same(cell, 45))
check("guard: a single stored id behaves as before",
      _oid_same(f"{RUN}-3", 3) and not _oid_same(f"{RUN}-3", 4))

# 17. the core validator keeps it only when it can mean something
base = {"id": "wf_x", "type": "dataset", "dataset": "t", "mode": "alert",
        "columns": COLS, "ids_in_row": "group"}
check("validator keeps group with an identity column",
      dc._validate_output(dict(base)).get("ids_in_row") == "group")
check("validator drops it without one",
      "ids_in_row" not in dc._validate_output(dict(base, columns=COLS[1:])))
check("validator drops it on a crossing table",
      "ids_in_row" not in dc._validate_output(dict(base, mode="crossing", line="l1")))
check("validator: 'object' leaves no key",
      "ids_in_row" not in dc._validate_output(dict(base, ids_in_row="object")))

# =============================================================================
# compile
# =============================================================================
TABLES = {"pcb": {"key": "pcb", "name": "PCB",
                  "columns": [{"name": "object_id", "type": "text"},
                              {"name": "n", "type": "number"}]}}
SOURCES = {"object_id": {"src": "event", "field": "track_id"},
           "n": {"src": "const", "value": "1"}}


def graph(sources=SOURCES, **extra):
    data = {"dataset": "pcb", "name": "PCB", "mode": "every", "interval_s": 10,
            "sources": sources, **extra}
    return {"nodes": [{"id": "db", "type": "output.dataset",
                       "position": {"x": 0, "y": 0}, "data": data}],
            "edges": []}


def compiled(g):
    res = wf.compile_graph(wf.clean_graph(g), [], tables=TABLES)
    assert not res["errors"], res["errors"]
    docs = [o for o in res["outputs"] if o["type"] == "dataset"]
    assert len(docs) == 1, docs
    return docs[0], [w["message"] for w in res["warnings"] if w["node"] == "db"]


old, _ = compiled(graph())
check("an old graph compiles with no ids_in_row key", "ids_in_row" not in old, old)
same, _ = compiled(graph(ids_in_row="object"))
check("'One row per object' compiles to the identical document", same == old)
grp, notes = compiled(graph(ids_in_row="group"))
check("'One row for the whole group' compiles to ids_in_row=group",
      grp.get("ids_in_row") == "group"
      and {k: v for k, v in grp.items() if k != "ids_in_row"} == old, grp)
check("...with no warning", not any("whole group" in m for m in notes), notes)
both, _ = compiled(graph(ids_in_row="group", on_repeat="update"))
check("group + update compile together",
      both.get("ids_in_row") == "group" and both.get("on_repeat") == "update", both)
noid, notes = compiled(graph(sources={"n": {"src": "const", "value": "1"}},
                             ids_in_row="group"))
check("no identity column: dropped with a plain warning",
      "ids_in_row" not in noid and any("whole group" in m for m in notes),
      (noid, notes))
check("the node default is 'object'",
      wf.REGISTRY["output.dataset"]["data"].get("ids_in_row") == "object")

print()
print("FAILED" if bad else "all passed", f"({bad} failing)" if bad else "")
sys.exit(1 if bad else 0)
