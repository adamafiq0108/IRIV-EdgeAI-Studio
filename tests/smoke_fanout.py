# Fan-out and fan-in on the workflow canvas (client UAT 2026-10-04, item 6).
#
# The client's rule: anything that touches hardware -- Board I/O, MQTT, an
# Inject, a Detection Stream -- must let one output feed many nodes and let
# one input take many nodes. The audit is docs/team/tarong/2026-10-04-node-
# fanout.md; this pins what changed:
#
#   1. ONE-TO-MANY: one Inject (or any rule) wired into two outputs fires
#      both; one Board Input feeds two cards; the engine publishes one stat
#      that every reader reads, so nothing more is needed at run time
#   2. MANY-TO-ONE on an output: several rules into one Board Output, Buzzer,
#      Snapshot or MQTT Publish compile to ONE OUTPUT DOCUMENT PER RULE (the
#      first keeps the node's id), and the app fires each on its own moment;
#      the same rule wired in twice is one rule, not an ambiguity
#   3. the level-followers keep one wire, with a plain message: a PWM set to
#      follow a value cannot follow two
#   4. ONE BOARD INPUT PER TERMINAL: a second node reading DI0 is refused by
#      name, pointing at the first node's output
#   5. what stays one-to-one on purpose still does: a Card shows one value
#
#   py -3 tests/smoke_fanout.py
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                       # noqa: E402
from app import outputs as outputs_mod                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def inject(nid, name):
    return node(nid, "common.inject", name=name, ptype="number", payload="1")


def edge(src, tgt, handle=None, src_handle=None):
    e = {"id": f"e_{src}_{tgt}_{handle or ''}", "source": src, "target": tgt}
    if handle:
        e["targetHandle"] = handle
    if src_handle:
        e["sourceHandle"] = src_handle
    return e


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], tables={})


def fn_of(g, nid):
    return next(n for n in g["nodes"] if n["id"] == nid)["data"]["fnId"]


def errors_on(out, nid):
    return [e["message"] for e in out["errors"] if e["node"] == nid]


# -- 1. one-to-many -----------------------------------------------------------
g, out = compile_([inject("i1", "Kick"),
                   node("o1", "output.io_out", port="DO0"),
                   node("o2", "output.buzzer")],
                  [edge("i1", "o1"), edge("i1", "o2")])
check("one Inject into two outputs compiles", not out["errors"], out["errors"])
fid = fn_of(g, "i1")
bound = sorted(o["function"] for o in out["outputs"])
check("...both outputs fire on the same Inject", bound == [fid, fid], out["outputs"])

g, out = compile_([node("di", "variable.board_input", port="DI0", name="Door"),
                   node("c1", "dashboard.card"), node("c2", "dashboard.card")],
                  [edge("di", "c1"), edge("di", "c2")])
check("one Board Input feeds two cards", not out["errors"]
      and len(out["widgets"]) == 2
      and {w["key"] for w in out["widgets"]} == {f"fn_{fn_of(g, 'di')}_state"},
      (out["errors"], out["widgets"]))

# -- 2. many-to-one on an output ----------------------------------------------
g, out = compile_([inject("i1", "Kick"), inject("i2", "Kick 2"),
                   node("o1", "output.io_out", port="DO0")],
                  [edge("i1", "o1"), edge("i2", "o1")])
check("two rules into one Board Output compile", not out["errors"], out["errors"])
ids = [o["id"] for o in out["outputs"]]
check("...as one output document per rule, the first keeping the node's id",
      ids == ["wf_o1", "wf_o1_1"], ids)
check("...each bound to its own rule",
      [o["function"] for o in out["outputs"]] == [fn_of(g, "i1"), fn_of(g, "i2")],
      out["outputs"])
check("...both switching the same terminal",
      all(o["port"] == "DO0" and o["type"] == "io_out" for o in out["outputs"]),
      out["outputs"])

for otype, extra in (("output.buzzer", {}), ("output.snapshot", {}),
                     ("output.webhook", {"url": "http://10.0.0.1/x"})):
    g, out = compile_([inject("i1", "A"), inject("i2", "B"), node("o1", otype, **extra)],
                      [edge("i1", "o1"), edge("i2", "o1")])
    check(f"two rules into one {wf.REGISTRY[otype]['label']} compile to two documents",
          not out["errors"] and len(out["outputs"]) == 2, (out["errors"], out["outputs"]))

g, out = compile_([inject("i1", "A"), inject("i2", "B"),
                   node("p1", "output.mqtt_publish", topic="line/x")],
                  [edge("i1", "p1"), edge("i2", "p1")])
