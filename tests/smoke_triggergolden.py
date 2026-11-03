# The Trigger inspector redesign (Tok Aba, 2026-10-14) is a CANVAS change:
# the firing choice, the payload duration and the three opt-in rows write the
# same stored fields as before (d.alert, d.repeat, d.cooldown_s, d.rate,
# d.rate_s, d.out_hold, d.out_hold_s, d.payload_type, d.idle_type, d.draw,
# d.box_color, d.box_in_zone, d.zone_color, d.dup_guard, d.dup_threshold).
#
# GOLDEN: representative saved Triggers - a timer cooldown alarm, a watchdog
# "only for a new object", a rate "at most once every N s" with payloads, a
# "once per object" with an interval hold, the paint combinations, the
# duplicate guard wired - compile byte-for-byte as they did before the
# redesign. The golden file was captured BEFORE any inspector code changed
# (tests/golden_trigger_inspector.json).
#
#   py -3 tests/smoke_triggergolden.py
#   py -3 tests/smoke_triggergolden.py --write-golden   (only before a change)
import io
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                          # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden_trigger_inspector.json")
ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True,
          "camera": "cam1",
          "points": [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]}]
TABLES = {"visits": {"key": "visits", "name": "Visits", "columns": [
    {"name": "oid", "type": "text"}, {"name": "weight", "type": "number"}]}}

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label
          + ("" if ok else f"\n      {ascii(detail)[:1500]}"))
    if not ok:
        bad += 1


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, handle=None, target_handle=None):
    return {"id": f"e_{src}_{tgt}_{handle or ''}_{target_handle or ''}",
            "source": src, "target": tgt,
            "sourceHandle": handle, "targetHandle": target_handle}


def graph(trigger, extra_wires=(), oid_wire=False, zone_wire=False):
    nodes = [node("src", "source.detection", camera="cam1"),
             node("z", "zone.ref", zone="bay"),
             node("cnt", "analytics.violations", fnId="cnt", name="People",
                  zone="bay", mode="realtime", OID=True),
             node("cmp", "branch.cases", fnId="busy",
                  rules=[{"op": ">", "value": 1, "input": "in1"}], otherwise=True),
             node("trg", "output.trigger", fnId="trg", **trigger)]
    edges = [wire("src", "cnt"), wire("z", "cnt", None, "zone"),
             wire("cnt", "cmp"), wire("cmp", "trg", "out0")]
    if extra_wires:
        # The duplicate guard reads a Data Table the Trigger writes.
        nodes.append(node("ds", "output.dataset", dataset="visits", name="Visits",
                          mode="alert", sources={}))
        edges += [wire("trg", "ds"), wire("cnt", "ds", "OID", "c_oid"),
                  wire("cnt", "ds", None, "c_weight")]
    if oid_wire:
        edges.append(wire("cnt", "trg", "OID", "oid"))
    if zone_wire:
        edges.append(wire("z", "trg", None, "zone"))
    edges += list(extra_wires)
    return {"nodes": nodes, "edges": edges}


GUARD = [wire("ds", "trg", "o_oid", "cur_oid"), wire("ds", "trg", "o_weight", "cur_value")]


def fixtures():
    """name -> graph: every one a Trigger shape saved on a box today."""
    return {
        "plain_event": graph({"name": "Seen"}),
        "timer_cooldown_alert": graph({"name": "Overcrowded", "alert": True,
                                       "severity": "crit", "message": "Too many",
                                       "repeat": "cooldown", "cooldown_s": 45}),
        "timer_default_cooldown": graph({"name": "Busy", "alert": True}),
        "watchdog_new_object": graph({"name": "Intruder", "alert": True,
                                      "severity": "warn", "repeat": "new_object"},
                                     oid_wire=True),
        "rate_cooldown_payloads": graph({"name": "Pulse", "rate": "cooldown",
                                         "rate_s": 20, "payload_type": "number",
                                         "payload": "5", "idle_type": "string",
                                         "idle_payload": "idle"}),
        "rate_cooldown_alert_hold": graph({"name": "Held", "alert": True,
                                           "rate": "cooldown", "rate_s": 7,
                                           "out_hold": "hold", "out_hold_s": 12}),
        "once_per_oid_interval": graph({"name": "Each one", "rate": "once_per_oid",
                                        "out_hold": "interval", "out_hold_s": 30,
                                        "alert": True, "repeat": "new_object"},
                                       oid_wire=True),
        "legacy_hold_seconds": graph({"name": "Old hold", "out_hold_s": 15}),
        "paint_boxes_in_zone_and_zone": graph({"name": "Paint", "draw": True,
                                               "box_color": "#FF0000",
                                               "box_in_zone": True,
                                               "zone_color": "#00FF00"},
                                              zone_wire=True),
        "paint_boxes_everywhere": graph({"name": "Boxes", "draw": True,
                                         "box_color": "#FF8800"}),
        "dup_guard_timer": graph({"name": "Seen", "alert": True, "dup_guard": True,
                                  "dup_threshold": 4}, GUARD, oid_wire=True),
        "dup_guard_watchdog": graph({"name": "Seen", "alert": True,
                                     "repeat": "new_object", "dup_guard": True},
                                    GUARD[:1], oid_wire=True),
    }


def compile_all():
    got = {}
    for name, g in fixtures().items():
        res = wf.compile_graph(wf.clean_graph(json.loads(json.dumps(g))),
                               ZONES, tables=TABLES)
        got[name] = json.loads(json.dumps(res, sort_keys=True, default=str))
    return got


if "--write-golden" in sys.argv:
    with io.open(GOLDEN, "w", encoding="utf-8", newline="\n") as f:
        json.dump(compile_all(), f, indent=1, sort_keys=True)
        f.write("\n")
    print("golden written:", GOLDEN)
    sys.exit(0)

golden = json.load(io.open(GOLDEN, encoding="utf-8"))
now = compile_all()
check("every fixture is in the golden file", sorted(golden) == sorted(now),
      (sorted(golden), sorted(now)))
for name in sorted(golden):
    want, got = golden[name], now.get(name) or {}
    check(f"'{name}' compiled without errors when captured", not want.get("errors"),
          want.get("errors"))
    for part in sorted(set(want) | set(got)):
        check(f"  ...its {part} are unchanged", got.get(part) == want.get(part),
              {"want": want.get(part), "got": got.get(part)})

# The fixtures really are the shapes they claim (a golden of empty compiles
# would pin nothing).
trig = {n: next((f.get("trigger") for f in now[n]["functions"] if f.get("trigger")), None)
        for n in now}
check("the fixtures compile to real triggers",
      all(trig[n] for n in now), [n for n in now if not trig[n]])

print("FAIL" if bad else "all good")
sys.exit(1 if bad else 0)
