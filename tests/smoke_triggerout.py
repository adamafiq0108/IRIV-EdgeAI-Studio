# The Trigger node's Advanced output: what it sends, how often, how long.
#
# Tok Aba (2026-10-05): a Trigger fires with the Inject's typed payload
# instead of the bare 1, may be limited to "at most once every N seconds" or
# "once per object (OID)", may hold its payload N seconds and then go back to
# 0 or to a payload of the operator's own for "not triggered".
#
# What is pinned here:
#   1. an OLD Trigger compiles byte-for-byte as before (no new keys, the
#      defaults present or absent alike) and its runtime is unchanged
#   2. the compile cleans and refuses each setting with a plain sentence
#   3. everything wired after an Advanced Trigger reads fn_<rule>_out
#   4. the runtime: payload types, idle, the three holds -- Real-time
#      following the trigger live, Interval re-checking every N s (held
#      while active and unchanged, dropped when not), Hold N (on N s, a
#      retrigger restarting it), old saved holds migrating to Real-time or
#      Hold unchanged -- the cooldown, once per object (and across a
#      reload), bounded memory, one camera's objects kept apart
#   5. with the AI off a detection-fed Trigger publishes nothing at all --
#      neither its payload nor its idle payload
#   6. a PLAIN Trigger wired into a Math or Formula input reads the moment
#      it names (1 while it holds, 0 otherwise), compile and runtime -- it
#      compiled to 0 before (input_alias did not resolve output.trigger)
#   7. a Trigger reaches Modbus Write and MQTT Publish end to end, plain and
#      with every payload type; a text payload on a register write is refused
#      on the node in plain words. MQTT Publish has no clock of its own
#      (Tok Aba 2026-10-08): the Trigger's hold says when -- Real-time the
#      payload at a fire and the idle one when it lets go, Interval N again
#      every N s while it holds, For N seconds once, once per object once per
#      new object, a plain Trigger on each of its fires
import json
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import functions as appfx                                # noqa: E402
from app import run as apprun                                     # noqa: E402
from app.functions import Function, FunctionEngine                # noqa: E402
from core.services import functions as fx                         # noqa: E402
from core.services import trigger_out                             # noqa: E402
from core.services import workflow as wf                          # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read_text(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


# -- the graph the compile tests share ----------------------------------------
def node(nid, ntype, defaults=True, **data):
    base = dict(wf.REGISTRY[ntype]["data"]) if defaults else {}
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**base, **data}}


def graph(trigger=None, oid=False, extra_nodes=(), extra_edges=(),
          defaults=True):
    nodes = [node("src", "source.detection"),
             node("fr", "analytics.frame", fnId="crowd", name="Crowd",
                  mode="realtime", OID=oid),
             node("cmp", "branch.multi", fnId="many", join="and",
                  rules=[{"op": ">", "value": 1, "input": "in1"}]),
             node("t1", "output.trigger", defaults=defaults,
                  **({"name": "crowded"} | (trigger or {}))),
             *extra_nodes]
    edges = [{"id": "e1", "source": "src", "target": "fr"},
             {"id": "e2", "source": "fr", "target": "cmp", "targetHandle": "in1"},
             {"id": "e3", "source": "cmp", "target": "t1",
              "sourceHandle": "out0", "targetHandle": "in"},
             *extra_edges]
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g


def compile_(**kw):
    return wf.compile_graph(graph(**kw), [], tables={})


def trig_of(c, fid="many_b0"):
    return next((f.get("trigger") for f in c["functions"] if f["id"] == fid), None)


def errors_of(c, nid="t1"):
    return [e["message"] for e in c["errors"] if e["node"] == nid]


# -- 1. an old Trigger is exactly what it was ---------------------------------
print("== an old Trigger compiles as before")
reg = wf.REGISTRY["output.trigger"]["data"]
check("the new settings are on a fresh Trigger, all off",
      all(k in reg for k in ("payload_type", "payload", "idle_type",
                             "idle_payload", "rate", "rate_s", "out_hold_s"))
      and not trigger_out.is_set(reg), repr(reg))
PINNED = {"metric": "value", "op": ">", "value": 0.0, "severity": "info",
          "message": "crowded", "cooldown_s": 0}
for label, trig in (("plain", {}),
                    ("alert", {"alert": True, "severity": "crit",
                               "message": "too many", "cooldown_s": 15}),
                    ("painting", {"draw": True, "box_color": "#00A0FF",
                                  "zone_color": "#FFAA00"})):
    saved = compile_(trigger=trig, defaults=False)       # keys absent
    fresh = compile_(trigger=trig)                        # defaults present
    check(f"{label}: no new keys and the defaults compile identically",
          json.dumps(saved, sort_keys=True) == json.dumps(fresh, sort_keys=True),
          f"{trig_of(saved)} / {trig_of(fresh)}")
    check(f"{label}: nothing Advanced reaches the compiled trigger",
          not ({"out", "rate", "rate_s"} & set(trig_of(fresh) or {})),
          repr(trig_of(fresh)))
check("the plain one is pinned to the shape it has always had",
      trig_of(compile_(defaults=False)) == PINNED,
      repr(trig_of(compile_(defaults=False))))
old_g = graph(defaults=False)
check("...and its node carries no stamp", "_out_key" not in old_g["nodes"][3]["data"])
wf.compile_graph(old_g, [], tables={})
check("...not even after compiling", "_out_key" not in old_g["nodes"][3]["data"])
check("an old compiled trigger validates to exactly itself",
      fx._validate_trigger(dict(PINNED), "expr", "R") == PINNED,
      repr(fx._validate_trigger(dict(PINNED), "expr", "R")))

# A Trigger switched back off forgets its stamp, even on a graph object a
# previous compile already stamped.
g = graph(trigger={"payload_type": "number", "payload": "5"})
wf.compile_graph(g, [], tables={})
check("an Advanced Trigger is stamped with its output key",
      g["nodes"][3]["data"].get("_out_key") == "fn_many_b0_out",
      repr(g["nodes"][3]["data"]))
g["nodes"][3]["data"]["payload_type"] = ""
wf.compile_graph(g, [], tables={})
check("...and loses the stamp when it is switched back off",
      "_out_key" not in g["nodes"][3]["data"])

