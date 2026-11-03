# The Trigger's DUPLICATE GUARD (Tok Aba, 2026-10-13).
#
# A Trigger feeding a Data Table wrote the same object again and again --
# every cooldown, or each time it re-entered the moment -- when the row it
# would write was already the newest one in the table. Two new ports read
# back what the table holds: Current OID (the stored object id) and Current
# value (the stored value), plus a "Total difference threshold".
#
#   watchdog (only for a new object)  the stored object is never pushed
#                                     again, whatever its value
#   timer    (every cooldown)         the stored object is skipped while
#                                     |value - stored| <= threshold
#   Current OID unwired / no match    exactly the old behaviour
#
# Contract checked here: the runtime decision (app/functions.py
# _stored_repeat and the three paths that call it), the compile of the two
# ports into query variables (core/services/workflow.py _trigger_guard), and
# that a Trigger without them compiles byte-for-byte as before.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import outputs                                           # noqa: E402
from app.functions import Function, FunctionEngine, _oid_same     # noqa: E402
from core.services import functions as fx                         # noqa: E402
from core.services import workflow as wf                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


RUN = outputs._RUN_ID
OIDK, VALK = "fn_tgo_trg_cur_oid_value", "fn_tgv_trg_cur_value_value"


# -- the runtime -------------------------------------------------------------
def rule(guard=True, value_wired=True, **trig):
    t = {"metric": "count", "op": ">", "value": 0, "severity": "warn",
         "message": "seen", "cooldown_s": 10, "oid_source": "f1"}
    if guard:
        t["cur_oid_key"] = OIDK
        if value_wired:
            t["cur_value_key"] = VALK
    t.update(trig)
    return Function({"id": "f1", "type": "zone_count", "name": "z",
                     "params": {"zone": "z1"}, "triggers": [t]})


def engine(fn, stored_oid=None, stored_value=None):
    e = FunctionEngine.__new__(FunctionEngine)
    e.functions, e._trig, e._stats, e._live_oids = [fn], {}, {}, {}
    e._scope = f"guard-{id(e)}"
    if stored_oid is not None:
        e._stats[OIDK] = stored_oid
    if stored_value is not None:
        e._stats[VALK] = stored_value
    return e


def run(e, frames):
    """[(t, who, count)] -> [(t, source_oid)] for each frame that fired."""
    out = []
    for t, who, count in frames:
        e._stats["fn_f1_count"] = count
        e._live_oids = {"f1": set(who)}
        for ev in e._check_triggers(t):
            out.append((t, ev.get("source_oid")))
    return out


print("== matching a stored id")
check("the run-scoped spelling the table records is the same object",
      _oid_same(f"{RUN}-3", 3))
check("...a bare stored id too, and a number column's 3.0",
      _oid_same("3", 3) and _oid_same("3.0", 3) and _oid_same(3.0, 3))
check("...but not another run's 3, and not nothing",
      not _oid_same("zzzz999-3", 3) and not _oid_same("", 3)
      and not _oid_same(None, 3) and not _oid_same(f"{RUN}-3", None))

print("== unwired: the old behaviour")
frames = [(t, {1}, 5) for t in range(0, 31)]
old_timer = run(engine(rule(guard=False, repeat="")), frames)
check("timer: fires on its cooldown as it always did",
      [f[0] for f in old_timer] == [0, 10, 20, 30], str(old_timer))
old_watch = run(engine(rule(guard=False, repeat="new_object")),
                [(0, {1}, 1), (1, {1}, 1), (2, {1, 2}, 2)])
check("watchdog: once, then the newcomer",
      old_watch == [(0, 1), (2, 2)], str(old_watch))
# A stat the guard would read is ignored when the trigger names none.
e = engine(rule(guard=False, repeat=""), stored_oid=f"{RUN}-1", stored_value=5)
check("a stored row is ignored by a trigger without Current OID",
      run(e, frames) == old_timer, "")
# Wired, but the stored object is somebody else: as before.
e = engine(rule(repeat=""), stored_oid=f"{RUN}-9", stored_value=5)
check("wired, a different stored object: the timer fires exactly as before",
      run(e, frames) == old_timer)
e = engine(rule(repeat=""))
check("wired, nothing read yet (first poll pending): as before",
      run(e, frames) == old_timer)

print("== watchdog (only for a new object)")
e = engine(rule(repeat="new_object"), stored_oid=f"{RUN}-1", stored_value=0)
got = run(e, [(0, {1}, 1), (1, {1}, 1), (2, {1}, 1)])
check("the stored object is not pushed again", got == [], str(got))
e = engine(rule(repeat="new_object"), stored_oid=f"{RUN}-1", stored_value=999)
got = run(e, [(0, {1}, 50)])
check("...whatever its value", got == [], str(got))
e = engine(rule(repeat="new_object"), stored_oid=f"{RUN}-1")
got = run(e, [(0, {1}, 1), (1, {1, 2}, 2), (2, {1, 2}, 2)])
check("a new object still pushes, and is the one named",
      got == [(1, 2)], str(got))
