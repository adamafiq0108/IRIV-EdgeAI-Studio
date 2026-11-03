# A Trigger's "Recolour the bounding box", and "Only boxes inside the zone".
#
# Tok Aba (2026-10-07): "if trigger have zone, then the bounding box in the
# zone only change the color if user choose that".
#
# What is pinned here:
#   1. with the option off: a workflow Trigger decorates a branch (or a
#      Formula), an expression that watches no zone, so its box colour lands
#      on every object OF THE RULE'S CLASSES the camera sees, inside the
#      zone or not, while the branch holds -- even with a zone rule behind
#      the Compare. The classes are the rule's (smoke_triggerboxclass.py);
#      with no Class Filter anywhere behind it, any class, and said on the
#      node
#   2. the option off (or absent) compiles exactly as before
#   3. on, the compiled trigger names the zone: the Trigger's own Zone port
#      first, else the zone rule behind it; no zone, or a line, is a warning
#      on the node and every box is painted as before
#   4. the runtime paints only the objects the zone engine counts as inside
#      that zone (the real ZoneEngine, so the same anchor test the zone
#      rules count with); the rest keep their own colour
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from app import run as apprun                                   # noqa: E402
from app import zones as zn                                     # noqa: E402
from core.services import functions as fx                       # noqa: E402
from core.services import trigger_paint                         # noqa: E402
from core.services import workflow as wf                        # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True},
         {"id": "floor", "name": "Floor", "kind": "area", "enabled": True},
         {"id": "door", "name": "Door", "kind": "line", "enabled": True}]


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def graph(trigger=None, rule_zone="bay", port_zone=None, classes=None):
    """Detection [-> Class Filter] -> rule (on rule_zone, or In Frame with
    none) -> Compare -> Trigger, with port_zone wired into the Trigger's
    own Zone port."""
    nodes = [node("src", "source.detection"),
             node("cmp", "branch.multi", fnId="crowded", join="and",
                  rules=[{"op": ">", "value": 3, "input": "in1"}]),
             node("trig", "output.trigger",
                  **({"name": "crowded", "draw": True} | (trigger or {})))]
    edges = [{"id": "e3", "source": "r", "target": "cmp", "targetHandle": "in1"},
             {"id": "e4", "source": "cmp", "target": "trig",
              "sourceHandle": "out0", "targetHandle": "in"}]
    feed = "src"
    if classes:
        nodes.append(node("flt", "filter.class", classes=list(classes)))
        edges.append({"id": "e0", "source": "src", "target": "flt"})
        feed = "flt"
    if rule_zone:
        nodes += [node("z", "zone.ref", zone=rule_zone),
                  node("r", "analytics.occupancy", fnId="bay_now", name="Bay")]
        edges += [{"id": "e1", "source": feed, "target": "r", "targetHandle": "in"},
                  {"id": "e2", "source": "z", "target": "r", "targetHandle": "zone"}]
    else:
        nodes.append(node("r", "analytics.frame", fnId="seen", name="Seen",
                          mode="realtime"))
        edges.append({"id": "e1", "source": feed, "target": "r"})
    if port_zone:
        nodes.append(node("zp", "zone.ref", zone=port_zone))
        edges.append({"id": "e5", "source": "zp", "target": "trig",
                      "targetHandle": "zone"})
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g


def compiled(**kw):
    c = wf.compile_graph(graph(**kw), ZONES, tables={})
    trig = next(f["trigger"] for f in c["functions"] if f["id"] == "crowded_b0")
    warns = [w["message"] for w in c.get("warnings") or [] if w["node"] == "trig"]
    return c, trig, warns


def det(label, zones):
    return {"label": label, "zones": list(zones)}


def painted(trig, dets, value=1):
    rules = apprun._color_rules({"functions": [
        {"id": "crowded_b0", "params": {"expr": "x"}, "trigger": trig}]})
    apprun._stamp_colors(rules, dets, {"fn_crowded_b0_value": value}, [])
    return [d.get("box_color", "") for d in dets]


# -- 1. what it recolours with the option off ------------------------------------
print("== the option off: every object of the rule's classes in view")
c, trig, warns = compiled()
check("a zone graph with a box colour compiles", not c["errors"], c["errors"])
fn = next(f for f in c["functions"] if f["id"] == "crowded_b0")
check("the Trigger's rule is the branch: an expression, no zone, no classes",
      fn["params"] == {"expr": "(fn_bay_now_count > 3)"}, fn["params"])
dets = [det("person", ["bay"]), det("person", []), det("forklift", ["floor"])]
cols = painted(trig, dets)
check("no Class Filter anywhere behind it: while it holds EVERY box takes "
      "the colour - inside the bay, outside it, any class",
      "box_classes" not in trig and cols == [trig["box_color"]] * 3, (trig, cols))
check("...and the node says so",
      any("no Class Filter behind this Trigger" in w for w in warns), warns)
check("...and none while it does not",
      painted(trig, [det("person", ["bay"])], value=0) == [""])