check("two values into one MQTT Publish compile", not out["errors"], out["errors"])
pubs = out["outputs"]
check("...each value published to the SAME topic, each on its own Inject's "
      "fire (the Publish has no clock of its own)",
      len(pubs) == 2 and {p["topic"] for p in pubs} == {"line/x"}
      and [p["key"] for p in pubs] == [f"fn_{fn_of(g, 'i1')}_value",
                                       f"fn_{fn_of(g, 'i2')}_value"]
      and [p.get("fired") for p in pubs] == [f"fn_{fn_of(g, 'i1')}_fired",
                                             f"fn_{fn_of(g, 'i2')}_fired"]
      and all(p.get("mode") == "fire" and "interval_s" not in p for p in pubs),
      pubs)

# The same rule wired in twice (the plain port and its named port) is ONE rule.
g, out = compile_([inject("i1", "A"), node("o1", "output.io_out", port="DO0")],
                  [edge("i1", "o1"), edge("i1", "o1", src_handle="value")])
check("the same rule wired in twice is one output, not an ambiguity",
      not out["errors"] and len(out["outputs"]) == 1, (out["errors"], out["outputs"]))

# The app fires each document on its own rule's event.
fired = []
real = outputs_mod._fire_one
outputs_mod._fire_one = lambda o, hooks, event, stats: fired.append(o["id"])
try:
    docs = [{"id": "wf_o1", "type": "io_out", "enabled": True, "trigger": "alert",
             "function": "a"},
            {"id": "wf_o1_1", "type": "io_out", "enabled": True, "trigger": "alert",
             "function": "b"}]
    outputs_mod.fire(docs, {"type": "alert", "detail": {"function": "a"}})
    outputs_mod.fire(docs, {"type": "alert", "detail": {"function": "b"}})
    outputs_mod.fire(docs, {"type": "alert", "detail": {"function": "c"}})
finally:
    outputs_mod._fire_one = real
check("the app fires each document on its own rule, in arrival order",
      fired == ["wf_o1", "wf_o1_1"], fired)

# -- 3. the level-followers keep one wire ---------------------------------------
g, out = compile_([inject("i1", "A"), inject("i2", "B"),
                   node("w1", "output.io_pwm", port="L0", source="value", when="always")],
                  [edge("i1", "w1"), edge("i2", "w1")])
msgs = errors_on(out, "w1")
check("a PWM following a value refuses two of them, in plain words",
      len(msgs) == 1 and "follows ONE value" in msgs[0] and "has 2 wired in" in msgs[0],
      out["errors"])
g, out = compile_([inject("i1", "A"), inject("i2", "B"),
                   node("w1", "output.io_pwm", port="L0", when="alert")],
                  [edge("i1", "w1"), edge("i2", "w1")])
check("...while a PWM set on alert takes both rules", not out["errors"]
      and len(out["outputs"]) == 2, (out["errors"], out["outputs"]))

# -- 4. one Board Input per terminal --------------------------------------------
g, out = compile_([node("d1", "variable.board_input", port="DI0", name="Door"),
                   node("d2", "variable.board_input", port="DI0", name="Door again"),
                   node("c1", "dashboard.card")],
                  [edge("d2", "c1")])
msgs = errors_on(out, "d2")
check("a second Board Input on DI0 is refused, naming the first",
      len(msgs) == 1 and "DI0 is already read by 'Door'" in msgs[0]
      and "wire that node's output here" in msgs[0], out["errors"])
check("...and the first one is not blamed", not errors_on(out, "d1"), out["errors"])
g, out = compile_([node("d1", "variable.board_input", port="DI0"),
                   node("d2", "variable.board_input", port="DI1")], [])
check("one node per terminal: DI0 and DI1 side by side are fine",
      not out["errors"], out["errors"])

# -- 5. what stays one-to-one ----------------------------------------------------
g, out = compile_([inject("i1", "A"), inject("i2", "B"), node("c1", "dashboard.card")],
                  [edge("i1", "c1"), edge("i2", "c1")])
check("a Card still shows ONE value", any("ONE value" in m for m in errors_on(out, "c1")),
      out["errors"])

# -- 6. Modbus Write fed by Injects (Boboiboy's rule, client UAT item 5) ------------
# Trigger port unwired and EVERY value wire a clock source: mode "fire", each
# press writes (the same payload twice is two writes), the newest-fired wire
# wins. Anything else keeps the one-value rule, said plainly.
MBW = {"server": "mb_x", "fc": 6, "address": 0, "quantity": 1}
g, out = compile_([inject("i1", "A"), inject("i2", "B"),
                   node("w1", "output.modbus_write", **MBW)],
                  [edge("i1", "w1", "in"), edge("i2", "w1", "in")])
