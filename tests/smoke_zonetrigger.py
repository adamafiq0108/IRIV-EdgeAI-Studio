# Ticking a zone's Trigger builds the whole chain on the Workflow canvas.
#
# The tick used to compile a bare counter: a number, no alert, and a canvas
# showing a Zone Counter wired to nothing. So the one thing an operator ticks
# it FOR -- react to this -- had no node to configure. Where the box colour is
# set, whether a photo is saved, what a card reports: all of that lives on an
# Alert node that was never drawn.
#
# Now the tick compiles a rule WITH a trigger, and the mirror draws the chain
# it deserves: Detection Stream -> Zone -> Zone Counter -> Alert.
#
# The tick is also no longer called "violation" on screen. It is ticked for
# anything worth reacting to -- an object arriving, a part reaching a station,
# somebody where nobody should be -- and only some of those are violations.
# The stored KEY stays `violation`: the app, the overlay colours and every
# config already written read it, so renaming the word is the fix and renaming
# the key would break every system running today.
import io
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import workflow as wf                      # noqa: E402

SLUG = "smoketrigger"
ZONES = [{"id": "zone_a", "name": "Keep out", "kind": "area", "enabled": True,
          "violation": True}]

# What apply_zone_actions compiles for a ticked zone: the live count, and the
# alert the tick is ticked for.
ZONE_FNS = [{"id": "z_zone_a_zoneviol", "name": "Keep out violations now",
             "type": "zone_count", "enabled": True,
             "params": {"zone": "zone_a"},
             "trigger": {"metric": "count", "op": ">", "value": 0,
                         "severity": "warn", "message": "object in Keep out",
                         "cooldown_s": 2}}]

wf.save_store(SLUG, {"version": 3, "workflows": [{
    "id": wf.MAIN_ID, "name": "Main", "description": "", "enabled": True,
    "author": "", "created": 0, "modified": 0,
    "graph": {"version": wf.SCHEMA_VERSION, "nodes": [], "edges": []}}]})

assert wf.sync_zone_actions(SLUG, ZONE_FNS, ZONES) is True
graph = wf.load_store(SLUG)["workflows"][0]["graph"]
types = [n["type"] for n in graph["nodes"]]
assert "source.detection" in types, types
assert "zone.ref" in types, types
assert "analytics.zone_count" in types or any(
    t.startswith("analytics.") for t in types), types
assert "output.alert" in types, "the tick drew no Alert node"
print("== the tick draws the whole chain:", sorted(set(types)))

# The Alert carries the rule's own trigger, so what it says on the canvas is
# what the engine does -- not a decorative node.
alert = next(n for n in graph["nodes"] if n["type"] == "output.alert")
assert alert["data"]["message"] == "object in Keep out", alert["data"]
assert alert["data"]["metric"] == "count", alert["data"]
print("== and it carries the rule's real trigger:", alert["data"]["message"])

# It is WIRED to the counter: an Alert nobody feeds compiles to nothing.
an = next(n for n in graph["nodes"] if n["type"].startswith("analytics."))
assert any(e["source"] == an["id"] and e["target"] == alert["id"]
           for e in graph["edges"]), graph["edges"]
print("== wired from the counter that feeds it")

# -- the compile side ---------------------------------------------------------
SRC = io.open(os.path.join(ROOT, "core", "services", "functions.py"),
              encoding="utf-8").read()
block = SRC[SRC.index('if z.get("violation") and not is_line and not viol_actions:'):]
block = block[:block.index("for a in z.get(")]
assert '"trigger"' in block, "the tick still compiles a counter with no alert"
assert '"metric": "count"' in block, block
print("== apply_zone_actions gives the tick a trigger")

# -- no double logging --------------------------------------------------------
# The app already writes its own "object in keep-out zone" event for a ticked
# zone. With the rule now raising its own alert, that is the same incident
# twice, and an operator reconciling two rows for one thing is worse off than
# before. The built-in stands down where a rule owns the zone.
APP = io.open(os.path.join(ROOT, "systems", "demo", "app", "run.py"),
              encoding="utf-8").read()
gated = APP[APP.index("def _gated_events("):]
gated = gated[:gated.index("\ndef ", 5)]
assert "_alerting_zone_ids(pipe)" in gated, "the built-in event never stands down"
assert "violations = [v for v in violations" in gated, gated[:600]
own = APP[APP.index("def _alerting_zone_ids("):]
own = own[:own.index("\ndef ", 5)]
# Only rules that actually alert suppress it -- a plain counter on a zone must
# not silence the keep-out event, because nothing else would report it.
assert 'getattr(fn, "trigger", None)' in own, own
assert '"zone"' in own, own
print("== the built-in keep-out event stands down only where a rule alerts")

# -- the wiring the alert now has ---------------------------------------------
NODES = io.open(os.path.join(ROOT, "frontend", "src", "workflow", "nodes.jsx"),
                encoding="utf-8").read()
# The node that NAMES a moment is the one output with a way out: the old
# Condition, and the Trigger that replaced it (smoke_branch.py).
assert 'hasSource={["output.alert", "output.trigger"].includes(props.type)}' \
    in NODES, "the node that names a moment has no way out"
print("== the node that names a moment has an output handle to drag from")

WF = io.open(os.path.join(ROOT, "frontend", "src", "pages", "Workflow.jsx"),
             encoding="utf-8").read()
conn = WF[WF.index("const onConnect = useCallback"):]
conn = conn[:conn.index("const onConnectEnd")]
assert 'kind: "verdict", metric: "active"' in conn, \
    "an Alert wired into a Card does not adopt the verdict shape"
# ...but only an untouched card. Wiring into a card somebody already set up is
# not consent to overwrite their choice.
assert "untouched" in conn, conn
print("== Alert -> Card adopts the verdict shape, and only on an untouched card")

# -- the words on the card ----------------------------------------------------
REG = io.open(os.path.join(ROOT, "core", "services", "workflow.py"),
              encoding="utf-8").read()
assert '"ok_label": "COMPLIANT"' in REG, "the default verdict wording did not change"
assert "NORMAL" not in REG.split("dashboard.card")[1][:2000], "NORMAL left behind"
print("== a verdict reads COMPLIANT / VIOLATION by default")
print("ALL OK")