# -- 2. the compile cleans and refuses -----------------------------------------
print("== the compile cleans and refuses")
c = compile_(trigger={"payload_type": "string", "payload": "door open",
                      "idle_type": "json", "idle_payload": '{"ok": true}',
                      "out_hold_s": 3, "rate": "cooldown", "rate_s": 5})
t = trig_of(c)
check("a full Advanced Trigger compiles", not c["errors"], repr(c["errors"]))
check("...carrying payload, idle and hold",
      t.get("out") == {"type": "string", "value": "door open",
                       "idle_type": "json", "idle_value": '{"ok": true}',
                       "hold_s": 3.0}, repr(t))
check("...and the cooldown, which is also the alarm's own",
      t.get("rate") == "cooldown" and t.get("rate_s") == 5.0
      and t.get("cooldown_s") == 5, repr(t))
c = compile_(trigger={"rate": "cooldown", "rate_s": 5, "alert": True,
                      "repeat": "new_object"})
check("with a rate set, the alarm's 'raise again' is not a second answer",
      "repeat" not in trig_of(c) and not c["errors"], repr(trig_of(c)))
for data, word in (({"payload_type": "number", "payload": "abc"},
                    "the payload when triggered: 'abc' is not a number"),
                   ({"payload_type": "json", "payload": "{x"},
                    "the payload when triggered: the JSON payload does not parse"),
                   ({"idle_type": "boolean", "idle_payload": "maybe"},
                    "the payload when not triggered: a true/false payload"),
                   ({"payload_type": "buffer"}, "the payload type must be one of"),
                   ({"out_hold_s": "soon"}, "the hold time must be a number"),
                   ({"out_hold_s": -1}, "the hold time must be between 0"),
                   ({"rate": "cooldown", "rate_s": 0},
                    "the time between two fires must be between 1"),
                   ({"rate": "sometimes"}, "how often it can fire must be"),
                   ({"rate": "once_per_oid"},
                    "fires once per object, so it must know which object")):
    msgs = errors_of(compile_(trigger=data))
    check(f"refused: {word}", any(word in m for m in msgs), repr(msgs))
    check("...in plain words, no codes",
          all(not re.search(r"\b(HTTP|[45]\d\d)\b", m) and "\u2014" not in m
              for m in msgs), repr(msgs))

c = compile_(trigger={"rate": "once_per_oid"}, oid=True)
check("once per object finds the OID rule upstream by itself",
      not c["errors"] and trig_of(c).get("oid_source") == "crowd"
      and trig_of(c).get("rate") == "once_per_oid", repr(c["errors"] or trig_of(c)))
# A wire on the Trigger's own OID port names a different rule, and wins.
c = wf.compile_graph(graph(
    trigger={"rate": "once_per_oid"}, oid=True,
    extra_nodes=[node("fr2", "analytics.frame", fnId="others", name="Others",
                      mode="realtime", OID=True)],
    extra_edges=[{"id": "e9", "source": "src", "target": "fr2"},
                 {"id": "e10", "source": "fr2", "target": "t1",
                  "sourceHandle": "OID", "targetHandle": "oid"}]), [], tables={})
check("...and a wire on its OID port wins over the walk",
      trig_of(c).get("oid_source") == "others", repr(c["errors"] or trig_of(c)))

# -- 3. what is wired after it reads its output --------------------------------
print("== downstream reads the Trigger's output")
c = wf.compile_graph(graph(
    trigger={"payload_type": "number", "payload": "7"},
    extra_nodes=[node("m", "math.arith", fnId="plus", op="add"),
                 node("card", "dashboard.card"),
                 node("pub", "output.mqtt_publish", topic="x/y"),
                 node("mb", "output.modbus_write", server="plc", address=1)],
    extra_edges=[{"id": "w1", "source": "t1", "target": "m", "targetHandle": "a"},
                 {"id": "w2", "source": "t1", "target": "card"},
                 {"id": "w3", "source": "t1", "target": "pub"},
                 {"id": "w4", "source": "t1", "target": "mb",
                  "targetHandle": "in"}]), [], tables={})
expr = next((f["params"]["expr"] for f in c["functions"] if f["id"] == "plus"), "")
check("a Math node reads it", "fn_many_b0_out" in expr, expr)
check("a card reads it", [w.get("key") for w in c["widgets"]] == ["fn_many_b0_out"],
      repr(c["widgets"]))
keys = {o["id"]: o.get("key") for o in c["outputs"]}
check("an MQTT Publish reads it", keys.get("wf_pub") == "fn_many_b0_out", repr(keys))
check("a Modbus Write's value reads it", keys.get("wf_mb") == "fn_many_b0_out",
      repr(keys) + repr(c["errors"]))
c_old = wf.compile_graph(graph(
    extra_nodes=[node("card", "dashboard.card")],
    extra_edges=[{"id": "w2", "source": "t1", "target": "card"}]), [], tables={})
check("...while a card after an OLD Trigger reads what it always read",
      [w.get("key") for w in c_old["widgets"]] == ["fn_many_b0_value"],
      repr(c_old["widgets"]))

# -- 6. a plain Trigger into Math reads the moment it names ------------------
print("== a plain Trigger into a Math input reads the moment it names")
FX = node("fx", "analytics.formula", fnId="fx", name="Formula", op="add",
          inputs=2, a_value=1, b_value=1)
c = wf.compile_graph(graph(
    extra_nodes=[node("m", "math.arith", fnId="plus", op="add"),
                 node("g", "math.arith", fnId="gated", op="add", a_value=5),
                 FX, node("t2", "output.trigger", name="formula says"),
                 node("m2", "math.arith", fnId="plus2", op="add")],
    extra_edges=[{"id": "w1", "source": "t1", "target": "m", "targetHandle": "a"},
                 {"id": "w2", "source": "t1", "target": "g", "targetHandle": "when"},
                 {"id": "w3", "source": "fx", "target": "t2", "targetHandle": "in"},
                 {"id": "w4", "source": "t2", "target": "m2",
                  "targetHandle": "a"}]), [], tables={})
