# A Trigger paints two things, and they are two settings.
#
# It used to be ONE colour plus a "colour what" picker -- box, zone, or both.
# That could say "the zone is red" or "the object is red", never "the zone is
# amber and the object inside it is red", which is the drawing an operator
# actually wants: WHERE is wrong, and WHICH THING is wrong, at a glance.
#
# So there are two colours now, each optional and each meaning exactly what it
# is called. The zone it paints is not picked here -- it is the zone the rule
# wired into the Trigger watches, the same one the runtime keys on.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import functions as fx                       # noqa: E402
from core.services import workflow as wf                        # noqa: E402
from app import run as apprun                                   # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


# -- the vocabulary: two colours, both optional -------------------------------
spec = wf.REGISTRY["output.trigger"]["data"]
check("a Trigger carries a colour per thing",
      "box_color" in spec and "zone_color" in spec, repr(spec))
check("...and neither is set on a fresh one",
      spec["box_color"] == "" and spec["zone_color"] == "")

t = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                          "box_color": "#FF0000", "zone_color": "#00A0FF"},
                         "expr", "R")
check("both survive validation", t.get("box_color") == "#FF0000"
      and t.get("zone_color") == "#00A0FF", repr(t))
check("...and the retired picker is not written back",
      "color_target" not in t, repr(t))
t = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                          "zone_color": "not a colour"}, "expr", "R")
check("a colour that is not one is dropped, not passed to a draw call",
      "zone_color" not in t, repr(t))

# -- an OLD trigger keeps meaning what it meant -------------------------------
# One colour aimed by color_target. Until it is re-saved, the runtime must
# paint exactly what it painted before.
old = fx._validate_trigger({"metric": "value", "op": ">", "value": 0,
                            "box_color": "#FF0000", "color_target": "zone"},
                           "expr", "R")
check("an old aimed colour keeps its aim", old.get("color_target") == "zone",
      repr(old))
rules = apprun._color_rules({"functions": [
    {"id": "r1", "params": {"zone": "bay"}, "trigger": old}]})
check("...and the runtime reads it as a ZONE colour",
      len(rules) == 1 and rules[0]["zone_colour"] == "#FF0000"
      and rules[0]["colour"] == "", repr(rules))
both = dict(old, color_target="both")
rules = apprun._color_rules({"functions": [
    {"id": "r1", "params": {"zone": "bay"}, "trigger": both}]})
check("...\"both\" still paints both, in the one colour it had",
      rules[0]["zone_colour"] == "#FF0000" and rules[0]["colour"] == "#FF0000")

# -- and a NEW one paints two colours at once ---------------------------------
rules = apprun._color_rules({"functions": [{"id": "r1", "params": {"zone": "bay"},
                                            "trigger": t | {"box_color": "#FF0000",
                                                            "zone_color": "#00A0FF"}}]})
check("a rule may paint its zone and its objects differently",
      rules[0]["zone_colour"] == "#00A0FF" and rules[0]["colour"] == "#FF0000",
      repr(rules))

dets = [{"label": "person", "violation": True, "zone": "bay"},
        {"label": "person", "violation": False, "zone": ""}]
overlays = [{"id": "bay", "color": "#22C55E"}]
apprun._stamp_colors(rules, dets, {}, overlays)
check("the zone takes the zone colour while the rule holds",
      overlays[0]["color"] == "#00A0FF", repr(overlays))
check("...the violating object takes the box colour",
      dets[0].get("box_color") == "#FF0000", repr(dets[0]))
check("...and the object that is not violating is left alone",
      "box_color" not in dets[1], repr(dets[1]))

# The zone goes BACK when the rule stops -- a zone that went red for one
# crossing must not stay red for the rest of the shift.
apprun._stamp_colors(rules, [{"label": "person", "violation": False}], {}, overlays)
check("the zone is restored when the rule stops holding",
      overlays[0]["color"] == "#22C55E", repr(overlays))

# A zone colour with no zone upstream paints nothing rather than guessing.
rules = apprun._color_rules({"functions": [
    {"id": "r1", "params": {}, "trigger": {"metric": "value", "op": ">",
                                           "value": 0, "zone_color": "#00A0FF"}}]})
ov = [{"id": "bay", "color": "#22C55E"}]
apprun._stamp_colors(rules, [], {"fn_r1_value": 5}, ov)
check("a zone colour with no zone paints nothing",
      ov[0]["color"] == "#22C55E", repr(ov))

# -- the migration lands on the new shape, not the old ------------------------
for aim, want in (("zone", ("", "#FF0000")), ("both", ("#FF0000", "#FF0000")),
                  ("box", ("#FF0000", ""))):
    check(f"a migrated '{aim}' alert becomes {want}",
          wf._split_colour("#FF0000", aim) == want,
          repr(wf._split_colour("#FF0000", aim)))
