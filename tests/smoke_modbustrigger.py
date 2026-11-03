# Modbus Read on a trigger: interval 0 (client UAT 2026-10-04, item 5).
#
# The client's PLC rig reads a register when something HAPPENS -- an operator
# presses Inject, a shift starts, a count crosses a line -- not every few
# seconds forever. So the Read node's interval may be 0, and at 0 the node
# grows a Trigger port: an Inject, a Schedule or a Condition wired into it
# fires exactly one read per rising edge.
#
# What is pinned, and why each one is the part that bites:
#
#   * the compile: 0 with an Inject wired in becomes params.trigger =
#     {fn, metric} (the Inject's `fired`, a Schedule's `due`, a rule's
#     primary value, a Condition's rule's `alert`); 0 with nothing wired in
#     is an error on the node that says what to do, because a reader that
#     would never read is the kind of bug nobody notices for a week;
#   * both validators (core and the app's copy) agree -- a doc core accepted
#     and the app refused would be a function that silently vanished;
#   * the runtime: the poller sleeps on its kick, one kick = one read, a
#     trigger that STAYS high reads once, and the next rising edge reads again;
#   * the 2 s floor still holds for a clock -- interval 1 reads every 2 s,
#     and a missing interval is still the old default, not a trigger;
#   * both edge rules (server and canvas) let the trigger wire be drawn, and
#     refuse it on every other variable maker as before.
#
#     py -3 tests/smoke_modbustrigger.py
import io
import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import buses                                          # noqa: E402
from app import functions as fx                                # noqa: E402
from core.services import functions as fn_service              # noqa: E402
from core.services import workflow as wf                       # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def raises(call, word=""):
    try:
        call()
    except ValueError as exc:
        return word in str(exc)
    return False


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 1, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], tables={})


def fn_of(out, ftype):
    return next((f for f in out["functions"] if f["type"] == ftype), None)


# -- 1. the compile -----------------------------------------------------------
rd = dict(name="Flow meter", fc=3, address=7, quantity=1, interval_s=0,
          server="plc1", unit=1)
g, out = compile_([node("i1", "common.inject", name="Kick"),
                   node("r1", "variable.rs485", **rd)],
                  [{"id": "e1", "source": "i1", "target": "r1",
                    "targetHandle": "trig"}])
rfn, ifn = fn_of(out, "modbus_read"), fn_of(out, "inject")
check("Inject -> Read(trig) compiles", not out["errors"] and rfn and ifn, out["errors"])
check("...interval 0 kept, trigger = the Inject's `fired`",
      rfn and rfn["params"]["interval_s"] == 0
      and rfn["params"]["trigger"] == {"fn": ifn["id"], "metric": "fired"},
      rfn and rfn["params"])

g, out = compile_([node("s1", "variable.schedule", name="Shift"),
                   node("r1", "variable.rs485", **rd)],
                  [{"id": "e1", "source": "s1", "target": "r1",
                    "targetHandle": "trig"}])
rfn, sfn = fn_of(out, "modbus_read"), fn_of(out, "schedule")
check("Schedule -> Read(trig): trigger = the Schedule's `due`",
      not out["errors"] and rfn and sfn
      and rfn["params"]["trigger"] == {"fn": sfn["id"], "metric": "due"},
      (out["errors"], rfn and rfn["params"]))

# Through a Condition: the rule's alert, not a value.
g, out = compile_([node("c1", "variable.number", name="Level", value=3),
                   node("w1", "logic.when", name="Full", mode="above", value=2),
                   node("r1", "variable.rs485", **rd)],
                  [{"id": "e1", "source": "c1", "target": "w1"},
                   {"id": "e2", "source": "w1", "target": "r1",
                    "targetHandle": "trig"}])
rfn = fn_of(out, "modbus_read")
rule = next((f for f in out["functions"] if f["id"] != (rfn or {}).get("id")), None)
check("Condition -> Read(trig): trigger = that rule's alert",
      not out["errors"] and rfn and rule
      and rfn["params"]["trigger"] == {"fn": rule["id"], "metric": "alert"}
      and rule.get("triggers"),
      (out["errors"], rfn and rfn["params"], rule))

g, out = compile_([node("r1", "variable.rs485", **rd)], [])
check("interval 0 with nothing wired in is an error on the node",
      any(e["node"] == "r1" and "interval 0 means read on a trigger" in e["message"]
          and "set an interval" in e["message"] for e in out["errors"]),
      out["errors"])

