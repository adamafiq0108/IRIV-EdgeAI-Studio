# "In the zone more than 20 s": a Cycle Time, an If / Else if on its live_s, a Trigger.
#
# live_s is the LONGEST stay running, so an If / Else if over a limit on it is
# true for everybody in the zone the moment one object passes the limit -- a
# newcomer at one second included. The compiler now writes the clock and the
# limit on the rule the If / Else if becomes (timer_fn, timer_s); the engine
# names only the objects whose own stay is past the limit, and a Snapshot taken
# on that rule reports the object's time against the limit.
#
#   1. the compile: the limit and the clock ride on the rule, and survive validation
#   2. two objects, one past 20 s and one just arrived: only the first is named
#   3. presence() reports the object's own time against the limit
#
#     py -3 tests/smoke_dwelltimer.py
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from app.functions import Function, FunctionEngine               # noqa: E402
from app.zones import ZoneEngine, parse_zones                    # noqa: E402
from core.services import functions as fsvc                      # noqa: E402
from core.services import workflow as wf                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, **kw):
    return {"id": f"e_{src}_{tgt}_{kw.get('targetHandle', '')}",
            "source": src, "target": tgt, **kw}


# -- 1. the compile -------------------------------------------------------------------
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
          "points": [[0.0, 0.0], [0.5, 0.0], [0.5, 1.0], [0.0, 1.0]]}]


def compiled(limit, op=">"):
    g = wf.clean_graph({
        "nodes": [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
                  node("cy", "analytics.dwell", fnId="cyc", name="Cycle time",
                       mode="present", OID=True, delivery="once"),
                  node("br", "branch.cases", fnId="brx", rules=[
                      {"op": op, "value": limit, "input": "in1"}], otherwise=True)],
        "edges": [wire("s", "cy", targetHandle="in"), wire("z", "cy", targetHandle="zone"),
                  wire("cy", "br", targetHandle="in1")]})
    return wf.compile_graph(g, ZONES)


out = compiled(20)
fns = {f["id"]: f for f in out["functions"]}
first = next((f for f in out["functions"] if f["id"].endswith("_b0")), {})
check("the first test of the If / Else if names the clock and the limit",
      first.get("params", {}).get("timer_fn") == "cyc"
      and first["params"].get("timer_s") == 20.0, first)
other = next((f for f in out["functions"] if f["id"].endswith("_b1")), {})
check("the otherwise branch is not a timer",
      "timer_fn" not in (other.get("params") or {}), other)
check("a limit of 7.5 s and >= are read the same way",
      next(f for f in compiled(7.5, ">=")["functions"]
           if f["id"].endswith("_b0"))["params"].get("timer_s") == 7.5)
kept = fsvc.validate_function(first)
check("the clock and the limit survive validation",
      kept["params"].get("timer_fn") == "cyc" and kept["params"].get("timer_s") == 20.0, kept)
check("...and a limit that is nonsense is dropped",
      "timer_fn" not in fsvc.validate_function(
          {"id": "x", "type": "expr", "params": {"expr": "1", "timer_fn": "cyc",
                                                 "timer_s": -4}})["params"])

# -- 2. two objects, one past the limit ------------------------------------------------
zeng = ZoneEngine(parse_zones(ZONES))
dw = Function({"id": "dw", "type": "zone_dwell", "name": "Time",
               "params": {"zone": "bay", "stamp_events": True}})
over = Function({"id": "over", "type": "expr", "name": "Over",
                 "params": {"expr": "(fn_dw_live_s > 20)", "timer_fn": "dw", "timer_s": 20.0},
                 "trigger": {"metric": "value", "op": ">", "value": 0, "severity": "warn",
                             "message": "Over Limit", "cooldown_s": 0,
                             "oid_source": "dw", "rate": "once_per_oid"}})
eng = FunctionEngine([dw, over], state_dir=None)


def box(tid):
    return {"label": "person", "box": [40, 40, 120, 400], "track_id": tid, "score": 0.9}


named = []
at_25 = None
for sec in range(0, 61):
    here = []
    if sec < 50:
        here.append(box(1))                 # in from 0 s
    if 24 <= sec < 50:
        here.append(box(2))                 # arrives at 24 s
    res = zeng.update(here, 640, 480)
    out = eng.update(res, here, {d["track_id"]: object() for d in here},
                     zeng.stats(res), 640, 480, zones=zeng.zones, now=float(sec))
    for e in out:
        if e.get("id") == "over" and e.get("type") == "alert":
            named.append((sec, e.get("source_oid")))
    if sec == 30:
        at_25 = eng.presence("over", 1, now=30.0), eng.presence("over", 2, now=30.0)