check("...and no colour stays no colour", wf._split_colour("", "both") == ("", ""))

# -- the zone it paints may be NAMED on the Trigger itself --------------------
# The rule's own zone is the usual answer and stays the fallback. But a rule
# about a doorway may want the whole floor lit, and a frame-wide count watches
# no zone at all -- so the Trigger takes a Zone of its own on a port that
# appears with the setting.
check("a zone may be wired straight into a Trigger",
      wf.edge_allowed("zone.ref", "output.trigger", "zone")[0])
check("...on that port only", not wf.edge_allowed("zone.ref", "output.trigger")[0])
check("...and no other output takes one",
      not wf.edge_allowed("zone.ref", "output.snapshot", "zone")[0])

ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True},
         {"id": "floor", "name": "Floor", "kind": "area", "enabled": True}]


def graph(zone_wired):
    nodes = [
        {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
         "data": {}},
        {"id": "z", "type": "zone.ref", "position": {"x": 0, "y": 1},
         "data": {"zone": "bay"}},
        {"id": "occ", "type": "analytics.occupancy", "position": {"x": 1, "y": 0},
         "data": {"fnId": "bay_now", "name": "Bay", "zone": "bay"}},
        {"id": "cmp", "type": "branch.multi", "position": {"x": 2, "y": 0},
         "data": {"fnId": "crowded", "join": "and",
                  "rules": [{"op": ">", "value": 3, "input": "in1"}]}},
        {"id": "trig", "type": "output.trigger", "position": {"x": 3, "y": 0},
         "data": {"name": "crowded", "zone_color": "#00A0FF"}},
        {"id": "zf", "type": "zone.ref", "position": {"x": 2, "y": 2},
         "data": {"zone": "floor"}},
    ]
    edges = [{"id": "e1", "source": "src", "target": "occ", "targetHandle": "in"},
             {"id": "e2", "source": "z", "target": "occ", "targetHandle": "zone"},
             {"id": "e3", "source": "occ", "target": "cmp", "targetHandle": "in1"},
             {"id": "e4", "source": "cmp", "target": "trig",
              "sourceHandle": "out0", "targetHandle": "in"}]
    if zone_wired:
        edges.append({"id": "e5", "source": "zf", "target": "trig",
                      "targetHandle": "zone"})
    return wf.clean_graph({"nodes": nodes, "edges": edges})


c = wf.compile_graph(graph(True), ZONES)
check("a Trigger with a Zone wired in compiles", not c["errors"], repr(c["errors"]))
# The Trigger names the BRANCH's function (that is the moment it fires on),
# so the compiled trigger is looked for wherever one landed.
trig = next((f.get("trigger") or {} for f in c["functions"] if f.get("trigger")), {})
check("...and the compiled trigger names that zone",
      trig.get("zone_paint") == "floor", repr(trig))
rules = apprun._color_rules({"functions": [
    {"id": "bay_now", "params": {"zone": "bay"}, "trigger": trig}]})
check("...so the runtime paints the wired zone, not the watched one",
      rules[0]["paint_zone"] == "floor" and rules[0]["zone"] == "bay",
      repr(rules))
ov = [{"id": "bay", "color": "#22C55E"}, {"id": "floor", "color": "#22C55E"}]
apprun._stamp_colors(rules, [{"violation": True, "zone": "bay"}], {}, ov)
check("...and the watched zone is left alone",
      ov[1]["color"] == "#00A0FF" and ov[0]["color"] == "#22C55E", repr(ov))

# Unwired, the rule's own zone still answers: a graph drawn before this port
# existed paints exactly what it painted.
c = wf.compile_graph(graph(False), ZONES)
trig = next((f.get("trigger") or {} for f in c["functions"] if f.get("trigger")), {})
check("with no zone wired, the rule's own zone is the fallback",
      "zone_paint" not in trig, repr(trig))
rules = apprun._color_rules({"functions": [
    {"id": "bay_now", "params": {"zone": "bay"}, "trigger": trig}]})
check("...which is what the runtime paints", rules[0]["paint_zone"] == "bay")


# -- what went wrong in the field ---------------------------------------------
# Two failures, both silent, both found on a live customer graph where a
# Trigger was set to paint and nothing on the picture ever changed colour.