e = engine(rule(repeat="new_object"), stored_oid=f"{RUN}-1")
got = run(e, [(0, {1, 4}, 2)])
check("an edge with the stored object AND a new one names the new one",
      got == [(0, 4)], str(got))

print("== timer (every cooldown)")
e = engine(rule(repeat="", dup_diff=5), stored_oid=f"{RUN}-1", stored_value=8)
got = run(e, [(t, {1}, 10) for t in range(0, 31)])
check("same object, |10 - 8| <= 5: not pushed", got == [], str(got))
e = engine(rule(repeat="", dup_diff=5), stored_oid=f"{RUN}-1", stored_value=2)
got = run(e, [(t, {1}, 10) for t in range(0, 31)])
check("same object, |10 - 2| > 5: pushed (a new row is allowed), on the cooldown",
      [g[0] for g in got] == [0, 10, 20, 30] and all(g[1] == 1 for g in got),
      str(got))
e = engine(rule(repeat="", dup_diff=5), stored_oid=f"{RUN}-1", stored_value=8)
got = run(e, [(0, {1}, 10), (5, {1}, 10), (10, {1}, 20), (15, {1}, 20)])
check("a skipped push uses its cooldown slot: the move to 20 pushes at the "
      "next one", [g[0] for g in got] == [10], str(got))
e = engine(rule(repeat="", dup_diff=0), stored_oid=f"{RUN}-1", stored_value=10)
got = run(e, [(t, {1}, 10) for t in range(0, 21)])
check("threshold 0 and equal values: not pushed", got == [], str(got))
e = engine(rule(repeat="", dup_diff=0), stored_oid=f"{RUN}-1", stored_value=10)
got = run(e, [(0, {1}, 11)])
check("threshold 0: any change at all pushes", got == [(0, 1)], str(got))
e = engine(rule(repeat="", value_wired=False), stored_oid=f"{RUN}-1")
got = run(e, [(t, {1}, 10) for t in range(0, 21)])
check("no Current value wired: the stored object is skipped", got == [], str(got))

print("== the Advanced 'how often' twins")
e = engine(rule(repeat="", rate="cooldown", rate_s=5, dup_diff=0),
           stored_oid=f"{RUN}-1", stored_value=10)
got = run(e, [(0, {1}, 10)])
check("at most once every N s reads as a timer: same value skipped", got == [],
      str(got))
e = engine(rule(repeat="", rate="once_per_oid"), stored_oid=f"{RUN}-1")
got = run(e, [(0, {1}, 1), (1, {1, 2}, 2)])
check("once per object reads as a watchdog: only the new object fires",
      got == [(1, 2)], str(got))

# -- parse / validate --------------------------------------------------------
t = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                          "cur_oid_key": OIDK, "cur_value_key": VALK,
                          "dup_diff": "2.5"}, "expr", "R")
check("validation carries both keys and the threshold",
      t.get("cur_oid_key") == OIDK and t.get("cur_value_key") == VALK
      and t.get("dup_diff") == 2.5, repr(t))
t = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                          "cur_value_key": VALK, "dup_diff": 3}, "expr", "R")
check("...and nothing without Current OID",
      not any(k in t for k in ("cur_oid_key", "cur_value_key", "dup_diff")), repr(t))
parsed = Function({"id": "x", "type": "expr", "name": "x", "params": {"expr": "1"},
                   "trigger": {"metric": "value", "op": ">", "value": 0}}).trigger
check("an old trigger parses with no guard",
      parsed["cur_oid_key"] == "" and parsed["dup_diff"] == 0.0, repr(parsed))

# -- the compiler ------------------------------------------------------------
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]
TABLES = {"visits": {"key": "visits", "name": "Visits", "columns": [
    {"name": "oid", "type": "text"}, {"name": "weight", "type": "number"}]}}


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, handle=None, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{handle or ''}_{target_handle or ''}",
            "source": src, "target": tgt,
            "sourceHandle": handle, "targetHandle": target_handle}


