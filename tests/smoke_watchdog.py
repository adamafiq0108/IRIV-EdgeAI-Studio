# A Trigger that re-raises per NEW OBJECT, not per cooldown.
#
# The cooldown answers "how often may this alarm repeat while it holds" with a
# clock. For a crowd that is the wrong question: the operator wants to hear
# once that the bay is over the limit, and again when SOMEBODY ELSE walks into
# it -- not every thirty seconds while the same three people stand there, and
# not silence while a fourth arrives because the timer has not run out.
#
# So a Trigger can be set to repeat "new_object", and is then told WHO by a
# wire from the rule's OID port. The engine keeps the objects it has already
# announced for as long as they are there; a newcomer fires, a lingerer does
# not, a leaver who comes back is new again, and the condition letting go
# wipes the slate.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app.functions import Function, FunctionEngine                # noqa: E402
from core.services import functions as fx                         # noqa: E402
from core.services import workflow as wf                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# -- the runtime -------------------------------------------------------------
def rule(**trig):
    t = {"metric": "count", "op": ">", "value": 1, "severity": "crit",
         "message": "overcrowded", "cooldown_s": 30,
         "oid_source": "f1", "repeat": "new_object"}
    t.update(trig)
    return Function({"id": "f1", "type": "zone_count", "name": "z",
                     "params": {"zone": "z1"}, "triggers": [t]})


def engine(fn):
    e = FunctionEngine.__new__(FunctionEngine)
    e.functions, e._trig, e._stats, e._live_oids = [fn], {}, {}, {}
    return e


def run(e, frames):
    """[(t, who)] -> [(t, source_oid)] for each frame that fired. The count
    IS len(who): the rule is "more than one in the zone"."""
    out = []
    for t, who in frames:
        e._stats["fn_f1_count"] = len(who)
        e._live_oids = {"f1": set(who)}
        for ev in e._check_triggers(t):
            out.append((t, ev.get("source_oid")))
    return out


print("== the same crowd is one alarm")
e = engine(rule())
fired = run(e, [(t, {1, 2}) for t in range(0, 120)])
check("fires once at the edge", [f[0] for f in fired] == [0], str(fired))
check("...named for one of the people in it", fired[0][1] in (1, 2), str(fired))
check("and not again on the cooldown, which is not in charge here",
      len(fired) == 1, str(fired))

print("== somebody joins")
e = engine(rule())
fired = run(e, [(0, {1, 2}), (1, {1, 2}), (2, {1, 2, 3}), (3, {1, 2, 3}),
                (4, {1, 2, 3, 4}), (5, {1, 2, 3, 4})])
check("a newcomer is announced the frame they are counted",
      [f[0] for f in fired] == [0, 2, 4], str(fired))
check("...and the alarm names THEM", [f[1] for f in fired][1:] == [3, 4],
      str(fired))

print("== somebody leaves and comes back")
e = engine(rule())
# Gone longer than the grace (functions.NEW_OBJECT_GRACE_S, 10 s): a real
# leave. A shorter absence is smoke_newobjgrace.py's flicker case.
fired = run(e, [(0, {1, 2, 3}), (1, {1, 2}), (12, {1, 2}), (13, {1, 2, 3})])
check("a leaver who returns is new again", [f[0] for f in fired] == [0, 13],
      str(fired))

print("== the condition lets go")
e = engine(rule())
fired = run(e, [(0, {1, 2}), (1, {1}), (12, {1}), (13, {1, 2})])
check("the slate is wiped: the next violation is a fresh alarm for the same ids",
      [f[0] for f in fired] == [0, 13], str(fired))

print("== a tracker hiccup")
e = engine(rule())
e._stats["fn_f1_count"] = 2
# The count holds (the rule still sees two) but every id drops for a frame.
out = []
for t, who in [(0, {1, 2}), (1, set()), (2, {1, 2}), (3, {1, 2})]:
    e._live_oids = {"f1": set(who)}
    out += [t for _ in e._check_triggers(t)]
check("an empty frame does not make everybody new again", out == [0], str(out))