# 1. TICKED, BUT NO COLOUR. "Recolour the bounding box" set `draw` and left
#    box_color empty, and an empty colour compiles to no colour -- a setting
#    that reads ON and paints nothing. Ticked means the default colour.
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "realtime"}},
    {"id": "cmp", "type": "branch.multi", "position": {"x": 2, "y": 0},
     "data": {"fnId": "many", "join": "and",
              "rules": [{"op": ">", "value": 1, "input": "in1"}]}},
    {"id": "t1", "type": "output.trigger", "position": {"x": 3, "y": 0},
     "data": {"name": "crowded", "draw": True, "box_color": ""}},
], "edges": [
    {"id": "e1", "source": "src", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "cmp", "targetHandle": "in1"},
    {"id": "e3", "source": "cmp", "target": "t1", "sourceHandle": "out0",
     "targetHandle": "in"},
]}), [])
trig = next((f.get("trigger") or {} for f in c["functions"] if f.get("trigger")), {})
check("ticking 'recolour' with no colour picked paints the default",
      trig.get("box_color") == wf.DEFAULT_BOX_COLOR, repr(trig))
# The Trigger's settings live in workflow/TriggerInspector.jsx since the
# 2026-10-14 layout; the page mounts them.
page = (read("frontend", "src", "pages", "Workflow.jsx")
        + read("frontend", "src", "workflow", "TriggerInspector.jsx"))
check("...and the box that ticks it writes that colour too",
      "box_color: on ? (d.box_color || DEFAULT_BOX_COLOR)" in page)

# 2. TWO TRIGGERS, ONE MOMENT. Both wired to the same branch port: a rule
#    carries one trigger, so the second overwrote the first and everything
#    the first said (its colours included) vanished with nothing on the
#    canvas to say so. It is a conflict now, named on the node.
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "realtime"}},
    {"id": "cmp", "type": "branch.multi", "position": {"x": 2, "y": 0},
     "data": {"fnId": "many", "join": "and",
              "rules": [{"op": ">", "value": 1, "input": "in1"}]}},
    {"id": "t1", "type": "output.trigger", "position": {"x": 3, "y": 0},
     "data": {"name": "painter", "draw": True, "box_color": "#00A0FF"}},
    {"id": "t2", "type": "output.trigger", "position": {"x": 3, "y": 1},
     "data": {"name": "plain"}},
], "edges": [
    {"id": "e1", "source": "src", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "cmp", "targetHandle": "in1"},
    {"id": "e3", "source": "cmp", "target": "t1", "sourceHandle": "out0",
     "targetHandle": "in"},
    {"id": "e4", "source": "cmp", "target": "t2", "sourceHandle": "out0",
     "targetHandle": "in"},
]}), [])
check("a BARE second Trigger just relays the moment",
      not c["errors"], repr(c["errors"]))
check("...saying whose name the events carry",
      any("relays the same moment" in w["message"] for w in c["warnings"]),
      repr(c["warnings"]))
trig = next((f.get("trigger") or {} for f in c["functions"] if f.get("trigger")), {})
check("...and the settings-carrying one still defines the moment",
      trig.get("box_color") == "#00A0FF", repr(trig))

# Order must not decide it: the same two nodes the other way round behave
# identically, or a graph's colours would survive only by luck of drawing.
c_rev = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "realtime"}},
    {"id": "cmp", "type": "branch.multi", "position": {"x": 2, "y": 0},
     "data": {"fnId": "many", "join": "and",
              "rules": [{"op": ">", "value": 1, "input": "in1"}]}},
    {"id": "t2", "type": "output.trigger", "position": {"x": 3, "y": 1},
     "data": {"name": "plain"}},
    {"id": "t1", "type": "output.trigger", "position": {"x": 3, "y": 0},
     "data": {"name": "painter", "draw": True, "box_color": "#00A0FF"}},
], "edges": [
    {"id": "e1", "source": "src", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "cmp", "targetHandle": "in1"},
    {"id": "e3", "source": "cmp", "target": "t2", "sourceHandle": "out0",
     "targetHandle": "in"},
    {"id": "e4", "source": "cmp", "target": "t1", "sourceHandle": "out0",
     "targetHandle": "in"},
]}), [])
trig_rev = next((f.get("trigger") or {} for f in c_rev["functions"]
                 if f.get("trigger")), {})
check("...whichever order they were drawn in",
      not c_rev["errors"] and trig_rev.get("box_color") == "#00A0FF",
      repr((c_rev["errors"], trig_rev)))