def chain(trigger_data, guard_wires=()):
    nodes = [node("src", "source.detection"),
             node("z", "zone.ref", zone="bay"),
             node("cnt", "analytics.violations", fnId="cnt", name="People",
                  zone="bay", mode="realtime", OID=True),
             node("cmp", "branch.cases", fnId="busy",
                  rules=[{"op": ">", "value": 1, "input": "in1"}], otherwise=True),
             node("trg", "output.trigger", **trigger_data),
             node("ds", "output.dataset", dataset="visits", name="Visits",
                  mode="alert", sources={})]
    edges = [wire("src", "cnt"), wire("z", "cnt", None, "zone"),
             wire("cnt", "cmp"), wire("cmp", "trg", "out0"),
             wire("cnt", "trg", "OID", "oid")] + list(guard_wires)
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}),
                            ZONES, tables=TABLES)


def trig_of(c):
    return next((f["trigger"] for f in c["functions"] if f.get("trigger")
                 and f["trigger"].get("message") == "Seen"), None)


base = {"name": "Seen", "alert": True}
plain = chain(base)
ticked = chain({**base, "dup_guard": True, "dup_threshold": 4})
check("unwired: compiles clean", not plain["errors"], repr(plain["errors"]))
check("unwired: the tick alone changes nothing in what is compiled",
      plain["functions"] == ticked["functions"]
      and plain["outputs"] == ticked["outputs"])
check("...and the trigger carries no guard",
      not any(k in (trig_of(plain) or {}) for k in ("cur_oid_key", "dup_diff")),
      repr(trig_of(plain)))

both = [wire("ds", "trg", "o_oid", "cur_oid"), wire("ds", "trg", "o_weight", "cur_value")]
c = chain({**base, "dup_guard": True, "dup_threshold": 4}, both)
check("wired: compiles clean", not c["errors"], repr(c["errors"]))
trig = trig_of(c) or {}
q_oid = next((f for f in c["functions"] if f["id"].startswith("tgo_")), None)
q_val = next((f for f in c["functions"] if f["id"].startswith("tgv_")), None)
check("Current OID becomes a query variable reading the column's newest WORD",
      q_oid and q_oid["type"] == "query_value" and q_oid["params"].get("text") is True
      and q_oid["params"].get("agg") == "latest" and q_oid["params"].get("field") == "oid",
      repr(q_oid))
check("Current value becomes one reading the newest number",
      q_val and not q_val["params"].get("text") and q_val["params"].get("agg") == "latest"
      and q_val["params"].get("field") == "weight", repr(q_val))
check("the trigger reads those two stats and carries the threshold",
      trig.get("cur_oid_key") == f"fn_{q_oid['id']}_value"
      and trig.get("cur_value_key") == f"fn_{q_val['id']}_value"
      and trig.get("dup_diff") == 4.0, repr(trig))
check("...and keeps its OID source (the guard ports are not a second moment "
      "or a second who)", trig.get("oid_source") == "cnt"
      and not any("ONE moment" in e["message"] for e in c["errors"]), repr(trig))
check("a branch carries only 1, and the compile says so for Current value",
      any("only carries 1" in w["message"] for w in c["warnings"]),
      repr(c["warnings"]))

# THE shape this exists for: the Trigger writes the row, and the same table
# is read back into Current OID. A poll of stored rows, not a cycle.
loop = [wire("trg", "ds", None, "trig"), wire("cnt", "ds", "OID", "c_oid"),
        wire("ds", "trg", "o_oid", "cur_oid")]
c = chain({**base, "dup_guard": True}, loop)
check("write the row, read it back into Current OID: compiles, no cycle",
      not c["errors"], repr(c["errors"]))
check("...the table still writes on this Trigger's moment, and the guard is on",
      any(o.get("function") == "busy_b0" for o in c["outputs"])
      and (trig_of(c) or {}).get("cur_oid_key"), repr(c["outputs"]))
ok, _why = wf.edge_allowed("output.dataset", "output.trigger", "cur_oid")
ok_in, _ = wf.edge_allowed("output.dataset", "output.trigger", "in")
check("the wire is allowed onto the guard ports only", ok and not ok_in)

c = chain(base, [wire("cnt", "trg", None, "cur_oid")])
check("a rule wired into Current OID is refused with what to wire",
      any("Current OID reads what a Data Table" in e["message"]
          for e in c["errors"] + c["warnings"]), repr(c["errors"]))
c = chain(base, [wire("ds", "trg", "o_oid", "cur_oid"),
                 wire("ds", "trg", "o_oid", "cur_value")])
check("a text column into Current value is refused",
      any("needs a number column" in e["message"]
          for e in c["errors"] + c["warnings"]), repr(c["errors"]))
c = chain(base, [wire("ds", "trg", "o_weight", "cur_value")])
check("Current value alone is a warning, and no guard is compiled",
      any("only read beside Current OID" in w["message"] for w in c["warnings"])
      and "cur_value_key" not in (trig_of(c) or {}), repr(c["warnings"]))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
