"""Nine Workflow node types that had never been compiled by any test.

Yaya's node-by-node pass over `wf.REGISTRY` (94 node types then; the
registry has grown since) turned up nodes
that only ever got exercised structurally (through smoke_math.py's loop over
MATH_NODES) or not at all:

  * analytics.proximity / analytics.guard -- real, undeprecated nodes with no
    compile test anywhere.
  * analytics.crowd -- deprecated (merged into analytics.violations) but the
    registry still promises an old canvas keeps compiling; nothing checked it.
  * analytics.block -- pulled from the palette (hidden) but not deprecated: an
    existing canvas must keep compiling its blueprint sub-graph.
  * variable.api / variable.formula -- API polling and the old Formula
    Variable; the second is deprecated but, like analytics.crowd, was never
    actually compiled.
  * output.mqtt_publish / output.webhook / output.webhook_publish --
    telemetry and alert HTTP/MQTT outputs with zero test coverage; webhook
    also pins that firing it with no Condition upstream fails with a message
    naming the fix, not a silent bad compile.

Nothing here is a fix. If any of these turn up broken they are Boboiboy's,
not Yaya's -- see docs/team/yaya/Workflow Node Test Report.docx.

    python tests/smoke_nodecoverage.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

from core.services import functions as fx                      # noqa: E402
from core.services import workflow as wf                       # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL") + "  " + name
          + (" -- " + detail if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0}, "data": data}


def wire(src, tgt, **kw):
    return {"id": f"e_{src}_{tgt}_{kw.get('targetHandle', '')}",
            "source": src, "target": tgt, **kw}


ZONES = [{"id": "bay", "name": "Bay", "kind": "area", "enabled": True}]


def compile_(nodes, edges, zones=ZONES):
    return wf.compile_graph(wf.clean_graph({"nodes": nodes, "edges": edges}), zones)


# -- analytics.proximity ------------------------------------------------------
c = compile_(
    [node("s", "source.detection"),
     node("px", "analytics.proximity", fnId="px1", name="Forklift distance",
          class_a="person", class_b="forklift", max_distance=0.2),
     node("crd", "dashboard.card", metric="pairs")],
    [wire("s", "px"), wire("px", "crd")])
check("analytics.proximity compiles clean", not c["errors"], c["errors"])
fn = next((f for f in c.get("functions", []) if f["id"] == "px1"), None)
check("...to a proximity rule with both classes",
      bool(fn) and fn["type"] == "proximity"
      and fn["params"]["class_a"] == "person"
      and fn["params"]["class_b"] == "forklift",
      fn)
check("...and the card reads the pairs metric",
      any(w.get("key") == "fn_px1_pairs" for w in c["widgets"]),
      c["widgets"])

c2 = compile_(
    [node("s", "source.detection"),
     node("px", "analytics.proximity", fnId="px2", name="Bad",
          class_a="person", class_b="")],
    [wire("s", "px")])
check("...and an empty second class is a clear compile error, not a silent 0",
      bool(c2["errors"]) and "class" in " ".join(
          e["message"] for e in c2["errors"]).lower(),
      c2["errors"])

# -- analytics.guard -----------------------------------------------------------
c = compile_(
    [node("s", "source.detection"),
     node("gd", "analytics.guard", fnId="gd1", name="Forklift bubble",
          classes=["forklift"], alert_classes=["person"], margin_pct=60),
     node("crd", "dashboard.card", metric="intrusions")],
    [wire("s", "gd"), wire("gd", "crd")])
check("analytics.guard compiles clean", not c["errors"], c["errors"])
fn = next((f for f in c.get("functions", []) if f["id"] == "gd1"), None)
check("...to a guard rule carrying both class lists",
      bool(fn) and fn["type"] == "guard"
      and fn["params"].get("alert_classes") == ["person"],
      fn)
check("...and the card reads the intrusions metric",
      any(w.get("key") == "fn_gd1_intrusions" for w in c["widgets"]), c["widgets"])

# -- analytics.crowd (deprecated, merged into analytics.violations) -----------
# The registry says an old canvas "still compiles" -- nothing had ever run
# one through the compiler to check that promise holds.
check("the node is actually marked deprecated, as the registry comment claims",
      wf.REGISTRY["analytics.crowd"].get("deprecated") is True)
c = compile_(
    [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
     node("cw", "analytics.crowd", fnId="cw1", name="Old crowd limit",
          limit=4, severity="warn")],
    [wire("s", "cw"), wire("z", "cw", targetHandle="zone")])
check("...and an old graph using it still compiles with no errors",
      not c["errors"], c["errors"])
fn = next((f for f in c.get("functions", []) if f["id"] == "cw1"), None)
check("...to a real zone_count rule, not a stub",
      bool(fn) and fn["type"] == "zone_count" and fn["params"].get("zone") == "bay",
      fn)

# -- analytics.block (hidden from the palette, not deprecated) ----------------
check("hidden, not deprecated: an existing canvas must keep compiling",
      wf.REGISTRY["analytics.block"].get("hidden") is True
      and not wf.REGISTRY["analytics.block"].get("deprecated"))
c = compile_([node("s", "source.detection"),
              node("fb", "analytics.block", fnId="fb1", name="Empty block",
                   blueprint={"nodes": [], "edges": []})],
             [wire("s", "fb")])
check("an empty blueprint is refused with a message that says how to fix it",
      bool(c["errors"]) and "double-click" in " ".join(
          e["message"] for e in c["errors"]),
      c["errors"])
c = compile_([node("s", "source.detection"),
              node("fb", "analytics.block", fnId="fb2", name="Five",
                   blueprint={
                       "nodes": [{"id": "n", "type": "bp.const", "data": {"value": 5}},
                                 {"id": "o", "type": "bp.out", "data": {}}],
                       "edges": [{"source": "n", "target": "o",
                                 "targetHandle": "in"}]})],
             [wire("s", "fb")])
check("a real blueprint (Number -> Output) compiles to that constant",
      not c["errors"] and any(
          f["id"] == "fb2" and f["params"]["expr"] == "5" for f in c["functions"]),
      (c["errors"], c.get("functions")))

# -- variable.api ---------------------------------------------------------------
check("api_poll needs no Detection Stream -- it is a standalone reading",
      "api_poll" in wf.STANDALONE_FN_TYPES)
c = compile_([node("api", "variable.api", fnId="parts", name="Parts made",
                   url="http://10.0.0.5/api/parts", interval_s=5,
                   json_path="data.count")], [])
check("variable.api compiles with no camera wired at all", not c["errors"], c["errors"])
fn = next((f for f in c.get("functions", []) if f["id"] == "parts"), None)
check("...to an api_poll rule carrying the url and json_path",
      bool(fn) and fn["type"] == "api_poll"
      and fn["params"]["url"] == "http://10.0.0.5/api/parts"
      and fn["params"]["json_path"] == "data.count",
      fn)
try:
    fx.validate_function({"id": "x", "name": "x", "type": "api_poll",
                          "params": {"url": "not-a-url"}})
    check("a url with no http(s) scheme is refused", False, "it was accepted")
except ValueError as exc:
    check("a url with no http(s) scheme is refused", True)
    print(f"        ({exc})")

# -- variable.formula (deprecated Formula Variable) ----------------------------
check("variable.formula is marked deprecated", wf.REGISTRY["variable.formula"].get("deprecated") is True)
c = compile_([node("v", "variable.formula", fnId="five", name="Five", expr="2 + 3")], [])
check("variable.formula still compiles an old typed expression", not c["errors"], c["errors"])
fn = next((f for f in c.get("functions", []) if f["id"] == "five"), None)
check("...to the same 'expr' rule type analytics.expr uses",
      bool(fn) and fn["type"] == "expr", fn)

# -- output.mqtt_publish (telemetry, no Condition needed) ---------------------
c = compile_(
    [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
     node("cnt", "analytics.counter", fnId="cnt1", name="People", mode="realtime"),
     node("mq", "output.mqtt_publish", host="10.0.0.9", topic="plc/line1/people",
          interval_s=2)],
    [wire("s", "cnt"), wire("z", "cnt", targetHandle="zone"),
     wire("cnt", "mq", sourceHandle="count")])
check("output.mqtt_publish compiles with no Condition upstream (it's telemetry)",
      not c["errors"], c["errors"])
doc = next((o for o in c.get("outputs", []) if o["type"] == "mqtt_publish"), None)
check("...and publishes the exact metric it was wired from, when it changes "
      "(a saved Every is dropped: the wire says when, not the Publish)",
      bool(doc) and doc["key"] == "fn_cnt1_count" and doc["trigger"] == "any"
      and doc["topic"] == "plc/line1/people" and doc.get("mode") == "change"
      and "interval_s" not in doc and "beat" not in doc,
      doc)

# -- output.webhook (an ALERT output -- needs a Condition, not telemetry) -----
c = compile_(
    [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
     node("cnt", "analytics.counter", fnId="cnt2", name="People2", mode="realtime"),
     node("cond", "output.alert", metric="count", op=">", value=3),
     node("wh", "output.webhook", url="http://10.0.0.9/ingest", method="POST")],
    [wire("s", "cnt"), wire("z", "cnt", targetHandle="zone"),
     wire("cnt", "cond"), wire("cond", "wh")])
check("output.webhook compiles when wired after a Condition", not c["errors"], c["errors"])
doc = next((o for o in c.get("outputs", []) if o["type"] == "webhook"), None)
check("...and fires on that rule's alert",
      bool(doc) and doc["trigger"] == "alert" and doc["function"] == "cnt2", doc)

c_bad = compile_(
    [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
     node("cnt", "analytics.counter", fnId="cnt3", name="People3", mode="realtime"),
     node("wh", "output.webhook", url="http://10.0.0.9/ingest")],
    [wire("s", "cnt"), wire("z", "cnt", targetHandle="zone"), wire("cnt", "wh")])
check("...but wired straight onto a measurement (no Condition) it refuses, "
      "naming the fix rather than compiling to nonsense",
      bool(c_bad["errors"]) and "Condition" in " ".join(
          e["message"] for e in c_bad["errors"]),
      c_bad["errors"])

# -- output.webhook_publish (the HTTP twin of mqtt_publish) -------------------
c = compile_(
    [node("s", "source.detection"), node("z", "zone.ref", zone="bay"),
     node("cnt", "analytics.counter", fnId="cnt4", name="People4", mode="realtime"),
     node("wp", "output.webhook_publish", url="http://10.0.0.9/telemetry",
          interval_s=3)],
    [wire("s", "cnt"), wire("z", "cnt", targetHandle="zone"),
     wire("cnt", "wp", sourceHandle="count")])
check("output.webhook_publish compiles with no Condition upstream", not c["errors"], c["errors"])
doc = next((o for o in c.get("outputs", []) if o["type"] == "webhook_publish"), None)
check("...and publishes the wired metric on its own schedule (MQTT Publish "
      "follows its wire instead)",
      bool(doc) and doc["key"] == "fn_cnt4_count" and doc["trigger"] == "any"
      and doc.get("interval_s") == 3 and "mode" not in doc, doc)

if FAIL:
    print(f"\n{len(FAIL)} FAILED: {FAIL}")
    sys.exit(1)
print("\nALL OK")