check("object 1, past 20 s, is named once", [o for _s, o in named if o == 1] == [1], named)
check("...and object 2, who has just arrived, is not named at that moment",
      all(not (o == 2 and s < 24 + 20) for s, o in named), named)
check("...but is named in its own time, 20 s after it arrived",
      any(o == 2 and 44 <= s <= 46 for s, o in named), named)

# -- 3. what a photo reports --------------------------------------------------------------
first_pres, second_pres = at_25
check("presence: object 1's own stay against the limit, on the clock's id",
      first_pres == {"fn": "dw", "threshold_s": 20.0, "elapsed_s": 30.0}, first_pres)
check("...object 2's own, much shorter, stay",
      second_pres and second_pres["elapsed_s"] == 6.0, second_pres)
check("...and nothing for an object that is not there",
      eng.presence("over", 99, now=30.0) is None)

# -- the Snapshot's HOLD: only behind a timer that can say when it is over ---------------
def held(once=True, hold=True, via_cycle=True):
    cy = node("cy", "analytics.dwell", fnId="cyc", name="Cycle time", mode="present",
              OID=True, delivery="once" if once else "live")
    nodes = [node("s", "source.detection"), node("z", "zone.ref", zone="bay"), cy,
             node("br", "branch.cases", fnId="brx",
                  rules=[{"op": ">", "value": 20, "input": "in1"}], otherwise=True),
             node("tg", "output.trigger", name="Over", wait_end=hold),
             node("sn", "output.snapshot")]
    edges = [wire("s", "cy", targetHandle="in"), wire("z", "cy", targetHandle="zone"),
             wire("cy", "br", targetHandle="in1"),
             wire("br", "tg", sourceHandle="out0", targetHandle="in"),
             wire("tg", "sn", targetHandle="in")]
    res = wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), ZONES)
    snap = next((o for o in res["outputs"] if o.get("type") == "snapshot"), {})
    return snap, res


snap, res = held()
check("a Trigger behind a Cycle Time that records each stay may hold its Snapshot",
      snap.get("hold") is True, (snap, res["errors"], res["warnings"]))
snap, res = held(once=False)
check("...but not when the Cycle Time does not record the stay's end",
      "hold" not in snap and any("record each stay" in w["message"] for w in res["warnings"]),
      (snap, res["warnings"]))
snap, res = held(hold=False)
check("...and it is off unless asked for", "hold" not in snap, snap)
g = wf.clean_graph({"nodes": [node("s", "source.detection"),
                              node("f", "filter.class", classes=["person"]),
                              node("o", "analytics.object", fnId="ob", name="Watch"),
                              node("br", "branch.cases", fnId="brx",
                                   rules=[{"op": ">", "value": 0, "input": "in1"}],
                                   otherwise=True),
                              node("tg", "output.trigger", name="Over", wait_end=True),
                              node("sn", "output.snapshot")],
                    "edges": [wire("s", "f", targetHandle="in"), wire("f", "o", targetHandle="in"),
                              wire("o", "br", targetHandle="in1"),
                              wire("br", "tg", sourceHandle="out0", targetHandle="in"),
                              wire("tg", "sn", targetHandle="in")]})
res2 = wf.compile_graph(g, ZONES)
snap2 = next((o for o in res2["outputs"] if o.get("type") == "snapshot"), None)
check("a Trigger with no timer anywhere behind it can never wait",
      snap2 is not None and "hold" not in snap2
      and any("needs a timer" in w["message"] for w in res2["warnings"]),
      (snap2, res2["errors"], res2["warnings"]))

# -- tolerance: past limit + tolerance is the violation ------------------------------------
def with_tolerance(tol):
    g = wf.clean_graph({
        "nodes": [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
                  node("cy", "analytics.dwell", fnId="cyc", name="Cycle time", mode="present",
                       OID=True, delivery="once", tolerance_s=tol),
                  node("br", "branch.cases", fnId="brx",
                       rules=[{"op": ">", "value": 20, "input": "in1"}], otherwise=True)],
        "edges": [wire("s", "cy", targetHandle="in"), wire("z", "cy", targetHandle="zone"),
                  wire("cy", "br", targetHandle="in1")]})
    return {f["id"]: f for f in wf.compile_graph(g, ZONES)["functions"]}


tf = with_tolerance(3)
b0 = next(f for i, f in tf.items() if i.endswith("_b0"))
b1 = next(f for i, f in tf.items() if i.endswith("_b1"))
check("a 3 s tolerance moves the line the rule tests to 23 s",
      b0["params"]["expr"] == "(fn_cyc_live_s > 23)", b0["params"])