print("== rising edge with nobody confirmed yet")
e = engine(rule())
e._stats["fn_f1_count"] = 2
out = []
for t, who in [(0, set()), (1, {7, 8}), (2, {7, 8})]:
    e._live_oids = {"f1": set(who)}
    out += [(t, ev.get("source_oid")) for ev in e._check_triggers(t)]
check("the edge fires nameless, and the first ids confirmed read as new",
      [o[0] for o in out] == [0, 1] and out[0][1] is None and out[1][1] in (7, 8),
      str(out))

print("== the cooldown path is untouched")
e = engine(rule(repeat="", cooldown_s=10))
fired = run(e, [(t, {1, 2}) for t in range(0, 31)])
check("a cooldown trigger still repeats on its clock",
      [f[0] for f in fired] == [0, 10, 20, 30], str(fired))
check("...and the OID wire names who, even there",
      all(f[1] in (1, 2) for f in fired), str(fired))

print("== a line rule's crossers")
line = Function({"id": "f1", "type": "line_count", "name": "door",
                 "params": {"line": "l1"},
                 "triggers": [{"metric": "cross", "op": ">", "value": 0,
                               "cooldown_s": 30, "oid_source": "f1",
                               "repeat": "new_object"}]})
e = engine(line)
out = []
for t, who in [(0, {1}), (1, set()), (2, set()), (3, {2}), (4, set()), (5, {1})]:
    e._stats["fn_f1_cross"] = t + 1
    e._live_oids = {"f1": set(who)}
    out += [(t, ev.get("source_oid")) for ev in e._check_triggers(t)]
check("each new crosser is announced once, and a crossing is not forgotten "
      "the frame after", out == [(0, 1), (3, 2)], str(out))

# -- end to end: the engine finds who on its own -------------------------------
# The shape the compiler emits: a counter, a branch expression over it, and
# the trigger on the EXPRESSION naming the counter as its OID source.
from app import zones as zones_mod                                # noqa: E402

ZONE = zones_mod.Zone({"id": "z1", "name": "Z", "kind": "area", "enabled": True,
                       "points": [[0, 0], [1, 0], [1, 1], [0, 1]]})
cnt = Function({"id": "cnt", "type": "zone_count", "name": "People",
                "enabled": True, "params": {"zone": "z1"}})
busy = Function({"id": "busy", "type": "expr", "name": "Overcrowded",
                 "enabled": True, "params": {"expr": "fn_cnt_count > 1"},
                 "trigger": {"metric": "value", "op": ">", "value": 0,
                             "severity": "crit", "message": "overcrowded",
                             "cooldown_s": 30, "oid_source": "cnt",
                             "repeat": "new_object"}})
eng = FunctionEngine([cnt, busy], state_dir=None)


def tick(t, ids, outside=()):
    dets = [{"track_id": i, "label": "person", "box": [10, 10, 50, 50]}
            for i in ids]
    dets += [{"track_id": i, "label": "person", "box": [200, 200, 250, 250]}
             for i in outside]
    evs = eng.update({"crossing_events": []}, dets,
                     {i: None for i in list(ids) + list(outside)}, {},
                     100, 100, zones=[ZONE], now=t)
    return [(t, e.get("source_oid")) for e in evs if e["type"] == "alert"]


got = []
for t, ids, out in [(0, [1], []), (1, [1, 2], [9]), (2, [1, 2], [9]),
                    (3, [1, 2, 3], [9]), (4, [1, 2, 3], []),
                    (5, [1, 3], []), (6, [1, 3, 2], []),
                    # 2 gone again, this time past the grace: new on return.
                    (7, [1, 3], []), (18, [1, 3], []), (19, [1, 3, 2], [])]:
    got += tick(t, ids, out)
check("through update(): the crowd once, then each newcomer, never the bystander "
      "(and a one-second absence is not a leave)",
      got == [(1, 1), (3, 3), (19, 2)], str(got))