exprs = {f["id"]: f["params"].get("expr") for f in c["functions"]}
check("the graph compiles", not c["errors"], repr(c["errors"]))
check("a Trigger after a branch port reads that branch's active (1 while it "
      "holds), not 0", exprs.get("plus") == "(fn_many_b0_active + 0)",
      repr(exprs.get("plus")))
check("...and on the run port it gates the maths, as the Condition it "
      "replaced did",
      exprs.get("gated") == "iff((fn_many_b0_active != 0), ((5 + 0)), 0)",
      repr(exprs.get("gated")))
check("a Trigger after a Formula reads that Formula's active",
      exprs.get("plus2") == "(fn_fx_active + 0)", repr(exprs.get("plus2")))
# The alias by name: a Formula may spell the Trigger's name in its text.
g = graph(extra_nodes=[node("m", "math.arith", fnId="plus", op="add")],
          extra_edges=[{"id": "w1", "source": "t1", "target": "m",
                        "targetHandle": "a"}])
preds = {}
by_id = {n["id"]: n for n in g["nodes"]}
for e in g["edges"]:
    preds.setdefault(e["target"], []).append((e, by_id[e["source"]]))
names = wf.formula_inputs("m", preds["m"], wf.switch_bases(g), preds)
check("...and answers to the Trigger's name as well as the rule's id",
      names == {"many_b0": "fn_many_b0_active", "crowded": "fn_many_b0_active"},
      repr(names))


# -- 4. the runtime ------------------------------------------------------------
def rule(fid="r1", **adv):
    t = {"metric": "value", "op": ">", "value": 0, "severity": "info",
         "message": "crowded", "cooldown_s": 0, **adv}
    return Function({"id": fid, "type": "expr", "name": "r",
                     "params": {"expr": "0"}, "triggers": [t]})


def engine(fn, scope=""):
    e = FunctionEngine.__new__(FunctionEngine)
    e.functions, e._trig, e._stats, e._live_oids = [fn], {}, {}, {}
    e._scope, e._outs, e._out_last = scope, {}, {}
    return e


def frame(e, t, value, who=None, fid="r1"):
    e._stats = {f"fn_{fid}_value": value}
    e._live_oids = {"src": set(who)} if who is not None else {}
    events = e._check_triggers(t)
    return events, e._stats.get(f"fn_{fid}_out", "absent")


print("== payload types")
for ptype, payload, want in (("string", "door open", "door open"),
                             ("number", "4.5", 4.5), ("number", "3", 3),
                             ("boolean", "true", 1), ("boolean", "false", 0),
                             ("json", '{"a": [1, 2]}', '{"a":[1,2]}'),
                             ("json", "7", 7)):
    e = engine(rule(out={"type": ptype, "value": payload}))
    _ev, idle = frame(e, 0, 0)
    ev, out = frame(e, 1, 1)
    check(f"{ptype} {payload!r}: idle 0, then {want!r} while it holds",
          idle == 0 and out == want, f"{idle!r} {out!r}")
    check("...and the event carries the payload", ev and ev[0]["value"] == want,
          repr(ev))
e = engine(rule(out={"type": "timestamp"}))
_ev, out = frame(e, 1700000000.5, 1)
check("a timestamp is the moment it fired, in ms", out == 1700000000500, out)
_ev, out = frame(e, 1700000009.0, 1)
check("...and stays that moment while it holds", out == 1700000000500, out)
e = engine(rule(out={}))
outs = [frame(e, t, v)[1] for t, v in enumerate([0, 1, 1, 0])]
check("no payload set: 1 while it holds, 0 after (the moment's own 0/1)",
      outs == [0, 1, 1, 0], outs)

print("== idle payload and hold")
e = engine(rule(out={"type": "string", "value": "ALARM",
                     "idle_type": "string", "idle_value": "ok"}))
outs = [frame(e, t, v)[1] for t, v in enumerate([0, 1, 1, 0, 0])]
check("no hold: the payload for as long as it holds, then the idle payload",
      outs == ["ok", "ALARM", "ALARM", "ok", "ok"], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 1, 1, 1, 1, 1])]
check("hold 3 s: on for 3 s, then back to 0 though the moment still holds",
      outs == [9, 9, 9, 0, 0, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 0, 0, 0, 0])]
check("...and on for 3 s though the moment let go at once",
      outs == [9, 9, 9, 0, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3},
                rate="cooldown", rate_s=60))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 0, 1, 0, 0, 0, 0])]
check("a new trigger while it is on restarts the hold (inside the cooldown, "
      "so no second fire)", outs == [9, 9, 9, 9, 9, 0, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 2,
                     "idle_type": "number", "idle_value": "-1"}))
outs = [frame(e, t, v)[1] for t, v in enumerate([0, 1, 1, 1, 0])]
check("hold then the idle payload", outs == [-1, 9, 9, -1, -1], outs)

# -- the three holds (Tok Aba, 2026-10-07) -------------------------------------
# Real-time follows the trigger live (what hold 0 always did, and the
# default); Interval N re-checks every N s and holds while the trigger is
# still active and unchanged; Hold N is the one above, kept exactly.
IV = {"hold": "interval"}
print("== hold: Real-time")
e = engine(rule(out={"type": "number", "value": "9", "idle_type": "number",
                     "idle_value": "-1"}))
outs = [frame(e, t, v)[1] for t, v in enumerate([0, 1, 1, 0, 2, 2, 0, 0])]
check("Real-time: the payload the frame it fires, the idle payload the "
      "frame it lets go, every time, no timer",
      outs == [-1, 9, 9, -1, 9, 9, -1, -1], outs)
e = engine(rule(out={"type": "number", "value": "9"}))
outs = [frame(e, t / 10, v)[1] for t, v in enumerate([1, 0, 1, 0])]
check("...even a blip a tenth of a second long", outs == [9, 0, 9, 0], outs)

print("== hold: Interval")
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1] * 10)]
check("Interval 3: held for as long as the trigger stays active and "
      "unchanged (re-checked at 3, 6, 9) -- where Hold 3 drops at 3",
      outs == [9] * 10, outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 0, 0, 0, 0])]
