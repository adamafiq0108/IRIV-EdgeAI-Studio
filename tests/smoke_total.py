# Running Total: the accumulator the "total = total + new" graphs were
# reaching for. Compile + engine behaviour, including the user's exact case.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_violation_1", "name": "Keepout", "kind": "area",
          "enabled": True}]

# The user's intent, drawn the supported way: zone violations-now -> Total.
g = wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_violation_1"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "z_zone_violation_1_zoneviol", "name": "Violations now"}},
    {"id": "tot", "type": "analytics.total", "position": {"x": 3, "y": 0},
     "data": {"fnId": "total_violation", "name": "Total violations",
              "mode": "count", "reset": "daily"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e3", "source": "occ", "target": "tot"},
]})
c = wf.compile_graph(g, ZONES)
assert not c["errors"], c["errors"]
fn = next(f for f in c["functions"] if f["id"] == "total_violation")
print("compiled:", fn["type"], fn["params"])
assert fn["type"] == "total"
assert fn["params"]["key"] == "fn_z_zone_violation_1_zoneviol_count"
assert fn["params"]["mode"] == "count" and fn["params"]["reset"] == "daily"
print("== the wire IS the source: no key typing, no self-reference")

# unwired -> a readable error
lone = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "tot", "type": "analytics.total", "position": {"x": 0, "y": 0},
     "data": {"fnId": "t", "name": "T", "mode": "count"}}], "edges": []}), ZONES)
assert lone["errors"] and "one value wired in" in lone["errors"][0]["message"]
print("== unwired Total says so:", lone["errors"][0]["message"])

# --- engine ------------------------------------------------------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import functions as appfx                            # noqa: E402

f = appfx.Function({"id": "total_violation", "name": "Total", "type": "total",
                    "enabled": True,
                    "params": {"key": "viol_now", "mode": "count"}})
eng = appfx.FunctionEngine([f])


def tick(value, now):
    eng.update({"crossing_events": []}, [], {}, {"viol_now": value}, 640, 480,
               zones=[], now=now)
    return eng._stats["fn_total_violation_total"]


# count mode: an episode counts ONCE however many frames it lasts
t = 1_000_000.0
seq = [0, 0, 2, 3, 3, 1, 0, 0, 1, 1, 0]      # two violation episodes
totals = [tick(v, t + i) for i, v in enumerate(seq)]
print("count mode:", list(zip(seq, totals)))
assert totals[-1] == 2, totals
print("== 30 fps of the same violation is ONE, not thirty")

# sum mode: accumulates growth, ignores resets
f2 = appfx.Function({"id": "grew", "name": "Grew", "type": "total",
                     "enabled": True,
                     "params": {"key": "counter", "mode": "sum"}})
eng2 = appfx.FunctionEngine([f2])


def tick2(value, now):
    eng2.update({"crossing_events": []}, [], {}, {"counter": value}, 640, 480,
                zones=[], now=now)
    return eng2._stats["fn_grew_total"]


seq2 = [(0, 0), (3, 3), (5, 5), (5, 5), (0, 5), (2, 7)]   # source resets at 0
for v, want in seq2:
    got = tick2(v, t)
    t += 1
    assert got == want, (v, got, want)
print("== sum mode adds growth and survives the source resetting")

# daily reset: a new day starts from zero
f3 = appfx.Function({"id": "day", "name": "Day", "type": "total",
                     "enabled": True,
                     "params": {"key": "viol_now", "mode": "count",
                                "reset": "daily"}})
eng3 = appfx.FunctionEngine([f3])
import time as _time                                          # noqa: E402
day1 = _time.mktime((2026, 8, 5, 10, 0, 0, 0, 0, -1))
day2 = _time.mktime((2026, 8, 6, 10, 0, 0, 0, 0, -1))
eng3.update({"crossing_events": []}, [], {}, {"viol_now": 0}, 640, 480, zones=[], now=day1)
eng3.update({"crossing_events": []}, [], {}, {"viol_now": 1}, 640, 480, zones=[], now=day1 + 1)
assert eng3._stats["fn_day_total"] == 1
eng3.update({"crossing_events": []}, [], {}, {"viol_now": 0}, 640, 480, zones=[], now=day2)
assert eng3._stats["fn_day_total"] == 0, eng3._stats
print("== daily reset starts the new day at zero")

# a formula the same frame can read the total (totals run before exprs)
f4 = appfx.Function({"id": "pct", "name": "Pct", "type": "expr", "enabled": True,
                     "params": {"expr": "fn_total_violation_total * 10"}})