# -- parse / validate ----------------------------------------------------------
t = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                          "repeat": "new_object", "oid_source": "cnt"}, "expr", "R")
check("validation carries the source and the mode",
      t.get("oid_source") == "cnt" and t.get("repeat") == "new_object", repr(t))
t = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                          "repeat": "new_object"}, "expr", "R")
check("...and drops the mode when nobody is named",
      "repeat" not in t and "oid_source" not in t, repr(t))
parsed = Function({"id": "x", "type": "expr", "name": "x",
                   "params": {"expr": "1"},
                   "trigger": {"metric": "value", "op": ">", "value": 0}}).trigger
check("an old trigger parses with the cooldown in charge",
      parsed["repeat"] == "" and parsed["oid_source"] == "", repr(parsed))

# -- the compiler -------------------------------------------------------------
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, handle=None, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{handle or ''}_{target_handle or ''}",
            "source": src, "target": tgt,
            "sourceHandle": handle, "targetHandle": target_handle}


def compile_(nodes, edges):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES)


def chain(trigger_data, oid_wire=True):
    nodes = [node("src", "source.detection"),
             node("z", "zone.ref", zone="bay"),
             node("cnt", "analytics.violations", fnId="cnt", name="People",
                  zone="bay", mode="realtime", OID=True),
             node("cmp", "branch.cases", fnId="busy",
                  rules=[{"op": ">", "value": 1, "input": "in1"}], otherwise=True),
             node("trg", "output.trigger", **trigger_data)]
    edges = [wire("src", "cnt"), wire("z", "cnt", None, "zone"),
             wire("cnt", "cmp"), wire("cmp", "trg", "out0")]
    if oid_wire:
        edges.append(wire("cnt", "trg", "OID", "oid"))
    return compile_(nodes, edges)


spec = wf.REGISTRY["output.trigger"]["data"]
check("a fresh Trigger repeats on the cooldown", spec.get("repeat") == "cooldown",
      repr(spec))

c = chain({"name": "Overcrowded", "alert": True, "severity": "crit",
           "repeat": "new_object"})
check("a Trigger fed by an OID wire compiles", not c["errors"], repr(c["errors"]))
trig = next((f["trigger"] for f in c["functions"] if f.get("trigger")
             and f["trigger"].get("message") == "Overcrowded"), None)
check("...with the counter as its source and the mode set",
      trig and trig.get("oid_source") == "cnt" and trig.get("repeat") == "new_object",
      repr(trig))
check("...and the OID wire is not read as a second moment",
      not any("ONE moment" in e["message"] for e in c["errors"]), repr(c["errors"]))

c = chain({"name": "Overcrowded", "alert": True, "repeat": "new_object"},
          oid_wire=False)
check("per-new-object with nobody wired is refused, and says what to wire",
      any("OID port" in e["message"] for e in c["errors"]), repr(c["errors"]))

c = chain({"name": "Overcrowded", "alert": True})
check("the OID wire alone is fine on a cooldown Trigger", not c["errors"],
      repr(c["errors"]))
trig = next((f["trigger"] for f in c["functions"] if f.get("trigger")
             and f["trigger"].get("message") == "Overcrowded"), None)
check("...and still names who the alarm is about",
      trig and trig.get("oid_source") == "cnt" and "repeat" not in trig, repr(trig))

# The wire-time guard: an OID onto a Trigger's OID port is allowed; onto its
# main input it is still refused, exactly as onto a card.
nodes = [node("src", "source.detection"), node("z", "zone.ref", zone="bay"),
         node("cnt", "analytics.violations", fnId="cnt", name="People",
              zone="bay", mode="realtime", OID=True),
         node("trg", "output.trigger", name="X")]
c = compile_(nodes, [wire("src", "cnt"), wire("z", "cnt", None, "zone"),
                     wire("cnt", "trg", "OID")])
check("an OID dropped on the Trigger's main input is still refused",
      any("row at a time" in e["message"] for e in c["errors"]), repr(c["errors"]))

print()
if bad:
    print(f"{bad} check(s) failed")
    sys.exit(1)
print("all good")