check("...at least N s though the trigger let go at once, then off at the "
      "re-check", outs == [9, 9, 9, 0, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 1, 1, 1, 1, 0, 0, 0])]
check("...inactive between two re-checks: off at the next one (6), not the "
      "frame it let go", outs == [9, 9, 9, 9, 9, 9, 0, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV,
                     "idle_type": "string", "idle_value": "ok"}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 1, 2, 2, 2, 2, 2])]
check("...value changed (1 to 2, still over the threshold): 'not "
      "triggered' at the re-check, and stays so -- no new fire",
      outs == [9, 9, 9, "ok", "ok", "ok", "ok"], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 2, 2, 1, 1, 1, 1])]
check("...only the re-check looks: changed and back by then is unchanged",
      outs == [9] * 7, outs)
appfx._trigger_memory.clear()      # the cooldown clock outlives an engine
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV},
                rate="cooldown", rate_s=60))
outs = [frame(e, t, v)[1] for t, v in enumerate([1, 0, 1, 0, 0, 0, 0])]
check("a new trigger while it is on restarts the interval (inside the "
      "cooldown, so no second fire)", outs == [9, 9, 9, 9, 9, 0, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 2, **IV,
                     "idle_type": "number", "idle_value": "-1"}))
outs = [frame(e, t, v)[1] for t, v in enumerate([0, 1, 1, 1, 0, 0])]
check("held across the re-check at 3, then the idle payload at 5",
      outs == [-1, 9, 9, 9, 9, -1], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3, **IV}))
outs = [frame(e, t, v)[1] for t, v in ((0, 1), (1, 1), (10, 1), (12, 1),
                                       (13, 1), (14, 0), (16, 0))]
check("sparse frames: re-checks missed are made up the next frame (10), and "
      "the next is N s after it (13 kept, 16 off)",
      outs == [9, 9, 9, 9, 9, 9, 0], outs)
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 2, **IV},
                mode="is_true"))
mid = None
for t, v in enumerate(["door", "door", "door", "gate", "gate"]):
    e._stats = {"fn_r1_value": v}
    e._check_triggers(t)
    if t == 2:
        mid = e._stats["fn_r1_out"]
check("a word is compared as the word: held while 'door', off when it "
      "reads 'gate'", mid == 9 and e._stats["fn_r1_out"] == 0,
      (mid, e._stats["fn_r1_out"]))

print("== hold: Interval with once per object")
appfx._trigger_memory.clear()
e = engine(rule(oid_source="src", rate="once_per_oid", rate_s=0,
                out={"type": "number", "value": "5", "hold_s": 3, **IV}))
seq = [(1, {"a"})] * 4 + [(1, {"a", "b"})] * 4 + [(0, set())] * 3
res = [frame(e, t, v, who=w) for t, (v, w) in enumerate(seq)]
outs = [o for _ev, o in res]
fires = [t for t, (ev, _o) in enumerate(res) if ev]
check("a new object while it is held is a new fire, which keeps it on and "
      "restarts the interval", fires == [0, 4], fires)
check("...held while the trigger stays as it was, off at the first re-check "
      "after it let go (fire 4, re-check 7 kept, 10 off)",
      outs == [5] * 10 + [0], outs)

print("== hold: Hold N is the hold it always was")
e = engine(rule(out={"type": "number", "value": "9", "hold_s": 3}))
outs = [frame(e, t, v)[1] for t, v in enumerate([1] * 6)]
check("Hold 3 (no 'hold' key, as every saved one compiles): on 3 s, then off "
      "though the trigger is still active", outs == [9, 9, 9, 0, 0, 0], outs)

print("== hold: the choice compiles, old saved Triggers migrate")
for data, want, word in (
        ({"payload_type": "number", "payload": "9"}, {},
         "no hold saved: Real-time"),
        ({"payload_type": "number", "payload": "9", "out_hold_s": 0}, {},
         "saved hold 0: Real-time, compiled exactly as before"),
        ({"out_hold_s": 10}, {"hold_s": 10.0},
         "saved hold 10: Hold 10, compiled exactly as before"),
        ({"out_hold_s": 0.5}, {"hold_s": 0.5},
         "saved hold 0.5 (the old field took it): Hold 0.5, still compiles"),
        ({"out_hold": "realtime", "out_hold_s": 10, "payload_type": "number",
          "payload": "9"}, {},
         "Real-time chosen: a leftover seconds value is ignored"),
        ({"out_hold": "interval", "out_hold_s": 10},
         {"hold_s": 10.0, "hold": "interval"}, "Interval 10 chosen"),
        ({"out_hold": "hold", "out_hold_s": 10}, {"hold_s": 10.0},
         "Hold 10 chosen: the same compile as a saved hold 10")):
    c = compile_(trigger=data)
    t = trig_of(c) or {}
    got = {k: v for k, v in (t.get("out") or {}).items()
           if k in ("hold_s", "hold")}
    check(word, not c["errors"] and got == want, f"{c['errors']} {t}")
for data, word in (({"out_hold": "interval", "out_hold_s": 0},
                    "the hold interval must be between 1 and 86400"),
                   ({"out_hold": "interval", "out_hold_s": 86401},
                    "the hold interval must be between 1 and 86400"),
                   ({"out_hold": "hold", "out_hold_s": 0.5},
                    "the hold time must be between 1 and 86400"),
                   ({"out_hold": "forever"},
                    "hold the payload must be real-time, re-checked")):
    msgs = errors_of(compile_(trigger=data))
    check(f"refused: {word}", any(word in m for m in msgs), repr(msgs))
check("Real-time alone is not an Advanced setting (a fresh node compiles as "
      "a plain Trigger)",
      not trigger_out.is_set({"out_hold": "realtime"})
      and not trigger_out.is_set({"out_hold": "", "out_hold_s": 0})
      and trigger_out.is_set({"out_hold_s": 3})
      and trigger_out.is_set({"out_hold": "interval", "out_hold_s": 3})
      and trigger_out.is_set({"out_hold": "hold", "out_hold_s": 3}))