g, out = compile_([node("i1", "common.inject", name="Kick"),
                   node("i2", "common.inject", name="Kick 2"),
                   node("r1", "variable.rs485", **rd)],
                  [{"id": "e1", "source": "i1", "target": "r1", "targetHandle": "trig"},
                   {"id": "e2", "source": "i2", "target": "r1", "targetHandle": "trig"}])
check("two triggers into one Read is refused",
      any(e["node"] == "r1" and "ONE trigger" in e["message"] for e in out["errors"]),
      out["errors"])

g, out = compile_([node("r1", "variable.rs485", **{**rd, "interval_s": 1})], [])
rfn = fn_of(out, "modbus_read")
check("interval 1 still clamps to the 2 s floor, no trigger",
      not out["errors"] and rfn and rfn["params"]["interval_s"] == 2
      and "trigger" not in rfn["params"], (out["errors"], rfn and rfn["params"]))

# -- 2. the validators agree ---------------------------------------------------
doc = {"id": "flow", "name": "Flow", "type": "modbus_read",
       "params": {"fc": 3, "address": 7, "quantity": 1, "interval_s": 0,
                  "server": "plc1", "trigger": {"fn": "kick", "metric": "fired"}}}
ok_core = fn_service.validate_function(dict(doc, params=dict(doc["params"])))
check("core: 0 + trigger accepted as is",
      ok_core["params"]["interval_s"] == 0
      and ok_core["params"]["trigger"] == {"fn": "kick", "metric": "fired"},
      ok_core["params"])
check("core: 0 without a trigger is refused, and says what to do",
      raises(lambda: fn_service.validate_function(
          {**doc, "params": {k: v for k, v in doc["params"].items() if k != "trigger"}}),
          "wire an Inject"))
clamped = fn_service.validate_function(
    {**doc, "params": {**doc["params"], "interval_s": 1}})
check("core: 1 clamps to 2 (trigger ignored)", clamped["params"]["interval_s"] == 2
      and "trigger" not in clamped["params"], clamped["params"])
missing = fn_service.validate_function(
    {**doc, "params": {k: v for k, v in doc["params"].items()
                       if k not in ("interval_s", "trigger")}})
check("core: a MISSING interval is still the old default, not a trigger",
      missing["params"]["interval_s"] == 2, missing["params"])

app_ok = fx.Function(dict(doc, params=dict(doc["params"])))
check("app: 0 + trigger accepted", app_ok.params["interval_s"] == 0
      and app_ok.params["trigger"] == {"fn": "kick", "metric": "fired"}, app_ok.params)
check("app: 0 without a trigger is refused",
      raises(lambda: fx.Function(
          {**doc, "params": {k: v for k, v in doc["params"].items() if k != "trigger"}}),
          "interval 0"))
check("app: 1 clamps to 2",
      fx.Function({**doc, "params": {**doc["params"], "interval_s": 1}})
      .params["interval_s"] == 2)
check("app: the poller's signature tells a triggered reader from a 2 s one",
      fx._modbus_sig(app_ok) != fx._modbus_sig(
          fx.Function({**doc, "params": {**doc["params"], "interval_s": 2}})))

# -- 3. the runtime: one kick, one read ----------------------------------------
buses.set_clients([{"id": "plc1", "type": "tcp", "host": "10.0.0.9", "port": 502,
                    "unit_id": 1, "timeout_ms": 1000}])
reads = []
real_read = buses.modbus_read
buses.modbus_read = lambda cfg_, address, count=1, kind="holding": (
    reads.append((address, count, kind)) or [42])