check("...the otherwise branch says the same thing",
      b1["params"]["expr"] == "not (fn_cyc_live_s > 23)", b1["params"])
check("...while the limit the photo reports stays 20 s, with the tolerance beside it",
      b0["params"]["timer_s"] == 20.0 and b0["params"]["timer_tol_s"] == 3.0, b0["params"])
kept = fsvc.validate_function(b0)
check("...and it all survives validation", kept["params"]["timer_tol_s"] == 3.0
      and kept["params"]["expr"] == "(fn_cyc_live_s > 23)", kept)
nt = with_tolerance(0)
check("no tolerance changes nothing",
      next(f for i, f in nt.items() if i.endswith("_b0"))["params"]["expr"] == "(fn_cyc_live_s > 20)"
      and "timer_tol_s" not in next(f for i, f in nt.items() if i.endswith("_b0"))["params"])

zeng3 = ZoneEngine(parse_zones(ZONES))
dw3 = Function({"id": "dw", "type": "zone_dwell", "name": "Time",
                "params": {"zone": "bay", "stamp_events": True, "tolerance_s": 3.0}})
over3 = Function({"id": "over3", "type": "expr", "name": "Over",
                  "params": {"expr": "(fn_dw_live_s > 23)", "timer_fn": "dw", "timer_s": 20.0,
                             "timer_tol_s": 3.0},
                  "trigger": {"metric": "value", "op": ">", "value": 0, "severity": "warn",
                              "message": "Over Limit", "cooldown_s": 0,
                              "oid_source": "dw", "rate": "once_per_oid"}})
eng3 = FunctionEngine([dw3, over3], state_dir=None)
fired3, pres3 = [], None
for sec in range(0, 40):
    here3 = [box(1)] if sec < 30 else []
    res3 = zeng3.update(here3, 640, 480)
    out3 = eng3.update(res3, here3, {d["track_id"]: object() for d in here3},
                       zeng3.stats(res3), 640, 480, zones=zeng3.zones, now=float(sec))
    fired3 += [sec for e in out3 if e.get("id") == "over3" and e.get("type") == "alert"]
    if sec == 25:
        pres3 = eng3.presence("over3", 1, now=25.0)
check("the violation is raised past limit + tolerance (23 s), not at 20 s",
      fired3 and 23 <= fired3[0] <= 25, fired3)
check("presence reports the limit, the tolerance and the object's own time",
      pres3 == {"fn": "dw", "threshold_s": 20.0, "elapsed_s": 25.0, "tolerance_s": 3.0}, pres3)
import app.snapmeta as snapmeta                                  # noqa: E402
tm = snapmeta.timer_from_event({"detail": {"function": "over", "track": 1}}, pres3)
check("the photo's timer keeps the tolerance", tm and tm["tolerance_s"] == 3.0
      and tm["threshold_s"] == 20.0, tm)
tm["ended"], tm["total_s"] = True, 30.0
vt = snapmeta.view_timer(tm)
check("...the exceed is still measured from the limit (30 s total = +10 s)",
      vt["exceeded_s"] == 10.0 and vt["tolerance_s"] == 3.0, vt)

# -- a Data Table row that waits records the FINISHED time -------------------------------
dwr = Function({"id": "dwr", "type": "zone_dwell", "name": "T", "params": {"zone": "bay"}})
fml = Function({"id": "fml", "type": "expr", "name": "Over by", "params": {"expr": "fn_dwr_live_s - 20"}})
fml2 = Function({"id": "fml2", "type": "expr", "name": "Twice", "params": {"expr": "fn_fml_value * 2"}})
engr = FunctionEngine([dwr, fml, fml2], state_dir=None)
alert_stats = {"fn_dwr_live_s": 22.1, "fn_dwr_max_s": 22.1, "fn_fml_value": 2.1,
               "fn_fml2_value": 4.2, "objects_in_frame": 2}
done = engr.restate(alert_stats, "dwr", 31.7)
check("the clock a row waited for reads the finished time (22.1 s -> 31.7 s)",
      done["fn_dwr_live_s"] == 31.7 and done["fn_dwr_max_s"] == 31.7, done)
check("...and a Formula on it is worked out again (time minus 20 = 11.7)",
      done["fn_fml_value"] == 11.7, done)
check("...down a chain of Formulas too", done["fn_fml2_value"] == 23.4, done)
check("...leaving every other number as it was", done["objects_in_frame"] == 2
      and alert_stats["fn_dwr_live_s"] == 22.1)
check("...and nonsense changes nothing", engr.restate(alert_stats, "dwr", "x") == alert_stats)

print("ALL OK" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