check("the hold mode reads an old node's seconds",
      [trigger_out.hold_mode(d) for d in ({}, {"out_hold_s": 0},
                                          {"out_hold_s": 4},
                                          {"out_hold": "Realtime",
                                           "out_hold_s": 4},
                                          {"out_hold": "interval",
                                           "out_hold_s": 4})]
      == ["realtime", "realtime", "hold", "realtime", "interval"])
check("a compiled config from before (hold_s 3) validates unchanged: Hold 3",
      fx._validate_trigger(dict(PINNED, out={"hold_s": 3}), "expr", "R")
      .get("out") == {"hold_s": 3.0})
check("...and an Interval one keeps its mode through the validator",
      fx._validate_trigger(dict(PINNED, out={"hold_s": 3, "hold": "interval"}),
                           "expr", "R").get("out")
      == {"hold_s": 3.0, "hold": "interval"})
check("the app reads the three: Real-time, Hold, Interval",
      [(o["hold_s"], o["interval"]) for o in (
          appfx._parse_out({}), appfx._parse_out({"hold_s": 3}),
          appfx._parse_out({"hold_s": 3, "hold": "interval"}))]
      == [(0.0, False), (3.0, False), (3.0, True)])

print("== at most once every N seconds")
appfx._trigger_memory.clear()
e = engine(rule(out={}, rate="cooldown", rate_s=10))
fires = [t for t, v in enumerate([1] * 30) if frame(e, t, v)[0]]
check("a moment that keeps holding fires once (a ceiling, not a repeat)",
      fires == [0], fires)
appfx._trigger_memory.clear()      # the clock outlives an engine (below)
e = engine(rule(out={}, rate="cooldown", rate_s=10))
seq = [1, 0] * 15
fires = [t for t, v in enumerate(seq) if frame(e, t, v)[0]]
check("a flickering moment fires at most every 10 s", fires == [0, 10, 20], fires)
# Survives a reload: a rebuilt engine with the same rule keeps the clock.
e2 = engine(rule(out={}, rate="cooldown", rate_s=10))
fires = [t for t, v in ((21, 1), (22, 0), (25, 1), (26, 0), (31, 1))
         if frame(e2, t, v)[0]]
check("...and the clock survives a workflow save", fires == [31], fires)

print("== once per object")
appfx._trigger_memory.clear()
e = engine(rule(out={"type": "string", "value": "new"}, rate="once_per_oid",
                oid_source="src"))
log = []
for t, v, who in [(0, 1, {1, 2}), (1, 1, {1, 2}), (2, 1, {1, 2, 3}),
                  (3, 0, {1, 2, 3}), (4, 1, {1, 2, 3}), (5, 1, {4})]:
    ev, _out = frame(e, t, v, who)
    log += [(t, x.get("source_oid"), x["detail"]["new"]) for x in ev]
check("the first objects fire once, a newcomer fires, the same never again "
      "(not even when the moment rises again)",
      log == [(0, 1, [1, 2]), (2, 3, [3]), (5, 4, [4])], log)
e = engine(rule(out={}, rate="once_per_oid", oid_source="src"))
log = []
for t, v, who in [(0, 0, {9}), (1, 1, {9})]:
    log += [t for _x in frame(e, t, v, who)[0]]
check("an object seen while the moment did not hold still fires when it does",
      log == [1], log)
e_reload = engine(rule(out={"type": "string", "value": "new"},
                       rate="once_per_oid", oid_source="src"))
log = [x.get("source_oid") for t in (6, 7)
       for x in frame(e_reload, t, 1, {1, 2, 3, 4, 5})[0]]
check("a workflow save remembers who fired: only the newcomer fires",
      log == [5], log)
e_cam2 = engine(rule(out={}, rate="once_per_oid", oid_source="src"), scope="cam2")
log = [x.get("source_oid") for x in frame(e_cam2, 8, 1, {1})[0]]
check("another camera's object 1 is a different object", log == [1], log)
e_changed = engine(rule(out={}, rate="cooldown", rate_s=5))
frame(e_changed, 9, 1)
e_back = engine(rule(out={}, rate="once_per_oid", oid_source="src"))
log = [x.get("source_oid") for x in frame(e_back, 10, 1, {1})[0]]
check("changing the setting starts the memory afresh", log == [1], log)
# Bounded: an object out of view TRIGGER_OID_KEEP_S is forgotten, and no
# more than TRIGGER_OID_MAX are kept.
appfx._trigger_memory.clear()
e = engine(rule(out={}, rate="once_per_oid", oid_source="src"))
frame(e, 0, 1, {1})
frame(e, appfx.TRIGGER_OID_KEEP_S + 5, 1, {2})
mem = appfx._trigger_memory[("", "r1")]["seen"]
check("an object gone longer than the keep time is forgotten",
      set(mem) == {2}, repr(mem))
frame(e, appfx.TRIGGER_OID_KEEP_S + 6, 1,
      set(range(100, 100 + appfx.TRIGGER_OID_MAX + 50)))
check("...and the memory never holds more than the cap",
      len(appfx._trigger_memory[("", "r1")]["seen"]) <= appfx.TRIGGER_OID_MAX,
      len(appfx._trigger_memory[("", "r1")]["seen"]))

print("== an old trigger runs as it always did")
plain = engine(rule(cooldown_s=10))
fires = [t for t in range(31) if frame(plain, t, 1)[0]]
check("no Advanced: the alarm's cooldown still repeats on its clock",
      fires == [0, 10, 20, 30], fires)
check("...and no output key is published", frame(plain, 31, 1)[1] == "absent")
parsed = rule().triggers[0]
check("...parsed with no output and no rate",
      parsed["out"] is None and parsed["rate"] == "", repr(parsed))
check("once per object without a who is dropped, not left meaning nothing",
      rule(rate="once_per_oid").triggers[0]["rate"] == "")

# -- 5. the AI switch ----------------------------------------------------------
print("== with the AI off a detection-fed Trigger publishes nothing")
src = Function({"id": "crowd", "type": "frame_count", "name": "c", "params": {}})
moment = Function({"id": "r1", "type": "expr", "name": "r",
                   "params": {"expr": "fn_crowd_count > 0"},
                   "triggers": [{"metric": "value", "op": ">", "value": 0,
                                 "cooldown_s": 0,
                                 "out": {"type": "string", "value": "ALARM",
                                         "idle_type": "string",
                                         "idle_value": "ok"}}]})
