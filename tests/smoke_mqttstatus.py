# When an MQTT Publish publishes, and the broker dot on the MQTT nodes
# (client UAT 2026-10-04, item 1; Tok Aba 2026-10-08).
#
#   1. an MQTT Publish has NO clock of its own any more: the Trigger or the
#      Inject wired in says when (core/services/publish_follow.py). Nothing
#      wired in, it compiles clean to NO document and says on the node what
#      to wire (it used to publish its own payload, or the time, on its own
#      "Every"). A node saved with that clock is migrated, never silenced:
#      unwired, an Inject repeating every N s (and once at start) is drawn in
#      front of it carrying the old payload; wired, the old Every is dropped.
#      The app: an Inject's every fire publishes, anything else publishes on
#      change and on each fire (beat) of the Trigger in front of it; a
#      document compiled before (no mode) keeps its own clock until recompiled
#   2. the validator: a keyless document only when it SAYS payload (the old
#      shape, still read); a following one keeps mode/fired/beat and no clock
#   3. the app publishes mqtt_<connection>_ok / _error for every Connection
#      it knows (app/mqtt_bus.py telemetry), on the same channel as the
#      counts, and the master's live values pass them to the canvas
#   4. the canvas: every MQTT node wears a StatusDot fed by nodeStatus.js
#      (the dot's own states are tests/check_nodestatus.mjs); the Publish
#      inspector has no Every and no payload, and says what to wire
#
#   py -3 tests/smoke_mqttstatus.py
import io
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "systems", "demo"))
os.chdir(ROOT)
os.environ["CYTRON_STORE"] = tempfile.mkdtemp()

# The canvas route imports fastapi, which is not installed on every dev box
# (same trick as tests/smoke_outputstat.py).
if "fastapi" not in sys.modules:
    fa = types.ModuleType("fastapi")
    fa.APIRouter = lambda *a, **k: types.SimpleNamespace(
        get=lambda *a, **k: (lambda f: f), post=lambda *a, **k: (lambda f: f),
        patch=lambda *a, **k: (lambda f: f), put=lambda *a, **k: (lambda f: f),
        delete=lambda *a, **k: (lambda f: f))
    fa.HTTPException = type("HTTPException", (Exception,), {})
    fa.Request = object
    sys.modules["fastapi"] = fa

from core.routes import workflow as wf_routes                  # noqa: E402
from core.services import detector_control as dc              # noqa: E402
from core.services import publish_follow                       # noqa: E402
from core.services import workflow as wf                       # noqa: E402
from app import functions as app_fns                           # noqa: E402
from app import mqtt_bus                                       # noqa: E402
from app import outputs as outputs_mod                         # noqa: E402

bad = 0


def check(label, ok, detail=""):
    global bad
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n      {detail}"))
    if not ok:
        bad += 1


def read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def node(nid, ntype, **data):
    return {"id": nid, "type": ntype, "position": {"x": 0, "y": 0},
            "data": {**wf.REGISTRY[ntype]["data"], **data}}


def compile_(nodes, edges):
    g = wf.clean_graph({"nodes": nodes, "edges": edges})
    wf.ensure_fn_ids(g)
    return g, wf.compile_graph(g, [], tables={})


# -- 1. no clock of its own: the wire says when -------------------------------------
def fresh(nid, **data):
    """A Publish dropped from today's palette: no interval_s, no payload."""
    n = node(nid, "output.mqtt_publish", **data)
    check("the palette's Publish carries no clock and no payload of its own",
          "interval_s" not in n["data"] and "payload" not in n["data"], n["data"])
    return n


def legacy(nid, **data):
    """A Publish saved before: its own Every (and nothing-wired payload)."""
    return {"id": nid, "type": "output.mqtt_publish", "position": {"x": 300, "y": 80},
            "data": {**wf.REGISTRY["output.mqtt_publish"]["data"],
                     "interval_s": 5, "payload": "", **data}}


def warnings_of(out, nid):
    return [w["message"] for w in out.get("warnings") or [] if w.get("node") == nid]


_g, out = compile_([fresh("p1", topic="line/alive")], [])
check("an MQTT Publish with nothing wired in compiles clean",
      not out["errors"], out["errors"])
check("...to NO document: with nothing wired in it publishes nothing",
      not out["outputs"], out["outputs"])
check("...and says on the node what to wire",
      any("wire a Trigger or an Inject" in m for m in warnings_of(out, "p1")),
      out.get("warnings"))