c, trig, warns = compiled(classes=["person"])
check("a Class Filter behind the rule names the Trigger's classes",
      trig.get("box_classes") == ["person"] and not warns, (trig, warns))
dets = [det("person", ["bay"]), det("person", []), det("forklift", ["bay"])]
cols = painted(trig, dets)
check("...and only those take the colour - inside the bay or out; the "
      "forklift in the bay never", cols == [trig["box_color"]] * 2 + [""], cols)

# -- 2. off compiles exactly as before -------------------------------------------
print("== the option off")
check("a fresh Trigger has it, off", wf.REGISTRY["output.trigger"]["data"]
      .get("box_in_zone") is False)
_c, plain, _w = compiled()
_c, off, _w = compiled(trigger={"box_in_zone": False})
check("off compiles to exactly what an untouched Trigger does",
      json.dumps(plain, sort_keys=True) == json.dumps(off, sort_keys=True)
      and "box_zone" not in off, off)
_c, nodraw, _w = compiled(trigger={"draw": False, "box_in_zone": True})
check("ticked with no box colour: nothing to keep inside, nothing compiled",
      "box_zone" not in nodraw and "box_color" not in nodraw, nodraw)

# -- 3. on: which zone ------------------------------------------------------------
print("== the option on: which zone")
_c, on, warns = compiled(trigger={"box_in_zone": True})
zone_warns = lambda ws: [w for w in ws if "zone" in w]           # noqa: E731
check("the zone rule behind the Compare names it", on.get("box_zone") == "bay"
      and not zone_warns(warns), (on, warns))
_c, on, warns = compiled(trigger={"box_in_zone": True}, port_zone="floor")
check("a Zone on the Trigger's own port answers first",
      on.get("box_zone") == "floor" and not zone_warns(warns), (on, warns))
_c, on, warns = compiled(trigger={"box_in_zone": True}, rule_zone=None,
                         port_zone="floor")
check("...and is enough on its own (a frame-wide rule behind it)",
      on.get("box_zone") == "floor", on)
_c, on, warns = compiled(trigger={"box_in_zone": True}, rule_zone=None)
check("no zone anywhere: said on the node, and every box painted as before",
      "box_zone" not in on and any("needs a zone" in w for w in warns), warns)
_c, on, warns = compiled(trigger={"box_in_zone": True}, port_zone="door")
check("a line has no inside: said on the node, every box painted",
      "box_zone" not in on and any("is a line" in w for w in warns), warns)
check("no em dash in what it says", all("\u2014" not in w for w in warns))
kept = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                             "box_color": "#FF0000", "box_zone": "bay",
                             "box_classes": ["person", " ", "car"]}, "expr", "R")
dropped = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                                "box_zone": "bay", "box_classes": ["person"]},
                               "expr", "R")
check("the validator keeps the zone and the classes only with a box colour",
      kept.get("box_zone") == "bay" and kept.get("box_classes") == ["person", "car"]
      and "box_zone" not in dropped and "box_classes" not in dropped,
      (kept, dropped))
preds = {"trig": [({"targetHandle": "oid"}, {"id": "zq", "type": "zone.ref",
                                            "data": {"zone": "floor"}})]}
check("the walk does not read a zone off the OID port",
      trigger_paint.zone_behind("trig", preds) == "")

# -- 4. on: which boxes -------------------------------------------------------------
print("== the option on: which boxes")
_c, on, _w = compiled(trigger={"box_in_zone": True})
dets = [det("person", ["bay"]), det("person", []), det("forklift", ["floor"]),
        det("forklift", ["floor", "bay"])]
cols = painted(on, dets)
c0 = on["box_color"]
check("only the objects inside the bay take the colour; the rest keep "
      "their own (no Class Filter: any class)", cols == [c0, "", "", c0], cols)
check("...and none while it does not hold",
      painted(on, [det("person", ["bay"])], value=0) == [""])
_c, on_p, _w = compiled(trigger={"box_in_zone": True}, classes=["person"])
check("with a Class Filter: only the rule's classes inside the bay, so the "
      "forklift in it keeps its own colour",
      painted(on_p, [det("person", ["bay"]), det("person", []),
                     det("forklift", ["bay"])]) == [c0, "", ""])
# The real zone engine decides "inside", with the anchor the zone rules use:
# the bottom centre of the box, by default.
engine = zn.ZoneEngine(zn.parse_zones([
    {"id": "bay", "name": "Bay", "kind": "area",
     "points": [[0.0, 0.5], [0.5, 0.5], [0.5, 1.0], [0.0, 1.0]]}]))
dets = [{"label": "person", "box": [100, 500, 200, 900]},    # feet in the bay
        {"label": "person", "box": [700, 500, 800, 900]},    # feet outside
        {"label": "person", "box": [100, 100, 200, 450]}]    # above it
engine.update(dets, 1000, 1000)
check("the zone engine stamps who is inside (what the zone rules count)",
      [d.get("zones", []) for d in dets] == [["bay"], [], []],
      [d.get("zones") for d in dets])
cols = painted(on, dets)
check("...and exactly those take the Trigger's colour", cols == [c0, "", ""],
      cols)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