reader = Function({"id": "echo", "type": "expr", "name": "e",
                   "params": {"expr": "fn_r1_out"}})
eng = FunctionEngine([src, moment, reader], state_dir=None)
person = [{"label": "person", "box": [10, 10, 50, 50], "track_id": 1,
           "score": 0.9}]
eng.update({}, [], {}, {}, 640, 480, now=0)
check("idle while nobody is there", eng.stats().get("fn_r1_out") == "ok",
      eng.stats().get("fn_r1_out"))
eng.update({}, person, {}, {}, 640, 480, now=1)
check("the payload while somebody is", eng.stats().get("fn_r1_out") == "ALARM",
      eng.stats().get("fn_r1_out"))
eng.update({}, person, {}, {}, 640, 480, now=2)
check("...which a Formula reads (a frame later, the triggers run last)",
      eng.stats().get("fn_echo_value") == "ALARM", eng.stats().get("fn_echo_value"))
eng.set_inactive(["crowd", "r1"])
fired = eng.update({}, [], {}, {}, 640, 480, now=3)
st = eng.stats()
check("AI off: neither the payload nor the idle payload is published",
      "fn_r1_out" not in st, repr(st))
check("...no event is raised", not fired, repr(fired))
eng.update({}, [], {}, {}, 640, 480, now=4)
check("...and a Formula reading it does not see a stale payload",
      eng.stats().get("fn_echo_value") != "ALARM", eng.stats().get("fn_echo_value"))
eng.set_inactive([])
eng.update({}, [], {}, {}, 640, 480, now=5)
check("AI back on with nobody there: idle again",
      eng.stats().get("fn_r1_out") == "ok", eng.stats().get("fn_r1_out"))

# -- 6b. ...and at runtime --------------------------------------------------
print("== at runtime a plain Trigger into Math reads 1 while the moment holds")
plain_moment = Function({"id": "r1", "type": "expr", "name": "r",
                         "params": {"expr": "fn_crowd_count > 0"},
                         "triggers": [{"metric": "value", "op": ">", "value": 0,
                                       "cooldown_s": 0}]})
plus = Function({"id": "plus", "type": "expr", "name": "p",
                 "params": {"expr": "(fn_r1_active + 0)"}})
eng = FunctionEngine([src, plain_moment, plus], state_dir=None)
merged = {}


def tick(dets, now):
    # run.py hands the engine LAST frame's merged stats, which is where a
    # formula reads a trigger's active from (the triggers run last).
    eng.update({}, dets, {}, dict(merged), 640, 480, now=now)
    merged.update(eng.stats())
    return eng.stats().get("fn_plus_value")


reads = [tick(d, t) for t, d in enumerate([[], person, person, [], []])]
check("0 while nobody is there, 1 while somebody is (a frame late, the "
      "triggers run last), 0 again after", reads == [0, 0, 1, 1, 0], reads)

# -- 7. a Trigger into Modbus Write and MQTT Publish ---------------------------
print("== a Trigger reaches Modbus Write and MQTT Publish")
from app import outputs as appout                                 # noqa: E402

for src_t, tgt_t, handle in (("output.trigger", "output.mqtt_publish", ""),
                             ("output.trigger", "output.modbus_write", "in"),
                             ("output.trigger", "output.modbus_write", "trig")):
    check(f"the server allows {src_t} -> {tgt_t} {handle or 'in'}",
          wf.edge_allowed(src_t, tgt_t, handle)[0],
          wf.edge_allowed(src_t, tgt_t, handle)[1])


def chain(trig=None, fc=6):
    """Trigger -> MQTT Publish, -> Modbus Write's value port, and -> a second
    Modbus Write's TRIGGER port (an In Frame count on its value port)."""
    return wf.compile_graph(graph(trigger=trig, extra_nodes=[
        node("pub", "output.mqtt_publish", topic="x/y"),
        node("mb", "output.modbus_write", server="plc", address=1, fc=fc),
        node("fr2", "analytics.frame", fnId="n", name="N", mode="realtime"),
        node("mb2", "output.modbus_write", server="plc", address=2)],
        extra_edges=[
            {"id": "w3", "source": "t1", "target": "pub"},
            {"id": "w4", "source": "t1", "target": "mb", "targetHandle": "in"},
            {"id": "w5", "source": "src", "target": "fr2"},
            {"id": "w6", "source": "fr2", "target": "mb2", "targetHandle": "in"},
            {"id": "w7", "source": "t1", "target": "mb2", "targetHandle": "trig"}]),
        [], tables={})


def outs(c):
    return {o["id"]: o for o in c["outputs"]}


c = chain()
o = outs(c)
check("a plain Trigger: the three wires compile", not c["errors"], repr(c["errors"]))
check("MQTT Publish publishes the moment's own 0/1, when the Trigger says "
      "(on change and on each of its fires), with no clock of its own",
      o["wf_pub"]["key"] == "fn_many_b0_value" and o["wf_pub"]["trigger"] == "any"
      and o["wf_pub"]["mode"] == "change" and o["wf_pub"]["beat"] == "many_b0"
      and "interval_s" not in o["wf_pub"],
      repr(o.get("wf_pub")))
check("Modbus Write's value port writes it whenever it changes",
      o["wf_mb"]["key"] == "fn_many_b0_value" and o["wf_mb"]["mode"] == "change",
      repr(o.get("wf_mb")))
check("...and on the trigger port it writes the value wire when the Trigger "
      "fires", o["wf_mb2"]["mode"] == "trigger"
      and o["wf_mb2"]["function"] == "many_b0" and o["wf_mb2"]["key"] == "fn_n_count",
      repr(o.get("wf_mb2")))
for trig in ({"payload_type": "number", "payload": "7"},
             {"payload_type": "boolean", "payload": "true"},
             {"payload_type": "json", "payload": "7"},
             {"payload_type": "json", "payload": "true"}):
    c = chain(trig)
    o = outs(c)
    check(f"an Advanced Trigger ({trig['payload_type']} {trig['payload']}): "
          "both read its payload key",
          not c["errors"] and o["wf_pub"]["key"] == "fn_many_b0_out"
          and o["wf_mb"]["key"] == "fn_many_b0_out"
          and o["wf_mb2"]["function"] == "many_b0",
          repr(c["errors"] or o))
