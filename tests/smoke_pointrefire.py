# An ON/OFF point's branch fires on EVERY transition, not only the first.
#
# Tok Aba, 2026-10-09 (v1.0.33, box .49 asri_test_4): Class Filter OFF ->
# If/Else -> Trigger (light 96) -> MQTT Publish, and OFF -> If/Else ->
# Trigger (motor 0) -> Modbus Write. The first OFF sent both; after OFF ->
# ON -> OFF nothing went out. While the filter was ON the OFF branch sat out
# (run.py point_gates), and FunctionEngine.set_inactive brought it back the
# way it brings a rule back from AI off: the Trigger still raised from the
# previous OFF, so no new fire, no beat, nothing published or written.
#
# This drives the app's own reload steps (point_gates -> outputs.forget ->
# set_inactive -> set_states -> update -> publish) through OFF, ON, OFF, ON,
# OFF for a Class Filter and for the AI switch, and pins that each
# transition fires the matching side's Trigger and output exactly once and
# the other side nothing; and that a rule coming back from plain AI off
# (no point) still keeps its raised alarm.
#
#   py -3 tests/smoke_pointrefire.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app import functions as fm                                # noqa: E402
from app import outputs as om                                  # noqa: E402
from app import run as apprun                                  # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


# What went out, instead of a broker and a bus.
sent = []
om._mqtt = lambda doc: sent.append(("mqtt", doc.get("id"), doc.get("payload")))
om._modbus_write_submit = lambda o, job, repeat=False: sent.append(
    ("modbus", o.get("id"), job.get("value"))) or True
om._repeat_arm = lambda *a, **k: None


def point(fid, watch, want, flt=""):
    return {"id": fid, "name": fid, "type": "state_watch", "enabled": True,
            "params": {"watch": watch, "want": want,
                       **({"filter": flt} if flt else {})}}


def branch(fid, src, message, payload):
    """If/Else "input1 == 1" with a Trigger sending `payload`, as compiled
    on .49 (rate cooldown 1 s on the Class Filter's, plain on the AI's)."""
    trig = {"metric": "value", "op": ">", "value": 0.0, "severity": "info",
            "message": message, "cooldown_s": 0,
            "out": {"type": "number", "value": payload}}
    if "cf" in fid:
        trig.update(cooldown_s=1, rate="cooldown", rate_s=1.0)
    return {"id": fid, "name": fid, "type": "expr", "enabled": True,
            "params": {"expr": f"(fn_{src}_value == 1)"}, "trigger": trig}


def mqtt(oid, fid):
    return {"id": oid, "type": "mqtt_publish", "enabled": True,
            "topic": "t", "key": f"fn_{fid}_out", "format": "value",
            "mode": "change", "beat": fid, "trigger": "any"}


def modbus(oid, fid):
    return {"id": oid, "type": "modbus_write", "enabled": True, "fc": 6,
            "address": 0, "quantity": 1, "unit": 1, "mode": "change",
            "key": f"fn_{fid}_out", "beat": fid, "trigger": "any"}


FUNCS = [point("st_flt_on", "class_filter", "on", "flt"),
         point("st_flt_off", "class_filter", "off", "flt"),
         point("st_src_on", "ai", "on"), point("st_src_off", "ai", "off"),
         branch("cf_off_light", "st_flt_off", "light_stop2", "96"),
         branch("cf_off_motor", "st_flt_off", "motor_stop2", "0"),
         branch("cf_on_light", "st_flt_on", "light_go", "49"),
         branch("ai_off_light", "st_src_off", "light_stop", "96"),
         branch("ai_off_motor", "st_src_off", "motor_stop", "0"),
         branch("ai_on_motor", "st_src_on", "motor_go", "1000"),
         # A Detection-Stream-fed rule (no point): sits out with AI off and
         # must come back with its alarm kept, as before.
         {"id": "det", "name": "det", "type": "expr", "enabled": True,
          "params": {"expr": "1"},
          "trigger": {"metric": "value", "op": ">", "value": 0.0,
                      "severity": "info", "message": "det", "cooldown_s": 0}}]
OUTS = [mqtt("o_cf_off_light", "cf_off_light"),
        modbus("o_cf_off_motor", "cf_off_motor"),
        mqtt("o_cf_on_light", "cf_on_light"),
        mqtt("o_ai_off_light", "ai_off_light"),
        modbus("o_ai_off_motor", "ai_off_motor"),
        modbus("o_ai_on_motor", "ai_on_motor")]