# A node saved with its own clock and nothing wired in: still publishes, now
# through an Inject the compiler draws in front of it (the load path draws
# and saves the same one, below).
_g, out = compile_([legacy("p1", topic="line/alive")], [])
fns = {f["id"]: f for f in out["functions"]}
doc = out["outputs"][0] if out["outputs"] else {}
clk = fns.get("mqtt_clock_p1") or {"params": {}}
check("an unwired Publish saved with Every 5 s still compiles to a document",
      not out["errors"] and doc.get("id") == "wf_p1", out)
check("...fed by an Inject repeating every 5 s, once at start too, sending the time",
      clk.get("type") == "inject" and clk["params"].get("interval_s") == 5
      and clk["params"].get("once") is True
      and clk["params"].get("ptype") == "timestamp", clk)
check("...which it publishes on each fire, with no clock of its own",
      doc.get("mode") == "fire" and doc.get("fired") == "fn_mqtt_clock_p1_fired"
      and doc.get("key") == "fn_mqtt_clock_p1_value"
      and "interval_s" not in doc and "payload" not in doc, doc)
check("...and it runs with the AI off (nothing of it waits for detection)",
      "p1" not in (out.get("waits_for_detection") or [])
      and any((o if isinstance(o, str) else o.get("id")) == "wf_p1"
              for o in out["frames_free"]["outputs"]),
      out["frames_free"])
_g, out = compile_([legacy("p1", topic="line/alive", payload="hello",
                           interval_s=0.5)], [])
clk = {f["id"]: f for f in out["functions"]}.get("mqtt_clock_p1") or {"params": {}}
check("a typed payload travels on the Inject as text; under 1 s becomes 1 s",
      not out["errors"] and clk["params"].get("ptype") == "string"
      and clk["params"].get("payload") == "hello"
      and clk["params"].get("interval_s") == 1, clk)
_g, out = compile_([node("i1", "common.inject", name="Kick", ptype="number", payload="7"),
                    legacy("p1", topic="line/x", payload="hello", interval_s=30)],
                   [{"id": "e1", "source": "i1", "target": "p1"}])
pub = out["outputs"][0] if out["outputs"] else {}
check("a WIRED Publish publishes the wired value on the Inject's fire; its "
      "old Every and payload are dropped",
      not out["errors"] and pub.get("key", "").endswith("_value")
      and pub.get("mode") == "fire" and pub.get("fired", "").endswith("_fired")
      and "payload" not in pub and "interval_s" not in pub
      and len(out["functions"]) == 1, pub)

# The load path: migrated once, saved, noted.
notes = []
g = {"nodes": [legacy("p1", topic="line/alive"),
               legacy("p2", topic="line/wired", interval_s=9),
               node("i1", "common.inject", name="Kick")],
     "edges": [{"id": "e1", "source": "i1", "target": "p2"}]}
changed = publish_follow.migrate(g, "demo", notify=lambda *a, **k: notes.append((a, k)))
by_id = {n["id"]: n for n in g["nodes"]}
clock = by_id.get("p1_clock") or {"data": {}}
check("on load an unwired Publish gets an Inject wired into it",
      changed and clock.get("type") == "common.inject"
      and clock["data"].get("every") == 5 and clock["data"].get("repeat") == "interval"
      and {"id": "p1_clock_wire", "source": "p1_clock", "target": "p1"} in g["edges"],
      g)
check("...both Publishes lose their own clock, and the wired one gets no Inject",
      all("interval_s" not in by_id[i]["data"] and "payload" not in by_id[i]["data"]
          for i in ("p1", "p2"))
      and not any(n["id"].startswith("p2_clock") for n in g["nodes"]), g)
check("...and the admins get one Inbox note saying where the clock went",
      len(notes) == 1 and "Inject" in notes[0][0][2]
      and "line/alive every 5s" in notes[0][0][2]
      and notes[0][1].get("page") == "systems", notes)
check("...once: a second load changes nothing and says nothing",
      not publish_follow.migrate(g, "demo", notify=lambda *a, **k: notes.append(a))
      and len(notes) == 1)
_g, out = compile_(g["nodes"], g["edges"])
check("...and the migrated canvas compiles to the same publisher",
      not out["errors"] and any(o["id"] == "wf_p1" and o.get("mode") == "fire"
                                for o in out["outputs"]), out)