for trig in ({"payload_type": "string", "payload": "door open"},
             {"payload_type": "json", "payload": '{"a": 1}'},
             {"payload_type": "timestamp"},
             {"idle_type": "string", "idle_payload": "ok"},
             {"payload_type": "number", "payload": "70000"}):
    c = chain(trig)
    o = outs(c)
    msgs = errors_of(c, "mb")
    check(f"{trig}: refused on a REGISTER write, on the Modbus node, in plain "
          "words", msgs and all("FC 6" in m or "register" in m for m in msgs)
          and not re.search(r"\b(HTTP|[45]\d\d)\b", msgs[0]), repr(c["errors"]))
    check("...while MQTT Publish and the trigger port still compile",
          "wf_pub" in o and "wf_mb2" in o
          and o["wf_pub"]["key"] == "fn_many_b0_out", repr(o))
    c = chain(trig, fc=5)
    check("...and a COIL write takes it, as on or off",
          not c["errors"] and outs(c)["wf_mb"]["key"] == "fn_many_b0_out",
          repr(c["errors"]))
c = chain({"payload_type": "string", "payload": "x"}, fc=16)
check("FC 16 is a register write too", errors_of(c, "mb"), repr(c["errors"]))

# The app: what goes out on the wire.
sent, written = [], []
real_mqtt, real_submit = appout._mqtt, appout._modbus_write_submit
appout._mqtt = lambda doc: sent.append((doc.get("topic"), doc.get("payload")))
appout._modbus_write_submit = lambda o, job: written.append(job["value"]) or True
try:
    o = outs(chain())
    appout.publish([o["wf_pub"]], {"fn_many_b0_value": 1}, now=1000.0)
    appout.publish([o["wf_pub"]], {"fn_many_b0_value": 0}, now=2000.0)
    check("MQTT Publish sends the plain Trigger's 1, then its 0",
          sent == [("x/y", "1"), ("x/y", "0")], repr(sent))
    appout._modbus_write_tick(o["wf_mb"], {"fn_many_b0_value": 1}, 1000.0)
    appout._modbus_write_tick(o["wf_mb"], {"fn_many_b0_value": 1}, 1001.0)
    appout._modbus_write_tick(o["wf_mb"], {"fn_many_b0_value": 0}, 1002.0)
    check("Modbus Write writes 1 when it rises and 0 when it falls, once each",
          written == [1, 0], repr(written))
    written.clear()
    fired = appout.fire([o["wf_mb2"]],
                        {"type": "alert", "id": "many_b0",
                         "detail": {"function": "many_b0"}},
                        stats={"fn_n_count": 3})
    check("...and on the trigger port the Trigger's fire writes the value wire",
          written == [3], repr(written) + repr(fired))
    written.clear()
    sent.clear()
    adv = outs(chain({"payload_type": "number", "payload": "7"}))
    for t, val in ((3000.0, 7), (4000.0, 0), (5000.0, "door"),
                   (6000.0, '{"a":[1,2]}')):
        appout.publish([adv["wf_pub"]], {"fn_many_b0_out": val}, now=t)
    check("MQTT Publish sends the Advanced payload as it is: 7, 0, text, JSON",
          [p for _t, p in sent] == ["7", "0", "door", '{"a":[1,2]}'], repr(sent))
    appout._modbus_write_tick(adv["wf_mb"], {"fn_many_b0_out": 7}, 7000.0)
    appout._modbus_write_tick(adv["wf_mb"], {"fn_many_b0_out": 0}, 7001.0)
    check("Modbus Write writes the payload, then the idle 0", written == [7, 0],
          repr(written))

    # The Trigger's hold decides when MQTT Publish publishes, end to end:
    # the engine's own output and fires, read by the app's publish tick.
    def published(fn, seq, key="fn_r1_out"):
        """[(t, payload)] an MQTT Publish after `fn` sends for `seq` of
        (value, who) frames, one a second."""
        appfx._trigger_memory.clear()
        appout._follow_state.clear()
        sent.clear()
        e, doc = engine(fn), {"id": "wf_follow", "type": "mqtt_publish",
                              "enabled": True, "topic": "x/y", "key": key,
                              "mode": "change", "beat": "r1"}
        got = []
        for t, (v, who) in enumerate(seq):
            frame(e, t, v, who=who)
            n = len(sent)
            appout.publish([doc], dict(e._stats), now=1000.0 + t)
            got += [(t, p) for _tp, p in sent[n:]]
        return got

    nine = {"type": "number", "value": "9"}
    got = published(rule(out=dict(nine)), [(v, None) for v in (0, 1, 1, 1, 0, 0)])
    check("Real-time: the state at start, the payload when it fires, the idle "
          "payload when it lets go, nothing in between",
          got == [(0, "0"), (1, "9"), (4, "0")], repr(got))
    got = published(rule(out={**nine, "hold_s": 3, "hold": "interval"}),
                    [(1, None)] * 10 + [(0, None)] * 4)
    check("Interval 3: the payload at the fire and again every 3 s while the "
          "trigger holds, then the idle payload at the first re-check after",
          got == [(0, "9"), (3, "9"), (6, "9"), (9, "9"), (12, "0")], repr(got))
    got = published(rule(out={**nine, "hold_s": 3}), [(1, None)] * 6)
    check("For 3 seconds: the payload once, the idle payload 3 s later",
          got == [(0, "9"), (3, "0")], repr(got))
    got = published(rule(oid_source="src", rate="once_per_oid", rate_s=0,
                         out=dict(nine)),
                    [(1, {"a"})] * 3 + [(1, {"a", "b"})] * 3 + [(0, set())])
    check("once per object: the payload once per new object (same payload "
          "again), the idle payload when it lets go",
          got == [(0, "9"), (3, "9"), (6, "0")], repr(got))
    got = published(rule(cooldown_s=2), [(1, None)] * 5 + [(0, None)],
                    key="fn_r1_value")
    check("a plain Trigger raising again every 2 s: published on each fire, "
          "then 0 when it lets go",
          got == [(0, "1"), (2, "1"), (4, "1"), (5, "0")], repr(got))