LOGIC = [f["id"] for f in FUNCS if f["id"] != "det"]
CFG = {"functions": FUNCS, "outputs": OUTS,
       "logic": {"functions": LOGIC, "outputs": [o["id"] for o in OUTS]},
       "class_filters": {"paused": [], "nodes": [
           {"node": "flt", "name": "people", "functions": [], "outputs": []}]},
       "state_points": {"gates": [
           {"watch": "class_filter", "want": "off", "filter": "flt",
            "functions": ["cf_off_light", "cf_off_motor"],
            "outputs": ["o_cf_off_light", "o_cf_off_motor"]},
           {"watch": "class_filter", "want": "on", "filter": "flt",
            "functions": ["cf_on_light"], "outputs": ["o_cf_on_light"]},
           {"watch": "ai", "want": "off",
            "functions": ["ai_off_light", "ai_off_motor"],
            "outputs": ["o_ai_off_light", "o_ai_off_motor"]},
           {"watch": "ai", "want": "on", "functions": ["ai_on_motor"],
            "outputs": ["o_ai_on_motor"]}]}}

parsed = fm.parse_functions(FUNCS)
check("every function the test builds parses", len(parsed) == len(FUNCS),
      [f.id for f in parsed])
eng = fm.FunctionEngine(parsed)
clock = {"t": 1_000_000.0, "gated_outs": set(), "outs": []}


def apply(ai, paused):
    """One config poll in run.py, in its order."""
    c = dict(CFG, ai={"on": ai},
             class_filters=dict(CFG["class_filters"], paused=list(paused)))
    c = apprun.point_gates(apprun.class_pause(apprun.ai_config(c)))
    now_outs = set(c.get("_gated_outputs") or ())
    om.forget(clock["gated_outs"] - now_outs)
    clock["gated_outs"] = now_outs
    eng.set_inactive(apprun.inactive_ids(c), c.get("_gated") or ())
    fm.set_states(ai, apprun.paused_filters(c))
    clock["outs"] = c["outputs"]


def run(ticks=6):
    """A few frames, 0.5 s apart: what fired and what went out."""
    fired, n0 = [], len(sent)
    for _ in range(ticks):
        clock["t"] += 0.5
        fired += [e["message"] for e in eng.update(
            {}, [], {}, {}, 640, 480, zones=[], now=clock["t"])]
        om.publish(clock["outs"], eng.stats(), now=clock["t"])
    return sorted(fired), sorted((k, o) for k, o, _ in sent[n0:])


def expect(label, got, fires, outs):
    check(label, got == (sorted(fires), sorted(outs)), got)


# -- a Class Filter, AI on ---------------------------------------------------
OFF_CF = (["light_stop2", "motor_stop2"],
          [("mqtt", "o_cf_off_light"), ("modbus", "o_cf_off_motor")])
ON_CF = (["light_go"], [("mqtt", "o_cf_on_light")])
apply(True, [])
expect("start, AI on, filter on: the ON sides fire once",
       run(), ["light_go", "motor_go", "det"],
       [("mqtt", "o_cf_on_light"), ("modbus", "o_ai_on_motor")])
for i, (paused, want, name) in enumerate((
        (["flt"], OFF_CF, "OFF"), ([], ON_CF, "ON"), (["flt"], OFF_CF, "OFF"),
        ([], ON_CF, "ON"), (["flt"], OFF_CF, "OFF"))):
    apply(True, paused)
    expect(f"Class Filter transition {i + 1} -> {name}: its side fires and "
           f"sends exactly once, the other side nothing", run(), *want)
apply(True, ["flt"])
expect("a config poll with nothing changed fires nothing", run(), [], [])

# -- the AI switch (in place), filter left paused ----------------------------
OFF_AI = (["light_stop", "motor_stop"],
          [("mqtt", "o_ai_off_light"), ("modbus", "o_ai_off_motor")])
ON_AI = (["motor_go"], [("modbus", "o_ai_on_motor")])
for i, (ai, want, name) in enumerate((
        (False, OFF_AI, "OFF"), (True, ON_AI, "ON"), (False, OFF_AI, "OFF"),
        (True, ON_AI, "ON"), (False, OFF_AI, "OFF"))):
    apply(ai, ["flt"])
    got = run()
    if ai:
        # The detection-fed rule is back from AI off: still raised, silent.
        check(f"AI transition {i + 1} -> ON: a detection rule back from AI "
              f"off keeps its alarm (no re-fire)", "det" not in got[0], got)
    expect(f"AI transition {i + 1} -> {name}: its side fires and sends "
           f"exactly once, the other side nothing", got, *want)

# -- the engine API on its own -----------------------------------------------
e2 = fm.FunctionEngine(fm.parse_functions([FUNCS[-1]]))
e2.update({}, [], {}, {}, 640, 480, zones=[], now=1.0)
e2.set_inactive({"det"})
e2.set_inactive(set())
check("set_inactive without `gated` keeps a raised Trigger raised",
      not e2.update({}, [], {}, {}, 640, 480, zones=[], now=2.0))
e2.set_inactive({"det"}, gated={"det"})
e2.set_inactive(set())
check("...and with it the rule comes back fresh and fires",
      [e["message"] for e in e2.update({}, [], {}, {}, 640, 480, zones=[],
                                       now=3.0)] == ["det"])

print("\nOK" if not bad else f"\n{bad} FAILED")
sys.exit(1 if bad else 0)