sent = []
real = outputs_mod._mqtt
outputs_mod._mqtt = lambda d: sent.append((d["topic"], d["payload"])) or "sent"
try:
    outputs_mod._publish_state.clear()
    # A document compiled BEFORE (no mode): a running app keeps its clock
    # until the workflow is recompiled, so nothing goes quiet mid-update.
    keyless = {"id": "wf_p1", "type": "mqtt_publish", "enabled": True, "trigger": "any",
               "topic": "line/alive", "key": "", "payload": "", "interval_s": 5,
               "host": "127.0.0.1", "port": 1883}
    outputs_mod.publish([keyless], {}, now=1700000000.5)
    check("an old keyless document still publishes the time when the payload is blank",
          sent == [("line/alive", "1700000000500")], sent)
    outputs_mod.publish([keyless], {}, now=1700000002.0)
    check("...paced by its old interval", len(sent) == 1, sent)
    outputs_mod.publish([{**keyless, "id": "wf_p2", "payload": "hello"}], {},
                        now=1700000010.0)
    check("...and a typed payload as it is", sent[-1] == ("line/alive", "hello"), sent)

    # fire: the Inject's own repeat is the clock.
    sent.clear()
    fire = {"id": "wf_pf", "type": "mqtt_publish", "enabled": True, "topic": "t/f",
            "key": "fn_k_value", "mode": "fire", "fired": "fn_k_fired"}
    for t, fired_now, val in ((10.0, 1, 7), (10.1, 0, 7), (15.0, 0, 7),
                              (20.0, 1, 7), (20.1, 1, 8)):
        outputs_mod.publish([fire], {"fn_k_value": val, "fn_k_fired": fired_now}, now=t)
    check("fire: published on each Inject fire only, the same payload again included",
          sent == [("t/f", "7"), ("t/f", "7"), ("t/f", "8")], sent)

    # change: the value, and each fire of the Trigger in front (its beat).
    sent.clear()
    follow = {"id": "wf_pc", "type": "mqtt_publish", "enabled": True, "topic": "t/c",
              "key": "fn_r_out", "mode": "change", "beat": "r"}
    app_fns._fire_beats.pop("r", None)
    seq = []
    for t, val, beats in ((1.0, 0, 0), (2.0, 0, 0), (3.0, 7, 1), (4.0, 7, 0),
                          (13.0, 7, 1), (23.0, 7, 1), (25.0, 0, 0), (25.05, 7, 1),
                          (25.3, 7, 0)):
        for _ in range(beats):
            app_fns._beat("r")
        outputs_mod.publish([follow], {"fn_r_out": val}, now=t)
        seq.append(len(sent))
    check("change: the state at start, the payload at a fire, again at each "
          "Interval re-check, the idle payload when it lets go",
          [p for _t, p in sent[:5]] == ["0", "7", "7", "7", "0"], sent)
    check("...paced: a fire 50 ms after a send goes out on the next tick",
          seq[-2] == 5 and seq[-1] == 6 and sent[-1] == ("t/c", "7"), (seq, sent))
finally:
    outputs_mod._mqtt = real

# -- 2. the validator ---------------------------------------------------------------
try:
    dc._validate_output({"type": "mqtt_publish", "topic": "t", "connection": ""})
    check("a document with neither key nor payload is refused", False)
except ValueError as exc:
    check("a document with neither key nor payload is refused",
          "needs a value" in str(exc), exc)
kept = dc._validate_output({"type": "mqtt_publish", "topic": "t", "payload": "",
                            "connection": ""})
check("...one that says payload (even blank) is kept keyless (the old shape)",
      kept["key"] == "" and kept["payload"] == "" and kept["interval_s"] == 5, kept)
kept = dc._validate_output({"type": "mqtt_publish", "topic": "t", "key": "fn_a_value",
                            "payload": "x", "connection": ""})
check("...and a keyed one ignores the payload", kept["key"] == "fn_a_value"
      and "payload" not in kept, kept)
kept = dc._validate_output({"type": "mqtt_publish", "topic": "t", "key": "fn_a_out",
                            "mode": "change", "beat": "a", "interval_s": 5,
                            "connection": ""})
check("a following one keeps its mode and beat, and no clock",
      kept.get("mode") == "change" and kept.get("beat") == "a"
      and "interval_s" not in kept, kept)
try:
    dc._validate_output({"type": "mqtt_publish", "topic": "t", "key": "fn_a_value",
                         "mode": "fire", "connection": ""})
    check("a fire one without the fire it follows is refused", False)
except ValueError as exc:
    check("a fire one without the fire it follows is refused",
          "fire it follows" in str(exc), exc)

# -- 3. the connection status reaches the canvas --------------------------------------
made = []