check("two Injects into a Modbus Write value port compile", not out["errors"], out["errors"])
doc = out["outputs"][0] if out["outputs"] else {}
check("...as ONE write in fire mode, naming each Inject's fired and value keys",
      doc.get("mode") == "fire"
      and doc.get("sources") == [{"fired": f"fn_{fn_of(g, 'i1')}_fired",
                                  "key": f"fn_{fn_of(g, 'i1')}_value"},
                                 {"fired": f"fn_{fn_of(g, 'i2')}_fired",
                                  "key": f"fn_{fn_of(g, 'i2')}_value"}], doc)
g, out = compile_([inject("i1", "A"), node("w1", "output.modbus_write", **MBW)],
                  [edge("i1", "w1", "in")])
check("a single Inject with no trigger writes on every press too (fire, not change)",
      not out["errors"] and out["outputs"][0].get("mode") == "fire", out)
g, out = compile_([inject("i1", "A"), node("v1", "variable.number", value=3),
                   node("w1", "output.modbus_write", **MBW)],
                  [edge("i1", "w1", "in"), edge("v1", "w1", "in")])
msgs = errors_on(out, "w1")
check("an Inject AND a value into the value port is refused, in plain words",
      len(msgs) == 1 and "writes ONE value" in msgs[0] and "has 2 wired" in msgs[0],
      out["errors"])
g, out = compile_([inject("i1", "A"), inject("i2", "B"), inject("t", "T"),
                   node("w1", "output.modbus_write", **MBW)],
                  [edge("i1", "w1", "in"), edge("i2", "w1", "in"), edge("t", "w1", "trig")])
msgs = errors_on(out, "w1")
check("...and two Injects WITH a trigger wired too, saying why",
      len(msgs) == 1 and "only while the trigger port is unwired" in msgs[0], out["errors"])
try:
    from core.services import detector_control as dc
    dc._validate_output({"type": "modbus_write", "key": "fn_a_value", "mode": "fire",
                         **MBW})
    check("the validator refuses fire mode without its sources", False)
except ValueError as exc:
    check("the validator refuses fire mode without its sources", "fired keys" in str(exc), exc)
kept = dc._validate_output({"type": "modbus_write", "key": "fn_a_value", "mode": "fire",
                            "sources": [{"fired": "fn_a_fired", "key": "fn_a_value"}],
                            **MBW})
check("...and keeps them when given", kept["mode"] == "fire"
      and kept["sources"] == [{"fired": "fn_a_fired", "key": "fn_a_value"}], kept)

written = []
real_submit = outputs_mod._modbus_write_submit
outputs_mod._modbus_write_submit = lambda o, job: written.append(job["value"]) or True
try:
    fire_doc = {"id": "wf_w1", "type": "modbus_write", "mode": "fire", "key": "fn_a_value",
                "sources": [{"fired": "fn_a_fired", "key": "fn_a_value"},
                            {"fired": "fn_b_fired", "key": "fn_b_value"}], **MBW}
    base = {"fn_a_value": 5, "fn_b_value": 9, "fn_a_fired": 0, "fn_b_fired": 0}
    outputs_mod._modbus_write_tick(fire_doc, dict(base), 1000.0)
    check("fire mode: nothing fired, nothing written", written == [], written)
    outputs_mod._modbus_write_tick(fire_doc, {**base, "fn_a_fired": 1}, 1001.0)
    outputs_mod._modbus_write_tick(fire_doc, {**base, "fn_a_fired": 1}, 1002.0)
    check("...each press writes, the SAME payload included", written == [5, 5], written)
    outputs_mod._modbus_write_tick(fire_doc, {**base, "fn_b_fired": 1}, 1003.0)
    check("...the other Inject writes its own value", written == [5, 5, 9], written)
    outputs_mod._modbus_write_tick(fire_doc, {**base, "fn_a_fired": 1, "fn_b_fired": 1},
                                   1004.0)
    check("...two in one tick: the last wired wins", written == [5, 5, 9, 9], written)
    change_doc = {**fire_doc, "mode": "change", "sources": None}
    outputs_mod._mbw_seen.clear()
    outputs_mod._modbus_write_tick(change_doc, dict(base), 2000.0)
    outputs_mod._modbus_write_tick(change_doc, dict(base), 2001.0)
    check("change mode still writes once for an unchanged value",
          written == [5, 5, 9, 9, 5], written)
finally:
    outputs_mod._modbus_write_submit = real_submit

# -- the canvas ---------------------------------------------------------------------
js = read("frontend", "src", "workflow", "nodes.jsx")
check("an output with several rules wired in says so on its face",
      "fires on each of them" in js and "publishes each of them" in js)
page = read("frontend", "src", "pages", "Workflow.jsx")
check("the canvas refuses a second wire on one input only for a zone or an OID",
      page.count("already has a zone wired") == 1
      and "already knows who from another" in page)

print("\n%s" % ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