finally:
    appout._mqtt, appout._modbus_write_submit = real_mqtt, real_submit

# The app and core agree on the payload types the Trigger takes.
check("app and core share the payload types",
      appfx.INJECT_TYPES == fx.INJECT_TYPES)

# -- 8. a Debug probe on an Advanced Trigger -----------------------------------
# Tok Aba (2026-10-05, v1.0.16): Trigger "People_Trigger", In from a Compare
# and OID from the detection rule, warn alert, payload 17, once per object,
# hold 10 s, idle 0, wired into a Debug. The Debug said "waiting" and
# "People_Trigger - holding - nothing published yet" for ever. The runtime was
# right all along (0 from the first tick, 17 for the hold, 0 again); the
# canvas resolved the probe to fn_people_active -- it walked the OID wire to
# the detection rule, whose `active` nothing publishes because the moment is
# the branch port's -- instead of fn_many_b0_out.
print("== a Debug probe on an Advanced Trigger reads fn_<rule>_out")
g = graph(trigger={"alert": True, "severity": "warn", "payload_type": "number",
                   "payload": "17", "rate": "once_per_oid", "out_hold_s": 10,
                   "idle_type": "number", "idle_payload": "0"},
          oid=True,
          extra_nodes=[node("dbg", "common.debug")],
          extra_edges=[{"id": "e4", "source": "fr", "target": "t1",
                        "sourceHandle": "OID", "targetHandle": "oid"},
                       {"id": "e5", "source": "t1", "target": "dbg"}])
c = wf.compile_graph(g, [], tables={})
check("Tok Aba's graph compiles", not c["errors"], c["errors"])
t1 = next(n for n in g["nodes"] if n["id"] == "t1")
check("the compiler stamps the Trigger with fn_many_b0_out, the key the "
      "canvas must read", t1["data"].get("_out_key") == "fn_many_b0_out",
      t1["data"].get("_out_key"))
trig = next(f for f in c["functions"] if f["id"] == "many_b0")["trigger"]
check("...once per object on the rule the OID wire names, Hold 10 (his "
      "saved hold, migrated unchanged: no Interval key), idle 0",
      trig.get("rate") == "once_per_oid" and trig.get("oid_source") == "crowd"
      and trig["out"].get("hold_s") == 10 and "hold" not in trig["out"]
      and trig["out"].get("idle_value") == "0",
      trig)
e = engine(rule("many_b0", oid_source="src", rate="once_per_oid", rate_s=0,
                out={"type": "number", "value": "17", "idle_type": "number",
                     "idle_value": "0", "hold_s": 10}))
_ev, out0 = frame(e, 0, 0, who=set(), fid="many_b0")
check("the runtime publishes the idle 0 BEFORE anyone has triggered it",
      out0 == 0, out0)
check("...and never the detection rule's own `active` under this engine",
      "fn_crowd_active" not in e._stats and "fn_many_b0_active" in e._stats,
      sorted(e._stats))
outs = [frame(e, t, 1, who={"p1"}, fid="many_b0")[1] for t in range(1, 14)]
check("17 for the 10 s hold with the person still there, then 0 (once per "
      "object)", outs == [17] * 10 + [0, 0, 0], outs)
_ev, out2 = frame(e, 14, 1, who={"p1", "p2"}, fid="many_b0")
check("17 again for the new object", out2 == 17, out2)
check("a shared rule's payload on two cameras is the moment held on either, "
      "not the two payloads added (run._merge_metric)",
      apprun._merge_metric("fn_many_b0_out", 17, 0) == 17
      and apprun._merge_metric("fn_many_b0_out", 17, 17) == 17
      and apprun._merge_metric("fn_many_b0_out", 0, 0) == 0)
# The canvas side, pinned by text: faceReads in pages/Workflow.jsx resolves a
# probe's wire, and a node cannot be rendered with its edges from here.
page = read_text("frontend", "src", "pages", "Workflow.jsx")
behind = page.split("const ruleBehind = (nd, hops = 0) => {", 1)[1] \
             .split("return null;\n    };", 1)[0]
check("the probe's walk behind a Trigger skips the zone and OID wires (and "
      "the duplicate guard's), as the compiler does",
      '["zone", "oid", "cur_oid", "cur_value"].includes(String(e.targetHandle || ""))'
      in behind,
      behind[:300])
check("...and resolves a branch port to fn_<base>_b<n>, the rule the "
      "Trigger decorates", "_b${Number(way[1])}" in behind
      and "isBranch(p.type)" in behind)
cond = page.split("if (CONDITIONS.includes(src.type)) {", 1)[1] \
           .split("continue;\n      }", 1)[0]
check("an Advanced Trigger's probe reads fn_<rule>_out under the Trigger's "
      "own name", "triggerAdvanced(src.data)" in cond
      and "key: `fn_${behind.name}_out`" in cond and "label: name, node: src.id" in cond,
      cond)
check("...a plain Trigger's probe reads the moment's `active` as before",
      "key: `fn_${behind.name}_active`" in cond and "- holding" in cond)
nodes_src = read_text("frontend", "src", "workflow", "nodes.jsx")
adv = nodes_src.split("export function triggerAdvanced(d) {", 1)[1].split("\n}\n", 1)[0]
check("the canvas's triggerAdvanced mirrors trigger_out.is_set (payload, "
      "idle, rate, hold)",
      all(k in adv for k in ("payload_type", "idle_type", "rate", "holdMode")),
      adv)
check("a probe on a node that waits for detection says AI off, not "
      "'nothing published yet'",
      "waitsForDetection.has(spec.node)" in nodes_src
      and "waits for detection" in nodes_src
      and "AI off - waits for detection"
          in read_text("frontend", "src", "workflow", "DebugPanel.jsx"))
check("...the live poll feeds the canvas the list the compiler already sends",
      "new Set(live.waits_for_detection)" in page
      and "waitsForDetection" in page.split("<WorkflowCtx.Provider", 1)[1][:3000])

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