class FakeClient:
    def __init__(self, client_id):
        self.client_id = client_id
        made.append(self)

    def username_pw_set(self, *a): pass
    def reconnect_delay_set(self, **k): pass
    def connect_async(self, *a, **k): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass
    def subscribe(self, *a, **k): pass

    def up(self, rc=0):
        self.on_connect(self, None, None, rc)

    def down(self):
        self.on_disconnect(self, None, 0)


mqtt_bus.close_all()
mqtt_bus.CLIENT_FACTORY = FakeClient
mqtt_bus.set_clients([{"id": "mq_0000aaaa", "name": "PLC", "host": "10.0.0.5", "port": 1883},
                      {"id": "mq_0000bbbb", "name": "Spare", "host": "10.0.0.6", "port": 1883}])
mqtt_bus.prestart(["mq_0000aaaa"])
t = mqtt_bus.telemetry()
check("a Connection still connecting reports ok 0, connecting",
      t["mqtt_mq_0000aaaa_ok"] == 0 and t["mqtt_mq_0000aaaa_error"] == "connecting", t)
check("a Connection no node started reports ok 0, not in use",
      t["mqtt_mq_0000bbbb_ok"] == 0 and t["mqtt_mq_0000bbbb_error"] == "not in use", t)
made[0].up()
t = mqtt_bus.telemetry()
check("a connected one reports ok 1, no error",
      t["mqtt_mq_0000aaaa_ok"] == 1 and t["mqtt_mq_0000aaaa_error"] == "", t)
made[0].down()
check("...and 0 again the moment the broker drops it",
      mqtt_bus.telemetry()["mqtt_mq_0000aaaa_ok"] == 0)
made[0].up(rc=5)
check("a refused login is said", "refused the login" in mqtt_bus.telemetry()["mqtt_mq_0000aaaa_error"],
      mqtt_bus.telemetry())
mqtt_bus.close_all()
mqtt_bus.CLIENT_FACTORY = None

live = wf_routes._live_values({"mqtt_mq_0000aaaa_ok": 1, "mqtt_mq_0000aaaa_error": "",
                               "fn_a_value": 3, "cam_1_fps": 9})
check("the master passes the mqtt_ keys to the canvas with the live values",
      live == {"mqtt_mq_0000aaaa_ok": 1, "mqtt_mq_0000aaaa_error": "", "fn_a_value": 3},
      live)
run_py = read("systems", "demo", "app", "run.py")
check("the app folds the connection status into its counts every tick",
      "counts.update(mqtt_bus.telemetry())" in run_py)

# -- 4. the canvas ---------------------------------------------------------------------
nodes_js = read("frontend", "src", "workflow", "nodes.jsx")
check("every MQTT node wears the status dot, fed by nodeStatus.js",
      'import StatusDot from "../components/StatusDot.jsx"' in nodes_js
      and 'mqttDot } from "./nodeStatus.js"' in nodes_js
      and 'attr="mqtt"' in nodes_js)
face = nodes_js[nodes_js.index('"output.mqtt_publish": d.topic'):]
face = face[:face.index('"output.webhook":')]
check("...and an unwired Publish says on its face what to wire, no 'every Ns'",
      "wire a Trigger or Inject to publish" in face
      and "d.payload" not in face and "interval_s" not in face, face)
page = read("frontend", "src", "pages", "Workflow.jsx")
pub_panel = page[page.index('node.type === "output.mqtt_publish" && (<>'):]
pub_panel = pub_panel[:pub_panel.index('node.type === "output.email"')]
check("the Publish inspector has no Every and no payload of its own",
      'label="Every"' not in pub_panel and 'set("interval_s")' not in pub_panel
      and 'set("payload")' not in pub_panel
      and "Payload when nothing is wired" not in page)
check("...says what to wire while nothing is, and how the Trigger decides",
      page.count("!(edges || []).some((e) => e.target === node.id) && (") == 1
      and "Nothing is wired in, so this publishes nothing." in pub_panel
      and "Interval sends it again" in pub_panel
      and "every N seconds while the trigger holds" in pub_panel
      and "An Inject publishes each time it fires" in pub_panel
      and "Publishes on a clock" not in pub_panel
      and "\u2014" not in pub_panel)
check("the palette says it too: no 'every few seconds' on MQTT Publish",
      "every few seconds" not in wf.REGISTRY["output.mqtt_publish"]["description"]
      and "Trigger or Inject" in wf.REGISTRY["output.mqtt_publish"]["description"])
check("the page polls the broker status for the dots on this box's broker",
      'usePoll("/api/mqtt/broker/status", 10000)' in page and "brokerUp," in page)

print("\n%s" % ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
