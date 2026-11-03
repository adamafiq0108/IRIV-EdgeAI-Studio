# MQTT in: compiles like an API value, and the engine holds the last message
# without ever blocking the detection loop.
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402
from core.services import functions as fx                     # noqa: E402

# --- compile ---------------------------------------------------------------
g = wf.clean_graph({"nodes": [
    {"id": "m", "type": "variable.mqtt", "position": {"x": 0, "y": 0},
     "data": {"fnId": "parts", "name": "Parts made", "host": "10.0.0.5",
              "port": 1883, "topic": "plc/line1/parts", "json_path": "data.count",
              "fallback": 0, "username": "iriv", "password": "s3cret"}},
], "edges": []})
c = wf.compile_graph(g, [])
assert not c["errors"], c["errors"]
fn = c["functions"][0]
print("compiled:", fn["type"], fn["params"])
assert fn["type"] == "mqtt_sub"
assert fn["params"]["topic"] == "plc/line1/parts"
assert fn["params"]["json_path"] == "data.count"
print("== MQTT node compiles to an mqtt_sub rule")

# it runs with no Detection Stream, like any other standalone value
assert not c["errors"] and not c["warnings"], (c["errors"], c["warnings"])
print("== it needs no Detection Stream")

# --- validation ------------------------------------------------------------
for bad, why in [
    ({"host": "", "topic": "a"}, "no broker"),
    ({"host": "h", "topic": ""}, "no topic"),
    ({"host": "h", "topic": "a/#/b"}, "'#' not last"),
    ({"host": "h", "topic": "a", "port": 0}, "bad port"),
]:
    try:
        fx.validate_function({"id": "x", "name": "x", "type": "mqtt_sub",
                              "params": bad})
        print("!! accepted:", why)
        raise SystemExit(1)
    except ValueError as exc:
        print(f"   refused ({why}): {exc}")
print("== bad settings are refused with a readable reason")

# --- engine ----------------------------------------------------------------
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
from app import functions as appfx                            # noqa: E402

spec = {"id": "parts", "name": "Parts", "type": "mqtt_sub", "enabled": True,
        "params": {"host": "127.0.0.1", "port": 1883,
                   "topic": "plc/line1/parts", "json_path": "data.count",
                   "fallback": 7}}
f = appfx.Function(spec)
engine = appfx.FunctionEngine([f])

started = time.time()
engine.update({"crossing_events": []}, [], {}, {}, 640, 480, zones=[])
elapsed = time.time() - started
stats = engine._stats
print(f"first tick took {elapsed*1000:.0f}ms ->",
      {k: v for k, v in stats.items() if k.startswith("fn_parts")})
assert elapsed < 0.2, "the loop blocked on a broker that is not there"
assert stats["fn_parts_value"] == 7, "the fallback was not used before any message"
assert stats["fn_parts_ok"] == 0, "it claimed a live reading with no broker"
print("== a missing broker costs the loop nothing and reads as not-ok")

# a message arriving updates the held value -- simulated by driving the state
# the subscriber thread owns, which is what on_message does.
with appfx._mqtt_lock:
    st = appfx._mqtt_state["parts"]
    st.update(value=42.0, ok=True, msgs=3)
engine.update({"crossing_events": []}, [], {}, {}, 640, 480, zones=[])
stats = engine._stats
print("after a message ->", {k: v for k, v in stats.items() if k.startswith("fn_parts")})
assert stats["fn_parts_value"] == 42.0 and stats["fn_parts_ok"] == 1
assert stats["fn_parts_messages"] == 3
print("== the held reading reaches the stats")

# the subscriber is reaped once nothing claims it
appfx._reap_subscribers(time.time() + appfx.POLLER_TTL_S + 1)
assert "parts" not in appfx._mqtt_state, "a deleted rule left its thread running"
print("== deleting the rule stops its subscriber")
print("ALL OK")
