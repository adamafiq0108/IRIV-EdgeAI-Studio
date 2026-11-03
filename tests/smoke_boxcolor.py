"""An Alert may say what colour its objects are drawn in. Red by default.

The colour belongs to the ALERT, but it has to reach the DETECTIONS, and how
depends on what the rule watches:

  * a zone rule  -- the objects inside it, which zones.py has already marked;
  * no zone      -- a class rule, or a count over the whole frame. A violation
                    does not require a zone ("more than 3 people in frame" is
                    about nowhere in particular), so its boxes must change
                    colour too -- matched by the classes it counts, while its
                    condition holds.

    python tests/smoke_boxcolor.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))

from core.services import functions as fs                     # noqa: E402
from core.services import workflow as wf                      # noqa: E402
from app import run as app_run                                # noqa: E402
from app import camera as cam                                 # noqa: E402

bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {got!r}")
    if not ok:
        print(f"      wanted {want!r}")
        bad += 1


def det(label="person", zone=None, violation=False):
    d = {"label": label, "confidence": 0.9, "box": [0, 0, 10, 10]}
    if zone:
        d["zone"] = zone
    if violation:
        d["violation"] = True
    return d


# -- the colour is validated, never guessed at ---------------------------------
check("a hex colour is kept", fs.box_colour("#00FF88"), "#00FF88")
check("...case-normalised", fs.box_colour("#00ff88"), "#00FF88")
check("...and a missing # forgiven", fs.box_colour("00ff88"), "#00FF88")
# A name or an rgb() would be a silent no-draw rather than an error anybody
# sees, so neither is accepted.
for junk in ("red", "rgb(255,0,0)", "#fff", "#gggggg", "", None):
    check(f"{junk!r} is not a colour", fs.box_colour(junk), "")

# -- it survives the trigger validator -----------------------------------------
trig = fs._validate_trigger({"metric": "count", "op": ">", "value": 1,
                             "box_color": "#00ff88"}, "frame_count", "Frame")
check("a trigger carries the colour", trig.get("box_color"), "#00FF88")
# A colour with no aim is a BOX colour, and says so by carrying no aim at
# all: `color_target` is the deprecated shape (one colour, aimed), written
# back only where it still changes the answer. See smoke_triggercolour.py.
check("...and a plain box colour needs no aim key",
      "color_target" in trig, False)
trig2 = fs._validate_trigger({"metric": "count", "op": ">", "value": 1,
                              "box_color": "#00ff88", "color_target": "zone"},
                             "frame_count", "Frame")
check("a chosen target is kept", trig2.get("color_target"), "zone")
trig3 = fs._validate_trigger({"metric": "count", "op": ">", "value": 1,
                              "box_color": "#00ff88", "color_target": "sky"},
                             "frame_count", "Frame")
check("an unknown target falls back to the boxes",
      trig3.get("color_target", "box"), "box")
# Absent means red, so a config written before this existed keeps its meaning
# rather than gaining a key that says "red" in a second way.
trig = fs._validate_trigger({"metric": "count", "op": ">", "value": 1},
                            "frame_count", "Frame")
check("no colour named, no key added", "box_color" in trig, False)

# -- a zone rule colours what is inside the zone -------------------------------
rules = app_run._color_rules({"functions": [
    {"id": "z1", "params": {"zone": "bay"},
     "triggers": [{"metric": "count", "op": ">", "value": 0,
                   "box_color": "#00FF88"}]}]})
dets = [det(zone="bay", violation=True), det(zone="door", violation=True), det()]
app_run._stamp_colors(rules, dets, {})
check("the violator in that zone is coloured", dets[0].get("box_color"), "#00FF88")
check("another zone's violator is not", dets[1].get("box_color"), None)
check("a passer-by is not", dets[2].get("box_color"), None)

# -- a rule with NO zone colours the objects it counts -------------------------
# The case that makes this more than a zone lookup.
rules = app_run._color_rules({"functions": [
    {"id": "crowd", "params": {"classes": ["person"]},
     "triggers": [{"metric": "count", "op": ">", "value": 3,
                   "box_color": "#FFAA00"}]}]})
dets = [det("person"), det("person"), det("forklift")]

app_run._stamp_colors(rules, dets, {"fn_crowd_count": 2})
check("below the threshold nothing is coloured",
      [d.get("box_color") for d in dets], [None, None, None])

app_run._stamp_colors(rules, dets, {"fn_crowd_count": 5})
check("over it, the counted class is coloured",
      [d.get("box_color") for d in dets[:2]], ["#FFAA00", "#FFAA00"])
check("...and a class it does not count is left alone",
      dets[2].get("box_color"), None)

# A rule that narrows no classes is about everything it sees.
rules = app_run._color_rules({"functions": [
    {"id": "any", "params": {},
     "triggers": [{"metric": "count", "op": ">=", "value": 1,
                   "box_color": "#123456"}]}]})
dets = [det("person"), det("forklift")]
app_run._stamp_colors(rules, dets, {"fn_any_count": 1})
check("an unnarrowed rule colours every object",
      [d.get("box_color") for d in dets], ["#123456", "#123456"])

# An alert that names no colour leaves everything default.
rules = app_run._color_rules({"functions": [
    {"id": "plain", "params": {}, "triggers": [{"metric": "count", "op": ">",
                                                "value": 0}]}]})
check("no colour means no rule to apply", rules, [])

# -- what gets recoloured is the operator's choice -----------------------------
# A zone rule often reads better with the ZONE going red than with the boxes
# inside it; which is right depends on the scene.
def zone_rules(target):
    return app_run._color_rules({"functions": [
        {"id": "z1", "params": {"zone": "bay"},
         "triggers": [{"metric": "count", "op": ">", "value": 0,
                       "box_color": "#00FF88", "color_target": target}]}]})


overlays = [{"id": "bay", "color": "#FF0000"}, {"id": "door", "color": "#FF0000"}]
inside = [det(zone="bay", violation=True)]

app_run._stamp_colors(zone_rules("zone"), inside, {}, overlays)
check("target=zone repaints the outline", overlays[0]["color"], "#00FF88")
check("...and leaves the box alone", inside[0].get("box_color"), None)
check("...and another zone alone", overlays[1]["color"], "#FF0000")

# ...and puts it back once nobody is in it. A zone that went red for one
# crossing must not stay red for the rest of the shift.
app_run._stamp_colors(zone_rules("zone"), [], {}, overlays)
check("the outline is restored when the rule stops holding",
      overlays[0]["color"], "#FF0000")

overlays = [{"id": "bay", "color": "#FF0000"}]
inside = [det(zone="bay", violation=True)]
app_run._stamp_colors(zone_rules("both"), inside, {}, overlays)
check("target=both repaints the outline", overlays[0]["color"], "#00FF88")
check("...and the box", inside[0].get("box_color"), "#00FF88")

overlays = [{"id": "bay", "color": "#FF0000"}]
inside = [det(zone="bay", violation=True)]
app_run._stamp_colors(zone_rules("box"), inside, {}, overlays)
check("target=box leaves the outline alone", overlays[0]["color"], "#FF0000")
check("...and repaints the box", inside[0].get("box_color"), "#00FF88")

# A Trigger carries a colour per thing now (box_color / zone_color), and the
# aimed single colour above is the DEPRECATED shape -- kept working, read
# into the pair. With no aim at all, it was always the boxes.
check("the default target is the boxes",
      (zone_rules("")[0]["colour"], zone_rules("")[0]["zone_colour"]),
      ("#00FF88", ""))

# -- drawing -------------------------------------------------------------------
check("a stamped colour is read back as RGB",
      cam._det_rgb({"box_color": "#00FF88"}), (0, 255, 136))
check("an unstamped detection has no colour", cam._det_rgb({}), None)
check("junk is ignored rather than guessed",
      cam._det_rgb({"box_color": "red"}), None)
# The colour applies WITHOUT a violation flag -- that is the zoneless case.
check("a coloured non-violation still resolves",
      cam._det_rgb({"box_color": "#112233"}), (17, 34, 51))

# -- the node offers it ---------------------------------------------------------
check("the Alert node carries a colour",
      "box_color" in wf.REGISTRY["output.alert"]["data"], True)
check("...defaulting to empty, which means red",
      wf.REGISTRY["output.alert"]["data"]["box_color"], "")
check("...and a target, defaulting to the boxes",
      wf.REGISTRY["output.alert"]["data"]["color_target"], "box")

# -- and it compiles through ----------------------------------------------------
graph = wf.clean_graph({"nodes": [
    {"id": "s", "type": "source.detection", "position": {"x": 0, "y": 0}, "data": {}},
    {"id": "c", "type": "analytics.frame", "position": {"x": 1, "y": 0},
     "data": {"fnId": "crowd", "name": "Crowd", "mode": "realtime"}},
    {"id": "a", "type": "output.alert", "position": {"x": 2, "y": 0},
     "data": {"metric": "count", "op": ">", "value": 3, "box_color": "#FFAA00"}},
], "edges": [{"id": "e1", "source": "s", "target": "c"},
             {"id": "e2", "source": "c", "target": "a"}]})
compiled = wf.compile_graph(graph, [])
check("the graph compiles", compiled["errors"], [])
fn = compiled["functions"][0]
check("the colour reaches the function's trigger",
      (fn.get("triggers") or [{}])[0].get("box_color"), "#FFAA00")

if bad:
    print(f"\n{bad} FAILED")
    sys.exit(1)
print("\nALL OK")