eng4 = appfx.FunctionEngine([f, f4])
eng4.update({"crossing_events": []}, [], {}, {"viol_now": 1}, 640, 480,
            zones=[], now=t + 100)
eng4.update({"crossing_events": []}, [], {}, {"viol_now": 1}, 640, 480,
            zones=[], now=t + 101)
assert eng4._stats["fn_pct_value"] == eng4._stats["fn_total_violation_total"] * 10
print("== a formula reads the total the same frame")

# --- the Reset wire and "continue after a restart" ---------------------------
g5 = wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_violation_1"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "z_zone_violation_1_zoneviol", "name": "Violations now"}},
    {"id": "btn", "type": "analytics.occupancy", "position": {"x": 2, "y": 1},
     "data": {"fnId": "z_zone_violation_1_rst", "name": "Reset signal"}},
    {"id": "tot", "type": "analytics.total", "position": {"x": 3, "y": 0},
     "data": {"fnId": "total_violation", "name": "Total violations",
              "mode": "count", "resume": "last"}},
], "edges": [
    {"id": "e1", "source": "s", "target": "z"},
    {"id": "e2", "source": "z", "target": "occ"},
    {"id": "e2b", "source": "z", "target": "btn"},
    {"id": "e3", "source": "occ", "target": "tot"},
    {"id": "e4", "source": "btn", "target": "tot", "targetHandle": "reset"},
]})
c5 = wf.compile_graph(g5, ZONES)
assert not c5["errors"], c5["errors"]
fn5 = next(f_ for f_ in c5["functions"] if f_["id"] == "total_violation")
assert fn5["params"]["key"] == "fn_z_zone_violation_1_zoneviol_count", fn5["params"]
assert fn5["params"]["reset_key"] == "fn_z_zone_violation_1_rst_count", fn5["params"]
assert fn5["params"]["resume"] is True
print("== the Reset wire and the resume choice compile to params")

fr = appfx.Function({"id": "rt", "name": "RT", "type": "total", "enabled": True,
                     "params": {"key": "viol_now", "mode": "count",
                                "reset_key": "rst"}})
engr = appfx.FunctionEngine([fr])


def tickr(v, r, now):
    engr.update({"crossing_events": []}, [], {}, {"viol_now": v, "rst": r},
                640, 480, zones=[], now=now)
    return engr._stats["fn_rt_total"]


rseq = [(0, 0), (2, 0), (0, 0), (3, 0), (0, 0), (0, 1), (2, 1), (0, 1), (0, 0), (4, 0)]
rtot = [tickr(v, r, t + 200 + i) for i, (v, r) in enumerate(rseq)]
print("reset:", list(zip(rseq, rtot)))
assert rtot[4] == 2, rtot             # two episodes before the reset
assert rtot[5] == 0, rtot             # 0 -> 1 on the Reset wire starts over
assert rtot[6] == 1 and rtot[7] == 1, rtot   # held high: no second reset
assert rtot[-1] == 2, rtot            # counting carries on afterwards
print("== a 0 -> 1 on Reset starts the total again, once")

state = tempfile.mkdtemp()
fk = appfx.Function({"id": "keep", "name": "Keep", "type": "total", "enabled": True,
                     "params": {"key": "viol_now", "mode": "count", "resume": True}})
e1 = appfx.FunctionEngine([fk], state_dir=state)
for i, v in enumerate([0, 2, 0, 2, 0]):
    e1.update({"crossing_events": []}, [], {}, {"viol_now": v}, 640, 480,
              zones=[], now=t + 300 + i)
assert e1._stats["fn_keep_total"] == 2
e1.flush_daily(force=True)
e2_ = appfx.FunctionEngine([fk], state_dir=state)
e2_.update({"crossing_events": []}, [], {}, {"viol_now": 0}, 640, 480,
           zones=[], now=t + 400)
assert e2_._stats["fn_keep_total"] == 2, e2_._stats
fz = appfx.Function({"id": "keep", "name": "Keep", "type": "total", "enabled": True,
                     "params": {"key": "viol_now", "mode": "count"}})
e3_ = appfx.FunctionEngine([fz], state_dir=state)
e3_.update({"crossing_events": []}, [], {}, {"viol_now": 0}, 640, 480,
           zones=[], now=t + 500)
assert e3_._stats["fn_keep_total"] == 0
print("== a restart continues from the last saved total only when asked to")
print("ALL OK")