state = tempfile.mkdtemp()
try:
    kick = fx.Function({"id": "kick", "name": "Kick", "type": "inject",
                        "enabled": True,
                        "params": {"ptype": "number", "payload": "1"}})
    flow = fx.Function(dict(doc, params=dict(doc["params"])))
    fx._modbus_state.pop("flow", None)
    eng = fx.FunctionEngine([kick, flow], state_dir=state)

    def tick(now):
        eng.update({"crossing_events": []}, [], {}, {}, 640, 480, now=now)
        return eng.stats()

    def settle(n, timeout=3.0):
        end = time.time() + timeout
        while len(reads) < n and time.time() < end:
            time.sleep(0.02)
        time.sleep(0.15)   # a stray second read would land in here
        return len(reads)

    tick(1000.0)
    with fx._modbus_lock:
        st = fx._modbus_state.get("flow")
    check("the triggered reader is spawned but has not read",
          st is not None and st["thread"].is_alive() and reads == [], reads)
    check("...and the node reports no value yet", eng.stats()["fn_flow_ok"] == 0)
    time.sleep(0.3)
    check("...still nothing after a while (no clock)", reads == [], reads)

    # A press: the master writes the file the Inject reads.
    import json
    with open(os.path.join(state, fx.INJECT_FILE), "w", encoding="utf-8") as f:
        json.dump({"kick": {"seq": 1, "ts": 1000.5}}, f)
    st_ = tick(1001.0)
    check("the Inject fired this tick", st_["fn_kick_fired"] == 1, st_)
    check("...and the Read read ONCE", settle(1) == 1
          and reads == [(7, 1, "holding")], reads)
    st_ = tick(1001.5)
    check("...the value landed on the node", st_["fn_flow_value"] == 42
          and st_["fn_flow_ok"] == 1, st_)
    tick(1002.0)
    tick(1003.0)
    check("more ticks without a press read nothing more", settle(2, 0.5) == 1, reads)

    with open(os.path.join(state, fx.INJECT_FILE), "w", encoding="utf-8") as f:
        json.dump({"kick": {"seq": 2, "ts": 1003.5}}, f)
    tick(1004.0)
    check("the next press reads again, once", settle(2) == 2, reads)

    # A trigger that stays HIGH (a rule's value) reads once per rise.
    reads.clear()
    fx._modbus_state.pop("flow", None)
    level_doc = dict(doc, params={**doc["params"],
                                  "trigger": {"fn": "lvl", "metric": "value"}})
    lvl = fx.Function({"id": "lvl", "name": "Level", "type": "expr",
                       "enabled": True, "params": {"expr": "fn_src_value"}})
    eng = fx.FunctionEngine([lvl, fx.Function(level_doc)], state_dir=state)

    def tick_level(now, v):
        eng.update({"crossing_events": []}, [], {}, {"fn_src_value": v}, 640, 480, now=now)

    tick_level(2000.0, 0)
    tick_level(2001.0, 3)
    check("a value going 0 -> 3 reads once", settle(1) == 1, reads)
    tick_level(2002.0, 5)
    tick_level(2003.0, 5)
    check("...and staying high reads no more", settle(2, 0.5) == 1, reads)
    tick_level(2004.0, 0)
    tick_level(2005.0, 1)
    check("...the next rise reads again", settle(2) == 2, reads)

    # A clock reader is untouched: interval 1 -> every 2 s, no kick needed.
    seen = []
    stop = threading.Event()
    buses.modbus_read = lambda cfg_, address, count=1, kind="holding": (
        seen.append(time.time()) or (stop.set() if len(seen) >= 2 else None) or [1])
    params = {"server": "plc1", "fc": 3, "address": 0, "quantity": 1, "interval_s": 1}
    t0 = time.time()
    fx._modbus_loop("clk", params, fx._modbus_sig(type("F", (), {"params": params})()), stop)
    check("interval 1 reads on a 2 s clock", len(seen) == 2 and 1.8 <= seen[1] - seen[0] <= 2.6,
          [round(s - t0, 2) for s in seen])
finally:
    buses.modbus_read = real_read
    with fx._modbus_lock:
        for st in fx._modbus_state.values():
            st["stop"].set()
        fx._modbus_state.clear()

# -- 4. the wiring rule, both sides ---------------------------------------------
for src in ("common.inject", "variable.schedule", "logic.when"):
    ok, why = wf.edge_allowed(src, "variable.rs485", "trig")
    check(f"server: {src} -> Read trig port is allowed", ok, why)
ok, why = wf.edge_allowed("common.inject", "variable.rs485", "in")
check("server: ...but not into its value side", not ok, why)
ok, why = wf.edge_allowed("common.inject", "variable.api", "trig")
check("server: ...and not into any other maker", not ok, why)
reg = read("frontend", "src", "workflow", "registry.js")
check("canvas: mirrors the rule",
      re.search(r'tgtType === "variable\.rs485" && String\(tgtHandle \|\| ""\) === "trig"'
                r'\s*&& \["variable", "analytics", "logic"\]\.includes\(s\.category\)', reg))
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
check("canvas: the Read node grows a Trigger port at interval 0",
      'props.type === "variable.rs485" && Number(d.interval_s ?? 5) === 0' in nodes_src
      and '{ id: "trig", label: "Trigger" }' in nodes_src)
page = read("frontend", "src", "pages", "Workflow.jsx")
check("inspector: the interval field allows 0 and says what 0 means",
      re.search(r'0 = read only when the trigger fires">\s*<input className="input" '
                r'type="number" min="0" max="3600"', page))

print()
print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