# TWO that both carry settings is the real conflict: one set would be lost.
c = wf.compile_graph(wf.clean_graph({"nodes": [
    {"id": "src", "type": "source.detection", "position": {"x": 0, "y": 0},
     "data": {}},
    {"id": "fr", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "realtime"}},
    {"id": "cmp", "type": "branch.multi", "position": {"x": 2, "y": 0},
     "data": {"fnId": "many", "join": "and",
              "rules": [{"op": ">", "value": 1, "input": "in1"}]}},
    {"id": "t1", "type": "output.trigger", "position": {"x": 3, "y": 0},
     "data": {"name": "painter", "draw": True, "box_color": "#00A0FF"}},
    {"id": "t2", "type": "output.trigger", "position": {"x": 3, "y": 1},
     "data": {"name": "alarmer", "alert": True, "severity": "crit"}},
], "edges": [
    {"id": "e1", "source": "src", "target": "fr"},
    {"id": "e2", "source": "fr", "target": "cmp", "targetHandle": "in1"},
    {"id": "e3", "source": "cmp", "target": "t1", "sourceHandle": "out0",
     "targetHandle": "in"},
    {"id": "e4", "source": "cmp", "target": "t2", "sourceHandle": "out0",
     "targetHandle": "in"},
]}), [])
check("two Triggers that BOTH carry settings is an error",
      any("both name the same moment and both carry settings" in e["message"]
          for e in c["errors"]), repr(c["errors"]))
check("...naming both of them, so the operator knows which two",
      any("'alarmer'" in e["message"] and "'painter'" in e["message"]
          for e in c["errors"]), repr(c["errors"]))
# ONE problem, ONE message. The losing Trigger still resolves to the rule, so
# a card or a Data Table wired to it does not ALSO complain that nothing
# feeding it alerts -- true, confusing, and not the thing to fix.
check("...and nothing downstream repeats the complaint",
      len(c["errors"]) == 1, repr(c["errors"]))

# -- the inspector asks the two questions -------------------------------------
page = (read("frontend", "src", "pages", "Workflow.jsx")
        + read("frontend", "src", "workflow", "TriggerInspector.jsx"))
check("the box option highlights the box that is already drawn (2026-10-14 wording)",
      'title="Highlight objects"' in page and "Draw a box on the object" not in page)
check("...and there is a zone option beside it, a row of its own",
      'title="Highlight the zone"' in page and 'onChange({ zone_color:' in page)
# The zone is read off the wiring, never picked twice: the runtime finds it
# through the function's own params.zone, and a picker here could disagree.
check("the zone it paints is read off the wired rule",
      "const triggerZone = (() => {" in page
      and "upstreamFn?.data?.zone" in page)
check("...and a Trigger with nothing to paint says where the port is",
      "Nothing to paint yet" in page)
check("the node grows the port only while the setting is on",
      "const takesZone = props.type === \"output.trigger\""
      in read("frontend", "src", "workflow", "nodes.jsx"))

# -- two rules, one zone: the ACTIVE one paints it ----------------------------
# The customer shape that found this: an If/Else pair painting the same zone,
# red on the violate branch, green on the else. Painting per rule let the
# inactive one restore the base colour over the active one's paint in the
# same frame -- rule order decided, and the red never survived a tick.
pair = apprun._color_rules({"functions": [
    {"id": "b0", "params": {},
     "trigger": {"metric": "value", "op": ">", "value": 0,
                 "box_color": "#FF0000", "zone_color": "#FF0000",
                 "zone_paint": "bay"}},
    {"id": "b1", "params": {},
     "trigger": {"metric": "value", "op": ">", "value": 0,
                 "zone_color": "#00FF1E", "zone_paint": "bay"}},
]})
ov = [{"id": "bay", "color": "#F59E0B"}]
# Violating: b0 holds, b1 does not. The zone must be RED -- b1's turn to say
# "back to base" must not erase it.
apprun._stamp_colors(pair, [], {"fn_b0_value": 1, "fn_b1_value": 0}, ov)
check("the active rule's colour survives the inactive rule's restore",
      ov[0]["color"] == "#FF0000", repr(ov))
# The else branch: green.
apprun._stamp_colors(pair, [], {"fn_b0_value": 0, "fn_b1_value": 1}, ov)
check("...and flips to the other branch's colour when it holds",
      ov[0]["color"] == "#00FF1E", repr(ov))
# Neither (rules gone quiet): base.
apprun._stamp_colors(pair, [], {"fn_b0_value": 0, "fn_b1_value": 0}, ov)
check("...and back to its own colour when neither holds",
      ov[0]["color"] == "#F59E0B", repr(ov))

# -- a Trigger is not an alert with extras ------------------------------------
# All three things it may do -- alarm, paint the objects, paint the zone --
# are equal and optional. They sat behind "Advanced" together, which read as
# "a Trigger is an alert, and here are some extras", so somebody who wanted
# only the colours had to find them behind a fold.
check("the three things it may do are offered together, not behind Advanced",
      "3. What should happen?" in page and "Any, all or none." in page
      and "Both halves OFF by default" not in page)
nodes_src = read("frontend", "src", "workflow", "nodes.jsx")
check("...and the node face does not call a painting Trigger 'event only'",
      'd.alert ? `${d.severity || "warn"} alert` : null,' in nodes_src
      and '|| "event only",' in nodes_src)

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("ALL OK")
