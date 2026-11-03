# Delay node: outputs after it fire late; delays in a row add up.
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

ZONES = [{"id": "zone_a", "name": "Bay", "kind": "area", "enabled": True}]

BASE = [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "z", "type": "zone.ref", "position": {"x": 1, "y": 0},
     "data": {"zone": "zone_a"}},
    {"id": "occ", "type": "analytics.occupancy", "position": {"x": 2, "y": 0},
     "data": {"fnId": "bay", "name": "Bay"}},
    {"id": "when", "type": "logic.when", "position": {"x": 3, "y": 0},
     "data": {"metric": "count", "mode": "above", "value": 2}},
]
BASE_E = [{"id": "e1", "source": "s", "target": "z"},
          {"id": "e2", "source": "z", "target": "occ"},
          {"id": "e3", "source": "occ", "target": "when"}]


def compile_with(nodes, edges):
    g = wf.clean_graph({"nodes": BASE + nodes, "edges": BASE_E + edges})
    return wf.compile_graph(g, ZONES)


# 1. no delay -> no delay_s on the output
c = compile_with(
    [{"id": "snap", "type": "output.snapshot", "position": {"x": 5, "y": 0}, "data": {}}],
    [{"id": "e4", "source": "when", "target": "snap"}])
assert not c["errors"], c["errors"]
out = c["outputs"][0]
print("no delay      ->", {k: out[k] for k in ("type", "trigger") if k in out},
      "delay_s" in out)
assert "delay_s" not in out

# 2. one delay
c = compile_with([
    {"id": "dly", "type": "logic.delay", "position": {"x": 4, "y": 0},
     "data": {"delay_s": 5}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 5, "y": 0}, "data": {}},
], [
    {"id": "e4", "source": "when", "target": "dly"},
    {"id": "e5", "source": "dly", "target": "snap"},
])
assert not c["errors"], c["errors"]
out = c["outputs"][0]
print("one delay     -> delay_s =", out.get("delay_s"))
assert out.get("delay_s") == 5, out
assert out.get("function") == "bay", "the output lost the rule it fires on"

# 3. two delays in a row add up
c = compile_with([
    {"id": "d1", "type": "logic.delay", "position": {"x": 4, "y": 0},
     "data": {"delay_s": 5}},
    {"id": "d2", "type": "logic.delay", "position": {"x": 4, "y": 1},
     "data": {"delay_s": 10}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 5, "y": 0}, "data": {}},
], [
    {"id": "e4", "source": "when", "target": "d1"},
    {"id": "e5", "source": "d1", "target": "d2"},
    {"id": "e6", "source": "d2", "target": "snap"},
])
assert not c["errors"], c["errors"]
print("chained       -> delay_s =", c["outputs"][0].get("delay_s"))
assert c["outputs"][0].get("delay_s") == 15, c["outputs"][0]

# 4. two outputs after the same rule may wait different amounts
c = compile_with([
    {"id": "dly", "type": "logic.delay", "position": {"x": 4, "y": 0},
     "data": {"delay_s": 7}},
    {"id": "snap", "type": "output.snapshot", "position": {"x": 5, "y": 0}, "data": {}},
    {"id": "rec", "type": "output.record", "position": {"x": 5, "y": 1}, "data": {}},
], [
    {"id": "e4", "source": "when", "target": "dly"},
    {"id": "e5", "source": "dly", "target": "snap"},
    {"id": "e6", "source": "when", "target": "rec"},
])
assert not c["errors"], c["errors"]
delays = {o["type"]: o.get("delay_s") for o in c["outputs"]}
print("split         ->", delays)
assert delays["snapshot"] == 7 and delays.get("record") is None, delays

# 5. the engine honours it without sleeping the detection loop
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import outputs as app_outputs                        # noqa: E402
import time                                                   # noqa: E402

fired = []
o = {"id": "t1", "type": "snapshot", "trigger": "alert", "enabled": True,
     "delay_s": 0.3}
started = time.time()
app_outputs.fire([o], {"type": "alert", "detail": {}},
                 # (output, event) -- the event carries which object caused
                 # this firing, for a capture that asked to stamp it.
                 hooks={"snapshot": lambda o, ev=None: fired.append(time.time())})
elapsed_call = time.time() - started
# "Did not block" is measured against the DELAY, not a fixed 0.1s: a fire()
# that slept the loop would take the whole 0.3s, while a loaded machine can
# take longer than 0.1s just to start a timer thread.
assert elapsed_call < o["delay_s"], f"fire() blocked the loop for {elapsed_call:.2f}s"
assert not fired, "it fired immediately instead of waiting"
deadline = time.time() + 5.0            # waits for the send, not a fixed sleep
while not fired and time.time() < deadline:
    time.sleep(0.02)
assert fired, "the delayed send never happened"
assert fired[0] - started >= o["delay_s"] - 0.01,     f"it fired after {fired[0] - started:.2f}s, before its {o['delay_s']}s delay"
print(f"engine        -> fire() returned in {elapsed_call*1000:.0f}ms, "
      f"sent {fired[0]-started:.2f}s later")
print("ALL OK")
